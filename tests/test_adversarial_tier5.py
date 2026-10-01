"""tests/test_adversarial_tier5.py - Tier 5 White-Box Adversarial Coverage Hardening.

EMPIRICAL CHALLENGER TEST SUITE (Tier 5):
Conducts white-box adversarial stress testing on:
1. smart_drive/mcp/server.py (JSON-RPC 2.0 frames, non-string tools, null arguments,
   pagination boundaries, extreme token budgets, file:// and UNC sanitization).
2. smart_drive/mcp/registrar.py (unset environment variables, corrupt JSON files,
   primitive JSON roots, multi-agent selective flags, non-existent workspace dirs).
3. smart_drive/cli/ (cmd_mcp action routing, cmd_mcp_config JSON emission,
   root discovery fallback, DB path resolution).

Strict Invariants:
- 100% Python Standard Library (zero external dependencies).
- Real filesystem interactions via tempfile & mock SSD trees.
- Deterministic empirical assertions.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import platform
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.cli.cmd_mcp import cmd_mcp
from smart_drive.cli.cmd_mcp_config import cmd_mcp_config
from smart_drive.cli.main import detect_default_root, get_default_db_path
from smart_drive.core.config import PROTECTED_CORE_TAXONOMIES, PROTECTED_ROOT_FILES
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.mcp.registrar import (
    build_mcp_entry,
    detect_installed_agents,
    get_agent_config_paths,
    load_json_config,
    register_ide_configs,
    save_json_config,
)
from smart_drive.mcp.server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    SlidingWindowRateLimiter,
    SmartDriveMCPServer,
    TOOLS,
)
from tests.helpers import SmartDriveTestCase


class TestMCPAdversarialJSONRPCFrames(SmartDriveTestCase):
    """White-box stress testing of JSON-RPC 2.0 protocol handling and frame validation."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

        # Suppress server error logger output during adversarial testing
        self.mcp_logger = logging.getLogger("smart_drive.mcp.server")
        self.prev_log_level = self.mcp_logger.level
        self.mcp_logger.setLevel(logging.CRITICAL)

        # Initialize test index db
        db = DatabaseManager(self.server.get_db_path())
        db.initialize_schema()
        db.close()

    def tearDown(self) -> None:
        self.mcp_logger.setLevel(self.prev_log_level)
        super().tearDown()

    def test_malformed_non_string_tool_names(self) -> None:
        """Non-string tool names in tools/call are caught and return isError=True."""
        bad_tool_names = [12345, None, "", True, False, ["ssd_search"], {"name": "ssd_search"}, 3.14159]
        for bad_name in bad_tool_names:
            req = {
                "jsonrpc": "2.0",
                "id": 1001,
                "method": "tools/call",
                "params": {"name": bad_name, "arguments": {}},
            }
            resp = self.server.handle_request(req)
            self.assertIsNotNone(resp, f"Response was None for bad tool name {bad_name!r}")
            self.assertIn("result", resp, f"No result in response for bad tool name {bad_name!r}")
            self.assertTrue(resp["result"].get("isError"), f"isError was not True for bad tool name {bad_name!r}")
            text_content = resp["result"]["content"][0]["text"]
            self.assertIn(f"Unknown tool '{bad_name}'", text_content)

    def test_null_and_empty_arguments_all_eight_tools(self) -> None:
        """All 8 tools handle null arguments (params.arguments = None) without throwing."""
        for tool_def in TOOLS:
            tool_name = tool_def["name"]
            req = {
                "jsonrpc": "2.0",
                "id": 2001,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": None},
            }
            resp = self.server.handle_request(req)
            self.assertIsNotNone(resp, f"Tool {tool_name} returned None on arguments=None")
            self.assertIn("result", resp, f"Tool {tool_name} response missing 'result'")
            self.assertFalse(
                resp["result"].get("isError", False),
                f"Tool {tool_name} raised error on arguments=None: {resp['result']}",
            )
            raw_text = resp["result"]["content"][0]["text"]
            parsed = json.loads(raw_text)
            self.assertIsInstance(parsed, dict, f"Tool {tool_name} output must parse to a JSON object")

    def test_invalid_arguments_types_return_code_32602(self) -> None:
        """Non-dict arguments in tools/call return JSON-RPC error -32602."""
        invalid_args = ["not-a-dict", [1, 2, 3], 42, False, 99.9]
        for arg in invalid_args:
            req = {
                "jsonrpc": "2.0",
                "id": 3001,
                "method": "tools/call",
                "params": {"name": "ssd_search", "arguments": arg},
            }
            resp = self.server.handle_request(req)
            self.assertIsNotNone(resp)
            self.assertIn("error", resp)
            self.assertEqual(resp["error"]["code"], -32602)
            self.assertIn("'arguments' must be a JSON object dictionary", resp["error"]["message"])

    def test_falsy_zero_and_negative_jsonrpc_ids(self) -> None:
        """JSON-RPC IDs: 0, negative integers, string UUIDs, and None are correctly preserved."""
        test_ids = [0, -1, -999999, "req-uuid-abc-123", None]
        for tid in test_ids:
            req = {
                "jsonrpc": "2.0",
                "id": tid,
                "method": "ping",
            }
            resp = self.server.handle_request(req)
            self.assertIsNotNone(resp)
            self.assertEqual(resp.get("id"), tid, f"ID {tid!r} was not preserved in response")

    def test_unhandled_method_with_and_without_id(self) -> None:
        """Unhandled methods with ID return -32601; unhandled notifications return None."""
        # Request with ID -> returns error
        req_with_id = {
            "jsonrpc": "2.0",
            "id": 4001,
            "method": "custom/unimplemented_action",
            "params": {},
        }
        resp = self.server.handle_request(req_with_id)
        self.assertIsNotNone(resp)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

        # Notification without ID -> returns None
        req_notification = {
            "jsonrpc": "2.0",
            "method": "custom/unimplemented_notification",
            "params": {},
        }
        resp_notif = self.server.handle_request(req_notification)
        self.assertIsNone(resp_notif, "Notification without ID must return None")

    def test_notification_initialized_bypasses_rate_limiter(self) -> None:
        """'notifications/initialized' returns None and does not consume a rate-limit slot."""
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0, enabled=True)
        self.server.rate_limiter = limiter

        # Send multiple initialized notifications
        for _ in range(5):
            resp = self.server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.assertIsNone(resp)

        # Rate limiter load should still be 0
        self.assertEqual(int(limiter.current_load), 0)

    def test_content_length_mode_formatting(self) -> None:
        """Content-Length mode formats headers and body according to MCP stdio specification."""
        self.server.use_content_length_mode = True
        fake_stdout = io.StringIO()
        self.server._raw_stdout = fake_stdout

        self.server.send_response({"jsonrpc": "2.0", "id": 5001, "result": {"ok": True}})
        output = fake_stdout.getvalue()

        self.assertTrue(output.startswith("Content-Length: "))
        self.assertIn("\r\n\r\n", output)
        header, body = output.split("\r\n\r\n", 1)
        expected_len = int(header.split(":")[1].strip())
        self.assertEqual(len(body.encode("utf-8")), expected_len)
        parsed_body = json.loads(body)
        self.assertEqual(parsed_body["id"], 5001)


