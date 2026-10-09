"""smart_drive.cli.cmd_clean - Detect and safely purge system junk."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def _purge(root: str, junk_items: List[Any], log_path: Optional[str]) -> Tuple[int, List[Dict[str, Any]]]:
    """Deletes the detected junk through the PurgeEngine (all its safety guards apply).

    Returns (deleted_count, one result dict per item). Each result carries the engine's own status:
    DELETED, BLOCKED (the safety guard refused: expected, not an error) or FAILED (the OS refused).
    Shared by the text and JSON output modes so that `--apply` does the same thing whichever way the
    result is printed.
    """
    purge_engine = PurgeEngine(root, dry_run=False)
    deleted_count = 0
    results: List[Dict[str, Any]] = []
    for j in junk_items:
        path = getattr(j, "path", None) or j.get("path") or os.path.join(root, j["rel_path"])
        is_dir = getattr(j, "is_dir", False) or j.get("is_dir", False)
        size = getattr(j, "size", 0) or j.get("size", 0)
        tier_val = getattr(j, "tier", None)
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
    return deleted_count, results


def _tally(results: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """(deleted, blocked by the safety guard, failed) over the results of `_purge`."""
    deleted = sum(1 for r in results if r["deleted"])
    blocked = sum(1 for r in results if r["status"] == "BLOCKED")
    return deleted, blocked, len(results) - deleted - blocked


def cmd_clean(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive clean`."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2
    tier_map = {1: JunkTier.TIER_1_SAFE, 2: JunkTier.TIER_2_DEV_CACHE, 3: JunkTier.TIER_3_SENSITIVE}
    raw_tier = getattr(args, "tier", 1)
    max_tier = tier_map.get(raw_tier, JunkTier.TIER_1_SAFE)

    detector = JunkDetector(root, max_tier=max_tier)
    junk_items = detector.find_junk()

    is_dry_run = not getattr(args, "apply", False)
    log_path = getattr(args, "log", None)

    if getattr(args, "json", False):
        junk_dicts = [j.to_dict() if hasattr(j, "to_dict") else j for j in junk_items]
        data: Dict[str, Any] = {
            "root": root,
            "dry_run": is_dry_run,
            "junk_count": len(junk_items),
            "junk_items": junk_dicts,
            "total_reclaimable_bytes": sum(j["size"] for j in junk_items),
        }
        if not is_dry_run:
            # `--apply --json` used to print dry_run=false and return without deleting anything.
            deleted_count, results = _purge(root, junk_items, log_path)
            _, blocked_count, failed_count = _tally(results)
            data["deleted_count"] = deleted_count
            data["blocked_count"] = blocked_count
            data["failed_count"] = failed_count
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

    deleted_count, results = _purge(root, junk_items, log_path)
    _, blocked_count, failed_count = _tally(results)
    if not blocked_count and not failed_count:
        print(f"✓ Purged {deleted_count} junk files successfully.")
        return 0
    print(f"{'✓' if not failed_count else '✗'} Purged {deleted_count} of {len(results)} junk files.")
    if blocked_count:
        print(f"  {blocked_count} item(s) kept by the safety guard (protected paths).")
    failures = [r for r in results if r["status"] == "FAILED"]
    for r in failures[:10]:
        print(f"  ✗ {r['rel_path']}: {r['reason']}")
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more failures.")
    return 1 if failed_count else 0


__all__ = ["cmd_clean"]
