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

from unittest.mock import patch
from smart_drive.core.system_refs import SystemRefIndex
from tests.helpers import SmartDriveTestCase


class _IsolatedAutoZonerTestCase(SmartDriveTestCase):
    """Base test case ensuring unit tests never scan the host Registry/Start Menu/Desktop."""

    def setUp(self) -> None:
        super().setUp()
        self._collect_patcher = patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect",
            side_effect=AssertionError(
                "SystemRefIndex.collect() must not be called in unit tests; inject system_refs=SystemRefIndex([])"
            ),
        )
        self._collect_patcher.start()

    def tearDown(self) -> None:
        self._collect_patcher.stop()
        super().tearDown()


class TestAutoZonerClassification(_IsolatedAutoZonerTestCase):
    """Tier 1: Classification of loose files and directories into taxonomies."""

    def test_classify_loose_ai_model(self) -> None:
        """Loose GGUF model file at root routes to 01_AI_Models."""
        root = self.test_dir
        model_file = root / "unorganized_model.gguf"
        model_file.write_bytes(b"GGUF_TEST_DATA")

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
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

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
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

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
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

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
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

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
        self.assertIsNone(zoner.classify_item(str(gemini)))
        self.assertIsNone(zoner.classify_item(str(agents)))
        self.assertIsNone(zoner.classify_item(str(tax_dir)))


