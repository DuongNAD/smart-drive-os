"""smart_drive.core.processes - Cross-platform running-process probe."""

from __future__ import annotations

import csv
import ctypes
import os
import subprocess
import sys
from typing import Any, Iterable, List, Optional, Set


def _strip_win32_long_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _norm_exe_path(path: str) -> str:
    cleaned = _strip_win32_long_prefix(str(path).strip().strip('"'))
    try:
        resolved = os.path.realpath(os.path.abspath(cleaned))
    except (OSError, ValueError):
        resolved = os.path.abspath(cleaned)
    return os.path.normcase(resolved).rstrip("\\/")


def _probe_windows_executable_paths() -> Optional[Set[str]]:
    """Enumerates accessible running executable paths on Windows via Win32 Toolhelp32 + QueryFullProcessImageNameW."""
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return None

    paths: Set[str] = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return None
        while True:
            pid = int(entry.th32ProcessID)
            if pid > 4:
                proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if proc and proc != INVALID_HANDLE:
                    try:
                        size = wintypes.DWORD(1024)
                        buf = ctypes.create_unicode_buffer(size.value)
                        if kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
                            raw_path = _strip_win32_long_prefix(buf.value.strip())
                            if raw_path:
                                paths.add(raw_path)
                    finally:
                        kernel32.CloseHandle(proc)
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return paths


def probe_running_executable_paths(run_fn: Any = None) -> Optional[Set[str]]:
    """Returns full executable paths of running processes, or None if the process table cannot be read."""
    if sys.platform == "win32" and run_fn is None:
        try:
            return _probe_windows_executable_paths()
        except Exception:
            return None

    if sys.platform.startswith("linux") and run_fn is None:
        paths: Set[str] = set()
        try:
            proc_entries = list(os.scandir("/proc"))
        except OSError:
            return None
        for ent in proc_entries:
            if not ent.name.isdigit():
                continue
            try:
                target = os.readlink(f"/proc/{ent.name}/exe")
            except OSError:
                continue
            if target.endswith(" (deleted)"):
                target = target[: -len(" (deleted)")]
            if target:
                paths.add(target)
        return paths

    runner = subprocess.run if run_fn is None else run_fn
    try:
        done = runner(
            ["ps", "-A", "-o", "comm="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or done.stdout is None:
        return None

    paths = set()
    for line in done.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("/"):
            paths.add(stripped)
    return paths


def running_executable_paths() -> Set[str]:
    """Returns full executable paths of accessible running processes (empty set if unreadable)."""
    return probe_running_executable_paths() or set()


def executables_under_path(target_path: str, running_exes: Optional[Iterable[str]] = None) -> List[str]:
    """Returns running executable paths equal to or located strictly underneath `target_path`."""
    exes = running_executable_paths() if running_exes is None else running_exes
    target_norm = _norm_exe_path(target_path)
    if not target_norm:
        return []
    prefix = target_norm if target_norm.endswith(os.sep) else target_norm + os.sep
    matches: Set[str] = set()
    for exe in exes:
        if not exe:
            continue
        exe_norm = _norm_exe_path(exe)
        if exe_norm == target_norm or exe_norm.startswith(prefix):
            matches.add(_strip_win32_long_prefix(str(exe)))
    return sorted(matches)


def probe_running_processes(run_fn: Any = None) -> Optional[Set[str]]:
    """Returns running process names from `tasklist` (Windows) or `ps` (POSIX), or None if unreadable."""
    runner = subprocess.run if run_fn is None else run_fn
    cmd = ["tasklist", "/FO", "CSV", "/NH"] if sys.platform == "win32" else ["ps", "-A", "-o", "comm="]
    try:
        done = runner(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0 or not done.stdout:
        return None

    names: Set[str] = set()
    if sys.platform == "win32":
        for row in csv.reader(done.stdout.splitlines()):
            if row and row[0].strip():
                names.add(row[0].strip())
    else:
        for line in done.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                names.add(stripped)
    return names


def running_processes() -> Set[str]:
    """Returns running process names from `tasklist` (Windows) or `ps` (POSIX).

    An unreadable list gives an empty set: callers that act on the answer use `probe_running_processes()`.
    """
    return probe_running_processes() or set()


_running_processes = running_processes


def normalize_process_name(raw: str) -> str:
    """Normalizes a process executable name (strips path and `.exe`, lowercases)."""
    base = os.path.basename(str(raw).strip().replace("\\", "/")).casefold()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


__all__ = [
    "probe_running_processes",
    "running_processes",
    "_running_processes",
    "normalize_process_name",
    "probe_running_executable_paths",
    "running_executable_paths",
    "executables_under_path",
]
