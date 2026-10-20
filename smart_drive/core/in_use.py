"""smart_drive.core.in_use - Find what is open inside a folder that Windows refuses to rename.

NTFS refuses to rename a directory while anything beneath it is open, even when the entry that is open could be
renamed on its own. So the folder that "[WinError 5] Access is denied" names is rarely the one that is held: the
holder has a handle on a subfolder or a file somewhere below it, and often it is a program that cannot be named from
here (Explorer keeps a change-notification handle on every folder listed in Quick Access; a system service or an
elevated program is hidden from a normal user).

``find_in_use`` looks for the handle itself. It walks the folder breadth first and tries to open every directory and
file exclusively (read access, no sharing). That open fails with ERROR_SHARING_VIOLATION exactly when another handle
is open on the entry, whoever owns it and whatever it is used for; Explorer's watch handles (access mask 0x00120189,
which includes reading the directory) count as well. The deepest entries that fail so are reported: an entry with an
open descendant is left out, because the descendant is what keeps the folder from being renamed.

Nothing is renamed, written or kept open, and every handle is closed right away. The walk is bounded (depth, entries
and seconds), tries the folders before the files so that the budget goes to the likeliest holders first, does not
enter dependency, repository and environment trees (the entry of such a tree is still tried) and never raises. On
other platforms it reports nothing.

100% Python Standard Library (ctypes only on Windows).
"""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from collections import deque
from itertools import islice
from typing import Callable, Deque, List, Optional, Sequence, Set, Tuple

MAX_DEPTH = 8  # levels below the folder that are walked
MAX_ENTRIES = 20_000  # files and folders tried in one walk
TIME_BUDGET = 5.0  # seconds for one walk
MAX_REPORTED = 3  # paths reported
_STUCK_GRACE = 1.0  # seconds past the budget that the caller waits for a walk that is stuck inside a single open

ERROR_SHARING_VIOLATION = 32
_GENERIC_READ = 0x80000000
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000  # needed to open a directory
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000  # a link or a cloud placeholder is tried itself, never what it leads to
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: junctions, symlinks, cloud placeholders
_LONG_PATH = 240  # a path this long gets the extended-length form (MAX_PATH is 260, and 248 for a folder)

# Whole trees of dependencies and repositories: their own entry is tried, what is inside is not walked.
_NOT_ENTERED = frozenset({".git", "node_modules", "site-packages", ".venv", "venv"})
_VENV_MARKER = "pyvenv.cfg"  # a folder that has this file is a virtual environment, whatever its name

# path -> 0 when it could be opened exclusively (and was closed again), else the Win32 error code of the refusal
Opener = Callable[[str], int]
Clock = Callable[[], float]


def _extended(path: str) -> str:
    """``path`` in the extended-length form, which Windows needs once a path has more than MAX_PATH characters."""
    if len(path) < _LONG_PATH or path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):  # \\server\share\... becomes \\?\UNC\server\share\...
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _win32_opener() -> Opener:
    """An opener that asks Windows for an exclusive handle: read access, share mode 0, closed again at once."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value  # INVALID_HANDLE_VALUE
    last_error = ctypes.get_last_error  # type: ignore[attr-defined]
    flags = _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT

    def open_exclusively(path: str) -> int:
        handle = create_file(_extended(path), _GENERIC_READ, 0, None, _OPEN_EXISTING, flags, None)
        if handle is None or handle == invalid:
            return last_error() or -1
        close_handle(handle)
        return 0

    return open_exclusively


def _path_kind(path: str) -> Optional[str]:
    """'dir' or 'file' for a real folder or file, 'link' for a symlink or junction, None when there is nothing."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "link"
    if stat.S_ISDIR(info.st_mode):
        return "link" if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT else "dir"
    return "file" if stat.S_ISREG(info.st_mode) else None


def _entry_kind(entry: "os.DirEntry[str]") -> Optional[str]:
    """'dir' or 'file' for a real folder or file; None for anything else. Links are neither tried nor followed."""
    try:
        if entry.is_symlink():
            return None
        if entry.is_dir(follow_symlinks=False):
            junction = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE_POINT
            return None if junction else "dir"
        return "file" if entry.is_file(follow_symlinks=False) else None
    except OSError:
        return None


