"""tests/test_junk_detector_prefilter.py - the junk detector must not do filesystem work for entries no rule can match.

Regression: `inspect_file` resolved the path and looked for an enclosing learning unit (`find_unit_root`: several
`stat`/`_getfinalpathname` calls) and walked the protected-folder tables for every one of the 382,772 entries of a real
drive before it asked whether any junk rule matched. That was 680 of the 899 profiled seconds of `smart-drive clean`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest.mock import patch

from smart_drive.core.config import JUNK_RULES, JunkRule, JunkTier, match_junk_rule
from smart_drive.core.junk_detector import JunkDetector

# A name that each rule with a `name_regex` matches: the wildcards of a glob cannot be filled in for those
REGEX_RULE_SAMPLES = {"core.<pid>": "core.4321"}


def _sample_name(rule: JunkRule) -> str:
    """A name that the rule matches."""
    if rule.name_regex is not None:
        return REGEX_RULE_SAMPLES[rule.pattern]
    return rule.pattern.replace("*", "x").replace("?", "x")


class TestPrefilterNeverHidesAMatch(unittest.TestCase):
    def test_every_name_a_rule_matches_passes_the_filter(self) -> None:
        for max_tier in JunkTier:
            detector = JunkDetector(tempfile.gettempdir(), max_tier=max_tier)
            for rule in JUNK_RULES:
                for variant in (str.lower, str.upper):
                    full = variant(_sample_name(rule))
                    name = full.rsplit("/", 1)[-1]
                    rel_path = f"projects/{full}"
                    matched = match_junk_rule(
                        name, rule.is_directory, max_tier=max_tier, is_root_level=False, rel_path=rel_path
                    )
                    if matched is None:
                        if rule.tier <= max_tier:  # (above the chosen tier a rule is rightly silent)
                            self.assertIsNone(rule.name_regex, f"{rule.pattern}: its sample name matches no rule")
                        continue
                    with self.subTest(tier=int(max_tier), pattern=rule.pattern, variant=variant.__name__):
                        self.assertTrue(detector._may_be_junk(name, rule.is_directory, rel_path))

    def test_names_no_rule_can_match_are_filtered_out(self) -> None:
        detector = JunkDetector(tempfile.gettempdir(), max_tier=JunkTier.TIER_3_SENSITIVE)
        for name, is_dir in (("report.docx", False), ("src", True), ("main.py", False), ("buildings_2024", True)):
            with self.subTest(name=name):
                self.assertFalse(detector._may_be_junk(name, is_dir, f"stuff/{name}"))

    def test_a_directory_rule_does_not_let_a_file_of_that_name_through(self) -> None:
        detector = JunkDetector(tempfile.gettempdir(), max_tier=JunkTier.TIER_2_DEV_CACHE)
        self.assertTrue(detector._may_be_junk("__pycache__", True, "pkg/__pycache__"))
        self.assertFalse(detector._may_be_junk("__pycache__", False, "pkg/__pycache__"))

    def test_nothing_above_the_chosen_tier_passes(self) -> None:
        detector = JunkDetector(tempfile.gettempdir(), max_tier=JunkTier.TIER_1_SAFE)
        self.assertTrue(detector._may_be_junk("mod.pyc", False, "pkg/mod.pyc"))
        self.assertFalse(detector._may_be_junk("crash.dmp", False, "crash.dmp"))
        self.assertFalse(detector._may_be_junk(".pytest_cache", True, ".pytest_cache"))

    def test_polaris_cache_contents_pass_whatever_their_name(self) -> None:
        detector = JunkDetector(tempfile.gettempdir(), max_tier=JunkTier.TIER_2_DEV_CACHE)
        self.assertTrue(detector._may_be_junk("api_data.json", False, "course/polaris/.cache/api_data.json"))


class TestScanSkipsFilesystemWorkForOrdinaryEntries(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_prefilter_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for i in range(30):
            folder = self.root / "work" / f"part{i % 3}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"file{i}.txt").write_text("x", encoding="utf-8")
            (folder / f"mod{i}.py").write_text("x", encoding="utf-8")
        (self.root / "work" / "__pycache__").mkdir()
        (self.root / "work" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"x" * 10)
        (self.root / "work" / ".DS_Store").write_bytes(b"x")

    def test_unit_lookup_is_only_made_for_entries_a_rule_matches(self) -> None:
        looked_up: List[str] = []

        def fake_find_unit_root(path: object, drive_root: object = None) -> None:
            looked_up.append(os.path.basename(str(path)))
            return None

        with patch("smart_drive.core.junk_detector.find_unit_root", side_effect=fake_find_unit_root):
            items = JunkDetector(str(self.root), max_tier=JunkTier.TIER_1_SAFE).find_junk()

        self.assertEqual(sorted(i.name for i in items), [".DS_Store", "__pycache__"])
        # 60 ordinary files plus 4 folders never reach the lookup, nor does the .pyc inside the junk folder.
        self.assertEqual(sorted(looked_up), [".DS_Store", "__pycache__"])

    def test_protected_root_tables_are_not_walked_for_ordinary_entries(self) -> None:
        calls: List[str] = []
        from smart_drive.core import junk_detector

        real = junk_detector.is_protected_root_dir

        def counting(name: str) -> bool:
            calls.append(name)
            return real(name)

        with patch.object(junk_detector, "is_protected_root_dir", side_effect=counting):
            JunkDetector(str(self.root), max_tier=JunkTier.TIER_1_SAFE).find_junk()

        self.assertLessEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
