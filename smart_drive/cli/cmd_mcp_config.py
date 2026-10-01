"""smart_drive.cli.cmd_mcp_config - Multi-IDE MCP Configuration Registrar Command."""

from __future__ import annotations

import argparse
import json
import os
import sys

from smart_drive.mcp.registrar import register_ide_configs


def cmd_mcp_config(args: argparse.Namespace) -> int:
    """Handles the `mcp-config` and `mcp register` subcommands."""
    target_dir = getattr(args, "target_dir", None)
    if getattr(args, "workspace", False) and not target_dir:
        target_dir = os.getcwd()

    is_all = getattr(args, "all", False)

    flags = None
    if not is_all and (
        getattr(args, "antigravity", False)
        or getattr(args, "claude", False)
        or getattr(args, "cursor", False)
        or getattr(args, "codex", False)
        or getattr(args, "windsurf", False)
    ):
        flags = {
            "antigravity": bool(getattr(args, "antigravity", False)),
            "claude": bool(getattr(args, "claude", False)),
            "cursor": bool(getattr(args, "cursor", False) or getattr(args, "codex", False)),
            "windsurf": bool(getattr(args, "windsurf", False)),
        }

    # Enable auto_detect if no selective flags were supplied and not --all
    auto_detect = flags is None and not is_all
    results = register_ide_configs(target_dir=target_dir, flags=flags, auto_detect=auto_detect)

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
        return 0

    print("SmartDrive MCP Server Registration:")
    for ide, success in results.items():
        state = "✓ Registered" if success else "✗ Skipped/Failed"
        print(f"  {state}: {ide}")

    return 0
