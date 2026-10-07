"""tests/test_root_resolution.py - Comprehensive Unit & Integration Tests for Safe Drive Root Resolution.

Tests the authoritative, zero-guess root resolver and safe registrar behavior:
- Anchor-only folder (AGENTS.md, no marker) is NOT a root.
- Marker + anchor IS a root.
- Marker-only non-mount folder is NOT a root.
- Invalid env var -> not found without probing (and raises DriveRootNotFound).
- Windows probe with D: existing but no marker -> returns None.
- Multiple candidate marked drives -> returns None / ambiguous error.
- Explicit root wins over env, anchor, and probe.
- Drive C: is never probed under any circumstances.
- CLI: `organize --apply` and `clean --apply` with no resolvable root exit code 2 and leave filesystem untouched.
- MCP server with no resolvable root returns a tool error from ssd_clean / ssd_auto_organize and never uses cwd.
- Registrar: default call writes no global configs (only workspace when requested).
- Corrupt global config stays byte-for-byte unchanged and returns False.
- BOM-prefixed (utf-8-sig) config is parsed and preserved.
- Corrupt workspace .mcp.json creates a .bak-YYYYmmdd-HHMMSS copy and writes fresh config.
- CLI mcp config --all registers all four agent targets.
- `init --force` creates .bak-YYYYmmdd-HHMMSS copies of existing manifests before overwriting.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.cli.cmd_clean import cmd_clean
from smart_drive.cli.cmd_init import cmd_init
from smart_drive.cli.cmd_mcp_config import cmd_mcp_config
from smart_drive.cli.cmd_organize import cmd_organize
from smart_drive.core.initializer import DriveInitializer
from smart_drive.core.root import (
    DriveRootNotFound,
    RootResolution,
    _has_anchor,
    _has_marker,
    _is_candidate,
    find_drive_root,
    resolve_drive_root,
)
from smart_drive.mcp.registrar import (
    ConfigParseError,
    get_agent_config_paths,
    load_json_config,
    register_ide_configs,
    save_json_config,
)
from smart_drive.mcp.server import SmartDriveMCPServer


class TestRootCandidateRules(unittest.TestCase):
    """Unit tests for root candidate marker and anchor definitions."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_anchor_only_is_not_candidate(self) -> None:
        """Anchor file alone (AGENTS.md or GEMINI.md) without marker is NOT a candidate."""
        agents_file = os.path.join(self.temp_dir, "AGENTS.md")
        with open(agents_file, "w", encoding="utf-8") as f:
            f.write("# AGENTS\n")

        self.assertTrue(_has_anchor(self.temp_dir))
        self.assertFalse(_has_marker(self.temp_dir))
        with patch("os.path.ismount", return_value=False):
            self.assertFalse(_is_candidate(self.temp_dir))

    def test_marker_and_anchor_is_candidate(self) -> None:
        """Marker directory (.smart_drive) + anchor file (GEMINI.md) IS a candidate."""
        os.makedirs(os.path.join(self.temp_dir, ".smart_drive"), exist_ok=True)
        gemini_file = os.path.join(self.temp_dir, "GEMINI.md")
        with open(gemini_file, "w", encoding="utf-8") as f:
            f.write("# GEMINI\n")

        self.assertTrue(_has_marker(self.temp_dir))
        self.assertTrue(_has_anchor(self.temp_dir))
        self.assertTrue(_is_candidate(self.temp_dir))

    def test_marker_only_non_mount_is_not_candidate(self) -> None:
        """Marker directory (.smart_drive) without anchor on non-mount is NOT a candidate."""
        os.makedirs(os.path.join(self.temp_dir, ".smart_drive"), exist_ok=True)

        self.assertTrue(_has_marker(self.temp_dir))
        self.assertFalse(_has_anchor(self.temp_dir))
        with patch("os.path.ismount", return_value=False):
            self.assertFalse(_is_candidate(self.temp_dir))

    def test_marker_only_on_mount_is_candidate(self) -> None:
        """Marker on a real OS mount point IS a candidate even without anchor file."""
        os.makedirs(os.path.join(self.temp_dir, ".smart_drive_manager"), exist_ok=True)
        with patch("os.path.ismount", return_value=True):
            self.assertTrue(_is_candidate(self.temp_dir))


