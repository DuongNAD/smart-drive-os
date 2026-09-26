"""smart_drive.cli.cmd_mcp_config - Multi-IDE MCP Configuration Registrar Command."""

from __future__ import annotations

import argparse
import json
import sys

from smart_drive.mcp.registrar import register_ide_configs


def cmd_mcp_config(args: argparse.Namespace) -> int:
    """Handles the `mcp-config` subcommand."""
    target_dir = getattr(args, "target_dir", None)

    flags = None
    if getattr(args, "antigravity", False) or getattr(args, "claude", False) or getattr(args, "cursor", False) or getattr(args, "windsurf", False):
        flags = {
            "antigravity": bool(getattr(args, "antigravity", False)),
            "claude": bool(getattr(args, "claude", False)),
            "cursor": bool(getattr(args, "cursor", False)),
            "windsurf": bool(getattr(args, "windsurf", False)),
        }

    results = register_ide_configs(target_dir=target_dir, flags=flags)

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
        return 0

    print("SmartDrive MCP Server Registration:")
    for ide, success in results.items():
        state = "✓ Registered" if success else "✗ Skipped/Failed"
        print(f"  {state}: {ide}")

    return 0
