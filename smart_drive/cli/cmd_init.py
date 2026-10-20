"""smart_drive.cli.cmd_init - 1-Touch SSD Initialization Command."""

from __future__ import annotations

import argparse
import json
import os
import sys

from typing import Optional

from smart_drive.core.fsinfo import detect_filesystem
from smart_drive.core.initializer import DriveInitializer, PROFILES, find_enclosing_code_dir
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def normalize_drive_path(path_spec: str) -> str:
    """Normalizes drive letters and root paths to canonical form (e.g. 'D:' -> 'D:\\')."""
    s = path_spec.strip()
    if len(s) == 2 and s[1] == ":" and s[0].isalpha():
        return f"{s[0].upper()}:\\"
    if len(s) == 1 and s[0].isalpha():
        return f"{s.upper()}:\\"
    if len(s) == 3 and s[1] == ":" and s[0].isalpha() and s[2] in ("\\", "/"):
        return f"{s[0].upper()}:\\"
    return s


def _is_os_system_root(path: str) -> bool:
    """True when path is the primary OS root (C:\\ on Windows, / on POSIX)."""
    abs_p = os.path.abspath(path)
    if sys.platform == "win32":
        drv, tail = os.path.splitdrive(abs_p)
        return drv.upper() in ("A:", "B:", "C:") and tail in ("\\", "/", "")
    return abs_p in ("/", "/System/Volumes/Data")


def _is_volume_root(path: str) -> bool:
    """True when path is the root of a volume (a Windows drive root or a POSIX mount point)."""
    abs_p = os.path.abspath(path)
    if sys.platform == "win32":
        drv, tail = os.path.splitdrive(abs_p)
        return bool(drv) and tail in ("\\", "/", "")
    return os.path.ismount(abs_p)


def default_init_root(start_dir: Optional[str] = None) -> Optional[str]:
    """Determines the volume root to initialize when a drive has no markers yet.

    Uses detect_filesystem(start_dir).mount_point (or the Windows drive root of start_dir),
    and never returns a directory that contains `smart_drive/`.
    """
    abs_start = os.path.abspath(start_dir or os.getcwd())
    info = detect_filesystem(abs_start)
    mp = info.mount_point.strip() if info and info.mount_point else ""
    if not mp and sys.platform == "win32":
        drive, _ = os.path.splitdrive(abs_start)
        if drive:
            mp = f"{drive.upper()}\\"

    if mp:
        abs_mp = os.path.abspath(mp)
        if not _is_os_system_root(abs_mp) and find_enclosing_code_dir(abs_mp) is None:
            return abs_mp

    # If running from inside <drive>/repo (which contains smart_drive/), step above the repo,
    # but only onto a real volume root: an ordinary parent folder must never become a drive root.
    code_dir = find_enclosing_code_dir(abs_start)
    if code_dir is not None:
        parent = os.path.dirname(code_dir)
        if (
            parent
            and parent != code_dir
            and _is_volume_root(parent)
            and not _is_os_system_root(parent)
            and find_enclosing_code_dir(parent) is None
        ):
            return parent

    return None


def resolve_init_target(
    raw_path: Optional[str] = None,
    raw_root: Optional[str] = None,
    start_dir: Optional[str] = None,
) -> str:
    """Resolves the target drive root for `init`, refusing any directory containing `smart_drive/`."""
    abs_start = os.path.abspath(start_dir or os.getcwd())

    if raw_path:
        target = os.path.abspath(normalize_drive_path(raw_path))
        code_dir = find_enclosing_code_dir(target)
        if code_dir is not None:
            raise DriveRootNotFound(
                f"Refusing to initialize inside '{code_dir}' because it contains 'smart_drive/'. "
                "Specify the drive root instead."
            )
        return target

    if raw_root:
        resolved = resolve_drive_root(normalize_drive_path(raw_root), start_path=abs_start)
        code_dir = find_enclosing_code_dir(resolved.path)
        if code_dir is not None:
            raise DriveRootNotFound(
                f"Refusing to initialize inside '{code_dir}' because it contains 'smart_drive/'. "
                "Specify the drive root instead."
            )
        return resolved.path

    try:
        resolved = resolve_drive_root(None, start_path=abs_start)
        if find_enclosing_code_dir(resolved.path) is None:
            return resolved.path
    except DriveRootNotFound as err:
        msg = str(err)
        if msg.startswith("Environment variable ") or msg.startswith("Multiple candidate "):
            raise

    fallback = default_init_root(abs_start)
    if fallback is not None:
        return fallback

    raise DriveRootNotFound(
        "Could not determine a safe volume root to initialize. Pass --root <path> or set SMART_DRIVE_ROOT."
    )


def cmd_init(args: argparse.Namespace) -> int:
    """Handles the `init` subcommand."""
    raw_path = getattr(args, "path", None)
    raw_root = getattr(args, "root", None)
    try:
        target_path = resolve_init_target(raw_path=raw_path, raw_root=raw_root)
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    profile = getattr(args, "profile", "general-workspace")
    force = getattr(args, "force", False)

    if not getattr(args, "json", False):
        print(f"Initializing SmartDrive volume root at: {target_path}")

    try:
        initializer = DriveInitializer(target_path)
        result = initializer.initialize(target_path=target_path, profile=profile, force=force)
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 2

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(f"✓ Initialized SmartDrive workspace at: {result['root']}")
    print(f"  Profile: {result['profile']}")
    print(f"  Created Taxonomies: {len(result['created_dirs'])} dirs")
    print(f"  Anti-Indexing Shields: {'Active' if result['shields'].get('all_healthy') else 'Partial'}")
    print(f"  Manifests Created: {', '.join(result['manifests']) if result['manifests'] else 'Existing preserved'}")
    print(f"  FTS5 Search DB: {'Ready' if result['database_initialized'] else 'Error'}")

    return 0

