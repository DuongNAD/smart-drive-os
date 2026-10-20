"""tests.test_launchers_and_invariants_tier5 - White-Box Adversarial Stress Suite for Launchers & Core Invariants.

Tier 5 Empirical Challenger Suite:
1. Complete inventory and mirror synchronization of all 20 launcher scripts in `launchers/` and root.
2. Python version check logic boundary validation (Python 3.8 rejection banner vs Python 3.9+ acceptance).
3. Zero hardcoded machine-specific usernames or home directory paths.
4. `PYTHONDONTWRITEBYTECODE=1` cluster slack defense verification and empirical bytecode suppression test.
5. Host isolation verification (`GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, UTF-8).
6. Master menu interactive options [0]-[11] CLI mapping verification and empirical execution.
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
import threading
import time
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
    "Smart_Organize",
]

EXPECTED_EXTENSIONS = [".bat", ".command", ".ps1", ".sh"]

ALL_24_LAUNCHER_NAMES = [
    f"{base}{ext}" for base in EXPECTED_LAUNCHER_BASES for ext in EXPECTED_EXTENSIONS
]
ALL_20_LAUNCHER_NAMES = ALL_24_LAUNCHER_NAMES


class TestLauncherInventoryAndSynchronization(unittest.TestCase):
    """Verifies that all 24 launchers exist in both launchers/ and repository root and are synchronized."""

    def test_launcher_inventory_count_and_presence(self):
        """All 24 launcher scripts must exist in launchers/ and root with non-zero size."""
        self.assertEqual(len(ALL_24_LAUNCHER_NAMES), 24)
        for name in ALL_24_LAUNCHER_NAMES:
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
            # Bytes, not text: the line endings belong to the file (.gitattributes sets them per extension).
            self.assertTrue(
                launcher_path.read_bytes() == root_path.read_bytes(),
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

    def test_powershell_launchers_are_ascii_or_carry_a_bom(self):
        """Windows PowerShell 5.1 reads a script without a BOM in the ANSI code page, where UTF-8 text such as
        a check mark turns into a curly quote that ends a string early, and the whole script fails to parse."""
        for name in ALL_20_LAUNCHER_NAMES:
            if name.endswith(".ps1"):
                for path in (LAUNCHERS_DIR / name, REPO_ROOT / name):
                    raw = path.read_bytes()
                    self.assertTrue(
                        raw.isascii() or raw.startswith(b"\xef\xbb\xbf"),
                        f"{path} must be ASCII or start with a UTF-8 BOM",
                    )

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "needs Windows PowerShell")
    def test_powershell_launchers_parse_in_windows_powershell(self):
        """A parse error in a .ps1 launcher kills the whole script, so Windows PowerShell 5.1 must parse each one."""
        paths = [
            str(path)
            for name in ALL_20_LAUNCHER_NAMES
            if name.endswith(".ps1")
            for path in (LAUNCHERS_DIR / name, REPO_ROOT / name)
        ]
        quoted = ", ".join("'" + path.replace("'", "''") + "'" for path in paths)
        command = (
            f"$bad = 0; foreach ($p in @({quoted})) {{ $errs = $null; $toks = $null; "
            "[void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$toks, [ref]$errs); "
            "foreach ($e in $errs) { Write-Output ($p + ': ' + $e.ToString()); $bad++ } }; exit $bad"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"PowerShell parse errors:\n{proc.stdout}\n{proc.stderr}")


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
        """Verify that every script resolves CODE_DIR for PYTHONPATH/cd without pinning SMART_DRIVE_ROOT to the repo."""
        for name in ALL_20_LAUNCHER_NAMES:
            content = (LAUNCHERS_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn('SMART_DRIVE_ROOT="$DRIVE_ROOT"', content)
            self.assertNotIn('SMART_DRIVE_ROOT="$CODE_DIR"', content)
            self.assertNotIn('SMART_DRIVE_ROOT=%DRIVE_ROOT%', content)
            self.assertNotIn('SMART_DRIVE_ROOT=%CODE_DIR%', content)
            self.assertNotIn('$env:SMART_DRIVE_ROOT = $DriveRoot', content)
            self.assertNotIn('$env:SMART_DRIVE_ROOT = $CODE_DIR', content)
            if name.endswith((".sh", ".command")):
                self.assertIn("smart_drive", content)
                self.assertIn("CODE_DIR", content)
                self.assertIn('cd "$CODE_DIR"', content)
            elif name.endswith(".bat"):
                self.assertIn("smart_drive", content)
                self.assertIn("CODE_DIR", content)
                self.assertIn('cd /d "%CODE_DIR%"', content)
            elif name.endswith(".ps1"):
                self.assertIn("smart_drive", content)
                self.assertIn("CODE_DIR", content)
                self.assertIn("Set-Location $CODE_DIR", content)

    def test_launcher_in_repo_does_not_pin_smart_drive_root_to_repo(self):
        """When a launcher runs inside <drive>/03_Development_Projects/smart-drive-os, SMART_DRIVE_ROOT stays unpinned."""
        temp_drive = Path(tempfile.mkdtemp(prefix="test_launcher_root_"))
        try:
            repo_dir = temp_drive / "03_Development_Projects" / "smart-drive-os"
            pkg_dir = repo_dir / "smart_drive"
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
            (pkg_dir / "__main__.py").write_text(
                "import os\n"
                "print('OBSERVED_SMART_DRIVE_ROOT=' + os.environ.get('SMART_DRIVE_ROOT', ''))\n"
                "print('OBSERVED_CWD=' + os.getcwd())\n",
                encoding="utf-8",
            )

            env = {k: v for k, v in os.environ.items() if k not in ("SMART_DRIVE_ROOT", "KINGSTON_SSD_ROOT")}
            # Ensure sys.executable's directory is first on PATH so python/py is found quickly
            py_dir = str(Path(sys.executable).resolve().parent)
            env["PATH"] = py_dir + os.pathsep + env.get("PATH", "")

            if sys.platform == "win32":
                bat_copy = repo_dir / "Quick_Audit.bat"
                shutil.copy2(LAUNCHERS_DIR / "Quick_Audit.bat", bat_copy)
                proc = subprocess.run(
                    ["cmd.exe", "/c", str(bat_copy)],
                    input="\r\n",
                    text=True,
                    capture_output=True,
                    env=env,
                    cwd=str(temp_drive),
                    timeout=15,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, f"Quick_Audit.bat failed: {proc.stdout}\n{proc.stderr}")
                self.assertIn("OBSERVED_SMART_DRIVE_ROOT=\n", proc.stdout.replace("\r\n", "\n"))

                # Launcher copied to drive root (no smart_drive/ beside it) sets SMART_DRIVE_ROOT to drive root
                root_bat = temp_drive / "Quick_Audit.bat"
                shutil.copy2(LAUNCHERS_DIR / "Quick_Audit.bat", root_bat)
                env_with_pypath = dict(env)
                env_with_pypath["PYTHONPATH"] = str(repo_dir)
                proc_root = subprocess.run(
                    ["cmd.exe", "/c", str(root_bat)],
                    input="\r\n",
                    text=True,
                    capture_output=True,
                    env=env_with_pypath,
                    cwd=str(temp_drive),
                    timeout=15,
                    check=False,
                )
                self.assertEqual(proc_root.returncode, 0, f"Root Quick_Audit.bat failed: {proc_root.stdout}\n{proc_root.stderr}")
                expected_root = os.path.normcase(os.path.abspath(str(temp_drive)).rstrip("\\/"))
                observed_lines = [
                    line.split("=", 1)[1]
                    for line in proc_root.stdout.replace("\r\n", "\n").splitlines()
                    if line.startswith("OBSERVED_SMART_DRIVE_ROOT=")
                ]
                self.assertEqual(len(observed_lines), 1)
                self.assertEqual(os.path.normcase(os.path.abspath(observed_lines[0]).rstrip("\\/")), expected_root)

                # Pre-set SMART_DRIVE_ROOT is preserved without being overwritten
                preset_dir = temp_drive / "preset_custom_root"
                preset_dir.mkdir(exist_ok=True)
                env_preset = dict(env_with_pypath)
                env_preset["SMART_DRIVE_ROOT"] = str(preset_dir)
                proc_preset = subprocess.run(
                    ["cmd.exe", "/c", str(root_bat)],
                    input="\r\n",
                    text=True,
                    capture_output=True,
                    env=env_preset,
                    cwd=str(temp_drive),
                    timeout=15,
                    check=False,
                )
                self.assertEqual(proc_preset.returncode, 0)
                self.assertIn(f"OBSERVED_SMART_DRIVE_ROOT={preset_dir}\n", proc_preset.stdout.replace("\r\n", "\n"))
            else:
                sh_copy = repo_dir / "Quick_Audit.sh"
                shutil.copy2(LAUNCHERS_DIR / "Quick_Audit.sh", sh_copy)
                proc = subprocess.run(
                    ["/bin/bash", str(sh_copy)],
                    input="\n",
                    text=True,
                    capture_output=True,
                    env=env,
                    cwd=str(temp_drive),
                    timeout=15,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, f"Quick_Audit.sh failed: {proc.stdout}\n{proc.stderr}")
                self.assertIn("OBSERVED_SMART_DRIVE_ROOT=\n", proc.stdout)

                # Launcher copied to drive root (no smart_drive/ beside it) sets SMART_DRIVE_ROOT to drive root
                root_sh = temp_drive / "Quick_Audit.sh"
                shutil.copy2(LAUNCHERS_DIR / "Quick_Audit.sh", root_sh)
                env_with_pypath = dict(env)
                env_with_pypath["PYTHONPATH"] = str(repo_dir)
                proc_root = subprocess.run(
                    ["/bin/bash", str(root_sh)],
                    input="\n",
                    text=True,
                    capture_output=True,
                    env=env_with_pypath,
                    cwd=str(temp_drive),
                    timeout=15,
                    check=False,
                )
                self.assertEqual(proc_root.returncode, 0, f"Root Quick_Audit.sh failed: {proc_root.stdout}\n{proc_root.stderr}")
                expected_root = os.path.realpath(str(temp_drive)).rstrip("/")
                observed_lines = [
                    line.split("=", 1)[1]
                    for line in proc_root.stdout.splitlines()
                    if line.startswith("OBSERVED_SMART_DRIVE_ROOT=")
                ]
                self.assertEqual(len(observed_lines), 1)
                self.assertEqual(os.path.realpath(observed_lines[0]).rstrip("/"), expected_root)

                # Pre-set SMART_DRIVE_ROOT is preserved without being overwritten
                preset_dir = temp_drive / "preset_custom_root"
                preset_dir.mkdir(exist_ok=True)
                env_preset = dict(env_with_pypath)
                env_preset["SMART_DRIVE_ROOT"] = str(preset_dir)
                proc_preset = subprocess.run(
                    ["/bin/bash", str(root_sh)],
                    input="\n",
                    text=True,
                    capture_output=True,
                    env=env_preset,
                    cwd=str(temp_drive),
                    timeout=15,
                    check=False,
                )
                self.assertEqual(proc_preset.returncode, 0)
                self.assertIn(f"OBSERVED_SMART_DRIVE_ROOT={preset_dir}\n", proc_preset.stdout)
        finally:
            shutil.rmtree(temp_drive, ignore_errors=True)



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


# Where option [11] (relink the latest move batch) starts and ends in each flavour of SmartDrive, and the pieces
# it must contain in this order: the preview, the [y/N] question, the check of the answer, the relink, the "no" branch.
_SH_OPTION_11 = {
    "start": "        11)\n",
    "end": "        0)\n",
    "preview": '"$PYTHON_CMD" -m smart_drive relink --dry-run',
    "ask": "[y/N]",
    "guard": 'if [ "$CONF_LAT" = "y" ] || [ "$CONF_LAT" = "Y" ]; then',
    "apply": '"$PYTHON_CMD" -m smart_drive relink --wait',
    "aborted": 'echo "Relink aborted."',
}
OPTION_11_LAYOUT = {
    ".bat": {
        "dispatch": 'if "%OPT%"=="11" goto OP_RELINK_LATEST\n',
        "start": ":OP_RELINK_LATEST\n",
        "end": ":OP_EXIT\n",
        "preview": "%PYTHON_CMD% -m smart_drive relink --dry-run",
        "ask": "[y/N]",
        "guard": 'if /i "%CONF_LAT%"=="y" (',
        "apply": "%PYTHON_CMD% -m smart_drive relink --wait",
        "aborted": "echo Relink aborted.",
    },
    ".ps1": {
        "start": '        "11" {\n',
        "end": '        "0" {\n',
        "preview": 'Run-SmartDrive @("relink", "--dry-run")',
        "ask": "[y/N]",
        "guard": 'if ($Confirm -eq "y" -or $Confirm -eq "Y") {',
        "apply": 'Run-SmartDrive @("relink", "--wait")',
        "aborted": 'Write-Host "Relink aborted."',
    },
    ".sh": _SH_OPTION_11,
    ".command": _SH_OPTION_11,
}

FAKE_SMART_DRIVE_MAIN = (
    "import os\n"
    "import sys\n"
    "with open(os.environ['FAKE_SD_LOG'], 'a', encoding='utf-8') as handle:\n"
    "    handle.write(' '.join(sys.argv[1:]) + '\\n')\n"
)


def _make_fake_repo(prefix: str) -> Path:
    """A temp folder holding only a fake `smart_drive` package that records the arguments it is given.

    A launcher copied in here runs `python -m smart_drive ...` against the fake, so a menu option can be
    driven end to end without ever running the real smart-drive (and so without touching real agents).
    """
    repo_dir = Path(tempfile.mkdtemp(prefix=prefix))
    pkg_dir = repo_dir / "smart_drive"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "__main__.py").write_text(FAKE_SMART_DRIVE_MAIN, encoding="utf-8")
    return repo_dir


def _fake_repo_env(log_path: Path) -> dict[str, str]:
    """Environment for a launcher run against `_make_fake_repo`: the fake logs its arguments to `log_path`."""
    env = {k: v for k, v in os.environ.items() if k not in ("SMART_DRIVE_ROOT", "PYTHONPATH")}
    env["FAKE_SD_LOG"] = str(log_path)
    # The folder of this interpreter first, so the launcher's python probe finds a working Python at once.
    env["PATH"] = str(Path(sys.executable).resolve().parent) + os.pathsep + env.get("PATH", "")
    return env


def _logged_calls(log_path: Path) -> list[str]:
    """The argument lines the fake smart_drive wrote so far (see `_make_fake_repo`)."""
    return log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []


def _drive_prompts(command, env, cwd, steps, timeout=90.0):
    """Runs `command`, answering its prompts one at a time. Returns (returncode, stdout, stderr).

    Each step is (wait, text): `wait` is a marker to look for in the output after the previous step, or a function
    that gets the output so far and says whether the program has got far enough; `text` is then typed as it is.
    The answers cannot be piped in at once: cmd.exe takes whatever a pipe holds for the first `set /p` and drops
    the rest, and `pause` takes a single character, so each answer is sent only once its question is on screen.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        bufsize=0,
    )
    stdout_bytes = bytearray()
    stderr_bytes = bytearray()

    def pump(stream, sink):
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            sink.extend(chunk)

    pumps = [
        threading.Thread(target=pump, args=(process.stdout, stdout_bytes), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr_bytes), daemon=True),
    ]
    for thread in pumps:
        thread.start()

    position = 0

    def reached(wait):
        """Where the output stood when `wait` was met, or None while it is not met yet."""
        output = bytes(stdout_bytes)
        if callable(wait):
            return len(output) if wait(output.decode("utf-8", "replace")) else None
        needle = wait.encode("utf-8")
        found = output.find(needle, position)
        return None if found < 0 else found + len(needle)

    deadline = time.monotonic() + timeout
    try:
        for wait, text in steps:
            while True:
                end = reached(wait)
                if end is not None:
                    break
                if process.poll() is not None:
                    for thread in pumps:
                        thread.join(timeout=5)
                    end = reached(wait)
                    if end is not None:
                        break
                    raise AssertionError(f"the program ended before {wait!r} was reached")
                if time.monotonic() > deadline:
                    tail = bytes(stdout_bytes).decode("utf-8", "replace")[-500:]
                    raise AssertionError(f"timed out waiting for {wait!r}; the output ends with {tail!r}")
                time.sleep(0.05)
            position = end
            process.stdin.write(text.encode("utf-8"))
            process.stdin.flush()
        process.stdin.close()
        returncode = process.wait(timeout=max(1.0, deadline - time.monotonic()))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for thread in pumps:
            thread.join(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    return returncode, bytes(stdout_bytes).decode("utf-8", "replace"), bytes(stderr_bytes).decode("utf-8", "replace")


class TestLauncherMasterMenuMapping(unittest.TestCase):
    """Verifies that master menu options [0]-[11] in SmartDrive.* map 1:1 to valid CLI commands."""

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

        # Option 8: relink --find / relink --find --apply --ask --wait
        args_8_find = self.parser.parse_args(["relink", "--find"])
        self.assertEqual(args_8_find.subcommand, "relink")
        self.assertTrue(args_8_find.find)
        self.assertFalse(args_8_find.apply)
        self.assertFalse(args_8_find.ask)

        args_8_apply = self.parser.parse_args(["relink", "--find", "--apply", "--ask", "--wait"])
        self.assertEqual(args_8_apply.subcommand, "relink")
        self.assertTrue(args_8_apply.find)
        self.assertTrue(args_8_apply.apply)
        self.assertTrue(args_8_apply.ask)
        self.assertEqual(args_8_apply.wait, 30.0)

        # Option 9: checkup
        args_9 = self.parser.parse_args(["checkup"])
        self.assertEqual(args_9.subcommand, "checkup")

        # Option 11: relink --dry-run (preview the latest move batch) / relink --wait (re-apply it)
        args_11_preview = self.parser.parse_args(["relink", "--dry-run"])
        self.assertEqual(args_11_preview.subcommand, "relink")
        self.assertTrue(args_11_preview.dry_run)
        self.assertFalse(args_11_preview.find)
        self.assertIsNone(args_11_preview.old)
        self.assertIsNone(args_11_preview.new)

        args_11_apply = self.parser.parse_args(["relink", "--wait"])
        self.assertEqual(args_11_apply.subcommand, "relink")
        self.assertFalse(args_11_apply.dry_run)
        self.assertFalse(args_11_apply.find)
        self.assertIsNone(args_11_apply.old)
        self.assertIsNone(args_11_apply.new)
        self.assertEqual(args_11_apply.wait, 30.0)

    def test_master_menu_option_8_asks_about_each_question_mark_folder(self):
        """Option [8] relinks with --ask, so a ? folder can be confirmed and relinked without leaving the menu."""
        for ext in EXPECTED_EXTENSIONS:
            text = (LAUNCHERS_DIR / f"SmartDrive{ext}").read_text(encoding="utf-8")
            with self.subTest(launcher=f"SmartDrive{ext}"):
                if ext == ".ps1":
                    self.assertIn('Run-SmartDrive @("relink", "--find", "--apply", "--ask", "--wait")', text)
                else:
                    self.assertIn("-m smart_drive relink --find --apply --ask --wait", text)
                self.assertIn("ask about each ? folder? Open coding agents will be waited for. [y/N]", text)

    def test_master_menu_option_11_previews_then_relinks_only_after_a_yes(self):
        """Option [11] shows `relink --dry-run`, asks [y/N], and runs `relink --wait` only in the yes branch."""
        for ext, layout in OPTION_11_LAYOUT.items():
            with self.subTest(launcher=f"SmartDrive{ext}"):
                text = (LAUNCHERS_DIR / f"SmartDrive{ext}").read_text(encoding="utf-8")
                self.assertIn("[11] Relink Coding Agents to the Latest Move (smart-drive relink)", text)
                self.assertIn("Select an option [0-11]", text)
                self.assertNotIn("[0-10]", text)
                if "dispatch" in layout:
                    self.assertIn(layout["dispatch"], text)
                start = text.index(layout["start"])
                block = text[start : text.index(layout["end"], start)]
                steps = ("preview", "ask", "guard", "apply", "aborted")
                positions = [block.index(layout[step]) for step in steps]
                self.assertEqual(positions, sorted(positions), f"option [11] must run in this order: {steps}")
                self.assertEqual(block.count(layout["preview"]), 1)
                self.assertEqual(block.count(layout["apply"]), 1)
                # It re-applies the latest batch: no OLD/NEW, no --find, and --wait holds it until the agents are closed.
                self.assertNotIn("--find", block)
                self.assertNotIn("--apply", block)

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
        self.assertIn("SSD HEALTH & SELF-HEALING AUDIT", proc.stdout)
        self.assertNotIn("KINGSTON XS2000", proc.stdout, "the banner must not name a specific drive model")
        self.assertIn("Exiting SmartDrive-OS. Goodbye!", proc.stdout)

    @unittest.skipIf(sys.platform == "win32", "POSIX interactive bash execution")
    def test_empirical_menu_option_11_asks_before_relinking(self):
        """Drives option [11] of SmartDrive.sh and .command against a fake smart_drive that only logs its arguments."""
        both = ["relink --dry-run", "relink --wait"]
        for ext in (".sh", ".command"):
            for answer, expected_calls in (("y", both), ("Y", both), ("n", both[:1]), ("", both[:1])):
                with self.subTest(launcher=f"SmartDrive{ext}", answer=answer):
                    repo_dir = _make_fake_repo("test_menu_11_")
                    try:
                        log = repo_dir / "calls.log"
                        launcher = repo_dir / f"SmartDrive{ext}"
                        shutil.copy2(LAUNCHERS_DIR / f"SmartDrive{ext}", launcher)
                        proc = subprocess.run(
                            ["/bin/bash", str(launcher)],
                            input=f"11\n{answer}\n\n0\n",
                            text=True,
                            capture_output=True,
                            env=_fake_repo_env(log),
                            cwd=str(repo_dir),
                            timeout=30,
                            check=False,
                        )
                        self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
                        self.assertEqual(_logged_calls(log), expected_calls)
                        self.assertEqual("Relink aborted." in proc.stdout, expected_calls == both[:1])
                    finally:
                        shutil.rmtree(repo_dir, ignore_errors=True)

    @unittest.skipUnless(sys.platform == "win32", "cmd.exe batch execution")
    def test_empirical_batch_menu_option_11_asks_before_relinking(self):
        """Drives option [11] of SmartDrive.bat against a fake smart_drive that only logs its arguments."""
        both = ["relink --dry-run", "relink --wait"]
        for answer, expected_calls in (("y", both), ("n", both[:1]), ("", both[:1])):
            with self.subTest(answer=answer):
                repo_dir = _make_fake_repo("test_menu_11_bat_")
                try:
                    log = repo_dir / "calls.log"
                    launcher = repo_dir / "SmartDrive.bat"
                    shutil.copy2(LAUNCHERS_DIR / "SmartDrive.bat", launcher)

                    def answered(output, log=log):
                        return "Relink aborted." in output or len(_logged_calls(log)) >= 2

                    returncode, stdout, stderr = _drive_prompts(
                        ["cmd.exe", "/c", str(launcher)],
                        _fake_repo_env(log),
                        str(repo_dir),
                        [
                            ("Select an option [0-11]: ", "11\r\n"),
                            ("[y/N]: ", answer + "\r\n"),
                            # Any key ends the `pause` that follows (its own text is in the language of Windows).
                            (answered, "x"),
                            ("Select an option [0-11]: ", "0\r\n"),
                        ],
                    )
                    self.assertEqual(returncode, 0, f"{stdout}\n{stderr}")
                    self.assertEqual(_logged_calls(log), expected_calls)
                    self.assertEqual("Relink aborted." in stdout, expected_calls == both[:1])
                finally:
                    shutil.rmtree(repo_dir, ignore_errors=True)

    def test_powershell_menu_option_0_leaves_the_menu_loop(self):
        """In PowerShell a bare `break` inside `switch` only leaves the switch, so option 0 needs the loop's label."""
        text = (LAUNCHERS_DIR / "SmartDrive.ps1").read_text(encoding="utf-8")
        self.assertIn(":menu while ($true) {", text)
        start = text.index('        "0" {\n')
        block = text[start : text.index("        }\n", start)]
        self.assertIn("break menu", block)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "needs Windows PowerShell")
    def test_empirical_powershell_menu_option_0_exits(self):
        """Selecting 0 in SmartDrive.ps1 ends the script instead of drawing the menu again."""
        repo_dir = _make_fake_repo("test_menu_ps1_exit_")
        try:
            launcher = repo_dir / "SmartDrive.ps1"
            shutil.copy2(LAUNCHERS_DIR / "SmartDrive.ps1", launcher)
            try:
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher)],
                    input="0\n",
                    text=True,
                    capture_output=True,
                    env=_fake_repo_env(repo_dir / "calls.log"),
                    cwd=str(repo_dir),
                    timeout=60,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self.fail("option 0 did not end SmartDrive.ps1: it kept drawing the menu")
            self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
            self.assertEqual(proc.stdout.count("Exiting SmartDrive-OS. Goodbye!"), 1)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)

    def test_batch_menu_escapes_ampersands_in_echo_lines(self):
        """An unescaped & ends an echo and runs the rest of the line as a command ('Presets' is not recognized)."""
        text = (LAUNCHERS_DIR / "SmartDrive.bat").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().lower().startswith("echo"):
                self.assertIsNone(re.search(r"(?<!\^)&", line), f"unescaped & in: {line}")

    @unittest.skipUnless(sys.platform == "win32", "cmd.exe batch execution")
    def test_empirical_batch_menu_draws_whole_lines_and_nothing_on_stderr(self):
        """Drawing the menu of SmartDrive.bat must not run pieces of its own text as commands."""
        repo_dir = _make_fake_repo("test_menu_bat_draw_")
        try:
            launcher = repo_dir / "SmartDrive.bat"
            shutil.copy2(LAUNCHERS_DIR / "SmartDrive.bat", launcher)
            returncode, stdout, stderr = _drive_prompts(
                ["cmd.exe", "/c", str(launcher)],
                _fake_repo_env(repo_dir / "calls.log"),
                str(repo_dir),
                [("Select an option [0-11]: ", "0\r\n")],
            )
            self.assertEqual(returncode, 0, f"{stdout}\n{stderr}")
            self.assertEqual(stderr.strip(), "", "the menu text must not be run as commands")
            for line in (
                "[1] Initialize Drive & Presets (smart-drive init)",
                "[2] Sentinel & Integrity Health Check (smart-drive sentinel)",
                "[4] Storage & Cluster Slack Audit (smart-drive audit)",
                "[6] Auto-Zoning & Anti-Slack Rebalancer (smart-drive organize)",
                "[9] Drive Health Checkup & Score (smart-drive checkup)",
            ):
                self.assertIn(line, stdout)
        finally:
            shutil.rmtree(repo_dir, ignore_errors=True)


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


