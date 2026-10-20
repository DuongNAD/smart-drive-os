"""tests/test_git_no_fsmonitor.py - smart-drive's own git commands never start a git fsmonitor daemon.

Regression: a repository with core.fsmonitor=true gets a background `git fsmonitor--daemon` at the first git command
that refreshes its index (status, ls-files, diff, check-ignore, rm ...). The daemon keeps a handle on the work tree,
and on Windows that makes every rename of the repository's folder, or of a folder above it, fail with
[WinError 5] Access is denied. `relink`, `repos`, `gitignore`, `sentinel` and the junk scan all run git in the repositories
they inspect, so a drive they had just looked at could no longer be organized. Every git command smart-drive runs now
carries `-c core.fsmonitor=false`.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, List
from unittest.mock import patch

from smart_drive.core import gitignore, junk_detector, relink, repos, sentinel
from smart_drive.core.git_locks import NO_FSMONITOR
from tests.fsmonitor_helpers import (
    GIT,
    daemon_is_watching,
    force_remove,
    make_repository,
    start_daemon,
    stop_daemon,
)


class RecordingRun:
    """Stands in for subprocess.run: records every command and answers with an empty success."""

    def __init__(self, binary: bool = False) -> None:
        self.commands: List[List[str]] = []
        self._empty: Any = b"" if binary else ""

    def __call__(self, command: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        self.commands.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, stdout=self._empty, stderr=self._empty)

    def assert_every_command_disables_the_daemon(self, test: unittest.TestCase) -> None:
        test.assertTrue(self.commands, "no git command was run")
        for command in self.commands:
            with test.subTest(command=command):
                test.assertEqual(command[0].replace("\\", "/").rsplit("/", 1)[-1].lower().split(".")[0], "git")
                test.assertEqual(command[1:3], ["-c", "core.fsmonitor=false"], f"git must start with it: {command}")


class TestEveryGitCommandDisablesTheDaemon(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_nofsm_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, onerror=force_remove)

    def test_the_shared_flag_is_the_documented_config_override(self) -> None:
        self.assertEqual(NO_FSMONITOR, ("-c", "core.fsmonitor=false"))

    def test_repos_inspection_and_version_probe(self) -> None:
        run = RecordingRun()
        with patch.object(repos.subprocess, "run", run), patch.object(repos.shutil, "which", return_value="git"):
            repos.inspect_repository(str(self.root))
            repos.git_version()
        self.assertGreaterEqual(len(run.commands), 6)  # rev-parse, config, status, rev-list, for-each-ref, count-objects
        run.assert_every_command_disables_the_daemon(self)

    def test_gitignore_helper(self) -> None:
        (self.root / ".git").mkdir()
        run = RecordingRun()
        with patch.object(gitignore.subprocess, "run", run):
            gitignore.detect_stacks(str(self.root))  # asks `git ls-files`
            gitignore._git(str(self.root), "status", "--porcelain")
        self.assertGreaterEqual(len(run.commands), 2)
        run.assert_every_command_disables_the_daemon(self)

    def test_junk_detector_tracked_files_check(self) -> None:
        run = RecordingRun(binary=True)
        with patch.object(junk_detector.shutil, "which", return_value="git"), patch.object(
            junk_detector.subprocess, "run", run
        ):
            junk_detector._git_tracks(str(self.root))
            junk_detector._git_tracks(str(self.root), "dist")
        self.assertEqual(len(run.commands), 2)
        run.assert_every_command_disables_the_daemon(self)
        self.assertIn("--literal-pathspecs", run.commands[0])  # the rest of the command is as it was
        self.assertEqual(run.commands[0][-2:], ["ls-files", "-z"])

    def test_relink_worktree_repair(self) -> None:
        run = RecordingRun()
        with patch.object(relink.subprocess, "run", run):
            self.assertTrue(relink._run_git_repair("git", self.root, ["../wt"], relink.RelinkReport()))
        run.assert_every_command_disables_the_daemon(self)
        self.assertEqual(run.commands[0][3:], ["-C", str(self.root), "worktree", "repair", "../wt"])

    def test_sentinel_git_status(self) -> None:
        (self.root / "proj" / ".git").mkdir(parents=True)
        run = RecordingRun()
        with patch.object(sentinel.subprocess, "run", run):
            sentinel.check_git_repositories(str(self.root))
        self.assertEqual(len(run.commands), 1)
        run.assert_every_command_disables_the_daemon(self)
        self.assertEqual(run.commands[0][3:], ["status", "--porcelain", "-b"])


@unittest.skipIf(GIT is None, "git executable not found on PATH")
class TestSmartDriveStartsNoDaemon(unittest.TestCase):
    """With real git: a repository that enables the daemon is read by every helper and keeps no daemon afterwards."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_nofsm_git_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, onerror=force_remove)
        self.repo = make_repository(self.root / "repo")
        self.addCleanup(stop_daemon, self.repo)  # runs before the folder is removed

    def test_reading_the_repository_leaves_no_daemon_behind(self) -> None:
        # The control: a plain `git status` does start one here. Where it does not (git without
        # fsmonitor--daemon, platforms that cannot run it) there is nothing to guard against.
        if not start_daemon(self.repo):
            self.skipTest("this git does not start an fsmonitor daemon on this platform")
        stop_daemon(self.repo)
        deadline = time.monotonic() + 10
        while daemon_is_watching(self.repo) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(daemon_is_watching(self.repo))

        repos.inspect_repository(str(self.repo))
        gitignore._git(str(self.repo), "status", "--porcelain")
        gitignore._git(str(self.repo), "ls-files")
        junk_detector._git_tracks(str(self.repo))
        junk_detector._git_tracks(str(self.repo), "a.txt")
        sentinel.check_git_repositories(str(self.root))
        relink._run_git_repair(shutil.which("git") or "git", self.repo, [], relink.RelinkReport())

        self.assertFalse(daemon_is_watching(self.repo), "a smart-drive git command started the fsmonitor daemon")


if __name__ == "__main__":
    unittest.main()
