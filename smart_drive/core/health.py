"""smart_drive.core.health - SSD Health, TRIM Verification & Partition Geometry Monitor.

Provides zero-dependency SSD health diagnostic inspection:
- Windows TRIM verification via 'fsutil behavior query DisableDeleteNotify' (unprivileged)
- Cluster / sector geometry calculation via Win32 GetDiskFreeSpaceW (4KB NTFS, 512KB exFAT)
- Real-time disk capacity & utilization monitoring via shutil.disk_usage
- Diagnostic warning evaluation: <15% free warning, <5% free critical, TRIM disabled warning
- Pluggable mock backend support for offline CI/CD test runners

Conforms to PROJECT.md § Interface Contracts.
100% Python Standard Library (ctypes, subprocess, os, sys, shutil, dataclasses).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

from smart_drive.core.drive_detector import (
    DriveDetectorBackend,
    DriveInfo,
    DriveType,
    FilesystemType,
    get_backend,
    get_system_drive_letter,
    inspect_drive,
    list_secondary_drive_letters,
    normalize_drive_letter,
    use_mock_backend,
    verify_trim_support,
)


# ==============================================================================
# 1. SSD HEALTH REPORT DATA CONTRACT
# ==============================================================================

@dataclass
class SSDHealthReport:
    """Authoritative SSD health and diagnostic assessment report.

    Matches PROJECT.md § Interface Contracts:
    - drive_letter: Normalized drive letter (e.g. 'D:')
    - filesystem: Volume filesystem name (e.g. 'NTFS', 'exFAT', 'unknown')
    - trim_enabled: True if TRIM is verified enabled, False if disabled, None if unsupported
    - trim_status_message: Human-readable diagnostic description of TRIM state
    - total_bytes: Total volume capacity in bytes
    - free_bytes: Available free space in bytes
    - free_percent: Percentage of free space (0.0 to 100.0)
    - cluster_size_bytes: Cluster allocation unit size in bytes (e.g. 4096 or 524288)
    - warnings: List of actionable diagnostic warning strings
    """

    drive_letter: str
    filesystem: str
    trim_enabled: Optional[bool]
    trim_status_message: str
    total_bytes: int
    free_bytes: int
    free_percent: float
    cluster_size_bytes: int
    warnings: List[str] = field(default_factory=list)

    @property
    def used_bytes(self) -> int:
        """Calculates bytes used on volume."""
        return max(0, self.total_bytes - self.free_bytes)

    @property
    def total_gb(self) -> float:
        """Total space in gigabytes (GiB)."""
        return self.total_bytes / (1024 ** 3)

    @property
    def free_gb(self) -> float:
        """Free space in gigabytes (GiB)."""
        return self.free_bytes / (1024 ** 3)

    @property
    def used_gb(self) -> float:
        """Used space in gigabytes (GiB)."""
        return self.used_bytes / (1024 ** 3)

    @property
    def is_healthy(self) -> bool:
        """Returns True if no warnings or critical alerts are present."""
        return len(self.warnings) == 0

    def to_dict(self) -> Dict[str, Any]:
        """Serializes report to dictionary matching required interface contracts."""
        return {
            "drive_letter": self.drive_letter,
            "filesystem": self.filesystem,
            "trim_enabled": self.trim_enabled,
            "trim_status_message": self.trim_status_message,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "free_percent": round(self.free_percent, 2),
            "cluster_size_bytes": self.cluster_size_bytes,
            "warnings": list(self.warnings),
        }


# ==============================================================================
# 2. TRIM PARSING & STATUS INFERENCE
# ==============================================================================

def parse_trim_output(raw_output: str) -> Tuple[Optional[bool], str]:
    """Parses output of 'fsutil behavior query DisableDeleteNotify'.

    Windows 10/11 outputs:
      NTFS DisableDeleteNotify = 0  (Allows TRIM operations to be sent...)
      ReFS DisableDeleteNotify = 0  (...)
    Where DisableDeleteNotify = 0 means TRIM is ENABLED.
    DisableDeleteNotify = 1 means TRIM is DISABLED.

    Returns:
        (trim_enabled: Optional[bool], trim_status_message: str)
    """
    if not raw_output or not raw_output.strip():
        return None, "TRIM status unavailable"

    out_lower = raw_output.lower()

    # DisableDeleteNotify = 0 -> TRIM is ENABLED
    if "ntfs disabledeletenotify = 0" in out_lower or "disabledeletenotify = 0" in out_lower:
        return True, "TRIM is enabled (DisableDeleteNotify = 0)"

    # DisableDeleteNotify = 1 -> TRIM is DISABLED
    if "ntfs disabledeletenotify = 1" in out_lower or "disabledeletenotify = 1" in out_lower:
        return False, "TRIM is disabled (DisableDeleteNotify = 1)"

    if "not recognized" in out_lower or "not supported" in out_lower or "error" in out_lower:
        return False, f"TRIM query unsupported or error: {raw_output.strip()}"

    return None, f"Unrecognized TRIM status: {raw_output.strip()}"


# ==============================================================================
# 3. WARNING EVALUATION ENGINE
# ==============================================================================

def evaluate_health_warnings(
    free_percent: float,
    free_bytes: int,
    total_bytes: int,
    trim_enabled: Optional[bool],
    filesystem: str,
    cluster_size_bytes: int,
) -> List[str]:
    """Evaluates storage capacity, TRIM status, and geometry to generate actionable warnings.

    Warning Thresholds:
    - Free Space < 5% or Free Space < 10 GB: Critical write stall alert
    - Free Space < 15%: Warning that reserve is low and write amplification will increase
    - TRIM disabled (trim_enabled is False): Warning that endurance/performance will degrade
    - Large cluster size on exFAT (>= 64KB): Slack space advisory
    """
    warnings: List[str] = []

    # 1. Capacity thresholds
    if total_bytes > 0:
        if free_percent < 5.0 or free_bytes < 10 * (1024 ** 3):
            warnings.append(
                f"CRITICAL: Free space is critically low ({free_percent:.1f}% remaining, "
                f"{free_bytes / (1024**3):.1f} GB). SSD performance degradation and write stalls likely."
            )
        elif free_percent < 15.0:
            warnings.append(
                f"WARNING: Free space is below recommended 15% reserve ({free_percent:.1f}% remaining, "
                f"{free_bytes / (1024**3):.1f} GB). SSD write amplification will increase."
            )

    # 2. TRIM status
    if trim_enabled is False:
        warnings.append(
            "WARNING: SSD TRIM is disabled. Write endurance and garbage collection performance will degrade. "
            "Enable via 'fsutil behavior set DisableDeleteNotify 0' from an elevated prompt."
        )

    # 3. exFAT large cluster slack notice
    if filesystem.upper() == "EXFAT" and cluster_size_bytes >= 65536:
        warnings.append(
            f"NOTICE: Drive is formatted as exFAT with {cluster_size_bytes // 1024}KB clusters. "
            f"Storing numerous small files (e.g. build caches) will waste significant disk space in cluster slack."
        )

    return warnings


# ==============================================================================
# 4. PRIMARY HEALTH CHECK FUNCTION
# ==============================================================================

def check_drive_health(
    drive_letter: Optional[Union[str, os.PathLike[str]]] = None,
    backend: Optional[DriveDetectorBackend] = None,
) -> SSDHealthReport:
    """Inspects SSD TRIM status, partition geometry, and storage utilization.

    Args:
        drive_letter: Optional target drive letter or path (e.g. 'D:', 'D:\\', or path).
                      If None, defaults to the first available secondary drive (D:, E:, etc.),
                      or falls back to the system drive (C:) if no secondary drives exist.
        backend: Optional pluggable DriveDetectorBackend (defaults to active backend).

    Returns:
        SSDHealthReport conforming to PROJECT.md § Interface Contracts.
    """
    active_backend = backend or get_backend()

    # Step 1: Default drive letter resolution
    target_letter: Optional[str] = None
    if drive_letter is None:
        # Check secondary drives first
        sys_letter = get_system_drive_letter().upper()
        all_drives = active_backend.list_drive_letters()
        secondary = [
            normalize_drive_letter(d)
            for d in all_drives
            if normalize_drive_letter(d).upper() not in ("C:", sys_letter)
        ]
        if secondary:
            target_letter = secondary[0]
        else:
            target_letter = sys_letter
    else:
        try:
            target_letter = normalize_drive_letter(drive_letter)
        except ValueError as ve:
            # Malformed drive specifier
            return SSDHealthReport(
                drive_letter=str(drive_letter),
                filesystem="unknown",
                trim_enabled=None,
                trim_status_message="Drive specifier invalid",
                total_bytes=0,
                free_bytes=0,
                free_percent=0.0,
                cluster_size_bytes=0,
                warnings=[str(ve)],
            )

    # Step 2: Validate drive availability
    available_drives = [d.upper() for d in active_backend.list_drive_letters()]
    if target_letter.upper() not in available_drives:
        return SSDHealthReport(
            drive_letter=target_letter,
            filesystem="unknown",
            trim_enabled=None,
            trim_status_message="Drive not mounted or inaccessible",
            total_bytes=0,
            free_bytes=0,
            free_percent=0.0,
            cluster_size_bytes=0,
            warnings=[f"Drive '{target_letter}' not found or is not mounted."],
        )

    # Step 3: Query drive details via inspect_drive or backend
    try:
        # If custom backend was passed, temporarily set it for inspect_drive
        if backend is not None:
            with use_mock_backend(backend):
                info: DriveInfo = inspect_drive(target_letter)
        else:
            info = inspect_drive(target_letter)
    except Exception as exc:
        return SSDHealthReport(
            drive_letter=target_letter,
            filesystem="unknown",
            trim_enabled=None,
            trim_status_message=f"Drive inspection error: {exc}",
            total_bytes=0,
            free_bytes=0,
            free_percent=0.0,
            cluster_size_bytes=0,
            warnings=[f"Failed to inspect drive '{target_letter}': {exc}"],
        )

    # Step 4: Query TRIM status
    trim_info = active_backend.query_trim_status()
    raw_trim = trim_info.get("raw", "")
    if raw_trim:
        trim_enabled, trim_status_msg = parse_trim_output(raw_trim)
    else:
        trim_enabled = trim_info.get("enabled")
        trim_status_msg = trim_info.get("message", "TRIM query completed")

    # Step 5: Evaluate warnings
    fs_str = info.filesystem.value if hasattr(info.filesystem, "value") else str(info.filesystem)
    free_pct = round(info.free_percent, 2)
    warnings = evaluate_health_warnings(
        free_percent=free_pct,
        free_bytes=info.free_bytes,
        total_bytes=info.total_bytes,
        trim_enabled=trim_enabled,
        filesystem=fs_str,
        cluster_size_bytes=info.cluster_size_bytes,
    )

    return SSDHealthReport(
        drive_letter=info.drive_letter,
        filesystem=fs_str,
        trim_enabled=trim_enabled,
        trim_status_message=trim_status_msg,
        total_bytes=info.total_bytes,
        free_bytes=info.free_bytes,
        free_percent=free_pct,
        cluster_size_bytes=info.cluster_size_bytes,
        warnings=warnings,
    )


__all__ = [
    "SSDHealthReport",
    "check_drive_health",
    "evaluate_health_warnings",
    "parse_trim_output",
]
