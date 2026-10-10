"""tests/test_mcp_grade_a.py - Comprehensive Test Suite for SmartDrive-OS MCP Grade A Upgrade.

100% Python Standard Library unittest. Zero external runtime dependencies.
Covers 4 core dimensions for MCP Grade A compliance (95-100/100 score):
1. Static AST Handler Isolation:
   - Class-level TOOL_HANDLERS mapping matching all 8 declared tools.
   - 100% static AST resolvable branching in dispatch_tool (ast.Compare with string constants).
   - Valid handler routing and ValueError rejection for unknown tools.
2. Tool Schema & Behavior Accuracy:
   - ssd_find_duplicates: min_size schema parameter (default 0) and size filtering.
   - ssd_check_safety: symlink detection (is_symlink, is_safe=False) and intermediate path segment auditing.
   - ssd_status: description accuracy covering database integrity, exFAT safety, and Git multi-repository status.
   - ssd_auto_organize: apply mode anti-indexing shield creation and search index synchronization.
   - ssd_audit: dual support for sub_dir and directory aliases.
3. Authentication Handshake & Access Control:
   - Zero-friction default stdio mode (require_auth=False, unauthenticated access permitted).
   - Protected mode (require_auth=True / auth_token configured):
     - tools/list and tools/call return JSON-RPC error -32001 when unauthenticated.
     - initialize, ping, notifications/initialized accessible without authentication.
     - auth/handshake fails with invalid/missing token (-32001).
     - auth/handshake succeeds with valid token, unblocking protected tool endpoints.
     - Inline token support via params (authToken, token) and _meta (authToken, token).
     - Session authentication during initialize handshake.
     - Constant-time verification using hmac.compare_digest.
     - Configuration precedence and CLI argument parsing (--auth-token, --require-auth, --no-require-auth).
4. Domain Consistency & Packaging Metadata:
   - Author, maintainer, and repository owner alignment with DuongNAD in pyproject.toml.
   - Privacy URL and MCP keyword tags in pyproject.toml.
   - Zero runtime dependencies invariant (dependencies = []).
   - MCP SERVER_VERSION synchronized to 1.1.0.
   - Package __version__, __author__, and LICENSE synchronized.
"""

from __future__ import annotations

import ast
import hmac
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    import tomllib
except ImportError:  # Python < 3.11: no stdlib TOML parser, the packaging-metadata tests are skipped
    tomllib = None  # type: ignore[assignment]

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import smart_drive
from smart_drive.cli.cmd_mcp import cmd_mcp
from smart_drive.cli.main import build_parser
from smart_drive.mcp.server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOLS,
    SmartDriveMCPServer,
)
from tests.helpers import SmartDriveTestCase


# ==============================================================================
# 1. Static AST Handler Isolation
# ==============================================================================

