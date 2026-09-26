"""smart_drive.cli.cmd_backup - CLI Subcommand Handler for `smart-drive backup`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.exfat_compat import detect_drive_root
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

    root = os.path.abspath(getattr(args, "root", None) or detect_drive_root())
    manager = SnapshotManager(root)

    raw_parts = getattr(args, "partitions", None)
    partitions = [p.strip() for p in raw_parts.split(",") if p.strip()] if raw_parts else None

    dry_run = getattr(args, "dry_run", False)
    skip_junk = not getattr(args, "no_skip_junk", False)
    use_hash = getattr(args, "hash", False)
    as_json = getattr(args, "json", False)

    try:
        report = manager.incremental_backup(
            target_dir=target,
            partitions=partitions,
            dry_run=dry_run,
            skip_junk=skip_junk,
            use_hash_comparison=use_hash,
        )
    except BackupError as b_err:
        sys.stderr.write(f"Backup Error: {b_err}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"Unexpected backup error: {exc}\n")
        return 1

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0 if report.failed_count == 0 else 1

    mode_str = "DRY-RUN SIMULATION (No files copied)" if dry_run else "EXECUTION (Applied)"
    print(f"\nSmartDrive Incremental Backup ({mode_str})")
    print("=" * 70)
    print(f"Source Root: {report.source_root}")
    print(f"Target Dir:  {report.target_dir}")
    print("-" * 70)
    print(f"Copied:      {report.copied_count:,} files ({_format_size(report.copied_bytes)})")
    print(f"Skipped:     {report.skipped_count:,} files (identical size & mtime)")
    print(f"Failed:      {report.failed_count:,} files")
    if report.manifest_path:
        print(f"Manifest:    {report.manifest_path}")

    if report.failed_files:
        print("\nFailed File Transfers:")
        for ff in report.failed_files[:10]:
            print(f"  ! {ff['path']}: {ff['error']}")
        if len(report.failed_files) > 10:
            print(f"  ... and {len(report.failed_files) - 10} more errors.")

    print()
    return 0 if report.failed_count == 0 else 1


__all__ = ["cmd_backup"]
