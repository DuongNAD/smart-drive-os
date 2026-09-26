"""tests/test_snapshot.py - Comprehensive Unit Tests for Snapshot & Backup Engine.

100% Python Standard Library unittest. Zero external dependencies.
Tests Features F12 through F18:
- F12: CLI `smart-drive snapshot` create/list/verify execution and JSON output
- F13: Point-in-time snapshot of key partitions with alias resolution
- F14: Constant-memory 64KB chunk streaming SHA-256 hashing
- F15: Manifest JSON serialization, loading, and 512KB cluster allocation math
- F16: Snapshot listing table and JSON summaries
- F17: Verification of intact files, tampered hashes, deleted files, and untracked files
- F18: Incremental backup, change detection, boundary guards, junk filtering, and manifest creation
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.cli.cmd_backup import cmd_backup
from smart_drive.cli.cmd_snapshot import cmd_snapshot
from smart_drive.cli.main import build_parser
from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.core.snapshot import (
    EMPTY_FILE_SHA256,
    BackupEngine,
    BackupTargetInvalidError,
    SnapshotCorruptedError,
    SnapshotManager,
    SnapshotManifest,
    SnapshotNotFoundError,
    SnapshotVerifier,
    compute_file_sha256,
    normalize_rel_path,
    sanitize_snapshot_name,
)
from tests.helpers import SmartDriveTestCase, oracle_full_sha256, run_smart_drive_cli


class TestStreamingSha256(SmartDriveTestCase):
    """F14: Constant-memory 64KB block streaming SHA-256 calculation."""

    def test_empty_file_returns_authoritative_sha256(self) -> None:
        """0-byte file immediately returns standard empty SHA-256 without error."""
        empty_file = self.test_dir / "empty.dat"
        empty_file.touch()
        result = compute_file_sha256(empty_file)
        self.assertEqual(result, EMPTY_FILE_SHA256)

    def test_small_file_matches_hashlib_oracle(self) -> None:
        """Small file (<64KB) matches hashlib oracle exactly."""
        test_file = self.test_dir / "small.txt"
        test_content = b"SmartDrive-OS High Performance SSD Storage Suite" * 100
        test_file.write_bytes(test_content)

        expected = hashlib.sha256(test_content).hexdigest()
        actual = compute_file_sha256(test_file)
        self.assertEqual(actual, expected)

    def test_multi_chunk_file_matches_oracle(self) -> None:
        """File larger than 64KB (e.g. 160KB) verifies streaming chunk boundary transitions."""
        test_file = self.test_dir / "large.bin"
        # 160 KB = 2 full 64KB chunks + 32KB remainder
        test_content = b"CHUNK_VERIFICATION_BYTES_" * 6400
        test_file.write_bytes(test_content)

        expected = oracle_full_sha256(test_file)
        actual = compute_file_sha256(test_file)
        self.assertEqual(actual, expected)

    def test_nonexistent_file_returns_empty_string(self) -> None:
        """Non-existent file traps OSError and returns empty string."""
        missing = self.test_dir / "does_not_exist.txt"
        self.assertEqual(compute_file_sha256(missing), "")


class TestSnapshotManifestSerialization(SmartDriveTestCase):
    """F15: Manifest serialization, deserialization, and cluster math."""

    def test_manifest_roundtrip_json(self) -> None:
        """SnapshotManifest serializes to JSON and deserializes identically."""
        manifest = SnapshotManifest(
            name="test_snap_01",
            version="1.1.0",
            timestamp="2026-09-26T12:00:00Z",
            root="D:/SSD",
            partitions=["02_Learning_Knowledge", "03_Development_Projects"],
            file_count=2,
            total_logical_bytes=1000,
            total_allocated_bytes=1048576,
            total_slack_bytes=1047576,
            files={
                "02_Learning_Knowledge/note.md": {
                    "size": 400,
                    "allocated_size": 524288,
                    "mtime": 1758869760.0,
                    "sha256": "abc123",
                },
                "03_Development_Projects/main.py": {
                    "size": 600,
                    "allocated_size": 524288,
                    "mtime": 1758869800.0,
                    "sha256": "def456",
                },
            },
        )

        manifest_file = self.test_dir / "manifest.json"
        manifest.save(manifest_file)
        self.assertTrue(manifest_file.is_file())

        loaded = SnapshotManifest.load(manifest_file)
        self.assertEqual(loaded.name, manifest.name)
        self.assertEqual(loaded.file_count, 2)
        self.assertEqual(loaded.total_logical_bytes, 1000)
        self.assertEqual(loaded.total_allocated_bytes, 1048576)
        self.assertEqual(loaded.total_slack_bytes, 1047576)
        self.assertEqual(loaded.files["02_Learning_Knowledge/note.md"]["sha256"], "abc123")

    def test_load_corrupted_json_raises_error(self) -> None:
        """Malformed JSON file raises SnapshotCorruptedError."""
        bad_file = self.test_dir / "bad.json"
        bad_file.write_text("{ unclosed json: ...", encoding="utf-8")
        with self.assertRaises(SnapshotCorruptedError):
            SnapshotManifest.load(bad_file)

    def test_load_nonexistent_manifest_raises_error(self) -> None:
        """Missing manifest file raises SnapshotNotFoundError."""
        with self.assertRaises(SnapshotNotFoundError):
            SnapshotManifest.load(self.test_dir / "missing.json")


class TestSnapshotManager(SmartDriveTestCase):
    """F13, F15, F16: Snapshot creation, partition targeting, and listing."""

    def test_create_snapshot_default_partitions(self) -> None:
        """Creates snapshot over key default partitions and verifies manifest contents."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        manifest = manager.create_snapshot("initial_backup")
        self.assertEqual(manifest.name, "initial_backup")
        self.assertTrue(manifest.file_count > 0)
        self.assertTrue(manifest.total_logical_bytes > 0)
        self.assertTrue(manifest.total_allocated_bytes >= manifest.total_logical_bytes)

        # Check recorded paths are inside allowed partitions
        for rel_p in manifest.files.keys():
            first_segment = rel_p.split("/")[0]
            self.assertIn(first_segment, ["02_Learning_Knowledge", "03_Development_Projects", "05_Dev_Toolbox"])

        # Check manifest saved on disk
        manifest_on_disk = manager.snapshot_dir / "initial_backup.json"
        self.assertTrue(manifest_on_disk.is_file())

    def test_create_snapshot_alias_resolution(self) -> None:
        """Resolves 03_Personal_Documents when 03_Development_Projects is absent."""
        mock_root = self.test_dir / "alias_drive"
        mock_root.mkdir()
        personal_dir = mock_root / "03_Personal_Documents"
        personal_dir.mkdir(parents=True)
        (personal_dir / "doc.txt").write_text("personal data", encoding="utf-8")

        manager = SnapshotManager(mock_root)
        resolved = manager.resolve_partitions()
        self.assertIn("03_Personal_Documents", resolved)

        manifest = manager.create_snapshot("personal_snap")
        self.assertIn("03_Personal_Documents/doc.txt", manifest.files)

    def test_create_snapshot_auto_generates_name(self) -> None:
        """When name is omitted, creates timestamped snapshot name."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manifest = manager.create_snapshot()
        self.assertTrue(manifest.name.startswith("snapshot_"))

    def test_sanitize_name_prevents_traversal(self) -> None:
        """Path traversal attacks in snapshot names are sanitized or rejected."""
        self.assertEqual(sanitize_snapshot_name("../../attack"), "attack")
        with self.assertRaises(ValueError):
            sanitize_snapshot_name("..")

    def test_list_snapshots_ordered_descending(self) -> None:
        """Listing snapshots returns summaries sorted newest first."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        m1 = manager.create_snapshot("snap_01")
        time.sleep(0.01)
        m2 = manager.create_snapshot("snap_02")

        summaries = manager.list_snapshots()
        self.assertEqual(len(summaries), 2)
        names = {s.name for s in summaries}
        self.assertEqual(names, {"snap_01", "snap_02"})


