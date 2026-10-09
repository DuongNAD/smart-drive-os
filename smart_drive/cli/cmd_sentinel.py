"""smart_drive.cli.cmd_sentinel - 1-Touch SSD Health Audit, Shield Self-Healing & Git Sentinel."""

from __future__ import annotations

import argparse
import json
import os
import sys

from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.sentinel import SentinelEngine


def _use_color() -> bool:
    """ANSI colours only for a real terminal that has not opted out (https://no-color.org)."""
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def format_ansi_report(report: dict, color: bool = True) -> str:
    """Renders human-readable terminal output from health report dictionary (ANSI colours if ``color``)."""
    C_CYAN = "\033[1;36m" if color else ""
    C_GREEN = "\033[1;32m" if color else ""
    C_YELLOW = "\033[1;33m" if color else ""
    C_RED = "\033[1;31m" if color else ""
    C_RESET = "\033[0m" if color else ""

    lines = []
    lines.append(f"\n{C_CYAN}======================================================================{C_RESET}")
    lines.append(f"{C_CYAN}            SMARTDRIVE-OS SSD HEALTH & SELF-HEALING AUDIT             {C_RESET}")
    lines.append(f"{C_CYAN}======================================================================{C_RESET}")

    root = report.get("drive_root", "unknown")
    mount = report.get("mount", {})
    fs_name = mount.get("filesystem", "unknown")
    unit = mount.get("allocation_unit_bytes") or 0
    unit_text = f", {unit // 1024} KB allocation unit" if unit >= 1024 and unit % 1024 == 0 else ""
    lines.append(f"[{C_GREEN}✓{C_RESET}] Drive Mount & Geometry:")
    lines.append(f"    - Root: {root} ({fs_name}{unit_text})")
    if mount.get("slack_model_note"):
        lines.append(f"    - Note: {mount['slack_model_note']}")

    shields_info = report.get("anti_indexing_shields", {})
    if shields_info.get("all_healthy"):
        heal_msg = " (auto-healed)" if shields_info.get("auto_healed") else ""
        lines.append(f"[{C_GREEN}✓{C_RESET}] Anti-Indexing Shields: Active{heal_msg}")
        shields_dict = shields_info.get("shields", {})
        s_meta = shields_dict.get(".metadata_never_index", {}).get("active", False)
        s_fsevents = shields_dict.get(".fseventsd/no_log", {}).get("active", False)
        lines.append(f"    - .metadata_never_index: {'Active' if s_meta else 'Missing'}")
        lines.append(f"    - .fseventsd/no_log: {'Active' if s_fsevents else 'Missing'}")
    else:
        lines.append(f"[{C_RED}✗{C_RESET}] Anti-Indexing Shields: Incomplete")

    db_info = report.get("database", {})
    if db_info.get("exists"):
        db_state = f"{C_GREEN}✓ Ready{C_RESET}" if db_info.get("healthy") else f"{C_RED}✗ Corrupted{C_RESET}"
        lines.append(f"[{db_state}] FTS5 Search Database:")
        lines.append(f"    - Path: {db_info.get('path')}")
        lines.append(f"    - Integrity (PRAGMA quick_check): {db_info.get('quick_check')}")
        lines.append(f"    - Indexed Records: {db_info.get('file_count', 0):,} files ({db_info.get('size_bytes', 0) / (1024*1024):.2f} MB)")
    else:
        lines.append(f"[{C_YELLOW}!{C_RESET}] FTS5 Search Database: Not initialized (run 'index' to build)")

    safety_info = report.get("exfat_safety", {})
    if safety_info.get("is_safe"):
        lines.append(f"[{C_GREEN}✓{C_RESET}] exFAT Safety & Geometry:")
        lines.append("    - Forbidden Characters: None (Clean)")
        lines.append("    - Symlinks: None (Clean)")
    else:
        lines.append(f"[{C_RED}✗{C_RESET}] exFAT Safety Violations:")
        lines.append(f"    - Forbidden Characters: {safety_info.get('forbidden_char_violations')}")
        lines.append(f"    - Symlinks: {safety_info.get('symlinks_found')}")

    git_info = report.get("git_status", {})
    repos_checked = git_info.get("repos_checked", 0)
    if repos_checked > 0:
        if git_info.get("dirty_repos") == 0 and git_info.get("unpushed_repos") == 0:
            lines.append(f"[{C_GREEN}✓{C_RESET}] Git Repositories ({repos_checked} checked): All clean and synchronized!")
        else:
            lines.append(f"[{C_YELLOW}!{C_RESET}] Git Repositories ({repos_checked} checked):")
            lines.append(f"    - Clean: {git_info.get('clean_repos')}, Dirty: {git_info.get('dirty_repos')}, Unpushed: {git_info.get('unpushed_repos')}")
            for r in git_info.get("details", []):
                if r.get("is_dirty") or r.get("ahead", 0) > 0:
                    status_parts = []
                    if r.get("is_dirty"):
                        status_parts.append(f"Dirty ({r.get('uncommitted_count')} files)")
                    if r.get("ahead", 0) > 0:
                        status_parts.append(f"{r.get('ahead')} to push")
                    lines.append(f"      * {r.get('path')} [{r.get('branch')}]: {', '.join(status_parts)}")
    else:
        lines.append(f"[{C_GREEN}✓{C_RESET}] Git Repositories: 0 checked in root scope")

    lines.append(f"{C_CYAN}======================================================================{C_RESET}")
    is_healthy = report.get("is_healthy", False)
    if is_healthy:
        h_note = " (Shields Auto-Healed)" if shields_info.get("auto_healed") else ""
        lines.append(f"{C_GREEN}Overall Health: HEALTHY{h_note} (Exit Code: 0){C_RESET}")
    else:
        lines.append(f"{C_RED}Overall Health: UNHEALTHY (Issues detected){C_RESET}")
    lines.append(f"{C_CYAN}======================================================================{C_RESET}\n")

    return "\n".join(lines)


def cmd_sentinel(args: argparse.Namespace) -> int:
    """Handles the `sentinel` / `agent-check` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    auto_heal = getattr(args, "auto_heal", True)

    engine = SentinelEngine(root)
    report = engine.run_health_check(root, auto_heal=auto_heal)

    if getattr(args, "json", False):
        print(json.dumps(dict(report), indent=2, ensure_ascii=False))
        return 0 if report.is_healthy else 1

    print(format_ansi_report(report, color=_use_color()))
    return 0 if report.is_healthy else 1
