"""tests/test_cli_clean_json.py - `clean --apply` must purge whichever way the result is printed.

Regression: with --json the command printed the detection report with "dry_run": false and returned
before reaching the purge code, so scripts and agents were told a cleanup happened when nothing did.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

from smart_drive.cli import cmd_clean
from smart_drive.cli.main import main

JUNK = (".DS_Store", "Thumbs.db", "scratch.tmp", "__pycache__/mod.pyc")
KEEPERS = ("notes.txt", "important.bak", ".env", ".git/HEAD", "src/main.py")


class TestCleanApply(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_clean_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in JUNK + KEEPERS:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")

    def run_clean(self, *extra: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["clean", "--root", str(self.root), "--tier", "3", *extra])
        return code, out.getvalue(), err.getvalue()

    def present(self, names: Tuple[str, ...]) -> List[str]:
        return [n for n in names if (self.root / n).exists()]

    def test_dry_run_json_changes_nothing(self) -> None:
        code, out, _ = self.run_clean("--json")
        report: Dict[str, Any] = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(report["dry_run"])
        self.assertGreaterEqual(report["junk_count"], 4)
        self.assertNotIn("deleted_count", report)
        self.assertEqual(self.present(JUNK), [n for n in JUNK])

    def test_apply_json_really_deletes_and_reports_it(self) -> None:
        code, out, _ = self.run_clean("--apply", "--json")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertFalse(report["dry_run"])
        self.assertEqual(self.present(("scratch.tmp", ".DS_Store", "Thumbs.db")), [])
        self.assertFalse((self.root / "__pycache__").exists())
        self.assertGreaterEqual(report["deleted_count"], 4)
        self.assertEqual(
            report["deleted_count"] + report["blocked_count"] + report["failed_count"], len(report["results"])
        )
        self.assertEqual(report["failed_count"], 0)
        self.assertTrue(all({"rel_path", "deleted", "status", "reason"} <= set(r) for r in report["results"]))

    def test_apply_json_never_touches_files_that_only_look_like_junk(self) -> None:
        self.run_clean("--apply", "--json")
        self.assertEqual(self.present(KEEPERS), list(KEEPERS))

    def test_apply_json_honours_the_log_option(self) -> None:
        log = self.root.parent / f"{self.root.name}_audit.json"
        self.addCleanup(lambda: log.unlink() if log.exists() else None)
        _, out, _ = self.run_clean("--apply", "--json", "--log", str(log))
        self.assertEqual(json.loads(out)["log"], str(log))
        self.assertTrue(log.is_file(), "the audit log was silently ignored in JSON mode")
        self.assertIn("scratch.tmp", log.read_text(encoding="utf-8"))

    def test_apply_text_mode_still_purges(self) -> None:
        code, out, _ = self.run_clean("--apply")
        self.assertEqual(code, 0)
        self.assertIn("Purged", out)
        self.assertEqual(self.present(("scratch.tmp", ".DS_Store", "Thumbs.db")), [])
        self.assertEqual(self.present(KEEPERS), list(KEEPERS))

    def test_apply_json_on_a_clean_drive_reports_zero(self) -> None:
        self.run_clean("--apply", "--json")
        _, out, _ = self.run_clean("--apply", "--json")
        report = json.loads(out)
        self.assertEqual((report["junk_count"], report["deleted_count"]), (0, 0))


def _can_ignore_permissions() -> bool:
    return sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


@unittest.skipIf(_can_ignore_permissions(), "needs a POSIX user that directory permissions apply to")
class TestCleanReportsFailuresHonestly(unittest.TestCase):
    """A deletion the OS refuses used to be printed as "✓ Purged 1 junk files successfully." with exit code 0."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_clean_fail_")).resolve()
        (self.root / "open").mkdir()
        (self.root / "open" / ".DS_Store").write_text("x", encoding="utf-8")
        (self.root / "locked").mkdir()
        (self.root / "locked" / "Thumbs.db").write_text("x", encoding="utf-8")
        (self.root / "locked").chmod(0o555)  # entries in it cannot be removed
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup((self.root / "locked").chmod, 0o755)  # runs first, so the cleanup above can delete it

    def run_clean(self, *extra: str) -> Tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["clean", "--root", str(self.root), "--apply", *extra])
        return code, out.getvalue()

    def test_text_mode_says_what_failed_and_exits_nonzero(self) -> None:
        code, out = self.run_clean()
        self.assertEqual(code, 1)
        self.assertNotIn("successfully", out)
        self.assertIn("Purged 1 of 2 junk files.", out)
        self.assertIn("locked/Thumbs.db", out.replace("\\", "/"))
        self.assertFalse((self.root / "open" / ".DS_Store").exists())
        self.assertTrue((self.root / "locked" / "Thumbs.db").exists())

    def test_json_mode_counts_the_failure_and_exits_nonzero(self) -> None:
        code, out = self.run_clean("--json")
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual((report["deleted_count"], report["blocked_count"], report["failed_count"]), (1, 0, 1))
        failed = [r for r in report["results"] if r["status"] == "FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["deleted"])
        self.assertTrue(failed[0]["reason"])


class TestBlockedIsNotFailed(unittest.TestCase):
    """Items the safety guard refuses are expected behaviour, so they are reported but are not an error."""

    def test_tally_separates_deleted_blocked_and_failed(self) -> None:
        results = [
            {"rel_path": "a", "deleted": True, "status": "DELETED", "reason": ""},
            {"rel_path": "b", "deleted": False, "status": "BLOCKED", "reason": "protected"},
            {"rel_path": "c", "deleted": False, "status": "FAILED", "reason": "denied"},
        ]
        self.assertEqual(cmd_clean._tally(results), (1, 1, 1))

    def test_a_run_with_only_blocked_items_exits_zero_and_says_so(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sd_clean_blocked_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".DS_Store").write_text("x", encoding="utf-8")
        blocked = [{"rel_path": ".DS_Store", "deleted": False, "status": "BLOCKED", "reason": "protected"}]
        out = io.StringIO()
        with patch.object(cmd_clean, "_purge", return_value=(0, blocked)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["clean", "--root", str(root), "--apply"])
        self.assertEqual(code, 0)
        self.assertIn("1 item(s) kept by the safety guard", out.getvalue())
        self.assertIn("Purged 0 of 1 junk files.", out.getvalue())


class TestReclaimableSpaceCountsEveryItem(unittest.TestCase):
    def test_the_total_is_not_limited_to_the_thirty_items_listed(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sd_clean_total_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for i in range(40):
            (root / f"junk_{i:02d}.tmp").write_bytes(b"x" * 1024 * 1024)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(["clean", "--root", str(root), "--tier", "3"])
        self.assertIn("Found 40 junk items. Reclaimable space: 40.00 MB", out.getvalue())
        self.assertIn("and 10 more items", out.getvalue())


if __name__ == "__main__":
    unittest.main()
