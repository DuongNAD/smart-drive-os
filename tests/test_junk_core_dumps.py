"""tests/test_junk_core_dumps.py - the Tier 3 core-dump rule takes core dumps, not the sources named core.*.

Regression: the rule was the glob `core.*`, which also matches core.py, core.js, core.css, core.min.js, core.ts...
On a real drive it matched 334 entries (Python, TypeScript, JSON, source maps) that only the dependency-tree and
installed-app checks kept out of a Tier 3 report; a project outside both, not tracked by git, would have had its
`core.py` moved to the quarantine by `clean --tier 3 --apply`, and left out of every snapshot and backup.
A core dump is named `core` or `core.<pid>`.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Set
from unittest.mock import patch

from smart_drive.cli.main import main
from smart_drive.core.config import JUNK_RULES, JunkRule, JunkTier, match_junk_rule
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.snapshot import SnapshotManager
from smart_drive.core.system_refs import SystemRefIndex

SOURCES = (
    "core.py", "core.js", "core.css", "core.min.js", "core.ts", "core.tsx", "core.json", "core.h", "core.go",
    "core.txt", "core.d.ts", "core.js.map", "core.1.py", "core.2x.css", "core.4321.log", "core.12ab", "core.",
    "Core.PY", "core_utils.py", "corefile", "core-1.0.tar.gz", "score.123", "encore.4", ".core.4321",
)
DUMPS = ("core", "core.0", "core.4321", "core.1234567", "CORE", "Core.4321")


def _rule_for(name: str, is_dir: bool = False, tier: JunkTier = JunkTier.TIER_3_SENSITIVE) -> Optional[JunkRule]:
    return match_junk_rule(name, is_dir, max_tier=tier, is_root_level=False, rel_path=f"proj/{name}")


class TestTheRule(unittest.TestCase):
    def test_a_source_file_named_core_is_not_a_core_dump(self) -> None:
        for name in SOURCES:
            with self.subTest(name=name):
                self.assertIsNone(_rule_for(name))

    def test_core_and_core_dot_digits_are_core_dumps_at_tier_3(self) -> None:
        for name in DUMPS:
            with self.subTest(name=name):
                rule = _rule_for(name)
                self.assertIsNotNone(rule)
                self.assertEqual((rule.description, rule.tier), ("Unix core dump", JunkTier.TIER_3_SENSITIVE))

    def test_a_core_dump_is_not_junk_below_tier_3(self) -> None:
        for tier in (JunkTier.TIER_1_SAFE, JunkTier.TIER_2_DEV_CACHE):
            for name in DUMPS:
                with self.subTest(tier=int(tier), name=name):
                    self.assertIsNone(_rule_for(name, tier=tier))

    def test_a_folder_named_core_is_not_junk(self) -> None:
        for name in ("core", "core.4321", "core.py"):
            with self.subTest(name=name):
                self.assertIsNone(_rule_for(name, is_dir=True))

    def test_only_ascii_digits_make_a_process_id(self) -> None:
        for name in ("core.١٢٣", "core.²", "core.４３"):  # Arabic-Indic, superscript, full-width
            with self.subTest(name=name):
                self.assertIsNone(_rule_for(name))

    def test_the_rules_are_core_and_core_pid_and_only_the_second_is_a_regex(self) -> None:
        by_label = {rule.pattern: rule for rule in JUNK_RULES if rule.description == "Unix core dump"}
        self.assertEqual(sorted(by_label), ["core", "core.<pid>"])
        self.assertIsNone(by_label["core"].name_regex)
        self.assertEqual(by_label["core.<pid>"].name_regex, r"core\.[0-9]+")

    def test_the_other_rules_still_match_as_globs(self) -> None:
        for name, tier in (("crash.dmp", 3), ("hs_err_pid4242.log", 3), ("a.tmp", 3), ("notes~", 3), ("m.pyc", 1)):
            with self.subTest(name=name):
                self.assertIsNotNone(_rule_for(name, tier=JunkTier(tier)))


class TestTheDetector(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_core_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in SOURCES:
            if rel.endswith("."):
                continue  # (a name ending in a dot cannot be made on Windows; the rule test above covers it)
            self.write(f"src/{rel}")
        self.write("src/core/module.py")  # a folder called core: the commonest name of all
        self.write("crash/core", "\x7fELF")
        self.write("crash/core.4321", "\x7fELF")

    def write(self, rel: str, data: str = "x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def found(self, tier: JunkTier) -> Set[str]:
        detector = JunkDetector(str(self.root), max_tier=tier, system_refs=SystemRefIndex([]))
        return {item.rel_path.replace("\\", "/") for item in detector.find_junk()}

    def test_tier_3_reports_the_core_dumps_and_no_source_file(self) -> None:
        self.assertEqual(self.found(JunkTier.TIER_3_SENSITIVE), {"crash/core", "crash/core.4321"})

    def test_lower_tiers_report_none(self) -> None:
        for tier in (JunkTier.TIER_1_SAFE, JunkTier.TIER_2_DEV_CACHE):
            with self.subTest(tier=int(tier)):
                self.assertEqual(self.found(tier), set())

    def test_the_name_filter_in_front_of_the_rules_agrees(self) -> None:
        detector = JunkDetector(str(self.root), max_tier=JunkTier.TIER_3_SENSITIVE, system_refs=SystemRefIndex([]))
        for name in DUMPS:
            with self.subTest(name=name):
                self.assertTrue(detector._may_be_junk(name, False, f"proj/{name}"))
        for name in SOURCES:
            with self.subTest(name=name):
                self.assertFalse(detector._may_be_junk(name, False, f"proj/{name}"))
        self.assertFalse(detector._may_be_junk("core", True, "proj/core"))  # a folder of that name

    def test_the_name_filter_is_off_below_tier_3(self) -> None:
        detector = JunkDetector(str(self.root), max_tier=JunkTier.TIER_2_DEV_CACHE, system_refs=SystemRefIndex([]))
        self.assertFalse(detector._may_be_junk("core.4321", False, "proj/core.4321"))


class TestCleanApplyLeavesSourcesAlone(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_core_apply_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in ("app/core.py", "app/core.js", "app/core.css", "app/core.min.js", "app/dump/core.4321"):
            path = self.root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")

    def run_clean(self, *extra: str) -> Dict[str, Any]:
        out = io.StringIO()
        with patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["clean", "--root", str(self.root), "--json", *extra])
        self.assertEqual(code, 0, out.getvalue())
        return json.loads(out.getvalue())

    def test_the_preview_lists_only_the_core_dump(self) -> None:
        report = self.run_clean("--tier", "3")
        self.assertEqual([i["rel_path"].replace("\\", "/") for i in report["junk_items"]], ["app/dump/core.4321"])

    def test_apply_quarantines_the_dump_and_touches_no_source(self) -> None:
        report = self.run_clean("--tier", "3", "--apply")
        self.assertEqual((report["quarantined_count"], report["failed_count"]), (1, 0))
        self.assertFalse((self.root / "app" / "dump" / "core.4321").exists())
        for name in ("core.py", "core.js", "core.css", "core.min.js"):
            self.assertTrue((self.root / "app" / name).is_file(), name)


class TestSnapshotsKeepSources(unittest.TestCase):
    def test_a_snapshot_records_core_py_and_skips_a_core_dump(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sd_snap_core_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "03_Development_Projects" / "app").mkdir(parents=True)
        for name in ("core.py", "core.js", "core.4321"):
            (root / "03_Development_Projects" / "app" / name).write_text("x", encoding="utf-8")

        manifest = SnapshotManager(root).create_snapshot(name="core", partitions=["03_Development_Projects"])

        recorded = sorted(path.split("/")[-1] for path in manifest.files)
        self.assertEqual(recorded, ["core.js", "core.py"])  # the dump is junk and left out, as with skip_junk always


if __name__ == "__main__":
    unittest.main()
