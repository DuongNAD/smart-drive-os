"""smart_drive.cli.cmd_path_check - Self PATH Verification and Environment Helper.

Checks whether Python script launchers (smart-drive, smart_drive) are accessible
on the system PATH, with step-by-step guidance for Windows and POSIX environments.

The advice is based on where pip really put the command for *this* interpreter (its own
environment first, then the per-user location), so it never tells anyone to add a directory
that is already on PATH or that holds nothing.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import shlex
import shutil
import site
import sys
import sysconfig
from pathlib import Path
from typing import Any, Dict, List, Optional

DIST_NAME = "smart-drive-os"
COMMAND_NAMES = ("smart-drive", "smart_drive")


def _norm(path: str) -> str:
    # No expanduser on purpose: a literal "~" inside PATH is expanded by neither zsh nor shutil.which,
    # so it must not count as the home directory here either.
    return os.path.normcase(os.path.normpath(path))


def _quote(text: str) -> str:
    """One shell word for a command the user will copy: paths with spaces are common on Windows and macOS."""
    if sys.platform == "win32":
        return f'"{text}"' if any(ch in text for ch in ' \t&()^%!') else text
    return shlex.quote(text)


def _env_scripts_dir() -> Optional[str]:
    """Where pip puts console scripts for this interpreter's own environment (a venv, a pyenv version, ...)."""
    try:
        return sysconfig.get_path("scripts")
    except (KeyError, ValueError):
        return None


def _user_scripts_dir() -> str:
    """Where `pip install --user` puts console scripts (site.getuserbase() honours PYTHONUSERBASE)."""
    try:
        base = Path(site.getuserbase())
    except Exception:  # unusual embedded or frozen interpreters
        base = Path.home() / ("AppData/Roaming/Python" if sys.platform == "win32" else ".local")
    if sys.platform == "win32":
        return str(base / f"Python{sys.version_info.major}{sys.version_info.minor}" / "Scripts")
    return str(base / "bin")


def _find_installed_script(directories: List[str]) -> Optional[str]:
    for directory in directories:
        for name in COMMAND_NAMES:
            found = shutil.which(name, path=directory)  # honours PATHEXT on Windows
            if found:
                return found
    return None


def _package_installed() -> bool:
    try:
        importlib.metadata.distribution(DIST_NAME)
        return True
    except Exception:  # PackageNotFoundError, or damaged metadata: a diagnostic command must not crash on it
        return False


def _source_checkout() -> Optional[str]:
    """The project folder this code is running from, if it is a source checkout (not site-packages)."""
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and not {"site-packages", "dist-packages"} & set(root.parts):
        return str(root)
    return None


def check_system_path() -> Dict[str, Any]:
    """Inspects system PATH and detects whether smart-drive executable is discoverable."""
    paths = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    on_path = {_norm(p) for p in paths}

    cli_executable = shutil.which("smart-drive") or shutil.which("smart_drive")
    python_cmd = sys.executable or "python"

    user_scripts_dir = _user_scripts_dir()
    candidates: List[str] = []
    for directory in (_env_scripts_dir(), user_scripts_dir):
        if directory and directory not in candidates:
            candidates.append(directory)

    installed_script = _find_installed_script(candidates)
    installed_dir = os.path.dirname(installed_script) if installed_script else None

    return {
        "platform": sys.platform,
        "cli_executable": cli_executable,
        "is_discoverable": cli_executable is not None,
        "python_executable": python_cmd,
        "user_scripts_dir": user_scripts_dir,
        "scripts_in_path": _norm(user_scripts_dir) in on_path,
        "python_module_syntax_guaranteed": True,
        "package_installed": _package_installed(),
        "installed_script": installed_script,
        "installed_scripts_dir": installed_dir,
        "installed_dir_in_path": bool(installed_dir) and _norm(installed_dir) in on_path,
        "source_checkout": _source_checkout(),
    }


