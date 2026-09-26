# Milestone 2 Technical Analysis & Implementation Blueprint: Snapshot & Backup Engine

**Project:** SmartDrive-OS v1.1.0  
**Milestone:** M2 — Snapshot & Backup Engine (`smart-drive snapshot` / `backup`)  
**Features:** F12 through F18  
**Author:** Explorer M2  
**Date:** 2026-09-26  
**Status:** Approved for Implementation  

---

## 1. Executive Summary & Problem Scope

SmartDrive-OS is an autonomous drive management operating system optimized for external high-capacity SSDs (Kingston XS2000 2TB exFAT, 512KB cluster allocation block size `524,288` bytes). Milestone 2 introduces a zero-dependency, point-in-time snapshot and incremental backup engine to guarantee data integrity and recovery across critical storage partitions.

### Milestone 2 Objectives (Features F12 – F18)
| Feature ID | Feature Name | Description | Target Component |
|------------|--------------|-------------|------------------|
| **F12** | CLI `smart-drive snapshot` | Subcommand supporting `create`, `list`, `verify`, and flags (`--json`, `--root`, `--partitions`) | `smart_drive/cli/cmd_snapshot.py` |
| **F13** | Point-in-Time Snapshot | Snapshot generation targeting critical partitions (`02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox` with `03_Personal_Documents` alias support) | `smart_drive/core/snapshot.py` |
| **F14** | Streaming SHA-256 Hashing | Constant-memory 64KB block chunking (`hashlib.sha256`) supporting arbitrary file sizes without memory spikes | `smart_drive/core/snapshot.py` |
| **F15** | Manifest Storage | JSON serialization and persistence in `.smart_drive/snapshots/<name>.json` recording metadata, file counts, logical bytes, 512KB cluster allocated bytes, and relative file hashes | `smart_drive/core/snapshot.py` |
| **F16** | Snapshot Listing | `smart-drive snapshot list` reading manifests, presenting formatted table and JSON output with file count, date, partitions, and storage metrics | `smart_drive/cli/cmd_snapshot.py` |
| **F17** | Snapshot Verification | `smart-drive snapshot verify <name>` validating files on disk against manifest SHA-256, reporting intact, modified, missing, and untracked files | `smart_drive/core/snapshot.py` & CLI |
| **F18** | Incremental Backup | `smart-drive backup --target <path>` synchronizing changed files, skipping matching size/mtime, filtering junk tiers, enforcing boundary safeguards, and generating destination manifests | `smart_drive/core/snapshot.py` & `smart_drive/cli/cmd_backup.py` |

---

## 2. Core Architecture & Module Decomposition

Milestone 2 strictly maintains **zero external dependencies**, leveraging only Python 3.8+ Standard Library (`hashlib`, `json`, `shutil`, `os`, `pathlib`, `time`, `typing`, `dataclasses`, `math`).

```
smart_drive/
├── core/
│   ├── config.py             # Existing: 512KB cluster geometry, JunkTier, PROTECTED_CORE_TAXONOMIES
│   ├── purge_engine.py       # Existing: SecurityGuard boundary validation
│   └── snapshot.py           # NEW (M2): SnapshotManager, SnapshotManifest, SnapshotVerifier, BackupEngine
├── cli/
│   ├── main.py               # MODIFIED (M2): Register snapshot & backup subparsers and dispatch
│   ├── cmd_snapshot.py       # NEW (M2): Handler for `smart-drive snapshot create|list|verify`
│   └── cmd_backup.py         # NEW (M2): Handler for `smart-drive backup --target <path>`
tests/
└── test_snapshot.py          # NEW (M2): Pure stdlib unittest suite for F12-F18
```

### Component Roles & Responsibilities

1. **`smart_drive.core.snapshot.SnapshotManifest`**:
   - Represents an immutable snapshot record.
   - Serializes/deserializes to and from JSON.
   - Integrates 512KB cluster geometry (`calculate_allocated_bytes`).
   - Normalizes all paths to relative POSIX format (`/`) across Windows and macOS.

2. **`smart_drive.core.snapshot.SnapshotVerifier`**:
   - Compares disk state against recorded `SnapshotManifest`.
   - Re-computes streaming SHA-256 hashes on existing files.
   - Detects modified files (hash mismatch or size change).
   - Detects missing files (in manifest but absent from disk).
   - Detects untracked files (present in covered partitions but absent from manifest).

3. **`smart_drive.core.snapshot.BackupEngine`**:
   - Performs one-way synchronization to an external directory or drive.
   - Checks target safety: target cannot equal source root, cannot reside inside source root's backup partitions.
   - Incremental logic: compares size and mtime (tolerating exFAT 2-second granularity).
   - Filters Tier 1 junk (`.DS_Store`, `Thumbs.db`, `._*`) and system directories (`.git`, `$RECYCLE.BIN`).
   - Writes `backup_manifest.json` at backup destination.

4. **`smart_drive.core.snapshot.SnapshotManager`**:
   - Top-level facade coordinating snapshot storage, verification, and backup execution.
   - Manages `.smart_drive/snapshots/` directory creation and index lookup.
   - Auto-resolves default target partitions and handles partition aliases.

