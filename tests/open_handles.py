"""tests/open_handles.py - Keep a handle open on a folder or file, the way another program does (Windows only).

Explorer's change-notification handle on a Quick Access folder, a terminal's current directory, a file watcher and an
editor's open file are all handles that Windows keeps open while smart-drive tries to rename a folder above them.
`OpenHandle` opens one with CreateFileW and closes it again, so a test can show what smart-drive does while it is held.
"""

from __future__ import annotations

import sys
from types import TracebackType
from typing import Optional, Type

FILE_LIST_DIRECTORY = 0x00000001  # also FILE_READ_DATA: the access a directory watcher asks for
EXPLORER_WATCH_ACCESS = 0x00120189  # the access mask of Explorer's watch handles on Quick Access folders
SHARE_ALL = 0x00000007  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE: still blocks an exclusive open
_OPEN_EXISTING = 3
_BACKUP_SEMANTICS = 0x02000000  # needed to open a directory


class OpenHandle:
    """``with OpenHandle(path):`` keeps ``path`` open for the length of the block."""

    def __init__(self, path: str, access: int = FILE_LIST_DIRECTORY, share: int = SHARE_ALL) -> None:
        if sys.platform != "win32":
            raise OSError("open handles are a Windows thing")
        import ctypes
        from ctypes import wintypes

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        handle = self._kernel32.CreateFileW(path, access, share, None, _OPEN_EXISTING, _BACKUP_SEMANTICS, None)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        self._handle: Optional[int] = handle
        self.path = path

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "OpenHandle":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()
