"""smart_drive.core.duplicates - 3-Phase SHA-256 Cascade Duplicate Detection Engine.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Implements:
1. Phase 1: Group by exact byte size (0 disk I/O, >78% elimination).
2. Phase 2: 8KB prefix/head+tail hash for size collisions (>98% elimination of remaining candidates).
3. Phase 3: Streaming full SHA-256 (64KB chunks) only for prefix collisions.
4. Duplicate grouping & space reclamation analysis with 512KB cluster allocation math.
5. Zero dependencies outside Python 3.9+ standard library.

Dependency, environment and cache trees (node_modules, site-packages, virtualenvs, folders tagged CACHEDIR.TAG, ...)
are not entered: their files are copies of one another by design, so they are never worth reclaiming, and on a real
drive they hold most of the files that share a size (a scan that opened every one of them took more than 15 minutes).
Neither are the folders Windows or an installed application depends on (`protected_by_system`, as in `organize`).
A scan of one folder of a drive (`drive_root`) is judged by the folders above it too: inside any of those it scans
nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_for
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

from smart_drive.core.learning_units import is_in_learning_unit
from smart_drive.core.config import (
    CLUSTER_SIZE,
    CLUSTER_SIZE_BYTES,
    DEFAULT_EXCLUDE_DIRS,
    SCAN_SKIPPED_DEPENDENCY_TREE,
    SCAN_SKIPPED_PROTECTED_BY_SYSTEM,
    calculate_allocated_bytes,
    calculate_slack_bytes,
)
from smart_drive.core.system_refs import (
    SystemRef,
    SystemRefIndex,
    format_protected_by_system_entry,
    summarize_system_refs,
)

logger = logging.getLogger(__name__)

EMPTY_FILE_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Folders holding dependency, environment, cache or version-control internals, matched case-insensitively on the
# folder's name. Thousands of small files in them are the same bytes over and over (every project's node_modules
# carries its own copy of the same package), so a "duplicate" there is expected, saves nothing a user would ever
# take, and breaks the program that installed it when removed.
DEPENDENCY_DIR_NAMES: FrozenSet[str] = frozenset({
    "node_modules", "bower_components", "jspm_packages", ".pnpm-store",  # JavaScript
    "site-packages", "dist-packages",  # Python packages
    "__pycache__", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",  # Python tool caches
    ".gradle", ".next", ".nuxt",  # build tool caches
    ".git", ".hg", ".svn",  # version-control internals
})
# A folder holding this file is a Python virtual environment, whatever it is called.
VENV_MARKER_FILE = "pyvenv.cfg"
# A folder holding this file, starting with this signature, is a cache directory (Cache Directory Tagging
# Specification, https://bford.info/cachedir/): Cargo tags its target/ folder, as do pytest, mypy, restic and more.
CACHE_TAG_FILE = "cachedir.tag"
CACHE_TAG_SIGNATURE = b"Signature: 8a477f597d28d172789f06886806bc55"

# Files hashed at the same time. What a hash waits for is the file being opened, not read: with Windows Defender
# scanning each file as it is opened that was ~16 files a second one after another, and overlapping the opens is an
# order of magnitude faster. `workers=1` hashes one file at a time, for a disk that dislikes parallel reads.
HASH_WORKERS = 8

# A long scan tells its caller how far it got, at most this often (seconds).
PROGRESS_INTERVAL_SECONDS = 2.0


# Set on the threads of a hashing pool: the event of the run they work for. Set when the run is abandoned (Ctrl-C, an
# error), it makes a thread stop reading a big file instead of finishing it.
_worker_state = threading.local()


def _run_abandoned() -> bool:
    event = getattr(_worker_state, "abandoned", None)
    return event is not None and event.is_set()


def compute_partial_hash(filepath: str, chunk_size: int = 8192) -> str:
    """Computes a quick partial SHA-256 hash using the head of the file.

    For files larger than 2 * chunk_size, also incorporates the tail chunk
    to eliminate collisions in structured binary files (e.g., identical headers).
    """
    hasher = hashlib.sha256()
    try:
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            return EMPTY_FILE_SHA256

        with open(filepath, "rb") as f:
            head = f.read(chunk_size)
            hasher.update(head)
            if file_size > 2 * chunk_size:
                f.seek(-chunk_size, os.SEEK_END)
                tail = f.read(chunk_size)
                hasher.update(tail)
        return hasher.hexdigest()
    except (OSError, PermissionError):
        return ""


def compute_full_sha256(filepath: str, chunk_size: int = 65536) -> str:
    """Computes streaming full SHA-256 hash using 64KB blocks."""
    hasher = hashlib.sha256()
    try:
        if os.path.getsize(filepath) == 0:
            return EMPTY_FILE_SHA256
        with open(filepath, "rb") as f:
            while True:
                if _run_abandoned():
                    return ""
                buf = f.read(chunk_size)
                if not buf:
                    break
                hasher.update(buf)
        return hasher.hexdigest()
    except (OSError, PermissionError):
        return ""


def _is_cache_dir_tag(path: str) -> bool:
    """Whether the CACHEDIR.TAG at `path` carries the signature that makes its folder a cache directory."""
    try:
        with open(path, "rb") as f:
            return f.read(len(CACHE_TAG_SIGNATURE)) == CACHE_TAG_SIGNATURE
    except OSError:
        return False


def _result_of(future: Future) -> str:
    """The result of `future`, waited for in half-second slices: on Windows a wait without a timeout is not
    interrupted by Ctrl-C, and a scan that cannot be stopped is the thing this exists to avoid."""
    while not future.done():
        wait_for([future], timeout=0.5)
    return future.result()


class DuplicateDetector:
    """High-performance 3-phase duplicate file detector.

    ``root`` is the folder scanned, and it is always scanned, whatever it is called or holds. Given ``drive_root``
    (the drive or storage root that ``root`` lies in), the folders from there down to ``root`` are judged first, the
    way a scan of the whole drive judges them: when one is a dependency tree or something Windows or an installed app
    depends on, nothing is scanned and ``scan_skipped`` says which folder and why. The drive root and everything above
    it is never judged. (The names in ``exclude_dirs`` are a choice about a drive-wide scan, not a reason to refuse a
    folder somebody asked for.)
    """

    def __init__(
        self,
        root: str,
        cluster_size: int = CLUSTER_SIZE,
        exclude_dirs: Optional[Set[str]] = None,
        *,
        skip_dependency_dirs: bool = True,
        system_refs: Optional[SystemRefIndex] = None,
        drive_root: Optional[str] = None,
        min_size: int = 0,
        workers: Optional[int] = None,
        progress: Optional[Callable[[Dict[str, Any]], None]] = None,
        progress_interval: Optional[float] = None,
    ) -> None:
        self.root = os.path.abspath(root)
        self.drive_root = os.path.abspath(drive_root) if drive_root else self.root
        self.cluster_size = cluster_size
        self.exclude_dirs = (
            {d.lower() for d in exclude_dirs}
            if exclude_dirs is not None
            else {d.lower() for d in DEFAULT_EXCLUDE_DIRS}
        )
        # Whether dependency / environment / cache trees (DEPENDENCY_DIR_NAMES, virtualenvs) are left unscanned.
        # The folder passed as `root` is always scanned, whatever it is called or holds.
        self.skip_dependency_dirs = skip_dependency_dirs
        self._system_refs: Optional[SystemRefIndex] = system_refs
        self._scoped_refs: Optional[SystemRefIndex] = None
        # Files smaller than this many bytes are not looked at, so they are neither grouped nor opened. The default
        # keeps empty files: they are grouped by size alone (never opened) and are worth 0 bytes to reclaim.
        self.min_size = max(0, int(min_size))
        self.workers = HASH_WORKERS if workers is None else max(1, int(workers))
        # `progress(info)` is called at most every `progress_interval` seconds while a run is under way (see
        # progress_info); it is the caller's to show, the detector itself prints nothing
        self._progress = progress
        self.progress_interval = PROGRESS_INTERVAL_SECONDS if progress_interval is None else max(0.0, progress_interval)
        self._next_progress: Optional[float] = None
        self._phase = "scan"
        self._hash_total = 0
        # Extra paths of hard-linked data met during the last walk. They are the same storage as a path already
        # yielded, so they are neither hashed again nor reported as redundant copies.
        self.hardlinked_paths_ignored = 0
        # What the last walk saw: folders listed, regular files looked at, and the trees it did not enter (each
        # counted once, at its top: the files inside are never listed)
        self.folders_scanned = 0
        self.files_scanned = 0
        self.files_below_min_size = 0
        self.skipped_dependency_dirs = 0
        # Folders (and loose files of the root) that Windows or an installed app depends on, as `organize` reports them
        self.protected_by_system: List[Dict[str, Any]] = []
        self.skipped_protected_dirs = 0
        # Set when `root` lies in a folder the scan never enters, so that nothing was scanned: {"folder": the outermost
        # such folder, "reason": SCAN_SKIPPED_*, "detail": what it is, or who uses it}
        self.scan_skipped: Optional[Dict[str, str]] = None
        # What the last run opened: files that share their size with another file, then those hashed on their head
        # and tail, then those hashed in full (the partial-hash collisions)
        self.size_candidate_files = 0
        self.files_partially_hashed = 0
        self.files_fully_hashed = 0

    @property
    def system_refs(self) -> SystemRefIndex:
        """Lazily collects OS/application references once per detector."""
        if self._system_refs is None:
            self._system_refs = SystemRefIndex.collect(shortcut_roots=[self.drive_root])
        return self._system_refs

    def _protected_refs(self) -> SystemRefIndex:
        """The references that point into the drive being scanned: only they can protect a folder inside it.

        Checking a folder walks every reference it is given, and a machine has over a thousand (services, shortcuts,
        installed apps), nearly all of them elsewhere; filtering once keeps the per-folder check cheap. (They are the
        ones under `drive_root`, not only those under `root`: the folders above `root` are judged by them too.)
        """
        if self._scoped_refs is None:
            self._scoped_refs = SystemRefIndex(self.system_refs.refs_under(self.drive_root, is_file=False))
        return self._scoped_refs

    def _protecting_refs(self, path: str, is_file: bool) -> List[SystemRef]:
        """The references that make `path` something Windows or an installed app depends on: a PATH entry, a service,
        an app's folder, a shortcut target, or a folder holding an uninstaller. Empty: it is free to scan. Records the
        folder for the report."""
        refs = self._protected_refs().check_folder(path, is_file=is_file)
        if refs:
            self.protected_by_system.append(format_protected_by_system_entry(path, refs))
            if not is_file:
                self.skipped_protected_dirs += 1
        return refs

    def _is_protected_by_system(self, path: str, is_file: bool) -> bool:
        return bool(self._protecting_refs(path, is_file))

    def _folders_above_root(self) -> List[str]:
        """The folders from just below `drive_root` down to `root` itself, outermost first. None at all when `root` is
        the drive root, or lies outside it (nothing is above it to judge)."""
        drive_key = os.path.normcase(self.drive_root)
        below_drive = drive_key if drive_key.endswith(os.sep) else drive_key + os.sep
        if not os.path.normcase(self.root).startswith(below_drive):
            return []
        folders: List[str] = []
        current = self.root
        while os.path.normcase(current) != drive_key:
            folders.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        folders.reverse()
        return folders

    @staticmethod
    def _environment_kind(folder: str) -> Optional[str]:
        """What `folder` is when `_walk_files` would not enter it for what it holds: a virtualenv (pyvenv.cfg) or a
        tagged cache directory (CACHEDIR.TAG), the same marker files it looks for in a listing. None: neither."""
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    name = entry.name.lower()
                    if name == VENV_MARKER_FILE and entry.is_file(follow_symlinks=False):
                        return "a Python virtual environment (it holds pyvenv.cfg)"
                    if (
                        name == CACHE_TAG_FILE
                        and entry.is_file(follow_symlinks=False)
                        and _is_cache_dir_tag(entry.path)
                    ):
                        return "a cache directory (it holds a CACHEDIR.TAG)"
        except OSError:
            pass
        return None

    def _judge_root(self) -> Optional[Dict[str, str]]:
        """Why `root` is not to be scanned, when a folder from `drive_root` down to it is one a scan of the whole drive
        never enters (a dependency tree, a folder the system depends on); None when it is free to scan. The outermost
        such folder decides, as everything inside it belongs to it."""
        for folder in self._folders_above_root():
            if self.skip_dependency_dirs:
                name = os.path.basename(folder)
                kind = (
                    f"a dependency, cache or version-control folder ({name})"
                    if name.lower() in DEPENDENCY_DIR_NAMES
                    else self._environment_kind(folder)
                )
                if kind is not None:
                    self.skipped_dependency_dirs += 1
                    return {"folder": folder, "reason": SCAN_SKIPPED_DEPENDENCY_TREE, "detail": kind}
            refs = self._protecting_refs(folder, is_file=False)
            if refs:
                return {
                    "folder": folder,
                    "reason": SCAN_SKIPPED_PROTECTED_BY_SYSTEM,
                    "detail": summarize_system_refs(refs),
                }
        return None

    def progress_info(self) -> Dict[str, Any]:
        """Where the run is now: its phase ("scan", "partial" or "full") and the counters so far."""
        return {
            "phase": self._phase,
            "folders_scanned": self.folders_scanned,
            "files_scanned": self.files_scanned,
            "size_candidate_files": self.size_candidate_files,
            "files_partially_hashed": self.files_partially_hashed,
            "files_fully_hashed": self.files_fully_hashed,
            "hash_total": self._hash_total,  # files the current hashing phase has to go through
            "skipped_dependency_dirs": self.skipped_dependency_dirs,
            "skipped_protected_dirs": self.skipped_protected_dirs,
        }

    def _report_progress(self) -> None:
        """Calls the progress callback, if there is one, unless it was called less than `progress_interval` ago."""
        if self._progress is None:
            return
        now = time.monotonic()
        if self._next_progress is None:  # the first report comes one interval after the run started
            self._next_progress = now + self.progress_interval
        elif now >= self._next_progress:
            self._next_progress = now + self.progress_interval
            self._progress(self.progress_info())

    def _walk_files(self) -> Iterator[Tuple[str, int]]:
        """Fast non-recursive directory scan yielding (abs_path, size).

        Of several hard links to the same data (same device and inode) only the first is yielded. Folders named in
        `exclude_dirs`, dependency / environment / cache trees, virtualenvs and the folders Windows or an installed
        app depends on are not entered. The root itself is always scanned; only what lies below it is judged. (Unless
        a `drive_root` was given: then the folders above the root are judged too, and nothing is yielded when one of
        them is a place the scan never enters; see `scan_skipped`.)
        """
        self.hardlinked_paths_ignored = 0
        self.folders_scanned = 0
        self.files_scanned = 0
        self.files_below_min_size = 0
        self.skipped_dependency_dirs = 0
        self.protected_by_system = []
        self.skipped_protected_dirs = 0
        self.scan_skipped = self._judge_root()
        if self.scan_skipped is not None:
            return
        seen_links: Set[Tuple[int, int]] = set()
        stack = [self.root]
        while stack:
            curr_dir = stack.pop()
            try:
                with os.scandir(curr_dir) as it:
                    entries = list(it)
            except (OSError, PermissionError):
                continue
            self.folders_scanned += 1

            # One pass over the listing sorts the entries and spots a virtualenv (its pyvenv.cfg) or a tagged cache
            # directory (its CACHEDIR.TAG) before any file of the folder is reported
            subdirs: List[str] = []
            files: List[os.DirEntry] = []
            dependency_dirs = 0
            is_venv = False
            cache_tag: Optional[str] = None
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        name = entry.name.lower()
                        if name in self.exclude_dirs:
                            continue
                        if self.skip_dependency_dirs and name in DEPENDENCY_DIR_NAMES:
                            dependency_dirs += 1
                            continue
                        subdirs.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        files.append(entry)
                        file_name = entry.name.lower()
                        if file_name == VENV_MARKER_FILE:
                            is_venv = True
                        elif file_name == CACHE_TAG_FILE:
                            cache_tag = entry.path
                except (OSError, PermissionError):
                    continue

            if self.skip_dependency_dirs and curr_dir != self.root and (
                is_venv or (cache_tag is not None and _is_cache_dir_tag(cache_tag))
            ):
                self.skipped_dependency_dirs += 1
                continue
            self.skipped_dependency_dirs += dependency_dirs
            for subdir in subdirs:
                if not self._is_protected_by_system(subdir, is_file=False):
                    stack.append(subdir)

            for entry in files:
                try:
                    info = entry.stat()
                except (OSError, PermissionError):
                    continue
                self.files_scanned += 1
                if info.st_size < self.min_size:
                    self.files_below_min_size += 1
                    continue
                if curr_dir == self.root and self._is_protected_by_system(entry.path, is_file=True):
                    continue
                if info.st_nlink > 1 and info.st_ino:  # (Windows leaves both at 0: "unknown")
                    identity = (info.st_dev, info.st_ino)
                    if identity in seen_links:
                        self.hardlinked_paths_ignored += 1
                        continue
                    seen_links.add(identity)
                yield entry.path, info.st_size

    def _hash_each(self, hash_one: Callable[[str], str], paths: List[str]) -> Iterator[Tuple[str, str]]:
        """Yields (path, digest) for each of `paths`, in order, hashing up to `self.workers` files at the same time.

        Results come back in the order of `paths`, so what is reported does not depend on which file finished first.
        """
        if self.workers <= 1 or len(paths) < 2:
            for path in paths:
                yield path, hash_one(path)
            return
        abandoned = threading.Event()
        pool = ThreadPoolExecutor(
            max_workers=self.workers,
            initializer=lambda: setattr(_worker_state, "abandoned", abandoned),
        )
        pending: Deque[Tuple[str, Future]] = deque()
        finished = False
        try:
            for path in paths:
                pending.append((path, pool.submit(hash_one, path)))
                # A window of work in flight, not every future at once; wide enough that one huge file at the head
                # does not leave the other workers idle for long
                if len(pending) >= self.workers * 64:
                    done, future = pending.popleft()
                    yield done, _result_of(future)
            while pending:
                done, future = pending.popleft()
                yield done, _result_of(future)
            finished = True
        finally:
            if not finished:  # Ctrl-C or an error: files being read are told to stop, nothing more is started
                abandoned.set()
            pool.shutdown(wait=finished, cancel_futures=True)

    def filter_by_size(self) -> Dict[int, List[str]]:
        """Phase 1: Groups all files by exact byte size.

        Returns:
            Dict mapping file size to list of file paths.
            Only sizes containing >= 2 files are returned.
        """
        size_map: Dict[int, List[str]] = defaultdict(list)
        # (the start of a run: the hashing counters and the progress clock start over)
        self.size_candidate_files = self.files_partially_hashed = self.files_fully_hashed = self._hash_total = 0
        self._phase = "scan"
        self._next_progress = None
        for filepath, size in self._walk_files():
            bucket = size_map[size]
            bucket.append(filepath)
            if len(bucket) == 2:
                self.size_candidate_files += 2
            elif len(bucket) > 2:
                self.size_candidate_files += 1
            self._report_progress()

        return {s: paths for s, paths in size_map.items() if len(paths) >= 2}

    def _group_by_partial_hash(self, chunk_size: int = 8192) -> Dict[Tuple[int, str], List[str]]:
        """Phase 2: files of one size that also share a partial (head/tail) hash, keyed by (size, hash).

        Empty files share their size and need no hash: they are grouped under EMPTY_FILE_SHA256 without being opened.
        """
        size_candidates = self.filter_by_size()
        partial_map: Dict[Tuple[int, str], List[str]] = defaultdict(list)

        size_of: Dict[str, int] = {}
        for size, files in size_candidates.items():
            if size == 0:
                partial_map[(0, EMPTY_FILE_SHA256)].extend(files)
            else:
                size_of.update((filepath, size) for filepath in files)
        self._phase, self._hash_total = "partial", len(size_of)

        for filepath, p_hash in self._hash_each(lambda path: compute_partial_hash(path, chunk_size), list(size_of)):
            self.files_partially_hashed += 1
            self._report_progress()
            if p_hash:
                partial_map[(size_of[filepath], p_hash)].append(filepath)

        return {k: paths for k, paths in partial_map.items() if len(paths) >= 2}

    def filter_by_partial_hash(self, chunk_size: int = 8192) -> Dict[str, List[str]]:
        """Phase 2: Filters size-candidate collisions using partial (head/tail) hashes.

        Returns:
            Dict mapping partial hash string to list of file paths.
            Only partial hashes containing >= 2 files are returned.
        """
        return {
            (EMPTY_FILE_SHA256 if size == 0 else f"{size}:{p_hash}"): paths
            for (size, p_hash), paths in self._group_by_partial_hash(chunk_size).items()
        }

    def find_duplicates(self) -> List[Dict[str, Any]]:
        """Phase 3: Computes streaming full SHA-256 for surviving candidate groups."""
        partial_candidates = self._group_by_partial_hash()
        full_hash_map: Dict[Tuple[int, str], List[str]] = defaultdict(list)

        size_of: Dict[str, int] = {}
        for (size, _), files in partial_candidates.items():
            if size == 0:
                full_hash_map[(0, EMPTY_FILE_SHA256)].extend(files)
            else:
                size_of.update((filepath, size) for filepath in files)
        self._phase, self._hash_total = "full", len(size_of)

        for filepath, full_hash in self._hash_each(lambda path: compute_full_sha256(path), list(size_of)):
            self.files_fully_hashed += 1
            self._report_progress()
            if full_hash:
                full_hash_map[(size_of[filepath], full_hash)].append(filepath)

        # Whether a file is inside a learning unit depends on its folder alone, and the lookup resolves the path (two
        # system calls on Windows, about a millisecond): ask once per folder, not once per copy
        unit_of_folder: Dict[str, bool] = {}

        def in_learning_unit(filepath: str) -> bool:
            folder = os.path.dirname(filepath)
            if folder not in unit_of_folder:
                unit_of_folder[folder] = is_in_learning_unit(filepath, self.root)
            return unit_of_folder[folder]

        duplicate_groups: List[Dict[str, Any]] = []
        for (fsize, full_hash), file_list in full_hash_map.items():
            if len(file_list) < 2:
                continue

            in_unit = [(f, in_learning_unit(f)) for f in file_list]
            unit_files = [f for f, inside in in_unit if inside]
            other_files = [f for f, inside in in_unit if not inside]

            # Learning unit copies must never be offered for deletion (they are kept/canonical)
            sorted_files = sorted(unit_files) + sorted(other_files)

            if unit_files:
                reclaimable_count = len(other_files)
            else:
                reclaimable_count = max(0, len(other_files) - 1)

            if fsize == 0:
                reclaimable_bytes = 0
                reclaimable_slack = 0
            else:
                reclaimable_bytes = fsize * reclaimable_count
                single_slack = calculate_slack_bytes(fsize, self.cluster_size)
                reclaimable_slack = single_slack * reclaimable_count

            duplicate_groups.append({
                "hash": full_hash,
                "size": fsize,
                "files": sorted_files,
                "reclaimable_bytes": reclaimable_bytes,
                "reclaimable_slack": reclaimable_slack,
            })

        duplicate_groups.sort(key=lambda g: g["reclaimable_bytes"], reverse=True)
        return duplicate_groups

    def generate_reclamation_plan(self) -> Dict[str, Any]:
        """Generates a complete summary of duplicates and space reclamation savings."""
        started = time.monotonic()
        groups = self.find_duplicates()
        elapsed = time.monotonic() - started
        total_reclaimable_bytes = sum(g["reclaimable_bytes"] for g in groups)
        total_reclaimable_slack = sum(g["reclaimable_slack"] for g in groups)
        total_dup_files = sum(len(g["files"]) for g in groups)

        return {
            "root": self.root,
            "cluster_size": self.cluster_size,  # the allocation unit the slack figures are worked out at
            "total_reclaimable_bytes": total_reclaimable_bytes,
            "total_reclaimable_slack": total_reclaimable_slack,
            "total_reclaimable_physical": total_reclaimable_bytes + total_reclaimable_slack,
            "duplicate_group_count": len(groups),
            "duplicate_file_count": total_dup_files,
            "duplicate_groups": groups,
            "hardlinked_paths_ignored": self.hardlinked_paths_ignored,
            "elapsed_seconds": round(elapsed, 2),
            "min_size": self.min_size,
            "folders_scanned": self.folders_scanned,
            "files_scanned": self.files_scanned,
            "files_below_min_size": self.files_below_min_size,
            "size_candidate_files": self.size_candidate_files,
            "files_partially_hashed": self.files_partially_hashed,
            "files_fully_hashed": self.files_fully_hashed,
            "skipped_dependency_dirs": self.skipped_dependency_dirs,
            "skipped_protected_dirs": self.skipped_protected_dirs,
            "protected_by_system": list(self.protected_by_system),
            "scan_skipped": self.scan_skipped,
        }


__all__ = [
    "DuplicateDetector",
    "compute_partial_hash",
    "compute_full_sha256",
    "CACHE_TAG_FILE",
    "CACHE_TAG_SIGNATURE",
    "DEPENDENCY_DIR_NAMES",
    "HASH_WORKERS",
    "PROGRESS_INTERVAL_SECONDS",
    "VENV_MARKER_FILE",
    "EMPTY_FILE_SHA256",
]
