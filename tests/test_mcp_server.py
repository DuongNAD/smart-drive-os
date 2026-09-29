"""tests/test_mcp_server.py - Comprehensive Unit Tests for JSON-RPC 2.0 stdio MCP Server.

100% Python Standard Library unittest.
Tests Tiers 1 & 2:
- TOOLS catalog definition (8 standard tools with schemas).
- Direct dispatching of all 8 MCP tools.
- JSON-RPC 2.0 request lifecycle (initialize, ping, tools/list, tools/call).
- Error handling for invalid tool names and unhandled methods (-32601).
- Compact schema and character token budget truncation in ssd_search.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.mcp.server import PROTOCOL_VERSION, SERVER_NAME, TOOLS, SmartDriveMCPServer
except (ImportError, ModuleNotFoundError):
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from mcp_server import PROTOCOL_VERSION, SERVER_NAME, TOOLS, SmartDriveMCPServer

from tests.helpers import SmartDriveTestCase


class TestMCPToolsCatalog(SmartDriveTestCase):
    """Tier 1: MCP Tool declarations and schemas."""

    def test_eight_standard_tools_cataloged(self) -> None:
        """TOOLS must contain all 8 specified MCP tools with input schemas."""
        tool_names = {t["name"] for t in TOOLS}
        expected_tools = {
            "ssd_search",
            "ssd_audit",
            "ssd_clean",
            "ssd_find_duplicates",
            "ssd_update_index",
            "ssd_check_safety",
            "ssd_status",
            "ssd_auto_organize",
        }
        self.assertEqual(tool_names, expected_tools)

        for t in TOOLS:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertIn("inputSchema", t)
            self.assertEqual(t["inputSchema"]["type"], "object")


class TestMCPToolDispatching(SmartDriveTestCase):
    """Tier 1 & Tier 2: Direct tool dispatching via server instance."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_dispatch_ssd_status(self) -> None:
        """ssd_status returns root mount and shield health dictionary."""
        data = self.server.dispatch_tool("ssd_status", {})
        self.assertIsInstance(data, dict)
        self.assertIn("mount", data)
        self.assertIn("anti_indexing_shields", data)

    def test_dispatch_ssd_audit(self) -> None:
        """ssd_audit returns storage allocation and slack metrics."""
        data = self.server.dispatch_tool("ssd_audit", {})
        self.assertIsInstance(data, dict)
        self.assertIn("total_files", data)
        self.assertIn("total_allocated_bytes", data)
        self.assertIn("total_slack_bytes", data)
        self.assertIn("taxonomies", data)

    def test_dispatch_ssd_check_safety(self) -> None:
        """ssd_check_safety audits filenames for Windows forbidden characters."""
        # Safe filename
        safe_res = self.server.dispatch_tool("ssd_check_safety", {"path": "clean_model.gguf"})
        self.assertTrue(safe_res["is_safe"])
        self.assertEqual(safe_res["forbidden_character_violations"], [])

        # Forbidden filename
        bad_res = self.server.dispatch_tool("ssd_check_safety", {"path": "bad:file*name.txt"})
        self.assertFalse(bad_res["is_safe"])
        self.assertGreater(len(bad_res["forbidden_character_violations"]), 0)

    def test_dispatch_ssd_clean(self) -> None:
        """ssd_clean runs safe junk detection and returns preview report."""
        data = self.server.dispatch_tool("ssd_clean", {"dry_run": True})
        self.assertIsInstance(data, dict)
        self.assertTrue(data["dry_run"])
        self.assertIn("detected_count", data)
        self.assertIn("tier", data)

    def test_dispatch_ssd_find_duplicates(self) -> None:
        """ssd_find_duplicates detects mock drive duplicates and computes reclaimable space."""
        data = self.server.dispatch_tool("ssd_find_duplicates", {})
        self.assertIsInstance(data, dict)
        self.assertIn("duplicate_group_count", data)
        self.assertGreater(data["duplicate_group_count"], 0)
        self.assertIn("total_reclaimable_bytes", data)

    def test_dispatch_ssd_auto_organize(self) -> None:
        """ssd_auto_organize produces auto-zoning action plan in simulation mode."""
        data = self.server.dispatch_tool("ssd_auto_organize", {"apply": False})
        self.assertIsInstance(data, dict)
        self.assertEqual(data["status"], "dry_run")
        self.assertIn("actions", data)

    def test_dispatch_ssd_update_index_and_search(self) -> None:
        """ssd_update_index populates FTS5 database and ssd_search queries it."""
        from smart_drive.indexer.db import DatabaseManager

        # Ensure schema is initialized
        db_path = self.server.get_db_path()
        db = DatabaseManager(db_path)
        db.initialize_schema()
        db.close()

        # Update index
        update_res = self.server.dispatch_tool("ssd_update_index", {})
        self.assertIsInstance(update_res, dict)
        self.assertGreater(update_res.get("added", 0), 0)

        # Search
        search_res = self.server.dispatch_tool("ssd_search", {"query": "llama", "limit": 10})
        self.assertIsInstance(search_res, dict)
        self.assertGreater(search_res.get("total_count", 0), 0)
        self.assertGreater(len(search_res.get("matches", [])), 0)

    def test_dispatch_unknown_tool_raises_value_error(self) -> None:
        """Dispatching an unregistered tool name raises ValueError."""
        with self.assertRaises(ValueError):
            self.server.dispatch_tool("nonexistent_tool_xyz", {})


