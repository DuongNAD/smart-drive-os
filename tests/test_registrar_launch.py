"""tests/test_registrar_launch.py - The registered MCP entry must start from any working directory.

MCP clients start their servers from an arbitrary directory. `python -m smart_drive mcp` only works
there when the package is importable, so an entry written from a source checkout has to carry
PYTHONPATH (the launchers do the same). A pip-installed copy needs nothing extra, and neither does one
in an environment that persists (`pipx install`, `uv tool install`).

`uvx smart-drive-os ...` is different: uv unpacks its environment into its cache and may delete it at any
time (`uv cache clean`, `uv cache prune`, a newer release), so that interpreter must never reach an agent's
config. The entry starts `uvx smart-drive-os mcp` instead. `pipx run smart-drive-os ...` is the same: pipx
deletes its temporary environment (<PIPX_HOME>/.cache/<hash>) after 14 days without use, so the entry starts
`pipx run smart-drive-os mcp`.

The launch tests run with `-S` (no site-packages) so that an editable install on the developer's
machine cannot hide a missing PYTHONPATH.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple
from unittest.mock import patch

from smart_drive.mcp import bootstrap, registrar

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


WINDOWS = sys.platform == "win32"
EXE = ".exe" if WINDOWS else ""  # uv ships uv.exe and uvx.exe on Windows only
SCRIPTS_DIR = "Scripts" if WINDOWS else "bin"
SITE_PACKAGES = ("Lib", "site-packages") if WINDOWS else ("lib", "python3.11", "site-packages")
ENTRY_ENV = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}  # what every entry sets, and nothing more


class _EnvironmentCase(unittest.TestCase):
    """Makes the registrar believe it runs from a made-up environment: prefix, interpreter, `UV` and PATH lookup."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="sd_registrar_")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()

    @staticmethod
    def make_file(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return path

    def make_uv_tools(self, folder: str, with_uvx: bool = True) -> Tuple[Path, Path]:
        """A fake `uv` in <tmp>/<folder>, and the `uvx` next to it when `with_uvx`; returns where both are."""
        uv = self.make_file(self.tmp / folder / f"uv{EXE}")
        uvx = uv.with_name(f"uvx{EXE}")
        if with_uvx:
            self.make_file(uvx)
        return uv, uvx

    def register_workspace_only(self) -> Dict[str, Any]:
        """Runs register_ide_configs for a workspace folder with every global agent config switched off.

        Call it inside `environment()`. Returns the `smart-drive` server written to <workspace>/.mcp.json. The
        agents' config paths are redirected into the temp dir as well, and none of them may have been written.
        """
        workspace = self.tmp / "workspace"
        workspace.mkdir()
        agent_configs = {
            name: self.tmp / "agents" / f"{name}.json"
            for name in ("antigravity", "claude", "claude_code", "cursor", "codex", "windsurf")
        }
        flags = {"antigravity": False, "claude": False, "cursor": False, "windsurf": False}
        with patch.object(registrar, "get_agent_config_paths", return_value=agent_configs):
            results = registrar.register_ide_configs(target_dir=str(workspace), flags=flags)
        self.assertEqual(results, {"workspace": True})
        self.assertEqual([name for name, path in agent_configs.items() if path.exists()], [])
        written = json.loads((workspace / ".mcp.json").read_text(encoding="utf-8"))
        return written["mcpServers"]["smart-drive"]

    @contextlib.contextmanager
    def environment(
        self,
        prefix: Path,
        uv: Optional[Path] = None,
        uvx_on_path: Optional[Path] = None,
        pipx_on_path: Optional[Path] = None,
    ) -> Iterator[Path]:
        """Runs as if installed in `prefix`, with `UV` naming `uv` (unset when None); the `uvx` and `pipx` that
        PATH lookups find are `uvx_on_path` and `pipx_on_path` (nothing when None).

        Yields the environment's interpreter. The module's own file is moved into the prefix's site-packages, so
        a source checkout is not mistaken for the layout under test.
        """
        python = self.make_file(prefix / SCRIPTS_DIR / f"python{EXE}")
        module = prefix.joinpath(*SITE_PACKAGES, "smart_drive", "mcp", "registrar.py")
        on_path = {"uvx": uvx_on_path, "pipx": pipx_on_path}

        def which(name: str, *args: object, **kwargs: object) -> Optional[str]:
            found = on_path.get(name)
            return str(found) if found is not None else None

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ))
            os.environ.pop("UV", None)
            if uv is not None:
                os.environ["UV"] = str(uv)
            stack.enter_context(patch.object(sys, "prefix", str(prefix)))
            stack.enter_context(patch.object(sys, "executable", str(python)))
            stack.enter_context(patch.object(registrar, "__file__", str(module)))
            stack.enter_context(patch.object(registrar.shutil, "which", side_effect=which))
            yield python


