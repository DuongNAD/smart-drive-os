"""smart_drive.cli.cmd_clean - Detect and safely purge system junk."""

from __future__ import annotations

import argparse
import json
import os
import sys

from smart_drive.core.config import JunkTier
from smart_drive.core.exfat_compat import detect_drive_root
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine


def cmd_clean(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive clean`."""
    root = os.path.abspath(getattr(args, "root", None) or detect_drive_root())
    tier_map = {1: JunkTier.TIER_1_SAFE, 2: JunkTier.TIER_2_DEV_CACHE, 3: JunkTier.TIER_3_SENSITIVE}
    raw_tier = getattr(args, "tier", 1)
    max_tier = tier_map.get(raw_tier, JunkTier.TIER_1_SAFE)

    detector = JunkDetector(root, max_tier=max_tier)
    junk_items = detector.find_junk()

    is_dry_run = not getattr(args, "apply", False)

    if getattr(args, "json", False):
        junk_dicts = [j.to_dict() if hasattr(j, "to_dict") else j for j in junk_items]
        data = {
            "root": root,
            "dry_run": is_dry_run,
            "junk_count": len(junk_items),
            "junk_items": junk_dicts,
            "total_reclaimable_bytes": sum(j["size"] for j in junk_items),
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"\nScanning for system junk in: {root}")
    print(f"Junk Tier: {max_tier.name} | Mode: {'DRY RUN (Simulation)' if is_dry_run else 'APPLY (Purge)'}")
    print("-" * 70)

    if not junk_items:
        print("✓ No system junk files detected. Drive is clean.")
        return 0

    total_bytes = 0
    for j in junk_items[:30]:
        sz = j["size"]
        total_bytes += sz
        desc = getattr(j, "description", j.get("description", j["name"]))
        rel = getattr(j, "rel_path", j.get("rel_path", ""))
        print(f"  [{desc}] {rel} ({sz:,} B)")

    if len(junk_items) > 30:
        print(f"  ... and {len(junk_items) - 30} more items.")

    print("-" * 70)
    print(f"Found {len(junk_items)} junk items. Reclaimable space: {total_bytes / (1024*1024):.2f} MB")

    if is_dry_run:
        print("\n[DRY RUN] No files were deleted. Run with --apply to execute purge.")
    else:
        purge_engine = PurgeEngine(root, dry_run=False)
        deleted_count = 0
        for j in junk_items:
            path = getattr(j, "path", None) or j.get("path") or os.path.join(root, j["rel_path"])
            is_dir = getattr(j, "is_dir", False) or j.get("is_dir", False)
            size = getattr(j, "size", 0) or j.get("size", 0)
            tier_val = getattr(j, "tier", None)
            success, _ = purge_engine.delete_item(path, size=size, is_dir=is_dir, tier=tier_val)
            if success:
                deleted_count += 1
        log_path = getattr(args, "log", None)
        if log_path:
            purge_engine.export_audit_log(log_path)
        print(f"✓ Purged {deleted_count} junk files successfully.")

    return 0


__all__ = ["cmd_clean"]
