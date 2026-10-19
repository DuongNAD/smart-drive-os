"""tests/test_undo.py - Unit and CLI tests for `smart-drive undo` (Milestone 3, T10).

Verifies:
1. Preview (default dry-run) lists reverse moves from the latest un-undone batch without moving files.
2. `undo --apply` moves items back (`new -> old`), records `{"tool": "undo", "undoes": "<batch_time>", "moves": ...}`
   in `.smart_drive/moves.jsonl`, relinks coding agents/IDEs back to the original paths, updates the index, and
   preserves standard taxonomy folders.
3. Subsequent `undo` skips already-undone batches and reports nothing left to undo (or steps back to an earlier batch).
4. Conflicts (original path already exists, or moved path no longer exists) are reported and skipped without overwriting.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any, List
import unittest
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.relink import after_moves, journal_path, last_undoable_moves, record_moves
from smart_drive.core.system_refs import SystemRefIndex
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from tests.helpers import SmartDriveTestCase


class TestUndo(SmartDriveTestCase):
    """Tests for `smart-drive undo`."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_temp = self.test_dir / "sys_temp"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.fake_appdata.mkdir(parents=True, exist_ok=True)
        self.fake_temp.mkdir(parents=True, exist_ok=True)

        self._loc_patcher = patch(
            "smart_drive.core.relink._resolve_locations",
            return_value=(self.fake_home, self.fake_appdata, False),
        )
        self._loc_patcher.start()
        self._proc_patcher = patch("smart_drive.core.relink._running_processes", return_value=set())
        self._proc_patcher.start()
        self._sysrefs_patcher = patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect",
            return_value=SystemRefIndex([]),
        )
        self._sysrefs_patcher.start()

    def tearDown(self) -> None:
        self._sysrefs_patcher.stop()
        self._proc_patcher.stop()
        self._loc_patcher.stop()
        super().tearDown()

    def _run_cli(self, argv: List[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_01_auto_zoner_apply_plan_then_undo_preview_and_apply_and_repeat(self) -> None:
        drive = self.create_mock_drive()
        repo_dir = drive / "my_repo"
        repo_dir.mkdir()
        (repo_dir / "package.json").write_text('{"name": "my_repo"}\n', encoding="utf-8")
        pdf_file = drive / "handbook.pdf"
        pdf_file.write_bytes(b"%PDF-1.7\n%%EOF\n")

        claude_cfg = self.fake_home / ".claude.json"
        claude_cfg.write_text(
            json.dumps({"projects": {str(repo_dir): {"hasTrustDialogAccepted": True}}}, indent=2),
            encoding="utf-8",
        )

        # 1) AutoZoner.apply_plan + after_moves (same flow as cmd_organize)
        zoner = AutoZoner(str(drive), system_refs=SystemRefIndex([]))
        plan = zoner.generate_plan()
        apply_res = zoner.apply_plan(plan)
        self.assertEqual(apply_res["moved_count"], 2)
        moves = [(m["src"], m["dest"]) for m in apply_res["moved"]]
        after_moves(str(drive), moves, "organize")

        moved_repo = drive / "03_Development_Projects" / "my_repo"
        self.assertFalse(repo_dir.exists())
        self.assertTrue(moved_repo.is_dir())
        self.assertFalse(pdf_file.exists())
        claude_after_org = json.loads(claude_cfg.read_text(encoding="utf-8"))
        self.assertIn(str(moved_repo), claude_after_org["projects"])

        # 2) `smart-drive undo` preview (default dry-run) lists reverse moves without modifying disk
        code, out_dry, err_dry = self._run_cli(["undo", "--root", str(drive), "--json"])
        self.assertEqual(code, 0, err_dry)
        dry_data = json.loads(out_dry)
        self.assertEqual(dry_data["status"], "dry_run")
        self.assertTrue(dry_data["dry_run"])
        self.assertEqual(dry_data["planned_count"], 2)
        self.assertEqual(dry_data["conflict_count"], 0)
        self.assertEqual(dry_data["relink_preview"]["agents"].get("Claude Code"), 1)
        self.assertFalse(repo_dir.exists())
        self.assertTrue(moved_repo.is_dir())

        # Also check human-readable preview output
        code_txt, out_txt, _ = self._run_cli(["undo", "--root", str(drive)])
        self.assertEqual(code_txt, 0)
        self.assertIn("Undo Plan (Dry-Run Preview)", out_txt)
        self.assertIn("my_repo", out_txt)

        # 3) `smart-drive undo --apply` moves everything back, records undo batch, and relinks agents
        code_app, out_app, err_app = self._run_cli(["undo", "--root", str(drive), "--apply", "--json"])
        self.assertEqual(code_app, 0, err_app)
        app_data = json.loads(out_app)
        self.assertEqual(app_data["status"], "applied")
        self.assertFalse(app_data["dry_run"])
        self.assertEqual(app_data["restored_count"], 2)
        self.assertEqual(app_data["conflict_count"], 0)
        self.assertTrue(app_data["undoes"])

        # Files are back in their original locations; standard taxonomy folders are preserved
        self.assertTrue((repo_dir / "package.json").is_file())
        self.assertTrue(pdf_file.is_file())
        self.assertFalse(moved_repo.exists())
        self.assertTrue((drive / "03_Development_Projects").is_dir())
        self.assertTrue((drive / "02_Learning_Knowledge").is_dir())

        # Agent config relinked back to original repo_dir
        claude_after_undo = json.loads(claude_cfg.read_text(encoding="utf-8"))
        self.assertIn(str(repo_dir), claude_after_undo["projects"])

        # Journal has both the organize line and the undo line with matching "undoes"
        j_lines = [
            json.loads(line)
            for line in Path(journal_path(str(drive))).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(j_lines), 2)
        self.assertEqual(j_lines[0]["tool"], "organize")
        self.assertEqual(j_lines[1]["tool"], "undo")
        self.assertEqual(j_lines[1]["undoes"], j_lines[0]["time"])

        # 4) Running undo again reports nothing left to undo
        self.assertIsNone(last_undoable_moves(str(drive)))
        code_again, out_again, _ = self._run_cli(["undo", "--root", str(drive), "--apply", "--json"])
        self.assertEqual(code_again, 0)
        again_data = json.loads(out_again)
        self.assertEqual(again_data["status"], "nothing_to_undo")
        self.assertEqual(again_data["restored_count"], 0)

    def test_02_conflicts_skipped_and_earlier_batch_can_still_be_undone(self) -> None:
        drive = self.create_mock_drive()

        # Batch 1: move earlier_proj
        earlier_old = drive / "earlier_proj"
        earlier_new = drive / "03_Development_Projects" / "earlier_proj"
        earlier_new.mkdir(parents=True, exist_ok=True)
        (earlier_new / "main.py").write_text("print(1)\n", encoding="utf-8")
        record_moves(str(drive), [(str(earlier_old), str(earlier_new))], "organize")

        # Batch 2: move 3 items (conflict_dest, conflict_missing, clean_item)
        dest_old = drive / "conflict_dest.txt"
        dest_new = drive / "02_Learning_Knowledge" / "Notes" / "conflict_dest.txt"
        dest_new.parent.mkdir(parents=True, exist_ok=True)
        dest_new.write_text("moved version", encoding="utf-8")
        dest_old.write_text("new file at old path", encoding="utf-8")  # Conflict 1: old already exists

        missing_old = drive / "conflict_missing.txt"
        missing_new = drive / "02_Learning_Knowledge" / "Notes" / "conflict_missing.txt"
        # Conflict 2: missing_new does not exist on disk

        clean_old = drive / "clean_item.txt"
        clean_new = drive / "02_Learning_Knowledge" / "Notes" / "clean_item.txt"
        clean_new.write_text("clean content", encoding="utf-8")

        record_moves(
            str(drive),
            [
                (str(dest_old), str(dest_new)),
                (str(missing_old), str(missing_new)),
                (str(clean_old), str(clean_new)),
            ],
            "classify",
        )

        # Undo Batch 2: only clean_item is restored; both conflicts are reported and skipped
        code, out, err = self._run_cli(["undo", "--root", str(drive), "--apply", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["status"], "applied")
        self.assertEqual(data["restored_count"], 1)
        self.assertEqual(data["conflict_count"], 2)
        self.assertTrue(clean_old.is_file())
        self.assertEqual(clean_old.read_text(encoding="utf-8"), "clean content")
        self.assertFalse(clean_new.exists())
        # Conflicting files untouched
        self.assertEqual(dest_old.read_text(encoding="utf-8"), "new file at old path")
        self.assertEqual(dest_new.read_text(encoding="utf-8"), "moved version")

        # Next undo targets Batch 1 (since Batch 2 is now marked undone)
        code2, out2, err2 = self._run_cli(["undo", "--root", str(drive), "--apply", "--json"])
        self.assertEqual(code2, 0, err2)
        data2 = json.loads(out2)
        self.assertEqual(data2["status"], "applied")
        self.assertEqual(data2["restored_count"], 1)
        self.assertTrue((earlier_old / "main.py").is_file())
        self.assertFalse(earlier_new.exists())

    def test_03_undo_updates_search_index_when_present(self) -> None:
        drive = self.create_mock_drive()
        loose = drive / "guide.pdf"
        loose.write_bytes(b"%PDF-1.7\n%%EOF\n")

        zoner = AutoZoner(str(drive), system_refs=SystemRefIndex([]))
        plan = zoner.generate_plan()
        res = zoner.apply_plan(plan)
        after_moves(str(drive), [(m["src"], m["dest"]) for m in res["moved"]], "organize")

        db_path = drive / ".smart_drive" / "index.db"
        db = DatabaseManager(str(db_path))
        try:
            db.initialize_schema()
            IndexManager(db, str(drive)).full_index()
        finally:
            db.close()

        code, out, err = self._run_cli(["undo", "--root", str(drive), "--apply", "--json"])
        self.assertEqual(code, 0, err)
        payload: dict[str, Any] = json.loads(out)
        self.assertIsNotNone(payload["index_update"])
        self.assertTrue(loose.is_file())

    def test_04_undo_exits_nonzero_when_move_raises_oserror(self) -> None:
        drive = self.create_mock_drive()
        old_file = drive / "locked.txt"
        new_file = drive / "02_Learning_Knowledge" / "Notes" / "locked.txt"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("locked content", encoding="utf-8")
        record_moves(str(drive), [(str(old_file), str(new_file))], "classify")

        with patch("smart_drive.cli.cmd_undo._move", side_effect=OSError("Permission denied")):
            code_j, out_j, _ = self._run_cli(["undo", "--root", str(drive), "--apply", "--json"])
            self.assertEqual(code_j, 1)
            data = json.loads(out_j)
            self.assertEqual(data["restored_count"], 0)
            self.assertEqual(data["failed_count"], 1)
            self.assertIn("Permission denied", data["errors"][0]["error"])

            # Text mode also returns 1 and writes error to stderr
            code_t, _, err_t = self._run_cli(["undo", "--root", str(drive), "--apply"])
            self.assertEqual(code_t, 1)
            self.assertIn("Failed to restore", err_t)
            self.assertIn("Permission denied", err_t)


if __name__ == "__main__":
    unittest.main()
