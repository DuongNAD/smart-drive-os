"""smart_drive.core.project_classifier - Project Sub-Zoning & Lifecycle Classification Engine.

Categorizes projects inside `03_Development_Projects` into 5 canonical lifecycle zones:
- `01_Active_Focus`: Daily active development projects (WIP <= 3).
- `02_Wave_Incubator`: Scaffolded/designed projects awaiting unlocking.
- `03_MCP_Agents`: MCP server infrastructure and background agent servers.
- `04_Hackathons_Pitch`: Short-term competitions, coding tests, and pitch decks.
- `99_Archive_Dormant`: Completed or dormant projects inactive for >= 60 days with clean git tree.

Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple, Union

from smart_drive.core.system_refs import SystemRefIndex, summarize_system_refs

# ==============================================================================
# 1. CANONICAL CONSTANTS & TAXONOMY SPECIFICATIONS
# ==============================================================================

LIFECYCLE_ZONES: Tuple[str, ...] = (
    "01_Active_Focus",
    "02_Wave_Incubator",
    "03_MCP_Agents",
    "04_Hackathons_Pitch",
    "99_Archive_Dormant",
)

STAGE_NAME_TO_ZONE: Dict[str, str] = {
    "active": "01_Active_Focus",
    "focus": "01_Active_Focus",
    "incubator": "02_Wave_Incubator",
    "wave": "02_Wave_Incubator",
    "draft": "02_Wave_Incubator",
    "mcp": "03_MCP_Agents",
    "agent": "03_MCP_Agents",
    "agents": "03_MCP_Agents",
    "hackathon": "04_Hackathons_Pitch",
    "pitch": "04_Hackathons_Pitch",
    "pitching": "04_Hackathons_Pitch",
    "contest": "04_Hackathons_Pitch",
    "challenge": "04_Hackathons_Pitch",
    "archive": "99_Archive_Dormant",
    "dormant": "99_Archive_Dormant",
}
# Map canonical zone names (and variants)
for _zone in LIFECYCLE_ZONES:
    STAGE_NAME_TO_ZONE[_zone.lower()] = _zone
STAGE_NAME_TO_ZONE["99_archive"] = "99_Archive_Dormant"

ZONE_TO_STAGE_NAME: Dict[str, str] = {
    "01_Active_Focus": "active",
    "02_Wave_Incubator": "incubator",
    "03_MCP_Agents": "mcp",
    "04_Hackathons_Pitch": "hackathon",
    "99_Archive_Dormant": "archive",
}

HACKATHON_KEYWORDS: FrozenSet[str] = frozenset({
    "hackathon",
    "hackerrank",
    "pitch",
    "pitching",
    "contest",
    "challenge",
    "rush",
    "orchestrate",
    "comp",
})

EXECUTABLE_SOURCE_EXTENSIONS: FrozenSet[str] = frozenset({
    ".py",
    ".ts",
    ".js",
    ".rs",
    ".go",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".v",
    ".rb",
    ".php",
    ".swift",
    ".kt",
})

LIFECYCLE_ZONE_ROOTS: FrozenSet[str] = frozenset({
    "01_active_focus",
    "02_wave_incubator",
    "03_mcp_agents",
    "04_hackathons_pitch",
    "99_archive_dormant",
    "99_archive",
})

PROJECT_SKELETON_INDICATORS: FrozenSet[str] = frozenset({
    "readme.md",
    "readme",
    ".gitignore",
    "docs",
    "src",
    "tests",
    "test",
    ".git",
    "package.json",
    "pyproject.toml",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "cmakelists.txt",
    "requirements.txt",
})

PRUNE_DIRS: FrozenSet[str] = frozenset({
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
})


# ==============================================================================
# 2. DATA TRANSFER OBJECT (ProjectZoneResult)
# ==============================================================================

@dataclass
class ProjectZoneResult:
    """Classification outcome and lifecycle state for one development project."""

    project_name: str
    project_path: str
    current_zone: Optional[str]
    suggested_zone: str
    reason: str
    is_explicit: bool = False
    protected_by_mcp: bool = False
    protected_by_system: bool = False
    last_activity_time: float = 0.0
    age_days: float = 0.0
    git_status: Dict[str, Any] = field(default_factory=dict)
    source_file_count: int = 0
    system_ref_summary: Optional[str] = None
    nominal_size: int = 0
    slack_bytes: int = 0
    references: List[str] = field(default_factory=list)

    @property
    def last_active_time(self) -> float:
        """Alias for last_activity_time for specification compatibility."""
        return self.last_activity_time

    def to_dict(self) -> Dict[str, Any]:
        """Serializes result to JSON-compliant dictionary."""
        return {
            "project_name": self.project_name,
            "project_path": self.project_path,
            "current_zone": self.current_zone,
            "suggested_zone": self.suggested_zone,
            "reason": self.reason,
            "is_explicit": self.is_explicit,
            "protected_by_mcp": self.protected_by_mcp,
            "protected_by_system": self.protected_by_system,
            "system_ref_summary": self.system_ref_summary,
            "last_activity_time": self.last_activity_time,
            "last_active_time": self.last_activity_time,
            "age_days": round(self.age_days, 1),
            "git_status": dict(self.git_status),
            "source_file_count": self.source_file_count,
            "nominal_size": self.nominal_size,
            "slack_bytes": self.slack_bytes,
            "references": list(self.references),
        }


# ==============================================================================
# 3. PROJECT CLASSIFIER ENGINE
# ==============================================================================

class ProjectClassifier:
    """Autonomous classification engine for development project lifecycle zones."""

    def __init__(
        self,
        root: str,
        system_refs: Optional[SystemRefIndex] = None,
        mcp_paths: Optional[Set[str]] = None,
    ) -> None:
        self.root = os.path.abspath(root)

        # Injectable system reference index
        if system_refs is None:
            self.system_refs = SystemRefIndex.collect(self.root)
        else:
            self.system_refs = system_refs

        # Injectable or discovered MCP server paths
        if mcp_paths is None:
            self.mcp_paths = self._collect_mcp_paths()
        else:
            self.mcp_paths = {os.path.normcase(os.path.abspath(p)) for p in mcp_paths}

    @staticmethod
    def is_zone_container(name_or_path: str) -> bool:
        """Rule 0 helper: Returns True if name/path is an inviolable lifecycle zone root."""
        base = os.path.basename(os.path.normpath(name_or_path)).lower()
        return base in LIFECYCLE_ZONE_ROOTS

    @staticmethod
    def _folder_name_tokens(name: str) -> Set[str]:
        """Splits a folder name into lowercase whole-word tokens across separators and camelCase."""
        cleaned = re.sub(r"(?i)dumpstack", " ", name)
        camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", cleaned)
        camel_split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", camel_split)
        return {tok.lower() for tok in re.findall(r"[a-z0-9]+", camel_split, flags=re.IGNORECASE)}

    def _collect_mcp_paths(self) -> Set[str]:
        """Discovers referenced MCP server directory paths from agent and project configs."""
        discovered: Set[str] = set()

        # 1. Project root / SSD .mcp.json
        root_mcp = os.path.join(self.root, ".mcp.json")
        if os.path.isfile(root_mcp):
            self._extract_mcp_paths_from_json(root_mcp, discovered)

        # 2. Agent configs via smart_drive.core.relink.agent_files
        try:
            from smart_drive.core.relink import agent_files
            for af in agent_files(root=self.root):
                p_str = str(af.path)
                if p_str.endswith(".json") and os.path.isfile(p_str):
                    self._extract_mcp_paths_from_json(p_str, discovered)
        except Exception:
            pass

        return {os.path.normcase(os.path.abspath(p)) for p in discovered}

    def _extract_mcp_paths_from_json(self, file_path: str, out: Set[str]) -> None:
        """Safely extracts command/cwd/path locations from an MCP or agent JSON configuration."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return

            servers = data.get("mcpServers") or data.get("servers") or {}
            if isinstance(servers, dict):
                for _, s_conf in servers.items():
                    if not isinstance(s_conf, dict):
                        continue
                    cwd = s_conf.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        out.add(cwd)
                    cmd = s_conf.get("command")
                    if isinstance(cmd, str) and (os.path.isabs(cmd) or "/" in cmd or "\\" in cmd):
                        out.add(os.path.dirname(cmd) if os.path.splitext(cmd)[1] else cmd)
                    args = s_conf.get("args")
                    if isinstance(args, list):
                        for a in args:
                            if isinstance(a, str) and (os.path.isabs(a) or "/" in a or "\\" in a):
                                if os.path.exists(a):
                                    out.add(os.path.dirname(a) if os.path.isfile(a) else a)
                                elif os.path.splitext(a)[1]:
                                    out.add(os.path.dirname(a))
                                else:
                                    out.add(a)
                    srv_path = s_conf.get("path")
                    if isinstance(srv_path, str) and srv_path:
                        out.add(srv_path)
        except Exception:
            pass

    def _is_mcp_server(self, project_path: str) -> bool:
        """Returns True if project_path or any direct subfolder is referenced as an MCP server."""
        if not self.mcp_paths:
            return False

        norm_project = os.path.normcase(os.path.abspath(project_path))
        if norm_project in self.mcp_paths:
            return True

        prefix = norm_project + os.sep
        for p in self.mcp_paths:
            if p == norm_project or p.startswith(prefix):
                return True

        # Check direct subfolders
        try:
            for entry in os.scandir(project_path):
                if entry.is_dir():
                    norm_child = os.path.normcase(os.path.abspath(entry.path))
                    if norm_child in self.mcp_paths:
                        return True
        except (OSError, PermissionError):
            pass

        return False

    def _check_explicit_marker(self, project_path: str) -> Optional[str]:
        """Rule 1: Checks for explicit user marker in .smart_drive_stage or .project.json."""
        # 1. <project>/.smart_drive_stage takes absolute precedence
        stage_file = os.path.join(project_path, ".smart_drive_stage")
        if os.path.isfile(stage_file):
            try:
                with open(stage_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    first_line = content.splitlines()[0].strip().lower()
                    words = first_line.split()
                    stage_token = words[0] if words else first_line
                    if stage_token in STAGE_NAME_TO_ZONE:
                        return STAGE_NAME_TO_ZONE[stage_token]
                    if first_line in STAGE_NAME_TO_ZONE:
                        return STAGE_NAME_TO_ZONE[first_line]
            except (OSError, UnicodeDecodeError):
                pass

        # 2. <project>/.project.json fallback
        proj_json = os.path.join(project_path, ".project.json")
        if os.path.isfile(proj_json):
            try:
                with open(proj_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    raw_stage = data.get("stage") or data.get("wave") or data.get("smart_drive_stage")
                    if raw_stage is not None:
                        if isinstance(raw_stage, int) or str(raw_stage).isdigit():
                            val = int(raw_stage)
                            return "01_Active_Focus" if val == 1 else "02_Wave_Incubator"
                        stage_str = str(raw_stage).strip().lower()
                        words = stage_str.split()
                        first_word = words[0] if words else stage_str
                        if first_word in STAGE_NAME_TO_ZONE:
                            return STAGE_NAME_TO_ZONE[first_word]
                        if stage_str in STAGE_NAME_TO_ZONE:
                            return STAGE_NAME_TO_ZONE[stage_str]
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass

        return None

    def _scan_project_files(
        self, project_path: str
    ) -> Tuple[int, float, int, int]:
        """Scans project tree for source count, latest mtime, nominal size, and cluster slack."""
        source_count = 0
        latest_mtime = 0.0
        nominal_size = 0
        slack_bytes = 0

        try:
            from smart_drive.core.config import calculate_slack_bytes
        except ImportError:
            calculate_slack_bytes = lambda sz: 0  # type: ignore[assignment]

        try:
            for root, dirs, files in os.walk(project_path):
                dirs[:] = [d for d in dirs if d.lower() not in PRUNE_DIRS and not d.startswith(".")]

                for file_name in files:
                    if file_name.startswith("."):
                        continue
                    file_path = os.path.join(root, file_name)
                    try:
                        st = os.stat(file_path)
                        sz = st.st_size
                        nominal_size += sz
                        slack_bytes += calculate_slack_bytes(sz)
                        if st.st_mtime > latest_mtime:
                            latest_mtime = st.st_mtime

                        ext = os.path.splitext(file_name)[1].lower()
                        if ext in EXECUTABLE_SOURCE_EXTENSIONS:
                            source_count += 1
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            pass

        return source_count, latest_mtime, nominal_size, slack_bytes

    def _scan_git(
        self, project_path: str
    ) -> Tuple[float, int, int, str, bool, str]:
        """Inspects git status, commit timestamp, and working tree cleanliness."""
        git_dir = os.path.join(project_path, ".git")
        if not (os.path.isdir(git_dir) or os.path.isfile(git_dir)):
            return 0.0, 0, 0, "", True, "not a git repo"

        try:
            from smart_drive.core.repos import inspect_repository
            copy = inspect_repository(project_path)
            head_time = float(copy.head_time)
            changes = copy.changes
            unpushed = copy.unpushed
            branch = copy.branch or ""
            error = copy.error
            is_clean = (changes == 0 and unpushed == 0 and not error)

            if error:
                summary = f"git error: {error}"
            elif is_clean:
                summary = "clean"
            elif changes and unpushed:
                summary = f"M:{changes} ↑{unpushed}"
            elif changes:
                summary = f"M:{changes}"
            else:
                summary = f"↑{unpushed}"

            return head_time, changes, unpushed, branch, is_clean, summary
        except Exception as e:
            return 0.0, 0, 0, "", True, f"git unavailable: {e}"

    def classify_project(
        self,
        project_path: str,
        current_zone: Optional[str] = None,
    ) -> ProjectZoneResult:
        """Classifies a single project directory according to Rules 0 through 5."""
        project_path = os.path.abspath(project_path)
        project_name = os.path.basename(project_path)

        # Rule 0: Inviolable zone containers are never treated as movable projects
        if self.is_zone_container(project_path):
            return ProjectZoneResult(
                project_name=project_name,
                project_path=project_path,
                current_zone=current_zone or project_name,
                suggested_zone=project_name,
                reason="Inviolable zone container",
                is_explicit=True,
                protected_by_system=True,
            )

        # Infer current zone if not explicitly provided
        if current_zone is None:
            parent_name = os.path.basename(os.path.dirname(project_path))
            if parent_name in LIFECYCLE_ZONES:
                current_zone = parent_name

        # System reference guard
        protected_by_system = False
        system_ref_summary = None
        references: List[str] = []
        if self.system_refs is not None:
            refs = self.system_refs.check_folder(project_path)
            if refs:
                protected_by_system = True
                system_ref_summary = summarize_system_refs(refs)
                references = [f"{r.kind}:{r.source}" for r in refs]

        # Scan filesystem files and git status
        source_file_count, latest_source_mtime, nominal_size, slack_bytes = self._scan_project_files(project_path)
        git_head_time, git_changes, git_unpushed, git_branch, is_git_clean, git_summary = self._scan_git(project_path)

        # Calculate recency & age
        last_activity_time = max(float(git_head_time), float(latest_source_mtime))
        if last_activity_time <= 0.0:
            try:
                last_activity_time = os.path.getmtime(project_path)
            except (OSError, PermissionError):
                last_activity_time = time.time()

        now = time.time()
        age_days = max(0.0, (now - last_activity_time) / 86400.0)

        git_status: Dict[str, Any] = {
            "clean": is_git_clean,
            "changes": git_changes,
            "unpushed": git_unpushed,
            "branch": git_branch,
            "summary": git_summary,
        }

        # Check MCP server status (used in Rule 2 and flagged in all rules)
        is_mcp = self._is_mcp_server(project_path)

        # ----------------------------------------------------------------------
        # Rule 1: Explicit project stage marker (.smart_drive_stage / .project.json)
        # ----------------------------------------------------------------------
        marker_zone = self._check_explicit_marker(project_path)
        if marker_zone:
            return ProjectZoneResult(
                project_name=project_name,
                project_path=project_path,
                current_zone=current_zone,
                suggested_zone=marker_zone,
                reason="Explicit project stage marker",
                is_explicit=True,
                protected_by_mcp=is_mcp,
                protected_by_system=protected_by_system,
                system_ref_summary=system_ref_summary,
                last_activity_time=last_activity_time,
                age_days=age_days,
                git_status=git_status,
                source_file_count=source_file_count,
                nominal_size=nominal_size,
                slack_bytes=slack_bytes,
                references=references,
            )

        # ----------------------------------------------------------------------
        # Rule 2: MCP server infrastructure protection (03_MCP_Agents & protected_by_mcp)
        # ----------------------------------------------------------------------
        if is_mcp:
            return ProjectZoneResult(
                project_name=project_name,
                project_path=project_path,
                current_zone=current_zone,
                suggested_zone="03_MCP_Agents",
                reason="Active MCP server infrastructure (protected_by_mcp)",
                is_explicit=False,
                protected_by_mcp=True,
                protected_by_system=protected_by_system,
                system_ref_summary=system_ref_summary,
                last_activity_time=last_activity_time,
                age_days=age_days,
                git_status=git_status,
                source_file_count=source_file_count,
                nominal_size=nominal_size,
                slack_bytes=slack_bytes,
                references=references,
            )

        # ----------------------------------------------------------------------
        # Rule 3: Hackathon / Pitch keywords
        # ----------------------------------------------------------------------
        tokens = self._folder_name_tokens(project_name)
        if tokens & HACKATHON_KEYWORDS:
            return ProjectZoneResult(
                project_name=project_name,
                project_path=project_path,
                current_zone=current_zone,
                suggested_zone="04_Hackathons_Pitch",
                reason="Detected hackathon/pitch keywords in folder name",
                is_explicit=False,
                protected_by_mcp=False,
                protected_by_system=protected_by_system,
                system_ref_summary=system_ref_summary,
                last_activity_time=last_activity_time,
                age_days=age_days,
                git_status=git_status,
                source_file_count=source_file_count,
                nominal_size=nominal_size,
                slack_bytes=slack_bytes,
                references=references,
            )

        # ----------------------------------------------------------------------
        # Rule 4: Incubator structure (Scaffold present & <= 2 executable source files)
        # ----------------------------------------------------------------------
        try:
            entries = {e.name.lower() for e in os.scandir(project_path)}
        except (OSError, PermissionError):
            entries = set()

        has_skeleton = bool(entries & PROJECT_SKELETON_INDICATORS)
        if has_skeleton and source_file_count <= 2:
            return ProjectZoneResult(
                project_name=project_name,
                project_path=project_path,
                current_zone=current_zone,
                suggested_zone="02_Wave_Incubator",
                reason="Scaffolded project with <= 2 source files",
                is_explicit=False,
                protected_by_mcp=False,
                protected_by_system=protected_by_system,
                system_ref_summary=system_ref_summary,
                last_activity_time=last_activity_time,
                age_days=age_days,
                git_status=git_status,
                source_file_count=source_file_count,
                nominal_size=nominal_size,
                slack_bytes=slack_bytes,
                references=references,
            )

        # ----------------------------------------------------------------------
        # Rule 5: Git activity recency & file modification time
        # ----------------------------------------------------------------------
        has_git_changes = (git_changes > 0) or (git_unpushed > 0)

        if age_days <= 14.0 or has_git_changes:
            suggested_zone = "01_Active_Focus"
            reason = "Active project (activity within 14d or uncommitted/unpushed git changes)"
        elif age_days >= 60.0 and is_git_clean:
            suggested_zone = "99_Archive_Dormant"
            reason = "Dormant project (inactive for >= 60d with clean git tree)"
        else:
            # Intermediate activity (14 < age_days < 60)
            if current_zone in LIFECYCLE_ZONES:
                suggested_zone = current_zone
                reason = f"Intermediate activity ({age_days:.1f}d) - preserved current zone '{current_zone}'"
            else:
                suggested_zone = "02_Wave_Incubator"
                reason = f"Intermediate activity ({age_days:.1f}d) - assigned to incubator"

        return ProjectZoneResult(
            project_name=project_name,
            project_path=project_path,
            current_zone=current_zone,
            suggested_zone=suggested_zone,
            reason=reason,
            is_explicit=False,
            protected_by_mcp=False,
            protected_by_system=protected_by_system,
            system_ref_summary=system_ref_summary,
            last_activity_time=last_activity_time,
            age_days=age_days,
            git_status=git_status,
            source_file_count=source_file_count,
            nominal_size=nominal_size,
            slack_bytes=slack_bytes,
            references=references,
        )

    def scan_all_projects(
        self, dev_projects_dir: Optional[str] = None
    ) -> List[ProjectZoneResult]:
        """Scans flat projects and sub-zoned projects inside 03_Development_Projects."""
        if dev_projects_dir:
            target_dir = os.path.abspath(dev_projects_dir)
        else:
            base = os.path.basename(os.path.normpath(self.root))
            if base == "03_Development_Projects":
                target_dir = self.root
            else:
                cand = os.path.join(self.root, "03_Development_Projects")
                target_dir = cand if os.path.isdir(cand) else self.root

        if not os.path.isdir(target_dir):
            return []

        results: List[ProjectZoneResult] = []

        try:
            entries = sorted(os.scandir(target_dir), key=lambda e: e.name.lower())
        except (OSError, PermissionError):
            return []

        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue

            entry_path = entry.path
            entry_name = entry.name

            # Rule 0: If entry is an inviolable zone container, scan its children as sub-zoned projects
            if self.is_zone_container(entry_path):
                try:
                    sub_entries = sorted(os.scandir(entry_path), key=lambda e: e.name.lower())
                    for sub_entry in sub_entries:
                        if sub_entry.is_dir() and not sub_entry.name.startswith("."):
                            res = self.classify_project(sub_entry.path, current_zone=entry_name)
                            results.append(res)
                except (OSError, PermissionError):
                    continue
            else:
                # Flat unzoned project directly under dev_projects_dir
                res = self.classify_project(entry_path, current_zone=None)
                results.append(res)

        return results

    def get_wip_count(self, results: List[ProjectZoneResult]) -> int:
        """Returns count of projects whose current or suggested zone is 01_Active_Focus."""
        return sum(
            1 for r in results
            if r.current_zone == "01_Active_Focus" or r.suggested_zone == "01_Active_Focus"
        )
