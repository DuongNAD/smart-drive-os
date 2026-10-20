"""smart_drive.cli.cmd_paths - `smart-drive paths --broken`: find and fix stale shortcuts, IDE entries, PATH & agent configs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from smart_drive.cli.cmd_relink import print_relink
from smart_drive.core.relink import (
    Move,
    MovedFolder,
     PathMapper,
    RelinkReport,
    _CASE_INSENSITIVE,
    _all_move_batches,
    _exists,
    _is_dir,
    _is_under,
    _join_split,
    _key,
    _same_name_folders,
    _split,
    _unmounted_root,
    find_moved,
    record_moves,
    relink,
)
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex


def _locate_missing_target(
    target: str,
    batches: Sequence[Sequence[Move]],
) -> Optional[Dict[str, Any]]:
    """Checks if `target` is missing on a mounted volume and locates where its top missing folder went.

    Returns None if `target` exists or is not a valid local path, a dict with `{"unmounted": root}`
    if its volume is disconnected, or a broken-target resolution dictionary whose `kind` is one of:
    journal (a recorded move), same-name (the one same-named folder that also holds what lay below the
    missing one), name-only (the one same-named folder, with nothing below the missing one to check: a guess),
    ambiguous (several candidates) or not-found.
    """
    parsed = _split(target)
    if parsed is None:
        return None
    anchor, names = parsed
    if not names:
        return None
    if sys.platform == "win32" and anchor == "/":
        return None

    unmounted = _unmounted_root(anchor, names)
    if unmounted is not None:
        return {"unmounted": unmounted}

    full_path = _join_split(anchor, names)
    if _exists(Path(full_path)):
        return None
    if anchor == "/" and not _exists(Path("/" + names[0])):
        return None

    k = len(names) - 1
    while k > 0 and not _exists(Path(_join_split(anchor, names[:k]))):
        k -= 1

    m_names = names[: k + 1]
    m_key = _key(anchor, m_names)
    m_str = _join_split(anchor, m_names)
    a_path = Path(_join_split(anchor, names[:k]))
    rel_tail = names[k + 1 :]

    resolved_new: Optional[str] = None
    resolved_kind: Optional[str] = None
    candidates: List[str] = []

    # 1. Check move journal
    for batch_moves in batches:
        matched_old: Optional[Tuple[str, List[str]]] = None
        for old_p, _ in batch_moves:
            old_parsed = _split(old_p)
            if old_parsed and old_parsed[1] and _is_under(m_key, _key(*old_parsed)):
                matched_old = old_parsed
                break
        if matched_old is not None:
            old_anchor, old_names = matched_old
            m_for_map = _join_split(old_anchor, list(old_names) + list(m_names[len(old_names) :]))
            mapped = PathMapper(batch_moves).map(m_for_map)
            if mapped and _exists(Path(mapped)):
                resolved_new = mapped
                resolved_kind = "journal"
            break

    # 2. Search A/*/<name> and A/*/*/<name>
    if resolved_kind is None:
        same_all = _same_name_folders(a_path, m_names)

        if rel_tail:
            same_passing = [
                str(c)
                for c in same_all
                if _exists(c.joinpath(*rel_tail))
                or (len(rel_tail) > 1 and _is_dir(c.joinpath(*rel_tail[:-1])))
            ]
        else:
            same_passing = [str(c) for c in same_all]

        if len(same_passing) == 1:
            resolved_new = same_passing[0]
            # A name alone proves nothing (the old folder may be deleted and this one another program's): only a
            # folder that also holds what lay below the old one is sure, as in find_moved
            resolved_kind = "same-name" if rel_tail else "name-only"
        elif same_all:
            resolved_kind = "ambiguous"
            candidates = [str(c) for c in same_all]

    if resolved_kind is None:
        resolved_kind = "not-found"

    suggested_target: Optional[str] = None
    if resolved_new is not None:
        new_parsed = _split(resolved_new)
        if new_parsed is not None:
            suggested_target = _join_split(new_parsed[0], list(new_parsed[1]) + list(rel_tail))
        else:
            suggested_target = str(Path(resolved_new).joinpath(*rel_tail)) if rel_tail else resolved_new

    return {
        "target": full_path,
        "missing_folder": m_str,
        "suggested_folder": resolved_new,
        "suggested_target": suggested_target,
        "kind": resolved_kind,
        "candidates": candidates,
    }


def _drive_membership(root: Optional[str]) -> Callable[[str], bool]:
    """Returns a test for "this path is the drive root or lies inside it" (spelled and cased as relink compares)."""
    parsed_root = _split(os.path.abspath(root)) if root else None
    if parsed_root is None:
        return lambda path: True
    root_key = _key(*parsed_root)

    def on_drive(path: str) -> bool:
        parsed = _split(path)
        return parsed is not None and _is_under(_key(*parsed), root_key)

    return on_drive


def inspect_broken_paths(
    root: Optional[str] = None,
    home: Optional[Path] = None,
    app_data: Optional[Path] = None,
    reader: Optional[Union[RegistryReader, Mapping[str, Any]]] = None,
    skip_dirs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Scans IDE/agent configs, Windows shortcuts (.lnk), and PATH entries for broken paths."""
    ide_entries: List[MovedFolder] = list(
        find_moved(root=root, home=home, app_data=app_data, skip_dirs=skip_dirs)
    )
    skipped_unmounted: Dict[str, int] = dict(getattr(ide_entries, "skipped_unmounted", {}) or {})

    batches = _all_move_batches(root)
    ref_index = SystemRefIndex.collect(home=home, reader=reader)

    broken_shortcuts: List[Dict[str, Any]] = []
    broken_path_entries: List[Dict[str, Any]] = []
    seen_shortcuts = set()
    seen_paths = set()

    for ref in ref_index.refs:
        if ref.kind not in ("shortcut", "path"):
            continue
        info = _locate_missing_target(ref.target, batches)
        if info is None:
            continue
        if "unmounted" in info:
            vol = info["unmounted"]
            skipped_unmounted[vol] = skipped_unmounted.get(vol, 0) + 1
            continue

        if ref.kind == "shortcut":
            dedup_key = ref.source.casefold() if _CASE_INSENSITIVE else ref.source
            if dedup_key in seen_shortcuts:
                continue
            seen_shortcuts.add(dedup_key)
            if info["kind"] in ("journal", "same-name") and info["suggested_target"]:
                suggestion = f"Can be repointed to {info['suggested_target']} with `smart-drive paths --broken --fix`"
            elif info["kind"] == "name-only":
                missing, found = info["missing_folder"], info["suggested_folder"]
                suggestion = (
                    "Only the folder name matches; if you moved it there, run: "
                    f'smart-drive relink "{missing}" "{found}"'
                )
            elif info["kind"] == "ambiguous":
                suggestion = f"Multiple candidates found ({', '.join(info['candidates'])}); update or delete shortcut manually"
            else:
                suggestion = "Orphan shortcut: target no longer exists; delete or update the .lnk file manually"
            broken_shortcuts.append({
                "shortcut": ref.source,
                "target": info["target"],
                "missing_folder": info["missing_folder"],
                "suggested_folder": info["suggested_folder"],
                "suggested_target": info["suggested_target"],
                "kind": info["kind"],
                "candidates": info["candidates"],
                "suggestion": suggestion,
            })
        elif ref.kind == "path":
            dedup_key = (
                ref.source,
                info["target"].casefold() if _CASE_INSENSITIVE else info["target"],
            )
            if dedup_key in seen_paths:
                continue
            seen_paths.add(dedup_key)
            if info["kind"] == "name-only":
                suggestion = (
                    "Only the folder name matches; if you moved it there, update the "
                    f"{ref.source} entry to {info['suggested_target']}, otherwise remove it"
                )
            elif info["suggested_target"]:
                suggestion = f"Update {ref.source} entry to {info['suggested_target']}"
            else:
                suggestion = f"Remove stale directory {info['target']} from {ref.source}"
            broken_path_entries.append({
                "source": ref.source,
                "target": info["target"],
                "missing_folder": info["missing_folder"],
                "suggested_folder": info["suggested_folder"],
                "suggested_target": info["suggested_target"],
                "kind": info["kind"],
                "candidates": info["candidates"],
                "suggestion": suggestion,
            })

    # Collect unique sure moves across IDE/agents and shortcuts (a name-only match is a guess: --fix never relinks it)
    sure_moves: List[Move] = []
    seen_moves = set()
    for e in ide_entries:
        if e.kind in ("journal", "same-name") and e.new is not None:
            mk = (
                e.old.casefold() if _CASE_INSENSITIVE else e.old,
                e.new.casefold() if _CASE_INSENSITIVE else e.new,
            )
            if mk not in seen_moves:
                seen_moves.add(mk)
                sure_moves.append((e.old, e.new))

    for sc in broken_shortcuts:
        if sc["kind"] in ("journal", "same-name") and sc["suggested_folder"] is not None:
            old_f = sc["missing_folder"]
            new_f = sc["suggested_folder"]
            mk = (
                old_f.casefold() if _CASE_INSENSITIVE else old_f,
                new_f.casefold() if _CASE_INSENSITIVE else new_f,
            )
            if mk not in seen_moves:
                seen_moves.add(mk)
                sure_moves.append((old_f, new_f))

    fixable_ide = sum(1 for e in ide_entries if e.kind in ("journal", "same-name") and e.new is not None)
    fixable_shortcuts = sum(
        1 for s in broken_shortcuts if s["kind"] in ("journal", "same-name") and s["suggested_folder"] is not None
    )
    total_broken = len(ide_entries) + len(broken_shortcuts) + len(broken_path_entries)

    # Which of them involve this drive: a stale reference to C:\Users\x\old-project is the computer's business,
    # and nothing done on the drive can repair it. (Without a root there is no way to tell: all count.)
    on_drive = _drive_membership(root)
    ide_on_drive = [on_drive(e.old) for e in ide_entries]
    shortcut_on_drive = [on_drive(s["missing_folder"]) or on_drive(s["shortcut"]) for s in broken_shortcuts]
    path_on_drive = [on_drive(p["missing_folder"]) for p in broken_path_entries]
    fixable_on_drive = sum(
        1 for e, here in zip(ide_entries, ide_on_drive)
        if here and e.kind in ("journal", "same-name") and e.new is not None
    ) + sum(
        1 for s, here in zip(broken_shortcuts, shortcut_on_drive)
        if here and s["kind"] in ("journal", "same-name") and s["suggested_folder"] is not None
    )

    return {
        "root": root,
        "total_broken": total_broken,
        "fixable_count": fixable_ide + fixable_shortcuts,
        "on_drive_count": sum(ide_on_drive) + sum(shortcut_on_drive) + sum(path_on_drive),
        "fixable_on_drive_count": fixable_on_drive,
        "ide_and_agents": [e.to_dict() for e in ide_entries],
        "shortcuts": broken_shortcuts,
        "path_entries": broken_path_entries,
        "sure_moves": [[old_p, new_p] for old_p, new_p in sure_moves],
        "skipped_unmounted": {k: skipped_unmounted[k] for k in sorted(skipped_unmounted)},
    }