class TestASTHandlerIsolation(unittest.TestCase):
    """Verifies 100% static AST-resolvable handler mapping and tool dispatching."""

    def test_tool_handlers_class_attribute_exists_and_matches_tools(self) -> None:
        """SmartDriveMCPServer.TOOL_HANDLERS must be a class-level dictionary matching all 8 TOOLS."""
        self.assertTrue(hasattr(SmartDriveMCPServer, "TOOL_HANDLERS"))
        handlers = SmartDriveMCPServer.TOOL_HANDLERS
        self.assertIsInstance(handlers, dict)

        declared_tool_names = {tool["name"] for tool in TOOLS}
        self.assertEqual(len(declared_tool_names), 8)
        self.assertEqual(set(handlers.keys()), declared_tool_names)

        # Each handler must map to handle_ssd_<suffix> and be an existing callable method
        for tool_name, handler_name in handlers.items():
            self.assertEqual(handler_name, f"handle_{tool_name}")
            self.assertTrue(
                hasattr(SmartDriveMCPServer, handler_name),
                f"SmartDriveMCPServer missing handler method '{handler_name}' for tool '{tool_name}'",
            )
            self.assertTrue(
                callable(getattr(SmartDriveMCPServer, handler_name)),
                f"Handler '{handler_name}' on SmartDriveMCPServer must be callable",
            )

    def test_ast_dispatch_tool_static_comparison_chain(self) -> None:
        """Inspects dispatch_tool AST to verify all 8 tools are dispatched via static string comparisons."""
        server_path = PROJECT_ROOT / "smart_drive" / "mcp" / "server.py"
        self.assertTrue(server_path.is_file(), f"Cannot find server.py at {server_path}")

        source_code = server_path.read_text(encoding="utf-8")
        parsed_ast = ast.parse(source_code, filename=str(server_path))

        # Find SmartDriveMCPServer class definition
        server_class = next(
            (node for node in ast.walk(parsed_ast)
             if isinstance(node, ast.ClassDef) and node.name == "SmartDriveMCPServer"),
            None,
        )
        self.assertIsNotNone(server_class, "SmartDriveMCPServer class not found in AST")

        # Find dispatch_tool method
        dispatch_fn = next(
            (node for node in server_class.body
             if isinstance(node, ast.FunctionDef) and node.name == "dispatch_tool"),
            None,
        )
        self.assertIsNotNone(dispatch_fn, "dispatch_tool method not found in SmartDriveMCPServer AST")

        # Extract all string constants compared in ast.Compare nodes
        statically_compared_tools = set()
        for node in ast.walk(dispatch_fn):
            if isinstance(node, ast.Compare):
                # Verify left side is 'name'
                if isinstance(node.left, ast.Name) and node.left.id == "name":
                    for comparator in node.comparators:
                        if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                            statically_compared_tools.add(comparator.value)

        declared_tool_names = {t["name"] for t in TOOLS}
        self.assertEqual(
            statically_compared_tools,
            declared_tool_names,
            f"Static AST comparison mismatch. Missing: {declared_tool_names - statically_compared_tools}",
        )
        self.assertEqual(len(statically_compared_tools), 8)

    def test_ast_dispatch_tool_handler_method_calls(self) -> None:
        """Inspects dispatch_tool AST to verify each branch invokes self.handle_<tool>(args)."""
        server_path = PROJECT_ROOT / "smart_drive" / "mcp" / "server.py"
        parsed_ast = ast.parse(server_path.read_text(encoding="utf-8"))

        server_class = next(
            node for node in ast.walk(parsed_ast)
            if isinstance(node, ast.ClassDef) and node.name == "SmartDriveMCPServer"
        )
        dispatch_fn = next(
            node for node in server_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "dispatch_tool"
        )

        called_handlers = set()
        for node in ast.walk(dispatch_fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "self":
                        called_handlers.add(node.func.attr)

        expected_handlers = {f"handle_{t['name']}" for t in TOOLS}
        self.assertTrue(
            expected_handlers.issubset(called_handlers),
            f"Handlers not statically called in AST: {expected_handlers - called_handlers}",
        )

    def test_ast_dispatch_tool_raises_value_error_for_unknown(self) -> None:
        """Inspects dispatch_tool AST to verify the final branch raises ValueError."""
        server_path = PROJECT_ROOT / "smart_drive" / "mcp" / "server.py"
        parsed_ast = ast.parse(server_path.read_text(encoding="utf-8"))

        dispatch_fn = next(
            node for node in ast.walk(parsed_ast)
            if isinstance(node, ast.FunctionDef) and node.name == "dispatch_tool"
        )

        has_raise_value_error = False
        for node in ast.walk(dispatch_fn):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                if isinstance(node.exc.func, ast.Name) and node.exc.func.id == "ValueError":
                    has_raise_value_error = True
                    break

        self.assertTrue(has_raise_value_error, "dispatch_tool must raise ValueError on unknown tool")

    def test_runtime_dispatch_tool_routes_all_eight_tools(self) -> None:
        """Runtime execution of dispatch_tool forwards to the corresponding handle_ssd_* method."""
        with tempfile.TemporaryDirectory() as tmp_root:
            server = SmartDriveMCPServer(root=tmp_root)
            dummy_args = {"dummy_key": "dummy_val"}

            for tool_name, handler_name in SmartDriveMCPServer.TOOL_HANDLERS.items():
                mock_handler = MagicMock(return_value={"status": "mocked", "tool": tool_name})
                with patch.object(server, handler_name, mock_handler):
                    result = server.dispatch_tool(tool_name, dummy_args)
                    mock_handler.assert_called_once_with(dummy_args)
                    self.assertEqual(result, {"status": "mocked", "tool": tool_name})

    def test_runtime_dispatch_tool_unknown_name_raises_value_error(self) -> None:
        """Calling dispatch_tool with unknown or invalid tool names raises ValueError."""
        with tempfile.TemporaryDirectory() as tmp_root:
            server = SmartDriveMCPServer(root=tmp_root)
            for bad_name in ("unknown_tool", "", "__init__", "ssd_nonexistent", "123"):
                with self.assertRaises(ValueError) as ctx:
                    server.dispatch_tool(bad_name, {})
                self.assertIn("Unknown tool", str(ctx.exception))


# ==============================================================================
# 2. Tool Schema & Behavior Accuracy
# ==============================================================================

class TestToolSchemaAndBehaviorAccuracy(SmartDriveTestCase):
    """Verifies schema definitions, annotations, and real execution behaviors across MCP tools."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_find_duplicates_schema_and_description(self) -> None:
        """ssd_find_duplicates schema must declare min_size integer property (default: 0) and accurate description."""
        tool = next(t for t in TOOLS if t["name"] == "ssd_find_duplicates")

        # Description accuracy
        desc = tool["description"]
        self.assertIn("3-phase cascade", desc)
        self.assertIn("nominal", desc)
        self.assertIn("512KB physical cluster space savings", desc)

        # Schema property verification
        props = tool["inputSchema"]["properties"]
        self.assertIn("min_size", props)
        self.assertEqual(props["min_size"]["type"], "integer")
        self.assertEqual(props["min_size"]["default"], 0)
        self.assertIn("sub_dir", props)

    def test_find_duplicates_min_size_filtering_behavior(self) -> None:
        """ssd_find_duplicates filters duplicate groups based on min_size threshold."""
        dup_dir = self.mock_root / "03_Development_Projects" / "dups"
        dup_dir.mkdir(parents=True, exist_ok=True)

        # Group 1: Small duplicate files (128 bytes)
        small_content = b"S" * 128
        (dup_dir / "small_1.bin").write_bytes(small_content)
        (dup_dir / "small_2.bin").write_bytes(small_content)

        # Group 2: Large duplicate files (8192 bytes)
        large_content = b"L" * 8192
        (dup_dir / "large_1.bin").write_bytes(large_content)
        (dup_dir / "large_2.bin").write_bytes(large_content)

        # Call with min_size=0: both groups included
        plan_all = self.server.dispatch_tool("ssd_find_duplicates", {"sub_dir": "03_Development_Projects/dups", "min_size": 0})
        self.assertGreaterEqual(plan_all["duplicate_group_count"], 2)

        # Call with min_size=1024: small duplicates (128 bytes) must be filtered out
        plan_filtered = self.server.dispatch_tool("ssd_find_duplicates", {"sub_dir": "03_Development_Projects/dups", "min_size": 1024})
        self.assertGreaterEqual(plan_filtered["duplicate_group_count"], 1)
        for group in plan_filtered["duplicate_groups"]:
            self.assertGreaterEqual(group["size"], 1024)

        # Call with min_size=100000: zero groups match
        plan_none = self.server.dispatch_tool("ssd_find_duplicates", {"sub_dir": "03_Development_Projects/dups", "min_size": 100000})
        self.assertEqual(plan_none["duplicate_group_count"], 0)
        self.assertEqual(plan_none["total_reclaimable_bytes"], 0)
        self.assertEqual(plan_none["total_reclaimable_physical"], 0)

    def test_check_safety_symlink_detection(self) -> None:
        """handle_ssd_check_safety must detect symlinks and mark is_symlink=True and is_safe=False."""
        test_file = self.mock_root / "sample.txt"
        test_file.write_text("sample content", encoding="utf-8")

        # Test with real symlink if filesystem permits, otherwise mock
        symlink_path = self.mock_root / "sample_symlink.txt"
        symlink_created = False
        try:
            os.symlink(str(test_file), str(symlink_path))
            symlink_created = True
        except (OSError, NotImplementedError):
            pass

        if symlink_created:
            result = self.server.dispatch_tool("ssd_check_safety", {"path": "sample_symlink.txt"})
            self.assertTrue(result["is_symlink"])
            self.assertFalse(result["is_safe"])
        else:
            with patch("smart_drive.core.exfat_compat.ExFatEngine.is_symlink", return_value=True):
                result = self.server.dispatch_tool("ssd_check_safety", {"path": "sample.txt"})
                self.assertTrue(result["is_symlink"])
                self.assertFalse(result["is_safe"])

    def test_check_safety_intermediate_path_segments_and_forbidden_chars(self) -> None:
        """handle_ssd_check_safety inspects intermediate path segments for Windows forbidden characters."""
        # Forbidden characters in intermediate directory name
        bad_intermediate = "folder_normal/sub:dir/file.txt"
        res1 = self.server.dispatch_tool("ssd_check_safety", {"path": bad_intermediate})
        self.assertFalse(res1["is_safe"])
        self.assertTrue(
            any(":" in v for v in res1["forbidden_character_violations"]),
            f"Expected colon in violations, got: {res1['forbidden_character_violations']}",
        )

        # Wildcard in intermediate segment
        bad_wildcard = "models/checkpoint*/model.bin"
        res2 = self.server.dispatch_tool("ssd_check_safety", {"path": bad_wildcard})
        self.assertFalse(res2["is_safe"])
        self.assertTrue(
            any("*" in v for v in res2["forbidden_character_violations"]),
            f"Expected wildcard in violations, got: {res2['forbidden_character_violations']}",
        )

        # Valid clean multi-segment path
        clean_path = "01_AI_Models/GGUF/llama-3-8b.Q4_K_M.gguf"
        res3 = self.server.dispatch_tool("ssd_check_safety", {"path": clean_path})
        self.assertTrue(res3["is_safe"])
        self.assertFalse(res3["is_symlink"])
        self.assertEqual(res3["forbidden_character_violations"], [])

        # Windows drive prefix must not trigger false positive for colon
        drive_path = "D:\\01_AI_Models\\clean_file.txt"
        res4 = self.server.dispatch_tool("ssd_check_safety", {"path": drive_path})
        self.assertFalse(any(":" in v for v in res4["forbidden_character_violations"]))

    def test_ssd_status_description_and_behavior(self) -> None:
        """ssd_status description must mention database integrity, exFAT safety, and Git multi-repository status."""
        tool = next(t for t in TOOLS if t["name"] == "ssd_status")
        desc = tool["description"]
        self.assertIn("SQLite search database integrity", desc)
        self.assertIn("exFAT safety", desc)
        self.assertIn("Git multi-repository status", desc)

        # Check annotations
        ann = tool["annotations"]
        self.assertTrue(ann["readOnlyHint"])
        self.assertFalse(ann["destructiveHint"])
        self.assertTrue(ann["idempotentHint"])
        self.assertFalse(ann["openWorldHint"])

        # Runtime behavior
        res = self.server.dispatch_tool("ssd_status", {})
        self.assertIn("mount", res)
        self.assertIn("anti_indexing_shields", res)
        self.assertIn("database", res)
        self.assertIn("exfat_safety", res)
        self.assertIn("git_status", res)

    def test_ssd_auto_organize_apply_creates_shields_and_syncs_index(self) -> None:
        """ssd_auto_organize with apply=True creates anti-indexing markers and synchronizes search index."""
        # Remove any existing shields to test creation
        shield_file = self.mock_root / ".metadata_never_index"
        fsevents_shield = self.mock_root / ".fseventsd" / "no_log"
        if shield_file.exists():
            shield_file.unlink()
        if fsevents_shield.exists():
            fsevents_shield.unlink()

        # Dry run must not create shields
        dry_res = self.server.dispatch_tool("ssd_auto_organize", {"apply": False})
        self.assertEqual(dry_res["status"], "dry_run")
        self.assertIn("plan_count", dry_res)
        self.assertFalse(shield_file.exists())

        # Apply mode must create shields and sync index
        apply_res = self.server.dispatch_tool("ssd_auto_organize", {"apply": True})
        self.assertEqual(apply_res["status"], "applied")
        self.assertIn("shields_created", apply_res)
        self.assertIn("index_sync", apply_res)
        self.assertIsInstance(apply_res["shields_created"], list)
        self.assertIsInstance(apply_res["index_sync"], dict)

        # Verify shields exist on disk
        self.assertTrue(shield_file.is_file())
        self.assertTrue(fsevents_shield.is_file())

    def test_ssd_audit_sub_dir_and_directory_aliases(self) -> None:
        """ssd_audit schema and handler must support both sub_dir and directory aliases."""
        tool = next(t for t in TOOLS if t["name"] == "ssd_audit")
        props = tool["inputSchema"]["properties"]
        self.assertIn("sub_dir", props)
        self.assertIn("directory", props)

        # Description accuracy
        desc = tool["description"]
        self.assertIn("6 standard taxonomies", desc)
        self.assertIn("512KB exFAT cluster slack", desc)
        self.assertIn("top slack directories", desc)

        # Execution using sub_dir
        res_sub = self.server.dispatch_tool("ssd_audit", {"sub_dir": "01_AI_Models"})
        self.assertIn("total_files", res_sub)
        self.assertIn("total_slack_bytes", res_sub)

        # Execution using directory
        res_dir = self.server.dispatch_tool("ssd_audit", {"directory": "01_AI_Models"})
        self.assertEqual(res_sub["total_files"], res_dir["total_files"])
        self.assertEqual(res_sub["total_slack_bytes"], res_dir["total_slack_bytes"])


# ==============================================================================
# 3. Authentication Handshake & Access Control
# ==============================================================================

class TestAuthenticationAndAccessControl(SmartDriveTestCase):
    """Verifies token verification, handshake protocols, stdio transparent access, and endpoint protection."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()

    def test_default_stdio_mode_zero_friction(self) -> None:
        """Default stdio mode with no token provides zero-friction unauthenticated access."""
        server = SmartDriveMCPServer(root=str(self.mock_root))
        self.assertFalse(server.require_auth)
        self.assertTrue(server._authenticated)

        # tools/list succeeds immediately
        resp_list = server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        })
        self.assertIsNotNone(resp_list)
        self.assertIn("result", resp_list)
        self.assertIn("tools", resp_list["result"])
        self.assertEqual(len(resp_list["result"]["tools"]), 8)

        # tools/call succeeds immediately
        resp_call = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ssd_status", "arguments": {}},
        })
        self.assertIsNotNone(resp_call)
        self.assertIn("result", resp_call)
        self.assertIn("content", resp_call["result"])

    def test_protected_mode_blocks_unauthenticated_endpoints(self) -> None:
        """Protected mode blocks tools/list and tools/call with JSON-RPC -32001 error."""
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            auth_token="super_secret_token_123",
            require_auth=True,
        )
        self.assertTrue(server.require_auth)
        self.assertFalse(server._authenticated)

        # tools/list blocked
        resp_list = server.handle_request({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/list",
            "params": {},
        })
        self.assertIsNotNone(resp_list)
        self.assertIn("error", resp_list)
        self.assertEqual(resp_list["error"]["code"], -32001)
        self.assertIn("Authentication required", resp_list["error"]["message"])
        self.assertFalse(resp_list["error"]["data"]["authenticated"])

        # tools/call blocked
        resp_call = server.handle_request({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "ssd_status", "arguments": {}},
        })
        self.assertIsNotNone(resp_call)
        self.assertIn("error", resp_call)
        self.assertEqual(resp_call["error"]["code"], -32001)
        self.assertFalse(resp_call["error"]["data"]["authenticated"])

    def test_discovery_methods_accessible_without_authentication(self) -> None:
        """initialize, ping, and notifications/initialized succeed in protected mode before auth."""
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            auth_token="test_token",
            require_auth=True,
        )

        # initialize accessible
        init_resp = server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        self.assertIsNotNone(init_resp)
        self.assertIn("result", init_resp)
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], SERVER_NAME)
        self.assertEqual(init_resp["result"]["serverInfo"]["version"], SERVER_VERSION)

        # ping accessible
        ping_resp = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "ping",
            "params": {},
        })
        self.assertIsNotNone(ping_resp)
        self.assertEqual(ping_resp["result"], {})

        # notifications/initialized returns None
        notif_resp = server.handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        })
        self.assertIsNone(notif_resp)

    def test_handshake_flow_failure_and_success(self) -> None:
        """auth/handshake rejects incorrect tokens (-32001) and unblocks server on correct token."""
        token = "grade_a_auth_secret_xyz"
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            auth_token=token,
            require_auth=True,
        )

        # 1. Missing token
        res_empty = server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "auth/handshake",
            "params": {},
        })
        self.assertEqual(res_empty["error"]["code"], -32001)
        self.assertFalse(server._authenticated)

        # 2. Invalid token
        res_bad = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "auth/handshake",
            "params": {"token": "incorrect_secret"},
        })
        self.assertEqual(res_bad["error"]["code"], -32001)
        self.assertFalse(server._authenticated)

        # 3. Valid token
        res_good = server.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "auth/handshake",
            "params": {"token": token},
        })
        self.assertIn("result", res_good)
        self.assertTrue(res_good["result"]["authenticated"])
        self.assertEqual(res_good["result"]["status"], "authenticated")
        self.assertTrue(server._authenticated)

        # 4. Subsequent tools/list call succeeds without inline token
        res_list = server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/list",
            "params": {},
        })
        self.assertIn("result", res_list)
        self.assertIn("tools", res_list["result"])

    def test_inline_token_authentication(self) -> None:
        """Inline tokens via params (authToken, token) or _meta authenticate the session."""
        token = "inline_secret_key_456"

        # Test params.authToken
        srv1 = SmartDriveMCPServer(root=str(self.mock_root), auth_token=token, require_auth=True)
        res1 = srv1.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"authToken": token},
        })
        self.assertIn("result", res1)
        self.assertTrue(srv1._authenticated)

        # Test params._meta.authToken
        srv2 = SmartDriveMCPServer(root=str(self.mock_root), auth_token=token, require_auth=True)
        res2 = srv2.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": {"authToken": token}},
        })
        self.assertIn("result", res2)
        self.assertTrue(srv2._authenticated)

        # Test params._meta.token during initialize
        srv3 = SmartDriveMCPServer(root=str(self.mock_root), auth_token=token, require_auth=True)
        init_res = srv3.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "initialize",
            "params": {"_meta": {"token": token}},
        })
        self.assertIn("result", init_res)
        self.assertTrue(srv3._authenticated)
        # Next call requires no token
        list_res = srv3.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/list",
            "params": {},
        })
        self.assertIn("result", list_res)

    def test_constant_time_token_verification(self) -> None:
        """verify_token uses hmac.compare_digest and handles edge cases safely."""
        token = "secure_constant_time_token"
        server = SmartDriveMCPServer(root=str(self.mock_root), auth_token=token)

        # Valid token
        self.assertTrue(server.verify_token(token))

        # Invalid tokens
        self.assertFalse(server.verify_token("wrong_token"))
        self.assertFalse(server.verify_token(""))
        self.assertFalse(server.verify_token(None))
        self.assertFalse(server.verify_token("secure_constant_time_toke"))

        # Verify hmac.compare_digest is called
        with patch("hmac.compare_digest", wraps=hmac.compare_digest) as mock_compare:
            server.verify_token(token)
            mock_compare.assert_called_once()

    def test_auth_configuration_precedence_and_env_vars(self) -> None:
        """Tests parameter vs environment variable resolution precedence."""
        # 1. Parameter overrides environment variable
        with patch.dict(os.environ, {"SMART_DRIVE_MCP_AUTH_TOKEN": "env_token", "SMART_DRIVE_MCP_REQUIRE_AUTH": "0"}):
            srv = SmartDriveMCPServer(root=str(self.mock_root), auth_token="param_token", require_auth=True)
            self.assertEqual(srv.auth_token, "param_token")
            self.assertTrue(srv.require_auth)

        # 2. Environment variable used when parameter is None
        with patch.dict(os.environ, {"SMART_DRIVE_MCP_AUTH_TOKEN": "env_token", "SMART_DRIVE_MCP_REQUIRE_AUTH": "1"}):
            srv = SmartDriveMCPServer(root=str(self.mock_root))
            self.assertEqual(srv.auth_token, "env_token")
            self.assertTrue(srv.require_auth)

        # 3. SMART_DRIVE_MCP_REQUIRE_AUTH truthy variations
        for val in ("1", "true", "yes", "on", "TRUE", "True"):
            with patch.dict(os.environ, {"SMART_DRIVE_MCP_REQUIRE_AUTH": val}):
                srv = SmartDriveMCPServer(root=str(self.mock_root))
                self.assertTrue(srv.require_auth)

        # 4. SMART_DRIVE_MCP_REQUIRE_AUTH falsy variations
        for val in ("0", "false", "no", "off", "FALSE", "False"):
            with patch.dict(os.environ, {"SMART_DRIVE_MCP_REQUIRE_AUTH": val}):
                srv = SmartDriveMCPServer(root=str(self.mock_root))
                self.assertFalse(srv.require_auth)

    def test_cli_auth_arguments_and_cmd_mcp_forwarding(self) -> None:
        """build_parser parses --auth-token and --require-auth, and cmd_mcp forwards them."""
        parser = build_parser()

        # Parse --auth-token and --require-auth
        args1 = parser.parse_args(["mcp", "--auth-token", "cli_secret", "--require-auth"])
        self.assertEqual(args1.auth_token, "cli_secret")
        self.assertTrue(args1.require_auth)

        # Parse --no-require-auth
        args2 = parser.parse_args(["mcp", "--no-require-auth"])
        self.assertFalse(args2.require_auth)

        # Forwarding to SmartDriveMCPServer in cmd_mcp
        with patch("smart_drive.cli.cmd_mcp.SmartDriveMCPServer") as mock_srv_cls:
            mock_inst = MagicMock()
            mock_srv_cls.return_value = mock_inst
            args = parser.parse_args(["mcp", "--root", str(self.mock_root), "--auth-token", "tok", "--require-auth"])
            ret = cmd_mcp(args)
            self.assertEqual(ret, 0)
            mock_srv_cls.assert_called_once_with(
                root=str(self.mock_root),
                auth_token="tok",
                require_auth=True,
            )
            mock_inst.run_stdio.assert_called_once()


