"""smart_drive.core.processes - Cross-platform running-process probe."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from typing import Any, Optional, Set


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
]
