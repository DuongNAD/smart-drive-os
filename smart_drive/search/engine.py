"""smart_drive.search.engine - Sub-100ms SQLite FTS5 Query Engine.

Executes full-text keyword queries with BM25 ranking and compound metadata filtering.
Handles dual execution paths:
- Path A (FTS5 MATCH): when keyword or phrase query is present.
- Path B (Direct B-Tree Filter): when wildcard '*' or purely metadata-driven filters are requested.
"""

from __future__ import annotations

import logging
import re
import shlex
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from smart_drive.indexer.db import DatabaseManager
from smart_drive.search.parser import SearchParams, sanitize_fts_query

logger = logging.getLogger("smart_drive.search.engine")

_SQLITE_MAX_INT = 2 ** 63 - 1


def _to_sqlite_int(value: int) -> int:
    """Clamps to what an SQLite INTEGER holds: a bound beyond it still means 'nothing' or 'everything'."""
    return max(-_SQLITE_MAX_INT - 1, min(int(value), _SQLITE_MAX_INT))


def _category_key(text: str) -> str:
    """Spelling-insensitive form of a category: 'AI Models', 'ai_models' and 'ai-models' compare equal."""
    return re.sub(r"[\s_/\-]+", " ", text).strip().lower()


def _like_contains(text: str) -> str:
    """`%text%` for `LIKE ... ESCAPE '\\'`, with %, _ and the backslash itself matched literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass
class SearchMatch:
    """Individual search hit."""
    id: int
    path: str
    name: str
    extension: str
    size: int
    mtime: float
    category: str
    rank: float = 0.0

    @property
    def filename(self) -> str:
        return self.name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "filename": self.name,
            "extension": self.extension,
            "size": self.size,
            "mtime": self.mtime,
            "category": self.category,
            "rank": round(self.rank, 4),
        }


@dataclass
class SearchResult:
    """Encapsulates list of matches and query latency."""
    matches: List[SearchMatch] = field(default_factory=list)
    total_count: int = 0
    elapsed_ms: float = 0.0
    # Things the caller should know about the query (an ignored filter, a category that does not exist).
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "matches": [m.to_dict() for m in self.matches],
            "total_count": self.total_count,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }
        if self.warnings:
            data["warnings"] = list(self.warnings)
        return data


class SearchEngine:
    """High-performance query engine backed by SQLite FTS5."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db = db_manager

    def search(self, params: SearchParams) -> SearchResult:
        """Executes search with sub-100ms latency target."""
        t0 = time.perf_counter()
        con = self.db.get_connection()
        cur = con.cursor()

        where_clauses: List[str] = []
        sql_params: Dict[str, Any] = {
            "limit": _to_sqlite_int(params.limit),
            "offset": _to_sqlite_int(params.offset),
        }
        warnings: List[str] = list(params.warnings)
        target_cat: Optional[str] = None

        # 1. Extension filters
        if params.extensions:
            ext_placeholders = []
            for i, ext in enumerate(sorted(params.extensions)):
                p_name = f"ext_{i}"
                clean_ext = ext if ext.startswith(".") else f".{ext}"
                sql_params[p_name] = clean_ext.lower()
                ext_placeholders.append(f":{p_name}")
            where_clauses.append(f"f.extension IN ({', '.join(ext_placeholders)})")

        # 2. Size filters
        if params.min_size is not None:
            where_clauses.append("f.size >= :min_size")
            sql_params["min_size"] = _to_sqlite_int(params.min_size)

        if params.max_size is not None:
            where_clauses.append("f.size <= :max_size")
            sql_params["max_size"] = _to_sqlite_int(params.max_size)

        # 3. Category filter
        if params.category:
            cat_input = params.category.strip()
            cat_key = _category_key(cat_input)
            canonical_cat = None
            for known in ("Code", "AI Models", "Books/Learning", "Docs", "Media", "Archives", "System Junk", "Other"):
                known_key = _category_key(known)
                if cat_key == known_key:
                    canonical_cat = known
                    break
                elif cat_key and cat_key in known_key:
                    if canonical_cat is None:
                        canonical_cat = known
            target_cat = canonical_cat if canonical_cat else cat_input
            where_clauses.append("f.category = :cat")
            sql_params["cat"] = target_cat

        # 4. Directory / path filter
        if params.directory:
            dir_clean = params.directory.replace("\\", "/").strip("/")
            for drive_prefix in ("/Volumes/KINGSTON", "/Volumes/Kingston", "/volumes/kingston"):
                if dir_clean.startswith(drive_prefix.strip("/")):
                    dir_clean = dir_clean[len(drive_prefix.strip("/")):].strip("/")
                    break
                if dir_clean.startswith(drive_prefix):
                    dir_clean = dir_clean[len(drive_prefix):].strip("/")
                    break
            if len(dir_clean) >= 2 and dir_clean[1] == ":" and dir_clean[0].isalpha():
                dir_clean = dir_clean[2:].strip("/")
            where_clauses.append("f.path LIKE :dir ESCAPE '\\'")
            sql_params["dir"] = _like_contains(dir_clean)

        # 5. Timestamp filters
        if params.min_mtime is not None:
            where_clauses.append("f.mtime >= :min_mtime")
            sql_params["min_mtime"] = params.min_mtime

        if params.max_mtime is not None:
            where_clauses.append("f.mtime <= :max_mtime")
            sql_params["max_mtime"] = params.max_mtime

        # Check if FTS keyword search is requested
        kw = params.keyword.strip() if params.keyword else ""
        use_fts = bool(kw and kw != "*")

        matches: List[SearchMatch] = []
        total_count = 0
        rows = []

        if use_fts:
            sanitized = sanitize_fts_query(kw)
            if not sanitized:
                sanitized = kw

            fts_terms = []
            try:
                raw_tokens = shlex.split(sanitized, posix=False)
            except ValueError:
                raw_tokens = sanitized.split()

            for token in raw_tokens:
                if token.upper() in {"AND", "OR", "NOT"}:
                    fts_terms.append(token.upper())
                elif token.startswith('"') and token.endswith('"'):
                    fts_terms.append(token)
                elif token.endswith("*"):
                    bare = token[:-1]
                    if any(not c.isalnum() and c != '"' for c in bare):
                        clean_tok = bare.replace('"', "")
                        if clean_tok:
                            fts_terms.append(f'"{clean_tok}"')
                    else:
                        fts_terms.append(token)
                elif any(not c.isalnum() and c != '"' for c in token):
                    clean_tok = token.replace('"', "")
                    if clean_tok:
                        fts_terms.append(f'"{clean_tok}"')
                else:
                    fts_terms.append(f"{token}*")
            fts_expr = " ".join(fts_terms)
            sql_params["fts_expr"] = fts_expr

            if not where_clauses:
                count_sql = "SELECT COUNT(*) FROM files_fts WHERE files_fts MATCH :fts_expr;"
                sql = """
                    WITH top_matches AS (
                        SELECT rowid, bm25(files_fts, 5.0, 1.0) AS rank
                        FROM files_fts
                        WHERE files_fts MATCH :fts_expr
                        ORDER BY rank ASC
                        LIMIT :limit OFFSET :offset
                    )
                    SELECT f.id, f.path, f.filename, f.extension, f.size, f.mtime, f.category, t.rank
                    FROM top_matches t
                    JOIN files f ON f.id = t.rowid
                    ORDER BY t.rank ASC;
                """
            else:
                where_str = "WHERE files_fts MATCH :fts_expr AND " + " AND ".join(where_clauses)
                count_sql = f"""
                    SELECT COUNT(*)
                    FROM files_fts
                    CROSS JOIN files f ON f.id = files_fts.rowid
                    {where_str};
                """
                sql = f"""
                    SELECT f.id, f.path, f.filename, f.extension, f.size, f.mtime, f.category,
                           bm25(files_fts, 5.0, 1.0) AS rank
                    FROM files_fts
                    CROSS JOIN files f ON f.id = files_fts.rowid
                    {where_str}
                    ORDER BY rank ASC, f.mtime DESC
                    LIMIT :limit OFFSET :offset;
                """

            try:
                cur.execute(count_sql, sql_params)
                total_count = cur.fetchone()[0]
                cur.execute(sql, sql_params)
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                use_fts = False

        if not use_fts:
            if kw and kw != "*":
                where_clauses.append("(f.filename LIKE :like_kw ESCAPE '\\' OR f.path LIKE :like_kw ESCAPE '\\')")
                sql_params["like_kw"] = _like_contains(kw)

            where_str = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            sql = f"""
                SELECT f.id, f.path, f.filename, f.extension, f.size, f.mtime, f.category, 0.0 AS rank
                FROM files f
                {where_str}
                ORDER BY f.size DESC, f.mtime DESC
                LIMIT :limit OFFSET :offset;
            """
            count_sql = f"SELECT COUNT(*) FROM files f {where_str};"
            cur.execute(count_sql, sql_params)
            total_count = cur.fetchone()[0]
            cur.execute(sql, sql_params)
            rows = cur.fetchall()

        for r in rows:
            matches.append(
                SearchMatch(
                    id=r[0],
                    path=r[1],
                    name=r[2],
                    extension=r[3] or "",
                    size=r[4],
                    mtime=r[5],
                    category=r[6],
                    rank=r[7],
                )
            )

        if target_cat is not None and total_count == 0:
            # An empty answer for a category nobody has is a typo, not a finding: say which ones exist.
            try:
                present = [row[0] for row in cur.execute("SELECT DISTINCT category FROM files ORDER BY category")]
            except sqlite3.Error:
                present = []
            if present and target_cat not in present:
                warnings.append(
                    f"No file has category {params.category!r}; categories in this index: {', '.join(present)}"
                )

        elapsed = (time.perf_counter() - t0) * 1000.0
        return SearchResult(matches=matches, total_count=total_count, elapsed_ms=elapsed, warnings=warnings)


__all__ = [
    "SearchMatch",
    "SearchResult",
    "SearchEngine",
]
