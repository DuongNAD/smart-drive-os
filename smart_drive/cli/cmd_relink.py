"""smart_drive.cli.cmd_relink - `smart-drive relink`: rewrite coding agent paths after folder moves."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple

from smart_drive.core.relink import BACKUP_SUFFIX, RelinkReport, last_moves, relink
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def print_relink(report: Optional[RelinkReport]) -> None:
    """Prints a human-readable summary of a RelinkReport."""
    if report is None:
        return

    verb = "would be updated" if report.dry_run else "updated"
    for agent, count in report.agents.items():
        print(f"  ✓ {agent}: {count} path(s) {verb}")

    for item in report.history:
        print(f"  ✓ Claude Code history: {item['from']} -> {item['to']}")

    for wt in report.worktrees:
        print(f"  ✓ git worktrees repaired: {wt}")

    if not report.changed and not report.warnings and not report.errors:
        print("  ✓ Nothing left to change: all coding agent paths are up to date.")

    for warning in report.warnings:
        print(f"  ! {warning}")

    for err in report.errors:
        print(f"  ✗ {err['file']}: {err['error']}")

    if not report.dry_run and report.updated:
        print(
            "  Restart the coding agents that are open so they load the new paths "
            f"(backups: <file>{BACKUP_SUFFIX})."
        )
        print("  If one of them was open during the move, run `smart-drive relink` again after closing it.")


def cmd_relink(args: argparse.Namespace) -> int:
    """Handles the `relink` subcommand."""
    old_arg: Optional[str] = getattr(args, "old", None)
    new_arg: Optional[str] = getattr(args, "new", None)
    explicit_root: Optional[str] = getattr(args, "root", None)
    dry_run: bool = bool(getattr(args, "dry_run", False))
    as_json: bool = bool(getattr(args, "json", False))

    if (old_arg is None) != (new_arg is None):
        sys.stderr.write("relink: both OLD and NEW paths are required when specifying a manual move.\n")
        return 2

    moves: List[Tuple[str, str]]
    root: Optional[str] = None

    if old_arg is not None and new_arg is not None:
        old_abs = os.path.abspath(old_arg)
        new_abs = os.path.abspath(new_arg)
        if not os.path.exists(new_abs):
            sys.stderr.write(f"relink: destination path does not exist: {new_abs}\n")
            return 2
        if os.path.exists(old_abs):
            sys.stderr.write(f"note: old path still exists on disk: {old_abs}\n")
        try:
            root = resolve_drive_root(explicit_root).path
        except DriveRootNotFound as err:
            if explicit_root:
                sys.stderr.write(f"{err}\n")
                return 2
            root = None
        moves = [(old_abs, new_abs)]
    else:
        try:
            root = resolve_drive_root(explicit_root).path
        except DriveRootNotFound as err:
            sys.stderr.write(f"{err}\n")
            return 2
        batch = last_moves(root)
        if batch is None:
            if as_json:
                print(json.dumps(RelinkReport(dry_run=dry_run).to_dict(), indent=2, ensure_ascii=False))
            else:
                print("Nothing to relink yet: organize and classify have not moved any folder.")
            return 0
        moves = list(batch["moves"])

    report = relink(moves, root=root, dry_run=dry_run)
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        header = "Coding agents (dry-run preview):" if dry_run else "Coding agents relinked:"
        print(header)
        print_relink(report)

    return 1 if report.errors else 0


__all__ = ["cmd_relink", "print_relink"]
