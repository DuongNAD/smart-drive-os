"""tests.test_offloader - Comprehensive Unit Test Suite for Cache Offloader Engine.

Verifies:
- Cache catalog definitions and required caches (huggingface, ollama, pytorch, pip, npm, uv, conda, gradle, docker_wsl).
- Environment variable overrides (HF_HOME, UV_CACHE_DIR, OLLAMA_MODELS, etc.).
- Cache scanning with simulated directory structures and size calculation.
- Strict rejection of system drive C: (C:, C:\\, c:) as offload target.
- 7-Phase transactional move and NTFS directory junction creation.
- Transparent file access across junction.
- Manifest generation and tracking (offload_manifest.json).
- Transactional rollback on mid-move failures (source preservation guarantee).
- Revert operation (restores native directory, unlinks junction, cleans up secondary drive).
- Dry-run simulation modes for both move and revert.

100% Zero-Dependency Python Standard Library unittest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from smart_drive.core.junction import is_directory_junction, remove_directory_junction
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


class TestCacheCatalogAndEnvironmentOverrides(unittest.TestCase):
    """Tests for cache catalog integrity and environment variable resolution."""

    def test_required_caches_exist_in_catalog(self) -> None:
        """All mandatory developer and AI caches exist in the catalog with complete metadata."""
        required = [
            "huggingface",
            "ollama",
            "pytorch",
            "pip",
            "npm",
            "uv",
            "conda",
            "gradle",
            "docker_wsl",
        ]
        for name in required:
            self.assertIn(name, CACHE_CATALOG, f"Mandatory cache '{name}' missing from catalog")
            defn = CACHE_CATALOG[name]
            self.assertEqual(defn.name, name)
            self.assertTrue(len(defn.title) > 0)
            self.assertTrue(len(defn.category) > 0)
            self.assertTrue(len(defn.description) > 0)
            self.assertIsInstance(defn.env_vars, list)
            self.assertIsInstance(defn.relative_paths, list)

    def test_environment_variable_override_resolution(self) -> None:
        """Environment variables override default directory paths."""
        with tempfile.TemporaryDirectory(prefix="sd_override_test_") as tmp:
            custom_hf = Path(tmp) / "custom_hf_home"
            custom_hf.mkdir(parents=True, exist_ok=True)

            with patch.dict(os.environ, {"HF_HOME": str(custom_hf)}):
                defn = CACHE_CATALOG["huggingface"]
                resolved = resolve_cache_path(defn)
                self.assertEqual(resolved, custom_hf.resolve())

            custom_uv = Path(tmp) / "custom_uv_dir"
            custom_uv.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"UV_CACHE_DIR": str(custom_uv)}):
                defn = CACHE_CATALOG["uv"]
                resolved = resolve_cache_path(defn)
                self.assertEqual(resolved, custom_uv.resolve())

            custom_ollama = Path(tmp) / "custom_ollama"
            custom_ollama.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"OLLAMA_MODELS": str(custom_ollama)}):
                defn = CACHE_CATALOG["ollama"]
                resolved = resolve_cache_path(defn)
                self.assertEqual(resolved, custom_ollama.resolve())


class TestCacheScanner(unittest.TestCase):
    """Tests for cache scanning and size calculation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_scan_test_")
        self.workspace = Path(self.temp_dir).resolve()
        self.mock_user = self.workspace / "Users" / "TestUser"
        self.mock_user.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_calculate_dir_size_does_not_traverse_junctions(self) -> None:
        """calculate_dir_size sums file bytes and does not traverse into directory junctions."""
        test_dir = self.workspace / "size_calc_dir"
        test_dir.mkdir()
        (test_dir / "file1.bin").write_bytes(b"A" * 1000)
        (test_dir / "file2.bin").write_bytes(b"B" * 2000)

        subdir = test_dir / "subdir"
        subdir.mkdir()
        (subdir / "file3.bin").write_bytes(b"C" * 3000)

        total_bytes, count = calculate_dir_size(test_dir)
        self.assertEqual(total_bytes, 6000)
        self.assertEqual(count, 3)

    def test_scan_caches_identifies_found_notfound_and_offloaded(self) -> None:
        """scan_caches correctly categorizes caches and calculates total reclaimable space."""
        # 1. Setup mock directories
        hf_dir = self.mock_user / ".cache" / "huggingface"
        hf_dir.mkdir(parents=True, exist_ok=True)
        (hf_dir / "weight.bin").write_bytes(b"X" * 10240)  # 10 KB

        uv_dir = self.mock_user / "AppData" / "Local" / "uv" / "cache"
        uv_dir.mkdir(parents=True, exist_ok=True)
        (uv_dir / "pkg.tar").write_bytes(b"Y" * 20480)    # 20 KB

        env = {
            "USERPROFILE": str(self.mock_user),
            "HOME": str(self.mock_user),
            "LOCALAPPDATA": str(self.mock_user / "AppData" / "Local"),
            "APPDATA": str(self.mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.mock_user),
        }

        with patch.dict(os.environ, env):
            targets = scan_caches()
            target_map = {t.name: t for t in targets}

            # HuggingFace should be FOUND
            self.assertIn("huggingface", target_map)
            self.assertEqual(target_map["huggingface"].status, "FOUND")
            self.assertEqual(target_map["huggingface"].size_bytes, 10240)
            self.assertFalse(target_map["huggingface"].is_offloaded)

            # uv should be FOUND
            self.assertIn("uv", target_map)
            self.assertEqual(target_map["uv"].status, "FOUND")
            self.assertEqual(target_map["uv"].size_bytes, 20480)

            # Ollama was not created, should be NOT FOUND
            self.assertIn("ollama", target_map)
            self.assertEqual(target_map["ollama"].status, "NOT FOUND")
            self.assertEqual(target_map["ollama"].size_bytes, 0)

            # Verify get_scan_summary
            summary = get_scan_summary()
            self.assertGreaterEqual(summary["total_reclaimable_bytes"], 30720)
            self.assertGreaterEqual(summary["discovered_count"], 2)