class TestSmartOrganizeLauncher(unittest.TestCase):
    """T20: Tests for Smart_Organize launchers, i18n strings, SD_SELFTEST=1, and guided workflow."""

    def test_i18n_string_tables_and_language_detection(self) -> None:
        """All i18n keys and format placeholders match between EN and VI, and locale detection follows spec."""
        from smart_drive.cli.smart_organize_runner import I18N_STRINGS, detect_language, t

        en_keys = set(I18N_STRINGS["en"].keys())
        vi_keys = set(I18N_STRINGS["vi"].keys())
        self.assertEqual(en_keys, vi_keys, "EN and VI i18n dictionaries must have identical keys")

        placeholder_re = re.compile(r"\{([a-zA-Z0-9_]+)\}")
        for key in en_keys:
            en_ph = set(placeholder_re.findall(I18N_STRINGS["en"][key]))
            vi_ph = set(placeholder_re.findall(I18N_STRINGS["vi"][key]))
            self.assertEqual(en_ph, vi_ph, f"Placeholder mismatch for i18n key '{key}'")

        # Default is English; Vietnamese when SMARTDRIVE_LANG=vi or system locale is vi-*
        self.assertEqual(detect_language(env={"LANG": "en_US.UTF-8"}, system_locale="en-US"), "en")
        self.assertEqual(detect_language(env={"SMARTDRIVE_LANG": "vi"}, system_locale="en-US"), "vi")
        self.assertEqual(detect_language(env={"SMARTDRIVE_LANG": "en"}, system_locale="vi-VN"), "en")
        self.assertEqual(detect_language(env={}, system_locale="vi-VN"), "vi")
        self.assertEqual(detect_language(env={"LANG": "vi_VN.UTF-8"}), "vi")

        self.assertIn("Smart Organize", t("banner_title", "en"))
        self.assertIn("Dời thư mục an toàn", t("banner_title", "vi"))

    def test_smart_drive_menu_includes_smart_organize_entry(self) -> None:
        """All 4 SmartDrive.* master menu scripts must include an option invoking Smart_Organize."""
        for ext in EXPECTED_EXTENSIONS:
            menu_path = LAUNCHERS_DIR / f"SmartDrive{ext}"
            content = menu_path.read_text(encoding="utf-8")
            self.assertIn("Smart_Organize", content, f"{menu_path} must reference Smart_Organize in its menu")

    def test_sd_selftest_mode_is_readonly_and_writes_log(self) -> None:
        """Running Smart_Organize with SD_SELFTEST=1 performs a read-only dry-run and logs to .smart_drive/smart-organize.log."""
        temp_drive = Path(tempfile.mkdtemp(prefix="test_smart_org_selftest_"))
        try:
            # Create an unorganized repo that would be moved in --apply mode
            repo_dir = temp_drive / "sample_repo"
            (repo_dir / ".git").mkdir(parents=True)
            (repo_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (repo_dir / "main.py").write_text("print('hi')\n", encoding="utf-8")

            env = dict(os.environ)
            env["SMART_DRIVE_ROOT"] = str(temp_drive)
            env["SD_SELFTEST"] = "1"
            env["SMARTDRIVE_LANG"] = "vi"
            py_dir = str(Path(sys.executable).resolve().parent)
            env["PATH"] = py_dir + os.pathsep + env.get("PATH", "")

            if sys.platform == "win32":
                cmd = ["cmd.exe", "/c", str(LAUNCHERS_DIR / "Smart_Organize.bat")]
            else:
                cmd = ["/bin/bash", str(LAUNCHERS_DIR / "Smart_Organize.sh")]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=str(REPO_ROOT),
                timeout=20,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"Smart_Organize selftest failed: {proc.stdout}\n{proc.stderr}")
            self.assertIn("[SELFTEST OK]", proc.stdout)
            self.assertIn("sample_repo", proc.stdout)

            # Verify read-only: sample_repo was NOT moved
            self.assertTrue(repo_dir.is_dir(), "SD_SELFTEST=1 must never move folders")
            self.assertFalse((temp_drive / "03_Development_Projects" / "sample_repo").exists())

            # Verify log file written
            log_file = temp_drive / ".smart_drive" / "smart-organize.log"
            self.assertTrue(log_file.is_file(), "smart-organize.log must be created")
            log_text = log_file.read_text(encoding="utf-8")
            self.assertIn("SELFTEST_OK", log_text)
        finally:
            shutil.rmtree(temp_drive, ignore_errors=True)

    def test_guided_flow_busy_apps_confirm_apply_verify_and_undo(self) -> None:
        """Full guided flow: dry-run -> wait busy_apps -> YES -> apply -> verify -> R retry -> U undo."""
        import io
        from unittest import mock
        from smart_drive.cli.smart_organize_runner import run_smart_organize
        from smart_drive.core.system_refs import SystemRef, SystemRefIndex

        temp_drive = Path(tempfile.mkdtemp(prefix="test_smart_org_flow_"))
        try:
            fake_home = temp_drive / "_fake_home"
            fake_appdata = fake_home / "AppData" / "Roaming"
            fake_home.mkdir(parents=True)
            fake_appdata.mkdir(parents=True)

            # 1. Create a movable repo with .git and .venv
            repo_dir = temp_drive / "my_service"
            (repo_dir / ".git").mkdir(parents=True)
            (repo_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (repo_dir / "pyproject.toml").write_text("[project]\nname='my_service'\n", encoding="utf-8")
            venv_dir = repo_dir / ".venv"
            (venv_dir / "Scripts").mkdir(parents=True)
            (venv_dir / "pyvenv.cfg").write_text("home = C:\\Python311\n", encoding="utf-8")
            (venv_dir / "Scripts" / "activate").write_text("VIRTUAL_ENV=ok\n", encoding="utf-8")

            # 2. Create a system-protected folder (e.g. referenced by PATH) to verify "cannot move" reporting
            prot_dir = temp_drive / "GitTool"
            prot_dir.mkdir()
            (prot_dir / "git.exe").write_bytes(b"MZ")

            fake_ref_idx = SystemRefIndex([
                SystemRef(kind="path", source="PATH (User)", target=str(prot_dir / "git.exe")),
            ])

            # Simulate busy_apps on first check, then clear on second check
            busy_calls = {"n": 0}

            def fake_preview_after_moves(root_p, pairs, tool_name, dry_run=False):
                busy_calls["n"] += 1
                if dry_run and busy_calls["n"] == 1:
                    return {"busy_apps": ["Cursor"], "changed": []}
                return {"busy_apps": [], "changed": []}

            def fake_relink(pairs, dry_run=True, **kwargs):
                return {"busy_apps": [], "changed": []}

            # User inputs:
            # 1) "" (Enter to re-check busy_apps after closing Cursor)
            # 2) "YES" (confirm organize --apply)
            # 3) "R" (retry verification once)
            # 4) "U" (undo the move batch)
            inputs = iter(["", "YES", "R", "U"])
            out_buf = io.StringIO()

            with mock.patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=fake_ref_idx), \
                 mock.patch("smart_drive.core.relink._resolve_locations", return_value=(fake_home, fake_appdata, False)), \
                 mock.patch("smart_drive.core.relink._running_processes", return_value=set()):
                rc = run_smart_organize(
                    str(temp_drive),
                    lang="vi",
                    selftest=False,
                    input_fn=lambda _prompt: next(inputs),
                    output_stream=out_buf,
                    relink_fn=fake_relink,
                    after_moves_fn=fake_preview_after_moves,
                )

            self.assertEqual(rc, 0)
            output = out_buf.getvalue()
            # Protected item and reason shown
            self.assertIn("GitTool", output)
            self.assertIn("PATH", output)
            # Busy app warning shown
            self.assertIn("Cursor", output)
            # Verification checks shown
            self.assertIn("relink --dry-run", output)
            self.assertIn("Git / worktree", output)
            self.assertIn(".venv", output)
            # Undo restored my_service back to root
            self.assertTrue((temp_drive / "my_service").is_dir())
            self.assertFalse((temp_drive / "03_Development_Projects" / "my_service").exists())

            log_text = (temp_drive / ".smart_drive" / "smart-organize.log").read_text(encoding="utf-8")
            self.assertIn("DRY_RUN", log_text)
            self.assertIn("APPLY", log_text)
            self.assertIn("RETRY", log_text)
            self.assertIn("UNDO", log_text)
        finally:
            shutil.rmtree(temp_drive, ignore_errors=True)


