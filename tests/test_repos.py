"""tests/test_repos.py - `smart-drive repos`: duplicates in one place, mirrors across the portable SSD.

Builds real repositories (a bare "remote" plus clones) in a temp folder split into `portable/` and `machine/`:
- two clones of the same branch in one place: one is kept, the other is a redundant copy;
- a clone of another branch: recreate it as a worktree of the kept clone;
- a clone with uncommitted work, or without a remote: blocked until that work is pushed;
- a linked worktree is part of its clone, never a duplicate; a deleted one is reported as stale;
- the same project on the SSD and on the machine is a mirror, reported in or out of sync;
- dependency and hidden folders are not searched, and the scan never writes to a repository.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict
from unittest.mock import patch

from smart_drive.cli.main import main
from smart_drive.core.repos import (
    MACHINE,
    PORTABLE,
    _is_cloud_name,
    build_report,
    default_scan_roots,
    find_repositories,
    normalize_remote,
    supports_relative_worktrees,
)

GIT = shutil.which("git")
ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="Test",
    GIT_AUTHOR_EMAIL="test@example.com",
    GIT_COMMITTER_NAME="Test",
    GIT_COMMITTER_EMAIL="test@example.com",
    GIT_CONFIG_NOSYSTEM="1",
)


def git(cwd: Path, *args: str, date: str = "") -> str:
    env = dict(ENV)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    res = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, check=True,
    )
    return res.stdout.strip()


def commit(repo: Path, name: str, date: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", name, date=date)


def _force_remove(func, path, _exc) -> None:
    os.chmod(path, stat.S_IWRITE)  # git marks object files read-only, which Windows refuses to delete
    func(path)


_TEMPLATE_DIR: Path = Path()


def _template_remote() -> Path:
    """A bare remote whose main has commits from 2020 and 2022 and a `feature` branch from 2021, built once."""
    global _TEMPLATE_DIR
    if _TEMPLATE_DIR.name:
        return _TEMPLATE_DIR / "template.git"
    _TEMPLATE_DIR = Path(tempfile.mkdtemp(prefix="sd_repos_template_")).resolve()
    remote = _TEMPLATE_DIR / "template.git"
    git(_TEMPLATE_DIR, "init", "--bare", "-b", "main", str(remote))
    seed = _TEMPLATE_DIR / "seed"
    git(_TEMPLATE_DIR, "clone", str(remote), str(seed))
    git(seed, "checkout", "-b", "main")
    commit(seed, "a.txt", "2020-01-01T00:00:00")
    git(seed, "checkout", "-b", "feature")
    commit(seed, "f.txt", "2021-01-01T00:00:00")
    git(seed, "checkout", "main")
    commit(seed, "b.txt", "2022-01-01T00:00:00")
    git(seed, "push", "origin", "main", "feature")
    return remote


def tearDownModule() -> None:
    if _TEMPLATE_DIR.name:
        shutil.rmtree(_TEMPLATE_DIR, onerror=_force_remove)


@unittest.skipIf(GIT is None, "git is not installed")
class _Repos(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_repos_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, onerror=_force_remove)
        self.portable = self.base / "portable"
        self.machine = self.base / "machine"
        self.portable.mkdir()
        self.machine.mkdir()

    def make_remote(self, name: str) -> Path:
        """This test's own copy of the template remote."""
        remote = self.base / f"{name}.git"
        shutil.copytree(_template_remote(), remote)
        return remote

    def clone(self, remote: Path, dest: Path, branch: str = "main") -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(self.base, "clone", "-b", branch, str(remote), str(dest))
        return dest

    def report(self, *roots: Path):
        return build_report([str(r) for r in roots], [str(self.portable)], workers=4)