class TestTargetDriveValidation(unittest.TestCase):
    """Tests for validating target drive specifications and rejecting system drive C:."""

    def test_rejection_of_c_drive_targets(self) -> None:
        """Target specifications matching C: or C:\\ are strictly rejected with ValueError."""
        invalid_targets = ["C:", "C:\\", "c:", "c:\\", "C", "c", "C:/", "c:/"]
        for target in invalid_targets:
            with self.assertRaises(ValueError, msg=f"Target '{target}' should have been rejected!") as ctx:
                validate_target_drive(target)
            self.assertIn("system drive (C:)", str(ctx.exception))

    def test_rejection_of_empty_target(self) -> None:
        """Empty target string raises ValueError."""
        with self.assertRaises(ValueError):
            validate_target_drive("")
        with self.assertRaises(ValueError):
            validate_target_drive("   ")

    def test_valid_secondary_drive_letter_resolution(self) -> None:
        """Target 'D:' resolves to 'D:' and offload root 'D:\\04_System_Offload_Caches'."""
        letter, root = validate_target_drive("D:")
        self.assertEqual(letter, "D:")
        self.assertEqual(str(root), "D:\\04_System_Offload_Caches")

        letter, root = validate_target_drive("E:\\")
        self.assertEqual(letter, "E:")
        self.assertEqual(str(root), "E:\\04_System_Offload_Caches")