class TestMCPAdversarialPaginationAndLimits(SmartDriveTestCase):
    """White-box testing of pagination math, boundary clamping, and token budgeting."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

        # Build database with 45 sample files
        db_path = self.server.get_db_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db = DatabaseManager(db_path)
        db.initialize_schema()
        db.close()

        # Populate directory with files
        code_dir = os.path.join(self.mock_root, "03_Development_Projects", "test_pkg")
        os.makedirs(code_dir, exist_ok=True)
        for i in range(45):
            with open(os.path.join(code_dir, f"module_{i:03d}.py"), "w") as f:
                f.write(f"# test file {i}\nprint('hello {i}')\n")

        db = DatabaseManager(db_path)
        mgr = IndexManager(db, str(self.mock_root))
        mgr.full_index()
        db.close()

    def test_ssd_search_extreme_negative_and_zero_limits(self) -> None:
        """Negative and zero limits clamp to 1; negative offset clamps to 0."""
        res_zero = self.server.handle_ssd_search({"query": "module", "limit": 0, "offset": -100})
        self.assertEqual(res_zero["limit"], 1)
        self.assertEqual(res_zero["offset"], 0)
        self.assertEqual(res_zero["returned"], 1)
        self.assertTrue(res_zero["has_more"])
        self.assertEqual(res_zero["next_offset"], 1)

        res_neg = self.server.handle_ssd_search({"query": "module", "limit": -50, "offset": 0})
        self.assertEqual(res_neg["limit"], 1)

        res_huge = self.server.handle_ssd_search({"query": "module", "limit": 9999999, "offset": 0})
        self.assertEqual(res_huge["limit"], 100)

        res_non_numeric = self.server.handle_ssd_search({"query": "module", "limit": "invalid", "offset": "bad"})
        self.assertEqual(res_non_numeric["limit"], 25)
        self.assertEqual(res_non_numeric["offset"], 0)

    def test_ssd_search_extreme_offset_beyond_total_count(self) -> None:
        """Offset beyond total file count returns 0 matches without crashing."""
        res = self.server.handle_ssd_search({"query": "module", "limit": 25, "offset": 500000})
        self.assertEqual(res["offset"], 500000)
        self.assertEqual(res["returned"], 0)
        self.assertEqual(res["matches"], [])
        self.assertFalse(res["has_more"])
        self.assertIsNone(res["next_offset"])

    def test_ssd_find_duplicates_pagination_and_size_filtering(self) -> None:
        """Duplicate detection pagination controls and min_size boundary clamping."""
        # Create identical duplicate files in mock root
        dup_dir = os.path.join(self.mock_root, "03_Development_Projects", "dups")
        os.makedirs(dup_dir, exist_ok=True)
        payload = b"IDENTICAL DUPLICATE CONTENT FOR TIER 5 HARDENING" * 10
        for i in range(5):
            with open(os.path.join(dup_dir, f"dup_file_{i}.dat"), "wb") as f:
                f.write(payload)

        # Boundary checks
        res = self.server.handle_ssd_find_duplicates({
            "min_size": -500,
            "limit": 0,
            "offset": -20,
        })
        self.assertEqual(res["limit"], 1)
        self.assertEqual(res["offset"], 0)
        self.assertGreaterEqual(res["duplicate_group_count"], 1)

        # Paged offset beyond count
        res_paged = self.server.handle_ssd_find_duplicates({
            "limit": 10,
            "offset": 999999,
        })
        self.assertEqual(res_paged["returned_group_count"], 0)
        self.assertFalse(res_paged["has_more"])
        self.assertIsNone(res_paged["next_offset"])

    def test_ssd_find_duplicates_large_group_truncation(self) -> None:
        """Duplicate groups with >10 files truncate preview and set truncated_files_count."""
        large_group_dir = os.path.join(self.mock_root, "04_Creative_Assets", "swarm")
        os.makedirs(large_group_dir, exist_ok=True)
        content = b"SWARM DATA BLOB" * 64
        for i in range(16):
            with open(os.path.join(large_group_dir, f"swarm_{i:02d}.bin"), "wb") as f:
                f.write(content)

        res = self.server.handle_ssd_find_duplicates({"sub_dir": "04_Creative_Assets/swarm"})
        self.assertGreaterEqual(len(res["duplicate_groups"]), 1)
        group = res["duplicate_groups"][0]
        # Max files per group is 10
        self.assertEqual(len(group["files"]), 10)
        self.assertEqual(group.get("truncated_files_count"), 6)

    def test_ssd_search_token_budget_truncation_exact_next_offset(self) -> None:
        """Token budgeting triggers truncation under non-compact mode and sets next_offset."""
        # Non-compact mode on 45 files exceeds 4800 char budget
        res_page1 = self.server.handle_ssd_search({"query": "module", "limit": 45, "compact": False})
        self.assertTrue(res_page1["truncated_to_token_limit"])
        self.assertTrue(res_page1["has_more"])
        self.assertIsNotNone(res_page1["next_offset"])
        self.assertEqual(res_page1["next_offset"], res_page1["offset"] + res_page1["returned"])

        # Fetch page 2 from next_offset
        next_off = res_page1["next_offset"]
        res_page2 = self.server.handle_ssd_search({"query": "module", "limit": 45, "offset": next_off, "compact": False})
        self.assertGreater(res_page2["returned"], 0)

        # Verify no overlap between page 1 and page 2 paths
        paths_p1 = {m["path"] for m in res_page1["matches"]}
        paths_p2 = {m["path"] for m in res_page2["matches"]}
        self.assertEqual(paths_p1.intersection(paths_p2), set(), "Page 1 and Page 2 must not overlap")


class TestMCPAdversarialPathSanitizationAndEscapes(SmartDriveTestCase):
    """White-box testing of boundary defenses against weird UNC, file URI, slashes, and escapes."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_file_uri_scheme_rejection(self) -> None:
        """file:// URI schemes are sandboxed or flagged in check_safety."""
        # handle_ssd_check_safety must detect forbidden colon in URI scheme
        res1 = self.server.handle_ssd_check_safety({"path": "file:///etc/passwd"})
        self.assertFalse(res1["is_safe"])
        self.assertIn("FORBIDDEN_CHAR::", res1["forbidden_character_violations"])

        res2 = self.server.handle_ssd_check_safety({"path": "file://C:/Windows"})
        self.assertFalse(res2["is_safe"])
        self.assertIn("FORBIDDEN_CHAR::", res2["forbidden_character_violations"])

    def test_deep_relative_escape_traversal(self) -> None:
        """Deep nested escapes and mixed slash dot combinations are strictly blocked."""
        adversarial_paths = [
            "sub1/sub2/sub3/../../../../outside.txt",
            "01_AI_Models//..//..//escaped.key",
            "nested/./././../../../../root_escape",
            "/../",
            "/../../../../etc/shadow",
            "..\\..\\Windows\\System32",
        ]
        for p in adversarial_paths:
            with self.assertRaises(ValueError, msg=f"Payload {p!r} was not blocked by _resolve_safe_path"):
                self.server._resolve_safe_path(p)

            # Check safety must also flag it as unsafe
            res = self.server.handle_ssd_check_safety({"path": p})
            self.assertFalse(res["is_safe"], f"handle_ssd_check_safety failed to reject {p!r}")
            self.assertIsNotNone(res.get("error"))

    def test_unc_and_device_namespaces_combinations(self) -> None:
        """UNC and Win32 device namespace paths are strictly rejected."""
        unc_payloads = [
            "\\\\attacker\\share\\payload.exe",
            "//attacker/share/payload.exe",
            "\\\\?\\C:\\Windows",
            "\\\\.\\PhysicalDrive0",
            "\\??\\GlobalRoot\\Device\\HarddiskVolume1",
            "//",
            "\\\\",
        ]
        for unc in unc_payloads:
            with self.assertRaises(ValueError, msg=f"UNC {unc!r} was not blocked by _resolve_safe_path"):
                self.server._resolve_safe_path(unc)

            res = self.server.handle_ssd_check_safety({"path": unc})
            self.assertFalse(res["is_safe"], f"handle_ssd_check_safety failed to reject UNC {unc!r}")

    def test_colon_in_inner_path_segment_flagged(self) -> None:
        """Colons in filenames or subpath segments are flagged as forbidden exFAT chars."""
        res = self.server.handle_ssd_check_safety({"path": "01_AI_Models/stream:alt.dat"})
        self.assertFalse(res["is_safe"])
        self.assertIn("FORBIDDEN_CHAR::", res["forbidden_character_violations"])

    def test_protected_root_taxonomies_and_files(self) -> None:
        """Root taxonomy directories and critical manifest files are guarded."""
        for prot_file in PROTECTED_ROOT_FILES:
            res = self.server.handle_ssd_check_safety({"path": prot_file})
            self.assertFalse(res["is_safe"])
            self.assertTrue(res["is_protected_root_file"])

        for prot_dir in PROTECTED_CORE_TAXONOMIES:
            res = self.server.handle_ssd_check_safety({"path": prot_dir})
            self.assertFalse(res["is_safe"])
            self.assertTrue(res["is_protected_root_dir"])

        # Inner valid files inside taxonomies are safe
        res_inner = self.server.handle_ssd_check_safety({"path": "01_AI_Models/llama3.gguf"})
        self.assertTrue(res_inner["is_safe"])
        self.assertFalse(res_inner["is_protected_root_file"])
        self.assertFalse(res_inner["is_protected_root_dir"])


