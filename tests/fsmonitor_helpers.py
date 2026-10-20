"""tests/fsmonitor_helpers.py - Real-git helpers for the tests about git's fsmonitor daemon.

A repository with core.fsmonitor=true gets a background `git fsmonitor--daemon` at the first git command that
refreshes its index. These helpers make such a repository, start the daemon on purpose, look at it and stop it again.
Every git command used to set a repository up runs with the daemon disabled, so only `start_daemon` ever starts one.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

GIT = shutil.which("git")
ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="Test",
    GIT_AUTHOR_EMAIL="test@example.com",
    GIT_COMMITTER_NAME="Test",
    GIT_COMMITTER_EMAIL="test@example.com",
    GIT_CONFIG_NOSYSTEM="1",
    GIT_TERMINAL_PROMPT="0",
)
_QUIET = ["-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", "-c", "gc.auto=0", "-c", "core.fsmonitor=false"]


def force_remove(func: Any, path: str, _exc: Any) -> None:
    """rmtree onerror: git writes read-only object files, which Windows will not delete."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Runs git in cwd with the daemon disabled; raises when git fails."""
    return subprocess.run(
        ["git", *_QUIET, *args], cwd=str(cwd), env=ENV, capture_output=True, text=True, check=True, timeout=60
    )


def make_repository(folder: Path) -> Path:
    """A repository with one commit whose own configuration enables the fsmonitor daemon."""
    folder.mkdir(parents=True, exist_ok=True)
    git(folder, "init")
    (folder / "a.txt").write_text("hello\n", encoding="utf-8")
    git(folder, "add", "a.txt")
    git(folder, "commit", "-m", "init")
    git(folder, "config", "core.fsmonitor", "true")  # only now: the commands above must not start a daemon
    return folder


def daemon_is_watching(repo: Path) -> bool:
    """True when `git fsmonitor--daemon status` says a daemon is watching the repository (it exits 0 only then)."""
    done = subprocess.run(
        ["git", "-C", str(repo), "fsmonitor--daemon", "status"],
        env=ENV, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30, check=False,
    )
    return done.returncode == 0


def stop_daemon(repo: Path) -> None:
    """Stops the repository's daemon if one runs. Safe to call on a repository that was moved or deleted."""
    if GIT is None or not repo.exists():
        return
    try:
        subprocess.run(
            ["git", "-C", str(repo), "fsmonitor--daemon", "stop"],
            env=ENV, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def start_daemon(repo: Path, wait: float = 10.0) -> bool:
    """Starts the daemon the way users do (a plain `git status` in a repository that enables it).

    Returns False when this git or platform does not start one (git without fsmonitor--daemon, Linux builds).
    """
    subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        env=ENV, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, check=False,
    )
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if daemon_is_watching(repo):
            return True
        time.sleep(0.1)
    return False
