"""tests/test_junk_dependency_trees.py - installed packages are never junk.

Regression: a dry run of `smart-drive clean --tier 2` on a real drive flagged 945 `dist` and 130 `build` folders
inside `node_modules` (1.47 GB of installed package code, e.g. next/dist, onnxruntime-web/dist, pixi.js/dist),
`.cache` folders with downloaded models, and `build` folders inside Python `site-packages` for PERMANENT deletion
(Tier 1-2 is deleted at once, not quarantined). Deleting them breaks the projects that use the packages, and
`npm install` does not notice. Tier 2-3 rules must not match anywhere inside a dependency tree.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import List, Set

from smart_drive.cli.main import main
from smart_drive.core.config import (
    DEPENDENCY_TREE_DIR_NAMES,
    JunkTier,
    is_inside_dependency_tree,
    match_junk_rule,
)
from smart_drive.core.junk_detector import KEPT_DEPENDENCY_TREE, JunkDetector
from smart_drive.core.purge_engine import PurgeEngine


class TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_dep_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel: str, data: str = "x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def detect(self, tier: JunkTier = JunkTier.TIER_3_SENSITIVE) -> JunkDetector:
        detector = JunkDetector(str(self.root), max_tier=tier)
        self.found = {item.rel_path.replace("\\", "/") for item in detector.find_junk()}
        return detector

    found: Set[str]


class TestDependencyTreePaths(unittest.TestCase):
    def test_a_folder_on_the_way_counts_whatever_the_separator_or_case(self) -> None:
        for path in (
            "proj/node_modules/pkg/dist",
            "proj\\node_modules\\pkg\\dist",
            "E:\\proj\\Node_Modules\\pkg\\dist",
            "/usr/lib/python3/dist-packages/pkg/build",
            "py/Lib/SITE-PACKAGES/pkg/build",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_inside_dependency_tree(path))

    def test_ordinary_paths_and_look_alike_names_do_not(self) -> None:
        for path in (
            "proj/dist",
            "proj/my_node_modules_notes/dist",
            "proj/node_modules_backup/dist",
            "proj/site-packages-notes.txt",
            "",
        ):
            with self.subTest(path=path):
                self.assertFalse(is_inside_dependency_tree(path))

    def test_the_entry_itself_is_not_inside_itself(self) -> None:
        self.assertFalse(is_inside_dependency_tree("proj/node_modules"))
        self.assertTrue(is_inside_dependency_tree("proj/node_modules", include_last=True))

    def test_every_documented_folder_name_is_covered(self) -> None:
        for name in ("node_modules", "bower_components", "jspm_packages", ".pnpm-store", "site-packages", "dist-packages"):
            with self.subTest(name=name):
                self.assertIn(name, DEPENDENCY_TREE_DIR_NAMES)
                self.assertTrue(is_inside_dependency_tree(f"proj/{name}/pkg/dist"))


class TestRuleMatchingKnowsDependencyTrees(unittest.TestCase):
    def test_tier_2_and_3_rules_do_not_match_inside_a_dependency_tree(self) -> None:
        for name, is_dir in (("dist", True), ("build", True), (".cache", True), ("x.egg-info", True), ("a.tmp", False)):
            with self.subTest(name=name):
                self.assertIsNone(
                    match_junk_rule(name, is_dir, max_tier=JunkTier.TIER_3_SENSITIVE, rel_path=f"p/node_modules/k/{name}")
                )
                self.assertIsNotNone(
                    match_junk_rule(name, is_dir, max_tier=JunkTier.TIER_3_SENSITIVE, rel_path=f"p/k/{name}")
                )

    def test_tier_1_junk_still_matches_inside_a_dependency_tree(self) -> None:
        for name, is_dir in (("__pycache__", True), ("Thumbs.db", False), (".DS_Store", False), ("m.pyc", False)):
            with self.subTest(name=name):
                rule = match_junk_rule(
                    name, is_dir, max_tier=JunkTier.TIER_3_SENSITIVE, rel_path=f"p/site-packages/k/{name}"
                )
                self.assertIsNotNone(rule)
                self.assertEqual(rule.tier, JunkTier.TIER_1_SAFE)

    def test_a_caller_that_checks_the_location_itself_can_turn_the_check_off(self) -> None:
        rule = match_junk_rule(
            "dist", True, max_tier=JunkTier.TIER_2_DEV_CACHE, rel_path="p/node_modules/k/dist", check_location=False
        )
        self.assertIsNotNone(rule)


class TestDetectorKeepsInstalledPackages(TreeCase):
    def test_dist_and_build_inside_node_modules_are_not_flagged(self) -> None:
        self.write("proj/package.json")
        self.write("proj/node_modules/next/package.json")
        self.write("proj/node_modules/next/dist/server.js")
        self.write("proj/node_modules/googleapis/build/src/index.js")
        self.write("proj/node_modules/@firebase/firestore/dist/index.cjs.js")
        self.write("proj/node_modules/pixi.js/package.json")
        self.write("proj/node_modules/pixi.js/dist/pixi.js")
        detector = self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())
        self.assertEqual(detector.kept, {KEPT_DEPENDENCY_TREE: 4})

    def test_cache_folders_inside_node_modules_are_not_flagged(self) -> None:
        self.write("proj/node_modules/@huggingface/transformers/.cache/model.onnx")
        self.write("proj/node_modules/.cache/babel-loader/a.json")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())

    def test_build_and_egg_info_inside_site_packages_are_not_flagged(self) -> None:
        self.write("py/Lib/site-packages/pkg/build/lib.pyd")
        self.write("py/Lib/site-packages/foo.egg-info/PKG-INFO")
        self.write("usr/lib/python3/dist-packages/bar.egg-info/PKG-INFO")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())

    def test_other_dependency_folders_are_covered_too(self) -> None:
        for name in ("bower_components", "jspm_packages", ".pnpm-store", "dist-packages", "NODE_MODULES"):
            self.write(f"proj/{name}/pkg/dist/main.js")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, set())

    def test_tier_3_files_inside_a_dependency_tree_are_not_flagged(self) -> None:
        self.write("proj/node_modules/pkg/debug.tmp")
        self.write("proj/node_modules/pkg/crash.dmp")
        self.write("proj/node_modules/pkg/notes.txt~")
        self.write("proj/scratch.tmp")
        self.detect(JunkTier.TIER_3_SENSITIVE)
        self.assertEqual(self.found, {"proj/scratch.tmp"})

    def test_bytecode_and_os_metadata_inside_a_dependency_tree_are_still_reported(self) -> None:
        self.write("py/Lib/site-packages/pkg/__pycache__/mod.cpython-311.pyc")
        self.write("proj/node_modules/pkg/Thumbs.db")
        self.write("proj/node_modules/pkg/.DS_Store")
        self.detect(JunkTier.TIER_3_SENSITIVE)
        self.assertEqual(
            self.found,
            {"py/Lib/site-packages/pkg/__pycache__", "proj/node_modules/pkg/Thumbs.db", "proj/node_modules/pkg/.DS_Store"},
        )

    def test_a_projects_own_build_output_next_to_its_node_modules_is_still_reported(self) -> None:
        self.write("proj/package.json")
        self.write("proj/dist/bundle.js")
        self.write("proj/build/out.js")
        self.write("proj/node_modules/pkg/dist/index.js")
        self.detect(JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found, {"proj/dist", "proj/build"})

    def test_a_scan_root_inside_a_dependency_tree_reports_no_tier_2_items(self) -> None:
        inner = self.root / "node_modules" / "pkg"
        (inner / "dist").mkdir(parents=True)
        (inner / "dist" / "index.js").write_text("x", encoding="utf-8")
        (inner / "package.json").write_text("{}", encoding="utf-8")
        found: List[str] = [i.rel_path for i in JunkDetector(str(inner), max_tier=JunkTier.TIER_2_DEV_CACHE).find_junk()]
        self.assertEqual(found, [])

    def test_the_count_of_kept_entries_starts_over_for_every_scan(self) -> None:
        self.write("proj/node_modules/pkg/dist/index.js")
        detector = JunkDetector(str(self.root), max_tier=JunkTier.TIER_2_DEV_CACHE)
        detector.find_junk()
        detector.find_junk()
        self.assertEqual(detector.kept, {KEPT_DEPENDENCY_TREE: 1})

    def test_nothing_is_counted_for_tiers_that_were_not_asked_for(self) -> None:
        self.write("proj/node_modules/pkg/dist/index.js")
        detector = JunkDetector(str(self.root), max_tier=JunkTier.TIER_1_SAFE)
        self.assertEqual(detector.find_junk(), [])
        self.assertEqual(detector.kept, {})


class TestPurgeEngineRefusesDependencyTrees(TreeCase):
    """A second line of defence for a list of items that did not come from JunkDetector."""

    def engine(self, dry_run: bool = False) -> PurgeEngine:
        return PurgeEngine(str(self.root), dry_run=dry_run)

    def test_a_tier_2_folder_inside_node_modules_is_blocked_and_left_alone(self) -> None:
        package = self.write("proj/node_modules/pkg/dist/index.js").parent
        record = self.engine().delete_dir(str(package), tier=2)
        self.assertEqual(record.status, "BLOCKED")
        self.assertIn("dependency tree", record.reason)
        self.assertTrue(package.is_dir())

    def test_a_tier_3_file_inside_site_packages_is_blocked_and_left_alone(self) -> None:
        target = self.write("py/Lib/site-packages/pkg/leftover.tmp")
        record = self.engine().delete_file(str(target), tier=3)
        self.assertEqual(record.status, "BLOCKED")
        self.assertTrue(target.is_file())

    def test_a_dry_run_reports_the_same_refusal(self) -> None:
        package = self.write("proj/node_modules/pkg/build/out.js").parent
        record = self.engine(dry_run=True).delete_dir(str(package), tier=2)
        self.assertEqual(record.status, "BLOCKED")

    def test_tier_1_junk_inside_a_dependency_tree_is_still_removed(self) -> None:
        pyc = self.write("py/Lib/site-packages/pkg/__pycache__/m.pyc").parent
        record = self.engine().delete_dir(str(pyc), tier=1)
        self.assertEqual(record.status, "DELETED")
        self.assertFalse(pyc.exists())

    def test_the_same_tier_2_folder_outside_a_dependency_tree_is_removed(self) -> None:
        dist = self.write("proj/dist/bundle.js").parent
        record = self.engine().delete_dir(str(dist), tier=2)
        self.assertEqual(record.status, "DELETED")
        self.assertFalse(dist.exists())


class TestCleanCommandKeepsInstalledPackages(TreeCase):
    def run_clean(self, *extra: str) -> dict:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["clean", "--root", str(self.root), "--json", *extra])
        self.assertEqual(code, 0, out.getvalue())
        return json.loads(out.getvalue())

    def test_dry_run_lists_only_the_projects_own_output(self) -> None:
        self.write("proj/package.json")
        self.write("proj/dist/bundle.js")
        self.write("proj/node_modules/next/dist/server.js")
        report = self.run_clean("--tier", "2")
        self.assertEqual([j["rel_path"].replace("\\", "/") for j in report["junk_items"]], ["proj/dist"])

    def test_apply_deletes_the_projects_output_and_leaves_installed_packages(self) -> None:
        self.write("proj/package.json")
        bundle = self.write("proj/dist/bundle.js")
        installed = self.write("proj/node_modules/next/dist/server.js")
        report = self.run_clean("--tier", "2", "--apply")
        self.assertEqual(report["deleted_count"], 1)
        self.assertFalse(bundle.exists())
        self.assertTrue(installed.is_file())


if __name__ == "__main__":
    unittest.main()
