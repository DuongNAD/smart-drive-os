"""tests/test_mcp_adversarial_challenger1.py - Milestone 1 Adversarial Verification Suite.

EMPIRICAL CHALLENGER 1 TEST SUITE:
Adversarially challenges path traversal defenses and cross-platform security fixes in smart_drive/mcp/server.py:
1. Complex path traversals (mixed slashes, parent escapes, deep traversals).
2. Windows drive letters and cross-drive escapes (C:, Z:, drive-relative paths).
3. UNC network paths and Win32 device namespace paths (\\\\, //, \\\\?\\, \\\\.\\, \\??\\).
4. Null byte injections (\\x00 in path segments, prefixes, and extensions).
5. Windows reserved DOS device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9).
6. Multi-segment exFAT forbidden character audits.
7. Protected root files and taxonomies immutability.
8. JSON-RPC tools/call protocol boundary enforcement across all path-accepting tools.
9. Cross-platform core fixes regression verification.
"""

from __future__ import annotations

import io
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

from smart_drive.core.config import PROTECTED_ROOT_DIRS, PROTECTED_ROOT_FILES
from smart_drive.core.drive_detector import get_system_drive_letter, normalize_drive_letter
from smart_drive.core.exfat_compat import WINDOWS_RESERVED_NAMES, ExFatEngine
from smart_drive.core.junction import is_directory_junction
from smart_drive.core.offloader import validate_target_drive
from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.mcp.server import SmartDriveMCPServer
from smart_drive.ui.server import ThreadingHTTPServer
from tests.helpers import SmartDriveTestCase


