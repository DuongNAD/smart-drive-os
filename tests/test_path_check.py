"""tests/test_path_check.py - `self-path-check` must give advice that can actually work.

Regression: on a pyenv Python it reported "smart-drive is not found" and told the user to add
~/.local/bin to PATH, a directory that was already on PATH and held nothing, while the command sat in
~/.pyenv/versions/<v>/bin. It also promised that `python -m smart_drive` works "always", which is only
true once the package is installed. The advice now follows where pip really put the command.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable, Dict, Optional
from unittest.mock import patch

from smart_drive.cli.cmd_path_check import (
    DIST_NAME,
    _add_to_path_advice,
    _quote,
    _user_scripts_dir,
    check_system_path,
    cmd_path_check,
)

MODULE = "smart_drive.cli.cmd_path_check"


@unittest.skipIf(sys.platform == "win32", "POSIX PATH advice")
class _PathCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sd_path_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.user_base = self.tmp / "userbase"
        self.env_bin = self.tmp / "envbin"
        self.env_bin.mkdir()

    def make_command(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        script = directory / "smart-drive"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
        return script

    def run_check(self, path_dirs: str = "", installed: bool = False, env_bin: Optional[Path] = None) -> Dict[str, object]:
        """Runs the command under a controlled PATH / interpreter layout; returns its text and the raw info."""
        distribution: Callable[..., object]
        if installed:
            distribution = lambda name: object()  # noqa: E731 - any object means "found"
        else:
            def distribution(name: str) -> object:
                raise importlib.metadata.PackageNotFoundError(name)

        out = io.StringIO()
        with patch.dict(os.environ, {"PATH": path_dirs}), \
                patch(f"{MODULE}.sysconfig.get_path", return_value=str(env_bin or self.env_bin)), \
                patch(f"{MODULE}.site.getuserbase", return_value=str(self.user_base)), \
                patch(f"{MODULE}.importlib.metadata.distribution", side_effect=distribution), \
                contextlib.redirect_stdout(out):
            info = check_system_path()
            code = cmd_path_check(argparse.Namespace())
        return {"text": out.getvalue(), "info": info, "code": code}


class TestWhereTheCommandReallyIs(_PathCase):
    def test_the_user_scripts_dir_follows_the_interpreters_user_base(self) -> None:
        result = self.run_check()
        self.assertEqual(result["info"]["user_scripts_dir"], str(self.user_base / "bin"))

    def test_a_command_in_the_environments_own_bin_is_found_even_off_path(self) -> None:
        script = self.make_command(self.env_bin)
        result = self.run_check(path_dirs="/usr/bin", installed=True)
        info = result["info"]
        self.assertEqual(info["installed_script"], str(script))
        self.assertEqual(info["installed_scripts_dir"], str(self.env_bin))
        self.assertFalse(info["installed_dir_in_path"])
        self.assertFalse(info["is_discoverable"])

    def test_the_advice_names_that_directory(self) -> None:
        self.make_command(self.env_bin)
        text = self.run_check(path_dirs="/usr/bin", installed=True)["text"]
        self.assertIn(f'export PATH="$PATH:{self.env_bin}"', text)
        self.assertNotIn(str(self.user_base), text.split("User Scripts Dir:")[1].split("\n", 2)[2])

    def test_a_pyenv_layout_gets_the_rehash_hint(self) -> None:
        pyenv_bin = self.tmp / ".pyenv" / "versions" / "3.11.8" / "bin"
        self.make_command(pyenv_bin)
        text = self.run_check(path_dirs="/usr/bin", installed=True, env_bin=pyenv_bin)["text"]
        self.assertIn("pyenv rehash", text)
        self.assertIn(f'export PATH="$PATH:{pyenv_bin}"', text)

    def test_a_literal_tilde_in_path_is_not_the_home_directory(self) -> None:
        """zsh and shutil.which do not expand a quoted "~", so ~/bin in PATH is not the real ~/bin."""
        home_bin = self.tmp / "home" / "bin"
        self.make_command(home_bin)
        with patch.dict(os.environ, {"HOME": str(self.tmp / "home")}):
            result = self.run_check(path_dirs=f"/usr/bin{os.pathsep}~/bin", installed=True, env_bin=home_bin)
        self.assertFalse(result["info"]["installed_dir_in_path"])
        self.assertFalse(result["info"]["is_discoverable"])
        self.assertIn(f'export PATH="$PATH:{home_bin}"', result["text"])

    def test_a_plain_layout_does_not_mention_pyenv(self) -> None:
        self.make_command(self.env_bin)
        self.assertNotIn("pyenv rehash", self.run_check(path_dirs="/usr/bin", installed=True)["text"])

    def test_a_command_on_path_is_reported_as_working(self) -> None:
        script = self.make_command(self.env_bin)
        result = self.run_check(path_dirs=f"/usr/bin{os.pathsep}{self.env_bin}", installed=True)
        self.assertTrue(result["info"]["is_discoverable"])
        self.assertIn("correctly registered", result["text"])
        self.assertIn(str(script), result["text"])
        self.assertNotIn("export PATH", result["text"])

    def test_a_directory_already_on_path_is_never_suggested(self) -> None:
        self.make_command(self.env_bin)

        def which(name: str, path: Optional[str] = None) -> Optional[str]:
            # The shell cannot see it (a stale lookup cache), though the directory is on PATH.
            return str(self.env_bin / "smart-drive") if path else None

        with patch(f"{MODULE}.shutil.which", side_effect=which):
            result = self.run_check(path_dirs=f"/usr/bin{os.pathsep}{self.env_bin}", installed=True)
        self.assertNotIn("export PATH", result["text"])
        self.assertIn("already on PATH", result["text"])
        self.assertIn("hash -r", result["text"])


class TestWhenTheCommandDoesNotExist(_PathCase):
    def test_not_installed_says_so_and_how_to_install(self) -> None:
        text = self.run_check(path_dirs="/usr/bin", installed=False)["text"]
        self.assertIn("not installed for this Python", text)
        self.assertIn("pip install", text)
        self.assertNotIn("export PATH", text)  # nothing to put on PATH yet

    def test_a_scripts_dir_that_is_on_path_but_empty_is_not_blamed(self) -> None:
        """The original bug: ~/.local/bin was on PATH, held nothing, and was still the advice."""
        user_bin = self.user_base / "bin"
        user_bin.mkdir(parents=True)
        result = self.run_check(path_dirs=f"/usr/bin{os.pathsep}{user_bin}", installed=False)
        self.assertTrue(result["info"]["scripts_in_path"])
        self.assertIsNone(result["info"]["installed_script"])
        self.assertNotIn("export PATH", result["text"])
        self.assertNotIn("Add this", result["text"])

    def test_an_installed_package_without_its_script_is_told_to_reinstall(self) -> None:
        text = self.run_check(path_dirs="/usr/bin", installed=True)["text"]
        self.assertIn("--force-reinstall", text)
        self.assertIn(DIST_NAME, text)

    def test_the_universal_fallback_is_only_promised_once_installed(self) -> None:
        self.assertIn("Fallback that needs no PATH setup", self.run_check(installed=True)["text"])
        not_installed = self.run_check(installed=False)["text"]
        self.assertNotIn("Fallback that needs no PATH setup", not_installed)
        self.assertNotIn("ALWAYS", not_installed)
        self.assertIn("inside the project folder", not_installed)  # we are running from a source checkout

    def test_paths_with_spaces_are_quoted_in_every_command_to_copy(self) -> None:
        checkout = "/Users/Some User/smart drive os"
        python = "/opt/my python/bin/python3"
        with patch(f"{MODULE}._source_checkout", return_value=checkout), patch("sys.executable", python):
            not_installed = self.run_check(path_dirs="/usr/bin", installed=False)["text"]
            reinstall = self.run_check(path_dirs="/usr/bin", installed=True)["text"]
        self.assertIn(f"'{python}' -m pip install -e '{checkout}'", not_installed)
        self.assertIn(f"cd '{checkout}' && '{python}' -m smart_drive", not_installed)
        self.assertIn(f"'{python}' -m pip install --force-reinstall --no-deps '{checkout}'", reinstall)

    def test_the_command_always_exits_zero(self) -> None:
        self.assertEqual(self.run_check(installed=False)["code"], 0)


class TestWindowsSpecifics(unittest.TestCase):
    """Run on every OS by faking the platform; the Windows CI job covers the real calls separately."""

    def test_the_powershell_command_names_the_real_directory(self) -> None:
        info = {"platform": "win32", "installed_scripts_dir": r"C:\Python311\Scripts"}
        text = "\n".join(_add_to_path_advice(info))
        self.assertIn(r'SetEnvironmentVariable("Path", $env:Path + ";C:\Python311\Scripts", "User")', text)
        self.assertNotIn("export PATH", text)

    def test_the_user_scripts_dir_follows_pythonuserbase(self) -> None:
        with patch(f"{MODULE}.sys.platform", "win32"), patch(f"{MODULE}.site.getuserbase", return_value=r"D:\py-user"):
            result = _user_scripts_dir()
        expected = Path(r"D:\py-user") / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts"
        self.assertEqual(Path(result), expected)

    def test_quoting_follows_the_platform(self) -> None:
        with patch(f"{MODULE}.sys.platform", "win32"):
            self.assertEqual(_quote(r"C:\Program Files\Python311\python.exe"), '"C:\\Program Files\\Python311\\python.exe"')
            self.assertEqual(_quote(r"C:\Python311\python.exe"), r"C:\Python311\python.exe")

    def test_a_missing_user_base_does_not_crash_the_check(self) -> None:
        with patch(f"{MODULE}.site.getuserbase", side_effect=AttributeError("no user site")):
            self.assertTrue(_user_scripts_dir())

    def test_damaged_package_metadata_does_not_crash_the_check(self) -> None:
        with patch(f"{MODULE}.importlib.metadata.distribution", side_effect=OSError("unreadable")):
            self.assertIs(check_system_path()["package_installed"], False)


class TestRealEnvironmentSmoke(unittest.TestCase):
    def test_the_real_check_returns_the_documented_keys(self) -> None:
        info = check_system_path()
        for key in (
            "platform", "cli_executable", "is_discoverable", "python_executable", "user_scripts_dir",
            "scripts_in_path", "python_module_syntax_guaranteed", "package_installed", "installed_script",
            "installed_scripts_dir", "installed_dir_in_path", "source_checkout",
        ):
            self.assertIn(key, info)
        self.assertIsInstance(info["package_installed"], bool)


if __name__ == "__main__":
    unittest.main()
