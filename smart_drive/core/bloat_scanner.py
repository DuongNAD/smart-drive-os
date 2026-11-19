"""smart_drive.core.bloat_scanner - Dev-Artifact Bloat Scanner & Safe Reclamation.

Detects high-slack development artifact trees that standard metadata junk rules intentionally
leave out (Cargo target/ dirs including custom target-* dirs tagged with CACHEDIR.TAG,
node_modules, Composer vendor/, Python virtualenvs, web framework caches like .next/.turbo,
and Rust toolchain HTML docs).

Reports both file cluster allocation and exFAT directory-cluster allocation overhead at the
volume's real cluster size, detects linked git worktrees, enforces MCP/process/system/git
safety guards before marking any item reclaimable, and stays 100% read-only unless
`purge_bloat(..., dry_run=False)` is explicitly requested.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import shutil
import stat
import subprocess
import sys
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from smart_drive.core.config import (
    PROTECTED_CORE_TAXONOMIES,
    is_protected_root_dir,
)
from smart_drive.core.fsinfo import cluster_size_text, detect_filesystem, slack_cluster
from smart_drive.core.git_locks import NO_FSMONITOR
from smart_drive.core.junk_detector import (
    _holds_git_repository,
    _norm_abs_case,
    collect_registered_mcp_paths,
)
from smart_drive.core.learning_units import find_unit_root
from smart_drive.core.processes import executables_under_path, running_executable_paths
from smart_drive.core.system_refs import SystemRefIndex, summarize_system_refs

BLOAT_KINDS: Tuple[str, ...] = (
    "rust-target",
    "node-modules",
    "composer-vendor",
    "python-venv",
    "web-build",
    "toolchain-docs",
)

_KIND_LABELS: Dict[str, str] = {
    "rust-target": "Rust Cargo target artifacts",
    "node-modules": "Node.js dependencies (node_modules)",
    "composer-vendor": "PHP Composer dependencies (vendor)",
    "python-venv": "Python virtual environment",
    "web-build": "Web framework build/bundler cache",
    "toolchain-docs": "Rust toolchain HTML documentation",
}

_KIND_REBUILD_HINTS: Dict[str, str] = {
    "rust-target": "Rebuilds via 'cargo build'; prevent recurrence on exFAT by setting CARGO_TARGET_DIR to an NTFS drive (smart-drive shield).",
    "node-modules": "Reinstall via 'npm ci' or 'pnpm install' (pnpm avoids per-project copies).",
    "composer-vendor": "Reinstall via 'composer install'.",
    "python-venv": "Recreate via 'uv venv' or 'python -m venv' + requirements/lockfile.",
    "web-build": "Rebuilds automatically on next dev/build run.",
    "toolchain-docs": "Remove safely or run 'rustup component remove rust-docs'.",
}

_WEB_BUILD_DIR_NAMES: FrozenSet[str] = frozenset({
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".parcel-cache",
})

_WALK_PRUNE_DIRS: FrozenSet[str] = frozenset({
    ".git",
    ".smart_drive",
    ".smart_drive_manager",
    "system volume information",
    "$recycle.bin",
    ".trashes",
    ".fseventsd",
    ".spotlight-v100",
    ".agents",
})

# Workspace roots can legitimately hold project build/dependency trees we want to inspect;
# OS/game/self-metadata directories never should.
_NON_BLOCKING_ANCESTORS: FrozenSet[str] = frozenset({
    *(t.lower() for t in PROTECTED_CORE_TAXONOMIES),
    "smart_ssd_workspace",
    "teamwork_projects",
    "python_master_final",
    "downloads",
})

_CACHE_TAG_SIGNATURE = b"Signature: 8a477f597d28d172789f06886806bc55"
_GIT_ENV_UNSET = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")


def _win32_long_path(path: str) -> str:
    if sys.platform == "win32" and not path.startswith("\\\\?\\") and len(path) >= 240:
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]
        return "\\\\?\\" + path
    return path


def _win32_rmtree_long_path(path: str) -> str:
    if sys.platform == "win32" and not path.startswith("\\\\?\\"):
        abs_p = os.path.abspath(path)
        if abs_p.startswith("\\\\"):
            return "\\\\?\\UNC\\" + abs_p[2:]
        return "\\\\?\\" + abs_p
    return path


def _read_cachedir_tag_cargo(dirpath: str) -> bool:
    """True when dirpath holds a valid CACHEDIR.TAG mentioning Cargo."""
    tag_path = os.path.join(dirpath, "CACHEDIR.TAG")
    try:
        with open(_win32_long_path(tag_path), "rb") as handle:
            head = handle.read(512)
    except OSError:
        return False
    if not head.startswith(_CACHE_TAG_SIGNATURE):
        return False
    return b"cargo" in head.lower()


def _parse_linked_worktree_gitdir(dot_git_file: str) -> Optional[str]:
    try:
        with open(_win32_long_path(dot_git_file), "r", encoding="utf-8", errors="replace") as handle:
            line = handle.readline(1024).strip()
    except OSError:
        return None
    if not line.lower().startswith("gitdir:"):
        return None
    target = line.split(":", 1)[1].strip()
    norm = target.replace("\\", "/").lower()
    if "/worktrees/" in norm or norm.endswith("/worktrees"):
        return target
    return None


def _inspect_worktree_git(wt_path: str) -> Tuple[str, bool]:
    git = shutil.which("git")
    if not git:
        return "unknown", False
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_UNSET}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        res = subprocess.run(
            [git, *NO_FSMONITOR, "-c", "safe.directory=*", "-C", wt_path, "status", "--porcelain", "-b"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", False
    if res.returncode != 0:
        return "unknown", False
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    branch = "unknown"
    if lines and lines[0].startswith("## "):
        branch = lines[0][3:].split("...")[0].strip()
    dirty = len(lines) > 1
    return branch, dirty


def _git_tracks_dir(parent_repo_root: str, target_dir: str) -> Optional[bool]:
    """Returns True if git tracks any file inside `target_dir`, False if untracked, None if unverified."""
    git = shutil.which("git")
    if git is None:
        return None
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_UNSET}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        rel = os.path.relpath(target_dir, parent_repo_root).replace("\\", "/")
    except ValueError:
        return None
    try:
        res = subprocess.run(
            [git, *NO_FSMONITOR, "-c", "safe.directory=*", "--literal-pathspecs", "-C", parent_repo_root, "ls-files", "-z", "--", rel],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return bool(res.stdout)


def measure_bloat_tree(path: str, cluster_size: int) -> Tuple[int, int, int, int]:
    """Measures (file_count, dir_count, nominal_bytes, file_allocated_bytes) without following symlinks."""
    file_count = 0
    dir_count = 0
    nominal_bytes = 0
    allocated_bytes = 0
    stack: List[str] = [path]

    while stack:
        curr = stack.pop()
        dir_count += 1
        try:
            with os.scandir(_win32_long_path(curr)) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            sz = entry.stat(follow_symlinks=False).st_size
                            file_count += 1
                            nominal_bytes += sz
                            if sz > 0 and cluster_size > 0:
                                allocated_bytes += ((sz + cluster_size - 1) // cluster_size) * cluster_size
                    except OSError:
                        continue
        except OSError:
            continue

    return file_count, dir_count, nominal_bytes, allocated_bytes


@dataclass
class BloatFinding:
    """One detected dev-artifact tree with physical cluster math and safety verdict."""

    path: str
    rel_path: str
    kind: str
    label: str
    file_count: int
    dir_count: int
    nominal_bytes: int
    allocated_bytes: int
    dir_allocated_bytes: int
    total_physical_bytes: int
    slack_bytes: int
    slack_percentage: float
    reclaimable: bool
    blocked_reason: Optional[str]
    detail: str
    rebuild_hint: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LinkedWorktreeInfo:
    """Detected linked git worktree (informational advisory; never auto-deleted)."""

    path: str
    rel_path: str
    git_common_dir: str
    branch: str
    is_dirty: bool
    has_build_artifacts: bool
    hint: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BloatScanner:
    """Scans a drive or folder for high-slack dev-artifact bloat with safety guards."""

    def __init__(
        self,
        root: str,
        cluster_size: Optional[int] = None,
        kinds: Optional[Iterable[str]] = None,
        min_physical_bytes: int = 0,
        system_refs: Optional[SystemRefIndex] = None,
        mcp_paths: Optional[Iterable[str]] = None,
        running_exes: Optional[Iterable[str]] = None,
    ) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self.fs_info = detect_filesystem(self.root)
        if cluster_size is None:
            self.cluster_size, self.cluster_size_source = slack_cluster(self.fs_info)
        else:
            self.cluster_size = int(cluster_size)
            self.cluster_size_source = "explicit"
        if kinds is None:
            self.kinds: FrozenSet[str] = frozenset(BLOAT_KINDS)
        else:
            self.kinds = frozenset(k.strip().lower() for k in kinds if k and k.strip())
        self.min_physical_bytes = max(0, int(min_physical_bytes))
        self._system_refs = system_refs
        self._drive_refs: Optional[SystemRefIndex] = None
        self._mcp_input = mcp_paths
        self._mcp_resolved: Optional[Set[str]] = None
        self._running_input = running_exes
        self._running_resolved: Optional[Set[str]] = None
        self._work_tree_cache: Dict[str, Optional[str]] = {}

    def _get_drive_refs(self) -> SystemRefIndex:
        if self._drive_refs is None:
            idx = self._system_refs if self._system_refs is not None else SystemRefIndex.collect(shortcut_roots=[self.root])
            self._drive_refs = SystemRefIndex(idx.refs_under(self.root, is_file=False))
        return self._drive_refs

    def _get_mcp_paths(self) -> Set[str]:
        if self._mcp_resolved is None:
            root_norm = _norm_abs_case(self.root)
            raw = collect_registered_mcp_paths(self.root) if self._mcp_input is None else self._mcp_input
            self._mcp_resolved = {
                _norm_abs_case(p)
                for p in raw
                if _norm_abs_case(p).startswith(root_norm + os.sep) or _norm_abs_case(p) == root_norm
            }
        return self._mcp_resolved

    def _get_running_exes(self) -> Set[str]:
        if self._running_resolved is None:
            if self._running_input is None:
                self._running_resolved = running_executable_paths()
            else:
                self._running_resolved = {str(p) for p in self._running_input if p}
        return self._running_resolved

    def _enclosing_git_root(self, folder: str) -> Optional[str]:
        pending: List[str] = []
        found: Optional[str] = None
        curr = os.path.abspath(folder)
        while True:
            key = os.path.normcase(curr)
            if key in self._work_tree_cache:
                found = self._work_tree_cache[key]
                break
            pending.append(key)
            if _holds_git_repository(curr):
                found = curr
                break
            if key == os.path.normcase(self.root):
                break
            parent = os.path.dirname(curr)
            if parent == curr:
                break
            curr = parent
        for k in pending:
            self._work_tree_cache[k] = found
        return found

    def _is_protected_location(self, target_path: str, rel_path: str) -> bool:
        if _norm_abs_case(target_path) == _norm_abs_case(self.root):
            return True
        if find_unit_root(target_path, self.root) is not None:
            return True
        parts = [p for p in rel_path.replace("\\", "/").split("/") if p and p != "."]
        if len(parts) <= 1 and is_protected_root_dir(rel_path):
            return True
        for ancestor in parts[:-1]:
            low = ancestor.lower()
            if low in _NON_BLOCKING_ANCESTORS:
                continue
            if is_protected_root_dir(low):
                return True
        return False

    def _evaluate_safety(self, target_path: str, rel_path: str) -> Tuple[bool, Optional[str], str]:
        """Returns (reclaimable, blocked_reason, detail)."""
        if self._is_protected_location(target_path, rel_path):
            return False, "protected", "Inside a protected system, app, or learning-unit path"

        running_matches = executables_under_path(target_path, self._get_running_exes())
        if running_matches:
            exe_names = sorted({os.path.basename(m) for m in running_matches})
            return False, "active_process", f"Active process binary running: {', '.join(exe_names[:3])}"

        norm_target = _norm_abs_case(target_path)
        prefix = norm_target + os.sep
        for mcp_path in self._get_mcp_paths():
            if mcp_path == norm_target or mcp_path.startswith(prefix) or norm_target.startswith(mcp_path + os.sep):
                return False, "mcp_server", f"Referenced by MCP configuration ({mcp_path})"

        # Check target folder and enclosing folders (below root) against SystemRefIndex
        curr = os.path.abspath(target_path)
        root_key = os.path.normcase(self.root)
        while os.path.normcase(curr) != root_key:
            refs = self._get_drive_refs().check_folder(curr, is_file=False)
            if refs:
                return False, "system_referenced", f"Used by system/app: {summarize_system_refs(refs)}"
            parent = os.path.dirname(curr)
            if parent == curr or not os.path.normcase(parent).startswith(root_key):
                break
            curr = parent

        git_root = self._enclosing_git_root(os.path.dirname(target_path))
        if git_root is not None:
            tracked = _git_tracks_dir(git_root, target_path)
            if tracked is True:
                return False, "git_tracked", "Contains git-tracked files"
            if tracked is None:
                return False, "git_unverified", "Inside git repository where tracked state could not be verified"

        return True, None, "Untracked and safe to reclaim"

    def _make_finding(self, path: str, kind: str) -> Optional[BloatFinding]:
        rel = os.path.relpath(path, self.root).replace("\\", "/")
        files, dirs, nominal, file_alloc = measure_bloat_tree(path, self.cluster_size)
        dir_alloc = dirs * self.cluster_size
        total_phys = file_alloc + dir_alloc
        if total_phys < self.min_physical_bytes:
            return None
        slack = max(0, total_phys - nominal)
        slack_pct = round((slack / total_phys * 100.0), 2) if total_phys > 0 else 0.0
        reclaimable, blocked_reason, detail = self._evaluate_safety(path, rel)
        return BloatFinding(
            path=os.path.abspath(path),
            rel_path=rel,
            kind=kind,
            label=_KIND_LABELS.get(kind, kind),
            file_count=files,
            dir_count=dirs,
            nominal_bytes=nominal,
            allocated_bytes=file_alloc,
            dir_allocated_bytes=dir_alloc,
            total_physical_bytes=total_phys,
            slack_bytes=slack,
            slack_percentage=slack_pct,
            reclaimable=reclaimable,
            blocked_reason=blocked_reason,
            detail=detail,
            rebuild_hint=_KIND_REBUILD_HINTS.get(kind, ""),
        )

    def _collect_rust_target_findings(self, target_dir: str, out: List[BloatFinding], depth: int = 0) -> None:
        running = executables_under_path(target_dir, self._get_running_exes())
        if not running or depth >= 2:
            finding = self._make_finding(target_dir, "rust-target")
            if finding is not None:
                out.append(finding)
            return

        target_norm = _norm_abs_case(target_dir)
        has_direct_running = any(_norm_abs_case(os.path.dirname(exe)) == target_norm for exe in running)
        if has_direct_running:
            finding = self._make_finding(target_dir, "rust-target")
            if finding is not None:
                out.append(finding)
            return

        subdirs: List[os.DirEntry[str]] = []
        try:
            with os.scandir(_win32_long_path(target_dir)) as it:
                for entry in it:
                    if not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                        subdirs.append(entry)
        except OSError:
            subdirs = []

        if not subdirs:
            finding = self._make_finding(target_dir, "rust-target")
            if finding is not None:
                out.append(finding)
            return

        for sub in sorted(subdirs, key=lambda e: e.name.lower()):
            sub_running = executables_under_path(sub.path, running)
            if sub_running and sub.name.lower() not in ("debug", "release") and depth == 0:
                self._collect_rust_target_findings(sub.path, out, depth=depth + 1)
            else:
                finding = self._make_finding(sub.path, "rust-target")
                if finding is not None:
                    out.append(finding)

    @staticmethod
    def _classify_dir(
        dir_path: str,
        dir_name: str,
        rel_path: str,
        parent_files_lower: Set[str],
    ) -> Optional[str]:
        low = dir_name.lower()
        rel_norm = rel_path.replace("\\", "/").lower().strip("/")

        if rel_norm == "share/doc/rust/html" or rel_norm.endswith("/share/doc/rust/html"):
            return "toolchain-docs"
        if low == "node_modules" and "package.json" in parent_files_lower:
            return "node-modules"
        if low == "vendor" and "composer.json" in parent_files_lower:
            return "composer-vendor"
        if low in _WEB_BUILD_DIR_NAMES and "package.json" in parent_files_lower:
            return "web-build"
        if low == "target" and "cargo.toml" in parent_files_lower:
            return "rust-target"

        # Inspect directory's own immediate entries only for pyvenv.cfg or Cargo CACHEDIR.TAG
        if low in ("venv", ".venv") or "venv" in low or "env" in low or os.path.isfile(os.path.join(dir_path, "pyvenv.cfg")):
            if os.path.isfile(_win32_long_path(os.path.join(dir_path, "pyvenv.cfg"))):
                return "python-venv"
        if os.path.isfile(_win32_long_path(os.path.join(dir_path, "CACHEDIR.TAG"))):
            if _read_cachedir_tag_cargo(dir_path):
                return "rust-target"
        return None

    def scan(self) -> Dict[str, Any]:
        """Runs a single pruned tree walk returning bloat findings and linked worktree advisories."""
        findings: List[BloatFinding] = []
        raw_worktrees: List[Tuple[str, str, str, str, bool]] = []

        stack: List[Tuple[str, Set[str]]] = []
        # Seed immediate entries of self.root cleanly
        try:
            with os.scandir(_win32_long_path(self.root)) as it:
                root_entries = list(it)
        except OSError:
            root_entries = []

        root_files_lower = {
            e.name.lower()
            for e in root_entries
            if not e.is_symlink() and e.is_file(follow_symlinks=False)
        }
        for e in root_entries:
            try:
                if e.is_symlink() or not e.is_dir(follow_symlinks=False):
                    continue
                if e.name.lower() in _WALK_PRUNE_DIRS:
                    continue
                stack.append((e.path, root_files_lower))
            except OSError:
                continue

        while stack:
            curr_dir, parent_files_lower = stack.pop()
            dir_name = os.path.basename(curr_dir)
            rel_path = os.path.relpath(curr_dir, self.root).replace("\\", "/")

            kind = self._classify_dir(curr_dir, dir_name, rel_path, parent_files_lower)
            if kind is not None:
                if kind in self.kinds:
                    if kind == "rust-target":
                        self._collect_rust_target_findings(curr_dir, findings)
                    else:
                        finding = self._make_finding(curr_dir, kind)
                        if finding is not None:
                            findings.append(finding)
                # Always stop descent inside recognized bloat/dependency containers
                continue

            try:
                with os.scandir(_win32_long_path(curr_dir)) as it:
                    entries = list(it)
            except OSError:
                continue

            curr_files_lower: Set[str] = set()
            subdirs: List[str] = []
            dot_git_file: Optional[str] = None
            for e in entries:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_file(follow_symlinks=False):
                        nl = e.name.lower()
                        curr_files_lower.add(nl)
                        if nl == ".git":
                            dot_git_file = e.path
                    elif e.is_dir(follow_symlinks=False):
                        if e.name.lower() not in _WALK_PRUNE_DIRS:
                            subdirs.append(e.path)
                except OSError:
                    continue

            if dot_git_file is not None:
                common_dir = _parse_linked_worktree_gitdir(dot_git_file)
                if common_dir is not None:
                    branch, dirty = _inspect_worktree_git(curr_dir)
                    raw_worktrees.append((curr_dir, rel_path, common_dir, branch, dirty))

            for sub in subdirs:
                stack.append((sub, curr_files_lower))

        findings.sort(key=lambda f: f.total_physical_bytes, reverse=True)

        finding_paths_norm = [_norm_abs_case(f.path) for f in findings]
        worktrees: List[LinkedWorktreeInfo] = []
        for wt_dir, wt_rel, common_dir, branch, dirty in sorted(raw_worktrees, key=lambda x: x[1].lower()):
            wt_prefix = _norm_abs_case(wt_dir) + os.sep
            has_artifacts = any(fp.startswith(wt_prefix) for fp in finding_paths_norm)
            hint = (
                f"Dirty ({branch}) — inspect changes before removing"
                if dirty
                else f"Clean linked worktree ({branch}) — remove with 'git worktree remove \"{wt_dir}\"'"
            )
            worktrees.append(
                LinkedWorktreeInfo(
                    path=os.path.abspath(wt_dir),
                    rel_path=wt_rel,
                    git_common_dir=common_dir,
                    branch=branch,
                    is_dirty=dirty,
                    has_build_artifacts=has_artifacts,
                    hint=hint,
                )
            )

        by_kind: Dict[str, Dict[str, int]] = {}
        total_files = 0
        total_dirs = 0
        total_nominal = 0
        total_alloc = 0
        total_dir_alloc = 0
        total_physical = 0
        reclaimable_physical = 0
        reclaimable_count = 0
        blocked_count = 0

        for f in findings:
            total_files += f.file_count
            total_dirs += f.dir_count
            total_nominal += f.nominal_bytes
            total_alloc += f.allocated_bytes
            total_dir_alloc += f.dir_allocated_bytes
            total_physical += f.total_physical_bytes
            if f.reclaimable:
                reclaimable_count += 1
                reclaimable_physical += f.total_physical_bytes
            else:
                blocked_count += 1
            bucket = by_kind.setdefault(
                f.kind,
                {"count": 0, "files": 0, "nominal_bytes": 0, "physical_bytes": 0, "reclaimable_physical_bytes": 0},
            )
            bucket["count"] += 1
            bucket["files"] += f.file_count
            bucket["nominal_bytes"] += f.nominal_bytes
            bucket["physical_bytes"] += f.total_physical_bytes
            if f.reclaimable:
                bucket["reclaimable_physical_bytes"] += f.total_physical_bytes

        return {
            "root": self.root,
            "filesystem": self.fs_info.to_dict(),
            "cluster_size": self.cluster_size,
            "cluster_size_source": self.cluster_size_source,
            "cluster_size_label": cluster_size_text(self.cluster_size),
            "findings": [f.to_dict() for f in findings],
            "linked_worktrees": [w.to_dict() for w in worktrees],
            "summary": {
                "total_findings": len(findings),
                "reclaimable_count": reclaimable_count,
                "blocked_count": blocked_count,
                "total_files": total_files,
                "total_dirs": total_dirs,
                "total_nominal_bytes": total_nominal,
                "total_allocated_bytes": total_alloc,
                "total_dir_allocated_bytes": total_dir_alloc,
                "total_physical_bytes": total_physical,
                "reclaimable_physical_bytes": reclaimable_physical,
                "linked_worktrees_count": len(worktrees),
                "by_kind": by_kind,
            },
        }


def _rmtree_readonly_handler(func: Any, path: str, exc_info: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(path)
    except OSError:
        pass


def purge_bloat(report: Dict[str, Any], root: str, dry_run: bool = True) -> Dict[str, Any]:
    """Deletes reclaimable bloat directories from `report` when `dry_run=False`; never deletes blocked items."""
    root_norm = _norm_abs_case(root)
    planned: List[Dict[str, Any]] = []
    skipped_blocked = 0

    for item in report.get("findings", []):
        if not item.get("reclaimable", False):
            skipped_blocked += 1
            continue
        target = str(item.get("path", ""))
        target_norm = _norm_abs_case(target)
        if not target_norm.startswith(root_norm + os.sep):
            skipped_blocked += 1
            continue
        planned.append(item)

    if dry_run:
        return {
            "dry_run": True,
            "planned_count": len(planned),
            "skipped_blocked": skipped_blocked,
            "reclaimable_physical_bytes": sum(int(i.get("total_physical_bytes", 0)) for i in planned),
            "items": [i.get("rel_path", i.get("path")) for i in planned],
        }

    deleted_count = 0
    failed_count = 0
    reclaimed_physical = 0
    results: List[Dict[str, Any]] = []

    for item in planned:
        target = str(item["path"])
        rel = str(item.get("rel_path", target))
        phys = int(item.get("total_physical_bytes", 0))
        if not os.path.exists(target):
            results.append({"rel_path": rel, "status": "MISSING", "reclaimed_physical_bytes": 0})
            continue
        try:
            shutil.rmtree(_win32_rmtree_long_path(target), onerror=_rmtree_readonly_handler)
        except OSError as exc:
            failed_count += 1
            results.append({"rel_path": rel, "status": "FAILED", "error": str(exc), "reclaimed_physical_bytes": 0})
            continue
        if os.path.exists(target):
            failed_count += 1
            results.append({"rel_path": rel, "status": "FAILED", "error": "Directory still exists", "reclaimed_physical_bytes": 0})
        else:
            deleted_count += 1
            reclaimed_physical += phys
            results.append({"rel_path": rel, "status": "DELETED", "reclaimed_physical_bytes": phys})

    return {
        "dry_run": False,
        "deleted_count": deleted_count,
        "failed_count": failed_count,
        "skipped_blocked": skipped_blocked,
        "reclaimed_physical_bytes": reclaimed_physical,
        "results": results,
    }


__all__ = [
    "BLOAT_KINDS",
    "BloatFinding",
    "BloatScanner",
    "LinkedWorktreeInfo",
    "measure_bloat_tree",
    "purge_bloat",
]
