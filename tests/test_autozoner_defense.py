"""tests/test_autozoner_defense.py - Comprehensive Unit Tests for AutoZoner Self-Defense,
Windows Services/Apps Protection, Compound Paths, and Scanner Quiet Errors.

100% Python Standard Library unittest.
Tests:
- Inviolable execution root self-defense in AutoZoner (running code, repo root, package, ancestors).
- Prevention of self-relocation disaster scenario when running from drive root.
- Protection of Windows system dirs (WindowsApps, WpSystem, DeliveryOptimization, etc.).
- Protection of game/app libraries (SteamLibrary, Riot Games, LDPlayer, SQL2022, Downloads, 32837, fo4).
- Protection of active database services (DBI202_VuPT\\MSSQL16.MSSQLSERVER, MSSQLSERVER, MSSQL).
- Compound path resolution in is_protected_root_dir.
- FastDirectoryScanner pre-probe directory exclusion (zero syscalls on excluded entries).
- FastDirectoryScanner quiet permission handling (DEBUG vs WARNING log levels).
- SecurityGuard integration blocking deletion of protected apps and services.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.auto_zoner import (
    AutoZoner,
    ZoningAction,
    _CURRENT_FILE,
    _SMART_DRIVE_CORE_DIR,
    _SMART_DRIVE_PKG_DIR,
    _SMART_DRIVE_REPO_DIR,
)
from smart_drive.core.config import (
    DEFAULT_EXCLUDE_DIRS,
    PROTECTED_ROOT_DIRS,
    is_protected_root_dir,
)
from smart_drive.core.purge_engine import SecurityGuard, SecurityViolationError
from smart_drive.core.scanner import FastDirectoryScanner, ScanError, ScanOptions
from tests.helpers import SmartDriveTestCase


class TestAutoZonerSelfDefense(SmartDriveTestCase):
    """Test inviolable self-defense mechanics preventing AutoZoner from relocating SmartDrive-OS."""

    def test_self_defense_anchors_and_collections(self) -> None:
        """AutoZoner initializes execution anchors and protected names correctly."""
        zoner = AutoZoner(str(self.test_dir))

        self.assertIn(_CURRENT_FILE, zoner._self_protected_paths)
        self.assertIn(_SMART_DRIVE_CORE_DIR, zoner._self_protected_paths)
        self.assertIn(_SMART_DRIVE_PKG_DIR, zoner._self_protected_paths)
        self.assertIn(_SMART_DRIVE_REPO_DIR, zoner._self_protected_paths)

        # Ancestors of running code must be tracked
        for parent in _CURRENT_FILE.parents:
            self.assertIn(parent, zoner._self_ancestors)

        # Protected names must include repo and package names
        self.assertIn("smart-drive-os", zoner._self_protected_names)
        self.assertIn("smart_drive_os", zoner._self_protected_names)
        self.assertIn("smart_drive", zoner._self_protected_names)
        self.assertIn("smart_drive_manager", zoner._self_protected_names)

    def test_classify_item_rejects_running_code_and_repo_root(self) -> None:
        """classify_item returns None for the executing code, package, repo root, and ancestors."""
        zoner = AutoZoner(str(self.test_dir))

        # Direct execution file
        self.assertIsNone(zoner.classify_item(str(_CURRENT_FILE)))
        # Core directory
        self.assertIsNone(zoner.classify_item(str(_SMART_DRIVE_CORE_DIR)))
        # Package directory
        self.assertIsNone(zoner.classify_item(str(_SMART_DRIVE_PKG_DIR)))
        # Repo root
        self.assertIsNone(zoner.classify_item(str(_SMART_DRIVE_REPO_DIR)))
        # Direct parent/ancestor of repo root
        self.assertIsNone(zoner.classify_item(str(_SMART_DRIVE_REPO_DIR.parent)))

    def test_classify_item_rejects_mock_repo_with_project_indicators(self) -> None:
        """A directory named smart-drive-os containing git and pyproject is never classified."""
        mock_repo = self.test_dir / "smart-drive-os"
        mock_repo.mkdir()
        (mock_repo / ".git").mkdir()
        (mock_repo / "pyproject.toml").write_text("[project]\nname = 'smart-drive-os'\n", encoding="utf-8")
        (mock_repo / "smart_drive").mkdir()

        zoner = AutoZoner(str(self.test_dir))
        res = zoner.classify_item(str(mock_repo))
        self.assertIsNone(res, "AutoZoner classified smart-drive-os repo directory instead of defending it!")

    def test_classify_item_rejects_tool_name_variants(self) -> None:
        """All variations of SmartDrive tool names are defended from classification."""
        zoner = AutoZoner(str(self.test_dir))

        for name in ("smart-drive-os", "smart_drive_os", "smart_drive", "smart_drive_manager"):
            target = self.test_dir / name
            target.mkdir()
            (target / "package.json").write_text("{}", encoding="utf-8")
            self.assertIsNone(zoner.classify_item(str(target)), f"Failed to defend {name}")

    def test_generate_plan_excludes_smart_drive_os(self) -> None:
        """generate_plan never includes smart-drive-os directory in zoning actions."""
        mock_repo = self.test_dir / "smart-drive-os"
        mock_repo.mkdir()
        (mock_repo / "pyproject.toml").write_text("name='test'", encoding="utf-8")

        # Also add a genuine loose file that SHOULD be zoned
        loose_file = self.test_dir / "tutorial.pdf"
        loose_file.write_bytes(b"%PDF mock")

        zoner = AutoZoner(str(self.test_dir))
        plan = zoner.generate_plan()

        src_paths = [a.src_path for a in plan]
        self.assertNotIn(str(mock_repo), src_paths)
        self.assertIn(str(loose_file), src_paths)

    def test_apply_plan_defends_against_adversarial_action(self) -> None:
        """apply_plan re-verifies actions and refuses to relocate running repo or package."""
        mock_repo = self.test_dir / "smart-drive-os"
        mock_repo.mkdir()
        canary = mock_repo / "canary.txt"
        canary.write_text("must survive", encoding="utf-8")

        zoner = AutoZoner(str(self.test_dir))

        # Adversarially constructed action attempting to relocate the mock repo
        hostile_action = ZoningAction(
            src_path=str(mock_repo),
            dest_path=str(self.test_dir / "03_Development_Projects" / "smart-drive-os"),
            target_taxonomy="03_Development_Projects",
            reason="Hostile relocation attempt",
            is_dir=True,
            nominal_size=1024,
            slack_bytes=524288,
        )

        # Hostile action attempting to relocate real running file
        hostile_code_action = ZoningAction(
            src_path=str(_CURRENT_FILE),
            dest_path=str(self.test_dir / "05_Dev_Toolbox" / "auto_zoner.py"),
            target_taxonomy="05_Dev_Toolbox",
            reason="Hostile code relocation",
            is_dir=False,
            nominal_size=1024,
            slack_bytes=524288,
        )

        res = zoner.apply_plan([hostile_action, hostile_code_action])
        self.assertEqual(res["moved_count"], 0)
        self.assertTrue(mock_repo.exists(), "smart-drive-os mock repo was moved!")
        self.assertTrue(canary.exists(), "canary file was moved!")
        self.assertTrue(_CURRENT_FILE.exists(), "running code was moved!")


class TestWindowsAppsAndServicesProtection(SmartDriveTestCase):
    """Test protection of Windows system directories, game libraries, and database services."""

    PROTECTED_SYSTEM_DIRS = [
        "WindowsApps",
        "WpSystem",
        "DeliveryOptimization",
        "WUDownloadCache",
        "Program Files",
        "Program Files (x86)",
        "$Recycle.Bin",
        "System Volume Information",
    ]

    PROTECTED_GAME_APP_DIRS = [
        "SteamLibrary",
        "Riot Games",
        "LDPlayer",
        "SQL2022",
        "Downloads",
        "32837",
        "fo4",
    ]

    PROTECTED_DATABASE_SERVICES = [
        "MSSQL16.MSSQLSERVER",
        "MSSQLSERVER",
        "MSSQL",
        "DBI202_VuPT/MSSQL16.MSSQLSERVER",
        "DBI202_VuPT\\MSSQL16.MSSQLSERVER",
    ]

    def test_is_protected_root_dir_for_all_targets(self) -> None:
        """Every target system, game, app, and service directory evaluates to True."""
        all_targets = (
            self.PROTECTED_SYSTEM_DIRS
            + self.PROTECTED_GAME_APP_DIRS
            + self.PROTECTED_DATABASE_SERVICES
        )
        for target in all_targets:
            self.assertTrue(
                is_protected_root_dir(target),
                f"Failed is_protected_root_dir for {target}",
            )
            # Check lowercase variant
            self.assertTrue(
                is_protected_root_dir(target.lower()),
                f"Failed is_protected_root_dir for lowercase {target.lower()}",
            )
            # Check Windows drive-prefixed variant
            self.assertTrue(
                is_protected_root_dir(f"D:\\{target}"),
                f"Failed is_protected_root_dir for D:\\{target}",
            )

    def test_classify_item_blocks_all_protected_targets(self) -> None:
        """classify_item returns None for all protected system, game, and database folders."""
        zoner = AutoZoner(str(self.test_dir))

        all_targets = (
            self.PROTECTED_SYSTEM_DIRS
            + self.PROTECTED_GAME_APP_DIRS
            + ["MSSQL16.MSSQLSERVER", "MSSQLSERVER", "SQL2022"]
        )

        for target in all_targets:
            target_path = self.test_dir / target
            target_path.mkdir(parents=True, exist_ok=True)
            # Add an indicator that would normally trigger project classification
            (target_path / "package.json").write_text("{}", encoding="utf-8")

            res = zoner.classify_item(str(target_path))
            self.assertIsNone(res, f"classify_item did not protect {target}")

    def test_generate_plan_ignores_protected_directories(self) -> None:
        """generate_plan creates zero actions for protected apps and system folders."""
        for name in ("WindowsApps", "SteamLibrary", "Riot Games", "SQL2022", "32837", "fo4"):
            d = self.test_dir / name
            d.mkdir()
            (d / "binary.bin").write_bytes(b"\x00" * 100)

        zoner = AutoZoner(str(self.test_dir))
        plan = zoner.generate_plan()
        self.assertEqual(len(plan), 0, f"Expected 0 actions, got {[a.src_path for a in plan]}")

    def test_security_guard_enforces_protection_on_all_targets(self) -> None:
        """SecurityGuard detects protected entities and raises SecurityViolationError on deletion."""
        guard = SecurityGuard(str(self.test_dir))

        test_cases = [
            "WindowsApps",
            "SteamLibrary",
            "Riot Games",
            "LDPlayer",
            "SQL2022",
            "32837",
            "fo4",
            "MSSQL16.MSSQLSERVER",
            "MSSQLSERVER",
            "smart-drive-os",
        ]

        for item in test_cases:
            target_path = self.test_dir / item
            target_path.mkdir(parents=True, exist_ok=True)

            is_prot, reason = guard.is_protected(str(target_path))
            self.assertTrue(is_prot, f"SecurityGuard failed to mark {item} as protected: {reason}")

            with self.assertRaises(SecurityViolationError, msg=f"SecurityGuard allowed deletion of {item}"):
                guard.validate_deletion(str(target_path))


class TestCompoundPathProtection(SmartDriveTestCase):
    """Test resolution of compound and nested paths in config and scanner."""

    def test_compound_database_path_resolution(self) -> None:
        """DBI202_VuPT/MSSQL16.MSSQLSERVER is recognized with slashes and backslashes."""
        self.assertTrue(is_protected_root_dir("DBI202_VuPT/MSSQL16.MSSQLSERVER"))
        self.assertTrue(is_protected_root_dir("DBI202_VuPT\\MSSQL16.MSSQLSERVER"))
        self.assertTrue(is_protected_root_dir("D:/DBI202_VuPT/MSSQL16.MSSQLSERVER"))
        self.assertTrue(is_protected_root_dir("D:\\DBI202_VuPT\\MSSQL16.MSSQLSERVER"))

    def test_taxonomy_inner_items_not_protected_as_root(self) -> None:
        """Items inside standard taxonomies are NOT protected root directories."""
        self.assertFalse(is_protected_root_dir("01_AI_Models/llama3.gguf"))
        self.assertFalse(is_protected_root_dir("02_Learning_Knowledge/notes.pdf"))
        self.assertFalse(is_protected_root_dir("03_Development_Projects/web_app"))
        self.assertFalse(is_protected_root_dir("03_Development_Projects/web_app/package.json"))

    def test_config_sets_completeness(self) -> None:
        """PROTECTED_ROOT_DIRS and DEFAULT_EXCLUDE_DIRS contain all mandatory items."""
        required = [
            "windowsapps", "wpsystem", "deliveryoptimization", "wudownloadcache",
            "program files", "program files (x86)", "$recycle.bin", "system volume information",
            "steamlibrary", "riot games", "ldplayer", "sql2022", "downloads", "32837", "fo4",
            "mssql16.mssqlserver", "mssqlserver", "mssql",
            "smart-drive-os", "smart_drive_os", "smart_drive", "smart_drive_manager",
        ]
        for item in required:
            self.assertIn(item, PROTECTED_ROOT_DIRS, f"Missing {item} from PROTECTED_ROOT_DIRS")

        required_excludes = [
            "WindowsApps", "WpSystem", "DeliveryOptimization", "WUDownloadCache",
            "Program Files", "Program Files (x86)", "$Recycle.Bin", "System Volume Information",
            "SteamLibrary", "Riot Games", "LDPlayer", "SQL2022", "Downloads", "32837", "fo4",
            "MSSQL16.MSSQLSERVER", "MSSQLSERVER", "MSSQL",
            "smart-drive-os", "smart_drive_os", "smart_drive", "smart_drive_manager",
        ]
        exclude_lower = {d.lower() for d in DEFAULT_EXCLUDE_DIRS}
        for item in required_excludes:
            self.assertIn(item.lower(), exclude_lower, f"Missing {item} from DEFAULT_EXCLUDE_DIRS")


class TestScannerDefenseAndQuietErrors(SmartDriveTestCase):
    """Test FastDirectoryScanner pre-probe exclusion and quiet permission handling."""

    def test_scanner_pre_probe_exclusion_zero_syscalls(self) -> None:
        """Scanner prunes excluded directories immediately without calling is_symlink or is_dir."""
        root = self.test_dir / "scanner_probe_test"
        root.mkdir()

        # Create excluded system directories
        (root / "WindowsApps").mkdir()
        (root / "SteamLibrary").mkdir()
        # Create normal directory and file
        normal_dir = root / "NormalDir"
        normal_dir.mkdir()
        (normal_dir / "file.txt").write_text("content", encoding="utf-8")

        scanner = FastDirectoryScanner(str(root))

        # We will wrap os.DirEntry probes to ensure excluded items are not probed
        probed_entries = []
        orig_is_symlink = os.DirEntry.is_symlink

        def recording_is_symlink(entry_self):
            probed_entries.append(entry_self.name)
            return orig_is_symlink(entry_self)

        with patch.object(os.DirEntry, "is_symlink", recording_is_symlink):
            entries = list(scanner.scan_iter())

        names = {e.name for e in entries}
        self.assertIn("NormalDir", names)
        self.assertIn("file.txt", names)
        self.assertNotIn("WindowsApps", names)
        self.assertNotIn("SteamLibrary", names)

        # Confirm is_symlink was NEVER called for WindowsApps or SteamLibrary
        self.assertNotIn("WindowsApps", probed_entries, "is_symlink was called on excluded WindowsApps!")
        self.assertNotIn("SteamLibrary", probed_entries, "is_symlink was called on excluded SteamLibrary!")

    def test_scanner_compound_path_exclusion(self) -> None:
        """Scanner supports compound path segment exclusions like DBI202_VuPT/MSSQL16.MSSQLSERVER."""
        root = self.test_dir / "compound_test"
        root.mkdir()

        course_dir = root / "DBI202_VuPT"
        course_dir.mkdir()
        (course_dir / "lecture.pdf").write_bytes(b"%PDF mock")

        sql_dir = course_dir / "MSSQL16.MSSQLSERVER"
        sql_dir.mkdir()
        (sql_dir / "master.mdf").write_bytes(b"\x00" * 1024)

        scanner = FastDirectoryScanner(str(root))
        entries = list(scanner.scan_iter())
        rel_paths = {e.rel_path for e in entries}

        self.assertIn("DBI202_VuPT", rel_paths)
        self.assertIn("DBI202_VuPT/lecture.pdf", rel_paths)
        # Database directory and its contents must be excluded
        self.assertNotIn("DBI202_VuPT/MSSQL16.MSSQLSERVER", rel_paths)
        self.assertNotIn("DBI202_VuPT/MSSQL16.MSSQLSERVER/master.mdf", rel_paths)

    def test_scanner_quiet_permission_handling(self) -> None:
        """PermissionError on system/proprietary directories is logged at DEBUG, not WARNING."""
        scanner = FastDirectoryScanner(str(self.test_dir))

        with self.assertLogs("smart_drive.core.scanner", level=logging.DEBUG) as log_ctx:
            # 1. PermissionError on WindowsApps
            scanner._record_error("D:\\WindowsApps", "PERMISSION_DENIED", PermissionError("Access is denied"))
            # 2. PermissionError on SQL Server log
            scanner._record_error("D:\\DBI202_VuPT\\MSSQL16.MSSQLSERVER\\log.ldf", "PERMISSION_DENIED", PermissionError("Locked"))
            # 3. STAT_FAILED with PermissionError
            scanner._record_error("D:\\System Volume Information", "STAT_FAILED", PermissionError("Access is denied"))

            # 4. Genuine warning: FileNotFoundError on regular file
            scanner._record_error("D:\\data\\missing.txt", "NOT_FOUND", FileNotFoundError("No such file"))

        # Inspect logged records
        debug_msgs = [r.getMessage() for r in log_ctx.records if r.levelno == logging.DEBUG]
        warning_msgs = [r.getMessage() for r in log_ctx.records if r.levelno == logging.WARNING]

        self.assertEqual(len(warning_msgs), 1, f"Expected exactly 1 warning, got: {warning_msgs}")
        self.assertIn("missing.txt", warning_msgs[0])

        self.assertEqual(len(debug_msgs), 3, f"Expected 3 debug logs, got: {debug_msgs}")
        self.assertTrue(any("WindowsApps" in msg for msg in debug_msgs))
        self.assertTrue(any("MSSQL16.MSSQLSERVER" in msg for msg in debug_msgs))
        self.assertTrue(any("System Volume Information" in msg for msg in debug_msgs))

        # Check stats and error recording
        self.assertEqual(scanner.stats.error_count, 4)
        self.assertEqual(len(scanner.errors), 4)


if __name__ == "__main__":
    unittest.main()
