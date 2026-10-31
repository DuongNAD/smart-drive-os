"""smart_drive.cli.cmd_projects - Lifecycle Management & Sub-Zoning for 03_Development_Projects.

Provides commands:
- `smart-drive projects [--root <path>] [--json]`: Read-only table of projects, git status, WIP overload check.
- `smart-drive projects --organize [--apply] [--wait] [--json]`: Sub-zoning planner & executor.
- `smart-drive stage <project_name> <stage> [--apply] [--wait] [--json]`: Lifecycle stage transition.

Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from smart_drive.cli.cmd_organize import (
    BUSY_APPS_UNKNOWN_NOTE,
    busy_apps_of,
    get_default_db_path,
)
from smart_drive.cli.cmd_relink import _wait_for_busy_agents, print_relink
from smart_drive.core.auditor import format_bytes
from smart_drive.core.auto_zoner import _move
from smart_drive.core.project_classifier import (
    LIFECYCLE_ZONES,
    STAGE_NAME_TO_ZONE,
    ZONE_TO_STAGE_NAME,
    ProjectClassifier,
    ProjectZoneResult,
)
from smart_drive.core.relink import after_moves, relink, RelinkReport
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.system_refs import SystemRefIndex
from smart_drive.core.workspace_sync import WorkspaceSynchronizer
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager

WIP_LIMIT: int = 3


def _resolve_dev_projects_dir(root: str) -> str:
    """Finds or resolves the 03_Development_Projects directory."""
    base = os.path.basename(os.path.normpath(root))
    if base == "03_Development_Projects":
        return root
    cand = os.path.join(root, "03_Development_Projects")
    return cand


def _detect_coding_agent_references(project_path: str, root: str) -> Set[str]:
    """Detects coding agent configs (VS Code, Antigravity, Cursor, etc.) referencing project_path."""
    found: Set[str] = set()
    norm_path = os.path.normcase(os.path.abspath(project_path))
    norm_fwd = norm_path.replace("\\", "/")

    try:
        from smart_drive.core.relink import agent_files
        for af in agent_files(root=root):
            try:
                if not af.path.is_file():
                    continue
                content = af.path.read_text(encoding="utf-8", errors="ignore").lower()
                if norm_path in content or norm_fwd in content:
                    found.add(af.agent)
            except Exception:
                pass
    except Exception:
        pass

    return found


def _collect_references_display(
    res: ProjectZoneResult,
    root: str,
    agent_refs: Optional[Set[str]] = None,
) -> List[str]:
    """Collects concise labels of references (Antigravity, VS Code, MCP, .lnk, etc.)."""
    refs: Set[str] = set()
    if res.protected_by_mcp:
        refs.add("MCP")

    if res.references:
        for r in res.references:
            r_lower = r.lower()
            if "shortcut" in r_lower or ".lnk" in r_lower:
                refs.add(".lnk")
            elif "mcp" in r_lower:
                refs.add("MCP")
            elif "path" in r_lower:
                refs.add("PATH")
            elif "service" in r_lower:
                refs.add("Service")
            elif "app" in r_lower:
                refs.add("App")

    if agent_refs:
        refs.update(agent_refs)

    return sorted(refs)


def _format_git_status(status: Dict[str, Any]) -> str:
    """Formats git status into a compact string like 'clean', 'M:3', '↑1', or 'M:2 ↑1'."""
    if not status:
        return "-"
    if status.get("clean"):
        return "clean"

    parts: List[str] = []
    changes = status.get("changes", 0)
    if isinstance(changes, int) and changes > 0:
        parts.append(f"M:{changes}")

    unpushed = status.get("unpushed", 0)
    if isinstance(unpushed, int) and unpushed > 0:
        parts.append(f"↑{unpushed}")

    if parts:
        return " ".join(parts)
    if status.get("summary"):
        return str(status["summary"])
    return "clean"


def _format_last_active(age_days: float) -> str:
    """Formats age into human-readable recency string."""
    if age_days < 0.1:
        return "today"
    elif age_days < 1.0:
        return f"{age_days * 24:.0f}h ago"
    elif age_days < 30.0:
        return f"{age_days:.1f}d ago"
    else:
        return f"{age_days:.0f}d ago"


def _format_size_slack(nominal: int, slack: int) -> str:
    """Formats nominal size and cluster slack bytes."""
    return f"{format_bytes(nominal)} ({format_bytes(slack)})"


def _locate_project(dev_projects_dir: str, project_name: str) -> Optional[str]:
    """Searches for a project directory within 03_Development_Projects (flat or in subzones)."""
    if not os.path.isdir(dev_projects_dir):
        return None

    # 1. Flat under dev_projects_dir
    flat_cand = os.path.join(dev_projects_dir, project_name)
    if os.path.isdir(flat_cand):
        return flat_cand

    # 2. Inside any lifecycle zone container
    try:
        entries = os.scandir(dev_projects_dir)
    except (OSError, PermissionError):
        return None

    for entry in entries:
        if entry.is_dir() and ProjectClassifier.is_zone_container(entry.name):
            sub_cand = os.path.join(entry.path, project_name)
            if os.path.isdir(sub_cand):
                return sub_cand

    # 3. Case-insensitive fallback
    target_lower = project_name.lower()
    for root_dir, dirs, _ in os.walk(dev_projects_dir):
        # Don't recurse into project subdirectories
        rel = os.path.relpath(root_dir, dev_projects_dir)
        depth = len(rel.split(os.sep)) if rel != "." else 0
        if depth > 1:
            dirs.clear()
            continue
        for d in dirs:
            if d.lower() == target_lower and not ProjectClassifier.is_zone_container(d):
                return os.path.join(root_dir, d)

    return None


def _format_projects_table(
    projects_data: List[Dict[str, Any]],
    wip_count: int,
    wip_limit: int = WIP_LIMIT,
) -> str:
    """Renders human-readable summary table of development projects."""
    lines: List[str] = []

    if wip_count > wip_limit:
        lines.append("=" * 88)
        lines.append(f"⚠  WARNING: WIP OVERLOAD IN 01_Active_Focus! (Current WIP: {wip_count} > Limit: {wip_limit})")
        lines.append("   Solo developers should focus on at most 3 active projects at a time to maintain velocity.")
        lines.append("   Consider moving inactive projects to incubator or archive:")
        lines.append("     smart-drive stage <project_name> incubator --apply")
        lines.append("     smart-drive stage <project_name> archive --apply")
        lines.append("=" * 88)
        lines.append("")

    headers = [
        "Project",
        "Current Zone",
        "Suggested Zone",
        "Last Active",
        "Git Status",
        "Size (Slack)",
        "Referenced By",
    ]

    rows: List[List[str]] = []
    for p in projects_data:
        rows.append([
            p["project"],
            p["current_zone"] or "(flat)",
            p["suggested_zone"],
            p["last_active"],
            p["git_status"],
            p["size_slack"],
            p["referenced_by"],
        ])

    if not rows:
        lines.append("No development projects found in 03_Development_Projects.")
        return "\n".join(lines)

    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val)))

    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "  ".join("-" * col_widths[i] for i in range(len(headers)))
    lines.append(header_line)
    lines.append(sep_line)

    for row in rows:
        line = "  ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row))
        lines.append(line)

    lines.append("")
    lines.append(f"Total projects: {len(rows)} | Active Focus WIP: {wip_count}/{wip_limit}")
    return "\n".join(lines)


# ==============================================================================
# SUBCOMMAND HANDLERS
# ==============================================================================

def cmd_projects(args: argparse.Namespace) -> int:
    """Handles `smart-drive projects` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    dev_projects_dir = _resolve_dev_projects_dir(root)
    as_json = bool(getattr(args, "json", False))
    organize_mode = bool(getattr(args, "organize", False))
    apply_mode = bool(getattr(args, "apply", False))

    try:
        classifier = ProjectClassifier(root)
    except Exception:
        classifier = ProjectClassifier(root, system_refs=SystemRefIndex([]))

    results = classifier.scan_all_projects(dev_projects_dir)
    wip_count = classifier.get_wip_count(results)

    # --------------------------------------------------------------------------
    # Case 1: Read-only summary table / JSON (when --organize is NOT passed)
    # --------------------------------------------------------------------------
    if not organize_mode:
        projects_data: List[Dict[str, Any]] = []
        for r in results:
            agent_refs = _detect_coding_agent_references(r.project_path, root)
            ref_list = _collect_references_display(r, root, agent_refs)
            projects_data.append({
                "project": r.project_name,
                "project_name": r.project_name,
                "project_path": r.project_path,
                "current_zone": r.current_zone,
                "suggested_zone": r.suggested_zone,
                "last_active": _format_last_active(r.age_days),
                "last_activity_time": r.last_activity_time,
                "age_days": round(r.age_days, 1),
                "git_status": _format_git_status(r.git_status),
                "git_status_raw": r.git_status,
                "size_slack": _format_size_slack(r.nominal_size, r.slack_bytes),
                "nominal_size": r.nominal_size,
                "slack_bytes": r.slack_bytes,
                "referenced_by": ", ".join(ref_list) if ref_list else "-",
                "references": ref_list,
                "protected_by_mcp": r.protected_by_mcp,
                "protected_by_system": r.protected_by_system,
                "reason": r.reason,
            })

        if as_json:
            payload = {
                "status": "ok",
                "root": root,
                "dev_projects_dir": dev_projects_dir,
                "wip_count": wip_count,
                "wip_limit": WIP_LIMIT,
                "wip_overload": wip_count > WIP_LIMIT,
                "total_projects": len(projects_data),
                "projects": projects_data,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print(_format_projects_table(projects_data, wip_count, WIP_LIMIT))
        return 0

    # --------------------------------------------------------------------------
    # Case 2: Sub-zoning move planner & executor (--organize passed)
    # --------------------------------------------------------------------------
    planned_moves: List[Tuple[str, str, ProjectZoneResult]] = []
    pinned_projects: List[Dict[str, Any]] = []
    already_organized: List[ProjectZoneResult] = []

    for r in results:
        # Already in suggested zone?
        if r.current_zone == r.suggested_zone:
            already_organized.append(r)
            continue

        # Pinned by MCP or system protection?
        if r.protected_by_mcp or r.protected_by_system:
            pinned_reason = []
            if r.protected_by_mcp:
                pinned_reason.append("Active MCP server infrastructure (protected_by_mcp)")
            if r.protected_by_system:
                pinned_reason.append(
                    r.system_ref_summary or "Referenced by system/shortcuts (protected_by_system)"
                )
            pinned_projects.append({
                "project": r.project_name,
                "path": r.project_path,
                "current_zone": r.current_zone,
                "suggested_zone": r.suggested_zone,
                "reason": "; ".join(pinned_reason),
            })
            continue

        target_subzone = os.path.join(dev_projects_dir, r.suggested_zone)
        dest_path = os.path.join(target_subzone, r.project_name)
        if os.path.normcase(os.path.abspath(r.project_path)) == os.path.normcase(os.path.abspath(dest_path)):
            already_organized.append(r)
            continue

        planned_moves.append((r.project_path, dest_path, r))

    move_pairs = [(src, dest) for src, dest, _ in planned_moves]

    # Dry-run mode (default when --apply is False): writes ZERO bytes
    if not apply_mode:
        preview = relink(move_pairs, root=root, dry_run=True) if move_pairs else None
        busy_apps, busy_apps_unknown = busy_apps_of(preview)

        if as_json:
            payload = {
                "status": "dry_run",
                "root": root,
                "dev_projects_dir": dev_projects_dir,
                "wip_count": wip_count,
                "wip_limit": WIP_LIMIT,
                "planned_moves": [
                    {
                        "project": r.project_name,
                        "src": src,
                        "dest": dest,
                        "current_zone": r.current_zone,
                        "target_zone": r.suggested_zone,
                        "reason": r.reason,
                        "nominal_size": r.nominal_size,
                        "slack_bytes": r.slack_bytes,
                    }
                    for src, dest, r in planned_moves
                ],
                "pinned_projects": pinned_projects,
                "already_organized_count": len(already_organized),
                "relink_preview": preview.to_dict() if preview else None,
                "busy_apps": busy_apps,
                "busy_apps_unknown": busy_apps_unknown,
                "note": "Use --apply to execute relocation.",
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print("=== Project Sub-Zoning Plan (Dry-Run Preview) ===")
        print(f"Development Projects Root: {dev_projects_dir}")
        if pinned_projects:
            print("\nPinned projects (skipped to preserve running services and system references):")
            for pin in pinned_projects:
                print(f"  [PINNED] {pin['project']} (current: {pin['current_zone'] or '(flat)'})")
                print(f"           Reason: {pin['reason']}")

        if planned_moves:
            print("\nPlanned relocations:")
            for src, dest, r in planned_moves:
                print(f"  -> [DIR] {r.project_name}")
                print(f"     From: {r.current_zone or '(flat)'} -> Target: {r.suggested_zone}")
                print(f"     Reason: {r.reason}")
                print(f"     Destination: {dest}")
            print(f"\nTotal planned relocations: {len(planned_moves)} projects.")

            if preview and (preview.changed or preview.warnings or preview.errors or preview.busy_apps):
                print("\nCoding agents that will follow these moves:")
                print_relink(preview)
            if busy_apps_unknown:
                print(BUSY_APPS_UNKNOWN_NOTE)
            print("\nRun with --apply to execute the relocation safely.")
        else:
            print("\n  ✓ No unorganized projects found. All projects are in their canonical zones!")

        return 0

    # Apply mode: execute moves and relink
    wait_minutes = getattr(args, "wait", None)
    if wait_minutes is not None and move_pairs:
        if not _wait_for_busy_agents(move_pairs, root, float(wait_minutes)):
            return 3

    moved_moves: List[Tuple[str, str]] = []
    errors: List[Dict[str, Any]] = []
    fsmonitor_stopped: List[str] = []

    for src, dest, _ in planned_moves:
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            stopped = _move(src, dest)
            if stopped:
                fsmonitor_stopped.extend(stopped)
            moved_moves.append((src, dest))
        except Exception as err:
            errors.append({
                "src": src,
                "dest": dest,
                "error": str(err),
            })

    relinked: Optional[RelinkReport] = None
    ws_sync_report: Optional[Dict[str, Any]] = None
    if moved_moves:
        relinked = after_moves(root, moved_moves, tool="projects")
        try:
            ws_sync_report = WorkspaceSynchronizer(root).sync_all(moved_moves)
        except Exception:
            ws_sync_report = None

    # Update SQLite search index if database exists
    db_path = os.path.abspath(get_default_db_path(root))
    inc_res = None
    if os.path.exists(db_path):
        try:
            db = DatabaseManager(db_path)
            try:
                mgr = IndexManager(db, root)
                inc = mgr.incremental_update()
                inc_res = dataclasses.asdict(inc)
            finally:
                db.close()
        except Exception:
            pass

    if as_json:
        payload = {
            "status": "applied",
            "root": root,
            "dev_projects_dir": dev_projects_dir,
            "moved_count": len(moved_moves),
            "moves": [{"src": s, "dest": d} for s, d in moved_moves],
            "errors": errors,
            "fsmonitor_stopped": fsmonitor_stopped,
            "pinned_projects": pinned_projects,
            "relink": relinked.to_dict() if relinked else None,
            "workspace_sync": ws_sync_report,
            "index_update": inc_res,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("✓ Project sub-zoning applied successfully:")
    print(f"  - Moved projects: {len(moved_moves)}")
    for s, d in moved_moves:
        print(f"    * {os.path.basename(s)} -> {os.path.basename(os.path.dirname(d))}/{os.path.basename(d)}")
    for repo in fsmonitor_stopped:
        print(f"  - Stopped git fsmonitor daemon in: {repo}")
    for err in errors:
        print(f"  ✗ Could not move {os.path.basename(err['src'])}: {err['error']}")
    if ws_sync_report and ws_sync_report.get("updated_count", 0) > 0:
        print(f"  - Workspaces updated: {ws_sync_report['updated_count']}")
    if ws_sync_report and ws_sync_report.get("roadmap_kanban"):
        print("  - ROADMAP_KANBAN.md updated")
    if inc_res:
        print(f"  - Index updated: +{inc_res['added']} ~{inc_res['modified']} -{inc_res['deleted']}")
    if relinked and (relinked.changed or relinked.warnings or relinked.errors):
        print("Coding agents updated:")
        print_relink(relinked)
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    """Handles `smart-drive stage <project_name> <stage>` subcommand."""
    project_name = getattr(args, "project_name", None)
    stage_input = getattr(args, "stage", None)
    apply_mode = bool(getattr(args, "apply", False))
    dry_run = bool(getattr(args, "dry_run", False))
    as_json = bool(getattr(args, "json", False))

    if not project_name:
        sys.stderr.write("Error: Missing project_name argument\n")
        return 1

    if not stage_input:
        sys.stderr.write("Error: Missing stage argument\n")
        return 1

    stage_key = str(stage_input).strip().lower()
    if stage_key not in STAGE_NAME_TO_ZONE:
        sys.stderr.write(
            f"Error: Invalid stage '{stage_input}'. Valid stages: active, incubator, mcp, hackathon, archive\n"
        )
        return 1

    target_zone = STAGE_NAME_TO_ZONE[stage_key]
    canonical_stage = ZONE_TO_STAGE_NAME.get(target_zone, stage_key)

    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    dev_projects_dir = _resolve_dev_projects_dir(root)
    old_path = _locate_project(dev_projects_dir, project_name)
    if not old_path or not os.path.isdir(old_path):
        sys.stderr.write(f"Error: Project '{project_name}' not found in 03_Development_Projects\n")
        return 1

    target_zone_dir = os.path.join(dev_projects_dir, target_zone)
    new_path = os.path.join(target_zone_dir, os.path.basename(old_path))
    paths_differ = os.path.normcase(os.path.abspath(old_path)) != os.path.normcase(os.path.abspath(new_path))

    # --------------------------------------------------------------------------
    # Dry-run mode: writes zero bytes
    # --------------------------------------------------------------------------
    if dry_run:
        preview = relink([(old_path, new_path)], root=root, dry_run=True) if paths_differ else None
        if as_json:
            payload = {
                "status": "dry_run",
                "project": project_name,
                "stage": canonical_stage,
                "target_zone": target_zone,
                "old_path": old_path,
                "new_path": new_path,
                "marker_written": False,
                "moved": False,
                "relink_preview": preview.to_dict() if preview else None,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print(f"Project '{project_name}' would be staged to '{canonical_stage}' ({target_zone})")
        print(f"  Target path: {new_path}")
        print("  Marker to write: .smart_drive_stage")
        if preview and (preview.changed or preview.warnings or preview.errors):
            print("Coding agents that will follow this move:")
            print_relink(preview)
        return 0

    # --------------------------------------------------------------------------
    # Apply mode: write marker, move folder, relink
    # --------------------------------------------------------------------------
    if apply_mode:
        wait_minutes = getattr(args, "wait", None)
        if wait_minutes is not None and paths_differ:
            if not _wait_for_busy_agents([(old_path, new_path)], root, float(wait_minutes)):
                return 3

        # Write .smart_drive_stage inside project
        marker_path = os.path.join(old_path, ".smart_drive_stage")
        try:
            with open(marker_path, "w", encoding="utf-8") as f:
                f.write(f"{canonical_stage}\n")
        except OSError as e:
            sys.stderr.write(f"Error writing stage marker: {e}\n")
            return 1

        relinked: Optional[RelinkReport] = None
        ws_sync_report: Optional[Dict[str, Any]] = None
        if paths_differ:
            os.makedirs(target_zone_dir, exist_ok=True)
            _move(old_path, new_path)
            relinked = after_moves(root, [(old_path, new_path)], tool="stage")
            try:
                ws_sync_report = WorkspaceSynchronizer(root).sync_all([(old_path, new_path)])
            except Exception:
                ws_sync_report = None

            # Update search index if DB exists
            db_path = os.path.abspath(get_default_db_path(root))
            if os.path.exists(db_path):
                try:
                    db = DatabaseManager(db_path)
                    try:
                        mgr = IndexManager(db, root)
                        mgr.incremental_update()
                    finally:
                        db.close()
                except Exception:
                    pass

        final_path = new_path if paths_differ else old_path
        if as_json:
            payload = {
                "status": "applied",
                "project": project_name,
                "stage": canonical_stage,
                "target_zone": target_zone,
                "old_path": old_path,
                "new_path": final_path,
                "marker_written": True,
                "moved": paths_differ,
                "relink": relinked.to_dict() if relinked else None,
                "workspace_sync": ws_sync_report,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print(f"✓ Project '{project_name}' staged to '{canonical_stage}' ({target_zone})")
        if paths_differ:
            print(f"  Moved: {old_path} -> {final_path}")
        else:
            print(f"  Updated marker at: {os.path.join(final_path, '.smart_drive_stage')}")
        if ws_sync_report and ws_sync_report.get("updated_count", 0) > 0:
            print(f"  - Workspaces updated: {ws_sync_report['updated_count']}")
        if ws_sync_report and ws_sync_report.get("roadmap_kanban"):
            print("  - ROADMAP_KANBAN.md updated")
        if relinked and (relinked.changed or relinked.warnings or relinked.errors):
            print("Coding agents updated:")
            print_relink(relinked)
        return 0

    # --------------------------------------------------------------------------
    # Marking mode (when neither --apply nor --dry-run is given)
    # --------------------------------------------------------------------------
    marker_path = os.path.join(old_path, ".smart_drive_stage")
    try:
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write(f"{canonical_stage}\n")
    except OSError as e:
        sys.stderr.write(f"Error writing stage marker: {e}\n")
        return 1

    if as_json:
        payload = {
            "status": "marked",
            "project": project_name,
            "stage": canonical_stage,
            "target_zone": target_zone,
            "old_path": old_path,
            "new_path": new_path,
            "marker_written": True,
            "moved": False,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"✓ Project '{project_name}' stage marked as '{canonical_stage}' ({target_zone})")
    print(f"  Stage marker written: {marker_path}")
    if paths_differ:
        print(f"  Run with --apply to move folder to {new_path}")
    return 0