class TestMCPAdversarialRegistrarHardening(SmartDriveTestCase):
    """White-box stress testing of multi-IDE registrar under hostile/missing environment."""

    def test_registrar_with_completely_unset_environment(self) -> None:
        """get_agent_config_paths operates reliably when environment variables are cleared."""
        with patch.dict(os.environ, {}, clear=True):
            paths = get_agent_config_paths()
            self.assertIn("antigravity", paths)
            self.assertIn("claude", paths)
            self.assertIn("cursor", paths)
            self.assertIn("windsurf", paths)
            self.assertTrue(str(paths["antigravity"]).endswith("mcp_config.json"))

        # Test Windows branch without APPDATA
        with patch("platform.system", return_value="Windows"):
            with patch.dict(os.environ, {}, clear=True):
                win_paths = get_agent_config_paths()
                self.assertIn("AppData", str(win_paths["claude"]))

    def test_registrar_corrupt_json_resilience(self) -> None:
        """load_json_config recovers from corrupted/syntax-broken JSON files."""
        temp_dir = tempfile.mkdtemp()
        try:
            bad_file = Path(temp_dir) / "broken.json"
            bad_file.write_text("{malformed_json: 'bad' missing_brace")
            loaded = load_json_config(bad_file)
            self.assertEqual(loaded, {})

            # register_ide_configs safely overwrites corrupt config
            res = register_ide_configs(target_dir=temp_dir)
            self.assertTrue(res.get("workspace"))
            recovered = load_json_config(Path(temp_dir) / ".mcp.json")
            self.assertIn("mcpServers", recovered)
            self.assertIn("smart-drive", recovered["mcpServers"])
        finally:
            shutil.rmtree(temp_dir)

    def test_registrar_array_and_primitive_json_resilience(self) -> None:
        """load_json_config and register_ide_configs safely handle non-object root JSON."""
        temp_dir = tempfile.mkdtemp()
        try:
            # Config is a JSON array
            arr_file = Path(temp_dir) / ".mcp.json"
            arr_file.write_text("[1, 2, 3, 4]")
            self.assertEqual(load_json_config(arr_file), {})

            # Registration resets to dict and adds smart-drive
            register_ide_configs(target_dir=temp_dir)
            cfg = load_json_config(arr_file)
            self.assertIsInstance(cfg, dict)
            self.assertIn("smart-drive", cfg.get("mcpServers", {}))
        finally:
            shutil.rmtree(temp_dir)

    def test_registrar_existing_other_servers_preserved(self) -> None:
        """Registration preserves third-party MCP servers in target config."""
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = Path(temp_dir) / ".mcp.json"
            existing_data = {
                "mcpServers": {
                    "existing_tool_alpha": {"command": "node", "args": ["index.js"]},
                    "existing_tool_beta": {"command": "python", "args": ["serve.py"]},
                }
            }
            save_json_config(cfg_path, existing_data)

            register_ide_configs(target_dir=temp_dir)
            updated = load_json_config(cfg_path)

            self.assertIn("smart-drive", updated["mcpServers"])
            self.assertIn("existing_tool_alpha", updated["mcpServers"])
            self.assertIn("existing_tool_beta", updated["mcpServers"])
        finally:
            shutil.rmtree(temp_dir)

    def test_registrar_non_existent_target_dir_creation(self) -> None:
        """register_ide_configs creates nested directories when target_dir does not exist."""
        temp_dir = tempfile.mkdtemp()
        try:
            deep_target = os.path.join(temp_dir, "nested", "level1", "level2", "workspace")
            self.assertFalse(os.path.exists(deep_target))

            results = register_ide_configs(target_dir=deep_target)
            self.assertTrue(results.get("workspace"))
            self.assertTrue(os.path.isfile(os.path.join(deep_target, ".mcp.json")))
        finally:
            shutil.rmtree(temp_dir)

    def test_registrar_selective_flags_and_auto_detect_boundaries(self) -> None:
        """Selective IDE flags register only requested targets; codex maps to cursor."""
        temp_dir = tempfile.mkdtemp()
        try:
            cursor_dir = Path(temp_dir) / "cursor_test"
            cursor_cfg = cursor_dir / "mcp.json"

            with patch("smart_drive.mcp.registrar.get_agent_config_paths", return_value={"cursor": cursor_cfg}):
                flags = {"codex": True}
                results = register_ide_configs(flags=flags)
                self.assertTrue(results.get("cursor"))
                self.assertTrue(cursor_cfg.is_file())
        finally:
            shutil.rmtree(temp_dir)


