"""tests/test_git_locks.py - a folder is freed from git's fsmonitor daemons before smart-drive renames it.

Regression: with core.fsmonitor=true the first `git status` in a repository starts a background
`git fsmonitor--daemon` that keeps a handle on the work tree. On Windows that handle makes every rename of the
repository's folder, or of a folder above it, fail with [WinError 5] Access is denied: `organize --apply` could not move
E:\\project (17 repositories, one of them with the daemon) until `git fsmonitor--daemon stop` was run by hand.
Every move now stops the daemons of the repositories inside the folder first, and never fails because of it.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core import git_locks
from smart_drive.core.auto_zoner import AutoZoner, _move
from smart_drive.core.classifier import ClassifierEngine
from smart_drive.core.git_locks import find_work_trees, release_git_locks, watching_repositories
from smart_drive.core.junction import create_directory_junction
from smart_drive.core.relink import after_moves
from smart_drive.core.system_refs import SystemRefIndex
from tests.fsmonitor_helpers import (
    GIT,
    daemon_is_watching,
    force_remove,
    make_repository,
    start_daemon,
    stop_daemon,
)
from tests.helpers import SmartDriveTestCase


def same(left: Iterable[str], right: Iterable[str]) -> bool:
    """Whether two lists of paths name the same folders (case and separators as the platform sees them)."""
    return sorted(os.path.normcase(os.path.normpath(p)) for p in left) == sorted(
        os.path.normcase(os.path.normpath(p)) for p in right
    )


def fake_repo(folder: Path) -> Path:
    """A folder with a `.git` directory: enough for the walk, no git needed."""
    (folder / ".git").mkdir(parents=True, exist_ok=True)
    return folder


class FakeGit:
    """Stands in for git: answers `fsmonitor--daemon status|stop` for a set of watched repositories.

    `on_stop` runs when a daemon is asked to stop, to look at the disk at that moment.
    """

    def __init__(
        self,
        watching: Iterable[Path] = (),
        stop_fails: Iterable[Path] = (),
        on_stop: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.watching = {os.path.normcase(str(p)) for p in watching}
        self.stop_fails = {os.path.normcase(str(p)) for p in stop_fails}
        self.on_stop = on_stop
        self.calls: List[Tuple[str, Tuple[str, ...]]] = []

    def __call__(self, repo: str, args: Sequence[str], timeout: float) -> Tuple[int, str]:
        self.calls.append((repo, tuple(args)))
        key = os.path.normcase(repo)
        if tuple(args) == ("fsmonitor--daemon", "status"):
            if key in self.watching:
                return 0, f"fsmonitor-daemon is watching '{repo}'\n"
            return 1, f"fsmonitor-daemon is not watching '{repo}'\n"
        if tuple(args) == ("fsmonitor--daemon", "stop"):
            if self.on_stop:
                self.on_stop(repo)
            if key in self.stop_fails:
                return 128, "fatal: could not stop\n"
            self.watching.discard(key)
            return 0, ""
        raise AssertionError(f"unexpected git command: {args}")

    def asked(self, verb: str) -> List[str]:
        return [os.path.normcase(os.path.normpath(r)) for r, a in self.calls if a[-1] == verb]


class TestFindWorkTrees(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_locks_walk_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, onerror=force_remove)

    def test_finds_the_folder_itself_and_every_repository_below(self) -> None:
        fake_repo(self.root)
        fake_repo(self.root / "a")
        fake_repo(self.root / "group" / "b")
        (self.root / "plain").mkdir()
        self.assertTrue(same(find_work_trees(str(self.root)), [self.root, self.root / "a", self.root / "group" / "b"]))

    def test_a_git_file_is_a_work_tree_too(self) -> None:
        (self.root / "wt").mkdir()
        (self.root / "wt" / ".git").write_text("gitdir: ../elsewhere/.git/worktrees/wt\n", encoding="utf-8")
        self.assertTrue(same(find_work_trees(str(self.root)), [self.root / "wt"]))

    def test_hidden_folders_are_searched_but_dependency_and_git_trees_are_not(self) -> None:
        fake_repo(self.root / "kept")
        fake_repo(self.root / ".claude" / "worktrees" / "agent")  # where Claude Code keeps its worktrees
        for skipped in ("node_modules/pkg", ".venv/lib/pkg", "venv/pkg", "kept/.git/modules/sub", "target/debug/dep"):
            fake_repo(self.root / skipped)
        self.assertTrue(
            same(find_work_trees(str(self.root)), [self.root / "kept", self.root / ".claude" / "worktrees" / "agent"])
        )

    def test_the_walk_is_bounded(self) -> None:
        for name in "abcde":
            fake_repo(self.root / name)
        self.assertEqual(len(find_work_trees(str(self.root), max_repositories=2)), 2)
        self.assertEqual(len(find_work_trees(str(self.root), max_dirs=3)), 2)  # the root and two of the five

        fake_repo(self.root / "x" / "y" / "z")
        self.assertNotIn(
            os.path.normcase(str(self.root / "x" / "y" / "z")),
            [os.path.normcase(p) for p in find_work_trees(str(self.root), max_depth=2)],
        )
        self.assertIn(
            os.path.normcase(str(self.root / "x" / "y" / "z")),
            [os.path.normcase(p) for p in find_work_trees(str(self.root), max_depth=3)],
        )

    def test_a_deadline_in_the_past_stops_the_walk(self) -> None:
        fake_repo(self.root / "a")
        self.assertEqual(find_work_trees(str(self.root), deadline=0.0), [])

    def test_links_are_not_followed(self) -> None:
        outside = fake_repo(self.root / "outside" / "repo")
        (self.root / "moved").mkdir()
        link = self.root / "moved" / "link"
        try:
            if sys.platform == "win32":
                if not create_directory_junction(link, outside.parent):
                    self.skipTest("cannot create a junction here")
            else:
                os.symlink(str(outside.parent), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("cannot create a link here")
        self.assertEqual(find_work_trees(str(self.root / "moved")), [])

    def test_a_missing_folder_has_no_work_trees(self) -> None:
        self.assertEqual(find_work_trees(str(self.root / "gone")), [])

    def test_linked_worktrees_are_followed_wherever_they_live(self) -> None:
        main = fake_repo(self.root / "box" / "main")
        far = self.root / "far" / "wt_far"
        far.mkdir(parents=True)
        near = self.root / "far" / "wt_rel"
        near.mkdir()
        registry = main / ".git" / "worktrees"
        for name, recorded in (
            ("wt_far", (far / ".git").as_posix()),  # git records "<worktree>/.git" with forward slashes
            ("wt_rel", os.path.relpath(near / ".git", registry / "wt_rel").replace("\\", "/")),  # git 2.48 relative
            ("wt_gone", (self.root / "far" / "gone" / ".git").as_posix()),
            ("wt_empty", ""),
        ):
            (registry / name).mkdir(parents=True)
            (registry / name / "gitdir").write_text(recorded + "\n", encoding="utf-8")
        (registry / "wt_nofile").mkdir()  # an entry without a gitdir file

        found = find_work_trees(str(self.root / "box"))
        self.assertTrue(same(found, [main, far, near]), found)


class TestReleaseWithAFakeGit(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_locks_fake_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, onerror=force_remove)
        self.box = self.root / "box"
        self.watched = fake_repo(self.box / "watched")
        self.idle = fake_repo(self.box / "idle")
        self.outside = fake_repo(self.root / "other" / "outside")

    def test_only_the_repositories_inside_the_folder_are_asked_and_only_watched_ones_stopped(self) -> None:
        git = FakeGit(watching=[self.watched, self.outside])
        stopped = release_git_locks(str(self.box), run_git=git)
        self.assertTrue(same(stopped, [self.watched]), stopped)
        self.assertTrue(same(git.asked("status"), [self.watched, self.idle]))
        self.assertTrue(same(git.asked("stop"), [self.watched]))
        self.assertEqual(git.watching, {os.path.normcase(str(self.outside))})  # the daemon outside was left running

    def test_the_folder_being_a_repository_itself_is_released(self) -> None:
        fake_repo(self.box)
        git = FakeGit(watching=[self.box])
        self.assertTrue(same(release_git_locks(str(self.box), run_git=git), [self.box]))

    def test_a_linked_worktree_elsewhere_is_released_with_the_main_repository(self) -> None:
        far = self.root / "far" / "wt"
        far.mkdir(parents=True)
        entry = self.watched / ".git" / "worktrees" / "wt"
        entry.mkdir(parents=True)
        (entry / "gitdir").write_text((far / ".git").as_posix() + "\n", encoding="utf-8")
        git = FakeGit(watching=[far])  # only the worktree's daemon runs: it watches the main repository's git dir
        self.assertTrue(same(release_git_locks(str(self.box), run_git=git), [far]))

    def test_watching_repositories_only_looks(self) -> None:
        git = FakeGit(watching=[self.watched])
        self.assertTrue(same(watching_repositories([str(self.box)], run_git=git), [self.watched]))
        self.assertEqual(git.asked("stop"), [])

    def test_watching_repositories_covers_several_folders_once_each(self) -> None:
        git = FakeGit(watching=[self.watched, self.outside])
        folders = [str(self.box), str(self.box / "watched"), str(self.root / "other")]
        self.assertTrue(same(watching_repositories(folders, run_git=git), [self.watched, self.outside]))
        self.assertEqual(len(git.asked("status")), 3)  # watched, idle and outside: none asked twice

    def test_a_folder_without_repositories_and_a_file_run_no_git(self) -> None:
        git = FakeGit()
        (self.root / "plain").mkdir()
        (self.root / "file.txt").write_text("x", encoding="utf-8")
        self.assertEqual(release_git_locks(str(self.root / "plain"), run_git=git), [])
        self.assertEqual(release_git_locks(str(self.root / "file.txt"), run_git=git), [])
        self.assertEqual(release_git_locks(str(self.root / "gone"), run_git=git), [])
        self.assertEqual(git.calls, [])

    def test_without_git_nothing_runs_and_nothing_is_raised(self) -> None:
        with patch.object(git_locks.shutil, "which", return_value=None), patch.object(
            git_locks.subprocess, "run", side_effect=AssertionError("git is not installed")
        ):
            self.assertEqual(release_git_locks(str(self.box)), [])
            self.assertEqual(watching_repositories([str(self.box)]), [])

    def test_a_failing_git_never_escapes(self) -> None:
        failures = (
            OSError("git vanished"),
            subprocess.TimeoutExpired("git", 5),
            RuntimeError("broken runner"),
            ValueError("garbage"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):

                def broken(repo: str, args: Sequence[str], timeout: float, _failure: Exception = failure) -> Tuple[int, str]:
                    raise _failure

                self.assertEqual(release_git_locks(str(self.box), run_git=broken), [])
                self.assertEqual(watching_repositories([str(self.box)], run_git=broken), [])

    def test_a_stop_that_fails_or_raises_is_not_reported_and_does_not_hide_the_others(self) -> None:
        other = fake_repo(self.box / "another")
        stuck = fake_repo(self.box / "stuck")
        git = FakeGit(watching=[self.watched, other, stuck], stop_fails=[stuck])
        stopped = release_git_locks(str(self.box), run_git=git)
        self.assertTrue(same(stopped, [self.watched, other]), stopped)

        calls: List[str] = []

        def raising(repo: str, args: Sequence[str], timeout: float) -> Tuple[int, str]:
            calls.append(args[-1])
            if args[-1] == "stop":
                raise OSError("pipe broke")
            return 0, ""

        self.assertEqual(release_git_locks(str(self.box), run_git=raising), [])
        self.assertIn("stop", calls)

    def test_an_exhausted_budget_asks_nothing(self) -> None:
        git = FakeGit(watching=[self.watched])
        self.assertEqual(release_git_locks(str(self.box), run_git=git, budget=0.0), [])
        self.assertEqual(git.calls, [])

    def test_the_real_runner_runs_git_without_the_daemon_and_with_a_clean_environment(self) -> None:
        seen: Dict[str, Any] = {}

        def fake_run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
            seen.setdefault("commands", []).append(list(command))
            seen["kwargs"] = kwargs
            return subprocess.CompletedProcess(command, 0, stdout="fsmonitor-daemon is watching\n", stderr="")

        with patch.object(git_locks.shutil, "which", return_value="/usr/bin/git"), patch.object(
            git_locks.subprocess, "run", side_effect=fake_run
        ), patch.dict(os.environ, {"GIT_DIR": "/elsewhere/.git", "GIT_WORK_TREE": "/elsewhere"}):
            stopped = release_git_locks(str(self.box / "watched"))

        self.assertTrue(same(stopped, [self.watched]))
        status, stop = seen["commands"]
        prefix = ["/usr/bin/git", "-c", "core.fsmonitor=false", "-C"]
        self.assertEqual(status[:4], prefix)
        self.assertTrue(same([status[4]], [self.watched]))
        self.assertEqual(status[5:], ["fsmonitor--daemon", "status"])
        self.assertEqual(stop[:4], prefix)
        self.assertEqual(stop[5:], ["fsmonitor--daemon", "stop"])
        env = seen["kwargs"]["env"]
        self.assertNotIn("GIT_DIR", env)  # a git hook's repository must not decide which daemon is asked
        self.assertNotIn("GIT_WORK_TREE", env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertLessEqual(seen["kwargs"]["timeout"], 10)  # every git call is time-boxed
        self.assertEqual(seen["kwargs"]["stdin"], subprocess.DEVNULL)

    def test_the_real_runner_treats_a_timeout_as_not_watching(self) -> None:
        with patch.object(git_locks.shutil, "which", return_value="git"), patch.object(
            git_locks.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 5)
        ):
            self.assertEqual(release_git_locks(str(self.box)), [])


class TestMoveReleasesTheFolderFirst(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_locks_move_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, onerror=force_remove)
        self.src = self.root / "project"
        self.repo = fake_repo(self.src / "app")
        (self.repo / "main.py").write_text("print(1)\n", encoding="utf-8")
        self.dest = self.root / "03_Development_Projects" / "project"
        self.dest.parent.mkdir()

    def patch_git(self, git: Callable[..., Tuple[int, str]]) -> Any:
        return patch.object(git_locks, "_git_runner", return_value=git)

    def test_the_daemon_is_stopped_before_the_rename_and_reported(self) -> None:
        at_stop: List[Tuple[bool, bool]] = []
        git = FakeGit(
            watching=[self.repo],
            on_stop=lambda repo: at_stop.append((self.src.is_dir(), self.dest.exists())),
        )
        with self.patch_git(git):
            stopped = _move(str(self.src), str(self.dest))
        self.assertTrue(same(stopped, [self.repo]))
        self.assertEqual(at_stop, [(True, False)])  # the folder was still where it was, the target still free
        self.assertFalse(self.src.exists())
        self.assertTrue((self.dest / "app" / "main.py").is_file())

    def test_a_move_never_fails_because_of_git(self) -> None:
        def broken(repo: str, args: Sequence[str], timeout: float) -> Tuple[int, str]:
            raise OSError("git exploded")

        with self.patch_git(broken):
            self.assertEqual(_move(str(self.src), str(self.dest)), [])
        self.assertTrue((self.dest / "app" / "main.py").is_file())

    def test_a_file_is_moved_without_asking_git(self) -> None:
        target = self.dest.parent / "note.txt"
        (self.root / "note.txt").write_text("x", encoding="utf-8")
        with self.patch_git(FakeGit()) as patched:
            self.assertEqual(_move(str(self.root / "note.txt"), str(target)), [])
        patched.assert_not_called()
        self.assertTrue(target.is_file())

    def test_apply_plan_reports_the_daemons_it_stopped(self) -> None:
        drive = self.root / "drive"
        proj = drive / "my_repo"
        fake_repo(proj)
        (proj / "package.json").write_text('{"name": "my_repo"}\n', encoding="utf-8")
        zoner = AutoZoner(str(drive), cluster_size=4096, system_refs=SystemRefIndex([]))
        plan = [a for a in zoner.generate_plan() if os.path.basename(a.src_path) == "my_repo"]
        self.assertEqual(len(plan), 1)
        with self.patch_git(FakeGit(watching=[proj])):
            res = zoner.apply_plan(plan)
        self.assertEqual(res["moved_count"], 1, res)
        self.assertTrue(same(res["fsmonitor_stopped"], [proj]))
        self.assertTrue((drive / "03_Development_Projects" / "my_repo" / "package.json").is_file())

    def test_classify_releases_a_folder_before_it_moves_it(self) -> None:
        drive = self.root / "classify_drive"
        incoming = drive / "staging" / "web_app"
        incoming.mkdir(parents=True)
        (incoming / "package.json").write_text("{}", encoding="utf-8")
        engine = ClassifierEngine(root_path=drive, system_refs=SystemRefIndex([]))
        results = engine.scan_and_classify(incoming.parent, recursive=True)
        self.assertEqual([r.name for r in results], ["web_app"])

        seen: List[Tuple[str, bool]] = []

        def release(folder: str) -> List[str]:
            seen.append((folder, incoming.is_dir()))
            return []

        with patch("smart_drive.core.classifier.release_git_locks", side_effect=release):
            actions = engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["MOVED"])
        self.assertEqual(seen, [(str(incoming), True)])  # asked for the folder itself, while it was still in place
        self.assertTrue((drive / "03_Development_Projects" / "web_app" / "package.json").is_file())

    def test_classify_does_not_release_anything_on_a_dry_run(self) -> None:
        drive = self.root / "classify_dry"
        incoming = drive / "staging" / "web_app"
        incoming.mkdir(parents=True)
        (incoming / "package.json").write_text("{}", encoding="utf-8")
        engine = ClassifierEngine(root_path=drive, system_refs=SystemRefIndex([]))
        results = engine.scan_and_classify(incoming.parent, recursive=True)
        with patch("smart_drive.core.classifier.release_git_locks", side_effect=AssertionError("dry run")):
            actions = engine.execute_relocation(results, dry_run=True)
        self.assertEqual([a.status for a in actions], ["PLANNED"])
        self.assertTrue(incoming.is_dir())


@unittest.skipIf(GIT is None, "git executable not found on PATH")
class TestRealDaemon(unittest.TestCase):
    """A real repository, a real fsmonitor daemon, a real rename."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_locks_real_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, onerror=force_remove)
        self.to_stop: List[Path] = []
        # Stops a daemon wherever the repository ends up; runs before the temp folder is removed.
        self.addCleanup(lambda: [stop_daemon(p) for p in self.to_stop])
        self.parent = self.root / "parent"
        self.repo = make_repository(self.parent / "repo")
        self.to_stop.append(self.repo)

    def start_or_skip(self, repo: Path) -> None:
        if not start_daemon(repo):
            self.skipTest("this git does not start an fsmonitor daemon on this platform")

    def test_the_folder_above_a_repository_with_a_running_daemon_can_be_moved(self) -> None:
        self.start_or_skip(self.repo)
        moved = self.root / "moved"
        self.to_stop.append(moved / "repo")

        if sys.platform == "win32":  # the problem itself: the daemon keeps the rename from working
            probe = self.root / "probe"
            try:
                os.rename(str(self.parent), str(probe))
            except OSError:
                pass
            else:
                os.rename(str(probe), str(self.parent))
                self.skipTest("the daemon does not lock the folder with this git")

        stopped = _move(str(self.parent), str(moved))

        self.assertTrue(same(stopped, [self.repo]), stopped)
        self.assertFalse(self.parent.exists())
        self.assertTrue((moved / "repo" / "a.txt").is_file())
        self.assertFalse(daemon_is_watching(moved / "repo"))

    def test_a_folder_without_a_running_daemon_is_moved_and_nothing_is_reported(self) -> None:
        moved = self.root / "moved_idle"
        self.assertEqual(_move(str(self.parent), str(moved)), [])
        self.assertTrue((moved / "repo" / "a.txt").is_file())

    def test_watching_repositories_sees_the_daemon_without_stopping_it(self) -> None:
        self.start_or_skip(self.repo)
        self.assertTrue(same(watching_repositories([str(self.parent)]), [self.repo]))
        self.assertTrue(daemon_is_watching(self.repo))
        stop_daemon(self.repo)
        self.assertEqual(watching_repositories([str(self.parent)]), [])


