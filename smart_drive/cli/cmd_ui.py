"""smart_drive.cli.cmd_ui - CLI Subcommand Handler for `smart-drive ui`."""

from __future__ import annotations

import argparse
import os
import sys

from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.sentinel import get_default_db_path
from smart_drive.ui.server import run_server


def cmd_ui(args: argparse.Namespace) -> int:
    """Handles the `smart-drive ui` command to launch the web dashboard."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2
    port = int(getattr(args, "port", 8765) or 8765)
    open_browser = not getattr(args, "no_browser", False)
    db_path = getattr(args, "db", None) or get_default_db_path(root)

    try:
        run_server(root_path=root, port=port, open_browser=open_browser, db_path=db_path)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        sys.stderr.write(f"Error starting web dashboard: {exc}\n")
        return 1


__all__ = ["cmd_ui"]
