"""tests/test_registrar_launch.py - The registered MCP entry must start from any working directory.

MCP clients start their servers from an arbitrary directory. `python -m smart_drive mcp` only works
there when the package is importable, so an entry written from a source checkout has to carry
PYTHONPATH (the launchers do the same). A pip-installed copy needs nothing extra.

The launch tests run with `-S` (no site-packages) so that an editable install on the developer's
machine cannot hide a missing PYTHONPATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.mcp import registrar

INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    }
) + "\n"


def _launch(entry: dict, env_overrides: dict, cwd: str) -> "subprocess.CompletedProcess[str]":
    """Runs the registered command the way an MCP client would, without site-packages."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(env_overrides)
    return subprocess.run(
        [entry["command"], "-S", *entry["args"]],
        input=INITIALIZE,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=60,
    )


class TestRegisteredEntryFromSourceCheckout(unittest.TestCase):
    """Entries written from a source checkout must be launchable from a foreign directory."""

    def setUp(self) -> None:
        if registrar._source_checkout_root() is None:
            self.skipTest("running from an installed copy: nothing to carry on PYTHONPATH")

    def test_entry_carries_the_checkout_root_on_pythonpath(self) -> None:
        entry = registrar.build_mcp_entry()
        self.assertEqual(Path(entry["env"]["PYTHONPATH"]), registrar._source_checkout_root())
        self.assertEqual(entry["env"]["PYTHONUTF8"], "1")
        self.assertEqual(entry["env"]["PYTHONIOENCODING"], "utf-8")

    def test_entry_starts_from_a_foreign_working_directory(self) -> None:
        entry = registrar.build_mcp_entry()
        with tempfile.TemporaryDirectory() as cwd:
            done = _launch(entry, entry["env"], cwd)
        self.assertEqual(done.returncode, 0, done.stderr)
        reply = json.loads(done.stdout.splitlines()[0])
        self.assertEqual(reply["result"]["serverInfo"]["name"], "smart-drive")

    def test_without_pythonpath_the_same_command_fails(self) -> None:
        """Negative control: the failure that entries without PYTHONPATH used to cause."""
        entry = registrar.build_mcp_entry()
        bare_env = {k: v for k, v in entry["env"].items() if k != "PYTHONPATH"}
        with tempfile.TemporaryDirectory() as cwd:
            done = _launch(entry, bare_env, cwd)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("No module named smart_drive", done.stderr)


class TestRegisteredEntryFromInstalledCopy(unittest.TestCase):
    """A pip-installed copy is already importable, so no PYTHONPATH is added."""

    def test_installed_copy_adds_no_pythonpath(self) -> None:
        for site_dir in ("site-packages", "dist-packages"):
            fake_file = f"/venv/lib/python3.11/{site_dir}/smart_drive/mcp/registrar.py"
            with patch.object(registrar, "__file__", fake_file):
                self.assertIsNone(registrar._source_checkout_root())
                self.assertNotIn("PYTHONPATH", registrar.build_mcp_entry()["env"])


if __name__ == "__main__":
    unittest.main()
