"""tests/test_duplicates_sub_dir_scope.py - a scan of one folder of a drive is judged like the scan of the whole drive.

Regression: `ssd_find_duplicates(sub_dir=...)` made the sub-folder the DuplicateDetector's root, and the root is never
judged, so `sub_dir="Git"` (E:\\Git is protected_by_system) or a folder inside `node_modules` hashed what the scan
of the whole drive does not enter. With `drive_root` the detector judges the folders from the drive root down to its
own root (a dependency tree, or something Windows or an installed app depends on), and scans nothing when one of
them is.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import patch

import smart_drive.core.duplicates as duplicates
from smart_drive.core.config import SCAN_SKIPPED_DEPENDENCY_TREE, SCAN_SKIPPED_PROTECTED_BY_SYSTEM
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.system_refs import SystemRef, SystemRefIndex

SAME = b"the same bytes in every copy\n" * 20
OTHER = b"a different payload, shared by another set of copies\n" * 10


class _Drive(unittest.TestCase):
    """A temp drive; the machine's own Registry, PATH and shortcuts are never read (an index is injected)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_dup_scope_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        collect = patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect",
            side_effect=AssertionError("SystemRefIndex.collect() must not be called; inject system_refs"),
        )
        collect.start()
        self.addCleanup(collect.stop)
        self.git = SystemRef(
            kind="installed_app", source='installed app "Git"', target=str(self.root / "Git" / "cmd" / "git.exe")
        )

    def write(self, rel: str, data: bytes = SAME) -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def detector(self, sub: str = "", *refs: SystemRef, **kwargs: Any) -> DuplicateDetector:
        """A detector for the folder `sub` of the drive (the drive itself when empty), told where the drive begins."""
        target = self.root.joinpath(*sub.split("/")) if sub else self.root
        kwargs.setdefault("system_refs", SystemRefIndex(list(refs)))
        kwargs.setdefault("drive_root", str(self.root))
        return DuplicateDetector(str(target), **kwargs)

    def rel(self, path: str) -> str:
        return Path(path).relative_to(self.root).as_posix()

    def reported(self, plan: Dict[str, Any]) -> List[str]:
        return sorted(self.rel(f) for g in plan["duplicate_groups"] for f in g["files"])

    def opened_by(self, run: Callable[[], Any]) -> List[str]:
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


