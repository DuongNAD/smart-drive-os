"""smart_drive.core.junk_detector - 3-Tier Filesystem Junk Pattern Matcher & Classifier.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Matches macOS artifacts, Windows thumbnails & recycle bins, Python bytecode,
developer test/build caches, and temporary/crash files.
Guarantees zero-destruction of anti-indexing shields (.metadata_never_index,
.fseventsd/no_log) and inviolable root scripts/taxonomies.
"""

from __future__ import annotations

import fnmatch
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

from smart_drive.core.config import (
    ANTI_INDEXING_FSEVENT_DIR,
    ANTI_INDEXING_FSEVENT_FILE,
    ANTI_INDEXING_ROOT_FILE,
    CLUSTER_SIZE_BYTES,
    JUNK_RULES,
    JunkRule,
    JunkTier,
    calculate_allocated_bytes,
    is_protected_root_dir,
    is_protected_root_file,
    match_junk_rule,
)
from smart_drive.core.learning_units import find_unit_root
from smart_drive.core.scanner import FastDirectoryScanner, ScanEntry, ScanOptions


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

# dataclass() only accepts `slots` on Python 3.10+; even slots=False raises TypeError on 3.9.
_SLOTS_KWARGS = {"slots": True} if sys.version_info >= (3, 10) else {}


@dataclass(**_SLOTS_KWARGS)
class JunkItem:
    """Represents a filesystem entry identified as system or developer junk.

    Supports both object attribute access (.rel_path, .name) and dictionary
    subscripting (item["rel_path"], item["name"]) for backward compatibility.
    """
    path: str
    rel_path: str
    name: str
    size: int
    allocated_size: int
    is_dir: bool
    tier: JunkTier
    description: str
    os_source: str

    def __getitem__(self, key: str) -> Any:
        """Enables dict-like subscripting (e.g. item['rel_path'], item['name'])."""
        if key == "rule":
            return self.description
        if hasattr(self, key):
            val = getattr(self, key)
            if key == "tier" and isinstance(val, JunkTier):
                return int(val)
            return val
        raise KeyError(f"JunkItem has no attribute or key '{key}'")

    def __contains__(self, key: str) -> bool:
        return key == "rule" or hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        """Convert JunkItem to JSON-serializable dictionary."""
        d = asdict(self)
        d["tier"] = int(self.tier)
        d["rule"] = self.description
        return d


# ---------------------------------------------------------------------------
# Junk Detector Engine
# ---------------------------------------------------------------------------

