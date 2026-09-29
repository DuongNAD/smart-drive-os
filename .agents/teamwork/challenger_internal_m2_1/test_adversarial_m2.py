"""Empirical Adversarial Test Suite for Milestone M2:
Cache Offloader & NTFS Directory Junction Engine.

Adversarial Stress Scenarios:
1. Target Drive Validation & C: Rejection across all string formats & variations.
2. Already-offloaded cache behaviors (idempotency, force error handling, zero corruption).
3. Reverting non-junction directories, regular files, non-existent paths (safety violations).
4. Corrupt and broken junctions (missing/moved target, dangling links, safe cleanup).
5. Transactional rollback under mid-operation failures (Phase 2 copy failure, Phase 3 rename failure,
   Phase 4 quarantine lock, Phase 5 junction failure, Phase 6 target mismatch, insufficient space).
   Verification via cryptographic SHA-256 checksums ensuring pristine file preservation.
6. CLI handler adversarial error paths and JSON status validation.

100% Zero-Dependency Python Standard Library unittest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from smart_drive.cli.cmd_offload import cmd_offload
from smart_drive.core.junction import (
    create_directory_junction,
    get_junction_target,
    is_directory_junction,
    remove_directory_junction,
)
from smart_drive.core.offloader import (
    CACHE_CATALOG,
    CacheTarget,
    calculate_dir_size,
    format_bytes,
    get_scan_summary,
    offload_cache,
    resolve_cache_path,
    revert_cache,
    scan_caches,
    validate_target_drive,
)


def _compute_dir_hashes(directory: Path) -> dict[str, str]:
    """Compute SHA-256 hashes of all files in directory relative to directory root."""
    hashes = {}
    if not directory.exists():
        return hashes
    for root, _, files in os.walk(str(directory)):
        for f in files:
            full_p = Path(root) / f
            rel_p = str(full_p.relative_to(directory)).replace("\\", "/")
            try:
                h = hashlib.sha256(full_p.read_bytes()).hexdigest()
                hashes[rel_p] = h
            except (OSError, PermissionError):
                pass
    return hashes


class TestAdversarialCDriveRejection(unittest.TestCase):
    """Stress tests verifying rejection of system drive C: under all formats."""

    def test_c_drive_spec_rejections_in_all_formats(self) -> None:
        """Every permutation of C: drive designation must be unconditionally rejected."""
        c_variations = [
            "C:",
            "c:",
            "C:\\",
            "c:\\",
            "C:/",
            "c:/",
            "C",
            "c",
            "  C:  ",
            "   c:\\   ",
            " \t C:/ \n ",
            "C:\\Windows",
            "c:\\Program Files",
            "C:/some/custom/path",
        ]
        for spec in c_variations:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError, msg=f"Spec '{spec}' should have been rejected as C: drive!") as ctx:
                    validate_target_drive(spec)
                err_msg = str(ctx.exception)
                self.assertTrue(
                    "system drive (C:)" in err_msg or "Windows OS system drive" in err_msg or "Cannot offload" in err_msg,
                    f"Unexpected error message for '{spec}': {err_msg}",
                )

    def test_empty_or_whitespace_target_rejected(self) -> None:
        """Empty or whitespace-only target strings must raise ValueError."""
        for empty_spec in ["", "   ", "\t", "\n"]:
            with self.subTest(spec=empty_spec):
                with self.assertRaises(ValueError) as ctx:
                    validate_target_drive(empty_spec)
                self.assertIn("cannot be empty", str(ctx.exception).lower())

    def test_offload_cache_rejects_c_drive_before_filesystem_mutation(self) -> None:
        """offload_cache must reject C: immediately without creating any files or directories."""
        with tempfile.TemporaryDirectory(prefix="sd_adv_c_test_") as tmp_dir:
            mock_c = Path(tmp_dir) / "DriveC"
            mock_user = mock_c / "Users" / "Tester"
            hf_cache = mock_user / ".cache" / "huggingface"
            hf_cache.mkdir(parents=True, exist_ok=True)
            test_file = hf_cache / "weights.bin"
            test_file.write_bytes(b"ORIGINAL_CACHE_DATA_DO_NOT_DELETE")

            env = {
                "USERPROFILE": str(mock_user),
                "HOME": str(mock_user),
                "LOCALAPPDATA": str(mock_user / "AppData" / "Local"),
                "APPDATA": str(mock_user / "AppData" / "Roaming"),
                "SMART_DRIVE_MOCK_USERPROFILE": str(mock_user),
            }

            with patch.dict(os.environ, env):
                for bad_target in ["C:", "c:\\", "C:/", "c"]:
                    with self.subTest(target=bad_target):
                        with self.assertRaises(ValueError):
                            offload_cache("huggingface", bad_target)

                        # Confirm source was completely untouched
                        self.assertTrue(hf_cache.exists())
                        self.assertFalse(is_directory_junction(hf_cache))
                        self.assertEqual(test_file.read_bytes(), b"ORIGINAL_CACHE_DATA_DO_NOT_DELETE")


class TestAdversarialAlreadyOffloaded(unittest.TestCase):
    """Stress tests verifying behavior when offloading an already-offloaded cache."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_adv_already_")
        self.workspace = Path(self.temp_dir).resolve()
        self.mock_c = self.workspace / "DriveC"
        self.mock_d = self.workspace / "DriveD"
        self.mock_user = self.mock_c / "Users" / "DevUser"

        self.env = {
            "USERPROFILE": str(self.mock_user),
            "HOME": str(self.mock_user),
            "LOCALAPPDATA": str(self.mock_user / "AppData" / "Local"),
            "APPDATA": str(self.mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.mock_user),
            "SMART_DRIVE_ALLOW_MOCK_TARGET": "1",
        }

    def tearDown(self) -> None:
        # Safely unlink junctions
        if self.workspace.exists():
            for root, dirs, _ in os.walk(str(self.workspace)):
                for d in list(dirs):
                    full_p = Path(root) / d
                    if is_directory_junction(full_p):
                        try:
                            remove_directory_junction(full_p)
                        except Exception:
                            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_offload_already_offloaded_cache_without_force(self) -> None:
        """Re-offloading an active junction without force must return already_offloaded status safely."""
        with patch.dict(os.environ, self.env):
            uv_cache = self.mock_user / "AppData" / "Local" / "uv" / "cache"
            uv_cache.mkdir(parents=True, exist_ok=True)
            (uv_cache / "uv_item.txt").write_text("uv_cache_data", encoding="utf-8")

            # 1. First offload
            res1 = offload_cache("uv", self.mock_d)
            self.assertEqual(res1["status"], "success")
            self.assertTrue(is_directory_junction(uv_cache))

            hashes_before = _compute_dir_hashes(self.mock_d / "04_System_Offload_Caches" / "uv")

            # 2. Second offload attempt without force
            res2 = offload_cache("uv", self.mock_d, force=False)
            self.assertEqual(res2["status"], "already_offloaded")
            self.assertIn("already offloaded", res2["message"])

            # Verify junction is still intact and points to target
            self.assertTrue(is_directory_junction(uv_cache))
            resolved = get_junction_target(uv_cache)
            expected_target = self.mock_d / "04_System_Offload_Caches" / "uv"
            self.assertEqual(
                os.path.normcase(os.path.abspath(resolved)),
                os.path.normcase(os.path.abspath(str(expected_target))),
            )

            # Verify target files are 100% intact
            hashes_after = _compute_dir_hashes(expected_target)
            self.assertEqual(hashes_before, hashes_after)

    def test_offload_already_offloaded_cache_with_force_raises_safe_error(self) -> None:
        """Re-offloading an active junction WITH force=True must raise RuntimeError directing to revert."""
        with patch.dict(os.environ, self.env):
            pip_cache = self.mock_user / "AppData" / "Local" / "pip" / "cache"
            pip_cache.mkdir(parents=True, exist_ok=True)
            (pip_cache / "wheel.whl").write_bytes(b"PIP_WHEEL_BYTES" * 20)

            # First offload
            offload_cache("pip", self.mock_d)
            self.assertTrue(is_directory_junction(pip_cache))

            expected_target = self.mock_d / "04_System_Offload_Caches" / "pip"
            hashes_before = _compute_dir_hashes(expected_target)

            # Re-offload with force=True
            with self.assertRaises(RuntimeError) as ctx:
                offload_cache("pip", self.mock_d, force=True)

            self.assertIn("already an active ntfs directory junction", str(ctx.exception).lower())
            self.assertIn("revert", str(ctx.exception).lower())

            # Verify junction and target files were NOT destroyed
            self.assertTrue(is_directory_junction(pip_cache))
            hashes_after = _compute_dir_hashes(expected_target)
            self.assertEqual(hashes_before, hashes_after)