class TestMCPAdversarialCLICommands(SmartDriveTestCase):
    """White-box testing of CLI dispatchers in smart_drive/cli/."""

    def test_cli_cmd_mcp_register_action_forwarding(self) -> None:
        """cmd_mcp routes 'register' action cleanly to cmd_mcp_config."""
        temp_dir = tempfile.mkdtemp()
        try:
            args = argparse.Namespace(
                action="register",
                target_dir=temp_dir,
                workspace=False,
                all=False,
                antigravity=True,
                claude=False,
                cursor=False,
                codex=False,
                windsurf=False,
                json=True,
            )
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                exit_code = cmd_mcp(args)
                out = mock_out.getvalue().strip()

            self.assertEqual(exit_code, 0)
            data = json.loads(out)
            self.assertTrue(data.get("antigravity"))
            self.assertTrue(data.get("workspace"))
        finally:
            shutil.rmtree(temp_dir)

    def test_cli_cmd_mcp_config_json_and_human_output(self) -> None:
        """cmd_mcp_config handles both JSON and human-readable terminal output."""
        temp_dir = tempfile.mkdtemp()
        try:
            # Human readable output
            args_human = argparse.Namespace(
                target_dir=temp_dir,
                workspace=False,
                all=False,
                antigravity=True,
                claude=False,
                cursor=False,
                codex=False,
                windsurf=False,
                json=False,
            )
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                code_human = cmd_mcp_config(args_human)
                human_text = mock_out.getvalue()

            self.assertEqual(code_human, 0)
            self.assertIn("SmartDrive MCP Server Registration:", human_text)
            self.assertIn("antigravity", human_text)

            # JSON output
            args_json = argparse.Namespace(
                target_dir=temp_dir,
                workspace=False,
                all=False,
                antigravity=True,
                claude=False,
                cursor=False,
                codex=False,
                windsurf=False,
                json=True,
            )
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out_json:
                code_json = cmd_mcp_config(args_json)
                json_text = mock_out_json.getvalue()

            self.assertEqual(code_json, 0)
            parsed = json.loads(json_text)
            self.assertIsInstance(parsed, dict)
            self.assertTrue(parsed.get("antigravity"))
        finally:
            shutil.rmtree(temp_dir)

    def test_cli_main_detect_default_root_fallbacks(self) -> None:
        """detect_default_root falls back safely when mount detector returns None."""
        with patch("smart_drive.mcp.proxy.SmartDriveProxy.detect_mount_point", return_value=None):
            with patch("os.path.exists", return_value=False):
                root = detect_default_root()
                self.assertEqual(root, os.getcwd())

    def test_cli_main_get_default_db_path(self) -> None:
        """get_default_db_path prioritizes .smart_drive over legacy .smart_drive_manager."""
        temp_dir = tempfile.mkdtemp()
        try:
            new_db_dir = os.path.join(temp_dir, ".smart_drive")
            os.makedirs(new_db_dir, exist_ok=True)
            new_db = os.path.join(new_db_dir, "index.db")
            with open(new_db, "w") as f:
                f.write("")

            db_path = get_default_db_path(temp_dir)
            self.assertEqual(db_path, new_db)
        finally:
            shutil.rmtree(temp_dir)


if __name__ == "__main__":
    unittest.main()