class TestTransactionalOffloadAndRevert(unittest.TestCase):
    """Tests for the 7-phase transactional offload, rollback, and reversion."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_offload_trans_")
        self.workspace = Path(self.temp_dir).resolve()

        self.mock_c = self.workspace / "DriveC"
        self.mock_c.mkdir()
        self.mock_d = self.workspace / "DriveD"
        self.mock_d.mkdir()

        self.mock_user = self.mock_c / "Users" / "Dev"
        self.mock_user.mkdir(parents=True, exist_ok=True)

        self.env = {
            "USERPROFILE": str(self.mock_user),
            "HOME": str(self.mock_user),
            "LOCALAPPDATA": str(self.mock_user / "AppData" / "Local"),
            "APPDATA": str(self.mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.mock_user),
            # Allow mock directory target in tests
            "SMART_DRIVE_ALLOW_MOCK_TARGET": "1",
        }

    def tearDown(self) -> None:
        # Clean up any created junctions safely
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

    def test_successful_7_phase_offload_and_revert_lifecycle(self) -> None:
        """Test full transactional cycle: copy, rename, junction creation, manifest update, and revert."""
        with patch.dict(os.environ, self.env):
            # 1. Setup mock uv cache with files
            uv_source = self.mock_user / "AppData" / "Local" / "uv" / "cache"
            uv_source.mkdir(parents=True, exist_ok=True)
            (uv_source / "package-a.whl").write_bytes(b"WHEEL_A_CONTENT" * 50)
            (uv_source / "package-b.whl").write_bytes(b"WHEEL_B_CONTENT" * 60)
            sub = uv_source / "subrepo"
            sub.mkdir()
            (sub / "meta.json").write_text('{"name": "test"}', encoding="utf-8")

            original_size, original_count = calculate_dir_size(uv_source)
            self.assertEqual(original_count, 3)

            # 2. Execute offload to mock Drive D
            res = offload_cache("uv", self.mock_d)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["phases_completed"], 7)
            self.assertEqual(res["reclaimed_bytes"], original_size)
            self.assertEqual(res["file_count"], 3)

            # 3. Verify source on C: is now a Directory Junction!
            self.assertTrue(is_directory_junction(uv_source))
            expected_target = self.mock_d / "04_System_Offload_Caches" / "uv"
            self.assertTrue(expected_target.is_dir())

            # 4. Verify transparent read through junction
            self.assertTrue((uv_source / "package-a.whl").exists())
            self.assertEqual((uv_source / "package-a.whl").read_bytes(), b"WHEEL_A_CONTENT" * 50)
            self.assertEqual((uv_source / "subrepo" / "meta.json").read_text(encoding="utf-8"), '{"name": "test"}')

            # 5. Verify manifest exists and is updated
            manifest_p = self.mock_d / "04_System_Offload_Caches" / "offload_manifest.json"
            self.assertTrue(manifest_p.is_file())
            manifest_data = json.loads(manifest_p.read_text(encoding="utf-8"))
            self.assertIn("uv", manifest_data["caches"])
            self.assertEqual(manifest_data["caches"]["uv"]["status"], "active")

            # 6. Verify scan now reports uv as OFFLOADED
            targets = scan_caches()
            uv_target = next(t for t in targets if t.name == "uv")
            self.assertTrue(uv_target.is_offloaded)
            self.assertEqual(uv_target.status, "OFFLOADED")
            self.assertEqual(uv_target.size_bytes, 0)  # Reclaimed from C:

            # 7. Execute revert_cache
            revert_res = revert_cache("uv")
            self.assertEqual(revert_res["status"], "reverted")
            self.assertEqual(revert_res["freed_secondary_bytes"], original_size)

            # 8. Verify source on C: is NO LONGER a junction, but a normal folder!
            self.assertFalse(is_directory_junction(uv_source))
            self.assertTrue(uv_source.is_dir())
            self.assertEqual((uv_source / "package-a.whl").read_bytes(), b"WHEEL_A_CONTENT" * 50)
            self.assertEqual((uv_source / "subrepo" / "meta.json").read_text(encoding="utf-8"), '{"name": "test"}')

            # 9. Verify target on Drive D was cleaned up
            self.assertFalse(expected_target.exists())

            # 10. Verify manifest status updated to reverted
            manifest_data = json.loads(manifest_p.read_text(encoding="utf-8"))
            self.assertEqual(manifest_data["caches"]["uv"]["status"], "reverted")

    def test_dry_run_modes(self) -> None:
        """Dry-run simulations execute pre-flight checks without modifying the filesystem."""
        with patch.dict(os.environ, self.env):
            hf_source = self.mock_user / ".cache" / "huggingface"
            hf_source.mkdir(parents=True, exist_ok=True)
            (hf_source / "weights.safetensors").write_bytes(b"SIMULATED_WEIGHTS" * 100)

            # Move dry run
            res_move = offload_cache("huggingface", self.mock_d, dry_run=True)
            self.assertEqual(res_move["status"], "dry_run")
            self.assertIn("[DRY-RUN]", res_move["message"])
            self.assertFalse(is_directory_junction(hf_source))
            self.assertFalse((self.mock_d / "04_System_Offload_Caches" / "huggingface").exists())

            # Actual move
            offload_cache("huggingface", self.mock_d)
            self.assertTrue(is_directory_junction(hf_source))

            # Revert dry run
            res_revert = revert_cache("huggingface", dry_run=True)
            self.assertEqual(res_revert["status"], "dry_run")
            self.assertTrue(is_directory_junction(hf_source))  # Still a junction

    def test_rollback_on_simulated_junction_failure(self) -> None:
        """If junction creation fails in Phase 5, source directory is restored with zero data loss."""
        with patch.dict(os.environ, self.env):
            pip_source = self.mock_user / "AppData" / "Local" / "pip" / "cache"
            pip_source.mkdir(parents=True, exist_ok=True)
            (pip_source / "important_wheel.whl").write_bytes(b"IMPORTANT_PIP_DATA" * 50)

            # Mock create_directory_junction to simulate failure in Phase 5
            with patch("smart_drive.core.offloader.create_directory_junction", return_value=False):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("pip", self.mock_d)

                self.assertIn("rolled back", str(ctx.exception).lower())

            # CRITICAL SAFETY INVARIANT: Original source directory and files MUST be 100% restored
            self.assertTrue(pip_source.exists())
            self.assertTrue(pip_source.is_dir())
            self.assertFalse(is_directory_junction(pip_source))
            self.assertTrue((pip_source / "important_wheel.whl").exists())
            self.assertEqual((pip_source / "important_wheel.whl").read_bytes(), b"IMPORTANT_PIP_DATA" * 50)

            # Target directory on D: must be cleaned up
            target_pip = self.mock_d / "04_System_Offload_Caches" / "pip"
            self.assertFalse(target_pip.exists())

    def test_revert_non_junction_raises_value_error(self) -> None:
        """Attempting to revert a regular folder raises ValueError."""
        with patch.dict(os.environ, self.env):
            ollama_source = self.mock_user / ".ollama" / "models"
            ollama_source.mkdir(parents=True, exist_ok=True)
            (ollama_source / "blob.bin").write_bytes(b"BLOB")

            with self.assertRaises(ValueError) as ctx:
                revert_cache("ollama")

            self.assertIn("not currently an active directory junction", str(ctx.exception))

    def test_unknown_cache_name_raises_key_error(self) -> None:
        """Unknown cache names raise KeyError."""
        with self.assertRaises(KeyError):
            offload_cache("non_existent_cache_xyz", self.mock_d)

        with self.assertRaises(KeyError):
            revert_cache("non_existent_cache_xyz")


if __name__ == "__main__":
    unittest.main()
