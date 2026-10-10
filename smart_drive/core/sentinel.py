"""smart_drive.core.sentinel - 1-Touch SSD Health Audit, Shield Self-Healing & Git Sentinel.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Provides:
1. Verification and auto-healing of anti-indexing shields (.metadata_never_index, .fseventsd/no_log).
2. SQLite FTS5 database connectivity and PRAGMA quick_check integrity validation.
3. exFAT safety auditing: Windows forbidden characters and symlink prevention.
4. Multi-repository Git status sentinel across the SSD.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.core.exfat_compat import ExFatEngine
from smart_drive.core.fsinfo import detect_filesystem, slack_model_note
from smart_drive.core.learning_units import get_learning_summary


def get_default_db_path(root: str) -> str:
    """Default location for the SQLite search database."""
    # Check both .smart_drive/index.db (new standard) and .smart_drive_manager/index.db (legacy)
    new_path = os.path.join(root, ".smart_drive", "index.db")
    legacy_path = os.path.join(root, ".smart_drive_manager", "index.db")
    if os.path.exists(new_path):
        return new_path
    if os.path.exists(legacy_path):
        return legacy_path
    return new_path


def check_and_heal_shields(root: str, auto_heal: bool = True) -> Dict[str, Any]:
    """Verifies and auto-heals essential Spotlight and FSEvents anti-indexing shields."""
    meta_path = os.path.join(root, ".metadata_never_index")
    fsevents_dir = os.path.join(root, ".fseventsd")
    fsevents_file = os.path.join(fsevents_dir, "no_log")

    meta_existed = os.path.exists(meta_path)
    fsevents_existed = os.path.exists(fsevents_file)

    meta_healed = False
    fsevents_healed = False

    if not meta_existed and auto_heal:
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write("# Spotlight indexing disabled for Kingston SSD performance\n")
            meta_healed = True
        except OSError:
            pass

    if not fsevents_existed and auto_heal:
        try:
            os.makedirs(fsevents_dir, exist_ok=True)
            with open(fsevents_file, "w", encoding="utf-8") as f:
                f.write("")
            fsevents_healed = True
        except OSError:
            pass

    meta_active = os.path.exists(meta_path)
    fsevents_active = os.path.exists(fsevents_file)
    all_active = meta_active and fsevents_active
    auto_healed = meta_healed or fsevents_healed

    return {
        "status": "healthy" if all_active else "incomplete",
        "all_healthy": all_active,
        "auto_healed": auto_healed,
        "shields": {
            ".metadata_never_index": {
                "active": meta_active,
                "auto_healed": meta_healed,
            },
            ".fseventsd/no_log": {
                "active": fsevents_active,
                "auto_healed": fsevents_healed,
            },
        },
    }


def check_search_database(root: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Validates SQLite index.db connectivity, PRAGMA quick_check, count, and size."""
    target_db = db_path or get_default_db_path(root)
    db_exists = os.path.exists(target_db)

    if not db_exists:
        return {
            "path": target_db,
            "exists": False,
            "healthy": False,
            "quick_check": "not_found",
            "file_count": 0,
            "size_bytes": 0,
            "note": "Database not initialized. Run 'init' or 'index' command to build index.",
        }

    size_bytes = os.path.getsize(target_db)
    con: Optional[sqlite3.Connection] = None
    try:
        con = sqlite3.connect(target_db, timeout=5.0)
        cur = con.cursor()
        cur.execute("PRAGMA quick_check;")
        qc_rows = cur.fetchall()
        qc_result = qc_rows[0][0] if qc_rows else "empty"
        quick_check_ok = (qc_result == "ok")

        try:
            cur.execute("SELECT count(*) FROM files;")
            row = cur.fetchone()
            file_count = row[0] if row else 0
        except sqlite3.OperationalError:
            file_count = 0

        return {
            "path": target_db,
            "exists": True,
            "healthy": quick_check_ok,
            "quick_check": qc_result,
            "file_count": file_count,
            "size_bytes": size_bytes,
        }
    except Exception as exc:
        return {
            "path": target_db,
            "exists": True,
            "healthy": False,
            "quick_check": f"error: {exc}",
            "file_count": 0,
            "size_bytes": size_bytes,
        }
    finally:
        if con is not None:
            con.close()  # also when quick_check itself fails on a damaged file


def check_exfat_safety(root: str) -> Dict[str, Any]:
    """Audits root items and immediate project dirs for forbidden characters and symlinks."""
    symlinks: List[str] = []
    forbidden_violations: List[Dict[str, Any]] = []

    try:
        root_entries = os.listdir(root)
    except OSError:
        root_entries = []

    paths_to_check = [os.path.join(root, e) for e in root_entries]

    for sub in ["teamwork_projects", "03_Development_Projects"]:
        sub_path = os.path.join(root, sub)
        if os.path.isdir(sub_path):
            try:
                for entry in os.listdir(sub_path):
                    paths_to_check.append(os.path.join(sub_path, entry))
            except OSError:
                pass

    for p in paths_to_check:
        base = os.path.basename(p)
        if os.path.islink(p):
            symlinks.append(os.path.relpath(p, root).replace("\\", "/"))
        v = ExFatEngine.audit_forbidden_characters(base)
        if v:
            forbidden_violations.append({
                "path": os.path.relpath(p, root).replace("\\", "/"),
                "violations": [str(violation) for violation in v],
            })

    return {
        "is_safe": len(symlinks) == 0 and len(forbidden_violations) == 0,
        "symlinks_found": len(symlinks),
        "symlinks": symlinks,
        "forbidden_char_violations": len(forbidden_violations),
        "forbidden_details": forbidden_violations,
    }


