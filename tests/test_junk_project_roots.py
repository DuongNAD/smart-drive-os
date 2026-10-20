"""tests/test_junk_project_roots.py - `build`, `dist` and `.cache` are junk only at a project root.

Regression: those three names matched every folder called that, anywhere. On a real drive that included
`Arduino IDE/resources/app/lib/build` and `Git/usr/share/vim/vim91/pack/dist` (parts of installed programs),
a Rust `target/debug/build`, Next.js's `.next/build`, and 5 `.cache` folders labelled "Polaris regenerable API
cache" although they had nothing to do with Polaris. They now count only when the folder holding them also holds a
project manifest (package.json, pyproject.toml, Cargo.toml, a .csproj ...); the Polaris cache keeps matching
wherever it is.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Set
from unittest.mock import patch

from smart_drive.core.config import (
    JUNK_RULES,
    JunkTier,
    is_project_manifest,
    match_junk_rule,
)
from smart_drive.core.junk_detector import KEPT_NOT_PROJECT_ROOT, JunkDetector
from smart_drive.core.system_refs import SystemRefIndex

# One real-looking name for every pattern, and for the NetBeans folder (which is not a file)
SAMPLE_MANIFESTS = (
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "build.gradle.kts", "build.xml", "CMakeLists.txt", "Makefile", "composer.json", "Gemfile", "angular.json",
    "App.csproj", "Solution.sln", "vite.config.ts", "webpack.config.js", "next.config.mjs",
)


class TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_roots_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel: str, data: str = "x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def detect(self, tier: JunkTier = JunkTier.TIER_2_DEV_CACHE) -> JunkDetector:
        detector = JunkDetector(str(self.root), max_tier=tier, system_refs=SystemRefIndex([]))
        self.found: Set[str] = {item.rel_path.replace("\\", "/") for item in detector.find_junk()}
        return detector


class TestManifestNames(unittest.TestCase):
    def test_every_project_file_the_task_names_is_a_manifest(self) -> None:
        for name in SAMPLE_MANIFESTS:
            with self.subTest(name=name):
                self.assertTrue(is_project_manifest(name))
                self.assertTrue(is_project_manifest(name.upper()))

    def test_the_netbeans_folder_is_a_manifest_but_only_as_a_folder(self) -> None:
        self.assertTrue(is_project_manifest("nbproject", is_dir=True))
        self.assertFalse(is_project_manifest("nbproject", is_dir=False))
        self.assertFalse(is_project_manifest("package.json", is_dir=True))

    def test_other_files_are_not(self) -> None:
        for name in ("readme.md", "index.js", "notes.txt", "main.py", "package.json.bak", "my.csproj.user", "vite.config"):
            with self.subTest(name=name):
                self.assertFalse(is_project_manifest(name))


class TestRules(unittest.TestCase):
    def test_exactly_build_dist_and_the_plain_cache_need_a_project_root(self) -> None:
        self.assertEqual(
            sorted(rule.pattern for rule in JUNK_RULES if rule.project_root_only), [".cache", "build", "dist"]
        )

    def test_the_plain_cache_is_no_longer_called_a_polaris_cache(self) -> None:
        rule = match_junk_rule(".cache", True, max_tier=JunkTier.TIER_2_DEV_CACHE, rel_path="proj/.cache")
        self.assertNotIn("Polaris", rule.description)
        self.assertNotEqual(rule.os_source, "Polaris")

    def test_a_polaris_cache_still_matches_as_one_and_needs_no_project_root(self) -> None:
        rule = match_junk_rule(".cache", True, max_tier=JunkTier.TIER_2_DEV_CACHE, rel_path="course/polaris/.cache")
        self.assertEqual(rule.pattern, "polaris/.cache")
        self.assertFalse(rule.project_root_only)
        self.assertEqual(rule.os_source, "Polaris")


class TestProjectRoots(TreeCase):
    def test_build_dist_and_cache_next_to_a_manifest_are_junk(self) -> None:
        for manifest in SAMPLE_MANIFESTS:
            with self.subTest(manifest=manifest):
                shutil.rmtree(self.root / "proj", ignore_errors=True)
                self.write(f"proj/{manifest}")
                for name in ("build", "dist", ".cache"):
                    self.write(f"proj/{name}/out.bin")
                self.detect()
                self.assertEqual(self.found, {"proj/build", "proj/dist", "proj/.cache"})

    def test_a_netbeans_project_is_a_project_root(self) -> None:
        self.write("DemoGraph/nbproject/project.xml")
        self.write("DemoGraph/build/classes/App.class")
        self.detect()
        self.assertEqual(self.found, {"DemoGraph/build"})

    def test_without_a_manifest_beside_them_they_are_ordinary_folders(self) -> None:
        for name in ("build", "dist", ".cache"):
            self.write(f"lib/{name}/out.bin")
        detector = self.detect()
        self.assertEqual(self.found, set())
        self.assertEqual(detector.kept, {KEPT_NOT_PROJECT_ROOT: 3})

    def test_a_manifest_two_levels_up_does_not_count(self) -> None:
        self.write("proj/package.json")
        self.write("proj/src/dist/out.js")
        self.write("proj/.next/build/manifest.json")  # Next.js: output folders live in an output folder
        self.write("proj/target/debug/build/out.rlib")  # Rust
        self.detect()
        self.assertEqual(self.found, set())

    def test_a_manifest_inside_the_folder_does_not_count_either(self) -> None:
        self.write("pkg/dist/package.json")
        self.write("pkg/dist/index.js")
        self.detect()
        self.assertEqual(self.found, set())

    def test_the_real_world_look_alikes_are_kept(self) -> None:
        self.write("Arduno/Arduino IDE/resources/app/lib/build/x.js")
        self.write("Git/usr/share/vim/vim91/pack/dist/opt/README")
        self.write("home/.cache/pip/http/a")
        self.detect()
        self.assertEqual(self.found, set())

    def test_other_tier_2_rules_do_not_need_a_project_root(self) -> None:
        self.write("loose/.pytest_cache/v/state")
        self.write("loose/.mypy_cache/3.11/a.json")
        self.write("loose/htmlcov/index.html")
        self.write("loose/.coverage")
        self.write("loose/pkg.egg-info/PKG-INFO")
        self.detect()
        self.assertEqual(
            self.found,
            {"loose/.pytest_cache", "loose/.mypy_cache", "loose/htmlcov", "loose/.coverage", "loose/pkg.egg-info"},
        )

    def test_a_manifest_in_one_folder_does_not_make_its_neighbours_projects(self) -> None:
        self.write("a/package.json")
        self.write("a/dist/x.js")
        self.write("b/dist/x.js")
        self.detect()
        self.assertEqual(self.found, {"a/dist"})

    def test_a_folder_is_listed_for_its_manifest_once_however_many_candidates_it_has(self) -> None:
        self.write("one/package.json")
        self.write("one/build/out.bin")
        self.write("three/package.json")
        for name in ("build", "dist", ".cache"):
            self.write(f"three/{name}/out.bin")
        listed = []
        real_scandir = os.scandir

        def counting(path: object = ".") -> object:
            listed.append(os.path.normcase(str(path)))
            return real_scandir(path)  # type: ignore[arg-type]

        with patch("os.scandir", side_effect=counting):
            self.detect()
        self.assertEqual(self.found, {"one/build", "three/build", "three/dist", "three/.cache"})
        one, three = (os.path.normcase(str(self.root / name)) for name in ("one", "three"))
        self.assertEqual(listed.count(three), listed.count(one))


class TestPolarisCacheKeepsWorking(TreeCase):
    def test_a_polaris_cache_inside_a_learning_unit_is_junk_without_any_manifest(self) -> None:
        self.write("biology/polaris/brief.json", "{}")
        self.write("biology/polaris/.cache/api.json", "{}")
        self.detect()
        self.assertEqual(self.found, {"biology/polaris/.cache"})

    def test_a_polaris_cache_outside_a_recognised_unit_is_junk_too(self) -> None:
        self.write("unit/polaris/.cache/api.json", "{}")
        self.detect()
        self.assertEqual(self.found, {"unit/polaris/.cache"})

    def test_a_plain_cache_beside_it_is_not(self) -> None:
        self.write("unit/polaris/.cache/api.json", "{}")
        self.write("unit/.cache/other.json", "{}")
        self.detect()
        self.assertEqual(self.found, {"unit/polaris/.cache"})


if __name__ == "__main__":
    unittest.main()
