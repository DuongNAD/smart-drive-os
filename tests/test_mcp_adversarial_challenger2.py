"""tests/test_mcp_adversarial_challenger2.py - Adversarial Boundary & Path Traversal Verification.

EMPIRICAL CHALLENGER TEST SUITE:
Adversarially tests boundary defenses, path traversal sanitization, boolean coercion,
cross-drive handling, extreme integer bounds, and malformed payloads in smart_drive/mcp/server.py.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.config import PROTECTED_CORE_TAXONOMIES, PROTECTED_ROOT_FILES
from smart_drive.indexer.db import DatabaseManager
from smart_drive.mcp.server import PROTOCOL_VERSION, SERVER_NAME, TOOLS, SmartDriveMCPServer
from tests.helpers import SmartDriveTestCase


class TestMCPAdversarialPathTraversal(SmartDriveTestCase):
    """Adversarially tests path traversal, absolute drive, UNC, and null byte escapes."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_directory_escape_attacks_resolve_safe_path(self) -> None:
        """_resolve_safe_path must strictly block all escape payloads."""
        escape_payloads = [
            "../../../../",
            "../../../../Windows",
            "01_AI_Models/../../../../",
            "01_AI_Models/../../outside.txt",
            "C:\\",
            "C:\\Windows",
            "C:\\Windows\\System32\\cmd.exe",
            "D:\\other",
            "D:\\",
            "\\\\attacker\\share\\payload",
            "\\\\?\\C:\\Windows",
            "\\\\.\\C:\\Windows",
            "\\Windows",
            "/etc/passwd",
            "/var/log",
            "sub\x00dir",
            "01_AI_Models\x00/../../etc",
        ]
        for p in escape_payloads:
            with self.assertRaises((ValueError, FileNotFoundError), msg=f"Payload {p!r} was not blocked!"):
                self.server._resolve_safe_path(p)

    def test_path_traversal_blocked_in_tools_call_jsonrpc(self) -> None:
        """Invoking tools with path traversal via tools/call returns isError=True in content."""
        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        mcp_logger = logging.getLogger("smart_drive.mcp.server")
        prev_level = mcp_logger.level
        mcp_logger.setLevel(logging.CRITICAL)

        try:
            tools_to_test = [
                ("ssd_audit", {"sub_dir": "../../../../"}),
                ("ssd_clean", {"sub_dir": "C:\\Windows"}),
                ("ssd_find_duplicates", {"sub_dir": "\\\\attacker\\share"}),
                ("ssd_update_index", {"directory": "01_AI_Models/../../../../"}),
                ("ssd_search", {"directory": "/etc/passwd"}),
            ]
            for tool_name, args in tools_to_test:
                req = {
                    "jsonrpc": "2.0",
                    "id": 101,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": args},
                }
                resp = self.server.handle_request(req)
                self.assertIsNotNone(resp)
                self.assertTrue(resp["result"].get("isError", False), f"Tool {tool_name} did not set isError=True on traversal")
                error_msg = resp["result"]["content"][0]["text"]
                self.assertIn("Access denied", error_msg, f"Expected 'Access denied' error message, got: {error_msg}")
        finally:
            sys.stdout = orig_stdout
            mcp_logger.setLevel(prev_level)

    def test_check_safety_relative_traversal_vulnerability(self) -> None:
        """Verify handle_ssd_check_safety blocks prefix traversal (e.g. 01_AI_Models/../../outside.txt)."""
        # A relative traversal without leading '..' escaping the root must evaluate as unsafe
        res = self.server.handle_ssd_check_safety({"path": "01_AI_Models/../../outside_root.txt"})
        self.assertFalse(
            res.get("is_safe"),
            "Remediation verification: prefix relative traversal evaluates as is_safe=False in ssd_check_safety",
        )
        self.assertIn("escapes", res.get("error", "").lower())



class TestMCPAdversarialBooleanCoercion(SmartDriveTestCase):
    """Adversarially tests boolean coercion exploits on live files in ssd_clean and ssd_auto_organize."""

    def test_boolean_coercion_on_ssd_clean_live_files_intact(self) -> None:
        """Passing falsy strings ('false', '0', 'no', 'off', 'dry_run') must NEVER delete files."""
        temp_dir = tempfile.mkdtemp()
        try:
            # Create live data and junk
            model_dir = os.path.join(temp_dir, "01_AI_Models")
            os.makedirs(model_dir, exist_ok=True)
            live_model = os.path.join(model_dir, "model.gguf")
            with open(live_model, "wb") as f:
                f.write(b"CRITICAL AI WEIGHTS")

            junk_file = os.path.join(temp_dir, ".DS_Store")
            with open(junk_file, "wb") as f:
                f.write(b"junk")

            server = SmartDriveMCPServer(root=temp_dir)

            falsy_apply_values = ["false", "False", "FALSE", "0", "no", "NO", "off", "OFF", "dry_run", False, 0]
            for val in falsy_apply_values:
                res = server.handle_ssd_clean({"apply": val})
                self.assertTrue(res["dry_run"], f"dry_run must be True for apply={val!r}")
                self.assertTrue(os.path.exists(junk_file), f"Junk file was deleted when apply={val!r}!")
                self.assertTrue(os.path.exists(live_model), f"Live file was deleted when apply={val!r}!")

            # Test explicit apply=True purges junk but live model remains strictly untouched
            res_apply = server.handle_ssd_clean({"apply": "true", "tier": 1})
            self.assertFalse(res_apply["dry_run"])
            self.assertFalse(os.path.exists(junk_file), "Junk file should be purged on apply=True")
            self.assertTrue(os.path.exists(live_model), "LIVE FILE WAS DELETED ON APPLY=TRUE!")
        finally:
            shutil.rmtree(temp_dir)

    def test_boolean_coercion_on_ssd_auto_organize(self) -> None:
        """Passing falsy strings to ssd_auto_organize must NOT relocate loose items."""
        temp_dir = tempfile.mkdtemp()
        try:
            loose_file = os.path.join(temp_dir, "loose_doc.pdf")
            with open(loose_file, "wb") as f:
                f.write(b"DOCUMENT CONTENT")

            server = SmartDriveMCPServer(root=temp_dir)

            for val in ["false", "0", "no", "off", "dry_run", False, 0]:
                res = server.handle_ssd_auto_organize({"apply": val})
                self.assertEqual(res["status"], "dry_run", f"Status should be dry_run for apply={val!r}")
                self.assertTrue(os.path.exists(loose_file), f"File was moved when apply={val!r}!")
        finally:
            shutil.rmtree(temp_dir)