class TestUvxEnvironmentDetection(unittest.TestCase):
    """uv unpacks the environment of `uvx` / `uv tool run` into <cache>/archive-v<N>/<id>."""

    @staticmethod
    def detected(bucket: str) -> bool:
        with patch.object(sys, "prefix", str(Path("cache") / bucket / "9eCBIXNJ0fn2LssP")):
            return registrar._uvx_environment()

    def test_the_archive_buckets_of_the_uv_cache_are_recognised(self) -> None:
        for bucket in ("archive-v0", "archive-v12", "Archive-V3"):  # Windows folder names ignore case
            with self.subTest(bucket=bucket):
                self.assertTrue(self.detected(bucket))

    def test_other_folders_are_not(self) -> None:
        for bucket in ("archive", "archive-v", "my-archive-v0", "archive-v0.bak", "archive-v1x", "venvs", "tools"):
            with self.subTest(bucket=bucket):
                self.assertFalse(self.detected(bucket))

    def test_only_the_direct_parent_of_the_prefix_counts(self) -> None:
        for prefix in (Path("archive-v0"), Path("archive-v0") / "9eCBIXNJ0fn2LssP" / "inner"):
            with self.subTest(prefix=str(prefix)), patch.object(sys, "prefix", str(prefix)):
                self.assertFalse(registrar._uvx_environment())


class TestRegisteredEntryUnderUvx(_EnvironmentCase):
    """`uvx smart-drive-os ...` runs from a cache folder that uv may delete: its interpreter must not be recorded."""

    def setUp(self) -> None:
        super().setUp()
        self.prefix = self.tmp / "uv-cache" / "archive-v0" / "9eCBIXNJ0fn2LssP"
        self.uv, self.uvx = self.make_uv_tools("uv-install")

    def test_the_package_name_is_the_one_uvx_runs(self) -> None:
        self.assertEqual(registrar.DIST_NAME, "smart-drive-os")

    def test_the_uvx_next_to_the_uv_that_ran_is_used(self) -> None:
        with self.environment(self.prefix, uv=self.uv):
            from_default = registrar.build_mcp_entry()
            from_executable = registrar.build_mcp_entry(sys.executable)  # what register_ide_configs passes
        expected = {"command": str(self.uvx), "args": ["smart-drive-os", "mcp"], "env": ENTRY_ENV}
        self.assertEqual(from_default, expected)
        self.assertEqual(from_executable, expected)

    def test_the_cache_interpreter_never_reaches_the_entry(self) -> None:
        with self.environment(self.prefix, uv=self.uv) as python:
            entry = registrar.build_mcp_entry()
        recorded = [entry["command"], *entry["args"], *entry["env"].values()]
        self.assertNotIn(str(python), recorded)
        self.assertFalse(any(str(self.prefix) in text for text in recorded))

    def test_a_uvx_on_path_is_used_when_uv_is_not_known(self) -> None:
        on_path = self.make_file(self.tmp / "tools" / f"uvx{EXE}")
        with self.environment(self.prefix, uvx_on_path=on_path):
            entry = registrar.build_mcp_entry()
        self.assertEqual(entry, {"command": str(on_path), "args": ["smart-drive-os", "mcp"], "env": ENTRY_ENV})

    def test_a_uvx_on_path_beats_uv_tool_run(self) -> None:
        uv, uvx = self.make_uv_tools("uv-only", with_uvx=False)
        on_path = self.make_file(self.tmp / "tools" / f"uvx{EXE}")
        with self.environment(self.prefix, uv=uv, uvx_on_path=on_path):
            entry = registrar.build_mcp_entry()
        self.assertFalse(uvx.exists())
        self.assertEqual(entry, {"command": str(on_path), "args": ["smart-drive-os", "mcp"], "env": ENTRY_ENV})

    def test_uv_tool_run_when_there_is_no_uvx_anywhere(self) -> None:
        uv, uvx = self.make_uv_tools("uv-only", with_uvx=False)
        with self.environment(self.prefix, uv=uv):
            entry = registrar.build_mcp_entry()
        self.assertFalse(uvx.exists())
        self.assertEqual(entry, {"command": str(uv), "args": ["tool", "run", "smart-drive-os", "mcp"], "env": ENTRY_ENV})

    def test_a_bare_uvx_when_nothing_is_found(self) -> None:
        with self.environment(self.prefix):
            entry = registrar.build_mcp_entry()
        self.assertEqual(entry, {"command": "uvx", "args": ["smart-drive-os", "mcp"], "env": ENTRY_ENV})

    def test_a_uv_variable_naming_no_file_is_ignored(self) -> None:
        gone = self.tmp / "removed" / f"uv{EXE}"
        with self.environment(self.prefix, uv=gone):
            self.assertEqual(registrar.build_mcp_entry()["command"], "uvx")
        on_path = self.make_file(self.tmp / "tools" / f"uvx{EXE}")
        with self.environment(self.prefix, uv=gone, uvx_on_path=on_path):
            self.assertEqual(registrar.build_mcp_entry()["command"], str(on_path))

    def test_an_explicit_other_interpreter_keeps_the_module_form(self) -> None:
        other = self.make_file(self.tmp / "other-env" / SCRIPTS_DIR / f"python{EXE}")
        with self.environment(self.prefix, uv=self.uv):
            entry = registrar.build_mcp_entry(str(other))
        self.assertEqual(entry, {"command": str(other), "args": ["-m", "smart_drive", "mcp"], "env": ENTRY_ENV})

    def test_there_is_no_checkout_to_put_on_pythonpath(self) -> None:
        with self.environment(self.prefix, uv=self.uv):
            self.assertIsNone(registrar._source_checkout_root())

    def test_register_ide_configs_writes_the_uvx_entry(self) -> None:
        with self.environment(self.prefix, uv=self.uv):
            server = self.register_workspace_only()
        self.assertEqual(server, {"command": str(self.uvx), "args": ["smart-drive-os", "mcp"], "env": ENTRY_ENV})

    def test_the_setup_manifest_entry_matches_and_has_no_checkout_path(self) -> None:
        with self.environment(self.prefix, uv=self.uv):
            entry = bootstrap.self_manifest_entry()
        self.assertEqual(entry["command"], str(self.uvx))
        self.assertEqual(entry["args"], ["smart-drive-os", "mcp"])
        self.assertEqual(entry["env"], ENTRY_ENV)
        self.assertNotIn("path", entry)


