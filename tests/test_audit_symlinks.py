"""tests/test_audit_symlinks.py - symlinks are skipped quietly and counted, and nothing claims the drive is exFAT.

Found by running the audit on the internal APFS disk: every symlink inside a virtualenv printed its own
"Skipping symlink on exFAT: ..." warning (thousands on a developer's home folder, on a volume that is not
exFAT), and the report never said that links had been left out of the numbers.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

from smart_drive.core.auditor import StorageAuditor


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: List[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
class TestSkippedSymlinks(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_audit_links_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "venv" / "bin").mkdir(parents=True)
        (self.root / "real.txt").write_text("hello", encoding="utf-8")
        for name in ("python", "python3", "python3.11"):
            os.symlink("/usr/bin/true", self.root / "venv" / "bin" / name)

    def audit(self, level: int = logging.WARNING):
        handler = _Collect()
        logger = logging.getLogger("smart_drive.core.scanner")
        previous = logger.level
        logger.setLevel(level)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        self.addCleanup(logger.setLevel, previous)
        return StorageAuditor(str(self.root)).run_audit(), handler.records

    def test_links_are_counted_not_announced_one_by_one(self) -> None:
        report, records = self.audit()
        self.assertEqual(report["scan_stats"]["symlinks_skipped"], 3)
        self.assertEqual([r for r in records if r.levelno >= logging.WARNING], [])
        self.assertEqual(report["summary"]["total_files"], 1)  # real.txt only: links are not counted

    def test_the_detail_is_still_there_for_whoever_asks_for_debug_output(self) -> None:
        _, records = self.audit(level=logging.DEBUG)
        messages = [r.getMessage() for r in records]
        self.assertEqual(len(messages), 3)
        self.assertTrue(all("never followed" in m and "exFAT" not in m for m in messages))

    def test_the_text_and_markdown_reports_say_so(self) -> None:
        report, _ = self.audit()
        text = report.to_ascii_table()
        self.assertIn("Symlinks Skipped:", text)
        self.assertIn("3", text.split("Symlinks Skipped:")[1].split("\n")[0])
        markdown = report.to_markdown()
        self.assertIn("**Symlinks Skipped** | 3", markdown)

    def test_a_drive_without_links_prints_no_such_line(self) -> None:
        shutil.rmtree(self.root / "venv")
        report, _ = self.audit()
        self.assertEqual(report["scan_stats"]["symlinks_skipped"], 0)
        self.assertNotIn("Symlinks Skipped", report.to_ascii_table())


if __name__ == "__main__":
    unittest.main()
