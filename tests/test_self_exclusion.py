"""tests/test_self_exclusion.py - The tool's own state folder must never be scanned.

Regression: `.smart_drive/` (the search index, its SQLite -wal/-shm files, snapshots) was scanned like
user data. The index contained rows for its own files, every `update` reported three phantom
"modified" files, `dup` listed `index.db-wal` as a duplicate of other empty files (one more thing an
AI agent could be tempted to delete), and audit totals counted the tool's own files.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from smart_drive.cli.main import main
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.config import DEFAULT_EXCLUDE_DIRS


class _Drive(unittest.TestCase):
    STATE_DIR = ".smart_drive"

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_self_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "notes").mkdir()
        for name in ("a.txt", "b.txt"):
            (self.root / "notes" / name).write_text(name * 10, encoding="utf-8")
        for name in ("empty1.dat", "empty2.dat"):  # genuine duplicates of each other
            (self.root / "notes" / name).write_bytes(b"")

    def cli(self, *argv: str) -> Tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        return code, out.getvalue()

    def indexed_paths(self) -> list:
        db = sqlite3.connect(str(self.root / self.STATE_DIR / "index.db"))
        try:
            return [row[0] for row in db.execute("SELECT path FROM files")]
        finally:
            db.close()


class TestStateFolderIsExcluded(_Drive):
    def test_both_state_folder_names_are_in_the_default_exclusions(self) -> None:
        self.assertIn(".smart_drive", DEFAULT_EXCLUDE_DIRS)
        self.assertIn(".smart_drive_manager", DEFAULT_EXCLUDE_DIRS)

    def test_the_index_does_not_contain_its_own_files(self) -> None:
        self.cli("index", "--root", str(self.root))
        paths = self.indexed_paths()
        self.assertEqual(sorted(paths), ["notes/a.txt", "notes/b.txt", "notes/empty1.dat", "notes/empty2.dat"])
        self.assertFalse([p for p in paths if p.startswith(".smart_drive")])

    def test_update_reports_nothing_when_nothing_changed(self) -> None:
        self.cli("index", "--root", str(self.root))
        for _ in range(2):  # used to report modified=3 every time
            _, out = self.cli("update", "--root", str(self.root), "--json")
            stats = json.loads(out)
            self.assertEqual((stats["added"], stats["modified"], stats["deleted"]), (0, 0, 0))
            self.assertEqual(stats["unchanged"], 4)

    def test_update_drops_state_rows_left_by_an_older_version(self) -> None:
        self.cli("index", "--root", str(self.root))
        db = sqlite3.connect(str(self.root / self.STATE_DIR / "index.db"))
        db.executemany(
            "INSERT INTO files (path, filename, extension, size, mtime, category, indexed_at) VALUES (?, ?, '', 0, 0, 'Other', 0)",
            [(".smart_drive/index.db-wal", "index.db-wal"), (".smart_drive/index.db-shm", "index.db-shm")],
        )
        db.commit()
        db.close()
        _, out = self.cli("update", "--root", str(self.root), "--json")
        self.assertEqual(json.loads(out)["deleted"], 2)
        self.assertFalse([p for p in self.indexed_paths() if p.startswith(".smart_drive")])

    def test_dup_never_offers_the_state_files(self) -> None:
        self.cli("index", "--root", str(self.root))
        (self.root / self.STATE_DIR / "extra_empty").write_bytes(b"")  # would match the empty user files
        _, out = self.cli("dup", "--root", str(self.root), "--json")
        groups = json.loads(out)["duplicate_groups"]
        files = sorted(Path(f).name for g in groups for f in g["files"])
        self.assertEqual(files, ["empty1.dat", "empty2.dat"])

    def test_audit_does_not_count_the_state_files(self) -> None:
        self.cli("index", "--root", str(self.root))
        total = StorageAuditor(str(self.root)).run_audit()["total_files"]
        self.assertEqual(total, 4)

    def test_legacy_state_folder_is_excluded_too(self) -> None:
        legacy = self.root / ".smart_drive_manager"
        legacy.mkdir()
        (legacy / "index.db").write_bytes(b"x" * 100)
        self.assertEqual(StorageAuditor(str(self.root)).run_audit()["total_files"], 4)


if __name__ == "__main__":
    unittest.main()