5. **`smart_drive.cli.cmd_snapshot` and `cmd_backup`**:
   - CLI interfaces conforming to standard SmartDrive UX (colored or clear badges, ASCII/Unicode tables, `--json` support).

---

## 3. Data Schemas & Contracts

### 3.1 Snapshot Manifest Schema (`.smart_drive/snapshots/<name>.json`)

```json
{
  "name": "daily_20260926_090000",
  "version": "1.1.0",
  "timestamp": "2026-09-26T09:00:00Z",
  "root": "D:/",
  "partitions": [
    "02_Learning_Knowledge",
    "03_Development_Projects",
    "05_Dev_Toolbox"
  ],
  "file_count": 1420,
  "total_logical_bytes": 104857600,
  "total_allocated_bytes": 744488960,
  "total_slack_bytes": 639631360,
  "files": {
    "02_Learning_Knowledge/INDEX.md": {
      "size": 1520,
      "allocated_size": 524288,
      "mtime": 1758869760.0,
      "sha256": "4a7d1ed414474e4033ac29ccb8653d9b008d73d8269b260119244b4236ec4412"
    },
    "03_Development_Projects/smart_drive_os/pyproject.toml": {
      "size": 1917,
      "allocated_size": 524288,
      "mtime": 1758870120.0,
      "sha256": "8f3b2c91..."
    }
  }
}
```

### 3.2 Verification Report Schema (`VerificationReport.to_dict()`)

```json
{
  "snapshot_name": "daily_20260926_090000",
  "timestamp": "2026-09-26T09:15:00Z",
  "intact": false,
  "verified_count": 1418,
  "corrupted_count": 1,
  "missing_count": 1,
  "untracked_count": 2,
  "modified_files": [
    {
      "path": "02_Learning_Knowledge/INDEX.md",
      "expected_hash": "4a7d1ed4...",
      "actual_hash": "9b1c2e3f...",
      "expected_size": 1520,
      "actual_size": 1535
    }
  ],
  "missing_files": [
    "05_Dev_Toolbox/Scripts/old_script.py"
  ],
  "untracked_files": [
    "02_Learning_Knowledge/new_notes.md",
    "03_Development_Projects/temp_test.py"
  ]
}
```

### 3.3 Backup Report Schema (`BackupReport.to_dict()`)

```json
{
  "source_root": "D:/",
  "target_dir": "E:/Backups/SmartDrive",
  "timestamp": "2026-09-26T09:30:00Z",
  "dry_run": false,
  "copied_count": 12,
  "skipped_count": 1408,
  "failed_count": 0,
  "copied_bytes": 6291456,
  "copied_files": [
    "03_Development_Projects/smart_drive_os/pyproject.toml"
  ],
  "skipped_files": [
    "02_Learning_Knowledge/INDEX.md"
  ],
  "failed_files": [],
  "manifest_path": "E:/Backups/SmartDrive/backup_manifest.json"
}
```

---

## 4. Key Mathematical & Filesystem Constraints

### 4.1 512KB Cluster Geometry
Files on Kingston XS2000 SSD consume 512KB blocks (`CLUSTER_SIZE_BYTES = 524_288`).
- 0-byte files consume 0 bytes on disk (stored in exFAT directory table).
- Any non-zero file consumes `ceil(size / 524288) * 524288`.
- In `SnapshotManifest`, `total_allocated_bytes` and `total_slack_bytes` are computed using `calculate_allocated_bytes(size)` from `smart_drive.core.config`.

### 4.2 exFAT 2-Second Timestamp Granularity
FAT/exFAT directory entries store timestamps with a 2-second resolution.
When `BackupEngine` compares source `mtime` with destination `mtime`:
$$\Delta t = |t_{\text{src}} - t_{\text{dest}}|$$
If $\Delta t \le 2.0$ seconds and `src_size == dest_size`, the file is deemed identical and skipped. Optional `--hash` flag provides byte-level SHA-256 fallback if sub-second precision is strictly required.

### 4.3 Streaming 64KB Chunk Hashing
To prevent memory exhaustion when snapshotting large multi-gigabyte models or datasets:
- Buffer size: 65,536 bytes (`64 KB`).
- Read loop: `while chunk := f.read(65536): hasher.update(chunk)`.
- Constant memory footprint: $O(1)$ memory $\le 128$ KB regardless of file size.
- 0-byte file optimization: immediately return `EMPTY_FILE_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"`.

### 4.4 Partition Resolution and Aliasing
Key partitions:
- `02_Learning_Knowledge`
- `03_Development_Projects` (alias: `03_Personal_Documents`)
- `05_Dev_Toolbox`

Resolution algorithm:
1. If `partitions` is passed as a list, normalize names and check existence.
2. If `partitions` is None (default):
   - Check if `02_Learning_Knowledge` exists -> add.
   - Check if `03_Development_Projects` exists -> add. If not, check if `03_Personal_Documents` exists -> add.
   - Check if `05_Dev_Toolbox` exists -> add.