class DriveCase(SmartDriveTestCase):
    """A mock drive whose agents, processes and system references are all stand-ins, driven through the CLI."""

    def setUp(self) -> None:
        super().setUp()
        fake_home = self.test_dir / "fake_home"
        fake_appdata = fake_home / "AppData" / "Roaming"
        fake_appdata.mkdir(parents=True, exist_ok=True)
        patchers = [
            patch("smart_drive.core.relink._resolve_locations", return_value=(fake_home, fake_appdata, False)),
            patch("smart_drive.core.relink._running_processes", return_value=set()),
            patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=SystemRefIndex([])),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.to_stop: List[Path] = []

    def tearDown(self) -> None:
        for repo in self.to_stop:  # a daemon would keep the temp folder from being removed
            stop_daemon(repo)
        super().tearDown()

    def run_cli(self, argv: List[str]) -> Tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(argv)
        return code, out.getvalue()


class TestOrganizeAndUndoSayWhatTheyStopped(DriveCase):
    def test_json_and_text_name_the_daemons_stopped_for_the_moves(self) -> None:
        drive = self.create_mock_drive()
        parent = drive / "project"
        watched = fake_repo(parent / "watched")
        fake_repo(parent / "idle")
        moved_watched = drive / "03_Development_Projects" / "project" / "watched"
        git = FakeGit(watching=[watched])
        with patch.object(git_locks, "_git_runner", return_value=git):
            code, text = self.run_cli(["organize", "--root", str(drive), "--apply"])
        self.assertEqual(code, 0, text)
        self.assertIn(f"Stopped the git fsmonitor daemon in {watched}", text)
        self.assertTrue(moved_watched.is_dir())

        git = FakeGit(watching=[moved_watched])
        with patch.object(git_locks, "_git_runner", return_value=git):
            code, text = self.run_cli(["undo", "--root", str(drive), "--apply"])
        self.assertEqual(code, 0, text)
        self.assertIn(f"Stopped the git fsmonitor daemon in {moved_watched}", text)
        self.assertTrue(watched.is_dir())

    def test_nothing_is_said_when_no_daemon_ran(self) -> None:
        drive = self.create_mock_drive()
        fake_repo(drive / "project" / "a")
        fake_repo(drive / "project" / "b")
        with patch.object(git_locks, "_git_runner", return_value=FakeGit()):
            code, text = self.run_cli(["organize", "--root", str(drive), "--apply", "--json"])
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)["zoning"]["fsmonitor_stopped"], [])
        with patch.object(git_locks, "_git_runner", return_value=FakeGit()):
            code, text = self.run_cli(["undo", "--root", str(drive), "--apply"])
        self.assertNotIn("fsmonitor", text)


