"""smart_drive.cli.cmd_path_check - Self PATH Verification and Environment Helper.

Checks whether Python script launchers (smart-drive, smart_drive) are accessible
on the system PATH, with step-by-step guidance for Windows and POSIX environments.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict


def check_system_path() -> Dict[str, Any]:
    """Inspects system PATH and detects whether smart-drive executable is discoverable."""
    is_windows = sys.platform == "win32"
    path_env = os.environ.get("PATH", "")
    paths = [p for p in path_env.split(os.pathsep) if p]

    cli_executable = shutil.which("smart-drive") or shutil.which("smart_drive")
    python_cmd = sys.executable or "python"

    user_scripts_dir = None
    scripts_in_path = False

    if is_windows:
        app_data = os.environ.get("APPDATA")
        py_ver = f"Python{sys.version_info.major}{sys.version_info.minor}"
        if app_data:
            user_scripts_dir = str(Path(app_data) / "Python" / py_ver / "Scripts")
        else:
            user_scripts_dir = str(Path.home() / "AppData" / "Roaming" / "Python" / py_ver / "Scripts")

        if user_scripts_dir:
            scripts_in_path = any(
                os.path.normcase(os.path.normpath(p)) == os.path.normcase(os.path.normpath(user_scripts_dir))
                for p in paths
            )
    else:
        user_scripts_dir = str(Path.home() / ".local" / "bin")
        scripts_in_path = any(
            os.path.normpath(p) == os.path.normpath(user_scripts_dir)
            for p in paths
        )

    return {
        "platform": sys.platform,
        "cli_executable": cli_executable,
        "is_discoverable": cli_executable is not None,
        "python_executable": python_cmd,
        "user_scripts_dir": user_scripts_dir,
        "scripts_in_path": scripts_in_path,
        "python_module_syntax_guaranteed": True,
    }


def cmd_path_check(args: argparse.Namespace) -> int:
    """CLI handler for `smart-drive self-path-check`."""
    info = check_system_path()

    print("=" * 60)
    print("  SmartDrive-OS: Environment & PATH Verification")
    print("=" * 60)
    print(f"  Platform:          {info['platform']}")
    print(f"  Python Binary:     {info['python_executable']}")
    print(f"  CLI in PATH:       {'Yes (' + info['cli_executable'] + ')' if info['is_discoverable'] else 'No'}")
    print(f"  User Scripts Dir:  {info['user_scripts_dir']}")
    print(f"  Scripts in PATH:   {'Yes' if info['scripts_in_path'] else 'No'}")
    print("=" * 60)

    if info["is_discoverable"]:
        print("\n✓ smart-drive CLI is correctly registered in your PATH!")
        print("  You can run commands directly using: smart-drive <subcommand>")
    else:
        print("\n⚠️  smart-drive is not found on your current terminal PATH.")
        if info["platform"] == "win32" and info["user_scripts_dir"]:
            print("\n  To add Python Scripts to your Windows User PATH permanently, run in PowerShell:")
            print(f'  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";{info["user_scripts_dir"]}", "User")')
        else:
            print("\n  Add this directory to your ~/.bashrc or ~/.zshrc:")
            print(f'  export PATH="$PATH:{info["user_scripts_dir"]}"')

    print("\n💡 Guaranteed Universal Fallback:")
    print("   You can ALWAYS run SmartDrive-OS without PATH configuration using:")
    print(f"   {info['python_executable']} -m smart_drive <subcommand>\n")

    return 0


__all__ = ["check_system_path", "cmd_path_check"]