class TestSmartOrganizeRetryAndUndo(unittest.TestCase):
    """The guided runner's R (retry) and U (undo) choices act on this run's moves only."""

    def setUp(self) -> None:
        self.drive = Path(tempfile.mkdtemp(prefix="test_smart_org_choices_"))
        self.fake_home = Path(tempfile.mkdtemp(prefix="test_smart_org_home_"))
        (self.fake_home / "AppData" / "Roaming").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.drive, True)
        self.addCleanup(shutil.rmtree, self.fake_home, True)

    def _make_repo(self, name: str) -> Path:
        repo = self.drive / name
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text(f"[project]\nname='{name}'\n", encoding="utf-8")
        return repo

    def _journal(self) -> list:
        import json

        path = self.drive / ".smart_drive" / "moves.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _run_flow(self, answers: list, relink_calls: list = None, lang: str = "en", on_answer=None) -> tuple:
        import io
        from unittest import mock
        from smart_drive.cli.smart_organize_runner import run_smart_organize
        from smart_drive.core.system_refs import SystemRefIndex

        answers_it = iter(answers)

        def fake_relink(pairs, dry_run=True, **kwargs):
            if relink_calls is not None:
                relink_calls.append((list(pairs), dry_run))
            return {"busy_apps": [], "changed": []}

        def input_fn(_prompt: str) -> str:
            answer = next(answers_it)
            if on_answer is not None:
                on_answer(answer)
            return answer

        out = io.StringIO()
        with mock.patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=SystemRefIndex([])),              mock.patch(
                 "smart_drive.core.relink._resolve_locations",
                 return_value=(self.fake_home, self.fake_home / "AppData" / "Roaming", False),
             ),              mock.patch("smart_drive.core.relink._running_processes", return_value=set()):
            rc = run_smart_organize(
                str(self.drive),
                lang=lang,
                input_fn=input_fn,
                output_stream=out,
                relink_fn=fake_relink,
            )
        return rc, out.getvalue()

    def test_retry_only_relinks_and_does_not_journal_the_batch_again(self) -> None:
        self._make_repo("my_service")
        calls: list = []

        rc, out = self._run_flow(["YES", "R", "R", ""], relink_calls=calls)

        self.assertEqual(rc, 0, out)
        journal = self._journal()
        self.assertEqual([rec["tool"] for rec in journal], ["organize"], "retry must not add journal batches")
        retries = [pairs for pairs, dry_run in calls if not dry_run]
        self.assertEqual(len(retries), 2)
        for pairs in retries:
            self.assertEqual(
                [(os.path.basename(src), os.path.relpath(dest, self.drive)) for src, dest in pairs],
                [("my_service", os.path.join("03_Development_Projects", "my_service"))],
            )

    def test_undo_is_refused_when_this_run_moved_nothing(self) -> None:
        from unittest import mock
        from smart_drive.cli.smart_organize_runner import t
        from smart_drive.core.relink import record_moves

        older_old = self.drive / "older.txt"
        older_new = self.drive / "02_Learning_Knowledge" / "Notes" / "older.txt"
        older_new.parent.mkdir(parents=True)
        older_new.write_text("older batch", encoding="utf-8")
        record_moves(str(self.drive), [(str(older_old), str(older_new))], "organize")
        repo = self._make_repo("my_service")

        nothing_moved = {
            "moved": [],
            "moved_count": 0,
            "errors": [{"src": str(repo), "dest": str(self.drive / "x"), "error": "locked"}],
            "total_bytes_moved": 0,
            "total_slack_rebalanced": 0,
        }
        with mock.patch("smart_drive.core.auto_zoner.AutoZoner.apply_plan", return_value=nothing_moved):
            rc, out = self._run_flow(["YES", "U", ""])

        self.assertEqual(rc, 0, out)
        self.assertIn(t("undo_not_this_run", "en"), out)
        self.assertTrue(older_new.is_file(), "an older, unrelated batch must not be reverted")
        self.assertFalse(older_old.exists())
        self.assertTrue(repo.is_dir())
        self.assertEqual(len(self._journal()), 1)

    def test_apply_moves_only_the_plan_the_user_confirmed(self) -> None:
        self._make_repo("my_service")

        def late_arrival(answer: str) -> None:
            # A new folder lands at the drive root while the user is typing YES
            if answer == "YES":
                self._make_repo("late_arrival")

        rc, out = self._run_flow(["YES", ""], on_answer=late_arrival)

        self.assertEqual(rc, 0, out)
        self.assertTrue((self.drive / "03_Development_Projects" / "my_service").is_dir())
        self.assertTrue((self.drive / "late_arrival").is_dir(), "an item the user never saw must not be moved")
        self.assertFalse((self.drive / "03_Development_Projects" / "late_arrival").exists())
        journal = self._journal()
        self.assertEqual(len(journal), 1)
        self.assertEqual([os.path.basename(src) for src, _dest in journal[0]["moves"]], ["my_service"])

    def test_undo_failure_is_reported_and_returns_nonzero(self) -> None:
        from unittest import mock

        self._make_repo("my_service")

        with mock.patch("smart_drive.cli.cmd_undo._move", side_effect=OSError("locked")):
            rc, out = self._run_flow(["YES", "U"])

        self.assertEqual(rc, 1, out)
        self.assertIn("Undo only partly done", out)
        self.assertNotIn("Undo completed", out)
        self.assertTrue((self.drive / "03_Development_Projects" / "my_service").is_dir())
        self.assertFalse((self.drive / "my_service").exists())


if __name__ == "__main__":
    unittest.main()

