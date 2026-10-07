"""tests/test_cli_tidy.py - Comprehensive Unit Tests for 'smart-drive tidy' CLI.

Tests:
- tidy dry-run text output
- tidy --json plan output
- tidy --apply execution and journal creation
- tidy --journals and tidy --journals --json
- tidy --undo <ID> preview and tidy --undo <ID> --apply rollback
- tidy with scoped path and --mode in_place
- Error handling: missing root returns exit code 2, missing journal returns exit code 1
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.cli.main import main
from tests.helpers import SmartDriveTestCase


class TestCliTidy(SmartDriveTestCase):
    """Test suite for smart-drive tidy CLI subcommand."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()

    def test_tidy_dry_run_text_output(self) -> None:
        """smart-drive tidy without --apply prints proposed moves and does not move files."""
        (self.mock_root / "sample_model.gguf").write_text("gguf data", encoding="utf-8")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            ret = main(["tidy", "--root", str(self.mock_root)])

        self.assertEqual(ret, 0)
        output = stdout_buf.getvalue()
        self.assertIn("Proposed Organization Plan", output)
        self.assertIn("01_AI_Models/GGUF", output)
        self.assertIn("sample_model.gguf", output)
        # Verify file was not moved
        self.assertTrue(os.path.exists(self.mock_root / "sample_model.gguf"))
        self.assertFalse(os.path.exists(self.mock_root / "01_AI_Models" / "GGUF" / "sample_model.gguf"))

    def test_tidy_json_output(self) -> None:
        """smart-drive tidy --json returns parseable JSON plan."""
        (self.mock_root / "weights.safetensors").write_text("safetensors", encoding="utf-8")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            ret = main(["tidy", "--root", str(self.mock_root), "--json"])

        self.assertEqual(ret, 0)
        output = stdout_buf.getvalue()
        plan_dict = json.loads(output)
        self.assertIn("plan_id", plan_dict)
        self.assertIn("moves", plan_dict)
        move_srcs = {m["src"] for m in plan_dict["moves"]}
        self.assertIn("weights.safetensors", move_srcs)

    def test_tidy_apply_and_journal(self) -> None:
        """smart-drive tidy --apply executes moves and creates journal."""
        (self.mock_root / "weights2.safetensors").write_text("safetensors", encoding="utf-8")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            ret = main(["tidy", "--root", str(self.mock_root), "--apply"])

        self.assertEqual(ret, 0)
        output = stdout_buf.getvalue()
        self.assertIn("Applied organization plan", output)

        # File moved
        self.assertFalse(os.path.exists(self.mock_root / "weights2.safetensors"))
        self.assertTrue(os.path.exists(self.mock_root / "01_AI_Models" / "SafeTensors" / "weights2.safetensors"))

        # Journal created
        journal_dir = self.mock_root / ".smart_drive" / "journal"
        self.assertTrue(journal_dir.is_dir())
        journals = list(journal_dir.glob("*.jsonl"))
        self.assertEqual(len(journals), 1)

    def test_tidy_journals_listing(self) -> None:
        """smart-drive tidy --journals lists execution journals."""
        # Create a move first
        (self.mock_root / "document.pdf").write_text("pdf data", encoding="utf-8")
        with patch("sys.stdout", io.StringIO()):
            main(["tidy", "--root", str(self.mock_root), "--apply"])

        # Query journals as text
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = main(["tidy", "--root", str(self.mock_root), "--journals"])
        self.assertEqual(ret, 0)
        output = stdout_buf.getvalue()
        self.assertIn("Organization Journals for:", output)
        self.assertIn("Journal ID:", output)

        # Query journals as JSON
        stdout_json = io.StringIO()
        with patch("sys.stdout", stdout_json):
            ret2 = main(["tidy", "--root", str(self.mock_root), "--journals", "--json"])
        self.assertEqual(ret2, 0)
        res = json.loads(stdout_json.getvalue())
        self.assertEqual(res["count"], 1)
        self.assertEqual(len(res["journals"]), 1)

    def test_tidy_undo_preview_and_apply(self) -> None:
        """smart-drive tidy --undo previews and reverts organization moves."""
        (self.mock_root / "data.parquet").write_text("parquet data", encoding="utf-8")
        stdout_apply = io.StringIO()
        with patch("sys.stdout", stdout_apply):
            main(["tidy", "--root", str(self.mock_root), "--apply", "--json"])

        apply_res = json.loads(stdout_apply.getvalue())
        journal_id = apply_res["journal_id"]

        # 1. Preview undo
        stdout_undo_preview = io.StringIO()
        with patch("sys.stdout", stdout_undo_preview):
            ret = main(["tidy", "--root", str(self.mock_root), "--undo", journal_id])
        self.assertEqual(ret, 0)
        self.assertIn("[DRY RUN] Rollback preview", stdout_undo_preview.getvalue())
        self.assertTrue(os.path.exists(self.mock_root / "01_AI_Models" / "Datasets" / "data.parquet"))

        # 2. Execute undo
        stdout_undo_apply = io.StringIO()
        with patch("sys.stdout", stdout_undo_apply):
            ret_apply = main(["tidy", "--root", str(self.mock_root), "--undo", journal_id, "--apply"])
        self.assertEqual(ret_apply, 0)
        self.assertIn("Rollback applied", stdout_undo_apply.getvalue())
        # Restored to original location
        self.assertTrue(os.path.exists(self.mock_root / "data.parquet"))
        self.assertFalse(os.path.exists(self.mock_root / "01_AI_Models" / "Datasets" / "data.parquet"))

    def test_tidy_in_place_mode(self) -> None:
        """smart-drive tidy with scoped path and --mode in_place organizes inside folder."""
        scope_dir = self.mock_root / "Unsorted_Inbox"
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / "clip.mp4").write_text("video", encoding="utf-8")

        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = main([
                "tidy",
                "Unsorted_Inbox",
                "--root", str(self.mock_root),
                "--mode", "in_place",
                "--apply",
            ])
        self.assertEqual(ret, 0)
        self.assertFalse(os.path.exists(scope_dir / "clip.mp4"))
        # Should be inside Unsorted_Inbox/Videos/YYYY/clip.mp4
        found_videos = list(scope_dir.glob("Videos/*/*.mp4"))
        self.assertEqual(len(found_videos), 1)

    def test_missing_root_returns_exit_code_2(self) -> None:
        """smart-drive tidy with non-existent root returns exit code 2."""
        stderr_buf = io.StringIO()
        fake_non_existent = str(self.mock_root / "definitely_not_existing_drive_root_12345")
        with patch("sys.stderr", stderr_buf):
            ret = main(["tidy", "--root", fake_non_existent])
        self.assertEqual(ret, 2)

    def test_undo_nonexistent_journal_returns_exit_code_1(self) -> None:
        """smart-drive tidy --undo nonexistent-id returns exit code 1."""
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            ret = main(["tidy", "--root", str(self.mock_root), "--undo", "nonexistent-journal-id-999"])
        self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main()
