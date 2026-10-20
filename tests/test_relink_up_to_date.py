"""tests/test_relink_up_to_date.py - relink lists only what it really changed.

Running relink again for a move that was already relinked finds nothing to do. The report must then be empty:
no Python venv "updated", no console-script warning and no git worktree "repaired", in a dry run and in the real
run alike, so that the command ends with "Nothing left to change" instead of claiming work that did not happen.

It also asks to close or restart a coding agent only when one of that agent's own config files changes: a venv,
an editable install or a shortcut is no agent's file.

100% isolated: every test uses a fake home and fake AppData inside a temporary directory and never reads or
writes real coding agent configuration files on the host machine.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from typing import Any, Dict, List, Sequence, Tuple
from unittest.mock import patch

from smart_drive.cli.cmd_relink import print_relink
from smart_drive.cli.main import main as cli_main
from smart_drive.core.relink import AgentFile, NOT_AGENT_CONFIGS, PathMapper, RelinkReport, relink
import smart_drive.core.relink as relink_core
from tests.helpers import SmartDriveTestCase

NEEDS_GIT = unittest.skipUnless(shutil.which("git"), "git is required for the worktree repair tests")


class TestRelinkUpToDate(SmartDriveTestCase):
    """A venv or a git worktree is listed only when relink really rewrote (or, in a dry run, would rewrite) it."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        # macOS looks for AppData under <home>/Library/Application Support, whatever APPDATA says
        self.fake_appdata = (
            self.fake_home / "Library" / "Application Support"
            if sys.platform == "darwin"
            else self.fake_home / "AppData" / "Roaming"
        )
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
    def _file_uri(path: Path) -> str:
        """The file URI under which VS Code remembers a folder."""
        if sys.platform == "win32":
            return "file:///" + path.as_posix().replace(":", "%3A", 1)
        return "file://" + path.as_posix()

    def _vscode_remembers(self, *folders: Path) -> Path:
        """VS Code's storage.json in the fake AppData, with the given folders among its open ones; returns it."""
        storage = self.fake_appdata / "Code" / "User" / "globalStorage" / "storage.json"
        storage.parent.mkdir(parents=True, exist_ok=True)
        remembered = [{"folderUri": self._file_uri(folder)} for folder in folders]
        payload = {"backupWorkspaces": {"folders": remembered, "workspaces": []}}
        storage.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return storage

    @staticmethod
    def _console_script_warnings(report: RelinkReport) -> List[str]:
        return [w for w in report.warnings if "console scripts" in w]

    @staticmethod
    def _git(cwd: Path, *args: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )

    def _make_repo(self, repo: Path, worktrees: Sequence[Path] = ()) -> None:
        """A git repository with one commit and a linked worktree at each of the given folders."""
        repo.mkdir(parents=True)
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Test User")
        (repo / "README.md").write_text("# Test\n", encoding="utf-8")
        self._git(repo, "add", "README.md")
        self._git(repo, "commit", "-q", "-m", "initial")
        for index, worktree in enumerate(worktrees):
            self._git(repo, "worktree", "add", "-q", str(worktree), "-b", f"wt{index}")

    def _plain_worktree_layout(self, name: str = "plain") -> Tuple[Path, Path, Path, Path]:
        """The files git keeps for one linked worktree, written by hand (no git needed).

        Returns the repository, the worktree folder, the repository's `gitdir` pointer to the worktree and
        the worktree's `.git` file, which names the repository's admin folder back.
        """
        repo = self.test_dir / f"{name}_repo"
        worktree = self.test_dir / f"{name}_wt"
        admin = repo / ".git" / "worktrees" / "wt"
        admin.mkdir(parents=True)
        worktree.mkdir()
        pointer = admin / "gitdir"
        pointer.write_text(f"{worktree / '.git'}\n", encoding="utf-8")
        gitfile = worktree / ".git"
        gitfile.write_text(f"gitdir: {admin}\n", encoding="utf-8")
        return repo, worktree, pointer, gitfile

    @staticmethod
    def _fake_git(
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        rewrites: Sequence[Path] = (),
    ) -> Tuple[Any, List[List[str]]]:
        """A stand-in for subprocess.run that behaves like `git worktree repair`: it may rewrite the given files
        and print what it did. Returns (the patch to apply, the list of commands it was called with)."""
        calls: List[List[str]] = []

        def run(cmd: Sequence[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
            calls.append(list(cmd))
            for path in rewrites:
                path.write_bytes(b"rewritten by git\n")
            return subprocess.CompletedProcess(list(cmd), returncode, stdout, stderr)

        return patch.object(relink_core.subprocess, "run", side_effect=run), calls

    def test_01_relinking_a_venv_twice_lists_it_only_the_first_time(self) -> None:
        old_proj = self.test_dir / "my_pkg"
        new_proj = self.test_dir / "03_Development_Projects" / "my_pkg"
        venv_dir = new_proj / ".venv"
        pth_file = self._make_venv(venv_dir, ["import sys; pass", str(old_proj / "src")])
        moves = [(str(old_proj), str(new_proj))]

        first = self._relink(moves)
        self.assertEqual(first.venvs, [str(venv_dir)])
        self.assertEqual(first.agents, {"Python venv": 1})
        self.assertEqual(first.warnings, [])  # no console script in it that could not be repointed
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

        preview = self._relink(moves, dry_run=True)  # the folder is still where it was
        new_proj.parent.mkdir(parents=True)
        os.rename(old_proj, new_proj)
        real = self._relink(moves)

        for report in (preview, real):
            self.assertEqual(report.venvs, [listed])
            self.assertEqual(report.warnings, [])

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
        self.assertNotIn("may still start the old path", out)
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

    @NEEDS_GIT
    def test_06_relinking_a_moved_repository_twice_repairs_its_worktrees_only_once(self) -> None:
        old_repo = self.test_dir / "repo"
        new_repo = self.test_dir / "03_Development_Projects" / "repo"
        outside = self.test_dir / "outside_wt"
        self._make_repo(old_repo, [old_repo / ".claude" / "worktrees" / "wt", outside])
        new_repo.parent.mkdir(parents=True)
        os.rename(old_repo, new_repo)
        moves = [(str(old_repo), str(new_repo))]

        first = self._relink(moves)
        self.assertEqual(first.worktrees, [str(new_repo)])
        self.assertEqual(first.warnings, [])
        for worktree in (new_repo / ".claude" / "worktrees" / "wt", outside):
            self._git(worktree, "status", "--porcelain")  # fails while the worktree is still cut off

        second = self._relink(moves)
        self.assertEqual(second.worktrees, [])
        self.assertEqual(second.warnings, [])
        self.assertFalse(second.changed)
        self.assertEqual(second.to_dict()["worktrees"], [])

    @NEEDS_GIT
    def test_07_dry_run_and_real_run_agree_on_the_worktrees_of_a_moved_repository(self) -> None:
        old_repo = self.test_dir / "repo"
        new_repo = self.test_dir / "03_Development_Projects" / "repo"
        self._make_repo(old_repo, [old_repo / ".claude" / "worktrees" / "wt"])
        new_repo.parent.mkdir(parents=True)
        os.rename(old_repo, new_repo)
        moves = [(str(old_repo), str(new_repo))]

        preview = self._relink(moves, dry_run=True)  # moved, not relinked yet
        first = self._relink(moves)
        self.assertEqual(preview.worktrees, [str(new_repo)])
        self.assertEqual(first.worktrees, preview.worktrees)

        dry, real = self._relink(moves, dry_run=True), self._relink(moves)
        for report in (dry, real):
            self.assertEqual(report.worktrees, [])
            self.assertFalse(report.changed)
        self.assertEqual({**dry.to_dict(), "dry_run": False}, real.to_dict())

    @NEEDS_GIT
    def test_08_a_moved_linked_worktree_is_listed_only_the_first_time(self) -> None:
        main = self.test_dir / "main_repo"
        old_wt = self.test_dir / "feature_wt"
        new_wt = self.test_dir / "03_Development_Projects" / "feature_wt"
        self._make_repo(main, [old_wt])
        new_wt.parent.mkdir(parents=True)
        os.rename(old_wt, new_wt)
        moves = [(str(old_wt), str(new_wt))]

        first = self._relink(moves)
        self.assertEqual(first.worktrees, [str(new_wt)])
        listing = self._git(main, "worktree", "list", "--porcelain").stdout.replace("\\", "/")
        self.assertIn(new_wt.as_posix(), listing)

        second = self._relink(moves)
        self.assertEqual(second.worktrees, [])
        self.assertFalse(second.changed)

    @NEEDS_GIT
    def test_09_cli_relink_again_after_a_move_says_there_is_nothing_left_to_change(self) -> None:
        # What the menu's [11] does after folders were moved: the repositories and venvs sit one folder down
        drive = self.test_dir / "drive"
        old_container = drive / "project"
        new_container = drive / "03_Development_Projects" / "project"
        self._make_repo(old_container / "openclaw", [old_container / "openclaw" / ".claude" / "worktrees" / "wt"])
        self._make_venv(old_container / "research" / "venv", [str(old_container / "research" / "src")])
        new_container.parent.mkdir(parents=True)
        os.rename(old_container, new_container)

        rc, out = self._cli(["relink", str(old_container), str(new_container), "--root", str(drive)])
        self.assertEqual(rc, 0, out)
        self.assertIn(f"git worktrees repaired: {new_container / 'openclaw'}", out)
        self.assertIn(f"Python venv updated: {new_container / 'research' / 'venv'}", out)
        self.assertNotIn("Nothing left to change", out)

        for argv in (["relink", "--root", str(drive), "--dry-run"], ["relink", "--root", str(drive)]):
            rc, out = self._cli(argv)
            self.assertEqual(rc, 0, out)
            self.assertIn("Nothing left to change: all coding agent paths are up to date.", out)
            for claim in ("repaired", "Python venv", "console scripts"):
                self.assertNotIn(claim, out)

        rc, out = self._cli(["relink", "--root", str(drive), "--json"])
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertEqual(
            (payload["worktrees"], payload["venvs"], payload["warnings"], payload["errors"]), ([], [], [], [])
        )

    def test_10_git_that_repairs_nothing_lists_nothing(self) -> None:
        repo, worktree, _pointer, _gitfile = self._plain_worktree_layout()
        patcher, calls = self._fake_git()
        report = RelinkReport()
        with patcher:
            ok = relink_core._run_git_repair("git", repo, [str(worktree)], report)

        self.assertTrue(ok)
        self.assertEqual(report.worktrees, [])
        self.assertEqual(report.warnings, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-3:], ["worktree", "repair", str(worktree)])

    def test_11_a_repair_line_from_git_is_a_repair(self) -> None:
        # git prints "repair: <what>: <path>" on stderr (stdout in some versions) for each file it fixes
        for channel in ("stdout", "stderr"):
            with self.subTest(channel=channel):
                repo, worktree, _pointer, _gitfile = self._plain_worktree_layout(channel)
                line = f"repair: gitdir incorrect: {repo}/.git/worktrees/wt/gitdir\n"
                patcher, _calls = self._fake_git(**{channel: line})
                report = RelinkReport()
                with patcher:
                    self.assertTrue(relink_core._run_git_repair("git", repo, [str(worktree)], report))
                self.assertEqual(report.worktrees, [str(repo)])

    def test_12_a_rewritten_gitdir_file_is_a_repair_whatever_git_prints(self) -> None:
        # git translates its messages and older versions print nothing, so the files are compared, too
        said = {"silent": "", "translated": "réparation : gitdir incorrect\n"}
        for what, index in (("the repository's pointer", 0), ("the worktree's .git file", 1)):
            for how, stderr in said.items():
                with self.subTest(rewritten=what, git=how):
                    repo, worktree, pointer, gitfile = self._plain_worktree_layout(f"{index}{how}")
                    patcher, _calls = self._fake_git(stderr=stderr, rewrites=[(pointer, gitfile)[index]])
                    report = RelinkReport()
                    with patcher:
                        self.assertTrue(relink_core._run_git_repair("git", repo, [str(worktree)], report))
                    self.assertEqual(report.worktrees, [str(repo)])

    def test_13_a_failing_git_lists_nothing_and_warns(self) -> None:
        repo, worktree, _pointer, _gitfile = self._plain_worktree_layout()
        patcher, _calls = self._fake_git(returncode=1, stderr="error: not a valid path: nowhere\n")
        report = RelinkReport()
        with patcher:
            ok = relink_core._run_git_repair("git", repo, [str(worktree)], report)

        self.assertFalse(ok)
        self.assertEqual(report.worktrees, [])
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("not a valid path: nowhere", report.warnings[0])

    def test_14_pointing_the_gitfile_at_the_moved_repository_is_a_repair(self) -> None:
        # The worktree's .git file still names the old repository folder; relink rewrites it before git runs
        old_main = self.test_dir / "old_main"
        repo, worktree, _pointer, gitfile = self._plain_worktree_layout("moved_main")
        admin = repo / ".git" / "worktrees" / "wt"
        gitfile.write_text(f"gitdir: {old_main / '.git' / 'worktrees' / 'wt'}\n", encoding="utf-8")
        mapper = PathMapper([(str(old_main), str(repo))])
        patcher, calls = self._fake_git()
        first, second = RelinkReport(), RelinkReport()
        with patcher, patch("smart_drive.core.relink.shutil.which", return_value="git"):
            relink_core._repair_worktrees([str(worktree)], mapper, first)
            relink_core._repair_worktrees([str(worktree)], mapper, second)

        self.assertEqual(first.worktrees, [str(worktree)])
        self.assertEqual(gitfile.read_text(encoding="utf-8").strip().replace("\\", "/"), f"gitdir: {admin}".replace("\\", "/"))
        self.assertEqual(second.worktrees, [])
        self.assertEqual(len(calls), 2)  # git ran both times, and had nothing to say the second time

    def test_15_a_worktree_repaired_through_its_repository_is_not_repaired_again(self) -> None:
        repo = self.test_dir / "nested_repo"
        worktree = repo / "feature"  # not a hidden folder, so that it is scanned as a candidate of its own
        admin = repo / ".git" / "worktrees" / "feature"
        admin.mkdir(parents=True)
        worktree.mkdir()
        (admin / "gitdir").write_text(f"{worktree / '.git'}\n", encoding="utf-8")
        (worktree / ".git").write_text(f"gitdir: {admin}\n", encoding="utf-8")
        mapper = PathMapper([(str(self.test_dir / "was_here"), str(repo))])
        patcher, calls = self._fake_git()
        report = RelinkReport()
        with patcher, patch("smart_drive.core.relink.shutil.which", return_value="git"):
            relink_core._repair_worktrees([str(repo)], mapper, report)

        self.assertEqual(len(calls), 1)
        self.assertEqual(report.worktrees, [])

    def test_16_a_dry_run_with_nothing_to_apply_does_not_ask_to_close_apps(self) -> None:
        drive = self.test_dir / "drive"
        old_proj = drive / "my_pkg"
        new_proj = drive / "03_Development_Projects" / "my_pkg"
        self._make_venv(new_proj / ".venv", [str(old_proj / "src")])
        self._vscode_remembers(old_proj)  # a coding agent's own file to change, which a venv alone is not
        move = ["relink", str(old_proj), str(new_proj), "--root", str(drive)]

        with patch("smart_drive.core.relink._running_processes", return_value={"Code.exe"}):
            rc, pending = self._cli(move + ["--dry-run"])
            self.assertEqual(rc, 0, pending)
            # there is something to apply; relink applies by default, so --apply is not the way to do it
            self.assertIn("Close these before running it without --dry-run: VS Code", pending)
            self.assertNotIn("--apply", pending)

            rc, applied = self._cli(move)
            self.assertEqual(rc, 0, applied)
            rc, idle_dry = self._cli(["relink", "--root", str(drive), "--dry-run"])
            self.assertEqual(rc, 0, idle_dry)
            rc, idle_real = self._cli(["relink", "--root", str(drive)])
            self.assertEqual(rc, 0, idle_real)

        # Nothing to apply: the dry run says what the real run says, and asks for nothing
        done = "  ✓ Nothing left to change: all coding agent paths are up to date.\n"
        self.assertTrue(idle_dry.endswith(done), idle_dry)
        self.assertNotIn("Close these", idle_dry)
        self.assertEqual(idle_dry.split("\n", 1)[1], idle_real.split("\n", 1)[1])

    def test_17_a_venv_alone_never_asks_to_close_or_restart_an_agent(self) -> None:
        drive = self.test_dir / "drive"
        old_proj = drive / "my_pkg"
        new_proj = drive / "03_Development_Projects" / "my_pkg"
        venv_dir = new_proj / ".venv"
        self._make_venv(venv_dir, [str(old_proj / "src")])
        move = ["relink", str(old_proj), str(new_proj), "--root", str(drive)]

        with patch("smart_drive.core.relink._running_processes", return_value={"Code.exe", "claude.exe"}):
            rc, preview = self._cli(move + ["--dry-run"])
            self.assertEqual(rc, 0, preview)
            rc, applied = self._cli(move)
            self.assertEqual(rc, 0, applied)

        # No coding agent holds a venv's files: there is nothing to close before and nothing to restart after
        self.assertIn(f"Python venv would be updated: {venv_dir}", preview)
        self.assertNotIn("Close these", preview)
        self.assertIn(f"Python venv updated: {venv_dir}", applied)
        for said in ("Restart the coding agents", "If one of them was open", "is running"):
            self.assertNotIn(said, applied)

    def test_18_only_the_agents_whose_own_files_change_are_asked_to_close_and_restart(self) -> None:
        drive = self.test_dir / "drive"
        old_proj = drive / "my_pkg"
        new_proj = drive / "03_Development_Projects" / "my_pkg"
        self._make_venv(new_proj / ".venv", [str(old_proj / "src")])
        storage = self._vscode_remembers(old_proj)  # of the two apps that run, only VS Code remembers the old folder
        move = ["relink", str(old_proj), str(new_proj), "--root", str(drive)]

        with patch("smart_drive.core.relink._running_processes", return_value={"Code.exe", "claude.exe"}):
            rc, preview = self._cli(move + ["--dry-run"])
            self.assertEqual(rc, 0, preview)
            rc, applied = self._cli(move)
            self.assertEqual(rc, 0, applied)

        closing = [line for line in preview.splitlines() if "Close these" in line]
        self.assertEqual(closing, ["  ! Close these before running it without --dry-run: VS Code"])
        self.assertIn(self._file_uri(new_proj), storage.read_text(encoding="utf-8"))
        # relink's own warning names the same agent, and the restart advice follows
        running = [line for line in applied.splitlines() if "is running" in line]
        self.assertEqual(len(running), 1, applied)
        self.assertTrue(running[0].startswith("  ! VS Code is running: "), running[0])
        self.assertIn("  Restart the coding agents that are open so they load the new paths", applied)
        self.assertIn("  If one of them was open during the move, run `smart-drive relink` again", applied)

    def test_19_find_apply_dry_run_asks_to_close_the_same_agents_in_the_same_words(self) -> None:
        drive = self.test_dir / "drive"
        old_proj = drive / "my_pkg"
        new_proj = drive / "03_Development_Projects" / "my_pkg"
        (new_proj / "src").mkdir(parents=True)
        self._vscode_remembers(old_proj / "src")  # inside the old folder, so that --find marks the new one with a tick

        with patch("smart_drive.core.relink._running_processes", return_value={"Code.exe", "claude.exe"}):
            rc, out = self._cli(["relink", "--find", "--apply", "--dry-run", "--root", str(drive)])

        self.assertEqual(rc, 0, out)
        self.assertIn(f"✓ {old_proj} -> {new_proj}  (same-name; VS Code)", out)
        closing = [line for line in out.splitlines() if "Close these" in line]
        self.assertEqual(closing, ["  ! Close these before running it without --dry-run: VS Code"])
        self.assertNotIn("before --apply", out)

    def test_20_print_relink_without_relink_only_prints_what_organize_undo_classify_and_paths_print(self) -> None:
        def printed(dry_run: bool, **options: bool) -> str:
            report = RelinkReport(
                dry_run=dry_run,
                updated=[{"file": "f", "agent": "Python venv", "paths": 1}],
                venvs=["v"],
                busy_apps=["Claude Code"],
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                print_relink(report, **options)
            return out.getvalue()

        self.assertIn("  ! Close these before --apply: Claude Code\n", printed(True))
        restart = printed(False)
        self.assertIn("  Restart the coding agents that are open so they load the new paths", restart)
        self.assertIn("  If one of them was open during the move, run `smart-drive relink` again", restart)
        # `smart-drive relink` itself leaves both out: a venv is no coding agent's file
        for dry_run in (True, False):
            text = printed(dry_run, relink_only=True)
            self.assertNotIn("Close these", text)
            self.assertNotIn("Restart the coding agents", text)
            self.assertNotIn("If one of them was open", text)

    def test_21_relink_only_asks_to_close_and_restart_only_for_an_agents_own_files(self) -> None:
        def printed(labels: Sequence[str], dry_run: bool) -> str:
            report = RelinkReport(
                dry_run=dry_run,
                updated=[{"file": "f", "agent": label, "paths": 1} for label in labels],
                busy_apps=["Claude Code", "VS Code"],
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                print_relink(report, relink_only=True)
            return out.getvalue()

        for label in sorted(NOT_AGENT_CONFIGS):
            for dry_run in (True, False):
                with self.subTest(label=label, dry_run=dry_run):
                    text = printed([label], dry_run)
                    self.assertNotIn("Close these", text)
                    self.assertNotIn("Restart the coding agents", text)

        # One agent's own file among them is enough, and then only the busy agents that have one are named
        self.assertIn(
            "  ! Close these before running it without --dry-run: VS Code\n", printed(["Python venv", "VS Code"], True)
        )
        self.assertIn("  Restart the coding agents that are open", printed(["Python venv", "VS Code"], False))


if __name__ == "__main__":
    unittest.main()
