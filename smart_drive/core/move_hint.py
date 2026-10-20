"""smart_drive.core.move_hint - Say what is probably keeping a folder from being moved.

Windows refuses to rename a folder ([WinError 5] Access is denied, [WinError 32] sharing violation) while a
process has a handle open on it or on something inside it. The usual holders are a program that opened a file in
the folder (an editor, a build, a virus scan), a terminal whose current directory is in it, an Explorer window
showing it and a git fsmonitor daemon watching a repository inside it. Linux and macOS report EACCES or EBUSY for
the moves they refuse.

``inspect_refused_move`` turns such an error into advice (``move_failure_hint`` and ``explain_refused_move`` are the
text-only forms). What it looks up on top of the error is best effort: running fsmonitor daemons, and on Windows
what is open below the folder (``in_use.find_in_use`` tries every entry exclusively and reports the deepest ones) and
the processes the Windows Restart Manager names for the files that are open. A lookup that fails is left out, and
nothing here raises.

100% Python Standard Library (ctypes only on Windows).
"""

from __future__ import annotations

import errno
import os
import sys
from dataclasses import dataclass
from typing import Callable, List, Sequence

from smart_drive.core.git_locks import watching_repositories
from smart_drive.core.in_use import find_in_use

_LOCK_WINERRORS = (5, 32)  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION
_LOCK_ERRNOS = (errno.EACCES, errno.EPERM, errno.EBUSY)
MAX_HOLDER_FILES = 64  # files of the folder handed to the Restart Manager
MAX_NAMES_SHOWN = 5  # processes or repositories named in one hint
HINT_BUDGET = 5.0  # seconds for looking up running fsmonitor daemons after a failure
_ERROR_MORE_DATA = 234


def is_lock_error(err: BaseException) -> bool:
    """Whether ``err`` is the system refusing to move a folder that is in use.

    That is WinError 5 or 32, EACCES/EPERM (a PermissionError) or EBUSY. A missing source, an existing
    target or a full disk is not one.
    """
    if isinstance(err, PermissionError):
        return True
    if not isinstance(err, OSError):
        return False
    return getattr(err, "winerror", None) in _LOCK_WINERRORS or err.errno in _LOCK_ERRNOS


def _direct_files(folder: str, limit: int) -> List[str]:
    """Up to ``limit`` files that sit directly in ``folder``."""
    files: List[str] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    files.append(os.path.abspath(entry.path))
            except OSError:
                continue
            if len(files) >= limit:
                break
    return files


def _restart_manager_holders(files: List[str]) -> List[str]:
    """Asks the Windows Restart Manager which processes have any of ``files`` open ("name (pid 1234)")."""
    import ctypes
    from ctypes import wintypes

    class UniqueProcess(ctypes.Structure):  # RM_UNIQUE_PROCESS
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class ProcessInfo(ctypes.Structure):  # RM_PROCESS_INFO
        _fields_ = [
            ("Process", UniqueProcess),
            ("strAppName", ctypes.c_wchar * 256),
            ("strServiceShortName", ctypes.c_wchar * 64),
            ("ApplicationType", ctypes.c_int),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    rstrtmgr = ctypes.WinDLL("rstrtmgr.dll")  # type: ignore[attr-defined]
    session = wintypes.DWORD(0)
    session_key = ctypes.create_unicode_buffer(33)  # CCH_RM_SESSION_KEY + 1
    if rstrtmgr.RmStartSession(ctypes.byref(session), 0, session_key) != 0:
        return []
    try:
        names = (ctypes.c_wchar_p * len(files))(*files)
        if rstrtmgr.RmRegisterResources(session, len(files), names, 0, None, 0, None) != 0:
            return []
        needed, count, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
        # The first call only counts the processes: it answers ERROR_MORE_DATA when there are any.
        code = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if code != _ERROR_MORE_DATA or not needed.value:
            return []
        infos = (ProcessInfo * needed.value)()
        count = wintypes.UINT(needed.value)
        if rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)) != 0:
            return []
        return [f"{info.strAppName} (pid {info.Process.dwProcessId})" for info in infos[: count.value]]
    finally:
        rstrtmgr.RmEndSession(session)