class TestPipxRunEnvironmentDetection(_EnvironmentCase):
    """`pipx run` unpacks a temporary environment into <PIPX_HOME>/.cache/<hash>, marked by pipx_metadata.json."""

    def detected(self, prefix: Path, metadata: bool = True) -> bool:
        if metadata:
            self.make_file(prefix / "pipx_metadata.json")
        else:
            prefix.mkdir(parents=True)
        with patch.object(sys, "prefix", str(prefix)):
            return registrar._pipx_run_environment()

    def test_a_cache_folder_holding_pipx_metadata_is_recognised(self) -> None:
        self.assertTrue(self.detected(self.tmp / "pipx" / ".cache" / "a1b2c3d4e5f6"))

    def test_other_layouts_are_not(self) -> None:
        cases = {
            "a cache folder without the metadata": (self.tmp / "a" / ".cache" / "a1b2c3d4e5f6", False),
            "the persistent pipx venv": (self.tmp / "b" / "venvs" / "smart-drive-os", True),
            "a folder called cache, not .cache": (self.tmp / "c" / "cache" / "a1b2c3d4e5f6", True),
            "only the direct parent counts": (self.tmp / "d" / ".cache" / "a1b2c3d4e5f6" / "inner", True),
        }
        for name, (prefix, metadata) in cases.items():
            with self.subTest(layout=name):
                self.assertFalse(self.detected(prefix, metadata))


