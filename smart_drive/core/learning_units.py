"""smart_drive.core.learning_units - Learning Unit Recognition, Caching & Boundary Guards.

Recognizes atomic educational resource folders from:
1. Aurora Slides (course.json -> Aurora course; deck.json -> Aurora deck).
2. Polaris (polaris/brief.json -> Polaris subject).

Guarantees:
- Units are atomic: movers never split units, auto-organize never moves unit roots.
- All files inside a unit are protected from deletion, except polaris/.cache (Tier 2).
- Fast upward traversal to drive root with path caching.

100% Python Standard Library. Zero external dependencies. Python 3.9+ compatible.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Global in-memory cache: resolved path string -> unit root Path (or None)
_UNIT_ROOT_CACHE: Dict[str, Optional[Path]] = {}


def clear_unit_cache() -> None:
    """Clears the unit root path resolution cache (useful between test runs)."""
    _UNIT_ROOT_CACHE.clear()


def unit_kind(root: Union[str, Path]) -> Optional[str]:
    """Inspects a directory path and returns the unit kind name, or None.

    Detection rules:
    1. Directory containing course.json -> "Aurora course"
    2. Directory containing polaris/brief.json -> "Polaris subject"
    3. Directory containing deck.json -> "Aurora deck"
    """
    try:
        p = Path(root).resolve()
        if not p.is_dir():
            return None

        # 1. Aurora Course takes precedence over standalone deck
        if (p / "course.json").is_file():
            return "Aurora course"

        # 2. Polaris Subject
        if (p / "polaris" / "brief.json").is_file():
            return "Polaris subject"

        # 3. Aurora Deck (standalone)
        if (p / "deck.json").is_file():
            return "Aurora deck"

    except (OSError, PermissionError, ValueError):
        return None

    return None


def is_unit_root(path: Union[str, Path]) -> bool:
    """Checks whether the given path is a recognized learning unit root directory."""
    return unit_kind(path) is not None


def find_unit_root(
    path: Union[str, Path],
    drive_root: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """Walks upward from path to drive_root to find an enclosing learning unit root.

    Cached per resolved directory path to ensure sub-millisecond lookup performance.

    If nested (e.g. an Aurora deck inside an Aurora course), returns the topmost
    enclosing unit root below drive_root.

    Args:
        path: File or directory path to check.
        drive_root: Optional boundary root (stops upward walk if reached).

    Returns:
        Path of the topmost unit root, or None if path is not in any unit.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None

    # Determine start directory
    try:
        curr_dir = resolved if resolved.is_dir() else resolved.parent
    except (OSError, PermissionError):
        curr_dir = resolved.parent

    cache_key = str(curr_dir)
    if cache_key in _UNIT_ROOT_CACHE:
        return _UNIT_ROOT_CACHE[cache_key]

    resolved_drive_root: Optional[Path] = None
    if drive_root is not None:
        try:
            resolved_drive_root = Path(drive_root).resolve()
        except (OSError, RuntimeError, ValueError):
            resolved_drive_root = None

    # Walk upwards collecting all unit roots on the path
    unit_roots: List[Path] = []
    curr: Optional[Path] = curr_dir

    visited_on_walk: List[str] = []

    while curr is not None:
        # Stop if we hit the drive root boundary itself
        if resolved_drive_root is not None and curr == resolved_drive_root:
            break

        visited_on_walk.append(str(curr))

        k = unit_kind(curr)
        if k is not None:
            unit_roots.append(curr)

        parent = curr.parent
        if parent == curr:
            break
        curr = parent

    # Choose the topmost unit root found
    chosen_root: Optional[Path] = unit_roots[-1] if unit_roots else None

    # Populate cache for all visited directories along this walk
    for v_key in visited_on_walk:
        _UNIT_ROOT_CACHE[v_key] = chosen_root

    return chosen_root


def is_in_learning_unit(
    path: Union[str, Path],
    drive_root: Optional[Union[str, Path]] = None,
) -> bool:
    """Returns True if the path is inside or is a recognized learning unit."""
    return find_unit_root(path, drive_root) is not None


