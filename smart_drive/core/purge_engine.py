"""smart_drive.core.purge_engine - Safe Purge Engine & Inviolable Boundary Guards.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Provides non-destructive simulation by default (--dry-run), explicit apply mode (--apply),
Windows read-only attribute unlocking (stat.S_IWRITE), anti-indexing shield enforcement,
and JSON audit logging.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from smart_drive.core.config import (
    ANTI_INDEXING_FSEVENT_DIR,
    ANTI_INDEXING_FSEVENT_FILE,
    ANTI_INDEXING_ROOT_FILE,
    CLUSTER_SIZE_BYTES,
    PROTECTED_CORE_TAXONOMIES,
    PROTECTED_ROOT_DIRS,
    PROTECTED_ROOT_FILES,
    JunkTier,
    calculate_allocated_bytes,
    ensure_anti_indexing_markers,
    is_protected_root_dir,
    is_protected_root_file,
    match_junk_rule,
    verify_anti_indexing_markers,
)

logger = logging.getLogger("smart_drive.core.purge_engine")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SecurityViolationError(PermissionError):
    """Raised when an operation attempts to delete an inviolable protected file or directory."""
    pass


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class DeletionRecord:
    """Audit log entry recording an individual purge operation attempt."""
    path: str
    rel_path: str
    size: int
    allocated_size: int
    is_dir: bool
    status: str          # "SIMULATED", "DELETED", "BLOCKED", "FAILED"
    reason: str          # Descriptive rationale (e.g. "AppleDouble junk", "Inviolable file")
    timestamp: str       # ISO-8601 UTC timestamp
    tier: Optional[int] = None
    error: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        """Enables dictionary-style subscripting for backward compatibility."""
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(f"DeletionRecord has no attribute '{key}'")

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to plain dictionary."""
        return asdict(self)