class TestInsideAFolderTheSystemDependsOn(_Drive):
    def seed(self) -> None:
        self.write("Git/cmd/git.exe", b"MZ")
        self.write("Git/usr/a.bin")
        self.write("Git/usr/sub/b.bin")  # the copies would be a group, if the folder were scanned
        self.write("docs/original.bin")
        self.write("backup/copy.bin")

    def test_a_folder_inside_an_installed_application_is_not_scanned(self) -> None:
        self.seed()
        plan: Dict[str, Any] = {}
        opened = self.opened_by(lambda: plan.update(self.detector("Git/usr", self.git).generate_reclamation_plan()))
        self.assertEqual(opened, [])
        self.assertEqual(plan["duplicate_groups"], [])
        skipped = plan["scan_skipped"]
        self.assertEqual(Path(skipped["folder"]), self.root / "Git")
        self.assertEqual(skipped["reason"], SCAN_SKIPPED_PROTECTED_BY_SYSTEM)
        self.assertIn('installed app "Git"', skipped["detail"])
        self.assertEqual((plan["folders_scanned"], plan["files_scanned"]), (0, 0))

    def test_the_folder_that_kept_the_scan_from_starting_is_counted_and_listed(self) -> None:
        self.seed()
        plan = self.detector("Git/usr", self.git).generate_reclamation_plan()
        self.assertEqual(plan["skipped_protected_dirs"], 1)
        self.assertEqual(plan["skipped_dependency_dirs"], 0)
        (entry,) = plan["protected_by_system"]
        self.assertEqual(Path(entry["path"]), self.root / "Git")
        self.assertEqual(entry["refs"], [{"kind": "installed_app", "source": 'installed app "Git"'}])

    def test_the_protected_folder_itself_is_not_scanned_either(self) -> None:
        self.seed()
        plan = self.detector("Git", self.git).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_groups"], [])
        self.assertEqual(Path(plan["scan_skipped"]["folder"]), self.root / "Git")

    def test_the_outermost_protected_folder_is_the_one_named(self) -> None:
        self.seed()
        inner = SystemRef(kind="path", source="PATH (User)", target=str(self.root / "Git" / "usr" / "sub"))
        plan = self.detector("Git/usr/sub", inner, self.git).generate_reclamation_plan()
        self.assertEqual(Path(plan["scan_skipped"]["folder"]), self.root / "Git")

    def test_a_folder_holding_an_uninstaller_protects_what_is_inside_it(self) -> None:
        self.write("Tool/unins000.exe", b"MZ")
        self.write("Tool/lib/a.bin")
        self.write("Tool/lib/b.bin")
        plan = self.detector("Tool/lib").generate_reclamation_plan()
        self.assertEqual(Path(plan["scan_skipped"]["folder"]), self.root / "Tool")
        self.assertEqual(plan["duplicate_groups"], [])

    def test_a_folder_beside_the_application_is_scanned(self) -> None:
        self.seed()
        self.write("backup/more/copy2.bin")
        plan = self.detector("backup", self.git).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(plan["duplicate_group_count"], 1)
        self.assertEqual(self.reported(plan), ["backup/copy.bin", "backup/more/copy2.bin"])
        self.assertEqual(plan["protected_by_system"], [])

    def test_the_drive_root_itself_is_scanned_with_the_application_left_out(self) -> None:
        self.seed()
        plan = self.detector("", self.git).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(self.reported(plan), ["backup/copy.bin", "docs/original.bin"])
        self.assertEqual(plan["skipped_protected_dirs"], 1)

    def test_a_sub_folder_finds_what_the_whole_drive_scan_finds_in_it(self) -> None:
        self.write("work/a/one.bin")
        self.write("work/b/two.bin")
        self.write("work/c/three.bin", OTHER)
        self.write("elsewhere/four.bin")
        whole = self.detector("", self.git).generate_reclamation_plan()
        inside = self.detector("work", self.git).generate_reclamation_plan()
        self.assertEqual(self.reported(whole), ["elsewhere/four.bin", "work/a/one.bin", "work/b/two.bin"])
        self.assertEqual(self.reported(inside), ["work/a/one.bin", "work/b/two.bin"])

    def test_the_drive_root_and_what_is_above_it_are_never_judged(self) -> None:
        # a drive root on PATH, or called node_modules, protects nothing
        self.write("node_modules/Drive/pkg/a.bin")
        self.write("node_modules/Drive/pkg/b.bin")
        drive = self.root / "node_modules" / "Drive"
        on_path = SystemRef(kind="path", source="PATH (User)", target=str(drive))
        plan = DuplicateDetector(
            str(drive / "pkg"), drive_root=str(drive), system_refs=SystemRefIndex([on_path])
        ).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(plan["duplicate_group_count"], 1)

    def test_references_are_collected_once_with_the_drive_as_a_shortcut_root(self) -> None:
        self.write("Git/cmd/git.exe", b"MZ")
        self.write("Git/usr/a.bin")
        self.write("Git/usr/b.bin")
        fake = SystemRefIndex([self.git])
        with patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=fake) as collect:
            detector = DuplicateDetector(str(self.root / "Git" / "usr"), drive_root=str(self.root))
            first = detector.generate_reclamation_plan()
            second = detector.generate_reclamation_plan()
        collect.assert_called_once_with(shortcut_roots=[str(self.root)])
        for plan in (first, second):
            self.assertEqual(Path(plan["scan_skipped"]["folder"]), self.root / "Git")
        self.assertEqual(second["skipped_protected_dirs"], 1)  # reported per run, not piled up

    def test_without_a_drive_root_the_folder_asked_for_is_scanned_as_before(self) -> None:
        self.seed()
        plan = DuplicateDetector(
            str(self.root / "Git" / "usr"), system_refs=SystemRefIndex([self.git])
        ).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(self.reported(plan), ["Git/usr/a.bin", "Git/usr/sub/b.bin"])

    def test_a_drive_root_the_folder_is_not_in_changes_nothing(self) -> None:
        self.seed()
        elsewhere = Path(tempfile.mkdtemp(prefix="sd_dup_scope_other_")).resolve()
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        plan = DuplicateDetector(
            str(self.root / "Git" / "usr"), drive_root=str(elsewhere), system_refs=SystemRefIndex([self.git])
        ).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(plan["duplicate_group_count"], 1)