def deepest(paths: Sequence[str], limit: int = MAX_REPORTED) -> List[str]:
    """The paths of ``paths`` that none of the others lies below, in the order given; at most ``limit`` of them."""
    ordered = list(dict.fromkeys(paths))
    ancestors: Set[str] = set()
    for path in ordered:
        key = os.path.normcase(path)
        while True:
            parent = os.path.dirname(key)
            if not parent or parent == key or parent in ancestors:  # the root, or the chain above is already in
                break
            ancestors.add(parent)
            key = parent
    return [path for path in ordered if os.path.normcase(path) not in ancestors][: max(limit, 0)]


class _Walk:
    """One bounded walk. ``hits`` fills up while it runs, so a walk that is given up on still has its findings."""

    def __init__(self, opener: Opener, max_depth: int, max_entries: int, deadline: float, clock: Clock) -> None:
        self.opener = opener
        self.max_depth = max_depth
        self.max_entries = max_entries
        self.deadline = deadline
        self.clock = clock
        self.stop = threading.Event()
        self.tried = 0
        self.hits: List[str] = []

    def exhausted(self) -> bool:
        return self.stop.is_set() or self.tried >= self.max_entries or self.clock() > self.deadline

    def try_open(self, path: str) -> None:
        """Counts one entry, and records it when something else has it open."""
        self.tried += 1
        try:
            if self.opener(path) == ERROR_SHARING_VIOLATION:
                self.hits.append(path)
        except Exception:  # an entry that cannot be tried is no reason to give up on the others
            pass

    def run(self, root: str) -> None:
        try:
            self._run(root)
        except Exception:
            pass

    def _run(self, root: str) -> None:
        kind = _path_kind(root)
        if kind is None or self.exhausted():
            return
        self.try_open(root)
        if kind != "dir" or self.max_depth <= 0:
            return
        # Folders first, breadth first: the usual holders (Explorer, a terminal, an editor's file watcher) hold a
        # folder, and there are far fewer folders than files. The files are tried afterwards with what is left.
        files: List[Tuple[str, List[str]]] = []
        remembered = 0
        queue: Deque[Tuple[str, int]] = deque([(root, 0)])
        while queue:
            folder, depth = queue.popleft()
            try:
                with os.scandir(_extended(folder)) as scanner:
                    entries = list(islice(scanner, self.max_entries))
            except OSError:  # unreadable, or gone since it was listed
                continue
            if depth > 0 and any(entry.name.lower() == _VENV_MARKER for entry in entries):
                continue  # a virtual environment: its own entry was tried, what is inside is not walked
            names: List[str] = []
            for entry in entries:
                if self.exhausted():
                    return
                kind = _entry_kind(entry)
                if kind == "dir":
                    path = os.path.join(folder, entry.name)
                    self.try_open(path)
                    if depth + 1 < self.max_depth and entry.name.lower() not in _NOT_ENTERED:
                        queue.append((path, depth + 1))
                elif kind == "file" and remembered < self.max_entries:
                    names.append(entry.name)
                    remembered += 1
            if names:
                files.append((folder, names))
        for folder, names in files:
            for name in names:
                if self.exhausted():
                    return
                self.try_open(os.path.join(folder, name))


def find_in_use(
    folder: str,
    opener: Optional[Opener] = None,
    max_depth: int = MAX_DEPTH,
    max_entries: int = MAX_ENTRIES,
    budget: float = TIME_BUDGET,
    limit: int = MAX_REPORTED,
    clock: Clock = time.monotonic,
) -> List[str]:
    """The deepest entries at and below ``folder`` that something has open: at most ``limit`` paths.

    Every folder and file is tried with ``opener`` (by default an exclusive open through the Win32 API), and an
    entry counts when the answer is ERROR_SHARING_VIOLATION. The walk stops after ``max_depth`` levels,
    ``max_entries`` entries or ``budget`` seconds; it runs in a thread that is given up on a second after the
    budget, so a single open that hangs (a virus scan, a dying disk) cannot hold the caller up either. Returns
    nothing on other platforms, when the folder is gone, and on any failure.
    """
    try:
        if opener is None:
            if sys.platform != "win32":
                return []
            opener = _win32_opener()
        walk = _Walk(opener, max_depth, max_entries, clock() + budget, clock)
        worker = threading.Thread(
            target=walk.run, args=(os.path.abspath(folder),), name="smart-drive-in-use", daemon=True
        )
        worker.start()
        worker.join(budget + _STUCK_GRACE)
        walk.stop.set()  # a walk that is stuck in one open ends as soon as that open returns
        return deepest(list(walk.hits), limit)
    except Exception:
        return []
