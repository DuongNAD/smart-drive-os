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
    Win32DriveBackend,
)
from smart_drive.core.fsinfo import cluster_size_text, detect_filesystem


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
    unorganized_dev_projects: int = 0
    wip_overload: bool = False

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
            "unorganized_dev_projects": self.unorganized_dev_projects,
            "wip_overload": self.wip_overload,
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
# 3. WARNING EVALUATION ENGINE & DEV PROJECTS INSPECTION
# ==============================================================================

DEV_ZONE_CONTAINER_NAMES: FrozenSet[str] = frozenset({
    "01_active_focus",
    "02_wave_incubator",
    "03_mcp_agents",
    "04_hackathons_pitch",
    "99_archive_dormant",
    "99_archive",
})


def inspect_dev_projects_health(root_path: Optional[str]) -> Tuple[int, bool]:
    """Inspects 03_Development_Projects for unorganized loose projects and WIP overload.

    Returns:
        (unorganized_dev_projects, wip_overload)
        - unorganized_dev_projects: count of projects directly at root of 03_Development_Projects
        - wip_overload: True if count of projects in 01_Active_Focus > 3, False otherwise.
    """
    if not root_path:
        return 0, False

    try:
        norm_root = os.path.abspath(root_path)
    except Exception:
        return 0, False

    base = os.path.basename(os.path.normpath(norm_root))
    if base == "03_Development_Projects":
        dev_dir = norm_root
    else:
        cand = os.path.join(norm_root, "03_Development_Projects")
        dev_dir = cand if os.path.isdir(cand) else None

    if not dev_dir or not os.path.isdir(dev_dir):
        return 0, False

    # 1. Unorganized projects directly under 03_Development_Projects
    unorganized_count = 0
    try:
        for entry in os.scandir(dev_dir):
            if entry.is_dir() and not entry.name.startswith("."):
                if entry.name.lower() not in DEV_ZONE_CONTAINER_NAMES:
                    unorganized_count += 1
    except (OSError, PermissionError):
        pass

    # 2. WIP overload in 01_Active_Focus (> 3 projects)
    active_dir = os.path.join(dev_dir, "01_Active_Focus")
    active_count = 0
    if os.path.isdir(active_dir):
        try:
            for entry in os.scandir(active_dir):
                if entry.is_dir() and not entry.name.startswith("."):
                    active_count += 1
        except (OSError, PermissionError):
            pass

    wip_overload = active_count > 3
    return unorganized_count, wip_overload


