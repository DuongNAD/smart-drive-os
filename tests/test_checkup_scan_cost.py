"""tests/test_checkup_scan_cost.py - `checkup` must not repeat per-file / per-folder filesystem work.

Regression: a real checkup of a 167 GB NTFS drive took 571 s. Besides the junk scan (see test_junk_detector_prefilter),
profiling (984 s under cProfile) showed:
- find_unit_root re-resolved every ancestor of every path it was asked about (`unit_kind` = Path.resolve() = two
  _getfinalpathname calls, plus is_dir and three is_file) although the ancestors of a resolved path are resolved;
- the audit normalised the parent folder of every file again (two normpath calls and a relpath per file);
- the learning-unit summary ran the same resolve + four stats in every folder of the drive;
- AutoZoner sized each loose root folder with os.walk plus a getsize (stat) per file.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Callable, List
from unittest.mock import patch

from smart_drive.core import auditor as auditor_module
from smart_drive.core import learning_units
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.learning_units import clear_unit_cache, find_unit_root, get_learning_summary

CLUSTER = 4096


class TmpTreeCase(unittest.TestCase):
    def setUp(self) -> None:
        clear_unit_cache()
        self.addCleanup(clear_unit_cache)
        self.root = Path(tempfile.mkdtemp(prefix="sd_scan_cost_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel: str, data: bytes = b"x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class TestFindUnitRootDoesNotResolveEveryAncestor(TmpTreeCase):
    def test_one_resolve_for_a_deep_path_and_the_unit_is_still_found(self) -> None:
        self.write("course/course.json", b"{}")
        deep = self.write("course/a/b/c/d/e/f/lesson.md")
        resolves: List[str] = []
        real_resolve: Callable[..., Path] = pathlib.Path.resolve

        def counting(self_: Path, *args: object, **kwargs: object) -> Path:
            resolves.append(str(self_))
            return real_resolve(self_, *args, **kwargs)

        with patch.object(pathlib.Path, "resolve", counting):
            unit = find_unit_root(deep, drive_root=self.root)

        self.assertEqual(unit, self.root / "course")
        self.assertLessEqual(len(resolves), 2, resolves)  # the start path (and the drive root), not 8 ancestors

    def test_paths_outside_any_unit_still_report_none(self) -> None:
        deep = self.write("plain/a/b/c/file.txt")
        self.assertIsNone(find_unit_root(deep, drive_root=self.root))

    def test_topmost_unit_wins_when_units_nest(self) -> None:
        self.write("course/course.json", b"{}")
        self.write("course/lesson/deck.json", b"{}")
        deck_file = self.write("course/lesson/slide.md")
        self.assertEqual(find_unit_root(deck_file, drive_root=self.root), self.root / "course")


class TestLearningSummaryChecksOnlyCandidateFolders(TmpTreeCase):
    def test_unit_kind_is_called_only_for_folders_that_list_a_marker(self) -> None:
        for i in range(40):
            self.write(f"plain{i}/notes.txt")
        self.write("aurora/course.json", b"{}")
        self.write("aurora/lessons/one/deck.json", b"{}")
        self.write("standalone/deck.json", b"{}")
        self.write("subject/polaris/brief.json", b"{}")
        asked: List[str] = []
        real = learning_units.unit_kind

        def counting(root: object) -> object:
            asked.append(os.path.basename(str(root)))
            return real(root)  # type: ignore[arg-type]

        with patch.object(learning_units, "unit_kind", counting):
            summary = get_learning_summary(self.root)

        self.assertEqual(
            (summary["aurora_courses"], summary["aurora_decks"], summary["polaris_subjects"]), (1, 1, 1)
        )
        self.assertEqual(sorted(asked), ["aurora", "standalone", "subject"])


class TestAuditNormalisesEachFolderOnce(TmpTreeCase):
    def test_parent_folder_is_normalised_once_per_folder_not_per_file(self) -> None:
        for folder in ("alpha", "beta/inner"):
            for i in range(25):
                self.write(f"{folder}/f{i}.bin", b"y" * (i + 1))
        calls: List[str] = []
        real = auditor_module.normalize_rel_path

        def counting(path: str, root: str, *args: object, **kwargs: object) -> str:
            calls.append(path)
            return real(path, root, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(auditor_module, "normalize_rel_path", counting):
            report = StorageAuditor(str(self.root), cluster_size=CLUSTER).run_audit()

        self.assertEqual(report["total_files"], 50)
        self.assertLessEqual(len(calls), 3, calls)  # alpha and beta/inner (the scanner's own paths are not re-derived)
        self.assertEqual(report["tree"]["subdirs"]["alpha"]["direct_files"], 25)
        self.assertEqual(report["tree"]["subdirs"]["beta"]["subdirs"]["inner"]["direct_files"], 25)


class TestAutoZonerSizesWithoutStatPerFile(TmpTreeCase):
    def test_folder_size_and_slack_come_from_the_listing(self) -> None:
        self.write("loose_project/a.bin", b"a" * 1000)
        self.write("loose_project/sub/b.bin", b"b" * 5000)
        zoner = AutoZoner(str(self.root), cluster_size=CLUSTER)

        with patch("os.path.getsize", side_effect=AssertionError("a stat per file")):
            nominal, slack = zoner._get_item_size(str(self.root / "loose_project"))

        self.assertEqual(nominal, 6000)
        self.assertEqual(slack, 3 * CLUSTER - 6000)

    def test_single_file_size_is_unchanged(self) -> None:
        path = self.write("one.bin", b"z" * 100)
        nominal, slack = AutoZoner(str(self.root), cluster_size=CLUSTER)._get_item_size(str(path))
        self.assertEqual((nominal, slack), (100, CLUSTER - 100))


if __name__ == "__main__":
    unittest.main()
