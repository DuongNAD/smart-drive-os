"""tests/test_adversarial_snapshot.py - Empirical Adversarial Stress Suite for Snapshot & Backup Engine.

100% Python Standard Library unittest. Zero external dependencies.
Adversarially challenges Features F12 through F18:
1. File Modification Detection:
   - Single-byte tampering at offset 0, middle, and EOF
   - File truncation (1 byte, 50%)
   - File appending (1 byte, 64KB block)
   - mtime modified without size/hash change (intact content)
   - size modified with preserved mtime via os.utime
   - content modified with preserved mtime and size via os.utime
2. Deletion Detection:
   - Single tracked file removal
   - Partition-wide file removal
   - Partition directory deletion
3. Untracked File Detection:
   - New files in snapshotted partitions
   - New files in non-snapshotted partitions (ignored)
   - Deeply nested new files
   - Junk file filtering (skip_junk=True vs False)
4. Empty Files and Multi-Block Streaming:
   - 0-byte file creation and hashing
   - 0-byte modified to 1-byte
   - 1-byte truncated to 0-byte
   - Multi-chunk 64KB boundary tampering (offset 0, 65535, 65536, 131071, 131072)
   - 512KB cluster slack geometry extremes
5. Incremental Backup Validation:
   - Zero recopying on unmodified state
   - Recopying on size change with preserved mtime
   - Recopying on mtime change (>2.0s) with same size
   - exFAT 2.0s tolerance skipping (mtime diff <= 2.0s)
   - Hash comparison mode detecting same-size same-mtime tampering
   - Destination corruption auto-recovery
   - Target inside source partition rejection
   - Target equals source root rejection
   - Target inside source root outside partitions allowed
6. Malformed Snapshot Name Injection & Path Traversal:
   - Unix traversal (../../bad)
   - Windows backslash traversal (..\\..\\bad)
   - Root / drive traversal (C:\\Windows\\bad, /etc/shadow)
   - Dot and double dot rejection
   - Dangerous characters stripping
   - Safe manifest loading and verification boundary confinement
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

from smart_drive.core.config import CLUSTER_SIZE_BYTES, calculate_allocated_bytes, calculate_slack_bytes
from smart_drive.core.snapshot import (
    EMPTY_FILE_SHA256,
    HASH_CHUNK_SIZE,
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
from tests.helpers import SmartDriveTestCase, oracle_full_sha256


class TestAdversarialFileTampering(SmartDriveTestCase):
    """Stress-tests file modification detection across byte tampering, truncation, append, and mtime spoofing."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.manager = SnapshotManager(self.mock_root)
        self.target_rel = "02_Learning_Knowledge/INDEX.md"
        self.target_file = self.mock_root / self.target_rel
        # Ensure a known multi-byte payload
        self.initial_content = b"MASTER_KNOWLEDGE_INDEX_BASE_DATA_FOR_TAMPER_TESTING_1234567890"
        self.target_file.write_bytes(self.initial_content)
        self.manager.create_snapshot("tamper_base")

    def test_single_byte_tamper_at_beginning(self) -> None:
        """Tampering with byte 0 modifies SHA-256 while preserving size."""
        raw = bytearray(self.target_file.read_bytes())
        raw[0] = (raw[0] + 1) % 256
        self.target_file.write_bytes(bytes(raw))

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["path"], self.target_rel)
        self.assertEqual(report.modified_files[0]["expected_size"], len(self.initial_content))
        self.assertEqual(report.modified_files[0]["actual_size"], len(self.initial_content))
        self.assertNotEqual(report.modified_files[0]["expected_hash"], report.modified_files[0]["actual_hash"])

    def test_single_byte_tamper_in_middle(self) -> None:
        """Tampering with a middle byte modifies SHA-256 while preserving size."""
        raw = bytearray(self.target_file.read_bytes())
        mid = len(raw) // 2
        raw[mid] = (raw[mid] ^ 0xFF)
        self.target_file.write_bytes(bytes(raw))

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["path"], self.target_rel)
        self.assertNotEqual(report.modified_files[0]["expected_hash"], report.modified_files[0]["actual_hash"])

    def test_single_byte_tamper_at_eof(self) -> None:
        """Tampering with the last byte modifies SHA-256 while preserving size."""
        raw = bytearray(self.target_file.read_bytes())
        raw[-1] = (raw[-1] ^ 0x01)
        self.target_file.write_bytes(bytes(raw))

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["path"], self.target_rel)
        self.assertNotEqual(report.modified_files[0]["expected_hash"], report.modified_files[0]["actual_hash"])

    def test_file_truncation_by_one_byte(self) -> None:
        """Truncating exactly 1 byte triggers size and hash mismatch."""
        raw = self.target_file.read_bytes()[:-1]
        self.target_file.write_bytes(raw)

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["actual_size"], len(self.initial_content) - 1)

    def test_file_truncation_to_half(self) -> None:
        """Truncating 50% of the file triggers size and hash mismatch."""
        raw = self.target_file.read_bytes()[: len(self.initial_content) // 2]
        self.target_file.write_bytes(raw)

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["actual_size"], len(self.initial_content) // 2)

    def test_file_append_single_byte(self) -> None:
        """Appending 1 byte triggers size and hash mismatch."""
        raw = self.target_file.read_bytes() + b"!"
        self.target_file.write_bytes(raw)

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["actual_size"], len(self.initial_content) + 1)

    def test_file_append_large_chunk(self) -> None:
        """Appending a 64KB block triggers size and hash mismatch."""
        raw = self.target_file.read_bytes() + (b"X" * HASH_CHUNK_SIZE)
        self.target_file.write_bytes(raw)

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["actual_size"], len(self.initial_content) + HASH_CHUNK_SIZE)

    def test_mtime_modified_content_identical_verifies_intact(self) -> None:
        """Modifying mtime without changing content or size leaves verification intact."""
        orig_mtime = self.target_file.stat().st_mtime
        new_mtime = orig_mtime + 5000.0
        os.utime(self.target_file, (new_mtime, new_mtime))

        report = self.manager.verify_snapshot("tamper_base")
        self.assertTrue(report.intact)
        self.assertEqual(report.corrupted_count, 0)

    def test_size_changed_with_preserved_mtime(self) -> None:
        """Changing size and forcibly resetting mtime to original is still caught."""
        orig_mtime = self.target_file.stat().st_mtime
        self.target_file.write_bytes(self.initial_content + b"_EXPANDED")
        os.utime(self.target_file, (orig_mtime, orig_mtime))

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["path"], self.target_rel)

    def test_content_tampered_with_preserved_mtime(self) -> None:
        """Tampering content (same size) and resetting mtime to original is still caught by SHA-256."""
        orig_mtime = self.target_file.stat().st_mtime
        raw = bytearray(self.target_file.read_bytes())
        raw[5] = (raw[5] ^ 0xAA)
        self.target_file.write_bytes(bytes(raw))
        os.utime(self.target_file, (orig_mtime, orig_mtime))

        report = self.manager.verify_snapshot("tamper_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["path"], self.target_rel)
        self.assertNotEqual(report.modified_files[0]["expected_hash"], report.modified_files[0]["actual_hash"])


class TestAdversarialDeletionAndUntracked(SmartDriveTestCase):
    """Stress-tests deletion detection and untracked file discovery."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.manager = SnapshotManager(self.mock_root)
        self.manifest = self.manager.create_snapshot("del_base")

    def test_delete_single_tracked_file(self) -> None:
        """Deleting a single file from a snapshotted partition is detected as missing."""
        target = self.mock_root / "05_Dev_Toolbox" / "Scripts" / "clean_mac_junk.sh"
        self.assertTrue(target.is_file())
        target.unlink()

        report = self.manager.verify_snapshot("del_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.missing_count, 1)
        self.assertIn("05_Dev_Toolbox/Scripts/clean_mac_junk.sh", report.missing_files)

    def test_delete_all_files_in_partition(self) -> None:
        """Deleting all files in an entire partition flags every deleted file."""
        part_dir = self.mock_root / "05_Dev_Toolbox"
        tracked_in_part = [p for p in self.manifest.files if p.startswith("05_Dev_Toolbox/")]
        self.assertTrue(len(tracked_in_part) > 0)

        for rel in tracked_in_part:
            abs_p = self.mock_root / rel
            if abs_p.is_file():
                abs_p.unlink()

        report = self.manager.verify_snapshot("del_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.missing_count, len(tracked_in_part))
        for rel in tracked_in_part:
            self.assertIn(rel, report.missing_files)

    def test_delete_entire_partition_directory(self) -> None:
        """Removing the partition directory completely does not crash and flags all files."""
        part_dir = self.mock_root / "05_Dev_Toolbox"
        tracked_in_part = [p for p in self.manifest.files if p.startswith("05_Dev_Toolbox/")]
        shutil.rmtree(part_dir)

        report = self.manager.verify_snapshot("del_base")
        self.assertFalse(report.intact)
        self.assertEqual(report.missing_count, len(tracked_in_part))

    def test_untracked_file_in_snapshotted_partition(self) -> None:
        """Adding a new file in a snapshotted partition is flagged in untracked_files."""
        new_file = self.mock_root / "03_Development_Projects" / "untracked_repo_file.py"
        new_file.write_text("print('rogue file')", encoding="utf-8")

        report = self.manager.verify_snapshot("del_base", check_untracked=True)
        self.assertTrue(report.intact)  # Recorded files themselves are intact
        self.assertEqual(report.untracked_count, 1)
        self.assertIn("03_Development_Projects/untracked_repo_file.py", report.untracked_files)

    def test_untracked_file_in_non_snapshotted_partition_ignored(self) -> None:
        """Adding a new file in 01_AI_Models is NOT reported as untracked in default snapshot."""
        new_file = self.mock_root / "01_AI_Models" / "GGUF" / "untracked_model.gguf"
        new_file.write_bytes(b"MODEL_BYTES")

        report = self.manager.verify_snapshot("del_base", check_untracked=True)
        self.assertEqual(report.untracked_count, 0)
        self.assertNotIn("01_AI_Models/GGUF/untracked_model.gguf", report.untracked_files)

    def test_untracked_deeply_nested_file(self) -> None:
        """Deeply nested file (5 subdirectories) in a partition is detected."""
        deep_dir = self.mock_root / "02_Learning_Knowledge" / "sub1" / "sub2" / "sub3" / "sub4"
        deep_dir.mkdir(parents=True)
        deep_file = deep_dir / "deep_discovery.txt"
        deep_file.write_text("Deep data", encoding="utf-8")

        report = self.manager.verify_snapshot("del_base", check_untracked=True)
        self.assertEqual(report.untracked_count, 1)
        self.assertIn("02_Learning_Knowledge/sub1/sub2/sub3/sub4/deep_discovery.txt", report.untracked_files)

    def test_junk_files_skipped_by_default_in_untracked_check(self) -> None:
        """Junk files like .DS_Store or *.tmp added after snapshot are ignored if skip_junk=True."""
        junk1 = self.mock_root / "02_Learning_Knowledge" / ".DS_Store"
        junk2 = self.mock_root / "03_Development_Projects" / "temp_cache.tmp"
        junk1.write_bytes(b"\x00\x00\x00\x01DSStore")
        junk2.write_text("temporary cache", encoding="utf-8")

        # By default verifier uses skip_junk=True
        verifier = SnapshotVerifier(self.mock_root)
        report = verifier.verify(self.manifest, check_untracked=True, skip_junk=True)
        self.assertEqual(report.untracked_count, 0)

    def test_junk_files_included_when_skip_junk_false(self) -> None:
        """When skip_junk=False, junk files ARE included in untracked_files."""
        junk = self.mock_root / "02_Learning_Knowledge" / "rogue_cache.tmp"
        junk.write_text("temporary cache", encoding="utf-8")

        verifier = SnapshotVerifier(self.mock_root)
        report = verifier.verify(self.manifest, check_untracked=True, skip_junk=False)
        self.assertIn("02_Learning_Knowledge/rogue_cache.tmp", report.untracked_files)


class TestAdversarialEmptyAndMultiBlockFiles(SmartDriveTestCase):
    """Stress-tests 0-byte edge cases, multi-chunk 64KB hashing, and cluster slack geometry."""

    def test_zero_byte_file_manifest_and_verification(self) -> None:
        """0-byte file records EMPTY_FILE_SHA256 and allocated_size=0, and verifies clean."""
        mock_root = self.test_dir / "zero_drive"
        part = mock_root / "02_Learning_Knowledge"
        part.mkdir(parents=True)
        zero_file = part / "empty_note.md"
        zero_file.touch()

        manager = SnapshotManager(mock_root)
        manifest = manager.create_snapshot("zero_snap")

        rel = "02_Learning_Knowledge/empty_note.md"
        self.assertIn(rel, manifest.files)
        self.assertEqual(manifest.files[rel]["size"], 0)
        self.assertEqual(manifest.files[rel]["allocated_size"], 0)
        self.assertEqual(manifest.files[rel]["sha256"], EMPTY_FILE_SHA256)
        self.assertEqual(manifest.total_allocated_bytes, 0)
        self.assertEqual(manifest.total_slack_bytes, 0)

        report = manager.verify_snapshot("zero_snap")
        self.assertTrue(report.intact)
        self.assertEqual(report.verified_count, 1)

    def test_zero_byte_modified_to_one_byte(self) -> None:
        """Modifying 0-byte file to 1 byte is detected immediately."""
        mock_root = self.test_dir / "zero_to_one"
        part = mock_root / "02_Learning_Knowledge"
        part.mkdir(parents=True)
        zero_file = part / "empty.txt"
        zero_file.touch()

        manager = SnapshotManager(mock_root)
        manager.create_snapshot("zero_snap")

        # Modify to 1 byte
        zero_file.write_bytes(b"Z")

        report = manager.verify_snapshot("zero_snap")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["expected_size"], 0)
        self.assertEqual(report.modified_files[0]["actual_size"], 1)

    def test_one_byte_truncated_to_zero_bytes(self) -> None:
        """Truncating 1-byte file to 0 bytes is detected immediately."""
        mock_root = self.test_dir / "one_to_zero"
        part = mock_root / "02_Learning_Knowledge"
        part.mkdir(parents=True)
        one_file = part / "single.txt"
        one_file.write_bytes(b"X")

        manager = SnapshotManager(mock_root)
        manager.create_snapshot("one_snap")

        # Truncate to 0
        one_file.write_bytes(b"")

        report = manager.verify_snapshot("one_snap")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        self.assertEqual(report.modified_files[0]["expected_size"], 1)
        self.assertEqual(report.modified_files[0]["actual_size"], 0)
        self.assertEqual(report.modified_files[0]["actual_hash"], EMPTY_FILE_SHA256)

    def test_multi_chunk_exact_multiples_64kb(self) -> None:
        """Multi-chunk files of exact multiples of 64KB match hashlib oracle."""
        for num_chunks in (1, 2, 4):
            size = num_chunks * HASH_CHUNK_SIZE
            data = os.urandom(size)
            fpath = self.test_dir / f"chunk_{num_chunks}.bin"
            fpath.write_bytes(data)

            expected = hashlib.sha256(data).hexdigest()
            actual = compute_file_sha256(fpath)
            self.assertEqual(actual, expected)

    def test_multi_chunk_tamper_boundary_bytes(self) -> None:
        """Tampering boundary bytes across 64KB chunk transitions is detected."""
        # 192KB file = exactly 3 64KB chunks
        total_size = 3 * HASH_CHUNK_SIZE
        raw_data = bytearray(os.urandom(total_size))
        mock_root = self.test_dir / "boundary_drive"
        part = mock_root / "02_Learning_Knowledge"
        part.mkdir(parents=True)
        target = part / "big_data.bin"
        target.write_bytes(bytes(raw_data))

        manager = SnapshotManager(mock_root)
        manager.create_snapshot("boundary_snap")

        # Critical boundary offsets:
        # 0 (start), 65535 (last byte of chunk 0), 65536 (first byte of chunk 1),
        # 131071 (last byte of chunk 1), 131072 (first byte of chunk 2), 196607 (last byte of chunk 2)
        boundary_offsets = [0, 65535, 65536, 131071, 131072, total_size - 1]

        for offset in boundary_offsets:
            corrupted = bytearray(raw_data)
            corrupted[offset] = (corrupted[offset] ^ 0xFF)
            target.write_bytes(bytes(corrupted))

            report = manager.verify_snapshot("boundary_snap")
            self.assertFalse(
                report.intact,
                f"Failed to detect tampering at 64KB chunk boundary offset {offset}",
            )
            self.assertEqual(report.corrupted_count, 1)

    def test_cluster_slack_calculation_extremes(self) -> None:
        """Verifies 512KB cluster allocation and slack space at edge geometries."""
        # 0 bytes
        self.assertEqual(calculate_allocated_bytes(0, CLUSTER_SIZE_BYTES), 0)
        self.assertEqual(calculate_slack_bytes(0, CLUSTER_SIZE_BYTES), 0)

        # 1 byte -> 1 cluster (524,288 bytes)
        self.assertEqual(calculate_allocated_bytes(1, CLUSTER_SIZE_BYTES), 524288)
        self.assertEqual(calculate_slack_bytes(1, CLUSTER_SIZE_BYTES), 524287)

        # 524,288 bytes (exact cluster boundary) -> 0 slack
        self.assertEqual(calculate_allocated_bytes(524288, CLUSTER_SIZE_BYTES), 524288)
        self.assertEqual(calculate_slack_bytes(524288, CLUSTER_SIZE_BYTES), 0)

        # 524,289 bytes (1 byte past cluster boundary) -> 2 clusters (1,048,576 bytes)
        self.assertEqual(calculate_allocated_bytes(524289, CLUSTER_SIZE_BYTES), 1048576)
        self.assertEqual(calculate_slack_bytes(524289, CLUSTER_SIZE_BYTES), 524287)


class TestAdversarialIncrementalBackup(SmartDriveTestCase):
    """Stress-tests incremental backup recopying, mtime tolerance, hash mode, and boundary guards."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.manager = SnapshotManager(self.mock_root)
        self.target_dir = self.test_dir / "backup_dest"

    def test_subsequent_backup_copies_zero_files(self) -> None:
        """Immediately repeating incremental backup transfers exactly 0 files and skips all."""
        r1 = self.manager.incremental_backup(self.target_dir)
        self.assertTrue(r1.copied_count > 0)
        self.assertEqual(r1.skipped_count, 0)

        r2 = self.manager.incremental_backup(self.target_dir)
        self.assertEqual(r2.copied_count, 0)
        self.assertEqual(r2.skipped_count, r1.copied_count)
        self.assertEqual(r2.copied_bytes, 0)

    def test_recopy_on_size_change_with_preserved_mtime(self) -> None:
        """File whose size changed but mtime was preserved is recopied."""
        self.manager.incremental_backup(self.target_dir)

        target_file = self.mock_root / "02_Learning_Knowledge" / "INDEX.md"
        orig_mtime = target_file.stat().st_mtime

        # Change size and force mtime back
        target_file.write_bytes(target_file.read_bytes() + b"ADDED_DATA_FOR_BACKUP")
        os.utime(target_file, (orig_mtime, orig_mtime))

        r = self.manager.incremental_backup(self.target_dir)
        self.assertEqual(r.copied_count, 1)
        self.assertIn("02_Learning_Knowledge/INDEX.md", r.copied_files)

    def test_recopy_on_mtime_change_greater_than_two_seconds(self) -> None:
        """File with same size but mtime changed by >2.0s is recopied."""
        self.manager.incremental_backup(self.target_dir)

        target_file = self.mock_root / "02_Learning_Knowledge" / "INDEX.md"
        cur_mtime = target_file.stat().st_mtime
        new_mtime = cur_mtime + 10.0
        os.utime(target_file, (new_mtime, new_mtime))

        r = self.manager.incremental_backup(self.target_dir)
        self.assertEqual(r.copied_count, 1)
        self.assertIn("02_Learning_Knowledge/INDEX.md", r.copied_files)

    def test_exfat_two_second_tolerance_skips_within_two_seconds(self) -> None:
        """In default mode, mtime diff <= 2.0s with identical size is skipped (exFAT tolerance)."""
        self.manager.incremental_backup(self.target_dir)

        target_file = self.mock_root / "02_Learning_Knowledge" / "INDEX.md"
        cur_mtime = target_file.stat().st_mtime
        # Alter mtime by 1.0s (less than 2.0s)
        sub_two_mtime = cur_mtime + 1.0
        os.utime(target_file, (sub_two_mtime, sub_two_mtime))

        r = self.manager.incremental_backup(self.target_dir, use_hash_comparison=False)
        self.assertEqual(r.copied_count, 0)
        self.assertIn("02_Learning_Knowledge/INDEX.md", r.skipped_files)

    def test_hash_comparison_mode_recopies_tampered_content_with_same_mtime_and_size(self) -> None:
        """When use_hash_comparison=True, content change with identical size and mtime IS recopied."""
        # Initial backup with hash comparison
        self.manager.incremental_backup(self.target_dir, use_hash_comparison=True)

        target_file = self.mock_root / "02_Learning_Knowledge" / "INDEX.md"
        orig_stat = target_file.stat()
        orig_mtime = orig_stat.st_mtime

        # Tamper 1 byte (preserving size) and reset mtime exactly
        raw = bytearray(target_file.read_bytes())
        raw[0] = (raw[0] ^ 0x01)
        target_file.write_bytes(bytes(raw))
        os.utime(target_file, (orig_mtime, orig_mtime))

        # In default mode, mtime & size match so it would skip
        r_default = self.manager.incremental_backup(self.target_dir, use_hash_comparison=False)
        self.assertEqual(r_default.copied_count, 0)

        # In hash comparison mode, hash mismatch triggers recopy
        r_hash = self.manager.incremental_backup(self.target_dir, use_hash_comparison=True)
        self.assertEqual(r_hash.copied_count, 1)
        self.assertIn("02_Learning_Knowledge/INDEX.md", r_hash.copied_files)

    def test_incremental_backup_recovers_deleted_dest_file(self) -> None:
        """If a file is removed from backup target, subsequent incremental run re-transfers it."""
        r1 = self.manager.incremental_backup(self.target_dir)
        total_copied = r1.copied_count

        # Delete one file in target destination
        dest_deleted = self.target_dir / "02_Learning_Knowledge" / "INDEX.md"
        self.assertTrue(dest_deleted.is_file())
        dest_deleted.unlink()

        r2 = self.manager.incremental_backup(self.target_dir)
        self.assertEqual(r2.copied_count, 1)
        self.assertIn("02_Learning_Knowledge/INDEX.md", r2.copied_files)
        self.assertEqual(r2.skipped_count, total_copied - 1)
        self.assertTrue(dest_deleted.is_file())

    def test_target_boundary_guards(self) -> None:
        """Verify boundary violations raise BackupTargetInvalidError."""
        # 1. Target equals source root
        with self.assertRaises(BackupTargetInvalidError):
            self.manager.incremental_backup(self.mock_root)

        # 2. Target inside a backed-up partition
        nested_in_part = self.mock_root / "02_Learning_Knowledge" / "nested_backup"
        with self.assertRaises(BackupTargetInvalidError):
            self.manager.incremental_backup(nested_in_part)

        # 3. Target is the partition directory itself
        part_dir = self.mock_root / "02_Learning_Knowledge"
        with self.assertRaises(BackupTargetInvalidError):
            self.manager.incremental_backup(part_dir)

        # 4. Target deeply nested inside backed-up partition
        deep_in_part = self.mock_root / "03_Development_Projects" / "a" / "b" / "c"
        with self.assertRaises(BackupTargetInvalidError):
            self.manager.incremental_backup(deep_in_part)

    def test_target_in_root_outside_partitions_allowed(self) -> None:
        """Target placed in source root under a non-backed-up folder is allowed and safe."""
        safe_target = self.mock_root / "Safe_Backup_Storage"
        report = self.manager.incremental_backup(safe_target)
        self.assertTrue(report.copied_count > 0)
        self.assertTrue(safe_target.is_dir())
        self.assertTrue((safe_target / "backup_manifest.json").is_file())


class TestAdversarialSnapshotNameInjection(SmartDriveTestCase):
    """Stress-tests snapshot name sanitization, directory traversal attacks, and malformed inputs."""

    def test_sanitize_unix_path_traversal(self) -> None:
        """Unix relative traversal '../../evil_snap' resolves safely to 'evil_snap'."""
        clean = sanitize_snapshot_name("../../evil_snap")
        self.assertEqual(clean, "evil_snap")

    def test_sanitize_windows_backslash_traversal(self) -> None:
        """Windows traversal '..\\..\\evil_snap' resolves safely to 'evil_snap'."""
        clean = sanitize_snapshot_name("..\\..\\evil_snap")
        self.assertEqual(clean, "evil_snap")

    def test_sanitize_absolute_unix_path(self) -> None:
        """Absolute Unix path '/etc/passwd' resolves safely to 'passwd'."""
        clean = sanitize_snapshot_name("/etc/passwd")
        self.assertEqual(clean, "passwd")

    def test_sanitize_absolute_windows_path(self) -> None:
        """Absolute Windows path 'C:\\Windows\\System32\\cmd' resolves safely to 'cmd'."""
        clean = sanitize_snapshot_name("C:\\Windows\\System32\\cmd")
        self.assertEqual(clean, "cmd")

    def test_sanitize_dot_and_double_dot_rejected(self) -> None:
        """Pure '.' and '..' and variations raise ValueError."""
        for bad_name in (".", "..", "....//", "../", "..\\", "   ..   ", ""):
            with self.assertRaises(ValueError, msg=f"Should reject: '{bad_name}'"):
                sanitize_snapshot_name(bad_name)

    def test_sanitize_special_characters_stripped(self) -> None:
        """Special and control characters are stripped from the snapshot name."""
        clean = sanitize_snapshot_name("snap*name$with;cmd&inject|test`")
        self.assertEqual(clean, "snapnamewithcmdinjecttest")

    def test_create_snapshot_with_traversal_name_confined_to_snapshot_dir(self) -> None:
        """Creating snapshot with '../../escape' creates file strictly inside snapshot_dir."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        manifest = manager.create_snapshot("../../escape")
        self.assertEqual(manifest.name, "escape")

        expected_manifest_path = manager.snapshot_dir / "escape.json"
        self.assertTrue(expected_manifest_path.is_file())

        # Ensure no file was created in parent directory of snapshot_dir or root
        self.assertFalse((mock_root.parent / "escape.json").exists())
        self.assertFalse((mock_root / "escape.json").exists())

    def test_load_manifest_traversal_does_not_escape_dir(self) -> None:
        """Loading manifest with '../../system_secret' cannot read arbitrary files."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        # Place a file outside snapshot_dir
        secret_file = mock_root / "secret.json"
        secret_file.write_text('{"name": "secret"}', encoding="utf-8")

        # Attempt to load using traversal
        with self.assertRaises(SnapshotNotFoundError):
            manager.load_manifest("../secret")

    def test_verify_snapshot_traversal_does_not_escape_dir(self) -> None:
        """Verifying a snapshot with traversal name raises SnapshotNotFoundError."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        with self.assertRaises(SnapshotNotFoundError):
            manager.verify_snapshot("../../arbitrary_name")


if __name__ == "__main__":
    unittest.main()