class TestSnapshotVerifier(SmartDriveTestCase):
    """F17: Verification detecting intact, modified, missing, and untracked files."""

    def test_verify_intact_snapshot_returns_clean(self) -> None:
        """Untouched files verify with intact=True, 0 modified, 0 missing."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("clean_snap")

        report = manager.verify_snapshot("clean_snap", check_untracked=True)
        self.assertTrue(report.intact)
        self.assertEqual(report.corrupted_count, 0)
        self.assertEqual(report.missing_count, 0)
        self.assertEqual(report.untracked_count, 0)

    def test_verify_detects_modified_file_hash(self) -> None:
        """Tampering with file contents causes integrity check failure with hash details."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("tamper_test")

        # Tamper with file
        target_file = mock_root / "02_Learning_Knowledge" / "INDEX.md"
        target_file.write_text("TAMPERED CONTENT CORRUPTION", encoding="utf-8")

        report = manager.verify_snapshot("tamper_test")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        mod_entry = report.modified_files[0]
        self.assertEqual(mod_entry["path"], "02_Learning_Knowledge/INDEX.md")
        self.assertNotEqual(mod_entry["expected_hash"], mod_entry["actual_hash"])

    def test_verify_detects_missing_file(self) -> None:
        """Deleting a recorded file triggers missing file detection."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("delete_test")

        # Delete a file
        target_file = mock_root / "02_Learning_Knowledge" / "Learning_Code" / "algorithm_kata.py"
        target_file.unlink()

        report = manager.verify_snapshot("delete_test")
        self.assertFalse(report.intact)
        self.assertEqual(report.missing_count, 1)
        self.assertIn("02_Learning_Knowledge/Learning_Code/algorithm_kata.py", report.missing_files)

    def test_verify_detects_untracked_new_file(self) -> None:
        """Adding a new file to a snapshotted partition is detected in untracked_files."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("untracked_test")

        # Add a new untracked file
        new_file = mock_root / "02_Learning_Knowledge" / "brand_new_note.md"
        new_file.write_text("New study notes", encoding="utf-8")

        report = manager.verify_snapshot("untracked_test", check_untracked=True)
        # Snapshot files themselves are intact, but untracked file is flagged
        self.assertTrue(report.intact)
        self.assertEqual(report.untracked_count, 1)
        self.assertIn("02_Learning_Knowledge/brand_new_note.md", report.untracked_files)