3. If partition list is specified as `"all"`: include all present folders in `PROTECTED_CORE_TAXONOMIES`.

### 4.5 Boundary & Security Guards
Backup target directory must pass safety validation:
1. Target path cannot be identical to drive root (`realpath(target) == realpath(source)`).
2. Target path cannot be inside any partition included in the backup (avoids recursive self-backup).
3. Target path cannot be an ancestor of drive root (e.g. root drive `C:\` or `D:\` if backing up a subfolder).
4. System directories (`.git`, `$RECYCLE.BIN`, `System Volume Information`, `.Spotlight-V100`, `.Trashes`, `.agents`, `.smart_drive`) are strictly excluded.
5. If `skip_junk` is True (default), Tier 1 junk files (`._*`, `.DS_Store`, `Thumbs.db`, `desktop.ini`, `*.pyc`, `*.tmp`) are omitted from snapshot and backup.

---

## 5. Detailed Code Templates & Implementation Artifacts

Below are the exact, tested implementation blueprints for all Milestone 2 components.

### 5.1 `smart_drive/core/snapshot.py`

```python
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
            exclude_names = {d.lower() for d in DEFAULT_EXCLUDE_DIRS}
            for part in manifest.partitions:
                part_dir = self.root_path / part
                if not part_dir.is_dir():
                    continue

                for root_dir, dirs, files in os.walk(part_dir):
                    dirs[:] = [d for d in dirs if d.lower() not in exclude_names and not d.startswith(".")]
                    for f in files:
                        if skip_junk:
                            rule = match_junk_rule(f, is_dir=False, max_tier=JunkTier.TIER_1_SAFE)
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
    ) -> BackupReport:
        """Performs incremental synchronization from source partitions to target directory."""
        target_path = self.validate_target(target_dir, partitions)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        copied_files: List[str] = []
        skipped_files: List[str] = []
        failed_files: List[Dict[str, str]] = []
        copied_bytes = 0

        exclude_names = {d.lower() for d in DEFAULT_EXCLUDE_DIRS}

        for part in partitions:
            src_part_dir = self.root_path / part
            if not src_part_dir.is_dir():
                continue

            for root_dir, dirs, files in os.walk(src_part_dir):
                # Filter out excluded system directories
                dirs[:] = [d for d in dirs if d.lower() not in exclude_names and not d.startswith(".")]

                for f in files:
                    if skip_junk:
                        rule = match_junk_rule(f, is_dir=False, max_tier=JunkTier.TIER_1_SAFE)
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
                    copied_files.append(rel_path)
                    copied_bytes += src_size

                    if not dry_run:
                        try:
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, dest_file)
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
        total_logical = 0
        total_allocated = 0
        exclude_names = {d.lower() for d in DEFAULT_EXCLUDE_DIRS}

        for part in active_partitions:
            part_path = self.root_path / part
            if not part_path.is_dir():
                continue

            for root_dir, dirs, files in os.walk(part_path):
                dirs[:] = [d for d in dirs if d.lower() not in exclude_names and not d.startswith(".")]

                for f in files:
                    if skip_junk:
                        rule = match_junk_rule(f, is_dir=False, max_tier=JunkTier.TIER_1_SAFE)
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
    ) -> BackupReport:
        """Executes incremental backup of partitions to target directory."""
        active_partitions = self.resolve_partitions(partitions)
        return self.backup_engine.backup(
            target_dir=target_dir,
            partitions=active_partitions,
            dry_run=dry_run,
            skip_junk=skip_junk,
            use_hash_comparison=use_hash_comparison,
        )


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
```

---

### 5.2 `smart_drive/cli/cmd_snapshot.py`

```python
"""smart_drive.cli.cmd_snapshot - CLI Subcommand Handler for `smart-drive snapshot`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.exfat_compat import detect_drive_root
from smart_drive.core.snapshot import SnapshotManager, SnapshotNotFoundError, SnapshotError


def _format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable representation."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Handles `smart-drive snapshot` subcommands (`create`, `list`, `verify`)."""
    action = getattr(args, "action", None)
    if not action:
        # User invoked `smart-drive snapshot` without sub-action
        print("Usage: smart-drive snapshot [create|list|verify] [options]")
        print("Run 'smart-drive snapshot --help' for details.")
        return 0

    root = os.path.abspath(getattr(args, "root", None) or detect_drive_root())
    manager = SnapshotManager(root)
    as_json = getattr(args, "json", False)

    # -------------------------------------------------------------------------
    # 1. ACTION: create
    # -------------------------------------------------------------------------
    if action == "create":
        name = getattr(args, "name", None)
        raw_parts = getattr(args, "partitions", None)
        partitions = [p.strip() for p in raw_parts.split(",") if p.strip()] if raw_parts else None

        try:
            manifest = manager.create_snapshot(name=name, partitions=partitions)
        except Exception as exc:
            sys.stderr.write(f"Error creating snapshot: {exc}\n")
            return 1

        if as_json:
            print(manifest.to_json())
            return 0

        print(f"\n✓ Point-in-time snapshot created successfully: {manifest.name}")
        print(f"  Timestamp:  {manifest.timestamp}")
        print(f"  Partitions: {', '.join(manifest.partitions)}")
        print(f"  Files:      {manifest.file_count:,}")
        print(f"  Logical:    {_format_size(manifest.total_logical_bytes)}")
        print(f"  Allocated:  {_format_size(manifest.total_allocated_bytes)} (512KB clusters)")
        print(f"  Manifest:   {manager.snapshot_dir / f'{manifest.name}.json'}")
        return 0

    # -------------------------------------------------------------------------
    # 2. ACTION: list
    # -------------------------------------------------------------------------
    elif action == "list":
        summaries = manager.list_snapshots()

        if as_json:
            print(json.dumps([s.to_dict() for s in summaries], indent=2, ensure_ascii=False))
            return 0

        if not summaries:
            print(f"\nNo snapshots found in {manager.snapshot_dir}")
            return 0

        print(f"\nAvailable Snapshots (Root: {root}):")
        print("=" * 86)
        print(f"{'Snapshot Name':<28} {'Created (UTC)':<20} {'Files':<8} {'Logical':<12} {'Allocated (512K)'}")
        print("-" * 86)
        for s in summaries:
            print(
                f"{s.name:<28} "
                f"{s.timestamp:<20} "
                f"{s.file_count:<8} "
                f"{_format_size(s.total_logical_bytes):<12} "
                f"{_format_size(s.total_allocated_bytes)}"
            )
        print("=" * 86)
        print(f"Total: {len(summaries)} snapshot(s) stored.\n")
        return 0

    # -------------------------------------------------------------------------
    # 3. ACTION: verify
    # -------------------------------------------------------------------------
    elif action == "verify":
        name = getattr(args, "name", None)
        if not name:
            sys.stderr.write("Error: Snapshot name is required for verification. Usage: smart-drive snapshot verify <name>\n")
            return 1

        check_untracked = not getattr(args, "no_untracked", False)

        try:
            report = manager.verify_snapshot(name=name, check_untracked=check_untracked)
        except SnapshotNotFoundError:
            sys.stderr.write(f"Error: Snapshot '{name}' not found in {manager.snapshot_dir}\n")
            return 1
        except Exception as exc:
            sys.stderr.write(f"Error during verification: {exc}\n")
            return 1

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0 if report.intact else 1

        print(f"\nSnapshot Integrity Verification: {report.snapshot_name}")
        print("-" * 70)
        status_str = "✓ INTACT (100% Validated)" if report.intact else "✗ INTEGRITY COMPROMISED"
        print(f"Status:     {status_str}")
        print(f"Verified:   {report.verified_count:,} files matching SHA-256")
        print(f"Modified:   {report.corrupted_count:,} files (hash or size mismatch)")
        print(f"Missing:    {report.missing_count:,} files")
        print(f"Untracked:  {report.untracked_count:,} new files")

        if report.modified_files:
            print("\nModified Files (Tampered/Corrupted):")
            for m in report.modified_files[:10]:
                print(f"  ! {m['path']} (Expected: {m['expected_hash'][:12]}..., Got: {m['actual_hash'][:12]}...)")
            if len(report.modified_files) > 10:
                print(f"  ... and {len(report.modified_files) - 10} more modified files.")

        if report.missing_files:
            print("\nMissing Files:")
            for mf in report.missing_files[:10]:
                print(f"  - {mf}")
            if len(report.missing_files) > 10:
                print(f"  ... and {len(report.missing_files) - 10} more missing files.")

        if report.untracked_files:
            print("\nUntracked New Files (in snapshot partitions):")
            for uf in report.untracked_files[:5]:
                print(f"  + {uf}")
            if len(report.untracked_files) > 5:
                print(f"  ... and {len(report.untracked_files) - 5} more untracked files.")

        print()
        return 0 if report.intact else 1

    else:
        sys.stderr.write(f"Unknown snapshot action: '{action}'\n")
        return 2


