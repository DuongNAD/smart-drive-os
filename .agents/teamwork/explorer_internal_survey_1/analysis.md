# Architecture & Codebase Analysis: SmartDrive-OS Internal Secondary Drive Suite

**Explorer**: Explorer 1 (`explorer_internal_survey_1`)  
**Scope**: Codebase Survey, Architecture Mapping, CLI Integration, Profile Systems, Filesystem Adaptability, and Test Conventions.  
**Target Repository**: `smart_drive_os` (`d:\teamwork_projects\smart_drive_os`)  
**Baseline Version**: SmartDrive-OS v1.1.0  
**Target Branch**: `internal-secondary-drive`  

---

## Executive Summary

SmartDrive-OS is an autonomous drive management suite designed with a **100% pure Python Standard Library (Zero-Dependency)** architecture. It currently provides high-speed SQLite FTS5 search (<10ms), exFAT 512KB cluster slack audit and optimization, 3-tier safe junk purge, streaming SHA-256 point-in-time snapshot and backup, deep file format classification, an embedded HTTP visual web dashboard (`smart-drive ui`), and JSON-RPC 2.0 stdio Model Context Protocol (MCP) server support.

This survey establishes the complete technical foundation and architectural blueprint for extending SmartDrive-OS into an **Internal Secondary Drive Architect & C-Drive Cache Offloader**. This suite is dedicated to managing fixed internal secondary drives (NVMe/SATA SSDs on D:, E:, etc.), performing NTFS/exFAT filesystem adaptation, executing safe C-drive cache migrations via NTFS Directory Junctions (`mklink /J`), provisioning a workstation-tailored 6-partition layout (`internal-developer-vault`), and monitoring SSD hardware TRIM and geometry health.

Verification confirms that the current test suite passes **314/314 tests (100% OK)** with zero external dependencies.

---

## 1. Existing Codebase Architecture & Module Layout

### 1.1 Packaging and Entrypoints

- **`pyproject.toml`**:
  - Configures standard PEP 517/621 setuptools packaging.
  - Declares `dependencies = []` (strictly zero external pip dependencies).
  - CLI script entrypoints:
    ```toml
    [project.scripts]
    smart-drive = "smart_drive.cli.main:main"
    smart_drive = "smart_drive.cli.main:main"
    smart-drive-manager = "smart_drive.cli.main:main"
    ```
- **`smart_drive/__main__.py`**:
  - Direct package invocation router: forwards `sys.argv` to `smart_drive.cli.main.main()`.
- **`smart_drive/cli/main.py`**:
  - Primary CLI dispatcher.
  - Configures console encoding to UTF-8 on Windows (`sys.stdout.reconfigure(encoding="utf-8")`).
  - Implements `build_parser() -> argparse.ArgumentParser` with `subparsers = parser.add_subparsers(dest="subcommand")`.
  - Maps subcommands to dedicated handlers via a dispatch dictionary:
    ```python
    dispatch = {
        "init": cmd_init,
        "status": cmd_status,
        "audit": cmd_audit,
        "clean": cmd_clean,
        "search": cmd_search,
        "organize": cmd_organize,
        "sentinel": cmd_sentinel,
        "agent-check": cmd_sentinel,
        "agent_check": cmd_sentinel,
        "mcp": cmd_mcp,
        "mcp-config": cmd_mcp_config,
        "dup": cmd_dup,
        "index": cmd_index,
        "update": cmd_update,
        "ui": cmd_ui,
        "snapshot": cmd_snapshot,
        "backup": cmd_backup,
        "classify": cmd_classify,
    }
    ```

### 1.2 Subsystem Directory Hierarchy

