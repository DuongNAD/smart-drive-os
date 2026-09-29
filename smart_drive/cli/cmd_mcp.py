"""smart_drive.cli.cmd_mcp - MCP JSON-RPC 2.0 stdio server command."""

from __future__ import annotations

import argparse
import sys

from smart_drive.mcp.server import SmartDriveMCPServer


def cmd_mcp(args: argparse.Namespace) -> int:
    """Handles the `mcp` subcommand."""
    root = getattr(args, "root", None)
    auth_token = getattr(args, "auth_token", None)
    require_auth = getattr(args, "require_auth", None)
    server = SmartDriveMCPServer(
        root=root,
        auth_token=auth_token,
        require_auth=require_auth,
    )
    server.run_stdio()
    return 0
