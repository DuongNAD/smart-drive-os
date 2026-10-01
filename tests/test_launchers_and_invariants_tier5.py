"""tests.test_launchers_and_invariants_tier5 - White-Box Adversarial Stress Suite for Launchers & Core Invariants.

Tier 5 Empirical Challenger Suite:
1. Complete inventory and mirror synchronization of all 20 launcher scripts in `launchers/` and root.
2. Python version check logic boundary validation (Python 3.8 rejection banner vs Python 3.9+ acceptance).
3. Zero hardcoded machine-specific usernames or home directory paths.
4. `PYTHONDONTWRITEBYTECODE=1` cluster slack defense verification and empirical bytecode suppression test.
5. Host isolation verification (`GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, UTF-8).
6. Master menu interactive options [0]-[8] CLI mapping verification and empirical execution.
7. exFAT cluster geometry invariants (512KB allocation unit, cluster boundary math, edge cases).
8. Anti-indexing shields verification and auto-healing lifecycle.
9. Whitelist immutability for `AGENTS.md` and `GEMINI.md` across SecurityGuard, PurgeEngine, JunkDetector, and MCP.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar, Pattern

from smart_drive.cli.main import build_parser
from smart_drive.core.config import (
    ANTI_INDEXING_FSEVENT_DIR,
    ANTI_INDEXING_FSEVENT_FILE,
    ANTI_INDEXING_MARKERS,
    ANTI_INDEXING_ROOT_FILE,
    CLUSTER_SIZE,
    CLUSTER_SIZE_BYTES,
    CLUSTER_SIZE_KB,
    PROTECTED_CORE_TAXONOMIES,
    PROTECTED_ROOT_DIRS,
    PROTECTED_ROOT_FILES,
    SECTOR_SIZE,
    SECTORS_PER_CLUSTER,
    STANDARD_TAXONOMIES,
    calculate_allocated_bytes,
    calculate_cluster_count,
    calculate_slack_bytes,
    calculate_slack_percentage,
    ensure_anti_indexing_markers,
    is_protected_root_dir,
    is_protected_root_file,
    verify_anti_indexing_markers,
)
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import (
    PurgeEngine,
    SecurityGuard,
    SecurityViolationError,
)
from smart_drive.mcp.server import SmartDriveMCPServer

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHERS_DIR = REPO_ROOT / "launchers"

EXPECTED_LAUNCHER_BASES = [
    "Quick_Audit",
    "Quick_Clean",
    "Quick_Search",
    "Setup_SSD",
    "SmartDrive",
]

EXPECTED_EXTENSIONS = [".bat", ".command", ".ps1", ".sh"]

ALL_20_LAUNCHER_NAMES = [
    f"{base}{ext}" for base in EXPECTED_LAUNCHER_BASES for ext in EXPECTED_EXTENSIONS
]


class TestLauncherInventoryAndSynchronization(unittest.TestCase):
    """Verifies that all 20 launchers exist in both launchers/ and repository root and are synchronized."""

    def test_launcher_inventory_count_and_presence(self):
        """All 20 launcher scripts must exist in launchers/ and root with non-zero size."""
        self.assertEqual(len(ALL_20_LAUNCHER_NAMES), 20)
        for name in ALL_20_LAUNCHER_NAMES:
            launcher_path = LAUNCHERS_DIR / name
            root_path = REPO_ROOT / name
            self.assertTrue(launcher_path.is_file(), f"Missing launcher script: {launcher_path}")
            self.assertTrue(root_path.is_file(), f"Missing root launcher script: {root_path}")
            self.assertGreater(launcher_path.stat().st_size, 0, f"Empty launcher script: {launcher_path}")
            self.assertGreater(root_path.stat().st_size, 0, f"Empty root launcher script: {root_path}")

    def test_root_and_launcher_files_content_identical(self):
        """Root scripts and launchers/ scripts must be byte-for-byte identical in content."""
        for name in ALL_20_LAUNCHER_NAMES:
            launcher_path = LAUNCHERS_DIR / name
            root_path = REPO_ROOT / name
            launcher_content = launcher_path.read_text(encoding="utf-8")
            root_content = root_path.read_text(encoding="utf-8")
            self.assertEqual(
                launcher_content,
                root_content,
                f"Mismatch between {launcher_path} and {root_path}",
            )

    def test_launcher_script_headers_and_entrypoints(self):
        """Shell scripts must have valid shebangs, bat scripts have @echo off, ps1 have UTF-8 declarations."""
        for name in ALL_20_LAUNCHER_NAMES:
            content = (LAUNCHERS_DIR / name).read_text(encoding="utf-8")
            if name.endswith((".sh", ".command")):
                self.assertTrue(
                    content.startswith("#!/bin/bash"),
                    f"Shell script {name} must start with #!/bin/bash",
                )
            elif name.endswith(".bat"):
                self.assertTrue(
                    content.startswith("@echo off"),
                    f"Batch script {name} must start with @echo off",
                )
            elif name.endswith(".ps1"):
                self.assertIn(
                    "[System.Text.Encoding]::UTF8",
                    content,
                    f"PowerShell script {name} must declare UTF-8 console output",
                )


class TestLauncherSecurityAndHardcodedPaths(unittest.TestCase):
    """Adversarial audit verifying the absence of machine-specific usernames or hardcoded paths."""

    FORBIDDEN_PATH_PATTERNS: ClassVar[list[Pattern[str]]] = [
        re.compile(r"/Users/[a-zA-Z0-9_\-]+", re.IGNORECASE),
        re.compile(r"C:\\Users\\[a-zA-Z0-9_\-]+", re.IGNORECASE),
        re.compile(r"/home/[a-zA-Z0-9_\-]+", re.IGNORECASE),
        re.compile(r"duongnad", re.IGNORECASE),
        re.compile(r"/private/var", re.IGNORECASE),
    ]

    def test_zero_machine_specific_paths_in_all_launchers(self):
        """Ensure no launcher contains machine-specific absolute paths or developer usernames."""
        for name in ALL_20_LAUNCHER_NAMES:
            for path in [LAUNCHERS_DIR / name, REPO_ROOT / name]:
                content = path.read_text(encoding="utf-8")
                for pattern in self.FORBIDDEN_PATH_PATTERNS:
                    match = pattern.search(content)
                    self.assertIsNone(
                        match,
                        f"Found forbidden hardcoded machine path '{match.group(0) if match else ''}' in {path}",
                    )

    def test_portable_root_resolution_logic(self):
        """Verify that every script dynamically resolves its DRIVE_ROOT based on smart_drive directory location."""
        for name in ALL_20_LAUNCHER_NAMES:
            content = (LAUNCHERS_DIR / name).read_text(encoding="utf-8")
            if name.endswith((".sh", ".command")):
                self.assertIn("smart_drive", content)
                self.assertIn("DRIVE_ROOT", content)
                self.assertIn('cd "$DRIVE_ROOT"', content)
            elif name.endswith(".bat"):
                self.assertIn("smart_drive", content)
                self.assertIn("DRIVE_ROOT", content)
                self.assertIn('cd /d "%DRIVE_ROOT%"', content)
            elif name.endswith(".ps1"):
                self.assertIn("smart_drive", content)
                self.assertIn("DriveRoot", content)
                self.assertIn("Set-Location $DriveRoot", content)


class TestLauncherPythonVersionCheck(unittest.TestCase):
    """Adversarial stress-testing of Python 3.9+ version check logic."""

    def test_python_version_tuple_boundary_math(self):
        """Verify the exact condition `sys.version_info >= (3, 9)` across boundary values."""
        check_version = lambda v: v >= (3, 9)

        # Versions that MUST be rejected (< 3.9)
        self.assertFalse(check_version((2, 7, 18)))
        self.assertFalse(check_version((3, 5, 0)))
        self.assertFalse(check_version((3, 6, 12)))
        self.assertFalse(check_version((3, 7, 9)))
        self.assertFalse(check_version((3, 8, 0)))
        self.assertFalse(check_version((3, 8, 18)))

        # Versions that MUST be accepted (>= 3.9)
        self.assertTrue(check_version((3, 9, 0)))
        self.assertTrue(check_version((3, 9, 13)))
        self.assertTrue(check_version((3, 10, 0)))
        self.assertTrue(check_version((3, 11, 8)))
        self.assertTrue(check_version((3, 12, 2)))
        self.assertTrue(check_version((3, 13, 0)))

    def test_all_launchers_declare_python_39_minimum_check(self):
        """Every launcher must execute `sys.version_info >= (3, 9)` and print an error banner when missing."""
        for name in ALL_20_LAUNCHER_NAMES:
            content = (LAUNCHERS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("sys.version_info >= (3, 9)", content, f"Missing Python 3.9 version check in {name}")
            self.assertIn(
                "[ERROR] Python 3.9+ was not found on system PATH!",
                content,
                f"Missing error banner in {name}",
            )

    @unittest.skipIf(sys.platform == "win32", "POSIX mock path execution")
    def test_empirical_bash_launcher_python_38_rejection(self):
        """Empirically verify that when PATH only contains Python <= 3.8, launchers exit with code 1 and error banner."""
        temp_bin = tempfile.mkdtemp(prefix="mock_py38_bin_")
        try:
            # Create a mock python3 that fails the version check
            mock_py = Path(temp_bin) / "python3"
            mock_py.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            mock_py.chmod(mock_py.stat().st_mode | stat.S_IXUSR)

            # Link python and py to the mock
            (Path(temp_bin) / "python").symlink_to(mock_py)
            (Path(temp_bin) / "py").symlink_to(mock_py)

            env = {
                "PATH": f"{temp_bin}:/usr/bin:/bin",
            }

            test_targets = [
                LAUNCHERS_DIR / "Quick_Audit.sh",
                LAUNCHERS_DIR / "SmartDrive.sh",
                REPO_ROOT / "Quick_Clean.sh",
            ]

            for script in test_targets:
                proc = subprocess.run(
                    ["/bin/bash", str(script)],
                    input="\n",
                    text=True,
                    capture_output=True,
                    env=env,
                    cwd=str(REPO_ROOT),
                    check=False,
                )
                self.assertEqual(
                    proc.returncode,
                    1,
                    f"Expected exit code 1 on Python 3.8 failure for {script}, got {proc.returncode}",
                )
                self.assertIn("[ERROR] Python 3.9+ was not found on system PATH!", proc.stdout)
                self.assertIn("SmartDrive-OS requires Python 3.9 or higher.", proc.stdout)
        finally:
            shutil.rmtree(temp_bin, ignore_errors=True)


class TestLauncherBytecodeAndSlackDefense(unittest.TestCase):
    """Stress-tests the PYTHONDONTWRITEBYTECODE=1 invariant protecting exFAT from 512KB cluster slack."""

    def test_all_launchers_export_pythondontwritebytecode(self):
        """Every single launcher across launchers/ and root must set PYTHONDONTWRITEBYTECODE=1."""
        for name in ALL_20_LAUNCHER_NAMES:
            content = (LAUNCHERS_DIR / name).read_text(encoding="utf-8")
            self.assertIn(
                "PYTHONDONTWRITEBYTECODE",
                content,
                f"Missing PYTHONDONTWRITEBYTECODE in {name}",
            )
            self.assertTrue(
                re.search(r"PYTHONDONTWRITEBYTECODE[\"'\s=]+1", content),
                f"PYTHONDONTWRITEBYTECODE not set to 1 in {name}",
            )

    def test_empirical_bytecode_suppression_prevents_pyc_creation(self):
        """Empirically prove that Python invoked with PYTHONDONTWRITEBYTECODE=1 generates NO .pyc files."""
        temp_dir = tempfile.mkdtemp(prefix="test_bytecode_slack_")
        try:
            # Create a dummy module to import
            dummy_module = Path(temp_dir) / "slack_test_module.py"
            dummy_module.write_text("x = 42\ndef compute(): return x * 2\n", encoding="utf-8")

            # Run Python with PYTHONDONTWRITEBYTECODE=1
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONPATH"] = temp_dir

            proc = subprocess.run(
                [sys.executable, "-c", "import slack_test_module; assert slack_test_module.compute() == 84"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"Import failed: {proc.stderr}")

            # Check that no __pycache__ or .pyc exists in temp_dir
            pycache_dir = Path(temp_dir) / "__pycache__"
            pyc_files = list(Path(temp_dir).rglob("*.pyc"))

            self.assertFalse(pycache_dir.exists(), "__pycache__ directory was created despite PYTHONDONTWRITEBYTECODE=1")
            self.assertEqual(len(pyc_files), 0, f"Found .pyc files created: {pyc_files}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestLauncherHostIsolation(unittest.TestCase):
    """Verifies that launchers isolate execution from personal host profiles and unauthenticated Git prompts."""

    def test_all_launchers_export_git_isolation_and_utf8(self):
        """Every launcher must set GIT_TERMINAL_PROMPT=0, GIT_CONFIG_NOSYSTEM=1, and UTF-8 environment."""
        for name in ALL_20_LAUNCHER_NAMES:
            content = (LAUNCHERS_DIR / name).read_text(encoding="utf-8")
            self.assertIn("GIT_TERMINAL_PROMPT", content, f"Missing GIT_TERMINAL_PROMPT in {name}")
            self.assertTrue(
                re.search(r"GIT_TERMINAL_PROMPT[\"'\s=]+0", content),
                f"GIT_TERMINAL_PROMPT not set to 0 in {name}",
            )
            self.assertIn("GIT_CONFIG_NOSYSTEM", content, f"Missing GIT_CONFIG_NOSYSTEM in {name}")
            self.assertTrue(
                re.search(r"GIT_CONFIG_NOSYSTEM[\"'\s=]+1", content),
                f"GIT_CONFIG_NOSYSTEM not set to 1 in {name}",
            )
            self.assertIn("PYTHONIOENCODING", content, f"Missing PYTHONIOENCODING in {name}")
            self.assertIn("PYTHONUTF8", content, f"Missing PYTHONUTF8 in {name}")

    def test_git_terminal_prompt_zero_fails_fast_on_missing_credentials(self):
        """Verify that when GIT_TERMINAL_PROMPT=0 is active, Git terminates immediately without hanging."""
        git_executable = shutil.which("git")
        if not git_executable:
            self.skipTest("git executable not found on PATH")

        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"

        proc = subprocess.run(
            [git_executable, "ls-remote", "https://localhost:9999/non_existent_repo.git"],
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)


class TestLauncherMasterMenuMapping(unittest.TestCase):
    """Verifies that master menu options [0]-[8] in SmartDrive.* map 1:1 to valid CLI commands."""

    def setUp(self):
        self.parser = build_parser()

    def test_master_menu_subcommands_validity(self):
        """Verify all subcommands invoked by SmartDrive.* options are accepted by build_parser()."""
        # Option 1: init --profile
        args_1 = self.parser.parse_args(["init", "--profile", "general-workspace"])
        self.assertEqual(args_1.subcommand, "init")
        self.assertEqual(args_1.profile, "general-workspace")

        args_1_ai = self.parser.parse_args(["init", "--profile", "ai-developer"])
        self.assertEqual(args_1_ai.profile, "ai-developer")

        args_1_ds = self.parser.parse_args(["init", "--profile", "data-science"])
        self.assertEqual(args_1_ds.profile, "data-science")

        # Option 2: sentinel
        args_2 = self.parser.parse_args(["sentinel"])
        self.assertEqual(args_2.subcommand, "sentinel")

        # Option 3: search "<query>"
        args_3 = self.parser.parse_args(["search", "llama ext:gguf"])
        self.assertEqual(args_3.subcommand, "search")
        self.assertEqual(args_3.query, "llama ext:gguf")

        # Option 4: audit
        args_4 = self.parser.parse_args(["audit"])
        self.assertEqual(args_4.subcommand, "audit")

        # Option 5: clean --dry-run / clean --apply
        args_5_dry = self.parser.parse_args(["clean", "--dry-run"])
        self.assertEqual(args_5_dry.subcommand, "clean")
        self.assertTrue(args_5_dry.dry_run)

        args_5_apply = self.parser.parse_args(["clean", "--apply"])
        self.assertEqual(args_5_apply.subcommand, "clean")
        self.assertTrue(args_5_apply.apply)

        # Option 6: organize / organize --apply
        args_6 = self.parser.parse_args(["organize"])
        self.assertEqual(args_6.subcommand, "organize")

        args_6_apply = self.parser.parse_args(["organize", "--apply"])
        self.assertEqual(args_6_apply.subcommand, "organize")
        self.assertTrue(args_6_apply.apply)

        # Option 7: mcp register / mcp-config
        args_7_mcp = self.parser.parse_args(["mcp", "register"])
        self.assertEqual(args_7_mcp.subcommand, "mcp")
        self.assertEqual(args_7_mcp.action, "register")

        args_7_cfg = self.parser.parse_args(["mcp-config"])
        self.assertEqual(args_7_cfg.subcommand, "mcp-config")

        # Option 8: ui
        args_8 = self.parser.parse_args(["ui"])
        self.assertEqual(args_8.subcommand, "ui")

    @unittest.skipIf(sys.platform == "win32", "POSIX interactive bash execution")
    def test_empirical_master_menu_loop_option_0_exit(self):
        """Empirically test that selecting '0' in SmartDrive.sh exits cleanly with code 0."""
        proc = subprocess.run(
            ["/bin/bash", str(LAUNCHERS_DIR / "SmartDrive.sh")],
            input="0\n",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=5,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Exiting SmartDrive-OS. Goodbye!", proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "POSIX interactive bash execution")
    def test_empirical_master_menu_loop_option_2_sentinel_and_exit(self):
        """Empirically test that selecting '2' runs sentinel, returns to menu, and exits on '0'."""
        proc = subprocess.run(
            ["/bin/bash", str(LAUNCHERS_DIR / "SmartDrive.sh")],
            input="2\n\n0\n",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("KINGSTON XS2000 SSD HEALTH", proc.stdout)
        self.assertIn("Exiting SmartDrive-OS. Goodbye!", proc.stdout)


class TestExfatClusterGeometryInvariants(unittest.TestCase):
    """Stress tests on core exFAT 512KB cluster constants and mathematical invariants."""

    def test_cluster_size_constants_values_and_relations(self):
        """Verify cluster size constants across configuration."""
        self.assertEqual(CLUSTER_SIZE_BYTES, 524_288)
        self.assertEqual(CLUSTER_SIZE_KB, 512)
        self.assertEqual(CLUSTER_SIZE, 524_288)
        self.assertEqual(SECTOR_SIZE, 512)
        self.assertEqual(SECTORS_PER_CLUSTER, 1024)
        self.assertEqual(SECTOR_SIZE * SECTORS_PER_CLUSTER, CLUSTER_SIZE_BYTES)

    def test_zero_byte_file_cluster_allocation_invariant(self):
        """0-byte files in exFAT consume 0 clusters (0 physical bytes)."""
        self.assertEqual(calculate_allocated_bytes(0), 0)
        self.assertEqual(calculate_slack_bytes(0), 0)
        self.assertEqual(calculate_cluster_count(0), 0)
        self.assertEqual(calculate_slack_percentage(0), 0.0)

    def test_single_byte_file_cluster_slack_invariant(self):
        """A 1-byte file consumes an entire 512KB cluster (524,287 bytes wasted)."""
        self.assertEqual(calculate_allocated_bytes(1), 524_288)
        self.assertEqual(calculate_slack_bytes(1), 524_287)
        self.assertEqual(calculate_cluster_count(1), 1)
        slack_pct = calculate_slack_percentage(1)
        self.assertAlmostEqual(slack_pct, (524_287 / 524_288) * 100, places=4)
        self.assertGreater(slack_pct, 99.99)

    def test_exact_cluster_boundary_invariants(self):
        """Files exactly on cluster boundaries have 0 slack and 0.0% waste."""
        # 1 exact cluster: 524,288 bytes
        self.assertEqual(calculate_allocated_bytes(524_288), 524_288)
        self.assertEqual(calculate_slack_bytes(524_288), 0)
        self.assertEqual(calculate_cluster_count(524_288), 1)
        self.assertEqual(calculate_slack_percentage(524_288), 0.0)

        # 2 exact clusters: 1,048,576 bytes
        self.assertEqual(calculate_allocated_bytes(1_048_576), 1_048_576)
        self.assertEqual(calculate_slack_bytes(1_048_576), 0)
        self.assertEqual(calculate_cluster_count(1_048_576), 2)
        self.assertEqual(calculate_slack_percentage(1_048_576), 0.0)

    def test_boundary_plus_one_byte_multi_cluster_allocation(self):
        """524,289 bytes (1 byte into second cluster) must allocate 2 clusters."""
        self.assertEqual(calculate_allocated_bytes(524_289), 1_048_576)
        self.assertEqual(calculate_slack_bytes(524_289), 524_287)
        self.assertEqual(calculate_cluster_count(524_289), 2)
        slack_pct = calculate_slack_percentage(524_289)
        self.assertAlmostEqual(slack_pct, (524_287 / 1_048_576) * 100, places=4)
        self.assertAlmostEqual(slack_pct, 50.0, places=1)

    def test_negative_size_and_invalid_cluster_size_exceptions(self):
        """Negative sizes or invalid cluster sizes must raise ValueError."""
        with self.assertRaises(ValueError):
            calculate_allocated_bytes(-1)
        with self.assertRaises(ValueError):
            calculate_slack_bytes(-100)
        with self.assertRaises(ValueError):
            calculate_cluster_count(-5)
        with self.assertRaises(ValueError):
            calculate_allocated_bytes(100, cluster_size=0)
        with self.assertRaises(ValueError):
            calculate_allocated_bytes(100, cluster_size=-512)


class TestAntiIndexingShields(unittest.TestCase):
    """Stress-tests anti-indexing shields lifecycle and protection against search indexing and purge."""

    def setUp(self):
        self.temp_root = tempfile.mkdtemp(prefix="test_anti_index_")

    def tearDown(self):
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_shield_constants(self):
        """Verify standard anti-indexing filenames."""
        self.assertEqual(ANTI_INDEXING_ROOT_FILE, ".metadata_never_index")
        self.assertEqual(ANTI_INDEXING_FSEVENT_DIR, ".fseventsd")
        self.assertEqual(ANTI_INDEXING_FSEVENT_FILE, ".fseventsd/no_log")
        self.assertIn(ANTI_INDEXING_ROOT_FILE, ANTI_INDEXING_MARKERS)
        self.assertIn(ANTI_INDEXING_FSEVENT_FILE, ANTI_INDEXING_MARKERS)

    def test_anti_indexing_verification_and_healing_lifecycle(self):
        """Verify shields detection when absent, healing, and presence verification."""
        # Step 1: In a brand new empty directory, both shields are absent
        status_before = verify_anti_indexing_markers(self.temp_root)
        self.assertFalse(status_before[ANTI_INDEXING_ROOT_FILE])
        self.assertFalse(status_before[ANTI_INDEXING_FSEVENT_FILE])

        # Step 2: Ensure markers creates both
        created = ensure_anti_indexing_markers(self.temp_root)
        self.assertIn(ANTI_INDEXING_ROOT_FILE, created)
        self.assertIn(ANTI_INDEXING_FSEVENT_FILE, created)

        # Step 3: Verify both markers now exist on disk
        status_after = verify_anti_indexing_markers(self.temp_root)
        self.assertTrue(status_after[ANTI_INDEXING_ROOT_FILE])
        self.assertTrue(status_after[ANTI_INDEXING_FSEVENT_FILE])

        # Step 4: Idempotency check: calling again creates nothing new
        created_again = ensure_anti_indexing_markers(self.temp_root)
        self.assertEqual(created_again, [])

    def test_anti_indexing_markers_protected_from_purge(self):
        """Verify that SecurityGuard and PurgeEngine will not delete anti-indexing markers."""
        ensure_anti_indexing_markers(self.temp_root)
        guard = SecurityGuard(self.temp_root)

        root_marker = os.path.join(self.temp_root, ANTI_INDEXING_ROOT_FILE)
        fsevent_marker = os.path.join(self.temp_root, ANTI_INDEXING_FSEVENT_FILE)

        is_prot_1, reason_1 = guard.is_protected(root_marker)
        self.assertTrue(is_prot_1, "Root anti-indexing marker must be protected")
        self.assertIn("Anti-indexing marker", reason_1)

        is_prot_2, reason_2 = guard.is_protected(fsevent_marker)
        self.assertTrue(is_prot_2, ".fseventsd/no_log marker must be protected")
        self.assertIn("Anti-indexing marker", reason_2)

        with self.assertRaises(SecurityViolationError):
            guard.validate_deletion(root_marker)

        with self.assertRaises(SecurityViolationError):
            guard.validate_deletion(fsevent_marker)


class TestWhitelistImmutabilityAgentsAndGemini(unittest.TestCase):
    """Stress-tests the inviolable whitelist protection for AGENTS.md, GEMINI.md, and standard taxonomies."""

    def setUp(self):
        self.temp_root = tempfile.mkdtemp(prefix="test_whitelist_")
        # Create mock root files
        self.agents_file = Path(self.temp_root) / "AGENTS.md"
        self.agents_file.write_text("# Manifest for Autonomous AI Coding Agents\n", encoding="utf-8")

        self.gemini_file = Path(self.temp_root) / "GEMINI.md"
        self.gemini_file.write_text("# Directives for Gemini / Antigravity\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_agents_and_gemini_in_protected_root_files(self):
        """AGENTS.md and GEMINI.md must be present in PROTECTED_ROOT_FILES (casefolded)."""
        self.assertIn("agents.md", PROTECTED_ROOT_FILES)
        self.assertIn("gemini.md", PROTECTED_ROOT_FILES)

    def test_is_protected_root_file_case_insensitivity(self):
        """is_protected_root_file must recognize AGENTS.md and GEMINI.md across all case variants."""
        cases = [
            "AGENTS.md",
            "agents.md",
            "Agents.Md",
            "AGENTS.MD",
            "GEMINI.md",
            "gemini.md",
            "Gemini.Md",
            "GEMINI.MD",
        ]
        for c in cases:
            self.assertTrue(is_protected_root_file(c), f"Failed to identify protected file: {c}")

    def test_security_guard_blocks_deletion_of_agents_and_gemini(self):
        """SecurityGuard must raise SecurityViolationError when attempting deletion of AGENTS.md or GEMINI.md."""
        guard = SecurityGuard(self.temp_root)

        is_prot, reason = guard.is_protected(str(self.agents_file))
        self.assertTrue(is_prot)
        self.assertIn("Inviolable root file", reason)

        is_prot, reason = guard.is_protected(str(self.gemini_file))
        self.assertTrue(is_prot)
        self.assertIn("Inviolable root file", reason)

        with self.assertRaises(SecurityViolationError):
            guard.validate_deletion(str(self.agents_file))

        with self.assertRaises(SecurityViolationError):
            guard.validate_deletion(str(self.gemini_file))

    def test_purge_engine_refuses_to_delete_agents_and_gemini(self):
        """PurgeEngine delete_file and delete_item must not delete AGENTS.md or GEMINI.md."""
        engine = PurgeEngine(self.temp_root, dry_run=False)

        # Attempt to delete AGENTS.md directly
        record_agents = engine.delete_file(str(self.agents_file))
        self.assertEqual(record_agents.status, "BLOCKED")
        self.assertIn("Inviolable root file", record_agents.reason)
        self.assertTrue(self.agents_file.exists(), "AGENTS.md was deleted despite protection!")

        ok, reason = engine.delete_item(str(self.agents_file))
        self.assertFalse(ok)
        self.assertIn("Inviolable root file", reason)
        self.assertTrue(self.agents_file.exists())

        # Attempt to delete GEMINI.md directly
        record_gemini = engine.delete_file(str(self.gemini_file))
        self.assertEqual(record_gemini.status, "BLOCKED")
        self.assertIn("Inviolable root file", record_gemini.reason)
        self.assertTrue(self.gemini_file.exists(), "GEMINI.md was deleted despite protection!")

        ok, reason = engine.delete_item(str(self.gemini_file))
        self.assertFalse(ok)
        self.assertIn("Inviolable root file", reason)
        self.assertTrue(self.gemini_file.exists())

    def test_junk_detector_never_flags_agents_or_gemini_as_junk(self):
        """JunkDetector must never flag AGENTS.md or GEMINI.md as junk under any tier."""
        detector = JunkDetector(self.temp_root)
        findings = detector.find_junk()
        found_paths = [f.rel_path for f in findings]

        self.assertNotIn("AGENTS.md", found_paths)
        self.assertNotIn("GEMINI.md", found_paths)
        self.assertNotIn("agents.md", found_paths)
        self.assertNotIn("gemini.md", found_paths)

    def test_mcp_check_safety_flags_agents_and_gemini_as_protected(self):
        """MCP handle_ssd_check_safety must declare is_safe=False and is_protected_root_file=True."""
        server = SmartDriveMCPServer(self.temp_root)

        res_agents = server.handle_ssd_check_safety({"path": "AGENTS.md"})
        self.assertFalse(res_agents["is_safe"])
        self.assertTrue(res_agents["is_protected_root_file"])

        res_gemini = server.handle_ssd_check_safety({"path": "GEMINI.md"})
        self.assertFalse(res_gemini["is_safe"])
        self.assertTrue(res_gemini["is_protected_root_file"])

    def test_standard_taxonomies_protection(self):
        """All 6 standard taxonomies must be recognized by is_protected_root_dir and blocked from deletion."""
        guard = SecurityGuard(self.temp_root)
        for tax in STANDARD_TAXONOMIES:
            self.assertTrue(is_protected_root_dir(tax), f"Failed to identify protected taxonomy dir: {tax}")
            self.assertIn(tax, PROTECTED_CORE_TAXONOMIES)
            self.assertIn(tax.lower(), PROTECTED_ROOT_DIRS)

            tax_path = Path(self.temp_root) / tax
            tax_path.mkdir(exist_ok=True)
            is_prot, _ = guard.is_protected(str(tax_path))
            self.assertTrue(is_prot, f"Taxonomy {tax} was not protected by SecurityGuard")
            with self.assertRaises(SecurityViolationError):
                guard.validate_deletion(str(tax_path))


if __name__ == "__main__":
    unittest.main()
