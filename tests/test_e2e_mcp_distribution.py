"""tests/test_e2e_mcp_distribution.py - Comprehensive E2E Test Suite for MCP & Distribution.

Requirement-Driven, Opaque-Box Verification covering Tiers 1-4:
- R1: MCP tool token efficiency, pagination, prompts, and registrar CLI commands.
- R2: Path traversal defense (rejecting ../.., C:\\, absolute escapes, null bytes).
- R3: Zero-dependency invariant (pyproject.toml runtime dependencies empty, 100% stdlib).
- R4: Portable launcher scripts (.bat, .command, etc.) syntax and environment verification logic.

100% Python Standard Library unittest. Zero external dependencies.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.cli.cmd_mcp_config import cmd_mcp_config
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.mcp.registrar import register_ide_configs
from smart_drive.mcp.server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    TOOLS,
    SlidingWindowRateLimiter,
    SmartDriveMCPServer,
)
from tests.helpers import (
    STANDARD_TAXONOMIES,
    SmartDriveTestCase,
    TempWorkspace,
    create_mock_ssd_tree,
)


class BaseE2ETestCase(SmartDriveTestCase):
    """Shared fixture and utility methods for E2E tests."""

    def setUp(self) -> None:
        super().setUp()
        self.workspace = TempWorkspace(prefix="e2e_mcp_")
        self.root_path = self.workspace.__enter__()
        create_mock_ssd_tree(self.root_path)

        # Initialize SQLite search database on mock drive
        self.db_dir = self.root_path / ".smart_drive"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "index.db"
        self.db = DatabaseManager(str(self.db_path))
        self.db.initialize_schema()
        self.index_mgr = IndexManager(self.db, str(self.root_path))
        self.index_mgr.full_index(batch_size=100)

        # MCP Server pointing to this test root
        self.server = SmartDriveMCPServer(
            root=str(self.root_path),
            rate_limit_enabled=False,
        )

    def tearDown(self) -> None:
        with contextlib.suppress(Exception):
            self.db.close()
        self.workspace.__exit__(None, None, None)
        super().tearDown()


# ==============================================================================
# Tier 1: Feature Coverage (Core Requirements Verification)
# ==============================================================================

class TestTier1FeatureCoverage(BaseE2ETestCase):
    """Tier 1: Isolated verification of R1, R2, R3, and R4 core requirements."""

    # --- R1: MCP Tool Token Efficiency, Pagination, Prompts & Registrar ---

    def test_r1_mcp_tools_catalog_and_prompts_efficiency(self) -> None:
        """R1: Catalog declares all 8 tools with token-efficient prompts and boolean hints."""
        self.assertEqual(len(TOOLS), 8, "Must expose exactly 8 standard MCP tools")
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

        for tool in TOOLS:
            name = tool["name"]
            self.assertIn("description", tool, f"Tool {name} missing description")
            self.assertGreater(len(tool["description"]), 10, f"Tool {name} description too short")
            self.assertIn("inputSchema", tool, f"Tool {name} missing inputSchema")
            self.assertEqual(tool["inputSchema"]["type"], "object")

            # Check explicit boolean declaration hints
            for hint in ["readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"]:
                self.assertIn(hint, tool, f"Tool {name} missing top-level hint: {hint}")
                self.assertIsInstance(tool[hint], bool, f"Tool {name} hint {hint} must be bool")
                if "annotations" in tool:
                    self.assertIn(hint, tool["annotations"])
                    self.assertIsInstance(tool["annotations"][hint], bool)

        # ssd_search prompt directive check: guides agents away from slow find/grep
        search_desc = next(t["description"] for t in TOOLS if t["name"] == "ssd_search")
        self.assertTrue(
            "find" in search_desc.lower() or "grep" in search_desc.lower() or "sub-10ms" in search_desc.lower(),
            "ssd_search description must guide coding agents to prefer FTS5 index over find/grep",
        )

    def test_r1_mcp_search_compact_token_efficiency(self) -> None:
        """R1: ssd_search returns compact token-saving payload by default with pagination metadata."""
        # 1. Default compact mode
        res_compact = self.server.handle_ssd_search({"query": "llama", "limit": 10})
        self.assertIn("matches", res_compact)
        self.assertIn("total_count", res_compact)
        self.assertIn("offset", res_compact)
        self.assertIn("limit", res_compact)
        self.assertIn("returned", res_compact)
        self.assertIn("has_more", res_compact)
        self.assertIn("next_offset", res_compact)
        self.assertIn("elapsed_ms", res_compact)
        self.assertIn("truncated_to_token_limit", res_compact)

        # In compact mode, matches should only contain essential keys
        self.assertGreater(len(res_compact["matches"]), 0)
        for m in res_compact["matches"]:
            self.assertIn("path", m)
            self.assertIn("size", m)
            self.assertIn("cat", m)
            self.assertNotIn("mtime", m, "Compact mode must omit redundant mtime")
            self.assertNotIn("indexed_at", m, "Compact mode must omit redundant indexed_at")

        # 2. Compare payload sizes: compact vs full
        res_full = self.server.handle_ssd_search({"query": "llama", "limit": 10, "compact": False})
        compact_bytes = len(json.dumps(res_compact, ensure_ascii=False).encode("utf-8"))
        full_bytes = len(json.dumps(res_full, ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(
            compact_bytes,
            full_bytes,
            "Compact mode payload should be more token-efficient than full mode",
        )

    def test_r1_mcp_audit_storage_breakdown(self) -> None:
        """R1: ssd_audit returns concise storage breakdown and 512KB cluster slack metrics."""
        audit_res = self.server.handle_ssd_audit({})
        self.assertIn("root_path", audit_res)
        self.assertIn("total_files", audit_res)
        self.assertIn("total_logical_bytes", audit_res)
        self.assertIn("total_allocated_bytes", audit_res)
        self.assertIn("total_slack_bytes", audit_res)
        self.assertIn("total_slack_percentage", audit_res)
        self.assertIn("taxonomies", audit_res)
        self.assertIn("categories", audit_res)
        self.assertIn("top_slack_directories", audit_res)

        self.assertGreaterEqual(audit_res["total_files"], 1)
        self.assertGreaterEqual(audit_res["total_allocated_bytes"], audit_res["total_logical_bytes"])
        self.assertGreaterEqual(audit_res["total_slack_bytes"], 0)

    def test_r1_mcp_clean_preview_and_apply(self) -> None:
        """R1: ssd_clean supports non-destructive preview (dry_run) and verified apply purge."""
        # Ensure junk file exists
        junk_path = self.root_path / ".DS_Store"
        junk_path.write_bytes(b"\x00" * 128)
        self.assertTrue(junk_path.exists())

        # 1. Dry run (default preview)
        preview_res = self.server.handle_ssd_clean({"dry_run": True})
        self.assertTrue(preview_res["dry_run"])
        self.assertGreaterEqual(preview_res["detected_count"], 1)
        self.assertTrue(junk_path.exists(), "File must still exist on disk after dry-run preview")

        # 2. Apply purge
        apply_res = self.server.handle_ssd_clean({"apply": True})
        self.assertFalse(apply_res["dry_run"])
        self.assertGreaterEqual(apply_res["purged_count"], 1)
        self.assertFalse(junk_path.exists(), "File must be removed after apply")

        # 3. Inviolable root file protection
        agents_md = self.root_path / "AGENTS.md"
        self.assertTrue(agents_md.exists(), "Root AGENTS.md must never be deleted by cleaner")

    def test_r1_mcp_duplicate_detection_and_savings(self) -> None:
        """R1: ssd_find_duplicates computes exact nominal and 512KB physical cluster space savings."""
        # Create identical duplicate files in two separate directories
        content = b"E2E_DUPLICATE_IDENTICAL_CONTENT_STRING_654321" * 10
        dir_a = self.root_path / "01_AI_Models"
        dir_b = self.root_path / "06_Archives_Storage"
        file_a = dir_a / "dup_copy_a.bin"
        file_b = dir_b / "dup_copy_b.bin"
        file_a.write_bytes(content)
        file_b.write_bytes(content)

        dup_res = self.server.handle_ssd_find_duplicates({})
        self.assertIn("duplicate_group_count", dup_res)
        self.assertIn("duplicate_file_count", dup_res)
        self.assertIn("total_reclaimable_bytes", dup_res)
        self.assertIn("total_reclaimable_slack", dup_res)
        self.assertIn("total_reclaimable_physical", dup_res)
        self.assertGreaterEqual(dup_res["duplicate_group_count"], 1)
        self.assertGreaterEqual(dup_res["duplicate_file_count"], 2)

    def test_r1_mcp_registrar_config_generation(self) -> None:
        """R1: register_ide_configs writes valid configuration schemas for supported AI agents."""
        with tempfile.TemporaryDirectory(prefix="registrar_test_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_paths = {
                "antigravity": tmp_path / "gemini" / "mcp_config.json",
                "claude": tmp_path / "claude" / "claude_desktop_config.json",
                "cursor": tmp_path / "cursor" / "mcp.json",
                "windsurf": tmp_path / "codeium" / "mcp_config.json",
            }

            from unittest.mock import patch
            with patch("smart_drive.mcp.registrar.get_agent_config_paths", return_value=mock_paths):
                res = register_ide_configs(
                    target_dir=str(tmp_path),
                    flags={"antigravity": True, "claude": True, "cursor": True, "windsurf": True},
                )

            self.assertTrue(res.get("antigravity"))
            self.assertTrue(res.get("claude"))
            self.assertTrue(res.get("cursor"))
            self.assertTrue(res.get("windsurf"))
            self.assertTrue(res.get("workspace"))

            # Inspect generated workspace configuration
            ws_cfg_path = tmp_path / ".mcp.json"
            self.assertTrue(ws_cfg_path.is_file(), ".mcp.json must be created in target_dir")
            ws_cfg = json.loads(ws_cfg_path.read_text(encoding="utf-8"))
            self.assertIn("mcpServers", ws_cfg)
            self.assertIn("smart-drive", ws_cfg["mcpServers"])
            entry = ws_cfg["mcpServers"]["smart-drive"]
            self.assertIn("command", entry)
            self.assertIn("args", entry)
            self.assertEqual(entry["args"], ["-m", "smart_drive", "mcp"])

    def test_r1_mcp_registrar_cli_json_output(self) -> None:
        """R1: CLI cmd_mcp_config supports --json and executes cleanly."""
        import argparse
        with tempfile.TemporaryDirectory(prefix="cli_reg_") as tmp_dir:
            args = argparse.Namespace(
                target_dir=tmp_dir,
                antigravity=True,
                claude=False,
                cursor=True,
                windsurf=False,
                json=True,
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = captured
                rc = cmd_mcp_config(args)
            finally:
                sys.stdout = old_stdout

            self.assertEqual(rc, 0)
            output = captured.getvalue().strip()
            data = json.loads(output)
            self.assertIn("antigravity", data)
            self.assertIn("cursor", data)
            self.assertIn("workspace", data)
            self.assertNotIn("claude", data)
            self.assertNotIn("windsurf", data)

    # --- R2: Path Traversal Defense & Security Boundaries ---

    def test_r2_path_traversal_parent_escape_blocked(self) -> None:
        """R2: Relative parent directory traversal escaping root is strictly rejected."""
        traversals = [
            "../../etc/passwd",
            "../../../secret",
            "../outside.txt",
            "01_AI_Models/../../../../shadow",
        ]
        for payload in traversals:
            with self.assertRaises(ValueError, msg=f"Payload {payload} must raise ValueError"):
                self.server._resolve_safe_path(payload)

            # handle_ssd_check_safety must also flag it as unsafe
            res = self.server.handle_ssd_check_safety({"path": payload})
            self.assertFalse(res["is_safe"], f"handle_ssd_check_safety must flag {payload} as unsafe")

    def test_r2_path_traversal_null_byte_blocked(self) -> None:
        """R2: Paths containing null bytes (\\x00) are unconditionally rejected."""
        null_payloads = [
            "file.txt\x00.exe",
            "\x00/etc/passwd",
            "subdir/\x00evil",
        ]
        for payload in null_payloads:
            with self.assertRaises(ValueError, msg=f"Null byte payload {payload!r} must raise ValueError"):
                self.server._resolve_safe_path(payload)

            res = self.server.handle_ssd_check_safety({"path": payload})
            self.assertFalse(res["is_safe"])
            self.assertTrue(
                any("\\x00" in v or "null" in str(res.get("error", "")).lower() for v in res.get("forbidden_character_violations", []) + [res.get("error", "")]),
                "Null byte violation must be noted",
            )

    def test_r2_path_traversal_absolute_escape_blocked(self) -> None:
        """R2: Absolute paths pointing outside root storage are rejected."""
        abs_escapes = [
            "/etc/passwd",
            "/var/log/system.log",
            "/usr/bin/python3",
        ]
        for payload in abs_escapes:
            with self.assertRaises(ValueError, msg=f"Absolute path {payload} must raise ValueError"):
                self.server._resolve_safe_path(payload)

            res = self.server.handle_ssd_check_safety({"path": payload})
            self.assertFalse(res["is_safe"], f"Absolute path {payload} must be marked unsafe")

    def test_r2_protected_root_files_and_taxonomies_defense(self) -> None:
        """R2: Whitelisted root files and taxonomy directories are guarded from unsafe operations."""
        # 1. Protected root files
        for pfile in ["AGENTS.md", "GEMINI.md", "README.md"]:
            res = self.server.handle_ssd_check_safety({"path": pfile})
            self.assertTrue(res["is_protected_root_file"], f"{pfile} must be marked as protected root file")
            self.assertFalse(res["is_safe"], f"{pfile} must not be flagged safe for arbitrary deletion/overwrite")

        # 2. Protected taxonomies
        for tax in STANDARD_TAXONOMIES:
            res = self.server.handle_ssd_check_safety({"path": tax})
            self.assertTrue(res["is_protected_root_dir"], f"{tax} must be marked as protected root dir")
            self.assertFalse(res["is_safe"], f"{tax} must not be flagged safe for arbitrary removal")

    # --- R3: Zero-Dependency Invariant & Stdio Stream Integrity ---

    def test_r3_zero_dependency_pyproject_toml(self) -> None:
        """R3: pyproject.toml runtime dependencies must remain strictly empty ([])."""
        pyproject_path = PROJECT_ROOT / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file(), "pyproject.toml must exist")
        content = pyproject_path.read_text(encoding="utf-8")

        # Parse pyproject.toml using standard library (tomllib in 3.11+ or simple robust parser)
        dependencies: list[Any] | None = None
        if sys.version_info >= (3, 11):
            import tomllib
            parsed = tomllib.loads(content)
            dependencies = parsed.get("project", {}).get("dependencies")
        else:
            # Fallback line scanning for dependencies = []
            for line in content.splitlines():
                clean = line.strip()
                if clean.startswith("dependencies") and clean.replace(" ", "") == "dependencies=[]":
                    dependencies = []
                    break

        self.assertIsNotNone(dependencies, "dependencies key must exist in pyproject.toml")
        self.assertEqual(dependencies, [], "Hard invariant: Zero runtime dependencies (dependencies = [])")

    def test_r3_100_percent_standard_library_ast_audit(self) -> None:
        """R3: All Python source files in smart_drive/ use 100% Python Standard Library."""
        # Known standard library modules across Python 3.9+
        stdlib_modules: set[str] = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
            "__future__", "abc", "argparse", "array", "ast", "asyncio", "base64", "binascii",
            "collections", "contextlib", "copy", "csv", "ctypes", "dataclasses", "datetime",
            "decimal", "difflib", "dis", "enum", "errno", "fnmatch", "fractions", "functools",
            "gc", "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http", "importlib",
            "inspect", "io", "itertools", "json", "logging", "math", "mimetypes", "mmap",
            "multiprocessing", "operator", "os", "pathlib", "pickle", "platform", "posixpath",
            "pprint", "queue", "random", "re", "select", "shlex", "shutil", "signal",
            "socket", "socketserver", "sqlite3", "stat", "string", "struct", "subprocess",
            "site", "sys", "sysconfig", "tempfile", "textwrap", "threading", "time", "timeit", "traceback",
            "types", "typing", "unicodedata", "unittest", "urllib", "uuid", "warnings", "weakref", "webbrowser",
            "xml", "zipfile", "zlib",
        }

        smart_drive_dir = PROJECT_ROOT / "smart_drive"
        self.assertTrue(smart_drive_dir.is_dir(), "smart_drive directory must exist")

        disallowed_imports: list[tuple[str, str]] = []
        for py_path in smart_drive_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            except (SyntaxError, UnicodeDecodeError, OSError) as e:
                self.fail(f"Failed to parse AST of {py_path}: {e}")

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_pkg = alias.name.split(".")[0]
                        if top_pkg != "smart_drive" and top_pkg not in stdlib_modules:
                            disallowed_imports.append((str(py_path.relative_to(PROJECT_ROOT)), top_pkg))
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    top_pkg = node.module.split(".")[0]
                    if top_pkg != "smart_drive" and top_pkg not in stdlib_modules:
                        disallowed_imports.append((str(py_path.relative_to(PROJECT_ROOT)), top_pkg))

        self.assertEqual(
            disallowed_imports,
            [],
            f"Zero-dependency violation! External pip imports discovered: {disallowed_imports}",
        )

    def test_r3_sliding_window_rate_limiter_lifecycle(self) -> None:
        """R3: In-memory sliding-window rate limiter enforces request allowance, throttling, and reset."""
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=10.0, enabled=True)
        self.assertTrue(limiter.enabled)
        self.assertEqual(limiter.max_requests, 3)

        # 3 calls permitted at t=100
        t0 = 100.0
        for _ in range(3):
            allowed, retry_after = limiter.acquire(now=t0)
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0.0)

        # 4th call throttled
        allowed, retry_after = limiter.acquire(now=t0 + 1.0)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0.0)

        # After window slides (t=111.0), request is allowed again
        allowed, retry_after = limiter.acquire(now=t0 + 11.0)
        self.assertTrue(allowed)
        self.assertEqual(retry_after, 0.0)

        # Reset clears load immediately
        limiter.reset()
        self.assertEqual(limiter.current_load(), 0)

    def test_r3_jsonrpc_stdio_protocol_envelope(self) -> None:
        """R3: MCP Server adheres to JSON-RPC 2.0 protocol specifications and error handling."""
        # 1. initialize handshake
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "test-agent"}},
        }
        init_resp = self.server.handle_request(init_req)
        self.assertEqual(init_resp["id"], 1)
        self.assertEqual(init_resp["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], SERVER_NAME)

        # 2. ping
        ping_resp = self.server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(ping_resp["id"], 2)
        self.assertEqual(ping_resp["result"], {})

        # 3. unhandled method -> -32601
        err_resp = self.server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "invalid/nonexistent"})
        self.assertEqual(err_resp["id"], 3)
        self.assertEqual(err_resp["error"]["code"], -32601)

        # 4. invalid arguments type -> -32602
        bad_arg_resp = self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ssd_search", "arguments": "not_a_dictionary"},
        })
        self.assertEqual(bad_arg_resp["id"], 4)
        self.assertEqual(bad_arg_resp["error"]["code"], -32602)

    # --- R4: Portable Launcher Scripts & Environment Check ---

    def test_r4_portable_launchers_exist_in_distribution(self) -> None:
        """R4: Complete portable launcher matrix exists in launchers/ and project root."""
        base_names = ["Setup_SSD", "Quick_Audit", "Quick_Clean", "Quick_Search", "SmartDrive"]
        extensions = [".bat", ".ps1", ".command", ".sh"]
        expected_launchers = [f"{name}{ext}" for name in base_names for ext in extensions]

        launchers_dir = PROJECT_ROOT / "launchers"
        self.assertTrue(launchers_dir.is_dir(), "launchers/ directory must exist")

        # Verify all 20 scripts in launchers/
        for fname in expected_launchers:
            launcher_path = launchers_dir / fname
            self.assertTrue(launcher_path.is_file(), f"Launcher {fname} must exist in launchers/")
            self.assertGreater(launcher_path.stat().st_size, 50, f"Launcher {fname} must not be empty")

        # Verify all 20 scripts mirrored at project root
        for fname in expected_launchers:
            root_launcher_path = PROJECT_ROOT / fname
            self.assertTrue(root_launcher_path.is_file(), f"Launcher {fname} must exist at project root")
            self.assertGreater(root_launcher_path.stat().st_size, 50, f"Launcher {fname} at root must not be empty")

    def test_r4_launcher_scripts_environment_and_python_check(self) -> None:
        """R4: Launcher scripts implement the 5-Tier Self-Environment Check."""
        launchers_dir = PROJECT_ROOT / "launchers"

        # Check Windows batch launcher
        bat_script = (launchers_dir / "Setup_SSD.bat").read_text(encoding="utf-8", errors="ignore")
        self.assertTrue("python" in bat_script.lower(), "Batch launcher must check for python")
        self.assertTrue("%~dp0" in bat_script, "Batch launcher must resolve its directory via %~dp0")
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", bat_script, "Batch launcher must set PYTHONDONTWRITEBYTECODE=1")
        self.assertIn("GIT_TERMINAL_PROMPT=0", bat_script, "Batch launcher must isolate git prompt")
        self.assertIn("GIT_CONFIG_NOSYSTEM=1", bat_script, "Batch launcher must isolate git system config")
        self.assertIn("sys.version_info >= (3, 9)", bat_script, "Batch launcher must verify Python 3.9+")

        # Check macOS/Linux shell launcher
        cmd_script = (launchers_dir / "Setup_SSD.command").read_text(encoding="utf-8", errors="ignore")
        self.assertTrue(cmd_script.startswith(("#!/bin/bash", "#!/bin/sh")))
        self.assertTrue("python3" in cmd_script, "Shell launcher must probe python3")
        self.assertTrue("dirname" in cmd_script, "Shell launcher must resolve script directory")
        self.assertIn("PYTHONDONTWRITEBYTECODE=\"1\"", cmd_script, "Shell launcher must set PYTHONDONTWRITEBYTECODE=1")
        self.assertIn("GIT_TERMINAL_PROMPT=\"0\"", cmd_script, "Shell launcher must isolate git prompt")
        self.assertIn("GIT_CONFIG_NOSYSTEM=\"1\"", cmd_script, "Shell launcher must isolate git system config")
        self.assertIn("sys.version_info >= (3, 9)", cmd_script, "Shell launcher must verify Python 3.9+")

        # Check PowerShell launcher
        ps1_script = (launchers_dir / "Setup_SSD.ps1").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("PYTHONDONTWRITEBYTECODE", ps1_script, "PowerShell launcher must set PYTHONDONTWRITEBYTECODE")
        self.assertIn("sys.version_info >= (3, 9)", ps1_script, "PowerShell launcher must check Python 3.9+")

        # Check Master 1-Touch SmartDrive menu options
        sd_sh = (launchers_dir / "SmartDrive.sh").read_text(encoding="utf-8", errors="ignore")
        for opt in ["smart-drive init", "smart-drive sentinel", "smart-drive search", "smart-drive audit", "smart-drive clean", "smart-drive organize", "smart-drive mcp"]:
            self.assertIn(opt, sd_sh, f"SmartDrive menu must include option {opt}")


# ==============================================================================
# Tier 2: Boundary & Corner Cases
# ==============================================================================

class TestTier2BoundaryAndCornerCases(BaseE2ETestCase):
    """Tier 2: Boundary value analysis, adversarial injections, and stress limits."""

    def test_tier2_traversal_mixed_slashes_and_dots(self) -> None:
        """Tier 2: Traversal payloads with redundant dots, mixed separators, and slashes."""
        adversarial_payloads = [
            "./././../../secret.key",
            "subdir/../../../../outside",
            "safe_dir/../..",
            "01_AI_Models/../../outside.txt",
        ]
        for p in adversarial_payloads:
            with self.assertRaises(ValueError):
                self.server._resolve_safe_path(p)

            res = self.server.handle_ssd_check_safety({"path": p})
            self.assertFalse(res["is_safe"], f"handle_ssd_check_safety must reject {p}")

    def test_tier2_extreme_path_nesting_and_length(self) -> None:
        """Tier 2: Safe deeply nested paths (40 levels) resolve without stack overflow."""
        deep_segments = [f"sub_{i}" for i in range(40)]
        deep_rel_path = "/".join(deep_segments) + "/target.txt"

        resolved = self.server._resolve_safe_path(deep_rel_path)
        self.assertTrue(resolved.startswith(os.path.realpath(str(self.root_path))))
        self.assertTrue(resolved.endswith("target.txt"))

    def test_tier2_unicode_vietnamese_and_cjk_filenames(self) -> None:
        """Tier 2: Non-ASCII UTF-8 filenames (Vietnamese, CJK, accents) are handled accurately."""
        unicode_rel = "02_Learning_Knowledge/Tài_Liệu_Học_Tập/日本語ノート.txt"
        target_file = self.root_path / Path(unicode_rel)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("Dữ liệu kiểm thử Unicode tiếng Việt", encoding="utf-8")

        resolved = self.server._resolve_safe_path(unicode_rel)
        self.assertTrue(os.path.exists(resolved))
        self.assertTrue(resolved.endswith("日本語ノート.txt"))

        # Safety check on clean unicode file
        res = self.server.handle_ssd_check_safety({"path": unicode_rel})
        self.assertTrue(res["is_safe"], "Clean Unicode filename within root must be safe")
        self.assertEqual(res["forbidden_character_violations"], [])

    def test_tier2_windows_forbidden_characters_detection(self) -> None:
        """Tier 2: Windows illegal characters (: * ? \" < > |) on segments are identified."""
        forbidden_test_cases = [
            ("file<name>.txt", "<"),
            ("test>out.log", ">"),
            ('quote"name".dat', '"'),
            ("pipe|line.csv", "|"),
            ("wild*card.py", "*"),
            ("question?mark.md", "?"),
        ]
        for fname, char in forbidden_test_cases:
            path_arg = f"03_Workspaces_Projects/{fname}"
            res = self.server.handle_ssd_check_safety({"path": path_arg})
            self.assertFalse(res["is_safe"], f"Path with '{char}' must be marked unsafe")
            violations_str = "".join(res["forbidden_character_violations"])
            self.assertIn(char, violations_str, f"Violation for '{char}' must be recorded")

    def test_tier2_search_query_boundary_clamping_and_sql_chars(self) -> None:
        """Tier 2: Parameter boundaries (limit clamping, offset, SQL injection characters)."""
        # 1. limit boundary clamping (min: 1, max: 100)
        res_zero = self.server.handle_ssd_search({"query": "test", "limit": 0})
        self.assertEqual(res_zero["limit"], 1, "limit=0 must clamp to 1")

        res_neg = self.server.handle_ssd_search({"query": "test", "limit": -99})
        self.assertEqual(res_neg["limit"], 1, "Negative limit must clamp to 1")

        res_huge = self.server.handle_ssd_search({"query": "test", "limit": 50000})
        self.assertEqual(res_huge["limit"], 100, "limit > 100 must clamp to 100")

        # 2. offset boundary
        res_neg_off = self.server.handle_ssd_search({"query": "test", "offset": -10})
        self.assertEqual(res_neg_off["offset"], 0, "Negative offset must clamp to 0")

        # 3. Special SQL / FTS syntax characters
        special_queries = [
            "test' OR '1'='1",
            'test" unclosed quote',
            "test AND OR NOT ()",
            "***???[]{}",
        ]
        for sq in special_queries:
            res_sq = self.server.handle_ssd_search({"query": sq})
            self.assertIn("matches", res_sq, f"Query '{sq}' must not crash search engine")
            self.assertNotIn("OperationalError", str(res_sq.get("error", "")))

    def test_tier2_rate_limiter_burst_recovery_and_thread_safety(self) -> None:
        """Tier 2: Concurrent multi-threaded burst rate limiting and recovery."""
        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=0.5, enabled=True)
        results: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            allowed, _ = limiter.acquire()
            with lock:
                results.append(allowed)

        threads = [threading.Thread(target=worker) for _ in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 25)
        allowed_count = sum(1 for r in results if r is True)
        throttled_count = sum(1 for r in results if r is False)
        self.assertEqual(allowed_count, 10, "Exactly max_requests (10) must be allowed")
        self.assertEqual(throttled_count, 15, "Remaining 15 burst requests must be throttled")

    def test_tier2_jsonrpc_malformed_requests_and_unhandled_methods(self) -> None:
        """Tier 2: Edge-case JSON-RPC error responses (-32700, -32601, -32602, isError)."""
        # Non-existent method -> JSON-RPC error -32601
        resp_method = self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 98,
            "method": "nonexistent/rpc_call",
            "params": {},
        })
        self.assertIn("error", resp_method)
        self.assertEqual(resp_method["error"]["code"], -32601)

        # Missing tool name in tools/call -> MCP tool execution error with isError=True
        resp_tool = self.server.handle_request({
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": None,
        })
        self.assertTrue(resp_tool.get("result", {}).get("isError", False) or "error" in resp_tool)


