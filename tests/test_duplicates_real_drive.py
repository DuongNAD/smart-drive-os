"""tests/test_duplicates_real_drive.py - `dup` must not open the files of dependency trees and installed apps.

Regression: `smart-drive dup --json` ran for 15 minutes on a 167 GB NTFS drive (342k files, 40k folders) and
printed nothing. It grouped files by size, then opened and hashed every file of every same-size group, and it walked
into dependency trees full of small files that are copies of one another by design: node_modules (about a thousand
packages), Python virtualenvs and site-packages, caches, and into installed applications. With Windows Defender
scanning each opened file that is ~16 files a second. Such "duplicates" are expected and are never offered for
reclamation.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

import smart_drive.core.duplicates as duplicates
from smart_drive.cli.main import main
from smart_drive.core.duplicates import DEPENDENCY_DIR_NAMES, DuplicateDetector
from smart_drive.core.system_refs import SystemRef, SystemRefIndex

SAME = b"the same bytes in every copy\n" * 20
OTHER = b"a different payload, shared by another set of copies\n" * 10


class _Tree(unittest.TestCase):
    """A temp tree; the machine's own Registry, PATH and shortcuts are never read (an index is injected)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_dup_deps_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        collect = patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect",
            side_effect=AssertionError(
                "SystemRefIndex.collect() must not be called in unit tests; inject system_refs=SystemRefIndex([])"
            ),
        )
        collect.start()
        self.addCleanup(collect.stop)

    def write(self, rel: str, data: bytes = SAME) -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def rel(self, path: str) -> str:
        return Path(path).relative_to(self.root).as_posix()

    def detector(self, root: Optional[Path] = None, **kwargs: Any) -> DuplicateDetector:
        kwargs.setdefault("system_refs", SystemRefIndex([]))
        return DuplicateDetector(str(root or self.root), **kwargs)

    def reported(self, plan: Dict[str, Any]) -> List[str]:
        return sorted(self.rel(f) for g in plan["duplicate_groups"] for f in g["files"])

    def opened_by(self, run: Callable[[], Any]) -> List[str]:
        """The files `run()` opened to hash them (partial or full), as paths relative to the root."""
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
            run()
        return opened

    def run_dup(self, *argv: str) -> Tuple[int, str, str]:
        """`smart-drive dup --root <tree> ...` in-process: (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect", return_value=SystemRefIndex([])
        ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["dup", "--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()


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

        plan = self.detector().generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["backup/copy.bin", "docs/original.bin"])
        self.assertEqual(plan["duplicate_group_count"], 1)
        self.assertEqual(plan["skipped_dependency_dirs"], len(self.TREES))

    def test_the_names_match_whatever_the_letter_case(self) -> None:
        self.write("a/Node_Modules/x/data.bin")
        self.write("b/NODE_MODULES/x/data.bin")
        self.write("c/Site-Packages/x/data.bin")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 0)
        self.assertEqual(plan["skipped_dependency_dirs"], 3)

    def test_a_virtualenv_is_skipped_whatever_it_is_called(self) -> None:
        for env in ("venv", ".venv", "py311-env"):
            self.write(f"proj/{env}/pyvenv.cfg", b"home = /usr/bin\n")
            self.write(f"proj/{env}/Lib/some/module.py")
        self.write("proj/src/module.py")  # the project's own copy
        self.write("archive/module.py")

        plan = self.detector().generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["archive/module.py", "proj/src/module.py"])
        self.assertEqual(plan["skipped_dependency_dirs"], 3)

    def test_a_folder_without_pyvenv_cfg_is_not_taken_for_a_virtualenv(self) -> None:
        self.write("proj/venv/Lib/some/module.py")
        self.write("proj/src/module.py")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["proj/src/module.py", "proj/venv/Lib/some/module.py"])
        self.assertEqual(plan["skipped_dependency_dirs"], 0)

    def test_git_internals_are_not_scanned(self) -> None:
        self.write("repo/.git/objects/aa/blob")
        self.write("other/.git/objects/aa/blob")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 0)

    def test_the_files_of_a_dependency_tree_are_never_opened(self) -> None:
        for i in range(5):
            self.write(f"app{i}/node_modules/pkg/data.bin")
            self.write(f"app{i}/.venv/pyvenv.cfg", b"home = /usr/bin\n")
            self.write(f"app{i}/.venv/Lib/site-packages/pkg/data.bin")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")

        opened = self.opened_by(lambda: self.detector().generate_reclamation_plan())

        self.assertTrue(opened)  # the two ordinary copies were compared
        self.assertEqual(sorted(set(opened)), ["backup/copy.bin", "docs/original.bin"])

    def test_dependency_trees_are_scanned_when_asked_to(self) -> None:
        self.write("a/node_modules/x/data.bin")
        self.write("b/node_modules/x/data.bin")
        plan = self.detector(skip_dependency_dirs=False).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["a/node_modules/x/data.bin", "b/node_modules/x/data.bin"])
        self.assertEqual(plan["skipped_dependency_dirs"], 0)

    def test_the_folder_asked_for_is_scanned_even_when_it_is_a_dependency_tree(self) -> None:
        self.write("node_modules/a/data.bin")
        self.write("node_modules/b/data.bin")
        plan = self.detector(self.root / "node_modules").generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)

        self.write("env/pyvenv.cfg", b"home = /usr/bin\n")
        self.write("env/x/data.bin")
        self.write("env/y/data.bin")
        plan = self.detector(self.root / "env").generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)


class TestFoldersUsedBySystemAreNotScanned(_Tree):
    """Windows and installed apps depend on these folders: nothing in them is ever offered for reclamation."""

    def test_folders_windows_or_installed_apps_depend_on_are_not_scanned(self) -> None:
        for rel in ("App/a.bin", "App/sub/b.bin", "Svc/data/c.bin", "tool/d.bin", "Tools/e.bin"):
            self.write(rel)
        self.write("tool/unins000.exe", b"MZ")  # an uninstaller marks an installed app, no registry entry needed
        self.write("docs/original.bin")
        self.write("backup/copy.bin")
        index = SystemRefIndex([
            SystemRef(kind="installed_app", source='installed app "Demo"', target=str(self.root / "App")),
            SystemRef(kind="service", source="service DemoSvc", target=str(self.root / "Svc" / "Binn" / "svc.exe")),
            SystemRef(kind="path", source="PATH (User)", target=str(self.root / "Tools" / "bin")),
        ])

        plan = self.detector(system_refs=index).generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["backup/copy.bin", "docs/original.bin"])  # the copies inside are no group
        self.assertEqual(plan["skipped_protected_dirs"], 4)
        why = {Path(e["path"]).name: e["refs"][0]["kind"] for e in plan["protected_by_system"]}
        self.assertEqual(why, {"App": "installed_app", "Svc": "service", "tool": "uninstaller", "Tools": "path"})

    def test_a_protected_folder_is_not_entered_so_its_files_are_never_opened(self) -> None:
        for i in range(6):
            self.write(f"App/lib{i}/data.bin")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")
        index = SystemRefIndex([SystemRef(kind="shortcut", source="Demo.lnk", target=str(self.root / "App" / "demo.exe"))])

        opened = self.opened_by(lambda: self.detector(system_refs=index).generate_reclamation_plan())

        self.assertEqual(sorted(set(opened)), ["backup/copy.bin", "docs/original.bin"])

    def test_references_to_other_places_protect_nothing(self) -> None:
        self.write("a/x.bin")
        self.write("b/x.bin")
        other = Path(tempfile.gettempdir()) / "sd_dup_elsewhere_never_created"
        index = SystemRefIndex([
            SystemRef(kind="installed_app", source='installed app "Far"', target=str(other)),
            SystemRef(kind="path", source="PATH", target=str(other / "bin")),
        ])
        plan = self.detector(system_refs=index).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["a/x.bin", "b/x.bin"])
        self.assertEqual((plan["skipped_protected_dirs"], plan["protected_by_system"]), (0, []))

    def test_the_scanned_root_is_judged_only_by_what_lies_below_it(self) -> None:
        # A drive root that is itself on PATH: its folders and documents are still scanned, but a script Windows can
        # start by name from there is pinned, as `organize` pins it
        self.write("a.bin")
        self.write("sub/a_copy.bin")
        self.write("launch.ps1", OTHER)
        self.write("sub/launch_copy.ps1", OTHER)
        index = SystemRefIndex([SystemRef(kind="path", source="PATH (User)", target=str(self.root))])

        plan = self.detector(system_refs=index).generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["a.bin", "sub/a_copy.bin"])
        self.assertEqual([Path(e["path"]).name for e in plan["protected_by_system"]], ["launch.ps1"])
        self.assertEqual(plan["skipped_protected_dirs"], 0)  # a file, not a folder

    def test_references_are_collected_once_for_the_scanned_root(self) -> None:
        for rel in ("a/x.bin", "b/x.bin", "c/d/x.bin"):
            self.write(rel)
        fake = SystemRefIndex([SystemRef(kind="installed_app", source="app", target=str(self.root / "c"))])
        with patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=fake) as collect:
            det = DuplicateDetector(str(self.root))
            first = det.generate_reclamation_plan()
            second = det.generate_reclamation_plan()
        collect.assert_called_once_with(shortcut_roots=[str(self.root)])
        self.assertEqual(self.reported(first), ["a/x.bin", "b/x.bin"])
        self.assertEqual(self.reported(second), ["a/x.bin", "b/x.bin"])
        self.assertEqual(len(second["protected_by_system"]), 1)  # reported per run, not piled up

    def test_nothing_is_collected_when_an_index_is_given(self) -> None:
        self.write("a/x.bin")
        self.write("b/x.bin")
        self.detector().generate_reclamation_plan()  # the guard in setUp would raise


class TestMinSize(_Tree):
    """Cheap work first: files below the minimum are dropped by size alone, before anything is opened."""

    def seed(self) -> None:
        for rel in ("a/small1", "b/small2"):
            self.write(rel, b"s" * 40)
        for rel in ("a/big1", "b/big2"):
            self.write(rel, b"B" * 5000)
        for rel in ("a/empty1", "b/empty2"):
            self.write(rel, b"")

    def test_files_below_the_minimum_are_neither_grouped_nor_opened(self) -> None:
        self.seed()
        plan: Dict[str, Any] = {}
        opened = self.opened_by(lambda: plan.update(self.detector(min_size=1000).generate_reclamation_plan()))

        self.assertEqual(self.reported(plan), ["a/big1", "b/big2"])
        self.assertEqual(sorted(set(opened)), ["a/big1", "b/big2"])
        self.assertEqual((plan["min_size"], plan["files_below_min_size"]), (1000, 4))
        self.assertEqual(plan["total_reclaimable_bytes"], 5000)

    def test_by_default_empty_files_are_grouped_but_never_opened(self) -> None:
        self.seed()
        plan: Dict[str, Any] = {}
        opened = self.opened_by(lambda: plan.update(self.detector().generate_reclamation_plan()))

        self.assertEqual(sorted(g["size"] for g in plan["duplicate_groups"]), [0, 40, 5000])
        empty = next(g for g in plan["duplicate_groups"] if g["size"] == 0)
        self.assertEqual((empty["reclaimable_bytes"], empty["reclaimable_slack"]), (0, 0))
        self.assertFalse([name for name in opened if "empty" in name])
        self.assertEqual((plan["min_size"], plan["files_below_min_size"]), (0, 0))

    def test_a_minimum_of_one_byte_leaves_out_the_empty_files(self) -> None:
        self.seed()
        plan = self.detector(min_size=1).generate_reclamation_plan()
        self.assertEqual(sorted(g["size"] for g in plan["duplicate_groups"]), [40, 5000])
        self.assertEqual(plan["files_below_min_size"], 2)

    def test_a_negative_minimum_means_no_minimum(self) -> None:
        self.assertEqual(self.detector(min_size=-5).min_size, 0)


class TestDupCommandMinSize(_Tree):
    def seed(self) -> None:
        for rel in ("a/small1", "b/small2"):
            self.write(rel, b"s" * 40)
        for rel in ("a/big1", "b/big2"):
            self.write(rel, b"B" * 5000)

    def test_min_size_flag_filters_the_json_plan(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--json", "--min-size", "1000")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertEqual(self.reported(plan), ["a/big1", "b/big2"])
        self.assertEqual(plan["min_size"], 1000)

    def test_every_file_counts_without_the_flag(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--json")
        self.assertEqual(code, 0)
        self.assertEqual(self.reported(json.loads(out)), ["a/big1", "a/small1", "b/big2", "b/small2"])

    def test_min_size_flag_in_text_mode(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--min-size", "1000")
        self.assertEqual(code, 0)
        self.assertIn("Found 1 duplicate groups (2 files)", out)

    def test_a_negative_minimum_is_ignored_and_a_word_is_refused(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--json", "--min-size", "-5")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["min_size"], 0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
            self.run_dup("--min-size", "lots")
        self.assertEqual(refused.exception.code, 2)


class TestExistingScanOptionsKeepWorking(_Tree):
    def test_exclude_dirs_replaces_the_default_exclusions_as_before(self) -> None:
        self.write("skipme/a.bin")
        self.write("keep/b.bin")
        self.write("$RECYCLE.BIN/c.bin")  # excluded by default, but not when the caller passes its own set
        plan = self.detector(exclude_dirs={"SkipMe"}).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["$RECYCLE.BIN/c.bin", "keep/b.bin"])

    def test_the_default_exclusions_still_apply(self) -> None:
        self.write("$RECYCLE.BIN/a.bin")
        self.write("keep/b.bin")
        self.write("keep2/c.bin")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["keep/b.bin", "keep2/c.bin"])

    def test_folders_and_files_scanned_are_reported(self) -> None:
        self.write("a/one.bin", b"1")
        self.write("a/b/two.bin", b"22")
        self.write("c/three.bin", b"333")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual((plan["folders_scanned"], plan["files_scanned"]), (4, 3))  # the root counts as a folder


if __name__ == "__main__":
    unittest.main()