class TestRegisteredEntryUnderPipxRun(_EnvironmentCase):
    """`pipx run smart-drive-os ...` runs from a cache folder that pipx deletes after 14 days without use."""

    def setUp(self) -> None:
        super().setUp()
        self.prefix = self.tmp / "pipx" / ".cache" / "a1b2c3d4e5f6"
        self.make_file(self.prefix / "pipx_metadata.json")
        self.pipx = self.make_file(self.tmp / "tools" / f"pipx{EXE}")

    def test_pipx_run_is_recorded_instead_of_the_cache_interpreter(self) -> None:
        with self.environment(self.prefix, pipx_on_path=self.pipx):
            from_default = registrar.build_mcp_entry()
            from_executable = registrar.build_mcp_entry(sys.executable)  # what register_ide_configs passes
        expected = {"command": str(self.pipx), "args": ["run", "smart-drive-os", "mcp"], "env": ENTRY_ENV}
        self.assertEqual(from_default, expected)
        self.assertEqual(from_executable, expected)

    def test_a_bare_pipx_when_it_is_not_on_path(self) -> None:
        with self.environment(self.prefix):
            entry = registrar.build_mcp_entry()
        self.assertEqual(entry, {"command": "pipx", "args": ["run", "smart-drive-os", "mcp"], "env": ENTRY_ENV})

    def test_the_cache_interpreter_never_reaches_the_entry(self) -> None:
        with self.environment(self.prefix, pipx_on_path=self.pipx) as python:
            entry = registrar.build_mcp_entry()
        recorded = [entry["command"], *entry["args"], *entry["env"].values()]
        self.assertNotIn(str(python), recorded)
        self.assertFalse(any(str(self.prefix) in text for text in recorded))

    def test_a_cache_folder_without_pipx_metadata_keeps_the_module_form(self) -> None:
        elsewhere = self.tmp / "elsewhere" / ".cache" / "a1b2c3d4e5f6"  # some other tool's .cache folder
        with self.environment(elsewhere, pipx_on_path=self.pipx) as python:
            entry = registrar.build_mcp_entry()
        self.assertEqual(entry, {"command": str(python), "args": ["-m", "smart_drive", "mcp"], "env": ENTRY_ENV})

    def test_an_explicit_other_interpreter_keeps_the_module_form(self) -> None:
        other = self.make_file(self.tmp / "other-env" / SCRIPTS_DIR / f"python{EXE}")
        with self.environment(self.prefix, pipx_on_path=self.pipx):
            entry = registrar.build_mcp_entry(str(other))
        self.assertEqual(entry, {"command": str(other), "args": ["-m", "smart_drive", "mcp"], "env": ENTRY_ENV})

    def test_register_ide_configs_writes_the_pipx_run_entry(self) -> None:
        with self.environment(self.prefix, pipx_on_path=self.pipx):
            server = self.register_workspace_only()
        self.assertEqual(server, {"command": str(self.pipx), "args": ["run", "smart-drive-os", "mcp"], "env": ENTRY_ENV})

    def test_the_setup_manifest_entry_matches_and_has_no_checkout_path(self) -> None:
        with self.environment(self.prefix, pipx_on_path=self.pipx):
            entry = bootstrap.self_manifest_entry()
        self.assertEqual(entry["command"], str(self.pipx))
        self.assertEqual(entry["args"], ["run", "smart-drive-os", "mcp"])
        self.assertEqual(entry["env"], ENTRY_ENV)
        self.assertNotIn("path", entry)


class TestRegisteredEntryFromPersistentEnvironments(_EnvironmentCase):
    """`pipx install` and `uv tool install` keep their environment, so its interpreter is the right command."""

    def test_the_interpreter_of_a_persistent_environment_is_recorded(self) -> None:
        uv, _ = self.make_uv_tools("uv-install")
        pipx = self.make_file(self.tmp / "tools" / f"pipx{EXE}")
        layouts = {
            "pipx install": self.tmp / "pipx" / "venvs" / "smart-drive-os",
            "uv tool install": self.tmp / "uv" / "tools" / "smart-drive-os",
            "virtualenv": self.tmp / "project" / ".venv",
        }
        self.make_file(layouts["pipx install"] / "pipx_metadata.json")  # pipx marks every venv it manages
        for name, prefix in layouts.items():
            # UV is set (as under any uv command) and uvx and pipx exist: neither may be used outside their caches
            with self.subTest(layout=name), self.environment(prefix, uv=uv, pipx_on_path=pipx) as python:
                expected = {"command": str(python), "args": ["-m", "smart_drive", "mcp"], "env": ENTRY_ENV}
                self.assertEqual(registrar.build_mcp_entry(), expected)
                self.assertEqual(registrar.build_mcp_entry(sys.executable), expected)
                manifest = bootstrap.self_manifest_entry()
                self.assertEqual((manifest["command"], manifest["args"]), (str(python), ["-m", "smart_drive", "mcp"]))
                self.assertNotIn("path", manifest)


if __name__ == "__main__":
    unittest.main()