# ==============================================================================
# 4. Domain Consistency & Packaging Metadata
# ==============================================================================

@unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
class TestDomainConsistencyAndPackagingMetadata(unittest.TestCase):
    """Verifies packaging metadata, domain consistency, and zero runtime dependencies."""

    def setUp(self) -> None:
        pyproject_path = PROJECT_ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file(), "pyproject.toml not found")
        with open(pyproject_path, "rb") as f:
            self.pyproject = tomllib.load(f)

    def test_pyproject_toml_maintainers_and_authors(self) -> None:
        """pyproject.toml maintainers and authors must align with repository owner DuongNAD."""
        project = self.pyproject.get("project", {})

        # Maintainers check
        maintainers = project.get("maintainers", [])
        self.assertEqual(len(maintainers), 1)
        self.assertEqual(maintainers[0]["name"], "DuongNAD")
        self.assertEqual(maintainers[0]["email"], "smartdrive.os@proton.me")

        # Authors check
        authors = project.get("authors", [])
        self.assertGreaterEqual(len(authors), 2)
        author_names = [a["name"] for a in authors]
        self.assertIn("DuongNAD", author_names)
        self.assertIn("SmartDrive Team", author_names)
        self.assertEqual(authors[0]["name"], "DuongNAD")

    def test_pyproject_toml_urls_include_privacy(self) -> None:
        """project.urls must contain Homepage, Documentation, Repository, Issues, and Privacy."""
        urls = self.pyproject.get("project", {}).get("urls", {})
        self.assertIn("Privacy", urls)
        self.assertEqual(urls["Privacy"], "https://github.com/DuongNAD/smart-drive-os/blob/main/PRIVACY.md")
        self.assertIn("Homepage", urls)
        self.assertIn("Documentation", urls)
        self.assertIn("Repository", urls)
        self.assertIn("Issues", urls)
        self.assertIn("Changelog", urls)

    def test_pyproject_toml_keywords(self) -> None:
        """project.keywords must include 'mcp-server' and 'duongnad'."""
        keywords = self.pyproject.get("project", {}).get("keywords", [])
        self.assertIn("mcp-server", keywords)
        self.assertIn("duongnad", keywords)
        self.assertIn("mcp", keywords)
        self.assertIn("model-context-protocol", keywords)

    def test_pyproject_toml_dependencies_strictly_empty(self) -> None:
        """project.dependencies must remain strictly empty (zero external runtime dependencies)."""
        dependencies = self.pyproject.get("project", {}).get("dependencies", None)
        self.assertEqual(dependencies, [])

    def test_mcp_server_version(self) -> None:
        """smart_drive.mcp.server.SERVER_VERSION must be '1.2.0' matching package version."""
        self.assertEqual(SERVER_VERSION, "1.2.0")

    def test_smart_drive_init_metadata(self) -> None:
        """smart_drive package version and author must be synchronized."""
        self.assertEqual(smart_drive.__version__, "1.2.0")
        self.assertIn("DuongNAD", smart_drive.__author__)
        self.assertIn("SmartDrive Team", smart_drive.__author__)

    def test_license_authorship(self) -> None:
        """LICENSE file must cite DuongNAD."""
        license_path = PROJECT_ROOT / "LICENSE"
        self.assertTrue(license_path.is_file(), "LICENSE file not found")
        content = license_path.read_text(encoding="utf-8")
        self.assertIn("DuongNAD", content)


