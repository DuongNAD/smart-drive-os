"""tests/test_checkup_junk_step.py - what `checkup` says about junk matches what `clean` would do.

Regression: on a real drive checkup recommended "Clean 4940 safe junk item(s) (2.77 GB)" with the command
`smart-drive clean --tier 2 --apply`. Tier 1-2 is deleted permanently (no quarantine) and those items included
installed package code, so "safe" was untrue and going straight to --apply was the wrong advice. The recommendation
now asks for a dry run first, says that --apply deletes for good, and counts exactly what the dry run lists.
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.cli.main import main as cli_main
from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import KEPT_DEPENDENCY_TREE, JunkDetector
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from tests.helpers import SmartDriveTestCase


class CheckupCase(SmartDriveTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        (self.fake_home / "AppData" / "Roaming").mkdir(parents=True)
        self.fake_temp = self.test_dir / "sys_temp"
        self.fake_temp.mkdir()
        patchers = [
            patch("smart_drive.core.relink._running_processes", return_value=set()),
            patch("smart_drive.core.relink.tempfile.gettempdir", return_value=str(self.fake_temp)),
            patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, drive: Path, rel: str, data: str = "x") -> Path:
        path = drive.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def checkup(self, drive: Path, lang: str = "en") -> Dict[str, Any]:
        return run_checkup(
            root=str(drive),
            lang=lang,
            home=self.fake_home,
            app_data=self.fake_home / "AppData" / "Roaming",
            reader=RegistryReader(tree={}),
            scan_c_caches=False,
        )

    def cli_env(self) -> Dict[str, str]:
        """The home folder and settings folders the CLI reads, pointed at an empty fake home."""
        return {
            "HOME": str(self.fake_home),
            "USERPROFILE": str(self.fake_home),
            "APPDATA": str(self.fake_home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(self.fake_home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.fake_home / "AppData" / "Roaming"),
        }

    def junk_step(self, report: Dict[str, Any]) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = [s for s in report["next_steps"] if s["category"] == "reclaimable_junk"]
        self.assertEqual(len(steps), 1, report["next_steps"])
        return steps[0]


class TestTheRecommendationIsHonest(CheckupCase):
    def setUp(self) -> None:
        super().setUp()
        self.drive = self.create_mock_drive()  # has Thumbs.db, .DS_Store, AppleDouble files: Tier 1 junk

    def test_it_asks_for_a_dry_run_first_and_names_the_apply_command_separately(self) -> None:
        step = self.junk_step(self.checkup(self.drive))
        self.assertEqual(step["command"], "smart-drive clean --tier 2")
        self.assertNotIn("--apply", step["command"])
        self.assertEqual(step["apply_command"], "smart-drive clean --tier 2 --apply")

    def test_it_says_that_apply_deletes_for_good_and_never_calls_it_safe(self) -> None:
        step = self.junk_step(self.checkup(self.drive))
        text = f"{step['title']} {step['description']}".lower()
        self.assertIn("preview", text)
        self.assertIn("for good", text)
        self.assertIn("no quarantine", text)
        self.assertNotIn("safe", text)

    def test_the_vietnamese_text_says_the_same(self) -> None:
        step = self.junk_step(self.checkup(self.drive, lang="vi"))
        text = f"{step['title']} {step['description']}".lower()
        self.assertEqual(step["command"], "smart-drive clean --tier 2")
        self.assertIn("xem trước", text)
        self.assertIn("vĩnh viễn", text)
        self.assertNotIn("an toàn", text)

    def test_the_printed_step_shows_the_preview_and_then_the_follow_up(self) -> None:
        out = io.StringIO()
        with patch.dict(os.environ, self.cli_env()):
            with patch("smart_drive.cli.cmd_checkup.get_scan_summary", return_value={}):
                with contextlib.redirect_stdout(out):
                    code = cli_main(["checkup", "--root", str(self.drive), "--lang", "en"])
        self.assertEqual(code, 0)
        lines = [line.strip() for line in out.getvalue().splitlines()]
        preview = lines.index("-> smart-drive clean --tier 2")
        self.assertEqual(lines[preview + 1], "then, once the list looks right: smart-drive clean --tier 2 --apply")
        self.assertNotIn("-> smart-drive clean --tier 2 --apply", lines)


class TestTheCountIsWhatCleanListsAndTheScoreFollowsIt(CheckupCase):
    def make_drive_of_installed_packages_only(self) -> Path:
        drive = self.test_dir / "packages_only"
        self.write(drive, "proj/package.json")
        self.write(drive, "proj/node_modules/next/dist/server.js")
        self.write(drive, "proj/node_modules/googleapis/build/index.js")
        self.write(drive, "proj/node_modules/@scope/pkg/.cache/model.bin")
        self.write(drive, "py/Lib/site-packages/pkg/build/lib.pyd")
        self.write(drive, "py/Lib/site-packages/foo.egg-info/PKG-INFO")
        self.write(drive, "lib/build/out.o")  # no project file beside it
        return drive

    def test_installed_packages_are_neither_counted_nor_scored_nor_recommended(self) -> None:
        report = self.checkup(self.make_drive_of_installed_packages_only())
        self.assertEqual(report["junk"]["tier_1_2_count"], 0)
        self.assertEqual(report["junk"]["reclaimable_bytes"], 0)
        self.assertEqual(report["penalties"]["reclaimable_junk"], 0)
        self.assertNotIn("reclaimable_junk", [s["category"] for s in report["next_steps"]])

    def test_what_was_kept_is_reported_by_reason(self) -> None:
        report = self.checkup(self.make_drive_of_installed_packages_only())
        self.assertEqual(report["junk"]["kept_count"], 6)
        self.assertEqual(report["junk"]["kept_by_reason"][KEPT_DEPENDENCY_TREE], 5)
        self.assertEqual(report["junk"]["kept_by_reason"]["not_project_root"], 1)

    def test_the_counted_items_are_exactly_the_ones_a_tier_2_dry_run_lists(self) -> None:
        drive = self.make_drive_of_installed_packages_only()
        self.write(drive, "proj/dist/bundle.js")
        self.write(drive, "proj/__pycache__/m.pyc")
        self.write(drive, "proj/.pytest_cache/state")
        report = self.checkup(drive)
        listed = JunkDetector(str(drive), max_tier=JunkTier.TIER_2_DEV_CACHE, system_refs=SystemRefIndex([])).find_junk()
        self.assertEqual(report["junk"]["tier_1_2_count"], len(listed))
        self.assertEqual(report["junk"]["tier_1_2_count"], 3)
        self.assertEqual(self.junk_step(report)["command"], "smart-drive clean --tier 2")

    def test_the_text_report_mentions_what_was_kept(self) -> None:
        drive = self.make_drive_of_installed_packages_only()
        self.write(drive, "proj/dist/bundle.js")
        out = io.StringIO()
        with patch.dict(os.environ, self.cli_env()):
            with patch("smart_drive.cli.cmd_checkup.get_scan_summary", return_value={}):
                with contextlib.redirect_stdout(out):
                    cli_main(["checkup", "--root", str(drive), "--lang", "en"])
        self.assertIn("Tier 1-2 Junk:      1 item(s)", out.getvalue())
        self.assertIn("Kept, not junk:     6 item(s)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