class TestAdversarialRevertNonJunction(unittest.TestCase):
    """Stress tests attempting to revert directories or files that are not junctions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_adv_revert_")
        self.workspace = Path(self.temp_dir).resolve()
        self.mock_c = self.workspace / "DriveC"
        self.mock_user = self.mock_c / "Users" / "DevUser"

        self.env = {
            "USERPROFILE": str(self.mock_user),
            "HOME": str(self.mock_user),
            "LOCALAPPDATA": str(self.mock_user / "AppData" / "Local"),
            "APPDATA": str(self.mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.mock_user),
            "SMART_DRIVE_ALLOW_MOCK_TARGET": "1",
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_revert_cache_on_normal_directory_rejected(self) -> None:
        """revert_cache on an existing normal directory must raise ValueError and leave files intact."""
        with patch.dict(os.environ, self.env):
            hf_cache = self.mock_user / ".cache" / "huggingface"
            hf_cache.mkdir(parents=True, exist_ok=True)
            (hf_cache / "model.bin").write_bytes(b"REGULAR_DIRECTORY_MODEL_WEIGHTS" * 10)

            hashes_before = _compute_dir_hashes(hf_cache)

            with self.assertRaises(ValueError) as ctx:
                revert_cache("huggingface")

            self.assertIn("not currently an active directory junction", str(ctx.exception))

            # Critical data protection invariant
            self.assertTrue(hf_cache.exists())
            self.assertTrue(hf_cache.is_dir())
            self.assertFalse(is_directory_junction(hf_cache))
            hashes_after = _compute_dir_hashes(hf_cache)
            self.assertEqual(hashes_before, hashes_after)

    def test_revert_cache_on_nonexistent_cache_rejected(self) -> None:
        """revert_cache on a non-existent cache directory must raise ValueError."""
        with patch.dict(os.environ, self.env):
            # Ollama cache does not exist
            with self.assertRaises(ValueError) as ctx:
                revert_cache("ollama")
            self.assertIn("not currently an active directory junction", str(ctx.exception))

    def test_remove_directory_junction_safety_guard_on_files_and_dirs(self) -> None:
        """remove_directory_junction must reject regular folders and files with ValueError."""
        test_dir = self.workspace / "normal_folder"
        test_dir.mkdir()
        (test_dir / "keep_me.txt").write_text("precious_content")

        test_file = self.workspace / "normal_file.txt"
        test_file.write_text("file_content")

        # 1. Reject regular directory
        with self.assertRaises(ValueError) as ctx1:
            remove_directory_junction(test_dir)
        self.assertIn("Safety Violation", str(ctx1.exception))
        self.assertTrue(test_dir.exists())
        self.assertTrue((test_dir / "keep_me.txt").exists())

        # 2. Reject regular file
        with self.assertRaises(ValueError) as ctx2:
            remove_directory_junction(test_file)
        self.assertIn("Safety Violation", str(ctx2.exception))
        self.assertTrue(test_file.exists())

        # 3. Non-existent path returns False without error
        self.assertFalse(remove_directory_junction(self.workspace / "does_not_exist"))


class TestAdversarialBrokenAndCorruptJunctions(unittest.TestCase):
    """Stress tests on corrupt, broken, or dangling directory junctions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_adv_broken_")
        self.workspace = Path(self.temp_dir).resolve()
        self.mock_c = self.workspace / "DriveC"
        self.mock_d = self.workspace / "DriveD"
        self.mock_user = self.mock_c / "Users" / "DevUser"

        self.env = {
            "USERPROFILE": str(self.mock_user),
            "HOME": str(self.mock_user),
            "LOCALAPPDATA": str(self.mock_user / "AppData" / "Local"),
            "APPDATA": str(self.mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.mock_user),
            "SMART_DRIVE_ALLOW_MOCK_TARGET": "1",
        }

    def tearDown(self) -> None:
        if self.workspace.exists():
            for root, dirs, _ in os.walk(str(self.workspace)):
                for d in list(dirs):
                    full_p = Path(root) / d
                    if is_directory_junction(full_p):
                        try:
                            remove_directory_junction(full_p)
                        except Exception:
                            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_broken_junction_target_deleted(self) -> None:
        """When the target directory is deleted, the junction is still detected, target is read, and unlinking works."""
        target_dir = self.mock_d / "target_to_be_deleted"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "temp.bin").write_bytes(b"DATA")

        junction_link = self.mock_c / "broken_link_test"
        ok = create_directory_junction(junction_link, target_dir)
        self.assertTrue(ok)
        self.assertTrue(is_directory_junction(junction_link))

        target_before = get_junction_target(junction_link)
        self.assertIsNotNone(target_before)

        # Delete physical target to create a broken/dangling junction
        shutil.rmtree(str(target_dir))
        self.assertFalse(target_dir.exists())

        # Broken junction must STILL be recognized as a directory junction
        self.assertTrue(is_directory_junction(junction_link))

        # Target path should still be readable via readlink
        target_after = get_junction_target(junction_link)
        self.assertEqual(
            os.path.normcase(os.path.abspath(target_before)),
            os.path.normcase(os.path.abspath(target_after)),
        )

        # Safe removal of broken junction must succeed cleanly
        removed = remove_directory_junction(junction_link)
        self.assertTrue(removed)
        self.assertFalse(is_directory_junction(junction_link))
        self.assertFalse(os.path.lexists(str(junction_link)))

    def test_revert_cache_on_broken_junction_raises_filenotfound(self) -> None:
        """revert_cache on a broken junction must raise FileNotFoundError and NOT delete the junction."""
        with patch.dict(os.environ, self.env):
            uv_cache = self.mock_user / "AppData" / "Local" / "uv" / "cache"
            uv_cache.mkdir(parents=True, exist_ok=True)
            (uv_cache / "uv.bin").write_bytes(b"UV_DATA")

            # Offload uv
            offload_cache("uv", self.mock_d)
            self.assertTrue(is_directory_junction(uv_cache))

            # Delete the offloaded target on mock Drive D (simulate user deleting or unmounting target)
            offloaded_target = self.mock_d / "04_System_Offload_Caches" / "uv"
            self.assertTrue(offloaded_target.exists())
            shutil.rmtree(str(offloaded_target))
            self.assertFalse(offloaded_target.exists())

            # Attempt revert_cache
            with self.assertRaises(FileNotFoundError) as ctx:
                revert_cache("uv")

            self.assertIn("does not exist on disk", str(ctx.exception))

            # Invariant: source junction must not have been removed / damaged
            self.assertTrue(is_directory_junction(uv_cache))


