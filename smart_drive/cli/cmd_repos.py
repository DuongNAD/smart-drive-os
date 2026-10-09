"""smart_drive.cli.cmd_repos - `smart-drive repos`: duplicate git clones and portable-SSD mirrors."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

from smart_drive.core.repos import MACHINE, PORTABLE, RepoReport, build_report, default_scan_roots, git_version
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root

_PLACE = {PORTABLE: "portable SSD", MACHINE: "this machine"}


def format_report(report: RepoReport) -> str:
    """Human-readable report: duplicates with what to do, then mirrors, stale worktrees and errors."""
    lines: List[str] = [
        f"Git repositories: {report.clones} clone(s) and {report.worktrees} linked worktree(s)"
        f" under {', '.join(report.roots)}"
        + (f" ({report.submodules} submodule checkout(s) left to their parent repository)" if report.submodules else ""),
        "Portable SSD: " + (", ".join(report.portable_roots) or "not found, so every clone counts as this machine"),
        "",
        f"DUPLICATES: the same project twice in the same place ({len(report.duplicates)})",
    ]
    if not report.duplicates:
        lines.append("  none")
    for group in report.duplicates:
        lines.append(f"  {group.identity}  [{_PLACE[group.location]}]")
        for s in group.suggestions:
            lines.append(f"    {s.action:<8} {s.path}  [{s.branch}]")
            if s.action != "keep":
                lines.append(f"             {s.reason}")
            if s.command:
                lines.append(f"             $ {s.command}")

    in_sync = sum(1 for m in report.mirrors if m.in_sync)
    lines += ["", f"MIRRORS: on the portable SSD and on this machine ({len(report.mirrors)}, {in_sync} in sync)"]
    if not report.mirrors:
        lines.append("  none")
    for mirror in sorted(report.mirrors, key=lambda m: m.in_sync):
        lines.append(f"  {mirror.identity}  {'in sync' if mirror.in_sync else 'differs'}")
        if mirror.in_sync:
            continue
        for c in mirror.copies:
            hint = f" -> {', '.join(c['next'])}" if c["next"] else ""
            lines.append(f"    {c['location']:<8} {c['path']}  [{c['branch']}] {c['state']}{hint}")

    if report.stale_worktrees:
        by_repo: Dict[str, List[Dict[str, str]]] = {}
        for wt in report.stale_worktrees:
            by_repo.setdefault(wt["repository"], []).append(wt)
        lines += ["", f"STALE WORKTREES: registered but missing on disk ({len(report.stale_worktrees)})"]
        for repo, items in by_repo.items():
            lines.append(f"  {repo}: {len(items)} missing worktree(s), e.g. {items[0]['worktree']}")
            lines.append(f"    $ {items[0]['command']}")

    if report.unreadable:
        lines += ["", f"UNREADABLE ({len(report.unreadable)})"]
        for item in report.unreadable:
            lines.append(f"  {item['path']}: {item['error']}")
            if "dubious ownership" in item["error"]:
                lines.append(f'    $ git config --global --add safe.directory "{item["path"]}"')

    lines += ["", "Read-only report: nothing was fetched, changed or deleted."]
    return "\n".join(lines)


def cmd_repos(args: argparse.Namespace) -> int:
    """Handles the `repos` subcommand."""
    if not git_version():
        sys.stderr.write("git was not found on PATH; `smart-drive repos` needs it.\n")
        return 2

    explicit_root: Optional[str] = getattr(args, "root", None)
    portable_root: Optional[str] = None
    try:
        portable_root = resolve_drive_root(explicit_root).path
    except DriveRootNotFound as e:
        if explicit_root:
            sys.stderr.write(f"{e}\n")
            return 2

    portable = [p for p in [portable_root, *(getattr(args, "portable", None) or [])] if p]
    roots = list(getattr(args, "paths", None) or []) or default_scan_roots(portable_root)
    missing = [p for p in roots + portable if not os.path.isdir(p)]
    if missing:
        sys.stderr.write(f"Not a directory: {', '.join(missing)}\n")
        return 2

    report = build_report(roots, portable, max_depth=getattr(args, "max_depth", 12))
    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return 0


__all__ = ["cmd_repos", "format_report"]
