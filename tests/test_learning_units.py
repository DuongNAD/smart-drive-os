"""tests/test_learning_units.py - Unit Tests for Learning Units, Protection & Hotspots.

Tests SmartDrive-OS Learning Integration (Part 2):
1. Learning unit detection (Aurora course, standalone deck, Polaris subject) and path caching.
2. Movers & AutoZoner never split units and pin unit roots.
3. Cleaners protect unit data while treating polaris/.cache as Tier-2 dev cache junk.
4. Duplicate finder never proposes deleting copies inside learning units.
5. Storage auditor detects micro-file cluster slack hotspots and produces project-aware advice.
6. Sentinel and status report learning summaries (Aurora courses/decks, Polaris subjects, doc chunks).

100% Python Standard Library unittest.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.classifier import ClassifierEngine
from smart_drive.core.config import JunkTier
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.learning_units import (
    clear_unit_cache,
    find_unit_root,
    get_hotspot_advice,
    get_learning_summary,
    is_in_learning_unit,
    is_unit_root,
    unit_kind,
)
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard
from smart_drive.core.sentinel import SentinelEngine
from tests.helpers import CLUSTER_SIZE_BYTES, SmartDriveTestCase


class TestLearningUnitDetectionAndCaching(unittest.TestCase):
    """Test unit detection, hierarchy walking, and in-memory caching."""

    def setUp(self) -> None:
        clear_unit_cache()
        self.temp_dir = tempfile.mkdtemp(prefix="test_lu_detect_")
        self.root = Path(self.temp_dir)

    def tearDown(self) -> None:
        clear_unit_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_unit_kind_detection(self) -> None:
        """Correctly identifies Aurora courses, Aurora decks, and Polaris subjects."""
        # 1. Aurora course
        course_dir = self.root / "02_Learning" / "course_python"
        course_dir.mkdir(parents=True)
        (course_dir / "course.json").write_text("{}", encoding="utf-8")
        self.assertEqual(unit_kind(course_dir), "Aurora course")
        self.assertTrue(is_unit_root(course_dir))

        # 2. Standalone Aurora deck
        deck_dir = self.root / "02_Learning" / "deck_react"
        deck_dir.mkdir(parents=True)
        (deck_dir / "deck.json").write_text("{}", encoding="utf-8")
        self.assertEqual(unit_kind(deck_dir), "Aurora deck")
        self.assertTrue(is_unit_root(deck_dir))

        # 3. Polaris subject
        polaris_dir = self.root / "02_Learning" / "math_subject"
        (polaris_dir / "polaris").mkdir(parents=True)
        (polaris_dir / "polaris" / "brief.json").write_text("{}", encoding="utf-8")
        self.assertEqual(unit_kind(polaris_dir), "Polaris subject")
        self.assertTrue(is_unit_root(polaris_dir))

        # 4. Ordinary directory
        plain_dir = self.root / "03_Materials_Code" / "src"
        plain_dir.mkdir(parents=True)
        self.assertIsNone(unit_kind(plain_dir))
        self.assertFalse(is_unit_root(plain_dir))

    def test_find_unit_root_and_caching(self) -> None:
        """Upward traversal discovers enclosing unit root and uses in-memory cache."""
        course_dir = self.root / "ai_course"
        lesson_images = course_dir / "lessons" / "01_intro" / "images"
        lesson_images.mkdir(parents=True)
        (course_dir / "course.json").write_text("{}", encoding="utf-8")
        deep_file = lesson_images / "arch.png"
        deep_file.write_text("image-data", encoding="utf-8")

        # Upward lookup
        found = find_unit_root(deep_file, drive_root=self.root)
        self.assertIsNotNone(found)
        self.assertEqual(found.resolve(), course_dir.resolve())
        self.assertTrue(is_in_learning_unit(deep_file, drive_root=self.root))

        # Cache should be hit on second call
        found_cached = find_unit_root(lesson_images, drive_root=self.root)
        self.assertEqual(found_cached.resolve(), course_dir.resolve())

        # Clearing cache works
        clear_unit_cache()
        found_after_clear = find_unit_root(deep_file, drive_root=self.root)
        self.assertEqual(found_after_clear.resolve(), course_dir.resolve())

    def test_boundary_respects_drive_root(self) -> None:
        """Traversal stops at drive_root and does not search above it."""
        mock_drive = self.root / "mock_drive"
        mock_drive.mkdir()
        sub_dir = mock_drive / "folder"
        sub_dir.mkdir()
        # Even if parent of mock_drive had course.json, drive_root stops it
        (self.root / "course.json").write_text("{}", encoding="utf-8")

        found_with_boundary = find_unit_root(sub_dir, drive_root=mock_drive)
        self.assertIsNone(found_with_boundary)


class TestMoversPinLearningUnits(unittest.TestCase):
    """Test AutoZoner and ClassifierEngine protect learning units from moves and splits."""

    def setUp(self) -> None:
        clear_unit_cache()
        self.temp_dir = tempfile.mkdtemp(prefix="test_lu_movers_")
        self.root = Path(self.temp_dir)

        # Create standard taxonomies
        for t in ["01_AI_Models", "02_Learning_Knowledge", "03_Development_Projects",
                  "04_System_Workspaces", "05_Dev_Toolbox", "06_Archives_Storage"]:
            (self.root / t).mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        clear_unit_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_autozoner_skips_files_in_learning_units(self) -> None:
        """AutoZoner skips files and folders inside Aurora courses and Polaris subjects."""
        # Setup Aurora Course in root
        course_dir = self.root / "aurora_course"
        course_dir.mkdir()
        (course_dir / "course.json").write_text("{}", encoding="utf-8")
        (course_dir / "lesson.py").write_text("print('hello')", encoding="utf-8")
        (course_dir / "notes.md").write_text("# Notes", encoding="utf-8")

        # Setup Polaris Subject
        polaris_dir = self.root / "polaris_subject"
        (polaris_dir / "polaris").mkdir(parents=True)
        (polaris_dir / "polaris" / "brief.json").write_text("{}", encoding="utf-8")
        (polaris_dir / "polaris" / "errorlog.md").write_text("# Errors", encoding="utf-8")
        (polaris_dir / "helper.py").write_text("x = 1", encoding="utf-8")

        # Setup loose file outside unit
        standalone_file = self.root / "standalone_model.gguf"
        standalone_file.write_text("weights", encoding="utf-8")

        zoner = AutoZoner(str(self.root))
        plan = zoner.generate_plan()

        # The loose model should be planned for move
        move_sources = [action.src_path for action in plan]
        self.assertIn(str(standalone_file), move_sources)

        # Files inside units must NOT be in move_sources
        for src in move_sources:
            self.assertFalse(str(course_dir) in src)
            self.assertFalse(str(polaris_dir) in src)

        # Apply plan
        summary = zoner.apply_plan(plan)
        self.assertTrue(summary.get("success", False))
        self.assertGreaterEqual(summary.get("moved_count", 0), 1)

        # Verify learning unit files are completely intact in original location
        self.assertTrue((course_dir / "course.json").is_file())
        self.assertTrue((course_dir / "lesson.py").is_file())
        self.assertTrue((polaris_dir / "polaris" / "brief.json").is_file())
        self.assertTrue((polaris_dir / "helper.py").is_file())

    def test_classifier_engine_pins_learning_units(self) -> None:
        """ClassifierEngine generates pinned status for learning unit roots and does not relocate them."""
        polaris_dir = self.root / "math_polaris"
        (polaris_dir / "polaris").mkdir(parents=True)
        (polaris_dir / "polaris" / "brief.json").write_text("{}", encoding="utf-8")
        (polaris_dir / "notes.txt").write_text("math", encoding="utf-8")

        classifier = ClassifierEngine(str(self.root))
        results = classifier.scan_and_classify(self.root)

        found_pinned = False
        for res in results:
            if "math_polaris" in str(res.source_path):
                self.assertIn("pinned", res.reason)
                found_pinned = True

        self.assertTrue(found_pinned)


class TestCleanersAndJunkProtection(unittest.TestCase):
    """Test SecurityGuard and JunkDetector protect learning units while treating polaris/.cache as Tier-2."""

    def setUp(self) -> None:
        clear_unit_cache()
        self.temp_dir = tempfile.mkdtemp(prefix="test_lu_clean_")
        self.root = Path(self.temp_dir)

    def tearDown(self) -> None:
        clear_unit_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_security_guard_protection_rules(self) -> None:
        """SecurityGuard strictly protects unit items, exempting only polaris/.cache/**."""
        guard = SecurityGuard(drive_root=str(self.root))

        # Setup Polaris subject
        subj_dir = self.root / "02_Learning" / "subj"
        (subj_dir / "polaris").mkdir(parents=True)
        (subj_dir / "polaris" / "brief.json").write_text("{}", encoding="utf-8")
        (subj_dir / "polaris" / "sources.json").write_text("{}", encoding="utf-8")
        (subj_dir / "polaris" / "captures").mkdir()
        (subj_dir / "polaris" / "captures" / "ref.pdf").write_text("pdf", encoding="utf-8")
        (subj_dir / "polaris" / ".cache").mkdir()
        cache_file = subj_dir / "polaris" / ".cache" / "cache_123.json"
        cache_file.write_text("{}", encoding="utf-8")

        # Protected items
        is_prot, _ = guard.is_protected(str(subj_dir / "polaris" / "brief.json"))
        self.assertTrue(is_prot)
        is_prot, _ = guard.is_protected(str(subj_dir / "polaris" / "sources.json"))
        self.assertTrue(is_prot)
        is_prot, _ = guard.is_protected(str(subj_dir / "polaris" / "captures" / "ref.pdf"))
        self.assertTrue(is_prot)

        # polaris/.cache is exempt from protection (safe to clean as Tier-2)
        is_prot_cache, _ = guard.is_protected(str(cache_file))
        self.assertFalse(is_prot_cache)

    def test_junk_detector_tier_discrimination(self) -> None:
        """JunkDetector ignores unit files at Tier 1, flags polaris/.cache at Tier 2."""
        # Setup Polaris subject
        subj_dir = self.root / "biology"
        (subj_dir / "polaris").mkdir(parents=True)
        (subj_dir / "polaris" / "brief.json").write_text("{}", encoding="utf-8")
        (subj_dir / "polaris" / "errorlog.md").write_text("errors", encoding="utf-8")
        (subj_dir / "polaris" / ".cache").mkdir()
        cache_item = subj_dir / "polaris" / ".cache" / "api_data.json"
        cache_item.write_text("{}", encoding="utf-8")

        # Setup standalone junk outside unit
        outside_junk = self.root / "Thumbs.db"
        outside_junk.write_text("junk", encoding="utf-8")

        # Tier 1 scan: polaris/.cache is NOT reported (Tier 2 only)
        det_t1 = JunkDetector(str(self.root), max_tier=JunkTier.TIER_1_SAFE)
        items_t1 = det_t1.find_junk()
        t1_paths = [i.path for i in items_t1]
        self.assertIn(str(outside_junk), t1_paths)
        self.assertNotIn(str(cache_item), t1_paths)

        # Tier 2 scan: polaris/.cache is reported as Tier 2
        det_t2 = JunkDetector(str(self.root), max_tier=JunkTier.TIER_2_DEV_CACHE)
        items_t2 = det_t2.find_junk()
        t2_paths = [i.path for i in items_t2]
        self.assertIn(str(outside_junk), t2_paths)
        self.assertIn(str(cache_item), t2_paths)

        # Purge execution at Tier 2 safely removes cache and leaves errorlog.md intact
        guard = SecurityGuard(drive_root=str(self.root))
        engine = PurgeEngine(guard, dry_run=False)
        summary = engine.purge_batch(items_t2, dry_run=False)

        self.assertFalse(cache_item.exists())
        self.assertFalse(outside_junk.exists())
        self.assertTrue((subj_dir / "polaris" / "errorlog.md").exists())
        self.assertTrue((subj_dir / "polaris" / "brief.json").exists())


class TestDuplicateFinderProtection(unittest.TestCase):
    """Test DuplicateDetector never proposes deleting copies inside learning units."""

    def setUp(self) -> None:
        clear_unit_cache()
        self.temp_dir = tempfile.mkdtemp(prefix="test_lu_dups_")
        self.root = Path(self.temp_dir)

    def tearDown(self) -> None:
        clear_unit_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_unit_copy_is_canonical_and_never_reclaimable(self) -> None:
        """When a file exists in a learning unit and outside, the unit copy is preserved first."""
        # Create Aurora Deck
        deck_dir = self.root / "react_deck"
        deck_dir.mkdir()
        (deck_dir / "deck.json").write_text('{"type": "deck"}', encoding="utf-8")
        (deck_dir / "images").mkdir()
        unit_img = deck_dir / "images" / "logo.png"
        content = b"identical-image-bytes-1234567890" * 100
        unit_img.write_bytes(content)

        # Create outside copy (in non-excluded folder)
        outside_copy = self.root / "06_Archives_Storage" / "logo_copy.png"
        outside_copy.parent.mkdir(parents=True, exist_ok=True)
        outside_copy.write_bytes(content)

        det = DuplicateDetector(str(self.root))
        dups = det.find_duplicates()

        # Find the group for the image
        img_groups = [g for g in dups if g["size"] == len(content)]
        self.assertEqual(len(img_groups), 1)
        group = img_groups[0]
        # Unit file must be first (index 0 = canonical keeper)
        self.assertEqual(group["files"][0], str(unit_img))
        # Reclaimable bytes should be for exactly 1 file (the outside copy)
        self.assertEqual(group["reclaimable_bytes"], len(content))

    def test_both_copies_in_units_yields_zero_reclaimable(self) -> None:
        """When identical copies exist across different learning units, neither is offered for deletion."""
        # Unit 1: Course
        c1 = self.root / "course_1"
        c1.mkdir()
        (c1 / "course.json").write_text('{"course_id": 1}', encoding="utf-8")
        f1 = c1 / "shared_asset.json"
        content = b'{"data": "shared-value-123"}'
        f1.write_bytes(content)

        # Unit 2: Polaris subject
        c2 = self.root / "polaris_1"
        (c2 / "polaris").mkdir(parents=True)
        (c2 / "polaris" / "brief.json").write_text('{"subject_id": 2}', encoding="utf-8")
        f2 = c2 / "shared_asset.json"
        f2.write_bytes(content)

        det = DuplicateDetector(str(self.root))
        dups = det.find_duplicates()

        asset_groups = [g for g in dups if g["size"] == len(content)]
        self.assertEqual(len(asset_groups), 1)
        group = asset_groups[0]
        # Reclaimable bytes must be 0 because neither copy in a learning unit may be deleted
        self.assertEqual(group["reclaimable_bytes"], 0)
        self.assertEqual(group["reclaimable_slack"], 0)


class TestAuditorHotspotsAndAdvice(unittest.TestCase):
    """Test StorageAuditor detects micro-file cluster slack hotspots and gives project-aware advice."""

    def setUp(self) -> None:
        clear_unit_cache()
        self.temp_dir = tempfile.mkdtemp(prefix="test_lu_audit_")
        self.root = Path(self.temp_dir)

    def tearDown(self) -> None:
        clear_unit_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hotspot_detection_and_tailored_advice(self) -> None:
        """Folders with >=20 micro-files (<32KB) and >90% slack are reported with tailored advice."""
        # 1. Polaris cache (30 tiny files)
        pol_dir = self.root / "polaris_math"
        (pol_dir / "polaris" / ".cache").mkdir(parents=True)
        (pol_dir / "polaris" / "brief.json").write_text("{}", encoding="utf-8")
        for i in range(30):
            (pol_dir / "polaris" / ".cache" / f"c_{i}.json").write_text('{"val":1}', encoding="utf-8")

        # 2. Polaris captures (25 files)
        (pol_dir / "polaris" / "captures").mkdir(parents=True)
        for i in range(25):
            (pol_dir / "polaris" / "captures" / f"cap_{i}.txt").write_text("capture data", encoding="utf-8")

        # 3. Aurora Deck images (22 files)
        deck_dir = self.root / "deck_js"
        deck_dir.mkdir(parents=True, exist_ok=True)
        (deck_dir / "deck.json").write_text("{}", encoding="utf-8")
        (deck_dir / "images").mkdir(parents=True, exist_ok=True)
        for i in range(22):
            (deck_dir / "images" / f"img_{i}.png").write_text("fake-png-data", encoding="utf-8")

        # 4. node_modules (20 files)
        node_dir = self.root / "app" / "node_modules"
        node_dir.mkdir(parents=True)
        for i in range(20):
            (node_dir / f"pkg_{i}.js").write_text("module.exports={};", encoding="utf-8")

        # 5. Generic folder (20 files)
        other_dir = self.root / "misc_tiny"
        other_dir.mkdir(parents=True)
        for i in range(20):
            (other_dir / f"file_{i}.dat").write_text("data", encoding="utf-8")

        auditor = StorageAuditor(str(self.root), cluster_size=524_288)
        result = auditor.run_audit()

        hotspots = result.get("micro_file_hotspots", [])
        self.assertGreaterEqual(len(hotspots), 5)

        # Check specific advice per hotspot
        advice_map = {h["rel_path"].replace("\\", "/").lower(): h["advice"] for h in hotspots}

        # Check Polaris cache advice
        found_cache = any("polaris/.cache" in k and "regenerable; safe to clean" in adv for k, adv in advice_map.items())
        self.assertTrue(found_cache)

        # Check Polaris captures advice
        found_captures = any("captures" in k and "study copies; fine unless very large" in adv for k, adv in advice_map.items())
        self.assertTrue(found_captures)

        # Check Aurora images advice
        found_images = any("images" in k and "prefer fewer, larger images" in adv for k, adv in advice_map.items())
        self.assertTrue(found_images)

        # Check node_modules advice
        found_node = any("node_modules" in k and "never on exFAT" in adv for k, adv in advice_map.items())
        self.assertTrue(found_node)

        # Check generic advice
        found_other = any("misc_tiny" in k and "bundle into an archive" in adv for k, adv in advice_map.items())
        self.assertTrue(found_other)

        # Verify get_hotspot_advice rules directly
        self.assertEqual(get_hotspot_advice("polaris/.cache"), "regenerable; safe to clean (tier 2); Polaris 0.3.2+ stores it on C:")
        self.assertEqual(get_hotspot_advice("polaris/captures"), "study copies; fine unless very large")
        self.assertEqual(get_hotspot_advice("slides/react/images"), "prefer fewer, larger images")
        self.assertEqual(get_hotspot_advice("frontend/node_modules"), "never on exFAT; keep toolchains on C:")
        self.assertEqual(get_hotspot_advice("repo/.git/objects"), "run git gc or keep the git dir on NTFS")
        self.assertEqual(get_hotspot_advice("other/random_folder"), "bundle into an archive or move to NTFS")

        # Verify total recoverable slack calculation
        self.assertGreater(result.get("total_recoverable_slack", 0), 0)

        # Verify markdown & ascii table reports include hotspots
        md = auditor.generate_markdown_report(result)
        self.assertIn("Micro-File Hotspots", md)
        self.assertIn("Total Recoverable Hotspot Slack", md)

        ascii_rep = auditor.generate_ascii_table_report(result)
        self.assertIn("MICRO-FILE CLUSTER SLACK HOTSPOTS", ascii_rep)


class TestLearningSummaryInStatus(unittest.TestCase):
    """Test learning summary reporting across learning_units, Sentinel, and status."""

    def setUp(self) -> None:
        clear_unit_cache()
        self.temp_dir = tempfile.mkdtemp(prefix="test_lu_status_")
        self.root = Path(self.temp_dir)

    def tearDown(self) -> None:
        clear_unit_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_learning_summary_and_sentinel(self) -> None:
        """get_learning_summary counts courses, standalone decks, subjects, and doc_chunks."""
        # 1. Course
        course_dir = self.root / "02_Learning_Knowledge" / "course_1"
        course_dir.mkdir(parents=True)
        (course_dir / "course.json").write_text("{}", encoding="utf-8")

        # 2. Standalone deck
        deck_dir = self.root / "02_Learning_Knowledge" / "deck_1"
        deck_dir.mkdir(parents=True)
        (deck_dir / "deck.json").write_text("{}", encoding="utf-8")

        # 3. Polaris subject
        pol_dir = self.root / "02_Learning_Knowledge" / "polaris_1"
        (pol_dir / "polaris").mkdir(parents=True)
        (pol_dir / "polaris" / "brief.json").write_text("{}", encoding="utf-8")

        # 4. Mock SQLite index.db with doc_chunks table
        db_dir = self.root / ".smart_drive"
        db_dir.mkdir()
        db_file = db_dir / "index.db"
        con = sqlite3.connect(str(db_file))
        con.execute("CREATE TABLE doc_chunks (id INTEGER PRIMARY KEY, content TEXT);")
        con.execute("INSERT INTO doc_chunks (content) VALUES ('slide 1'), ('slide 2'), ('fact 1');")
        con.execute("CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT);")
        con.execute("INSERT INTO index_meta (key, value) VALUES ('last_incremental_index', '1700000000.0');")
        con.commit()
        con.close()

        # Run get_learning_summary
        summary = get_learning_summary(str(self.root), db_path=str(db_file))
        self.assertEqual(summary["aurora_courses"], 1)
        self.assertEqual(summary["aurora_decks"], 1)
        self.assertEqual(summary["polaris_subjects"], 1)
        self.assertEqual(summary["chunk_count"], 3)
        self.assertIsNotNone(summary["last_index_update"])

        # Run SentinelEngine health check
        sentinel = SentinelEngine(str(self.root))
        report = sentinel.run_health_check()
        self.assertIn("learning", report)
        self.assertEqual(report["learning"]["aurora_courses"], 1)
        self.assertEqual(report["learning"]["chunk_count"], 3)


if __name__ == "__main__":
    unittest.main()