class TestDuplicatesInOnePlace(_Repos):
    def setUp(self) -> None:
        super().setUp()
        remote = self.make_remote("proj")
        self.keeper = self.clone(remote, self.machine / "proj")
        self.copy = self.clone(remote, self.machine / "copies" / "proj-copy")
        self.other_branch = self.clone(remote, self.machine / "feature-copy", "feature")
        self.dirty = self.clone(remote, self.machine / "dirty-copy")
        (self.dirty / "a.txt").write_text("edited", encoding="utf-8")
        self.result = self.report(self.machine)
        self.groups = [g for g in self.result.duplicates if g.location == MACHINE]

    def actions(self) -> Dict[str, str]:
        return {Path(s.path).name: s.action for s in self.groups[0].suggestions}

    def test_one_group_with_one_keeper(self) -> None:
        self.assertEqual(len(self.groups), 1)
        self.assertEqual(self.groups[0].identity, normalize_remote(str(self.base / "proj.git")))
        self.assertEqual(self.actions()["proj"], "keep")  # newest commit, then the shortest path

    def test_same_branch_copy_is_redundant(self) -> None:
        self.assertEqual(self.actions()["proj-copy"], "remove")

    def test_other_branch_becomes_a_worktree_of_the_keeper(self) -> None:
        self.assertEqual(self.actions()["feature-copy"], "worktree")
        suggestion = next(s for s in self.groups[0].suggestions if s.action == "worktree")
        self.assertIn(f'worktree add', suggestion.command)
        self.assertIn(str(self.keeper), suggestion.command)
        self.assertTrue(suggestion.command.endswith(" feature"))
        if supports_relative_worktrees(self.result.git_version):
            self.assertIn("--relative-paths", suggestion.command)

    def test_uncommitted_work_blocks_the_copy(self) -> None:
        self.assertEqual(self.actions()["dirty-copy"], "blocked")
        reason = next(s.reason for s in self.groups[0].suggestions if s.action == "blocked")
        self.assertIn("1 uncommitted file(s)", reason)

    def test_nothing_is_a_mirror_without_a_portable_copy(self) -> None:
        self.assertEqual(self.result.mirrors, [])


class TestWorktrees(_Repos):
    def test_a_linked_worktree_is_not_a_duplicate(self) -> None:
        remote = self.make_remote("proj")
        repo = self.clone(remote, self.machine / "proj")
        git(repo, "worktree", "add", "-b", "topic", str(self.machine / "proj-topic"))
        result = self.report(self.machine)
        self.assertEqual(result.duplicates, [])
        self.assertEqual((result.clones, result.worktrees), (1, 1))
        self.assertTrue(any(c.is_worktree for c in result.copies))

    def test_a_deleted_worktree_is_reported_as_stale(self) -> None:
        remote = self.make_remote("proj")
        repo = self.clone(remote, self.machine / "proj")
        gone = self.base / "elsewhere" / "gone"
        git(repo, "worktree", "add", "-b", "temp", str(gone))
        shutil.rmtree(gone, onerror=_force_remove)
        result = self.report(self.machine)
        self.assertEqual(len(result.stale_worktrees), 1)
        self.assertIn("worktree prune", result.stale_worktrees[0]["command"])

    def test_a_clone_with_worktrees_is_never_offered_for_removal(self) -> None:
        remote = self.make_remote("proj")
        self.clone(remote, self.machine / "a")
        older = self.clone(remote, self.machine / "much_longer_path_b")
        git(older, "worktree", "add", "-b", "topic", str(self.base / "wt"))
        result = self.report(self.machine)
        actions = {Path(s.path).name: (s.action, s.reason) for s in result.duplicates[0].suggestions}
        self.assertEqual(actions["much_longer_path_b"][0], "blocked")
        self.assertIn("linked worktree", actions["much_longer_path_b"][1])


