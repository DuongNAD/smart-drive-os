"""smart_drive.core.fsinfo - Which filesystem, and which allocation unit, holds a path.

SmartDrive-OS models cluster slack with one fixed 512 KB exFAT cluster (CLUSTER_SIZE_BYTES). That is
right for the exFAT SSD the tool was built for and only a what-if number anywhere else, so its
reports need to know what the volume really is instead of claiming exFAT.

100% Python Standard Library: ``os.statvfs`` plus ``/proc/self/mounts`` on Linux, the ``mount``
command on macOS, and ctypes (kernel32) on Windows. Nothing here ever raises: a volume that cannot
be identified is reported as "unknown".
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Display names for the lower-cased names the operating systems report.
_DISPLAY_NAMES: Dict[str, str] = {
    "exfat": "exFAT", "ntfs": "NTFS", "refs": "ReFS", "apfs": "APFS", "hfs": "HFS+", "hfsplus": "HFS+",
    "msdos": "FAT32", "vfat": "FAT32", "fat32": "FAT32", "fat": "FAT",
    "ext2": "ext2", "ext3": "ext3", "ext4": "ext4", "xfs": "XFS", "btrfs": "Btrfs", "f2fs": "F2FS", "zfs": "ZFS",
    "tmpfs": "tmpfs", "overlay": "overlay", "nfs": "NFS", "smbfs": "SMB", "cifs": "SMB",
    "fuseblk": "FUSE", "fuse": "FUSE",
}

# `mount` on macOS: "/dev/disk4s1 on /Volumes/My Drive (2) (exfat, local, nodev)"
_MOUNT_LINE = re.compile(r"^.+? on (?P<mp>/.*) \((?P<fs>[^,()]+)[,)]")
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")

_MOUNT_TABLE_TTL_SECONDS = 30.0
_mount_cache: Tuple[float, List[Tuple[str, str]]] = (0.0, [])


@dataclass(frozen=True)
class FilesystemInfo:
    """The volume that holds a path, as reported by the operating system."""

    path: str
    fs_type: str       # lower-case key ("exfat", "apfs", "ntfs", "ext4" ...) or "unknown"
    block_size: int    # allocation unit in bytes (0 when it could not be read)
    mount_point: str   # "" when unknown
    source: str        # how it was found: "win32", "proc-mounts", "mount" or "unknown"

    @property
    def known(self) -> bool:
        return self.fs_type != "unknown"

    @property
    def is_exfat(self) -> bool:
        return self.fs_type == "exfat"

    @property
    def display_name(self) -> str:
        return _DISPLAY_NAMES.get(self.fs_type, self.fs_type if self.known else "unknown")

    def summary(self) -> str:
        """'APFS (4 KB allocation unit)', 'exFAT (512 KB allocation unit)' or 'unknown'."""
        if not self.known:
            return "unknown"
        if self.block_size >= 1024 and self.block_size % 1024 == 0:
            return f"{self.display_name} ({self.block_size // 1024} KB allocation unit)"
        if self.block_size > 0:
            return f"{self.display_name} ({self.block_size} B allocation unit)"
        return self.display_name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.fs_type,
            "display_name": self.display_name,
            "allocation_unit_bytes": self.block_size,
            "summary": self.summary(),
            "mount_point": self.mount_point,
            "detected_via": self.source,
        }


# ------------------------------------------------------------------------------
# Pure parsers (testable on every platform)
# ------------------------------------------------------------------------------

def normalize_fs_name(raw: str) -> str:
    """Lower-cases a filesystem name and drops vendor prefixes ('ufsd_NTFS' -> 'ntfs')."""
    name = (raw or "").strip().lower()
    for prefix in ("ufsd_", "tuxera_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name or "unknown"


def parse_mount_output(text: str) -> List[Tuple[str, str]]:
    """Parses `mount` output (macOS/BSD) into (mount_point, fs_type) pairs."""
    mounts: List[Tuple[str, str]] = []
    for line in text.splitlines():
        match = _MOUNT_LINE.match(line.strip())
        if match:
            mounts.append((match.group("mp"), normalize_fs_name(match.group("fs"))))
    return mounts


def parse_proc_mounts(text: str) -> List[Tuple[str, str]]:
    """Parses /proc/self/mounts (Linux) into (mount_point, fs_type) pairs."""
    mounts: List[Tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            mount_point = _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), parts[1])
            mounts.append((mount_point, normalize_fs_name(parts[2])))
    return mounts


def best_mount(real_path: str, mounts: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """The mount whose mount point is the longest prefix of ``real_path``."""
    best: Optional[Tuple[str, str]] = None
    for mount_point, fs_type in mounts:
        base = mount_point.rstrip("/")
        if mount_point == "/" or real_path == mount_point or real_path.startswith(base + "/"):
            if best is None or len(mount_point) > len(best[0]):
                best = (mount_point, fs_type)
    return best


# ------------------------------------------------------------------------------
# Detection
# ------------------------------------------------------------------------------

def _read_mount_table() -> List[Tuple[str, str]]:
    """The system's mount table, cached briefly (one `mount` call serves a whole command)."""
    global _mount_cache
    cached_at, cached = _mount_cache
    if cached and time.monotonic() - cached_at < _MOUNT_TABLE_TTL_SECONDS:
        return cached
    table: List[Tuple[str, str]] = []
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/mounts", encoding="utf-8", errors="replace") as handle:
                table = parse_proc_mounts(handle.read())
        else:
            executable = "/sbin/mount" if os.path.exists("/sbin/mount") else "mount"
            out = subprocess.run([executable], capture_output=True, text=True, timeout=5, check=False).stdout
            table = parse_mount_output(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        table = []
    _mount_cache = (time.monotonic(), table)
    return table


def _posix_block_size(path: str) -> int:
    try:
        stats = os.statvfs(path)
    except (OSError, AttributeError):
        return 0
    return int(stats.f_frsize or stats.f_bsize or 0)


def _detect_windows(path: str) -> Tuple[str, str, int]:
    """(fs_type, volume_root, cluster_bytes) through kernel32; ('', '', 0) when it cannot tell."""
    try:
        import ctypes
        from ctypes import wintypes

        drive, _tail = os.path.splitdrive(path)
        if not drive:
            return "", "", 0
        root = drive + "\\"
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        fs_buffer = ctypes.create_unicode_buffer(64)
        ok = kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), None, 0, None, None, None, fs_buffer, len(fs_buffer))
        fs_type = normalize_fs_name(fs_buffer.value) if ok else ""
        sectors, bytes_per_sector, free_clusters, total_clusters = (wintypes.DWORD() for _ in range(4))
        ok = kernel32.GetDiskFreeSpaceW(
            ctypes.c_wchar_p(root),
            ctypes.byref(sectors),
            ctypes.byref(bytes_per_sector),
            ctypes.byref(free_clusters),
            ctypes.byref(total_clusters),
        )
        cluster = int(sectors.value) * int(bytes_per_sector.value) if ok else 0
        return ("" if fs_type == "unknown" else fs_type), root, cluster
    except Exception:  # noqa: BLE001 - detection must never break a command
        return "", "", 0


