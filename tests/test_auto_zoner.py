"""tests/test_auto_zoner.py - Comprehensive Unit Tests for AutoZoner Engine.

100% Python Standard Library unittest.
Tests Tiers 1, 2, 3:
- Automated classification of loose files/folders into 6 standard taxonomies.
- Whitelist safeguards: root manifests and launchers are never relocated.
- Safe dry-run planning (generate_plan) vs execution (apply_plan).
- Destination collision resolution (_1, _2 suffixing to prevent data overwriting).
- Anti-indexing shield verification and automatic seeding.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.core.auto_zoner import AutoZoner, ZoningAction
except (ImportError, ModuleNotFoundError):
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from core.auto_zoner import AutoZoner, ZoningAction

from tests.helpers import SmartDriveTestCase


class TestAutoZonerClassification(SmartDriveTestCase):
    """Tier 1: Classification of loose files and directories into taxonomies."""

    def test_classify_loose_ai_model(self) -> None:
        """Loose GGUF model file at root routes to 01_AI_Models."""
        root = self.test_dir
        model_file = root / "unorganized_model.gguf"
        model_file.write_bytes(b"GGUF_TEST_DATA")

        zoner = AutoZoner(str(root))
        res = zoner.classify_item(str(model_file))
        self.assertIsNotNone(res)
        taxonomy, target_subpath, reason = res
        self.assertEqual(taxonomy, "01_AI_Models")
        self.assertIn("01_AI_Models", target_subpath)

    def test_classify_loose_document(self) -> None:
        """Loose PDF file at root routes to 02_Learning_Knowledge."""
        root = self.test_dir
        pdf_file = root / "lecture_notes.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 Mock PDF")

        zoner = AutoZoner(str(root))
        res = zoner.classify_item(str(pdf_file))
        self.assertIsNotNone(res)
        taxonomy, target_subpath, reason = res
        self.assertEqual(taxonomy, "02_Learning_Knowledge")

    def test_classify_loose_code_project_directory(self) -> None:
        """Loose directory containing package.json routes to 03_Development_Projects."""
        root = self.test_dir
        proj_dir = root / "my_web_app"
        proj_dir.mkdir()
        (proj_dir / "package.json").write_text('{"name": "app"}', encoding="utf-8")

        zoner = AutoZoner(str(root))
        res = zoner.classify_item(str(proj_dir))
        self.assertIsNotNone(res)
        taxonomy, target_subpath, reason = res
        self.assertEqual(taxonomy, "03_Development_Projects")

    def test_classify_loose_script_and_archive(self) -> None:
        """Loose script routes to 05_Dev_Toolbox and zip to 06_Archives_Storage."""
        root = self.test_dir
        sh_file = root / "install.sh"
        sh_file.write_text("#!/bin/bash\n", encoding="utf-8")
        zip_file = root / "backup_data.zip"
        zip_file.write_bytes(b"PK\x03\x04")

        zoner = AutoZoner(str(root))
        sh_res = zoner.classify_item(str(sh_file))
        self.assertEqual(sh_res[0], "05_Dev_Toolbox")

        zip_res = zoner.classify_item(str(zip_file))
        self.assertEqual(zip_res[0], "06_Archives_Storage")

    def test_whitelist_items_never_classified(self) -> None:
        """Root manifests, scripts, and taxonomies return None (never moved)."""
        root = self.test_dir
        gemini = root / "GEMINI.md"
        gemini.write_text("# GEMINI", encoding="utf-8")
        agents = root / "AGENTS.md"
        agents.write_text("# AGENTS", encoding="utf-8")
        tax_dir = root / "01_AI_Models"
        tax_dir.mkdir()

        zoner = AutoZoner(str(root))
        self.assertIsNone(zoner.classify_item(str(gemini)))
        self.assertIsNone(zoner.classify_item(str(agents)))
        self.assertIsNone(zoner.classify_item(str(tax_dir)))


class TestAutoZonerPlanningAndExecution(SmartDriveTestCase):
    """Tier 1 & Tier 3: Plan generation, conflict resolution, and plan application."""

    def test_generate_plan_does_not_modify_disk(self) -> None:
        """generate_plan creates list of actions while files remain unmoved."""
        root = self.test_dir
        loose = root / "loose_guide.pdf"
        loose.write_bytes(b"%PDF data")

        zoner = AutoZoner(str(root))
        plan = zoner.generate_plan()

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].target_taxonomy, "02_Learning_Knowledge")
        self.assertTrue(loose.exists(), "File was moved during dry-run plan generation!")

    def test_conflict_resolution_suffixes_filename(self) -> None:
        """If destination already exists, resolve_destination_conflict appends _1."""
        root = self.test_dir
        dest = root / "target.txt"
        dest.write_text("existing", encoding="utf-8")

        zoner = AutoZoner(str(root))
        candidate = zoner.resolve_destination_conflict(str(dest))
        expected_suffix = str(root / "target_1.txt")
        self.assertEqual(candidate, expected_suffix)

    def test_apply_plan_relocates_files(self) -> None:
        """apply_plan moves loose files into proper taxonomy directories."""
        root = self.test_dir
        loose = root / "loose_model.gguf"
        loose.write_bytes(b"GGUF_MODEL_DATA")

        zoner = AutoZoner(str(root))
        plan = zoner.generate_plan()
        result = zoner.apply_plan(plan)

        self.assertTrue(result["success"])
        self.assertEqual(result["moved_count"], 1)
        self.assertFalse(loose.exists(), "Original file should have been moved")

        # Verify target file exists in destination
        moved_dest = result["moved"][0]["dest"]
        self.assertTrue(os.path.exists(moved_dest))
        self.assertIn("01_AI_Models", moved_dest)

    def test_ensure_anti_indexing_shields(self) -> None:
        """ensure_anti_indexing_shields creates .metadata_never_index and .fseventsd/no_log."""
        root = self.test_dir
        zoner = AutoZoner(str(root))
        shields = zoner.ensure_anti_indexing_shields()

        self.assertTrue(shields[".metadata_never_index"])
        self.assertTrue(shields[".fseventsd/no_log"])
        self.assertTrue((root / ".metadata_never_index").exists())
        self.assertTrue((root / ".fseventsd" / "no_log").exists())


if __name__ == "__main__":
    unittest.main()