class TestReposWithoutRemote(_Repos):
    def test_copies_are_matched_by_their_first_commit_and_never_removed(self) -> None:
        solo = self.machine / "solo"
        solo.mkdir()
        git(solo, "init", "-b", "main")
        commit(solo, "x.txt", "2020-01-01T00:00:00")
        shutil.copytree(solo, self.machine / "solo-copy")
        result = self.report(self.machine)
        self.assertEqual(len(result.duplicates), 1)
        self.assertTrue(result.duplicates[0].identity.startswith("root:"))
        blocked = [s for s in result.duplicates[0].suggestions if s.action == "blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertIn("no remote", blocked[0].reason)


class TestMirrors(_Repos):
    def setUp(self) -> None:
        super().setUp()
        remote = self.make_remote("proj")
        self.on_ssd = self.clone(remote, self.portable / "proj")
        self.on_machine = self.clone(remote, self.machine / "proj")

    def test_the_same_commit_in_both_places_is_in_sync(self) -> None:
        result = self.report(self.portable, self.machine)
        self.assertEqual(result.duplicates, [])
        self.assertEqual(len(result.mirrors), 1)
        self.assertTrue(result.mirrors[0].in_sync)
        self.assertEqual({c["location"] for c in result.mirrors[0].copies}, {PORTABLE, MACHINE})

    def test_unpushed_work_on_the_ssd_says_push(self) -> None:
        commit(self.on_ssd, "new.txt", "2023-01-01T00:00:00")
        result = self.report(self.portable, self.machine)
        mirror = result.mirrors[0]
        self.assertFalse(mirror.in_sync)
        ssd = next(c for c in mirror.copies if c["location"] == PORTABLE)
        self.assertEqual(ssd["next"], ["push"])

    def test_pushed_but_not_fetched_says_pull(self) -> None:
        commit(self.on_ssd, "new.txt", "2023-01-01T00:00:00")
        git(self.on_ssd, "push", "origin", "main")
        git(self.on_machine, "fetch")
        result = self.report(self.portable, self.machine)
        machine = next(c for c in result.mirrors[0].copies if c["location"] == MACHINE)
        self.assertEqual(machine["next"], ["pull"])

    def test_different_commits_without_local_work_ask_for_a_fetch(self) -> None:
        commit(self.on_ssd, "new.txt", "2023-01-01T00:00:00")
        git(self.on_ssd, "push", "origin", "main")  # the machine copy has not fetched it
        result = self.report(self.portable, self.machine)
        self.assertFalse(result.mirrors[0].in_sync)
        self.assertTrue(all(c["next"] == ["fetch to compare"] for c in result.mirrors[0].copies))


class TestDiscovery(_Repos):
    def test_dependency_and_hidden_folders_are_not_searched(self) -> None:
        for rel in ("node_modules/pkg", ".cargo/registry", "app/target/dep", "visible/repo"):
            path = self.machine / rel
            path.mkdir(parents=True)
            git(path, "init", "-b", "main")
        found = [Path(p).relative_to(self.machine).as_posix() for p in find_repositories([str(self.machine)])]
        self.assertEqual(found, ["visible/repo"])

    def test_max_depth_bounds_the_walk(self) -> None:
        deep = self.machine / "a" / "b" / "c"
        deep.mkdir(parents=True)
        git(deep, "init", "-b", "main")
        self.assertEqual(find_repositories([str(self.machine)], max_depth=2), [])
        self.assertEqual(len(find_repositories([str(self.machine)], max_depth=3)), 1)

    def test_a_broken_git_entry_is_reported_not_raised(self) -> None:
        broken = self.machine / "broken"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /nowhere/at/all", encoding="utf-8")
        result = self.report(self.machine)
        self.assertEqual([Path(u["path"]).name for u in result.unreadable], ["broken"])

    def test_the_scan_writes_nothing(self) -> None:
        remote = self.make_remote("proj")
        repo = self.clone(remote, self.machine / "proj")
        (repo / "a.txt").write_text("edited", encoding="utf-8")  # a stale index makes `git status` want to refresh it
        index = repo / ".git" / "index"
        before = (index.stat().st_mtime_ns, sorted(p.name for p in (repo / ".git").iterdir()))
        self.report(self.machine)
        after = (index.stat().st_mtime_ns, sorted(p.name for p in (repo / ".git").iterdir()))
        self.assertEqual(before, after)


class TestSubmodules(_Repos):
    def test_a_submodule_checkout_belongs_to_its_parent(self) -> None:
        lib = self.make_remote("lib")
        parent = self.clone(self.make_remote("parent"), self.machine / "parent")
        git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(lib), "references/lib")
        git(parent, "commit", "-m", "add lib", date="2023-01-01T00:00:00")
        self.clone(lib, self.machine / "lib")  # the same project cloned on its own, in the same place
        result = self.report(self.machine)
        self.assertEqual(result.duplicates, [])
        self.assertEqual(result.submodules, 1)
        sub = next(c for c in result.copies if c.superproject)
        self.assertEqual(os.path.normcase(sub.superproject), os.path.normcase(str(parent)))

    def test_an_embedded_repository_without_gitmodules_is_still_a_clone(self) -> None:
        lib = self.make_remote("lib")
        parent = self.clone(self.make_remote("parent"), self.machine / "parent")
        self.clone(lib, parent / "vendored" / "lib")
        git(parent, "-c", "advice.addEmbeddedRepo=false", "add", "vendored/lib")
        git(parent, "commit", "-m", "embed lib", date="2023-01-01T00:00:00")
        self.clone(lib, self.machine / "lib")
        result = self.report(self.machine)
        self.assertEqual(result.submodules, 0)
        self.assertEqual(len(result.duplicates), 1)
        self.assertEqual(len(result.duplicates[0].suggestions), 2)

