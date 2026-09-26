"""smart_drive.indexer - SQLite FTS5 database and indexing manager."""
from __future__ import annotations

from smart_drive.indexer.db import DatabaseManager, SCHEMA_SQL
from smart_drive.indexer.manager import IndexManager, IndexStats, IncrementalStats

__all__ = [
    "DatabaseManager",
    "SCHEMA_SQL",
    "IndexManager",
    "IndexStats",
    "IncrementalStats",
]
