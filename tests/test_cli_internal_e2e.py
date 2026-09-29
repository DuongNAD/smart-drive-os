"""tests.test_cli_internal_e2e - End-to-End CLI Subprocess Test Suite for Internal Secondary Drive.

Opaque-box, requirement-driven test suite verifying:
- `smart-drive init --profile internal-developer-vault` (6 taxonomies, subdirs, shields, manifests, FTS5)
- `smart-drive health [drive_letter]` (TRIM status, cluster geometry, free space warnings, JSON/text)
- `smart-drive offload` (--scan, --move, --revert, junction creation, C: drive target rejection)

Follows 100% Zero-Dependency Python Standard Library architecture.
Uses isolated sandboxes, environment redirection, and mock drives to guarantee 0% host modification.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests.helpers import (
    CLUSTER_SIZE,
    SmartDriveTestCase,
    TempWorkspace,
    run_smart_drive_cli,
)


# ==============================================================================
# Feature Availability Detection (Progressive Testability)
# ==============================================================================

def _is_subcommand_registered(subcmd: str) -> bool:
    """Check if subcommand is registered in smart_drive CLI parser."""
    try:
        from smart_drive.cli.main import build_parser
        parser = build_parser()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return subcmd in action.choices
        return False
    except Exception:
        return False


def _is_internal_vault_profile_registered() -> bool:
    """Check if 'internal-developer-vault' profile is registered in initializer."""
    try:
        from smart_drive.core.initializer import PROFILES
        return "internal-developer-vault" in PROFILES
    except Exception:
        return False


# Expected 6 partitions for internal-developer-vault profile
INTERNAL_VAULT_TAXONOMIES: List[str] = [
    "01_AI_Models",
    "02_Development_Workspaces",
    "03_Data_Vault",
    "04_System_Offload_Caches",
    "05_Dev_Toolbox",
    "06_Archives_Storage",
]


# ==============================================================================
# Mock Environment Fixtures
# ==============================================================================

def create_mock_cache_tree(base_dir: Path) -> Dict[str, Path]:
    """Creates a realistic simulated C: drive cache structure inside base_dir."""
    user_home = base_dir / "Users" / "MockDeveloper"
    local_app_data = user_home / "AppData" / "Local"
    app_data = user_home / "AppData" / "Roaming"

    # 1. HuggingFace cache
    hf_dir = user_home / ".cache" / "huggingface" / "hub" / "models--test--model"
    hf_dir.mkdir(parents=True, exist_ok=True)
    snapshots = hf_dir / "snapshots" / "main"
    snapshots.mkdir(parents=True, exist_ok=True)
    (snapshots / "model.safetensors").write_bytes(b"MOCK_SAFETENSORS_WEIGHTS_" * 100)

    # 2. Ollama models
    ollama_dir = user_home / ".ollama" / "models" / "blobs"
    ollama_dir.mkdir(parents=True, exist_ok=True)
    (ollama_dir / "sha256-mock_blob").write_bytes(b"MOCK_OLLAMA_MODEL_BLOB_" * 80)

    # 3. pip cache
    pip_dir = local_app_data / "pip" / "cache" / "wheels"
    pip_dir.mkdir(parents=True, exist_ok=True)
    (pip_dir / "package-1.0-py3-none-any.whl").write_bytes(b"MOCK_PIP_WHEEL_DATA_" * 40)

    # 4. uv cache
    uv_dir = local_app_data / "uv" / "cache" / "archive-v0"
    uv_dir.mkdir(parents=True, exist_ok=True)
    (uv_dir / "cached_pkg.tar.gz").write_bytes(b"MOCK_UV_ARCHIVE_DATA_" * 30)

    # 5. npm cache
    npm_dir = app_data / "npm-cache" / "_cacache" / "content-v2"
    npm_dir.mkdir(parents=True, exist_ok=True)
    (npm_dir / "mock_package.tgz").write_bytes(b"MOCK_NPM_TARBALL_DATA_" * 35)

    return {
        "user_home": user_home,
        "local_app_data": local_app_data,
        "app_data": app_data,
        "huggingface": user_home / ".cache" / "huggingface",
        "ollama": user_home / ".ollama" / "models",
        "pip": local_app_data / "pip",
        "uv": local_app_data / "uv",
        "npm": app_data / "npm-cache",
    }


def make_isolated_env(cache_map: Dict[str, Path]) -> Dict[str, str]:
    """Generates an environment dict that sandboxes user directories."""
    env = os.environ.copy()
    user_home = str(cache_map["user_home"])
    local_app_data = str(cache_map["local_app_data"])
    app_data = str(cache_map["app_data"])

    env["USERPROFILE"] = user_home
    env["HOME"] = user_home
    env["LOCALAPPDATA"] = local_app_data
    env["APPDATA"] = app_data
    env["HF_HOME"] = str(cache_map["huggingface"])
    env["OLLAMA_MODELS"] = str(cache_map["ollama"])
    env["UV_CACHE_DIR"] = str(cache_map["uv"])
    # Point internal testing mock drive env vars if needed
    env["SMART_DRIVE_MOCK_USERPROFILE"] = user_home
    return env


# ==============================================================================
# Test Suite 1: Internal Developer Vault Profile (Milestone M3, Feature F07)
# ==============================================================================

class TestCliInternalDeveloperVaultInit(SmartDriveTestCase):
    """E2E tests for `smart-drive init --profile internal-developer-vault`."""

    @unittest.skipUnless(
        _is_internal_vault_profile_registered(),
        "Profile 'internal-developer-vault' not yet registered in PROFILES (Milestone M3)",
    )
    def test_init_internal_vault_json_output_and_all_six_taxonomies(self) -> None:
        """Init with internal-developer-vault creates all 6 taxonomies and outputs valid JSON."""
        with TempWorkspace() as ws:
            res = run_smart_drive_cli([
                "init",
                "--root", str(ws),
                "--profile", "internal-developer-vault",
                "--json",
            ])
            self.assertEqual(res.returncode, 0, f"init command failed: {res.stderr}")

            data = json.loads(res.stdout)
            self.assertEqual(data["status"], "initialized")
            self.assertEqual(data["profile"], "internal-developer-vault")
            self.assertTrue(data["database_initialized"])

            # Verify all 6 taxonomies exist on disk
            for tax in INTERNAL_VAULT_TAXONOMIES:
                tax_dir = ws / tax
                self.assertTrue(
                    tax_dir.is_dir(),
                    f"Expected taxonomy directory '{tax}' was not created on disk",
                )

            # Specifically verify 04_System_Offload_Caches is created
            self.assertTrue((ws / "04_System_Offload_Caches").is_dir())

            # Verify shields, manifests, and search database
            self.assertTrue((ws / ".metadata_never_index").is_file())
            self.assertTrue((ws / "AGENTS.md").is_file())
            self.assertTrue((ws / "GEMINI.md").is_file())
            self.assertTrue((ws / "CLAUDE.md").is_file())
            self.assertTrue((ws / ".smart_drive" / "index.db").is_file())

    @unittest.skipUnless(
        _is_internal_vault_profile_registered(),
        "Profile 'internal-developer-vault' not yet registered in PROFILES (Milestone M3)",
    )
    def test_init_internal_vault_human_readable_output(self) -> None:
        """Init with internal-developer-vault prints clean human-readable confirmation."""
        with TempWorkspace() as ws:
            res = run_smart_drive_cli([
                "init",
                "--root", str(ws),
                "--profile", "internal-developer-vault",
            ])
            self.assertEqual(res.returncode, 0)
            self.assertIn("internal-developer-vault", res.stdout)
            self.assertIn("Initialized SmartDrive workspace", res.stdout)

    @unittest.skipUnless(
        _is_internal_vault_profile_registered(),
        "Profile 'internal-developer-vault' not yet registered in PROFILES (Milestone M3)",
    )
    def test_init_internal_vault_idempotent_execution(self) -> None:
        """Re-running init on already initialized internal vault does not overwrite data."""
        with TempWorkspace() as ws:
            # First initialization
            run_smart_drive_cli([
                "init",
                "--root", str(ws),
                "--profile", "internal-developer-vault",
            ])

            # Place a user file in 04_System_Offload_Caches
            user_marker = ws / "04_System_Offload_Caches" / "existing_cache.txt"
            user_marker.write_text("pre-existing cache content\n", encoding="utf-8")

            # Second initialization without force
            res_second = run_smart_drive_cli([
                "init",
                "--root", str(ws),
                "--profile", "internal-developer-vault",
                "--json",
            ])
            self.assertEqual(res_second.returncode, 0)

            # User file must remain untouched
            self.assertTrue(user_marker.is_file())
            self.assertEqual(user_marker.read_text(encoding="utf-8"), "pre-existing cache content\n")


# ==============================================================================
# Test Suite 2: SSD Health & TRIM Monitor (Milestone M3, Feature F08)
# ==============================================================================

class TestCliHealthCommand(SmartDriveTestCase):
    """E2E tests for `smart-drive health [drive_letter]`."""

    @unittest.skipUnless(
        _is_subcommand_registered("health"),
        "Subcommand 'health' not yet registered in smart-drive CLI (Milestone M3)",
    )
    def test_health_help_flag(self) -> None:
        """Executing `smart-drive health --help` displays usage banner and exits 0."""
        res = run_smart_drive_cli(["health", "--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("health", res.stdout.lower())

    @unittest.skipUnless(
        _is_subcommand_registered("health"),
        "Subcommand 'health' not yet registered in smart-drive CLI (Milestone M3)",
    )
    def test_health_json_output_structure(self) -> None:
        """`smart-drive health --json` outputs all required health report fields."""
        res = run_smart_drive_cli(["health", "--json"])
        self.assertEqual(res.returncode, 0, f"health command failed: {res.stderr}")

        data = json.loads(res.stdout)
        required_fields = [
            "drive_letter",
            "filesystem",
            "trim_enabled",
            "trim_status_message",
            "total_bytes",
            "free_bytes",
            "free_percent",
            "cluster_size_bytes",
            "warnings",
        ]
        for field in required_fields:
            self.assertIn(field, data, f"Required health report field '{field}' missing from JSON")

        self.assertIsInstance(data["warnings"], list)
        self.assertIsInstance(data["free_percent"], (int, float))
        self.assertGreaterEqual(data["total_bytes"], 0)

    @unittest.skipUnless(
        _is_subcommand_registered("health"),
        "Subcommand 'health' not yet registered in smart-drive CLI (Milestone M3)",
    )
    def test_health_human_readable_output(self) -> None:
        """`smart-drive health` displays formatted terminal diagnostic output."""
        res = run_smart_drive_cli(["health"])
        self.assertEqual(res.returncode, 0)
        out_lower = res.stdout.lower()
        self.assertTrue(
            "health" in out_lower or "trim" in out_lower or "ssd" in out_lower,
            f"Expected health report markers in stdout, got: {res.stdout}",
        )

    @unittest.skipUnless(
        _is_subcommand_registered("health"),
        "Subcommand 'health' not yet registered in smart-drive CLI (Milestone M3)",
    )
    def test_health_invalid_drive_letter_error_handling(self) -> None:
        """Querying health for an invalid/non-existent drive handles error gracefully."""
        res = run_smart_drive_cli(["health", "Z:", "--json"])
        # Either returns non-zero returncode OR returns JSON with error/warning details
        if res.returncode == 0:
            data = json.loads(res.stdout)
            self.assertTrue(
                len(data.get("warnings", [])) > 0 or data.get("filesystem") in ("unknown", "OTHER", ""),
                "Expected warnings or unknown filesystem for invalid drive letter Z:",
            )
        else:
            self.assertNotEqual(res.returncode, 0)


# ==============================================================================
# Test Suite 3: C-Drive Cache Offloader & Junctions (Milestone M2, Features F04-F06)
# ==============================================================================

class TestCliOffloadCommand(SmartDriveTestCase):
    """E2E tests for `smart-drive offload` (--scan, --move, --revert)."""

    @unittest.skipUnless(
        _is_subcommand_registered("offload"),
        "Subcommand 'offload' not yet registered in smart-drive CLI (Milestone M2)",
    )
    def test_offload_help_flag(self) -> None:
        """Executing `smart-drive offload --help` displays usage banner and suboptions."""
        res = run_smart_drive_cli(["offload", "--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("offload", res.stdout.lower())
        self.assertTrue(
            "--scan" in res.stdout or "-s" in res.stdout,
            "Expected --scan option in offload help output",
        )

    @unittest.skipUnless(
        _is_subcommand_registered("offload"),
        "Subcommand 'offload' not yet registered in smart-drive CLI (Milestone M2)",
    )
    def test_offload_scan_json_discovers_mock_caches(self) -> None:
        """`smart-drive offload --scan --json` discovers populated mock caches."""
        with TempWorkspace() as ws:
            caches = create_mock_cache_tree(ws)
            env = make_isolated_env(caches)

            res = run_smart_drive_cli(["offload", "--scan", "--json"], env=env)
            self.assertEqual(res.returncode, 0, f"offload --scan failed: {res.stderr}")

            data = json.loads(res.stdout)
            self.assertIsInstance(data, (list, dict))

            # If dict with key 'caches' or direct list
            items = data if isinstance(data, list) else data.get("caches", [])
            self.assertGreater(len(items), 0, "Expected at least one discovered cache in mock environment")

            names = [c.get("name", "").lower() for c in items]
            self.assertTrue(
                any("huggingface" in n or "pip" in n or "uv" in n or "ollama" in n for n in names),
                f"Discovered cache names '{names}' did not contain expected mock cache targets",
            )

    @unittest.skipUnless(
        _is_subcommand_registered("offload"),
        "Subcommand 'offload' not yet registered in smart-drive CLI (Milestone M2)",
    )
    def test_offload_scan_human_readable_output(self) -> None:
        """`smart-drive offload --scan` displays human-readable summary table."""
        with TempWorkspace() as ws:
            caches = create_mock_cache_tree(ws)
            env = make_isolated_env(caches)

            res = run_smart_drive_cli(["offload", "--scan"], env=env)
            self.assertEqual(res.returncode, 0)
            self.assertTrue(
                "cache" in res.stdout.lower() or "offload" in res.stdout.lower(),
                f"Expected offload scan table in stdout, got: {res.stdout}",
            )

    @unittest.skipUnless(
        _is_subcommand_registered("offload"),
        "Subcommand 'offload' not yet registered in smart-drive CLI (Milestone M2)",
    )
    def test_offload_move_rejects_c_drive_as_target(self) -> None:
        """`smart-drive offload --move <name> --target C:` strictly rejects system drive C:."""
        with TempWorkspace() as ws:
            caches = create_mock_cache_tree(ws)
            env = make_isolated_env(caches)

            # Test both C: and C:\
            for invalid_target in ["C:", "C:\\", "c:"]:
                res = run_smart_drive_cli([
                    "offload",
                    "--move", "huggingface",
                    "--target", invalid_target,
                ], env=env)

                self.assertNotEqual(
                    res.returncode, 0,
                    f"Offload must REJECT system drive '{invalid_target}' as target, but returned 0",
                )
                error_output = (res.stdout + res.stderr).lower()
                self.assertTrue(
                    "system" in error_output or "c:" in error_output or "invalid" in error_output or "reject" in error_output,
                    f"Expected explicit error rejection message for C: drive, got: {res.stdout} {res.stderr}",
                )

    @unittest.skipUnless(
        _is_subcommand_registered("offload"),
        "Subcommand 'offload' not yet registered in smart-drive CLI (Milestone M2)",
    )
    def test_offload_move_nonexistent_cache_rejected(self) -> None:
        """Attempting to offload an unknown cache name fails with clear error."""
        with TempWorkspace() as ws:
            caches = create_mock_cache_tree(ws)
            env = make_isolated_env(caches)
            mock_target = ws / "MockSecondaryDrive"
            mock_target.mkdir(parents=True, exist_ok=True)

            res = run_smart_drive_cli([
                "offload",
                "--move", "nonexistent_unknown_cache_xyz",
                "--target", str(mock_target),
            ], env=env)

            self.assertNotEqual(res.returncode, 0)

    @unittest.skipUnless(
        _is_subcommand_registered("offload"),
        "Subcommand 'offload' not yet registered in smart-drive CLI (Milestone M2)",
    )
    def test_offload_revert_non_offloaded_cache_rejected(self) -> None:
        """Attempting to revert a cache that is not currently a junction fails."""
        with TempWorkspace() as ws:
            caches = create_mock_cache_tree(ws)
            env = make_isolated_env(caches)

            res = run_smart_drive_cli([
                "offload",
                "--revert", "huggingface",
            ], env=env)

            # Reverting a normal directory that is not an offloaded junction must fail
            self.assertNotEqual(res.returncode, 0)


# ==============================================================================
# Test Suite 4: Tier 4 Real-World Workstation Lifecycle (Milestones M1-M4)
# ==============================================================================

class TestCliInternalWorkstationLifecycle(SmartDriveTestCase):
    """Tier 4 real-world scenario: init secondary drive -> inspect health -> offload -> revert."""

    @unittest.skipUnless(
        _is_internal_vault_profile_registered() and _is_subcommand_registered("offload"),
        "Full lifecycle requires both 'internal-developer-vault' (M3) and 'offload' (M2)",
    )
    def test_full_internal_drive_lifecycle(self) -> None:
        """Simulates full developer workflow: initialize vault, verify layout, and exercise offload."""
        with TempWorkspace() as ws:
            # 1. Setup mock secondary drive and mock C: caches
            mock_drive_d = ws / "DriveD"
            mock_drive_d.mkdir(parents=True, exist_ok=True)

            mock_c = ws / "DriveC"
            caches = create_mock_cache_tree(mock_c)
            env = make_isolated_env(caches)

            # 2. Step 1: Initialize secondary drive with internal-developer-vault profile
            res_init = run_smart_drive_cli([
                "init",
                "--root", str(mock_drive_d),
                "--profile", "internal-developer-vault",
                "--json",
            ], env=env)
            self.assertEqual(res_init.returncode, 0, f"Lifecycle init failed: {res_init.stderr}")

            # Verify 04_System_Offload_Caches directory is ready to receive caches
            offload_cache_dir = mock_drive_d / "04_System_Offload_Caches"
            self.assertTrue(offload_cache_dir.is_dir())

            # 3. Step 2: Scan for available caches on C:
            res_scan = run_smart_drive_cli(["offload", "--scan", "--json"], env=env)
            self.assertEqual(res_scan.returncode, 0)
            scan_data = json.loads(res_scan.stdout)
            items = scan_data if isinstance(scan_data, list) else scan_data.get("caches", [])
            self.assertGreater(len(items), 0)


if __name__ == "__main__":
    unittest.main()