```
d:\teamwork_projects\smart_drive_os\
├── smart_drive/
│   ├── __init__.py
│   ├── __main__.py
│   ├── py.typed
│   ├── cli/                   # Subcommand handlers
│   │   ├── cmd_audit.py
│   │   ├── cmd_backup.py
│   │   ├── cmd_classify.py
│   │   ├── cmd_clean.py
│   │   ├── cmd_init.py
│   │   ├── cmd_mcp.py
│   │   ├── cmd_mcp_config.py
│   │   ├── cmd_organize.py
│   │   ├── cmd_search.py
│   │   ├── cmd_sentinel.py
│   │   ├── cmd_snapshot.py
│   │   ├── cmd_status.py
│   │   ├── cmd_ui.py
│   │   └── main.py            # Central parser & router
│   ├── core/                  # Core domain logic
│   │   ├── auditor.py         # Storage breakdown & slack calculations
│   │   ├── auto_zoner.py      # Relocation & zoning engine
│   │   ├── classifier.py      # Magic byte format inspector
│   │   ├── config.py          # Geometry, taxonomies & protected rules
│   │   ├── duplicates.py      # 3-phase duplicate detection
│   │   ├── exfat_compat.py    # Cluster math, path normalization, Win32 sanitization
│   │   ├── initializer.py     # Drive setup, profiles & manifests
│   │   ├── junk_detector.py   # 3-tier junk detection
│   │   ├── purge_engine.py    # Safe deletion with dry-run protection
│   │   ├── scanner.py         # Fast streaming directory traversal
│   │   ├── sentinel.py        # Shield healing & git status audit
│   │   └── snapshot.py        # Streaming SHA-256 snapshot & backup
│   ├── indexer/               # SQLite FTS5 database management
│   │   ├── db.py              # Schema definition & transactions
│   │   └── manager.py         # Full & incremental indexing
│   ├── mcp/                   # Model Context Protocol stdio server
│   │   ├── proxy.py
│   │   ├── registrar.py
│   │   └── server.py
│   ├── search/                # Search syntax parser & BM25 engine
│   │   ├── engine.py
│   │   ├── formatter.py
│   │   └── parser.py
│   └── ui/                    # Zero-dependency Web Dashboard
│       ├── dashboard.py       # Embedded HTML/CSS/JS frontend SPA
│       └── server.py          # Python http.server ThreadingHTTPServer
└── tests/                     # 100% Python unittest test suite
    ├── helpers.py             # SmartDriveTestCase, sandboxes, oracles
    ├── test_adversarial_*.py  # Edge-case & security regression tests
    ├── test_auditor.py
    ├── test_auto_zoner.py
    ├── test_classifier.py
    ├── test_cleaner.py
    ├── test_cli_e2e.py        # Subprocess E2E CLI testing
    ├── test_exfat_compat.py
    ├── test_geometry.py
    ├── test_indexer.py
    ├── test_initializer.py
    ├── test_mcp_*.py
    ├── test_scanner.py
    ├── test_search.py
    ├── test_sentinel.py
    ├── test_snapshot.py
    └── test_ui*.py
```

---

## 2. Profile System Deep-Dive & `internal-developer-vault` Specification

### 2.1 Current Profile Architecture (`smart_drive/core/initializer.py`)

In `smart_drive/core/initializer.py` (lines 18–74), profiles are declared via a static dictionary `PROFILES`:
- `general-workspace`: Universal workspace with active/archive code, docs, learning notes.
- `ai-developer`: Model engineering, fine-tuning checkpoints, GGUF trees, agent workspaces.
- `data-science`: Data analysis, Parquet/CSV pipelines, EDA notebooks.

Each profile schema contains:
```python
{
    "description": str,
    "taxonomies": List[str],  # Exactly 6 root partition directories
    "subdirs": List[str],     # Pre-created subtrees
}
```

### 2.2 The New `internal-developer-vault` Profile

For internal secondary workstation SSDs (e.g. D:), the profile must organize high-throughput developer workloads and offloaded system caches into the canonical 6-partition structure specified in Requirement R3:

