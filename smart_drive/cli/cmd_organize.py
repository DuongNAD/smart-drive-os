"""smart_drive.cli.cmd_organize - Autonomous drive auto-zoning and rebalancing."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

from smart_drive.cli.cmd_relink import print_relink
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard
from smart_drive.core.relink import after_moves, relink, running_agents
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


def _move_key(src: str, dest: str) -> tuple:
    return (os.path.normcase(os.path.abspath(src)), os.path.normcase(os.path.abspath(dest)))


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
    only_moves = getattr(args, "only_moves", None)
    if only_moves is not None:
        # Callers that showed the user a plan (Smart_Organize) apply exactly that plan: items that
        # appeared, or whose destination changed, since the preview are left where they are.
        confirmed = {_move_key(src, dest) for src, dest in only_moves}
        plan = [a for a in plan if _move_key(a.src_path, a.dest_path) in confirmed]

    if getattr(args, "apply", False):
        apply_res = zoner.apply_plan(plan)
        moves = [(m["src"], m["dest"]) for m in apply_res["moved"]]
        relinked = after_moves(root, moves, "organize")
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

        busy_apps = relinked.busy_apps if relinked else sorted(running_agents())
        if getattr(args, "json", False):
            print(json.dumps({
                "status": "applied",
                "cluster_size": zoner.cluster_size,
                "clean": clean_summary,
                "zoning": apply_res,
                "protected_by_system": zoner.protected_by_system,
                "busy_apps": busy_apps,
                "relink": relinked.to_dict() if relinked else None,
                "index_update": inc_res,
                "anti_indexing_shields": shields,
            }, indent=2, ensure_ascii=False))
            return 0

        print("✓ Auto-Zoning applied successfully:")
        print(f"  - Moved items: {apply_res['moved_count']}")
        print(f"  - Total bytes relocated: {apply_res['total_bytes_moved']:,} bytes")
        print(f"  - Slack rebalanced: {apply_res['total_slack_rebalanced']:,} bytes")
        for repo in apply_res.get("fsmonitor_stopped", []):
            print(f"  - Stopped the git fsmonitor daemon in {repo} (it would have kept its folder from being moved)")
        if clean_summary:
            print(f"  - Safe Tier 1 cleaned: {clean_summary['purged_count']} files")
        if inc_res:
            print(f"  - Index updated: +{inc_res['added']} ~{inc_res['modified']} -{inc_res['deleted']}")
        for item in zoner.protected_by_system:
            p = item["path"]
            print(f"  Skipped {p} — used by {zoner.describe_protected_by_system(p)}")
        for err_entry in apply_res.get("errors", []):
            print(f"  ✗ Could not move {os.path.basename(err_entry['src'])}: {err_entry['error']}")
        if relinked and (relinked.changed or relinked.warnings or relinked.errors):
            print("Coding agents updated:")
            print_relink(relinked)
        return 0

    # Dry-run mode
    preview = relink([(a.src_path, a.dest_path) for a in plan], root=root, dry_run=True) if plan else None
    busy_apps = preview.busy_apps if preview else sorted(running_agents())
    if getattr(args, "json", False):
        print(json.dumps({
            "status": "dry_run",
            "cluster_size": zoner.cluster_size,
            "clean_preview": clean_summary,
            "planned_actions": [a.to_dict() for a in plan],
            "protected_by_system": zoner.protected_by_system,
            "busy_apps": busy_apps,
            "relink_preview": preview.to_dict() if preview else None,
            "anti_indexing_shields": shields,
            "note": "Use --apply to execute relocation."
        }, indent=2, ensure_ascii=False))
        return 0

    print("=== Auto-Zoning Plan (Dry-Run Preview) ===")
    print(f"Root: {root}")
    for item in zoner.protected_by_system:
        p = item["path"]
        print(f"  Skipped {p} — used by {zoner.describe_protected_by_system(p)}")
    if plan:
        for action in plan:
            item_type = "[DIR]" if action.is_dir else "[FILE]"
            print(f"  -> {item_type} {os.path.basename(action.src_path)}")
            print(f"     Taxonomy: {action.target_taxonomy} ({action.reason})")
            print(f"     Destination: {action.dest_path}")
        print(f"\nTotal planned relocations: {len(plan)} items.")
        if preview and (preview.changed or preview.warnings or preview.errors or preview.busy_apps):
            print("Coding agents that will follow these moves:")
            print_relink(preview)
        print("Run with --apply to execute the relocation safely.")
    else:
        print("  ✓ No loose or unorganized items detected. SSD taxonomy is already well-organized!")
    return 0
