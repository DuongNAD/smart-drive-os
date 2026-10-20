"""tests/test_mcp_sub_dir_scope.py - `sub_dir` of ssd_clean and ssd_find_duplicates is judged like the whole drive.

Regression: both tools made the sub-folder the root of their detector, and a detector never judges its own root, so
`ssd_clean {"sub_dir": "Git/usr", "tier": 2}` listed junk inside an installed program (E:\\Git is protected_by_system
for every other command) and `ssd_find_duplicates {"sub_dir": "Git"}` hashed it. They now keep the storage root, judge
the folders from there down to the sub-folder, and answer a scan inside an installed app (or, for duplicates, a
dependency tree) with `scan_skipped`: the folder that kept it from starting, and why. `ssd_clean` also reports what
it kept and which folders the system depends on, as `clean --json` does, and `min_size` of `ssd_find_duplicates` is
handed to the detector, so small files are dropped before anything is hashed.

The machine's own Registry, PATH and shortcuts are never read: an index is injected through SystemRefIndex.collect.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import patch

import smart_drive.core.duplicates as duplicates
from smart_drive.core import junk_detector
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.system_refs import SystemRef, SystemRefIndex
from smart_drive.mcp.server import SmartDriveMCPServer

SAME = b"the same bytes in every copy\n" * 20
OTHER = b"a different payload, shared by another set of copies\n" * 10


class _Storage(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_mcp_sub_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.write("Git/cmd/git.exe", b"MZ")
        self.write("Git/usr/lib/__pycache__/m.pyc")
        self.write("Git/usr/share/Thumbs.db")
        self.write("Git/usr/a.bin", SAME)  # copies, which would make a group if the folder were scanned
        self.write("Git/usr/sub/b.bin", SAME)
        self.write("proj/package.json", b"{}")
        self.write("proj/__pycache__/m.pyc")
        self.write("proj/sub/Thumbs.db")
        self.write("proj/node_modules/pkg/__pycache__/m.pyc")
        self.write("proj/node_modules/pkg/c.bin", OTHER)
        self.write("proj/node_modules/pkg/d.bin", OTHER)
        self.write("proj/e.bin", OTHER)
        self.write("proj/f.bin", OTHER)
        self.write("docs/original.bin", SAME)
        index = SystemRefIndex([
            SystemRef(
                kind="installed_app", source='installed app "Git"', target=str(self.root / "Git" / "cmd" / "git.exe")
            ),
            SystemRef(kind="path", source="PATH (User)", target=str(self.root / "Git" / "cmd")),
        ])
        collect = patch.object(SystemRefIndex, "collect", return_value=index)
        collect.start()
        self.addCleanup(collect.stop)
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def write(self, rel: str, data: bytes = b"x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def clean(self, **args: Any) -> Dict[str, Any]:
        return self.server.handle_ssd_clean({"dry_run": True, "tier": 2, **args})

    def hashed_by(self, run: Callable[[], Any]) -> List[str]:
        hashed: List[str] = []
        real_partial, real_full = duplicates.compute_partial_hash, duplicates.compute_full_sha256

        def partial(path: str, *args: Any, **kwargs: Any) -> str:
            hashed.append(Path(path).relative_to(self.root).as_posix())
            return real_partial(path, *args, **kwargs)

        def full(path: str, *args: Any, **kwargs: Any) -> str:
            hashed.append(Path(path).relative_to(self.root).as_posix())
            return real_full(path, *args, **kwargs)

        with patch.object(duplicates, "compute_partial_hash", partial), patch.object(
            duplicates, "compute_full_sha256", full
        ):
            run()
        return hashed


class TestCleanSubDirInsideAnInstalledApp(_Storage):
    def test_nothing_is_scanned_and_the_result_names_the_folder_and_why(self) -> None:
        with patch.object(junk_detector, "FastDirectoryScanner", side_effect=AssertionError("the folder was walked")):
            res = self.clean(sub_dir="Git/usr")
        self.assertEqual(res["detected_count"], 0)
        self.assertEqual(Path(res["target_root"]), self.root / "Git" / "usr")
        skipped = res["scan_skipped"]
        self.assertEqual(Path(skipped["folder"]), self.root / "Git")
        self.assertEqual(skipped["reason"], "protected_by_system")
        self.assertIn('installed app "Git"', skipped["detail"])
        self.assertIn(str(self.root / "Git"), skipped["message"])
        self.assertIn("Nothing was scanned", skipped["message"])

    def test_the_folder_is_listed_as_protected_by_the_system(self) -> None:
        res = self.clean(sub_dir="Git/usr")
        (entry,) = res["protected_by_system"]
        self.assertEqual(Path(entry["path"]), self.root / "Git")
        self.assertEqual(res["kept"], {"total": 0, "by_reason": {}})

    def test_the_folder_itself_and_every_spelling_of_the_path_are_judged(self) -> None:
        absolute = str(self.root / "Git" / "usr")
        for sub in ("Git", "Git/", "Git/usr/share", absolute, str(self.root / "Git").replace("\\", "/")):
            with self.subTest(sub_dir=sub):
                res = self.clean(sub_dir=sub)
                self.assertEqual(res["detected_count"], 0)
                self.assertEqual(Path(res["scan_skipped"]["folder"]), self.root / "Git")

    def test_the_response_keeps_its_shape(self) -> None:
        res = self.clean(sub_dir="Git/usr")
        for key in (
            "target_root", "dry_run", "tier", "detected_count", "purged_count", "deleted_count", "freed_bytes",
            "quarantined_count", "quarantined_bytes", "batch_id", "nominal_bytes_reclaimed", "slack_bytes_reclaimed",
            "breakdown_by_type", "sample_preview",
        ):
            self.assertIn(key, res)
        self.assertTrue(res["dry_run"])
        self.assertEqual((res["purged_count"], res["sample_preview"], res["breakdown_by_type"]), (0, [], {}))

    def test_apply_deletes_nothing_inside_the_application(self) -> None:
        res = self.server.handle_ssd_clean({"apply": True, "tier": 3, "sub_dir": "Git/usr"})
        self.assertFalse(res["dry_run"])
        self.assertEqual((res["detected_count"], res["deleted_count"], res["quarantined_count"]), (0, 0, 0))
        for rel in ("Git/usr/lib/__pycache__/m.pyc", "Git/usr/share/Thumbs.db", "Git/usr/a.bin"):
            self.assertTrue(self.root.joinpath(*rel.split("/")).is_file(), rel)


class TestCleanOtherScans(_Storage):
    def test_a_sub_dir_outside_the_application_is_scanned_and_says_so(self) -> None:
        res = self.clean(sub_dir="proj")
        self.assertIsNone(res["scan_skipped"])
        self.assertEqual(res["protected_by_system"], [])
        self.assertEqual(
            sorted(res["sample_preview"]),
            ["proj/__pycache__", "proj/node_modules/pkg/__pycache__", "proj/sub/Thumbs.db"],
        )

    def test_the_paths_of_a_sub_dir_scan_are_relative_to_the_storage_root(self) -> None:
        res = self.clean(sub_dir="proj/sub")
        self.assertEqual(res["sample_preview"], ["proj/sub/Thumbs.db"])

    def test_a_sub_dir_finds_what_the_whole_drive_scan_finds_in_it(self) -> None:
        whole = self.clean()
        self.assertEqual(whole["detected_count"], 3)
        self.assertEqual(self.clean(sub_dir="proj")["detected_count"], 3)

    def test_a_sub_dir_inside_a_dependency_tree_reports_what_the_whole_scan_reports_there(self) -> None:
        res = self.clean(sub_dir="proj/node_modules/pkg", tier=3)
        self.assertEqual(res["sample_preview"], ["proj/node_modules/pkg/__pycache__"])  # OS metadata and bytecode only
        self.assertIsNone(res["scan_skipped"])

    def test_a_sub_dir_inside_a_learning_unit_leaves_the_unit_alone(self) -> None:
        self.write("course/course.json", b"{}")
        self.write("course/assets/Thumbs.db")
        self.assertEqual(self.clean()["detected_count"], 3)  # the unit's Thumbs.db is not among them
        res = self.clean(sub_dir="course/assets")
        self.assertEqual((res["detected_count"], res["sample_preview"]), (0, []))

    def test_the_whole_drive_scan_reports_what_it_kept_and_the_folders_the_system_depends_on(self) -> None:
        res = self.clean()
        self.assertIsNone(res["scan_skipped"])
        self.assertEqual(res["kept"]["total"], sum(res["kept"]["by_reason"].values()))
        self.assertEqual(res["kept"]["by_reason"], {"system_referenced": 2})  # Git's pycache and Thumbs.db
        (entry,) = res["protected_by_system"]
        self.assertEqual((Path(entry["path"]), entry["kept_items"]), (self.root / "Git", 2))
        self.assertIn('installed app "Git"', entry["used_by"])

    def test_what_is_kept_is_counted_by_reason(self) -> None:
        self.write("proj/dist/bundle.js")  # a project's dist next to package.json: junk at Tier 2
        self.write("proj/node_modules/pkg/dist/index.js")  # package code: kept
        res = self.clean(sub_dir="proj")
        self.assertEqual(res["kept"]["by_reason"], {"dependency_tree": 1})
        self.assertIn("proj/dist", [path.replace("\\", "/") for path in res["sample_preview"]])


class TestFindDuplicatesSubDir(_Storage):
    def test_a_sub_dir_inside_an_installed_app_is_not_scanned(self) -> None:
        res: Dict[str, Any] = {}
        hashed = self.hashed_by(lambda: res.update(self.server.handle_ssd_find_duplicates({"sub_dir": "Git/usr"})))
        self.assertEqual(hashed, [])
        self.assertEqual((res["duplicate_group_count"], res["duplicate_groups"]), (0, []))
        self.assertEqual(res["total_reclaimable_bytes"], 0)
        skipped = res["scan_skipped"]
        self.assertEqual(Path(skipped["folder"]), self.root / "Git")
        self.assertEqual(skipped["reason"], "protected_by_system")
        self.assertIn('installed app "Git"', skipped["detail"])
        self.assertIn("Nothing was scanned", skipped["message"])
        self.assertEqual(res["skipped_protected_dirs"], 1)

    def test_the_folder_itself_is_judged_too(self) -> None:
        res = self.server.handle_ssd_find_duplicates({"sub_dir": "Git"})
        self.assertEqual(res["duplicate_group_count"], 0)
        self.assertEqual(Path(res["scan_skipped"]["folder"]), self.root / "Git")
        self.assertIn(f"{self.root / 'Git'} is used by Windows or an installed app", res["scan_skipped"]["message"])

    def test_a_sub_dir_inside_a_dependency_tree_is_not_scanned(self) -> None:
        res: Dict[str, Any] = {}
        args = {"sub_dir": "proj/node_modules/pkg"}
        hashed = self.hashed_by(lambda: res.update(self.server.handle_ssd_find_duplicates(args)))
        self.assertEqual(hashed, [])
        skipped = res["scan_skipped"]
        self.assertEqual(Path(skipped["folder"]), self.root / "proj" / "node_modules")
        self.assertEqual(skipped["reason"], "dependency_tree")
        self.assertEqual((res["skipped_dependency_dirs"], res["duplicate_group_count"]), (1, 0))

    def test_the_response_keeps_its_shape(self) -> None:
        res = self.server.handle_ssd_find_duplicates({"sub_dir": "Git/usr", "limit": 5})
        for key in (
            "root", "limit", "offset", "total_groups", "duplicate_group_count", "returned_group_count",
            "duplicate_file_count", "total_reclaimable_bytes", "total_reclaimable_slack", "total_reclaimable_physical",
            "has_more", "next_offset", "duplicate_groups",
        ):
            self.assertIn(key, res)
        self.assertEqual((res["limit"], res["has_more"], res["next_offset"]), (5, False, None))

    def test_an_ordinary_sub_dir_is_scanned(self) -> None:
        res = self.server.handle_ssd_find_duplicates({"sub_dir": "proj"})
        self.assertIsNone(res["scan_skipped"])
        self.assertEqual(res["duplicate_group_count"], 1)  # e.bin and f.bin; the copies in node_modules are not offered
        self.assertEqual(res["skipped_dependency_dirs"], 2)  # node_modules and __pycache__

    def test_the_whole_drive_scan_names_nothing_as_skipped(self) -> None:
        res = self.server.handle_ssd_find_duplicates({})
        self.assertIsNone(res["scan_skipped"])
        self.assertEqual((res["skipped_protected_dirs"], res["skipped_dependency_dirs"]), (1, 2))


class TestMinSizeReachesTheDetector(_Storage):
    def seed(self) -> None:
        for rel in ("m/small1", "n/small2"):
            self.write(rel, b"s" * 40)
        for rel in ("m/big1", "n/big2"):
            self.write(rel, b"B" * 5000)

    def test_the_detector_is_given_min_size_and_the_drive_root(self) -> None:
        self.seed()
        with patch("smart_drive.mcp.server.DuplicateDetector", wraps=DuplicateDetector) as detector_class:
            self.server.handle_ssd_find_duplicates({"sub_dir": "m", "min_size": 1000})
        (call,) = detector_class.call_args_list
        self.assertEqual(call.kwargs["min_size"], 1000)
        self.assertEqual(Path(call.kwargs["drive_root"]), self.root)
        self.assertEqual(Path(call.args[0]), self.root / "m")

    def test_small_files_are_dropped_before_they_are_hashed(self) -> None:
        self.seed()
        res: Dict[str, Any] = {}
        hashed = self.hashed_by(lambda: res.update(self.server.handle_ssd_find_duplicates({"min_size": 1000})))
        self.assertTrue(hashed)
        self.assertFalse([name for name in hashed if "small" in name], hashed)
        self.assertEqual([g["size"] for g in res["duplicate_groups"]], [5000])
        self.assertEqual(res["total_reclaimable_bytes"], 5000)

    def test_without_a_minimum_the_small_copies_are_compared_too(self) -> None:
        self.seed()
        res = self.server.handle_ssd_find_duplicates({})
        self.assertEqual(sorted(g["size"] for g in res["duplicate_groups"] if g["size"] < 1000), [40, len(OTHER)])

    def test_a_negative_or_unreadable_minimum_means_none(self) -> None:
        self.seed()
        for value in (-500, "not_a_number", None):
            with self.subTest(min_size=value):
                res = self.server.handle_ssd_find_duplicates({"min_size": value})
                self.assertIn(40, [g["size"] for g in res["duplicate_groups"]])

    def test_a_minimum_filters_the_groups_as_it_did(self) -> None:
        self.seed()
        res = self.server.handle_ssd_find_duplicates({"min_size": 5000})
        self.assertEqual([g["size"] for g in res["duplicate_groups"]], [5000])
        res = self.server.handle_ssd_find_duplicates({"min_size": 5001})
        self.assertEqual((res["duplicate_group_count"], res["total_reclaimable_physical"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
