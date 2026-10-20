"""tests.test_smoke_script - packaging/smoke_test.py, the release check, is itself tested.

The script installs nothing from the checkout and imports nothing from it: it starts the CLI as a separate process
and checks what comes back (help, status, an organize dry run, `mcp register`, and the MCP server it registers).
Run against ``python -m smart_drive`` it needs no pip, venv or network, so this test covers it on every CI operating
system and Python version. ``--wheel``, ``--uvx`` and ``--pipx`` need a built wheel and an installer, so only their
argument handling is tested here, plus a negative control: a program that does not work must fail the run, not pass it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_TEST = REPO_ROOT / "packaging" / "smoke_test.py"


def run_smoke_test(*args: str, env: Optional[dict] = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(SMOKE_TEST), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        env=env,
        timeout=600,
    )


def describe(done: "subprocess.CompletedProcess[str]") -> str:
    return f"exit code {done.returncode}\n--- stdout ---\n{done.stdout}\n--- stderr ---\n{done.stderr}"


class TestSmokeScriptOnTheSourceCheckout(unittest.TestCase):
    def test_every_check_passes(self) -> None:
        # The checkout is only importable from an unrelated working directory through PYTHONPATH; the script
        # keeps the environment's PYTHONPATH in this mode and the registered MCP entry carries it as well.
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(path for path in (str(REPO_ROOT), env.get("PYTHONPATH")) if path)
        done = run_smoke_test("--", sys.executable, "-m", "smart_drive", env=env)
        self.assertEqual(done.returncode, 0, describe(done))
        self.assertIn("smoke test passed", done.stdout, describe(done))


class TestSmokeScriptFailsWhenItShould(unittest.TestCase):
    def test_a_program_that_exits_with_an_error_fails_the_run_and_is_reported(self) -> None:
        done = run_smoke_test("--", sys.executable, "-c", "import sys; sys.exit(3)")
        self.assertEqual(done.returncode, 1, describe(done))
        self.assertNotIn("smoke test passed", done.stdout, describe(done))
        # The report names the command that failed and its exit code.
        self.assertIn("--help", done.stderr, describe(done))
        self.assertIn("exit code: 3", done.stderr, describe(done))

    def test_a_program_that_does_not_exist_fails_the_run(self) -> None:
        done = run_smoke_test("--", "no-such-program-for-the-smoke-test")
        self.assertEqual(done.returncode, 1, describe(done))
        self.assertIn("no-such-program-for-the-smoke-test", done.stderr, describe(done))

    def test_the_wheel_modes_need_exactly_one_wheel(self) -> None:
        for flag in ("--wheel", "--uvx", "--pipx"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as folder:
                done = run_smoke_test(flag, folder)
                self.assertEqual(done.returncode, 1, describe(done))
                self.assertIn("found 0", done.stderr, describe(done))

                for name in ("first-1.0-py3-none-any.whl", "second-1.0-py3-none-any.whl"):
                    Path(folder, name).write_bytes(b"")
                done = run_smoke_test(flag, folder)
                self.assertEqual(done.returncode, 1, describe(done))
                self.assertIn("found 2", done.stderr, describe(done))

                missing = run_smoke_test(flag, str(Path(folder, "no-such-folder")))
                self.assertEqual(missing.returncode, 1, describe(missing))
                self.assertIn("is not a folder", missing.stderr, describe(missing))

    def test_exactly_one_way_to_start_the_program_is_required(self) -> None:
        nothing = run_smoke_test()
        self.assertEqual(nothing.returncode, 2, describe(nothing))
        with tempfile.TemporaryDirectory() as folder:
            for other in (("--uvx", folder), ("--pipx", folder), ("--", sys.executable)):
                with self.subTest(other=other[0]):
                    both = run_smoke_test("--wheel", folder, *other)
                    self.assertEqual(both.returncode, 2, describe(both))


def load_smoke_module() -> Any:
    """packaging/smoke_test.py as a module, to call its functions in this process (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("smoke_test_under_test", SMOKE_TEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the dataclasses in the script look their module up here
    spec.loader.exec_module(module)
    return module


class TestSmokeScriptResolvesRelativePaths(unittest.TestCase):
    """The docs and the release workflow write ``--wheel dist``. The folder is relative to where the script was
    started, but every command it runs starts in a sandbox folder, where ``dist/x.whl`` is no file: pip said
    "looks like a filename, but the file does not exist"."""

    def setUp(self) -> None:
        self.smoke_test = load_smoke_module()
        self.addCleanup(sys.modules.pop, self.smoke_test.__name__, None)
        start = tempfile.TemporaryDirectory()
        self.addCleanup(start.cleanup)
        self.start = Path(os.path.realpath(start.name))
        (self.start / "dist").mkdir()
        self.wheel = self.start / "dist" / "smart_drive_os-0-py3-none-any.whl"
        self.wheel.write_bytes(b"")
        self.addCleanup(os.chdir, os.getcwd())  # runs before the folder is removed: a folder in use cannot be deleted
        os.chdir(self.start)

    def main_until_the_installer(self, flag: str, installer: str) -> Tuple[int, Tuple[Any, ...]]:
        """Runs ``main([flag, "dist"])`` and stops it at the function that would install or run the wheel."""
        seen: Dict[str, Tuple[Any, ...]] = {}

        def stop(*args: Any) -> None:
            seen["args"] = args
            raise self.smoke_test.SmokeFailure("stopped before anything was installed")

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.smoke_test, installer, stop))
            if flag == "--uvx":
                stack.enter_context(mock.patch.object(self.smoke_test, "find_uvx", return_value="uvx"))
            if flag == "--pipx":
                stack.enter_context(mock.patch.object(self.smoke_test, "find_pipx", return_value=["pipx"]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            code = self.smoke_test.main([flag, "dist"])
        return code, seen["args"]

    def test_a_relative_dist_folder_reaches_the_installer_as_an_absolute_path(self) -> None:
        for flag, installer in (("--wheel", "install_wheel"), ("--uvx", "setup_uvx"), ("--pipx", "setup_pipx")):
            with self.subTest(flag=flag):
                code, args = self.main_until_the_installer(flag, installer)
                self.assertEqual(code, 1)  # the stand-in installer failed on purpose
                wheel = args[-1]
                self.assertTrue(os.path.isabs(wheel), f"{installer} got the relative path {wheel!r}")
                self.assertTrue(os.path.samefile(wheel, self.wheel), f"{installer} got {wheel!r}")

    def test_uv_is_pointed_at_the_absolute_folder(self) -> None:
        seen: Dict[str, str] = {}

        def stop_at_the_checks(smoke: Any) -> None:
            seen["links"] = smoke.base_env["UV_FIND_LINKS"]  # what every uv command of the run would get
            raise self.smoke_test.SmokeFailure("stopped before any check ran")

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.smoke_test, "find_uvx", return_value="uvx"))
            stack.enter_context(mock.patch.object(self.smoke_test, "run_checks", stop_at_the_checks))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            self.assertEqual(self.smoke_test.main(["--uvx", "dist"]), 1)
        self.assertTrue(os.path.isabs(seen["links"]), seen["links"])
        self.assertTrue(os.path.samefile(seen["links"], self.start / "dist"), seen["links"])

    def test_a_relative_program_path_becomes_absolute(self) -> None:
        name = "tool.bat" if os.name == "nt" else "tool"
        program = self.start / name
        program.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n", encoding="ascii")
        program.chmod(program.stat().st_mode | stat.S_IXUSR)
        resolved = self.smoke_test.resolve_command([os.curdir + os.sep + name, "--flag"])
        self.assertTrue(os.path.isabs(resolved[0]), resolved)
        self.assertTrue(os.path.samefile(resolved[0], program), resolved)
        self.assertEqual(resolved[1:], ["--flag"])


if __name__ == "__main__":
    unittest.main()