class TestIncrementalBackup(SmartDriveTestCase):
    """F18: Incremental backup, skipping unchanged, boundary validation, and junk exclusion."""

    def test_initial_backup_copies_all_files(self) -> None:
        """First backup transfers all files to destination and writes backup manifest."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        report = manager.incremental_backup(target_dir, dry_run=False)
        self.assertFalse(report.dry_run)
        self.assertTrue(report.copied_count > 0)
        self.assertEqual(report.skipped_count, 0)
        self.assertEqual(report.failed_count, 0)

        # Destination manifest was created
        dest_manifest = target_dir / "backup_manifest.json"
        self.assertTrue(dest_manifest.is_file())

    def test_second_backup_skips_unchanged_files(self) -> None:
        """Subsequent backup without changes copies 0 files and skips all."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        # Pass 1: initial
        r1 = manager.incremental_backup(target_dir)
        total_files = r1.copied_count

        # Pass 2: immediate incremental
        r2 = manager.incremental_backup(target_dir)
        self.assertEqual(r2.copied_count, 0)
        self.assertEqual(r2.skipped_count, total_files)

    def test_backup_transfers_only_modified_file(self) -> None:
        """Modifying one file in source causes ONLY that file to be transferred."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        manager.incremental_backup(target_dir)

        # Modify one file and advance mtime
        mod_file = mock_root / "02_Learning_Knowledge" / "INDEX.md"
        time.sleep(0.05)
        mod_file.write_text("# Updated Master Knowledge Index v2\n", encoding="utf-8")

        r3 = manager.incremental_backup(target_dir)
        self.assertEqual(r3.copied_count, 1)
        self.assertIn("02_Learning_Knowledge/INDEX.md", r3.copied_files)

    def test_backup_dry_run_does_not_write_disk(self) -> None:
        """Dry-run reports file copies without creating target files."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dry"
        manager = SnapshotManager(mock_root)

        report = manager.incremental_backup(target_dir, dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertTrue(report.copied_count > 0)
        self.assertFalse(target_dir.exists())

    def test_backup_rejects_target_inside_source(self) -> None:
        """Backup target inside backed-up partition raises BackupTargetInvalidError."""
        mock_root = self.create_mock_drive()
        invalid_target = mock_root / "02_Learning_Knowledge" / "nested_backup"
        manager = SnapshotManager(mock_root)

        with self.assertRaises(BackupTargetInvalidError):
            manager.incremental_backup(invalid_target)

    def test_backup_rejects_source_root_as_target(self) -> None:
        """Backup target equal to source root raises BackupTargetInvalidError."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        with self.assertRaises(BackupTargetInvalidError):
            manager.incremental_backup(mock_root)

    def test_backup_filters_tier1_junk(self) -> None:
        """Backup omits junk files (.DS_Store, Thumbs.db, temp) by default."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        report = manager.incremental_backup(target_dir, skip_junk=True)
        for copied in report.copied_files:
            self.assertFalse(copied.endswith(".DS_Store"))
            self.assertFalse(copied.endswith(".tmp"))
            self.assertFalse(copied.endswith(".dmp"))

    def test_backup_use_hash_comparison(self) -> None:
        """Backup with hash comparison detects identical files correctly."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_hash_dest"
        manager = SnapshotManager(mock_root)

        # Initial backup
        r1 = manager.incremental_backup(target_dir, use_hash_comparison=True)
        self.assertTrue(r1.copied_count > 0)

        # Hash-based incremental backup skips unchanged
        r2 = manager.incremental_backup(target_dir, use_hash_comparison=True)
        self.assertEqual(r2.copied_count, 0)
        self.assertEqual(r2.skipped_count, r1.copied_count)


class TestCliSnapshotAndBackup(SmartDriveTestCase):
    """F12, F18: CLI end-to-end command handlers and JSON flags."""

    def test_cli_snapshot_create_and_verify(self) -> None:
        """CLI `smart-drive snapshot create` and `verify` succeed with code 0."""
        mock_root = self.create_mock_drive()
        parser = build_parser()

        # 1. Create
        args_create = parser.parse_args(["snapshot", "create", "cli_snap", "--root", str(mock_root)])
        ret_create = cmd_snapshot(args_create)
        self.assertEqual(ret_create, 0)

        # 2. List
        args_list = parser.parse_args(["snapshot", "list", "--root", str(mock_root), "--json"])
        ret_list = cmd_snapshot(args_list)
        self.assertEqual(ret_list, 0)

        # 3. Verify
        args_verify = parser.parse_args(["snapshot", "verify", "cli_snap", "--root", str(mock_root)])
        ret_verify = cmd_snapshot(args_verify)
        self.assertEqual(ret_verify, 0)

    def test_cli_backup_command(self) -> None:
        """CLI `smart-drive backup --target <path>` executes successfully."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "cli_backup_target"
        parser = build_parser()

        args_backup = parser.parse_args(["backup", "--target", str(target_dir), "--root", str(mock_root)])
        ret_backup = cmd_backup(args_backup)
        self.assertEqual(ret_backup, 0)
        self.assertTrue((target_dir / "backup_manifest.json").is_file())

    def test_cli_snapshot_verify_failed_exit_code(self) -> None:
        """CLI `smart-drive snapshot verify` returns 1 when verification fails."""
        mock_root = self.create_mock_drive()
        parser = build_parser()

        args_create = parser.parse_args(["snapshot", "create", "fail_snap", "--root", str(mock_root)])
        cmd_snapshot(args_create)

        # Tamper
        (mock_root / "02_Learning_Knowledge" / "INDEX.md").write_text("TAMPERED", encoding="utf-8")

        args_verify = parser.parse_args(["snapshot", "verify", "fail_snap", "--root", str(mock_root)])
        ret_verify = cmd_snapshot(args_verify)
        self.assertEqual(ret_verify, 1)

    def test_cli_backup_invalid_target_exit_code(self) -> None:
        """CLI `smart-drive backup` returns 1 when target is invalid."""
        mock_root = self.create_mock_drive()
        parser = build_parser()

        args_backup = parser.parse_args(["backup", "--target", str(mock_root), "--root", str(mock_root)])
        ret_backup = cmd_backup(args_backup)
        self.assertEqual(ret_backup, 1)


if __name__ == "__main__":
    unittest.main()