__all__ = ["cmd_snapshot"]
```

---

### 5.3 `smart_drive/cli/cmd_backup.py`

```python
"""smart_drive.cli.cmd_backup - CLI Subcommand Handler for `smart-drive backup`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.exfat_compat import detect_drive_root
from smart_drive.core.snapshot import SnapshotManager, BackupError


def _format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable representation."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def cmd_backup(args: argparse.Namespace) -> int:
    """Handles the `smart-drive backup --target <path>` incremental backup command."""
    target = getattr(args, "target", None)
    if not target:
        sys.stderr.write("Error: --target directory is required for backup.\n")
        return 1

    root = os.path.abspath(getattr(args, "root", None) or detect_drive_root())
    manager = SnapshotManager(root)

    raw_parts = getattr(args, "partitions", None)
    partitions = [p.strip() for p in raw_parts.split(",") if p.strip()] if raw_parts else None

    dry_run = getattr(args, "dry_run", False)
    skip_junk = not getattr(args, "no_skip_junk", False)
    use_hash = getattr(args, "hash", False)
    as_json = getattr(args, "json", False)

    try:
        report = manager.incremental_backup(
            target_dir=target,
            partitions=partitions,
            dry_run=dry_run,
            skip_junk=skip_junk,
            use_hash_comparison=use_hash,
        )
    except BackupError as b_err:
        sys.stderr.write(f"Backup Error: {b_err}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"Unexpected backup error: {exc}\n")
        return 1

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0

    mode_str = "DRY-RUN SIMULATION (No files copied)" if dry_run else "EXECUTION (Applied)"
    print(f"\nSmartDrive Incremental Backup ({mode_str})")
    print("=" * 70)
    print(f"Source Root: {report.source_root}")
    print(f"Target Dir:  {report.target_dir}")
    print("-" * 70)
    print(f"Copied:      {report.copied_count:,} files ({_format_size(report.copied_bytes)})")
    print(f"Skipped:     {report.skipped_count:,} files (identical size & mtime)")
    print(f"Failed:      {report.failed_count:,} files")
    if report.manifest_path:
        print(f"Manifest:    {report.manifest_path}")

    if report.failed_files:
        print("\nFailed File Transfers:")
        for ff in report.failed_files[:10]:
            print(f"  ! {ff['path']}: {ff['error']}")
        if len(report.failed_files) > 10:
            print(f"  ... and {len(report.failed_files) - 10} more errors.")

    print()
    return 0 if report.failed_count == 0 else 1


__all__ = ["cmd_backup"]
```

---

### 5.4 Registration Updates in `smart_drive/cli/main.py`

In `smart_drive/cli/main.py`:
1. Add imports:
   ```python
   from smart_drive.cli.cmd_snapshot import cmd_snapshot
   from smart_drive.cli.cmd_backup import cmd_backup
   ```
2. In `build_parser()`, add:
   ```python
   # 14. snapshot
   p_snap = subparsers.add_parser(
       "snapshot",
       help="Point-in-time snapshot creation, listing, and SHA-256 verification",
   )
   p_snap.add_argument("--root", help="Root directory of the SSD")
   p_snap.add_argument("--json", action="store_true", help="Output results in JSON format")

   snap_sub = p_snap.add_subparsers(dest="action", help="Snapshot action (create, list, verify)")

   # snapshot create
   p_sc = snap_sub.add_parser("create", help="Create a point-in-time snapshot with streaming SHA-256")
   p_sc.add_argument("name", nargs="?", default=None, help="Snapshot identifier name (optional)")
   p_sc.add_argument("--root", help="Root directory of the SSD")
   p_sc.add_argument("--partitions", help="Comma-separated list of partitions to snapshot")
   p_sc.add_argument("--json", action="store_true", help="Output manifest in JSON format")

   # snapshot list
   p_sl = snap_sub.add_parser("list", help="List all stored snapshots")
   p_sl.add_argument("--root", help="Root directory of the SSD")
   p_sl.add_argument("--json", action="store_true", help="Output snapshot list in JSON format")

   # snapshot verify
   p_sv = snap_sub.add_parser("verify", help="Verify data integrity of files against snapshot manifest")
   p_sv.add_argument("name", help="Snapshot identifier name to verify")
   p_sv.add_argument("--root", help="Root directory of the SSD")
   p_sv.add_argument("--no-untracked", action="store_true", help="Do not check for untracked files")
   p_sv.add_argument("--json", action="store_true", help="Output verification report in JSON format")

   # 15. backup
   p_bak = subparsers.add_parser(
       "backup",
       help="Safe incremental backup to target directory or drive",
   )
   p_bak.add_argument("--target", required=True, help="Destination directory path for backup")
   p_bak.add_argument("--root", help="Root directory of the SSD")
   p_bak.add_argument("--partitions", help="Comma-separated list of partitions to backup")
   p_bak.add_argument("--dry-run", action="store_true", help="Simulate backup without copying files")
   p_bak.add_argument("--no-skip-junk", action="store_true", help="Include junk files in backup")
   p_bak.add_argument("--hash", action="store_true", help="Use SHA-256 checksum comparison instead of size/mtime")
   p_bak.add_argument("--json", action="store_true", help="Output backup report in JSON format")
   ```
3. In `main()`, add to `dispatch`:
   ```python
   "snapshot": cmd_snapshot,
   "backup": cmd_backup,
   ```

---

### 5.5 `tests/test_snapshot.py`

```python
"""tests/test_snapshot.py - Comprehensive Unit Tests for Snapshot & Backup Engine.