def evaluate_health_warnings(
    free_percent: float,
    free_bytes: int,
    total_bytes: int,
    trim_enabled: Optional[bool],
    filesystem: str,
    cluster_size_bytes: int,
    unorganized_dev_projects: int = 0,
    wip_overload: bool = False,
) -> List[str]:
    """Evaluates storage capacity, TRIM status, and geometry to generate actionable warnings.

    Warning Thresholds:
    - Free Space < 5% or Free Space < 10 GB: Critical write stall alert
    - Free Space < 15%: Warning that reserve is low and write amplification will increase
    - TRIM disabled (trim_enabled is False): Warning that endurance/performance will degrade
    - Large cluster size on exFAT (>= 64KB): Slack space advisory
    - WIP overload in 01_Active_Focus (> 3 projects active)
    - Unorganized development projects (> 5 loose projects in 03_Development_Projects)
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
            f"NOTICE: Drive is formatted as exFAT with {cluster_size_text(cluster_size_bytes)} clusters. "
            f"Storing numerous small files (e.g. build caches) will waste significant disk space in cluster slack."
        )

    # 4. WIP overload notice
    if wip_overload:
        warnings.append(
            "WARNING: WIP overload detected in 03_Development_Projects/01_Active_Focus (> 3 active projects). "
            "Stage inactive projects via 'smart-drive stage <project> incubator --apply'."
        )

    # 5. Unorganized dev projects notice
    if unorganized_dev_projects > 5:
        warnings.append(
            f"WARNING: Found {unorganized_dev_projects} unorganized development projects directly under "
            "03_Development_Projects. Organize them into lifecycle zones via 'smart-drive projects --organize'."
        )

    return warnings


# ==============================================================================
# 4. PRIMARY HEALTH CHECK FUNCTION
# ==============================================================================

def _empty_report(label: str, message: str, warning: str) -> SSDHealthReport:
    """A report for a volume that could not be inspected."""
    return SSDHealthReport(
        drive_letter=label,
        filesystem="unknown",
        trim_enabled=None,
        trim_status_message=message,
        total_bytes=0,
        free_bytes=0,
        free_percent=0.0,
        cluster_size_bytes=0,
        warnings=[warning],
        unorganized_dev_projects=0,
        wip_overload=False,
    )


def _check_path_health(path: str) -> SSDHealthReport:
    """Health of the volume that holds ``path`` on macOS/Linux, where there are no drive letters."""
    info = detect_filesystem(path)
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return _empty_report(path, f"Cannot read capacity: {exc}", f"Failed to inspect '{path}': {exc}")
    free_percent = round(usage.free / usage.total * 100.0, 2) if usage.total else 0.0
    mount_or_path = info.mount_point or path
    unorg_dev, wip_ov = inspect_dev_projects_health(mount_or_path)
    return SSDHealthReport(
        drive_letter=mount_or_path,
        filesystem=info.display_name,
        trim_enabled=None,
        trim_status_message="TRIM is not queried on this OS",
        total_bytes=usage.total,
        free_bytes=usage.free,
        free_percent=free_percent,
        cluster_size_bytes=info.block_size,
        warnings=evaluate_health_warnings(
            free_percent=free_percent,
            free_bytes=usage.free,
            total_bytes=usage.total,
            trim_enabled=None,
            filesystem=info.display_name,
            cluster_size_bytes=info.block_size,
            unorganized_dev_projects=unorg_dev,
            wip_overload=wip_ov,
        ),
        unorganized_dev_projects=unorg_dev,
        wip_overload=wip_ov,
    )


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

    # Off Windows there are no drive letters: a path on the volume is the natural specifier.
    # (Only with the real backend; the pluggable mock backends keep their drive-letter flow.)
    if backend is None and sys.platform != "win32" and isinstance(active_backend, Win32DriveBackend):
        if drive_letter is None:
            return _empty_report(
                "", "No volume given",
                "Pass the volume to check as a path, e.g. /Volumes/MySSD (drive letters exist only on Windows).",
            )
        spec = os.fspath(drive_letter)
        if os.path.isdir(spec):
            return _check_path_health(spec)
        if os.sep in spec or spec.startswith(("~", ".")):
            return _empty_report(spec, "Volume path not found", f"Volume path '{spec}' does not exist or is not a directory.")
        # "C:", "d" ... would otherwise fall through to the stub backend, which invents an NTFS volume with TRIM
        # off and advice to run fsutil: none of that is true of this machine.
        return _empty_report(
            spec, "Drive letters exist only on Windows",
            f"'{spec}' looks like a Windows drive letter. Pass a path to a folder on the volume instead, e.g. /Volumes/MySSD.",
        )

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

    # Step 5: Evaluate warnings & dev projects inspection
    target_path = target_letter
    if sys.platform == "win32" and len(target_letter) == 2 and target_letter[1] == ":":
        target_path = f"{target_letter}\\"
    unorg_dev, wip_ov = inspect_dev_projects_health(target_path)

    fs_str = info.filesystem.value if hasattr(info.filesystem, "value") else str(info.filesystem)
    free_pct = round(info.free_percent, 2)
    warnings = evaluate_health_warnings(
        free_percent=free_pct,
        free_bytes=info.free_bytes,
        total_bytes=info.total_bytes,
        trim_enabled=trim_enabled,
        filesystem=fs_str,
        cluster_size_bytes=info.cluster_size_bytes,
        unorganized_dev_projects=unorg_dev,
        wip_overload=wip_ov,
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
        unorganized_dev_projects=unorg_dev,
        wip_overload=wip_ov,
    )


__all__ = [
    "SSDHealthReport",
    "check_drive_health",
    "evaluate_health_warnings",
    "inspect_dev_projects_health",
    "parse_trim_output",
]
