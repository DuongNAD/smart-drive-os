"""tests/test_cleaner.py - Comprehensive Unit Tests for JunkDetector and PurgeEngine.

100% Python Standard Library unittest.
Tests Tiers 1, 2, 3:
- 3-Tier junk detection (Tier 1 safe OS junk, Tier 2 dev cache, Tier 3 crash logs/tmp).
- Inviolable whitelist safeguards (never flags or deletes protected files/taxonomies).
- AppleDouble resource fork vs anti-indexing shield discrimination.
- PurgeEngine dry-run simulation (verifies files remain untouched on disk).
- PurgeEngine apply execution (verifies junk files are purged and space reclaimed).
- SecurityGuard boundary validation and SecurityViolationError enforcement.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.core.config import JunkTier, is_protected_root_file, is_protected_root_dir
    from smart_drive.core.junk_detector import JunkDetector, JunkItem
    from smart_drive.core.purge_engine import (
        DeletionRecord,
        PurgeEngine,
        PurgeSummary,
        SecurityGuard,
        SecurityViolationError,
    )
except ImportError:
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from config import JunkTier, is_protected_root_file, is_protected_root_dir
    from core.junk_detector import JunkDetector, JunkItem
    from core.purge_engine import (
        DeletionRecord,
        PurgeEngine,
        PurgeSummary,
        SecurityGuard,
        SecurityViolationError,
    )

from tests.helpers import SmartDriveTestCase, oracle_is_protected, oracle_is_junk


class TestJunkDetectionTiers(SmartDriveTestCase):
    """Tier 1: Declarative 3-tier junk detection rules."""

    def test_tier1_safe_junk_detected(self) -> None:
        """Tier 1 matches OS junk (._*, .DS_Store, Thumbs.db) while ignoring clean files."""
        mock_root = self.create_mock_drive()
        detector = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_1_SAFE)
        junk_items = detector.find_junk()

        names = {item.name for item in junk_items}
        self.assertIn(".DS_Store", names)
        self.assertIn("Thumbs.db", names)
        self.assertTrue(any(name.startswith("._") for name in names))

        # Must NOT include Tier 3 files (.tmp, .dmp) when searching Tier 1 only
        self.assertNotIn("cache.tmp", names)
        self.assertNotIn("crash_dump.dmp", names)

    def test_tier2_and_tier3_junk_detected(self) -> None:
        """Tier 3 search includes all tiers (OS junk, dev caches, and temp dumps)."""
        mock_root = self.create_mock_drive()
        detector = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_3_SENSITIVE)
        junk_items = detector.find_junk()

        names = {item.name for item in junk_items}
        self.assertIn(".DS_Store", names)
        self.assertIn("Thumbs.db", names)
        self.assertIn("cache.tmp", names)
        self.assertIn("crash_dump.dmp", names)

    def test_shield_is_not_flagged_as_junk(self) -> None:
        """Anti-indexing shields (.metadata_never_index, .fseventsd/no_log) must never be junk."""
        mock_root = self.create_mock_drive()
        detector = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_3_SENSITIVE)
        junk_items = detector.find_junk()

        names = {item.name for item in junk_items}
        self.assertNotIn(".metadata_never_index", names)
        self.assertNotIn("no_log", names)


class TestInviolableWhitelistGuards(SmartDriveTestCase):
    """Tier 1 & Tier 2: Inviolable whitelist safeguards against accidental purges."""

    def test_inviolable_root_files_never_flagged_or_deleted(self) -> None:
        """Root manifests and launchers are strictly protected."""
        mock_root = self.create_mock_drive()
        guard = SecurityGuard(str(mock_root))

        protected_files = [
            "GEMINI.md",
            "CLAUDE.md",
            "AGENTS.md",
            "README.md",
            "Setup_Win.bat",
            "Setup_Mac.command",
            "check_ssd_status.ps1",
            "sync_repos.ps1",
            ".metadata_never_index",
        ]

        for fname in protected_files:
            file_path = mock_root / fname
            is_prot, reason = guard.is_protected(str(file_path))
            self.assertTrue(is_prot, f"File {fname} was not protected: {reason}")
            oracle_prot, _ = oracle_is_protected(file_path, mock_root)
            self.assertTrue(oracle_prot, f"Oracle mismatch for {fname}")

    def test_protected_root_taxonomies_never_purged(self) -> None:
        """Standard taxonomy root directories are guarded against deletion."""
        mock_root = self.create_mock_drive()
        guard = SecurityGuard(str(mock_root))

        taxonomies = [
            "01_AI_Models",
            "02_Learning_Knowledge",
            "03_Development_Projects",
            "04_System_Workspaces",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ]

        for tax in taxonomies:
            tax_path = mock_root / tax
            is_prot, reason = guard.is_protected(str(tax_path))
            self.assertTrue(is_prot, f"Taxonomy {tax} was not protected: {reason}")

    def test_validate_deletion_raises_security_violation(self) -> None:
        """Attempting to validate deletion of a protected file raises SecurityViolationError."""
        mock_root = self.create_mock_drive()
        guard = SecurityGuard(str(mock_root))

        with self.assertRaises(SecurityViolationError):
            guard.validate_deletion(str(mock_root / "AGENTS.md"))
        with self.assertRaises(SecurityViolationError):
            guard.validate_deletion(str(mock_root / "01_AI_Models"))


class TestPurgeEngineExecution(SmartDriveTestCase):
    """Tier 1 & Tier 3: Dry-run simulation and apply purge execution."""

    def test_dry_run_simulation_preserves_files_on_disk(self) -> None:
        """Dry-run marks status as SIMULATED and does not delete any files."""
        mock_root = self.create_mock_drive()
        detector = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_1_SAFE)
        junk_items = detector.find_junk()
        self.assertGreater(len(junk_items), 0)

        engine = PurgeEngine(str(mock_root), dry_run=True)
        summary = engine.purge_items(junk_items)
        self.assertTrue(summary.dry_run)
        self.assertEqual(summary.total_attempted, len(junk_items))
        self.assertEqual(summary.total_succeeded, len(junk_items))

        # Verify all junk files still physically exist on disk
        for item in junk_items:
            self.assertTrue(os.path.exists(item.path), f"File was deleted in dry-run: {item.path}")

    def test_apply_purge_unlinks_junk_and_leaves_clean_drive(self) -> None:
        """Apply mode (dry_run=False) permanently unlinks junk items and keeps user data."""
        mock_root = self.create_mock_drive()
        detector = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_1_SAFE)
        junk_items = detector.find_junk()

        engine = PurgeEngine(str(mock_root), dry_run=False)
        summary = engine.purge_items(junk_items)
        self.assertFalse(summary.dry_run)
        self.assertGreater(summary.total_succeeded, 0)
        self.assertGreater(summary.nominal_bytes_reclaimed, 0)

        # Verify files are deleted
        for item in junk_items:
            self.assertFalse(os.path.exists(item.path), f"File still exists after purge: {item.path}")

        # Inviolable user files must still exist
        self.assertTrue((mock_root / "GEMINI.md").exists())
        self.assertTrue((mock_root / "01_AI_Models" / "GGUF" / "llama-3-8b.Q4_K_M.gguf").exists())
        self.assertTrue((mock_root / "02_Learning_Knowledge" / "INDEX.md").exists())
        self.assertTrue((mock_root / ".metadata_never_index").exists())

    def test_attempt_delete_protected_file_is_blocked(self) -> None:
        """Even with dry_run=False, deleting a protected file produces blocked record."""
        mock_root = self.create_mock_drive()
        engine = PurgeEngine(str(mock_root), dry_run=False)

        gemini_path = str(mock_root / "GEMINI.md")
        success, msg = engine.delete_item(gemini_path, size=100, is_dir=False)
        self.assertFalse(success)
        self.assertIn("Inviolable", msg)
        self.assertTrue((mock_root / "GEMINI.md").exists())


if __name__ == "__main__":
    unittest.main()
