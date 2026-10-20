"""smart_drive.core.move_hint - Say what is probably keeping a folder from being moved.

Windows refuses to rename a folder ([WinError 5] Access is denied, [WinError 32] sharing violation) while a
process has a handle open on it or on something inside it. The usual holders are a program that opened a file in
the folder (an editor, a build, a virus scan), a terminal whose current directory is in it, an Explorer window
showing it and a git fsmonitor daemon watching a repository inside it. Linux and macOS report EACCES or EBUSY for
the moves they refuse.

``move_failure_hint`` turns such an error into advice. What it looks up on top of the error (running fsmonitor
daemons, the processes the Windows Restart Manager names) is best effort: it is left out when it cannot be
found, and nothing here raises.

100% Python Standard Library (ctypes only on Windows).
"""

from __future__ import annotations

import errno
import os
import sys
from typing import List

from smart_drive.core.git_locks import watching_repositories

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


def holding_processes(folder: str, max_files: int = MAX_HOLDER_FILES) -> List[str]:
    """The processes that have a file directly in ``folder`` open, named by the Windows Restart Manager.

    Asking about every file below the folder would take too long, so only up to ``max_files`` of its own
    files are asked about; a program that holds just a subfolder or the folder itself is not named. Returns
    nothing on other platforms, when there is no file to ask about or when the API cannot be reached.
    """
    if sys.platform != "win32":
        return []
    try:
        files = _direct_files(folder, max_files)
        return _restart_manager_holders(files) if files else []
    except Exception:  # no rstrtmgr.dll, a locked-down session, a folder that is gone...
        return []


def _shown(names: List[str]) -> str:
    extra = len(names) - MAX_NAMES_SHOWN
    return ", ".join(names[:MAX_NAMES_SHOWN]) + (f" and {extra} more" if extra > 0 else "")


def move_failure_hint(err: BaseException, folder: str, retry: str = "organize") -> str:
    """What to tell the user after ``err`` kept ``folder`` from being moved; it ends with how to try again.

    Any error gets the plain advice. A lock error also lists what may be using the folder, and names the
    fsmonitor daemons still running in it and the processes holding its files when they can be found.
    """
    again = f"close the editor, terminal or coding agent using it, then run {retry} again"
    if not is_lock_error(err):
        return again
    parts = [
        "a program, a terminal whose current directory is inside it, an Explorer window or a git fsmonitor "
        "daemon is using the folder"
    ]
    try:
        daemons = watching_repositories([folder], budget=HINT_BUDGET)
        if daemons:
            parts.append(
                f"a git fsmonitor daemon is still running in {_shown(daemons)} "
                f"(stop it with `git -C <repository> fsmonitor--daemon stop`)"
            )
        holders = holding_processes(folder)
        if holders:
            parts.append(f"files in it are held open by {_shown(holders)}")
    except Exception:  # the hint is advice; it must not turn a refused move into a crash
        pass
    return "; ".join(parts + [again])


def explain_refused_move(err: BaseException, folder: str, retry: str) -> str:
    """The error as it was, followed by the hint when the system refused because the folder is in use."""
    return f"{err} ({move_failure_hint(err, folder, retry)})" if is_lock_error(err) else str(err)
