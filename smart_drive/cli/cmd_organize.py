"""smart_drive.cli.cmd_organize - Autonomous drive auto-zoning and rebalancing."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager


def get_default_db_path(root: str) -> str:
    """Default location for the SQLite search database."""
    new_path = os.path.join(root, ".smart_drive", "index.db")
    if os.path.exists(new_path):
        return new_path
    legacy_path = os.path.join(root, ".smart_drive_manager", "index.db")
    if os.path.exists(legacy_path):
        return legacy_path
    return new_path


def cmd_organize(args: argparse.Namespace) -> int:
    """Handles the `organize` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2
    zoner = AutoZoner(root)

    # 1. Anti-indexing shields
    shields = zoner.ensure_anti_indexing_shields()

    # 2. Safe Tier 1 Clean only if explicitly requested (--clean)
    clean_summary = None
    if getattr(args, "clean", False):
        detector = JunkDetector(root, max_tier=JunkTier.TIER_1_SAFE)
        junk_list = detector.find_junk()
        if junk_list:
            guard = SecurityGuard(drive_root=root)
            engine = PurgeEngine(guard)
            dry_run = not getattr(args, "apply", False)
            res = engine.purge_batch(junk_list, dry_run=dry_run)
            clean_summary = {
                "dry_run": dry_run,
                "purged_count": res.total_succeeded,
                "reclaimed_nominal_bytes": res.nominal_bytes_reclaimed,
                "reclaimed_slack_bytes": res.allocated_bytes_reclaimed,
            }

    # 3. Auto-Zoning Plan
    plan = zoner.generate_plan()

    if getattr(args, "apply", False):
        apply_res = zoner.apply_plan(plan)
        # Update search index if DB exists
        db_path = os.path.abspath(get_default_db_path(root))
        inc_res = None
        if os.path.exists(db_path):
            try:
                db = DatabaseManager(db_path)
                try:
                    mgr = IndexManager(db, root)
                    inc = mgr.incremental_update()
                    inc_res = dataclasses.asdict(inc)
                finally:
                    db.close()
            except Exception:
                pass

        if getattr(args, "json", False):
            print(json.dumps({
                "status": "applied",
                "clean": clean_summary,
                "zoning": apply_res,
                "index_update": inc_res,
                "anti_indexing_shields": shields,
            }, indent=2, ensure_ascii=False))
            return 0

        print("✓ Auto-Zoning applied successfully:")
        print(f"  - Moved items: {apply_res['moved_count']}")
        print(f"  - Total bytes relocated: {apply_res['total_bytes_moved']:,} bytes")
        print(f"  - Slack rebalanced: {apply_res['total_slack_rebalanced']:,} bytes")
        if clean_summary:
            print(f"  - Safe Tier 1 cleaned: {clean_summary['purged_count']} files")
        if inc_res:
            print(f"  - Index updated: +{inc_res['added']} ~{inc_res['modified']} -{inc_res['deleted']}")
        return 0

    # Dry-run mode
    if getattr(args, "json", False):
        print(json.dumps({
            "status": "dry_run",
            "clean_preview": clean_summary,
            "planned_actions": [a.to_dict() for a in plan],
            "anti_indexing_shields": shields,
            "note": "Use --apply to execute relocation."
        }, indent=2, ensure_ascii=False))
        return 0

    print("=== Auto-Zoning Plan (Dry-Run Preview) ===")
    print(f"Root: {root}")
    if plan:
        for action in plan:
            item_type = "[DIR]" if action.is_dir else "[FILE]"
            print(f"  -> {item_type} {os.path.basename(action.src_path)}")
            print(f"     Taxonomy: {action.target_taxonomy} ({action.reason})")
            print(f"     Destination: {action.dest_path}")
        print(f"\nTotal planned relocations: {len(plan)} items.")
        print("Run with --apply to execute the relocation safely.")
    else:
        print("  ✓ No loose or unorganized items detected. SSD taxonomy is already well-organized!")
    return 0