class TestAutoZonerPlanningAndExecution(_IsolatedAutoZonerTestCase):
    """Tier 1 & Tier 3: Plan generation, conflict resolution, and plan application."""

    def test_generate_plan_does_not_modify_disk(self) -> None:
        """generate_plan creates list of actions while files remain unmoved."""
        root = self.test_dir
        loose = root / "loose_guide.pdf"
        loose.write_bytes(b"%PDF data")

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
        plan = zoner.generate_plan()

        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].target_taxonomy, "02_Learning_Knowledge")
        self.assertTrue(loose.exists(), "File was moved during dry-run plan generation!")

    def test_conflict_resolution_suffixes_filename(self) -> None:
        """If destination already exists, resolve_destination_conflict appends _1."""
        root = self.test_dir
        dest = root / "target.txt"
        dest.write_text("existing", encoding="utf-8")

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
        candidate = zoner.resolve_destination_conflict(str(dest))
        expected_suffix = str(root / "target_1.txt")
        self.assertEqual(candidate, expected_suffix)

    def test_apply_plan_relocates_files(self) -> None:
        """apply_plan moves loose files into proper taxonomy directories."""
        root = self.test_dir
        loose = root / "loose_model.gguf"
        loose.write_bytes(b"GGUF_MODEL_DATA")

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
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
        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
        shields = zoner.ensure_anti_indexing_shields()

        self.assertTrue(shields[".metadata_never_index"])
        self.assertTrue(shields[".fseventsd/no_log"])
        self.assertTrue((root / ".metadata_never_index").exists())
        self.assertTrue((root / ".fseventsd" / "no_log").exists())

    def test_slack_bytes_uses_explicit_cluster_size(self) -> None:
        """A 100-byte file reports 3,996 slack bytes on 4 KB clusters and 524,188 on 512 KB clusters."""
        root = self.test_dir
        loose = root / "loose_note.pdf"
        loose.write_bytes(b"x" * 100)

        zoner_4k = AutoZoner(str(root), cluster_size=4096, system_refs=SystemRefIndex([]))
        plan_4k = zoner_4k.generate_plan()
        self.assertEqual(len(plan_4k), 1)
        self.assertEqual(plan_4k[0].nominal_size, 100)
        self.assertEqual(plan_4k[0].slack_bytes, 3996)

        zoner_512k = AutoZoner(str(root), cluster_size=524288, system_refs=SystemRefIndex([]))
        plan_512k = zoner_512k.generate_plan()
        self.assertEqual(len(plan_512k), 1)
        self.assertEqual(plan_512k[0].nominal_size, 100)
        self.assertEqual(plan_512k[0].slack_bytes, 524188)

    def test_cluster_size_detected_from_filesystem_with_512k_fallback(self) -> None:
        """AutoZoner uses detect_filesystem(root).block_size when > 0 and falls back to 524,288 when 0."""
        from smart_drive.core.fsinfo import FilesystemInfo

        root = self.test_dir
        fs_4k = FilesystemInfo(
            path=str(root),
            fs_type="ntfs",
            block_size=4096,
            mount_point="E:\\",
            source="win32",
        )
        with patch("smart_drive.core.auto_zoner.detect_filesystem", return_value=fs_4k):
            zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
            self.assertEqual(zoner.cluster_size, 4096)

        fs_unknown = FilesystemInfo(
            path=str(root),
            fs_type="unknown",
            block_size=0,
            mount_point="",
            source="unknown",
        )
        with patch("smart_drive.core.auto_zoner.detect_filesystem", return_value=fs_unknown):
            zoner_fallback = AutoZoner(str(root), system_refs=SystemRefIndex([]))
            self.assertEqual(zoner_fallback.cluster_size, 524288)

    def test_system_referenced_and_uninstaller_folders_never_planned_or_moved(self) -> None:
        """Folders referenced by PATH/services or containing unins000.exe are excluded from generate_plan and apply_plan."""
        from smart_drive.core.system_refs import SystemRef, SystemRefIndex

        root = self.test_dir
        git_dir = root / "Git"
        git_dir.mkdir()
        (git_dir / "cmd").mkdir()

        sql_dir = root / "SQL"
        sql_dir.mkdir()
        (sql_dir / "Binn").mkdir()

        tool_dir = root / "tool"
        tool_dir.mkdir()
        (tool_dir / "unins000.exe").write_bytes(b"MZ")

        safe_tool = root / "my_utility"
        safe_tool.mkdir()
        (safe_tool / "helper.txt").write_text("ok", encoding="utf-8")

        fake_index = SystemRefIndex([
            SystemRef(kind="path", source="PATH (System)", target=str(git_dir / "cmd")),
            SystemRef(kind="service", source="service MSSQLSERVER", target=str(sql_dir / "Binn" / "sqlservr.exe")),
        ])

        zoner = AutoZoner(str(root), system_refs=fake_index)
        plan = zoner.generate_plan()

        planned_names = {os.path.basename(a.src_path) for a in plan}
        self.assertEqual(planned_names, {"my_utility"})

        by_name = {os.path.basename(item["path"]): item["refs"] for item in zoner.protected_by_system}
        self.assertIn("Git", by_name)
        self.assertIn("SQL", by_name)
        self.assertIn("tool", by_name)
        self.assertEqual(by_name["Git"][0], {"kind": "path", "source": "PATH (System)"})
        self.assertEqual(by_name["SQL"][0], {"kind": "service", "source": "service MSSQLSERVER"})
        self.assertEqual(by_name["tool"][0], {"kind": "uninstaller", "source": "unins000.exe"})

        # Even if a stale plan containing Git, SQL, and tool is passed to apply_plan, none of them may be moved
        stale_plan = [
            ZoningAction(
                src_path=str(git_dir),
                dest_path=str(root / "02_Learning_Knowledge" / "Git"),
                target_taxonomy="02_Learning_Knowledge",
                reason="stale",
                is_dir=True,
                nominal_size=0,
                slack_bytes=0,
            ),
            ZoningAction(
                src_path=str(sql_dir),
                dest_path=str(root / "02_Learning_Knowledge" / "SQL"),
                target_taxonomy="02_Learning_Knowledge",
                reason="stale",
                is_dir=True,
                nominal_size=0,
                slack_bytes=0,
            ),
            ZoningAction(
                src_path=str(tool_dir),
                dest_path=str(root / "05_Dev_Toolbox" / "tool"),
                target_taxonomy="05_Dev_Toolbox",
                reason="stale",
                is_dir=True,
                nominal_size=0,
                slack_bytes=0,
            ),
        ]
        res = zoner.apply_plan(stale_plan)
        self.assertEqual(res["moved_count"], 0)
        self.assertTrue(git_dir.exists())
        self.assertTrue(sql_dir.exists())
        self.assertTrue(tool_dir.exists())

    def test_cmd_organize_reports_protected_by_system_in_json_and_text(self) -> None:
        """cmd_organize outputs protected_by_system in JSON and prints Skipped lines in text mode."""
        import argparse
        import io
        import json
        from unittest.mock import patch
        from smart_drive.cli.cmd_organize import cmd_organize
        from smart_drive.core.system_refs import SystemRef, SystemRefIndex

        root = self.test_dir
        git_dir = root / "Git"
        git_dir.mkdir()
        (git_dir / "cmd").mkdir()

        fake_index = SystemRefIndex([
            SystemRef(kind="path", source="PATH (User)", target=str(git_dir / "cmd")),
            SystemRef(kind="installed_app", source='installed app "Git"', target=str(git_dir)),
            SystemRef(kind="shortcut", source=r"C:\Users\X\Desktop\Git Bash.lnk", target=str(git_dir / "git-bash.exe")),
        ])

        with patch.object(SystemRefIndex, "collect", return_value=fake_index):
            # JSON mode
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            try:
                rc = cmd_organize(argparse.Namespace(root=str(root), apply=False, clean=False, json=True))
            finally:
                sys.stdout = old_stdout
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["planned_actions"], [])
            self.assertEqual(len(payload["protected_by_system"]), 1)
            self.assertEqual(payload["protected_by_system"][0]["path"], str(git_dir))

            # Text mode
            buf_txt = io.StringIO()
            sys.stdout = buf_txt
            try:
                rc_txt = cmd_organize(argparse.Namespace(root=str(root), apply=False, clean=False, json=False))
            finally:
                sys.stdout = old_stdout
            self.assertEqual(rc_txt, 0)
            txt = buf_txt.getvalue()
            self.assertIn(f"Skipped {git_dir} — used by PATH, installed app \"Git\", 1 shortcut", txt)

    def test_system_referenced_files_and_files_directly_on_path_never_moved(self) -> None:
        """Loose files targeted by shortcuts/services or lying directly inside a PATH directory are never moved."""
        from smart_drive.core.system_refs import SystemRef, SystemRefIndex

        root = self.test_dir
        shortcut_file = root / "pinned_script.ps1"
        shortcut_file.write_text("Write-Output 'shortcut'\n", encoding="utf-8")

        path_dir = root / "bin_dir"
        path_dir.mkdir()
        path_file = path_dir / "cli_helper.ps1"
        path_file.write_text("Write-Output 'on path'\n", encoding="utf-8")

        free_file = root / "free_script.ps1"
        free_file.write_text("Write-Output 'free'\n", encoding="utf-8")

        fake_index = SystemRefIndex([
            SystemRef(kind="shortcut", source=r"C:\Users\X\Desktop\Pinned.lnk", target=str(shortcut_file)),
            SystemRef(kind="path", source="PATH (User)", target=str(path_dir)),
        ])

        zoner = AutoZoner(str(root), system_refs=fake_index)
        self.assertIsNone(zoner.classify_item(str(shortcut_file)))
        self.assertIsNone(zoner.classify_item(str(path_file)))
        self.assertIsNotNone(zoner.classify_item(str(free_file)))

        plan = zoner.generate_plan()
        self.assertEqual([os.path.basename(a.src_path) for a in plan], ["free_script.ps1"])

        # Even if stale ZoningActions for shortcut_file or path_file are passed to apply_plan, they are not moved
        stale_plan = [
            ZoningAction(
                src_path=str(shortcut_file),
                dest_path=str(root / "05_Dev_Toolbox" / "scripts" / "pinned_script.ps1"),
                target_taxonomy="05_Dev_Toolbox",
                reason="stale",
                is_dir=False,
                nominal_size=10,
                slack_bytes=10,
            ),
            ZoningAction(
                src_path=str(path_file),
                dest_path=str(root / "05_Dev_Toolbox" / "scripts" / "cli_helper.ps1"),
                target_taxonomy="05_Dev_Toolbox",
                reason="stale",
                is_dir=False,
                nominal_size=10,
                slack_bytes=10,
            ),
        ]
        res = zoner.apply_plan(stale_plan)
        self.assertEqual(res["moved_count"], 0)
        self.assertTrue(shortcut_file.exists())
        self.assertTrue(path_file.exists())

    def test_drive_root_is_passed_as_a_shortcut_root_when_references_are_collected(self) -> None:
        """AutoZoner reads the .lnk files lying at its drive root, as the README promises."""
        root = self.test_dir
        with patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect",
            return_value=SystemRefIndex([]),
        ) as collect:
            AutoZoner(str(root)).system_refs
        collect.assert_called_once_with(shortcut_roots=[str(root)])

    def test_hoyoplay_and_genshin_impact_folders_are_protected_game_libraries(self) -> None:
        root = self.test_dir
        for name in ("HoYoPlay", "Genshin Impact"):
            game = root / name
            game.mkdir()
            (game / "package.json").write_text('{"name": "launcher"}', encoding="utf-8")
        (root / "my_project").mkdir()
        (root / "my_project" / "package.json").write_text('{"name": "my_project"}', encoding="utf-8")

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
        self.assertIsNone(zoner.classify_item(str(root / "HoYoPlay")))
        self.assertIsNone(zoner.classify_item(str(root / "Genshin Impact")))
        self.assertIsNotNone(zoner.classify_item(str(root / "my_project")))

    def test_loose_documents_in_a_path_folder_are_organized_but_executables_stay(self) -> None:
        """With the drive root itself on PATH, only files Windows can run from there are pinned, not every document."""
        from smart_drive.core.system_refs import SystemRef, SystemRefIndex

        root = self.test_dir
        (root / "manual.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
        (root / "backup.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (root / "model.gguf").write_bytes(b"GGUF")
        (root / "launcher.ps1").write_text("Write-Output 'on path'\n", encoding="utf-8")

        fake_index = SystemRefIndex([SystemRef(kind="path", source="PATH (User)", target=str(root))])
        zoner = AutoZoner(str(root), system_refs=fake_index)

        plan = zoner.generate_plan()
        self.assertEqual(
            sorted(os.path.basename(a.src_path) for a in plan),
            ["backup.zip", "manual.pdf", "model.gguf"],
        )
        self.assertEqual(
            [os.path.basename(item["path"]) for item in zoner.protected_by_system],
            ["launcher.ps1"],
        )

        res = zoner.apply_plan(plan)
        self.assertEqual(res["moved_count"], 3)
        self.assertTrue((root / "launcher.ps1").exists())

    def test_folder_of_multiple_repositories_routed_to_development_projects(self) -> None:
        """Folders containing >= 2 child repos route to 03_Development_Projects/<name>; 1 child repo returns None."""
        from unittest.mock import patch
        from smart_drive.core.system_refs import SystemRefIndex

        root = self.test_dir
        project_dir = root / "project"
        (project_dir / "a" / ".git").mkdir(parents=True)
        (project_dir / "b").mkdir(parents=True)
        (project_dir / "b" / "package.json").write_text("{}", encoding="utf-8")
        (project_dir / "c").mkdir(parents=True)

        misc_dir = root / "misc"
        (misc_dir / "a" / ".git").mkdir(parents=True)
        (misc_dir / "other").mkdir(parents=True)

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
        res_project = zoner.classify_item(str(project_dir))
        self.assertEqual(
            res_project,
            (
                "03_Development_Projects",
                os.path.join("03_Development_Projects", "project"),
                "Folder of 2 development projects",
            ),
        )
        self.assertIsNone(zoner.classify_item(str(misc_dir)))

        # If one of the 2 child repos is a symlink/junction, it is skipped -> only 1 real repo -> returns None
        with patch.object(
            AutoZoner,
            "_is_link_or_junction",
            side_effect=lambda entry: entry.name == "b",
        ):
            self.assertIsNone(zoner.classify_item(str(project_dir)))

        plan = zoner.generate_plan()
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].src_path, str(project_dir))
        self.assertEqual(plan[0].dest_path, str(root / "03_Development_Projects" / "project"))
        self.assertEqual(plan[0].reason, "Folder of 2 development projects")

    def test_folder_keyword_whole_word_and_camelcase_matching(self) -> None:
        """Substring false positives (Notepad++, Facebook, DumpStack) return None; whole words and camelCase match."""
        from smart_drive.core.system_refs import SystemRefIndex

        root = self.test_dir
        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))

        for non_match in ("Notepad++", "Facebook", "DumpStack", "remodeler", "bookmarklet", "scriptoria"):
            folder = root / non_match
            folder.mkdir()
            self.assertIsNone(
                zoner.classify_item(str(folder)),
                f"Expected {non_match!r} not to match any folder keyword",
            )

        expected = {
            "ai_models": "01_AI_Models",
            "loraCheckpoints": "01_AI_Models",
            "llamaWeights": "01_AI_Models",
            "my_notes": "02_Learning_Knowledge",
            "StudyNotes": "02_Learning_Knowledge",
            "python_tutorials": "02_Learning_Knowledge",
            "devTools": "05_Dev_Toolbox",
            "my_utilities": "05_Dev_Toolbox",
            "helper_scripts": "05_Dev_Toolbox",
            "old_backups": "06_Archives_Storage",
            "db_dumps": "06_Archives_Storage",
            "oldProject": "06_Archives_Storage",
        }
        for folder_name, target_taxonomy in expected.items():
            folder = root / folder_name
            folder.mkdir()
            res = zoner.classify_item(str(folder))
            self.assertIsNotNone(res, f"Expected {folder_name!r} to classify into {target_taxonomy}")
            assert res is not None
            self.assertEqual(res[0], target_taxonomy)

    def test_explicit_tool_folder_name_wins_over_multi_repo_rule(self) -> None:
        """A folder named tool (or tools/utility/utilities/script/scripts) routes to 05_Dev_Toolbox even with >= 2 child repos."""
        from smart_drive.core.system_refs import SystemRefIndex

        root = self.test_dir
        tool_dir = root / "tool"
        (tool_dir / "a" / ".git").mkdir(parents=True)
        (tool_dir / "b" / ".git").mkdir(parents=True)

        zoner = AutoZoner(str(root), system_refs=SystemRefIndex([]))
        res = zoner.classify_item(str(tool_dir))
        self.assertEqual(
            res,
            (
                "05_Dev_Toolbox",
                os.path.join("05_Dev_Toolbox", "tool"),
                "Detected tool/utility folder naming",
            ),
        )


if __name__ == "__main__":
    unittest.main()

