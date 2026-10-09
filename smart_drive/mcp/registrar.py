"""smart_drive.mcp.registrar - Multi-IDE MCP Configuration Registrar.

Registers the SmartDrive MCP Server across:
- Google Antigravity 2.0 (~/.gemini/antigravity/mcp_config.json)
- Claude Desktop (~/Library/Application Support/Claude or %APPDATA%/Claude) & Claude Code (~/.claude.json)
- Cursor IDE / OpenAI Codex (~/.cursor/mcp.json)
- Windsurf IDE (~/.codeium/windsurf/mcp_config.json)
- SSD Workspace (.mcp.json)
"""

from __future__ import annotations

from datetime import datetime
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


class ConfigParseError(ValueError):
    """Raised when JSON configuration parsing fails in strict mode."""
    pass


def _safe_home_dir() -> Path:
    """Safely determines user home directory with fallbacks for stripped environments (Python 3.13+)."""
    try:
        return Path.home()
    except Exception:
        pass

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        return Path(user_profile)
    home_env = os.environ.get("HOME")
    if home_env:
        return Path(home_env)
    if sys.platform == "win32" or platform.system().lower() == "windows":
        return Path("C:/Users/Default")
    return Path("/tmp")


def get_agent_config_paths() -> Dict[str, Path]:
    """Returns standard config paths for supported AI agents on this host."""
    home = _safe_home_dir()
    system = platform.system().lower()

    # 1. Google Antigravity 2.0
    antigravity_home = os.environ.get("ANTIGRAVITY_HOME") or os.environ.get("GEMINI_HOME")
    if antigravity_home:
        antigravity_path = Path(antigravity_home) / "mcp_config.json"
    else:
        antigravity_path = home / ".gemini" / "antigravity" / "mcp_config.json"

    # 2. Claude Desktop & Claude Code
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

    claude_code_path = home / ".claude.json"

    # 3. Cursor IDE & OpenAI Codex
    cursor_path = home / ".cursor" / "mcp.json"

    # 4. Windsurf IDE
    windsurf_path = home / ".codeium" / "windsurf" / "mcp_config.json"

    return {
        "antigravity": antigravity_path,
        "claude": claude_path,
        "claude_code": claude_code_path,
        "cursor": cursor_path,
        "codex": cursor_path,
        "windsurf": windsurf_path,
    }


def detect_installed_agents() -> Dict[str, bool]:
    """Detects which supported AI coding agents are installed on the local system."""
    home = _safe_home_dir()
    system = platform.system().lower()

    detected = {
        "antigravity": False,
        "claude": False,
        "cursor": False,
        "windsurf": False,
        "workspace": False,
    }

    # 1. Antigravity: check directory or config file or environment
    antigravity_dir = home / ".gemini" / "antigravity"
    if (
        antigravity_dir.is_dir()
        or (antigravity_dir / "mcp_config.json").is_file()
        or os.environ.get("ANTIGRAVITY_HOME")
        or os.environ.get("GEMINI_HOME")
    ):
        detected["antigravity"] = True

    # 2. Claude: check desktop directory, .claude.json, or binary
    if "darwin" in system:
        claude_desktop = home / "Library" / "Application Support" / "Claude"
    elif "windows" in system or sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        claude_desktop = Path(appdata) / "Claude" if appdata else home / "AppData" / "Roaming" / "Claude"
    else:  # Linux
        claude_desktop = home / ".config" / "Claude"

    if (
        claude_desktop.is_dir()
        or (home / ".claude.json").is_file()
        or (home / ".claude").is_dir()
        or (shutil.which("claude") is not None)
    ):
        detected["claude"] = True

    # 3. Cursor / Codex: check ~/.cursor, mcp.json, or binary
    if (home / ".cursor").is_dir() or (home / ".cursor" / "mcp.json").is_file() or (shutil.which("cursor") is not None):
        detected["cursor"] = True

    # 4. Windsurf: check ~/.codeium/windsurf, mcp_config.json, or binary
    if (
        (home / ".codeium" / "windsurf").is_dir()
        or (home / ".codeium" / "windsurf" / "mcp_config.json").is_file()
        or (shutil.which("windsurf") is not None)
    ):
        detected["windsurf"] = True

    # 5. Local Workspace: check for .mcp.json in current directory or project root
    if (
        Path(".mcp.json").is_file()
        or Path("configs/.mcp.json").is_file()
        or Path("smart_drive").is_dir()
    ):
        detected["workspace"] = True

    return detected


def load_json_config(path: Path, strict: bool = False) -> Dict[str, Any]:
    """Safely loads an existing JSON configuration file."""
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                if strict:
                    raise ConfigParseError(
                        f"Root JSON element in '{path}' must be an object/dict, got {type(data).__name__}"
                    )
                return {}
            return data
    except Exception as err:
        if strict:
            if isinstance(err, ConfigParseError):
                raise
            raise ConfigParseError(f"Failed to parse JSON config '{path}': {err}") from err
        return {}