def _add_to_path_advice(info: Dict[str, Any]) -> List[str]:
    """Lines telling the user how to put the directory that really holds the command on PATH."""
    directory = info["installed_scripts_dir"]
    lines = [f"\n  The command is installed in {directory}, which is not on your PATH."]
    if info["platform"] == "win32":
        lines += [
            "\n  To add it to your Windows User PATH permanently, run in PowerShell:",
            f'  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";{directory}", "User")',
        ]
        return lines
    if f"{os.sep}.pyenv{os.sep}" in directory:
        lines += [
            "\n  You use pyenv: it exposes commands through its shims, so after installing run",
            "  pyenv rehash",
            "  (and make sure ~/.pyenv/shims is on your PATH, see `pyenv init`).",
            "\n  Or add the directory itself to your ~/.zshrc or ~/.bashrc:",
        ]
    else:
        lines.append("\n  Add this line to your ~/.zshrc or ~/.bashrc:")
    lines.append(f'  export PATH="$PATH:{directory}"')
    return lines


def _install_advice(info: Dict[str, Any]) -> List[str]:
    python = info["python_executable"]
    checkout = info["source_checkout"]
    pip = f"{_quote(python)} -m pip install"
    if info["package_installed"]:
        return [
            "\n  The package is installed, but no smart-drive command was found in:",
            f"  {info['user_scripts_dir']} (and this Python's own scripts directory).",
            "  Reinstall it so the command is created:",
            f"  {pip} --force-reinstall --no-deps {_quote(checkout) if checkout else DIST_NAME}",
        ]
    lines = [f"\n  smart-drive-os is not installed for this Python ({python}), so there is no command to put on PATH."]
    if checkout:
        lines += ["  Install it from this project folder:", f"  {pip} -e {_quote(checkout)}"]
    else:
        lines += ["  Install the smart-drive-os package with:", f"  {pip} {DIST_NAME}"]
    return lines


def cmd_path_check(args: argparse.Namespace) -> int:
    """CLI handler for `smart-drive self-path-check`."""
    info = check_system_path()

    print("=" * 60)
    print("  SmartDrive-OS: Environment & PATH Verification")
    print("=" * 60)
    print(f"  Platform:          {info['platform']}")
    print(f"  Python Binary:     {info['python_executable']}")
    print(f"  CLI in PATH:       {'Yes (' + info['cli_executable'] + ')' if info['is_discoverable'] else 'No'}")
    print(f"  Installed Script:  {info['installed_script'] or 'not found for this Python'}")
    print(f"  User Scripts Dir:  {info['user_scripts_dir']}")
    print(f"  Scripts in PATH:   {'Yes' if info['scripts_in_path'] else 'No'}")
    print("=" * 60)

    if info["is_discoverable"]:
        print("\n✓ smart-drive CLI is correctly registered in your PATH!")
        print("  You can run commands directly using: smart-drive <subcommand>")
    else:
        print("\n⚠️  smart-drive is not found on your current terminal PATH.")
        if info["installed_scripts_dir"] and not info["installed_dir_in_path"]:
            lines = _add_to_path_advice(info)
        elif info["installed_scripts_dir"]:
            lines = [
                f"\n  The command is installed in {info['installed_scripts_dir']}, which is already on PATH:",
                "  open a new terminal (or run `hash -r`) so the shell picks it up.",
            ]
        else:
            lines = _install_advice(info)
        print("\n".join(lines))

    python = _quote(info["python_executable"])
    if info["package_installed"]:
        print("\n💡 Fallback that needs no PATH setup:")
        print(f"   {python} -m smart_drive <subcommand>\n")
    elif info["source_checkout"]:
        print("\n💡 Without installing, from inside the project folder only:")
        print(f"   cd {_quote(info['source_checkout'])} && {python} -m smart_drive <subcommand>\n")
    else:
        print()

    return 0


__all__ = ["check_system_path", "cmd_path_check"]