def check_git_repositories(root: str, max_depth: int = 4) -> Dict[str, Any]:
    """Inspects git repositories across the drive up to max_depth."""
    repos_found: List[str] = []
    ignored_dir_names = {
        "node_modules", "vendor", ".venv", ".cache", ".ruff_cache",
        ".pytest_cache", ".Trash", "$RECYCLE.BIN", ".agents",
    }

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            depth = len(Path(rel_dir).parts) if rel_dir != "." else 0

            dirnames[:] = [
                d for d in dirnames
                if d not in ignored_dir_names and not d.startswith("._")
            ]

            if ".git" in dirnames:
                repos_found.append(dirpath)
                dirnames.remove(".git")

            if depth >= max_depth:
                dirnames.clear()
    except (OSError, ValueError):
        pass

    repo_details: List[Dict[str, Any]] = []
    clean_count = 0
    dirty_count = 0
    unpushed_count = 0

    for repo_dir in repos_found:
        rel_repo = os.path.relpath(repo_dir, root).replace("\\", "/")
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain", "-b"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                lines = [ln for ln in res.stdout.strip().split("\n") if ln.strip()]
                branch_line = lines[0] if lines else ""
                branch = "unknown"
                ahead = 0
                behind = 0

                if branch_line.startswith("## "):
                    b_part = branch_line[3:].split("...")[0].strip()
                    branch = b_part
                    m_ahead = re.search(r"ahead (\d+)", branch_line)
                    if m_ahead:
                        ahead = int(m_ahead.group(1))
                    m_behind = re.search(r"behind (\d+)", branch_line)
                    if m_behind:
                        behind = int(m_behind.group(1))

                uncommitted_changes = lines[1:] if len(lines) > 1 else []
                is_dirty = len(uncommitted_changes) > 0

                if is_dirty:
                    dirty_count += 1
                if ahead > 0:
                    unpushed_count += 1
                if not is_dirty and ahead == 0 and behind == 0:
                    clean_count += 1

                repo_details.append({
                    "path": rel_repo,
                    "branch": branch,
                    "is_dirty": is_dirty,
                    "uncommitted_count": len(uncommitted_changes),
                    "ahead": ahead,
                    "behind": behind,
                })
            else:
                repo_details.append({
                    "path": rel_repo,
                    "branch": "error",
                    "is_dirty": False,
                    "uncommitted_count": 0,
                    "ahead": 0,
                    "behind": 0,
                })
        except (subprocess.SubprocessError, OSError, UnicodeDecodeError):
            repo_details.append({
                "path": rel_repo,
                "branch": "error",
                "is_dirty": False,
                "uncommitted_count": 0,
                "ahead": 0,
                "behind": 0,
            })

    return {
        "repos_checked": len(repos_found),
        "clean_repos": clean_count,
        "dirty_repos": dirty_count,
        "unpushed_repos": unpushed_count,
        "details": repo_details,
    }


class HealthReport(dict):
    """Encapsulates health status dictionary with convenience properties."""

    @property
    def is_healthy(self) -> bool:
        return bool(self.get("is_healthy", False))

    @property
    def status(self) -> str:
        return str(self.get("status", "unknown"))


class SentinelEngine:
    """Integrated sentinel audit and self-healing engine."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)

    def run_health_check(
        self,
        root_dir: Optional[str] = None,
        auto_heal: bool = True,
    ) -> HealthReport:
        target_root = os.path.abspath(root_dir) if root_dir else self.root
        is_mounted = os.path.isdir(target_root)

        if not is_mounted:
            return HealthReport({
                "status": "error",
                "error": f"Target drive root does not exist or is not mounted: {target_root}",
                "is_mounted": False,
                "is_healthy": False,
            })

        shields_info = check_and_heal_shields(target_root, auto_heal=auto_heal)
        db_info = check_search_database(target_root)
        safety_info = check_exfat_safety(target_root)
        git_info = check_git_repositories(target_root)

        is_healthy = (
            is_mounted
            and shields_info["all_healthy"]
            and safety_info["is_safe"]
            and (not db_info["exists"] or db_info["healthy"])
        )

        status_str = "healthy" if is_healthy else "unhealthy"
        if is_healthy and shields_info["auto_healed"]:
            status_str = "healed"

        # What the volume really is (this used to be the literal "exFAT" whatever the drive was).
        fs_info = detect_filesystem(target_root)

        learning_info = get_learning_summary(target_root, db_path=db_info.get("path"))

        report_data = {
            "status": status_str,
            "is_healthy": is_healthy,
            "drive_root": target_root,
            "mount": {
                "is_mounted": True,
                "filesystem": fs_info.display_name,
                "filesystem_type": fs_info.fs_type,
                "allocation_unit_bytes": fs_info.block_size,
                # The cluster size the slack figures MODEL (512 KB exFAT), not necessarily this volume's.
                "cluster_size_bytes": CLUSTER_SIZE_BYTES,
                "cluster_size_kb": CLUSTER_SIZE_BYTES // 1024,
                "slack_model_note": slack_model_note(fs_info, CLUSTER_SIZE_BYTES),
            },
            "anti_indexing_shields": shields_info,
            "database": db_info,
            "exfat_safety": safety_info,
            "git_status": git_info,
            "learning": learning_info,
            "learning_summary": learning_info,
        }

        return HealthReport(report_data)


__all__ = [
    "check_and_heal_shields",
    "check_search_database",
    "check_exfat_safety",
    "check_git_repositories",
    "get_default_db_path",
    "HealthReport",
    "SentinelEngine",
]