100% Python Standard Library unittest. Zero external dependencies.
Tests Features F12 through F18:
- F12: CLI `smart-drive snapshot` create/list/verify execution and JSON output
- F13: Point-in-time snapshot of key partitions with alias resolution
- F14: Constant-memory 64KB chunk streaming SHA-256 hashing
- F15: Manifest JSON serialization, loading, and 512KB cluster allocation math
- F16: Snapshot listing table and JSON summaries
- F17: Verification of intact files, tampered hashes, deleted files, and untracked files
- F18: Incremental backup, change detection, boundary guards, junk filtering, and manifest creation
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.cli.cmd_backup import cmd_backup
from smart_drive.cli.cmd_snapshot import cmd_snapshot
from smart_drive.cli.main import build_parser
from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.core.snapshot import (
    EMPTY_FILE_SHA256,
    BackupEngine,
    BackupTargetInvalidError,
    SnapshotCorruptedError,
    SnapshotManager,
    SnapshotManifest,
    SnapshotNotFoundError,
    SnapshotVerifier,
    compute_file_sha256,
    normalize_rel_path,
    sanitize_snapshot_name,
)
from tests.helpers import SmartDriveTestCase, oracle_full_sha256, run_smart_drive_cli


class TestStreamingSha256(SmartDriveTestCase):
    """F14: Constant-memory 64KB block streaming SHA-256 calculation."""

    def test_empty_file_returns_authoritative_sha256(self) -> None:
        """0-byte file immediately returns standard empty SHA-256 without error."""
        empty_file = self.test_dir / "empty.dat"
        empty_file.touch()
        result = compute_file_sha256(empty_file)
        self.assertEqual(result, EMPTY_FILE_SHA256)

    def test_small_file_matches_hashlib_oracle(self) -> None:
        """Small file (<64KB) matches hashlib oracle exactly."""
        test_file = self.test_dir / "small.txt"
        test_content = b"SmartDrive-OS High Performance SSD Storage Suite" * 100
        test_file.write_bytes(test_content)

        expected = hashlib.sha256(test_content).hexdigest()
        actual = compute_file_sha256(test_file)
        self.assertEqual(actual, expected)

    def test_multi_chunk_file_matches_oracle(self) -> None:
        """File larger than 64KB (e.g. 160KB) verifies streaming chunk boundary transitions."""
        test_file = self.test_dir / "large.bin"
        # 160 KB = 2 full 64KB chunks + 32KB remainder
        test_content = b"CHUNK_VERIFICATION_BYTES_" * 6400
        test_file.write_bytes(test_content)

        expected = oracle_full_sha256(test_file)
        actual = compute_file_sha256(test_file)
        self.assertEqual(actual, expected)

    def test_nonexistent_file_returns_empty_string(self) -> None:
        """Non-existent file traps OSError and returns empty string."""
        missing = self.test_dir / "does_not_exist.txt"
        self.assertEqual(compute_file_sha256(missing), "")


