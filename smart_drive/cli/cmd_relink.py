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
    NOT_AGENT_CONFIGS,
    RelinkReport,
    agents_still_running,
    calling_agents,
    find_moved,
    last_moves,
    record_moves,
    relink,
)
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def print_relink(report: Optional[RelinkReport], busy_hint_when_idle: bool = True, relink_only: bool = False) -> None:
    """Prints a human-readable summary of a RelinkReport.

    A dry run lists the running coding agents under "Close these before --apply". When it has nothing to apply,
    busy_hint_when_idle=False leaves that out, so that it ends like the real run: "Nothing left to change".

    relink_only=True is `smart-drive relink` itself, which moves nothing: only the running agents whose own config
    files change need to close, its real run is the same command without --dry-run, and agents need a restart only
    when one of their config files changed. A change to a file of NOT_AGENT_CONFIGS (a venv, an editable install or a
    shortcut) calls for neither.
    """
    if report is None:
        return

    verb = "would be updated" if report.dry_run else "updated"
    for agent, count in report.agents.items():
        print(f"  ✓ {agent}: {count} path(s) {verb}")
        if agent in ("Windows Shortcuts", "Python editable", "Python venv"):
            for entry in report.updated:
                if entry.get("agent") == agent:
                    print(f"    - {entry['file']}")

    for item in report.history:
        print(f"  ✓ Claude Code history: {item['from']} -> {item['to']}")

    wt_verb = "would be repaired" if report.dry_run else "repaired"
    for wt in report.worktrees:
        print(f"  ✓ git worktrees {wt_verb}: {wt}")

    venv_verb = "would be updated" if report.dry_run else "updated"
    for venv in report.venvs:
        counted = (
            (report.console_scripts.get(venv, 0), "console script"),
            (report.activation_scripts.get(venv, 0), "activation script"),
        )
        what = " and ".join(f"{count} {noun}{'' if count == 1 else 's'}" for count, noun in counted if count)
        note = f" ({what} {'would be repointed' if report.dry_run else 'repointed'})" if what else ""
        print(f"  ✓ Python venv {venv_verb}: {venv}{note}")

    idle = not report.changed and not report.warnings and not report.errors
    if idle:
        print("  ✓ Nothing left to change: all coding agent paths are up to date.")

    for warning in report.warnings:
        print(f"  ! {warning}")

    if report.dry_run and report.busy_apps and (busy_hint_when_idle or not idle):
        if relink_only:
            # Only the agents whose own config files change, the rule of the warning at the end of relink()
            closing = sorted(set(report.agents) & set(report.busy_apps))
            if closing:
                print(f"  ! Close these before running it without --dry-run: {', '.join(closing)}")
        else:
            print(f"  ! Close these before --apply: {', '.join(report.busy_apps)}")

    for err in report.errors:
        print(f"  ✗ {err['file']}: {err['error']}")

    # A venv, an editable install or a shortcut is no coding agent's file, so none of them has to restart for it
    restart = any(agent not in NOT_AGENT_CONFIGS for agent in report.agents) if relink_only else bool(report.updated)
    if not report.dry_run and restart:
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
        has_unsure = False
        for entry in entries:
            agents_str = ", ".join(entry.agents)
            if entry.kind in ("journal", "same-name"):
                has_sure = True
                print(f"  ✓ {entry.old} -> {entry.new}  ({entry.kind}; {agents_str})")
            elif entry.kind == "renamed":
                has_unsure = True
                print(f'  ? {entry.old} -> {entry.new}  (renamed? check it, then run: smart-drive relink "{entry.old}" "{entry.new}")')
            elif entry.kind == "name-only":
                has_unsure = True
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
        if has_unsure and show_apply_footer:
            if has_sure:
                print("Add `--ask` to also be asked about each ? folder with one candidate.")
            else:
                print("Run `smart-drive relink --find --apply --ask` to be asked about each ? folder with one candidate.")

    if skipped_unmounted:
        total_skipped = sum(skipped_unmounted.values())
        drives_str = ", ".join(skipped_unmounted.keys())
        print(f"Skipped {total_skipped} path(s) on drives that are not connected: {drives_str}")


def _stdin_is_terminal() -> bool:
    """True when a person can answer questions on stdin."""
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _confirm_moves(unsure: Sequence[Move]) -> List[Move]:
    """Asks whether to relink each ? match; returns the moves answered y. End of input answers no to the rest."""
    print("Answer y only for a ? folder you moved there yourself:")
    confirmed: List[Move] = []
    for old, new in unsure:
        try:
            answer = input(f"  Relink {old} -> {new}? [y/N]: ")
        except EOFError:
            print()
            break
        if answer.strip().lower() in ("y", "yes"):
            confirmed.append((old, new))
    return confirmed


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
    busy = sorted(affected) if getattr(preview, '_unreadable_processes', False) else sorted(affected & set(preview.busy_apps))
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
    ask_mode: bool = bool(getattr(args, "ask", False))
    wait_minutes: Optional[float] = getattr(args, "wait", None)

    if ask_mode and not (find_mode and apply_mode):
        sys.stderr.write("relink: --ask requires --find --apply.\n")
        return 2

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
        moves: List[Move] = []
        asked = False

        if not as_json:
            print_found(entries, show_apply_footer=not apply_mode)

        if apply_mode:
            moves = [
                (e.old, e.new)
                for e in entries
                if e.kind in ("journal", "same-name") and e.new is not None
            ]
            unsure = [
                (e.old, e.new)
                for e in entries
                if e.kind in ("name-only", "renamed") and e.new is not None
            ]
            asked = ask_mode and bool(unsure) and not as_json and _stdin_is_terminal()
            if asked:
                # Asked before --wait starts, so that it also waits for the agents the confirmed moves touch
                try:
                    moves += _confirm_moves(unsure)
                except KeyboardInterrupt:
                    sys.stderr.write("\nrelink: cancelled; no files were modified.\n")
                    return 130
            if wait_minutes is not None and not dry_run and moves:
                if not _wait_for_busy_agents(moves, root, wait_minutes):
                    return 3
            report = relink(moves, root=root, dry_run=dry_run)
            if moves and root is not None and not dry_run:
                try:
                    record_moves(root, moves, "relink")
                except OSError as err:
                    report.warnings.insert(0, f"Could not write move journal: {err}")

        if as_json:
            payload = {
                "entries": [e.to_dict() for e in entries],
                "relink": report.to_dict() if report is not None else None,
                "skipped_unmounted": dict(getattr(entries, "skipped_unmounted", {})),
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        elif report is not None and entries and not moves:
            # print_relink would say every path is up to date, under the stale folders listed above
            reason = "no folder is marked ✓ or confirmed" if asked else "no folder is marked ✓"
            print(f"  Nothing to relink: {reason}.")
        elif report is not None:
            print_relink(report, busy_hint_when_idle=False, relink_only=True)

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
        print_relink(report, busy_hint_when_idle=False, relink_only=True)

    return 1 if report.errors else 0


__all__ = ["cmd_relink", "print_found", "print_relink"]
