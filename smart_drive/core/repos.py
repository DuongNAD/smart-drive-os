"""smart_drive.core.repos - Find clones of the same project, and tell mirrors apart from duplicates.

A project may live once on the portable SSD and once on each machine the SSD is plugged into. Those copies
are MIRRORS: they are expected, and the report only shows whether they are in sync. Two clones of the same
project in the same place (the SSD, or this machine's internal drives taken together) are DUPLICATES: the
report picks one to keep and says how to fold each other clone in, either by removing a redundant copy of
the same branch or by recreating another branch as a `git worktree` of the kept clone. Linked worktrees of
one clone share its object store and are never duplicates.

Read-only: nothing is fetched, written or deleted. Git runs with optional locks and prompts disabled.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PORTABLE = "portable"
MACHINE = "machine"

# Skipped wherever they appear: dependency trees and build output hold no clones worth reporting, and walking
# them is most of the cost (a Rust `target/` alone can hold 170k files).
ALWAYS_PRUNE = frozenset({
    "node_modules", "site-packages", "__pycache__", "target", "$recycle.bin", "system volume information",
})
# Skipped only right under a scan root (a drive or a home folder), where these names mean OS or app state.
TOP_LEVEL_PRUNE = frozenset({
    "windows", "program files", "program files (x86)", "programdata", "appdata", "application data",
    "library", "recovery", "perflogs", "msocache",
})
# Hidden folders (".cargo", ".cache", ".venv", ".gitnexus" ...) are skipped too: clones inside them belong to
# the tools that manage them.

# Cloud-sync drives and folders hold files on demand: `git status` there is slow (it timed out on Google Drive)
# and can download whole trees. They are left out of the default scan; pass one explicitly to include it.
CLOUD_NAMES = ("google drive", "my drive", "dropbox", "icloud drive", "icloudrive", "box", "pcloud drive", "mega")


def _is_cloud_name(name: str) -> bool:
    """True for a volume label or folder name such as "Google Drive", "Dropbox (Personal)" or "OneDrive - Contoso"."""
    low = name.strip().lower()
    return low.startswith("onedrive") or any(low == n or low.startswith(n + " ") for n in CLOUD_NAMES)

DEFAULT_MAX_DEPTH = 12
GIT_TIMEOUT = 30
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: junctions and symlinks on Windows


@dataclass
class RepoCopy:
    """One working tree on disk: a clone, or a linked worktree of one."""

    path: str
    location: str = MACHINE
    common_dir: str = ""
    is_worktree: bool = False
    superproject: str = ""  # set for a submodule checkout, which its parent repository manages
    remote: str = ""
    identity: str = ""
    branch: str = ""  # empty when HEAD is detached
    head: str = ""
    head_time: int = 0
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    changes: int = 0  # staged, modified and untracked files
    stashes: int = 0
    unpushed: int = 0  # commits on local branches that no remote-tracking branch contains
    local_branches: List[str] = field(default_factory=list)
    git_bytes: int = 0  # object store size; 0 for linked worktrees
    error: str = ""

    @property
    def local_work(self) -> List[str]:
        """What would be lost if this working tree disappeared, as short phrases."""
        work = []
        if self.changes:
            work.append(f"{self.changes} uncommitted file(s)")
        if self.stashes:
            work.append(f"{self.stashes} stash(es)")
        if self.unpushed:
            work.append(f"{self.unpushed} unpushed commit(s)")
        return work


@dataclass
class Suggestion:
    """What to do with one clone of a duplicate group."""

    path: str
    action: str  # keep | remove | worktree | blocked
    reason: str = ""
    command: str = ""
    branch: str = ""


@dataclass
class DuplicateGroup:
    identity: str
    location: str
    suggestions: List[Suggestion]


@dataclass
class MirrorGroup:
    identity: str
    in_sync: bool
    copies: List[Dict[str, Any]]


@dataclass
class RepoReport:
    roots: List[str]
    portable_roots: List[str]
    git_version: str
    clones: int
    worktrees: int
    submodules: int
    copies: List[RepoCopy]
    duplicates: List[DuplicateGroup]
    mirrors: List[MirrorGroup]
    stale_worktrees: List[Dict[str, str]]
    unreadable: List[Dict[str, str]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------------------------------------

def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return True


def _is_within(path: str, root: str) -> bool:
    p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:  # different drives
        return False


def find_repositories(roots: Iterable[str], max_depth: int = DEFAULT_MAX_DEPTH) -> List[str]:
    """Every folder under the roots that has a `.git` entry (a clone, a worktree or a submodule)."""
    found: Dict[str, str] = {}
    for root in roots:
        stack: List[Tuple[str, int]] = [(os.path.abspath(root), 0)]
        while stack:
            path, depth = stack.pop()
            try:
                with os.scandir(path) as it:
                    entries = list(it)
            except OSError:
                continue
            if any(e.name == ".git" for e in entries):
                found.setdefault(os.path.normcase(path), path)
            if depth >= max_depth:
                continue
            for entry in entries:
                name = entry.name.lower()
                if name.startswith(".") or name in ALWAYS_PRUNE:
                    continue
                if depth == 0 and (name in TOP_LEVEL_PRUNE or _is_cloud_name(name)):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False) or _is_link(entry):
                        continue
                except OSError:
                    continue
                stack.append((entry.path, depth + 1))
    return sorted(found.values(), key=str.lower)


def default_scan_roots(portable_root: Optional[str]) -> List[str]:
    """The portable SSD, the user's home folder, and (on Windows) every other non-system, non-cloud drive."""
    roots: List[str] = []
    if portable_root:
        roots.append(portable_root)
    roots.append(os.path.expanduser("~"))
    if os.name == "nt":
        try:
            from smart_drive.core.drive_detector import get_backend, list_secondary_drive_letters

            backend = get_backend()
            for letter in list_secondary_drive_letters():
                root = letter.rstrip("\\/") + "\\"
                try:
                    label = str(backend.get_volume_info(root).get("volume_label", ""))
                except Exception:  # an unready drive (card reader, empty DVD) has no volume info
                    continue
                if not _is_cloud_name(label):
                    roots.append(root)
        except Exception:  # drive probing is best effort; home and the SSD are still scanned
            pass
    unique: Dict[str, str] = {}
    for root in roots:
        if os.path.isdir(root):
            unique.setdefault(os.path.normcase(os.path.abspath(root)), os.path.abspath(root))
    # A root inside another root would be walked twice.
    kept: List[str] = []
    for key in sorted(unique, key=len):
        if not any(_is_within(key, other) for other in kept):
            kept.append(key)
    return [unique[k] for k in kept]