def holding_processes(folder: str, max_files: int = MAX_HOLDER_FILES, also: Sequence[str] = ()) -> List[str]:
    """The processes that have a file open, named by the Windows Restart Manager.

    The files asked about are the ones in ``also`` (the files ``find_in_use`` found open) and up to ``max_files``
    of the files directly in ``folder``; asking about every file below the folder would take too long. The Restart
    Manager only knows files, so a program that holds just a subfolder or the folder itself is not named. Returns
    nothing on other platforms, when there is no file to ask about or when the API cannot be reached.
    """
    if sys.platform != "win32":
        return []
    try:
        files = [os.path.abspath(path) for path in also]
        try:
            files += _direct_files(folder, max_files)
        except OSError:  # a file or a folder that is gone: the files in ``also`` can still be asked about
            pass
        files = list({os.path.normcase(path): path for path in files}.values())
        return _restart_manager_holders(files) if files else []
    except Exception:  # no rstrtmgr.dll, a locked-down session...
        return []


def _shown(names: List[str]) -> str:
    extra = len(names) - MAX_NAMES_SHOWN
    return ", ".join(names[:MAX_NAMES_SHOWN]) + (f" and {extra} more" if extra > 0 else "")


def _lookup(find: Callable[[], List[str]]) -> List[str]:
    """What ``find`` returns, or nothing when it fails: the hint is advice and must not turn a refused move into a crash."""
    try:
        return list(find())
    except Exception:
        return []


def _unnamed_holder(in_use: List[str], tail: str) -> str:
    """What to do when something is open but no program can be named for it (the Restart Manager only knows files)."""
    name = in_use[0].rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1] or in_use[0]  # the last name, whatever the OS
    pronoun = "them" if len(in_use) > 1 else "it"
    return (
        f"Windows does not say which program has {pronoun} open: often explorer.exe (an open Explorer window or Quick "
        f"Access), else a terminal or editor using {pronoun}, a system service or an elevated program; to find it, open "
        f'Resource Monitor as administrator (resmon) → CPU → Associated Handles → search "{name}", or restart '
        f"Windows, then {tail}"
    )


@dataclass(frozen=True)
class RefusedMove:
    """What is known about a move that the system refused."""

    error: str  # the error as the system worded it
    hint: str  # what probably holds the folder and how to go on; it ends with how to try again
    in_use: List[str]  # the deepest entries at or below the folder that something has open (at most 3)
    lock: bool  # whether the system refused because the folder is in use

    def explained(self, always: bool = False) -> str:
        """The error as it was, followed by the hint when it was a lock error (or, with ``always``, for any error)."""
        return f"{self.error} ({self.hint})" if self.lock or always else self.error


def inspect_refused_move(err: BaseException, folder: str, retry: str = "organize") -> RefusedMove:
    """What to tell the user after ``err`` kept ``folder`` from being moved, and what was found open in it.

    Any error gets the plain advice. A lock error also lists what may be using the folder, and names the
    fsmonitor daemons still running in it. On Windows it also names the entries below the folder that are open
    (``in_use``) and the processes holding files, when they can be found, and says how to find a holder that
    cannot be named. The hint ends with how to try again (``retry`` is the command).
    """
    tail = f"run {retry} again"
    again = f"close the editor, terminal or coding agent using it, then {tail}"
    if not is_lock_error(err):
        return RefusedMove(str(err), again, [], False)

    daemons = _lookup(lambda: watching_repositories([folder], budget=HINT_BUDGET))
    in_use = _lookup(lambda: find_in_use(folder))
    held_files = [path for path in in_use if os.path.isfile(path)]
    holders = _lookup(lambda: holding_processes(folder, also=held_files))

    parts: List[str] = []
    if not in_use:  # nothing was found open: only the usual suspects can be listed
        parts.append(
            "a program, a terminal whose current directory is inside it, an Explorer window or a git fsmonitor "
            "daemon is using the folder"
        )
    if daemons:
        parts.append(
            f"a git fsmonitor daemon is still running in {_shown(daemons)} "
            f"(stop it with `git -C <repository> fsmonitor--daemon stop`)"
        )
    if in_use:
        parts.append("in use: " + ", ".join(in_use))
    if holders:
        parts.append(f"files in it are held open by {_shown(holders)}")
    if in_use and not holders and not daemons:
        parts.append(_unnamed_holder(in_use, tail))
    else:
        parts.append(again)
    return RefusedMove(str(err), "; ".join(parts), in_use, True)


def move_failure_hint(err: BaseException, folder: str, retry: str = "organize") -> str:
    """What to tell the user after ``err`` kept ``folder`` from being moved; it ends with how to try again."""
    return inspect_refused_move(err, folder, retry).hint


def explain_refused_move(err: BaseException, folder: str, retry: str) -> str:
    """The error as it was, followed by the hint when the system refused because the folder is in use."""
    return inspect_refused_move(err, folder, retry).explained()
