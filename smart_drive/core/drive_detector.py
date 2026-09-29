"""smart_drive.core.drive_detector - Secondary Drive Detection & Filesystem Adaptation Engine.

Provides zero-dependency Windows and cross-platform detection for internal secondary drives
(D:, E:, F:... strictly excluding the Windows OS system drive C:), hardware bus type
classification (Fixed Internal NVMe/SATA vs Removable External USB SSD), volume cluster
geometry inspection, dual-filesystem adaptation (NTFS vs exFAT), and SSD TRIM health query.

Conforms to PROJECT.md § Interface Contracts.
100% Python Standard Library (ctypes, subprocess, os, sys, shutil, dataclasses, enum).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union


# ==============================================================================
# 1. CORE ENUMS & CONSTANTS
# ==============================================================================

class DriveType(Enum):
    """Hardware physical connection and mounting type."""
    FIXED_INTERNAL = "fixed_internal"
    REMOVABLE_EXTERNAL = "removable_external"
    UNKNOWN = "unknown"


class FilesystemType(Enum):
    """Volume filesystem format."""
    NTFS = "NTFS"
    EXFAT = "exFAT"
    FAT32 = "FAT32"
    OTHER = "other"


# Win32 Kernel & Storage IOCTL Constants
IOCTL_STORAGE_QUERY_PROPERTY: int = 0x002D1400
FILE_SHARE_READ: int = 0x00000001
FILE_SHARE_WRITE: int = 0x00000002
OPEN_EXISTING: int = 3
INVALID_HANDLE_VALUE: int = -1

# Win32 GetDriveType Return Values
DRIVE_UNKNOWN: int = 0
DRIVE_NO_ROOT_DIR: int = 1
DRIVE_REMOVABLE: int = 2
DRIVE_FIXED: int = 3
DRIVE_REMOTE: int = 4
DRIVE_CDROM: int = 5
DRIVE_RAMDISK: int = 6

# Win32 File Attributes
FILE_ATTRIBUTE_REPARSE_POINT: int = 0x00000400
FILE_ATTRIBUTE_COMPRESSED: int = 0x00000800
FILE_ATTRIBUTE_NOT_CONTENT_INDEXED: int = 0x00002000

# Storage Bus Type mapping to (bus_name, DriveType)
STORAGE_BUS_TYPE_MAP: Dict[int, Tuple[str, DriveType]] = {
    0: ("Unknown", DriveType.UNKNOWN),
    1: ("SCSI", DriveType.FIXED_INTERNAL),
    2: ("ATAPI", DriveType.FIXED_INTERNAL),
    3: ("ATA", DriveType.FIXED_INTERNAL),
    4: ("1394", DriveType.REMOVABLE_EXTERNAL),
    5: ("SSA", DriveType.FIXED_INTERNAL),
    6: ("Fibre", DriveType.FIXED_INTERNAL),
    7: ("USB", DriveType.REMOVABLE_EXTERNAL),
    8: ("RAID", DriveType.FIXED_INTERNAL),
    9: ("iSCSI", DriveType.FIXED_INTERNAL),
    10: ("SAS", DriveType.FIXED_INTERNAL),
    11: ("SATA", DriveType.FIXED_INTERNAL),
    12: ("SD", DriveType.REMOVABLE_EXTERNAL),
    13: ("MMC", DriveType.REMOVABLE_EXTERNAL),
    14: ("Virtual", DriveType.UNKNOWN),
    15: ("FileBackedVirtual", DriveType.UNKNOWN),
    16: ("Spaces", DriveType.FIXED_INTERNAL),
    17: ("NVMe", DriveType.FIXED_INTERNAL),
    18: ("SCM", DriveType.FIXED_INTERNAL),
    19: ("Ufs", DriveType.REMOVABLE_EXTERNAL),
}


# ==============================================================================
# 2. FILESYSTEM ADAPTER
# ==============================================================================

@dataclass
class FilesystemAdapter:
    """Encapsulates filesystem-specific policies, capabilities, and cluster math."""

    filesystem: FilesystemType
    cluster_size_bytes: int = 4096

    @property
    def supports_junctions(self) -> bool:
        """True if volume supports NTFS Directory Junctions (mklink /J)."""
        return self.filesystem == FilesystemType.NTFS

    @property
    def supports_compression(self) -> bool:
        """True if volume supports transparent filesystem-level file compression."""
        return self.filesystem == FilesystemType.NTFS

    @property
    def supports_selective_indexing(self) -> bool:
        """True if volume supports FILE_ATTRIBUTE_NOT_CONTENT_INDEXED (0x2000)."""
        return self.filesystem == FilesystemType.NTFS

    @property
    def supports_trim(self) -> bool:
        """True if filesystem natively coordinates TRIM / deallocate with OS."""
        return self.filesystem == FilesystemType.NTFS

    @property
    def anti_symlink_required(self) -> bool:
        """True if filesystem requires strict anti-symlink enforcement (exFAT)."""
        return self.filesystem == FilesystemType.EXFAT

    @property
    def is_slack_sensitive(self) -> bool:
        """True if volume has large cluster size causing high slack waste on small files."""
        return self.filesystem == FilesystemType.EXFAT or self.cluster_size_bytes >= 65536

    def calculate_allocated_bytes(self, nominal_size: int) -> int:
        """Calculates physical cluster-allocated disk space in bytes.

        0-byte files consume 0 clusters (0 bytes).
        Files > 0 bytes consume ceil(nominal_size / cluster_size) * cluster_size.
        """
        if nominal_size < 0:
            raise ValueError(f"File size cannot be negative: {nominal_size}")
        if self.cluster_size_bytes <= 0:
            raise ValueError(f"Cluster size must be positive: {self.cluster_size_bytes}")
        if nominal_size == 0:
            return 0
        return ((nominal_size + self.cluster_size_bytes - 1) // self.cluster_size_bytes) * self.cluster_size_bytes

    def calculate_slack_bytes(self, nominal_size: int) -> int:
        """Calculates wasted cluster slack space in bytes."""
        return self.calculate_allocated_bytes(nominal_size) - nominal_size

    def calculate_slack_percentage(self, nominal_size: int) -> float:
        """Calculates percentage of allocated space lost to cluster slack."""
        allocated = self.calculate_allocated_bytes(nominal_size)
        if allocated == 0:
            return 0.0
        return ((allocated - nominal_size) / allocated) * 100.0

    def evaluate_cluster_slack(self, file_count: int, avg_file_size: int = 4096) -> Dict[str, Any]:
        """Evaluates cluster slack risk for a hypothetical file set (e.g. cache directory)."""
        if file_count < 0 or avg_file_size < 0:
            raise ValueError("File count and average size must be non-negative.")
        single_allocated = self.calculate_allocated_bytes(avg_file_size)
        single_slack = self.calculate_slack_bytes(avg_file_size)
        total_nominal = file_count * avg_file_size
        total_allocated = file_count * single_allocated
        total_slack = file_count * single_slack
        slack_pct = (total_slack / total_allocated * 100.0) if total_allocated > 0 else 0.0

        is_critical = self.is_slack_sensitive and (total_slack > 500 * 1024 * 1024 or slack_pct > 80.0)
        warning: Optional[str] = None
        if self.filesystem == FilesystemType.EXFAT and total_slack > 50 * 1024 * 1024:
            warning = (
                f"exFAT cluster slack guard: Storing {file_count} files (~{avg_file_size}B each) "
                f"on {self.cluster_size_bytes // 1024}KB clusters will waste "
                f"{total_slack / (1024 * 1024):.1f} MB in cluster slack ({slack_pct:.1f}% wasted). "
                f"Consider using an NTFS secondary drive or archiving into a consolidated container."
            )

        return {
            "filesystem": self.filesystem.value,
            "cluster_size_bytes": self.cluster_size_bytes,
            "file_count": file_count,
            "total_nominal_bytes": total_nominal,
            "total_allocated_bytes": total_allocated,
            "total_slack_bytes": total_slack,
            "slack_percentage": slack_pct,
            "is_critical": is_critical,
            "warning": warning,
        }

    def check_symlink_violation(self, path: Union[str, Path]) -> bool:
        """Checks if a path violates the filesystem's symlink policy.

        On exFAT: Returns True if the path is a symlink (policy violation).
        On NTFS: Returns False (symlinks/junctions permitted).
        """
        p = Path(path)
        if not self.anti_symlink_required:
            return False
        return p.is_symlink()

    def compress_path(self, target_path: Union[str, Path], recursive: bool = True) -> Tuple[bool, str]:
        """Applies transparent NTFS file compression to target path.

        Returns (success: bool, message: str).
        """
        if not self.supports_compression:
            return False, f"Transparent compression is not supported on {self.filesystem.value} filesystem."

        p = str(Path(target_path).resolve())
        cmd = ["compact", "/C"]
        if recursive:
            cmd.extend(["/S", "/I", "/Q"])
        cmd.append(p)

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode == 0:
                return True, f"Successfully compressed '{p}'"
            return False, f"compact failed with exit code {res.returncode}: {res.stderr.strip() or res.stdout.strip()}"
        except Exception as exc:
            return False, f"Failed to execute compact command: {exc}"

    def set_content_indexing(self, target_path: Union[str, Path], exclude: bool = True) -> Tuple[bool, str]:
        """Sets or unsets FILE_ATTRIBUTE_NOT_CONTENT_INDEXED (0x2000) on NTFS.

        Returns (success: bool, message: str).
        """
        if not self.supports_selective_indexing:
            return False, f"Selective indexing attribute is not supported on {self.filesystem.value} filesystem."

        p = str(Path(target_path).resolve())
        if sys.platform == "win32":
            try:
                # Use SetFileAttributesW directly
                kernel32 = ctypes.windll.kernel32
                current_attrs = kernel32.GetFileAttributesW(p)
                if current_attrs != 0xFFFFFFFF:
                    if exclude:
                        new_attrs = current_attrs | FILE_ATTRIBUTE_NOT_CONTENT_INDEXED
                    else:
                        new_attrs = current_attrs & ~FILE_ATTRIBUTE_NOT_CONTENT_INDEXED
                    if kernel32.SetFileAttributesW(p, new_attrs):
                        return True, f"Successfully updated index exclusion attribute on '{p}'"
            except Exception:
                pass

        # Fallback to attrib command
        flag = "+I" if exclude else "-I"
        try:
            res = subprocess.run(["attrib", flag, p, "/S", "/D"], capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                return True, f"Successfully executed attrib {flag} on '{p}'"
            return False, f"attrib failed with exit code {res.returncode}: {res.stderr.strip()}"
        except Exception as exc:
            return False, f"Failed to execute attrib command: {exc}"


def get_filesystem_adapter(filesystem: Union[FilesystemType, str], cluster_size_bytes: Optional[int] = None) -> FilesystemAdapter:
    """Factory creating a FilesystemAdapter for the given filesystem."""
    if isinstance(filesystem, str):
        fs_upper = filesystem.upper().strip()
        if "NTFS" in fs_upper:
            fs_type = FilesystemType.NTFS
        elif "EXFAT" in fs_upper:
            fs_type = FilesystemType.EXFAT
        elif "FAT32" in fs_upper:
            fs_type = FilesystemType.FAT32
        else:
            fs_type = FilesystemType.OTHER
    else:
        fs_type = filesystem

    if cluster_size_bytes is None:
        if fs_type == FilesystemType.NTFS:
            cluster_size_bytes = 4096
        elif fs_type == FilesystemType.EXFAT:
            cluster_size_bytes = 524288
        else:
            cluster_size_bytes = 4096

    return FilesystemAdapter(filesystem=fs_type, cluster_size_bytes=cluster_size_bytes)


# ==============================================================================
# 3. DRIVEINFO DATACLASS
# ==============================================================================

@dataclass
class DriveInfo:
    """Authoritative descriptor of a logical drive volume and its physical hardware."""

    # Core interface contract fields (matching PROJECT.md § Interface Contracts)
    drive_letter: str          # e.g. "D:"
    mount_point: str           # e.g. "D:\\"
    is_system_drive: bool      # True for C:
    hardware_type: DriveType   # FIXED_INTERNAL vs REMOVABLE_EXTERNAL
    filesystem: FilesystemType # NTFS vs exFAT
    cluster_size_bytes: int    # e.g. 4096 or 524288
    total_bytes: int
    free_bytes: int
    volume_label: str

    # Extended metadata fields with clean defaults
    bus_type: str = "unknown"
    vendor_model: str = ""
    serial_number: str = ""
    sectors_per_cluster: int = 8
    bytes_per_sector: int = 512

    @property
    def supports_junctions(self) -> bool:
        """True if volume supports NTFS Directory Junctions."""
        return self.filesystem == FilesystemType.NTFS

    @property
    def supports_compression(self) -> bool:
        """True if volume supports transparent filesystem-level file compression."""
        return self.filesystem == FilesystemType.NTFS

    @property
    def supports_trim(self) -> bool:
        """True if drive hardware is Fixed Internal or volume is NTFS."""
        return self.hardware_type == DriveType.FIXED_INTERNAL or self.filesystem == FilesystemType.NTFS

    @property
    def anti_symlink_required(self) -> bool:
        """True if volume requires strict anti-symlink enforcement (exFAT)."""
        return self.filesystem == FilesystemType.EXFAT

    @property
    def is_slack_sensitive(self) -> bool:
        """True if volume has large cluster size (>= 64KB)."""
        return self.filesystem == FilesystemType.EXFAT or self.cluster_size_bytes >= 65536

    @property
    def used_bytes(self) -> int:
        """Bytes used on disk."""
        return max(0, self.total_bytes - self.free_bytes)

    @property
    def free_percent(self) -> float:
        """Percentage of free space [0.0, 100.0]."""
        if self.total_bytes <= 0:
            return 0.0
        return (self.free_bytes / self.total_bytes) * 100.0

    @property
    def free_ratio(self) -> float:
        """Ratio of free space [0.0, 1.0]."""
        if self.total_bytes <= 0:
            return 0.0
        return self.free_bytes / self.total_bytes

    @property
    def free_gb(self) -> float:
        """Free space in gigabytes."""
        return self.free_bytes / (1024 ** 3)

    @property
    def total_gb(self) -> float:
        """Total space in gigabytes."""
        return self.total_bytes / (1024 ** 3)

    @property
    def used_gb(self) -> float:
        """Used space in gigabytes."""
        return self.used_bytes / (1024 ** 3)

    @property
    def adapter(self) -> FilesystemAdapter:
        """Returns the FilesystemAdapter associated with this drive's configuration."""
        return get_filesystem_adapter(self.filesystem, self.cluster_size_bytes)

    def calculate_allocated_bytes(self, nominal_size: int) -> int:
        """Calculate physical bytes allocated for a file on this volume."""
        return self.adapter.calculate_allocated_bytes(nominal_size)

    def calculate_slack_bytes(self, nominal_size: int) -> int:
        """Calculate wasted slack bytes for a file on this volume."""
        return self.adapter.calculate_slack_bytes(nominal_size)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes DriveInfo to dictionary representation."""
        return {
            "drive_letter": self.drive_letter,
            "mount_point": self.mount_point,
            "is_system_drive": self.is_system_drive,
            "hardware_type": self.hardware_type.value,
            "filesystem": self.filesystem.value,
            "cluster_size_bytes": self.cluster_size_bytes,
            "total_bytes": self.total_bytes,
            "free_bytes": self.free_bytes,
            "used_bytes": self.used_bytes,
            "free_percent": round(self.free_percent, 2),
            "volume_label": self.volume_label,
            "bus_type": self.bus_type,
            "vendor_model": self.vendor_model,
            "serial_number": self.serial_number,
            "supports_junctions": self.supports_junctions,
            "supports_compression": self.supports_compression,
            "supports_trim": self.supports_trim,
            "anti_symlink_required": self.anti_symlink_required,
        }


# ==============================================================================
# 4. BACKEND ABSTRACTION (WIN32 REAL & IN-MEMORY MOCK)
# ==============================================================================

class DriveDetectorBackend:
    """Abstract base backend interface for drive detection."""

    def get_system_directory(self) -> str:
        raise NotImplementedError

    def list_drive_letters(self) -> List[str]:
        raise NotImplementedError

    def get_volume_info(self, mount_point: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_disk_space(self, mount_point: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_storage_hardware(self, drive_letter: str) -> Dict[str, Any]:
        raise NotImplementedError

    def query_trim_status(self) -> Dict[str, Any]:
        raise NotImplementedError


class Win32DriveBackend(DriveDetectorBackend):
    """Authoritative Windows implementation using Win32 API and IOCTL."""

    def get_system_directory(self) -> str:
        if sys.platform == "win32":
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.kernel32.GetSystemDirectoryW(buf, 260):
                return buf.value
            if ctypes.windll.kernel32.GetWindowsDirectoryW(buf, 260):
                return buf.value
        env_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
        if env_root:
            return env_root
        sys_drive = os.environ.get("SystemDrive", "C:")
        return f"{sys_drive}\\Windows"

    def list_drive_letters(self) -> List[str]:
        if sys.platform != "win32":
            return ["C:"]

        mask = ctypes.windll.kernel32.GetLogicalDrives()
        letters = []
        for i in range(26):
            if mask & (1 << i):
                letters.append(f"{chr(65 + i)}:")
        return letters

    def get_volume_info(self, mount_point: str) -> Dict[str, Any]:
        if sys.platform != "win32":
            return {
                "volume_label": "",
                "serial_number": 0,
                "max_component_length": 255,
                "filesystem_flags": 0,
                "filesystem_name": "NTFS",
            }

        kernel32 = ctypes.windll.kernel32
        vname = ctypes.create_unicode_buffer(261)
        fsname = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_ulong(0)
        maxlen = ctypes.c_ulong(0)
        flags = ctypes.c_ulong(0)

        ok = kernel32.GetVolumeInformationW(
            mount_point,
            vname,
            ctypes.sizeof(vname) // 2,
            ctypes.byref(serial),
            ctypes.byref(maxlen),
            ctypes.byref(flags),
            fsname,
            ctypes.sizeof(fsname) // 2
        )
        if not ok:
            err = kernel32.GetLastError()
            raise OSError(f"GetVolumeInformationW failed for '{mount_point}' with error code {err}")

        return {
            "volume_label": vname.value,
            "serial_number": serial.value,
            "max_component_length": maxlen.value,
            "filesystem_flags": flags.value,
            "filesystem_name": fsname.value,
        }

    def get_disk_space(self, mount_point: str) -> Dict[str, Any]:
        spc = 8
        bps = 512
        total_bytes = 0
        free_bytes = 0

        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            c_spc = ctypes.c_ulong(0)
            c_bps = ctypes.c_ulong(0)
            c_free_clusters = ctypes.c_ulong(0)
            c_total_clusters = ctypes.c_ulong(0)

            if kernel32.GetDiskFreeSpaceW(
                mount_point,
                ctypes.byref(c_spc),
                ctypes.byref(c_bps),
                ctypes.byref(c_free_clusters),
                ctypes.byref(c_total_clusters)
            ):
                spc = c_spc.value
                bps = c_bps.value

        try:
            usage = shutil.disk_usage(mount_point)
            total_bytes = usage.total
            free_bytes = usage.free
        except Exception:
            pass

        return {
            "sectors_per_cluster": spc,
            "bytes_per_sector": bps,
            "cluster_size_bytes": spc * bps,
            "total_bytes": total_bytes,
            "free_bytes": free_bytes,
        }

    def get_storage_hardware(self, drive_letter: str) -> Dict[str, Any]:
        letter = drive_letter.upper().rstrip(":\\")
        if sys.platform != "win32":
            return {
                "hardware_type": DriveType.FIXED_INTERNAL,
                "bus_type": "NVMe",
                "vendor_model": "Standard Internal Storage",
                "serial_number": "",
                "removable_media": False,
            }

        kernel32 = ctypes.windll.kernel32
        volume_path = f"\\\\.\\{letter}:"

        # Step 1: Query IOCTL_STORAGE_QUERY_PROPERTY via unprivileged volume handle
        handle = kernel32.CreateFileW(
            volume_path,
            0,  # Unprivileged access: no GENERIC_READ / GENERIC_WRITE needed
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None
        )

        if handle != INVALID_HANDLE_VALUE and handle != 0xFFFFFFFFFFFFFFFF:
            try:
                # STORAGE_PROPERTY_QUERY (PropertyId=0, QueryType=0)
                query = (ctypes.c_int * 3)(0, 0, 0)
                buf = ctypes.create_string_buffer(2048)
                returned = ctypes.c_ulong(0)

                ok = kernel32.DeviceIoControl(
                    handle,
                    IOCTL_STORAGE_QUERY_PROPERTY,
                    ctypes.byref(query),
                    ctypes.sizeof(query),
                    buf,
                    ctypes.sizeof(buf),
                    ctypes.byref(returned),
                    None
                )
                if ok and returned.value >= 32:
                    raw = buf.raw
                    (
                        version, size, devtype, devmod, removable, queue,
                        v_off, p_off, r_off, s_off, bus_type_id
                    ) = struct.unpack_from('<IIBBBBIIIII', raw, 0)

                    def read_null_str(offset: int) -> str:
                        if offset <= 0 or offset >= len(raw):
                            return ""
                        end = raw.find(b'\x00', offset)
                        if end == -1:
                            end = len(raw)
                        return raw[offset:end].decode('ascii', errors='ignore').strip()

                    vendor = read_null_str(v_off)
                    product = read_null_str(p_off)
                    serial = read_null_str(s_off)
                    model = f"{vendor} {product}".strip()

                    bus_name, mapped_type = STORAGE_BUS_TYPE_MAP.get(bus_type_id, ("Unknown", DriveType.UNKNOWN))

                    # Authoritative classification logic:
                    if removable == 1:
                        hardware_type = DriveType.REMOVABLE_EXTERNAL
                    elif bus_type_id in (7, 12, 13, 4):  # USB, SD, MMC, 1394
                        hardware_type = DriveType.REMOVABLE_EXTERNAL
                    elif bus_type_id in (17, 11, 3, 8, 10, 16, 18):  # NVMe, SATA, ATA, RAID, SAS, Spaces, SCM
                        hardware_type = DriveType.FIXED_INTERNAL
                    else:
                        hardware_type = mapped_type

                    return {
                        "hardware_type": hardware_type,
                        "bus_type": bus_name,
                        "vendor_model": model,
                        "serial_number": serial,
                        "removable_media": bool(removable),
                    }
            finally:
                kernel32.CloseHandle(handle)

        # Step 2: Fallback to PowerShell Get-Partition / Get-Disk
        ps_info = self._query_powershell_disk(letter)
        if ps_info:
            return ps_info

        # Step 3: Fallback to GetDriveTypeW
        root_path = f"{letter}:\\"
        drive_type_val = kernel32.GetDriveTypeW(root_path)
        if drive_type_val == DRIVE_REMOVABLE:
            return {
                "hardware_type": DriveType.REMOVABLE_EXTERNAL,
                "bus_type": "Removable",
                "vendor_model": "",
                "serial_number": "",
                "removable_media": True,
            }
        elif drive_type_val == DRIVE_FIXED:
            return {
                "hardware_type": DriveType.FIXED_INTERNAL,
                "bus_type": "Fixed",
                "vendor_model": "",
                "serial_number": "",
                "removable_media": False,
            }

        return {
            "hardware_type": DriveType.UNKNOWN,
            "bus_type": "Unknown",
            "vendor_model": "",
            "serial_number": "",
            "removable_media": False,
        }

    def _query_powershell_disk(self, drive_letter: str) -> Optional[Dict[str, Any]]:
        cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Get-Partition -DriveLetter '{drive_letter}' | Get-Disk | Select-Object BusType, FriendlyName, SerialNumber | ConvertTo-Json"
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                if isinstance(data, list) and len(data) > 0:
                    data = data[0]
                bus = str(data.get("BusType", "")).strip()
                name = str(data.get("FriendlyName", "")).strip()
                serial = str(data.get("SerialNumber", "")).strip()

                bus_upper = bus.upper()
                if any(x in bus_upper for x in ["NVME", "SATA", "RAID", "SAS", "SCSI", "ATA"]):
                    hw_type = DriveType.FIXED_INTERNAL
                elif any(x in bus_upper for x in ["USB", "SD", "MMC"]):
                    hw_type = DriveType.REMOVABLE_EXTERNAL
                else:
                    hw_type = DriveType.UNKNOWN

                return {
                    "hardware_type": hw_type,
                    "bus_type": bus or "Unknown",
                    "vendor_model": name,
                    "serial_number": serial,
                    "removable_media": hw_type == DriveType.REMOVABLE_EXTERNAL,
                }
        except Exception:
            pass
        return None

    def query_trim_status(self) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                ["fsutil", "behavior", "query", "DisableDeleteNotify"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=0x08000000 if sys.platform == "win32" else 0
            )
            if res.returncode == 0:
                raw = res.stdout.strip()
                out_lower = raw.lower()
                ntfs_enabled = "ntfs disabledeletenotify = 0" in out_lower
                return {
                    "supported": True,
                    "enabled": ntfs_enabled,
                    "message": "TRIM is enabled" if ntfs_enabled else "TRIM is disabled (DisableDeleteNotify = 1)",
                    "raw": raw,
                }
        except Exception as exc:
            return {"supported": False, "enabled": False, "message": str(exc), "raw": ""}
        return {"supported": False, "enabled": False, "message": "Unknown status", "raw": ""}


class MockDriveBackend(DriveDetectorBackend):
    """In-memory mock backend for offline, deterministic testing across platforms."""

    def __init__(self, system_drive: str = "C:", drives: Optional[Dict[str, DriveInfo]] = None) -> None:
        self.system_drive: str = system_drive.upper().rstrip(":\\") + ":"
        self.drives: Dict[str, DriveInfo] = {}
        self.trim_status: Dict[str, Any] = {
            "supported": True,
            "enabled": True,
            "message": "TRIM is enabled (Mock)",
            "raw": "NTFS DisableDeleteNotify = 0",
        }
        if drives:
            for letter, d_info in drives.items():
                self.register_drive(d_info)

    def register_drive(self, drive_info: DriveInfo) -> None:
        key = drive_info.drive_letter.upper().rstrip(":\\") + ":"
        self.drives[key] = drive_info

    def unregister_drive(self, drive_letter: str) -> None:
        key = drive_letter.upper().rstrip(":\\") + ":"
        self.drives.pop(key, None)

    def set_system_drive(self, letter: str) -> None:
        self.system_drive = letter.upper().rstrip(":\\") + ":"
        for k, d in self.drives.items():
            if k == self.system_drive:
                d.is_system_drive = True
            else:
                d.is_system_drive = False

    def get_system_directory(self) -> str:
        return f"{self.system_drive}\\Windows\\System32"

    def list_drive_letters(self) -> List[str]:
        return sorted(list(self.drives.keys()))

    def get_volume_info(self, mount_point: str) -> Dict[str, Any]:
        key = normalize_drive_letter(mount_point)
        if key not in self.drives:
            raise FileNotFoundError(f"Mock drive not found: {mount_point}")
        d = self.drives[key]
        return {
            "volume_label": d.volume_label,
            "serial_number": 0x12345678,
            "max_component_length": 255,
            "filesystem_flags": 0x3e72eff if d.filesystem == FilesystemType.NTFS else 0x20206,
            "filesystem_name": d.filesystem.value,
        }

    def get_disk_space(self, mount_point: str) -> Dict[str, Any]:
        key = normalize_drive_letter(mount_point)
        if key not in self.drives:
            raise FileNotFoundError(f"Mock drive not found: {mount_point}")
        d = self.drives[key]
        return {
            "sectors_per_cluster": d.sectors_per_cluster,
            "bytes_per_sector": d.bytes_per_sector,
            "cluster_size_bytes": d.cluster_size_bytes,
            "total_bytes": d.total_bytes,
            "free_bytes": d.free_bytes,
        }

    def get_storage_hardware(self, drive_letter: str) -> Dict[str, Any]:
        key = normalize_drive_letter(drive_letter)
        if key not in self.drives:
            raise FileNotFoundError(f"Mock drive not found: {drive_letter}")
        d = self.drives[key]
        return {
            "hardware_type": d.hardware_type,
            "bus_type": d.bus_type,
            "vendor_model": d.vendor_model,
            "serial_number": d.serial_number,
            "removable_media": d.hardware_type == DriveType.REMOVABLE_EXTERNAL,
        }

    def query_trim_status(self) -> Dict[str, Any]:
        return self.trim_status

    @classmethod
    def create_standard_mock(cls) -> "MockDriveBackend":
        """Builds a rich standard topology simulating C: (NVMe OS), D: (USB exFAT SSD), E: (NVMe NTFS SSD), F: (SATA NTFS SSD)."""
        backend = cls(system_drive="C:")
        # C: System Drive (NVMe SSD, NTFS, 4KB clusters)
        backend.register_drive(
            DriveInfo(
                drive_letter="C:",
                mount_point="C:\\",
                is_system_drive=True,
                hardware_type=DriveType.FIXED_INTERNAL,
                filesystem=FilesystemType.NTFS,
                cluster_size_bytes=4096,
                total_bytes=500 * (1024 ** 3),
                free_bytes=80 * (1024 ** 3),
                volume_label="Windows",
                bus_type="NVMe",
                vendor_model="WD Blue SN580 500GB",
                serial_number="E823_8FA6_BF53_0001",
                sectors_per_cluster=8,
                bytes_per_sector=512,
            )
        )
        # D: Secondary Drive (External USB SSD Kingston XS2000, exFAT, 512KB clusters)
        backend.register_drive(
            DriveInfo(
                drive_letter="D:",
                mount_point="D:\\",
                is_system_drive=False,
                hardware_type=DriveType.REMOVABLE_EXTERNAL,
                filesystem=FilesystemType.EXFAT,
                cluster_size_bytes=524288,
                total_bytes=2000 * (1024 ** 3),
                free_bytes=1400 * (1024 ** 3),
                volume_label="KINGSTON",
                bus_type="USB",
                vendor_model="Kingston XS2000",
                serial_number="50026B749D196CDE",
                sectors_per_cluster=1024,
                bytes_per_sector=512,
            )
        )
        # E: Secondary Drive (Internal NVMe SSD Crucial P3, NTFS, 4KB clusters)
        backend.register_drive(
            DriveInfo(
                drive_letter="E:",
                mount_point="E:\\",
                is_system_drive=False,
                hardware_type=DriveType.FIXED_INTERNAL,
                filesystem=FilesystemType.NTFS,
                cluster_size_bytes=4096,
                total_bytes=1000 * (1024 ** 3),
                free_bytes=600 * (1024 ** 3),
                volume_label="Dev_Vault",
                bus_type="NVMe",
                vendor_model="CT1000P3PSSD8",
                serial_number="0000_0000_0000_0001",
                sectors_per_cluster=8,
                bytes_per_sector=512,
            )
        )
        # F: Secondary Drive (Internal SATA SSD Samsung 870, NTFS, 4KB clusters, low free space)
        backend.register_drive(
            DriveInfo(
                drive_letter="F:",
                mount_point="F:\\",
                is_system_drive=False,
                hardware_type=DriveType.FIXED_INTERNAL,
                filesystem=FilesystemType.NTFS,
                cluster_size_bytes=4096,
                total_bytes=500 * (1024 ** 3),
                free_bytes=10 * (1024 ** 3),  # 2% free space: critically low
                volume_label="Cold_Archive",
                bus_type="SATA",
                vendor_model="Samsung SSD 870 EVO 500GB",
                serial_number="S5YBNF0M123456",
                sectors_per_cluster=8,
                bytes_per_sector=512,
            )
        )
        return backend


# Active Backend State Management
_DEFAULT_BACKEND: DriveDetectorBackend = Win32DriveBackend()
_ACTIVE_BACKEND: DriveDetectorBackend = _DEFAULT_BACKEND


def get_backend() -> DriveDetectorBackend:
    """Returns currently active DriveDetectorBackend."""
    global _ACTIVE_BACKEND
    return _ACTIVE_BACKEND


def set_backend(backend: DriveDetectorBackend) -> None:
    """Sets the active DriveDetectorBackend."""
    global _ACTIVE_BACKEND
    _ACTIVE_BACKEND = backend


def reset_backend() -> None:
    """Resets the active DriveDetectorBackend to default (Win32DriveBackend)."""
    global _ACTIVE_BACKEND
    _ACTIVE_BACKEND = _DEFAULT_BACKEND


@contextmanager
def use_mock_backend(backend: Optional[MockDriveBackend] = None) -> Generator[MockDriveBackend, None, None]:
    """Context manager temporarily swapping the active backend for unit testing."""
    if backend is None:
        backend = MockDriveBackend.create_standard_mock()
    previous = get_backend()
    try:
        set_backend(backend)
        yield backend
    finally:
        set_backend(previous)


# ==============================================================================
# 5. PUBLIC API FUNCTIONS (PROJECT.md § Interface Contracts)
# ==============================================================================

def normalize_drive_letter(drive_spec: Union[str, Path]) -> str:
    """Normalizes any drive specifier (e.g. 'D', 'd:', 'D:\\', 'D:/path', Path('D:/')) to 'D:'."""
    if isinstance(drive_spec, Path):
        drive_spec = str(drive_spec)

    s = drive_spec.strip()
    if not s:
        raise ValueError("Drive specification cannot be empty.")

    # Strip \\.\ device namespace prefix if present
    if s.startswith(("\\\\.\\", "//./", "\\\\?\\", "//?/")):
        s = s[4:]

    # Handle single character letter e.g. "D" or "d"
    if len(s) == 1 and s.isalpha():
        return f"{s.upper()}:"

    # Match leading drive letter and colon e.g. "D:", "D:\...", "D:/..."
    match = re.match(r"^([a-zA-Z]):", s)
    if match:
        return f"{match.group(1).upper()}:"

    # Try splitdrive
    drive_part, _ = os.path.splitdrive(s)
    if drive_part and len(drive_part) >= 2 and drive_part[0].isalpha() and drive_part[1] == ":":
        return f"{drive_part[0].upper()}:"

    raise ValueError(f"Invalid drive specifier: '{drive_spec}'. Must contain a valid drive letter (A-Z).")


def normalize_mount_point(drive_spec: Union[str, Path]) -> str:
    """Normalizes any drive specifier to root mount point with trailing backslash (e.g. 'D:\\')."""
    letter = normalize_drive_letter(drive_spec)
    return f"{letter}\\"


def get_system_drive_letter() -> str:
    """Returns uppercase system drive letter with colon, e.g. 'C:'.

    Determined authoritatively via backend GetSystemDirectoryW,
    with secondary fallback to Windows environment variables.
    """
    backend = get_backend()
    try:
        sys_dir = backend.get_system_directory()
        drive_part, _ = os.path.splitdrive(sys_dir)
        if drive_part:
            return normalize_drive_letter(drive_part)
    except Exception:
        pass

    env = os.environ.get("SystemDrive") or os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if env:
        drive_part, _ = os.path.splitdrive(env)
        if drive_part:
            return normalize_drive_letter(drive_part)

    return "C:"


def is_system_drive(drive_spec: Union[str, Path]) -> bool:
    """Returns True if the specified drive matches the Windows OS system drive."""
    spec_letter = normalize_drive_letter(drive_spec)
    sys_letter = get_system_drive_letter()
    return spec_letter.upper() == sys_letter.upper()


def list_drive_letters() -> List[str]:
    """Returns sorted list of all available logical drive letters (e.g. ['C:', 'D:', 'E:'])."""
    return get_backend().list_drive_letters()


def list_secondary_drive_letters() -> List[str]:
    """Returns list of all available drive letters strictly excluding C: and the OS system drive."""
    sys_drive = get_system_drive_letter().upper()
    all_drives = list_drive_letters()
    results: List[str] = []
    for d in all_drives:
        try:
            norm = normalize_drive_letter(d)
        except ValueError:
            continue
        if norm.upper() != "C:" and norm.upper() != sys_drive:
            if norm not in results:
                results.append(norm)
    return sorted(results)


def inspect_drive(drive_spec: Union[str, Path]) -> DriveInfo:
    """Inspects a specific drive and returns its authoritative DriveInfo descriptor.

    Args:
        drive_spec: Drive letter, mount point, or path (e.g. 'D:', 'D:\\', 'D:/Data').

    Returns:
        DriveInfo containing geometry, filesystem type, hardware bus, and volume status.

    Raises:
        ValueError: If drive_spec is malformed.
        FileNotFoundError: If drive is not mounted or accessible.
    """
    drive_letter = normalize_drive_letter(drive_spec)
    mount_point = f"{drive_letter}\\"
    backend = get_backend()

    # Verify drive exists in available drives
    available = backend.list_drive_letters()
    if drive_letter not in available and drive_letter.upper() not in [d.upper() for d in available]:
        raise FileNotFoundError(f"Drive '{drive_letter}' not found or is not mounted. Available: {available}")

    is_sys = (drive_letter.upper() == get_system_drive_letter().upper())

    # Retrieve volume info
    vol_info = backend.get_volume_info(mount_point)
    fs_name_raw = vol_info.get("filesystem_name", "")
    vol_label = vol_info.get("volume_label", "")

    fs_adapter = get_filesystem_adapter(fs_name_raw)
    filesystem_type = fs_adapter.filesystem

    # Retrieve cluster geometry and free space
    space_info = backend.get_disk_space(mount_point)
    cluster_size = space_info.get("cluster_size_bytes", 4096)
    if cluster_size <= 0:
        cluster_size = 4096 if filesystem_type == FilesystemType.NTFS else 524288

    total_bytes = space_info.get("total_bytes", 0)
    free_bytes = space_info.get("free_bytes", 0)
    spc = space_info.get("sectors_per_cluster", 8)
    bps = space_info.get("bytes_per_sector", 512)

    # Retrieve hardware bus and drive type
    hw_info = backend.get_storage_hardware(drive_letter)
    hardware_type = hw_info.get("hardware_type", DriveType.UNKNOWN)
    bus_type = hw_info.get("bus_type", "unknown")
    vendor_model = hw_info.get("vendor_model", "")
    serial_number = hw_info.get("serial_number", "")

    return DriveInfo(
        drive_letter=drive_letter,
        mount_point=mount_point,
        is_system_drive=is_sys,
        hardware_type=hardware_type,
        filesystem=filesystem_type,
        cluster_size_bytes=cluster_size,
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        volume_label=vol_label,
        bus_type=bus_type,
        vendor_model=vendor_model,
        serial_number=serial_number,
        sectors_per_cluster=spc,
        bytes_per_sector=bps,
    )


def list_secondary_drives() -> List[DriveInfo]:
    """Returns all available drives strictly excluding C: and the Windows system drive.

    Drives that encounter access errors (e.g. empty optical drive) are safely skipped.
    """
    secondary_letters = list_secondary_drive_letters()
    results: List[DriveInfo] = []

    for letter in secondary_letters:
        try:
            info = inspect_drive(letter)
            # Extra safety check: strictly exclude system drive AND C:
            if not info.is_system_drive and info.drive_letter.upper() != "C:":
                results.append(info)
        except (OSError, FileNotFoundError):
            # Gracefully ignore drives that are offline, unready, or inaccessible
            continue

    return results


def verify_trim_support(drive_letter: Optional[str] = None) -> Dict[str, Any]:
    """Queries Windows TRIM status using fsutil behavior query DisableDeleteNotify."""
    return get_backend().query_trim_status()
