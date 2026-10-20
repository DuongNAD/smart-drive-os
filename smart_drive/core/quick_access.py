"""smart_drive.core.quick_access - The folders Explorer lists in Quick Access.

Explorer keeps a change-notification handle open on every folder in its Quick Access list (the pinned ones, and the
frequent ones), and opens them again at each logon. A handle on a folder keeps Windows from renaming that folder and
every folder above it ("[WinError 5] Access is denied"), so a folder that has a pinned folder somewhere inside cannot be
moved. Unpinning does not close the handle, and neither does a restart of the computer while the folder is still
pinned: Windows Explorer has to be restarted (Task Manager -> Windows Explorer -> Restart) after the folder was unpinned.

``quick_access_folders`` reads the list with a short PowerShell call (the Shell.Application COM object is the only way to
read it without a dependency); ``pinned_inside`` picks the entries that lie in a folder that is about to be moved. Both
are best effort: they give nothing when the list cannot be read (no PowerShell, a locked-down session, a timeout), they
never raise, and the call is given up on after a few seconds.

100% Python Standard Library.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Iterable, List, Optional, Sequence, Set

QUICK_ACCESS_TIMEOUT = 5.0  # seconds for the PowerShell call
_QUICK_ACCESS = "shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}"  # the Quick Access view of the shell
_CREATE_NO_WINDOW = 0x08000000

# (command, seconds it may take) -> what the command printed; None when it could not be run or did not succeed
ShellRunner = Callable[[Sequence[str], float], Optional[str]]


def _powershell() -> Optional[str]:
    """Windows PowerShell by its full path: a bare name would be looked up in the current folder first."""
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return path if os.path.isfile(path) else None


def _script(namespace: str) -> str:
    """The PowerShell that prints the path of every item of a shell folder, one per line, as UTF-8."""
    return (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding $false; "
        "(New-Object -ComObject Shell.Application).Namespace('" + namespace.replace("'", "''") + "').Items() "
        "| % { $_.Path }"
    )


def _run_shell(command: Sequence[str], timeout: float) -> Optional[str]:
    """Runs ``command`` without a window and gives its output as text; None when it fails or takes too long."""
    try:
        done = subprocess.run(
            list(command),
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8-sig", errors="replace")


def quick_access_folders(
    run: Optional[ShellRunner] = None,
    timeout: float = QUICK_ACCESS_TIMEOUT,
    namespace: str = _QUICK_ACCESS,
) -> List[str]:
    """The folders listed in Explorer's Quick Access, or nothing on other systems and when they cannot be read.

    ``run`` replaces the PowerShell call (tests inject the query's answer) and ``namespace`` any other shell folder.
    """
    try:
        if run is None:
            if sys.platform != "win32":
                return []
            shell = _powershell()
            if shell is None:
                return []
            run = _run_shell
        else:
            shell = _powershell() or "powershell.exe"
        output = run([shell, "-NoProfile", "-NonInteractive", "-Command", _script(namespace)], timeout)
        if not output:
            return []
        folders: List[str] = []
        seen: Set[str] = set()
        for line in output.splitlines():
            path = line.strip()
            if not path or not os.path.isabs(path):  # items that are no folder of a drive print "::{GUID}" or nothing
                continue
            path = os.path.normpath(path)
            if os.path.normcase(path) not in seen:
                seen.add(os.path.normcase(path))
                folders.append(path)
        return folders
    except Exception:
        return []


def pinned_inside(folder: str, pins: Iterable[str]) -> List[str]:
    """The entries of ``pins`` that are ``folder`` or lie below it (without regard to case on Windows)."""
    base = os.path.normcase(os.path.normpath(os.path.abspath(folder)))
    below = base if base.endswith(os.sep) else base + os.sep
    found: List[str] = []
    for pin in pins:
        key = os.path.normcase(os.path.normpath(pin))
        if key == base or key.startswith(below):
            found.append(os.path.normpath(pin))
    return found