class JunkDetector:
    """High-speed declarative pattern matcher and classifier for storage junk.

    Capabilities:
    1. 3-Tier risk categorization (Tier 1: Safe, Tier 2: Dev caches, Tier 3: Logs/Dumps).
    2. macOS AppleDouble (._*) and .DS_Store detection.
    3. Windows Thumbs.db, ehthumbs, and $RECYCLE.BIN detection.
    4. Anti-indexing shield protection (.metadata_never_index, no_log).
    5. Inviolable root file and root directory protection.
    6. Hardware-accurate 512KB cluster allocation math.
    """

    def __init__(
        self,
        drive_root: str,
        max_tier: JunkTier = JunkTier.TIER_3_SENSITIVE,
        cluster_size: int = CLUSTER_SIZE_BYTES,
    ) -> None:
        self.drive_root = os.path.realpath(os.path.abspath(drive_root))
        self.max_tier = max_tier
        self.cluster_size = cluster_size

    def _normalize_rel_path(self, target_path: str) -> str:
        """Computes clean relative path from drive root using forward slashes."""
        try:
            rel = os.path.relpath(target_path, self.drive_root)
            if rel == ".":
                return ""
            return rel.replace("\\", "/")
        except ValueError:
            return target_path.replace("\\", "/")

    def inspect_file(
        self,
        path: str,
        name: str,
        size: int,
        is_dir: bool,
        rel_path: Optional[str] = None,
    ) -> Optional[JunkItem]:
        """Inspects an individual file or directory against junk rules and safeguards.

        Args:
            path: Absolute filesystem path.
            name: Filename or directory basename.
            size: Nominal size in bytes.
            is_dir: True if directory, False if regular file.
            rel_path: Optional relative path from drive root.

        Returns:
            JunkItem if identified as junk within max_tier and not protected; else None.
        """
        clean_name = name.strip()
        norm_rel = rel_path if rel_path is not None else self._normalize_rel_path(path)
        parts = norm_rel.split("/") if norm_rel else []

        # ----------------------------------------------------------------------
        # HARD SAFEGUARD 1: Anti-Indexing Shields (NEVER JUNK)
        # ----------------------------------------------------------------------
        if clean_name in {ANTI_INDEXING_ROOT_FILE, "no_log"}:
            return None
        if norm_rel in {ANTI_INDEXING_ROOT_FILE, ANTI_INDEXING_FSEVENT_FILE}:
            return None
        if parts and parts[0].lower() == ANTI_INDEXING_FSEVENT_DIR.lower():
            if len(parts) == 1 or parts[1].lower() == "no_log":
                return None

        # ----------------------------------------------------------------------
        # HARD SAFEGUARD 2: Inviolable Root Files and Taxonomies
        # ----------------------------------------------------------------------
        is_root_level = len(parts) <= 1
        if is_root_level:
            if is_dir and is_protected_root_dir(clean_name):
                return None
            if not is_dir and is_protected_root_file(clean_name):
                return None

        # ----------------------------------------------------------------------
        # HARD SAFEGUARD 3: Learning Unit Protection (Aurora & Polaris)
        # ----------------------------------------------------------------------
        unit_root = find_unit_root(path, self.drive_root)
        if unit_root is not None:
            # Everything inside a unit is protected, EXCEPT polaris/.cache/**
            norm_fwd = norm_rel.lower().replace("\\", "/")
            is_polaris_cache = (
                "/polaris/.cache" in norm_fwd
                or norm_fwd.startswith("polaris/.cache")
                or norm_fwd.endswith("/polaris/.cache")
                or norm_fwd == "polaris/.cache"
            )
            if is_polaris_cache:
                if self.max_tier >= JunkTier.TIER_2_DEV_CACHE:
                    allocated_bytes = 0 if is_dir else calculate_allocated_bytes(size, self.cluster_size)
                    return JunkItem(
                        path=os.path.abspath(path),
                        rel_path=norm_rel,
                        name=clean_name,
                        size=size,
                        allocated_size=allocated_bytes,
                        is_dir=is_dir,
                        tier=JunkTier.TIER_2_DEV_CACHE,
                        description="Polaris regenerable API cache (Polaris 0.3.2+ keeps it on C:)",
                        os_source="Polaris",
                    )
                return None
            # All other unit data (images, progress.jsonl, captures, JSON/MD) is protected
            return None

        # ----------------------------------------------------------------------
        # RULE MATCHING: 3-Tier Pattern Matcher
        # ----------------------------------------------------------------------
        rule = match_junk_rule(
            name=clean_name,
            is_dir=is_dir,
            max_tier=self.max_tier,
            is_root_level=is_root_level,
            rel_path=norm_rel,
        )
        if not rule:
            return None

        # Calculate allocated cluster bytes
        allocated_bytes = 0 if is_dir else calculate_allocated_bytes(size, self.cluster_size)

        return JunkItem(
            path=os.path.abspath(path),
            rel_path=norm_rel,
            name=clean_name,
            size=size,
            allocated_size=allocated_bytes,
            is_dir=is_dir,
            tier=rule.tier,
            description=rule.description,
            os_source=rule.os_source,
        )

    def inspect_entry(self, entry: ScanEntry) -> Optional[JunkItem]:
        """Inspects a ScanEntry produced by FastDirectoryScanner."""
        return self.inspect_file(
            path=entry.path,
            name=entry.name,
            size=entry.size,
            is_dir=entry.is_dir,
            rel_path=entry.rel_path,
        )

    def find_junk(self, sub_path: Optional[str] = None) -> List[JunkItem]:
        """Scans the drive root or subtree and returns all detected junk items.

        Uses FastDirectoryScanner for high throughput, bypassing symlinks and
        handling permission boundaries gracefully.

        Args:
            sub_path: Optional subfolder path to restrict the scan.

        Returns:
            List of JunkItem objects matching rules up to self.max_tier.
        """
        scan_root = os.path.realpath(os.path.abspath(sub_path or self.drive_root))
        junk_items: List[JunkItem] = []

        # Only exclude version control and agent working directories during scan;
        # allow scanner to inspect system junk directories like $RECYCLE.BIN, .Trashes
        scanner_excludes = {".git", ".agents", "System Volume Information"}

        if FastDirectoryScanner is not None:
            scanner = FastDirectoryScanner(
                root_path=scan_root,
                follow_symlinks=False,
                exclude_dirs=scanner_excludes,
            )
            for entry in scanner.scan_iter():
                item = self.inspect_entry(entry)
                if item:
                    junk_items.append(item)
        else:
            stack = [scan_root]
            while stack:
                curr = stack.pop()
                try:
                    with os.scandir(curr) as it:
                        for entry in it:
                            try:
                                is_symlink = entry.is_symlink()
                                if is_symlink:
                                    continue
                                is_dir = entry.is_dir(follow_symlinks=False)
                                if is_dir:
                                    if entry.name in scanner_excludes:
                                        continue
                                    item = self.inspect_file(
                                        path=entry.path,
                                        name=entry.name,
                                        size=0,
                                        is_dir=True,
                                    )
                                    if item:
                                        junk_items.append(item)
                                    else:
                                        stack.append(entry.path)
                                else:
                                    st = entry.stat(follow_symlinks=False)
                                    item = self.inspect_file(
                                        path=entry.path,
                                        name=entry.name,
                                        size=st.st_size,
                                        is_dir=False,
                                    )
                                    if item:
                                        junk_items.append(item)
                            except (PermissionError, FileNotFoundError, OSError):
                                continue
                except (PermissionError, FileNotFoundError, OSError):
                    continue

        return junk_items

    def get_summary(self, items: Sequence[JunkItem]) -> Dict[str, Any]:
        """Calculates aggregated metrics across a collection of junk items."""
        total_nominal = sum(item.size for item in items)
        total_allocated = sum(item.allocated_size for item in items)
        by_tier: Dict[int, int] = {int(tier): 0 for tier in JunkTier}
        by_os: Dict[str, int] = {}

        for item in items:
            by_tier[int(item.tier)] = by_tier.get(int(item.tier), 0) + 1
            by_os[item.os_source] = by_os.get(item.os_source, 0) + 1

        return {
            "total_items": len(items),
            "total_nominal_bytes": total_nominal,
            "total_allocated_bytes": total_allocated,
            "slack_bytes": total_allocated - total_nominal,
            "by_tier": by_tier,
            "by_os": by_os,
        }


__all__ = [
    "JunkItem",
    "JunkDetector",
]
