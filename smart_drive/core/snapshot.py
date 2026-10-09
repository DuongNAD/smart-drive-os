"""smart_drive.core.snapshot - Snapshot & Incremental Backup Engine for SmartDrive-OS.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT, 512KB allocation blocks).
Features:
1. Point-in-time snapshot generation with streaming 64KB chunk SHA-256 hashing.
2. exFAT 512KB cluster slack and physical allocation calculations.
3. High-integrity verification (clean status, modified, missing, and untracked files).
4. Safe incremental backup with 2-second exFAT timestamp tolerance and junk filtering.
5. Inviolable boundary guards preventing recursive or self-backup destruction.
6. 100% Python Standard Library (zero external dependencies).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, Union

from smart_drive.core.config import (
    CLUSTER_SIZE_BYTES,
    DEFAULT_EXCLUDE_DIRS,
    JunkTier,
    PROTECTED_CORE_TAXONOMIES,
    calculate_allocated_bytes,
    calculate_slack_bytes,
    match_junk_rule,
)

logger = logging.getLogger("smart_drive.core.snapshot")

# 64KB streaming buffer for SHA-256 calculation
HASH_CHUNK_SIZE: int = 65_536

# Authoritative SHA-256 for 0-byte file
EMPTY_FILE_SHA256: str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Default targeted partitions for snapshots
DEFAULT_SNAPSHOT_PARTITIONS: Tuple[str, ...] = (
    "02_Learning_Knowledge",
    "03_Development_Projects",
    "05_Dev_Toolbox",
)

# Partition aliasing dictionary (case-insensitive lookup)
PARTITION_ALIASES: Dict[str, str] = {
    "03_development_projects": "03_Personal_Documents",
    "03_personal_documents": "03_Development_Projects",
}

# Hidden folders that hold the user's own work. They are left out by default (a .git folder is
# thousands of tiny objects, which is exactly the cluster slack this tool fights) but the user can
# opt in with include_hidden / --include-hidden.
USER_HIDDEN_DIRS: frozenset = frozenset({".git", ".agents"})

# This tool's own state folders (search index, snapshot manifests). Everywhere else they are bookkeeping
# that is never scanned, but inside a snapshot or backup a nested one is an archived copy of another
# managed drive: it is an ordinary hidden folder, left out and reported by default, kept with
# include_hidden. (The live one in the drive root is never reached: only the partitions are walked.)
_TOOL_STATE_DIRS: frozenset = frozenset({".smart_drive", ".smart_drive_manager"})

# OS bookkeeping: never user data, never part of a snapshot or backup, and not worth a line in the report.
_OS_BOOKKEEPING_DIRS: frozenset = frozenset({
    "$recycle.bin", "system volume information", ".spotlight-v100", ".trashes", ".fseventsd",
})

# Everything else on the tool's global exclusion list (Downloads, game libraries, Program Files, database
# service folders ...) is skipped whatever the options, but these are folders someone named, possibly
# deep inside a partition: a run says it left them out instead of dropping them silently.
_NAMED_EXCLUDED_DIRS: frozenset = (
    frozenset(d.lower() for d in DEFAULT_EXCLUDE_DIRS)
    - USER_HIDDEN_DIRS - _TOOL_STATE_DIRS - _OS_BOOKKEEPING_DIRS
)
_SYSTEM_EXCLUDED_DIRS: frozenset = _OS_BOOKKEEPING_DIRS | _NAMED_EXCLUDED_DIRS


def _prune_dirs(
    root_dir: Union[str, Path],
    dirs: List[str],
    include_hidden: bool,
    base: Union[str, Path],
    left_out: List[str],
) -> None:
    """Filters ``dirs`` in place the way os.walk expects, recording the hidden folders left out.

    OS bookkeeping folders are skipped silently. Hidden folders (.git, .github, .vscode ...) are skipped
    unless ``include_hidden`` is set, and folders on the global exclusion list (Downloads ...) are always
    skipped; every one that is skipped is appended to ``left_out`` as a path relative to ``base`` so that
    reports can say what a run did not cover.
    """
    kept: List[str] = []
    at_drive_root = os.path.normcase(os.path.abspath(str(root_dir))) == os.path.normcase(os.path.abspath(str(base)))
    for name in dirs:
        lowered = name.lower()
        if lowered in _OS_BOOKKEEPING_DIRS:
            continue
        if at_drive_root and lowered in _TOOL_STATE_DIRS:
            continue  # this tool's own live state (index, manifests), reached when the partition is the drive root
        if lowered in _NAMED_EXCLUDED_DIRS:
            left_out.append(normalize_rel_path(Path(root_dir) / name, base))
            continue
        if name.startswith(".") and not include_hidden:
            left_out.append(normalize_rel_path(Path(root_dir) / name, base))
            continue
        kept.append(name)
    dirs[:] = kept


# ==============================================================================
# Exceptions
# ==============================================================================

class SnapshotError(Exception):
    """Base exception for all snapshot operations."""
    pass


class SnapshotNotFoundError(SnapshotError):
    """Raised when a requested snapshot manifest does not exist."""
    pass


class SnapshotCorruptedError(SnapshotError):
    """Raised when a snapshot manifest JSON is malformed or unreadable."""
    pass


class BackupError(Exception):
    """Base exception for all backup operations."""
    pass


class BackupTargetInvalidError(BackupError):
    """Raised when the specified backup target violates safety boundaries."""
    pass


# ==============================================================================
# Utility Functions
# ==============================================================================

def compute_file_sha256(filepath: Union[str, Path], chunk_size: int = HASH_CHUNK_SIZE) -> str:
    """Computes streaming full SHA-256 checksum in constant 64KB memory blocks.

    Handles 0-byte files, symlinks, and granular OS errors gracefully.
    """
    p = Path(filepath)
    try:
        size = p.stat().st_size
    except OSError:
        return ""

    if size == 0:
        return EMPTY_FILE_SHA256

    hasher = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            while True:
                buf = f.read(chunk_size)
                if not buf:
                    break
                hasher.update(buf)
        return hasher.hexdigest()
    except (OSError, PermissionError) as exc:
        logger.warning("Could not read file for hashing: %s (%s)", filepath, exc)
        return ""


def normalize_rel_path(path: Union[str, Path], root: Union[str, Path]) -> str:
    """Converts a path to a forward-slash normalized relative path from root."""
    try:
        p = Path(path).resolve()
        r = Path(root).resolve()
        rel = p.relative_to(r)
        return rel.as_posix()
    except Exception:
        clean_p = str(path).replace("\\", "/").rstrip("/")
        clean_r = str(root).replace("\\", "/").rstrip("/")
        if clean_p.startswith(clean_r):
            return clean_p[len(clean_r):].lstrip("/")
        return clean_p


def sanitize_snapshot_name(name: str) -> str:
    """Sanitizes snapshot name to prevent directory traversal and invalid characters."""
    clean = name.strip().replace("\\", "/").split("/")[-1]
    clean = "".join(c for c in clean if c.isalnum() or c in ("-", "_", "."))
    if not clean or clean in (".", ".."):
        raise ValueError(f"Invalid snapshot name: '{name}'")
    return clean


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class SnapshotManifest:
    """Immutable point-in-time snapshot manifest recording file metadata and hashes."""
    name: str
    version: str
    timestamp: str               # ISO-8601 UTC
    root: str
    partitions: List[str]
    file_count: int
    total_logical_bytes: int
    total_allocated_bytes: int
    total_slack_bytes: int
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    include_hidden: bool = False                                    # hidden folders (.git ...) were walked
    excluded_dirs: List[str] = field(default_factory=list)          # folders that were left out (hidden, or on the exclusion list)

    @property
    def total_bytes(self) -> int:
        """Backward-compatible alias for total_logical_bytes."""
        return self.total_logical_bytes

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SnapshotManifest:
        return cls(
            name=str(data["name"]),
            version=str(data.get("version", "1.1.0")),
            timestamp=str(data["timestamp"]),
            root=str(data["root"]),
            partitions=list(data.get("partitions", [])),
            file_count=int(data["file_count"]),
            total_logical_bytes=int(data.get("total_logical_bytes", data.get("total_bytes", 0))),
            total_allocated_bytes=int(data.get("total_allocated_bytes", 0)),
            total_slack_bytes=int(data.get("total_slack_bytes", 0)),
            files=dict(data.get("files", {})),
            include_hidden=bool(data.get("include_hidden", False)),
            excluded_dirs=list(data.get("excluded_dirs", [])),
        )

    @classmethod
    def from_json(cls, json_str: str) -> SnapshotManifest:
        return cls.from_dict(json.loads(json_str))

    def save(self, destination: Union[str, Path]) -> Path:
        out_path = Path(destination).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return out_path

    @classmethod
    def load(cls, source: Union[str, Path]) -> SnapshotManifest:
        in_path = Path(source).resolve()
        if not in_path.is_file():
            raise SnapshotNotFoundError(f"Snapshot manifest not found at: {in_path}")
        try:
            with open(in_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except json.JSONDecodeError as exc:
            raise SnapshotCorruptedError(f"Corrupted snapshot JSON in {in_path}: {exc}") from exc


@dataclass
class SnapshotSummary:
    """Condensed summary for snapshot listing."""
    name: str
    timestamp: str
    partitions: List[str]
    file_count: int
    total_logical_bytes: int
    total_allocated_bytes: int
    path: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationReport:
    """Detailed integrity assessment comparing manifest against live disk files."""
    snapshot_name: str
    timestamp: str
    intact: bool
    verified_count: int
    corrupted_count: int
    missing_count: int
    untracked_count: int
    modified_files: List[Dict[str, Any]] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    untracked_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BackupReport:
    """Structured report of an incremental backup session."""
    source_root: str
    target_dir: str
    timestamp: str
    dry_run: bool
    copied_count: int
    skipped_count: int
    failed_count: int
    copied_bytes: int
    copied_files: List[str] = field(default_factory=list)
    skipped_files: List[str] = field(default_factory=list)
    failed_files: List[Dict[str, str]] = field(default_factory=list)
    manifest_path: str = ""
    include_hidden: bool = False
    excluded_dirs: List[str] = field(default_factory=list)          # folders that were left out (hidden, or on the exclusion list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ==============================================================================
# Snapshot Verifier
# ==============================================================================

class SnapshotVerifier:
    """Verifies file existence, size, and SHA-256 hashes against a SnapshotManifest."""

    def __init__(self, root_path: Union[str, Path]) -> None:
        self.root_path = Path(root_path).resolve()

    def verify(
        self,
        manifest: SnapshotManifest,
        check_untracked: bool = True,
        skip_junk: bool = True,
    ) -> VerificationReport:
        """Performs full integrity verification of live filesystem against manifest."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        verified_count = 0
        modified_files: List[Dict[str, Any]] = []
        missing_files: List[str] = []
        untracked_files: List[str] = []

        # 1. Verify tracked files recorded in manifest
        for rel_path, rec in manifest.files.items():
            file_abs = self.root_path / rel_path
            if not file_abs.is_file():
                missing_files.append(rel_path)
                continue

            expected_size = rec.get("size", 0)
            expected_hash = rec.get("sha256", "")

            try:
                actual_size = file_abs.stat().st_size
            except OSError:
                missing_files.append(rel_path)
                continue

            actual_hash = compute_file_sha256(file_abs)

            if actual_size != expected_size or actual_hash != expected_hash:
                modified_files.append({
                    "path": rel_path,
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash,
                    "expected_size": expected_size,
                    "actual_size": actual_size,
                })
            else:
                verified_count += 1

        # 2. Check for untracked files in covered partitions
        if check_untracked:
            for part in manifest.partitions:
                part_dir = self.root_path / part
                if not part_dir.is_dir():
                    continue

                for root_dir, dirs, files in os.walk(part_dir):
                    # Walk exactly what the snapshot walked (hidden folders only if it included them)
                    _prune_dirs(root_dir, dirs, manifest.include_hidden, self.root_path, [])
                    for f in files:
                        if skip_junk:
                            rule = match_junk_rule(f, is_dir=False, max_tier=JunkTier.TIER_3_SENSITIVE)
                            if rule is not None:
                                continue

                        file_path = Path(root_dir) / f
                        rel = normalize_rel_path(file_path, self.root_path)
                        if rel not in manifest.files:
                            untracked_files.append(rel)

        intact = (len(missing_files) == 0 and len(modified_files) == 0)

        return VerificationReport(
            snapshot_name=manifest.name,
            timestamp=ts,
            intact=intact,
            verified_count=verified_count,
            corrupted_count=len(modified_files),
            missing_count=len(missing_files),
            untracked_count=len(untracked_files),
            modified_files=modified_files,
            missing_files=missing_files,
            untracked_files=untracked_files,
        )


