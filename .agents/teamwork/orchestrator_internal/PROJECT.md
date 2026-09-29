# Project: SmartDrive-OS — Internal Drive Architect & C-Drive Cache Offloader

## Architecture Overview
Modular, zero-dependency extension to SmartDrive-OS for high-performance internal secondary drives (D:, E:, etc.), NTFS deep optimization, zero-data-loss C-drive cache offloading via NTFS Directory Junctions (`mklink /J`), internal workstation 6-partition profile initialization, and SSD TRIM/SMART health monitoring.

```
                    ┌────────────────────────────┐
                    │  smart_drive.cli.main      │
                    │  (argparse subcommands)    │
                    └─────────────┬──────────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         │                        │                        │
         ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  cmd_init        │    │  cmd_offload     │    │  cmd_health      │
│  (--profile ...) │    │  (--scan/--move) │    │  (TRIM/SMART)    │
└────────┬─────────┘    └────────┬─────────┘    └────────┬─────────┘
         │                        │                        │
         ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ core.initializer │    │ core.offloader   │    │ core.health      │
│ core.config      │    │ core.junction    │    │ core.drive_detect│
└──────────────────┘    └──────────────────┘    └──────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Secondary Drive Enumeration & Auto-Exclusion | Auto-detect secondary drive letters (D:, E:, etc.), strictly exclude system drive (C:) | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Hardware Drive Type Detection | Distinguish Fixed Internal NVMe/SATA SSD vs Removable External USB SSD via Win32 unprivileged storage query APIs | M1 | ORIGINAL_REQUEST §R1 |
| 3 | Filesystem Adaptation (NTFS vs exFAT) | Detect volume filesystem; activate NTFS features (4KB cluster allocation, compression, selective indexing) or exFAT guards (slack warning, anti-symlink) | M1 | ORIGINAL_REQUEST §R1 |
| 4 | C-Drive Cache Discovery Engine | Scan known caches (HuggingFace, Ollama, PyTorch, pip, npm, uv, Conda/Mamba, Gradle, Docker WSL2) and calculate disk sizes | M2 | ORIGINAL_REQUEST §R2 |
| 5 | NTFS Directory Junction Engine | Create and inspect NTFS directory junctions (`mklink /J`) without admin elevation, verify reparse points, safe unlinking | M2 | ORIGINAL_REQUEST §R2 |
| 6 | Transactional Cache Offloader & Revert | 7-phase safe move from C: to `<drive>:\04_System_Offload_Caches\<name>` with rollback on error; revert junction back to original | M2 | ORIGINAL_REQUEST §R2 |
| 7 | Internal Developer Vault Profile | 6-partition structure (`01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches`, `05_Dev_Toolbox`, `06_Archives_Storage`) & partition protection in `config.py` | M3 | ORIGINAL_REQUEST §R3 |
| 8 | SSD Health & TRIM Monitor | Query Windows TRIM status (`fsutil behavior query DisableDeleteNotify`), inspect cluster/sector geometry, free space thresholds & warnings | M3 | ORIGINAL_REQUEST §R4 |
| 9 | Git Branching & Remote Delivery | Create branch `internal-secondary-drive`, commit all features, push to `DuongNAD/smart-drive-os` | M4 | ORIGINAL_REQUEST §R5 |
| 10 | Comprehensive Documentation & Navigation | Author `README_INTERNAL.md`, update `README.md` and `README_VN.md`, add prominent cross-branch navigation banner on `main` | M4 | ORIGINAL_REQUEST §R5 |
| 11 | Comprehensive Pure Stdlib Test Suite | 100% test pass on `python -m unittest discover tests` with zero external dependencies | M4 | ORIGINAL_REQUEST §R5 |

## Code Layout
```
smart_drive/
  core/
    config.py               # Add new partition constants to PROTECTED_CORE_TAXONOMIES and PROTECTED_ROOT_DIRS
    initializer.py          # Register 'internal-developer-vault' profile definition
    drive_detector.py       # [NEW] Drive enumeration, exclusion of system drive, hardware/bus type, filesystem adapter
    junction.py             # [NEW] NTFS directory junction operations (create, is_junction, get_target, remove)
    offloader.py            # [NEW] Cache scanner catalog, transactional offload move, revert logic
    health.py               # [NEW] TRIM query, cluster/sector geometry, capacity monitor
  cli/
    main.py                 # Register subparsers for offload, health, update init choices
    cmd_init.py             # Drive letter path normalization (e.g. 'D:' -> 'D:\\')
    cmd_offload.py          # [NEW] CLI handler for 'smart-drive offload' (--scan, --move, --revert)
    cmd_health.py           # [NEW] CLI handler for 'smart-drive health'
