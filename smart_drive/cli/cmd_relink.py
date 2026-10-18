"""smart_drive.cli.cmd_relink - `smart-drive relink`: rewrite coding agent paths after folder moves."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple

from smart_drive.core.relink import (
    BACKUP_SUFFIX,
    MovedFolder,
    RelinkReport,
    find_moved,
    last_moves,
    record_moves,
    relink,
)
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


def print_found(entries: List[MovedFolder], show_apply_footer: bool = True) -> None:
    """Prints human-readable lines and footer for `smart-drive relink --find`."""
    if not entries:
        print("No stale paths: every folder the coding agents remember exists.")
        return

    has_sure = False
    for entry in entries:
        agents_str = ", ".join(entry.agents)
        if entry.kind in ("journal", "same-name"):
            has_sure = True
            print(f"  ✓ {entry.old} -> {entry.new}  ({entry.kind}; {agents_str})")
        elif entry.kind == "renamed":
            print(f'  ? {entry.old} -> {entry.new}  (renamed? check it, then run: smart-drive relink "{entry.old}" "{entry.new}")')
        elif entry.kind == "ambiguous":
            cands = ", ".join(entry.candidates)
            print(f"  ? {entry.old}: several candidates: {cands}")
        elif entry.kind == "unreadable":
            print(f"  ✗ {entry.old}: not an absolute path ({agents_str})")
        else:
            print(f"  ✗ {entry.old}: not found ({agents_str})")

    if has_sure and show_apply_footer:
        print("Run `smart-drive relink --find --apply` to relink the ✓ folders.")


def cmd_relink(args: argparse.Namespace) -> int:
    """Handles the `relink` subcommand."""
    old_arg: Optional[str] = getattr(args, "old", None)
    new_arg: Optional[str] = getattr(args, "new", None)
    explicit_root: Optional[str] = getattr(args, "root", None)
    dry_run: bool = bool(getattr(args, "dry_run", False))
    as_json: bool = bool(getattr(args, "json", False))
    find_mode: bool = bool(getattr(args, "find", False))
    apply_mode: bool = bool(getattr(args, "apply", False))

    if find_mode:
        if old_arg is not None or new_arg is not None:
            sys.stderr.write("relink: --find cannot be used together with OLD/NEW paths.\n")
            return 2

        try:
            root = resolve_drive_root(explicit_root).path
        except DriveRootNotFound as err:
            if explicit_root:
                sys.stderr.write(f"{err}\n")
                return 2
            root = None

        entries = find_moved(root=root)
        report: Optional[RelinkReport] = None

        if apply_mode:
            sure_moves = [
                (e.old, e.new)
                for e in entries
                if e.kind in ("journal", "same-name") and e.new is not None
            ]
            report = relink(sure_moves, root=root, dry_run=dry_run)
            if sure_moves and root is not None and not dry_run:
                try:
                    record_moves(root, sure_moves, "relink")
                except OSError as err:
                    report.warnings.insert(0, f"Could not write move journal: {err}")

        if as_json:
            payload = {
                "entries": [e.to_dict() for e in entries],
                "relink": report.to_dict() if report is not None else None,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print_found(entries, show_apply_footer=not apply_mode)
            if report is not None:
                print_relink(report)

        return 1 if (report is not None and report.errors) else 0

    if (old_arg is None) != (new_arg is None):
        sys.stderr.write("relink: both OLD and NEW paths are required when specifying a manual move.\n")
        return 2

    moves: List[Tuple[str, str]]
    root = None
    is_manual = False

    if old_arg is not None and new_arg is not None:
        is_manual = True
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
    if is_manual and root is not None and not dry_run:
        try:
            record_moves(root, moves, "relink")
        except OSError as err:
            report.warnings.insert(0, f"Could not write move journal: {err}")

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        header = "Coding agents (dry-run preview):" if dry_run else "Coding agents relinked:"
        print(header)
        print_relink(report)

    return 1 if report.errors else 0


__all__ = ["cmd_relink", "print_found", "print_relink"]
