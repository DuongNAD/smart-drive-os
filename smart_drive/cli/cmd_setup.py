"""smart_drive.cli.cmd_setup - `setup`: one command that sets up the MCP ecosystem."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from smart_drive.mcp import bootstrap


def _split(value: Optional[str]) -> Optional[List[str]]:
    """``"a, b"`` -> ``["a", "b"]``; nothing (or only separators) -> None."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return parts or None


def cmd_setup(args: argparse.Namespace) -> int:
    """Handles the `setup` subcommand."""
    dest = getattr(args, "dest", None)
    options = bootstrap.Options(
        dest=Path(dest).expanduser() if dest else None,
        agents=_split(getattr(args, "agents", None)),
        only=_split(getattr(args, "only", None)),
        dry_run=bool(getattr(args, "dry_run", False)),
        check=bool(getattr(args, "check", False)),
        no_clone=bool(getattr(args, "no_clone", False)),
        no_health=bool(getattr(args, "no_health", False)),
        repair=bool(getattr(args, "repair", False)),
    )

    def progress(message: str) -> None:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()

    try:
        report = bootstrap.run_setup(options, log=progress)
    except ValueError as err:
        sys.stderr.write(f"setup: {err}\n")
        return 2

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(bootstrap.format_report(report))
    return bootstrap.exit_code(report)