class TestRootResolutionHierarchy(unittest.TestCase):
    """Tests priority order: explicit > env > candidate anchor > probe."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_explicit_root_wins(self) -> None:
        """Explicit path overrides environment variables, anchors, and probes."""
        explicit_dir = os.path.join(self.temp_dir, "explicit_dir")
        env_dir = os.path.join(self.temp_dir, "env_dir")
        os.makedirs(explicit_dir, exist_ok=True)
        os.makedirs(env_dir, exist_ok=True)

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": env_dir}):
            res = resolve_drive_root(explicit=explicit_dir)
            self.assertEqual(res.path, os.path.abspath(explicit_dir))
            self.assertEqual(res.source, "explicit")

    def test_invalid_explicit_fails_without_fallback(self) -> None:
        """Invalid explicit path fails immediately without fallback."""
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": self.temp_dir}):
            non_existent = os.path.join(self.temp_dir, "does_not_exist")
            with self.assertRaises(DriveRootNotFound) as cm:
                resolve_drive_root(explicit=non_existent)
            self.assertIn("Explicit root path", str(cm.exception))

    def test_invalid_env_smart_drive_root_fails_without_probing(self) -> None:
        """Invalid SMART_DRIVE_ROOT raises DriveRootNotFound without falling through to probe."""
        bad_path = os.path.join(self.temp_dir, "non_existent_ssd")
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": bad_path, "KINGSTON_SSD_ROOT": ""}):
            with self.assertRaises(DriveRootNotFound) as cm:
                resolve_drive_root()
            self.assertIn("SMART_DRIVE_ROOT", str(cm.exception))
            self.assertIn(bad_path, str(cm.exception))

    def test_invalid_env_kingston_ssd_root_fails_without_probing(self) -> None:
        """Invalid KINGSTON_SSD_ROOT raises DriveRootNotFound without falling through to probe."""
        bad_path = os.path.join(self.temp_dir, "bad_kingston")
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": bad_path}):
            with self.assertRaises(DriveRootNotFound) as cm:
                resolve_drive_root()
            self.assertIn("KINGSTON_SSD_ROOT", str(cm.exception))

    def test_anchor_resolution_walking_up(self) -> None:
        """Climbs directory tree to find first candidate directory with source 'anchor'."""
        root_cand = os.path.join(self.temp_dir, "drive_cand")
        nested_sub = os.path.join(root_cand, "sub1", "sub2", "sub3")
        os.makedirs(nested_sub, exist_ok=True)
        os.makedirs(os.path.join(root_cand, ".smart_drive"), exist_ok=True)
        with open(os.path.join(root_cand, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("# AGENTS\n")

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
            res = resolve_drive_root(start_path=nested_sub)
            self.assertEqual(res.path, os.path.abspath(root_cand))
            self.assertEqual(res.source, "anchor")


class TestOSProbingSafety(unittest.TestCase):
    """Tests probing boundaries (Windows D:-Z: with markers, never C:)."""

    def test_c_drive_is_never_probed_on_windows(self) -> None:
        """Prober never tests drive C: (or A: or B:) on Windows."""
        probed_drives = []

        def mock_has_marker(path: str) -> bool:
            probed_drives.append(str(path).upper())
            return False

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": ""}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                with patch("platform.system", return_value="Windows"):
                    with patch("smart_drive.core.root.sys.platform", "win32"):
                        with patch("os.path.isdir", return_value=True):
                            with patch("smart_drive.core.root._is_candidate", return_value=False):
                                with patch("smart_drive.core.root._has_marker", side_effect=mock_has_marker):
                                    res = find_drive_root()
                                    self.assertIsNone(res)

        self.assertNotIn("A:\\", probed_drives)
        self.assertNotIn("B:\\", probed_drives)
        self.assertNotIn("C:\\", probed_drives)
        self.assertIn("D:\\", probed_drives)
        self.assertIn("Z:\\", probed_drives)

    def test_windows_probe_existing_d_without_marker_returns_none(self) -> None:
        """If D:\\ exists but has no SmartDrive marker, Windows probe returns None."""
        def mock_isdir(path: str) -> bool:
            return str(path).startswith("D:")

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": ""}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                with patch("platform.system", return_value="Windows"):
                    with patch("smart_drive.core.root.sys.platform", "win32"):
                        with patch("os.path.isdir", side_effect=mock_isdir):
                            with patch("smart_drive.core.root._has_marker", return_value=False):
                                res = find_drive_root()
                                self.assertIsNone(res)

    def test_multiple_marked_drives_returns_none_and_ambiguous_error(self) -> None:
        """When multiple probed drives have markers, resolution fails as ambiguous."""
        def mock_isdir(path: str) -> bool:
            return str(path).startswith("D:") or str(path).startswith("E:")

        def mock_has_marker(path: str) -> bool:
            return str(path).startswith("D:") or str(path).startswith("E:")

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": ""}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                with patch("platform.system", return_value="Windows"):
                    with patch("smart_drive.core.root.sys.platform", "win32"):
                        with patch("os.path.isdir", side_effect=mock_isdir):
                            with patch("smart_drive.core.root._has_marker", side_effect=mock_has_marker):
                                self.assertIsNone(find_drive_root())
                                with self.assertRaises(DriveRootNotFound) as cm:
                                    resolve_drive_root()
                                self.assertIn("Multiple candidate SmartDrive roots found", str(cm.exception))


class TestCLIDriveResolutionSafety(unittest.TestCase):
    """Verifies that CLI commands exit 2 without filesystem modifications on unresolvable root."""

    def test_clean_apply_unresolvable_root_exits_code_2(self) -> None:
        """`clean --apply` exits 2 and prints error to stderr when root cannot be determined."""
        args = argparse.Namespace(
            root=None,
            apply=True,
            profile="general-workspace",
            dry_run=False,
            json=False,
            force=False,
        )
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                err_io = io.StringIO()
                with patch("sys.stderr", err_io):
                    rc = cmd_clean(args)
                self.assertEqual(rc, 2)
                self.assertIn("Could not determine the SmartDrive root", err_io.getvalue())

    def test_organize_apply_unresolvable_root_exits_code_2(self) -> None:
        """`organize --apply` exits 2 and prints error to stderr when root cannot be determined."""
        args = argparse.Namespace(
            root=None,
            apply=True,
            profile="general-workspace",
            dry_run=False,
            json=False,
        )
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                err_io = io.StringIO()
                with patch("sys.stderr", err_io):
                    rc = cmd_organize(args)
                self.assertEqual(rc, 2)
                self.assertIn("Could not determine the SmartDrive root", err_io.getvalue())


class TestMCPServerRootSafety(unittest.TestCase):
    """Tests MCP server handling of unresolvable root."""

    def test_mcp_server_with_unresolvable_root_returns_tool_error(self) -> None:
        """MCP server initializes with root=None and returns tool error for operations requiring root."""
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
            with patch("pathlib.Path.cwd", return_value=Path("C:/MockNonDrive")):
                server = SmartDriveMCPServer(root=None)
                self.assertIsNone(server.root)

                # Initialize succeeds
                init_res = server.handle_request({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {},
                })
                self.assertIn("result", init_res)

                # ssd_clean returns a tool error
                clean_res = server.handle_request({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ssd_clean", "arguments": {}},
                })
                self.assertIn("result", clean_res)
                self.assertTrue(clean_res["result"].get("isError"))
                self.assertIn("Could not determine the SmartDrive root", clean_res["result"]["content"][0]["text"])

                # ssd_auto_organize returns a tool error
                org_res = server.handle_request({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "ssd_auto_organize", "arguments": {}},
                })
                self.assertTrue(org_res["result"].get("isError"))


class TestRegistrarSafetyAndHardening(unittest.TestCase):
    """Tests MCP registrar atomic writes, UTF-8-BOM parsing, and safe defaults."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_register_writes_no_global_configs(self) -> None:
        """Calling register_ide_configs() without flags and without auto_detect writes NO global files."""
        mock_paths = {
            "antigravity": Path(self.temp_dir) / "antigravity.json",
            "claude": Path(self.temp_dir) / "claude.json",
            "cursor": Path(self.temp_dir) / "cursor.json",
            "windsurf": Path(self.temp_dir) / "windsurf.json",
        }
        with patch("smart_drive.mcp.registrar.get_agent_config_paths", return_value=mock_paths):
            res = register_ide_configs(flags=None, auto_detect=False)
            self.assertEqual(res, {})
            for path in mock_paths.values():
                self.assertFalse(path.exists(), f"Global config {path} should not have been created")

    def test_corrupt_global_config_stays_unchanged_and_fails_gracefully(self) -> None:
        """Corrupt global agent configuration is left untouched (strict mode)."""
        corrupt_file = Path(self.temp_dir) / "corrupt_global.json"
        corrupt_content = "{corrupted json syntax: 'missing brace'"
        corrupt_file.write_text(corrupt_content, encoding="utf-8")

        mock_paths = {
            "claude": corrupt_file,
        }
        with patch("smart_drive.mcp.registrar.get_agent_config_paths", return_value=mock_paths):
            res = register_ide_configs(flags={"claude": True}, auto_detect=False)
            self.assertFalse(res.get("claude"))
            self.assertEqual(corrupt_file.read_text(encoding="utf-8"), corrupt_content)

    def test_utf8_bom_prefixed_config_preserved(self) -> None:
        """UTF-8 BOM prefixed config is parsed cleanly and preserved."""
        bom_file = Path(self.temp_dir) / "bom_config.json"
        initial_data = {"mcpServers": {"existing": {"command": "echo"}}}
        # Write with UTF-8 BOM
        bom_file.write_bytes(b"\xef\xbb\xbf" + json.dumps(initial_data).encode("utf-8"))

        loaded = load_json_config(bom_file, strict=True)
        self.assertIn("mcpServers", loaded)
        self.assertIn("existing", loaded["mcpServers"])

    def test_corrupt_workspace_config_backed_up_with_timestamp(self) -> None:
        """Corrupt workspace .mcp.json is copied to .mcp.json.bak-YYYYmmdd-HHMMSS."""
        ws_file = Path(self.temp_dir) / ".mcp.json"
        ws_corrupt = "{broken: true"
        ws_file.write_text(ws_corrupt, encoding="utf-8")

        res = register_ide_configs(target_dir=self.temp_dir)
        self.assertTrue(res.get("workspace"))

        # Check backup file exists
        bak_files = list(Path(self.temp_dir).glob(".mcp.json.bak-*"))
        self.assertEqual(len(bak_files), 1)
        self.assertEqual(bak_files[0].read_text(encoding="utf-8"), ws_corrupt)
        self.assertNotIn(":", bak_files[0].name)  # No colons in filename for exFAT

        # New .mcp.json is valid JSON
        new_cfg = json.loads(ws_file.read_text(encoding="utf-8"))
        self.assertIn("smart-drive", new_cfg["mcpServers"])

    def test_cli_cmd_mcp_config_all_registers_all_four(self) -> None:
        """CLI `mcp config --all` explicitly registers all four agents."""
        mock_paths = {
            "antigravity": Path(self.temp_dir) / "antigravity.json",
            "claude": Path(self.temp_dir) / "claude.json",
            "cursor": Path(self.temp_dir) / "cursor.json",
            "windsurf": Path(self.temp_dir) / "windsurf.json",
        }
        args = argparse.Namespace(
            all=True,
            antigravity=False,
            claude=False,
            cursor=False,
            codex=False,
            windsurf=False,
            workspace=False,
            target_dir=None,
            json=True,
        )
        with patch("smart_drive.mcp.registrar.get_agent_config_paths", return_value=mock_paths):
            out_io = io.StringIO()
            with patch("sys.stdout", out_io):
                rc = cmd_mcp_config(args)
            self.assertEqual(rc, 0)
            data = json.loads(out_io.getvalue())
            self.assertTrue(data.get("antigravity"))
            self.assertTrue(data.get("claude"))
            self.assertTrue(data.get("cursor"))
            self.assertTrue(data.get("windsurf"))


