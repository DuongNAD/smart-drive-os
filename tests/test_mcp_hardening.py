"""tests/test_mcp_hardening.py - Comprehensive Unit Tests for MCP Hardening & Rate Limiting.

100% Python Standard Library unittest. Zero external dependencies.
Tests:
1. SlidingWindowRateLimiter:
   - Burst allowance up to max_requests
   - Throttle trigger returning (False, retry_after)
   - Window reset clearing state and current_load
   - Window slide / expiry (both simulated time and real-time)
   - Multi-threaded concurrency and thread safety under high load
   - Environment variable configuration (SMART_DRIVE_MCP_RATE_LIMIT_*)
   - Dual property/callable access on current_load
   - JSON-RPC stdio protocol integration with error code -32000
   - Unthrottled notifications/initialized handling
2. Input Sanitization & Boundary Protections:
   - Path traversal prevention via _resolve_safe_path across tools
   - Null byte injection (\x00) rejection
   - Boolean coercion safety preventing string 'false'/'0' from triggering live deletion
   - Bounds clamping and fallbacks for integer parameters (limit, offset, tier, min_size)
   - Cross-drive path safety on Windows in handle_ssd_check_safety
   - Malformed non-dictionary arguments in tools/call returning JSON-RPC -32602
3. MCP Tool Hint Annotations:
   - Verification of readOnlyHint, destructiveHint, idempotentHint, openWorldHint
   - Consistency between top-level tool schemas and annotations dictionaries
   - Invariant checks: idempotentHint=True for all, openWorldHint=False for all.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.indexer.db import DatabaseManager
from smart_drive.mcp.server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    TOOLS,
    SlidingWindowRateLimiter,
    SmartDriveMCPServer,
)
from tests.helpers import SmartDriveTestCase


class TestMCPRateLimiter(SmartDriveTestCase):
    """Verifies SlidingWindowRateLimiter behavior, concurrency, and JSON-RPC integration."""

    def setUp(self) -> None:
        super().setUp()
        self._orig_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._orig_env)
        super().tearDown()

    def test_burst_allowance(self) -> None:
        """Requests up to max_requests succeed immediately with allowed=True and retry_after=0.0."""
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)
        for i in range(5):
            allowed, retry_after = limiter.acquire()
            self.assertTrue(allowed, f"Request {i + 1} within burst capacity should be allowed")
            self.assertEqual(retry_after, 0.0)

        self.assertEqual(limiter.current_load, 5)
        self.assertEqual(limiter.current_load(), 5)

    def test_throttle_trigger(self) -> None:
        """Request exceeding max_requests within active window is throttled with positive retry_after."""
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=10.0)
        for _ in range(3):
            allowed, _ = limiter.acquire()
            self.assertTrue(allowed)

        # 4th request must be throttled
        allowed, retry_after = limiter.acquire()
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0.0)
        self.assertLessEqual(retry_after, 10.0)

    def test_window_reset(self) -> None:
        """Calling reset() clears all timestamps and resets current_load to 0."""
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=10.0)
        limiter.acquire()
        limiter.acquire()
        self.assertFalse(limiter.acquire()[0])
        self.assertEqual(limiter.current_load, 2)

        limiter.reset()
        self.assertEqual(limiter.current_load, 0)
        self.assertEqual(limiter.current_load(), 0)

        # Next request immediately allowed
        allowed, retry_after = limiter.acquire()
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0.0)

    def test_window_slide_simulated_time(self) -> None:
        """Window slides forward as simulated timestamps advance, permitting new requests."""
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=10.0)
        t0 = 1000.0

        # t=1000.0: request 1 allowed
        self.assertTrue(limiter.acquire(now=t0)[0])
        # t=1002.0: request 2 allowed
        self.assertTrue(limiter.acquire(now=t0 + 2.0)[0])
        # t=1004.0: request 3 allowed
        self.assertTrue(limiter.acquire(now=t0 + 4.0)[0])

        # t=1005.0: limit reached (3 in window [995.0, 1005.0])
        allowed, retry_after = limiter.acquire(now=t0 + 5.0)
        self.assertFalse(allowed)
        self.assertAlmostEqual(retry_after, 5.0, delta=0.01)

        # Advance to t=1010.5 (cutoff = 1000.5): request 1 (1000.0) has expired
        allowed, retry_after = limiter.acquire(now=t0 + 10.5)
        self.assertTrue(allowed, "Expired slot from t0 should permit a new request")
        self.assertEqual(retry_after, 0.0)

        # Advance past all initial timestamps: t=1020.0
        self.assertTrue(limiter.acquire(now=t0 + 20.0)[0])

    def test_window_slide_real_time(self) -> None:
        """Short-duration window naturally slides in real-time."""
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.05)
        self.assertTrue(limiter.acquire()[0])
        self.assertTrue(limiter.acquire()[0])
        self.assertFalse(limiter.acquire()[0])

        time.sleep(0.08)
        # Should now be permitted
        allowed, _ = limiter.acquire()
        self.assertTrue(allowed, "Real-time window expiry did not permit new request")

    def test_concurrency_thread_safety(self) -> None:
        """Concurrent requests across multiple threads strictly maintain rate limit invariants."""
        max_limit = 25
        limiter = SlidingWindowRateLimiter(max_requests=max_limit, window_seconds=30.0)

        results: List[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            for _ in range(10):
                allowed, _ = limiter.acquire()
                with lock:
                    results.append(allowed)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Total attempts: 100. Exactly max_limit must be allowed.
        self.assertEqual(len(results), 100)
        self.assertEqual(results.count(True), max_limit)
        self.assertEqual(results.count(False), 75)
        self.assertEqual(limiter.current_load, max_limit)

    def test_env_var_configuration_requests(self) -> None:
        """SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS sets max_requests via environment."""
        os.environ["SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS"] = "48"
        limiter = SlidingWindowRateLimiter()
        self.assertEqual(limiter.max_requests, 48)

    def test_env_var_configuration_window(self) -> None:
        """SMART_DRIVE_MCP_RATE_LIMIT_WINDOW sets window_seconds via environment."""
        os.environ["SMART_DRIVE_MCP_RATE_LIMIT_WINDOW"] = "15.5"
        limiter = SlidingWindowRateLimiter()
        self.assertEqual(limiter.window_seconds, 15.5)

    def test_env_var_configuration_disabled(self) -> None:
        """SMART_DRIVE_MCP_RATE_LIMIT_ENABLED=false completely disables rate limiting."""
        for disabled_val in ("false", "0", "no", "off"):
            os.environ["SMART_DRIVE_MCP_RATE_LIMIT_ENABLED"] = disabled_val
            limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=10.0)
            self.assertFalse(limiter.enabled)

            # High volume requests all succeed
            for _ in range(50):
                allowed, retry_after = limiter.acquire()
                self.assertTrue(allowed)
                self.assertEqual(retry_after, 0.0)

    def test_jsonrpc_rate_limit_error_response(self) -> None:
        """MCP server handle_request returns standard -32000 error with retry_after when throttled."""
        mock_root = self.create_mock_drive()
        server = SmartDriveMCPServer(
            root=str(mock_root),
            rate_limit_requests=2,
            rate_limit_window=10.0,
            rate_limit_enabled=True,
        )

        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        # Suppress expected logger warning during throttling test
        mcp_logger = logging.getLogger("smart_drive.mcp.server")
        prev_level = mcp_logger.level
        mcp_logger.setLevel(logging.ERROR)

        try:
            # Request 1 & 2 allowed
            req1 = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
            resp1 = server.handle_request(req1)
            self.assertEqual(resp1["result"], {})

            req2 = {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
            resp2 = server.handle_request(req2)
            self.assertEqual(resp2["result"], {})

            # Request 3 throttled
            req3 = {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}
            resp3 = server.handle_request(req3)
            self.assertIsNotNone(resp3)
            self.assertIn("error", resp3)
            self.assertEqual(resp3["error"]["code"], -32000)
            self.assertIn("Rate limit exceeded", resp3["error"]["message"])
            self.assertIn("data", resp3["error"])
            self.assertGreater(resp3["error"]["data"]["retry_after"], 0.0)
            self.assertEqual(resp3["error"]["data"]["max_requests"], 2)
            self.assertEqual(resp3["error"]["data"]["window_seconds"], 10.0)
        finally:
            sys.stdout = orig_stdout
            mcp_logger.setLevel(prev_level)

    def test_notifications_initialized_unthrottled(self) -> None:
        """notifications/initialized is not throttled and does not consume rate limit capacity."""
        mock_root = self.create_mock_drive()
        server = SmartDriveMCPServer(
            root=str(mock_root),
            rate_limit_requests=1,
            rate_limit_window=10.0,
            rate_limit_enabled=True,
        )

        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        try:
            # Notification should return None and not count
            notif_req = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            res = server.handle_request(notif_req)
            self.assertIsNone(res)
            self.assertEqual(server.rate_limiter.current_load, 0)

            # Legitimate request can still consume the 1 allowed slot
            ping_req = {"jsonrpc": "2.0", "id": 10, "method": "ping", "params": {}}
            resp = server.handle_request(ping_req)
            self.assertEqual(resp["result"], {})
        finally:
            sys.stdout = orig_stdout


class TestMCPInputSanitizationAndBoundaries(SmartDriveTestCase):
    """Verifies boundary sanitization, path traversal defense, and type coercion across all tools."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_resolve_safe_path_valid_subpaths(self) -> None:
        """Valid subpaths inside self.root resolve correctly to canonical real paths."""
        resolved = self.server._resolve_safe_path("01_AI_Models")
        expected = os.path.realpath(os.path.join(self.server.root, "01_AI_Models"))
        self.assertEqual(resolved, expected)

    def test_resolve_safe_path_none_and_empty_defaults_to_root(self) -> None:
        """None, empty string, or whitespace defaults safely to self.root."""
        canonical_root = os.path.realpath(self.server.root)
        self.assertEqual(self.server._resolve_safe_path(None), canonical_root)
        self.assertEqual(self.server._resolve_safe_path(""), canonical_root)
        self.assertEqual(self.server._resolve_safe_path("   "), canonical_root)

    def test_resolve_safe_path_traversal_relative_parent_rejected(self) -> None:
        """Relative path traversal escaping storage root raises ValueError."""
        escapes = [
            "../",
            "../../",
            "../../../Windows",
            "01_AI_Models/../../..",
            "..\\..\\Windows\\System32",
        ]
        for esc in escapes:
            with self.assertRaises(ValueError, msg=f"Should reject escape path: {esc}"):
                self.server._resolve_safe_path(esc)

    def test_resolve_safe_path_traversal_windows_root_rejected(self) -> None:
        """Absolute paths pointing outside storage root raise ValueError."""
        # Absolute path escape
        outside = "C:\\Windows" if os.name == "nt" else "/etc"
        with self.assertRaises(ValueError):
            self.server._resolve_safe_path(outside)

    def test_resolve_safe_path_null_byte_rejected(self) -> None:
        """Paths containing null bytes raise ValueError."""
        with self.assertRaises(ValueError):
            self.server._resolve_safe_path("sub\x00dir")

    def test_resolve_safe_path_must_exist_flag(self) -> None:
        """must_exist=True raises FileNotFoundError when path does not exist."""
        non_existent = "definitely_non_existent_dir_12345"
        with self.assertRaises(FileNotFoundError):
            self.server._resolve_safe_path(non_existent, must_exist=True)

        # Without must_exist=True, resolving a non-existent child inside root is allowed
        resolved = self.server._resolve_safe_path(non_existent, must_exist=False)
        self.assertTrue(resolved.startswith(os.path.realpath(self.server.root)))

    def test_path_traversal_prevention_across_all_five_tools(self) -> None:
        """All 5 MCP tools taking path arguments block path traversal escapes."""
        # 1. ssd_audit
        with self.assertRaises(ValueError):
            self.server.handle_ssd_audit({"sub_dir": "../../.."})

        # 2. ssd_clean
        with self.assertRaises(ValueError):
            self.server.handle_ssd_clean({"sub_dir": "../../.."})

        # 3. ssd_find_duplicates
        with self.assertRaises(ValueError):
            self.server.handle_ssd_find_duplicates({"sub_dir": "../../.."})

        # 4. ssd_update_index
        with self.assertRaises(ValueError):
            self.server.handle_ssd_update_index({"directory": "../../.."})

        # 5. ssd_search
        with self.assertRaises(ValueError):
            self.server.handle_ssd_search({"directory": "../../.."})

    def test_parse_bool_safely_coerces_false_strings(self) -> None:
        """_parse_bool safely maps common falsy representations to False."""
        falsy_inputs = ["false", "False", "FALSE", "0", "no", "off", "dry_run", False, 0, 0.0]
        for val in falsy_inputs:
            self.assertFalse(self.server._parse_bool(val, default=True), f"Failed for {val}")

    def test_parse_bool_safely_coerces_true_strings(self) -> None:
        """_parse_bool safely maps truthy representations to True."""
        truthy_inputs = ["true", "True", "TRUE", "1", "yes", "on", "apply", True, 1, 1.0]
        for val in truthy_inputs:
            self.assertTrue(self.server._parse_bool(val, default=False), f"Failed for {val}")

    def test_ssd_clean_boolean_coercion_prevents_accidental_apply(self) -> None:
        """String 'false' or '0' for apply does NOT trigger deletion in ssd_clean."""
        res_false = self.server.handle_ssd_clean({"apply": "false"})
        self.assertTrue(res_false["dry_run"], "String 'false' must maintain dry_run=True")

        res_zero = self.server.handle_ssd_clean({"apply": "0"})
        self.assertTrue(res_zero["dry_run"], "String '0' must maintain dry_run=True")

        res_bool_false = self.server.handle_ssd_clean({"apply": False})
        self.assertTrue(res_bool_false["dry_run"], "Boolean False must maintain dry_run=True")

        # Explicit apply=True initiates real action
        res_true = self.server.handle_ssd_clean({"apply": "true"})
        self.assertFalse(res_true["dry_run"], "String 'true' must set dry_run=False")

    def test_ssd_auto_organize_boolean_coercion_prevents_accidental_apply(self) -> None:
        """String 'false' or '0' for apply keeps ssd_auto_organize in dry_run simulation."""
        res_false = self.server.handle_ssd_auto_organize({"apply": "false"})
        self.assertEqual(res_false["status"], "dry_run")

        res_zero = self.server.handle_ssd_auto_organize({"apply": "0"})
        self.assertEqual(res_zero["status"], "dry_run")

    def test_parse_int_clamps_and_falls_back(self) -> None:
        """_parse_int clamps min and max bounds and falls back to default on invalid inputs."""
        self.assertEqual(self.server._parse_int("invalid", default=25), 25)
        self.assertEqual(self.server._parse_int(None, default=10), 10)
        self.assertEqual(self.server._parse_int(-5, default=1, min_val=0), 0)
        self.assertEqual(self.server._parse_int(500, default=25, min_val=1, max_val=100), 100)

    def test_ssd_search_limit_and_offset_clamping(self) -> None:
        """ssd_search clamps limit to [1, 100] and offset to [0, inf)."""
        db = DatabaseManager(self.server.get_db_path())
        db.initialize_schema()
        db.close()
        self.server.handle_ssd_update_index({})

        # Negative limit and offset
        res = self.server.handle_ssd_search({"query": "llama", "limit": -10, "offset": -5})
        self.assertEqual(res["limit"], 1)
        self.assertEqual(res["offset"], 0)

        # Huge limit
        res_huge = self.server.handle_ssd_search({"query": "llama", "limit": 9999})
        self.assertEqual(res_huge["limit"], 100)

        # Invalid string limit
        res_invalid = self.server.handle_ssd_search({"query": "llama", "limit": "not_a_number"})
        self.assertEqual(res_invalid["limit"], 25)

    def test_ssd_clean_tier_clamping(self) -> None:
        """ssd_clean clamps tier to [1, 3] and falls back safely."""
        res_low = self.server.handle_ssd_clean({"tier": -10})
        self.assertEqual(res_low["tier"], "TIER_1_SAFE")

        res_high = self.server.handle_ssd_clean({"tier": 99})
        self.assertEqual(res_high["tier"], "TIER_3_SENSITIVE")

    def test_ssd_find_duplicates_min_size_clamping(self) -> None:
        """ssd_find_duplicates clamps min_size to non-negative integer."""
        plan = self.server.handle_ssd_find_duplicates({"min_size": -500})
        self.assertIsInstance(plan, dict)
        self.assertIn("duplicate_groups", plan)

    def test_ssd_check_safety_cross_drive_path_handled_safely(self) -> None:
        """ssd_check_safety handles cross-drive paths on Windows without unhandled crashes."""
        # Construct path on another drive letter
        current_drive = os.path.splitdrive(self.server.root)[0]
        other_drive = "Z:" if current_drive.upper() != "Z:" else "X:"
        cross_drive_path = f"{other_drive}\\foreign_dir\\model.gguf"

        res = self.server.handle_ssd_check_safety({"path": cross_drive_path})
        self.assertIsInstance(res, dict)
        self.assertFalse(res["is_safe"])
        self.assertIn("different drive", res.get("error", "").lower())

    def test_ssd_check_safety_null_byte_detected(self) -> None:
        """ssd_check_safety detects null byte injections and marks path unsafe."""
        res = self.server.handle_ssd_check_safety({"path": "clean.txt\x00payload"})
        self.assertFalse(res["is_safe"])
        self.assertIn("\\x00", res["forbidden_character_violations"])
        self.assertIn("null byte", res.get("error", "").lower())

    def test_ssd_check_safety_missing_path_argument(self) -> None:
        """ssd_check_safety returns clean error when path argument is missing or empty."""
        res_empty = self.server.handle_ssd_check_safety({"path": ""})
        self.assertIn("error", res_empty)

        res_missing = self.server.handle_ssd_check_safety({})
        self.assertIn("error", res_missing)

    def test_jsonrpc_malformed_arguments_returns_error_32602(self) -> None:
        """Passing non-dictionary arguments in tools/call returns JSON-RPC error code -32602."""
        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        try:
            malformed_inputs = ["not_a_dict", [1, 2, 3], 12345, True]
            for bad_args in malformed_inputs:
                req = {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "tools/call",
                    "params": {"name": "ssd_status", "arguments": bad_args},
                }
                resp = self.server.handle_request(req)
                self.assertIsNotNone(resp)
                self.assertIn("error", resp)
                self.assertEqual(resp["error"]["code"], -32602)
                self.assertIn("Invalid params", resp["error"]["message"])

            # None arguments is safely converted to empty dict {}
            valid_none = {
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": {"name": "ssd_status", "arguments": None},
            }
            resp_none = self.server.handle_request(valid_none)
            self.assertIsNotNone(resp_none)
            self.assertIn("result", resp_none)
        finally:
            sys.stdout = orig_stdout