class TestAdversarialPathTraversalResolution(SmartDriveTestCase):
    """Adversarially tests _resolve_safe_path against malicious directory escape payloads."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_complex_path_traversals_blocked(self) -> None:
        """Complex traversals with nested parent directory escapes must raise ValueError."""
        payloads = [
            "../",
            "../../",
            "../../../",
            "../../../../",
            "../../../../etc/passwd",
            "..\\..\\Windows\\System32",
            "..\\..\\..\\..\\Windows\\System32\\cmd.exe",
            "01_AI_Models/../../..",
            "01_AI_Models/../../outside.txt",
            "01_AI_Models/subdir/../../../../",
            "sub_dir/../../..\\..\\etc",
            "dir/..\\..\\",
            "dir\\..\\..\\",
            "./../../",
            ".\\..\\..",
            "/etc/passwd",
            "/var/log",
            "\\Windows\\System32",
            "\\etc\\passwd",
            "///etc/passwd",
            "\\\\\\Windows\\System32",
        ]
        for p in payloads:
            with self.assertRaises(ValueError, msg=f"Complex traversal payload {p!r} was not blocked!"):
                self.server._resolve_safe_path(p)

    def test_windows_drive_letter_escapes_blocked(self) -> None:
        """Windows drive letters pointing to different or root drives must raise ValueError."""
        payloads = [
            "C:/Windows/System32",
            "C:\\Windows\\System32",
            "C:",
            "C:/",
            "C:\\",
            "C:test.txt",
            "D:",
            "D:/",
            "D:\\",
            "Z:\\outside",
            "z:/escaped/file.txt",
            "X:\\Windows\\System32",
        ]
        for p in payloads:
            with self.assertRaises(ValueError, msg=f"Drive escape payload {p!r} was not blocked!"):
                self.server._resolve_safe_path(p)

    def test_unc_and_device_namespaces_blocked(self) -> None:
        """UNC paths and Win32 device namespace paths must be immediately rejected."""
        payloads = [
            "\\\\127.0.0.1\\c$\\exploit",
            "\\\\localhost\\share\\test",
            "//localhost/share/test",
            "//127.0.0.1/c$/exploit",
            "\\\\?\\C:\\Windows",
            "\\\\.\\C:\\Windows",
            "\\\\?\\UNC\\server\\share",
            "\\??\\C:\\Windows",
            "//?/C:/Windows",
            "//./COM1",
        ]
        for p in payloads:
            with self.assertRaises(ValueError, msg=f"UNC/device namespace payload {p!r} was not blocked!"):
                self.server._resolve_safe_path(p)

    def test_null_byte_injections_blocked(self) -> None:
        """Any path containing null bytes must be blocked with explicit null byte error."""
        payloads = [
            "valid/path\x00/../../etc/passwd",
            "sub\x00dir",
            "\x00",
            "01_AI_Models\x00/../../etc",
            "clean.txt\x00.exe",
            "dir/\x00/file.bin",
        ]
        for p in payloads:
            with self.assertRaises(ValueError, msg=f"Null byte payload {p!r} was not blocked!") as ctx:
                self.server._resolve_safe_path(p)
            self.assertIn("null byte", str(ctx.exception).lower())

    def test_symlink_directory_escape_blocked(self) -> None:
        """Symlinks pointing outside storage root must be blocked during path resolution."""
        outside_temp = tempfile.mkdtemp()
        try:
            outside_target = os.path.join(outside_temp, "secret.txt")
            with open(outside_target, "w") as f:
                f.write("SECRET OUTSIDE DATA")

            escape_link = os.path.join(str(self.mock_root), "escape_symlink")
            try:
                os.symlink(outside_target, escape_link)
                symlink_created = True
            except (OSError, NotImplementedError):
                symlink_created = False

            if symlink_created:
                with self.assertRaises(ValueError, msg="Symlink escape to outside directory was not blocked!"):
                    self.server._resolve_safe_path("escape_symlink")
        finally:
            shutil.rmtree(outside_temp)

    def test_valid_internal_paths_resolved_correctly(self) -> None:
        """Legitimate internal paths must resolve cleanly within canonical root."""
        canonical_root = os.path.realpath(str(self.mock_root))
        
        # Root defaults
        self.assertEqual(self.server._resolve_safe_path(None), canonical_root)
        self.assertEqual(self.server._resolve_safe_path(""), canonical_root)
        self.assertEqual(self.server._resolve_safe_path("   "), canonical_root)

        # Standard taxonomies
        resolved_ai = self.server._resolve_safe_path("01_AI_Models")
        self.assertEqual(resolved_ai, os.path.realpath(os.path.join(str(self.mock_root), "01_AI_Models")))

        # Nested relative paths
        resolved_nested = self.server._resolve_safe_path("01_AI_Models/subdir/../sub2")
        self.assertEqual(resolved_nested, os.path.realpath(os.path.join(str(self.mock_root), "01_AI_Models/sub2")))


class TestAdversarialSSDCheckSafety(SmartDriveTestCase):
    """Adversarially tests handle_ssd_check_safety against exFAT and boundary violations."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_complex_traversals_marked_unsafe(self) -> None:
        """All directory escape payloads must evaluate as is_safe=False with error message."""
        payloads = [
            "..\\..\\Windows\\System32",
            "....//....//etc/passwd",
            "C:/Windows/System32",
            "C:\\Windows\\System32",
            "sub_dir/../../..\\..\\etc",
            "01_AI_Models/../../outside.txt",
            "/etc/passwd",
            "\\Windows\\System32",
            "Z:/outside/dir",
        ]
        for p in payloads:
            res = self.server.handle_ssd_check_safety({"path": p})
            self.assertFalse(res.get("is_safe"), f"Payload {p!r} was erroneously marked as is_safe=True!")
            # Must either have an escape error or a forbidden violation (e.g. trailing dot for ....)
            has_error = bool(res.get("error"))
            has_viols = bool(res.get("forbidden_character_violations"))
            self.assertTrue(has_error or has_viols, f"Payload {p!r} lacked both error and violations in safety result!")

    def test_unc_paths_marked_unsafe_with_unc_error(self) -> None:
        """UNC paths must evaluate as is_safe=False with explicit UNC path error."""
        unc_payloads = [
            "\\\\127.0.0.1\\c$\\exploit",
            "//localhost/share/test",
            "\\\\remote-server\\share\\exploit.exe",
            "//192.168.1.1/backup",
        ]
        for p in unc_payloads:
            res = self.server.handle_ssd_check_safety({"path": p})
            self.assertFalse(res.get("is_safe"), f"UNC path {p!r} was marked safe!")
            self.assertIn("error", res)
            self.assertIn("unc path", res["error"].lower())

    def test_null_bytes_marked_unsafe_with_violation(self) -> None:
        """Paths containing null bytes must be marked unsafe with \\x00 violation."""
        null_payloads = [
            "valid/path\x00/../../etc/passwd",
            "normal_name.txt\x00.exe",
            "sub/\x00/file",
        ]
        for p in null_payloads:
            res = self.server.handle_ssd_check_safety({"path": p})
            self.assertFalse(res.get("is_safe"), f"Null byte path {p!r} was marked safe!")
            self.assertIn("\\x00", res.get("forbidden_character_violations", []))
            self.assertIn("null byte", res.get("error", "").lower())

    def test_all_22_windows_reserved_dos_devices(self) -> None:
        """All 22 Windows 16-bit DOS device names must evaluate as is_safe=False."""
        for stem in WINDOWS_RESERVED_NAMES:
            # Bare stem
            res_bare = self.server.handle_ssd_check_safety({"path": stem})
            self.assertFalse(res_bare.get("is_safe"), f"DOS device {stem} was marked safe!")
            self.assertTrue(any("RESERVED_NAME" in str(v) for v in res_bare.get("forbidden_character_violations", [])))

            # Lowercase stem with extension
            stem_lower = f"{stem.lower()}.txt"
            res_ext = self.server.handle_ssd_check_safety({"path": stem_lower})
            self.assertFalse(res_ext.get("is_safe"), f"DOS device {stem_lower} was marked safe!")
            self.assertTrue(any("RESERVED_NAME" in str(v) for v in res_ext.get("forbidden_character_violations", [])))

            # Nested in directory
            nested = f"01_AI_Models/{stem}/weights.bin"
            res_nested = self.server.handle_ssd_check_safety({"path": nested})
            self.assertFalse(res_nested.get("is_safe"), f"Nested DOS device {nested} was marked safe!")
            self.assertTrue(any("RESERVED_NAME" in str(v) for v in res_nested.get("forbidden_character_violations", [])))

    def test_windows_forbidden_characters_in_all_segments(self) -> None:
        """Windows forbidden characters in any intermediate segment must be detected."""
        forbidden_cases = [
            "folder_normal/sub:dir/file.txt",
            "models/checkpoint*/model.bin",
            "data/query?results/output.csv",
            "docs/my\"report/final.pdf",
            "src/<template>/index.html",
            "src/>output>/log.txt",
            "logs/stream|pipe/events.log",
        ]
        for p in forbidden_cases:
            res = self.server.handle_ssd_check_safety({"path": p})
            self.assertFalse(res.get("is_safe"), f"Path with forbidden characters {p!r} was marked safe!")
            self.assertGreater(len(res.get("forbidden_character_violations", [])), 0)

    def test_protected_root_files_and_directories_marked_unsafe(self) -> None:
        """Protected root files and taxonomies must be flagged as protected and unsafe to modify."""
        protected_files = ["AGENTS.md", "GEMINI.md", "README.md", "PRIVACY.md", ".mcp.json", "agents.md", "gemini.md"]
        for pf in protected_files:
            res = self.server.handle_ssd_check_safety({"path": pf})
            self.assertFalse(res.get("is_safe"), f"Protected root file {pf!r} was marked safe!")
            self.assertTrue(res.get("is_protected_root_file"), f"Protected root file flag not set for {pf!r}")

        protected_dirs = ["01_AI_Models", "02_Learning_Knowledge", "03_Personal_Documents", ".agents"]
        for pd in protected_dirs:
            res = self.server.handle_ssd_check_safety({"path": pd})
            self.assertFalse(res.get("is_safe"), f"Protected root dir {pd!r} was marked safe!")
            self.assertTrue(res.get("is_protected_root_dir"), f"Protected root dir flag not set for {pd!r}")

    def test_clean_valid_files_marked_safe(self) -> None:
        """Valid exFAT-compliant filenames inside allowed folders must evaluate as is_safe=True."""
        clean_paths = [
            "01_AI_Models/model.bin",
            "01_AI_Models/sub/clean_file.txt",
            "clean_file.txt",
            "02_Learning_Knowledge/notes.pdf",
            "03_Personal_Documents/tax/2026_return.pdf",
        ]
        for p in clean_paths:
            res = self.server.handle_ssd_check_safety({"path": p})
            self.assertTrue(res.get("is_safe"), f"Clean path {p!r} was unexpectedly marked unsafe: {res}")
            self.assertFalse(res.get("is_symlink"))
            self.assertEqual(res.get("forbidden_character_violations"), [])