class TestManifestBackupOnForceInit(unittest.TestCase):
    """Tests manifest backup behavior on `init --force`."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_force_creates_bak_copies_without_colons(self) -> None:
        """`init --force` makes .bak-YYYYmmdd-HHMMSS backups of existing manifests."""
        agents_path = Path(self.temp_dir) / "AGENTS.md"
        gemini_path = Path(self.temp_dir) / "GEMINI.md"
        agents_path.write_text("# Old Agents Content", encoding="utf-8")
        gemini_path.write_text("# Old Gemini Content", encoding="utf-8")

        initializer = DriveInitializer(self.temp_dir)
        res = initializer.initialize(target_path=self.temp_dir, profile="general-workspace", force=True)
        self.assertTrue(res["shields"]["all_healthy"])

        # Check backup files exist
        agents_bak = list(Path(self.temp_dir).glob("AGENTS.md.bak-*"))
        gemini_bak = list(Path(self.temp_dir).glob("GEMINI.md.bak-*"))

        self.assertEqual(len(agents_bak), 1)
        self.assertEqual(len(gemini_bak), 1)
        self.assertEqual(agents_bak[0].read_text(encoding="utf-8"), "# Old Agents Content")
        self.assertEqual(gemini_bak[0].read_text(encoding="utf-8"), "# Old Gemini Content")
        self.assertNotIn(":", agents_bak[0].name)
        self.assertNotIn(":", gemini_bak[0].name)


if __name__ == "__main__":
    unittest.main()
