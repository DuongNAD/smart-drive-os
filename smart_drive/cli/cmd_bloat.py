"""smart_drive.cli.cmd_bloat - CLI Handler for Dev-Artifact Bloat Scanner & Safe Clean."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from smart_drive.core.bloat_scanner import BLOAT_KINDS, BloatScanner, purge_bloat
from smart_drive.core.fsinfo import cluster_size_text
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def _fmt_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / (1024 ** 3):.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / (1024 ** 2):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


def cmd_bloat(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive bloat`."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    raw_kind = getattr(args, "kind", None)
    kinds: Optional[List[str]] = None
    if raw_kind:
        kinds = [k.strip().lower() for k in str(raw_kind).split(",") if k.strip()]
        invalid = [k for k in kinds if k not in BLOAT_KINDS]
        if invalid:
            sys.stderr.write(f"Unknown bloat kind(s): {', '.join(invalid)}. Valid: {', '.join(BLOAT_KINDS)}\n")
            return 2

    min_mb = float(getattr(args, "min_mb", 0.0) or 0.0)
    min_physical_bytes = max(0, int(min_mb * 1024 * 1024))
    want_clean = bool(getattr(args, "clean", False))
    apply_mode = bool(getattr(args, "apply", False))
    as_json = bool(getattr(args, "json", False))

    scanner = BloatScanner(root, kinds=kinds, min_physical_bytes=min_physical_bytes)
    report = scanner.scan()

    clean_result: Optional[Dict[str, Any]] = None
    if want_clean:
        clean_result = purge_bloat(report, root=root, dry_run=not apply_mode)
        report["clean"] = clean_result

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if clean_result and not clean_result.get("dry_run", True):
            return 1 if clean_result.get("failed_count", 0) > 0 else 0
        return 0

    cluster_lbl = cluster_size_text(int(report["cluster_size"]))
    summary = report["summary"]
    findings = report["findings"]
    worktrees = report["linked_worktrees"]

    print(f"\nSmartDrive-OS Dev-Artifact Bloat Report: {root}")
    print(f"Filesystem: {report['filesystem']['display_name']} (cluster {cluster_lbl})")
    print("-" * 86)

    if not findings:
        print("No matching dev-artifact bloat trees found.")
    else:
        print(f"{'Kind':<16} {'Physical':>10} {'Logical':>10} {'Slack':>7} {'Status':<22} Path")
        print("-" * 86)
        for item in findings:
            status = "RECLAIMABLE" if item["reclaimable"] else f"BLOCKED:{item['blocked_reason']}"
            print(
                f"{item['kind']:<16} "
                f"{_fmt_size(item['total_physical_bytes']):>10} "
                f"{_fmt_size(item['nominal_bytes']):>10} "
                f"{item['slack_percentage']:>6.1f}% "
                f"{status:<22} "
                f"{item['rel_path']}"
            )
        print("-" * 86)
        print(
            f"Total: {summary['total_findings']} item(s) | "
            f"Physical: {_fmt_size(summary['total_physical_bytes'])} "
            f"(Logical: {_fmt_size(summary['total_nominal_bytes'])}, "
            f"Dir clusters: {_fmt_size(summary['total_dir_allocated_bytes'])})"
        )
        print(
            f"Reclaimable: {summary['reclaimable_count']} item(s), "
            f"{_fmt_size(summary['reclaimable_physical_bytes'])} | "
            f"Blocked: {summary['blocked_count']} item(s)"
        )

    if worktrees:
        print(f"\nLinked Git Worktrees ({len(worktrees)} found, advisory only):")
        for wt in worktrees:
            dirty_tag = "DIRTY" if wt["is_dirty"] else "clean"
            art_tag = " [has build artifacts]" if wt["has_build_artifacts"] else ""
            print(f"  - [{dirty_tag}] {wt['rel_path']} ({wt['branch']}){art_tag}")

    if clean_result is not None:
        if clean_result.get("dry_run", True):
            print(
                f"\n[DRY RUN] Would remove {clean_result['planned_count']} reclaimable item(s) "
                f"({_fmt_size(clean_result['reclaimable_physical_bytes'])}), "
                f"skipping {clean_result['skipped_blocked']} blocked item(s)."
            )
            print("Re-run with 'smart-drive bloat --clean --apply' to delete permanently.")
        else:
            print(
                f"\nApplied: Deleted {clean_result['deleted_count']} item(s), "
                f"reclaimed {_fmt_size(clean_result['reclaimed_physical_bytes'])}, "
                f"failed {clean_result['failed_count']}, skipped {clean_result['skipped_blocked']}."
            )
            return 1 if clean_result["failed_count"] > 0 else 0
    elif summary["reclaimable_count"] > 0:
        print("\nRun 'smart-drive bloat --clean' to preview safe deletion (--clean --apply to delete).")

    return 0


__all__ = ["cmd_bloat"]
