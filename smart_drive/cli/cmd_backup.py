"""smart_drive.cli.cmd_backup - CLI Subcommand Handler for `smart-drive backup`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.snapshot import SnapshotManager, BackupError


def _format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable representation."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def cmd_backup(args: argparse.Namespace) -> int:
    """Handles the `smart-drive backup --target <path>` incremental backup command."""
    target = getattr(args, "target", None)
    if not target:
        sys.stderr.write("Error: --target directory is required for backup.\n")
        return 1

    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    manager = SnapshotManager(root)

    raw_parts = getattr(args, "partitions", None)
    partitions = [p.strip() for p in raw_parts.split(",") if p.strip()] if raw_parts else None

    dry_run = getattr(args, "dry_run", False)
    skip_junk = not getattr(args, "no_skip_junk", False)
    use_hash = getattr(args, "hash", False)
    include_hidden = getattr(args, "include_hidden", False)
    as_json = getattr(args, "json", False)

    try:
        report = manager.incremental_backup(
            target_dir=target,
            partitions=partitions,
            dry_run=dry_run,
            skip_junk=skip_junk,
            use_hash_comparison=use_hash,
            include_hidden=include_hidden,
        )
    except BackupError as b_err:
        sys.stderr.write(f"Backup Error: {b_err}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"Unexpected backup error: {exc}\n")
        return 1

    active_partitions = manager.resolve_partitions(partitions)
    if as_json:
        data = report.to_dict()
        folders, loose_files = manager.uncovered_entries(active_partitions)
        data["partitions"] = active_partitions
        data["not_covered"] = {"folders": folders, "loose_files": loose_files}
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0 if report.failed_count == 0 else 1

    mode_str = "DRY-RUN SIMULATION (No files copied)" if dry_run else "EXECUTION (Applied)"
    print(f"\nSmartDrive Incremental Backup ({mode_str})")
    print("=" * 70)
    print(f"Source Root: {report.source_root}")
    print(f"Target Dir:  {report.target_dir}")
    print(f"Partitions:  {', '.join(active_partitions)}")
    print("-" * 70)
    print(f"Copied:      {report.copied_count:,} files ({_format_size(report.copied_bytes)})")
    print(f"Skipped:     {report.skipped_count:,} files (identical size & mtime)")
    print(f"Failed:      {report.failed_count:,} files")
    if report.manifest_path:
        print(f"Manifest:    {report.manifest_path}")
    for note in manager.coverage_notes(active_partitions, report.excluded_dirs, include_hidden):
        print(note)

    if report.failed_files:
        print("\nFailed File Transfers:")
        for ff in report.failed_files[:10]:
            print(f"  ! {ff['path']}: {ff['error']}")
        if len(report.failed_files) > 10:
            print(f"  ... and {len(report.failed_files) - 10} more errors.")

    print()
    return 0 if report.failed_count == 0 else 1


__all__ = ["cmd_backup"]