def detect_filesystem(path: "os.PathLike[str] | str") -> FilesystemInfo:
    """Identifies the filesystem and allocation unit of the volume that holds ``path``."""
    target = os.fspath(path)
    try:
        real = os.path.realpath(target)
    except (OSError, ValueError):
        real = target

    if sys.platform == "win32":
        fs_type, mount_point, block = _detect_windows(real)
        return FilesystemInfo(target, fs_type or "unknown", block, mount_point, "win32" if fs_type else "unknown")

    block = _posix_block_size(real)
    chosen = best_mount(real, _read_mount_table())
    if chosen is None:
        return FilesystemInfo(target, "unknown", block, "", "unknown")
    mount_point, fs_type = chosen
    source = "proc-mounts" if sys.platform.startswith("linux") else "mount"
    return FilesystemInfo(target, fs_type, block, mount_point, source)


def slack_model_note(info: FilesystemInfo, model_cluster_size: int) -> Optional[str]:
    """Explains when the modelled cluster size is not this volume's real one; None when it is."""
    model = f"{model_cluster_size // 1024} KB"
    if not info.known:
        return f"Could not detect the filesystem; cluster-slack figures model {model} clusters."
    if info.is_exfat and info.block_size in (0, model_cluster_size):
        return None
    if info.is_exfat:
        return f"This exFAT volume uses a different cluster size ({info.summary()}); cluster-slack figures model {model} clusters."
    return (
        f"This volume is {info.summary()}; cluster-slack figures model {model} exFAT clusters, "
        "so treat them as what-if numbers."
    )


__all__ = [
    "FilesystemInfo",
    "best_mount",
    "detect_filesystem",
    "normalize_fs_name",
    "parse_mount_output",
    "parse_proc_mounts",
    "slack_model_note",
]
