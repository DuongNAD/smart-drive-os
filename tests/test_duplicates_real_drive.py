"""tests/test_duplicates_real_drive.py - `dup` must not open the files of dependency trees and installed apps.

Regression: `smart-drive dup --json` ran for 15 minutes on a 167 GB NTFS drive (342k files, 40k folders) and
printed nothing. It grouped files by size, then opened and hashed every file of every same-size group, and it walked
into dependency trees full of small files that are copies of one another by design: node_modules (about a thousand
packages), Python virtualenvs and site-packages, caches. With Windows Defender scanning each opened file that is
~16 files a second. Such "duplicates" are expected and are never offered for reclamation.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import smart_drive.core.duplicates as duplicates
from smart_drive.core.duplicates import DEPENDENCY_DIR_NAMES, DuplicateDetector

SAME = b"the same bytes in every copy\n" * 20


class _Tree(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_dup_deps_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel: str, data: bytes = SAME) -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def rel(self, path: str) -> str:
        return Path(path).relative_to(self.root).as_posix()

    def reported(self, plan: Dict[str, Any]) -> List[str]:
        return sorted(self.rel(f) for g in plan["duplicate_groups"] for f in g["files"])


class TestDependencyTreesAreNotScanned(_Tree):
    # The folders the bug report names, plus the other caches a developer drive collects
    TREES = (
        "node_modules", "bower_components", "jspm_packages", ".pnpm-store", "site-packages", "dist-packages",
        "__pycache__", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".next", ".nuxt",
    )

    def test_every_listed_tree_is_in_the_skip_list(self) -> None:
        self.assertTrue(set(self.TREES) <= DEPENDENCY_DIR_NAMES)
        self.assertIn(".git", DEPENDENCY_DIR_NAMES)

    def test_copies_inside_dependency_trees_are_not_duplicates(self) -> None:
        for i, tree in enumerate(self.TREES):
            self.write(f"app{i}/{tree}/pkg/data.bin")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")

        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["backup/copy.bin", "docs/original.bin"])
        self.assertEqual(plan["duplicate_group_count"], 1)
        self.assertEqual(plan["skipped_dependency_dirs"], len(self.TREES))

    def test_the_names_match_whatever_the_letter_case(self) -> None:
        self.write("a/Node_Modules/x/data.bin")
        self.write("b/NODE_MODULES/x/data.bin")
        self.write("c/Site-Packages/x/data.bin")
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 0)
        self.assertEqual(plan["skipped_dependency_dirs"], 3)

    def test_a_virtualenv_is_skipped_whatever_it_is_called(self) -> None:
        for env in ("venv", ".venv", "py311-env"):
            self.write(f"proj/{env}/pyvenv.cfg", b"home = /usr/bin\n")
            self.write(f"proj/{env}/Lib/some/module.py")
        self.write("proj/src/module.py")  # the project's own copy
        self.write("archive/module.py")

        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["archive/module.py", "proj/src/module.py"])
        self.assertEqual(plan["skipped_dependency_dirs"], 3)

    def test_a_folder_without_pyvenv_cfg_is_not_taken_for_a_virtualenv(self) -> None:
        self.write("proj/venv/Lib/some/module.py")
        self.write("proj/src/module.py")
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["proj/src/module.py", "proj/venv/Lib/some/module.py"])
        self.assertEqual(plan["skipped_dependency_dirs"], 0)

    def test_git_internals_are_not_scanned(self) -> None:
        self.write("repo/.git/objects/aa/blob")
        self.write("other/.git/objects/aa/blob")
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 0)

    def test_the_files_of_a_dependency_tree_are_never_opened(self) -> None:
        for i in range(5):
            self.write(f"app{i}/node_modules/pkg/data.bin")
            self.write(f"app{i}/.venv/pyvenv.cfg", b"home = /usr/bin\n")
            self.write(f"app{i}/.venv/Lib/site-packages/pkg/data.bin")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")
        opened: List[str] = []
        real_partial, real_full = duplicates.compute_partial_hash, duplicates.compute_full_sha256

        def partial(path: str, *args: Any, **kwargs: Any) -> str:
            opened.append(self.rel(path))
            return real_partial(path, *args, **kwargs)

        def full(path: str, *args: Any, **kwargs: Any) -> str:
            opened.append(self.rel(path))
            return real_full(path, *args, **kwargs)

        with patch.object(duplicates, "compute_partial_hash", partial), patch.object(
            duplicates, "compute_full_sha256", full
        ):
            DuplicateDetector(str(self.root)).generate_reclamation_plan()

        self.assertTrue(opened)  # the two ordinary copies were compared
        self.assertEqual(sorted(set(opened)), ["backup/copy.bin", "docs/original.bin"])

    def test_dependency_trees_are_scanned_when_asked_to(self) -> None:
        self.write("a/node_modules/x/data.bin")
        self.write("b/node_modules/x/data.bin")
        plan = DuplicateDetector(str(self.root), skip_dependency_dirs=False).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["a/node_modules/x/data.bin", "b/node_modules/x/data.bin"])
        self.assertEqual(plan["skipped_dependency_dirs"], 0)

    def test_the_folder_asked_for_is_scanned_even_when_it_is_a_dependency_tree(self) -> None:
        self.write("node_modules/a/data.bin")
        self.write("node_modules/b/data.bin")
        plan = DuplicateDetector(str(self.root / "node_modules")).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)

        self.write("env/pyvenv.cfg", b"home = /usr/bin\n")
        self.write("env/x/data.bin")
        self.write("env/y/data.bin")
        plan = DuplicateDetector(str(self.root / "env")).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)


class TestExistingScanOptionsKeepWorking(_Tree):
    def test_exclude_dirs_replaces_the_default_exclusions_as_before(self) -> None:
        self.write("skipme/a.bin")
        self.write("keep/b.bin")
        self.write("$RECYCLE.BIN/c.bin")  # excluded by default, but not when the caller passes its own set
        plan = DuplicateDetector(str(self.root), exclude_dirs={"SkipMe"}).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["$RECYCLE.BIN/c.bin", "keep/b.bin"])

    def test_the_default_exclusions_still_apply(self) -> None:
        self.write("$RECYCLE.BIN/a.bin")
        self.write("keep/b.bin")
        self.write("keep2/c.bin")
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["keep/b.bin", "keep2/c.bin"])

    def test_folders_and_files_scanned_are_reported(self) -> None:
        self.write("a/one.bin", b"1")
        self.write("a/b/two.bin", b"22")
        self.write("c/three.bin", b"333")
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual((plan["folders_scanned"], plan["files_scanned"]), (4, 3))  # the root counts as a folder


if __name__ == "__main__":
    unittest.main()
