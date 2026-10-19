"""smart_drive.cli.cmd_clean - Detect and safely purge system junk."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import (
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
    DELETED, BLOCKED (the safety guard refused: expected, not an error) or FAILED (the OS refused).
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
        tier_val = getattr(j, "tier", None) if hasattr(j, "tier") else (j.get("tier") if isinstance(j, dict) else None)
        success, reason = purge_engine.delete_item(path, size=size, is_dir=is_dir, tier=tier_val)
        if success:
            deleted_count += 1
        status = purge_engine.audit_records[-1].status if purge_engine.audit_records else ("DELETED" if success else "FAILED")
        results.append({
            "rel_path": getattr(j, "rel_path", j.get("rel_path", "")),
            "deleted": bool(success),
            "status": status,
            "reason": reason,
        })
    if log_path:
        purge_engine.export_audit_log(log_path)
    return deleted_count, results, purge_engine.batch_id


def _tally(results: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """(deleted, blocked by the safety guard, failed) over the results of `_purge`."""
    deleted = sum(1 for r in results if r["deleted"])
    blocked = sum(1 for r in results if r["status"] == "BLOCKED")
    return deleted, blocked, len(results) - deleted - blocked


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
            print(f"  {b['batch_id']} — {b['item_count']} item(s), {b['total_bytes']:,} B")
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
        older_than = float(getattr(args, "older_than", 0.0) or 0.0)
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
        return 0 if res.get("failed_count", 0) == 0 else 1

    tier_map = {1: JunkTier.TIER_1_SAFE, 2: JunkTier.TIER_2_DEV_CACHE, 3: JunkTier.TIER_3_SENSITIVE}
    raw_tier = getattr(args, "tier", 1)
    max_tier = tier_map.get(raw_tier, JunkTier.TIER_1_SAFE)

    detector = JunkDetector(root, max_tier=max_tier)
    junk_items = detector.find_junk()

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
        }
        if not is_dry_run:
            # `--apply --json` used to print dry_run=false and return without deleting anything.
            deleted_count, results, batch_id = _unpack_purge_result(
                _purge(root, junk_items, log_path, quarantine=quarantine)
            )
            _, blocked_count, failed_count = _tally(results)
            data["deleted_count"] = deleted_count
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

    if is_dry_run:
        print("\n[DRY RUN] No files were deleted. Run with --apply to execute purge.")
        return 0

    deleted_count, results, batch_id = _unpack_purge_result(
        _purge(root, junk_items, log_path, quarantine=quarantine)
    )
    _, blocked_count, failed_count = _tally(results)
    if not blocked_count and not failed_count:
        print(f"✓ Purged {deleted_count} junk files successfully.")
        if quarantine and batch_id:
            print(f"  Quarantined in .smart_drive/quarantine/{batch_id} (restore with: smart-drive clean --restore {batch_id})")
        return 0
    print(f"{'✓' if not failed_count else '✗'} Purged {deleted_count} of {len(results)} junk files.")
    if quarantine and batch_id:
        print(f"  Quarantined in .smart_drive/quarantine/{batch_id}")
    if blocked_count:
        print(f"  {blocked_count} item(s) kept by the safety guard (protected paths).")
    failures = [r for r in results if r["status"] == "FAILED"]
    for r in failures[:10]:
        print(f"  ✗ {r['rel_path']}: {r['reason']}")
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more failures.")
    return 1 if failed_count else 0


__all__ = ["cmd_clean"]
