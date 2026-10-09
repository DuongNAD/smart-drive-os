"""tests/test_cli_clean_json.py - `clean --apply` must purge whichever way the result is printed.

Regression: with --json the command printed the detection report with "dry_run": false and returned
before reaching the purge code, so scripts and agents were told a cleanup happened when nothing did.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
        self.assertEqual(report["failed_count"], len(report["results"]) - report["deleted_count"])
        self.assertTrue(all("rel_path" in r and "deleted" in r and "reason" in r for r in report["results"]))

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


if __name__ == "__main__":
    unittest.main()