# ---------------------------------------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------------------------------------

def _git(path: str, *args: str) -> Tuple[int, str, str]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        res = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return res.returncode, res.stdout, res.stderr


def git_version() -> str:
    """Installed git version ("2.55.0.windows.3"), or "" when git is not on PATH."""
    if shutil.which("git") is None:
        return ""
    code, out, _ = _git(".", "--version")
    return out.strip().replace("git version ", "") if code == 0 else ""


def supports_relative_worktrees(version: str) -> bool:
    """`git worktree add --relative-paths` arrived in git 2.48."""
    match = re.match(r"(\d+)\.(\d+)", version)
    return bool(match) and (int(match.group(1)), int(match.group(2))) >= (2, 48)


def normalize_remote(url: str) -> str:
    """One spelling per project: https, ssh and scp-style URLs of the same repository compare equal."""
    u = url.strip()
    if not u:
        return ""
    if u.lower().startswith("file://"):
        u = u[7:]
    if os.path.isabs(u) or re.match(r"^[A-Za-z]:[\\/]", u):
        local = os.path.normcase(os.path.abspath(u)).replace("\\", "/").rstrip("/")
        return local[:-4] if local.endswith(".git") else local
    u = u.lower()
    u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", u)  # scheme
    u = re.sub(r"^[^@/]+@", "", u)  # user@
    u = re.sub(r"^([^/:]+):(\d+)/", r"\1/", u)  # host:port/
    u = re.sub(r"^([^/:]+):", r"\1/", u)  # scp-style host:path
    u = u.rstrip("/")
    return u[:-4] if u.endswith(".git") else u


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")


def _int(text: str) -> int:
    text = text.strip()
    return int(text) if text.isdigit() else 0


def _declared_submodule(superproject: str, path: str) -> bool:
    """True when the parent's .gitmodules lists this path. A repository that was only `git add`-ed into its
    parent (an embedded gitlink, no .gitmodules entry) cannot be restored from the parent, so it stays a clone."""
    code, out, _ = _git(superproject, "config", "-f", ".gitmodules", "--get-regexp", "^submodule[.].*[.]path$")
    if code != 0:
        return False
    declared = {os.path.normcase(line.split(" ", 1)[1].strip()) for line in out.splitlines() if " " in line}
    return os.path.normcase(os.path.relpath(path, superproject)) in declared


