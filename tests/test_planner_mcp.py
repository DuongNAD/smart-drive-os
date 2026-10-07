"""tests/test_planner_mcp.py - Comprehensive Unit Tests for Organization MCP Tools.

Tests all 6 new MCP tools:
- ssd_survey
- ssd_plan_organize
- ssd_validate_moves
- ssd_apply_moves (apply defaults to False)
- ssd_undo_organize (apply defaults to False)
- ssd_list_journals
Validates error handling, safety containment, dry-run defaults, and JSON-RPC lifecycle.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.root import DriveRootNotFound
from smart_drive.mcp.server import SmartDriveMCPServer, TOOLS
from tests.helpers import SmartDriveTestCase


class TestPlannerMCPTools(SmartDriveTestCase):
    """End-to-end tests for planner MCP tools."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_all_six_planner_tools_in_catalog(self) -> None:
        """Verify all 6 planner tools are registered with schemas."""
        tool_map = {t["name"]: t for t in TOOLS}
        planner_tools = [
            "ssd_survey",
            "ssd_plan_organize",
            "ssd_validate_moves",
            "ssd_apply_moves",
            "ssd_undo_organize",
            "ssd_list_journals",
        ]
        for name in planner_tools:
            self.assertIn(name, tool_map)
            t = tool_map[name]
            self.assertIn("description", t)
            self.assertIn("inputSchema", t)
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_survey_tool_dispatch(self) -> None:
        """ssd_survey returns survey summary for drive root."""
        (self.mock_root / "sample_model.gguf").write_text("gguf content", encoding="utf-8")
        (self.mock_root / "document.pdf").write_text("pdf content", encoding="utf-8")

        res = self.server.dispatch_tool("ssd_survey", {})
        self.assertIsInstance(res, dict)
        self.assertIn("root", res)
        self.assertEqual(res["scope"], "")
        self.assertIn("entries", res)
        entry_names = {e["name"] for e in res["entries"]}
        self.assertIn("sample_model.gguf", entry_names)
        self.assertIn("document.pdf", entry_names)
        self.assertGreater(res["totals"]["loose_files"], 0)
        self.assertIn("suggestions", res)

    def test_survey_tool_with_scope(self) -> None:
        """ssd_survey works with a scoped subdirectory."""
        sub_dir = self.mock_root / "Downloads"
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "installer.exe").write_text("exe", encoding="utf-8")

        res = self.server.dispatch_tool("ssd_survey", {"scope": "Downloads"})
        self.assertEqual(res["scope"], "Downloads")
        self.assertEqual(len(res["entries"]), 1)
        self.assertEqual(res["entries"][0]["name"], "installer.exe")

    def test_plan_organize_tool(self) -> None:
        """ssd_plan_organize generates a deterministic plan with destination counts."""
        (self.mock_root / "weights.safetensors").write_text("weights", encoding="utf-8")
        (self.mock_root / "notes.docx").write_text("notes", encoding="utf-8")

        res = self.server.dispatch_tool("ssd_plan_organize", {"mode": "zones"})
        self.assertIsInstance(res, dict)
        self.assertIn("plan_id", res)
        self.assertEqual(res["mode"], "zones")
        self.assertIn("moves", res)
        self.assertIn("destination_counts", res)
        move_srcs = {m["src"] for m in res["moves"]}
        self.assertIn("weights.safetensors", move_srcs)
        self.assertIn("notes.docx", move_srcs)
        self.assertFalse(res["truncated"])

    def test_plan_organize_in_place(self) -> None:
        """ssd_plan_organize supports in_place mode within a scope."""
        in_dir = self.mock_root / "MessyFolder"
        in_dir.mkdir(parents=True, exist_ok=True)
        (in_dir / "book.epub").write_text("book", encoding="utf-8")

        res = self.server.dispatch_tool("ssd_plan_organize", {"scope": "MessyFolder", "mode": "in_place"})
        self.assertEqual(res["scope"], "MessyFolder")
        self.assertEqual(res["mode"], "in_place")
        self.assertEqual(len(res["moves"]), 1)
        self.assertEqual(res["moves"][0]["dst"], "MessyFolder/Books/book.epub")

    def test_validate_moves_tool(self) -> None:
        """ssd_validate_moves audits proposals for errors."""
        (self.mock_root / "clean.mp3").write_text("audio", encoding="utf-8")

        valid_move = {"src": "clean.mp3", "dst": "04_Creative_Assets/Audio/clean.mp3", "kind": "file"}
        invalid_move = {"src": "missing.mp3", "dst": "04_Creative_Assets/Audio/missing.mp3", "kind": "file"}

        res = self.server.dispatch_tool("ssd_validate_moves", {"moves": [valid_move, invalid_move]})
        self.assertFalse(res["valid"])
        self.assertEqual(res["summary"]["ok"], 1)
        self.assertEqual(res["summary"]["errors"], 1)
        self.assertEqual(res["moves"][0]["status"], "ok")
        self.assertEqual(res["moves"][1]["status"], "error")

    def test_apply_moves_defaults_to_dry_run(self) -> None:
        """ssd_apply_moves does NOT modify disk when apply is omitted or False."""
        (self.mock_root / "track.wav").write_text("audio content", encoding="utf-8")
        moves = [{"src": "track.wav", "dst": "04_Creative_Assets/Audio/track.wav", "kind": "file"}]

        # Call with no apply argument (defaults to dry run)
        res = self.server.dispatch_tool("ssd_apply_moves", {"moves": moves})
        self.assertEqual(res["status"], "dry_run")
        self.assertTrue(os.path.exists(self.mock_root / "track.wav"))
        self.assertFalse(os.path.exists(self.mock_root / "04_Creative_Assets" / "Audio" / "track.wav"))

        # Call with apply=False explicitly
        res2 = self.server.dispatch_tool("ssd_apply_moves", {"moves": moves, "apply": False})
        self.assertEqual(res2["status"], "dry_run")
        self.assertTrue(os.path.exists(self.mock_root / "track.wav"))

    def test_apply_moves_and_undo_workflow(self) -> None:
        """ssd_apply_moves with apply=True moves files and creates journal, ssd_undo_organize restores them."""
        (self.mock_root / "dataset.parquet").write_text("data", encoding="utf-8")
        moves = [{"src": "dataset.parquet", "dst": "01_AI_Models/Datasets/dataset.parquet", "kind": "file"}]

        # Apply moves
        apply_res = self.server.dispatch_tool("ssd_apply_moves", {"moves": moves, "apply": True})
        self.assertEqual(apply_res["status"], "applied")
        self.assertEqual(apply_res["moved"], 1)
        journal_id = apply_res["journal_id"]
        self.assertFalse(os.path.exists(self.mock_root / "dataset.parquet"))
        self.assertTrue(os.path.exists(self.mock_root / "01_AI_Models" / "Datasets" / "dataset.parquet"))

        # List journals
        list_res = self.server.dispatch_tool("ssd_list_journals", {})
        self.assertEqual(list_res["count"], 1)
        self.assertEqual(list_res["journals"][0]["id"], journal_id)

        # Undo dry-run first
        undo_dry = self.server.dispatch_tool("ssd_undo_organize", {"journal_id": journal_id})
        self.assertEqual(undo_dry["status"], "dry_run")
        self.assertEqual(undo_dry["can_undo_count"], 1)
        # File still at organized location
        self.assertTrue(os.path.exists(self.mock_root / "01_AI_Models" / "Datasets" / "dataset.parquet"))

        # Undo with apply=True
        undo_res = self.server.dispatch_tool("ssd_undo_organize", {"journal_id": journal_id, "apply": True})
        self.assertEqual(undo_res["status"], "applied")
        self.assertEqual(undo_res["undone_count"], 1)
        self.assertTrue(os.path.exists(self.mock_root / "dataset.parquet"))
        self.assertFalse(os.path.exists(self.mock_root / "01_AI_Models" / "Datasets" / "dataset.parquet"))

    def test_path_traversal_rejection(self) -> None:
        """Scope escaping root is rejected with ValueError."""
        with self.assertRaises(ValueError):
            self.server.dispatch_tool("ssd_survey", {"scope": "../outside"})

    def test_jsonrpc_tools_call_lifecycle(self) -> None:
        """JSON-RPC 2.0 tools/call method dispatches planner tools cleanly."""
        (self.mock_root / "archive.7z").write_text("7z archive", encoding="utf-8")

        req = {
            "jsonrpc": "2.0",
            "id": "test-req-1",
            "method": "tools/call",
            "params": {
                "name": "ssd_survey",
                "arguments": {},
            },
        }
        resp = self.server.handle_request(req)
        self.assertIsNotNone(resp)
        self.assertEqual(resp["id"], "test-req-1")
        self.assertIn("result", resp)
        result_content = resp["result"]["content"]
        parsed = json.loads(result_content[0]["text"])
        self.assertIn("entries", parsed)
        entry_names = {e["name"] for e in parsed["entries"]}
        self.assertIn("archive.7z", entry_names)
        self.assertGreater(parsed["totals"]["loose_files"], 0)


if __name__ == "__main__":
    unittest.main()