def _print_broken_report(data: Dict[str, Any], show_fix_hint: bool = True) -> None:
    ide_list = data["ide_and_agents"]
    shortcuts = data["shortcuts"]
    path_entries = data["path_entries"]
    skipped_unmounted = data.get("skipped_unmounted") or {}

    if data["total_broken"] == 0:
        print("✓ No broken paths detected: every IDE entry, coding agent config, shortcut, and PATH directory exists.")
    else:
        print(f"=== Broken Path Doctor ({data['total_broken']} stale item(s) found) ===")

        if ide_list:
            print("\nIDE & Coding Agent Entries:")
            for entry in ide_list:
                agents_str = ", ".join(entry.get("agents") or [])
                kind = entry.get("kind")
                old = entry.get("old")
                new = entry.get("new")
                if kind in ("journal", "same-name"):
                    print(f"  ✓ {old} -> {new}  ({kind}; {agents_str})")
                elif kind == "renamed":
                    print(f'  ? {old} -> {new}  (renamed? check it, then run: smart-drive relink "{old}" "{new}")')
                elif kind == "name-only":
                    print(
                        f"  ? {old} -> maybe moved to {new}  ({agents_str}; only the name matches; "
                        f'if you moved it there, run: smart-drive relink "{old}" "{new}")'
                    )
                elif kind == "ambiguous":
                    cands = ", ".join(entry.get("candidates") or [])
                    print(f"  ? {old}: several candidates: {cands} ({agents_str})")
                elif kind == "unreadable":
                    print(f"  ✗ {old}: not an absolute path ({agents_str})")
                else:
                    print(f"  ✗ {old}: not found ({agents_str})")

        if shortcuts:
            print("\nWindows Shortcuts (.lnk):")
            for sc in shortcuts:
                kind = sc["kind"]
                lnk = sc["shortcut"]
                target = sc["target"]
                if kind in ("journal", "same-name") and sc["suggested_target"]:
                    print(f"  ✓ {lnk}: {target} -> {sc['suggested_target']}  ({kind})")
                elif kind == "name-only":
                    print(f"  ? {lnk}: {target} -> maybe moved to {sc['suggested_target']}  ({sc['suggestion']})")
                elif kind == "ambiguous":
                    cands = ", ".join(sc["candidates"])
                    print(f"  ? {lnk}: {target} (several candidates: {cands})")
                else:
                    print(f"  ✗ {lnk}: {target} (orphan shortcut — {sc['suggestion']})")

        if path_entries:
            print("\nSystem / User PATH Entries:")
            for pe in path_entries:
                if pe["suggested_target"]:
                    moved = "maybe moved to" if pe["kind"] == "name-only" else "moved to"
                    print(f"  ? [{pe['source']}] {pe['target']} -> {moved} {pe['suggested_target']} ({pe['suggestion']})")
                else:
                    print(f"  ✗ [{pe['source']}] {pe['target']} (missing — {pe['suggestion']})")

        if data["fixable_count"] > 0 and show_fix_hint:
            print("\nRun `smart-drive paths --broken --fix` to automatically repair the ✓ items.")

    if skipped_unmounted:
        total_skipped = sum(skipped_unmounted.values())
        drives_str = ", ".join(skipped_unmounted.keys())
        print(f"Skipped {total_skipped} path(s) on drives that are not connected: {drives_str}")


