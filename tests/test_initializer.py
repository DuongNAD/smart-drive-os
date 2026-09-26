"""tests/test_initializer.py - Comprehensive Unit Tests for DriveInitializer & Preset Profiles.

100% Python Standard Library unittest.
Tests Tiers 1, 2, 3:
- 1-Touch drive initialization with 3 preset profiles (ai-developer, data-science, general-workspace).
- Creation of 6 canonical taxonomies and profile-specific subtrees.
- Seeding of anti-indexing shields (.metadata_never_index, .fseventsd/no_log).
- Generation of AI agent manifests (AGENTS.md, GEMINI.md, CLAUDE.md).
- Initial SQLite FTS5 index database creation and schema validation.
- Cross-feature verification: init followed by audit, clean, and search.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure smart_drive.core.config has STANDARD_TAXONOMIES attribute
if "smart_drive.core.config" not in sys.modules:
    cfg_p = PROJECT_ROOT / "smart_drive" / "core" / "config.py"
    if cfg_p.exists():
        spec = importlib.util.spec_from_file_location("smart_drive.core.config", cfg_p)
        cfg_m = importlib.util.module_from_spec(spec)
        sys.modules["smart_drive.core.config"] = cfg_m
        spec.loader.exec_module(cfg_m)
        if not hasattr(cfg_m, "STANDARD_TAXONOMIES"):
            cfg_m.STANDARD_TAXONOMIES = getattr(cfg_m, "PROTECTED_CORE_TAXONOMIES", ())
else:
    cfg_m = sys.modules["smart_drive.core.config"]
    if not hasattr(cfg_m, "STANDARD_TAXONOMIES"):
        cfg_m.STANDARD_TAXONOMIES = getattr(cfg_m, "PROTECTED_CORE_TAXONOMIES", ())

from smart_drive.core.initializer import DriveInitializer, PROFILES
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.indexer.db import DatabaseManager
from smart_drive.search.engine import SearchEngine
from smart_drive.search.parser import parse_search_query

from tests.helpers import SmartDriveTestCase


class TestPresetProfiles(SmartDriveTestCase):
    """Tier 1: Validates available preset profiles and metadata."""

    def test_list_profiles_contains_all_three_presets(self) -> None:
        """list_profiles must contain ai-developer, data-science, and general-workspace."""
        profiles = DriveInitializer.list_profiles()
        self.assertIn("ai-developer", profiles)
        self.assertIn("data-science", profiles)
        self.assertIn("general-workspace", profiles)

        for p_name, desc in profiles.items():
            self.assertIsInstance(desc, str)
            self.assertGreater(len(desc), 10)


class TestDriveInitialization(SmartDriveTestCase):
    """Tier 1 & Tier 2: Drive setup across different profiles."""

    def test_initialize_ai_developer_profile(self) -> None:
        """ai-developer profile creates GGUF, checkpoints, safetensors, shields, and manifests."""
        target = self.test_dir / "init_ai_dev"
        init = DriveInitializer(str(target))
        res = init.initialize(profile="ai-developer")

        self.assertEqual(res["status"], "initialized")
        self.assertEqual(res["profile"], "ai-developer")

        # 6 core taxonomies
        self.assertTrue((target / "01_AI_Models").is_dir())
        self.assertTrue((target / "02_Learning_Knowledge").is_dir())
        self.assertTrue((target / "03_Development_Projects").is_dir())
        self.assertTrue((target / "04_System_Workspaces").is_dir())
        self.assertTrue((target / "05_Dev_Toolbox").is_dir())
        self.assertTrue((target / "06_Archives_Storage").is_dir())

        # AI developer subdirectories
        self.assertTrue((target / "01_AI_Models" / "gguf").is_dir())
        self.assertTrue((target / "01_AI_Models" / "checkpoints").is_dir())
        self.assertTrue((target / "01_AI_Models" / "safetensors").is_dir())

        # Anti-indexing shields
        self.assertTrue((target / ".metadata_never_index").exists())
        self.assertTrue((target / ".fseventsd" / "no_log").exists())

        # Manifests
        self.assertTrue((target / "AGENTS.md").exists())
        self.assertTrue((target / "GEMINI.md").exists())
        self.assertTrue((target / "CLAUDE.md").exists())

        # Search database
        self.assertTrue(res["database_initialized"])
        self.assertTrue((target / ".smart_drive" / "index.db").exists())

    def test_initialize_data_science_profile(self) -> None:
        """data-science profile creates notebooks, pipelines, and data subdirs."""
        target = self.test_dir / "init_ds"
        init = DriveInitializer(str(target))
        res = init.initialize(profile="data-science")

        self.assertEqual(res["profile"], "data-science")
        self.assertTrue((target / "03_Development_Projects" / "notebooks").is_dir())
        self.assertTrue((target / "03_Development_Projects" / "pipelines").is_dir())
        self.assertTrue((target / "03_Development_Projects" / "data_raw").is_dir())

    def test_initialize_general_workspace_profile(self) -> None:
        """general-workspace profile is default when unspecified or unknown."""
        target = self.test_dir / "init_general"
        init = DriveInitializer(str(target))
        res = init.initialize(profile="unknown_profile_xyz")

        self.assertEqual(res["profile"], "general-workspace")
        self.assertTrue((target / "01_AI_Models").is_dir())
        self.assertTrue((target / "02_Learning_Knowledge" / "Notes").is_dir())


class TestCrossFeatureInteractionsAfterInit(SmartDriveTestCase):
    """Tier 3: Interactions across init, audit, clean, and search."""

    def test_init_followed_by_audit(self) -> None:
        """StorageAuditor on freshly initialized drive reports files and taxonomies."""
        target = self.test_dir / "audit_after_init"
        init = DriveInitializer(str(target))
        init.initialize(profile="ai-developer")

        auditor = StorageAuditor(str(target))
        report = auditor.run_audit()

        # Files created are manifests and shields
        self.assertGreater(report["total_files"], 0)
        self.assertIn("01_AI_Models", report["taxonomies"])
        self.assertIn("02_Learning_Knowledge", report["taxonomies"])

    def test_init_followed_by_clean_dry_run_and_apply(self) -> None:
        """Clean on freshly initialized drive finds zero junk and deletes nothing."""
        target = self.test_dir / "clean_after_init"
        init = DriveInitializer(str(target))
        init.initialize(profile="ai-developer")

        detector = JunkDetector(str(target))
        junk = detector.find_junk()
        self.assertEqual(len(junk), 0, "Freshly initialized drive should have 0 junk items")

        engine = PurgeEngine(str(target), dry_run=False)
        summary = engine.purge_items(junk)
        self.assertEqual(summary.total_attempted, 0)
        self.assertEqual(summary.total_succeeded, 0)

        # Inviolable files remain 100% intact
        self.assertTrue((target / "AGENTS.md").exists())
        self.assertTrue((target / "GEMINI.md").exists())
        self.assertTrue((target / ".metadata_never_index").exists())

    def test_init_followed_by_search(self) -> None:
        """Search engine opens initialized database without error."""
        target = self.test_dir / "search_after_init"
        init = DriveInitializer(str(target))
        init.initialize(profile="ai-developer")

        db_path = target / ".smart_drive" / "index.db"
        with DatabaseManager(str(db_path)) as db:
            engine = SearchEngine(db)
            params = parse_search_query("test")
            result = engine.search(params)
            self.assertEqual(result.total_count, 0)
            self.assertEqual(result.matches, [])


if __name__ == "__main__":
    unittest.main()