# ==============================================================================
# Backup Engine
# ==============================================================================

class BackupEngine:
    """Safe, incremental file synchronization engine with exFAT tolerance and junk filtering."""

    def __init__(
        self,
        root_path: Union[str, Path],
        cluster_size: int = CLUSTER_SIZE_BYTES,
    ) -> None:
        self.root_path = Path(root_path).resolve()
        self.cluster_size = cluster_size

    def validate_target(self, target_dir: Union[str, Path], partitions: List[str]) -> Path:
        """Validates that destination directory does not violate boundary constraints."""
        target_path = Path(target_dir).resolve()
        src_root = self.root_path

        # Boundary check 1: Target cannot equal source root
        if target_path == src_root:
            raise BackupTargetInvalidError(f"Backup target cannot be the source root: '{target_path}'")

        # Boundary check 2: Target cannot be inside any partition being backed up
        for part in partitions:
            part_dir = (src_root / part).resolve()
            if target_path == part_dir or part_dir in target_path.parents:
                raise BackupTargetInvalidError(
                    f"Backup target '{target_path}' cannot reside inside backed-up partition '{part}'"
                )

        return target_path

    def backup(
        self,
        target_dir: Union[str, Path],
        partitions: List[str],
        dry_run: bool = False,
        skip_junk: bool = True,
        use_hash_comparison: bool = False,
        include_hidden: bool = False,
    ) -> BackupReport:
        """Performs incremental synchronization from source partitions to target directory."""
        target_path = self.validate_target(target_dir, partitions)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        copied_files: List[str] = []
        skipped_files: List[str] = []
        failed_files: List[Dict[str, str]] = []
        excluded_dirs: List[str] = []
        copied_bytes = 0

        for part in partitions:
            src_part_dir = self.root_path / part
            if not src_part_dir.is_dir():
                continue

            for root_dir, dirs, files in os.walk(src_part_dir):
                # Filter out system folders, and hidden ones (.git ...) unless asked to include them
                _prune_dirs(root_dir, dirs, include_hidden, self.root_path, excluded_dirs)

                for f in files:
                    if skip_junk:
                        rule = match_junk_rule(f, is_dir=False, max_tier=JunkTier.TIER_3_SENSITIVE)
                        if rule is not None:
                            continue

                    src_file = Path(root_dir) / f
                    rel_path = normalize_rel_path(src_file, self.root_path)
                    dest_file = target_path / rel_path

                    try:
                        src_stat = src_file.stat()
                        src_size = src_stat.st_size
                        src_mtime = src_stat.st_mtime
                    except OSError as exc:
                        failed_files.append({"path": rel_path, "error": str(exc)})
                        continue

                    # Decision: Is destination file already up-to-date?
                    is_match = False
                    if dest_file.is_file():
                        try:
                            dest_stat = dest_file.stat()
                            dest_size = dest_stat.st_size
                            dest_mtime = dest_stat.st_mtime

                            if dest_size == src_size:
                                if use_hash_comparison:
                                    src_hash = compute_file_sha256(src_file)
                                    dest_hash = compute_file_sha256(dest_file)
                                    is_match = (src_hash == dest_hash and src_hash != "")
                                else:
                                    # 2.0s tolerance for FAT/exFAT mtime resolution
                                    is_match = (abs(src_mtime - dest_mtime) <= 2.0)
                        except OSError:
                            is_match = False

                    if is_match:
                        skipped_files.append(rel_path)
                        continue

                    # File needs to be copied
                    if dry_run:
                        copied_files.append(rel_path)
                        copied_bytes += src_size
                    else:
                        try:
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, dest_file)
                            copied_files.append(rel_path)
                            copied_bytes += src_size
                        except Exception as copy_err:
                            failed_files.append({"path": rel_path, "error": str(copy_err)})

        # Save destination backup manifest if not dry-run
        manifest_dest_str = ""
        if not dry_run:
            manifest_dest = target_path / "backup_manifest.json"
            manifest_data = {
                "backup_version": "1.1.0",
                "timestamp": ts,
                "source_root": str(self.root_path),
                "target_dir": str(target_path),
                "partitions": partitions,
                "copied_count": len(copied_files),
                "skipped_count": len(skipped_files),
                "copied_bytes": copied_bytes,
                "failed_count": len(failed_files),
                "copied_files": copied_files,
                "include_hidden": include_hidden,
                "excluded_dirs": excluded_dirs,
            }
            try:
                with open(manifest_dest, "w", encoding="utf-8") as f:
                    json.dump(manifest_data, f, indent=2, ensure_ascii=False)
                manifest_dest_str = str(manifest_dest)
            except OSError as mf_err:
                logger.warning("Could not write destination backup manifest: %s", mf_err)

        return BackupReport(
            source_root=str(self.root_path),
            target_dir=str(target_path),
            timestamp=ts,
            dry_run=dry_run,
            copied_count=len(copied_files),
            skipped_count=len(skipped_files),
            failed_count=len(failed_files),
            copied_bytes=copied_bytes,
            copied_files=copied_files,
            skipped_files=skipped_files,
            failed_files=failed_files,
            manifest_path=manifest_dest_str,
            include_hidden=include_hidden,
            excluded_dirs=excluded_dirs,
        )


