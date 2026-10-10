"""smart_drive.indexer.db - SQLite FTS5 Database Engine & Schema Manager.

Optimized for exFAT External SSD (Kingston XS2000 2TB).
Implements:
1. WAL journal mode with NORMAL synchronization.
2. 64MB RAM page cache and in-memory temporary tables.
3. Normalized files table with B-tree metadata indexes.
4. FTS5 virtual table with external content and unicode61 tokenizer.
5. Automated synchronization triggers (AFTER INSERT, UPDATE, DELETE).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional


SCHEMA_SQL = """
-- 1. Metadata Files Table
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,       -- Normalized relative path with '/'
    filename TEXT NOT NULL,          -- Base filename
    extension TEXT,                  -- Lowercase extension with dot (e.g. '.pdf')
    size INTEGER NOT NULL,           -- File size in bytes
    mtime REAL NOT NULL,             -- Modification timestamp (epoch seconds)
    category TEXT NOT NULL,          -- Code, AI Models, Books/Learning, Docs, Media, Archives, Other
    indexed_at REAL NOT NULL         -- Timestamp when row was indexed
);

-- B-Tree Indexes for Instant Metadata Filtering
CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension);
CREATE INDEX IF NOT EXISTS idx_files_cat ON files(category);
CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_cat_size ON files(category, size DESC);

-- 2. FTS5 External Content Virtual Table
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    filename,
    path,
    content='files',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- 3. Automatic Synchronization Triggers
CREATE TRIGGER IF NOT EXISTS trg_files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, filename, path) VALUES (new.id, new.filename, new.path);
END;

CREATE TRIGGER IF NOT EXISTS trg_files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, filename, path) VALUES ('delete', old.id, old.filename, old.path);
END;

CREATE TRIGGER IF NOT EXISTS trg_files_au AFTER UPDATE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, filename, path) VALUES ('delete', old.id, old.filename, old.path);
    INSERT INTO files_fts(rowid, filename, path) VALUES (new.id, new.filename, new.path);
END;

-- 4. Learning Content Chunks Table
CREATE TABLE IF NOT EXISTS doc_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id),
    kind TEXT NOT NULL,              -- slide, lesson, fact, source, module, capture, outline, research, error
    unit_id TEXT,                    -- Deck title, Course title, F-id, S-id, M-id, Heading, etc.
    locator TEXT,                    -- slide N (#id), lesson N (dir), locator · S-id, p. N, mm:ss, §k
    snippet TEXT,                    -- Slide title or first text (<= 160 chars)
    content TEXT NOT NULL            -- Chunk full text (<= 4000 chars)
);

CREATE INDEX IF NOT EXISTS idx_doc_chunks_file_id ON doc_chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_kind ON doc_chunks(kind);

-- 5. FTS5 External Content Virtual Table for Chunks
CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
    content,
    snippet,
    unit_id,
    content='doc_chunks',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- 6. Automatic Synchronization Triggers for Chunks
CREATE TRIGGER IF NOT EXISTS trg_doc_chunks_ai AFTER INSERT ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(rowid, content, snippet, unit_id) VALUES (new.id, new.content, new.snippet, new.unit_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_chunks_ad AFTER DELETE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, content, snippet, unit_id) VALUES ('delete', old.id, old.content, old.snippet, old.unit_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_doc_chunks_au AFTER UPDATE ON doc_chunks BEGIN
    INSERT INTO doc_chunks_fts(doc_chunks_fts, rowid, content, snippet, unit_id) VALUES ('delete', old.id, old.content, old.snippet, old.unit_id);
    INSERT INTO doc_chunks_fts(rowid, content, snippet, unit_id) VALUES (new.id, new.content, new.snippet, new.unit_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_files_doc_chunks_ad AFTER DELETE ON files BEGIN
    DELETE FROM doc_chunks WHERE file_id = old.id;
END;

-- 7. Index State & Meta Table
CREATE TABLE IF NOT EXISTS index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

SCHEMA_VERSION = "2"


class DatabaseManager:
    """Manages SQLite database connection lifecycle and schema setup."""

    def __init__(self, db_path: str) -> None:
        self.db_path = os.path.abspath(db_path)
        self._connection: Optional[sqlite3.Connection] = None

    def _ensure_dir(self) -> None:
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Returns active SQLite connection with optimized PRAGMAs."""
        if self._connection is None:
            self._ensure_dir()
            con = sqlite3.connect(self.db_path, timeout=30.0)
            con.row_factory = sqlite3.Row

            # Apply performance PRAGMAs for exFAT SSD
            cur = con.cursor()
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA cache_size = -64000;")       # 64 MB cache
            cur.execute("PRAGMA temp_store = MEMORY;")
            cur.execute("PRAGMA foreign_keys = OFF;")
            try:
                cur.execute("PRAGMA mmap_size = 268435456;")  # 256 MB memory-mapped I/O
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_files_cat_size ON files(category, size DESC);")
                con.commit()
            except sqlite3.OperationalError:
                pass
            cur.close()
            self._connection = con

        return self._connection

    def initialize_schema(self) -> None:
        """Creates tables, virtual tables, triggers, and indexes."""
        con = self.get_connection()
        con.executescript(SCHEMA_SQL)
        try:
            con.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild');")
        except sqlite3.Error:
            pass
        try:
            con.execute("INSERT INTO doc_chunks_fts(doc_chunks_fts) VALUES('rebuild');")
        except sqlite3.Error:
            pass
        con.execute("REPLACE INTO index_meta (key, value) VALUES ('schema_version', ?);", (SCHEMA_VERSION,))
        con.commit()

    def close(self) -> None:
        """Closes the underlying database connection."""
        if self._connection is not None:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
            self._connection = None

    def __enter__(self) -> DatabaseManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


__all__ = [
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "DatabaseManager",
]
