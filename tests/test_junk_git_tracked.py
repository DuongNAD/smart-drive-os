"""tests/test_junk_git_tracked.py - a folder that git tracks files in is part of a project, not junk.

Regression: `build/`, `dist/`, `htmlcov/`, `*.egg-info/` ... that a repository commits on purpose (a published
`dist/`, a vendored build, a generated site) were reported as regenerable junk and, at Tier 1-2, deleted outright.
The detector now asks `git ls-files`, only for the few Tier 2-3 entries that survive every other check and only
inside a git work tree. When git cannot be asked, an entry inside a work tree is kept.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional, Set
from unittest.mock import patch

from smart_drive.core import junk_detector
from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import KEPT_GIT_TRACKED, KEPT_GIT_UNVERIFIED, JunkDetector
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.core.system_refs import SystemRefIndex

GIT = shutil.which("git")
ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="Test",
    GIT_AUTHOR_EMAIL="test@example.com",
    GIT_COMMITTER_NAME="Test",
    GIT_COMMITTER_EMAIL="test@example.com",
    GIT_CONFIG_NOSYSTEM="1",
)


def _force_remove(func: Any, path: str, _exc: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def fake_repository(folder: Path) -> None:
    """A folder git would take for a repository, made without git."""
    (folder / ".git" / "objects").mkdir(parents=True)
    (folder / ".git" / "refs").mkdir()
    (folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def git_cmd(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "gc.auto=0", *args],
        cwd=str(cwd),
        env=ENV,
        capture_output=True,
        text=True,
        check=True,
    )


class TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_git_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, onerror=_force_remove)
        # A temp folder inside somebody's git work tree (a dotfiles repository in the home folder...) makes every
        # entry "inside a work tree"; the tests about folders outside one cannot run there.
        self.host_has_work_tree = any(
            os.path.lexists(os.path.join(str(folder), ".git")) for folder in (self.root, *self.root.parents)
        )

    def skip_if_temp_is_in_a_work_tree(self) -> None:
        if self.host_has_work_tree:
            self.skipTest("the temp folder lies inside a git work tree")

    def write(self, rel: str, data: str = "x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def detect(self, tier: JunkTier = JunkTier.TIER_3_SENSITIVE) -> JunkDetector:
        detector = JunkDetector(str(self.root), max_tier=tier, system_refs=SystemRefIndex([]))
        self.found: Set[str] = {item.rel_path.replace("\\", "/") for item in detector.find_junk()}
        return detector

    def make_repo(self, rel: str = "repo") -> Path:
        repo = self.root.joinpath(*rel.split("/"))
        repo.mkdir(parents=True, exist_ok=True)
        git_cmd(repo, "init")
        return repo

    def commit(self, repo: Path, *paths: str) -> None:
        git_cmd(repo, "add", "--", *paths)
        git_cmd(repo, "commit", "-m", "work")


@unittest.skipIf(GIT is None, "git executable not found on PATH")
class TestTrackedEntriesAreKept(TreeCase):
    def test_a_dist_folder_the_repository_commits_is_kept(self) -> None:
        repo = self.make_repo()
        self.write("repo/package.json", "{}")
        self.write("repo/dist/bundle.js")
        self.commit(repo, "package.json", "dist/bundle.js")
        detector = self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())
        self.assertEqual(detector.kept, {KEPT_GIT_TRACKED: 1})

    def test_the_same_folder_hidden_by_gitignore_is_still_junk(self) -> None:
        repo = self.make_repo()
        self.write("repo/package.json", "{}")
        self.write("repo/.gitignore", "dist/\n")
        self.write("repo/dist/bundle.js")
        self.commit(repo, "package.json", ".gitignore")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, {"repo/dist"})

    def test_one_tracked_file_among_generated_ones_keeps_the_folder(self) -> None:
        repo = self.make_repo()
        self.write("repo/pyproject.toml", "")
        self.write("repo/build/README.md")
        self.commit(repo, "pyproject.toml", "build/README.md")
        self.write("repo/build/generated.o")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())

    def test_other_tier_2_folders_and_files_are_checked_too(self) -> None:
        repo = self.make_repo()
        self.write("repo/htmlcov/index.html")
        self.write("repo/pkg.egg-info/PKG-INFO")
        self.write("repo/.coverage")
        self.write("repo/.pytest_cache/state")
        self.commit(repo, "htmlcov/index.html", "pkg.egg-info/PKG-INFO", ".coverage")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, {"repo/.pytest_cache"})

    def test_a_tracked_file_named_like_a_core_dump_is_kept_at_tier_3(self) -> None:
        repo = self.make_repo()
        self.write("repo/core.4321", "a core file a debugger test suite keeps as a fixture")
        self.write("repo/scratch.tmp")
        self.commit(repo, "core.4321")
        detector = self.detect()
        self.assertEqual(self.found, {"repo/scratch.tmp"})
        self.assertEqual(detector.kept, {KEPT_GIT_TRACKED: 1})

    def test_folders_outside_the_repository_are_not_affected(self) -> None:
        repo = self.make_repo()
        self.write("repo/package.json", "{}")
        self.write("repo/dist/bundle.js")
        self.commit(repo, "package.json", "dist/bundle.js")
        self.write("plain/package.json", "{}")
        self.write("plain/dist/bundle.js")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, {"plain/dist"})

    def test_the_environment_of_a_git_hook_does_not_send_the_question_to_another_repository(self) -> None:
        repo = self.make_repo()
        other = self.make_repo("other")
        self.write("repo/package.json", "{}")
        self.write("repo/dist/bundle.js")
        self.commit(repo, "package.json", "dist/bundle.js")
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}):
            self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())

    def test_clean_apply_leaves_the_tracked_folder_and_removes_the_ignored_one(self) -> None:
        repo = self.make_repo()
        self.write("repo/package.json", "{}")
        self.write("repo/.gitignore", "build/\n")
        self.write("repo/dist/bundle.js")
        self.write("repo/build/out.js")
        self.commit(repo, "package.json", ".gitignore", "dist/bundle.js")
        detector = JunkDetector(str(self.root), max_tier=JunkTier.TIER_2_DEV_CACHE, system_refs=SystemRefIndex([]))
        summary = PurgeEngine(str(self.root), dry_run=False).purge_items(detector.find_junk())
        self.assertEqual(summary.deleted_count, 1)
        self.assertTrue((self.root / "repo" / "dist" / "bundle.js").is_file())
        self.assertFalse((self.root / "repo" / "build").exists())


class TestWhatIsAskedOfGit(TreeCase):
    """These do not need git: it is replaced, or only a `.git` folder is made."""

    def test_git_is_not_asked_without_a_work_tree(self) -> None:
        self.skip_if_temp_is_in_a_work_tree()
        self.write("proj/package.json", "{}")
        self.write("proj/dist/bundle.js")
        with patch.object(junk_detector, "_git_tracks", side_effect=AssertionError("asked outside a work tree")):
            self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, {"proj/dist"})

    def test_git_is_not_asked_about_tier_1_entries(self) -> None:
        fake_repository(self.root / "repo")
        for i in range(5):
            self.write(f"repo/pkg{i}/__pycache__/m.pyc")
        self.write("repo/Thumbs.db")
        with patch.object(junk_detector, "_git_tracks", side_effect=AssertionError("asked about Tier 1")):
            self.detect(JunkTier.TIER_1_SAFE)
        self.assertEqual(len(self.found), 6)

    def test_git_is_asked_once_per_surviving_candidate(self) -> None:
        fake_repository(self.root / "repo")
        self.write("repo/package.json", "{}")
        self.write("repo/dist/bundle.js")
        self.write("repo/build/out.js")
        self.write("repo/lib/build/not_a_project_root.js")  # never reaches git: no manifest beside it
        asked = []

        def fake(folder: str, name: Optional[str] = None) -> Optional[bool]:
            asked.append(os.path.basename(folder))
            return False

        with patch.object(junk_detector, "_git_tracks", side_effect=fake):
            self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(sorted(asked), ["build", "dist"])
        self.assertEqual(self.found, {"repo/dist", "repo/build"})

    def test_the_number_of_questions_put_to_git_in_one_scan_is_bounded(self) -> None:
        fake_repository(self.root / "repo")
        for i in range(6):
            self.write(f"repo/d{i}/fixture.tmp")  # matches the `*.tmp` rule, and may well be tracked
        with patch.object(junk_detector, "MAX_GIT_QUERIES", 4):
            with patch.object(junk_detector, "_git_tracks", return_value=False) as asked:
                detector = self.detect()
        self.assertEqual(asked.call_count, 4)
        self.assertEqual(len(self.found), 4)
        self.assertEqual(detector.kept, {KEPT_GIT_UNVERIFIED: 2})  # past the limit the rest is kept, not guessed

    def test_without_git_an_entry_in_a_work_tree_is_kept_and_one_outside_is_not(self) -> None:
        self.skip_if_temp_is_in_a_work_tree()
        fake_repository(self.root / "repo")
        self.write("repo/package.json", "{}")
        self.write("repo/dist/bundle.js")
        self.write("plain/package.json", "{}")
        self.write("plain/dist/bundle.js")
        with patch.object(junk_detector.shutil, "which", return_value=None):
            detector = self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, {"plain/dist"})
        self.assertEqual(detector.kept, {KEPT_GIT_UNVERIFIED: 1})

    def test_a_git_that_fails_or_times_out_keeps_the_entry(self) -> None:
        fake_repository(self.root / "repo")
        self.write("repo/package.json", "{}")
        self.write("repo/dist/bundle.js")
        failures = (
            subprocess.CompletedProcess([], 128, b"", b"fatal: detected dubious ownership in repository"),
            subprocess.TimeoutExpired("git", 20),
            OSError("git vanished"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(junk_detector.shutil, "which", return_value="git"):
                    if isinstance(failure, subprocess.CompletedProcess):
                        patched = patch.object(junk_detector.subprocess, "run", return_value=failure)
                    else:
                        patched = patch.object(junk_detector.subprocess, "run", side_effect=failure)
                    with patched:
                        detector = self.detect(JunkTier.TIER_2_DEV_CACHE)
                self.assertEqual(self.found, set())
                self.assertEqual(detector.kept, {KEPT_GIT_UNVERIFIED: 1})

    def test_a_folder_that_is_a_repository_itself_is_kept_without_asking(self) -> None:
        self.write("proj/package.json", "{}")
        fake_repository(self.root / "proj" / "dist")  # e.g. a gh-pages checkout
        self.write("proj/dist/index.html")
        with patch.object(junk_detector, "_git_tracks", side_effect=AssertionError("no need to ask")):
            detector = self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())
        self.assertEqual(detector.kept, {KEPT_GIT_TRACKED: 1})

    def test_a_stray_head_file_is_not_a_repository(self) -> None:
        self.skip_if_temp_is_in_a_work_tree()
        self.write("repo/.git/HEAD", "ref: refs/heads/main\n")  # no objects, no refs: git would not accept it either
        self.write("repo/scratch.tmp")
        with patch.object(junk_detector, "_git_tracks", side_effect=AssertionError("not a work tree")):
            self.detect()
        self.assertEqual(self.found, {"repo/scratch.tmp"})

    def test_a_git_file_pointing_at_a_repository_elsewhere_is_a_work_tree(self) -> None:
        self.skip_if_temp_is_in_a_work_tree()
        self.write("linked/.git", "gitdir: ../elsewhere/.git/worktrees/linked\n")
        self.write("linked/package.json", "{}")
        self.write("linked/dist/bundle.js")
        with patch.object(junk_detector.shutil, "which", return_value=None):
            detector = self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())
        self.assertEqual(detector.kept, {KEPT_GIT_UNVERIFIED: 1})

    def test_the_work_tree_lookup_is_made_once_per_folder(self) -> None:
        self.write("proj/package.json", "{}")
        for name in ("build", "dist", ".cache"):
            self.write(f"proj/{name}/out.bin")
        probed = []
        real_lexists = os.path.lexists

        def counting(path: str) -> bool:
            if os.path.basename(path) == ".git":
                probed.append(os.path.normcase(os.path.dirname(path)))
            return real_lexists(path)

        with patch.object(junk_detector.os.path, "lexists", side_effect=counting):
            self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, {"proj/build", "proj/dist", "proj/.cache"})
        self.assertEqual(probed.count(os.path.normcase(str(self.root / "proj"))), 1)


if __name__ == "__main__":
    unittest.main()