# ==============================================================================
# Snapshot Manager (Facade)
# ==============================================================================

class SnapshotManager:
    """Unified coordinator for creating, listing, verifying snapshots and incremental backup."""

    def __init__(
        self,
        root_path: Union[str, Path],
        snapshot_dir: Optional[Union[str, Path]] = None,
        cluster_size: int = CLUSTER_SIZE_BYTES,
    ) -> None:
        self.root_path = Path(root_path).resolve()
        if snapshot_dir is not None:
            self.snapshot_dir = Path(snapshot_dir).resolve()
        else:
            self.snapshot_dir = self.root_path / ".smart_drive" / "snapshots"
        self.cluster_size = cluster_size
        self.verifier = SnapshotVerifier(self.root_path)
        self.backup_engine = BackupEngine(self.root_path, self.cluster_size)

    def resolve_partitions(self, requested: Optional[List[str]] = None) -> List[str]:
        """Resolves target partitions, evaluating aliases and on-disk directory existence."""
        if requested:
            resolved: List[str] = []
            for r in requested:
                clean_r = r.strip()
                if not clean_r:
                    continue
                # Special wildcard "all"
                if clean_r.lower() == "all":
                    for tax in PROTECTED_CORE_TAXONOMIES:
                        if (self.root_path / tax).is_dir() and tax not in resolved:
                            resolved.append(tax)
                    continue

                if (self.root_path / clean_r).is_dir():
                    resolved.append(clean_r)
                else:
                    alias = PARTITION_ALIASES.get(clean_r.lower())
                    if alias and (self.root_path / alias).is_dir():
                        resolved.append(alias)
                    else:
                        resolved.append(clean_r)
            return resolved

        # Default resolution for key partitions
        resolved_defaults: List[str] = []
        for def_part in DEFAULT_SNAPSHOT_PARTITIONS:
            if (self.root_path / def_part).is_dir():
                resolved_defaults.append(def_part)
            else:
                alias = PARTITION_ALIASES.get(def_part.lower())
                if alias and (self.root_path / alias).is_dir():
                    resolved_defaults.append(alias)
                elif (self.root_path / def_part).exists():
                    resolved_defaults.append(def_part)

        return resolved_defaults

    def create_snapshot(
        self,
        name: Optional[str] = None,
        partitions: Optional[List[str]] = None,
        skip_junk: bool = True,
        include_hidden: bool = False,
    ) -> SnapshotManifest:
        """Generates a streaming SHA-256 snapshot manifest for specified partitions."""
        now = time.gmtime()
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", now)

        if not name:
            name = f"snapshot_{time.strftime('%Y%m%d_%H%M%S', now)}"
        else:
            name = sanitize_snapshot_name(name)

        active_partitions = self.resolve_partitions(partitions)
        if not active_partitions:
            active_partitions = list(DEFAULT_SNAPSHOT_PARTITIONS)

        file_records: Dict[str, Dict[str, Any]] = {}
        excluded_dirs: List[str] = []
        total_logical = 0
        total_allocated = 0

        for part in active_partitions:
            part_path = self.root_path / part
            if not part_path.is_dir():
                continue

            for root_dir, dirs, files in os.walk(part_path):
                _prune_dirs(root_dir, dirs, include_hidden, self.root_path, excluded_dirs)

                for f in files:
                    if skip_junk:
                        rule = match_junk_rule(f, is_dir=False, max_tier=JunkTier.TIER_3_SENSITIVE)
                        if rule is not None:
                            continue

                    file_abs = Path(root_dir) / f
                    rel_p = normalize_rel_path(file_abs, self.root_path)

                    try:
                        st = file_abs.stat()
                        size = st.st_size
                        mtime = st.st_mtime
                    except OSError:
                        continue

                    sha = compute_file_sha256(file_abs)
                    alloc_size = calculate_allocated_bytes(size, self.cluster_size)

                    file_records[rel_p] = {
                        "size": size,
                        "allocated_size": alloc_size,
                        "mtime": mtime,
                        "sha256": sha,
                    }

                    total_logical += size
                    total_allocated += alloc_size

        total_slack = total_allocated - total_logical

        manifest = SnapshotManifest(
            name=name,
            version="1.1.0",
            timestamp=ts_iso,
            root=str(self.root_path),
            partitions=active_partitions,
            file_count=len(file_records),
            total_logical_bytes=total_logical,
            total_allocated_bytes=total_allocated,
            total_slack_bytes=total_slack,
            files=file_records,
            include_hidden=include_hidden,
            excluded_dirs=excluded_dirs,
        )

        # Save manifest to disk
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.snapshot_dir / f"{name}.json"
        manifest.save(manifest_path)

        return manifest

    def list_snapshots(self) -> List[SnapshotSummary]:
        """Lists all stored snapshot manifests in the snapshot directory."""
        if not self.snapshot_dir.is_dir():
            return []

        summaries: List[SnapshotSummary] = []
        for entry in self.snapshot_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".json":
                try:
                    manifest = SnapshotManifest.load(entry)
                    summaries.append(SnapshotSummary(
                        name=manifest.name,
                        timestamp=manifest.timestamp,
                        partitions=manifest.partitions,
                        file_count=manifest.file_count,
                        total_logical_bytes=manifest.total_logical_bytes,
                        total_allocated_bytes=manifest.total_allocated_bytes,
                        path=str(entry),
                    ))
                except Exception as exc:
                    logger.warning("Skipping unreadable snapshot file '%s': %s", entry, exc)

        # Sort descending by timestamp
        summaries.sort(key=lambda s: s.timestamp, reverse=True)
        return summaries

    def load_manifest(self, name: str) -> SnapshotManifest:
        """Loads a specific snapshot manifest by identifier name."""
        clean_name = sanitize_snapshot_name(name)
        manifest_path = self.snapshot_dir / f"{clean_name}.json"
        return SnapshotManifest.load(manifest_path)

    def verify_snapshot(self, name: str, check_untracked: bool = True) -> VerificationReport:
        """Loads and verifies a snapshot manifest against the live filesystem."""
        manifest = self.load_manifest(name)
        return self.verifier.verify(manifest, check_untracked=check_untracked)

    def incremental_backup(
        self,
        target_dir: Union[str, Path],
        partitions: Optional[List[str]] = None,
        dry_run: bool = False,
        skip_junk: bool = True,
        use_hash_comparison: bool = False,
        include_hidden: bool = False,
    ) -> BackupReport:
        """Executes incremental backup of partitions to target directory."""
        active_partitions = self.resolve_partitions(partitions)
        return self.backup_engine.backup(
            target_dir=target_dir,
            partitions=active_partitions,
            dry_run=dry_run,
            skip_junk=skip_junk,
            use_hash_comparison=use_hash_comparison,
            include_hidden=include_hidden,
        )

    def uncovered_entries(self, partitions: List[str]) -> Tuple[List[str], int]:
        """Top-level folders (and a count of loose files) that a run limited to ``partitions`` skips.

        Hidden entries and OS bookkeeping are not counted: they are never part of a run anyway.
        """
        covered = {p.lower() for p in partitions}
        folders: List[str] = []
        loose_files = 0
        try:
            with os.scandir(self.root_path) as entries:
                for entry in sorted(entries, key=lambda e: e.name.lower()):
                    name = entry.name
                    lowered = name.lower()
                    if name.startswith(".") or lowered in covered or lowered in _OS_BOOKKEEPING_DIRS:
                        continue
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders.append(name)
                    else:
                        loose_files += 1
        except OSError:
            return [], 0
        return folders, loose_files

    def coverage_notes(
        self,
        partitions: List[str],
        excluded_dirs: List[str],
        include_hidden: bool,
    ) -> List[str]:
        """Human-readable lines telling what a snapshot/backup run did NOT cover (and how to include it)."""
        notes: List[str] = []
        folders, loose_files = self.uncovered_entries(partitions)
        if folders or loose_files:
            parts: List[str] = []
            if folders:
                shown = ", ".join(folders[:6]) + (f" (+{len(folders) - 6} more)" if len(folders) > 6 else "")
                parts.append(shown)
            if loose_files:
                parts.append(f"{loose_files} loose file(s) at the root")
            notes.append(f"Not covered: {'; '.join(parts)} (add them with --partitions)")
        hidden = [e for e in excluded_dirs if e.rsplit("/", 1)[-1].startswith(".")]
        named = [e for e in excluded_dirs if e not in hidden]
        if hidden and not include_hidden:
            names = sorted({e.rsplit("/", 1)[-1] for e in hidden})
            shown_names = ", ".join(names[:5]) + (" ..." if len(names) > 5 else "")
            notes.append(
                f"Left out: {len(hidden)} hidden folder(s) ({shown_names}) - use --include-hidden to include them"
            )
        if named:
            names = sorted({e.rsplit("/", 1)[-1] for e in named})
            shown_names = ", ".join(names[:5]) + (" ..." if len(names) > 5 else "")
            notes.append(
                f"Also left out: {len(named)} folder(s) on the tool's exclusion list ({shown_names}); "
                "they are never part of a snapshot or backup"
            )
        return notes


__all__ = [
    "HASH_CHUNK_SIZE",
    "EMPTY_FILE_SHA256",
    "DEFAULT_SNAPSHOT_PARTITIONS",
    "PARTITION_ALIASES",
    "SnapshotError",
    "SnapshotNotFoundError",
    "SnapshotCorruptedError",
    "BackupError",
    "BackupTargetInvalidError",
    "compute_file_sha256",
    "normalize_rel_path",
    "sanitize_snapshot_name",
    "SnapshotManifest",
    "SnapshotSummary",
    "VerificationReport",
    "BackupReport",
    "SnapshotVerifier",
    "BackupEngine",
    "SnapshotManager",
]