tests/
  test_drive_detector.py    # [NEW] Tests for drive detection, exclusion, bus classification, filesystem adaptation
  test_junction.py          # [NEW] Tests for junction detection, creation, parsing, removal
  test_offloader.py         # [NEW] Tests for cache scanning, transactional move, rollback, revert
  test_internal_vault.py    # [NEW] Tests for internal-developer-vault profile & protected taxonomies
  test_health.py            # [NEW] Tests for TRIM query, disk geometry, health warnings
  test_cli_internal_e2e.py  # [NEW] End-to-end CLI tests for offload, health, and init --profile internal-developer-vault
docs/
  README_INTERNAL.md        # [NEW] Dedicated guide for internal secondary drives & cache offloading
README.md                   # Updated on both branches with cross-branch navigation banner
README_VN.md                # Updated on both branches with cross-branch navigation banner
```

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Secondary Drive Detector & Filesystem Adapter | `drive_detector.py`, `test_drive_detector.py` (R1) | none | DONE |
| M2 | Cache Offloader & Junction Engine | `junction.py`, `offloader.py`, `cmd_offload.py`, `test_junction.py`, `test_offloader.py` (R2) | M1 | DONE |
| M3 | Internal Profile & SSD TRIM/Health Monitor | `config.py`, `initializer.py`, `health.py`, `cmd_health.py`, `test_internal_vault.py`, `test_health.py` (R3, R4) | M1 | DONE |
| M4 | Git Branching, Full E2E Test Suite & Documentation | Branch `internal-secondary-drive`, CLI integration in `main.py`, `test_cli_internal_e2e.py`, `README_INTERNAL.md`, cross-branch banners on `main` & push | M1, M2, M3 | DONE |

## Interface Contracts

### 1. `smart_drive.core.drive_detector`
```python
class DriveType(Enum):
    FIXED_INTERNAL = "fixed_internal"
    REMOVABLE_EXTERNAL = "removable_external"
    UNKNOWN = "unknown"

class FilesystemType(Enum):
    NTFS = "NTFS"
    EXFAT = "exFAT"
    FAT32 = "FAT32"
    OTHER = "other"

@dataclass
class DriveInfo:
    drive_letter: str          # e.g. "D:"
    mount_point: str           # e.g. "D:\\"
    is_system_drive: bool      # True for C:
    hardware_type: DriveType   # FIXED_INTERNAL vs REMOVABLE_EXTERNAL
    filesystem: FilesystemType # NTFS vs exFAT
    cluster_size_bytes: int    # e.g. 4096 or 524288
    total_bytes: int
    free_bytes: int
    volume_label: str

def get_system_drive_letter() -> str:
    """Return uppercase system drive letter, e.g. 'C:'."""

def list_secondary_drives() -> list[DriveInfo]:
    """Return all available drives strictly excluding the Windows system drive."""

def inspect_drive(drive_spec: str) -> DriveInfo:
    """Inspect specific drive (e.g. 'D:', 'D:\\', or path on drive)."""
```

### 2. `smart_drive.core.junction`
```python
def is_directory_junction(path: str | Path) -> bool:
    """Return True if path exists and is an NTFS Directory Junction / reparse point."""

def get_junction_target(path: str | Path) -> str | None:
    """Return target path pointed to by junction, stripping Windows \\?\\ prefix."""

def create_directory_junction(junction_path: str | Path, target_path: str | Path) -> bool:
    """Create NTFS directory junction using 'cmd /c mklink /J'."""

def remove_directory_junction(junction_path: str | Path) -> bool:
    """Safely remove the junction link without deleting target directory contents."""
```

### 3. `smart_drive.core.offloader`
```python
@dataclass
class CacheTarget:
    name: str                  # e.g. "huggingface", "ollama", "uv"
    description: str
    source_path: Path
    is_offloaded: bool = False
    current_target: str | None = None
    size_bytes: int = 0

class CacheOffloader:
    def scan_caches() -> list[CacheTarget]:
        """Scan all known AI, dev, and container caches on C:."""

    def offload_cache(name: str, target_drive: str) -> dict:
        """Execute 7-phase safe move to <target_drive>:\\04_System_Offload_Caches\\<name> and link."""

    def revert_cache(name: str) -> dict:
        """Unlink junction and restore cache back to original C: directory."""
```

### 4. `smart_drive.core.health`
```python
@dataclass
class SSDHealthReport:
    drive_letter: str
    filesystem: str
    trim_enabled: bool | None
    trim_status_message: str
    total_bytes: int
    free_bytes: int
    free_percent: float
    cluster_size_bytes: int
    warnings: list[str]

def check_drive_health(drive_letter: str | None = None) -> SSDHealthReport:
    """Check TRIM status, geometry, free space, and output diagnostic warnings."""
```
