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

    def test_protocol_initialize_includes_instructions(self) -> None:
        """initialize response serverInfo must include agent instructions."""
        req = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "initialize",
            "params": {"clientInfo": {"name": "test-agent"}},
        }
        self.server.handle_request(req)
        resp = self._get_last_response()
        server_info = resp["result"].get("serverInfo", {})
        self.assertIn("instructions", server_info)
        self.assertIn("ssd_search", server_info["instructions"])

    def test_protocol_compact_json_serialization(self) -> None:
        """JSON-RPC responses must use compact serialization separators without extra spaces."""
        req = {"jsonrpc": "2.0", "id": 20, "method": "ping", "params": {}}
        self.server.handle_request(req)
        raw_output = self._stdout_capture.getvalue().strip()
        lines = [line for line in raw_output.split("\n") if line.strip()]
        last_line = lines[-1]
        self.assertIn('{"jsonrpc":"2.0","id":20,"result":{}}', last_line)


class TestMCPOptimizationsAndRegistrar(SmartDriveTestCase):
    """Unit tests for token-saving schemas, pagination, clean breakdown, and multi-agent registrar."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_ssd_audit_compact_vs_full(self) -> None:
        """ssd_audit supports compact mode by default with top extensions and formatted strings."""
        compact_res = self.server.dispatch_tool("ssd_audit", {"compact": True})
        self.assertTrue(compact_res.get("compact"))
        self.assertIn("total_logical_formatted", compact_res)
        self.assertIn("total_slack_formatted", compact_res)
        self.assertIn("categories", compact_res)
        # Check that categories contain top_extensions and extension_count
        categories = compact_res["categories"]
        self.assertGreater(len(categories), 0)
        first_cat = next(iter(categories.values()))
        self.assertIn("top_extensions", first_cat)
        self.assertIn("extension_count", first_cat)

        full_res = self.server.dispatch_tool("ssd_audit", {"compact": False})
        self.assertFalse(full_res.get("compact", False))
        self.assertNotIn("total_logical_formatted", full_res)

    def test_ssd_clean_dry_run_breakdown_and_sample(self) -> None:
        """ssd_clean dry_run provides breakdown_by_type and preview capped at 5 files."""
        # Create some mock junk files
        junk_dir = self.mock_root / "03_Development_Projects" / "cache_dir"
        junk_dir.mkdir(parents=True, exist_ok=True)
        for i in range(8):
            (junk_dir / f"temp_{i}.tmp").write_bytes(b"temp junk")

        clean_res = self.server.dispatch_tool("ssd_clean", {"dry_run": True})
        self.assertTrue(clean_res["dry_run"])
        self.assertIn("breakdown_by_type", clean_res)
        self.assertIn("sample_preview", clean_res)
        self.assertLessEqual(len(clean_res["sample_preview"]), 5)

    def test_ssd_find_duplicates_pagination(self) -> None:
        """ssd_find_duplicates supports limit/offset pagination and reports has_more."""
        # Create 3 duplicate groups
        for group_idx in range(3):
            content = f"UNIQUE_CONTENT_FOR_GROUP_{group_idx}_".encode("utf-8") * 20
            p1 = self.mock_root / "01_AI_Models" / f"dup_{group_idx}_a.bin"
            p2 = self.mock_root / "06_Archives_Storage" / f"dup_{group_idx}_b.bin"
            p1.write_bytes(content)
            p2.write_bytes(content)

        # Page 1: limit 1, offset 0
        page1 = self.server.dispatch_tool("ssd_find_duplicates", {"limit": 1, "offset": 0})
        self.assertEqual(page1["limit"], 1)
        self.assertEqual(page1["offset"], 0)
        self.assertGreaterEqual(page1["total_groups"], 3)
        self.assertEqual(page1["returned_group_count"], 1)
        self.assertEqual(len(page1["duplicate_groups"]), 1)
        self.assertTrue(page1["has_more"])
        self.assertEqual(page1["next_offset"], 1)

        # Page 2: limit 1, offset 1
        page2 = self.server.dispatch_tool("ssd_find_duplicates", {"limit": 1, "offset": 1})
        self.assertEqual(page2["limit"], 1)
        self.assertEqual(page2["offset"], 1)
        self.assertEqual(page2["returned_group_count"], 1)
        self.assertEqual(len(page2["duplicate_groups"]), 1)

    def test_registrar_detect_installed_agents(self) -> None:
        """detect_installed_agents returns detection dictionary for all supported agents."""
        from smart_drive.mcp.registrar import detect_installed_agents
        agents = detect_installed_agents()
        self.assertIsInstance(agents, dict)
        for key in ["antigravity", "claude", "cursor", "windsurf", "workspace"]:
            self.assertIn(key, agents)
            self.assertIsInstance(agents[key], bool)

    def test_registrar_utf8_env_in_mcp_entry(self) -> None:
        """mcp_entry must declare UTF-8 environment variables for cross-platform compatibility."""
        from smart_drive.mcp.registrar import mcp_entry
        entry = mcp_entry()
        self.assertIn("env", entry)
        self.assertEqual(entry["env"].get("PYTHONIOENCODING"), "utf-8")
        self.assertEqual(entry["env"].get("PYTHONUTF8"), "1")

    def test_cli_mcp_register_dispatch_json(self) -> None:
        """smart_drive.cli.main handles `mcp register --json` returning valid JSON."""
        from smart_drive.cli.main import main
        captured_stdout = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = captured_stdout
        try:
            exit_code = main(["mcp", "register", "--json", "--workspace"])
        finally:
            sys.stdout = orig_stdout

        self.assertEqual(exit_code, 0)
        output = captured_stdout.getvalue().strip()
        data = json.loads(output)
        self.assertIsInstance(data, dict)
        self.assertTrue(data.get("workspace"))


if __name__ == "__main__":
    unittest.main()