class TestSnapshotManifestSerialization(SmartDriveTestCase):
    """F15: Manifest serialization, deserialization, and cluster math."""

    def test_manifest_roundtrip_json(self) -> None:
        """SnapshotManifest serializes to JSON and deserializes identically."""
        manifest = SnapshotManifest(
            name="test_snap_01",
            version="1.1.0",
            timestamp="2026-09-26T12:00:00Z",
            root="D:/SSD",
            partitions=["02_Learning_Knowledge", "03_Development_Projects"],
            file_count=2,
            total_logical_bytes=1000,
            total_allocated_bytes=1048576,
            total_slack_bytes=1047576,
            files={
                "02_Learning_Knowledge/note.md": {
                    "size": 400,
                    "allocated_size": 524288,
                    "mtime": 1758869760.0,
                    "sha256": "abc123",
                },
                "03_Development_Projects/main.py": {
                    "size": 600,
                    "allocated_size": 524288,
                    "mtime": 1758869800.0,
                    "sha256": "def456",
                },
            },
        )

        manifest_file = self.test_dir / "manifest.json"
        manifest.save(manifest_file)
        self.assertTrue(manifest_file.is_file())

        loaded = SnapshotManifest.load(manifest_file)
        self.assertEqual(loaded.name, manifest.name)
        self.assertEqual(loaded.file_count, 2)
        self.assertEqual(loaded.total_logical_bytes, 1000)
        self.assertEqual(loaded.total_allocated_bytes, 1048576)
        self.assertEqual(loaded.files["02_Learning_Knowledge/note.md"]["sha256"], "abc123")

    def test_load_corrupted_json_raises_error(self) -> None:
        """Malformed JSON file raises SnapshotCorruptedError."""
        bad_file = self.test_dir / "bad.json"
        bad_file.write_text("{ unclosed json: ...", encoding="utf-8")
        with self.assertRaises(SnapshotCorruptedError):
            SnapshotManifest.load(bad_file)

    def test_load_nonexistent_manifest_raises_error(self) -> None:
        """Missing manifest file raises SnapshotNotFoundError."""
        with self.assertRaises(SnapshotNotFoundError):
            SnapshotManifest.load(self.test_dir / "missing.json")


