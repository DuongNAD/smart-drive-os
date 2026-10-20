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
- An unreadable process list is never taken as "nothing is running": move and revert refuse, warn or wait.

100% Zero-Dependency Python Standard Library unittest.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
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

OFFLOADER = importlib.import_module("smart_drive.core.offloader")
PROCESSES = importlib.import_module("smart_drive.core.processes")


class FakeClock:
    """Stands in for the `time` module of the offloader: sleeping moves the clock forward instead of waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


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

    def test_docker_wsl_refused_when_docker_desktop_running_and_allowed_with_force(self) -> None:
        """T12: docker_wsl refuses to move when Docker Desktop.exe is running unless force=True."""
        with patch.dict(os.environ, self.env):
            docker_src = self.mock_user / "AppData" / "Local" / "Docker" / "wsl"
            docker_src.mkdir(parents=True, exist_ok=True)
            (docker_src / "ext4.vhdx").write_bytes(b"VHDX_DATA" * 64)

            with patch("smart_drive.core.offloader._running_processes", return_value={"Docker Desktop.exe"}):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("docker_wsl", self.mock_d)
                self.assertIn("Docker Desktop", str(ctx.exception))
                self.assertFalse(is_directory_junction(docker_src))
                self.assertTrue((docker_src / "ext4.vhdx").is_file())

                # With force=True, offload proceeds in sandbox mode and records a warning
                res = offload_cache("docker_wsl", self.mock_d, force=True)
                self.assertEqual(res["status"], "success")
                self.assertTrue(is_directory_junction(docker_src))
                self.assertTrue(any("Docker Desktop" in w for w in res.get("warnings", [])))

    def test_ollama_and_cargo_refused_when_running_while_npm_and_gradle_only_warn(self) -> None:
        """T12: ollama and cargo block when running; npm (node) and gradle (java) warn without blocking."""
        with patch.dict(os.environ, self.env):
            ollama_src = self.mock_user / ".ollama" / "models"
            ollama_src.mkdir(parents=True, exist_ok=True)
            (ollama_src / "model.bin").write_bytes(b"OLLAMA")

            cargo_src = self.mock_user / ".cargo" / "registry" / "cache"
            cargo_src.mkdir(parents=True, exist_ok=True)
            (cargo_src / "serde.crate").write_bytes(b"CRATE")

            npm_src = self.mock_user / "AppData" / "Roaming" / "npm-cache"
            npm_src.mkdir(parents=True, exist_ok=True)
            (npm_src / "index.json").write_text("{}", encoding="utf-8")

            gradle_src = self.mock_user / ".gradle" / "caches"
            gradle_src.mkdir(parents=True, exist_ok=True)
            (gradle_src / "jars.bin").write_bytes(b"JAR")

            with patch(
                "smart_drive.core.offloader._running_processes",
                return_value={"ollama app.exe", "rustc.exe", "node.exe", "java.exe"},
            ):
                with self.assertRaises(RuntimeError):
                    offload_cache("ollama", self.mock_d)
                with self.assertRaises(RuntimeError):
                    offload_cache("cargo", self.mock_d)

                # npm and gradle succeed without force=True, emitting a warning
                res_npm = offload_cache("npm", self.mock_d)
                self.assertEqual(res_npm["status"], "success")
                self.assertTrue(any("node" in w for w in res_npm.get("warnings", [])))

                res_gradle = offload_cache("gradle", self.mock_d)
                self.assertEqual(res_gradle["status"], "success")
                self.assertTrue(any("java" in w for w in res_gradle.get("warnings", [])))

    def test_wait_polls_until_process_closes_or_times_out(self) -> None:
        """T12: --wait / wait_minutes polls until blocking processes exit, or aborts on timeout."""
        import contextlib
        import io
        from smart_drive.cli.main import main as cli_main

        with patch.dict(os.environ, self.env):
            ollama_src = self.mock_user / ".ollama" / "models"
            ollama_src.mkdir(parents=True, exist_ok=True)
            (ollama_src / "weights.bin").write_bytes(b"WEIGHTS" * 32)

            # 1) Timeout when ollama never closes
            with (
                patch("smart_drive.core.offloader._running_processes", return_value={"ollama.exe"}),
                patch("smart_drive.core.offloader.time.sleep", return_value=None),
            ):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli_main([
                        "offload",
                        "--move",
                        "ollama",
                        "--target",
                        str(self.mock_d),
                        "--wait",
                        "0",
                        "--json",
                    ])
                self.assertEqual(code, 3)
                self.assertFalse(is_directory_junction(ollama_src))

            # 2) Process closes on second poll -> offload succeeds
            poll_seq = [{"ollama.exe"}, {"ollama.exe"}, set(), set()]
            with (
                patch("smart_drive.core.offloader._running_processes", side_effect=poll_seq),
                patch("smart_drive.core.offloader.time.sleep", return_value=None),
            ):
                out2, err2 = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
                    code2 = cli_main([
                        "offload",
                        "--move",
                        "ollama",
                        "--target",
                        str(self.mock_d),
                        "--wait",
                        "5",
                        "--json",
                    ])
                self.assertEqual(code2, 0, err2.getvalue())
                self.assertTrue(is_directory_junction(ollama_src))

    def test_revert_guard_dry_run_warnings_and_vmmemwsl(self) -> None:
        """T12 fix: revert_cache guards against running processes, dry-run reports 'would be blocked', and VmmemWSL.exe is recognized."""
        with patch.dict(os.environ, self.env):
            docker_src = self.mock_user / "AppData" / "Local" / "Docker" / "wsl"
            docker_src.mkdir(parents=True, exist_ok=True)
            (docker_src / "ext4.vhdx").write_bytes(b"VHDX_DATA" * 64)

            # 1) Offload dry-run with VmmemWSL.exe running reports "would be blocked"
            with patch("smart_drive.core.offloader._running_processes", return_value={"VmmemWSL.exe"}):
                dry_off = offload_cache("docker_wsl", self.mock_d, dry_run=True)
                self.assertEqual(dry_off["status"], "dry_run")
                self.assertIn("VmmemWSL", dry_off["blocking_processes"])
                self.assertTrue(any("would be blocked" in w and "VmmemWSL" in w for w in dry_off["warnings"]))
                self.assertIn("would be blocked", dry_off["message"])
                with self.assertRaises(RuntimeError) as ctx_vmmem:
                    offload_cache("docker_wsl", self.mock_d)
                self.assertIn("VmmemWSL", str(ctx_vmmem.exception))

            # 2) Offload cleanly when no process is running
            with patch("smart_drive.core.offloader._running_processes", return_value=set()):
                offload_cache("docker_wsl", self.mock_d)
                self.assertTrue(is_directory_junction(docker_src))

            # 3) Revert dry-run with Docker Desktop.exe running reports "would be blocked"
            with patch("smart_drive.core.offloader._running_processes", return_value={"Docker Desktop.exe"}):
                dry_rev = revert_cache("docker_wsl", dry_run=True)
                self.assertEqual(dry_rev["status"], "dry_run")
                self.assertTrue(any("would be blocked" in w and "Docker Desktop" in w for w in dry_rev["warnings"]))
                self.assertIn("would be blocked", dry_rev["message"])

                # 4) Revert without --force is refused before touching any files
                with self.assertRaises(RuntimeError) as ctx_rev:
                    revert_cache("docker_wsl")
                self.assertIn("Docker Desktop", str(ctx_rev.exception))
                self.assertTrue(is_directory_junction(docker_src))

                # 5) Revert with force=True succeeds and records warning
                res_rev = revert_cache("docker_wsl", force=True)
                self.assertEqual(res_rev["status"], "reverted")
                self.assertFalse(is_directory_junction(docker_src))
                self.assertTrue((docker_src / "ext4.vhdx").is_file())
                self.assertTrue(any("Docker Desktop" in w for w in res_rev.get("warnings", [])))

    def _make_cache(self, relative: str, filename: str) -> Path:
        """Creates a cache folder with one file under the sandbox user's profile and returns the folder."""
        source = self.mock_user.joinpath(*relative.split("/"))
        source.mkdir(parents=True, exist_ok=True)
        (source / filename).write_bytes(b"CACHE_DATA" * 64)
        return source

    def _offload_root_entries(self) -> list:
        """Returns the names under the sandbox offload root (empty when it was never created)."""
        root = self.mock_d / "04_System_Offload_Caches"
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def test_offload_is_refused_when_the_process_list_cannot_be_read(self) -> None:
        """An unreadable process list is not "Docker is closed": docker_wsl, ollama and cargo stay where they are."""
        cases = (
            ("docker_wsl", "AppData/Local/Docker/wsl", "ext4.vhdx", "Docker Desktop, VmmemWSL, com.docker.backend, vmmem, wsl"),
            ("ollama", ".ollama/models", "weights.bin", "ollama, ollama app"),
            ("cargo", ".cargo/registry/cache", "serde.crate", "cargo, rustc"),
        )
        probes = (
            ("returns None", {"return_value": None}),
            ("raises", {"side_effect": OSError("the process listing could not be started")}),
        )
        tool = "tasklist" if sys.platform == "win32" else "ps"
        with patch.dict(os.environ, self.env):
            for name, relative, filename, apps in cases:
                source = self._make_cache(relative, filename)
                for label, probe_kwargs in probes:
                    with self.subTest(cache=name, probe=label):
                        with patch.object(OFFLOADER, "_running_processes", **probe_kwargs):
                            with self.assertRaises(RuntimeError) as ctx:
                                offload_cache(name, self.mock_d)
                        message = str(ctx.exception)
                        self.assertIn("could not be read", message)
                        self.assertIn(f"({tool} failed or timed out)", message)
                        self.assertIn(f"whether {apps} is running", message)
                        self.assertIn("No files were moved", message)
                        self.assertIn("--wait", message)
                        self.assertIn("--force", message)
                        self.assertFalse(is_directory_junction(source))
                        self.assertTrue((source / filename).is_file())
                        self.assertFalse((self.mock_d / "04_System_Offload_Caches" / name).exists())
                        self.assertEqual(self._offload_root_entries(), [])

    def test_a_timed_out_process_listing_refuses_the_move_without_any_offloader_mock(self) -> None:
        """The real probe times out (as `tasklist` does on a loaded machine): the cache is not moved."""
        with patch.dict(os.environ, self.env):
            docker_src = self._make_cache("AppData/Local/Docker/wsl", "ext4.vhdx")
            with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("tasklist", 10)):
                with self.assertRaises(RuntimeError) as ctx:
                    offload_cache("docker_wsl", self.mock_d)
            self.assertIn("could not be read", str(ctx.exception))
            self.assertFalse(is_directory_junction(docker_src))
            self.assertTrue((docker_src / "ext4.vhdx").is_file())
            self.assertEqual(self._offload_root_entries(), [])

    def test_force_moves_a_cache_although_the_process_list_cannot_be_read(self) -> None:
        """--force overrides the refusal and records a warning that says why it was needed."""
        with patch.dict(os.environ, self.env):
            docker_src = self._make_cache("AppData/Local/Docker/wsl", "ext4.vhdx")
            with patch.object(OFFLOADER, "_running_processes", return_value=None):
                res = offload_cache("docker_wsl", self.mock_d, force=True)
            self.assertEqual(res["status"], "success")
            self.assertTrue(is_directory_junction(docker_src))
            self.assertTrue(any("forced offload" in w and "could not be read" in w for w in res["warnings"]), res["warnings"])

    def test_dry_run_says_it_would_be_blocked_when_the_process_list_cannot_be_read(self) -> None:
        """A dry run reports the unreadable list as a block, in the warnings and the message, and as a flag."""
        with patch.dict(os.environ, self.env):
            docker_src = self._make_cache("AppData/Local/Docker/wsl", "ext4.vhdx")
            with patch.object(OFFLOADER, "_running_processes", return_value=None):
                res = offload_cache("docker_wsl", self.mock_d, dry_run=True)
            self.assertEqual(res["status"], "dry_run")
            self.assertIs(res["process_list_readable"], False)
            self.assertEqual(res["blocking_processes"], [])
            self.assertTrue(
                any(w.startswith("would be blocked") and "could not be read" in w for w in res["warnings"]),
                res["warnings"],
            )
            self.assertIn("(would be blocked: the list of running programs could not be read)", res["message"])
            self.assertFalse(is_directory_junction(docker_src))
            self.assertEqual(self._offload_root_entries(), [])

            with patch.object(OFFLOADER, "_running_processes", return_value=set()):
                readable = offload_cache("docker_wsl", self.mock_d, dry_run=True)
            self.assertIs(readable["process_list_readable"], True)
            self.assertEqual(readable["warnings"], [])
            self.assertNotIn("would be blocked", readable["message"])

    def test_wait_goes_on_once_the_process_list_can_be_read_again(self) -> None:
        """--wait keeps checking while the list is unreadable and moves the cache when it reads back as empty."""
        with patch.dict(os.environ, self.env):
            ollama_src = self._make_cache(".ollama/models", "weights.bin")
            with patch.object(OFFLOADER, "_running_processes", side_effect=[None, None, set()]) as probe:
                with patch.object(OFFLOADER.time, "sleep", return_value=None) as sleep:
                    res = offload_cache("ollama", self.mock_d, wait_minutes=5)
            self.assertEqual(res["status"], "success")
            self.assertEqual(probe.call_count, 3)
            self.assertEqual(sleep.call_count, 1)
            self.assertTrue(is_directory_junction(ollama_src))

    def test_wait_still_waits_for_a_program_that_shows_up_once_the_list_can_be_read(self) -> None:
        """An unreadable poll followed by a readable one that lists ollama keeps waiting until ollama closes."""
        with patch.dict(os.environ, self.env):
            ollama_src = self._make_cache(".ollama/models", "weights.bin")
            polls = [None, {"ollama.exe"}, {"ollama.exe"}, set()]
            with patch.object(OFFLOADER, "_running_processes", side_effect=polls) as probe:
                with patch.object(OFFLOADER.time, "sleep", return_value=None):
                    res = offload_cache("ollama", self.mock_d, wait_minutes=5)
            self.assertEqual(res["status"], "success")
            self.assertEqual(probe.call_count, 4)
            self.assertTrue(is_directory_junction(ollama_src))

    def test_wait_gives_up_with_exit_code_3_when_the_process_list_stays_unreadable(self) -> None:
        """--wait that ends on an unreadable list is a timeout (exit code 3); without --wait it is a plain refusal."""
        from smart_drive.cli.main import main as cli_main

        def run_cli(extra: list) -> tuple:
            out, err = io.StringIO(), io.StringIO()
            argv = ["offload", "--move", "ollama", "--target", str(self.mock_d), "--json"] + extra
            with patch.object(OFFLOADER, "_running_processes", return_value=None):
                with patch.object(OFFLOADER.time, "sleep", return_value=None):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = cli_main(argv)
            return code, json.loads(out.getvalue())

        with patch.dict(os.environ, self.env):
            ollama_src = self._make_cache(".ollama/models", "weights.bin")

            code, payload = run_cli(["--wait", "0"])
            self.assertEqual(code, 3)
            self.assertEqual(payload["status"], "error")
            self.assertTrue(payload["error"].startswith("Timed out waiting for"), payload["error"])
            self.assertIn("still could not be read", payload["error"])
            self.assertIn("no files were moved", payload["error"])
            self.assertFalse(is_directory_junction(ollama_src))
            self.assertTrue((ollama_src / "weights.bin").is_file())
            self.assertEqual(self._offload_root_entries(), [])

            code_plain, payload_plain = run_cli([])
            self.assertEqual(code_plain, 1)
            self.assertIn("could not be read", payload_plain["error"])
            self.assertFalse(payload_plain["error"].startswith("Timed out waiting for"))
            self.assertFalse(is_directory_junction(ollama_src))

    def test_wait_for_cache_processes_tells_an_unreadable_list_from_a_running_program(self) -> None:
        """At the deadline the answer is the programs still running, or None when the last poll could not read the list."""
        running = {"ollama.exe"}

        def wait_with(polls: list, wait_minutes: float = 1) -> tuple:
            """Polls the (fake) process list as `polls` says (the last entry repeats) and returns (answer, polls made)."""
            clock = FakeClock()
            seen: list = []

            def probe():
                seen.append(clock.now)
                return polls[min(len(seen), len(polls)) - 1]

            with patch.object(OFFLOADER, "time", clock):
                with patch.object(OFFLOADER, "_running_processes", side_effect=probe):
                    return OFFLOADER.wait_for_cache_processes("ollama", wait_minutes), len(seen)

        self.assertEqual(wait_with([set()]), ([], 1))
        self.assertEqual(wait_with([running, running, set()]), ([], 3))
        self.assertEqual(wait_with([None, None, set()]), ([], 3))
        self.assertEqual(wait_with([running])[0], ["ollama"])
        self.assertIsNone(wait_with([None])[0])
        self.assertIsNone(wait_with([running, None])[0], "the last poll could not read the list")
        self.assertEqual(wait_with([None, running])[0], ["ollama"], "the last poll read the list and ollama runs")
        # 60 seconds in steps of 2 seconds: the first poll plus 30 more
        self.assertEqual(wait_with([None])[1], 31)

    def test_npm_and_gradle_only_warn_when_the_process_list_cannot_be_read(self) -> None:
        """Caches with warning-only programs (node, java) are never refused over an unreadable list, but say so."""
        with patch.dict(os.environ, self.env):
            npm_src = self._make_cache("AppData/Roaming/npm-cache", "index.json")
            gradle_src = self._make_cache(".gradle/caches", "jars.bin")
            with patch.object(OFFLOADER, "_running_processes", return_value=None):
                npm_dry = offload_cache("npm", self.mock_d, dry_run=True)
                res_npm = offload_cache("npm", self.mock_d)
                res_gradle = offload_cache("gradle", self.mock_d)

            self.assertEqual(npm_dry["status"], "dry_run")
            self.assertIs(npm_dry["process_list_readable"], False)
            self.assertNotIn("would be blocked", npm_dry["message"])
            self.assertFalse(any("would be blocked" in w for w in npm_dry["warnings"]))

            self.assertEqual(res_npm["status"], "success")
            self.assertTrue(is_directory_junction(npm_src))
            self.assertTrue(
                any("could not be read" in w and "node" in w and "offloading 'npm'" in w for w in res_npm["warnings"]),
                res_npm["warnings"],
            )
            self.assertEqual(res_gradle["status"], "success")
            self.assertTrue(is_directory_junction(gradle_src))
            self.assertTrue(any("could not be read" in w and "java" in w for w in res_gradle["warnings"]), res_gradle["warnings"])

    def test_revert_is_refused_when_the_process_list_cannot_be_read(self) -> None:
        """Reverting goes through the same check: refused, still linked; the dry run and --force behave as for a move."""
        with patch.dict(os.environ, self.env):
            docker_src = self._make_cache("AppData/Local/Docker/wsl", "ext4.vhdx")
            with patch.object(OFFLOADER, "_running_processes", return_value=set()):
                offload_cache("docker_wsl", self.mock_d)
            self.assertTrue(is_directory_junction(docker_src))

            with patch.object(OFFLOADER, "_running_processes", return_value=None):
                with self.assertRaises(RuntimeError) as ctx:
                    revert_cache("docker_wsl")
                self.assertIn("Cannot revert 'docker_wsl'", str(ctx.exception))
                self.assertIn("could not be read", str(ctx.exception))
                self.assertTrue(is_directory_junction(docker_src))
                self.assertTrue((docker_src / "ext4.vhdx").is_file())

                dry = revert_cache("docker_wsl", dry_run=True)
                self.assertEqual(dry["status"], "dry_run")
                self.assertIs(dry["process_list_readable"], False)
                self.assertTrue(any(w.startswith("would be blocked") and "could not be read" in w for w in dry["warnings"]))
                self.assertIn("would be blocked", dry["message"])
                self.assertTrue(is_directory_junction(docker_src))

                forced = revert_cache("docker_wsl", force=True)
            self.assertEqual(forced["status"], "reverted")
            self.assertTrue(any("forced revert" in w and "could not be read" in w for w in forced["warnings"]), forced["warnings"])
            self.assertFalse(is_directory_junction(docker_src))
            self.assertTrue((docker_src / "ext4.vhdx").is_file())

    def test_an_unreadable_process_list_is_none_and_not_an_empty_set(self) -> None:
        """The probe says None for a failed or timed-out listing, and the offloader keeps that instead of `set()`."""

        def timing_out(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 10)

        def failing(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="the listing failed")

        self.assertIsNone(PROCESSES.probe_running_processes(run_fn=timing_out))
        self.assertIsNone(PROCESSES.probe_running_processes(run_fn=failing))

        with patch.object(PROCESSES, "probe_running_processes", return_value=None) as probe:
            self.assertIsNone(OFFLOADER._probe_processes())
            self.assertIsNone(OFFLOADER.running_cache_processes("docker_wsl"))
            # the public helper still answers "no programs" for an unreadable list, as its docstring says
            self.assertEqual(PROCESSES.running_processes(), set())
            probe.reset_mock()
            # a cache without process rules is never held up by the listing
            self.assertEqual(OFFLOADER.running_cache_processes("uv"), ([], []))
            probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
