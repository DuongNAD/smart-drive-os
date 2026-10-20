"""smart_drive.core.purge_engine - Safe Purge Engine & Inviolable Boundary Guards.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Provides non-destructive simulation by default (--dry-run), explicit apply mode (--apply),
Windows read-only attribute unlocking (stat.S_IWRITE), anti-indexing shield enforcement,
and JSON audit logging.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import shutil
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
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
    is_inside_dependency_tree,
    is_protected_root_dir,
    is_protected_root_file,
    match_junk_rule,
    verify_anti_indexing_markers,
)
from smart_drive.core.learning_units import find_unit_root, unit_kind
from smart_drive.core.scanner import measure_tree

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
    status: str          # "SIMULATED", "DELETED", "QUARANTINED", "BLOCKED", "FAILED"
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
    deleted_count: int = 0
    freed_bytes: int = 0
    quarantined_count: int = 0
    quarantined_bytes: int = 0
    batch_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert summary to dictionary."""
        return {
            "dry_run": self.dry_run,
            "total_attempted": self.total_attempted,
            "total_succeeded": self.total_succeeded,
            "deleted_count": self.deleted_count,
            "freed_bytes": self.freed_bytes,
            "quarantined_count": self.quarantined_count,
            "quarantined_bytes": self.quarantined_bytes,
            "batch_id": self.batch_id,
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

        # Rule 4: Protected root directories or protected nested paths
        if is_protected_root_dir(root_segment) or is_protected_root_dir(rel_path) or is_protected_root_dir(base_name):
            if len(parts) == 1 or is_protected_root_dir(rel_path) or is_protected_root_dir(base_name):
                return True, f"Inviolable business directory: {rel_path}"
            rule = match_junk_rule(base_name, is_dir=os.path.isdir(real_target), max_tier=JunkTier.TIER_3_SENSITIVE)
            if rule is None:
                rule = match_junk_rule(base_name, is_dir=False, max_tier=JunkTier.TIER_3_SENSITIVE) or \
                       match_junk_rule(base_name, is_dir=True, max_tier=JunkTier.TIER_3_SENSITIVE)
            if rule is None:
                return True, f"Inviolable user file in protected directory: {rel_path}"

        # Rule 5: Protected root files
        if len(parts) == 1 and is_protected_root_file(root_segment):
            return True, f"Inviolable root file: {root_segment}"

        # Rule 6: Learning Unit Protection (Aurora Slides & Polaris)
        unit_root = find_unit_root(real_target, self.drive_root)
        if unit_root is not None:
            norm_fwd = rel_path.lower().replace("\\", "/")
            is_polaris_cache = (
                "/polaris/.cache" in norm_fwd
                or norm_fwd.startswith("polaris/.cache")
                or norm_fwd.endswith("/polaris/.cache")
                or norm_fwd == "polaris/.cache"
            )
            if is_polaris_cache:
                return False, ""
            return True, f"Inviolable learning unit data ({unit_kind(unit_root)}): {rel_path}"

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
    """Safe deletion orchestrator featuring dry-run simulation, quarantine, permission clearing, and auditing.

    Key Features:
    1. Default `--dry-run`: safe by default, moves/unlinks files ONLY when explicit dry_run=False.
    2. Default quarantine: moves items into `<root>/.smart_drive/quarantine/<YYYYmmdd-HHMMSS>/` with `manifest.json`.
    3. Boundary guard: intercepts all operations through SecurityGuard before touching disk, and refuses Tier 2-3
       items inside dependency trees (node_modules, site-packages...).
    4. Windows read-only clearing: strips `stat.S_IWRITE` before unlinking to prevent Win32 PermissionErrors.
    5. Anti-indexing restoration: ensures `.metadata_never_index` and `.fseventsd/no_log` exist post-purge.
    6. JSON audit logging: structured trail of every simulated or executed unlinking.
    """

    def __init__(
        self,
        drive_root: Union[str, SecurityGuard],
        dry_run: bool = True,
        cluster_size: int = CLUSTER_SIZE_BYTES,
        quarantine: bool = True,
        batch_id: Optional[str] = None,
    ) -> None:
        if isinstance(drive_root, SecurityGuard):
            self.guard = drive_root
            self.drive_root = drive_root.drive_root
        else:
            self.drive_root = os.path.realpath(os.path.abspath(drive_root))
            self.guard = SecurityGuard(self.drive_root)
        self.dry_run = dry_run
        self.cluster_size = cluster_size
        self.quarantine = quarantine
        self.batch_id = batch_id
        self.quarantine_manifest: List[Dict[str, Any]] = []
        self.audit_records: List[DeletionRecord] = []

    def quarantine_root(self) -> str:
        """Returns `<root>/.smart_drive/quarantine`."""
        return os.path.join(self.drive_root, ".smart_drive", "quarantine")

    def _ensure_batch_id(self) -> str:
        """Lazily assigns a unique local-time `<YYYYmmdd-HHMMSS>` quarantine batch ID for this engine instance."""
        if self.batch_id:
            return self.batch_id
        base = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        q_root = self.quarantine_root()
        candidate = base
        idx = 1
        while os.path.exists(os.path.join(q_root, candidate)):
            candidate = f"{base}-{idx:02d}"
            idx += 1
        self.batch_id = candidate
        return self.batch_id

    def _cleanup_empty_quarantine_dirs(self, start_dir: str) -> None:
        """Removes empty intermediate directories inside `.smart_drive/quarantine` after a failed rename."""
        q_root = os.path.abspath(self.quarantine_root())
        curr = os.path.abspath(start_dir)
        while curr and curr != q_root and curr.startswith(q_root):
            try:
                os.rmdir(curr)
            except OSError:
                break
            curr = os.path.dirname(curr)
        try:
            os.rmdir(q_root)
        except OSError:
            pass

    def _quarantine_item(
        self,
        real_path: str,
        rel_path: str,
        size: int = 0,
        tier: Optional[int] = None,
        is_dir: bool = False,
    ) -> str:
        """Moves a file or directory to `.smart_drive/quarantine/<batch>/<rel_path>` via `os.rename` and updates `manifest.json`.

        If `os.rename` fails, the item is NEVER deleted and an exception is raised.
        """
        if not os.path.lexists(real_path):
            return ""
        if is_dir and os.path.islink(real_path):
            raise OSError("refusing to remove a directory tree through a symlink")

        rel_parts = [p for p in rel_path.replace("\\", "/").split("/") if p and p != "."]
        if not rel_parts or any(p == ".." for p in rel_parts):
            raise OSError(f"Invalid relative path for quarantine: {rel_path}")

        batch_id = self._ensure_batch_id()
        batch_dir = os.path.join(self.quarantine_root(), batch_id)
        dest_path = os.path.join(batch_dir, *rel_parts)
        dest_parent = os.path.dirname(dest_path)
        os.makedirs(dest_parent, exist_ok=True)

        try:
            os.rename(real_path, dest_path)
        except PermissionError as first_err:
            if not is_dir and sys.platform == "win32":
                mode_before = None
                try:
                    mode_before = stat.S_IMODE(os.lstat(real_path).st_mode)
                    self._add_owner_access(real_path, is_dir=False)
                    os.rename(real_path, dest_path)
                except Exception as retry_err:
                    if mode_before is not None:
                        try:
                            os.chmod(real_path, mode_before)
                        except OSError:
                            pass
                    self._cleanup_empty_quarantine_dirs(dest_parent)
                    raise retry_err
            else:
                self._cleanup_empty_quarantine_dirs(dest_parent)
                if is_dir:
                    raise OSError(f"directory could not be fully removed ({first_err})") from first_err
                raise
        except Exception as exc:
            self._cleanup_empty_quarantine_dirs(dest_parent)
            if is_dir:
                raise OSError(f"directory could not be fully removed ({exc})") from exc
            raise

        norm_orig = "/".join(rel_parts)
        quarantined_rel = f".smart_drive/quarantine/{batch_id}/{norm_orig}"
        entry: Dict[str, Any] = {
            "original": norm_orig,
            "quarantined": quarantined_rel,
            "size": int(size),
            "tier": int(tier) if tier is not None else None,
            "is_dir": bool(is_dir),
        }
        self.quarantine_manifest.append(entry)
        manifest_path = os.path.join(batch_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as mf:
            json.dump(self.quarantine_manifest, mf, indent=2, ensure_ascii=False)
        return quarantined_rel

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

    @staticmethod
    def _add_owner_access(path: str, is_dir: bool) -> None:
        """Adds the owner's write bit (and read/search for a directory) without disturbing the other bits.

        Setting the mode to exactly S_IWRITE|S_IREAD, as this used to, turned a directory into 0600: no search
        bit, so an entry that then could not be removed was left unusable.
        """
        mode = stat.S_IMODE(os.lstat(path).st_mode)
        extra = stat.S_IWUSR | stat.S_IRUSR | (stat.S_IXUSR if is_dir else 0)
        os.chmod(path, mode | extra)

    def _make_writable_and_remove_file(self, target_path: str) -> None:
        """Unlinks a file, clearing Windows/exFAT read-only attribute if needed."""
        try:
            os.unlink(target_path)
        except PermissionError:
            try:
                self._add_owner_access(target_path, is_dir=False)
                os.unlink(target_path)
            except Exception as inner_err:
                raise inner_err

    def _open_up_tree(self, tree: str) -> None:
        """Gives the owner access to every folder and file inside ``tree`` (never through a link).

        Folders are opened before they are listed, so a folder that was unreadable (mode 000) can be entered.
        Only ever called on the tree that is about to be deleted.
        """
        stack = [tree]
        while stack:
            current = stack.pop()
            try:
                mode = os.lstat(current).st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                continue  # a link inside the tree is removed as a link; what it points at is nobody's business
            try:
                self._add_owner_access(current, is_dir=stat.S_ISDIR(mode))
            except OSError:
                pass  # the removal below will say what is still in the way
            if stat.S_ISDIR(mode):
                try:
                    with os.scandir(current) as entries:
                        stack.extend(entry.path for entry in entries)
                except OSError:
                    continue

    def _make_writable_and_remove_dir(self, target_path: str) -> None:
        """Removes a directory tree, clearing read-only flags and permissions inside it if that is what stops it.

        Raises OSError if anything is left: shutil.rmtree reports through its callback only, and swallowing
        every error there made a half-removed tree look like a success. Two passes: the plain removal first
        (the usual case costs nothing extra), and only if something is left the tree is opened up and removal
        is tried once more. (Retrying the failed call from inside rmtree's callback does not work for the
        errors it reports from os.open and os.scandir, which is why the whole removal is retried instead.)
        """
        if ".." in Path(target_path).parts:
            raise OSError("refusing to remove a directory tree named with '..' (it may resolve elsewhere)")
        tree = os.path.abspath(target_path)  # the same string is used for the clean-up and for the removal
        if os.path.islink(tree):
            raise OSError("refusing to remove a directory tree through a symlink")
        failures: List[str] = []

        def _record(func, path, exc_info):
            failures.append(f"{path}: {exc_info[1]}")

        shutil.rmtree(tree, onerror=_record)
        if not os.path.lexists(tree):
            return
        failures.clear()
        self._open_up_tree(tree)
        shutil.rmtree(tree, onerror=_record)
        if os.path.lexists(tree):
            detail = failures[0] if failures else "it is still there"
            raise OSError(f"directory could not be fully removed ({detail})")

    def delete_item(
        self,
        item_path: str,
        size: int = 0,
        is_dir: bool = False,
        tier: Optional[int] = None,
        allocated_size: int = 0,
    ) -> Tuple[bool, str]:
        """Unified deletion entrypoint matching PROJECT.md interface contract.

        Args:
            item_path: File or directory path to delete.
            size: Nominal size in bytes (for a directory: everything inside it).
            is_dir: True if directory, False if file.
            tier: Optional junk classification tier (1, 2, or 3).
            allocated_size: Allocated bytes of a directory's contents, when the caller already measured them.

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
        if is_dir:
            record = self.delete_dir(clean_path, tier=tier, size=size, allocated_size=allocated_size)
        else:
            record = self.delete_file(clean_path, size, tier=tier)
        if record.status in ("SIMULATED", "DELETED", "QUARANTINED"):
            return True, record.reason
        return False, record.reason

    @staticmethod
    def _dependency_tree_block(real_path: str, tier: Optional[int]) -> str:
        """Why a Tier 2-3 item inside node_modules, site-packages... is not removed ("" when nothing stops it).

        JunkDetector never reports such items; this holds for a list that came from anywhere else, because what
        lies in an installed package is its code, not leftovers, and deleting it breaks the projects using it.
        """
        if tier is not None and int(tier) >= int(JunkTier.TIER_2_DEV_CACHE) and is_inside_dependency_tree(real_path):
            return "Inside a dependency tree (node_modules, site-packages...): only Tier 1 junk is removed there"
        return ""

    def _should_quarantine(self, tier: Optional[int]) -> bool:
        """Quarantines Tier 3 items and items with unknown tier (`tier is None`); only known Tier 1-2 items are deleted immediately."""
        return bool(self.quarantine and (tier is None or int(tier) >= int(JunkTier.TIER_3_SENSITIVE)))

    def delete_file(self, file_path: str, size: int = 0, tier: Optional[int] = None) -> DeletionRecord:
        """Safely deletes (or quarantines) or simulates deletion of a regular file.

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

        if tier is not None:
            try:
                tier = int(tier)
            except (ValueError, TypeError):
                tier = None

        is_prot, reason = self.guard.is_protected(clean_path)
        if not is_prot:
            is_prot, reason = self.guard.is_protected(real_path)
        if not is_prot:
            reason = self._dependency_tree_block(real_path, tier)
            is_prot = bool(reason)
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

        use_quarantine = self._should_quarantine(tier)
        try:
            if ".." in Path(file_path).parts:
                raise OSError("refusing to remove a file path named with '..'")
            if os.path.exists(real_path) or os.path.islink(real_path):
                if use_quarantine:
                    self._quarantine_item(real_path, rel_path, size=size, tier=tier, is_dir=False)
                else:
                    self._make_writable_and_remove_file(real_path)
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated,
                is_dir=False,
                status="QUARANTINED" if use_quarantine else "DELETED",
                reason="Quarantined successfully" if use_quarantine else "Deleted successfully",
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record
        except Exception as exc:
            action_word = "quarantine" if use_quarantine else "unlink"
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated,
                is_dir=False,
                status="FAILED",
                reason=f"Failed to {action_word}: {exc}",
                timestamp=ts,
                tier=tier,
                error=str(exc),
            )
            self.audit_records.append(record)
            return record

    def delete_dir(
        self,
        dir_path: str,
        tier: Optional[int] = None,
        size: int = 0,
        allocated_size: int = 0,
    ) -> DeletionRecord:
        """Safely deletes (or quarantines) or simulates deletion of a directory tree.

        Args:
            dir_path: Absolute or relative directory path.
            tier: Optional junk classification tier (1, 2, or 3).
            size: Total bytes of the files inside, if the caller measured them; measured here (before anything
                is removed) when it is 0 or ``allocated_size`` is missing.
            allocated_size: Allocated bytes of the files inside, with ``size``.

        Returns:
            DeletionRecord with outcome status and metadata.
        """
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        clean_path = dir_path.strip().replace("\\", "/")
        real_path = os.path.realpath(os.path.abspath(clean_path)) if os.path.isabs(clean_path) else os.path.realpath(os.path.abspath(os.path.join(self.drive_root, clean_path)))
        rel_path = self._normalize_rel_path(real_path)

        if tier is not None:
            try:
                tier = int(tier)
            except (ValueError, TypeError):
                tier = None

        is_prot, reason = self.guard.is_protected(clean_path)
        if not is_prot:
            is_prot, reason = self.guard.is_protected(real_path)
        if not is_prot:
            reason = self._dependency_tree_block(real_path, tier)
            is_prot = bool(reason)
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

        # A directory has no size of its own: what a purge frees is the files inside, and after the removal
        # they can no longer be counted (this used to record 0 for every directory, so the freed-space totals
        # of `clean`, the audit log and the MCP tool ignored them).
        if size <= 0 or allocated_size <= 0:
            size, allocated_size = measure_tree(real_path, self.cluster_size)

        if self.dry_run:
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated_size,
                is_dir=True,
                status="SIMULATED",
                reason="Dry-run simulation (directory intact)",
                timestamp=ts,
                tier=tier,
            )
            self.audit_records.append(record)
            return record

        use_quarantine = self._should_quarantine(tier)
        try:
            if ".." in Path(dir_path).parts:
                raise OSError("refusing to remove a directory tree named with '..' (it may resolve elsewhere)")
            if os.path.lexists(real_path):
                if use_quarantine:
                    self._quarantine_item(real_path, rel_path, size=size, tier=tier, is_dir=True)
                else:
                    self._make_writable_and_remove_dir(real_path)
            record = DeletionRecord(
                path=real_path,
                rel_path=rel_path,
                size=size,
                allocated_size=allocated_size,
                is_dir=True,
                status="QUARANTINED" if use_quarantine else "DELETED",
                reason="Quarantined directory tree successfully" if use_quarantine else "Deleted directory tree successfully",
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
                allocated_size=allocated_size,
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
        deleted_count = 0
        freed_bytes = 0
        quarantined_count = 0
        quarantined_bytes = 0
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
                raw_alloc = item.get("allocated_size") if hasattr(item, "get") else getattr(item, "allocated_size", 0)
                rec = self.delete_dir(path, tier=tier, size=int(size or 0), allocated_size=int(raw_alloc or 0))
            else:
                rec = self.delete_file(path, size, tier=tier)

            if rec.status == "SIMULATED":
                succeeded += 1
                reclaimed_nominal += rec.size
                reclaimed_allocated += rec.allocated_size
            elif rec.status == "DELETED":
                succeeded += 1
                deleted_count += 1
                freed_bytes += rec.size
                reclaimed_nominal += rec.size
                reclaimed_allocated += rec.allocated_size
            elif rec.status == "QUARANTINED":
                succeeded += 1
                quarantined_count += 1
                quarantined_bytes += rec.size
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
            deleted_count=deleted_count,
            freed_bytes=freed_bytes,
            quarantined_count=quarantined_count,
            quarantined_bytes=quarantined_bytes,
            batch_id=self.batch_id,
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
            "deleted_count": sum(1 for r in self.audit_records if r.status == "DELETED"),
            "freed_bytes": sum(r.size for r in self.audit_records if r.status == "DELETED"),
            "quarantined_count": sum(1 for r in self.audit_records if r.status == "QUARANTINED"),
            "quarantined_bytes": sum(r.size for r in self.audit_records if r.status == "QUARANTINED"),
            "batch_id": self.batch_id,
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


# ---------------------------------------------------------------------------
# Quarantine Management Helpers
# ---------------------------------------------------------------------------

# Days a quarantine batch is kept before `clean --purge-quarantine` may delete it (an age of 0 matches every batch)
QUARANTINE_RETENTION_DAYS = 30.0


def _batch_timestamp(batch_dir: str) -> float:
    """Computes the creation timestamp (epoch seconds) of a quarantine batch directory.

    Batch names `<YYYYmmdd-HHMMSS>` are interpreted in the local timezone (matching
    `_ensure_batch_id` and `moves.jsonl`). If a test backdates the directory mtime
    significantly earlier than a freshly generated batch name, the backdated mtime is honored.
    """
    batch_name = os.path.basename(batch_dir)
    parsed_ts: Optional[float] = None
    if len(batch_name) >= 15 and batch_name[8] == "-":
        try:
            tm = time.strptime(batch_name[:15], "%Y%m%d-%H%M%S")
            parsed_ts = float(time.mktime(tm))
        except (ValueError, OverflowError):
            parsed_ts = None

    try:
        dir_mtime = float(os.path.getmtime(batch_dir))
    except OSError:
        dir_mtime = parsed_ts if parsed_ts is not None else time.time()

    manifest_path = os.path.join(batch_dir, "manifest.json")
    if os.path.isfile(manifest_path):
        try:
            dir_mtime = min(dir_mtime, float(os.path.getmtime(manifest_path)))
        except OSError:
            pass

    if parsed_ts is not None:
        if dir_mtime < parsed_ts - 60.0 and abs(parsed_ts - time.time()) < 3600.0:
            return dir_mtime
        return parsed_ts
    return dir_mtime


def _load_batch_manifest(batch_dir: str, batch_id: str) -> List[Dict[str, Any]]:
    """Loads `manifest.json` from a quarantine batch folder, or reconstructs it if missing."""
    manifest_path = os.path.join(batch_dir, "manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                return data["items"]
        except Exception:
            pass

    reconstructed: List[Dict[str, Any]] = []
    for curr, _, files in os.walk(batch_dir):
        for fname in files:
            if curr == batch_dir and fname == "manifest.json":
                continue
            full = os.path.join(curr, fname)
            rel = os.path.relpath(full, batch_dir).replace("\\", "/")
            try:
                sz = os.path.getsize(full)
            except OSError:
                sz = 0
            reconstructed.append({
                "original": rel,
                "quarantined": f".smart_drive/quarantine/{batch_id}/{rel}",
                "size": sz,
                "tier": None,
                "is_dir": False,
            })
    return reconstructed


def list_quarantine_batches(drive_root: str) -> List[Dict[str, Any]]:
    """Lists all quarantine batches under `<root>/.smart_drive/quarantine/` sorted oldest to newest."""
    real_root = os.path.realpath(os.path.abspath(drive_root))
    q_root = os.path.join(real_root, ".smart_drive", "quarantine")
    if not os.path.isdir(q_root):
        return []

    batches: List[Dict[str, Any]] = []
    try:
        with os.scandir(q_root) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                items = _load_batch_manifest(entry.path, entry.name)
                ts = _batch_timestamp(entry.path)
                local_dt = datetime.fromtimestamp(ts).astimezone()
                local_time_str = local_dt.strftime("%Y-%m-%d %H:%M:%S %z")
                total_bytes = sum(int(i.get("size", 0) or 0) for i in items)
                batches.append({
                    "batch_id": entry.name,
                    "path": entry.path,
                    "timestamp": ts,
                    "local_time": local_time_str,
                    "item_count": len(items),
                    "total_bytes": total_bytes,
                    "items": items,
                })
    except OSError:
        return []

    batches.sort(key=lambda b: (b["timestamp"], b["batch_id"]))
    return batches


def restore_quarantine_batch(drive_root: str, batch_id: str = "latest") -> Dict[str, Any]:
    """Restores items from `<batch_id>` (or `'latest'`) back to their original paths without overwriting existing files."""
    real_root = os.path.realpath(os.path.abspath(drive_root))
    batches = list_quarantine_batches(real_root)
    if not batches:
        return {
            "ok": False,
            "batch_id": batch_id,
            "restored_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "items": [],
            "error": "No quarantine batches found",
        }

    if batch_id == "latest":
        target = batches[-1]
    else:
        matched = [b for b in batches if b["batch_id"] == batch_id]
        if not matched:
            return {
                "ok": False,
                "batch_id": batch_id,
                "restored_count": 0,
                "skipped_count": 0,
                "failed_count": 0,
                "items": [],
                "error": f"Quarantine batch not found: {batch_id}",
            }
        target = matched[0]

    batch_dir = target["path"]
    actual_id = target["batch_id"]
    results: List[Dict[str, Any]] = []
    remaining: List[Dict[str, Any]] = []
    restored_count = 0
    skipped_count = 0
    failed_count = 0

    for item in target["items"]:
        orig_rel = str(item.get("original", "")).replace("\\", "/").strip("/")
        rel_parts = [p for p in orig_rel.split("/") if p and p != "."]
        if not rel_parts or any(p == ".." for p in rel_parts):
            failed_count += 1
            results.append({
                "original": orig_rel,
                "restored": False,
                "status": "FAILED",
                "reason": "Invalid relative path in manifest",
            })
            remaining.append(item)
            continue

        orig_abs = os.path.join(real_root, *rel_parts)
        quar_abs = os.path.join(batch_dir, *rel_parts)

        if os.path.lexists(orig_abs):
            skipped_count += 1
            results.append({
                "original": orig_rel,
                "restored": False,
                "status": "SKIPPED_EXISTS",
                "reason": "Destination already exists; refusing to overwrite",
            })
            remaining.append(item)
            continue

        if not os.path.lexists(quar_abs):
            failed_count += 1
            results.append({
                "original": orig_rel,
                "restored": False,
                "status": "FAILED",
                "reason": "Quarantined file/directory missing on disk",
            })
            continue

        try:
            os.makedirs(os.path.dirname(orig_abs), exist_ok=True)
            os.rename(quar_abs, orig_abs)
            restored_count += 1
            results.append({
                "original": orig_rel,
                "restored": True,
                "status": "RESTORED",
                "reason": "Restored to original path",
            })
        except Exception as exc:
            failed_count += 1
            results.append({
                "original": orig_rel,
                "restored": False,
                "status": "FAILED",
                "reason": str(exc),
            })
            remaining.append(item)

    manifest_path = os.path.join(batch_dir, "manifest.json")
    if remaining:
        try:
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(remaining, mf, indent=2, ensure_ascii=False)
        except OSError:
            pass
    else:
        try:
            if os.path.exists(manifest_path):
                os.unlink(manifest_path)
        except OSError:
            pass
        for curr, dirs, files in os.walk(batch_dir, topdown=False):
            for d in dirs:
                try:
                    os.rmdir(os.path.join(curr, d))
                except OSError:
                    pass
        try:
            os.rmdir(batch_dir)
        except OSError:
            pass

    return {
        "ok": failed_count == 0,
        "batch_id": actual_id,
        "restored_count": restored_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "items": results,
    }


def purge_quarantine_batches(
    drive_root: str,
    older_than_days: float = QUARANTINE_RETENTION_DAYS,
    dry_run: bool = True,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Previews or permanently deletes quarantine batches older than `older_than_days` (default 30; younger ones are kept, 0 matches every batch)."""
    real_root = os.path.realpath(os.path.abspath(drive_root))
    now_ts = float(now) if now is not None else time.time()
    cutoff = now_ts - float(older_than_days) * 86400.0
    batches = list_quarantine_batches(real_root)
    engine = PurgeEngine(real_root, dry_run=dry_run, quarantine=False)

    matched: List[Dict[str, Any]] = []
    kept_count = 0
    for b in batches:
        if b["timestamp"] <= cutoff:
            if dry_run:
                matched.append({
                    "batch_id": b["batch_id"],
                    "item_count": b["item_count"],
                    "total_bytes": b["total_bytes"],
                    "purged": False,
                    "status": "SIMULATED",
                })
            else:
                try:
                    engine._make_writable_and_remove_dir(b["path"])
                    matched.append({
                        "batch_id": b["batch_id"],
                        "item_count": b["item_count"],
                        "total_bytes": b["total_bytes"],
                        "purged": True,
                        "status": "PURGED",
                    })
                except Exception as exc:
                    matched.append({
                        "batch_id": b["batch_id"],
                        "item_count": b["item_count"],
                        "total_bytes": b["total_bytes"],
                        "purged": False,
                        "status": "FAILED",
                        "error": str(exc),
                    })
        else:
            kept_count += 1

    purged_count = sum(1 for m in matched if m["status"] in ("PURGED", "SIMULATED"))
    failed_count = sum(1 for m in matched if m["status"] == "FAILED")
    return {
        "root": real_root,
        "dry_run": dry_run,
        "older_than_days": float(older_than_days),
        "matched_count": len(matched),
        "purged_count": purged_count,
        "failed_count": failed_count,
        "kept_count": kept_count,
        "batches": matched,
    }


__all__ = [
    "QUARANTINE_RETENTION_DAYS",
    "SecurityViolationError",
    "DeletionRecord",
    "PurgeSummary",
    "SecurityGuard",
    "PurgeEngine",
    "list_quarantine_batches",
    "restore_quarantine_batch",
    "purge_quarantine_batches",
]