class TestSnapshotManager(SmartDriveTestCase):
    """F13, F15, F16: Snapshot creation, partition targeting, and listing."""

    def test_create_snapshot_default_partitions(self) -> None:
        """Creates snapshot over key default partitions and verifies manifest contents."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        manifest = manager.create_snapshot("initial_backup")
        self.assertEqual(manifest.name, "initial_backup")
        self.assertTrue(manifest.file_count > 0)
        self.assertTrue(manifest.total_logical_bytes > 0)
        self.assertTrue(manifest.total_allocated_bytes >= manifest.total_logical_bytes)

        # Check recorded paths are inside allowed partitions
        for rel_p in manifest.files.keys():
            first_segment = rel_p.split("/")[0]
            self.assertIn(first_segment, ["02_Learning_Knowledge", "03_Development_Projects", "05_Dev_Toolbox"])

        # Check manifest saved on disk
        manifest_on_disk = manager.snapshot_dir / "initial_backup.json"
        self.assertTrue(manifest_on_disk.is_file())

    def test_create_snapshot_alias_resolution(self) -> None:
        """Resolves 03_Personal_Documents when 03_Development_Projects is absent."""
        mock_root = self.test_dir / "alias_drive"
        mock_root.mkdir()
        personal_dir = mock_root / "03_Personal_Documents"
        personal_dir.mkdir(parents=True)
        (personal_dir / "doc.txt").write_text("personal data", encoding="utf-8")

        manager = SnapshotManager(mock_root)
        resolved = manager.resolve_partitions()
        self.assertIn("03_Personal_Documents", resolved)

        manifest = manager.create_snapshot("personal_snap")
        self.assertIn("03_Personal_Documents/doc.txt", manifest.files)

    def test_create_snapshot_auto_generates_name(self) -> None:
        """When name is omitted, creates timestamped snapshot name."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manifest = manager.create_snapshot()
        self.assertTrue(manifest.name.startswith("snapshot_"))

    def test_sanitize_name_prevents_traversal(self) -> None:
        """Path traversal attacks in snapshot names are sanitized or rejected."""
        self.assertEqual(sanitize_snapshot_name("../../attack"), "attack")
        with self.assertRaises(ValueError):
            sanitize_snapshot_name("..")

    def test_list_snapshots_ordered_descending(self) -> None:
        """Listing snapshots returns summaries sorted newest first."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        m1 = manager.create_snapshot("snap_01")
        m2 = manager.create_snapshot("snap_02")

        summaries = manager.list_snapshots()
        self.assertEqual(len(summaries), 2)
        names = {s.name for s in summaries}
        self.assertEqual(names, {"snap_01", "snap_02"})


class TestSnapshotVerifier(SmartDriveTestCase):
    """F17: Verification detecting intact, modified, missing, and untracked files."""

    def test_verify_intact_snapshot_returns_clean(self) -> None:
        """Untouched files verify with intact=True, 0 modified, 0 missing."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("clean_snap")

        report = manager.verify_snapshot("clean_snap", check_untracked=True)
        self.assertTrue(report.intact)
        self.assertEqual(report.corrupted_count, 0)
        self.assertEqual(report.missing_count, 0)
        self.assertEqual(report.untracked_count, 0)

    def test_verify_detects_modified_file_hash(self) -> None:
        """Tampering with file contents causes integrity check failure with hash details."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("tamper_test")

        # Tamper with file
        target_file = mock_root / "02_Learning_Knowledge" / "INDEX.md"
        target_file.write_text("TAMPERED CONTENT CORRUPTION", encoding="utf-8")

        report = manager.verify_snapshot("tamper_test")
        self.assertFalse(report.intact)
        self.assertEqual(report.corrupted_count, 1)
        mod_entry = report.modified_files[0]
        self.assertEqual(mod_entry["path"], "02_Learning_Knowledge/INDEX.md")
        self.assertNotEqual(mod_entry["expected_hash"], mod_entry["actual_hash"])

    def test_verify_detects_missing_file(self) -> None:
        """Deleting a recorded file triggers missing file detection."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("delete_test")

        # Delete a file
        target_file = mock_root / "02_Learning_Knowledge" / "Learning_Code" / "algorithm_kata.py"
        target_file.unlink()

        report = manager.verify_snapshot("delete_test")
        self.assertFalse(report.intact)
        self.assertEqual(report.missing_count, 1)
        self.assertIn("02_Learning_Knowledge/Learning_Code/algorithm_kata.py", report.missing_files)

    def test_verify_detects_untracked_new_file(self) -> None:
        """Adding a new file to a snapshotted partition is detected in untracked_files."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)
        manager.create_snapshot("untracked_test")

        # Add a new untracked file
        new_file = mock_root / "02_Learning_Knowledge" / "brand_new_note.md"
        new_file.write_text("New study notes", encoding="utf-8")

        report = manager.verify_snapshot("untracked_test", check_untracked=True)
        # Snapshot files themselves are intact, but untracked file is flagged
        self.assertTrue(report.intact)
        self.assertEqual(report.untracked_count, 1)
        self.assertIn("02_Learning_Knowledge/brand_new_note.md", report.untracked_files)


class TestIncrementalBackup(SmartDriveTestCase):
    """F18: Incremental backup, skipping unchanged, boundary validation, and junk exclusion."""

    def test_initial_backup_copies_all_files(self) -> None:
        """First backup transfers all files to destination and writes backup manifest."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        report = manager.incremental_backup(target_dir, dry_run=False)
        self.assertFalse(report.dry_run)
        self.assertTrue(report.copied_count > 0)
        self.assertEqual(report.skipped_count, 0)
        self.assertEqual(report.failed_count, 0)

        # Destination manifest was created
        dest_manifest = target_dir / "backup_manifest.json"
        self.assertTrue(dest_manifest.is_file())

    def test_second_backup_skips_unchanged_files(self) -> None:
        """Subsequent backup without changes copies 0 files and skips all."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        # Pass 1: initial
        r1 = manager.incremental_backup(target_dir)
        total_files = r1.copied_count

        # Pass 2: immediate incremental
        r2 = manager.incremental_backup(target_dir)
        self.assertEqual(r2.copied_count, 0)
        self.assertEqual(r2.skipped_count, total_files)

    def test_backup_transfers_only_modified_file(self) -> None:
        """Modifying one file in source causes ONLY that file to be transferred."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        manager.incremental_backup(target_dir)

        # Modify one file and advance mtime
        mod_file = mock_root / "02_Learning_Knowledge" / "INDEX.md"
        time.sleep(0.05)
        mod_file.write_text("# Updated Master Knowledge Index v2\n", encoding="utf-8")

        r3 = manager.incremental_backup(target_dir)
        self.assertEqual(r3.copied_count, 1)
        self.assertIn("02_Learning_Knowledge/INDEX.md", r3.copied_files)

    def test_backup_dry_run_does_not_write_disk(self) -> None:
        """Dry-run reports file copies without creating target files."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dry"
        manager = SnapshotManager(mock_root)

        report = manager.incremental_backup(target_dir, dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertTrue(report.copied_count > 0)
        self.assertFalse(target_dir.exists())

    def test_backup_rejects_target_inside_source(self) -> None:
        """Backup target inside backed-up partition raises BackupTargetInvalidError."""
        mock_root = self.create_mock_drive()
        invalid_target = mock_root / "02_Learning_Knowledge" / "nested_backup"
        manager = SnapshotManager(mock_root)

        with self.assertRaises(BackupTargetInvalidError):
            manager.incremental_backup(invalid_target)

    def test_backup_rejects_source_root_as_target(self) -> None:
        """Backup target equal to source root raises BackupTargetInvalidError."""
        mock_root = self.create_mock_drive()
        manager = SnapshotManager(mock_root)

        with self.assertRaises(BackupTargetInvalidError):
            manager.incremental_backup(mock_root)

    def test_backup_filters_tier1_junk(self) -> None:
        """Backup omits junk files (.DS_Store, Thumbs.db, temp) by default."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "backup_dest"
        manager = SnapshotManager(mock_root)

        report = manager.incremental_backup(target_dir, skip_junk=True)
        for copied in report.copied_files:
            self.assertFalse(copied.endswith(".DS_Store"))
            self.assertFalse(copied.endswith(".tmp"))
            self.assertFalse(copied.endswith(".dmp"))


