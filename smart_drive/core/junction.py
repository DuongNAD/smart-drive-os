"""smart_drive.core.junction - NTFS Directory Junction Management Engine.

Provides safe, zero-dependency creation, inspection, and unlinking of NTFS
Directory Junctions (reparse points) on Windows without requiring Administrator
privileges or Developer Mode.

Conforms to PROJECT.md § Interface Contracts.
100% Python Standard Library (os, sys, subprocess, stat, pathlib).
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
from typing import Optional, Union

# Win32 File Attribute Flags
FILE_ATTRIBUTE_DIRECTORY: int = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT: int = 0x00000400


def is_directory_junction(path: Union[str, Path]) -> bool:
    """Return True if path exists and is an NTFS Directory Junction / reparse point directory.

    Works reliably on Windows without following the link to the target,
    meaning broken junctions with missing targets are still detected accurately.
    """
    p_str = str(path)
    try:
        st = os.lstat(p_str)
    except (OSError, ValueError):
        return False

    if sys.platform == "win32":
        attrs = getattr(st, "st_file_attributes", 0)
        is_reparse = bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
        is_dir = bool(attrs & FILE_ATTRIBUTE_DIRECTORY) or stat.S_ISDIR(st.st_mode)
        return is_reparse and is_dir
    else:
        # Cross-platform fallback for testing on POSIX systems
        return os.path.islink(p_str) and (os.path.isdir(p_str) or stat.S_ISDIR(st.st_mode))


def get_junction_target(path: Union[str, Path]) -> Optional[str]:
    """Return normalized target path pointed to by junction, stripping Windows \\?\\ prefixes.

    Returns:
        Target directory path as a clean normalized string, or None if path is not a junction.
    """
    p_str = str(path)
    if not is_directory_junction(p_str):
        return None

    try:
        raw_target = os.readlink(p_str)
    except (OSError, ValueError):
        return None

    # Strip Windows NT object manager prefixes: \\?\, \??\, \\?\UNC\
    target_str = str(raw_target)
    if target_str.startswith("\\\\?\\UNC\\"):
        target_str = "\\\\" + target_str[8:]
    elif target_str.startswith("\\\\?\\"):
        target_str = target_str[4:]
    elif target_str.startswith("\\??\\"):
        target_str = target_str[4:]

    return os.path.normpath(target_str)


def create_directory_junction(
    junction_path: Union[str, Path],
    target_path: Union[str, Path],
) -> bool:
    """Create an NTFS Directory Junction pointing junction_path to target_path.

    Uses Windows 'cmd.exe /c mklink /J' which requires NO Administrator elevation
    and NO Windows Developer Mode privilege.

    Args:
        junction_path: Path where the junction reparse point will be created.
        target_path: Existing directory on local volume to point to.

    Returns:
        True if junction was successfully created, False otherwise.
    """
    j_path = Path(junction_path).resolve()
    t_path = Path(target_path).resolve()

    if not t_path.is_dir():
        return False

    if j_path.exists() or os.path.lexists(str(j_path)):
        return False

    # Ensure parent directory of junction exists
    j_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        cmd = [
            "cmd.exe",
            "/c",
            "mklink",
            "/J",
            str(j_path),
            str(t_path),
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode != 0:
                return False
        except (subprocess.SubprocessError, OSError):
            return False
    else:
        # Cross-platform fallback: create symlink for POSIX
        try:
            os.symlink(str(t_path), str(j_path), target_is_directory=True)
        except OSError:
            return False

    return is_directory_junction(j_path)


def remove_directory_junction(junction_path: Union[str, Path]) -> bool:
    """Safely remove the directory junction link without deleting files inside the target directory!

    Critical Safety Invariant:
    Only the reparse point link is removed. Target directory and all files inside
    remain 100% intact. If junction_path is a regular directory (not a junction),
    this operation is strictly rejected to prevent accidental data deletion.

    Args:
        junction_path: Path of the directory junction to remove.

    Returns:
        True if junction link was safely removed, False otherwise.

    Raises:
        ValueError: If junction_path exists but is NOT a directory junction.
    """
    p_str = str(junction_path)
    if not os.path.lexists(p_str):
        return False

    if not is_directory_junction(p_str):
        raise ValueError(
            f"Safety Violation: '{p_str}' is a regular directory or file, not an NTFS Directory Junction! "
            "Refusing to remove to prevent data loss."
        )

    # First attempt: os.unlink (Python 3.8+ handles junctions safely on Windows)
    try:
        os.unlink(p_str)
        return not os.path.lexists(p_str)
    except OSError:
        pass

    # Second attempt: os.rmdir (Standard Win32 junction unlinking API)
    try:
        os.rmdir(p_str)
        return not os.path.lexists(p_str)
    except OSError:
        pass

    # Third attempt: cmd /c rmdir on Windows
    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["cmd.exe", "/c", "rmdir", p_str],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0:
                return not os.path.lexists(p_str)
        except Exception:
            pass

    return not os.path.lexists(p_str)