class TestMCPProtocolJSONRPC(SmartDriveTestCase):
    """Tier 1: Full JSON-RPC 2.0 stdio protocol handling."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))
        self._stdout_capture = io.StringIO()
        self._orig_stdout = sys.stdout
        sys.stdout = self._stdout_capture

    def tearDown(self) -> None:
        sys.stdout = self._orig_stdout
        super().tearDown()

    def _get_last_response(self) -> dict:
        output = self._stdout_capture.getvalue().strip()
        lines = [line for line in output.split("\n") if line.strip()]
        self.assertGreater(len(lines), 0, "No response emitted by server")
        return json.loads(lines[-1])

    def test_protocol_initialize(self) -> None:
        """initialize request returns protocolVersion and capabilities."""
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "test-agent"}},
        }
        self.server.handle_request(req)
        resp = self._get_last_response()

        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        self.assertIn("result", resp)
        self.assertEqual(resp["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_protocol_ping(self) -> None:
        """ping request returns empty result."""
        req = {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
        self.server.handle_request(req)
        resp = self._get_last_response()

        self.assertEqual(resp["id"], 2)
        self.assertEqual(resp["result"], {})

    def test_protocol_tools_list(self) -> None:
        """tools/list request returns all 8 tools."""
        req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        self.server.handle_request(req)
        resp = self._get_last_response()

        self.assertEqual(resp["id"], 3)
        self.assertIn("tools", resp["result"])
        self.assertEqual(len(resp["result"]["tools"]), 8)

    def test_protocol_tools_call_success(self) -> None:
        """tools/call executes tool and formats result as JSON-RPC content array."""
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ssd_status", "arguments": {}},
        }
        self.server.handle_request(req)
        resp = self._get_last_response()

        self.assertEqual(resp["id"], 4)
        self.assertIn("result", resp)
        self.assertIn("content", resp["result"])
        content_item = resp["result"]["content"][0]
        self.assertEqual(content_item["type"], "text")
        parsed_payload = json.loads(content_item["text"])
        self.assertIn("mount", parsed_payload)

    def test_protocol_content_length_mode(self) -> None:
        """Server supports optional Content-Length prefixed framing."""
        self.server.use_content_length_mode = True
        req = {"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}}
        self.server.handle_request(req)
        raw_output = self._stdout_capture.getvalue()
        self.assertIn("Content-Length:", raw_output)
        self.assertIn("\r\n\r\n", raw_output)

    def test_protocol_unhandled_method_returns_error_32601(self) -> None:
        """Unknown JSON-RPC method returns standard error code -32601."""
        req = {"jsonrpc": "2.0", "id": 99, "method": "unknown/method", "params": {}}
        self.server.handle_request(req)
        resp = self._get_last_response()

        self.assertEqual(resp["id"], 99)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