def inspect_repository(path: str, portable_roots: Sequence[str] = ()) -> RepoCopy:
    """State of one working tree, read without locks or network access."""
    copy = RepoCopy(path=path)
    copy.location = PORTABLE if any(_is_within(path, r) for r in portable_roots) else MACHINE

    code, out, err = _git(
        path, "rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir",
        "--show-superproject-working-tree",
    )
    if code != 0:
        copy.error = _first_line(err) or "not a readable git repository"
        return copy
    dirs = out.splitlines()
    copy.common_dir = os.path.normpath(dirs[0].strip()) if dirs else ""
    git_dir = os.path.normpath(dirs[1].strip()) if len(dirs) > 1 else copy.common_dir
    copy.is_worktree = os.path.normcase(git_dir) != os.path.normcase(copy.common_dir)
    superproject = os.path.normpath(dirs[2].strip()) if len(dirs) > 2 and dirs[2].strip() else ""
    if superproject and _declared_submodule(superproject, path):
        copy.superproject = superproject

    code, out, _ = _git(path, "config", "--get-regexp", r"^remote\..*\.url$")
    remotes = dict(line.split(" ", 1) for line in out.splitlines() if " " in line) if code == 0 else {}
    copy.remote = remotes.get("remote.origin.url") or (next(iter(remotes.values())) if remotes else "")

    show_stash = True
    code, out, err = _git(path, "status", "--porcelain=v2", "--branch", "--show-stash")
    if code != 0 and "show-stash" in err:  # git older than 2.35
        show_stash = False
        code, out, err = _git(path, "status", "--porcelain=v2", "--branch")
    if code != 0:
        copy.error = _first_line(err) or "git status failed"
        return copy
    for line in out.splitlines():
        if line.startswith("# branch.oid "):
            oid = line[13:].strip()
            copy.head = "" if oid == "(initial)" else oid
        elif line.startswith("# branch.head "):
            head = line[14:].strip()
            copy.branch = "" if head == "(detached)" else head
        elif line.startswith("# branch.upstream "):
            copy.upstream = line[18:].strip()
        elif line.startswith("# branch.ab "):
            match = re.match(r"# branch\.ab \+(\d+) -(\d+)", line)
            if match:
                copy.ahead, copy.behind = int(match.group(1)), int(match.group(2))
        elif line.startswith("# stash "):
            copy.stashes = _int(line[8:])
        elif line[:2] in ("1 ", "2 ", "u ", "? "):
            copy.changes += 1
    if not show_stash:
        code, out, _ = _git(path, "rev-list", "--walk-reflogs", "--count", "refs/stash")
        copy.stashes = _int(out) if code == 0 else 0

    code, out, _ = _git(path, "rev-list", "--count", "--branches", "--not", "--remotes")
    copy.unpushed = _int(out) if code == 0 else 0

    code, out, _ = _git(path, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    copy.local_branches = [b for b in out.splitlines() if b] if code == 0 else []

    if copy.head:
        code, out, _ = _git(path, "log", "-1", "--format=%ct")
        copy.head_time = _int(out) if code == 0 else 0

    if copy.remote:
        copy.identity = normalize_remote(copy.remote)
    elif copy.head:
        code, out, _ = _git(path, "rev-list", "--max-parents=0", "HEAD")
        roots = sorted(r for r in out.split() if r) if code == 0 else []
        copy.identity = f"root:{roots[0]}" if roots else f"path:{os.path.normcase(copy.common_dir)}"
    else:
        copy.identity = f"path:{os.path.normcase(copy.common_dir)}"

    if not copy.is_worktree:
        code, out, _ = _git(path, "count-objects", "-v")
        if code == 0:
            stats = dict(line.split(": ", 1) for line in out.splitlines() if ": " in line)
            copy.git_bytes = sum(_int(stats.get(k, "0")) for k in ("size", "size-pack")) * 1024
    return copy


def _list_worktrees(path: str) -> List[Dict[str, str]]:
    """`git worktree list --porcelain`, one dict per worktree (the main one first)."""
    code, out, _ = _git(path, "worktree", "list", "--porcelain")
    if code != 0:
        return []
    worktrees: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if current:
                worktrees.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return worktrees


# ---------------------------------------------------------------------------------------------------------
# Grouping and suggestions
# ---------------------------------------------------------------------------------------------------------

def _state(copy: RepoCopy) -> str:
    parts = copy.local_work
    if copy.behind:
        parts.append(f"{copy.behind} behind upstream")
    return ", ".join(parts) or "clean"


def _suggest(keeper: RepoCopy, other: RepoCopy, linked: int, relative_flag: str) -> Suggestion:
    branch = other.branch or "(detached)"
    blockers = list(other.local_work)
    if linked:
        blockers.append(f"{linked} linked worktree(s) depend on it")
    if not other.remote:
        blockers.append("no remote, so its history exists only on this disk")
    if blockers:
        return Suggestion(other.path, "blocked", "; ".join(blockers) + ". Commit and push first.", branch=branch)
    nested = f" It is nested inside {keeper.path}." if _is_within(other.path, keeper.path) else ""
    same = (other.branch and other.branch == keeper.branch) or (not other.branch and other.head == keeper.head)
    if same:
        return Suggestion(
            other.path,
            "remove",
            "Same branch as the clone kept. Check ignored files (.env, data, models, venvs) before deleting." + nested,
            branch=branch,
        )
    target = other.branch or f"--detach {other.head}"
    command = (
        f'git -C "{keeper.path}" fetch && '
        f'git -C "{keeper.path}" worktree add{relative_flag} "{other.path}" {target}'
    )
    return Suggestion(
        other.path,
        "worktree",
        "Different branch. Move this copy away (keep its ignored files), then recreate it as a worktree." + nested,
        command,
        branch=branch,
    )


def _mirror(identity: str, reps: List[RepoCopy]) -> MirrorGroup:
    in_sync = len({r.head for r in reps}) == 1 and all(not r.changes and not r.unpushed for r in reps)
    copies = []
    for r in sorted(reps, key=lambda r: (r.location != PORTABLE, r.path.lower())):
        hints = [h for h, needed in (("commit", r.changes), ("push", r.unpushed), ("pull", r.behind)) if needed]
        copies.append({
            "location": r.location,
            "path": r.path,
            "branch": r.branch or "(detached)",
            "head": r.head[:12],
            "state": _state(r),
            "next": hints,
        })
    if not in_sync and not any(c["next"] for c in copies):
        # Different commits but nothing local to push: one side has not fetched what the other pushed.
        for c in copies:
            c["next"] = ["fetch to compare"]
    return MirrorGroup(identity, in_sync, copies)


def build_report(
    roots: Sequence[str],
    portable_roots: Sequence[str] = (),
    max_depth: int = DEFAULT_MAX_DEPTH,
    workers: int = 8,
) -> RepoReport:
    """Scan the roots and group every clone by project, place and object store."""
    version = git_version()
    paths = find_repositories(roots, max_depth=max_depth)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        copies = list(pool.map(lambda p: inspect_repository(p, portable_roots), paths))

    unreadable = [{"path": c.path, "error": c.error} for c in copies if c.error]

    # One entry per object store: a clone plus the linked worktrees found on disk.
    stores: Dict[str, List[RepoCopy]] = {}
    for c in copies:
        if not c.error and not c.superproject:
            stores.setdefault(os.path.normcase(c.common_dir), []).append(c)
    representatives: List[RepoCopy] = []
    linked_count: Dict[str, int] = {}
    stale: List[Dict[str, str]] = []
    for key, members in stores.items():
        members.sort(key=lambda c: (c.is_worktree, c.path.lower()))
        rep = members[0]
        representatives.append(rep)
        listed = _list_worktrees(rep.path)
        linked_count[key] = max(0, len([w for w in listed if "bare" not in w]) - 1)
        for wt in listed:
            if "prunable" in wt:
                stale.append({
                    "repository": rep.path,
                    "worktree": wt.get("worktree", ""),
                    "reason": wt.get("prunable", "") or "missing",
                    "command": f'git -C "{rep.path}" worktree prune',
                })

    by_identity: Dict[str, List[RepoCopy]] = {}
    for rep in representatives:
        by_identity.setdefault(rep.identity, []).append(rep)

    relative_flag = " --relative-paths" if supports_relative_worktrees(version) else ""
    duplicates: List[DuplicateGroup] = []
    mirrors: List[MirrorGroup] = []
    for identity, reps in sorted(by_identity.items()):
        for location in (PORTABLE, MACHINE):
            here = [r for r in reps if r.location == location]
            if len(here) < 2:
                continue
            keeper = max(here, key=lambda r: (r.head_time, -len(r.path)))
            suggestions = [Suggestion(
                keeper.path,
                "keep",
                "Most recent commit; worktrees for other branches go here.",
                branch=keeper.branch or "(detached)",
            )]
            for other in sorted((r for r in here if r is not keeper), key=lambda r: r.path.lower()):
                linked = linked_count.get(os.path.normcase(other.common_dir), 0)
                suggestions.append(_suggest(keeper, other, linked, relative_flag))
            duplicates.append(DuplicateGroup(identity, location, suggestions))
        if {r.location for r in reps} == {PORTABLE, MACHINE}:
            mirrors.append(_mirror(identity, reps))

    return RepoReport(
        roots=list(roots),
        portable_roots=list(portable_roots),
        git_version=version,
        clones=len(representatives),
        worktrees=sum(linked_count.values()),
        submodules=sum(1 for c in copies if c.superproject and not c.error),
        copies=copies,
        duplicates=duplicates,
        mirrors=mirrors,
        stale_worktrees=stale,
        unreadable=unreadable,
    )


__all__ = [
    "MACHINE",
    "PORTABLE",
    "DuplicateGroup",
    "MirrorGroup",
    "RepoCopy",
    "RepoReport",
    "Suggestion",
    "build_report",
    "default_scan_roots",
    "find_repositories",
    "inspect_repository",
    "normalize_remote",
    "supports_relative_worktrees",
]
