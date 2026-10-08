"""tests/test_isolation.py - Validates test suite sandboxing and config isolation.

Ensures no test execution writes to the user's real HOME, APPDATA, or global agent configs.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from smart_drive.mcp.registrar import get_agent_config_paths


def _inside_system_temp(path: Path) -> bool:
    """True when path is under the OS temp dir (/tmp, %TEMP%, /var/folders/.../T on macOS)."""
    try:
        path.relative_to(Path(tempfile.gettempdir()).resolve())
    except ValueError:
        return False
    return True


class TestSuiteIsolation(unittest.TestCase):
    """Verifies that the test environment is completely isolated from the host system."""

    def test_smart_drive_no_probe_is_active(self) -> None:
        """SMART_DRIVE_NO_PROBE must be enabled by default during test execution."""
        self.assertEqual(os.environ.get("SMART_DRIVE_NO_PROBE"), "1")

    def test_home_and_appdata_in_sandbox(self) -> None:
        """Path.home(), USERPROFILE, and APPDATA point inside temporary sandboxed paths."""
        home = Path.home().resolve()
        # Ensure home is in a temp directory structure
        self.assertTrue(
            _inside_system_temp(home),
            f"Path.home() '{home}' does not appear to be inside a sandboxed temp directory",
        )

        if "APPDATA" in os.environ:
            appdata = Path(os.environ["APPDATA"]).resolve()
            self.assertTrue(
                _inside_system_temp(appdata),
                f"APPDATA '{appdata}' does not appear to be inside a sandboxed temp directory",
            )

    def test_agent_config_paths_point_to_temp_dir(self) -> None:
        """All IDE agent config paths from get_agent_config_paths() resolve inside sandbox."""
        config_paths = get_agent_config_paths()
        self.assertIn("antigravity", config_paths)
        self.assertIn("claude", config_paths)
        self.assertIn("cursor", config_paths)
        self.assertIn("windsurf", config_paths)

        home = Path.home().resolve()
        for agent, path in config_paths.items():
            path_obj = Path(path).resolve()
            # Must be rooted inside the sandboxed home or temp dir
            try:
                path_obj.relative_to(home)
                is_subpath = True
            except ValueError:
                is_subpath = False

            path_str = str(path_obj).lower()
            is_in_temp = "temp" in path_str or "tmp" in path_str

            self.assertTrue(
                is_subpath or is_in_temp,
                f"Agent '{agent}' config path '{path}' is not within sandboxed HOME '{home}' or TEMP",
            )


if __name__ == "__main__":
    unittest.main()