def cmd_paths(args: argparse.Namespace) -> int:
    """Handles the `paths` subcommand (`smart-drive paths --broken [--fix] [--dry-run] [--json]`)."""
    explicit_root: Optional[str] = getattr(args, "root", None)
    dry_run: bool = bool(getattr(args, "dry_run", False))
    fix_mode: bool = bool(getattr(args, "fix", False))
    as_json: bool = bool(getattr(args, "json", False))

    try:
        root = resolve_drive_root(explicit_root).path
    except DriveRootNotFound as err:
        if explicit_root:
            sys.stderr.write(f"{err}\n")
            return 2
        root = None

    report_data = inspect_broken_paths(root=root)
    relink_report: Optional[RelinkReport] = None

    if not as_json:
        _print_broken_report(report_data, show_fix_hint=not fix_mode)

    if fix_mode:
        sure_moves: List[Move] = [(pair[0], pair[1]) for pair in report_data["sure_moves"]]
        relink_report = relink(sure_moves, root=root, dry_run=dry_run)
        if sure_moves and root is not None and not dry_run:
            try:
                record_moves(root, sure_moves, "paths")
            except OSError as err:
                relink_report.warnings.insert(0, f"Could not write move journal: {err}")

    if as_json:
        payload = dict(report_data)
        payload["relink"] = relink_report.to_dict() if relink_report is not None else None
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif relink_report is not None:
        header = "\nPath repairs (dry-run preview):" if dry_run else "\nPath repairs applied:"
        print(header)
        print_relink(relink_report)

    return 1 if (relink_report is not None and relink_report.errors) else 0


__all__ = ["cmd_paths", "inspect_broken_paths"]