def save_json_config(path: Path, data: Dict[str, Any]) -> None:
    """Safely and atomically writes a JSON configuration file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    try:
        json.dump(data, temp_file, indent=2, ensure_ascii=False)
        temp_file.write("\n")
        temp_file.flush()
        os.fsync(temp_file.fileno())
        temp_file.close()
        os.replace(str(temp_path), str(path))
    except Exception:
        temp_file.close()
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise


def _source_checkout_root() -> Optional[Path]:
    """Returns the directory that must be on PYTHONPATH to import ``smart_drive``, or None.

    The portable, zero-install layout is only importable while its root is on ``sys.path``.
    ``python -m smart_drive`` finds it through the current directory, but MCP clients start
    their servers from an arbitrary directory, so the registered entry has to carry the path
    (the launchers do the same with PYTHONPATH). A pip-installed copy is already importable.
    """
    package_dir = Path(__file__).resolve().parent.parent
    if any(part.lower() in ("site-packages", "dist-packages") for part in package_dir.parts):
        return None
    return package_dir.parent


def build_mcp_entry(python_cmd: Optional[str] = None) -> Dict[str, Any]:
    """Builds standard MCP client server registration configuration dict."""
    if python_cmd is None:
        python_cmd = sys.executable or "python3"
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    checkout_root = _source_checkout_root()
    if checkout_root is not None:
        env["PYTHONPATH"] = str(checkout_root)
    return {
        "command": python_cmd,
        "args": ["-m", "smart_drive", "mcp"],
        "env": env,
    }


# Backwards compatibility alias
mcp_entry = build_mcp_entry


def register_ide_configs(
    target_dir: Optional[str] = None,
    flags: Optional[Dict[str, bool]] = None,
    auto_detect: bool = False,
    python_cmd: Optional[str] = None,
) -> Dict[str, bool]:
    """Registers SmartDrive MCP Server in standard IDE configuration files.

    Args:
        target_dir: Optional workspace directory to also write local `.mcp.json`.
        flags: Optional mapping of IDE names to boolean flags (e.g. {'antigravity': True}).
        auto_detect: If True and no selective flags provided, only register for detected agents.
        python_cmd: Optional Python executable to record in configuration (defaults to sys.executable).

    Returns:
        Mapping of target name to registration success status.
    """
    if python_cmd is None:
        python_cmd = sys.executable or "python3"
    results: Dict[str, bool] = {}

    entry = build_mcp_entry(python_cmd)
    config_paths = get_agent_config_paths()
    detected = detect_installed_agents() if auto_detect else {}

    primary_agents = ["antigravity", "claude", "cursor", "windsurf"]

    # Only register global targets if explicit flags are given OR auto_detect is True
    if flags is not None or auto_detect:
        for ide_name in primary_agents:
            path = config_paths.get(ide_name)
            if not path:
                continue

            if flags is not None:
                if ide_name == "cursor" and "codex" in flags and "cursor" not in flags:
                    should_register = flags.get("codex", True)
                else:
                    should_register = flags.get(ide_name, True)
                if not should_register:
                    continue
            elif auto_detect:
                if any(detected.values()) and not detected.get(ide_name, False):
                    continue

            try:
                if path.is_file():
                    cfg = load_json_config(path, strict=True)
                else:
                    cfg = {}
                if "mcpServers" not in cfg or not isinstance(cfg["mcpServers"], dict):
                    cfg["mcpServers"] = {}

                cfg["mcpServers"]["smart-drive"] = entry
                save_json_config(path, cfg)
                results[ide_name] = True

                # If registering Claude, also update ~/.claude.json if present
                if ide_name == "claude":
                    claude_code_path = config_paths.get("claude_code")
                    if claude_code_path and claude_code_path.is_file():
                        try:
                            cc_cfg = load_json_config(claude_code_path, strict=True)
                            if "mcpServers" not in cc_cfg or not isinstance(cc_cfg["mcpServers"], dict):
                                cc_cfg["mcpServers"] = {}
                            cc_cfg["mcpServers"]["smart-drive"] = entry
                            save_json_config(claude_code_path, cc_cfg)
                            results["claude_code"] = True
                        except Exception:
                            pass
            except Exception:
                results[ide_name] = False

    # Also register local workspace .mcp.json if target_dir provided or requested
    local_path = None
    if target_dir:
        local_path = Path(target_dir) / ".mcp.json"
    elif (flags and flags.get("workspace")) or (auto_detect and Path(".mcp.json").is_file()):
        local_path = Path.cwd() / ".mcp.json"

    if local_path is not None:
        try:
            if local_path.is_file():
                try:
                    cfg = load_json_config(local_path, strict=True)
                except ConfigParseError:
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    bak_path = local_path.parent / f"{local_path.name}.bak-{timestamp}"
                    shutil.copy2(str(local_path), str(bak_path))
                    cfg = {}
            else:
                cfg = {}

            if "mcpServers" not in cfg or not isinstance(cfg["mcpServers"], dict):
                cfg["mcpServers"] = {}
            cfg["mcpServers"]["smart-drive"] = entry
            save_json_config(local_path, cfg)
            results["workspace"] = True
        except Exception:
            results["workspace"] = False

    return results


__all__ = [
    "ConfigParseError",
    "_safe_home_dir",
    "detect_installed_agents",
    "get_agent_config_paths",
    "load_json_config",
    "register_ide_configs",
    "save_json_config",
]
