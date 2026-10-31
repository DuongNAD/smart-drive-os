"""smart_drive.core.workspace_sync - VS Code Workspace & Kanban Roadmaps Synchronizer.

Synchronizes project references in `*.code-workspace` files and `ROADMAP_KANBAN.md`
when projects inside `03_Development_Projects` are moved across lifecycle subzones.

Preserves path formatting style (relative vs absolute, forward vs back slashes),
and creates safety backups (`.bak.smart-drive`) before modifying any files.

Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple


PRUNE_DIRS = frozenset({
    ".git",
    "node_modules",
    "site-packages",
    "__pycache__",
    "target",
    ".venv",
    "venv",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    "$recycle.bin",
    "system volume information",
    ".smart_drive",
})


def _detect_indent(text: str) -> int:
    """Detects indentation in a JSON string (default 2 spaces)."""
    for line in text.splitlines():
        if line.startswith("  ") and not line.startswith("    ") and line.strip().startswith('"'):
            return 2
        if line.startswith("    ") and line.strip().startswith('"'):
            return 4
        if line.startswith("\t") and line.strip().startswith('"'):
            return 2
    return 2


def _detect_newline(text: str) -> str:
    """Detects newline convention (CRLF vs LF)."""
    return "\r\n" if "\r\n" in text else "\n"


class WorkspaceSynchronizer:
    """Synchronizes VS Code workspace files and Kanban roadmap references upon folder moves."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        base = os.path.basename(os.path.normpath(self.root))
        if base == "03_Development_Projects":
            self.dev_projects_dir = self.root
        else:
            cand = os.path.join(self.root, "03_Development_Projects")
            self.dev_projects_dir = cand if os.path.isdir(cand) else cand

    def _discover_workspace_files(self) -> List[str]:
        """Discovers all *.code-workspace files under 03_Development_Projects and root."""
        found: List[str] = []
        seen: Set[str] = set()

        def _add_if_new(path: str) -> None:
            norm = os.path.normcase(os.path.abspath(path))
            if norm not in seen and os.path.isfile(path):
                seen.add(norm)
                found.append(path)

        # 1. Check directly at root if different from dev_projects_dir
        if os.path.isdir(self.root) and self.root != self.dev_projects_dir:
            try:
                for entry in os.scandir(self.root):
                    if entry.is_file() and entry.name.lower().endswith(".code-workspace"):
                        _add_if_new(entry.path)
            except (OSError, PermissionError):
                pass

        # 2. Check dev_projects_dir and subdirectories
        if os.path.isdir(self.dev_projects_dir):
            for root_dir, dirs, files in os.walk(self.dev_projects_dir):
                # Prune unwanted directories
                dirs[:] = [d for d in dirs if d.lower() not in PRUNE_DIRS and not d.startswith(".")]

                # Limit depth to 4 levels
                rel = os.path.relpath(root_dir, self.dev_projects_dir)
                depth = len(rel.split(os.sep)) if rel != "." else 0
                if depth > 4:
                    dirs.clear()
                    continue

                for f in files:
                    if f.lower().endswith(".code-workspace"):
                        _add_if_new(os.path.join(root_dir, f))

        return sorted(found)

    def sync_workspaces(self, moves: List[Tuple[str, str]]) -> Dict[str, Any]:
        """Updates folder paths in *.code-workspace files for moved projects.

        For each moved folder (old_path, new_path):
        - Inspects folders[].path.
        - If path references old_path (either absolute path or relative path from
          the workspace file), updates it to new_path, preserving formatting
          and style (relative vs absolute, / vs \\).
        - Creates .bak.smart-drive backup before writing modified files.

        Returns:
            Dict with 'updated_count' and 'updated_workspaces'.
        """
        if not moves:
            return {
                "count": 0,
                "updated_count": 0,
                "workspaces": [],
                "updated_workspaces": [],
            }

        ws_files = self._discover_workspace_files()
        updated_workspaces: List[str] = []

        # Prepare normalized move pairs
        norm_moves: List[Tuple[str, str, str, str]] = []
        for old_p, new_p in moves:
            old_abs = os.path.abspath(old_p)
            new_abs = os.path.abspath(new_p)
            norm_moves.append((old_abs, new_abs, os.path.normcase(old_abs), os.path.normcase(new_abs)))

        for ws_path in ws_files:
            ws_dir = os.path.dirname(ws_path)
            try:
                with open(ws_path, "r", encoding="utf-8") as f:
                    orig_content = f.read()
            except OSError:
                continue

            try:
                data = json.loads(orig_content)
            except json.JSONDecodeError:
                continue

            if not isinstance(data, dict) or "folders" not in data or not isinstance(data["folders"], list):
                continue

            modified = False
            for folder_entry in data["folders"]:
                if not isinstance(folder_entry, dict) or "path" not in folder_entry:
                    continue

                orig_path_str = folder_entry["path"]
                if not isinstance(orig_path_str, str):
                    continue

                for old_abs, new_abs, old_norm, new_norm in norm_moves:
                    is_absolute = os.path.isabs(orig_path_str)
                    uses_forward = "/" in orig_path_str or ("\\" not in orig_path_str and not is_absolute)
                    had_leading_dot = orig_path_str.startswith("./") or orig_path_str.startswith(".\\")

                    if is_absolute:
                        entry_abs = os.path.abspath(orig_path_str)
                        entry_norm = os.path.normcase(entry_abs)

                        if entry_norm == old_norm:
                            replacement = new_abs
                        elif entry_norm.startswith(old_norm + os.sep):
                            rel_sub = os.path.relpath(entry_abs, old_abs)
                            replacement = os.path.join(new_abs, rel_sub)
                        else:
                            continue

                        # Preserve slash style
                        if uses_forward:
                            replacement = replacement.replace("\\", "/")
                        else:
                            replacement = replacement.replace("/", "\\")

                        if replacement != orig_path_str:
                            folder_entry["path"] = replacement
                            modified = True
                            break

                    else:
                        # Relative path resolved from the workspace file directory
                        entry_abs = os.path.abspath(os.path.join(ws_dir, orig_path_str))
                        entry_norm = os.path.normcase(entry_abs)

                        if entry_norm == old_norm:
                            target_abs = new_abs
                        elif entry_norm.startswith(old_norm + os.sep):
                            rel_sub = os.path.relpath(entry_abs, old_abs)
                            target_abs = os.path.join(new_abs, rel_sub)
                        else:
                            continue

                        # Compute new relative path
                        new_rel = os.path.relpath(target_abs, ws_dir)
                        if uses_forward:
                            new_rel = new_rel.replace("\\", "/")
                        else:
                            new_rel = new_rel.replace("/", "\\")

                        if had_leading_dot:
                            if uses_forward and not new_rel.startswith("./"):
                                new_rel = "./" + new_rel
                            elif not uses_forward and not new_rel.startswith(".\\"):
                                new_rel = ".\\" + new_rel

                        if new_rel != orig_path_str:
                            folder_entry["path"] = new_rel
                            modified = True
                            break

            if modified:
                # 1. Create .bak.smart-drive backup
                backup_path = ws_path + ".bak.smart-drive"
                try:
                    with open(backup_path, "w", encoding="utf-8") as f_bak:
                        f_bak.write(orig_content)
                except OSError:
                    pass

                # 2. Write updated file preserving indent and newline style
                indent = _detect_indent(orig_content)
                newline = _detect_newline(orig_content)
                dumped = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
                if newline == "\r\n":
                    dumped = dumped.replace("\r\n", "\n").replace("\n", "\r\n")

                try:
                    with open(ws_path, "w", encoding="utf-8", newline="") as f_out:
                        f_out.write(dumped)
                    updated_workspaces.append(ws_path)
                except OSError:
                    pass

        return {
            "count": len(updated_workspaces),
            "updated_count": len(updated_workspaces),
            "workspaces": updated_workspaces,
            "updated_workspaces": updated_workspaces,
        }

    def sync_roadmap_kanban(self, moves: List[Tuple[str, str]]) -> bool:
        """Updates project links and table columns in ROADMAP_KANBAN.md.

        If ROADMAP_KANBAN.md exists in 03_Development_Projects/:
        - Updates relative links and markdown path references [project](path)
          or table path columns to reflect the new subzone paths.
        - Creates .bak.smart-drive backup before updating.

        Returns:
            True if updated, False otherwise.
        """
        if not moves:
            return False

        # Locate ROADMAP_KANBAN.md
        kanban_path = os.path.join(self.dev_projects_dir, "ROADMAP_KANBAN.md")
        if not os.path.isfile(kanban_path):
            cand_root = os.path.join(self.root, "ROADMAP_KANBAN.md")
            if os.path.isfile(cand_root):
                kanban_path = cand_root
            else:
                return False

        kanban_dir = os.path.dirname(kanban_path)
        try:
            with open(kanban_path, "r", encoding="utf-8") as f:
                original_text = f.read()
        except OSError:
            return False

        content = original_text

        for old_p, new_p in moves:
            old_abs = os.path.abspath(old_p)
            new_abs = os.path.abspath(new_p)

            # Paths relative to kanban_dir
            old_rel_fwd = os.path.relpath(old_abs, kanban_dir).replace("\\", "/")
            new_rel_fwd = os.path.relpath(new_abs, kanban_dir).replace("\\", "/")

            old_rel_back = old_rel_fwd.replace("/", "\\")
            new_rel_back = new_rel_fwd.replace("/", "\\")

            # Absolute path strings
            old_abs_fwd = old_abs.replace("\\", "/")
            new_abs_fwd = new_abs.replace("\\", "/")

            old_abs_back = old_abs.replace("/", "\\")
            new_abs_back = new_abs.replace("/", "\\")

            # 1. Update Markdown links: [text](path) or [text](./path) with optional subpaths
            pattern_link = re.compile(
                r"(\[[^\]]+\]\()(\./)?" + re.escape(old_rel_fwd) + r"((?:/[^)]*)?\))"
            )
            content = pattern_link.sub(
                lambda m: f"{m.group(1)}{m.group(2) or ''}{new_rel_fwd}{m.group(3)}",
                content,
            )

            # 2. Update Markdown table cells: | ... | old_rel | ... |
            pattern_table = re.compile(
                r"(\|\s*)(\./)?" + re.escape(old_rel_fwd) + r"((?:/[^|]*)?)(\s*\|)"
            )
            content = pattern_table.sub(
                lambda m: f"{m.group(1)}{m.group(2) or ''}{new_rel_fwd}{m.group(3)}{m.group(4)}",
                content,
            )

            # Also with backticks in table or text: `old_rel` or `old_rel/...`
            pattern_code = re.compile(
                r"(`)(\./)?" + re.escape(old_rel_fwd) + r"((?:/[^`]*)?)(`)"
            )
            content = pattern_code.sub(
                lambda m: f"{m.group(1)}{m.group(2) or ''}{new_rel_fwd}{m.group(3)}{m.group(4)}",
                content,
            )

            # 3. Update absolute paths if referenced directly
            content = content.replace(old_abs_fwd, new_abs_fwd)
            content = content.replace(old_abs_back, new_abs_back)

        if content != original_text:
            backup_path = kanban_path + ".bak.smart-drive"
            try:
                with open(backup_path, "w", encoding="utf-8") as f_bak:
                    f_bak.write(original_text)
            except OSError:
                pass

            try:
                with open(kanban_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)
                return True
            except OSError:
                return False

        return False

    def sync_all(self, moves: List[Tuple[str, str]]) -> Dict[str, Any]:
        """Runs both sync_workspaces and sync_roadmap_kanban for the given moves."""
        ws_res = self.sync_workspaces(moves)
        kanban_res = self.sync_roadmap_kanban(moves)
        return {
            "workspaces": ws_res,
            "roadmap_kanban": kanban_res,
            "updated_workspaces": ws_res.get("updated_workspaces", []),
            "updated_count": ws_res.get("updated_count", 0),
            "kanban_updated": kanban_res,
        }


__all__ = [
    "WorkspaceSynchronizer",
]