# ==============================================================================
# 5. Adversarial Verification & Boundary Stress
# ==============================================================================

class TestAdversarialHardening(SmartDriveTestCase):
    """Adversarial security tests: encoding, malformed inputs, boundary conditions, and stress."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()

    def test_adversarial_auth_token_utf8_and_unicode_symbols(self) -> None:
        """Token with multi-byte UTF-8, Vietnamese diacritics, and symbols verifies safely."""
        unicode_token = "khóa_bảo_mật_mcp_grade_a_2026_🇻🇳"
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            auth_token=unicode_token,
            require_auth=True,
        )
        # Accurate match
        self.assertTrue(server.verify_token(unicode_token))

        # Similar but different UTF-8 string
        self.assertFalse(server.verify_token("khóa_bảo_mật_mcp_grade_a_2026"))

        # Handshake with unicode token
        res = server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "auth/handshake",
            "params": {"token": unicode_token},
        })
        self.assertTrue(res["result"]["authenticated"])
        self.assertTrue(server._authenticated)

    def test_adversarial_auth_token_extremely_long(self) -> None:
        """Extremely large authentication tokens (65,536+ bytes) do not crash or hang server."""
        large_token = "K" * 65536
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            auth_token=large_token,
            require_auth=True,
        )
        self.assertTrue(server.verify_token(large_token))
        self.assertFalse(server.verify_token("K" * 65535))
        self.assertFalse(server.verify_token("K" * 65537))

    def test_adversarial_malformed_rpc_params(self) -> None:
        """Server tolerates malformed param containers (null, list, string, invalid _meta) without crashing."""
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            auth_token="valid_secret",
            require_auth=True,
        )

        # 1. params is null
        res1 = server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": None,
        })
        self.assertIn("result", res1)

        # 2. params is a string instead of dict
        res2 = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": "not_a_dict",
        })
        self.assertIn("result", res2)

        # 3. _meta is not a dict
        res3 = server.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "initialize",
            "params": {"_meta": "string_meta"},
        })
        self.assertIn("result", res3)

        # 4. _meta is None
        res4 = server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "initialize",
            "params": {"_meta": None},
        })
        self.assertIn("result", res4)

    def test_adversarial_check_safety_null_byte_and_path_traversal(self) -> None:
        """handle_ssd_check_safety safely traps null bytes, path escapes, and multiple violations."""
        server = SmartDriveMCPServer(root=str(self.mock_root))

        # 1. Null byte injection
        res_null = server.dispatch_tool("ssd_check_safety", {"path": "safe_folder/file\x00.txt"})
        self.assertFalse(res_null["is_safe"])
        self.assertIn("\\x00", res_null["forbidden_character_violations"])
        self.assertIn("null byte", res_null.get("error", "").lower())

        # 2. Path traversal escaping root
        res_escape = server.dispatch_tool("ssd_check_safety", {"path": "../../../../Windows/System32"})
        self.assertFalse(res_escape["is_safe"])
        self.assertIn("error", res_escape)

        # 3. Multiple forbidden characters in a single segment
        res_multi = server.dispatch_tool("ssd_check_safety", {"path": 'bad:*?"<>|/file.txt'})
        self.assertFalse(res_multi["is_safe"])
        violations = res_multi["forbidden_character_violations"]
        self.assertGreaterEqual(len(violations), 5)

        # 4. Empty and whitespace-only path
        res_empty = server.dispatch_tool("ssd_check_safety", {"path": ""})
        self.assertIn("error", res_empty)

    def test_adversarial_duplicate_detector_min_size_parsing(self) -> None:
        """Duplicate detection min_size parsing handles string integers, negative values, and non-ints."""
        server = SmartDriveMCPServer(root=str(self.mock_root))

        # String integer should be parsed safely
        plan1 = server.dispatch_tool("ssd_find_duplicates", {"min_size": "1000"})
        self.assertIsInstance(plan1, dict)

        # Negative value should be clamped to min_val 0
        plan2 = server.dispatch_tool("ssd_find_duplicates", {"min_size": -500})
        self.assertIsInstance(plan2, dict)

        # Non-integer string should fall back to default 0
        plan3 = server.dispatch_tool("ssd_find_duplicates", {"min_size": "not_a_valid_number"})
        self.assertIsInstance(plan3, dict)

    def test_adversarial_auto_organize_boolean_inputs(self) -> None:
        """_parse_bool correctly resolves truthy/falsy string variations without evaluating all strings as true."""
        self.assertTrue(SmartDriveMCPServer._parse_bool("apply"))
        self.assertTrue(SmartDriveMCPServer._parse_bool("true"))
        self.assertTrue(SmartDriveMCPServer._parse_bool("1"))
        self.assertTrue(SmartDriveMCPServer._parse_bool("yes"))
        self.assertTrue(SmartDriveMCPServer._parse_bool("on"))
        self.assertTrue(SmartDriveMCPServer._parse_bool(True))

        self.assertFalse(SmartDriveMCPServer._parse_bool("false"))
        self.assertFalse(SmartDriveMCPServer._parse_bool("0"))
        self.assertFalse(SmartDriveMCPServer._parse_bool("no"))
        self.assertFalse(SmartDriveMCPServer._parse_bool("off"))
        self.assertFalse(SmartDriveMCPServer._parse_bool("dry_run"))
        self.assertFalse(SmartDriveMCPServer._parse_bool(False))

        # Arbitrary unexpected strings default to False
        self.assertFalse(SmartDriveMCPServer._parse_bool("arbitrary_string", default=False))
        self.assertTrue(SmartDriveMCPServer._parse_bool("arbitrary_string", default=True))


if __name__ == "__main__":
    unittest.main()
