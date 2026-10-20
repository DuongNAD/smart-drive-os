"""smart_drive.core.git_locks - Keep git's fsmonitor daemon from locking the folders smart-drive moves.

A repository with ``core.fsmonitor=true`` starts a background ``git fsmonitor--daemon`` the first time git
refreshes its index (``status``, ``ls-files``, ``diff``, ``check-ignore``, ``rm``...). The daemon keeps a handle
open on the work tree, and on Windows that handle makes every rename of the repository's folder, or of a folder
above it, fail with ``[WinError 5] Access is denied``. Two defenses:

- Every git command smart-drive runs carries ``NO_FSMONITOR``, so smart-drive never starts a daemon itself.
- ``release_git_locks`` stops the daemons that something else started (an IDE, the user's terminal, a global
  ``core.fsmonitor=true``) in the repositories inside a folder, right before the folder is renamed. Git starts a
  daemon again by itself the next time a repository that enables it runs ``git status``.

Nothing here raises: this step prepares a move and must never be the reason a move fails.

100% Python Standard Library (Python >= 3.9).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Deque, Iterable, List, Optional, Sequence, Set, Tuple

# Goes right after the git executable (``git -c core.fsmonitor=false -C <repo> ...``). Overrides the repository's,
# the user's and the system's configuration for that one command and for any git it starts.
NO_FSMONITOR: Tuple[str, str] = ("-c", "core.fsmonitor=false")

STATUS_TIMEOUT = 5.0  # seconds for one `git fsmonitor--daemon status`
STOP_TIMEOUT = 10.0  # `stop` returns once the daemon has quit, which takes a moment
RELEASE_BUDGET = 30.0  # seconds for finding and stopping every daemon under one folder
MAX_WALK_DIRS = 1500  # folders read while looking for repositories
MAX_WALK_DEPTH = 6  # how far below the moved folder a repository is looked for
MAX_REPOSITORIES = 200  # repositories asked about per folder
MAX_LINKED_WORKTREES = 50  # linked worktrees followed per repository
_STATUS_WORKERS = 8  # `status` calls made at once; each is a git process of its own

# Dependency and build trees hold no repositories worth asking about, and reading them is most of the cost.
_PRUNE = frozenset({
    ".git", "node_modules", ".venv", "venv", "site-packages", "__pycache__", "target",
    "$recycle.bin", "system volume information",
})
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: junctions and symlinks on Windows
# Set when this runs from a git hook; they would send `git -C <folder>` to the wrong repository
_GIT_ENV_UNSET = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")

# (repository, git arguments, timeout) -> (exit code, output). Tests substitute a fake one.
GitRunner = Callable[[str, Sequence[str], float], Tuple[int, str]]


def _git_runner() -> Optional[GitRunner]:
    """Runs git inside a repository without prompts or locks; None when git is not installed."""
    git = shutil.which("git")
    if git is None:
        return None
    env = {key: value for key, value in os.environ.items() if key not in _GIT_ENV_UNSET}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")

    def run(repo: str, args: Sequence[str], timeout: float) -> Tuple[int, str]:
        try:
            done = subprocess.run(
                [git, *NO_FSMONITOR, "-C", repo, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return 1, ""
        return done.returncode, done.stdout or ""

    return run


def _is_plain_folder(path: str) -> bool:
    """A real folder: not a file, not a symlink, not a junction (renaming those moves the link, not a work tree)."""
    try:
        if os.path.islink(path) or not os.path.isdir(path):
            return False
        return not getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE_POINT
    except OSError:
        return False


def _is_real_folder(entry: os.DirEntry) -> bool:
    """A folder entry that is not a symlink or a junction."""
    try:
        if not entry.is_dir(follow_symlinks=False):
            return False
        if os.name == "nt":  # a junction is a folder to is_dir; only the reparse flag tells it apart
            return not entry.stat(follow_symlinks=False).st_file_attributes & _REPARSE_POINT
    except OSError:
        return False
    return True


def _linked_work_trees(repo: str) -> List[str]:
    """Where the linked worktrees of ``repo`` live, read from ``.git/worktrees/<name>/gitdir``.

    The daemon of a linked worktree also watches the main repository's git directory, so it keeps the main
    repository's folder from being moved wherever the worktree itself lives.
    """
    registry = os.path.join(repo, ".git", "worktrees")
    try:
        names = sorted(os.listdir(registry))[:MAX_LINKED_WORKTREES]
    except OSError:  # no linked worktrees, or `.git` is a file (this repository is a linked worktree itself)
        return []
    trees: List[str] = []
    for name in names:
        try:
            with open(os.path.join(registry, name, "gitdir"), encoding="utf-8", errors="replace") as handle:
                recorded = handle.readline().strip()  # "<worktree>/.git"
        except OSError:
            continue
        if not recorded:
            continue
        if not os.path.isabs(recorded):  # git 2.48 can record it relative to the registry entry
            recorded = os.path.join(registry, name, recorded)
        tree = os.path.dirname(os.path.normpath(recorded))
        if os.path.isdir(tree):
            trees.append(tree)
    return trees


def find_work_trees(
    folder: str,
    max_dirs: int = MAX_WALK_DIRS,
    max_depth: int = MAX_WALK_DEPTH,
    max_repositories: int = MAX_REPOSITORIES,
    deadline: Optional[float] = None,
) -> List[str]:
    """The git work trees at ``folder`` and below it, plus the linked worktrees of those wherever they live.

    A work tree is a folder with a ``.git`` entry: a clone, a linked worktree or a submodule. The walk is breadth
    first and bounded by folders read, depth, repositories found and ``deadline`` (a ``time.monotonic`` value);
    it does not enter ``.git``, dependency or build trees, symlinks or junctions.
    """
    found: List[str] = []
    seen: Set[str] = set()

    def add(path: str) -> None:
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            found.append(path)

    queue: Deque[Tuple[str, int]] = deque([(os.path.abspath(folder), 0)])
    read = 0
    while queue and read < max_dirs and len(found) < max_repositories:
        if deadline is not None and time.monotonic() > deadline:
            break
        path, depth = queue.popleft()
        read += 1
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name == ".git":
                        add(path)
                    elif depth < max_depth and entry.name.lower() not in _PRUNE and _is_real_folder(entry):
                        queue.append((entry.path, depth + 1))
        except OSError:
            continue

    for repo in list(found):
        for tree in _linked_work_trees(repo):
            add(tree)
    return found


def _is_watching(run: GitRunner, repo: str, deadline: float) -> bool:
    """True when ``git fsmonitor--daemon status`` says a daemon watches ``repo`` (it exits 0 only then)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    try:
        code, _output = run(repo, ("fsmonitor--daemon", "status"), min(STATUS_TIMEOUT, remaining))
    except Exception:  # a broken git or runner: this repository counts as not watched
        return False
    return code == 0


