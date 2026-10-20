"""smart_drive.cli.cmd_undo - Reverse the latest un-undone organize/classify move batch."""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any, Dict, List, Tuple

from smart_drive.cli.cmd_organize import get_default_db_path
from smart_drive.cli.cmd_relink import print_relink
from smart_drive.core.auto_zoner import _move
from smart_drive.core.relink import after_moves, last_undoable_moves, record_moves, relink, running_agents
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager


def cmd_undo(args: argparse.Namespace) -> int:
    """Handles the `undo` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    apply_mode = bool(getattr(args, "apply", False)) and not bool(getattr(args, "dry_run", False))
    as_json = bool(getattr(args, "json", False))

    batch = last_undoable_moves(root)
    if batch is None:
        if as_json:
            print(json.dumps({
                "status": "nothing_to_undo",
                "dry_run": not apply_mode,
                "root": root,
                "batch": None,
                "moves": [],
                "restored_count": 0,
                "conflicts": [],
                "conflict_count": 0,
                "busy_apps": sorted(running_agents()),
            }, indent=2, ensure_ascii=False))
            return 0
        print("No organize or classify batch left to undo.")
        return 0

    batch_time = batch.get("time", "")
    batch_tool = batch.get("tool", "unknown")
    raw_moves: List[Tuple[str, str]] = list(batch.get("moves") or [])

    valid_pairs: List[Tuple[str, str]] = []
    conflicts: List[Dict[str, str]] = []

    for old_path, new_path in raw_moves:
        if not os.path.lexists(new_path):
            conflicts.append({
                "src": new_path,
                "dest": old_path,
                "reason": "Moved item no longer exists at destination",
            })
        elif os.path.lexists(old_path):
            conflicts.append({
                "src": new_path,
                "dest": old_path,
                "reason": "Original location already exists",
            })
        else:
            valid_pairs.append((new_path, old_path))

    if not apply_mode:
        preview = relink(valid_pairs, root=root, dry_run=True) if valid_pairs else None
        busy_apps = preview.busy_apps if preview else sorted(running_agents())
        if as_json:
            print(json.dumps({
                "status": "dry_run",
                "dry_run": True,
                "root": root,
                "batch_time": batch_time,
                "batch_tool": batch_tool,
                "moves": [{"src": src, "dest": dest} for src, dest in valid_pairs],
                "planned_count": len(valid_pairs),
                "restored_count": 0,
                "conflicts": conflicts,
                "conflict_count": len(conflicts),
                "busy_apps": busy_apps,
                "relink_preview": preview.to_dict() if preview else None,
                "note": "Use --apply to execute undo.",
            }, indent=2, ensure_ascii=False))
            return 0

        print("=== Undo Plan (Dry-Run Preview) ===")
        print(f"Root: {root}")
        print(f"Target batch: {batch_tool} ({batch_time})")
        if valid_pairs:
            for src, dest in valid_pairs:
                print(f"  -> {src} -> {dest}")
            print(f"\nTotal items to restore: {len(valid_pairs)}")
        else:
            print("  No items can be restored from this batch.")
        for c in conflicts:
            print(f"  ! Conflict skipped: {c['src']} -> {c['dest']} ({c['reason']})")
        if preview and (preview.changed or preview.warnings or preview.errors or preview.busy_apps):
            print("Coding agents that will follow these moves:")
            print_relink(preview)
        if valid_pairs:
            print("Run with --apply to execute the undo.")
        return 0

    executed_moves: List[Tuple[str, str]] = []
    errors: List[Dict[str, str]] = []
    fsmonitor_stopped: List[str] = []
    for src, dest in valid_pairs:
        if not os.path.lexists(src):
            conflicts.append({
                "src": src,
                "dest": dest,
                "reason": "Moved item no longer exists at destination",
            })
            continue
        if os.path.lexists(dest):
            conflicts.append({
                "src": src,
                "dest": dest,
                "reason": "Original location already exists",
            })
            continue
        try:
            parent_dir = os.path.dirname(dest)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
            fsmonitor_stopped.extend(_move(src, dest) or [])
            executed_moves.append((src, dest))
        except OSError as err:
            errors.append({
                "src": src,
                "dest": dest,
                "error": str(err),
            })

    relinked = after_moves(root, executed_moves, "undo") if executed_moves else None
    if executed_moves and errors:
        # The journal now treats the whole batch as undone; keep the items that could not
        # be moved back undoable as a batch of their own so a retry (or the next undo) finds them.
        try:
            record_moves(root, [(e["dest"], e["src"]) for e in errors], batch_tool)
        except OSError as err:
            sys.stderr.write(f"  ! Could not journal the items left to undo: {err}\n")

    db_path = os.path.abspath(get_default_db_path(root))
    inc_res: Any = None
    if executed_moves and os.path.exists(db_path):
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

    exit_code = 1 if errors else 0

    if as_json:
        print(json.dumps({
            "status": "applied" if not errors else ("partial_failure" if executed_moves else "failed"),
            "dry_run": False,
            "root": root,
            "undoes": batch_time,
            "batch_time": batch_time,
            "batch_tool": batch_tool,
            "moves": [{"src": src, "dest": dest} for src, dest in executed_moves],
            "restored_count": len(executed_moves),
            "moved_count": len(executed_moves),
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "errors": errors,
            "failed_count": len(errors),
            "fsmonitor_stopped": fsmonitor_stopped,
            "relink": relinked.to_dict() if relinked else None,
            "index_update": inc_res,
        }, indent=2, ensure_ascii=False))
        return exit_code

    if not errors:
        print(f"✓ Undo applied ({batch_tool} @ {batch_time}):")
    elif executed_moves:
        print(f"! Undo only partly applied ({batch_tool} @ {batch_time}): {len(errors)} item(s) could not be restored.")
        print("  They stay undoable: fix the cause (for example close the app using them) and run `smart-drive undo --apply` again.")
    else:
        print(f"✗ Undo failed ({batch_tool} @ {batch_time}): nothing was restored.")
        print("  Fix the cause (for example close the app using the items) and run `smart-drive undo --apply` again.")
    print(f"  - Restored items: {len(executed_moves)}")
    for src, dest in executed_moves:
        print(f"    {src} -> {dest}")
    for repo in fsmonitor_stopped:
        print(f"  - Stopped the git fsmonitor daemon in {repo} (it would have kept its folder from being moved)")
    for c in conflicts:
        print(f"  ! Conflict skipped: {c['src']} -> {c['dest']} ({c['reason']})")
    for e in errors:
        sys.stderr.write(f"  ! Failed to restore: {e['src']} -> {e['dest']} ({e['error']})\n")
    if inc_res:
        print(f"  - Index updated: +{inc_res['added']} ~{inc_res['modified']} -{inc_res['deleted']}")
    if relinked and (relinked.changed or relinked.warnings or relinked.errors):
        print("Coding agents updated:")
        print_relink(relinked)
    return exit_code
