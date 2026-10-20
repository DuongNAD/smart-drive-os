"""tests/test_junk_sub_path_scope.py - a scan of one folder is judged like the scan of the whole drive.

Regression: the MCP tool `ssd_clean(sub_dir=...)` made the sub-folder the JunkDetector's drive root, so the folders
above it were never asked about. `sub_dir="Git/usr"` (E:\\Git is protected_by_system: PATH, an installed app, shortcuts,
an uninstaller) walked an installed program and listed what it found, and every path the checks and the report use
was relative to the sub-folder instead of the drive. `find_junk(sub_path)` now keeps the drive root, judges the
folders from there down to the sub-folder, and scans nothing when one of them is something the system depends on.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, List, Set, Tuple
from unittest.mock import patch

from smart_drive.core import junk_detector
from smart_drive.core.config import SCAN_SKIPPED_PROTECTED_BY_SYSTEM, JunkTier
from smart_drive.core.junk_detector import KEPT_DEPENDENCY_TREE, KEPT_SYSTEM_REFERENCED, JunkDetector
from smart_drive.core.scanner import FastDirectoryScanner
from smart_drive.core.system_refs import SystemRef, SystemRefIndex


class _Drive(unittest.TestCase):
    """A drive with an installed program (Git), a project and a folder of its own, all holding junk."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_sub_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.write("Git/cmd/git.exe", "MZ")
        self.write("Git/usr/lib/__pycache__/m.pyc")
        self.write("Git/usr/share/Thumbs.db")
        self.write("Git/usr/share/deep/er/Thumbs.db")
        self.write("proj/package.json", "{}")
        self.write("proj/__pycache__/m.pyc")
        self.write("proj/dist/app.js")
        self.write("proj/sub/Thumbs.db")
        self.write("proj/sub/DumpStack.log.tmp")  # protected at the drive root only
        self.write("proj/node_modules/pkg/__pycache__/m.pyc")
        self.write("proj/node_modules/pkg/dist/index.js")
        self.write("other/Thumbs.db")
        self.git = SystemRef(
            kind="installed_app", source='installed app "Git"', target=str(self.root / "Git/cmd/git.exe")
        )

    def write(self, rel: str, data: str = "x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def detector(self, *refs: SystemRef, tier: JunkTier = JunkTier.TIER_3_SENSITIVE) -> JunkDetector:
        return JunkDetector(str(self.root), max_tier=tier, system_refs=SystemRefIndex(list(refs)))

    def scan(self, detector: JunkDetector, sub: str = "") -> Set[Tuple[str, int]]:
        """(rel_path, tier) of what a scan of `sub` (the whole drive when empty) reports."""
        sub_path = str(self.root.joinpath(*sub.split("/"))) if sub else None
        return {(item.rel_path.replace("\\", "/"), int(item.tier)) for item in detector.find_junk(sub_path)}


class TestInsideAFolderTheSystemDependsOn(_Drive):
    def never_scanned(self) -> Any:
        return patch.object(junk_detector, "FastDirectoryScanner", side_effect=AssertionError("the folder was walked"))

    def test_a_folder_inside_an_installed_application_is_not_scanned(self) -> None:
        detector = self.detector(self.git)
        with self.never_scanned():
            self.assertEqual(detector.find_junk(str(self.root / "Git" / "usr")), [])
        skipped = detector.scan_skipped
        self.assertIsNotNone(skipped)
        self.assertEqual(Path(skipped["folder"]), self.root / "Git")
        self.assertEqual(skipped["reason"], SCAN_SKIPPED_PROTECTED_BY_SYSTEM)
        self.assertIn('installed app "Git"', skipped["detail"])

    def test_the_protected_folder_itself_is_not_scanned_either(self) -> None:
        detector = self.detector(self.git)
        with self.never_scanned():
            self.assertEqual(detector.find_junk(str(self.root / "Git")), [])
        self.assertEqual(Path(detector.scan_skipped["folder"]), self.root / "Git")

    def test_a_folder_deep_inside_is_not_scanned_and_the_outermost_folder_is_named(self) -> None:
        nested = SystemRef(kind="path", source="PATH (User)", target=str(self.root / "Git/usr/share/deep"))
        detector = self.detector(nested, self.git)
        with self.never_scanned():
            self.assertEqual(detector.find_junk(str(self.root / "Git" / "usr" / "share" / "deep" / "er")), [])
        self.assertEqual(Path(detector.scan_skipped["folder"]), self.root / "Git")

    def test_the_folder_that_kept_the_scan_from_starting_is_listed_like_any_protected_folder(self) -> None:
        detector = self.detector(self.git)
        detector.find_junk(str(self.root / "Git" / "usr"))
        (entry,) = detector.protected_by_system
        self.assertEqual(Path(entry["path"]), self.root / "Git")
        self.assertEqual(entry["refs"], [{"kind": "installed_app", "source": 'installed app "Git"'}])
        self.assertEqual(entry["used_by"], detector.scan_skipped["detail"])
        self.assertEqual((entry["kept_items"], detector.kept), (0, {}))  # nothing was looked at, so nothing was kept

    def test_a_folder_holding_an_uninstaller_protects_what_is_inside_it(self) -> None:
        self.write("Tool/unins000.exe", "MZ")
        self.write("Tool/lib/__pycache__/m.pyc")
        detector = self.detector()
        with self.never_scanned():
            self.assertEqual(detector.find_junk(str(self.root / "Tool" / "lib")), [])
        self.assertEqual(Path(detector.scan_skipped["folder"]), self.root / "Tool")

    def test_the_whole_drive_scan_reports_nothing_there_either(self) -> None:
        found = self.scan(self.detector(self.git))
        self.assertFalse([path for path, _ in found if path.startswith("Git/")])


class TestOtherFoldersAreScanned(_Drive):
    def test_a_folder_beside_the_application_is_scanned(self) -> None:
        detector = self.detector(self.git)
        found = self.scan(detector, "other")
        self.assertEqual(found, {("other/Thumbs.db", 1)})
        self.assertIsNone(detector.scan_skipped)
        self.assertEqual(detector.protected_by_system, [])

    def test_a_sub_folder_finds_what_the_whole_drive_scan_finds_in_it(self) -> None:
        whole = self.scan(self.detector(self.git))
        for sub in ("proj", "proj/sub", "other"):
            with self.subTest(sub=sub):
                inside = {(path, tier) for path, tier in whole if path.startswith(sub + "/")}
                self.assertTrue(inside)
                self.assertEqual(self.scan(self.detector(self.git), sub), inside)

    def test_paths_are_relative_to_the_drive_root_not_to_the_sub_folder(self) -> None:
        found = self.scan(self.detector(), "proj/sub")
        self.assertIn(("proj/sub/Thumbs.db", 1), found)
        self.assertTrue(all(path.startswith("proj/sub/") for path, _ in found))

    def test_a_name_that_is_protected_only_at_the_drive_root_is_not_taken_for_one_in_a_sub_folder(self) -> None:
        # DumpStack.log.tmp is a Windows file that is never touched at the drive root; elsewhere it is a temp file
        self.write("DumpStack.log.tmp")
        whole = self.scan(self.detector())
        self.assertNotIn("DumpStack.log.tmp", {path for path, _ in whole})
        self.assertIn(("proj/sub/DumpStack.log.tmp", 3), whole)
        self.assertIn(("proj/sub/DumpStack.log.tmp", 3), self.scan(self.detector(), "proj/sub"))

    def test_the_drive_root_is_never_judged_so_a_path_entry_on_it_protects_nothing(self) -> None:
        on_path = SystemRef(kind="path", source="PATH (User)", target=str(self.root))
        detector = self.detector(on_path)
        self.assertIn(("proj/__pycache__", 1), self.scan(detector, "proj"))
        self.assertIsNone(detector.scan_skipped)
        self.assertIn(("other/Thumbs.db", 1), self.scan(detector))  # (and neither does it for the whole drive)

    def test_scanning_the_drive_root_by_name_is_the_whole_scan(self) -> None:
        detector = self.detector(self.git)
        self.assertEqual(
            {(item.rel_path.replace("\\", "/"), int(item.tier)) for item in detector.find_junk(str(self.root))},
            self.scan(self.detector(self.git)),
        )

    def test_a_sub_folder_inside_a_learning_unit_is_protected_as_in_the_whole_scan(self) -> None:
        # (the unit's own folder lies above the sub-folder, where the old sub-folder root never looked)
        self.write("course/course.json", "{}")
        self.write("course/assets/Thumbs.db")
        self.assertNotIn(("course/assets/Thumbs.db", 1), self.scan(self.detector()))
        detector = self.detector()
        self.assertEqual(self.scan(detector, "course/assets"), set())
        self.assertIsNone(detector.scan_skipped)  # (a unit is protected item by item, not skipped as a whole)

    def test_a_scan_does_not_remember_the_one_before(self) -> None:
        detector = self.detector(self.git)
        detector.find_junk(str(self.root / "Git" / "usr"))
        self.assertIsNotNone(detector.scan_skipped)
        self.assertTrue(detector.find_junk(str(self.root / "other")))
        self.assertIsNone(detector.scan_skipped)
        self.assertEqual((detector.protected_by_system, detector.kept), ([], {}))

    def test_a_folder_that_does_not_exist_finds_nothing(self) -> None:
        detector = self.detector(self.git)
        self.assertEqual(detector.find_junk(str(self.root / "nowhere")), [])
        self.assertIsNone(detector.scan_skipped)


class TestInsideADependencyTree(_Drive):
    def test_tier_2_and_3_stay_kept_there_and_tier_1_is_reported_as_in_the_whole_scan(self) -> None:
        detector = self.detector()
        found = self.scan(detector, "proj/node_modules/pkg")
        self.assertEqual(found, {("proj/node_modules/pkg/__pycache__", 1)})
        self.assertEqual(detector.kept, {KEPT_DEPENDENCY_TREE: 1})  # pkg/dist
        self.assertIsNone(detector.scan_skipped)  # (OS metadata and bytecode are junk there, so the scan is made)

    def test_the_whole_scan_agrees(self) -> None:
        found = self.scan(self.detector(), "")
        self.assertIn(("proj/node_modules/pkg/__pycache__", 1), found)
        self.assertNotIn(("proj/node_modules/pkg/dist", 2), found)


class TestInspectEntryKeepsItsShape(_Drive):
    """`inspect_entry(entry)` is wrapped by other code (and tests) as a method of one argument: it stays one."""

    def seen_by_inspect_entry(self, sub: str = "") -> List[str]:
        seen: List[str] = []
        original = JunkDetector.inspect_entry

        def recording(self_: JunkDetector, entry: Any) -> Any:
            seen.append(entry.rel_path)
            return original(self_, entry)

        with patch.object(JunkDetector, "inspect_entry", recording):
            self.scan(self.detector(), sub)
        return seen

    def test_an_entry_is_inspected_with_the_path_it_carries(self) -> None:
        entries: List[Any] = [
            e for e in FastDirectoryScanner(str(self.root / "other"), follow_symlinks=False).scan_iter()
            if e.name == "Thumbs.db"
        ]
        self.assertEqual(len(entries), 1)
        item = self.detector().inspect_entry(entries[0])
        self.assertEqual(item.rel_path, "Thumbs.db")  # (the path the scanner gave it, relative to its root)

    def test_a_scan_of_the_drive_hands_it_the_paths_the_scanner_made(self) -> None:
        seen = self.seen_by_inspect_entry()
        for expected in ("proj", "proj/sub", "proj/sub/Thumbs.db", "other/Thumbs.db"):
            self.assertIn(expected, seen)

    def test_a_scan_below_the_drive_root_hands_it_paths_relative_to_the_drive(self) -> None:
        self.assertEqual(
            sorted(self.seen_by_inspect_entry("proj/sub")), ["proj/sub/DumpStack.log.tmp", "proj/sub/Thumbs.db"]
        )


class TestDriveScanIsUnchanged(_Drive):
    def test_the_references_are_not_read_for_a_scan_of_the_drive_root_that_finds_nothing(self) -> None:
        empty = Path(tempfile.mkdtemp(prefix="sd_junk_sub_empty_")).resolve()
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        (empty / "docs").mkdir()
        (empty / "docs" / "notes.txt").write_text("x", encoding="utf-8")
        with patch.object(SystemRefIndex, "collect", side_effect=AssertionError("read the registry for nothing")):
            self.assertEqual(JunkDetector(str(empty)).find_junk(), [])

    def test_the_kept_counts_of_a_whole_scan_still_name_the_application(self) -> None:
        detector = self.detector(self.git)
        self.scan(detector)
        self.assertEqual(detector.kept.get(KEPT_SYSTEM_REFERENCED), 3)  # pycache, and two Thumbs.db files
        (entry,) = detector.protected_by_system
        self.assertEqual((Path(entry["path"]), entry["kept_items"]), (self.root / "Git", 3))


if __name__ == "__main__":
    unittest.main()
