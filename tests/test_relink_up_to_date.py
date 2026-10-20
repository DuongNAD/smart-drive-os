"""tests/test_relink_up_to_date.py - relink lists only what it really changed.

Running relink again for a move that was already relinked finds nothing to do. The report must then be empty:
no Python venv "updated", no console-script warning and no git worktree "repaired", in a dry run and in the real
run alike, so that the command ends with "Nothing left to change" instead of claiming work that did not happen.

100% isolated: every test uses a fake home and fake AppData inside a temporary directory and never reads or
writes real coding agent configuration files on the host machine.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from typing import Dict, List, Sequence, Tuple
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.relink import AgentFile, RelinkReport, relink
import smart_drive.core.relink as relink_core
from tests.helpers import SmartDriveTestCase


class TestRelinkUpToDate(SmartDriveTestCase):
    """A venv or a git worktree is listed only when relink really rewrote (or, in a dry run, would rewrite) it."""

    def setUp(self) -> None:
        super().setUp()
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
        # The suite itself may run inside a coding agent, which --wait would then skip
        self._caller_patcher = patch("smart_drive.cli.cmd_relink.calling_agents", return_value=set())
        self._caller_patcher.start()

    def tearDown(self) -> None:
        if hasattr(self, "_caller_patcher"):
            self._caller_patcher.stop()
        if hasattr(self, "_temp_patcher"):
            self._temp_patcher.stop()
        if hasattr(self, "_proc_patcher"):
            self._proc_patcher.stop()
        super().tearDown()

    def _cli_env(self) -> Dict[str, str]:
        return {
            "USERPROFILE": str(self.fake_home),
            "HOME": str(self.fake_home),
            "APPDATA": str(self.fake_appdata),
            "LOCALAPPDATA": str(self.fake_home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.fake_appdata),
            "TMP": str(self.fake_temp),
            "TEMP": str(self.fake_temp),
            "TMPDIR": str(self.fake_temp),
            "CONSTELLATION_HOME": "",
            "ANTIGRAVITY_HOME": "",
            "GEMINI_HOME": "",
        }

    def _cli(self, argv: List[str]) -> Tuple[int, str]:
        """Runs `smart-drive <argv>` in this process against the fake home; returns (exit code, stdout)."""
        out = io.StringIO()
        with patch.dict(os.environ, self._cli_env()), contextlib.redirect_stdout(out):
            rc = cli_main(argv)
        return rc, out.getvalue()

    def _relink(self, moves: Sequence[Tuple[str, str]], dry_run: bool = False) -> RelinkReport:
        return relink(list(moves), dry_run=dry_run, home=self.fake_home, app_data=self.fake_appdata)

    @staticmethod
    def _make_venv(venv_dir: Path, pth_lines: Sequence[str], pth_name: str = "_proj.pth") -> Path:
        """A virtualenv skeleton with one .pth file listing pth_lines; returns that .pth file."""
        if sys.platform == "win32":
            sp_dir = venv_dir / "Lib" / "site-packages"
        else:
            sp_dir = venv_dir / "lib" / "python3.11" / "site-packages"
        sp_dir.mkdir(parents=True, exist_ok=True)
        (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        pth_file = sp_dir / pth_name
        pth_file.write_text("".join(f"{line}\n" for line in pth_lines), encoding="utf-8")
        return pth_file

    @staticmethod
    def _console_script_warnings(report: RelinkReport) -> List[str]:
        return [w for w in report.warnings if "console scripts" in w]

    def test_01_relinking_a_venv_twice_lists_it_only_the_first_time(self) -> None:
        old_proj = self.test_dir / "my_pkg"
        new_proj = self.test_dir / "03_Development_Projects" / "my_pkg"
        venv_dir = new_proj / ".venv"
        pth_file = self._make_venv(venv_dir, ["import sys; pass", str(old_proj / "src")])
        moves = [(str(old_proj), str(new_proj))]

        first = self._relink(moves)
        self.assertEqual(first.venvs, [str(venv_dir)])
        self.assertEqual(first.agents, {"Python venv": 1})
        self.assertEqual(len(self._console_script_warnings(first)), 1)
        self.assertEqual(pth_file.read_text(encoding="utf-8").splitlines()[-1], str(new_proj / "src"))

        second = self._relink(moves)
        self.assertEqual(second.venvs, [])
        self.assertEqual(second.updated, [])
        self.assertEqual(second.warnings, [])
        self.assertFalse(second.changed)
        payload = second.to_dict()
        self.assertEqual((payload["venvs"], payload["warnings"], payload["agents"]), ([], [], {}))

    def test_02_dry_run_and_real_run_agree_on_a_tree_that_is_already_relinked(self) -> None:
        old_proj = self.test_dir / "my_pkg"
        new_proj = self.test_dir / "03_Development_Projects" / "my_pkg"
        self._make_venv(new_proj / ".venv", [str(new_proj / "src")])
        self._make_venv(new_proj / "tools" / "venv", [str(new_proj / "tools")], pth_name="_tools.pth")
        moves = [(str(old_proj), str(new_proj))]

        dry = self._relink(moves, dry_run=True)
        real = self._relink(moves)

        for report in (dry, real):
            self.assertEqual(report.venvs, [])
            self.assertEqual(report.worktrees, [])
            self.assertEqual(report.warnings, [])
            self.assertFalse(report.changed)
        self.assertEqual({**dry.to_dict(), "dry_run": False}, real.to_dict())

    def test_03_a_venv_without_a_path_to_rewrite_is_not_listed(self) -> None:
        old_proj = self.test_dir / "plain_pkg"
        new_proj = self.test_dir / "03_Development_Projects" / "plain_pkg"
        self._make_venv(old_proj / ".venv", ["import sys; pass", str(self.test_dir / "elsewhere")])
        self._make_venv(old_proj / "tools" / "venv", [str(old_proj / "tools")], pth_name="_tools.pth")
        moves = [(str(old_proj), str(new_proj))]
        listed = str(new_proj / "tools" / "venv")
        skipped = str(new_proj / ".venv")

        preview = self._relink(moves, dry_run=True)  # the folder is still where it was
        new_proj.parent.mkdir(parents=True)
        os.rename(old_proj, new_proj)
        real = self._relink(moves)

        for report in (preview, real):
            self.assertEqual(report.venvs, [listed])
            self.assertEqual(len(self._console_script_warnings(report)), 1)
            self.assertFalse(any(skipped in w for w in report.warnings), report.warnings)

    def test_04_a_venv_whose_file_could_not_be_written_is_not_listed(self) -> None:
        old_proj = self.test_dir / "locked_pkg"
        new_proj = self.test_dir / "03_Development_Projects" / "locked_pkg"
        pth_file = self._make_venv(new_proj / ".venv", [str(old_proj / "src")])

        def refuse(af: AgentFile, content: bytes, report: RelinkReport) -> bool:
            report.errors.append({"file": str(af.path), "error": "denied"})
            return False

        with patch.object(relink_core, "_write_agent_file", side_effect=refuse):
            report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual([e["error"] for e in report.errors], ["denied"])
        self.assertEqual(report.venvs, [])
        self.assertEqual(report.updated, [])
        self.assertEqual(self._console_script_warnings(report), [])
        # The file is untouched, so a later run still has something to do
        self.assertIn(str(old_proj / "src"), pth_file.read_text(encoding="utf-8"))

    def test_05_cli_relink_again_says_there_is_nothing_left_to_change(self) -> None:
        drive = self.test_dir / "drive"
        old_proj = drive / "my_pkg"
        new_proj = drive / "03_Development_Projects" / "my_pkg"
        venv_dir = new_proj / ".venv"
        self._make_venv(venv_dir, [str(old_proj / "src")])

        rc, out = self._cli(["relink", str(old_proj), str(new_proj), "--root", str(drive)])
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Python venv updated: {venv_dir}", out)
        self.assertIn("console scripts", out)
        self.assertNotIn("Nothing left to change", out)

        # The menu's [11]: a dry run, then the real run, both for the latest journaled move
        for argv in (["relink", "--root", str(drive), "--dry-run"], ["relink", "--root", str(drive)]):
            rc, out = self._cli(argv)
            self.assertEqual(rc, 0, out)
            self.assertIn("Nothing left to change: all coding agent paths are up to date.", out)
            self.assertNotIn("Python venv", out)
            self.assertNotIn("console scripts", out)

        rc, out = self._cli(["relink", "--root", str(drive), "--json"])
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertEqual((payload["venvs"], payload["worktrees"], payload["warnings"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