class TestMCPToolHintAnnotations(SmartDriveTestCase):
    """Verifies that all 8 MCP tools declare explicit boolean hints adhering to protocol specs."""

    def test_all_eight_tools_declare_hint_annotations(self) -> None:
        """All 8 tools must declare readOnlyHint, destructiveHint, idempotentHint, and openWorldHint."""
        self.assertEqual(len(TOOLS), 8)
        required_hints = ["readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"]

        for tool in TOOLS:
            name = tool.get("name")
            for hint in required_hints:
                self.assertIn(hint, tool, f"Tool '{name}' missing top-level hint '{hint}'")
                self.assertIsInstance(tool[hint], bool, f"Tool '{name}' top-level '{hint}' must be a bool")

                self.assertIn("annotations", tool, f"Tool '{name}' missing 'annotations' dictionary")
                self.assertIn(hint, tool["annotations"], f"Tool '{name}' annotations missing '{hint}'")
                self.assertIsInstance(
                    tool["annotations"][hint],
                    bool,
                    f"Tool '{name}' annotations '{hint}' must be a bool",
                )

    def test_all_tools_annotations_dict_matches_top_level_hints(self) -> None:
        """Top-level hint declarations must strictly match annotations dictionary values."""
        hints = ["readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"]
        for tool in TOOLS:
            name = tool.get("name")
            for h in hints:
                self.assertEqual(
                    tool[h],
                    tool["annotations"][h],
                    f"Tool '{name}' hint mismatch for '{h}' between top-level and annotations",
                )

    def test_all_tools_idempotent_hint_is_true(self) -> None:
        """Every tool in the suite is designed to be idempotent (idempotentHint == True)."""
        for tool in TOOLS:
            self.assertTrue(
                tool["idempotentHint"],
                f"Tool '{tool['name']}' must declare idempotentHint=True",
            )

    def test_all_tools_open_world_hint_is_false(self) -> None:
        """Every tool operates within local storage boundaries (openWorldHint == False)."""
        for tool in TOOLS:
            self.assertFalse(
                tool["openWorldHint"],
                f"Tool '{tool['name']}' must declare openWorldHint=False",
            )

    def test_specific_tool_read_only_and_destructive_hints(self) -> None:
        """Verify accurate categorization of read-only vs destructive operations."""
        tool_map = {t["name"]: t for t in TOOLS}

        # Read-only tools
        read_only_tools = ["ssd_search", "ssd_audit", "ssd_find_duplicates", "ssd_check_safety", "ssd_status"]
        for name in read_only_tools:
            tool = tool_map[name]
            self.assertTrue(tool["readOnlyHint"], f"{name} should be readOnly")
            self.assertFalse(tool["destructiveHint"], f"{name} should not be destructive")

        # Modifying / Destructive tools
        clean_tool = tool_map["ssd_clean"]
        self.assertFalse(clean_tool["readOnlyHint"])
        self.assertTrue(clean_tool["destructiveHint"])

        organize_tool = tool_map["ssd_auto_organize"]
        self.assertFalse(organize_tool["readOnlyHint"])
        self.assertTrue(organize_tool["destructiveHint"])

        update_tool = tool_map["ssd_update_index"]
        self.assertFalse(update_tool["readOnlyHint"])
        self.assertFalse(update_tool["destructiveHint"])


if __name__ == "__main__":
    unittest.main()