class TestAdversarialJSONRPCToolEscapes(SmartDriveTestCase):
    """Adversarially tests all path-accepting MCP tools invoked via JSON-RPC stdio handle_request."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.server = SmartDriveMCPServer(root=str(self.mock_root))

    def test_jsonrpc_tools_call_blocks_traversal_payloads(self) -> None:
        """All 5 path-accepting tools must return isError=True and Access denied message."""
        capture = io.StringIO()
        orig_stdout = sys.stdout
        sys.stdout = capture

        mcp_logger = logging.getLogger("smart_drive.mcp.server")
        prev_level = mcp_logger.level
        mcp_logger.setLevel(logging.CRITICAL)

        try:
            tools = [
                ("ssd_search", "directory"),
                ("ssd_audit", "sub_dir"),
                ("ssd_clean", "sub_dir"),
                ("ssd_find_duplicates", "sub_dir"),
                ("ssd_update_index", "directory"),
            ]
            adversarial_payloads = [
                "..\\..\\Windows\\System32",
                "C:/Windows/System32",
                "\\\\127.0.0.1\\c$\\exploit",
                "//localhost/share/test",
                "valid/path\x00/../../etc/passwd",
                "sub_dir/../../..\\..\\etc",
            ]
            for tool_name, param_key in tools:
                for payload in adversarial_payloads:
                    req = {
                        "jsonrpc": "2.0",
                        "id": 100,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {param_key: payload}},
                    }
                    resp = self.server.handle_request(req)
                    self.assertIsNotNone(resp)
                    result = resp.get("result", {})
                    self.assertTrue(
                        result.get("isError", False),
                        f"Tool {tool_name} did not set isError=True for payload {payload!r}",
                    )
                    content_text = result.get("content", [{}])[0].get("text", "")
                    self.assertIn(
                        "Access denied",
                        content_text,
                        f"Expected 'Access denied' error in response for {tool_name} with {payload!r}, got: {content_text}",
                    )
        finally:
            sys.stdout = orig_stdout
            mcp_logger.setLevel(prev_level)


class TestAdversarialWorkerRemediationsIntegrity(SmartDriveTestCase):
    """Verifies that Worker M1's cross-platform fixes remain intact and robust."""

    def test_system_drive_letter_cross_platform_parsing(self) -> None:
        """Drive letter normalization correctly parses Windows paths regardless of host OS."""
        self.assertEqual(normalize_drive_letter("E:\\Windows\\System32"), "E:")
        self.assertEqual(normalize_drive_letter("c:/windows"), "C:")
        self.assertEqual(normalize_drive_letter("D:\\"), "D:")

    @unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
    def test_broken_junction_detection_on_posix(self) -> None:
        """Broken directory symlinks on POSIX are correctly identified as junctions."""
        temp_dir = tempfile.mkdtemp()
        try:
            target_dir = os.path.join(temp_dir, "real_dir")
            os.makedirs(target_dir, exist_ok=True)
            link_dir = os.path.join(temp_dir, "junction_link")
            os.symlink(target_dir, link_dir)

            # Valid junction
            self.assertTrue(is_directory_junction(link_dir))

            # Delete target to make it a broken junction
            os.rmdir(target_dir)
            self.assertTrue(is_directory_junction(link_dir))
        finally:
            shutil.rmtree(temp_dir)

    def test_offloader_target_drive_formatting(self) -> None:
        """Offloader validate_target_drive does not insert dual separators on POSIX."""
        letter, path = validate_target_drive("D:")
        self.assertEqual(letter, "D:")
        # Path string must not contain double slashes like D:\/
        path_str = str(path)
        self.assertNotIn("D:\\/", path_str)
        self.assertNotIn("D://", path_str)

    def test_threading_http_server_queue_size_backlog(self) -> None:
        """ThreadingHTTPServer must configure request_queue_size to 128 to buffer concurrency bursts."""
        self.assertEqual(ThreadingHTTPServer.request_queue_size, 128)


if __name__ == "__main__":
    unittest.main()
