"""smart_drive.cli.cmd_clean - Detect and safely purge system junk."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import (
    KEPT_DEPENDENCY_TREE,
    KEPT_GIT_TRACKED,
    KEPT_GIT_UNVERIFIED,
    KEPT_NOT_PROJECT_ROOT,
    KEPT_SYSTEM_REFERENCED,
    JunkDetector,
)
from smart_drive.core.purge_engine import (
    QUARANTINE_RETENTION_DAYS,
    PurgeEngine,
    list_quarantine_batches,
    purge_quarantine_batches,
    restore_quarantine_batch,
)
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def _purge(
    root: str,
    junk_items: List[Any],
    log_path: Optional[str],
    quarantine: bool = True,
) -> Tuple[int, List[Dict[str, Any]], Optional[str]]:
    """Deletes or quarantines the detected junk through the PurgeEngine (all its safety guards apply).

    Returns (deleted_count, one result dict per item, batch_id). Each result carries the engine's own status:
    DELETED, QUARANTINED, BLOCKED (the safety guard refused: expected, not an error) or FAILED (the OS refused).
    Shared by the text and JSON output modes so that `--apply` does the same thing whichever way the
    result is printed.
    """
    purge_engine = PurgeEngine(root, dry_run=False, quarantine=quarantine)
    deleted_count = 0
    results: List[Dict[str, Any]] = []
    for j in junk_items:
        path = getattr(j, "path", None) or j.get("path") or os.path.join(root, j["rel_path"])
        is_dir = getattr(j, "is_dir", False) or j.get("is_dir", False)
        size = getattr(j, "size", 0) or j.get("size", 0)
        allocated = getattr(j, "allocated_size", 0) or j.get("allocated_size", 0)
        tier_val = getattr(j, "tier", None) if hasattr(j, "tier") else (j.get("tier") if isinstance(j, dict) else None)
        success, reason = purge_engine.delete_item(
            path, size=size, is_dir=is_dir, tier=tier_val, allocated_size=allocated
        )
        status = purge_engine.audit_records[-1].status if purge_engine.audit_records else ("DELETED" if success else "FAILED")
        rec_size = purge_engine.audit_records[-1].size if purge_engine.audit_records else int(size or 0)
        is_deleted = status == "DELETED"
        if is_deleted:
            deleted_count += 1
        results.append({
            "rel_path": getattr(j, "rel_path", j.get("rel_path", "")),
            "deleted": is_deleted,
            "quarantined": status == "QUARANTINED",
            "size": rec_size,
            "status": status,
            "reason": reason,
        })
    if log_path:
        purge_engine.export_audit_log(log_path)
    return deleted_count, results, purge_engine.batch_id


def _tally(results: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """(deleted, blocked by the safety guard, failed) over the results of `_purge`."""
    deleted = sum(1 for r in results if r.get("status") == "DELETED" or (r.get("deleted") and r.get("status") != "QUARANTINED"))
    quarantined = sum(1 for r in results if r.get("status") == "QUARANTINED" or r.get("quarantined"))
    blocked = sum(1 for r in results if r.get("status") == "BLOCKED")
    return deleted, blocked, len(results) - deleted - quarantined - blocked


def _tally_space(results: List[Dict[str, Any]]) -> Tuple[int, int, int, int]:
    """Returns (deleted_count, freed_bytes, quarantined_count, quarantined_bytes)."""
    deleted_count = 0
    freed_bytes = 0
    quarantined_count = 0
    quarantined_bytes = 0
    for r in results:
        sz = int(r.get("size", 0) or 0)
        if r.get("status") == "QUARANTINED" or r.get("quarantined"):
            quarantined_count += 1
            quarantined_bytes += sz
        elif r.get("status") == "DELETED" or r.get("deleted"):
            deleted_count += 1
            freed_bytes += sz
    return deleted_count, freed_bytes, quarantined_count, quarantined_bytes


# What the entries that look like junk but were kept are called in the text report, by JunkDetector.kept reason
KEPT_LABELS: Dict[str, str] = {
    KEPT_DEPENDENCY_TREE: "inside dependency folders (node_modules, site-packages...): installed packages, not leftovers",
    KEPT_SYSTEM_REFERENCED: "inside folders that Windows or an installed app depends on",
    KEPT_NOT_PROJECT_ROOT: "build/dist/.cache folders with no project file (package.json, pyproject.toml...) beside them",
    KEPT_GIT_TRACKED: "tracked by git",
    KEPT_GIT_UNVERIFIED: "inside a git repository that git could not be asked about",
}


def _kept_report_lines(detector: JunkDetector) -> List[str]:
    """The lines saying what was left alone although it looks like junk, and why (none when nothing was)."""
    kept = detector.get_kept_summary()
    if not kept["total"]:
        return []
    lines = [f"Kept {kept['total']} look-alike item(s) that are not junk:"]
    for reason, label in KEPT_LABELS.items():
        count = kept["by_reason"].get(reason, 0)
        if not count:
            continue
        line = f"  {count:>6}  {label}"
        if reason == KEPT_SYSTEM_REFERENCED and detector.protected_by_system:
            names = [os.path.basename(str(e["path"]).rstrip("/\\")) or str(e["path"]) for e in detector.protected_by_system]
            line += ": " + ", ".join(names[:5]) + (", ..." if len(names) > 5 else "")
        lines.append(line)
    return lines


def _unpack_purge_result(
    res: Any,
) -> Tuple[int, List[Dict[str, Any]], Optional[str]]:
    """Supports both 2-tuple (legacy mocks) and 3-tuple return values from `_purge`."""
    if isinstance(res, tuple) and len(res) == 2:
        return res[0], res[1], None
    return res[0], res[1], res[2]


def cmd_clean(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive clean`."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    as_json = bool(getattr(args, "json", False))

    raw_age = getattr(args, "older_than", None)
    if raw_age is not None and float(raw_age) < 0:
        message = f"--older-than must be 0 or more days (got {float(raw_age):g}). Nothing was changed."
        if as_json:
            print(json.dumps({"status": "error", "error": message}, indent=2, ensure_ascii=False))
        else:
            sys.stderr.write(f"{message}\n")
        return 2

    if getattr(args, "list_quarantine", False):
        batches = list_quarantine_batches(root)
        if as_json:
            print(json.dumps({
                "root": root,
                "total_batches": len(batches),
                "batches": batches,
            }, indent=2, ensure_ascii=False))
            return 0
        if not batches:
            print("No quarantine batches found.")
            return 0
        print(f"Quarantine batches in {root} ({len(batches)}):")
        for b in batches:
            local_time = b.get("local_time", "")
            time_part = f" ({local_time})" if local_time else ""
            print(f"  {b['batch_id']}{time_part} — {b['item_count']} item(s), {b['total_bytes']:,} B")
        return 0

    restore_target = getattr(args, "restore", None)
    if restore_target:
        res = restore_quarantine_batch(root, batch_id=restore_target)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res.get("ok") else 1
        if res.get("error"):
            sys.stderr.write(f"{res['error']}\n")
            return 1
        print(
            f"Restored {res['restored_count']} item(s) from batch {res['batch_id']} "
            f"(skipped: {res['skipped_count']}, failed: {res['failed_count']})."
        )
        for item in res.get("items", []):
            if item.get("status") != "RESTORED":
                print(f"  - {item['original']}: {item['status']} ({item.get('reason', '')})")
        return 0 if res.get("ok") else 1

    if getattr(args, "purge_quarantine", False):
        is_dry = not getattr(args, "apply", False)
        older_than = QUARANTINE_RETENTION_DAYS if raw_age is None else float(raw_age)
        res = purge_quarantine_batches(root, older_than_days=older_than, dry_run=is_dry)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res.get("failed_count", 0) == 0 else 1
        mode_label = "DRY RUN" if is_dry else "APPLIED"
        print(
            f"[{mode_label}] Quarantine purge (older than {older_than:g} day(s)): "
            f"{res['purged_count']} batch(es) {'would be ' if is_dry else ''}removed, {res['kept_count']} kept."
        )
        for b in res.get("batches", []):
            print(f"  - {b['batch_id']} ({b['item_count']} item(s), {b['total_bytes']:,} B): {b['status']}")
        if older_than > 0 and res["kept_count"] > 0:
            print(f"  {res['kept_count']} newer batch(es) kept; --older-than 0 purges every batch.")
        return 0 if res.get("failed_count", 0) == 0 else 1

    tier_map = {1: JunkTier.TIER_1_SAFE, 2: JunkTier.TIER_2_DEV_CACHE, 3: JunkTier.TIER_3_SENSITIVE}
    raw_tier = getattr(args, "tier", 1)
    max_tier = tier_map.get(raw_tier, JunkTier.TIER_1_SAFE)

    detector = JunkDetector(root, max_tier=max_tier)
    junk_items = detector.find_junk()
    kept = detector.get_kept_summary()

    is_dry_run = not getattr(args, "apply", False)
    quarantine = not getattr(args, "no_quarantine", False)
    log_path = getattr(args, "log", None)

    if as_json:
        junk_dicts = [j.to_dict() if hasattr(j, "to_dict") else j for j in junk_items]
        data: Dict[str, Any] = {
            "root": root,
            "dry_run": is_dry_run,
            "quarantine": quarantine,
            "junk_count": len(junk_items),
            "junk_items": junk_dicts,
            "total_reclaimable_bytes": sum(j["size"] for j in junk_items),
            # Entries named like junk that were left alone, by reason, and the folders the system depends on
            "kept": kept,
            "protected_by_system": detector.protected_by_system,
        }
        if not is_dry_run:
            # `--apply --json` used to print dry_run=false and return without deleting anything.
            _, results, batch_id = _unpack_purge_result(
                _purge(root, junk_items, log_path, quarantine=quarantine)
            )
            _, blocked_count, failed_count = _tally(results)
            deleted_count, freed_bytes, quarantined_count, quarantined_bytes = _tally_space(results)
            data["deleted_count"] = deleted_count
            data["freed_bytes"] = freed_bytes
            data["quarantined_count"] = quarantined_count
            data["quarantined_bytes"] = quarantined_bytes
            data["blocked_count"] = blocked_count
            data["failed_count"] = failed_count
            data["batch_id"] = batch_id
            data["results"] = results
            if log_path:
                data["log"] = log_path
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0 if failed_count == 0 else 1
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"\nScanning for system junk in: {root}")
    print(f"Junk Tier: {max_tier.name} | Mode: {'DRY RUN (Simulation)' if is_dry_run else 'APPLY (Purge)'}")
    print("-" * 70)

    if not junk_items:
        print("✓ No system junk files detected. Drive is clean.")
        for line in _kept_report_lines(detector):
            print(line)
        return 0

    total_bytes = sum(j["size"] for j in junk_items)  # every item, not just the 30 listed below
    for j in junk_items[:30]:
        sz = j["size"]
        desc = getattr(j, "description", j.get("description", j["name"]))
        rel = getattr(j, "rel_path", j.get("rel_path", ""))
        print(f"  [{desc}] {rel} ({sz:,} B)")

    if len(junk_items) > 30:
        print(f"  ... and {len(junk_items) - 30} more items.")

    print("-" * 70)
    print(f"Found {len(junk_items)} junk items. Reclaimable space: {total_bytes / (1024*1024):.2f} MB")
    for line in _kept_report_lines(detector):
        print(line)

    if is_dry_run:
        print("\n[DRY RUN] No files were deleted. Run with --apply to execute purge.")
        if quarantine:
            print("Tier 1-2 items are deleted permanently (no quarantine); only Tier 3 items can be restored (--restore).")
        else:
            print("--no-quarantine: every item is deleted permanently.")
        return 0

    _, results, batch_id = _unpack_purge_result(
        _purge(root, junk_items, log_path, quarantine=quarantine)
    )
    _, blocked_count, failed_count = _tally(results)
    deleted_count, freed_bytes, quarantined_count, quarantined_bytes = _tally_space(results)
    status_mark = "✓" if not failed_count else "✗"

    if deleted_count > 0 or quarantined_count == 0:
        if not blocked_count and not failed_count:
            print(f"✓ Deleted {deleted_count} item(s), freed {freed_bytes / (1024*1024):.2f} MB")
        else:
            print(f"{status_mark} Deleted {deleted_count} of {len(results)} item(s), freed {freed_bytes / (1024*1024):.2f} MB")

    if quarantined_count > 0:
        print(
            f"{status_mark} Quarantined {quarantined_count} item(s), {quarantined_bytes / (1024*1024):.2f} MB — "
            "space is freed only after: smart-drive clean --purge-quarantine --older-than 30 --apply"
        )
        if batch_id:
            print(f"  Restore with: smart-drive clean --restore {batch_id}")

    if blocked_count:
        print(f"  {blocked_count} item(s) kept by the safety guard (protected paths).")
    failures = [r for r in results if r["status"] == "FAILED"]
    for r in failures[:10]:
        print(f"  ✗ {r['rel_path']}: {r['reason']}")
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more failures.")
    return 1 if failed_count else 0


__all__ = ["cmd_clean"]
