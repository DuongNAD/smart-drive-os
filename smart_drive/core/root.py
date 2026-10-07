"""smart_drive.core.root - Safe Drive Root Resolver.

Provides authoritative, zero-guess root detection for SmartDrive-OS.
Guarantees zero data loss: never guesses cwd or falls back to hardcoded drive letters.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import sys
from typing import List, Optional, Tuple


class DriveRootNotFound(RuntimeError):
    """Raised when SmartDrive SSD root directory cannot be safely determined."""
    pass


@dataclass(frozen=True)
class RootResolution:
    """Encapsulates resolved root directory path and resolution source."""
    path: str
    source: str  # One of: "explicit", "env", "anchor", "probe"


def _has_marker(dir_path: str) -> bool:
    """Checks if directory contains a SmartDrive signature marker.

    MARKER:
    - .smart_drive directory
    - .smart_drive_manager directory
    - .metadata_never_index file
    """
    try:
        if not os.path.isdir(dir_path):
            return False
        if os.path.isdir(os.path.join(dir_path, ".smart_drive")):
            return True
        if os.path.isdir(os.path.join(dir_path, ".smart_drive_manager")):
            return True
        if os.path.isfile(os.path.join(dir_path, ".metadata_never_index")) or os.path.exists(os.path.join(dir_path, ".metadata_never_index")):
            return True
    except (OSError, PermissionError):
        pass
    return False


def _has_anchor(dir_path: str) -> bool:
    """Checks if directory contains an AI agent manifest anchor.

    ANCHOR:
    - AGENTS.md file
    - GEMINI.md file
    """
    try:
        if not os.path.isdir(dir_path):
            return False
        if os.path.isfile(os.path.join(dir_path, "AGENTS.md")) or os.path.exists(os.path.join(dir_path, "AGENTS.md")):
            return True
        if os.path.isfile(os.path.join(dir_path, "GEMINI.md")) or os.path.exists(os.path.join(dir_path, "GEMINI.md")):
            return True
    except (OSError, PermissionError):
        pass
    return False


def _is_candidate(dir_path: str) -> bool:
    """Determines if a directory is a valid SmartDrive candidate root.

    A directory is a CANDIDATE iff it has a MARKER and (it has an ANCHOR or os.path.ismount(dir) is true).
    """
    try:
        if not os.path.isdir(dir_path):
            return False
        if not _has_marker(dir_path):
            return False
        return _has_anchor(dir_path) or os.path.ismount(dir_path)
    except (OSError, PermissionError):
        return False


def _find_drive_root_internal(
    explicit: Optional[str] = None,
    start_path: Optional[str] = None,
) -> Tuple[Optional[RootResolution], Optional[str]]:
    """Internal resolver returning (RootResolution, error_message)."""
    # 1. Explicit path (from --root / constructor root)
    if explicit is not None and str(explicit).strip() != "":
        raw_explicit = str(explicit).strip()
        if os.path.isdir(raw_explicit):
            return RootResolution(os.path.abspath(raw_explicit), "explicit"), None
        return None, f"Explicit root path '{raw_explicit}' does not exist or is not a directory."

    # 2. Environment variables: SMART_DRIVE_ROOT, then KINGSTON_SSD_ROOT
    env_smart = os.environ.get("SMART_DRIVE_ROOT")
    if env_smart is not None and env_smart.strip() != "":
        val = env_smart.strip()
        if os.path.isdir(val):
            return RootResolution(os.path.abspath(val), "env"), None
        return None, f"Environment variable SMART_DRIVE_ROOT is set to '{val}', which is not an existing directory."

    env_kingston = os.environ.get("KINGSTON_SSD_ROOT")
    if env_kingston is not None and env_kingston.strip() != "":
        val = env_kingston.strip()
        if os.path.isdir(val):
            return RootResolution(os.path.abspath(val), "env"), None
        return None, f"Environment variable KINGSTON_SSD_ROOT is set to '{val}', which is not an existing directory."

    # 3. Walk up from start_path (default cwd)
    start = os.path.abspath(start_path) if start_path else str(Path.cwd())
    check_dir = start
    while True:
        if _is_candidate(check_dir):
            return RootResolution(os.path.abspath(check_dir), "anchor"), None
        parent = os.path.dirname(check_dir)
        if parent == check_dir:
            break
        check_dir = parent

    # 4. OS Probing (unless SMART_DRIVE_NO_PROBE == "1")
    if os.environ.get("SMART_DRIVE_NO_PROBE") != "1":
        candidates: List[str] = []
        system = platform.system().lower()

        # Windows: drive roots D: through Z: (never A, B, C) that have a MARKER
        if sys.platform == "win32" or "windows" in system:
            for letter_code in range(ord("D"), ord("Z") + 1):
                drive_letter = chr(letter_code)
                cand = f"{drive_letter}:\\"
                try:
                    if os.path.isdir(cand) and _has_marker(cand):
                        candidates.append(cand)
                except (OSError, PermissionError):
                    pass

        # macOS: entries of /Volumes that are CANDIDATES
        elif sys.platform == "darwin" or "darwin" in system:
            volumes_dir = "/Volumes"
            if os.path.isdir(volumes_dir):
                try:
                    for entry in sorted(os.listdir(volumes_dir)):
                        cand = os.path.join(volumes_dir, entry)
                        if os.path.isdir(cand) and _is_candidate(cand):
                            candidates.append(cand)
                except (OSError, PermissionError):
                    pass

        # Linux: /media/*, /media/*/*, /run/media/*/*, /mnt/* except /mnt/c
        else:
            # /media/* and /media/*/*
            if os.path.isdir("/media"):
                try:
                    for entry in sorted(os.listdir("/media")):
                        p1 = os.path.join("/media", entry)
                        if os.path.isdir(p1):
                            if _is_candidate(p1):
                                candidates.append(p1)
                            try:
                                for sub in sorted(os.listdir(p1)):
                                    p2 = os.path.join(p1, sub)
                                    if os.path.isdir(p2) and _is_candidate(p2):
                                        candidates.append(p2)
                            except (OSError, PermissionError):
                                pass
                except (OSError, PermissionError):
                    pass

            # /run/media/*/*
            if os.path.isdir("/run/media"):
                try:
                    for user_dir in sorted(os.listdir("/run/media")):
                        p1 = os.path.join("/run/media", user_dir)
                        if os.path.isdir(p1):
                            try:
                                for sub in sorted(os.listdir(p1)):
                                    p2 = os.path.join(p1, sub)
                                    if os.path.isdir(p2) and _is_candidate(p2):
                                        candidates.append(p2)
                            except (OSError, PermissionError):
                                pass
                except (OSError, PermissionError):
                    pass

            # /mnt/* except /mnt/c
            if os.path.isdir("/mnt"):
                try:
                    for entry in sorted(os.listdir("/mnt")):
                        if entry.lower() == "c":
                            continue
                        p1 = os.path.join("/mnt", entry)
                        if os.path.isdir(p1) and _is_candidate(p1):
                            candidates.append(p1)
                except (OSError, PermissionError):
                    pass

        # Deduplicate candidates preserving order
        dedup_candidates: List[str] = []
        for c in candidates:
            if c not in dedup_candidates:
                dedup_candidates.append(c)

        if len(dedup_candidates) == 1:
            return RootResolution(dedup_candidates[0], "probe"), None
        elif len(dedup_candidates) > 1:
            cand_str = ", ".join(dedup_candidates)
            return None, f"Multiple candidate SmartDrive roots found: {cand_str}. Specify one with --root <path> or set SMART_DRIVE_ROOT."

    return None, "Could not determine the SmartDrive root. Pass --root <path> or set SMART_DRIVE_ROOT."


def find_drive_root(
    explicit: Optional[str] = None,
    start_path: Optional[str] = None,
) -> Optional[RootResolution]:
    """Finds SmartDrive root directory. Returns None if resolution fails."""
    res, _ = _find_drive_root_internal(explicit=explicit, start_path=start_path)
    return res


def resolve_drive_root(
    explicit: Optional[str] = None,
    start_path: Optional[str] = None,
) -> RootResolution:
    """Resolves SmartDrive root directory. Raises DriveRootNotFound if resolution fails."""
    res, err_msg = _find_drive_root_internal(explicit=explicit, start_path=start_path)
    if res is not None:
        return res
    raise DriveRootNotFound(err_msg or "Could not determine the SmartDrive root. Pass --root <path> or set SMART_DRIVE_ROOT.")


__all__ = [
    "DriveRootNotFound",
    "RootResolution",
    "find_drive_root",
    "resolve_drive_root",
]