class TestAdversarialTransactionalRollback(unittest.TestCase):
    """Stress tests verifying transactional rollback and zero data loss on failures at every phase."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_adv_rollback_")
        self.workspace = Path(self.temp_dir).resolve()
        self.mock_c = self.workspace / "DriveC"
        self.mock_d = self.workspace / "DriveD"
        self.mock_user = self.mock_c / "Users" / "DevUser"

        self.env = {
            "USERPROFILE": str(self.mock_user),
            "HOME": str(self.mock_user),
            "LOCALAPPDATA": str(self.mock_user / "AppData" / "Local"),
            "APPDATA": str(self.mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.mock_user),
            "SMART_DRIVE_ALLOW_MOCK_TARGET": "1",
        }

    def tearDown(self) -> None:
        if self.workspace.exists():
            for root, dirs, _ in os.walk(str(self.workspace)):
                for d in list(dirs):
                    full_p = Path(root) / d
                    if is_directory_junction(full_p):
                        try:
                            remove_directory_junction(full_p)
                        except Exception:
                            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_complex_cache(self, cache_dir: Path) -> dict[str, str]:
        """Creates a multi-file nested cache and returns SHA-256 hashes of all files."""
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "config.json").write_text('{"version": 2, "active": true}', encoding="utf-8")
        (cache_dir / "weights.safetensors").write_bytes(os.urandom(8192))
        (cache_dir / "empty_marker").write_bytes(b"")

        sub1 = cache_dir / "submodels" / "bert"
        sub1.mkdir(parents=True, exist_ok=True)
        (sub1 / "pytorch_model.bin").write_bytes(b"BERT_MODEL_DATA" * 100)
        (sub1 / "vocab.txt").write_text("token1\ntoken2\ntoken3\n", encoding="utf-8")

        sub2 = cache_dir / "nested" / "deep" / "dir"
        sub2.mkdir(parents=True, exist_ok=True)
        (sub2 / "deep_file.dat").write_bytes(b"DEEP_NESTED_DATA" * 50)

        return _compute_dir_hashes(cache_dir)

    def test_rollback_on_phase2_copy_failure(self) -> None:
        """Failure during Phase 2 (shutil.copytree) must leave source completely pristine."""
        with patch.dict(os.environ, self.env):
            hf_dir = self.mock_user / ".cache" / "huggingface"
            original_hashes = self._setup_complex_cache(hf_dir)
            self.assertEqual(len(original_hashes), 6)

            # Mock shutil.copytree to simulate an I/O or read-only target failure
            with patch("shutil.copytree", side_effect=PermissionError("Mock Permission Denied on Target")):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("huggingface", self.mock_d)
                self.assertIn("rolled back", str(ctx.exception).lower())

            # SOURCE VERIFICATION:
            self.assertTrue(hf_dir.exists())
            self.assertTrue(hf_dir.is_dir())
            self.assertFalse(is_directory_junction(hf_dir))
            after_hashes = _compute_dir_hashes(hf_dir)
            self.assertEqual(original_hashes, after_hashes, "Files on C: were modified or lost during Phase 2 rollback!")

            # TARGET VERIFICATION:
            target_dir = self.mock_d / "04_System_Offload_Caches" / "huggingface"
            self.assertFalse(target_dir.exists(), "Target directory should have been cleaned up!")

    def test_rollback_on_phase3_target_rename_failure(self) -> None:
        """Failure during Phase 3 (renaming staging to target) triggers full rollback."""
        with patch.dict(os.environ, self.env):
            hf_dir = self.mock_user / ".cache" / "huggingface"
            original_hashes = self._setup_complex_cache(hf_dir)

            # Intercept os.rename specifically when renaming .tmp_offload to target
            orig_rename = os.rename

            def faulty_rename(src: str, dst: str) -> None:
                if ".tmp_offload_" in src:
                    raise OSError("Mock disk I/O error during target activation")
                return orig_rename(src, dst)

            with patch("os.rename", side_effect=faulty_rename):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("huggingface", self.mock_d)
                self.assertIn("rolled back", str(ctx.exception).lower())

            # Verify source is 100% pristine
            self.assertTrue(hf_dir.exists())
            self.assertFalse(is_directory_junction(hf_dir))
            after_hashes = _compute_dir_hashes(hf_dir)
            self.assertEqual(original_hashes, after_hashes)

    def test_rollback_on_phase4_source_quarantine_lock(self) -> None:
        """Failure during Phase 4 (source locked by active process) triggers rollback and cleans staging."""
        with patch.dict(os.environ, self.env):
            hf_dir = self.mock_user / ".cache" / "huggingface"
            original_hashes = self._setup_complex_cache(hf_dir)

            orig_rename = os.rename

            def faulty_rename(src: str, dst: str) -> None:
                if ".offload_bak_" in dst:
                    raise PermissionError("[WinError 32] The process cannot access the file because it is being used by another process")
                return orig_rename(src, dst)

            with patch("os.rename", side_effect=faulty_rename):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("huggingface", self.mock_d)
                self.assertIn("rolled back", str(ctx.exception).lower())

            # Source intact
            self.assertTrue(hf_dir.exists())
            self.assertFalse(is_directory_junction(hf_dir))
            after_hashes = _compute_dir_hashes(hf_dir)
            self.assertEqual(original_hashes, after_hashes)

            # Target cleaned up
            target_dir = self.mock_d / "04_System_Offload_Caches" / "huggingface"
            self.assertFalse(target_dir.exists())

    def test_rollback_on_phase5_junction_creation_failure(self) -> None:
        """When create_directory_junction fails in Phase 5, source backup is restored to original path."""
        with patch.dict(os.environ, self.env):
            hf_dir = self.mock_user / ".cache" / "huggingface"
            original_hashes = self._setup_complex_cache(hf_dir)

            # Mock create_directory_junction returning False
            with patch("smart_drive.core.offloader.create_directory_junction", return_value=False):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("huggingface", self.mock_d)
                self.assertIn("rolled back", str(ctx.exception).lower())

            # SOURCE VERIFICATION:
            self.assertTrue(hf_dir.exists(), "Source directory must be restored from .offload_bak!")
            self.assertTrue(hf_dir.is_dir())
            self.assertFalse(is_directory_junction(hf_dir), "Source should be restored as a normal directory!")
            after_hashes = _compute_dir_hashes(hf_dir)
            self.assertEqual(original_hashes, after_hashes, "Source file hashes do not match original!")

            # TARGET VERIFICATION:
            target_dir = self.mock_d / "04_System_Offload_Caches" / "huggingface"
            self.assertFalse(target_dir.exists(), "Target directory must be purged on rollback!")

    def test_rollback_on_phase6_target_mismatch(self) -> None:
        """If junction verification detects mismatched target in Phase 6, rollback cleanly restores source."""
        with patch.dict(os.environ, self.env):
            hf_dir = self.mock_user / ".cache" / "huggingface"
            original_hashes = self._setup_complex_cache(hf_dir)

            # Mock get_junction_target returning a completely wrong path
            with patch("smart_drive.core.offloader.get_junction_target", return_value="Z:\\Wrong\\Path"):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("huggingface", self.mock_d)
                self.assertIn("rolled back", str(ctx.exception).lower())

            # Source must be restored
            self.assertTrue(hf_dir.exists())
            self.assertFalse(is_directory_junction(hf_dir))
            after_hashes = _compute_dir_hashes(hf_dir)
            self.assertEqual(original_hashes, after_hashes)

    def test_insufficient_disk_space_aborts_without_modifications(self) -> None:
        """When target drive has insufficient space, offload_cache raises OSError and does nothing."""
        with patch.dict(os.environ, self.env):
            hf_dir = self.mock_user / ".cache" / "huggingface"
            original_hashes = self._setup_complex_cache(hf_dir)

            from collections import namedtuple
            Usage = namedtuple("Usage", ["total", "used", "free"])
            # Mock 100 bytes free space
            with patch("shutil.disk_usage", return_value=Usage(total=1000000, used=999900, free=100)):
                with self.assertRaises(OSError) as ctx:
                    offload_cache("huggingface", self.mock_d)
                self.assertIn("insufficient free space", str(ctx.exception).lower())

            # Source untouched
            self.assertTrue(hf_dir.exists())
            self.assertFalse(is_directory_junction(hf_dir))
            after_hashes = _compute_dir_hashes(hf_dir)
            self.assertEqual(original_hashes, after_hashes)


class TestAdversarialCliOffload(unittest.TestCase):
    """Stress tests on the CLI layer for offload command."""

    def test_cli_move_without_target_fails(self) -> None:
        """smart-drive offload --move <name> without --target returns exit code 1."""
        args = argparse.Namespace(
            move="uv",
            target=None,
            revert=None,
            scan=False,
            json=False,
            dry_run=False,
            force=False,
        )
        code = cmd_offload(args)
        self.assertEqual(code, 1)

    def test_cli_move_with_c_drive_target_fails(self) -> None:
        """smart-drive offload --move uv --target C: returns exit code 1."""
        args = argparse.Namespace(
            move="uv",
            target="C:",
            revert=None,
            scan=False,
            json=False,
            dry_run=False,
            force=False,
        )
        code = cmd_offload(args)
        self.assertEqual(code, 1)

    def test_cli_move_with_c_drive_target_json_output(self) -> None:
        """smart-drive offload --move uv --target C: --json outputs valid error JSON."""
        import io
        args = argparse.Namespace(
            move="uv",
            target="C:",
            revert=None,
            scan=False,
            json=True,
            dry_run=False,
            force=False,
        )
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            code = cmd_offload(args)
        self.assertEqual(code, 1)
        data = json.loads(captured.getvalue())
        self.assertEqual(data["status"], "error")
        self.assertIn("system drive (C:)", data["error"])

    def test_cli_revert_non_offloaded_cache_fails(self) -> None:
        """smart-drive offload --revert on non-offloaded cache returns exit code 1."""
        with tempfile.TemporaryDirectory() as tmp:
            env = {"SMART_DRIVE_MOCK_USERPROFILE": tmp}
            with patch.dict(os.environ, env):
                args = argparse.Namespace(
                    move=None,
                    target=None,
                    revert="huggingface",
                    scan=False,
                    json=False,
                    dry_run=False,
                    force=False,
                )
                code = cmd_offload(args)
                self.assertEqual(code, 1)


class TestAdversarialUnicodeAndEdgeCases(unittest.TestCase):
    """Stress tests covering Unicode filenames, spaces, and internal junction traversal guards."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_adv_unicode_")
        self.workspace = Path(self.temp_dir).resolve()
        self.mock_c = self.workspace / "DriveC"
        self.mock_d = self.workspace / "DriveD"
        self.mock_user = self.mock_c / "Người Dùng Thử Nghiệm"

        self.env = {
            "USERPROFILE": str(self.mock_user),
            "HOME": str(self.mock_user),
            "LOCALAPPDATA": str(self.mock_user / "AppData" / "Local"),
            "APPDATA": str(self.mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.mock_user),
            "SMART_DRIVE_ALLOW_MOCK_TARGET": "1",
        }

    def tearDown(self) -> None:
        if self.workspace.exists():
            for root, dirs, _ in os.walk(str(self.workspace)):
                for d in list(dirs):
                    full_p = Path(root) / d
                    if is_directory_junction(full_p):
                        try:
                            remove_directory_junction(full_p)
                        except Exception:
                            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_unicode_and_spaces_in_paths_offload_and_revert(self) -> None:
        """Full offload and revert lifecycle with Vietnamese unicode characters and spaces in files and directories."""
        with patch.dict(os.environ, self.env):
            hf_cache = self.mock_user / ".cache" / "huggingface"
            hf_cache.mkdir(parents=True, exist_ok=True)

            sub_unicode = hf_cache / "thư mục mô hình tiếng việt"
            sub_unicode.mkdir(parents=True, exist_ok=True)

            file1 = sub_unicode / "mô_hình_trọng_số_gốc.bin"
            file1.write_bytes(b"VIETNAMESE_UNICODE_MODEL_DATA" * 50)

            file2 = hf_cache / "ghi chú đặc tả.txt"
            file2.write_text("Dữ liệu kiểm thử tiếng Việt có dấu: Ắ, Ằ, Ẵ, Ặ, Ẹ, Ẻ, Ỗ, Ộ", encoding="utf-8")

            hashes_initial = _compute_dir_hashes(hf_cache)
            self.assertEqual(len(hashes_initial), 2)

            # 1. Offload to secondary drive
            res_offload = offload_cache("huggingface", self.mock_d)
            self.assertEqual(res_offload["status"], "success")
            self.assertTrue(is_directory_junction(hf_cache))

            # 2. Transparent access through junction
            self.assertTrue(file1.exists())
            self.assertTrue(file2.exists())
            self.assertEqual(file2.read_text(encoding="utf-8"), "Dữ liệu kiểm thử tiếng Việt có dấu: Ắ, Ằ, Ẵ, Ặ, Ẹ, Ẻ, Ỗ, Ộ")

            # 3. Revert back to local C: directory
            res_revert = revert_cache("huggingface")
            self.assertEqual(res_revert["status"], "reverted")
            self.assertFalse(is_directory_junction(hf_cache))
            self.assertTrue(hf_cache.is_dir())

            # 4. Verify 100% hash parity after round-trip
            hashes_final = _compute_dir_hashes(hf_cache)
            self.assertEqual(hashes_initial, hashes_final)

    def test_calculate_dir_size_does_not_traverse_internal_junction(self) -> None:
        """calculate_dir_size strictly ignores nested directory junctions, preventing infinite recursion."""
        cache_dir = self.workspace / "cache_with_junction"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "native_file.bin").write_bytes(b"A" * 1024)

        external_giant_dir = self.workspace / "external_giant_store"
        external_giant_dir.mkdir(parents=True, exist_ok=True)
        (external_giant_dir / "huge_1.bin").write_bytes(b"B" * 1048576)  # 1 MB
        (external_giant_dir / "huge_2.bin").write_bytes(b"C" * 1048576)  # 1 MB

        # Create nested junction inside cache_dir pointing to external_giant_dir
        nested_junction = cache_dir / "symlink_to_giant"
        ok = create_directory_junction(nested_junction, external_giant_dir)
        self.assertTrue(ok)
        self.assertTrue(is_directory_junction(nested_junction))

        # Size of cache_dir should ONLY be 1024 bytes (1 file), NOT 2MB + 1024!
        nominal_size, count = calculate_dir_size(cache_dir)
        self.assertEqual(nominal_size, 1024, "calculate_dir_size followed nested directory junction!")
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()

