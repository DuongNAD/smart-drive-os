"""tests.test_mcp_proxy - Dynamic Mount Discovery Proxy & MCP Registrar Tests.

Validates:
1. Dynamic SSD mount point discovery (<100ms across macOS, Windows, Linux).
2. Environment variable overrides (SMART_DRIVE_ROOT, KINGSTON_SSD_ROOT).
3. Manifest anchor detection (GEMINI.md, AGENTS.md) in cwd ancestors.
4. Fallback resolution when unmounted or non-standard.
5. Multi-IDE MCP configuration registrar (Antigravity, Claude, Cursor, Windsurf, workspace).
"""

from __future__ import annotations

import json
import os
import platform
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.mcp.registrar import (
    get_agent_config_paths,
    load_json_config,
    register_ide_configs,
    save_json_config,
)
from tests.helpers import SmartDriveTestCase, TempWorkspace


class TestSmartDriveProxy(SmartDriveTestCase):
    """Test suite for SmartDriveProxy dynamic mount detection."""

    def test_detect_mount_point_env_smart_drive_root(self) -> None:
        """SMART_DRIVE_ROOT environment variable takes highest priority."""
        with TempWorkspace() as ws:
            with patch.dict(os.environ, {"SMART_DRIVE_ROOT": str(ws)}):
                detected = SmartDriveProxy.detect_mount_point()
                self.assertIsNotNone(detected)
                self.assertEqual(detected.resolve(), ws.resolve())

    def test_detect_mount_point_env_kingston_ssd_root(self) -> None:
        """KINGSTON_SSD_ROOT environment variable is respected when SMART_DRIVE_ROOT is absent."""
        with TempWorkspace() as ws:
            with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": str(ws)}):
                detected = SmartDriveProxy.detect_mount_point()
                self.assertIsNotNone(detected)
                self.assertEqual(detected.resolve(), ws.resolve())

    def test_detect_mount_point_invalid_env_returns_none(self) -> None:
        """Non-existent path in environment variable does not fall through; returns None."""
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "/non/existent/path/987654321", "KINGSTON_SSD_ROOT": ""}):
            detected = SmartDriveProxy.detect_mount_point()
            self.assertIsNone(detected)

    def test_detect_mount_point_cwd_ancestor_gemini_md_with_marker(self) -> None:
        """Detects root directory by walking up from cwd when directory has marker and GEMINI.md anchor."""
        with TempWorkspace() as ws:
            (ws / ".metadata_never_index").write_text("", encoding="utf-8")
            manifest_file = ws / "GEMINI.md"
            manifest_file.write_text("# GEMINI.md\n", encoding="utf-8")

            nested_dir = ws / "03_Development_Projects" / "sub" / "project"
            nested_dir.mkdir(parents=True, exist_ok=True)

            with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": ""}):
                with patch("pathlib.Path.cwd", return_value=nested_dir):
                    detected = SmartDriveProxy.detect_mount_point()
                    self.assertIsNotNone(detected)
                    self.assertEqual(detected.resolve(), ws.resolve())

    def test_detect_mount_point_cwd_ancestor_agents_md_with_marker(self) -> None:
        """Detects root directory by walking up from cwd when directory has marker and AGENTS.md anchor."""
        with TempWorkspace() as ws:
            (ws / ".smart_drive").mkdir(parents=True, exist_ok=True)
            manifest_file = ws / "AGENTS.md"
            manifest_file.write_text("# AGENTS.md\n", encoding="utf-8")

            sub_dir = ws / "01_AI_Models" / "weights"
            sub_dir.mkdir(parents=True, exist_ok=True)

            with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": ""}):
                with patch("pathlib.Path.cwd", return_value=sub_dir):
                    detected = SmartDriveProxy.detect_mount_point()
                    self.assertIsNotNone(detected)
                    self.assertEqual(detected.resolve(), ws.resolve())

    def test_detect_mount_point_cwd_ancestor_named_kingston_without_marker_returns_none(self) -> None:
        """Ancestor named kingston without marker/anchor is NOT detected as root."""
        with TempWorkspace() as ws:
            kingston_dir = ws / "KINGSTON"
            nested_sub = kingston_dir / "projects" / "app"
            nested_sub.mkdir(parents=True, exist_ok=True)

            with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
                with patch("pathlib.Path.cwd", return_value=nested_sub):
                    detected = SmartDriveProxy.detect_mount_point()
                    self.assertIsNone(detected)

    def test_detect_mount_point_macos_mock(self) -> None:
        """Simulates macOS /Volumes/KINGSTON mount detection."""
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": ""}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                with patch("platform.system", return_value="Darwin"):
                    with patch("smart_drive.core.root.sys.platform", "darwin"):
                        with patch("os.path.isdir", side_effect=lambda p: str(p).replace("\\", "/") in ("/Volumes", "/Volumes/KINGSTON")):
                            with patch("os.listdir", side_effect=lambda p: ["KINGSTON"] if str(p).replace("\\", "/") == "/Volumes" else []):
                                with patch("smart_drive.core.root._is_candidate", side_effect=lambda p: str(p).replace("\\", "/") == "/Volumes/KINGSTON"):
                                    detected = SmartDriveProxy.detect_mount_point()
                                    self.assertIsNotNone(detected)
                                    self.assertEqual(detected.as_posix(), "/Volumes/KINGSTON")

    def test_detect_mount_point_windows_letters_mock(self) -> None:
        """Simulates Windows drive letter probe with marker."""
        def mock_isdir(p):
            p_str = str(p).replace("/", "\\")
            return p_str == "E:\\"

        def mock_has_marker(p):
            p_str = str(p).replace("/", "\\")
            return p_str == "E:\\"

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": ""}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                with patch("platform.system", return_value="Windows"):
                    with patch("smart_drive.core.root.sys.platform", "win32"):
                        with patch("os.path.isdir", side_effect=mock_isdir):
                            with patch("smart_drive.core.root._has_marker", side_effect=mock_has_marker):
                                detected = SmartDriveProxy.detect_mount_point()
                                self.assertIsNotNone(detected)
                                self.assertEqual(str(detected).upper().rstrip("\\"), "E:")

    def test_discover_with_latency_benchmark(self) -> None:
        """Benchmark: discovery execution must return latency and finish well under 100ms."""
        with TempWorkspace() as ws:
            with patch.dict(os.environ, {"SMART_DRIVE_ROOT": str(ws)}):
                mount, latency_ms = SmartDriveProxy.discover_with_latency()
                self.assertIsNotNone(mount)
                self.assertIsInstance(latency_ms, float)
                self.assertGreaterEqual(latency_ms, 0.0)
                # Performance requirement: dynamic discovery in <100ms
                self.assertLess(latency_ms, 100.0, f"Discovery latency {latency_ms:.2f}ms exceeded 100ms target")