class TestMCPAdversarialCrossDriveHandling(SmartDriveTestCase):
    """Adversarially tests Windows cross-drive path handling in ssd_check_safety."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_cross_drive_path_in_check_safety(self) -> None:
        """Cross-drive paths (e.g. C:, Z:) return is_safe=False without ValueError crash."""
        current_drive = os.path.splitdrive(self.server.root)[0]
        other_drive = "C:" if current_drive.upper() != "C:" else "Z:"
        cross_path = f"{other_drive}\\Windows\\System32\\notepad.exe"

        res = self.server.handle_ssd_check_safety({"path": cross_path})
        self.assertFalse(res["is_safe"])
        self.assertIn("error", res)
        self.assertIn("different drive", res["error"].lower())

    def test_unc_paths_in_check_safety(self) -> None:
        """UNC network paths return is_safe=False without unhandled crash."""
        res = self.server.handle_ssd_check_safety({"path": "\\\\remote-server\\share\\exploit.exe"})
        self.assertFalse(res["is_safe"])
        self.assertIn("error", res)

    def test_null_bytes_in_check_safety(self) -> None:
        """Null bytes in path return is_safe=False and record violation."""
        res = self.server.handle_ssd_check_safety({"path": "normal_name.txt\x00.exe"})
        self.assertFalse(res["is_safe"])
        self.assertIn("\\x00", res["forbidden_character_violations"])
        self.assertIn("null byte", res.get("error", "").lower())


class TestMCPAdversarialIntegerBoundaries(SmartDriveTestCase):
    """Adversarially tests extreme, negative, and invalid integer parameters."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

        db = DatabaseManager(self.server.get_db_path())
        db.initialize_schema()
        db.close()

    def test_extreme_negative_integers_ssd_search(self) -> None:
        """Negative and extreme integer limits and offsets are clamped safely."""
        extreme_values = [-1, -100, -999999999999, 0, 1000000000]
        for val in extreme_values:
            res = self.server.handle_ssd_search({"query": "test", "limit": val, "offset": val})
            self.assertGreaterEqual(res["limit"], 1)
            self.assertLessEqual(res["limit"], 100)
            self.assertGreaterEqual(res["offset"], 0)

    def test_infinity_overflow_in_parse_int(self) -> None:
        """Verify float('inf') in _parse_int is handled safely without unhandled OverflowError."""
        try:
            res = self.server._parse_int(float("inf"), default=25, min_val=1, max_val=100)
            overflow_handled = True
        except OverflowError:
            overflow_handled = False
        self.assertTrue(overflow_handled, "float('inf') should be safely handled without OverflowError in _parse_int")
        self.assertEqual(res, 25)


class TestMCPAdversarialMalformedPayloads(SmartDriveTestCase):
    """Adversarially tests malformed payloads (non-dict arguments, null params, bad JSON)."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_non_dict_arguments_returns_error_32602(self) -> None:
        """Non-dict arguments in tools/call return JSON-RPC error code -32602."""
        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        try:
            bad_arguments = ["string_payload", [1, 2, 3], 42, 3.14, True]
            for arg in bad_arguments:
                req = {
                    "jsonrpc": "2.0",
                    "id": 999,
                    "method": "tools/call",
                    "params": {"name": "ssd_status", "arguments": arg},
                }
                resp = self.server.handle_request(req)
                self.assertIsNotNone(resp)
                self.assertIn("error", resp)
                self.assertEqual(resp["error"]["code"], -32602)
        finally:
            sys.stdout = orig_stdout

    def test_missing_or_null_params(self) -> None:
        """Null or missing params dictionary defaults safely without crash."""
        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        try:
            # params is None
            req1 = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": None}
            resp1 = self.server.handle_request(req1)
            self.assertIsNotNone(resp1)
            self.assertIn("result", resp1)

            # params is not a dict (e.g. list or string)
            req2 = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": "invalid"}
            resp2 = self.server.handle_request(req2)
            self.assertIsNotNone(resp2)
            self.assertIn("result", resp2)
        finally:
            sys.stdout = orig_stdout

    def test_unknown_tool_name_error_handling(self) -> None:
        """Calling unknown tool via tools/call returns isError=True."""
        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        mcp_logger = logging.getLogger("smart_drive.mcp.server")
        prev_level = mcp_logger.level
        mcp_logger.setLevel(logging.CRITICAL)

        try:
            req = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "non_existent_tool", "arguments": {}},
            }
            resp = self.server.handle_request(req)
            self.assertIsNotNone(resp)
            self.assertTrue(resp["result"].get("isError", False))
            self.assertIn("Unknown tool", resp["result"]["content"][0]["text"])
        finally:
            sys.stdout = orig_stdout
            mcp_logger.setLevel(prev_level)


if __name__ == "__main__":
    unittest.main()
