"""tests/test_checkup.py - Unit and CLI tests for `smart-drive checkup` (T15).

100% isolated: uses temporary directories, fake home/AppData, and fake RegistryReader.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import SCORE_WEIGHTS, detect_preferred_lang, run_checkup
from smart_drive.cli.main import main as cli_main
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from tests.helpers import SmartDriveTestCase


class TestCheckup(SmartDriveTestCase):
    """Tests for `run_checkup` and `smart-drive checkup`."""

    def setUp(self) -> None:
        super().setUp()
        self.drive = self.create_mock_drive()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_temp = self.test_dir / "sys_temp"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.fake_appdata.mkdir(parents=True, exist_ok=True)
        self.fake_temp.mkdir(parents=True, exist_ok=True)
        self._proc_patcher = patch("smart_drive.core.relink._running_processes", return_value=set())
        self._proc_patcher.start()
        self._temp_patcher = patch("smart_drive.core.relink.tempfile.gettempdir", return_value=str(self.fake_temp))
        self._temp_patcher.start()

    def tearDown(self) -> None:
        if hasattr(self, "_temp_patcher"):
            self._temp_patcher.stop()
        if hasattr(self, "_proc_patcher"):
            self._proc_patcher.stop()
        super().tearDown()

    def _cli_env(self) -> dict[str, str]:
        return {
            "HOME": str(self.fake_home),
            "USERPROFILE": str(self.fake_home),
            "APPDATA": str(self.fake_appdata),
            "LOCALAPPDATA": str(self.fake_home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.fake_appdata),
        }

    def test_01_run_checkup_aggregates_junk_broken_paths_and_persists_previous_score(self) -> None:
        # 1. Seed Tier 1 junk
        (self.drive / ".DS_Store").write_bytes(b"junk" * 50)
        pycache = self.drive / "03_Development_Projects" / "__pycache__"
        pycache.mkdir(parents=True, exist_ok=True)
        (pycache / "mod.cpython-311.pyc").write_bytes(b"bytecode" * 100)

        # 2. Seed loose unorganized folder at drive root
        loose_dir = self.drive / "my_course_notes"
        loose_dir.mkdir(parents=True, exist_ok=True)
        (loose_dir / "lecture1.md").write_text("# Lecture 1\n", encoding="utf-8")

        # 3. Seed stale IDE entry pointing to moved folder
        moved_new = self.drive / "03_Development_Projects" / "demo_repo"
        (moved_new / "src").mkdir(parents=True, exist_ok=True)
        moved_old = self.drive / "demo_repo"

        def _to_uri(p: Path) -> str:
            if sys.platform == "win32":
                return "file:///" + p.as_posix().replace(":", "%3A", 1)
            return "file://" + p.as_posix()

        code_gs = self.fake_appdata / "Code" / "User" / "globalStorage"
        code_gs.mkdir(parents=True, exist_ok=True)
        (code_gs / "storage.json").write_text(
            json.dumps({"backupWorkspaces": {"folders": [{"folderUri": _to_uri(moved_old)}, {"folderUri": _to_uri(moved_old / "src")}], "workspaces": []}}),
            encoding="utf-8",
        )

        # 4. Seed a quarantine batch
        q_batch = self.drive / ".smart_drive" / "quarantine" / "20261020-010000"
        q_batch.mkdir(parents=True, exist_ok=True)
        (q_batch / "dump.dmp").write_bytes(b"dump" * 256)
        (q_batch / "manifest.json").write_text(
            json.dumps({
                "batch_id": "20261020-010000",
                "created_at": "2026-10-20T01:00:00Z",
                "items": [{"original": str(self.drive / "dump.dmp"), "quarantined": "dump.dmp", "size": 1024, "tier": 3}],
            }),
            encoding="utf-8",
        )

        reader = RegistryReader(tree={})
        rep_vi = run_checkup(
            root=str(self.drive),
            include_dup=False,
            lang="vi",
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
            scan_c_caches=False,
        )

        required_keys = {
            "timestamp",
            "root",
            "lang",
            "score",
            "previous_score",
            "score_delta",
            "comparison_text",
            "weights",
            "penalties",
            "filesystem",
            "health",
            "storage",
            "junk",
            "quarantine",
            "unorganized",
            "broken_paths",
            "protected_by_system",
            "offload_caches",
            "duplicates",
            "next_steps",
        }
        self.assertTrue(required_keys.issubset(rep_vi.keys()))
        self.assertEqual(rep_vi["lang"], "vi")
        self.assertIsNone(rep_vi["previous_score"])
        self.assertEqual(sum(SCORE_WEIGHTS.values()), 100)
        self.assertGreaterEqual(rep_vi["score"], 0)
        self.assertLess(rep_vi["score"], 100)
        self.assertGreaterEqual(rep_vi["junk"]["tier_1_2_count"], 2)
        self.assertEqual(rep_vi["quarantine"]["batch_count"], 1)
        self.assertEqual(rep_vi["unorganized"]["count"], 1)
        self.assertEqual(rep_vi["broken_paths"]["total_broken"], 1)
        self.assertEqual(rep_vi["broken_paths"]["fixable_count"], 1)
        self.assertFalse(rep_vi["duplicates"]["enabled"])

        # Next steps sorted by priority descending (3 steps)
        steps = rep_vi["next_steps"]
        self.assertEqual(len(steps), 3)
        priorities = [s["priority"] for s in steps]
        self.assertEqual(priorities, sorted(priorities, reverse=True))
        self.assertEqual(steps[0]["command"], "smart-drive paths --broken --fix")
        self.assertIn("Sửa", steps[0]["title"])

        # Single checkup.json file persisted in .smart_drive/
        checkup_json_path = self.drive / ".smart_drive" / "checkup.json"
        self.assertTrue(checkup_json_path.is_file())

        # Fix the issues and run a second checkup in English -> score improves and shows Previous X -> now Y
        (self.drive / ".DS_Store").unlink()
        (pycache / "mod.cpython-311.pyc").unlink()
        pycache.rmdir()
        (code_gs / "storage.json").unlink()

        rep_en = run_checkup(
            root=str(self.drive),
            include_dup=True,
            lang="en",
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
            scan_c_caches=False,
        )
        self.assertEqual(rep_en["lang"], "en")
        self.assertEqual(rep_en["previous_score"], rep_vi["score"])
        self.assertGreater(rep_en["score"], rep_vi["score"])
        self.assertEqual(rep_en["score_delta"], rep_en["score"] - rep_vi["score"])
        self.assertIn(f"Previous {rep_vi['score']} → now {rep_en['score']}", rep_en["comparison_text"])
        self.assertTrue(rep_en["duplicates"]["enabled"])

        # Re-run in Vietnamese to verify Vietnamese comparison text
        rep_vi_2 = run_checkup(
            root=str(self.drive),
            include_dup=False,
            lang="vi",
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
            scan_c_caches=False,
        )
        self.assertIn(f"Lần trước {rep_en['score']} → giờ {rep_vi_2['score']}", rep_vi_2["comparison_text"])

    def test_02_cli_checkup_text_and_json_output_and_lang_detection(self) -> None:
        with patch.dict(os.environ, {"SMARTDRIVE_LANG": "vi"}, clear=False):
            self.assertEqual(detect_preferred_lang(None), "vi")
        with patch.dict(os.environ, {"SMARTDRIVE_LANG": "en"}, clear=False):
            self.assertEqual(detect_preferred_lang(None), "en")

        orig_collect = SystemRefIndex.collect
        out_vi = io.StringIO()
        out_en = io.StringIO()
        out_json = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch.object(
                SystemRefIndex,
                "collect",
                side_effect=lambda **kw: orig_collect(home=self.fake_home, reader=RegistryReader(tree={})),
            ),
            patch("smart_drive.cli.cmd_checkup.get_scan_summary", return_value={"discovered_count": 0, "total_reclaimable_bytes": 0}),
        ):
            with contextlib.redirect_stdout(out_vi):
                rc_vi = cli_main(["checkup", "--root", str(self.drive), "--lang", "vi"])
            with contextlib.redirect_stdout(out_en):
                rc_en = cli_main(["checkup", "--root", str(self.drive), "--lang", "en"])
            with contextlib.redirect_stdout(out_json):
                rc_j = cli_main(["checkup", "--root", str(self.drive), "--json"])

        self.assertEqual(rc_vi, 0)
        self.assertEqual(rc_en, 0)
        self.assertEqual(rc_j, 0)
        self.assertIn("Điểm sức khỏe:", out_vi.getvalue())
        self.assertIn("3 việc nên làm tiếp theo:", out_vi.getvalue())
        self.assertIn("Health Score:", out_en.getvalue())
        self.assertIn("Previous ", out_en.getvalue())
        self.assertIn("Top 3 recommended next steps:", out_en.getvalue())
        parsed = json.loads(out_json.getvalue())
        self.assertIn("score", parsed)

    def _remember_folders(self, *folders: Path) -> None:
        """Makes a fake VS Code remember `folders` as recent workspaces."""
        def _to_uri(p: Path) -> str:
            if sys.platform == "win32":
                return "file:///" + p.as_posix().replace(":", "%3A", 1)
            return "file://" + p.as_posix()

        code_gs = self.fake_appdata / "Code" / "User" / "globalStorage"
        code_gs.mkdir(parents=True, exist_ok=True)
        (code_gs / "storage.json").write_text(
            json.dumps({"backupWorkspaces": {"folders": [{"folderUri": _to_uri(f)} for f in folders], "workspaces": []}}),
            encoding="utf-8",
        )

    def _checkup(self) -> dict:
        return run_checkup(
            root=str(self.drive),
            lang="en",
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=RegistryReader(tree={}),
            scan_c_caches=False,
        )

    def test_03_stale_references_elsewhere_on_the_computer_are_listed_but_not_scored(self) -> None:
        """A recent project that no longer exists off the drive cannot be repaired by anything done on the drive."""
        elsewhere = self.test_dir / "projects_off_drive" / "gone_app"
        self._remember_folders(elsewhere)

        rep = self._checkup()

        self.assertEqual(rep["broken_paths"]["total_broken"], 1)
        self.assertEqual(rep["broken_paths"]["on_drive_count"], 0)
        self.assertEqual(rep["penalties"]["broken_paths"], 0)
        self.assertNotIn("broken_paths", [s["category"] for s in rep["next_steps"]])

    def test_04_stale_references_into_the_drive_are_scored_and_recommended(self) -> None:
        elsewhere = self.test_dir / "projects_off_drive" / "gone_app"
        on_drive = self.drive / "demo_repo"  # organized away from the drive root: nothing there any more
        (self.drive / "03_Development_Projects" / "demo_repo" / "src").mkdir(parents=True)
        self._remember_folders(elsewhere, on_drive, on_drive / "src")  # a remembered subfolder makes the move sure

        rep = self._checkup()

        self.assertEqual(rep["broken_paths"]["total_broken"], 2)
        self.assertEqual(rep["broken_paths"]["on_drive_count"], 1)
        self.assertEqual(rep["broken_paths"]["fixable_on_drive_count"], 1)
        self.assertEqual(rep["penalties"]["broken_paths"], 5 + 1 * 3)
        step = next(s for s in rep["next_steps"] if s["category"] == "broken_paths")
        self.assertEqual(step["command"], "smart-drive paths --broken --fix")
        self.assertIn("Repair 1 broken", step["title"])


if __name__ == "__main__":
    unittest.main()