class TestMultiIdeMcpRegistrar(SmartDriveTestCase):
    """Test suite for Multi-IDE MCP configuration registration."""

    def test_get_agent_config_paths(self) -> None:
        """Returns standard configuration paths for Antigravity, Claude, Cursor, and Windsurf."""
        paths = get_agent_config_paths()
        self.assertIsInstance(paths, dict)
        for expected_key in ("antigravity", "claude", "cursor", "windsurf"):
            self.assertIn(expected_key, paths)
            self.assertIsInstance(paths[expected_key], Path)

    def test_save_and_load_json_config(self) -> None:
        """Validates safe round-trip JSON serialization and error resilience."""
        with TempWorkspace() as ws:
            cfg_file = ws / "nested" / "dir" / "test_config.json"

            # Loading non-existent file returns empty dict
            self.assertEqual(load_json_config(cfg_file), {})

            # Saving creates parents and writes formatted JSON
            payload = {"mcpServers": {"test": {"command": "python"}}}
            save_json_config(cfg_file, payload)
            self.assertTrue(cfg_file.is_file())

            # Loading returns exact payload
            loaded = load_json_config(cfg_file)
            self.assertEqual(loaded, payload)

            # Corrupted JSON returns empty dict
            with open(cfg_file, "w", encoding="utf-8") as f:
                f.write("{ INVALID JSON DATA")
            self.assertEqual(load_json_config(cfg_file), {})

    def test_register_ide_configs_workspace_target(self) -> None:
        """Registers smart-drive MCP server in local workspace .mcp.json."""
        with TempWorkspace() as ws:
            results = register_ide_configs(
                target_dir=str(ws),
                flags={"antigravity": False, "claude": False, "cursor": False, "windsurf": False},
            )
            self.assertIn("workspace", results)
            self.assertTrue(results["workspace"])

            local_mcp = ws / ".mcp.json"
            self.assertTrue(local_mcp.is_file())

            cfg = load_json_config(local_mcp)
            self.assertIn("mcpServers", cfg)
            self.assertIn("smart-drive", cfg["mcpServers"])
            entry = cfg["mcpServers"]["smart-drive"]
            self.assertIn("-m", entry["args"])
            self.assertIn("smart_drive", entry["args"])
            self.assertIn("mcp", entry["args"])

    def test_register_ide_configs_selective_flags(self) -> None:
        """Flags dictionary selectively toggles IDE registration targets."""
        with TempWorkspace() as ws:
            mock_paths = {
                "antigravity": ws / "gemini_mcp.json",
                "claude": ws / "claude_mcp.json",
                "cursor": ws / "cursor_mcp.json",
                "windsurf": ws / "windsurf_mcp.json",
            }
            with patch("smart_drive.mcp.registrar.get_agent_config_paths", return_value=mock_paths):
                # Only register for antigravity and cursor
                flags = {"antigravity": True, "claude": False, "cursor": True, "windsurf": False}
                res = register_ide_configs(flags=flags)
                self.assertIn("antigravity", res)
                self.assertTrue(res["antigravity"])
                self.assertIn("cursor", res)
                self.assertTrue(res["cursor"])
                self.assertNotIn("claude", res)
                self.assertNotIn("windsurf", res)

                self.assertTrue(mock_paths["antigravity"].is_file())
                self.assertTrue(mock_paths["cursor"].is_file())
                self.assertFalse(mock_paths["claude"].is_file())
                self.assertFalse(mock_paths["windsurf"].is_file())


if __name__ == "__main__":
    unittest.main()