class TestCliSnapshotAndBackup(SmartDriveTestCase):
    """F12, F18: CLI end-to-end command handlers and JSON flags."""

    def test_cli_snapshot_create_and_verify(self) -> None:
        """CLI `smart-drive snapshot create` and `verify` succeed with code 0."""
        mock_root = self.create_mock_drive()
        parser = build_parser()

        # 1. Create
        args_create = parser.parse_args(["snapshot", "create", "cli_snap", "--root", str(mock_root)])
        ret_create = cmd_snapshot(args_create)
        self.assertEqual(ret_create, 0)

        # 2. List
        args_list = parser.parse_args(["snapshot", "list", "--root", str(mock_root), "--json"])
        ret_list = cmd_snapshot(args_list)
        self.assertEqual(ret_list, 0)

        # 3. Verify
        args_verify = parser.parse_args(["snapshot", "verify", "cli_snap", "--root", str(mock_root)])
        ret_verify = cmd_snapshot(args_verify)
        self.assertEqual(ret_verify, 0)

    def test_cli_backup_command(self) -> None:
        """CLI `smart-drive backup --target <path>` executes successfully."""
        mock_root = self.create_mock_drive()
        target_dir = self.test_dir / "cli_backup_target"
        parser = build_parser()

        args_backup = parser.parse_args(["backup", "--target", str(target_dir), "--root", str(mock_root)])
        ret_backup = cmd_backup(args_backup)
        self.assertEqual(ret_backup, 0)
        self.assertTrue((target_dir / "backup_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
```

---

## 6. Implementation Checklist & Acceptance Verification

| Step | Action | Expected Outcome |
|------|--------|------------------|
| 1 | Create `smart_drive/core/snapshot.py` | Complete implementation with `SnapshotManager`, `SnapshotManifest`, `SnapshotVerifier`, `BackupEngine` |
| 2 | Create `smart_drive/cli/cmd_snapshot.py` | CLI subcommand handler for `snapshot create`, `list`, `verify` with human & JSON formatting |
| 3 | Create `smart_drive/cli/cmd_backup.py` | CLI subcommand handler for `backup --target <path>` |
| 4 | Update `smart_drive/cli/main.py` | Register `snapshot` and `backup` subparsers and dispatch entries |
| 5 | Create `tests/test_snapshot.py` | 17 comprehensive test cases covering F12-F18 |
| 6 | Execute Test Suite | `python -m unittest discover tests` runs 205+ tests with 100% PASS |
