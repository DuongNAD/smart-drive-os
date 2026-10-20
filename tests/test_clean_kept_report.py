"""tests/test_clean_kept_report.py - `clean` says what it left alone although it looks like junk, and why.

Before, a name that matched a rule was simply listed. Now installed packages, folders that Windows or an installed app
depends on, build/dist/.cache folders away from a project root and folders git tracks are kept, so the report counts
them (JSON `kept`, `protected_by_system`; text "Kept N look-alike item(s)") - otherwise "0 junk found" and "1,100
things were kept on purpose" look the same.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import patch

from smart_drive.cli.main import main
from smart_drive.core.junk_detector import (
    KEPT_DEPENDENCY_TREE,
    KEPT_NOT_PROJECT_ROOT,
    KEPT_SYSTEM_REFERENCED,
)
from smart_drive.core.system_refs import SystemRef, SystemRefIndex


class CleanReportCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_clean_kept_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.write("proj/package.json")
        self.write("proj/dist/bundle.js")  # the one real item
        self.write("proj/node_modules/pkg/dist/index.js")  # installed package
        self.write("lib/build/out.o")  # no project file beside it
        self.write("Arduno/IDE/IDE.exe", "MZ")
        self.write("Arduno/IDE/__pycache__/m.pyc")  # inside an installed app
        self.index = SystemRefIndex([
            SystemRef(kind="installed_app", source='installed app "Arduino IDE"', target=str(self.root / "Arduno" / "IDE" / "IDE.exe")),
        ])

    def write(self, rel: str, data: str = "x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def run_clean(self, *args: str) -> Tuple[int, str]:
        out = io.StringIO()
        with patch.object(SystemRefIndex, "collect", return_value=self.index):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["clean", "--root", str(self.root), *args])
        return code, out.getvalue()

    def run_json(self, *args: str) -> Dict[str, Any]:
        code, out = self.run_clean("--json", *args)
        self.assertEqual(code, 0, out)
        return json.loads(out)


class TestJsonReport(CleanReportCase):
    def test_the_report_counts_what_was_kept_by_reason(self) -> None:
        report = self.run_json("--tier", "2")
        self.assertEqual(report["junk_count"], 1)
        self.assertEqual(
            report["kept"],
            {
                "total": 3,
                "by_reason": {KEPT_DEPENDENCY_TREE: 1, KEPT_SYSTEM_REFERENCED: 1, KEPT_NOT_PROJECT_ROOT: 1},
            },
        )

    def test_the_report_lists_the_folders_the_system_depends_on(self) -> None:
        report = self.run_json("--tier", "2")
        (entry,) = report["protected_by_system"]
        self.assertEqual(Path(entry["path"]), self.root / "Arduno")
        self.assertEqual(entry["kept_items"], 1)
        self.assertEqual(entry["refs"], [{"kind": "installed_app", "source": 'installed app "Arduino IDE"'}])
        self.assertIn("Arduino IDE", entry["used_by"])

    def test_a_drive_with_nothing_kept_reports_zero(self) -> None:
        shutil.rmtree(self.root / "lib")
        shutil.rmtree(self.root / "Arduno")
        shutil.rmtree(self.root / "proj" / "node_modules")
        report = self.run_json("--tier", "2")
        self.assertEqual(report["kept"], {"total": 0, "by_reason": {}})
        self.assertEqual(report["protected_by_system"], [])

    def test_the_apply_report_carries_it_too_and_the_kept_files_are_untouched(self) -> None:
        report = self.run_json("--tier", "2", "--apply")
        self.assertEqual(report["kept"]["total"], 3)
        self.assertEqual(report["deleted_count"], 1)
        self.assertFalse((self.root / "proj" / "dist").exists())
        for rel in ("proj/node_modules/pkg/dist/index.js", "lib/build/out.o", "Arduno/IDE/__pycache__/m.pyc"):
            self.assertTrue((self.root / rel).is_file(), rel)


class TestTextReport(CleanReportCase):
    def test_the_text_says_what_was_kept_and_why(self) -> None:
        code, out = self.run_clean("--tier", "2")
        self.assertEqual(code, 0)
        self.assertIn("Found 1 junk items.", out)
        self.assertIn("Kept 3 look-alike item(s) that are not junk:", out)
        self.assertIn("inside dependency folders (node_modules, site-packages...)", out)
        self.assertIn("inside folders that Windows or an installed app depends on: Arduno", out)
        self.assertIn("build/dist/.cache folders with no project file", out)
        self.assertNotIn("tracked by git", out)

    def test_it_is_said_even_when_nothing_is_left_to_clean(self) -> None:
        shutil.rmtree(self.root / "proj" / "dist")
        code, out = self.run_clean("--tier", "2")
        self.assertEqual(code, 0)
        self.assertIn("No system junk files detected", out)
        self.assertIn("Kept 3 look-alike item(s) that are not junk:", out)

    def test_nothing_is_said_when_nothing_was_kept(self) -> None:
        shutil.rmtree(self.root / "lib")
        shutil.rmtree(self.root / "Arduno")
        shutil.rmtree(self.root / "proj" / "node_modules")
        _, out = self.run_clean("--tier", "2")
        self.assertNotIn("Kept ", out)

    def test_the_dry_run_says_that_tier_1_and_2_are_deleted_for_good(self) -> None:
        _, out = self.run_clean("--tier", "2")
        self.assertIn("[DRY RUN] No files were deleted.", out)
        self.assertIn("Tier 1-2 items are deleted permanently (no quarantine); only Tier 3 items can be restored", out)

    def test_with_no_quarantine_everything_is_deleted_for_good(self) -> None:
        _, out = self.run_clean("--tier", "3", "--no-quarantine")
        self.assertIn("--no-quarantine: every item is deleted permanently.", out)


if __name__ == "__main__":
    unittest.main()