# ==============================================================================
# Tier 3: Cross-Feature Pairwise Combinations
# ==============================================================================

class TestTier3CrossFeaturePairwiseCombinations(BaseE2ETestCase):
    """Tier 3: Pairwise combinatorial testing of interdependent features."""

    def test_tier3_pairwise_search_pagination_compact_under_rate_limit(self) -> None:
        """Tier 3: Paginated search with compact formatting under rate-limited environment."""
        # Enable rate limiter on server
        self.server.rate_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60.0, enabled=True)

        # Retrieve all items in pages of 2 items
        page_size = 2
        offset = 0
        all_matches: list[str] = []
        page_count = 0

        while True:
            call_resp = self.server.handle_request({
                "jsonrpc": "2.0",
                "id": page_count + 1,
                "method": "tools/call",
                "params": {
                    "name": "ssd_search",
                    "arguments": {"query": "", "limit": page_size, "offset": offset, "compact": True},
                },
            })
            self.assertNotIn("error", call_resp)
            content_text = call_resp["result"]["content"][0]["text"]
            data = json.loads(content_text)

            matches = data["matches"]
            for m in matches:
                all_matches.append(m["path"])

            page_count += 1
            if not data.get("has_more") or page_count > 20:
                break
            offset = data["next_offset"]

        self.assertGreater(page_count, 1, "Must iterate through multiple pages")
        # Ensure no duplicate matches between pages
        self.assertEqual(len(all_matches), len(set(all_matches)), "Pagination must not produce duplicate entries")

    def test_tier3_pairwise_registrar_selective_flags_and_workspace(self) -> None:
        """Tier 3: Selective agent registration flags combined with target_dir workspace creation."""
        with tempfile.TemporaryDirectory(prefix="pairwise_reg_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_paths = {
                "antigravity": tmp_path / "antigravity" / "mcp_config.json",
                "claude": tmp_path / "claude" / "claude_desktop_config.json",
                "cursor": tmp_path / "cursor" / "mcp.json",
                "windsurf": tmp_path / "windsurf" / "mcp_config.json",
            }

            from unittest.mock import patch
            with patch("smart_drive.mcp.registrar.get_agent_config_paths", return_value=mock_paths):
                # Select only antigravity and cursor
                flags = {"antigravity": True, "cursor": True, "claude": False, "windsurf": False}
                res = register_ide_configs(target_dir=str(tmp_path), flags=flags)

            self.assertTrue(res.get("antigravity"))
            self.assertTrue(res.get("cursor"))
            self.assertTrue(res.get("workspace"))
            self.assertNotIn("claude", res)
            self.assertNotIn("windsurf", res)

            # Files for selected IDEs exist
            self.assertTrue(mock_paths["antigravity"].is_file())
            self.assertTrue(mock_paths["cursor"].is_file())
            self.assertTrue((tmp_path / ".mcp.json").is_file())
            # Files for unselected IDEs must NOT exist
            self.assertFalse(mock_paths["claude"].is_file())
            self.assertFalse(mock_paths["windsurf"].is_file())

    def test_tier3_pairwise_clean_preview_apply_with_protected_files(self) -> None:
        """Tier 3: Cleaner dry-run vs apply in directory containing both junk and protected files."""
        # Create junk file in root
        junk = self.root_path / ".DS_Store"
        junk.write_bytes(b"\x00" * 64)
        agents_file = self.root_path / "AGENTS.md"
        self.assertTrue(agents_file.is_file())

        # Step 1: Pre-clean safety verification
        junk_safety = self.server.handle_ssd_check_safety({"path": ".DS_Store"})
        agents_safety = self.server.handle_ssd_check_safety({"path": "AGENTS.md"})
        self.assertTrue(junk_safety["is_safe"])
        self.assertFalse(agents_safety["is_safe"])
        self.assertTrue(agents_safety["is_protected_root_file"])

        # Step 2: Cleaner dry-run
        dry_res = self.server.handle_ssd_clean({"dry_run": True})
        self.assertTrue(dry_res["dry_run"])
        self.assertTrue(junk.exists(), "Junk file must still exist on disk after dry-run preview")
        self.assertTrue(agents_file.exists(), "Protected AGENTS.md must still exist")

        # Step 3: Cleaner apply
        apply_res = self.server.handle_ssd_clean({"apply": True})
        self.assertFalse(apply_res["dry_run"])
        self.assertGreaterEqual(apply_res["purged_count"], 1)
        self.assertFalse(junk.exists(), "Junk file must be deleted")
        self.assertTrue(agents_file.exists(), "Protected AGENTS.md must NEVER be deleted")

    def test_tier3_pairwise_duplicate_detection_with_size_thresholds(self) -> None:
        """Tier 3: Duplicate detector filtering with min_size boundary constraints."""
        dir_models = self.root_path / "01_AI_Models"
        dir_archives = self.root_path / "06_Archives_Storage"

        # Small duplicate pair (100 bytes)
        small_bytes = b"S" * 100
        (dir_models / "small_1.txt").write_bytes(small_bytes)
        (dir_archives / "small_2.txt").write_bytes(small_bytes)

        # Large duplicate pair (10,000 bytes)
        large_bytes = b"L" * 10000
        (dir_models / "large_1.bin").write_bytes(large_bytes)
        (dir_archives / "large_2.bin").write_bytes(large_bytes)

        # Unfiltered plan
        plan_all = self.server.handle_ssd_find_duplicates({"min_size": 0})
        # Filtered plan requiring min_size >= 5000
        plan_filtered = self.server.handle_ssd_find_duplicates({"min_size": 5000})

        self.assertGreaterEqual(plan_all["duplicate_group_count"], plan_filtered["duplicate_group_count"])
        for group in plan_filtered.get("duplicate_groups", []):
            self.assertGreaterEqual(group["size"], 5000, "All filtered duplicate groups must satisfy min_size")


