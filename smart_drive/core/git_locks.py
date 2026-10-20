"""smart_drive.core.git_locks - Keep git's fsmonitor daemon from locking the folders smart-drive moves.

A repository with ``core.fsmonitor=true`` starts a background ``git fsmonitor--daemon`` the first time git
refreshes its index (``status``, ``ls-files``, ``diff``, ``check-ignore``, ``rm``...). The daemon keeps a handle
open on the work tree, and on Windows that handle makes every rename of the repository's folder, or of a folder
above it, fail with ``[WinError 5] Access is denied``.

Every git command smart-drive runs carries ``NO_FSMONITOR``, so smart-drive never starts a daemon itself.

100% Python Standard Library (Python >= 3.9).
"""

from __future__ import annotations

from typing import Tuple

# Goes right after the git executable (``git -c core.fsmonitor=false -C <repo> ...``). Overrides the repository's,
# the user's and the system's configuration for that one command and for any git it starts.
NO_FSMONITOR: Tuple[str, str] = ("-c", "core.fsmonitor=false")