@unittest.skipIf(GIT is None, "git executable not found on PATH")
class TestOrganizeAndUndoWithARunningDaemon(DriveCase):
    """`organize --apply` and `undo --apply` on a drive whose repository has a daemon (the reported failure)."""

    def test_the_move_and_its_undo_work_while_the_repository_has_a_daemon(self) -> None:
        drive = self.create_mock_drive()
        parent = drive / "project"  # like E:\project: a folder of repositories, moved as a whole
        repo = make_repository(parent / "my_repo")
        fake_repo(parent / "other")  # a second project, so that `project` is a "folder of development projects"
        moved_parent = drive / "03_Development_Projects" / "project"
        moved_repo = moved_parent / "my_repo"
        self.to_stop.extend([repo, moved_repo])
        if not start_daemon(repo):
            self.skipTest("this git does not start an fsmonitor daemon on this platform")

        zoner = AutoZoner(str(drive), system_refs=SystemRefIndex([]))
        plan = [a for a in zoner.generate_plan() if os.path.basename(a.src_path) == "project"]
        self.assertEqual(len(plan), 1)
        applied = zoner.apply_plan(plan)
        self.assertEqual(applied["errors"], [])
        self.assertEqual(applied["moved_count"], 1, applied)
        self.assertTrue(same(applied["fsmonitor_stopped"], [repo]))
        after_moves(str(drive), [(m["src"], m["dest"]) for m in applied["moved"]], "organize")
        self.assertTrue((moved_repo / "a.txt").is_file())
        self.assertFalse(parent.exists())

        # Something starts the daemon again in the new place (an IDE, a `git status`): undo has to cope
        self.assertTrue(start_daemon(moved_repo))
        code, output = self.run_cli(["undo", "--root", str(drive), "--apply", "--json"])
        report = json.loads(output)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "applied")
        self.assertTrue(same(report["fsmonitor_stopped"], [moved_repo]))
        self.assertTrue((repo / "a.txt").is_file())
        self.assertFalse(moved_parent.exists())


if __name__ == "__main__":
    unittest.main()
