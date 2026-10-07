"""smart_drive.cli.cmd_classify - CLI Subcommand Handler for `smart-drive classify`.

Milestone 3 (SmartDrive-OS v1.1.0).
Zero external dependencies: 100% Python Standard Library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.classifier import ClassifierEngine, ClassificationReport
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def _format_size(size_bytes: int) -> str:
    """Formats bytes into human-readable size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def cmd_classify(args: argparse.Namespace) -> int:
    """Handles the `classify` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    target_path = getattr(args, "path", None)
    target = os.path.abspath(target_path) if target_path else root

    apply_mode = getattr(args, "apply", False)
    dry_run_mode = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)
    recursive = not getattr(args, "no_recursive", False)

    engine = ClassifierEngine(root_path=root)
    results = engine.scan_and_classify(target_dir=Path(target), recursive=recursive)

    # 1. APPLY MODE
    if apply_mode:
        actions = engine.execute_relocation(results, dry_run=False)
        report = ClassificationReport(
            root=root,
            total_scanned=len(results),
            total_classified=len([r for r in results if not r.format_type.startswith("Unknown")]),
            total_unknown=len([r for r in results if r.format_type.startswith("Unknown")]),
            applied=True,
            dry_run=False,
            results=results,
            actions=actions,
        )

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0

        moved = [a for a in actions if a.status == "MOVED"]
        skipped = [a for a in actions if a.status == "SKIPPED_ALREADY_IN_PLACE"]
        errors = [a for a in actions if a.status == "ERROR"]

        print(f"\nSmartDrive-OS Classification & Auto-Tagging [APPLIED]")
        print("=" * 90)
        for a in moved:
            suffix = " (Renamed to prevent overwrite)" if a.collision_resolved else ""
            print(f"  ✓ Moved: {os.path.basename(str(a.source_path))} -> {a.relative_target}{suffix}")
        for s in skipped:
            print(f"  - In place: {os.path.basename(str(s.source_path))} is already in {s.relative_target}")
        for e in errors:
            print(f"  ✗ Error moving {os.path.basename(str(e.source_path))}: {e.error_message}")
        print("-" * 90)
        print(f"Applied: {len(moved)} moved, {len(skipped)} already in place, {len(errors)} error(s).\n")
        return 0 if not errors else 1

    # 2. DRY-RUN SIMULATION MODE
    elif dry_run_mode:
        actions = engine.execute_relocation(results, dry_run=True)
        report = ClassificationReport(
            root=root,
            total_scanned=len(results),
            total_classified=len([r for r in results if not r.format_type.startswith("Unknown")]),
            total_unknown=len([r for r in results if r.format_type.startswith("Unknown")]),
            applied=False,
            dry_run=True,
            results=results,
            actions=actions,
        )

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0

        print(f"\nSmartDrive-OS Classification & Auto-Tagging [DRY-RUN SIMULATION]")
        print("=" * 90)
        print(f"{'Source File':<36} {'-> Target Destination':<40} {'Status'}")
        print("-" * 90)
        for a in actions:
            col_str = " (COLLISION RESOLVED)" if a.collision_resolved else ""
            src_name = os.path.basename(str(a.source_path))
            print(f"{src_name:<36} -> {a.relative_target:<38} {a.status}{col_str}")
        print("-" * 90)
        print(f"Simulation Complete: {len(actions)} item(s) analyzed. No changes made to disk.")
        print("Run with '--apply' to execute these relocations.\n")
        return 0

    # 3. SUGGEST MODE (Default)
    else:
        report = ClassificationReport(
            root=root,
            total_scanned=len(results),
            total_classified=len([r for r in results if not r.format_type.startswith("Unknown")]),
            total_unknown=len([r for r in results if r.format_type.startswith("Unknown")]),
            applied=False,
            dry_run=False,
            results=results,
            actions=[],
        )

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0

        print(f"\nSmartDrive-OS Intelligent Classifier (v1.1.0)")
        print(f"Scan Root: {target} (Total Items: {len(results)})")
        print("=" * 95)
        print(f"{'File / Directory':<34} {'Format':<18} {'Confidence':<12} {'Target Taxonomy'}")
        print("-" * 95)
        for r in results:
            item_display = f"{r.name}/" if r.is_dir else r.name
            conf_str = f"{int(r.confidence * 100)}%"
            target_disp = f"{r.category}/{r.subcategory}"
            print(f"{item_display:<34} {r.format_type:<18} {conf_str:<12} {target_disp}")
        print("=" * 95)
        print(f"Classified {report.total_classified} of {report.total_scanned} item(s) ({report.total_unknown} unknown).")
        print("Options: Run with '--dry-run' to simulate moves, or '--apply' to safely organize.\n")
        return 0
