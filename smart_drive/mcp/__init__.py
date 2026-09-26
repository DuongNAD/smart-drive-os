"""smart_drive.mcp - Model Context Protocol for Autonomous AI Agents."""

from __future__ import annotations

from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.mcp.registrar import get_agent_config_paths, register_ide_configs
from smart_drive.mcp.server import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION, SmartDriveMCPServer, TOOLS

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SmartDriveMCPServer",
    "SmartDriveProxy",
    "TOOLS",
    "get_agent_config_paths",
    "register_ide_configs",
]
