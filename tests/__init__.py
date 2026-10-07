"""SmartDrive-OS Test Suite - Session Isolation & Test Harness Initialization.

Executed at import before any test module in both pytest and unittest discovery runners.
Guarantees strict isolation of HOME, APPDATA, cache directories, and SSD root.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# Create session isolation temporary directory
_SESSION_TEMP_DIR = tempfile.mkdtemp(prefix="sdos_test_session_")
_SESSION_PATH = Path(_SESSION_TEMP_DIR).resolve()

_SESSION_HOME = _SESSION_PATH / "home"
_SESSION_APPDATA = _SESSION_PATH / "appdata"
_SESSION_LOCALAPPDATA = _SESSION_PATH / "localappdata"
_SESSION_DRIVE = _SESSION_PATH / "drive"
_SESSION_XDG_CONFIG = _SESSION_PATH / "xdg_config"
_SESSION_XDG_CACHE = _SESSION_PATH / "xdg_cache"
_SESSION_XDG_DATA = _SESSION_PATH / "xdg_data"

_SESSION_HOME.mkdir(parents=True, exist_ok=True)
_SESSION_APPDATA.mkdir(parents=True, exist_ok=True)
_SESSION_LOCALAPPDATA.mkdir(parents=True, exist_ok=True)
_SESSION_DRIVE.mkdir(parents=True, exist_ok=True)
_SESSION_XDG_CONFIG.mkdir(parents=True, exist_ok=True)
_SESSION_XDG_CACHE.mkdir(parents=True, exist_ok=True)
_SESSION_XDG_DATA.mkdir(parents=True, exist_ok=True)

# 1. Point standard home / user profile / appdata variables into session directory
os.environ["HOME"] = str(_SESSION_HOME)
os.environ["USERPROFILE"] = str(_SESSION_HOME)
os.environ["APPDATA"] = str(_SESSION_APPDATA)
os.environ["LOCALAPPDATA"] = str(_SESSION_LOCALAPPDATA)
os.environ["XDG_CONFIG_HOME"] = str(_SESSION_XDG_CONFIG)
os.environ["XDG_CACHE_HOME"] = str(_SESSION_XDG_CACHE)
os.environ["XDG_DATA_HOME"] = str(_SESSION_XDG_DATA)

# 2. Unset custom agent and drive roots
for var in ["ANTIGRAVITY_HOME", "GEMINI_HOME", "KINGSTON_SSD_ROOT"]:
    os.environ.pop(var, None)

# 3. Derive and unset cache environment variables from core.offloader catalog
_CACHE_ENV_VARS = {
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "UV_CACHE_DIR",
    "OLLAMA_MODELS",
    "PIP_CACHE_DIR",
    "npm_config_cache",
    "NPM_CONFIG_CACHE",
    "TORCH_HOME",
    "CONDA_PKGS_DIRS",
    "GRADLE_USER_HOME",
}
try:
    from smart_drive.core.offloader import CACHE_CATALOG
    for _defn in CACHE_CATALOG.values():
        for _ev in _defn.env_vars:
            _CACHE_ENV_VARS.add(_ev)
except Exception:
    pass

for _ev in _CACHE_ENV_VARS:
    os.environ.pop(_ev, None)

# 4. Configure default test drive root and disable host OS drive letter probing
os.environ["SMART_DRIVE_ROOT"] = str(_SESSION_DRIVE)
os.environ["SMART_DRIVE_NO_PROBE"] = "1"


def _cleanup_test_session() -> None:
    """Removes the session temp directory on Python exit."""
    shutil.rmtree(_SESSION_TEMP_DIR, ignore_errors=True)


atexit.register(_cleanup_test_session)
