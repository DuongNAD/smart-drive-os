"""smart_drive.search.parser - Query Syntax Parser and FTS Sanitizer.

Parses compound query syntax:
- Keywords & phrases: llama, "machine learning"
- Extension filters: ext:pdf,md or .pdf
- Size filters: size:>100MB, size:<=10GB, size:>1KB, size:0
- Category filters: cat:AI, category:Code, tag:Docs
- Path / Directory filters: dir:02_Learning_Knowledge
- Sub-100ms FTS5 token sanitizer resilient to unbalanced syntax.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Optional, Set, Tuple


SIZE_REGEX = re.compile(r"^([><=]{1,2})?(\d+(?:\.\d+)?)\s*([kmgtp]?b?)?$", re.IGNORECASE)
SIZE_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024 ** 2,
    "mb": 1024 ** 2,
    "g": 1024 ** 3,
    "gb": 1024 ** 3,
    "t": 1024 ** 4,
    "tb": 1024 ** 4,
}


@dataclass
class SearchParams:
    """Structured parameters for search engine execution."""
    keyword: Optional[str] = None
    extensions: Set[str] = field(default_factory=set)
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    min_mtime: Optional[float] = None
    max_mtime: Optional[float] = None
    category: Optional[str] = None
    directory: Optional[str] = None
    limit: int = 100
    offset: int = 0


def sanitize_fts_query(query: str) -> str:
    """Sanitizes raw user input into safe SQLite FTS5 query string.

    Prevents syntax errors from unbalanced quotes, dangling boolean operators,
    or wildcard abuse. Quotes punctuation in tokens (hyphens, dots, colons, +, @, #) to
    prevent FTS5 syntax errors (e.g. llama-3-8b, c++, react@18).
    """
    if not query:
        return ""

    clean = query.strip()
    # Normalize excessive quotes
    quote_count = clean.count('"')
    if quote_count % 2 != 0:
        clean = clean.replace('"', "")

    try:
        tokens = shlex.split(clean, posix=False)
    except ValueError:
        tokens = clean.split()

    reserved_words = {"AND", "OR", "NOT", "NEAR"}

    # If query contains only reserved words or symbols, return empty
    non_reserved = [
        t for t in tokens
        if t.upper() not in reserved_words and t not in {'*', '(', ')', '{', '}', ':'}
    ]
    if not non_reserved:
        return ""

    safe_tokens = []
    for i, token in enumerate(tokens):
        t_upper = token.upper()
        if t_upper in reserved_words:
            if not safe_tokens or safe_tokens[-1].upper() in reserved_words or i == len(tokens) - 1:
                continue
            safe_tokens.append(t_upper)
        else:
            sub_tok = re.sub(r"\*+", "*", token)
            if sub_tok == "*" or sub_tok in {'(', ')', '{', '}', ':'}:
                continue

            has_star = sub_tok.endswith("*")
            bare_tok = sub_tok[:-1] if has_star else sub_tok

            if sub_tok.startswith('"') and sub_tok.endswith('"') and len(sub_tok) >= 2:
                safe_tokens.append(sub_tok)
            elif any(not c.isalnum() and c != '"' for c in bare_tok):
                clean_inner = bare_tok.replace('"', "")
                if clean_inner:
                    safe_tokens.append(f'"{clean_inner}"' + ("*" if has_star else ""))
            else:
                safe_tokens.append(sub_tok)

    while safe_tokens and safe_tokens[-1].upper() in reserved_words:
        safe_tokens.pop()

    return " ".join(safe_tokens)


def parse_size_spec(val: str) -> Tuple[Optional[str], Optional[int]]:
    """Parses size spec like '>1KB', '<=100MB', '0' into (operator, bytes)."""
    m = SIZE_REGEX.match(val.strip())
    if not m:
        return None, None
    op = m.group(1) or "="
    num = float(m.group(2))
    unit = (m.group(3) or "b").lower()
    mult = SIZE_MULTIPLIERS.get(unit, 1)
    bytes_val = int(num * mult)
    return op, bytes_val


def parse_search_query(query_str: str) -> SearchParams:
    """Parses free-text query string into structured SearchParams."""
    params = SearchParams()
    if not query_str:
        return params

    try:
        parts = shlex.split(query_str)
    except ValueError:
        parts = query_str.split()

    free_words = []

    for part in parts:
        p_lower = part.lower()

        # ext:pdf,md or .pdf
        if p_lower.startswith("ext:"):
            exts = p_lower[4:].split(",")
            for e in exts:
                clean_e = e.strip().lstrip(".")
                if clean_e:
                    params.extensions.add(clean_e)
        elif p_lower.startswith(".") and len(p_lower) > 1 and not p_lower.startswith(".."):
            clean_e = p_lower.lstrip(".")
            if clean_e:
                params.extensions.add(clean_e)

        # size:>1KB, size:<10MB, size:0
        elif p_lower.startswith("size:"):
            s_val = p_lower[5:]
            op, b_val = parse_size_spec(s_val)
            if b_val is not None:
                if op in (">", ">="):
                    params.min_size = b_val
                elif op in ("<", "<="):
                    params.max_size = b_val
                elif op == "=":
                    params.min_size = b_val
                    params.max_size = b_val

        # cat:AI, category:Code, tag:Docs
        elif p_lower.startswith("cat:") or p_lower.startswith("category:") or p_lower.startswith("tag:"):
            colon_idx = part.find(":")
            cat_val = part[colon_idx + 1:].strip()
            if cat_val:
                params.category = cat_val

        # dir:02_Learning_Knowledge or path:AI_Models
        elif p_lower.startswith("dir:") or p_lower.startswith("path:"):
            colon_idx = part.find(":")
            dir_val = part[colon_idx + 1:].strip().strip('"').strip("'")
            if dir_val:
                # Strip common drive prefixes
                for drive_prefix in ("/Volumes/KINGSTON", "/Volumes/Kingston", "/volumes/kingston"):
                    if dir_val.startswith(drive_prefix):
                        dir_val = dir_val[len(drive_prefix):]
                        break
                # Windows drive letter e.g. D:, d:, C:, c:
                if len(dir_val) >= 2 and dir_val[1] == ":" and dir_val[0].isalpha():
                    dir_val = dir_val[2:]
                dir_val = dir_val.replace("\\", "/").strip("/")
                if dir_val:
                    params.directory = dir_val

        else:
            free_words.append(part)

    if free_words:
        raw_kw = " ".join(free_words)
        params.keyword = raw_kw

    return params


__all__ = [
    "SearchParams",
    "parse_size_spec",
    "parse_search_query",
    "sanitize_fts_query",
]
