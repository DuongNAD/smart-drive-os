"""smart_drive.cli.cmd_snapshot - CLI Subcommand Handler for `smart-drive snapshot`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.fsinfo import CLUSTER_SIZE_ASSUMED, cluster_size_text, detect_filesystem, slack_cluster
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

    # A snapshot works its allocated sizes out at the volume's own allocation unit (as `audit` does), 512 KB exFAT only
    # when it cannot be read, and records which in its manifest
    cluster_size, cluster_size_source = slack_cluster(detect_filesystem(root))
    manager = SnapshotManager(root, cluster_size=cluster_size, cluster_size_source=cluster_size_source)
    as_json = getattr(args, "json", False)

    # -------------------------------------------------------------------------
    # 1. ACTION: create
    # -------------------------------------------------------------------------
    if action == "create":
        name = getattr(args, "name", None)
        raw_parts = getattr(args, "partitions", None)
        partitions = [p.strip() for p in raw_parts.split(",") if p.strip()] if raw_parts else None

        include_hidden = getattr(args, "include_hidden", False)

        try:
            manifest = manager.create_snapshot(name=name, partitions=partitions, include_hidden=include_hidden)
        except Exception as exc:
            sys.stderr.write(f"Error creating snapshot: {exc}\n")
            return 1

        if as_json:
            data = manifest.to_dict()
            folders, loose_files = manager.uncovered_entries(manifest.partitions)
            data["not_covered"] = {"folders": folders, "loose_files": loose_files}
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return 0

        print(f"\n✓ Point-in-time snapshot created successfully: {manifest.name}")
        print(f"  Timestamp:  {manifest.timestamp}")
        print(f"  Partitions: {', '.join(manifest.partitions)}")
        print(f"  Files:      {manifest.file_count:,}")
        print(f"  Logical:    {_format_size(manifest.total_logical_bytes)}")
        cluster_note = f"at {cluster_size_text(manifest.cluster_size)} clusters"
        if manifest.cluster_size_source == CLUSTER_SIZE_ASSUMED:
            cluster_note += ", assumed: the drive's own could not be read"
        print(f"  Allocated:  {_format_size(manifest.total_allocated_bytes)} ({cluster_note})")
        print(f"  Manifest:   {manager.snapshot_dir / f'{manifest.name}.json'}")
        for note in manager.coverage_notes(manifest.partitions, manifest.excluded_dirs, manifest.include_hidden):
            print(f"  {note}")
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
        print("=" * 101)
        print(f"{'Snapshot Name':<28} {'Created (UTC)':<20} {'Files':<8} {'Logical':<12} {'Allocated':<12} {'Cluster'}")
        print("-" * 101)
        for s in summaries:
            # The cluster each snapshot was made at (its manifest says), not the one the volume has today
            cluster = cluster_size_text(s.cluster_size)
            if s.cluster_size_source == CLUSTER_SIZE_ASSUMED:
                cluster += " (assumed)"
            print(
                f"{s.name:<28} "
                f"{s.timestamp:<20} "
                f"{s.file_count:<8} "
                f"{_format_size(s.total_logical_bytes):<12} "
                f"{_format_size(s.total_allocated_bytes):<12} "
                f"{cluster}"
            )
        print("=" * 101)
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