def watching_repositories(
    folders: Iterable[str],
    run_git: Optional[GitRunner] = None,
    budget: float = RELEASE_BUDGET,
) -> List[str]:
    """The repositories at and below ``folders`` that a running fsmonitor daemon is watching. Changes nothing."""
    try:
        run = run_git or _git_runner()
        if run is None:
            return []
        deadline = time.monotonic() + budget
        repos: List[str] = []
        seen: Set[str] = set()
        for folder in folders:
            if not _is_plain_folder(folder):
                continue
            for repo in find_work_trees(folder, deadline=deadline):
                if os.path.normcase(repo) not in seen:
                    seen.add(os.path.normcase(repo))
                    repos.append(repo)
        if len(repos) < 2:
            watched = [_is_watching(run, repo, deadline) for repo in repos]
        else:
            with ThreadPoolExecutor(max_workers=min(_STATUS_WORKERS, len(repos))) as pool:
                watched = list(pool.map(lambda repo: _is_watching(run, repo, deadline), repos))
        return [repo for repo, flag in zip(repos, watched) if flag]
    except Exception:
        return []


def release_git_locks(folder: str, run_git: Optional[GitRunner] = None, budget: float = RELEASE_BUDGET) -> List[str]:
    """Stops the fsmonitor daemon of every repository at and below ``folder``, which is about to be renamed.

    Returns the repositories whose daemon was stopped. Without git, with nothing running or on any error it
    returns what it managed to stop (possibly nothing) and the move goes on: a daemon that is still there makes the
    rename fail with the usual "access denied", which the caller explains.
    """
    stopped: List[str] = []
    if not _is_plain_folder(folder):  # a file, a link or a folder that is gone: nothing can be watching it
        return stopped
    try:
        run = run_git or _git_runner()
        if run is None:
            return stopped
        deadline = time.monotonic() + budget
        for repo in watching_repositories([folder], run_git=run, budget=budget):
            if time.monotonic() > deadline:
                break
            try:
                code, _output = run(repo, ("fsmonitor--daemon", "stop"), STOP_TIMEOUT)
            except Exception:
                continue
            if code == 0:
                stopped.append(repo)
    except Exception:
        pass
    return stopped
