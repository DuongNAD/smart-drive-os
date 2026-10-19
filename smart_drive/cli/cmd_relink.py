"""smart_drive.cli.cmd_relink - `smart-drive relink`: rewrite coding agent paths after folder moves."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional, Sequence, Tuple

from smart_drive.core.relink import (
    BACKUP_SUFFIX,
    Move,
    MovedFolder,
    RelinkReport,
    agents_still_running,
    calling_agents,
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


def print_found(
    entries: List[MovedFolder],
    show_apply_footer: bool = True,
    skipped_unmounted: Optional[ dict[str, int] ] = None,
) -> None:
    """Prints human-readable lines and footer for `smart-drive relink --find`."""
    if skipped_unmounted is None:
        skipped_unmounted = getattr(entries, "skipped_unmounted", None)

    if not entries:
        print("No stale paths: every folder the coding agents remember exists.")
    else:
        has_sure = False
        for entry in entries:
            agents_str = ", ".join(entry.agents)
            if entry.kind in ("journal", "same-name"):
                has_sure = True
                print(f"  ✓ {entry.old} -> {entry.new}  ({entry.kind}; {agents_str})")
            elif entry.kind == "renamed":
                print(f'  ? {entry.old} -> {entry.new}  (renamed? check it, then run: smart-drive relink "{entry.old}" "{entry.new}")')
            elif entry.kind == "name-only":
                print(
                    f"  ? {entry.old} -> {entry.new}  (only the name matches; if you moved it there, run: "
                    f'smart-drive relink "{entry.old}" "{entry.new}")'
                )
            elif entry.kind == "ambiguous":
                cands = ", ".join(entry.candidates)
                print(f"  ? {entry.old}: several candidates: {cands}")
            elif entry.kind == "unreadable":
                print(f"  ✗ {entry.old}: not an absolute path ({agents_str})")
            else:
                print(f"  ✗ {entry.old}: not found ({agents_str})")

        if has_sure and show_apply_footer:
            print("Run `smart-drive relink --find --apply` to relink the ✓ folders.")

    if skipped_unmounted:
        total_skipped = sum(skipped_unmounted.values())
        drives_str = ", ".join(skipped_unmounted.keys())
        print(f"Skipped {total_skipped} path(s) on drives that are not connected: {drives_str}")


def _wait_for_busy_agents(moves: Sequence[Move], root: Optional[str], wait_minutes: float) -> bool:
    """Previews relink and waits for any busy coding agents to close. Returns False on timeout."""
    preview = relink(moves, root=root, dry_run=True)
    affected = set(preview.agents)
    if not affected:
        return True
    own = sorted(affected & calling_agents())
    if own:
        # An agent that runs this command waits for relink, so relink must not wait for it.
        them = "it" if len(own) == 1 else "them"
        sys.stderr.write(
            f"relink: this command runs inside {', '.join(own)}, so it cannot wait for {them} to close; "
            f"restart {them} afterwards.\n"
        )
        affected -= set(own)
    # An unreadable process list is no proof that the agents closed, so it counts as still running.
    busy = agents_still_running(affected)
    if not busy:
        return True

    # stderr, so that `--json` output stays parseable while relink waits
    print(f"Waiting for {', '.join(busy)} to close. Quit them now (Ctrl+C cancels)...", file=sys.stderr, flush=True)
    deadline = time.monotonic() + max(0.0, float(wait_minutes)) * 60.0
    while busy:
        if time.monotonic() >= deadline:
            sys.stderr.write(f"relink: timed out waiting for {', '.join(busy)} to close; no files were modified.\n")
            return False
        time.sleep(2)
        busy = agents_still_running(affected)
    return True


def cmd_relink(args: argparse.Namespace) -> int:
    """Handles the `relink` subcommand."""
    old_arg: Optional[str] = getattr(args, "old", None)
    new_arg: Optional[str] = getattr(args, "new", None)
    explicit_root: Optional[str] = getattr(args, "root", None)
    dry_run: bool = bool(getattr(args, "dry_run", False))
    as_json: bool = bool(getattr(args, "json", False))
    find_mode: bool = bool(getattr(args, "find", False))
    apply_mode: bool = bool(getattr(args, "apply", False))
    wait_minutes: Optional[float] = getattr(args, "wait", None)

    if find_mode:
        if old_arg is not None or new_arg is not None:
            sys.stderr.write("relink: --find cannot be used together with OLD/NEW paths.\n")
            return 2
        if wait_minutes is not None and not apply_mode:
            sys.stderr.write("relink: --wait with --find requires --apply.\n")
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
        sure_moves: List[Move] = []

        if not as_json:
            print_found(entries, show_apply_footer=not apply_mode)

        if apply_mode:
            sure_moves = [
                (e.old, e.new)
                for e in entries
                if e.kind in ("journal", "same-name") and e.new is not None
            ]
            if wait_minutes is not None and not dry_run and sure_moves:
                if not _wait_for_busy_agents(sure_moves, root, wait_minutes):
                    return 3
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
                "skipped_unmounted": dict(getattr(entries, "skipped_unmounted", {})),
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif report is not None and entries and not sure_moves:
            # print_relink would say every path is up to date, under the stale folders listed above
            print("  Nothing to relink: no folder is marked ✓.")
        elif report is not None:
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

    if wait_minutes is not None and not dry_run:
        if not _wait_for_busy_agents(moves, root, wait_minutes):
            return 3

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
