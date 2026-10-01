"""smart_drive.mcp.proxy - Dynamic Mount Discovery Proxy for Kingston SSD MCP Server.

Discovers the SSD mount root across macOS, Windows, and Linux in <100ms.
"""

from __future__ import annotations

import os
import platform
import string
import sys
import time
from pathlib import Path
from typing import Optional, Tuple


class SmartDriveProxy:
    """Dynamic discovery proxy for Kingston XS2000 SSD mount points."""

    @classmethod
    def detect_mount_point(cls) -> Optional[Path]:
        """Dynamically discovers the SSD mount root.

        Returns:
            Path object of the detected drive root, or None if not located.
        """
        # 1. Environment variable override
        env_root = os.environ.get("SMART_DRIVE_ROOT") or os.environ.get("KINGSTON_SSD_ROOT")
        if env_root and os.path.isdir(env_root):
            return Path(env_root).resolve()

        # 2. Check current working directory or ancestors
        cwd = Path.cwd()
        if sys.platform != "win32" and len(str(cwd)) >= 2 and str(cwd)[1] == ":":
            cwd_cand = cwd
        else:
            try:
                cwd_cand = cwd.resolve()
            except Exception:
                cwd_cand = cwd
        parents = [p for p in cwd_cand.parents if str(p) not in (".", "")]
        for p in [cwd_cand] + parents:
            if str(p) in (".", ""):
                continue
            if (p / "GEMINI.md").is_file() or (p / "AGENTS.md").is_file():
                return p
            if p.name.lower() == "kingston":
                return p

        # 3. macOS probe
        system = platform.system().lower()
        if "darwin" in system or sys.platform == "darwin":
            if os.path.isdir("/Volumes/KINGSTON"):
                return Path("/Volumes/KINGSTON")
            volumes = Path("/Volumes")
            if volumes.is_dir():
                try:
                    for vol in volumes.iterdir():
                        if (vol / "GEMINI.md").is_file() or (vol / "AGENTS.md").is_file():
                            return vol
                except (PermissionError, OSError):
                    pass

        # 4. Windows drive letter probe
        elif "windows" in system or sys.platform == "win32":
            letters = [l for l in string.ascii_uppercase if l not in ("A", "B", "C")] + ["C"]
            for letter in letters:
                root_cand = Path(f"{letter}:/")
                if root_cand.is_dir():
                    if (root_cand / "GEMINI.md").is_file() or (root_cand / "AGENTS.md").is_file():
                        return root_cand
            # Direct check for D:\ or D:/
            if Path("D:/").is_dir() or Path("D:\\").is_dir():
                return Path("D:/")

        # 5. Linux probe
        else:
            candidates = [Path("/media"), Path("/mnt"), Path("/run/media")]
            for base in candidates:
                if base.is_dir():
                    try:
                        for entry in base.glob("**/*"):
                            if entry.is_dir() and ((entry / "GEMINI.md").is_file() or (entry / "AGENTS.md").is_file()):
                                return entry
                    except (PermissionError, OSError):
                        pass

        return None

    @classmethod
    def discover_with_latency(cls) -> Tuple[Optional[Path], float]:
        """Detects mount point and returns (mount_path, latency_ms)."""
        t0 = time.perf_counter()
        mount = cls.detect_mount_point()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return mount, elapsed_ms


__all__ = ["SmartDriveProxy"]