def get_hotspot_advice(
    rel_path: str,
    is_learning_unit: bool = False,
) -> str:
    """Returns tailored project-aware storage advice for a micro-file cluster slack hotspot folder.

    Rules:
    - polaris/.cache -> 'regenerable; safe to clean (tier 2); Polaris 0.3.2+ stores it on C:'
    - polaris/captures -> 'study copies; fine unless very large'
    - Aurora images -> 'prefer fewer, larger images'
    - node_modules -> 'never on exFAT; keep toolchains on C:'
    - .git/objects -> 'run git gc or keep the git dir on NTFS'
    - otherwise -> 'bundle into an archive or move to NTFS'
    """
    norm = rel_path.replace("\\", "/").strip("/").lower()
    parts = norm.split("/") if norm else []

    if "polaris/.cache" in norm or (len(parts) >= 2 and parts[-2] == "polaris" and parts[-1] == ".cache") or norm.endswith("/.cache"):
        return "regenerable; safe to clean (tier 2); Polaris 0.3.2+ stores it on C:"

    if "polaris/captures" in norm or "captures" in parts:
        return "study copies; fine unless very large"

    if "images" in parts or norm.endswith("/images") or norm == "images":
        return "prefer fewer, larger images"

    if "node_modules" in parts:
        return "never on exFAT; keep toolchains on C:"

    if ".git/objects" in norm or (".git" in parts and "objects" in parts):
        return "run git gc or keep the git dir on NTFS"

    return "bundle into an archive or move to NTFS"


def get_learning_summary(
    drive_root: Union[str, Path],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Scans the drive root to summarize learning units and indexed chunk metrics.

    Returns:
        Dict with:
        - aurora_courses: count of Aurora course folders (course.json)
        - aurora_decks: count of standalone Aurora deck folders (deck.json)
        - polaris_subjects: count of Polaris subject folders (polaris/brief.json)
        - chunk_count: total chunks in doc_chunks SQLite table
        - last_index_update: ISO timestamp / formatted string of last index update
    """
    root_p = Path(drive_root).resolve()
    courses_count = 0
    decks_count = 0
    polaris_count = 0

    excluded_dir_names = {
        ".git", ".agents", "$recycle.bin", "system volume information",
        ".smart_drive", ".smart_drive_manager", "node_modules", ".venv"
    }

    if root_p.is_dir():
        for dirpath, dirnames, filenames in os.walk(str(root_p)):
            # Prune excluded system dirs
            dirnames[:] = [d for d in dirnames if d.lower() not in excluded_dir_names and not d.startswith("._")]

            curr = Path(dirpath)
            # Check unit kind
            k = unit_kind(curr)
            if k == "Aurora course":
                courses_count += 1
                # Do not recurse into course lessons to double-count lesson decks as standalone decks
                continue
            elif k == "Polaris subject":
                polaris_count += 1
                continue
            elif k == "Aurora deck":
                # Check if it's not inside an already counted course
                parent_unit = find_unit_root(curr.parent, drive_root=root_p)
                if parent_unit is None or unit_kind(parent_unit) != "Aurora course":
                    decks_count += 1

    # Query SQLite database for doc_chunks and last_index_update
    chunk_count = 0
    last_update: Optional[str] = None

    target_db = db_path
    if target_db is None:
        new_db = root_p / ".smart_drive" / "index.db"
        legacy_db = root_p / ".smart_drive_manager" / "index.db"
        if new_db.is_file():
            target_db = str(new_db)
        elif legacy_db.is_file():
            target_db = str(legacy_db)

    if target_db and os.path.isfile(target_db):
        try:
            con = sqlite3.connect(target_db, timeout=2.0)
            cur = con.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM doc_chunks;")
                row = cur.fetchone()
                if row:
                    chunk_count = int(row[0])
            except sqlite3.OperationalError:
                chunk_count = 0

            try:
                cur.execute(
                    "SELECT value FROM index_meta WHERE key IN ('last_incremental_index', 'last_full_index') "
                    "ORDER BY key DESC LIMIT 1;"
                )
                meta_row = cur.fetchone()
                if meta_row and meta_row[0]:
                    import datetime
                    try:
                        ts_val = float(meta_row[0])
                        dt = datetime.datetime.fromtimestamp(ts_val, tz=datetime.timezone.utc)
                        last_update = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    except (ValueError, TypeError):
                        last_update = str(meta_row[0])
            except sqlite3.OperationalError:
                last_update = None

            con.close()
        except Exception:
            pass

    return {
        "aurora_courses": courses_count,
        "aurora_decks": decks_count,
        "polaris_subjects": polaris_count,
        "chunk_count": chunk_count,
        "last_index_update": last_update,
    }


__all__ = [
    "clear_unit_cache",
    "unit_kind",
    "is_unit_root",
    "find_unit_root",
    "is_in_learning_unit",
    "get_hotspot_advice",
    "get_learning_summary",
]
