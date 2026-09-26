"""smart_drive.cli.cmd_health - SSD Health, TRIM & Geometry Monitor Command."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from smart_drive.core.health import check_drive_health


def format_bytes_human(b: int) -> str:
    """Formats bytes into human-readable string (GB / MB / KB)."""
    if b >= 1024 ** 4:
        return f"{b / (1024 ** 4):.2f} TB"
    if b >= 1024 ** 3:
        return f"{b / (1024 ** 3):.2f} GB"
    if b >= 1024 ** 2:
        return f"{b / (1024 ** 2):.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.2f} KB"
    return f"{b} B"


def cmd_health(args: argparse.Namespace) -> int:
    """Handles the `health` subcommand."""
    target_drive: Optional[str] = getattr(args, "drive", None) or getattr(args, "root", None)
    json_mode: bool = getattr(args, "json", False)

    try:
        report = check_drive_health(target_drive)
    except Exception as exc:
        if json_mode:
            print(json.dumps({
                "drive_letter": str(target_drive or "unknown"),
                "filesystem": "unknown",
                "trim_enabled": None,
                "trim_status_message": str(exc),
                "total_bytes": 0,
                "free_bytes": 0,
                "free_percent": 0.0,
                "cluster_size_bytes": 0,
                "warnings": [str(exc)],
            }, indent=2))
        else:
            print(f"Error checking drive health: {exc}", file=sys.stderr)
        return 1

    if json_mode:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    # Human-readable formatted terminal output
    used_bytes = max(0, report.total_bytes - report.free_bytes)
    cluster_kb = report.cluster_size_bytes // 1024 if report.cluster_size_bytes >= 1024 else report.cluster_size_bytes

    print("=" * 70)
    print("  SmartDrive-OS SSD Health, TRIM & Partition Diagnostics")
    print("=" * 70)
    print(f"Drive Volume:        {report.drive_letter}")
    print(f"Filesystem:          {report.filesystem}")
    print(f"Cluster Size:        {report.cluster_size_bytes:,} bytes ({cluster_kb} KB)")
    print(f"Total Capacity:      {format_bytes_human(report.total_bytes)}")
    print(f"Used Space:          {format_bytes_human(used_bytes)}")
    print(f"Free Space:          {format_bytes_human(report.free_bytes)} ({report.free_percent:.1f}% free)")

    if report.trim_enabled is True:
        trim_str = f"✓ Enabled ({report.trim_status_message})"
    elif report.trim_enabled is False:
        trim_str = f"✗ Disabled ({report.trim_status_message})"
    else:
        trim_str = f"? {report.trim_status_message}"
    print(f"TRIM Status:         {trim_str}")

    print("-" * 70)
    if not report.warnings:
        print("Overall Health:      ✓ HEALTHY (No warnings detected)")
    else:
        status_label = "CRITICAL ALERTS" if any("CRITICAL" in w for w in report.warnings) else "ATTENTION REQUIRED"
        print(f"Overall Health:      ! {status_label} ({len(report.warnings)} issues found):")
        for w in report.warnings:
            print(f"  • {w}")
    print("=" * 70)

    return 0


__all__ = ["cmd_health"]
