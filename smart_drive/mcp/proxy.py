"""smart_drive.mcp.proxy - Dynamic Mount Discovery Proxy for Kingston SSD MCP Server.

Discovers the SSD mount root across macOS, Windows, and Linux in <100ms.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Tuple

from smart_drive.core.root import find_drive_root


class SmartDriveProxy:
    """Dynamic discovery proxy for Kingston XS2000 SSD mount points."""

    @classmethod
    def detect_mount_point(cls) -> Optional[Path]:
        """Dynamically discovers the SSD mount root.

        Returns:
            Path object of the detected drive root, or None if not located.
        """
        res = find_drive_root()
        if res:
            return Path(res.path)
        return None

    @classmethod
    def discover_with_latency(cls) -> Tuple[Optional[Path], float]:
        """Detects mount point and returns (mount_path, latency_ms)."""
        t0 = time.perf_counter()
        mount = cls.detect_mount_point()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return mount, elapsed_ms


__all__ = ["SmartDriveProxy"]