```python
"internal-developer-vault": {
    "description": "Internal secondary SSD workstation vault for AI models, workspaces, datasets & offloaded C-drive caches",
    "taxonomies": [
        "01_AI_Models",
        "02_Development_Workspaces",
        "03_Data_Vault",
        "04_System_Offload_Caches",
        "05_Dev_Toolbox",
        "06_Archives_Storage",
    ],
    "subdirs": [
        "01_AI_Models/checkpoints",
        "01_AI_Models/gguf",
        "01_AI_Models/safetensors",
        "01_AI_Models/onnx",
        "02_Development_Workspaces/active",
        "02_Development_Workspaces/archive",
        "03_Data_Vault/datasets",
        "03_Data_Vault/databases",
        "04_System_Offload_Caches/huggingface",
        "04_System_Offload_Caches/ollama",
        "04_System_Offload_Caches/pip",
        "04_System_Offload_Caches/uv",
        "04_System_Offload_Caches/npm",
        "04_System_Offload_Caches/gradle",
        "05_Dev_Toolbox/scripts",
        "05_Dev_Toolbox/sdks",
        "06_Archives_Storage/backups",
    ],
}
```

### 2.3 Required Inviolable Protection Updates (`smart_drive/core/config.py`)

Because SmartDrive-OS protects canonical taxonomies from purge or accidental manipulation, introducing `02_Development_Workspaces`, `03_Data_Vault`, and `04_System_Offload_Caches` requires updating `smart_drive/core/config.py`:

1. **`PROTECTED_CORE_TAXONOMIES`** (lines 124–133):
   Must be expanded to include:
   - `"02_Development_Workspaces"`
   - `"03_Data_Vault"`
   - `"04_System_Offload_Caches"`
2. **`PROTECTED_ROOT_DIRS`** (lines 137–155):
   Must include the lowercased strings:
   - `"02_development_workspaces"`
   - `"03_data_vault"`
   - `"04_system_offload_caches"`

This ensures that `is_protected_root_dir()` prevents `JunkDetector`, `PurgeEngine`, `AutoZoner`, and `Classifier` from treating these partitions as unorganized or deleteable junk.

### 2.4 Manifest Customization