class TestCloudFolders(_Repos):
    def test_cloud_names(self) -> None:
        for name in ("Google Drive", "OneDrive", "OneDrive - Contoso", "Dropbox (Personal)", "iCloud Drive", "MEGA"):
            self.assertTrue(_is_cloud_name(name), name)
        for name in ("KINGSTON", "Du Lieu", "Megatron", "Boxes", "Projects", ""):
            self.assertFalse(_is_cloud_name(name), name)

    def test_a_cloud_folder_under_a_root_is_not_searched(self) -> None:
        for rel in ("OneDrive - Contoso/repo", "work/OneDrive notes/repo"):
            path = self.machine / rel
            path.mkdir(parents=True)
            git(path, "init", "-b", "main")
        found = [Path(p).relative_to(self.machine).as_posix() for p in find_repositories([str(self.machine)])]
        self.assertEqual(found, ["work/OneDrive notes/repo"])  # only the top level is a sync root

    @unittest.skipUnless(os.name == "nt", "drive letters are a Windows concept")
    def test_a_cloud_drive_is_left_out_of_the_default_roots(self) -> None:
        drive = os.path.splitdrive(str(self.base))[0]
        letter_root = drive + os.sep

        class Backend:
            def __init__(self, label: str) -> None:
                self.label = label

            def get_volume_info(self, root: str):
                return {"volume_label": self.label}

        with patch("smart_drive.core.drive_detector.list_secondary_drive_letters", return_value=[drive]):
            with patch("smart_drive.core.drive_detector.get_backend", return_value=Backend("Google Drive")):
                self.assertNotIn(letter_root, default_scan_roots(None))
            with patch("smart_drive.core.drive_detector.get_backend", return_value=Backend("Data")):
                self.assertIn(letter_root, default_scan_roots(None))

class TestNormalizeRemote(unittest.TestCase):
    def test_every_spelling_of_one_github_repo_is_equal(self) -> None:
        spellings = (
            "https://github.com/DuongNAD/smart-drive-os.git",
            "https://github.com/duongnad/smart-drive-os",
            "http://user@github.com/DuongNAD/smart-drive-os/",
            "git@github.com:DuongNAD/smart-drive-os.git",
            "ssh://git@github.com:22/DuongNAD/smart-drive-os.git",
        )
        self.assertEqual({normalize_remote(s) for s in spellings}, {"github.com/duongnad/smart-drive-os"})

    def test_local_paths_stay_paths(self) -> None:
        here = os.path.abspath("some_repo.git")
        self.assertEqual(normalize_remote(here), normalize_remote("file://" + here))
        self.assertFalse(normalize_remote(here).endswith(".git"))

    def test_relative_worktree_support_by_version(self) -> None:
        self.assertTrue(supports_relative_worktrees("2.55.0.windows.3"))
        self.assertTrue(supports_relative_worktrees("2.48.0"))
        self.assertFalse(supports_relative_worktrees("2.47.1"))
        self.assertFalse(supports_relative_worktrees(""))


@unittest.skipIf(GIT is None, "git is not installed")
class TestCli(_Repos):
    def setUp(self) -> None:
        super().setUp()
        remote = self.make_remote("proj")
        self.clone(remote, self.portable / "proj")
        self.clone(remote, self.machine / "proj")
        self.clone(remote, self.machine / "proj-again")

    def run_cli(self, *extra: str):
        out, err = io.StringIO(), io.StringIO()
        argv = ["repos", str(self.portable), str(self.machine), "--root", str(self.portable), *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_text_report(self) -> None:
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("DUPLICATES: the same project twice in the same place (1)", out)
        self.assertIn("MIRRORS: on the portable SSD and on this machine (1, 1 in sync)", out)
        self.assertIn("remove", out)
        self.assertIn("Read-only report", out)

    def test_json_report(self) -> None:
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["clones"], 3)
        self.assertEqual(data["portable_roots"], [str(self.portable)])
        self.assertEqual(len(data["duplicates"]), 1)
        self.assertEqual(len(data["mirrors"]), 1)

    def test_a_missing_folder_is_an_error(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["repos", str(self.base / "nope"), "--root", str(self.portable)])
        self.assertEqual(code, 2)
        self.assertIn("Not a directory", err.getvalue())


if __name__ == "__main__":
    unittest.main()