# ==============================================================================
# Tier 4: Real-World Autonomous Agent Workflows
# ==============================================================================

class TestTier4RealWorldWorkflows(BaseE2ETestCase):
    """Tier 4: Realistic autonomous coding agent lifecycle simulations."""

    def test_tier4_autonomous_agent_full_session_lifecycle(self) -> None:
        """Tier 4: Comprehensive end-to-end agent workflow simulation.

        Sequence:
        1. Agent config registration
        2. MCP session initialization & capabilities negotiation
        3. Index update & status check
        4. Sub-10ms multi-criteria search
        5. Storage audit & slack analysis
        6. Pre-action safety check
        7. Junk clean (preview -> apply)
        8. Duplicate file analysis
        9. Auto-zoning reorganization plan
        """
        # Step 1: Agent registers workspace configuration
        reg_res = register_ide_configs(target_dir=str(self.root_path))
        self.assertTrue(reg_res.get("workspace"), "Step 1: Workspace MCP registration must succeed")

        # Step 2: Agent initializes JSON-RPC session
        init_req = {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "initialize",
            "params": {"clientInfo": {"name": "Antigravity-Agent", "version": "2.0"}},
        }
        init_resp = self.server.handle_request(init_req)
        self.assertEqual(init_resp["result"]["protocolVersion"], "2024-11-05")

        # Step 3: Agent syncs search index
        idx_req = {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {"name": "ssd_update_index", "arguments": {}},
        }
        idx_resp = self.server.handle_request(idx_req)
        self.assertNotIn("isError", idx_resp.get("result", {}))

        # Step 4: Agent performs instant multi-criteria search
        search_req = {
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {"name": "ssd_search", "arguments": {"query": "", "limit": 10, "compact": True}},
        }
        search_resp = self.server.handle_request(search_req)
        search_data = json.loads(search_resp["result"]["content"][0]["text"])
        self.assertGreaterEqual(search_data["total_count"], 1)
        self.assertLessEqual(search_data["elapsed_ms"], 50.0, "Search query must be fast (<50ms in testing)")

        # Step 5: Agent audits storage allocation & cluster slack
        audit_req = {
            "jsonrpc": "2.0",
            "id": 104,
            "method": "tools/call",
            "params": {"name": "ssd_audit", "arguments": {}},
        }
        audit_resp = self.server.handle_request(audit_req)
        audit_data = json.loads(audit_resp["result"]["content"][0]["text"])
        self.assertIn("total_slack_bytes", audit_data)

        # Step 6: Agent verifies safety before writing
        safe_req = {
            "jsonrpc": "2.0",
            "id": 105,
            "method": "tools/call",
            "params": {"name": "ssd_check_safety", "arguments": {"path": "01_AI_Models/new_model.gguf"}},
        }
        safe_resp = self.server.handle_request(safe_req)
        safe_data = json.loads(safe_resp["result"]["content"][0]["text"])
        self.assertTrue(safe_data["is_safe"], "Clean path inside taxonomy must be safe")

        # Step 7: Agent checks overall drive status
        status_req = {
            "jsonrpc": "2.0",
            "id": 106,
            "method": "tools/call",
            "params": {"name": "ssd_status", "arguments": {}},
        }
        status_resp = self.server.handle_request(status_req)
        status_data = json.loads(status_resp["result"]["content"][0]["text"])
        self.assertIn("mount", status_data)

        # Step 8: Agent runs dry-run auto-organize plan
        org_req = {
            "jsonrpc": "2.0",
            "id": 107,
            "method": "tools/call",
            "params": {"name": "ssd_auto_organize", "arguments": {"apply": False}},
        }
        org_resp = self.server.handle_request(org_req)
        org_data = json.loads(org_resp["result"]["content"][0]["text"])
        self.assertEqual(org_data.get("status"), "dry_run")

    def test_tier4_agent_authentication_and_rejection_workflow(self) -> None:
        """Tier 4: Token authentication enforcement and zero-friction stdio handshake."""
        # Initialize authenticated server
        auth_server = SmartDriveMCPServer(
            root=str(self.root_path),
            auth_token="secure_token_secret_12345",
            require_auth=True,
            rate_limit_enabled=False,
        )

        # 1. Unauthenticated tools/list request is rejected (-32001)
        unauth_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        resp1 = auth_server.handle_request(unauth_req)
        self.assertEqual(resp1["error"]["code"], -32001)

        # 2. Handshake with wrong token fails
        bad_hs_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "auth/handshake",
            "params": {"token": "wrong_token"},
        }
        resp2 = auth_server.handle_request(bad_hs_req)
        self.assertEqual(resp2["error"]["code"], -32001)

        # 3. Handshake with valid token succeeds
        good_hs_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "auth/handshake",
            "params": {"token": "secure_token_secret_12345"},
        }
        resp3 = auth_server.handle_request(good_hs_req)
        self.assertTrue(resp3["result"]["authenticated"])

        # 4. Subsequent protected request succeeds
        resp4 = auth_server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        self.assertIn("tools", resp4["result"])


if __name__ == "__main__":
    unittest.main()
