"""smart_drive.cli.cmd_tidy - Agent-driven and interactive drive organization command.

Zero-dependency standard library CLI handler for survey, plan proposal,
execution, undo, and journal listing.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from typing import Any, Dict, List

from smart_drive.core.planner import (
    apply_moves,
    list_journals,
    normalize_scope,
    propose_plan,
    survey,
    undo_journal,
    validate_moves,
)
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def cmd_tidy(args: argparse.Namespace) -> int:
    """Handles the `tidy` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    is_json = getattr(args, "json", False)
    is_apply = getattr(args, "apply", False)

    # 1. Handle --journals
    if getattr(args, "journals", False):
        journals = list_journals(root)
        if is_json:
            print(json.dumps({"journals": journals, "count": len(journals)}, indent=2, ensure_ascii=False))
            return 0
        print(f"\nOrganization Journals for: {root}")
        print("-" * 75)
        if not journals:
            print("No organization journals found.")
            return 0
        for j in journals:
            print(f"Journal ID:  {j['id']}")
            print(f"Created:     {j['created_at']}")
            print(f"Scope:       {j['scope'] or '(root)'} | Mode: {j['mode']} | Status: {j['status']}")
            print(f"Moves:       {j['done_moves']}/{j['total_moves']} succeeded ({j['failed_moves']} failed)")
            print("-" * 75)
        return 0

    # 2. Handle --undo JOURNAL_ID
    undo_id = getattr(args, "undo", None)
    if undo_id:
        undo_res = undo_journal(root, journal_id=undo_id, apply=is_apply)
        if is_json:
            print(json.dumps(undo_res, indent=2, ensure_ascii=False))
            return 0 if undo_res.get("status") != "error" else 1

        if undo_res.get("status") == "error":
            sys.stderr.write(f"Error: {undo_res.get('error')}\n")
            return 1

        if is_apply:
            print(f"\n✓ Rollback applied for journal: {undo_id}")
            print(f"  - Restored moves: {undo_res.get('undone_count', 0)}")
            print(f"  - Conflicts:      {undo_res.get('conflict_count', 0)}")
        else:
            print(f"\n[DRY RUN] Rollback preview for journal: {undo_id}")
            print(f"  - Can restore:    {undo_res.get('can_undo_count', 0)} moves")
            print(f"  - Conflicts:      {undo_res.get('conflict_count', 0)}")
            for c in undo_res.get("conflicts", []):
                print(f"    ! Conflict on '{c.get('path')}': {c.get('reason')}")
            print("\nRun with --apply to execute the rollback.")
        return 0

    # 3. Propose and/or Apply plan
    raw_path = getattr(args, "path", None) or ""
    try:
        rel_scope = normalize_scope(raw_path, root) if raw_path else ""
    except ValueError:
        rel_scope = str(raw_path).replace("\\", "/").strip("/")
    mode = getattr(args, "mode", "auto") or "auto"
    detect_dups = getattr(args, "duplicates", False) or getattr(args, "detect_duplicates", False)
    min_conf = getattr(args, "min_confidence", "low") or "low"

    plan = propose_plan(
        root,
        scope=rel_scope,
        mode=mode,
        detect_duplicates=detect_dups,
        min_confidence=min_conf,
    )

    if is_apply:
        result = apply_moves(
            root,
            moves=plan.moves,
            apply=True,
            write_index=True,
            mode=mode,
            scope=rel_scope,
        )
        if is_json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("failed", 0) == 0 else 1

        print(f"\n✓ Applied organization plan [{result.get('journal_id')}]:")
        print(f"  - Moved:   {result.get('moved', 0)} items")
        print(f"  - Failed:  {result.get('failed', 0)} items")
        if result.get("errors"):
            for err in result["errors"][:5]:
                print(f"    ! Error: {err}")
        return 0 if result.get("failed", 0) == 0 else 1

    # Default dry-run plan display
    plan_dict = plan.to_dict()
    if is_json:
        print(json.dumps(plan_dict, indent=2, ensure_ascii=False))
        return 0

    print(f"\nProposed Organization Plan for: {os.path.join(root, rel_scope) if rel_scope else root}")
    print(f"Mode: {mode} | Scope: {rel_scope or '(root)'} | Plan ID: {plan.plan_id}")
    print(f"Total moves planned: {len(plan.moves)} | Kept: {len(plan.kept)}")
    print("=" * 75)

    # Group moves by destination directory
    grouped: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for m in plan.moves:
        dst = m.dst if isinstance(m, dict) else m.dst
        src = m.src if isinstance(m, dict) else m.src
        reason = m.reason if isinstance(m, dict) else m.reason
        dst_dir = os.path.dirname(dst) or "."
        grouped[dst_dir].append({"src": src, "dst": dst, "reason": reason})

    if not grouped:
        print("No moves proposed. Everything is already organized or kept.")
    else:
        for dst_dir, items in sorted(grouped.items()):
            print(f"\n📁 {dst_dir}/ ({len(items)} items)")
            for item in items[:15]:
                print(f"  → {os.path.basename(item['src'])}  ({item['reason']})")
            if len(items) > 15:
                print(f"  ... and {len(items) - 15} more items.")

    if plan.kept:
        print(f"\nKept (not moved): {len(plan.kept)} items (e.g. protected, mixed, or junk)")

    print("\nRun with --apply to execute this plan.")
    return 0
