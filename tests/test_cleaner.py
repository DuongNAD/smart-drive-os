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

    def test_rules_do_not_match_substrings_of_the_path(self) -> None:
        """User folders whose path merely contains a rule name (build, dist, ._) are never junk."""
        root = self.test_dir / "drive"
        user_files = [
            "Photos/buildings_2024/IMG_1.jpg",
            "Docs/my_dist_notes/notes.txt",
            "web/node_modules/lodash._basecopy/index.js",
        ]
        for rel in user_files:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("user data", encoding="utf-8")
        (root / "proj" / "build").mkdir(parents=True)
        (root / "proj" / "build" / "out.o").write_bytes(b"x")
        (root / "proj" / "Makefile").write_text("all:\n", encoding="utf-8")  # a build folder counts at a project root
        (root / "unit" / "polaris" / ".cache").mkdir(parents=True)
        (root / "unit" / "polaris" / ".cache" / "api.json").write_text("{}", encoding="utf-8")

        junk_items = JunkDetector(str(root), max_tier=JunkTier.TIER_3_SENSITIVE).find_junk()
        rel_paths = {item.rel_path.replace("\\", "/") for item in junk_items}

        for rel in user_files:
            for prefix in (rel, rel.rsplit("/", 1)[0]):
                self.assertNotIn(prefix, rel_paths)
        self.assertIn("proj/build", rel_paths)
        self.assertIn("unit/polaris/.cache", rel_paths)


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

    def test_windows_root_system_files_never_classified_or_purged(self) -> None:
        """Windows root system files (DumpStack.log.tmp, pagefile.sys, etc.) are protected from classify and purge."""
        from smart_drive.core.auto_zoner import AutoZoner
        from smart_drive.core.classifier import ClassifierEngine

        root = self.test_dir
        win_sys_files = [
            "DumpStack.log.tmp",
            "DumpStack.log",
            "pagefile.sys",
            "hiberfil.sys",
            "swapfile.sys",
        ]
        for fname in win_sys_files:
            (root / fname).write_bytes(b"WIN_KERNEL_LOCKED")

        subdir = root / "03_Development_Projects" / "p"
        subdir.mkdir(parents=True, exist_ok=True)
        nested_tmp = subdir / "foo.tmp"
        nested_tmp.write_text("regular temp file", encoding="utf-8")

        # 1. ClassifierEngine (both recursive=False and recursive=True) and AutoZoner ignore them
        classifier = ClassifierEngine(root_path=root)
        for rec in (False, True):
            classified_names = {r.name for r in classifier.scan_and_classify(root, recursive=rec)}
            for fname in win_sys_files:
                self.assertNotIn(fname, classified_names, f"{fname} should not be classified (recursive={rec})")

        zoner = AutoZoner(str(root))
        planned_names = {os.path.basename(a.src_path) for a in zoner.generate_plan()}
        for fname in win_sys_files:
            self.assertNotIn(fname, planned_names, f"{fname} should not be planned by AutoZoner")

        # 2. JunkDetector at Tier 3 ignores root system files, but still detects nested foo.tmp
        detector = JunkDetector(str(root), max_tier=JunkTier.TIER_3_SENSITIVE)
        junk_names = {item.name for item in detector.find_junk()}
        for fname in win_sys_files:
            self.assertNotIn(fname, junk_names, f"{fname} at root must not be flagged as Tier 3 junk")
        self.assertIn("foo.tmp", junk_names, "Nested foo.tmp should still be detected at Tier 3")

        # 3. PurgeEngine and SecurityGuard refuse direct deletion of root system files
        guard = SecurityGuard(str(root))
        engine = PurgeEngine(str(root), dry_run=False)
        for fname in win_sys_files:
            fpath = str(root / fname)
            is_prot, reason = guard.is_protected(fpath)
            self.assertTrue(is_prot, f"{fname} must be protected by SecurityGuard")
            with self.assertRaises(SecurityViolationError):
                guard.validate_deletion(fpath)
            rec_del = engine.delete_file(fpath)
            self.assertEqual(rec_del.status, "BLOCKED")
            self.assertTrue((root / fname).exists(), f"{fname} was deleted!")

    def test_common_game_library_folders_protected_from_move_and_clean(self) -> None:
        """All common game library folders are recognized by is_protected_root_dir and ignored by clean dry-run."""
        game_folders = [
            "steamapps",
            "steam",
            "epic games",
            "xboxgames",
            "battle.net",
            "gog games",
            "gog galaxy",
            "ea games",
            "electronic arts",
            "ubisoft",
            "ubisoft game launcher",
            "garena",
            "bluestacks",
            "games",
        ]
        for folder in game_folders:
            self.assertTrue(is_protected_root_dir(folder), f"{folder} must be protected")
            self.assertTrue(is_protected_root_dir(folder.upper()), f"{folder.upper()} must be protected")
            self.assertTrue(is_protected_root_dir(f"E:\\{folder}\\sub\\file.txt"), f"Nested path in {folder} must be protected")

        root = self.test_dir
        epic_sub = root / "Epic Games" / "x"
        epic_sub.mkdir(parents=True, exist_ok=True)
        (epic_sub / "a.log").write_text("log data", encoding="utf-8")
        (epic_sub / "hs_err_pid123.log").write_text("crash log", encoding="utf-8")
        (epic_sub / "cache.tmp").write_text("temp cache", encoding="utf-8")
        (epic_sub / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")

        detector = JunkDetector(str(root), max_tier=JunkTier.TIER_3_SENSITIVE)
        junk_paths = {item.rel_path for item in detector.find_junk()}
        self.assertFalse(
            any(p.lower().startswith("epic games/") for p in junk_paths),
            f"Files inside Epic Games should never be flagged by clean: {junk_paths}",
        )



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
        self.assertFalse((mock_root / ".smart_drive" / "quarantine").exists())


class TestQuarantineEngine(SmartDriveTestCase):
    """T11: PurgeEngine quarantines Tier 3 items, deletes Tier 1-2 immediately, and excludes quarantine from scanners."""

    def test_tier1_only_batch_does_not_create_quarantine_dir(self) -> None:
        mock_root = self.create_mock_drive()
        detector = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_1_SAFE)
        junk_items = detector.find_junk()
        self.assertGreater(len(junk_items), 0)

        engine = PurgeEngine(str(mock_root), dry_run=False, quarantine=True)
        summary = engine.purge_items(junk_items)
        self.assertEqual(summary.total_succeeded, len(junk_items))
        self.assertIsNone(engine.batch_id)
        self.assertFalse((mock_root / ".smart_drive" / "quarantine").exists())

    def test_quarantine_writes_manifest_for_tier3_only_and_is_ignored_by_scanners(self) -> None:
        import json
        from smart_drive.core.duplicates import DuplicateDetector
        from smart_drive.core.purge_engine import (
            list_quarantine_batches,
            restore_quarantine_batch,
        )
        from smart_drive.core.scanner import FastDirectoryScanner
        from smart_drive.indexer.db import DatabaseManager
        from smart_drive.indexer.manager import IndexManager

        mock_root = self.create_mock_drive()
        detector = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_3_SENSITIVE)
        junk_items = detector.find_junk()
        tier3_items = [j for j in junk_items if int(j.tier) == int(JunkTier.TIER_3_SENSITIVE)]
        tier12_items = [j for j in junk_items if int(j.tier) < int(JunkTier.TIER_3_SENSITIVE)]
        self.assertGreater(len(tier3_items), 0)
        self.assertGreater(len(tier12_items), 0)

        engine = PurgeEngine(str(mock_root), dry_run=False, quarantine=True)
        summary = engine.purge_items(junk_items)
        self.assertEqual(summary.total_succeeded, len(junk_items))
        self.assertIsNotNone(engine.batch_id)

        batch_dir = mock_root / ".smart_drive" / "quarantine" / str(engine.batch_id)
        manifest_path = batch_dir / "manifest.json"
        self.assertTrue(manifest_path.is_file())

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), len(tier3_items))
        manifest_origs = {entry["original"] for entry in manifest}
        for entry in manifest:
            self.assertTrue({"original", "quarantined", "size", "tier"} <= set(entry))
            self.assertEqual(entry["tier"], int(JunkTier.TIER_3_SENSITIVE))
            self.assertTrue((mock_root / entry["quarantined"]).exists())
        for j12 in tier12_items:
            self.assertNotIn(j12.rel_path, manifest_origs)
            self.assertFalse((batch_dir / j12.rel_path).exists())

        # Scanner, JunkDetector, DuplicateDetector, and IndexManager must ignore .smart_drive/quarantine
        scanned_rels = [e.rel_path for e in FastDirectoryScanner(str(mock_root)).scan_iter()]
        self.assertFalse(any(r.startswith(".smart_drive") for r in scanned_rels))

        re_detected = JunkDetector(str(mock_root), max_tier=JunkTier.TIER_3_SENSITIVE).find_junk()
        self.assertFalse(any(j.rel_path.startswith(".smart_drive") for j in re_detected))

        dup_plan = DuplicateDetector(str(mock_root)).generate_reclamation_plan()
        for grp in dup_plan.get("duplicate_groups", []):
            for fpath in grp.get("files", []):
                self.assertNotIn(".smart_drive", str(fpath))

        db_path = mock_root / ".smart_drive" / "index.db"
        db = DatabaseManager(str(db_path))
        try:
            db.initialize_schema()
            idx_mgr = IndexManager(db, str(mock_root))
            idx_mgr.full_index()
            rows = db.get_connection().execute("SELECT path FROM files WHERE path LIKE '%.smart_drive%'").fetchall()
            self.assertEqual(rows, [])
        finally:
            db.close()
        db_path.unlink(missing_ok=True)

        # List and restore Tier 3 items
        batches = list_quarantine_batches(str(mock_root))
        self.assertEqual(len(batches), 1)
        res = restore_quarantine_batch(str(mock_root), "latest")
        self.assertTrue(res["ok"])
        self.assertEqual(res["restored_count"], len(tier3_items))
        for item in tier3_items:
            self.assertTrue(os.path.exists(item.path))
        self.assertFalse(batch_dir.exists())

    def test_rename_failure_does_not_delete_original_file(self) -> None:
        from unittest.mock import patch

        mock_root = self.create_mock_drive()
        tmp_file = mock_root / "cache.tmp"
        tmp_file.write_bytes(b"temp data")
        self.assertTrue(tmp_file.exists())

        engine = PurgeEngine(str(mock_root), dry_run=False, quarantine=True)
        with patch("smart_drive.core.purge_engine.os.rename", side_effect=OSError("simulated rename failure")):
            rec = engine.delete_file(str(tmp_file))
        self.assertEqual(rec.status, "FAILED")
        self.assertTrue(tmp_file.exists(), "Original file must NOT be unlinked when os.rename fails")

    def test_purge_quarantine_batches_respects_older_than_and_dry_run(self) -> None:
        from smart_drive.core.purge_engine import purge_quarantine_batches

        root = self.test_dir
        old_junk = root / "old.tmp"
        old_junk.write_bytes(b"old")
        eng_old = PurgeEngine(str(root), dry_run=False, quarantine=True, batch_id="20260101-000000")
        eng_old.delete_file(str(old_junk))

        new_junk = root / "new.tmp"
        new_junk.write_bytes(b"new")
        eng_new = PurgeEngine(str(root), dry_run=False, quarantine=True, batch_id="20260110-000000")
        eng_new.delete_file(str(new_junk))

        import time as _time
        # 2026-01-11 00:00:00 local time: exactly 1.0 day after 20260110-000000 and 10.0 days after 20260101-000000
        injected_now = float(_time.mktime(_time.strptime("20260111-000000", "%Y%m%d-%H%M%S")))

        # Tight boundary check: at injected_now - 60s (less than 1 day after 20260110-000000), older_than=1 matches only 20260101-000000
        preview_tight = purge_quarantine_batches(str(root), older_than_days=1, dry_run=True, now=injected_now - 60.0)
        self.assertEqual(preview_tight["matched_count"], 1)
        self.assertEqual(preview_tight["kept_count"], 1)

        # Dry-run with older_than=5 days matches only 20260101-000000 and leaves both on disk
        preview = purge_quarantine_batches(str(root), older_than_days=5, dry_run=True, now=injected_now)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["matched_count"], 1)
        self.assertEqual(preview["kept_count"], 1)
        self.assertTrue((root / ".smart_drive" / "quarantine" / "20260101-000000").is_dir())
        self.assertTrue((root / ".smart_drive" / "quarantine" / "20260110-000000").is_dir())

        # Apply with older_than=5 days deletes only the old batch
        applied = purge_quarantine_batches(str(root), older_than_days=5, dry_run=False, now=injected_now)
        self.assertFalse(applied["dry_run"])
        self.assertEqual(applied["purged_count"], 1)
        self.assertEqual(applied["kept_count"], 1)
        self.assertFalse((root / ".smart_drive" / "quarantine" / "20260101-000000").exists())
        self.assertTrue((root / ".smart_drive" / "quarantine" / "20260110-000000").is_dir())

    def test_quarantine_batch_uses_local_time(self) -> None:
        import time as _time
        from unittest.mock import patch
        from smart_drive.core.purge_engine import list_quarantine_batches

        root = self.test_dir
        junk = root / "local.tmp"
        junk.write_bytes(b"local")

        fixed_local = _time.strptime("20260515-143025", "%Y%m%d-%H%M%S")
        with patch("smart_drive.core.purge_engine.time.localtime", return_value=fixed_local):
            eng = PurgeEngine(str(root), dry_run=False, quarantine=True)
            eng.delete_file(str(junk))

        self.assertEqual(eng.batch_id, "20260515-143025")
        batches = list_quarantine_batches(str(root))
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["batch_id"], "20260515-143025")
        self.assertIn("2026-05-15 14:30:25", batches[0]["local_time"])

    def test_unknown_tier_items_are_quarantined(self) -> None:
        root = self.test_dir
        f_dict = root / "Thumbs.db"
        f_dict.write_bytes(b"dict_no_tier")
        f_direct = root / "custom_item.dat"
        f_direct.write_bytes(b"direct_no_tier")

        eng = PurgeEngine(str(root), dry_run=False, quarantine=True)
        # 1) purge_items with dict lacking "tier" -> quarantined (even if name matches Tier 1)
        summary = eng.purge_items([{"path": str(f_dict), "size": 12, "is_dir": False}])
        self.assertEqual(summary.quarantined_count, 1)
        self.assertEqual(summary.deleted_count, 0)

        # 2) delete_file(path) without passing tier -> quarantined
        rec = eng.delete_file(str(f_direct))
        self.assertEqual(rec.status, "QUARANTINED")
        self.assertIsNone(rec.tier)

        self.assertIsNotNone(eng.batch_id)
        batch_dir = root / ".smart_drive" / "quarantine" / str(eng.batch_id)
        self.assertTrue((batch_dir / "Thumbs.db").is_file())
        self.assertTrue((batch_dir / "custom_item.dat").is_file())


if __name__ == "__main__":
    unittest.main()
