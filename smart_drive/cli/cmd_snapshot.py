"""smart_drive.cli.cmd_snapshot - CLI Subcommand Handler for `smart-drive snapshot`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.snapshot import SnapshotManager, SnapshotNotFoundError, SnapshotError


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


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Handles `smart-drive snapshot` subcommands (`create`, `list`, `verify`)."""
    action = getattr(args, "action", None)
    if not action:
        print("Usage: smart-drive snapshot [create|list|verify] [options]")
        print("Run 'smart-drive snapshot --help' for details.")
        return 0

    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    manager = SnapshotManager(root)
    as_json = getattr(args, "json", False)

    # -------------------------------------------------------------------------
    # 1. ACTION: create
    # -------------------------------------------------------------------------
    if action == "create":
        name = getattr(args, "name", None)
        raw_parts = getattr(args, "partitions", None)
        partitions = [p.strip() for p in raw_parts.split(",") if p.strip()] if raw_parts else None

        try:
            manifest = manager.create_snapshot(name=name, partitions=partitions)
        except Exception as exc:
            sys.stderr.write(f"Error creating snapshot: {exc}\n")
            return 1

        if as_json:
            print(manifest.to_json())
            return 0

        print(f"\n✓ Point-in-time snapshot created successfully: {manifest.name}")
        print(f"  Timestamp:  {manifest.timestamp}")
        print(f"  Partitions: {', '.join(manifest.partitions)}")
        print(f"  Files:      {manifest.file_count:,}")
        print(f"  Logical:    {_format_size(manifest.total_logical_bytes)}")
        print(f"  Allocated:  {_format_size(manifest.total_allocated_bytes)} (512KB clusters)")
        print(f"  Manifest:   {manager.snapshot_dir / f'{manifest.name}.json'}")
        return 0

    # -------------------------------------------------------------------------
    # 2. ACTION: list
    # -------------------------------------------------------------------------
    elif action == "list":
        summaries = manager.list_snapshots()

        if as_json:
            print(json.dumps([s.to_dict() for s in summaries], indent=2, ensure_ascii=False))
            return 0

        if not summaries:
            print(f"\nNo snapshots found in {manager.snapshot_dir}")
            return 0

        print(f"\nAvailable Snapshots (Root: {root}):")
        print("=" * 86)
        print(f"{'Snapshot Name':<28} {'Created (UTC)':<20} {'Files':<8} {'Logical':<12} {'Allocated (512K)'}")
        print("-" * 86)
        for s in summaries:
            print(
                f"{s.name:<28} "
                f"{s.timestamp:<20} "
                f"{s.file_count:<8} "
                f"{_format_size(s.total_logical_bytes):<12} "
                f"{_format_size(s.total_allocated_bytes)}"
            )
        print("=" * 86)
        print(f"Total: {len(summaries)} snapshot(s) stored.\n")
        return 0

    # -------------------------------------------------------------------------
    # 3. ACTION: verify
    # -------------------------------------------------------------------------
    elif action == "verify":
        name = getattr(args, "name", None)
        if not name:
            sys.stderr.write("Error: Snapshot name is required for verification. Usage: smart-drive snapshot verify <name>\n")
            return 1

        check_untracked = not getattr(args, "no_untracked", False)

        try:
            report = manager.verify_snapshot(name=name, check_untracked=check_untracked)
        except SnapshotNotFoundError:
            sys.stderr.write(f"Error: Snapshot '{name}' not found in {manager.snapshot_dir}\n")
            return 1
        except Exception as exc:
            sys.stderr.write(f"Error during verification: {exc}\n")
            return 1

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0 if report.intact else 1

        print(f"\nSnapshot Integrity Verification: {report.snapshot_name}")
        print("-" * 70)
        status_str = "✓ INTACT (100% Validated)" if report.intact else "✗ INTEGRITY COMPROMISED"
        print(f"Status:     {status_str}")
        print(f"Verified:   {report.verified_count:,} files matching SHA-256")
        print(f"Modified:   {report.corrupted_count:,} files (hash or size mismatch)")
        print(f"Missing:    {report.missing_count:,} files")
        print(f"Untracked:  {report.untracked_count:,} new files")

        if report.modified_files:
            print("\nModified Files (Tampered/Corrupted):")
            for m in report.modified_files[:10]:
                print(f"  ! {m['path']} (Expected: {m['expected_hash'][:12]}..., Got: {m['actual_hash'][:12]}...)")
            if len(report.modified_files) > 10:
                print(f"  ... and {len(report.modified_files) - 10} more modified files.")

        if report.missing_files:
            print("\nMissing Files:")
            for mf in report.missing_files[:10]:
                print(f"  - {mf}")
            if len(report.missing_files) > 10:
                print(f"  ... and {len(report.missing_files) - 10} more missing files.")

        if report.untracked_files:
            print("\nUntracked New Files (in snapshot partitions):")
            for uf in report.untracked_files[:5]:
                print(f"  + {uf}")
            if len(report.untracked_files) > 5:
                print(f"  ... and {len(report.untracked_files) - 5} more untracked files.")

        print()
        return 0 if report.intact else 1

    else:
        sys.stderr.write(f"Unknown snapshot action: '{action}'\n")
        return 2


__all__ = ["cmd_snapshot"]
