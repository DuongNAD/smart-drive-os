"""tests/test_planner.py - Comprehensive Unit Tests for SmartDrive Planner & Journal Engine.

100% Python Standard Library unittest. Zero external runtime dependencies.
Tests:
- Table-driven categorisation (>= 30 cases: screenshots, course codes, arXiv, OS installers, unknown, mtime date buckets).
- Unit detection: git repo, package.json, app bundles, exe+dll, albums, course folders, mixed folders, protected & hidden.
- Invariants & safety validation: boundary escapes, inside-project guards, collisions, invalid characters.
- Transactional execution: journal creation before rename, partial failure recovery, rollback integrity, empty dir cleanup.
- _INDEX.md generation with marker and preservation of user-authored index files.
- in_place mode paths.
- Survey scaling (< 3s for 5,000 loose files, depth-1 survey for nested trees, mess score, index dir sizes).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from unittest.mock import patch

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.config import TAXONOMY_ROOT_DIRS
from smart_drive.core.planner import (
    INDEX_MARKER,
    Move,
    Plan,
    apply_moves,
    classify_file_target,
    inspect_directory_unit,
    list_journals,
    propose_plan,
    survey,
    undo_journal,
    validate_moves,
)
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from tests.helpers import SmartDriveTestCase


def hash_tree(root_dir: str) -> Dict[str, str]:
    """Generates a dictionary mapping relative path to SHA-256 hash (or '<DIR>') for tree comparison."""
    tree: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root_dir):
        rel_dir = os.path.relpath(dirpath, root_dir).replace("\\", "/")
        if rel_dir != ".":
            tree[rel_dir] = "<DIR>"
        for f in filenames:
            full = os.path.join(dirpath, f)
            rel_f = os.path.relpath(full, root_dir).replace("\\", "/")
            try:
                with open(full, "rb") as fp:
                    h = hashlib.sha256(fp.read()).hexdigest()
                tree[rel_f] = h
            except OSError:
                tree[rel_f] = "<ERR>"
    return tree


class TestTableDrivenClassification(SmartDriveTestCase):
    """Table-driven categorization tests covering >= 30 distinct cases."""

    def test_table_driven_cases(self) -> None:
        # Fixed epoch timestamp: 2026-03-15 12:00:00 local time
        # Struct: (tm_year=2026, tm_mon=3, tm_mday=15, tm_hour=12, tm_min=0, tm_sec=0, ...)
        t_struct = time.struct_time((2026, 3, 15, 12, 0, 0, 6, 74, -1))
        fixed_mtime = time.mktime(t_struct)

        # Cases: (filename, expected_dest, expected_reason, expected_conf)
        test_cases: List[Tuple[str, str, str, str]] = [
            # 1. AI Models
            ("model.gguf", "01_AI_Models/GGUF/model.gguf", "GGUF AI model", "high"),
            ("weights.safetensors", "01_AI_Models/SafeTensors/weights.safetensors", "SafeTensors model", "high"),
            ("model.ckpt", "01_AI_Models/Checkpoints/model.ckpt", "AI model checkpoint", "high"),
            ("model.pt", "01_AI_Models/Checkpoints/model.pt", "AI model checkpoint", "high"),
            ("model.pth", "01_AI_Models/Checkpoints/model.pth", "AI model checkpoint", "high"),
            ("network.onnx", "01_AI_Models/Checkpoints/network.onnx", "AI model checkpoint", "high"),
            ("quant.tflite", "01_AI_Models/Checkpoints/quant.tflite", "AI model checkpoint", "high"),
            ("net.h5", "01_AI_Models/Checkpoints/net.h5", "AI model checkpoint", "high"),
            ("dataset.parquet", "01_AI_Models/Datasets/dataset.parquet", "AI dataset", "high"),
            ("features.arrow", "01_AI_Models/Datasets/features.arrow", "AI dataset", "high"),
            ("records.tfrecord", "01_AI_Models/Datasets/records.tfrecord", "AI dataset", "high"),

            # 2. Books & Papers & Courses
            ("clean_code.epub", "02_Learning_Knowledge/Books/clean_code.epub", "e-book", "high"),
            ("manual.mobi", "02_Learning_Knowledge/Books/manual.mobi", "e-book", "high"),
            ("guide.azw3", "02_Learning_Knowledge/Books/guide.azw3", "e-book", "high"),
            ("paper.djvu", "02_Learning_Knowledge/Books/paper.djvu", "e-book", "high"),
            ("2401.12345v2.pdf", "02_Learning_Knowledge/Papers/2401.12345v2.pdf", "arXiv paper", "medium"),
            ("arxiv_2302.01234.pdf", "02_Learning_Knowledge/Papers/arxiv_2302.01234.pdf", "arXiv paper", "medium"),
            ("DBI202_Lab1.docx", "02_Learning_Knowledge/Courses/DBI202/DBI202_Lab1.docx", "Course DBI202 document", "medium"),
            ("PRJ301-FinalProject.pdf", "02_Learning_Knowledge/Courses/PRJ301/PRJ301-FinalProject.pdf", "Course PRJ301 document", "medium"),
            ("SWE202c_slides.pptx", "02_Learning_Knowledge/Courses/SWE202c/SWE202c_slides.pptx", "Course SWE202c document", "medium"),

            # 3. Personal Documents
            ("resume.pdf", "03_Personal_Documents/PDF/resume.pdf", "PDF document", "high"),
            ("budget.xlsx", "03_Personal_Documents/Office/budget.xlsx", "Office document", "high"),
            ("report.docx", "03_Personal_Documents/Office/report.docx", "Office document", "high"),
            ("notes.odt", "03_Personal_Documents/Office/notes.odt", "Office document", "high"),
            ("presentation.key", "03_Personal_Documents/Office/presentation.key", "Office document", "high"),
            ("sheet.numbers", "03_Personal_Documents/Office/sheet.numbers", "Office document", "high"),

            # 4. Screenshots & Creative Assets
            ("Screenshot 2026-01-01.png", "04_Creative_Assets/Screenshots/2026-03/Screenshot 2026-01-01.png", "screenshot", "medium"),
            ("Screen Shot 2026-02.jpg", "04_Creative_Assets/Screenshots/2026-03/Screen Shot 2026-02.jpg", "screenshot", "medium"),
            ("Ảnh chụp màn hình 2026.png", "04_Creative_Assets/Screenshots/2026-03/Ảnh chụp màn hình 2026.png", "screenshot", "medium"),
            ("Anh chup man hinh 2026.png", "04_Creative_Assets/Screenshots/2026-03/Anh chup man hinh 2026.png", "screenshot", "medium"),
            ("Capture_window.png", "04_Creative_Assets/Screenshots/2026-03/Capture_window.png", "screenshot", "medium"),
            ("Snip_01.png", "04_Creative_Assets/Screenshots/2026-03/Snip_01.png", "screenshot", "medium"),
            ("photo.jpg", "04_Creative_Assets/Images/2026/2026-03/photo.jpg", "image", "high"),
            ("graphic.webp", "04_Creative_Assets/Images/2026/2026-03/graphic.webp", "image", "high"),
            ("raw_shot.dng", "04_Creative_Assets/Images/2026/2026-03/raw_shot.dng", "image", "high"),
            ("movie.mp4", "04_Creative_Assets/Videos/2026/movie.mp4", "video", "high"),
            ("clip.mkv", "04_Creative_Assets/Videos/2026/clip.mkv", "video", "high"),
            ("song.flac", "04_Creative_Assets/Audio/song.flac", "audio", "high"),
            ("podcast.m4a", "04_Creative_Assets/Audio/podcast.m4a", "audio", "high"),
            ("vector.svg", "04_Creative_Assets/Design/vector.svg", "design asset", "high"),
            ("layout.psd", "04_Creative_Assets/Design/layout.psd", "design asset", "high"),
            ("character.blend", "04_Creative_Assets/3D_Models/character.blend", "3D model", "high"),
            ("model.glb", "04_Creative_Assets/3D_Models/model.glb", "3D model", "high"),
            ("font.woff2", "04_Creative_Assets/Fonts/font.woff2", "font", "high"),

            # 5. Dev Toolbox & Installers
            ("setup.exe", "05_Dev_Toolbox/Installers/Windows/setup.exe", "Windows installer", "high"),
            ("installer.msi", "05_Dev_Toolbox/Installers/Windows/installer.msi", "Windows installer", "high"),
            ("app.msix", "05_Dev_Toolbox/Installers/Windows/app.msix", "Windows installer", "high"),
            ("macos_app.dmg", "05_Dev_Toolbox/Installers/macOS/macos_app.dmg", "macOS installer", "high"),
            ("installer.pkg", "05_Dev_Toolbox/Installers/macOS/installer.pkg", "macOS installer", "high"),
            ("package.deb", "05_Dev_Toolbox/Installers/Linux/package.deb", "Linux installer", "high"),
            ("package.rpm", "05_Dev_Toolbox/Installers/Linux/package.rpm", "Linux installer", "high"),
            ("tool.AppImage", "05_Dev_Toolbox/Installers/Linux/tool.AppImage", "Linux installer", "high"),
            ("script.py", "05_Dev_Toolbox/Scripts/script.py", "script", "high"),
            ("deploy.sh", "05_Dev_Toolbox/Scripts/deploy.sh", "script", "high"),
            ("build.ps1", "05_Dev_Toolbox/Scripts/build.ps1", "script", "high"),

            # 6. Archives & Storage
            ("archive.zip", "06_Archives_Storage/Archives/archive.zip", "archive", "high"),
            ("backup.7z", "06_Archives_Storage/Archives/backup.7z", "archive", "high"),
            ("bundle.tar.gz", "06_Archives_Storage/Archives/bundle.tar.gz", "archive", "high"),
            ("os.iso", "06_Archives_Storage/Disk_Images/os.iso", "disk image", "high"),
            ("drive.vmdk", "06_Archives_Storage/Disk_Images/drive.vmdk", "disk image", "high"),
            ("old_state.bak", "06_Archives_Storage/Backups/old_state.bak", "backup file", "high"),

            # 7. Unsorted
            ("data.unknownext123", "06_Archives_Storage/_Unsorted/data.unknownext123", "unrecognized extension", "low"),
            ("mystery.bin", "06_Archives_Storage/_Unsorted/mystery.bin", "unrecognized extension", "low"),
        ]

        self.assertGreaterEqual(len(test_cases), 30)

        for fname, exp_dest, exp_reason, exp_conf in test_cases:
            with self.subTest(filename=fname):
                dest, reason, conf = classify_file_target(
                    fname, fixed_mtime, mode="zones", scope="", is_root_scope=False
                )
                self.assertEqual(dest, exp_dest)
                self.assertEqual(conf, exp_conf)

    def test_mtime_date_bucketing_with_utime(self) -> None:
        """Verifies date bucketing across different years and months via os.utime."""
        root = self.test_dir / "mtime_test"
        root.mkdir()

        # 2024-05-10
        t_2024_05 = time.mktime(time.struct_time((2024, 5, 10, 10, 0, 0, 4, 131, -1)))
        f1 = root / "photo2024.jpg"
        f1.touch()
        os.utime(str(f1), (t_2024_05, t_2024_05))

        # 2025-11-20
        t_2025_11 = time.mktime(time.struct_time((2025, 11, 20, 10, 0, 0, 3, 324, -1)))
        f2 = root / "video2025.mp4"
        f2.touch()
        os.utime(str(f2), (t_2025_11, t_2025_11))

        plan = propose_plan(str(root), scope="")
        move_map = {m.src: m.dst for m in plan.moves}

        self.assertEqual(move_map.get("photo2024.jpg"), "04_Creative_Assets/Images/2024/2024-05/photo2024.jpg")
        self.assertEqual(move_map.get("video2025.mp4"), "04_Creative_Assets/Videos/2025/video2025.mp4")


class TestUnitDetection(SmartDriveTestCase):
    """Verifies unit recognition: project, app, album, course folder, mixed folder, protected, hidden."""

    def test_git_repo_project_unit(self) -> None:
        root = self.test_dir / "units_root"
        proj = root / "my_web_app"
        (proj / ".git").mkdir(parents=True)
        (proj / "main.py").touch()

        plan = propose_plan(str(root), scope="")
        move = next((m for m in plan.moves if m.src == "my_web_app"), None)
        self.assertIsNotNone(move)
        self.assertEqual(move.dst, "03_Development_Projects/my_web_app")
        self.assertEqual(move.kind, "directory")

    def test_package_json_project_unit(self) -> None:
        root = self.test_dir / "units_root"
        proj = root / "node_service"
        proj.mkdir(parents=True)
        (proj / "package.json").write_text('{"name": "test"}')

        plan = propose_plan(str(root), scope="")
        move = next((m for m in plan.moves if m.src == "node_service"), None)
        self.assertIsNotNone(move)
        self.assertEqual(move.dst, "03_Development_Projects/node_service")

    def test_app_bundle_and_portable_app_unit(self) -> None:
        root = self.test_dir / "units_root"
        # .app bundle
        app1 = root / "TextEditor.app"
        app1.mkdir(parents=True)

        # exe + dll directory
        app2 = root / "CustomTool"
        app2.mkdir(parents=True)
        (app2 / "tool.exe").touch()
        (app2 / "support.dll").touch()

        plan = propose_plan(str(root), scope="")
        move_map = {m.src: m.dst for m in plan.moves}
        self.assertEqual(move_map.get("TextEditor.app"), "05_Dev_Toolbox/Portable_Apps/TextEditor.app")
        self.assertEqual(move_map.get("CustomTool"), "05_Dev_Toolbox/Portable_Apps/CustomTool")

    def test_course_folder_unit(self) -> None:
        root = self.test_dir / "units_root"
        course = root / "DBI202_Database_Systems"
        course.mkdir(parents=True)
        (course / "lecture1.pdf").touch()

        plan = propose_plan(str(root), scope="")
        move = next((m for m in plan.moves if m.src == "DBI202_Database_Systems"), None)
        self.assertIsNotNone(move)
        self.assertEqual(move.dst, "02_Learning_Knowledge/Courses/DBI202/DBI202_Database_Systems")

    def test_album_unit(self) -> None:
        root = self.test_dir / "units_root"
        # Image album: 5 files, 4 images (80%)
        album_img = root / "Vacation_Photos"
        album_img.mkdir(parents=True)
        for i in range(4):
            (album_img / f"img_{i}.jpg").touch()
        (album_img / "notes.txt").touch()

        # Video album: 5 files, 5 videos (100%)
        album_vid = root / "GoPro_Clips"
        album_vid.mkdir(parents=True)
        for i in range(5):
            (album_vid / f"clip_{i}.mp4").touch()

        plan = propose_plan(str(root), scope="")
        move_map = {m.src: m.dst for m in plan.moves}
        self.assertEqual(move_map.get("Vacation_Photos"), "04_Creative_Assets/Images/Albums/Vacation_Photos")
        self.assertEqual(move_map.get("GoPro_Clips"), "04_Creative_Assets/Videos/Albums/GoPro_Clips")

    def test_mixed_folder_is_kept(self) -> None:
        root = self.test_dir / "units_root"
        mixed = root / "RandomStuff"
        mixed.mkdir(parents=True)
        (mixed / "doc.pdf").touch()
        (mixed / "music.mp3").touch()
        (mixed / "code.py").touch()

        plan = propose_plan(str(root), scope="")
        kept_paths = {k["path"]: k["reason"] for k in plan.kept}
        self.assertIn("RandomStuff", kept_paths)
        self.assertIn("mixed folder", kept_paths["RandomStuff"])

    def test_root_protections_and_launchers_kept(self) -> None:
        root = self.test_dir / "units_root"
        root.mkdir(parents=True, exist_ok=True)
        (root / "AGENTS.md").touch()
        (root / "README.md").touch()
        (root / "clean_mac_junk.sh").touch()
        (root / "check_ssd_status.ps1").touch()
        (root / ".hidden_file").touch()
        (root / ".fseventsd").mkdir(parents=True)
        (root / "01_AI_Models").mkdir(parents=True)

        plan = propose_plan(str(root), scope="")
        kept_paths = {k["path"] for k in plan.kept}
        self.assertIn("AGENTS.md", kept_paths)
        self.assertIn("README.md", kept_paths)
        self.assertIn("clean_mac_junk.sh", kept_paths)
        self.assertIn("check_ssd_status.ps1", kept_paths)
        self.assertIn(".hidden_file", kept_paths)
        self.assertIn(".fseventsd", kept_paths)
        self.assertIn("01_AI_Models", kept_paths)


class TestProjectProtectionAndValidation(SmartDriveTestCase):
    """Verifies that items inside project units are strictly protected and validate_moves rejects all violations."""

    def test_no_plan_moves_inside_project_unit(self) -> None:
        root = self.test_dir / "proj_nest"
        proj = root / "my_project"
        (proj / ".git").mkdir(parents=True)
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "main.py").touch()
        (proj / "loose_doc.pdf").touch()

        # Proposing plan on the drive root
        plan = propose_plan(str(root), scope="")
        # Only the project folder itself can be moved whole
        src_paths = {m.src for m in plan.moves}
        self.assertEqual(src_paths, {"my_project"})

    def test_validate_moves_rejections(self) -> None:
        root = self.test_dir / "val_test"
        root.mkdir()
        (root / "valid_file.pdf").touch()
        (root / "existing_dest.pdf").touch()
        proj = root / "inner_proj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "package.json").touch()
        (proj / "code.py").touch()

        bad_moves = [
            # 1. Missing source
            Move(src="missing_file.pdf", dst="03_Personal_Documents/PDF/missing_file.pdf", kind="file", reason="", confidence="high"),
            # 2. Outside root boundary
            Move(src="../escaped.txt", dst="03_Personal_Documents/PDF/escaped.txt", kind="file", reason="", confidence="high"),
            Move(src="valid_file.pdf", dst="../outside/valid_file.pdf", kind="file", reason="", confidence="high"),
            # 3. Source is root or protected
            Move(src="", dst="03_Personal_Documents/PDF/root", kind="directory", reason="", confidence="high"),
            # 4. Source inside project unit
            Move(src="inner_proj/code.py", dst="05_Dev_Toolbox/Scripts/code.py", kind="file", reason="", confidence="high"),
            # 5. Destination inside source
            Move(src="inner_proj", dst="inner_proj/sub/inner_proj", kind="directory", reason="", confidence="high"),
            # 6. Destination already exists
            Move(src="valid_file.pdf", dst="existing_dest.pdf", kind="file", reason="", confidence="high"),
            # 7. Destination contains invalid Windows/exFAT characters or reserved names
            Move(src="valid_file.pdf", dst="03_Personal_Documents/PDF/bad:name*.pdf", kind="file", reason="", confidence="high"),
            Move(src="valid_file.pdf", dst="03_Personal_Documents/PDF/AUX.pdf", kind="file", reason="", confidence="high"),
        ]

        report = validate_moves(str(root), bad_moves)
        self.assertFalse(report["valid"])
        for m in report["moves"]:
            self.assertEqual(m["status"], "error", f"Move {m['src']} -> {m['dst']} should have failed validation")


class TestExecutionJournalAndUndo(SmartDriveTestCase):
    """Verifies dry-run byte idempotency, live execution journaling, partial failure recovery, and undo integrity."""

    def test_dry_run_leaves_tree_byte_identical(self) -> None:
        root = self.test_dir / "dry_run_test"
        root.mkdir()
        (root / "doc1.pdf").write_text("content1")
        (root / "img1.png").write_text("content2")

        before_tree = hash_tree(str(root))
        plan = propose_plan(str(root), scope="")
        apply_res = apply_moves(str(root), plan.moves, apply=False)

        self.assertEqual(apply_res["status"], "dry_run")
        after_tree = hash_tree(str(root))
        self.assertEqual(before_tree, after_tree)

    def test_apply_and_undo_restores_identical_tree(self) -> None:
        root = self.test_dir / "apply_undo_test"
        root.mkdir()
        (root / "doc1.pdf").write_text("pdf_content")
        (root / "sheet.xlsx").write_text("excel_content")
        (root / "photo.jpg").write_text("jpeg_content")

        before_tree = hash_tree(str(root))
        plan = propose_plan(str(root), scope="")
        res = apply_moves(str(root), plan.moves, apply=True, write_index=True)

        self.assertEqual(res["status"], "applied")
        self.assertEqual(res["moved"], 3)
        self.assertTrue(os.path.exists(res["journal_path"]))

        # Undo execution
        undo_res = undo_journal(str(root), res["journal_id"], apply=True)
        self.assertEqual(undo_res["status"], "applied")
        self.assertEqual(undo_res["undone_count"], 3)
        self.assertEqual(undo_res["conflict_count"], 0)

        after_undo_tree = hash_tree(str(root))
        # Note: .smart_drive/journal/*.jsonl and _INDEX.md exist in metadata, but all original files restored
        for orig_file, orig_hash in before_tree.items():
            self.assertIn(orig_file, after_undo_tree)
            self.assertEqual(after_undo_tree[orig_file], orig_hash)

    def test_journal_exists_before_first_rename(self) -> None:
        root = self.test_dir / "journal_seq_test"
        root.mkdir()
        (root / "test1.pdf").write_text("content")

        journal_verified = False

        orig_rename = os.rename
        def monitored_rename(src, dst):
            nonlocal journal_verified
            jdir = os.path.join(str(root), ".smart_drive", "journal")
            if os.path.isdir(jdir):
                jfiles = os.listdir(jdir)
                if jfiles:
                    jpath = os.path.join(jdir, jfiles[0])
                    with open(jpath, "r", encoding="utf-8") as jf:
                        first_line = jf.readline()
                        ev = json.loads(first_line)
                        if ev.get("event") == "start":
                            journal_verified = True
            return orig_rename(src, dst)

        with patch("os.rename", side_effect=monitored_rename):
            plan = propose_plan(str(root), scope="")
            apply_moves(str(root), plan.moves, apply=True)

        self.assertTrue(journal_verified, "Journal with 'start' event must exist before first rename")

    def test_partial_failure_leaves_partial_status_and_undo_recovers(self) -> None:
        root = self.test_dir / "partial_fail_test"
        root.mkdir()
        (root / "file1.pdf").write_text("content1")
        (root / "file2.pdf").write_text("content2")
        (root / "file3.pdf").write_text("content3")

        call_count = 0
        orig_rename = os.rename
        def failing_third_rename(src, dst):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise OSError("Simulated disk error on 3rd rename")
            return orig_rename(src, dst)

        with patch("os.rename", side_effect=failing_third_rename):
            plan = propose_plan(str(root), scope="")
            res = apply_moves(str(root), plan.moves, apply=True)

        self.assertEqual(res["moved"], 2)
        self.assertEqual(res["failed"], 1)

        journals = list_journals(str(root))
        self.assertEqual(len(journals), 1)
        self.assertEqual(journals[0]["status"], "partial")

        # Undo the partial run
        undo_res = undo_journal(str(root), res["journal_id"], apply=True)
        self.assertEqual(undo_res["undone_count"], 2)
        self.assertTrue((root / "file1.pdf").exists())
        self.assertTrue((root / "file2.pdf").exists())
        self.assertTrue((root / "file3.pdf").exists())


class TestIndexMarkdownAndInPlace(SmartDriveTestCase):
    """Verifies _INDEX.md generation with markers, protection of custom index files, and in_place mode."""

    def test_index_md_generation_and_user_file_preservation(self) -> None:
        root = self.test_dir / "index_md_test"
        root.mkdir()
        (root / "file.pdf").write_text("doc")

        plan = propose_plan(str(root), scope="")
        res = apply_moves(str(root), plan.moves, apply=True, write_index=True)

        # Verify generated _INDEX.md in 03_Personal_Documents
        index_file = root / "03_Personal_Documents" / "_INDEX.md"
        self.assertTrue(index_file.exists())
        content = index_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith(INDEX_MARKER))

        # Test user-authored index file without marker is preserved
        user_index = root / "01_AI_Models" / "_INDEX.md"
        (root / "01_AI_Models").mkdir(parents=True, exist_ok=True)
        user_index.write_text("# My Custom User Index\nDo not overwrite!")

        (root / "model.gguf").write_text("gguf")
        plan2 = propose_plan(str(root), scope="")
        apply_moves(str(root), plan2.moves, apply=True, write_index=True)

        self.assertEqual(user_index.read_text(encoding="utf-8"), "# My Custom User Index\nDo not overwrite!")

    def test_in_place_mode_destinations(self) -> None:
        root = self.test_dir / "in_place_root"
        sub = root / "Downloads"
        sub.mkdir(parents=True)
        (sub / "report.pdf").touch()
        (sub / "tool.exe").touch()
        (sub / "unknown.xyz").touch()
        proj = sub / "my_project"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "package.json").touch()

        plan = propose_plan(str(root), scope="Downloads", mode="in_place")
        move_map = {m.src: m.dst for m in plan.moves}

        self.assertEqual(move_map.get("Downloads/report.pdf"), "Downloads/PDF/report.pdf")
        self.assertEqual(move_map.get("Downloads/tool.exe"), "Downloads/Installers/Windows/tool.exe")
        self.assertEqual(move_map.get("Downloads/unknown.xyz"), "Downloads/_Unsorted/unknown.xyz")
        self.assertEqual(move_map.get("Downloads/my_project"), "Downloads/Projects/my_project")


class TestSurveyAndPerformance(SmartDriveTestCase):
    """Verifies survey depth-1 constraint, mess score, index dir size usage, and 5000 file performance."""

    def test_survey_reports_only_depth_1_entries(self) -> None:
        root = self.test_dir / "nested_tree"
        root.mkdir()
        level1 = root / "Level1"
        level2 = level1 / "Level2"
        level3 = level2 / "Level3"
        level3.mkdir(parents=True)
        (level1 / "f1.txt").touch()
        (level2 / "f2.txt").touch()
        (level3 / "f3.txt").touch()
        (root / "root_file.pdf").touch()

        report = survey(str(root), scope="")
        entry_names = {e["name"] for e in report["entries"]}

        self.assertIn("Level1", entry_names)
        self.assertIn("root_file.pdf", entry_names)
        self.assertNotIn("Level2", entry_names)
        self.assertNotIn("Level3", entry_names)
        self.assertNotIn("f1.txt", entry_names)
        self.assertNotIn("f2.txt", entry_names)
        self.assertNotIn("f3.txt", entry_names)

    def test_survey_uses_index_for_directory_sizes(self) -> None:
        root = self.test_dir / "survey_index_test"
        sub = root / "MyFolder"
        sub.mkdir(parents=True)
        fpath = sub / "file.txt"
        fpath.write_text("A" * 1024)  # 1024 bytes

        # Build search index
        db_path = root / ".smart_drive" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = DatabaseManager(str(db_path))
        db.initialize_schema()
        mgr = IndexManager(db, str(root))
        mgr.full_index()
        db.close()

        report = survey(str(root), scope="")
        my_folder_entry = next((e for e in report["entries"] if e["name"] == "MyFolder"), None)
        self.assertIsNotNone(my_folder_entry)
        self.assertEqual(my_folder_entry["size_bytes"], 1024)

    def test_performance_5000_loose_files_under_3_seconds(self) -> None:
        root = self.test_dir / "perf_5000"
        root.mkdir()
        # Create 5,000 loose files in batches
        for i in range(5000):
            ext = ".pdf" if i % 3 == 0 else (".jpg" if i % 3 == 1 else ".py")
            (root / f"file_{i:04d}{ext}").touch()

        t0 = time.perf_counter()
        survey_res = survey(str(root), scope="", limit=200)
        plan_res = propose_plan(str(root), scope="")
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 3.0, f"Survey and plan proposal took {elapsed:.2f}s, expected < 3.0s")
        self.assertEqual(survey_res["totals"]["files"], 5000)
        self.assertEqual(len(plan_res.moves), 5000)


class TestNoClobberCrashSafetyAndUndoHardening(SmartDriveTestCase):
    """Moves never replace existing data, survive a crash mid-move, and undo trusts nothing it reads."""

    def _make_root(self, name: str) -> Path:
        root = self.test_dir / name
        root.mkdir()
        return root

    def _journal_path(self, root: Path, journal_id: str) -> Path:
        return root / ".smart_drive" / "journal" / f"{journal_id}.jsonl"

    def test_destinations_differing_only_in_case_are_duplicates(self) -> None:
        root = self._make_root("case_dupes")
        (root / "Desktop").mkdir()
        (root / "Downloads").mkdir()
        (root / "Desktop" / "A.jpg").write_text("from desktop")
        (root / "Downloads" / "a.jpg").write_text("from downloads")

        report = validate_moves(str(root), [
            {"src": "Desktop/A.jpg", "dst": "Images/A.jpg", "kind": "file"},
            {"src": "Downloads/a.jpg", "dst": "images/a.jpg", "kind": "file"},
        ])

        self.assertEqual([m["status"] for m in report["moves"]], ["ok", "error"])
        self.assertIn("Duplicate destination", report["moves"][1]["error"])

    def test_rename_refuses_a_destination_that_appeared_after_validation(self) -> None:
        root = self._make_root("late_dst")
        (root / "report.pdf").write_text("moving")
        moves = [{"src": "report.pdf", "dst": "Docs/report.pdf", "kind": "file"}]
        real_validate = validate_moves

        def validate_then_race(root_arg: str, moves_arg: List[Dict[str, Any]]) -> Dict[str, Any]:
            report = real_validate(root_arg, moves_arg)
            (root / "Docs").mkdir(exist_ok=True)
            (root / "Docs" / "report.pdf").write_text("already here")
            return report

        # os.rename replaces an existing file on macOS/Linux but not on Windows; emulate the
        # POSIX behaviour with os.replace so this test exercises the guard on every platform.
        real_replace = os.replace
        with patch("smart_drive.core.planner.validate_moves", side_effect=validate_then_race), \
                patch("os.rename", side_effect=real_replace):
            res = apply_moves(str(root), moves, apply=True)

        self.assertEqual((res["moved"], res["failed"]), (0, 1))
        self.assertEqual((root / "Docs" / "report.pdf").read_text(), "already here")
        self.assertEqual((root / "report.pdf").read_text(), "moving")

    def test_undo_recovers_a_move_whose_done_line_was_never_written(self) -> None:
        root = self._make_root("crash_after_rename")
        (root / "paper.pdf").write_text("content")
        plan = propose_plan(str(root), scope="")
        import smart_drive.core.planner as planner_mod
        real_append = planner_mod._append_journal_event

        def crash_on_done(jf: Any, event: Dict[str, Any]) -> None:
            if event.get("status") == "done":
                raise OSError("Simulated crash before the done line")
            real_append(jf, event)

        with patch("smart_drive.core.planner._append_journal_event", side_effect=crash_on_done):
            with self.assertRaises(OSError):
                apply_moves(str(root), plan.moves, apply=True)

        self.assertFalse((root / "paper.pdf").exists())
        journal_id = list_journals(str(root))[0]["id"]
        undo_res = undo_journal(str(root), journal_id, apply=True)

        self.assertEqual(undo_res["undone_count"], 1)
        self.assertEqual((root / "paper.pdf").read_text(), "content")

    def test_list_journals_does_not_count_begin_lines_as_failures(self) -> None:
        root = self._make_root("journal_counts")
        (root / "a.pdf").write_text("a")
        (root / "b.pdf").write_text("b")

        apply_moves(str(root), propose_plan(str(root), scope="").moves, apply=True)
        journal = list_journals(str(root))[0]

        self.assertEqual((journal["done_moves"], journal["failed_moves"]), (2, 0))
        self.assertEqual(journal["status"], "completed")

    def test_undo_rejects_invalid_unknown_and_ambiguous_ids(self) -> None:
        root = self._make_root("undo_ids")
        (root / "x.pdf").write_text("x")
        res = apply_moves(str(root), propose_plan(str(root), scope="").moves, apply=True)
        twin_id = res["journal_id"][:-1] + ("0" if res["journal_id"][-1] != "0" else "1")
        self._journal_path(root, twin_id).write_text("", encoding="utf-8")

        for bad_id in ("", "../../outside", "..\\x", "not-a-journal"):
            self.assertEqual(undo_journal(str(root), bad_id)["status"], "error", bad_id)
        ambiguous = undo_journal(str(root), res["journal_id"][:8])
        self.assertEqual(ambiguous["status"], "error")
        self.assertIn("ambiguous", ambiguous["error"])
        self.assertEqual(undo_journal(str(root), res["journal_id"])["status"], "dry_run")

    def test_undo_runs_once(self) -> None:
        root = self._make_root("undo_once")
        (root / "y.pdf").write_text("y")
        res = apply_moves(str(root), propose_plan(str(root), scope="").moves, apply=True)

        self.assertEqual(undo_journal(str(root), res["journal_id"], apply=True)["undone_count"], 1)
        second = undo_journal(str(root), res["journal_id"], apply=True)

        self.assertEqual(second["status"], "error")
        self.assertIn("already been undone", second["error"])
        self.assertEqual(list_journals(str(root))[0]["status"], "undone")

    def test_undo_skips_journal_entries_outside_the_root(self) -> None:
        root = self._make_root("tampered")
        outside = self.test_dir / "outside_target.txt"
        (root / "inside.txt").write_text("stay")
        journal_id = "20260101-000000-abcdef"
        journal = self._journal_path(root, journal_id)
        journal.parent.mkdir(parents=True)
        journal.write_text(
            json.dumps({"event": "start", "journal_id": journal_id, "total": 1}) + "\n"
            + json.dumps({"event": "move", "status": "done", "src": "../outside_target.txt",
                          "dst": "inside.txt", "kind": "file"}) + "\n",
            encoding="utf-8",
        )

        res = undo_journal(str(root), journal_id, apply=True)

        self.assertEqual(res["undone_count"], 0)
        self.assertEqual(res["conflict_count"], 1)
        self.assertTrue((root / "inside.txt").exists())
        self.assertFalse(outside.exists())

    def test_index_drops_the_old_row_of_a_moved_root_file(self) -> None:
        root = self._make_root("index_sync")
        (root / ".smart_drive").mkdir()
        (root / "notes.pdf").write_text("pdf")
        db = DatabaseManager(str(root / ".smart_drive" / "index.db"))
        db.initialize_schema()
        IndexManager(db, str(root)).full_index()
        db.close()

        res = apply_moves(str(root), propose_plan(str(root), scope="").moves, apply=True, write_index=False)

        self.assertIsNone(res["index_error"])
        db = DatabaseManager(str(root / ".smart_drive" / "index.db"))
        try:
            cur = db.get_connection().cursor()
            cur.execute("SELECT path FROM files WHERE filename = ?;", ("notes.pdf",))
            paths = [tuple(r)[0] for r in cur.fetchall()]
        finally:
            db.close()
        self.assertEqual(paths, ["03_Personal_Documents/PDF/notes.pdf"])


class TestPlanSemanticsOnRealisticLayouts(SmartDriveTestCase):
    """Plans respect repositories, folder manifests and zone boundaries (found on a real drive)."""

    def _make_root(self, name: str) -> Path:
        root = self.test_dir / name
        root.mkdir()
        return root

    def test_scope_inside_a_git_repo_is_locked(self) -> None:
        root = self._make_root("repo_scope")
        zone = root / "02_Learning_Knowledge"
        (zone / ".git").mkdir(parents=True)
        (zone / "NodeJS").mkdir()
        (zone / "NodeJS" / "package.json").write_text("{}")
        (zone / "pick_today.py").write_text("print(1)")
        (zone / "Java_Lab211_Bootcamp.zip").write_text("zip")

        for scope in ("02_Learning_Knowledge", "02_Learning_Knowledge/NodeJS"):
            plan = propose_plan(str(root), scope=scope)
            self.assertEqual(plan.moves, [], scope)
            self.assertIn("git repository or project", plan.kept[0]["reason"])
            report = survey(str(root), scope=scope)
            self.assertIsNotNone(report["locked"])
            self.assertEqual(report["mess_score"], 0)

    def test_folder_manifests_stay_with_their_folder(self) -> None:
        root = self._make_root("manifests")
        inbox = root / "Inbox"
        inbox.mkdir()
        for name in ("README.md", "AGENTS.md", "GEMINI.md", "INDEX.md", "LICENSE", "notes.pdf"):
            (inbox / name).write_text(name)

        plan = propose_plan(str(root), scope="Inbox")

        self.assertEqual([m.src for m in plan.moves], ["Inbox/notes.pdf"])

    def test_auto_mode_tidies_in_place_inside_a_zone_and_into_zones_elsewhere(self) -> None:
        root = self._make_root("auto_mode")
        (root / "04_Creative_Assets").mkdir()
        (root / "04_Creative_Assets" / "manual.pdf").write_text("pdf")
        (root / "Downloads").mkdir()
        (root / "Downloads" / "manual.pdf").write_text("pdf")

        in_zone = propose_plan(str(root), scope="04_Creative_Assets")
        elsewhere = propose_plan(str(root), scope="Downloads")

        self.assertEqual(in_zone.mode, "in_place")
        self.assertEqual(in_zone.moves[0].dst, "04_Creative_Assets/PDF/manual.pdf")
        self.assertEqual(elsewhere.mode, "zones")
        self.assertEqual(elsewhere.moves[0].dst, "03_Personal_Documents/PDF/manual.pdf")

    def test_survey_dir_sizes_do_not_treat_underscore_as_a_wildcard(self) -> None:
        root = self._make_root("dir_sizes")
        (root / ".smart_drive").mkdir()
        (root / "01_AI_Models").mkdir()
        (root / "01_AI_Models" / "a.gguf").write_bytes(b"x" * 10)
        (root / "01xAIxModels").mkdir()
        (root / "01xAIxModels" / "b.bin").write_bytes(b"y" * 1000)
        db = DatabaseManager(str(root / ".smart_drive" / "index.db"))
        db.initialize_schema()
        IndexManager(db, str(root)).full_index()
        db.close()

        sizes = {e["name"]: e["size_bytes"] for e in survey(str(root))["entries"] if e["kind"] == "directory"}

        self.assertEqual(sizes["01_AI_Models"], 10)
        self.assertEqual(sizes["01xAIxModels"], 1000)


if __name__ == "__main__":
    unittest.main()
