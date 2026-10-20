"""tests/test_scanner_paths_and_pruning.py - the scanner's cheaper path handling and its descent pruning.

Regression: for every entry the scanner re-derived the relative path from the absolute one (two normpath calls and a
relpath, with their syscalls on Windows) and tested every run of segments of that path against the exclusion names; the
junk detector then listed every file inside a `__pycache__`/`.pytest_cache` it had already decided to purge whole.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

from smart_drive.core.config import JunkTier
from smart_drive.core.exfat_compat import ExFatEngine
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.scanner import FastDirectoryScanner


class ScannerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_scan_paths_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def touch(self, *parts: str) -> None:
        path = self.root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    def rels(self, **kwargs: object) -> List[str]:
        return sorted(e.rel_path for e in FastDirectoryScanner(str(self.root), **kwargs).scan_iter())  # type: ignore[arg-type]


class TestRelativePaths(ScannerCase):
    def test_rel_path_matches_the_canonical_normalisation_for_every_entry(self) -> None:
        for parts in (
            ("a.txt",),
            ("Giáo trình", "bài 1 - Giải tích.md"),
            ("deep", "er", "and", "deeper", "file.bin"),
            ("with space", "trailing.dot.", "x"),
            ("dir.with.dots", "..hidden", "y"),
        ):
            self.touch(*parts)
        for root_spelling in (str(self.root), str(self.root) + os.sep):
            with self.subTest(root=root_spelling):
                entries = list(FastDirectoryScanner(root_spelling).scan_iter())
                self.assertGreaterEqual(len(entries), 12)
                for e in entries:
                    self.assertEqual(e.rel_path, ExFatEngine.normalize_rel_path(e.path, root_spelling), e.path)
                    self.assertEqual(os.path.basename(e.path), e.name)
                    self.assertEqual(os.path.normpath(os.path.join(self.root, *e.rel_path.split("/"))), e.path)


class TestCompoundExclusions(ScannerCase):
    def setUp(self) -> None:
        super().setUp()
        for rel in (
            ("alpha", "beta", "secret.txt"),
            ("alpha", "other", "kept.txt"),
            ("gamma", "alpha", "beta", "nested_secret.txt"),
            ("beta", "kept_too.txt"),
            ("alphabeta", "kept_three.txt"),
        ):
            self.touch(*rel)

    def test_a_multi_segment_exclusion_hides_that_run_wherever_it_occurs(self) -> None:
        rels = self.rels(exclude_dirs={"alpha/beta"})
        self.assertNotIn("alpha/beta", rels)
        self.assertNotIn("alpha/beta/secret.txt", rels)
        self.assertNotIn("gamma/alpha/beta", rels)
        self.assertNotIn("gamma/alpha/beta/nested_secret.txt", rels)
        for kept in ("alpha/other/kept.txt", "beta/kept_too.txt", "alphabeta/kept_three.txt", "gamma/alpha"):
            self.assertIn(kept, rels)

    def test_the_exclusion_is_case_insensitive_and_accepts_either_slash(self) -> None:
        for spelling in ("ALPHA/Beta", "alpha\\beta", "/alpha/beta/"):
            with self.subTest(spelling=spelling):
                rels = self.rels(exclude_dirs={spelling})
                self.assertNotIn("alpha/beta/secret.txt", rels)
                self.assertIn("alpha/other/kept.txt", rels)

    def test_single_name_exclusions_still_apply_at_any_depth(self) -> None:
        rels = self.rels(exclude_dirs={"other"})
        self.assertNotIn("alpha/other", rels)
        self.assertIn("alpha/beta/secret.txt", rels)


class TestSkipDescent(ScannerCase):
    def setUp(self) -> None:
        super().setUp()
        for rel in (
            ("cache", "deep", "a.bin"),
            ("cache", "b.bin"),
            ("keep", "c.bin"),
            ("top.bin",),
        ):
            self.touch(*rel)

    def scan(self, skip_names: List[str], **kwargs: object) -> List[str]:
        scanner = FastDirectoryScanner(str(self.root), **kwargs)  # type: ignore[arg-type]
        seen: List[str] = []
        for e in scanner.scan_iter():
            seen.append(e.rel_path)
            if e.name in skip_names:
                scanner.skip_descent()
        return sorted(seen)

    def test_the_contents_of_a_skipped_directory_are_not_listed(self) -> None:
        self.assertEqual(
            self.scan(["cache"]),
            ["cache", "keep", "keep/c.bin", "top.bin"],
        )

    def test_skipping_leaves_siblings_and_other_directories_alone(self) -> None:
        seen = self.scan(["keep"])
        self.assertIn("cache/deep/a.bin", seen)
        self.assertNotIn("keep/c.bin", seen)

    def test_a_request_made_on_a_file_does_not_leak_to_the_next_directory(self) -> None:
        seen = self.scan(["top.bin", "b.bin"])
        self.assertIn("cache/deep/a.bin", seen)
        self.assertIn("keep/c.bin", seen)

    def test_skipping_a_directory_that_was_not_going_to_be_entered_is_harmless(self) -> None:
        self.assertEqual(self.scan(["cache", "keep"], max_depth=1), ["cache", "keep", "top.bin"])


class TestJunkDetectorPrunesJunkDirectories(ScannerCase):
    def test_files_inside_a_junk_directory_are_never_inspected(self) -> None:
        for i in range(25):
            self.touch("pkg", "__pycache__", f"mod{i}.cpython-311.pyc")
        self.touch("pkg", "mod.py")
        inspected: Dict[str, bool] = {}
        original = JunkDetector.inspect_entry

        def recording(self_: JunkDetector, entry: object) -> object:
            inspected[entry.rel_path] = True  # type: ignore[attr-defined]
            return original(self_, entry)  # type: ignore[arg-type]

        with patch.object(JunkDetector, "inspect_entry", recording):
            items = JunkDetector(str(self.root), max_tier=JunkTier.TIER_1_SAFE).find_junk()

        self.assertEqual([i.rel_path for i in items], ["pkg/__pycache__"])
        self.assertEqual(items[0].size, 25)
        self.assertEqual(sorted(inspected), ["pkg", "pkg/__pycache__", "pkg/mod.py"])


if __name__ == "__main__":
    unittest.main()
