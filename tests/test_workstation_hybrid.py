"""tests.test_workstation_hybrid - Verification for Workstation Hybrid & AcademicClassifier.

Tests:
1. AcademicClassifier regex course codes (DBI202, WED201c, SWE202c, PRJ301, OSG, etc.).
2. Vietnamese semester and mojibake font sanitization (h?c k? ➔ Hoc_Ky, k 1 ➔ Ky_1).
3. AutoZoner integration: correctly maps FPTU courses into 02_Learning_Knowledge/FPTU/.
4. AutoZoner self-defense: never moves smart-drive-os directory or protected system/game/SQL folders.
5. Workstation-hybrid profile in DriveInitializer.
6. self-path-check CLI execution.
7. Safe home directory fallback in registrar on empty environment.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.core.academic_classifier import (
    AcademicClassifier,
    sanitize_folder_name,
    COURSE_CODE_PATTERN,
)
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import is_protected_root_dir, DEFAULT_EXCLUDE_DIRS
from smart_drive.core.initializer import PROFILES, DriveInitializer
from smart_drive.cli.cmd_path_check import check_system_path
from smart_drive.mcp.registrar import _safe_home_dir


class TestAcademicClassifier(unittest.TestCase):
    """Verifies academic course extraction and mojibake sanitization."""

    def test_course_code_regex(self) -> None:
        self.assertIsNotNone(COURSE_CODE_PATTERN.search("DBI202_VuPT"))
        self.assertIsNotNone(COURSE_CODE_PATTERN.search("wed201c"))
        self.assertIsNotNone(COURSE_CODE_PATTERN.search("PE_WED201c_SP26"))
        self.assertIsNotNone(COURSE_CODE_PATTERN.search("SWE202c"))
        self.assertIsNotNone(COURSE_CODE_PATTERN.search("PRJ301"))
        self.assertIsNotNone(COURSE_CODE_PATTERN.search("LAB1_sp26"))

    def test_sanitize_folder_name_mojibake(self) -> None:
        self.assertEqual(sanitize_folder_name("h?c k? 3 fptu"), "Hoc_Ky_3_fptu")
        self.assertEqual(sanitize_folder_name("k 1 fptu"), "Ky_1_fptu")
        self.assertEqual(sanitize_folder_name("n luy?n pe dbi202"), "On_Luyen_pe_dbi202")

    def test_classify_fptu_course_folders(self) -> None:
        res1 = AcademicClassifier.classify_folder("DBI202_VuPT")
        self.assertIsNotNone(res1)
        tax1, sub1, reason1 = res1
        self.assertEqual(tax1, "02_Learning_Knowledge")
        self.assertIn("FPTU", sub1)
        self.assertIn("DBI202", sub1)

        res2 = AcademicClassifier.classify_folder("h?c k? 3 fptu")
        self.assertIsNotNone(res2)
        tax2, sub2, _ = res2
        self.assertEqual(tax2, "02_Learning_Knowledge")
        self.assertIn("Ky_3", sub2)

        res3 = AcademicClassifier.classify_folder("FPTU_VuPTHE204339_HK4")
        self.assertIsNotNone(res3)
        self.assertIn("Ky_4", res3[1])

        res4 = AcademicClassifier.classify_folder("testjava")
        self.assertIsNotNone(res4)
        self.assertIn("Java_Labs", res4[1])

    def test_classify_workstation_special_folders(self) -> None:
        res_dich = AcademicClassifier.classify_folder("dich truyen")
        self.assertIsNotNone(res_dich)
        self.assertEqual(res_dich[0], "02_Learning_Knowledge")
        self.assertIn("Personal_Books", res_dich[1])

        res_lenovo = AcademicClassifier.classify_folder("LENOVO")
        self.assertIsNotNone(res_lenovo)
        self.assertEqual(res_lenovo[0], "05_Dev_Toolbox")
        self.assertIn("OEM_Drivers", res_lenovo[1])

        res_sql = AcademicClassifier.classify_folder("SQL2022")
        self.assertIsNotNone(res_sql)
        self.assertEqual(res_sql[0], "05_Dev_Toolbox")
        self.assertIn("Installers", res_sql[1])


class TestAutoZonerSelfDefenseAndProtectedDirs(unittest.TestCase):
    """Verifies that AutoZoner never moves protected items or SmartDrive-OS itself."""

    def test_protected_root_dirs_expansion(self) -> None:
        self.assertTrue(is_protected_root_dir("WindowsApps"))
        self.assertTrue(is_protected_root_dir("WpSystem"))
        self.assertTrue(is_protected_root_dir("SteamLibrary"))
        self.assertTrue(is_protected_root_dir("Riot Games"))
        self.assertTrue(is_protected_root_dir("LDPlayer"))
        self.assertTrue(is_protected_root_dir("32837"))
        self.assertTrue(is_protected_root_dir("smart-drive-os"))
        self.assertTrue(is_protected_root_dir("DBI202_VuPT/MSSQL16.MSSQLSERVER"))
        self.assertTrue(is_protected_root_dir("D:\\WindowsApps"))
        self.assertTrue(is_protected_root_dir("D:\\SteamLibrary"))

    def test_default_exclude_dirs_contains_system_and_games(self) -> None:
        self.assertIn("WindowsApps", DEFAULT_EXCLUDE_DIRS)
        self.assertIn("SteamLibrary", DEFAULT_EXCLUDE_DIRS)
        self.assertIn("Riot Games", DEFAULT_EXCLUDE_DIRS)
        self.assertIn("smart-drive-os", DEFAULT_EXCLUDE_DIRS)

    def test_auto_zoner_never_moves_smart_drive_runtime_dir(self) -> None:
        temp_dir = tempfile.mkdtemp()
        try:
            zoner = AutoZoner(temp_dir)
            # Simulate smart-drive-os folder inside root
            sd_folder = os.path.join(temp_dir, "smart-drive-os")
            os.makedirs(sd_folder, exist_ok=True)
            with open(os.path.join(sd_folder, "pyproject.toml"), "w") as f:
                f.write("[project]\nname='smart-drive-os'\n")

            # Classification should return None (protected)
            action = zoner.classify_item(sd_folder)
            self.assertIsNone(action, "AutoZoner must never move smart-drive-os!")
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_auto_zoner_classifies_academic_courses(self) -> None:
        temp_dir = tempfile.mkdtemp()
        try:
            zoner = AutoZoner(temp_dir)
            fptu_folder = os.path.join(temp_dir, "DBI202_VuPT")
            os.makedirs(fptu_folder, exist_ok=True)
            action = zoner.classify_item(fptu_folder)
            self.assertIsNotNone(action)
            self.assertEqual(action[0], "02_Learning_Knowledge")
            self.assertIn("FPTU", action[1])
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestWorkstationHybridProfileAndCLI(unittest.TestCase):
    """Verifies workstation-hybrid profile and CLI path check."""

    def test_workstation_hybrid_profile_exists(self) -> None:
        self.assertIn("workstation-hybrid", PROFILES)
        prof = PROFILES["workstation-hybrid"]
        self.assertIn("02_Learning_Knowledge/FPTU", prof["subdirs"])
        self.assertIn("05_Dev_Toolbox/OEM_Drivers", prof["subdirs"])

    def test_drive_initializer_with_workstation_hybrid(self) -> None:
        temp_dir = tempfile.mkdtemp()
        try:
            init = DriveInitializer(temp_dir)
            res = init.initialize(temp_dir, profile="workstation-hybrid")
            self.assertEqual(res["profile"], "workstation-hybrid")
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "02_Learning_Knowledge", "FPTU")))
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "05_Dev_Toolbox", "OEM_Drivers")))
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_check_system_path_returns_valid_dict(self) -> None:
        info = check_system_path()
        self.assertIn("platform", info)
        self.assertIn("is_discoverable", info)
        self.assertTrue(info["python_module_syntax_guaranteed"])

    def test_safe_home_dir_fallback_on_stripped_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            home = _safe_home_dir()
            self.assertIsInstance(home, Path)
            self.assertTrue(len(str(home)) > 0)


if __name__ == "__main__":
    unittest.main()
