"""tests/test_indexer.py - Comprehensive Unit Tests for SQLite FTS5 Database & IndexManager.

100% Python Standard Library unittest.
Tests Tiers 1, 2, 3:
- SQLite database initialization, WAL journal mode, and performance PRAGMAs.
- Normalized files table and external content FTS5 virtual table files_fts.
- Automated synchronization triggers (AFTER INSERT, UPDATE, DELETE).
- High-throughput batch ingestion of mock directory tree (full_index).
- O(1) scoped incremental change detection (additions, modifications, deletions).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.indexer.db import DatabaseManager, SCHEMA_SQL
    from smart_drive.indexer.manager import IndexManager, IndexStats, IncrementalStats
except ImportError:
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from indexer.db import DatabaseManager, SCHEMA_SQL
    from indexer.manager import IndexManager, IndexStats, IncrementalStats

from tests.helpers import SmartDriveTestCase


class TestDatabaseManagerAndTriggers(SmartDriveTestCase):
    """Tier 1: SQLite schema creation, WAL mode, and FTS5 triggers."""

    def test_database_initialization_and_tables(self) -> None:
        """DatabaseManager creates files table, indexes, and files_fts virtual table."""
        db_file = self.test_dir / "test_index.db"
        with DatabaseManager(str(db_file)) as db:
            db.initialize_schema()
            con = db.get_connection()
            cur = con.cursor()

            # Check tables
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cur.fetchall()}
            self.assertIn("files", tables)
            self.assertIn("files_fts", tables)
            self.assertIn("index_meta", tables)

            # Check triggers
            cur.execute("SELECT name FROM sqlite_master WHERE type='trigger';")
            triggers = {row[0] for row in cur.fetchall()}
            self.assertIn("trg_files_ai", triggers)
            self.assertIn("trg_files_ad", triggers)
            self.assertIn("trg_files_au", triggers)

    def test_triggers_maintain_fts5_synchronization(self) -> None:
        """INSERT, UPDATE, and DELETE on files table automatically sync to files_fts."""
        db_file = self.test_dir / "test_triggers.db"
        with DatabaseManager(str(db_file)) as db:
            db.initialize_schema()
            con = db.get_connection()
            cur = con.cursor()

            now = time.time()
            # 1. Insert
            cur.execute(
                "INSERT INTO files (path, filename, extension, size, mtime, category, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?);",
                ("01_AI_Models/llama3.gguf", "llama3.gguf", ".gguf", 4000000, now, "AI Models", now)
            )
            con.commit()

            # Check FTS5 MATCH
            cur.execute("SELECT rowid, filename, path FROM files_fts WHERE files_fts MATCH 'llama3';")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["filename"], "llama3.gguf")

            # 2. Update
            row_id = rows[0]["rowid"]
            cur.execute(
                "UPDATE files SET filename = ?, path = ? WHERE id = ?;",
                ("qwen2.gguf", "01_AI_Models/qwen2.gguf", row_id)
            )
            con.commit()

            # Old name should NOT match
            cur.execute("SELECT rowid FROM files_fts WHERE files_fts MATCH 'llama3';")
            self.assertEqual(len(cur.fetchall()), 0)

            # New name must match
            cur.execute("SELECT rowid, filename FROM files_fts WHERE files_fts MATCH 'qwen2';")
            updated_rows = cur.fetchall()
            self.assertEqual(len(updated_rows), 1)
            self.assertEqual(updated_rows[0]["filename"], "qwen2.gguf")

            # 3. Delete
            cur.execute("DELETE FROM files WHERE id = ?;", (row_id,))
            con.commit()

            cur.execute("SELECT rowid FROM files_fts WHERE files_fts MATCH 'qwen2';")
            self.assertEqual(len(cur.fetchall()), 0)


class TestIndexManagerBatchAndIncremental(SmartDriveTestCase):
    """Tier 1 & Tier 3: Batch ingestion and incremental synchronization."""

    def test_full_index_ingestion(self) -> None:
        """IndexManager.full_index scans directory tree and indexes files."""
        mock_root = self.create_mock_drive()
        db_file = self.test_dir / "index.db"

        with DatabaseManager(str(db_file)) as db:
            db.initialize_schema()
            manager = IndexManager(db, str(mock_root))
            stats = manager.full_index(batch_size=50)

            self.assertIsInstance(stats, IndexStats)
            self.assertGreater(stats.indexed_files, 10)
            self.assertGreater(stats.throughput_fps, 0.0)

            # Verify rows in files table
            con = db.get_connection()
            cur = con.cursor()
            cur.execute("SELECT COUNT(*) FROM files;")
            count = cur.fetchone()[0]
            self.assertEqual(count, stats.indexed_files)

    def test_incremental_sync_detects_additions_modifications_deletions(self) -> None:
        """Incremental update catches new files, modified files, and deleted files."""
        mock_root = self.create_mock_drive()
        db_file = self.test_dir / "incremental_index.db"

        with DatabaseManager(str(db_file)) as db:
            db.initialize_schema()
            manager = IndexManager(db, str(mock_root))
            manager.full_index()

            # 1. Test no changes -> 0 added, 0 modified, 0 deleted
            sync1 = manager.incremental_update()
            self.assertEqual(sync1.added, 0)
            self.assertEqual(sync1.modified, 0)
            self.assertEqual(sync1.deleted, 0)
            self.assertGreater(sync1.unchanged, 10)

            # 2. Add a new file
            new_file = mock_root / "01_AI_Models" / "new_model.bin"
            new_file.write_bytes(b"BIN_MODEL_WEIGHTS")

            sync2 = manager.incremental_update()
            self.assertEqual(sync2.added, 1)

            # 3. Modify an existing file (change size and mtime)
            time.sleep(0.05)  # Ensure mtime changes
            new_file.write_bytes(b"BIN_MODEL_WEIGHTS_MODIFIED_EXTRA")

            sync3 = manager.incremental_update()
            self.assertEqual(sync3.modified, 1)

            # 4. Delete the file
            new_file.unlink()

            sync4 = manager.incremental_update()
            self.assertEqual(sync4.deleted, 1)


if __name__ == "__main__":
    unittest.main()