@dataclass
class PurgeSummary:
    """Aggregated results of a batch purge session."""
    dry_run: bool
    total_attempted: int
    total_succeeded: int
    total_blocked: int
    total_failed: int
    nominal_bytes_reclaimed: int
    allocated_bytes_reclaimed: int
    timestamp_start: str
    timestamp_end: str
    records: List[DeletionRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert summary to dictionary."""
        return {
            "dry_run": self.dry_run,
            "total_attempted": self.total_attempted,
            "total_succeeded": self.total_succeeded,
            "total_blocked": self.total_blocked,
            "total_failed": self.total_failed,
            "nominal_bytes_reclaimed": self.nominal_bytes_reclaimed,
            "allocated_bytes_reclaimed": self.allocated_bytes_reclaimed,
            "timestamp_start": self.timestamp_start,
            "timestamp_end": self.timestamp_end,
            "records": [r.to_dict() for r in self.records],
        }


# ---------------------------------------------------------------------------
# Security Guard
# ---------------------------------------------------------------------------

class SecurityGuard:
    """Enforces absolute boundaries to prevent data destruction on SSD volumes.

    Inviolable Protection Matrix:
    1. Drive root itself can NEVER be unlinked or removed.
    2. Path canonicalization prevents directory traversal attacks (e.g. '../').
    3. Essential root scripts and operational documentation (GEMINI.md, README.md,
       check_ssd_status.*, sync_repos.*, setup_*, clean_mac_junk.*) are immutable.
    4. Business taxonomy directories (01_AI_Models .. 06_Archives_Storage)
       and workspaces (smart_ssd_workspace, teamwork_projects) cannot be wiped.
    5. Anti-indexing markers (.metadata_never_index, .fseventsd/no_log) are protected.
    """

    def __init__(self, drive_root: str) -> None:
        self.drive_root = os.path.realpath(os.path.abspath(drive_root))

    def _normalize_rel_path(self, target_path: str) -> Tuple[bool, str]:
        """Canonicalizes path and computes relative path from drive root.

        Handles relative paths, cross-platform slashes, and case-insensitivity.

        Returns:
            Tuple of (is_inside_root, rel_path).
        """
        clean_target = target_path.strip().replace("\\", "/")
        if not os.path.isabs(clean_target):
            real_target = os.path.realpath(os.path.abspath(os.path.join(self.drive_root, clean_target)))
        else:
            real_target = os.path.realpath(os.path.abspath(clean_target))

        if real_target == self.drive_root or real_target.lower() == self.drive_root.lower():
            return True, ""
        try:
            rel = os.path.relpath(real_target, self.drive_root)
            if rel == ".":
                return True, ""
            if rel.startswith("..") or os.path.isabs(rel):
                try:
                    rel_cf = os.path.relpath(real_target.lower(), self.drive_root.lower())
                    if rel_cf == ".":
                        return True, ""
                    if not (rel_cf.startswith("..") or os.path.isabs(rel_cf)):
                        return True, rel_cf.replace("\\", "/")
                except Exception:
                    pass
                return False, rel
            return True, rel.replace("\\", "/")
        except ValueError:
            return False, target_path

    def is_protected(self, target_path: str) -> Tuple[bool, str]:
        """Validates whether a target path is guarded against deletion.

        Args:
            target_path: File or directory path to check.

        Returns:
            Tuple[bool, str]: (is_protected, reason_string).
        """
        clean_target = target_path.strip().replace("\\", "/")
        if clean_target.rstrip("/") in ("", "."):
            return True, "Cannot delete drive root"

        target_path_obj = Path(clean_target)
        if any(p.lower() == ".git" for p in target_path_obj.parts):
            return True, "Git repository contents are inviolable"

        if not os.path.isabs(clean_target):
            real_target = os.path.realpath(os.path.abspath(os.path.join(self.drive_root, clean_target)))
        else:
            real_target = os.path.realpath(os.path.abspath(clean_target))

        real_target_obj = Path(real_target)
        if any(p.lower() == ".git" for p in real_target_obj.parts):
            return True, "Git repository contents are inviolable"

        # Rule 1: Drive root cannot be deleted
        if real_target == self.drive_root or real_target.lower() == self.drive_root.lower():
            return True, "Cannot delete drive root"

        # Rule 2: Target must reside strictly inside drive root
        is_inside, rel_path = self._normalize_rel_path(real_target)
        if not is_inside:
            return True, "Path escapes drive root boundary"

        parts = [p for p in rel_path.split("/") if p and p != "."]
        if not parts:
            return True, "Cannot delete drive root"

        if any(p.lower() == ".git" for p in parts):
            return True, "Git repository contents are inviolable"

        root_segment = parts[0]
        base_name = os.path.basename(real_target)

        # Rule 3: Anti-indexing markers anywhere on the drive
        if base_name.lower() in {ANTI_INDEXING_ROOT_FILE.lower(), "no_log"}:
            return True, f"Anti-indexing marker protected: {base_name}"
        if len(parts) >= 2 and parts[0].lower() == ANTI_INDEXING_FSEVENT_DIR.lower() and parts[1].lower() == "no_log":
            return True, "Anti-indexing marker protected: .fseventsd/no_log"

        # Rule 4: Protected root directories
        if is_protected_root_dir(root_segment):
            if len(parts) == 1:
                return True, f"Inviolable business directory: {root_segment}"
            rule = match_junk_rule(base_name, is_dir=os.path.isdir(real_target), max_tier=JunkTier.TIER_3_SENSITIVE)
            if rule is None:
                rule = match_junk_rule(base_name, is_dir=False, max_tier=JunkTier.TIER_3_SENSITIVE) or \
                       match_junk_rule(base_name, is_dir=True, max_tier=JunkTier.TIER_3_SENSITIVE)
            if rule is None:
                return True, f"Inviolable user file in protected directory: {rel_path}"

        # Rule 5: Protected root files
        if len(parts) == 1 and is_protected_root_file(root_segment):
            return True, f"Inviolable root file: {root_segment}"

        return False, ""

    def validate_deletion(self, target_path: str) -> None:
        """Validates that a path is safe to delete, raising SecurityViolationError if blocked."""
        is_prot, reason = self.is_protected(target_path)
        if is_prot:
            raise SecurityViolationError(f"Security Guard Blocked: {reason} ({target_path})")


# ---------------------------------------------------------------------------
# Safe Purge Engine
# ---------------------------------------------------------------------------

class PurgeEngine:
    """Safe deletion orchestrator featuring dry-run simulation, permission clearing, and auditing.

    Key Features:
    1. Default `--dry-run`: safe by default, unlinks files ONLY when explicit dry_run=False.
    2. Boundary guard: intercepts all operations through SecurityGuard before touching disk.
    3. Windows read-only clearing: strips `stat.S_IWRITE` before unlinking to prevent Win32 PermissionErrors.
    4. Anti-indexing restoration: ensures `.metadata_never_index` and `.fseventsd/no_log` exist post-purge.
    5. JSON audit logging: structured trail of every simulated or executed unlinking.
    """

    def __init__(
        self,
        drive_root: Union[str, SecurityGuard],
        dry_run: bool = True,
        cluster_size: int = CLUSTER_SIZE_BYTES,
    ) -> None:
        if isinstance(drive_root, SecurityGuard):
            self.guard = drive_root
            self.drive_root = drive_root.drive_root
        else:
            self.drive_root = os.path.realpath(os.path.abspath(drive_root))
            self.guard = SecurityGuard(self.drive_root)
        self.dry_run = dry_run
        self.cluster_size = cluster_size
        self.audit_records: List[DeletionRecord] = []

    def _normalize_rel_path(self, target_path: str) -> str:
        """Computes relative path from drive root using '/'."""
        clean_target = target_path.strip().replace("\\", "/")
        if not os.path.isabs(clean_target):
            real_target = os.path.realpath(os.path.abspath(os.path.join(self.drive_root, clean_target)))
        else:
            real_target = os.path.realpath(os.path.abspath(clean_target))
        try:
            rel = os.path.relpath(real_target, self.drive_root)
            if rel == ".":
                return ""
            return rel.replace("\\", "/")
        except ValueError:
            return clean_target

    def _make_writable_and_remove_file(self, target_path: str) -> None:
        """Unlinks a file, clearing Windows/exFAT read-only attribute if needed."""
        try:
            os.unlink(target_path)
        except PermissionError:
            try:
                os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)
                os.unlink(target_path)
            except Exception as inner_err:
                raise inner_err

    def _make_writable_and_remove_dir(self, target_path: str) -> None:
        """Removes a directory tree, clearing read-only flags on children if needed."""
        def _on_rm_error(func, path, exc_info):
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                func(path)
            except Exception:
                pass

        shutil.rmtree(target_path, onerror=_on_rm_error)

    def delete_item(
        self,
        item_path: str,
        size: int = 0,
        is_dir: bool = False,
        tier: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Unified deletion entrypoint matching PROJECT.md interface contract.

        Args:
            item_path: File or directory path to delete.
            size: Nominal size in bytes.
            is_dir: True if directory, False if file.
            tier: Optional junk classification tier (1, 2, or 3).

        Returns:
            Tuple[bool, str]: (success, status_or_reason_message).
        """
        clean_path = item_path.strip().replace("\\", "/")
        if not is_dir:
            if clean_path.rstrip("/") in ("", "."):
                is_dir = True
            elif clean_path.endswith("/"):
                is_dir = True
            else:
                real_p = os.path.realpath(os.path.abspath(clean_path)) if os.path.isabs(clean_path) else os.path.realpath(os.path.abspath(os.path.join(self.drive_root, clean_path)))
                if os.path.isdir(real_p):
                    is_dir = True
        record = self.delete_dir(clean_path, tier=tier) if is_dir else self.delete_file(clean_path, size, tier=tier)
        if record.status in ("SIMULATED", "DELETED"):
            return True, record.reason
        return False, record.reason

    def delete_file(self, file_path: str, size: int = 0, tier: Optional[int] = None) -> DeletionRecord:
        """Safely deletes or simulates deletion of a regular file.

        Args:
            file_path: Absolute or relative file path.
            size: Nominal file size in bytes.
            tier: Optional junk classification tier (1, 2, or 3).

        Returns:
            DeletionRecord with outcome status and metadata.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        clean_path = file_path.strip().replace("\\", "/")
        real_path = os.path.realpath(os.path.abspath(clean_path)) if os.path.isabs(clean_path) else os.path.realpath(os.path.abspath(os.path.join(self.drive_root, clean_path)))
        rel_path = self._normalize_rel_path(real_path)

        if size == 0 and os.path.exists(real_path) and not os.path.islink(real_path):
            try:
                size = os.path.getsize(real_path)
            except OSError:
                size = 0
        allocated = calculate_allocated_bytes(size, self.cluster_size)

        base_name = os.path.basename(real_path)
        if tier is None:
            rule = match_junk_rule(base_name, is_dir=False, max_tier=JunkTier.TIER_3_SENSITIVE)
            if rule is not None:
                tier = int(rule.tier)

        is_prot, reason = self.guard.is_protected(clean_path)
        if not is_prot:
            is_prot, reason = self.guard.is_protected(real_path)
        if is_prot:
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated,
                is_dir=False,
                status="BLOCKED",
                reason=reason,
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record

        if self.dry_run:
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated,
                is_dir=False,
                status="SIMULATED",
                reason="Dry-run simulation (file intact)",
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record

        try:
            if os.path.exists(real_path) or os.path.islink(real_path):
                self._make_writable_and_remove_file(real_path)
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated,
                is_dir=False,
                status="DELETED",
                reason="Purged successfully",
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record
        except Exception as exc:
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated,
                is_dir=False,
                status="FAILED",
                reason=f"Failed to unlink: {exc}",
                timestamp=ts,
                tier=tier,
                error=str(exc),
            )
            self.audit_records.append(record)
            return record

    def delete_dir(self, dir_path: str, tier: Optional[int] = None) -> DeletionRecord:
        """Safely deletes or simulates deletion of a directory tree.

        Args:
            dir_path: Absolute or relative directory path.
            tier: Optional junk classification tier (1, 2, or 3).

        Returns:
            DeletionRecord with outcome status and metadata.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        clean_path = dir_path.strip().replace("\\", "/")
        real_path = os.path.realpath(os.path.abspath(clean_path)) if os.path.isabs(clean_path) else os.path.realpath(os.path.abspath(os.path.join(self.drive_root, clean_path)))
        rel_path = self._normalize_rel_path(real_path)

        base_name = os.path.basename(real_path)
        if tier is None:
            rule = match_junk_rule(base_name, is_dir=True, max_tier=JunkTier.TIER_3_SENSITIVE)
            if rule is not None:
                tier = int(rule.tier)

        is_prot, reason = self.guard.is_protected(clean_path)
        if not is_prot:
            is_prot, reason = self.guard.is_protected(real_path)
        if is_prot:
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=0,
                allocated_size=0,
                is_dir=True,
                status="BLOCKED",
                reason=reason,
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record

        if self.dry_run:
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=0,
                allocated_size=0,
                is_dir=True,
                status="SIMULATED",
                reason="Dry-run simulation (directory intact)",
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record

        try:
            if os.path.exists(real_path):
                self._make_writable_and_remove_dir(real_path)
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=0,
                allocated_size=0,
                is_dir=True,
                status="DELETED",
                reason="Purged directory tree successfully",
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record
        except Exception as exc:
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=0,
                allocated_size=0,
                is_dir=True,
                status="FAILED",
                reason=f"Failed to remove directory: {exc}",
                timestamp=ts,
                tier=tier,
                error=str(exc),
            )
            self.audit_records.append(record)
            return record

    def purge_items(
        self,
        items: Sequence[Union[Any, Dict[str, Any]]],
    ) -> PurgeSummary:
        """Executes purge (or simulation) across a sequence of JunkItem or dict entries."""
        ts_start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        succeeded = 0
        blocked = 0
        failed = 0
        reclaimed_nominal = 0
        reclaimed_allocated = 0
        records_start_idx = len(self.audit_records)

        for item in items:
            path = item["path"] if isinstance(item, dict) or hasattr(item, "__getitem__") else getattr(item, "path")
            size = item["size"] if isinstance(item, dict) or hasattr(item, "__getitem__") else getattr(item, "size", 0)
            is_dir = item["is_dir"] if isinstance(item, dict) or hasattr(item, "__getitem__") else getattr(item, "is_dir", False)
            raw_tier = item.get("tier") if hasattr(item, "get") else getattr(item, "tier", None)
            tier = None
            if raw_tier is not None:
                try:
                    tier = int(raw_tier)
                except (ValueError, TypeError):
                    tier = None

            if is_dir:
                rec = self.delete_dir(path, tier=tier)
            else:
                rec = self.delete_file(path, size, tier=tier)

            if rec.status in ("SIMULATED", "DELETED"):
                succeeded += 1
                reclaimed_nominal += rec.size
                reclaimed_allocated += rec.allocated_size
            elif rec.status == "BLOCKED":
                blocked += 1
            else:
                failed += 1

        if not self.dry_run:
            self.ensure_anti_indexing()

        ts_end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        return PurgeSummary(
            dry_run=self.dry_run,
            total_attempted=len(items),
            total_succeeded=succeeded,
            total_blocked=blocked,
            total_failed=failed,
            nominal_bytes_reclaimed=reclaimed_nominal,
            allocated_bytes_reclaimed=reclaimed_allocated,
            timestamp_start=ts_start,
            timestamp_end=ts_end,
            records=self.audit_records[records_start_idx:],
        )

    def purge_batch(
        self,
        items: Sequence[Union[Any, Dict[str, Any]]],
        dry_run: Optional[bool] = None,
    ) -> PurgeSummary:
        """Alias for purge_items with optional dry_run override."""
        if dry_run is not None:
            self.dry_run = dry_run
        return self.purge_items(items)

    def execute_purge(
        self,
        plan: Sequence[Union[Any, Dict[str, Any]]],
        dry_run: Optional[bool] = None,
    ) -> PurgeSummary:
        """Interface contract method matching PROJECT.md."""
        return self.purge_batch(plan, dry_run=dry_run)

    def ensure_anti_indexing(self) -> List[str]:
        """Verifies and restores .metadata_never_index and .fseventsd/no_log at drive root."""
        return ensure_anti_indexing_markers(self.drive_root)

    def export_audit_log(self, filepath: Optional[str] = None) -> str:
        """Serializes current audit records to a structured JSON file."""
        if filepath is None:
            ts_str = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            filepath = os.path.join(self.drive_root, f"clean_audit_{ts_str}.json")

        out_path = Path(filepath).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        summary_dict = {
            "drive_root": self.drive_root,
            "dry_run": self.dry_run,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_records": len(self.audit_records),
            "total_reclaimed_nominal": sum(
                r.size for r in self.audit_records if r.status in ("DELETED", "SIMULATED")
            ),
            "total_reclaimed_allocated": sum(
                r.allocated_size for r in self.audit_records if r.status in ("DELETED", "SIMULATED")
            ),
            "records": [r.to_dict() for r in self.audit_records],
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary_dict, f, indent=2, ensure_ascii=False)

        return str(out_path)


__all__ = [
    "SecurityViolationError",
    "DeletionRecord",
    "PurgeSummary",
    "SecurityGuard",
    "PurgeEngine",
]