When `internal-developer-vault` is initialized, the standard manifest templates (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`) in `DriveInitializer` should be aware of:
- Internal fixed SSD architecture (supporting NTFS 4KB allocation as well as exFAT).
- Dedicated C-drive junction offload directory (`04_System_Offload_Caches`).
- Workstation workspace protocols.

---

## 3. Integration Mapping for New CLI Commands

### 3.1 Command 1: `smart-drive offload` (Cache Offloading & Junction Engine)

#### Purpose:
Safely migrate massive cache directories from system drive C: to `04_System_Offload_Caches` on the secondary drive (D:), replacing the original location with an **NTFS Directory Junction (`mklink /J`)**. Applications continue running with zero configuration changes while C: space is fully recovered.

#### CLI Interface in `smart_drive/cli/main.py`:
```python
p_offload = subparsers.add_parser(
    "offload",
    help="Scan, offload, and junction massive C-drive caches to secondary drive",
)
p_offload.add_argument("--scan", action="store_true", help="Scan system drive C: for known large caches")
p_offload.add_argument("--move", metavar="NAME", help="Name of cache to offload to secondary drive (or 'all')")
p_offload.add_argument("--revert", metavar="NAME", help="Revert junction and restore cache back to drive C:")
p_offload.add_argument("--target", default=None, help="Target secondary drive root or directory (e.g. D:)")
p_offload.add_argument("--dry-run", action="store_true", help="Preview migration without modifying filesystem")
p_offload.add_argument("--force", action="store_true", help="Bypass non-critical warnings")
p_offload.add_argument("--json", action="store_true", help="Output report in structured JSON format")
```

#### CLI Handler: `smart_drive/cli/cmd_offload.py`
Dispatches to core offload engine and formats tables for CLI stdout.

#### Core Logic: `smart_drive/core/junction.py` & `smart_drive/core/offload.py`
- **`smart_drive/core/junction.py`**:
  - `is_junction(path: str | Path) -> bool`:
    Checks `os.path.islink(path)` and verifies `stat.FILE_ATTRIBUTE_REPARSE_POINT` (0x0400).
  - `create_junction(link_path: str | Path, target_path: str | Path) -> bool`:
    Executes standard `cmd.exe /c mklink /J "<link_path>" "<target_path>"`.
    Directory junctions on NTFS do not require Administrator elevation or Developer Mode.
  - `remove_junction(link_path: str | Path) -> bool`:
    Uses `os.rmdir(link_path)`. In Windows/Python, `os.rmdir` on a directory junction unlinks the reparse point without deleting the underlying target directory.
  - `get_junction_target(link_path: str | Path) -> Optional[str]`:
    Retrieves the resolved target using `os.readlink(link_path)`.

- **`smart_drive/core/offload.py`**:
  - **Cache Catalogue**:
    | Cache Name | Category | Standard Windows Path |
    |---|---|---|
    | `huggingface` | AI Models | `%USERPROFILE%\.cache\huggingface` |
    | `ollama` | AI Models | `%USERPROFILE%\.ollama\models` |
    | `torch` | AI Models | `%USERPROFILE%\.cache\torch` |
    | `pip` | Package Mgr | `%LOCALAPPDATA%\pip` (fallback `%USERPROFILE%\.cache\pip`) |
    | `npm` | Package Mgr | `%APPDATA%\npm-cache` (fallback `%USERPROFILE%\.npm`) |
    | `uv` | Package Mgr | `%LOCALAPPDATA%\uv` (fallback `%USERPROFILE%\.cache\uv`) |
    | `conda` | Package Mgr | `%USERPROFILE%\.conda\pkgs` or `miniconda3\pkgs` |
    | `gradle` | Build Tools | `%USERPROFILE%\.gradle\caches` |
    | `docker` | Containers | `%LOCALAPPDATA%\Docker\wsl\data` |
  - **Hard Invariant & Safeguards**:
    1. **Strict Target Validation**: Target drive cannot be drive C: (or the drive containing the Windows directory). If target is on the system drive, raise `InvalidTargetDriveError("Cannot offload caches to system drive C:")`.
    2. **Pre-flight Check**: Check available free disk space on secondary drive before copying.
    3. **Two-Stage Atomic Transfer**:
       - Copy data to `04_System_Offload_Caches/<name>` on secondary drive.
       - Verify file count and total size match.
       - Rename source directory to `<name>.bak` as safeguard.
       - Create junction: `mklink /J "<source>" "<target>"`.
       - Verify junction points to target correctly.
       - Safely remove `<name>.bak`.
    4. **Manifest Registry**: Maintain `.smart_drive/offload_manifest.json` recording original path, target path, junction status, timestamp, and size for `--revert` capability.

---

### 3.2 Command 2: `smart-drive health [drive_letter]` (TRIM & SSD Geometry Monitor)

#### Purpose:
Inspect internal SSD hardware status, verify TRIM state on Windows, query sector/cluster geometry, report partition utilization, and warn of storage pressure or unoptimized TRIM.

#### CLI Interface in `smart_drive/cli/main.py`:
```python
p_health = subparsers.add_parser(
    "health",
    help="Check internal SSD health, TRIM state, partition geometry, and storage utilization",
)
p_health.add_argument("drive", nargs="?", default=None, help="Target drive letter (e.g. D: or D:\\)")
p_health.add_argument("--root", help="Alias for target drive root")
p_health.add_argument("--json", action="store_true", help="Output health report in structured JSON format")
```

#### Core Logic: `smart_drive/core/health.py`
Pure Python Standard Library implementation using `ctypes.windll.kernel32`, `subprocess`, and `shutil`:

1. **TRIM Status Inspection**:
   - Executes `fsutil behavior query DisableDeleteNotify` via `subprocess.run`.
   - Parses:
     - `NTFS DisableDeleteNotify = 0`: TRIM active (TRIM operations enabled).
     - `NTFS DisableDeleteNotify = 1`: TRIM disabled (warning issued).
     - Fallback for non-Windows environments: reports `n/a (unsupported on this OS)`.
2. **Drive Hardware Type Identification**:
   - Invokes `ctypes.windll.kernel32.GetDriveTypeW(drive_root)`:
     - `DRIVE_FIXED = 3` -> `"Fixed Internal (NVMe/SATA SSD)"`
     - `DRIVE_REMOVABLE = 2` -> `"Removable External (USB SSD)"`
3. **Filesystem & Volume Information**:
   - Invokes `ctypes.windll.kernel32.GetVolumeInformationW(drive_root, ...)`:
     - Retrieves Volume Label (e.g. `KINGSTON`) and File System Name (e.g. `NTFS`, `exFAT`).
4. **Sector & Cluster Geometry**:
   - Invokes `ctypes.windll.kernel32.GetDiskFreeSpaceW(drive_root, ...)`:
     - Returns `sectors_per_cluster` and `bytes_per_sector`.
     - Calculates cluster size: `4,096 bytes (4 KB)` on NTFS vs `524,288 bytes (512 KB)` on exFAT.
5. **Disk Capacity & Health Warnings**:
   - `shutil.disk_usage(drive_root)` calculates total, used, and free space.
   - Evaluates health warnings:
     - Low space warning if free space < 15% or < 20 GB.
     - Unoptimized TRIM warning if TRIM is disabled.
     - Cluster slack warning if formatting is exFAT 512KB for a code-heavy drive.

---

### 3.3 Command 3: `smart-drive init <drive> --profile internal-developer-vault`

#### CLI Parsing Updates:
In `smart_drive/cli/main.py`:
- Update `p_init` argument `--profile`:
  ```python
  p_init.add_argument(
      "--profile",
      default="general-workspace",
      choices=["general-workspace", "ai-developer", "data-science", "internal-developer-vault"],
      help="Preset profile configuration",
  )
  ```
- In `smart_drive/cli/cmd_init.py`:
  - Enhance drive letter normalization: when a user passes `"D:"` or `"d:"` without a trailing slash, Windows resolves this relative to the process CWD on that drive. It must be canonicalized to `"D:\\"`:
    ```python
    if target_path and len(target_path) == 2 and target_path[1] == ':':
        target_path = target_path.upper() + '\\'
    ```

---

## 4. Filesystem Compatibility & OS Adaptation Engine

SmartDrive-OS must dynamically adapt between **NTFS** (typical for internal drives) and **exFAT** (typical for external Kingston SSDs):

| Feature / Behavior | NTFS Mode (Internal SSD) | exFAT Mode (External SSD) |
|---|---|---|
| **Cluster Allocation** | 4,096 bytes (4 KB) | 524,288 bytes (512 KB) |
| **Cluster Slack Overhead** | Minimal (< 1% for standard code) | High (> 80% for loose micro-files) |
| **Directory Junctions** | Supported (`mklink /J`) | Prohibited (anti-symlink guard) |
| **Naming Restrictions** | Standard Win32 forbidden chars | Standard Win32 forbidden chars |
| **TRIM Support** | Verified via `fsutil DisableDeleteNotify` | N/A (typically not passed through USB) |
| **Search Indexing** | SQLite FTS5 instant search | SQLite FTS5 instant search |
| **Anti-indexing Shields** | Optional | Essential (`.metadata_never_index`) |

### Secondary Drive Discovery Strategy:
- Probes available drive letters: `[f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]`.
- Identifies system drive: `os.environ.get("SystemDrive", "C:")` or `os.environ.get("WINDIR", "C:\\Windows")[:2]`.
- Excludes system drive from candidate targets.
- Detects secondary drives (e.g. `D:`, `E:`).
- Reads drive type via `GetDriveTypeW` and filesystem via `GetVolumeInformationW`.

---

## 5. Test Suite Conventions & Verification Plan

### 5.1 Current Test Architecture
- **Framework**: Pure Python Standard Library `unittest`.
- **Test Base Class**: `SmartDriveTestCase` in `tests/helpers.py`.
  - Automatic sandbox isolation in `self.test_dir` (`tempfile.TemporaryDirectory(prefix="sd_test_")`).
  - Automatic cleanup in `tearDown()`.
  - `create_mock_drive()` fixture generating standard SSD mock tree.
  - Subprocess runner: `run_smart_drive_cli(args)` ensuring UTF-8 encoding and isolated `PYTHONPATH`.
- **Current Test Run**:
  - Command: `python -m unittest discover tests`
  - Output: `Ran 314 tests in 38.107s - OK`.

### 5.2 Test Specifications for Internal Drive Features

1. **`tests/test_offload.py`**:
   - `test_scan_detects_known_caches`: Verifies detection and size reporting across HuggingFace, pip, uv, npm mock paths.
   - `test_offload_rejects_c_drive_target`: Validates that passing `C:\` or `C:` as `--target` raises an error and halts execution.
   - `test_junction_creation_and_reversal`: In a temporary NTFS directory, tests `create_junction()`, confirms `is_junction()` is True, verifies file read/write across the junction, tests `remove_junction()`, and verifies target directory remains intact.
   - `test_offload_move_and_revert_workflow`: End-to-end simulation of cache offload, junction creation, and rollback reversion.
2. **`tests/test_health.py`**:
   - `test_trim_status_parsing`: Validates parsing of `fsutil` outputs (`DisableDeleteNotify = 0` vs `1`).
   - `test_drive_type_detection`: Mocks `GetDriveTypeW` for `DRIVE_FIXED` (3) and `DRIVE_REMOVABLE` (2).
   - `test_geometry_and_space_calculation`: Validates cluster size calculation from sectors per cluster and bytes per sector.
   - `test_health_warning_thresholds`: Verifies warning generation for low free space (< 15%) or disabled TRIM.
3. **`tests/test_internal_vault.py`**:
   - `test_initialize_internal_developer_vault_profile`: Tests `smart-drive init` with `--profile internal-developer-vault`, verifying creation of all 6 partitions (`01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches`, `05_Dev_Toolbox`, `06_Archives_Storage`), subdirectories, manifests, and search database.
   - `test_internal_taxonomies_protected`: Asserts that `is_protected_root_dir` returns True for the 3 new internal partition names.
4. **`tests/test_cli_e2e.py` updates**:
   - Tests `smart-drive offload --scan --json` via CLI subprocess.
   - Tests `smart-drive health --json` via CLI subprocess.
   - Tests `smart-drive init <path> --profile internal-developer-vault --json` via CLI subprocess.

---

## 6. Implementation Action Plan for Downstream Agents

| Step | Target Files | Primary Action |
|---|---|---|
| **Step 1** | `smart_drive/core/config.py` | Add `"02_Development_Workspaces"`, `"03_Data_Vault"`, `"04_System_Offload_Caches"` to `PROTECTED_CORE_TAXONOMIES` and `PROTECTED_ROOT_DIRS`. |
| **Step 2** | `smart_drive/core/initializer.py` | Add `"internal-developer-vault"` to `PROFILES` with the 6 canonical partitions and subtrees. |
| **Step 3** | `smart_drive/core/junction.py` | Implement `is_junction`, `create_junction`, `remove_junction`, `get_junction_target` using `cmd.exe /c mklink /J` and `os.rmdir`. |
| **Step 4** | `smart_drive/core/offload.py` | Implement `OffloadManager`: cache discovery catalogue, `--scan`, `--move`, `--revert`, C-drive rejection guard, manifest tracking. |
| **Step 5** | `smart_drive/core/health.py` | Implement `HealthMonitor`: TRIM check (`fsutil`), drive type (`GetDriveTypeW`), filesystem (`GetVolumeInformationW`), geometry (`GetDiskFreeSpaceW`), disk usage. |
| **Step 6** | `smart_drive/cli/cmd_offload.py` & `cmd_health.py` | Create CLI handlers for `offload` and `health`. |
| **Step 7** | `smart_drive/cli/main.py` | Register `offload` and `health` subparsers; update `init --profile` choices to include `internal-developer-vault`. |
| **Step 8** | `tests/test_offload.py`, `test_health.py`, `test_internal_vault.py` | Write comprehensive unit tests (100% pass target). |
| **Step 9** | Documentation & Git Branch | Prepare documentation updates (`README_INTERNAL.md`, `README.md`, `README_VN.md`) and branch isolation on `internal-secondary-drive`. |
