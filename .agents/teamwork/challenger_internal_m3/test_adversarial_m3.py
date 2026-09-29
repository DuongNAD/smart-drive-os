"""tests/test_adversarial_m3.py - Adversarial Stress & Empirical Challenge Suite for Milestone M3.

Milestone M3: Internal Profile & SSD TRIM/Health Monitor
Adversarial Challenge Dimensions:
1. Strange Path Formats for Profile Initialization:
   - Bare drive letters ("D:", "d:", "D", "d")
   - Root slashes ("D:\\", "d:\\", "D:/", "d:/")
   - Whitespace padding ("  D:  ", "   d:\\   ", "  D:/  ", "  d  ")
   - Multiple redundant trailing slashes ("D:\\\\\\", "D:////")
   - Directory paths with embedded spaces ("My Developer Vault", "Internal Workstation Root")
   - Paths with Unicode characters ("Ổ_Đĩa_Phụ_2026", "Vault_🚀")
   - Verification of all 6 partitions, 17 subdirs, anti-indexing shields, manifests, FTS5 DB
   - Re-initialization idempotency (force=False vs force=True)
   - Fallback behavior on invalid profile specification

2. Strict Protection Verification for New Internal Partitions:
   - Exact inclusion in PROTECTED_CORE_TAXONOMIES and PROTECTED_ROOT_DIRS:
     - 02_Development_Workspaces
     - 03_Data_Vault
     - 04_System_Offload_Caches
   - is_protected_root_dir() case insensitivity, drive prefixing, leading/trailing slashes
   - PurgeEngine.validate_deletion() raises SecurityViolationError on deletion attempts
   - AutoZoner.classify_root_item() returns None (whitelist guard, never moved)
   - AutoZoner.plan_organize() skips all 3 partitions
   - match_junk_rule() at max_tier (Tier 1, 2, 3) never flags protected partitions
   - User files inside protected directories remain inviolable

3. Adversarial SSD TRIM & Health Monitoring:
   - Malformed drive specifiers ("", "   ", "1:", "invalid", "???")
   - Non-existent / unmounted drive letters ("Z:", "Y:")
   - Mock critical full drive (<5% free space or <10 GB) -> CRITICAL warning
   - Mock low reserve drive (<15% free space) -> WARNING reserve low
   - Mock healthy drive (>15% free space) -> No capacity warnings
   - Mock zero total bytes edge-case -> Zero division immunity
   - Mock TRIM disabled (DisableDeleteNotify = 1) -> trim_enabled=False + warning
   - Mock TRIM enabled (DisableDeleteNotify = 0) -> trim_enabled=True + no warning
   - Mock TRIM error / unsupported strings -> Graceful degradation
   - Mock exFAT large cluster (512KB) -> cluster slack advisory
   - Mock NTFS standard cluster (4KB) -> no slack advisory

4. JSON Output Schema Stability:
   - Invariant schema validation across 10+ varied health states
   - Field presence and type contract verification (9 core keys)
   - CLI cmd_health --json execution
   - CLI cmd_init --json execution
   - Exception fallback schema stability
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from smart_drive.cli.cmd_health import cmd_health
from smart_drive.cli.cmd_init import cmd_init, normalize_drive_path
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import (
    ANTI_INDEXING_MARKERS,
    ANTI_INDEXING_ROOT_FILE,
    JunkTier,
    PROTECTED_CORE_TAXONOMIES,
    PROTECTED_ROOT_DIRS,
    is_protected_root_dir,
    match_junk_rule,
)
from smart_drive.core.drive_detector import (
    DriveDetectorBackend,
    DriveInfo,
    DriveType,
    FilesystemType,
    MockDriveBackend,
    use_mock_backend,
)
from smart_drive.core.health import (
    SSDHealthReport,
    check_drive_health,
    evaluate_health_warnings,
    parse_trim_output,
)
from smart_drive.core.initializer import DriveInitializer, PROFILES
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard, SecurityViolationError


EXPECTED_PARTITIONS_M3 = [
    "01_AI_Models",
    "02_Development_Workspaces",
    "03_Data_Vault",
    "04_System_Offload_Caches",
    "05_Dev_Toolbox",
    "06_Archives_Storage",
]

EXPECTED_SUBDIRS_M3 = [
    "01_AI_Models/checkpoints",
    "01_AI_Models/gguf",
    "01_AI_Models/safetensors",
    "01_AI_Models/onnx",
    "02_Development_Workspaces/active",
    "02_Development_Workspaces/archive",
    "03_Data_Vault/datasets",
    "03_Data_Vault/databases",
    "04_System_Offload_Caches/huggingface",
    "04_System_Offload_Caches/ollama",
    "04_System_Offload_Caches/pip",
    "04_System_Offload_Caches/uv",
    "04_System_Offload_Caches/npm",
    "04_System_Offload_Caches/gradle",
    "05_Dev_Toolbox/scripts",
    "05_Dev_Toolbox/sdks",
    "06_Archives_Storage/backups",
]


class TestAdversarialProfilePathFormats(unittest.TestCase):
    """Stress-tests drive path normalization and workspace initialization under adversarial path inputs."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_adv_init_")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_normalize_drive_path_variations(self) -> None:
        """normalize_drive_path must canonicalize all bare drive and slash variants."""
        cases = [
            ("D:", "D:\\"),
            ("d:", "D:\\"),
            ("D", "D:\\"),
            ("d", "D:\\"),
            ("D:\\", "D:\\"),
            ("d:\\", "D:\\"),
            ("D:/", "D:\\"),
            ("d:/", "D:\\"),
            ("  D:  ", "D:\\"),
            ("   d:\\   ", "D:\\"),
            ("  D:/  ", "D:\\"),
            ("  d  ", "D:\\"),
            ("E:", "E:\\"),
            ("f:/", "F:\\"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(normalize_drive_path(inp), expected)

    def test_normalize_drive_path_redundant_slashes_and_subpaths(self) -> None:
        """Paths with redundant slashes or subpaths normalize to valid abspath locations."""
        redundant_cases = ["D:\\\\\\", "d:////", "D:\\\\"]
        for case in redundant_cases:
            res = normalize_drive_path(case)
            abs_res = os.path.abspath(res)
            self.assertEqual(abs_res.upper(), "D:\\")

    def test_init_in_directory_with_spaces(self) -> None:
        """DriveInitializer must successfully provision all 6 partitions and 17 subdirs in paths with spaces."""
        target = os.path.join(self.temp_dir, "My Internal Workstation Vault 2026")
        init = DriveInitializer(target)
        result = init.initialize(profile="internal-developer-vault")

        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["profile"], "internal-developer-vault")

        for part in EXPECTED_PARTITIONS_M3:
            self.assertTrue(os.path.isdir(os.path.join(target, part)), f"Partition missing: {part}")

        for sdir in EXPECTED_SUBDIRS_M3:
            self.assertTrue(os.path.isdir(os.path.join(target, sdir)), f"Subdir missing: {sdir}")

        # Anti-indexing shields
        self.assertTrue(os.path.isfile(os.path.join(target, ".metadata_never_index")))
        self.assertTrue(os.path.isfile(os.path.join(target, ".fseventsd", "no_log")))

        # Manifests
        self.assertTrue(os.path.isfile(os.path.join(target, "AGENTS.md")))
        self.assertTrue(os.path.isfile(os.path.join(target, "GEMINI.md")))
        self.assertTrue(os.path.isfile(os.path.join(target, "CLAUDE.md")))

        # FTS5 Database
        self.assertTrue(os.path.isfile(os.path.join(target, ".smart_drive", "index.db")))

    def test_init_in_directory_with_trailing_slashes(self) -> None:
        """DriveInitializer handles paths ending in trailing forward and backward slashes."""
        target_trailing = os.path.join(self.temp_dir, "vault_trailing") + "///\\\\\\"
        init = DriveInitializer(target_trailing)
        result = init.initialize(profile="internal-developer-vault")

        canonical_root = os.path.abspath(target_trailing)
        self.assertEqual(result["root"], canonical_root)
        self.assertTrue(os.path.isdir(os.path.join(canonical_root, "04_System_Offload_Caches", "huggingface")))

    def test_init_in_directory_with_unicode_characters(self) -> None:
        """DriveInitializer handles international / Unicode path characters without encoding crashes."""
        target_unicode = os.path.join(self.temp_dir, "Ổ_Đĩa_Phụ_Chuyên_Dụng_2026_🚀")
        init = DriveInitializer(target_unicode)
        result = init.initialize(profile="internal-developer-vault")

        self.assertEqual(result["status"], "initialized")
        self.assertTrue(os.path.isdir(os.path.join(target_unicode, "02_Development_Workspaces", "active")))
        self.assertTrue(os.path.isfile(os.path.join(target_unicode, "AGENTS.md")))

    def test_init_idempotency_manifest_protection(self) -> None:
        """Re-initialization without force preserves existing customized manifests."""
        target = os.path.join(self.temp_dir, "idempotent_vault")
        init = DriveInitializer(target)
        init.initialize(profile="internal-developer-vault")

        # Customize AGENTS.md
        agents_path = os.path.join(target, "AGENTS.md")
        with open(agents_path, "w", encoding="utf-8") as f:
            f.write("# CUSTOM AGENTS MANIFEST - DO NOT OVERWRITE")

        # Second init without force
        res2 = init.initialize(profile="internal-developer-vault", force=False)
        self.assertNotIn("AGENTS.md", res2["manifests"])
        with open(agents_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "# CUSTOM AGENTS MANIFEST - DO NOT OVERWRITE")

        # Third init with force=True overwrites
        res3 = init.initialize(profile="internal-developer-vault", force=True)
        self.assertIn("AGENTS.md", res3["manifests"])
        with open(agents_path, "r", encoding="utf-8") as f:
            self.assertIn("Target: Autonomous AI Coding Agents", f.read())

    def test_init_fallback_on_unrecognized_profile(self) -> None:
        """Specifying an unknown profile name safely falls back to 'general-workspace' without crashing."""
        target = os.path.join(self.temp_dir, "unknown_profile_vault")
        init = DriveInitializer(target)
        result = init.initialize(profile="non-existent-wildcard-profile")

        self.assertEqual(result["status"], "initialized")
        self.assertEqual(result["profile"], "general-workspace")
        self.assertTrue(os.path.isdir(os.path.join(target, "01_AI_Models")))


class TestAdversarialProtectionLists(unittest.TestCase):
    """Stress-tests that the 3 new internal partitions are strictly immune to purge and organize operations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_adv_prot_")
        self.init = DriveInitializer(self.temp_dir)
        self.init.initialize(profile="internal-developer-vault")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_protection_constants_inclusion(self) -> None:
        """The 3 internal partitions must be explicitly present in PROTECTED_CORE_TAXONOMIES and PROTECTED_ROOT_DIRS."""
        new_partitions = [
            "02_Development_Workspaces",
            "03_Data_Vault",
            "04_System_Offload_Caches",
        ]
        for part in new_partitions:
            self.assertIn(part, PROTECTED_CORE_TAXONOMIES)
            self.assertIn(part.lower(), PROTECTED_ROOT_DIRS)

    def test_is_protected_root_dir_variations(self) -> None:
        """is_protected_root_dir must return True across all casings, prefixes, and slash orientations."""
        test_cases = [
            "02_Development_Workspaces",
            "02_DEVELOPMENT_WORKSPACES",
            "02_development_workspaces",
            "03_Data_Vault",
            "03_DATA_VAULT",
            "03_data_vault",
            "04_System_Offload_Caches",
            "04_SYSTEM_OFFLOAD_CACHES",
            "04_system_offload_caches",
            "D:\\02_Development_Workspaces",
            "D:/02_Development_Workspaces/",
            "E:\\03_Data_Vault\\\\\\",
            "  02_Development_Workspaces  ",
            "/03_Data_Vault/",
        ]
        for path in test_cases:
            with self.subTest(path=path):
                self.assertTrue(is_protected_root_dir(path), f"Failed to protect: {path}")

    def test_purge_engine_blocks_partition_deletion(self) -> None:
        """SecurityGuard and PurgeEngine must block deletion when targeting any internal partition."""
        guard = SecurityGuard(self.temp_dir)
        engine = PurgeEngine(self.temp_dir, dry_run=False)
        partitions_to_test = [
            "02_Development_Workspaces",
            "03_Data_Vault",
            "04_System_Offload_Caches",
        ]
        for part in partitions_to_test:
            target_path = os.path.join(self.temp_dir, part)
            with self.assertRaises(SecurityViolationError):
                guard.validate_deletion(target_path)

            # Test deletion through PurgeEngine returns False (blocked)
            success, reason = engine.delete_item(target_path, is_dir=True)
            self.assertFalse(success)
            self.assertIn("Inviolable", reason)

            # Test case variations
            with self.assertRaises(SecurityViolationError):
                guard.validate_deletion(os.path.join(self.temp_dir, part.lower()))
            with self.assertRaises(SecurityViolationError):
                guard.validate_deletion(os.path.join(self.temp_dir, part.upper()))

    def test_purge_engine_blocks_user_files_in_protected_partitions(self) -> None:
        """Normal non-junk files placed inside protected partitions are shielded from deletion."""
        guard = SecurityGuard(self.temp_dir)
        user_file = os.path.join(self.temp_dir, "03_Data_Vault", "datasets", "valuable_dataset.parquet")
        os.makedirs(os.path.dirname(user_file), exist_ok=True)
        with open(user_file, "w", encoding="utf-8") as f:
            f.write("important data")

        is_prot, reason = guard.is_protected(user_file)
        self.assertTrue(is_prot)
        self.assertIn("Inviolable", reason)

        with self.assertRaises(SecurityViolationError):
            guard.validate_deletion(user_file)

    def test_auto_zoner_never_moves_internal_partitions(self) -> None:
        """AutoZoner must treat the 3 internal partitions as inviolable whitelisted roots and never plan moves."""
        zoner = AutoZoner(self.temp_dir)

        # 1. classify_item returns None (no move action planned)
        for part in ["02_Development_Workspaces", "03_Data_Vault", "04_System_Offload_Caches"]:
            part_path = os.path.join(self.temp_dir, part)
            self.assertIsNone(zoner.classify_item(part_path), f"AutoZoner attempted to zone: {part}")

        # 2. generate_plan does not produce any actions affecting these partitions
        actions = zoner.generate_plan()
        action_sources = [a.src_path for a in actions]
        for part in ["02_Development_Workspaces", "03_Data_Vault", "04_System_Offload_Caches"]:
            part_path = os.path.join(self.temp_dir, part)
            self.assertNotIn(part_path, action_sources)

    def test_junk_detector_immunity_at_all_tiers(self) -> None:
        """match_junk_rule must return None for protected partitions across all junk tiers (Tier 1, 2, 3)."""
        partitions = ["02_Development_Workspaces", "03_Data_Vault", "04_System_Offload_Caches"]
        tiers = [JunkTier.TIER_1_SAFE, JunkTier.TIER_2_DEV_CACHE, JunkTier.TIER_3_SENSITIVE]

        for part in partitions:
            for tier in tiers:
                with self.subTest(part=part, tier=tier):
                    res = match_junk_rule(part, is_dir=True, max_tier=tier, is_root_level=True)
                    self.assertIsNone(res, f"Partition '{part}' falsely matched as junk at tier {tier}")


class TestAdversarialHealthMonitoring(unittest.TestCase):
    """Stress-tests SSD health diagnostics: malformed inputs, full drives, disabled TRIM, and geometry."""

    def test_health_malformed_drive_specifiers(self) -> None:
        """check_drive_health must return an invalid-report SSDHealthReport rather than throwing an exception."""
        malformed_inputs = [
            "",
            "   ",
            "1:",
            "Z9:",
            "invalid_drive_specifier",
            "???:",
            "relative/path/without/drive",
        ]
        for spec in malformed_inputs:
            with self.subTest(spec=spec):
                rep = check_drive_health(spec)
                self.assertIsInstance(rep, SSDHealthReport)
                self.assertEqual(rep.filesystem, "unknown")
                self.assertIsNone(rep.trim_enabled)
                self.assertEqual(rep.total_bytes, 0)
                self.assertEqual(rep.free_bytes, 0)
                self.assertGreaterEqual(len(rep.warnings), 1)

    def test_health_unmounted_or_nonexistent_drive(self) -> None:
        """check_drive_health on an unmounted drive letter returns a safe report with a clear warning."""
        mock_backend = MockDriveBackend(
            drives={
                "C:": DriveInfo(
                    drive_letter="C:",
                    mount_point="C:\\",
                    is_system_drive=True,
                    hardware_type=DriveType.FIXED_INTERNAL,
                    filesystem=FilesystemType.NTFS,
                    cluster_size_bytes=4096,
                    total_bytes=500 * (1024 ** 3),
                    free_bytes=200 * (1024 ** 3),
                    volume_label="System",
                )
            }
        )
        rep = check_drive_health("Z:", backend=mock_backend)
        self.assertIsInstance(rep, SSDHealthReport)
        self.assertEqual(rep.drive_letter, "Z:")
        self.assertEqual(rep.filesystem, "unknown")
        self.assertIn("not found or is not mounted", rep.warnings[0])

    def test_health_mock_critical_full_drive(self) -> None:
        """Drive with <5% free space or <10GB free space must produce a CRITICAL warning."""
        # Scenario A: 2% free on a 500GB drive (10GB)
        warnings_a = evaluate_health_warnings(
            free_percent=2.0,
            free_bytes=10 * (1024 ** 3),
            total_bytes=500 * (1024 ** 3),
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertTrue(any("CRITICAL" in w for w in warnings_a), f"Expected CRITICAL warning in: {warnings_a}")

        # Scenario B: 8% free on a 100GB drive (8GB free < 10GB threshold)
        warnings_b = evaluate_health_warnings(
            free_percent=8.0,
            free_bytes=8 * (1024 ** 3),
            total_bytes=100 * (1024 ** 3),
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertTrue(any("CRITICAL" in w for w in warnings_b), f"Expected CRITICAL for <10GB free: {warnings_b}")

    def test_health_mock_low_reserve_warning(self) -> None:
        """Drive with between 5% and 15% free space (and >10GB) must produce a low reserve WARNING."""
        warnings = evaluate_health_warnings(
            free_percent=12.0,
            free_bytes=60 * (1024 ** 3),
            total_bytes=500 * (1024 ** 3),
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertTrue(any("WARNING: Free space is below recommended 15% reserve" in w for w in warnings))
        self.assertFalse(any("CRITICAL" in w for w in warnings))

    def test_health_mock_healthy_capacity_no_warnings(self) -> None:
        """Drive with abundant free space (>15%) produces zero capacity warnings."""
        warnings = evaluate_health_warnings(
            free_percent=65.0,
            free_bytes=650 * (1024 ** 3),
            total_bytes=1000 * (1024 ** 3),
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(len(warnings), 0)

    def test_health_mock_zero_capacity_immunity(self) -> None:
        """Drive with total_bytes=0 causes no ZeroDivisionError and returns safely."""
        warnings = evaluate_health_warnings(
            free_percent=0.0,
            free_bytes=0,
            total_bytes=0,
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(warnings, [])

    def test_trim_parsing_disabled_state(self) -> None:
        """parse_trim_output correctly identifies DisableDeleteNotify = 1 as disabled TRIM."""
        raw_outputs = [
            "NTFS DisableDeleteNotify = 1  (Disables TRIM operations to be sent to the storage device)",
            "DisableDeleteNotify = 1",
            "ReFS DisableDeleteNotify = 1",
            "ntfs disabledeletenotify = 1",
        ]
        for raw in raw_outputs:
            with self.subTest(raw=raw):
                enabled, msg = parse_trim_output(raw)
                self.assertFalse(enabled)
                self.assertIn("TRIM is disabled", msg)

        # Warning evaluation for disabled TRIM
        warnings = evaluate_health_warnings(
            free_percent=50.0,
            free_bytes=500 * (1024 ** 3),
            total_bytes=1000 * (1024 ** 3),
            trim_enabled=False,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertTrue(any("SSD TRIM is disabled" in w for w in warnings))

    def test_trim_parsing_enabled_state(self) -> None:
        """parse_trim_output correctly identifies DisableDeleteNotify = 0 as enabled TRIM."""
        raw_outputs = [
            "NTFS DisableDeleteNotify = 0  (Allows TRIM operations to be sent to the storage device)\nReFS DisableDeleteNotify = 0",
            "DisableDeleteNotify = 0",
            "ntfs disabledeletenotify = 0",
        ]
        for raw in raw_outputs:
            with self.subTest(raw=raw):
                enabled, msg = parse_trim_output(raw)
                self.assertTrue(enabled)
                self.assertIn("TRIM is enabled", msg)

    def test_trim_parsing_unsupported_or_error(self) -> None:
        """parse_trim_output handles error messages and unexpected text gracefully."""
        err_outputs = [
            "The system cannot find the file specified.",
            "'fsutil' is not recognized as an internal or external command.",
            "Error: Access is denied.",
            "",
            "   ",
        ]
        for raw in err_outputs:
            with self.subTest(raw=raw):
                enabled, msg = parse_trim_output(raw)
                self.assertNotEqual(enabled, True)

    def test_health_mock_exfat_large_cluster_slack_notice(self) -> None:
        """exFAT filesystem with >=64KB clusters produces a cluster slack advisory NOTICE."""
        warnings_512k = evaluate_health_warnings(
            free_percent=70.0,
            free_bytes=700 * (1024 ** 3),
            total_bytes=1000 * (1024 ** 3),
            trim_enabled=True,
            filesystem="exFAT",
            cluster_size_bytes=524288,
        )
        self.assertTrue(any("NOTICE: Drive is formatted as exFAT with 512KB clusters" in w for w in warnings_512k))

        warnings_128k = evaluate_health_warnings(
            free_percent=70.0,
            free_bytes=700 * (1024 ** 3),
            total_bytes=1000 * (1024 ** 3),
            trim_enabled=True,
            filesystem="exFAT",
            cluster_size_bytes=131072,
        )
        self.assertTrue(any("NOTICE: Drive is formatted as exFAT with 128KB clusters" in w for w in warnings_128k))

        # exFAT with 32KB clusters should not trigger the large slack notice (<64KB)
        warnings_32k = evaluate_health_warnings(
            free_percent=70.0,
            free_bytes=700 * (1024 ** 3),
            total_bytes=1000 * (1024 ** 3),
            trim_enabled=True,
            filesystem="exFAT",
            cluster_size_bytes=32768,
        )
        self.assertFalse(any("NOTICE" in w for w in warnings_32k))

        # NTFS with 4KB should not trigger notice
        warnings_ntfs = evaluate_health_warnings(
            free_percent=70.0,
            free_bytes=700 * (1024 ** 3),
            total_bytes=1000 * (1024 ** 3),
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(len(warnings_ntfs), 0)


class TestAdversarialJsonSchemaStability(unittest.TestCase):
    """Stress-tests that JSON outputs strictly maintain schema stability across edge cases and commands."""

    MANDATORY_HEALTH_KEYS = {
        "drive_letter": str,
        "filesystem": str,
        "trim_enabled": (bool, type(None)),
        "trim_status_message": str,
        "total_bytes": int,
        "free_bytes": int,
        "free_percent": float,
        "cluster_size_bytes": int,
        "warnings": list,
    }

    def _assert_health_schema_valid(self, d: Dict[str, Any]) -> None:
        """Verifies that all 9 keys exist and match expected types."""
        self.assertEqual(set(d.keys()), set(self.MANDATORY_HEALTH_KEYS.keys()))
        for key, exp_type in self.MANDATORY_HEALTH_KEYS.items():
            val = d[key]
            if isinstance(exp_type, tuple):
                self.assertIsInstance(val, exp_type, f"Field '{key}' has value {val!r} which is not in {exp_type}")
            else:
                self.assertIsInstance(val, exp_type, f"Field '{key}' has value {val!r} of type {type(val)}")
        self.assertIsInstance(d["warnings"], list)
        for w in d["warnings"]:
            self.assertIsInstance(w, str)

    def test_schema_stability_across_mock_scenarios(self) -> None:
        """SSDHealthReport.to_dict() must adhere to identical 9-key schema across diverse test conditions."""
        scenarios = [
            # 1. Healthy NTFS
            SSDHealthReport("D:", "NTFS", True, "TRIM enabled", 1000, 800, 80.0, 4096, []),
            # 2. Critical full drive
            SSDHealthReport("D:", "NTFS", True, "TRIM enabled", 1000, 20, 2.0, 4096, ["CRITICAL: Full"]),
            # 3. TRIM disabled
            SSDHealthReport("D:", "NTFS", False, "TRIM disabled", 1000, 500, 50.0, 4096, ["WARNING: TRIM"]),
            # 4. Large cluster exFAT
            SSDHealthReport("E:", "exFAT", None, "TRIM unknown", 1000, 700, 70.0, 524288, ["NOTICE: exFAT"]),
            # 5. Invalid / unmounted drive
            SSDHealthReport("Z:", "unknown", None, "Not mounted", 0, 0, 0.0, 0, ["Drive not found"]),
            # 6. Zero capacity edge case
            SSDHealthReport("X:", "unknown", None, "Empty", 0, 0, 0.0, 0, []),
        ]
        for rep in scenarios:
            with self.subTest(rep=rep):
                d = rep.to_dict()
                self._assert_health_schema_valid(d)
                # Verify round-trip JSON serialization
                json_str = json.dumps(d)
                loaded = json.loads(json_str)
                self.assertEqual(loaded, d)

    def test_cli_cmd_health_json_output_schema(self) -> None:
        """smart-drive health --json output in stdout parses to JSON conforming to schema."""
        mock_backend = MockDriveBackend(
            drives={
                "D:": DriveInfo(
                    drive_letter="D:",
                    mount_point="D:\\",
                    is_system_drive=False,
                    hardware_type=DriveType.FIXED_INTERNAL,
                    filesystem=FilesystemType.NTFS,
                    cluster_size_bytes=4096,
                    total_bytes=500 * (1024 ** 3),
                    free_bytes=350 * (1024 ** 3),
                    volume_label="Dev_Vault",
                )
            }
        )
        mock_backend.trim_status = {"enabled": True, "message": "TRIM is enabled (DisableDeleteNotify = 0)", "raw": "NTFS DisableDeleteNotify = 0"}
        args = argparse.Namespace(drive="D:", root=None, json=True)

        buf = io.StringIO()
        with patch("sys.stdout", buf), use_mock_backend(mock_backend):
            ret = cmd_health(args)

        self.assertEqual(ret, 0)
        output_str = buf.getvalue().strip()
        data = json.loads(output_str)
        self._assert_health_schema_valid(data)
        self.assertEqual(data["drive_letter"], "D:")
        self.assertEqual(data["filesystem"], "NTFS")
        self.assertTrue(data["trim_enabled"])

    def test_cli_cmd_init_json_output_schema(self) -> None:
        """smart-drive init --profile internal-developer-vault --json outputs valid JSON with required schema."""
        temp_dir = tempfile.mkdtemp(prefix="sd_adv_init_json_")
        try:
            args = argparse.Namespace(
                path=temp_dir,
                root=None,
                profile="internal-developer-vault",
                force=False,
                json=True,
            )
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                ret = cmd_init(args)

            self.assertEqual(ret, 0)
            data = json.loads(buf.getvalue().strip())

            expected_keys = {
                "status",
                "root",
                "profile",
                "created_dirs",
                "shields",
                "manifests",
                "database_initialized",
                "db_path",
            }
            self.assertEqual(set(data.keys()), expected_keys)
            self.assertEqual(data["status"], "initialized")
            self.assertEqual(data["profile"], "internal-developer-vault")
            self.assertTrue(data["database_initialized"])
            self.assertIsInstance(data["created_dirs"], list)
            self.assertIsInstance(data["shields"], dict)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
