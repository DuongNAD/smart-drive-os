"""smart_drive.cli.cmd_offload - CLI Handler for C-Drive Developer Cache Offloading.

Provides command-line interface for:
- `smart-drive offload --scan` [--json]: Inspect reclaimable space on C:.
- `smart-drive offload --move <name> --target <drive>` [--dry-run] [--force] [--json]: Offload cache to secondary drive.
- `smart-drive offload --revert <name>` [--dry-run] [--json]: Revert offloaded junction back to native directory on C:.

100% Python Standard Library (argparse, sys, json).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from smart_drive.core.offloader import (
    get_scan_summary,
    offload_cache,
    revert_cache,
)


def cmd_offload(args: argparse.Namespace) -> int:
    """Dispatches `smart-drive offload` subcommand flags and actions."""
    is_json = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)

    # 1. Move Action
    if getattr(args, "move", None):
        target = getattr(args, "target", None)
        if not target:
            msg = "Error: --target <drive_letter> (e.g. 'D:') is required when using --move."
            if is_json:
                print(json.dumps({"status": "error", "error": msg}, indent=2))
            else:
                sys.stderr.write(f"{msg}\n")
            return 1

        try:
            res = offload_cache(
                name=args.move,
                target_drive=target,
                dry_run=dry_run,
                force=force,
            )
            if is_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"\n{res.get('message', 'Offload completed.')}")
                if res.get("reclaimed_formatted"):
                    print(f"Reclaimed Space on C: {res['reclaimed_formatted']} ({res.get('file_count', 0)} files)")
                print(f"Target Directory:      {res.get('target_path')}")
            return 0
        except Exception as exc:
            if is_json:
                print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            else:
                sys.stderr.write(f"Error: {exc}\n")
            return 1

    # 2. Revert Action
    if getattr(args, "revert", None):
        try:
            res = revert_cache(
                name=args.revert,
                dry_run=dry_run,
            )
            if is_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"\n{res.get('message', 'Revert completed.')}")
                print(f"Restored Native Path: {res.get('restored_path')}")
            return 0
        except Exception as exc:
            if is_json:
                print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
            else:
                sys.stderr.write(f"Error: {exc}\n")
            return 1

    # 3. Scan Action (Default when --scan or no move/revert specified)
    summary = get_scan_summary()
    if is_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    # Human-readable table rendering
    caches = summary.get("caches", [])
    print("\nC-Drive Developer & AI Cache Offload Scanner")
    print("=" * 96)
    print(f"{'Name':<14} {'Category':<22} {'Status':<12} {'Size (C:)':<14} {'Target / Location'}")
    print("-" * 96)

    for c in caches:
        name = c.get("name", "")
        cat = c.get("category", "")
        status = c.get("status", "")
        size_str = c.get("size_formatted", "-") if status == "FOUND" else ("0 B (linked)" if status == "OFFLOADED" else "-")
        target_str = c.get("current_target") or c.get("source_path", "")
        print(f"{name:<14} {cat:<22} {status:<12} {size_str:<14} {target_str}")

    print("-" * 96)
    total_str = summary.get("total_reclaimable_formatted", "0 B")
    count = summary.get("discovered_count", 0)
    print(f"Total Reclaimable Space on C: {total_str} (across {count} detected caches)")
    print("To offload: smart-drive offload --move <name> --target <drive_letter>\n")

    return 0
