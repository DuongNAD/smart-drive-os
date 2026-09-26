import os
import shutil
import sys
import tempfile
import time
import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.snapshot import (
    SnapshotManager,
    SnapshotManifest,
    SnapshotVerifier,
    BackupEngine,
    BackupTargetInvalidError,
    compute_file_sha256,
    EMPTY_FILE_SHA256,
)

def run_tests():
    print("=== STARTING ADVERSARIAL STRESS TESTS FOR MILESTONE 2 ===")
    
    with tempfile.TemporaryDirectory(prefix="forensic_m2_") as tmpdir:
        root = Path(tmpdir) / "drive_root"
        root.mkdir()
        
        # Setup standard partitions
        p1 = root / "02_Learning_Knowledge"
        p2 = root / "03_Development_Projects"
        p3 = root / "05_Dev_Toolbox"
        for p in [p1, p2, p3]:
            p.mkdir()
            
        # Create test files
        f1 = p1 / "doc1.txt"
        f1.write_text("Hello World", encoding="utf-8")
        
        f2 = p2 / "code.py"
        f2.write_text("print('test')", encoding="utf-8")
        
        f_empty = p3 / "zero.bin"
        f_empty.touch()
        
        f_large = p1 / "large.bin"
        # 200 KB
        large_bytes = os.urandom(200 * 1024)
        f_large.write_bytes(large_bytes)
        
        f_junk1 = p1 / ".DS_Store"
        f_junk1.write_bytes(b"\x00\x00\x00\x01")
        
        f_junk2 = p2 / "temp_cache.tmp"
        f_junk2.write_text("junk", encoding="utf-8")
        
        # Test 1: Verify 0-byte file hash calculation
        print("\n[Test 1] 0-byte file hash:")
        h_empty = compute_file_sha256(f_empty)
        assert h_empty == EMPTY_FILE_SHA256, f"Expected {EMPTY_FILE_SHA256}, got {h_empty}"
        print(f"  PASS: {h_empty}")
        
        # Test 2: Large streaming file SHA-256 matches independent computation
        print("\n[Test 2] Large streaming file SHA-256:")
        expected_large_hash = hashlib.sha256(large_bytes).hexdigest()
        actual_large_hash = compute_file_sha256(f_large)
        assert actual_large_hash == expected_large_hash, f"Hash mismatch on large file! Expected {expected_large_hash}, got {actual_large_hash}"
        print(f"  PASS: Streaming SHA-256 matches independent hashlib exactly ({actual_large_hash[:16]}...)")
        
        # Test 3: Snapshot creation and junk filtering
        print("\n[Test 3] Snapshot creation & junk exclusion:")
        mgr = SnapshotManager(root)
        manifest = mgr.create_snapshot("snap1")
        assert manifest.name == "snap1"
        assert manifest.file_count == 4, f"Expected 4 non-junk files, got {manifest.file_count}"
        for f in manifest.files.keys():
            assert ".DS_Store" not in f
            assert ".tmp" not in f
        print(f"  PASS: 4 files recorded, junk filtered out, manifest saved at {mgr.snapshot_dir / 'snap1.json'}")
        
        # Test 4: Verify intact snapshot
        print("\n[Test 4] Verify intact snapshot:")
        rep = mgr.verify_snapshot("snap1")
        assert rep.intact is True
        assert rep.verified_count == 4
        assert rep.corrupted_count == 0
        assert rep.missing_count == 0
        assert rep.untracked_count == 0
        print("  PASS: Intact snapshot validated successfully.")
        
        # Test 5: Adversarial bit flip (same file size, 1 byte changed)
        print("\n[Test 5] Adversarial bit flip (same size, altered content):")
        # f1 was "Hello World" (11 bytes). Change to "Jello World" (11 bytes).
        f1.write_text("Jello World", encoding="utf-8")
        rep = mgr.verify_snapshot("snap1")
        assert rep.intact is False
        assert rep.corrupted_count == 1
        assert rep.modified_files[0]["path"] == "02_Learning_Knowledge/doc1.txt"
        print("  PASS: Detected byte-level tampering with identical file size!")
        
        # Restore f1
        f1.write_text("Hello World", encoding="utf-8")
        
        # Test 6: Adversarial deletion
        print("\n[Test 6] Adversarial file deletion:")
        f2.unlink()
        rep = mgr.verify_snapshot("snap1")
        assert rep.intact is False
        assert rep.missing_count == 1
        assert rep.missing_files[0] == "03_Development_Projects/code.py"
        print("  PASS: Detected deleted file!")
        
        # Recreate f2
        f2.write_text("print('test')", encoding="utf-8")
        
        # Test 7: Untracked file addition
        print("\n[Test 7] Untracked file detection:")
        f_new = p1 / "untracked_secret.txt"
        f_new.write_text("secret", encoding="utf-8")
        rep = mgr.verify_snapshot("snap1")
        assert rep.intact is True  # tracked files are intact
        assert rep.untracked_count == 1
        assert rep.untracked_files[0] == "02_Learning_Knowledge/untracked_secret.txt"
        print("  PASS: Detected untracked file without corrupting intact state!")
        f_new.unlink()
        
        # Test 8: Backup engine boundary safety
        print("\n[Test 8] Backup boundary enforcement:")
        dest_inside = p1 / "nested_target"
        try:
            mgr.incremental_backup(dest_inside)
            assert False, "Should have raised BackupTargetInvalidError for nested target"
        except BackupTargetInvalidError as e:
            print(f"  PASS: Prevented nested backup into partition ({e})")
            
        try:
            mgr.incremental_backup(root)
            assert False, "Should have raised BackupTargetInvalidError for root"
        except BackupTargetInvalidError as e:
            print(f"  PASS: Prevented backup into root ({e})")
            
        # Test 9: Real incremental backup execution & verification
        print("\n[Test 9] Incremental backup execution & file verification:")
        dest_valid = Path(tmpdir) / "backup_storage"
        b_rep1 = mgr.incremental_backup(dest_valid, dry_run=False)
        assert b_rep1.copied_count == 4
        assert b_rep1.skipped_count == 0
        assert b_rep1.failed_count == 0
        
        # Verify copied files on disk
        for rel_path in b_rep1.copied_files:
            src_f = root / rel_path
            dst_f = dest_valid / rel_path
            assert dst_f.is_file(), f"Target file missing: {dst_f}"
            assert compute_file_sha256(src_f) == compute_file_sha256(dst_f), f"Hash mismatch for {rel_path}"
        assert (dest_valid / "backup_manifest.json").is_file(), "backup_manifest.json missing at destination"
        print(f"  PASS: All 4 files copied and verified with identical SHA-256 hashes.")
        
        # Test 10: Second backup without changes skips all
        print("\n[Test 10] Second backup with no changes:")
        b_rep2 = mgr.incremental_backup(dest_valid, dry_run=False)
        assert b_rep2.copied_count == 0
        assert b_rep2.skipped_count == 4
        print("  PASS: Exactly 0 files copied, 4 files skipped.")
        
        # Test 11: Modify 1 file, verify only 1 copied
        print("\n[Test 11] Modify single file:")
        time.sleep(0.05)
        f1.write_text("Hello World Updated Version", encoding="utf-8")
        b_rep3 = mgr.incremental_backup(dest_valid, dry_run=False)
        assert b_rep3.copied_count == 1
        assert b_rep3.copied_files == ["02_Learning_Knowledge/doc1.txt"]
        assert b_rep3.skipped_count == 3
        print("  PASS: Incremental copy transferred only the 1 modified file.")
        
        # Test 12: Backup with use_hash_comparison flag
        print("\n[Test 12] Backup with use_hash_comparison:")
        # Force mtime and size to match, but change content!
        dest_f1 = dest_valid / "02_Learning_Knowledge/doc1.txt"
        dest_stat = dest_f1.stat()
        # write altered content of EXACT same size to destination
        same_size_tampered = "Hello World Updated VersioX".encode("utf-8")
        assert len(same_size_tampered) == dest_stat.st_size
        dest_f1.write_bytes(same_size_tampered)
        os.utime(dest_f1, (dest_stat.st_atime, dest_stat.st_mtime))
        
        # Normal mtime backup would think they are identical (mtime & size match)
        b_rep4 = mgr.incremental_backup(dest_valid, dry_run=False, use_hash_comparison=False)
        assert b_rep4.copied_count == 0  # mtime matched, skipped!
        
        # With use_hash_comparison=True, it MUST detect the hash difference and re-copy
        b_rep5 = mgr.incremental_backup(dest_valid, dry_run=False, use_hash_comparison=True)
        assert b_rep5.copied_count == 1
        assert b_rep5.copied_files == ["02_Learning_Knowledge/doc1.txt"]
        print("  PASS: use_hash_comparison detected content change despite identical size and mtime.")

    print("\n=== ALL ADVERSARIAL STRESS TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_tests()
