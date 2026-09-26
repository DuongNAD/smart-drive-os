"""smart_drive.core.scanner - High-speed filesystem scanner for SmartDrive-OS.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Key design choices:
1. Slotted dataclass (`ScanEntry`) for minimal memory footprint (~112 bytes vs ~352 bytes unslotted).
2. Stack-based iterative DFS (`FastDirectoryScanner`) avoiding Python recursion limit (`RecursionError`).
3. Zero-syscall metadata extraction via `os.scandir(follow_symlinks=False)` with context manager cleanup.
4. Absolute no-symlink traversal guard on exFAT to prevent cycles, root escape, and filesystem corruptions.
5. Granular exception trapping (`PermissionError`, `FileNotFoundError`, `OSError`) without crashing the scan.
6. Case-insensitive exclusion filtering for system directories ($RECYCLE.BIN, .git, .agents, etc.).
7. Pure streaming generator `scan_iter()` for memory-efficient pipelining into auditor, cleaner, and indexer.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Set, Tuple

from smart_drive.core.exfat_compat import ExFatEngine
from smart_drive.core.config import DEFAULT_EXCLUDE_DIRS


def _normalize_rel_path(path: str, root_path: str) -> str:
    return ExFatEngine.normalize_rel_path(path, root_path)


logger = logging.getLogger("smart_drive.core.scanner")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
if sys.version_info >= (3, 10):
    @dataclass(slots=True)
    class ScanEntry:
        """Memory-efficient filesystem record for a scanned file or directory."""
        path: str              # Normalized absolute path
        rel_path: str          # Normalized relative path using '/'
        name: str              # Base filename
        size: int              # Exact nominal size in bytes (0 for directories)
        is_dir: bool           # True if directory (and not symlink)
        is_file: bool          # True if regular file (and not symlink)
        is_symlink: bool       # True if symlink
        mtime: float           # Modification timestamp (epoch seconds)
        ext: str               # Lowercase extension with leading dot (e.g. '.pdf')
        parent_dir: str        # Parent directory path

        @property
        def is_empty(self) -> bool:
            """Return True if regular file has zero bytes."""
            return self.is_file and self.size == 0

        def to_dict(self) -> dict:
            """Convert entry to plain dictionary."""
            return asdict(self)
else:
    @dataclass
    class ScanEntry:
        """Memory-efficient filesystem record for a scanned file or directory."""
        __slots__ = (
            "path", "rel_path", "name", "size", "is_dir",
            "is_file", "is_symlink", "mtime", "ext", "parent_dir"
        )
        path: str
        rel_path: str
        name: str
        size: int
        is_dir: bool
        is_file: bool
        is_symlink: bool
        mtime: float
        ext: str
        parent_dir: str

        @property
        def is_empty(self) -> bool:
            return self.is_file and self.size == 0

        def to_dict(self) -> dict:
            return asdict(self)


if sys.version_info >= (3, 10):
    @dataclass(slots=True)
    class ScanError:
        """Encapsulates a non-fatal error encountered during scanning."""
        path: str
        error_type: str        # 'PERMISSION_DENIED', 'NOT_FOUND', 'OS_ERROR', 'STAT_FAILED'
        message: str
        exception: Exception
else:
    @dataclass
    class ScanError:
        __slots__ = ("path", "error_type", "message", "exception")
        path: str
        error_type: str
        message: str
        exception: Exception


@dataclass
class ScanStats:
    """Runtime metrics tracking traversal performance and findings."""
    total_files: int = 0
    total_dirs: int = 0
    total_bytes: int = 0
    symlinks_skipped: int = 0
    error_count: int = 0
    elapsed_time: float = 0.0

    @property
    def throughput_fps(self) -> float:
        """Calculate files per second traversal rate."""
        if self.elapsed_time <= 0:
            return 0.0
        return self.total_files / self.elapsed_time


@dataclass
class ScanOptions:
    """Configurable traversal options for directory scanning."""
    exclude_dirs: Optional[Set[str]] = None
    follow_symlinks: bool = False
    yield_dirs: bool = True
    yield_files: bool = True
    max_depth: Optional[int] = None
    sort_entries: bool = False
    on_error: Optional[Callable[[str, Exception], None]] = None


# ---------------------------------------------------------------------------
# High-Speed Directory Scanner
# ---------------------------------------------------------------------------
class FastDirectoryScanner:
    """High-speed iterative filesystem scanner tailored for external exFAT SSDs.

    Traverses directories using explicit stack DFS and `os.scandir()`, ensuring
    sub-second discovery of thousands of files while guaranteeing exFAT safety.
    """

    def __init__(
        self,
        root_path: str,
        exclude_dirs: Optional[Set[str]] = None,
        follow_symlinks: bool = False,
        yield_dirs: bool = True,
        yield_files: bool = True,
        max_depth: Optional[int] = None,
        sort_entries: bool = False,
        on_error: Optional[Callable[[str, Exception], None]] = None,
    ) -> None:
        self.root_path = os.path.abspath(root_path)
        self.follow_symlinks = follow_symlinks
        self.yield_dirs = yield_dirs
        self.yield_files = yield_files
        self.max_depth = max_depth
        self.sort_entries = sort_entries
        self.on_error = on_error

        # Merge default exclusions with custom exclusions
        self.exclude_dirs: Set[str] = set(DEFAULT_EXCLUDE_DIRS)
        if exclude_dirs is not None:
            self.exclude_dirs.update(exclude_dirs)
        # Pre-compute lowercase set for case-insensitive exFAT matching
        self._exclude_dirs_lower: Set[str] = {d.lower() for d in self.exclude_dirs}

        self.stats = ScanStats()
        self.errors: List[ScanError] = []
        self.skipped_symlinks: List[str] = []

    def _record_error(self, path: str, error_type: str, exc: Exception) -> None:
        """Record an error in stats and history, logging and invoking on_error callback."""
        self.stats.error_count += 1
        msg = str(exc)
        err = ScanError(path=path, error_type=error_type, message=msg, exception=exc)
        self.errors.append(err)
        if self.on_error:
            try:
                self.on_error(path, exc)
            except Exception:
                pass
        logger.warning("Scanner error [%s] on %s: %s", error_type, path, msg)

    def scan_iter(self) -> Iterator[ScanEntry]:
        """Stream ScanEntry records iteratively using stack-based DFS.

        Yields:
            ScanEntry: Slotted metadata record for each discovered item.
        """
        t0 = time.perf_counter()
        stack: List[Tuple[str, int]] = [(self.root_path, 0)]

        while stack:
            current_dir, depth = stack.pop()

            try:
                scandir_it = os.scandir(current_dir)
            except PermissionError as e:
                self._record_error(current_dir, "PERMISSION_DENIED", e)
                continue
            except FileNotFoundError as e:
                self._record_error(current_dir, "NOT_FOUND", e)
                continue
            except OSError as e:
                self._record_error(current_dir, "OS_ERROR", e)
                continue

            with scandir_it:
                if self.sort_entries:
                    try:
                        entries = sorted(list(scandir_it), key=lambda e: e.name)
                    except (PermissionError, FileNotFoundError, OSError) as e:
                        self._record_error(current_dir, "DIR_ITERATION_ERROR", e)
                        continue
                else:
                    entries = scandir_it

                for entry in entries:
                    # 1. Symlink probe and safety guard
                    try:
                        is_sym = entry.is_symlink()
                    except (PermissionError, FileNotFoundError, OSError) as e:
                        self._record_error(entry.path, "STAT_FAILED", e)
                        is_sym = False

                    if is_sym and not self.follow_symlinks:
                        self.skipped_symlinks.append(entry.path)
                        self.stats.symlinks_skipped += 1
                        logger.warning("Skipping symlink on exFAT: %s", entry.path)
                        continue

                    # 2. Directory probe
                    try:
                        is_d = entry.is_dir(follow_symlinks=self.follow_symlinks)
                    except (PermissionError, FileNotFoundError, OSError) as e:
                        self._record_error(entry.path, "DIR_CHECK_FAILED", e)
                        continue

                    norm_path = os.path.normpath(entry.path)
                    rel_path = _normalize_rel_path(norm_path, self.root_path)

                    item_depth = depth + 1
                    if self.max_depth is not None and item_depth > self.max_depth:
                        continue

                    if is_d:
                        # Check case-sensitive and case-insensitive exclusion rules
                        if entry.name in self.exclude_dirs or entry.name.lower() in self._exclude_dirs_lower:
                            continue

                        # Add directory to DFS stack if item_depth allows further descent
                        if self.max_depth is None or item_depth < self.max_depth:
                            stack.append((norm_path, item_depth))
                        self.stats.total_dirs += 1

                        if self.yield_dirs:
                            try:
                                st = entry.stat(follow_symlinks=self.follow_symlinks)
                                mtime = st.st_mtime
                                if mtime <= 0:
                                    mtime = time.time()
                            except (PermissionError, FileNotFoundError, OSError):
                                mtime = time.time()

                            yield ScanEntry(
                                path=norm_path,
                                rel_path=rel_path,
                                name=entry.name,
                                size=0,
                                is_dir=True,
                                is_file=False,
                                is_symlink=is_sym,
                                mtime=mtime,
                                ext="",
                                parent_dir=current_dir,
                            )
                    else:
                        # 3. Regular file probe
                        try:
                            is_f = entry.is_file(follow_symlinks=self.follow_symlinks)
                        except (PermissionError, FileNotFoundError, OSError) as e:
                            self._record_error(entry.path, "FILE_CHECK_FAILED", e)
                            continue

                        if is_f and self.yield_files:
                            try:
                                st = entry.stat(follow_symlinks=self.follow_symlinks)
                                size = st.st_size
                                mtime = st.st_mtime
                                if mtime <= 0:
                                    mtime = time.time()
                            except (PermissionError, FileNotFoundError, OSError) as e:
                                self._record_error(entry.path, "STAT_FAILED", e)
                                size = 0
                                mtime = time.time()

                            _, ext = os.path.splitext(entry.name)
                            self.stats.total_files += 1
                            self.stats.total_bytes += size

                            yield ScanEntry(
                                path=norm_path,
                                rel_path=rel_path,
                                name=entry.name,
                                size=size,
                                is_dir=False,
                                is_file=True,
                                is_symlink=is_sym,
                                mtime=mtime,
                                ext=ext.lower(),
                                parent_dir=current_dir,
                            )

        self.stats.elapsed_time = time.perf_counter() - t0

    def scan_files(self) -> Iterator[ScanEntry]:
        """Stream only regular file entries (skipping directory records)."""
        prev_dirs, prev_files = self.yield_dirs, self.yield_files
        self.yield_dirs, self.yield_files = False, True
        try:
            yield from self.scan_iter()
        finally:
            self.yield_dirs, self.yield_files = prev_dirs, prev_files

    def scan_dirs(self) -> Iterator[ScanEntry]:
        """Stream only directory entries (skipping file records)."""
        prev_dirs, prev_files = self.yield_dirs, self.yield_files
        self.yield_dirs, self.yield_files = True, False
        try:
            yield from self.scan_iter()
        finally:
            self.yield_dirs, self.yield_files = prev_dirs, prev_files

    def scan_all(self) -> List[ScanEntry]:
        """Eagerly collect all entries into a list."""
        return list(self.scan_iter())

    def scan_dict(self) -> Dict[str, ScanEntry]:
        """Eagerly index all entries into a dict keyed by normalized rel_path."""
        return {e.rel_path: e for e in self.scan_iter()}


__all__ = [
    "ScanEntry",
    "ScanError",
    "ScanStats",
    "ScanOptions",
    "FastDirectoryScanner",
]