class TestInsideADependencyTree(_Drive):
    def test_a_folder_inside_node_modules_is_not_scanned(self) -> None:
        self.write("app/node_modules/pkg/a.bin")
        self.write("app/node_modules/pkg/b.bin")
        plan: Dict[str, Any] = {}
        opened = self.opened_by(lambda: plan.update(self.detector("app/node_modules/pkg").generate_reclamation_plan()))
        self.assertEqual(opened, [])
        skipped = plan["scan_skipped"]
        self.assertEqual(Path(skipped["folder"]), self.root / "app" / "node_modules")
        self.assertEqual(skipped["reason"], SCAN_SKIPPED_DEPENDENCY_TREE)
        self.assertIn("node_modules", skipped["detail"])
        self.assertEqual((plan["skipped_dependency_dirs"], plan["skipped_protected_dirs"]), (1, 0))

    def test_the_folder_asked_for_is_judged_too(self) -> None:
        self.write("app/node_modules/a/x.bin")
        self.write("app/node_modules/b/x.bin")
        plan = self.detector("app/node_modules").generate_reclamation_plan()
        self.assertEqual(Path(plan["scan_skipped"]["folder"]), self.root / "app" / "node_modules")
        self.assertEqual(plan["duplicate_groups"], [])

    def test_every_listed_tree_keeps_a_scan_inside_it_from_starting(self) -> None:
        for name in sorted(duplicates.DEPENDENCY_DIR_NAMES):
            with self.subTest(tree=name):
                self.write(f"p/{name}/x/one.bin")
                self.write(f"p/{name}/x/two.bin")
                plan = self.detector(f"p/{name}/x").generate_reclamation_plan()
                self.assertEqual(Path(plan["scan_skipped"]["folder"]), self.root / "p" / name)
                self.assertEqual(plan["scan_skipped"]["reason"], SCAN_SKIPPED_DEPENDENCY_TREE)

    def test_the_letter_case_of_the_name_does_not_matter(self) -> None:
        self.write("a/Node_Modules/x/one.bin")
        self.write("a/Node_Modules/x/two.bin")
        plan = self.detector("a/Node_Modules/x").generate_reclamation_plan()
        self.assertEqual(Path(plan["scan_skipped"]["folder"]).name, "Node_Modules")

    def test_a_virtualenv_is_recognised_whatever_it_is_called(self) -> None:
        self.write("proj/py311-env/pyvenv.cfg", b"home = /usr/bin\n")
        self.write("proj/py311-env/Lib/mod/one.bin")
        self.write("proj/py311-env/Lib/mod/two.bin")
        plan = self.detector("proj/py311-env/Lib/mod").generate_reclamation_plan()
        skipped = plan["scan_skipped"]
        self.assertEqual(Path(skipped["folder"]), self.root / "proj" / "py311-env")
        self.assertEqual(skipped["reason"], SCAN_SKIPPED_DEPENDENCY_TREE)
        self.assertIn("virtual environment", skipped["detail"])

    def test_a_cache_directory_tagged_with_cachedir_tag_is_recognised(self) -> None:
        self.write("crate/target/CACHEDIR.TAG", duplicates.CACHE_TAG_SIGNATURE + b"\n# created by cargo\n")
        self.write("crate/target/debug/one.bin")
        self.write("crate/target/debug/two.bin")
        plan = self.detector("crate/target/debug").generate_reclamation_plan()
        skipped = plan["scan_skipped"]
        self.assertEqual(Path(skipped["folder"]), self.root / "crate" / "target")
        self.assertIn("CACHEDIR.TAG", skipped["detail"])

    def test_a_cachedir_tag_without_the_signature_and_a_folder_without_pyvenv_cfg_are_only_folders(self) -> None:
        self.write("one/target/CACHEDIR.TAG", b"just a note")
        self.write("one/target/debug/a.bin")
        self.write("one/target/debug/b.bin")
        self.write("two/venv/Lib/a.bin")
        self.write("two/venv/Lib/b.bin")
        for sub in ("one/target/debug", "two/venv/Lib"):
            with self.subTest(sub=sub):
                plan = self.detector(sub).generate_reclamation_plan()
                self.assertIsNone(plan["scan_skipped"])
                self.assertEqual(plan["duplicate_group_count"], 1)

    def test_the_switch_that_turns_the_rule_off_applies_to_the_folders_above_too(self) -> None:
        self.write("app/node_modules/pkg/a.bin")
        self.write("app/node_modules/pkg/b.bin")
        plan = self.detector("app/node_modules/pkg", skip_dependency_dirs=False).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(plan["duplicate_group_count"], 1)

    def test_a_dependency_tree_beyond_the_drive_root_is_not_judged(self) -> None:
        self.write("node_modules/Drive/pkg/a.bin")
        self.write("node_modules/Drive/pkg/b.bin")
        drive = self.root / "node_modules" / "Drive"
        plan = DuplicateDetector(
            str(drive / "pkg"), drive_root=str(drive), system_refs=SystemRefIndex([])
        ).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])

    def test_the_names_a_drive_wide_scan_leaves_out_are_not_a_reason_to_refuse_a_folder(self) -> None:
        # `Downloads` is in exclude_dirs, which is a choice about walking the whole drive; somebody asked for this one
        self.write("Downloads/stuff/a.bin")
        self.write("Downloads/stuff/b.bin")
        plan = self.detector("Downloads/stuff").generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(plan["duplicate_group_count"], 1)


class TestAFreeFolderIsAnOrdinaryScan(_Drive):
    def test_the_plan_of_a_scan_that_was_made_says_it_was_not_skipped(self) -> None:
        self.write("a/x.bin")
        self.write("b/x.bin")
        plan = self.detector("", min_size=0).generate_reclamation_plan()
        self.assertIsNone(plan["scan_skipped"])
        self.assertEqual(plan["duplicate_group_count"], 1)

    def test_a_scan_that_was_skipped_hashes_nothing_and_counts_no_file(self) -> None:
        self.write("node_modules/p/x.bin")
        self.write("node_modules/p/y.bin")
        detector = self.detector("node_modules/p")
        self.assertEqual(list(detector._walk_files()), [])
        self.assertEqual((detector.files_scanned, detector.folders_scanned), (0, 0))


if __name__ == "__main__":
    unittest.main()
