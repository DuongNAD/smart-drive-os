"""smart_drive.mcp.registrar - Multi-IDE MCP Configuration Registrar.

Registers the SmartDrive MCP Server across:
- Google Antigravity 2.0 (~/.gemini/antigravity/mcp_config.json)
- Claude Desktop (~/Library/Application Support/Claude or %APPDATA%/Claude)
- Cursor IDE (~/.cursor/mcp.json)
- Windsurf IDE (~/.codeium/windsurf/mcp_config.json)
- SSD Workspace (.mcp.json)
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def get_agent_config_paths() -> Dict[str, Path]:
    """Returns standard config paths for supported AI agents on this host."""
    home = Path.home()
    system = platform.system().lower()

    # 1. Google Antigravity
    antigravity_path = home / ".gemini" / "antigravity" / "mcp_config.json"

    # 2. Claude Desktop
    if "darwin" in system:
        claude_path = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif "windows" in system or sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            claude_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
        else:
            claude_path = home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:  # Linux
        claude_path = home / ".config" / "Claude" / "claude_desktop_config.json"

    # 3. Cursor IDE
    cursor_path = home / ".cursor" / "mcp.json"

    # 4. Windsurf IDE
    windsurf_path = home / ".codeium" / "windsurf" / "mcp_config.json"

    return {
        "antigravity": antigravity_path,
        "claude": claude_path,
        "cursor": cursor_path,
        "windsurf": windsurf_path,
    }


def load_json_config(path: Path) -> Dict[str, Any]:
    """Safely loads an existing JSON configuration file."""
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json_config(path: Path, data: Dict[str, Any]) -> None:
    """Safely writes a JSON configuration file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def register_ide_configs(
    target_dir: Optional[str] = None,
    flags: Optional[Dict[str, bool]] = None,
) -> Dict[str, bool]:
    """Registers SmartDrive MCP Server in standard IDE configuration files.

    Args:
        target_dir: Optional workspace directory to also write local `.mcp.json`.
        flags: Optional mapping of IDE names to boolean flags (e.g. {'antigravity': True}).

    Returns:
        Mapping of target name to registration success status.
    """
    python_cmd = sys.executable or "python3"
    results: Dict[str, bool] = {}

    mcp_entry = {
        "command": python_cmd,
        "args": ["-m", "smart_drive", "mcp"],
        "env": {},
    }

    config_paths = get_agent_config_paths()

    for ide_name, path in config_paths.items():
        if flags is not None and not flags.get(ide_name, True):
            continue

        try:
            cfg = load_json_config(path)
            if "mcpServers" not in cfg or not isinstance(cfg["mcpServers"], dict):
                cfg["mcpServers"] = {}

            cfg["mcpServers"]["smart-drive"] = mcp_entry
            save_json_config(path, cfg)
            results[ide_name] = True
        except Exception:
            results[ide_name] = False

    # Also register local workspace .mcp.json if target_dir provided
    if target_dir:
        try:
            local_path = Path(target_dir) / ".mcp.json"
            cfg = load_json_config(local_path)
            if "mcpServers" not in cfg or not isinstance(cfg["mcpServers"], dict):
                cfg["mcpServers"] = {}
            cfg["mcpServers"]["smart-drive"] = mcp_entry
            save_json_config(local_path, cfg)
            results["workspace"] = True
        except Exception:
            results["workspace"] = False

    return results


__all__ = ["get_agent_config_paths", "register_ide_configs"]
