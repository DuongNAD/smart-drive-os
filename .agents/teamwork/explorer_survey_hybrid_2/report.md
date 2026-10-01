# Survey Report: AutoZoner Self-Defense & Windows Services/Apps Protection (R2)

**Explorer**: Explorer 2 (Survey: AutoZoner Self-Defense & Windows Services/Apps Protection)  
**Parent Agent**: orchestrator_3 (`7b5524b5-f368-4c9e-9c61-3310d53f6752`)  
**Target Working Directory**: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/explorer_survey_hybrid_2`  
**Date**: 2026-10-01  
**Project Invariants**: Zero runtime external dependencies (100% Python Standard Library), 100% test pass rate (current baseline: 697 tests pass).

---

## Executive Summary

This survey report provides the architectural investigation, vulnerability analysis, and design blueprint for **Requirement R2 (AutoZoner Self-Defense & Windows Services/Apps Protection)** under the Workstation Hybrid milestone.

### Core Discoveries
1. **Critical Self-Relocation Vulnerability in `AutoZoner`**:  
   If SmartDrive-OS is executed on a volume where the codebase repository (`smart-drive-os`) is located at the root level (such as `D:\smart-drive-os`), `AutoZoner.classify_item()` detects `.git` and `pyproject.toml` inside it, classifies the repository as `03_Development_Projects`, and plans to move the entire active codebase into `03_Development_Projects/smart-drive-os`. When executed with `--apply`, `shutil.move()` unlinks and relocates the executing Python codebase beneath the running interpreter, causing immediate `FileNotFoundError`, broken imports, and destroyed IDE/agent working directories.
2. **Missing System, Game, and Active Database Protections**:  
   `PROTECTED_ROOT_DIRS` and `DEFAULT_EXCLUDE_DIRS` in `smart_drive/core/config.py` currently only protect default business taxonomies and basic macOS/Unix metadata (`$RECYCLE.BIN`, `.Spotlight-V100`, `.Trashes`, `.git`, `.agents`, `System Volume Information`). Critical Windows workstation directories (`WindowsApps`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `Program Files`, `SteamLibrary`, `Riot Games`, `LDPlayer`, `SQL2022`, `Downloads`, `32837`, `fo4`, and active SQL Server database instances `DBI202_VuPT\MSSQL16.MSSQLSERVER`) are not excluded or protected, leaving them vulnerable to accidental relocation, permission errors, and service corruption.
3. **Flawed Exclusion Check Ordering in `FastDirectoryScanner`**:  
   In `smart_drive/core/scanner.py`, `entry.is_symlink()` and `entry.is_dir()` are called **before** checking `self.exclude_dirs`. When traversing drives containing permission-restricted system folders like `WindowsApps` or locked SQL Server database logs, these probes raise `PermissionError` immediately, triggering `self._record_error()` and flooding the console with `logger.warning("Scanner error [PERMISSION_DENIED]...")` before the directory name is ever checked for exclusion. Furthermore, `_record_error` unconditionally logs at `WARNING` severity.

---

## 1. AutoZoner Architecture & Self-Defense Mechanism

### 1.1 Current Folder Discovery Flow in `AutoZoner`
**File**: `smart_drive/core/auto_zoner.py` (lines 55–316)

```
AutoZoner(root)
  └── generate_plan(scan_dir=None)
        └── target_dir = os.path.abspath(scan_dir or self.root)
        └── os.scandir(target_dir) [iterates over root entries]
              └── classify_item(item_path)
                    ├── 1. Whitelist guard:
                    │      is_protected_root_dir(base_name) -> currently checks base_name.lower() in PROTECTED_ROOT_DIRS
                    │      is_protected_root_file(base_name)
                    │      base_name.startswith(".") -> skip hidden
                    ├── 2. Directory Classification:
                    │      os.scandir(item_path)
                    │      any(ind in entries for ind in PROJECT_INDICATORS):
                    │          PROJECT_INDICATORS = {".git", "package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml", "cmakelists.txt"}
                    │          ===> Returns ("03_Development_Projects", "03_Development_Projects/<base_name>", ...)
                    │      Folder name heuristics: "model", "weights", "learn", "tool", "archive"
                    └── 3. File Classification (extensions: .gguf, .pdf, .zip, .ps1)
        └── ZoningAction(src_path, dest_path, target_taxonomy, reason, ...)
  └── apply_plan(plan)
        └── ensure_taxonomies_exist()
        └── for action in plan:
              resolve_destination_conflict(action.dest_path)
              shutil.move(action.src_path, dest)
```

### 1.2 The "Self-Move" Disaster Scenario
When a developer or AI agent runs:
```bash
python -m smart_drive organize --root D:\ --apply
```
where SmartDrive-OS itself is located at `D:\smart-drive-os`:
1. `AutoZoner` scans `D:\`.
2. It encounters `D:\smart-drive-os`.
3. `is_protected_root_dir("smart-drive-os")` returns `False` because `"smart-drive-os"` is missing from `PROTECTED_ROOT_DIRS`.
4. It inspects children of `D:\smart-drive-os`: finds `.git` and `pyproject.toml`.
5. It matches `PROJECT_INDICATORS` and classifies `smart-drive-os` into `03_Development_Projects/smart-drive-os`.
6. When `apply_plan()` runs, `shutil.move("D:\\smart-drive-os", "D:\\03_Development_Projects\\smart-drive-os")` executes while Python is actively running from that exact directory.
7. Result: Immediate broken filesystem state, crash on lazy module loading, invalid terminal/agent current working directory, and corrupted git repository state.

### 1.3 Recommended Implementation: Dual-Layer Self-Defense
To guarantee that AutoZoner never moves itself or any part of its execution hierarchy:

#### Layer 1: Dynamic Execution Root Resolution in `AutoZoner`
In `smart_drive/core/auto_zoner.py`:
```python
# Compute execution anchors once at module level or in AutoZoner.__init__
_CURRENT_FILE = Path(__file__).resolve()
_SMART_DRIVE_CORE_DIR = _CURRENT_FILE.parent              # smart_drive/core
_SMART_DRIVE_PKG_DIR = _SMART_DRIVE_CORE_DIR.parent      # smart_drive
_SMART_DRIVE_REPO_DIR = _SMART_DRIVE_PKG_DIR.parent      # smart-drive-os (repository root)

class AutoZoner:
    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.compat = ExFatEngine()
        
        # Self-defense: collect all ancestors of running code up to volume root
        self._self_ancestors: Set[Path] = set(_CURRENT_FILE.parents)
        self._self_protected_paths: Set[Path] = {
            _CURRENT_FILE,
            _SMART_DRIVE_CORE_DIR,
            _SMART_DRIVE_PKG_DIR,
            _SMART_DRIVE_REPO_DIR,
        }
        self._self_protected_names: Set[str] = {
            "smart-drive-os",
            "smart_drive_os",
            "smart_drive",
            "smart_drive_manager",
            _SMART_DRIVE_REPO_DIR.name.lower(),
            _SMART_DRIVE_PKG_DIR.name.lower(),
        }
```

In `classify_item(self, item_path: str)`:
```python
        base_name = os.path.basename(item_path)
        base_name_lower = base_name.lower()
        is_dir = os.path.isdir(item_path)

        # 0. INVIOLABLE SELF-DEFENSE: Never relocate the running SmartDrive-OS code or its host repo
        if base_name_lower in self._self_protected_names:
            return None

        try:
            resolved_item = Path(item_path).resolve()
            # If current execution file is inside or equal to item_path, never move it
            if resolved_item in self._self_protected_paths or resolved_item in self._self_ancestors:
                return None
            if _CURRENT_FILE.is_relative_to(resolved_item):
                return None
            if resolved_item.is_relative_to(_SMART_DRIVE_REPO_DIR):
                return None
        except (ValueError, OSError, RuntimeError):
            pass

        # 1. Whitelist guard: Never move protected items
        if is_dir:
            if is_protected_root_dir(item_path) or is_protected_root_dir(base_name):
                return None
        ...
```

#### Layer 2: Defense-in-Depth in `apply_plan`
In `AutoZoner.apply_plan(self, plan: List[ZoningAction])`:
```python
        for action in plan:
            # Re-verify self-defense barrier before executing move
            try:
                resolved_src = Path(action.src_path).resolve()
                if (
                    resolved_src in self._self_protected_paths
                    or resolved_src in self._self_ancestors
                    or _CURRENT_FILE.is_relative_to(resolved_src)
                ):
                    continue
            except Exception:
                pass
```

---

## 2. Expansion of `PROTECTED_ROOT_DIRS` and `DEFAULT_EXCLUDE_DIRS`

### 2.1 File Analysis: `smart_drive/core/config.py`
In `smart_drive/core/config.py`:
- `PROTECTED_ROOT_DIRS` (lines 140–162) is a `FrozenSet[str]` storing casefolded strings used by `is_protected_root_dir()`, `SecurityGuard`, and `AutoZoner`.
- `DEFAULT_EXCLUDE_DIRS` (lines 542–549) is a `FrozenSet[str]` used by `FastDirectoryScanner` to prune directories during filesystem traversal.

### 2.2 Inventory of Categories to Add

| Category | Target Directories | In `PROTECTED_ROOT_DIRS` | In `DEFAULT_EXCLUDE_DIRS` | Rationale |
|---|---|---|---|---|
| **System Dirs** | `WindowsApps` | `windowsapps` | `"WindowsApps"` | Windows Store apps folder with restricted ACLs; scanning causes permission errors; moving breaks Windows apps. |
| | `WpSystem` | `wpsystem` | `"WpSystem"` | Windows Phone / Universal app storage on secondary drives. |
| | `DeliveryOptimization` | `deliveryoptimization` | `"DeliveryOptimization"` | Windows Update peer-to-peer delivery cache. |
| | `WUDownloadCache` | `wudownloadcache` | `"WUDownloadCache"` | Windows Update installer cache. |
| | `Program Files` | `program files`, `program files (x86)` | `"Program Files"`, `"Program Files (x86)"` | Native application installation directories. |
| | `$Recycle.Bin` | `$recycle.bin` | `"$Recycle.Bin"`, `"$RECYCLE.BIN"` | Windows Recycle Bin folder (ensure both casings covered). |
| | `System Volume Information` | `system volume information` | `"System Volume Information"` | NTFS / VSS system folder (already in config, keep harmonized). |
| **Game & App Dirs** | `SteamLibrary` | `steamlibrary` | `"SteamLibrary"` | Steam game installations and library manifest files. |
| | `Riot Games` | `riot games` | `"Riot Games"` | League of Legends, Valorant, Riot Client installations. |
| | `LDPlayer` | `ldplayer` | `"LDPlayer"` | Android emulator with massive virtual disk images. |
| | `SQL2022` | `sql2022` | `"SQL2022"` | Microsoft SQL Server 2022 setup binaries and instance directory. |
| | `Downloads` | `downloads` | `"Downloads"` | Default user download folder often relocated to secondary drive. |
| | `32837` | `32837` | `"32837"` | Dedicated application/Steam app-id numeric folder. |
| | `fo4` | `fo4` | `"fo4"` | Fallout 4 installation directory on D:\. |
| **Active Services / Databases** | `DBI202_VuPT\MSSQL16.MSSQLSERVER` | `mssql16.mssqlserver`, `dbi202_vupt/mssql16.mssqlserver` | `"MSSQL16.MSSQLSERVER"`, `"DBI202_VuPT/MSSQL16.MSSQLSERVER"` | Active Microsoft SQL Server 2022 database service instance with live `.mdf`, `.ldf`, and `ERRORLOG` files. |
| | Generic SQL Services | `mssqlserver`, `mssql` | `"MSSQLSERVER"`, `"MSSQL"` | Generic SQL service instances. |
| **Self-Defense** | SmartDrive-OS | `smart-drive-os`, `smart_drive_os`, `smart_drive`, `smart_drive_manager` | `"smart-drive-os"`, `"smart_drive_os"`, `"smart_drive"`, `"smart_drive_manager"` | Prevents AutoZoner and SecurityGuard from touching the tool codebase. |

### 2.3 Upgrading `is_protected_root_dir()`
Currently in `smart_drive/core/config.py` (lines 164–172):
```python
def is_protected_root_dir(dir_name: str) -> bool:
    normalized = dir_name.strip().replace("\\", "/").rstrip("/")
    base_name = os.path.basename(normalized) or normalized
    return base_name.lower() in PROTECTED_ROOT_DIRS
```
**Observation**: If a compound path such as `DBI202_VuPT\MSSQL16.MSSQLSERVER` is passed, `base_name` is `MSSQL16.MSSQLSERVER`. However, if `DBI202_VuPT/MSSQL16.MSSQLSERVER` is checked as a full relative path, or if an item inside it is checked, we should support:
1. `base_name.lower() in PROTECTED_ROOT_DIRS`
2. `normalized.lower() in PROTECTED_ROOT_DIRS`
3. Any path segment: `any(part.lower() in PROTECTED_ROOT_DIRS for part in normalized.split("/"))`

This guarantees that:
- Checking `"MSSQL16.MSSQLSERVER"` -> `True`
- Checking `"DBI202_VuPT/MSSQL16.MSSQLSERVER"` -> `True`
- Checking `"D:\\WindowsApps"` -> `True`
- Checking `"SteamLibrary"` -> `True`

---

## 3. Scanner Architecture & Permission Denial Error Suppression

### 3.1 Error Logging Analysis in `smart_drive/core/scanner.py`
In `smart_drive/core/scanner.py`:
- Line 31: `logger = logging.getLogger("smart_drive.core.scanner")`
- Lines 175–187:
```python
    def _record_error(self, path: str, error_type: str, exc: Exception) -> None:
        """Record an error in stats and history, logging and invoking on_error callback."""
        self.stats.error_count += 1
        msg = str(exc)
        err = ScanError(path=path, error_type=error_type, message=msg, exception=exc)
        self.errors.append(err)
        if self.on_error:
            try:
                self.on_error(path, exc)
            except Exception:
                pass
        logger.warning("Scanner error [%s] on %s: %s", error_type, path, msg)
```
- Lines 200–210:
```python
            try:
                scandir_it = os.scandir(current_dir)
            except PermissionError as e:
                self._record_error(current_dir, "PERMISSION_DENIED", e)
                continue
```
- Lines 223–254:
```python
                for entry in entries:
                    # 1. Symlink probe
                    try:
                        is_sym = entry.is_symlink()
                    except (PermissionError, FileNotFoundError, OSError) as e:
                        self._record_error(entry.path, "STAT_FAILED", e)
                        is_sym = False

                    # 2. Directory probe
                    try:
                        is_d = entry.is_dir(follow_symlinks=self.follow_symlinks)
                    except (PermissionError, FileNotFoundError, OSError) as e:
                        self._record_error(entry.path, "DIR_CHECK_FAILED", e)
                        continue

                    # 3. EXCLUSION CHECK HAPPENS HERE (TOO LATE!)
                    if is_d:
                        if entry.name in self.exclude_dirs or entry.name.lower() in self._exclude_dirs_lower:
                            continue
```

### 3.2 Root Causes of Log Spam
1. **Late Exclusion Check**: `entry.is_symlink()` and `entry.is_dir()` are invoked *before* testing if `entry.name` is in `self.exclude_dirs`. For Windows system directories like `WindowsApps`, calling `entry.is_symlink()` or `entry.is_dir()` immediately throws `PermissionError` (Windows Error 5: Access is Denied), triggering `self._record_error()` and logging a warning.
2. **Indiscriminate Warning Severity**: `_record_error()` logs all errors at `logger.warning`, regardless of whether the error is expected (such as permission denied on a locked system directory or database transaction log).
3. **Missing System Exclusions**: Because `WindowsApps`, `WpSystem`, `MSSQL16.MSSQLSERVER`, etc., are not in `DEFAULT_EXCLUDE_DIRS`, the scanner pushes them to its DFS traversal stack and tries to scan them.

### 3.3 Recommended Scanner Fixes
1. **Pre-Probe Exclusion Check**:  
   Check directory exclusion by name **before** making any syscalls (`is_symlink`, `is_dir`, `stat`):
   ```python
   for entry in entries:
       entry_name = entry.name
       entry_name_lower = entry_name.lower()
       
       # Fast in-memory check (zero syscalls)
       if entry_name in self.exclude_dirs or entry_name_lower in self._exclude_dirs_lower:
           continue
   ```
2. **Path-Based Exclusion Check**:  
   Support compound exclusions (e.g. matching `mssql16.mssqlserver` in relative path):
   ```python
   norm_path = os.path.normpath(entry.path)
   rel_path = _normalize_rel_path(norm_path, self.root_path)
   rel_path_lower = rel_path.lower()
   if any(ex in rel_path_lower.split("/") for ex in self._exclude_dirs_lower):
       continue
   ```
3. **Differentiated Log Severity in `_record_error`**:  
   Downtune expected permission errors to `DEBUG` level:
   ```python
   is_perm = (
       error_type in ("PERMISSION_DENIED", "STAT_FAILED", "DIR_CHECK_FAILED")
       and (
           isinstance(exc, PermissionError)
           or getattr(exc, "winerror", None) == 5  # ERROR_ACCESS_DENIED
           or getattr(exc, "errno", None) in (13, 1)  # EACCES, EPERM
       )
   )
   
   # Check if path contains known proprietary/system names
   path_lower = path.replace("\\", "/").lower()
   is_known_system = any(
       sys_dir in path_lower for sys_dir in (
           "windowsapps", "system volume information", "wpsystem",
           "deliveryoptimization", "wudownloadcache", "mssql", "$recycle.bin"
       )
   )
   
   if is_perm or is_known_system:
       logger.debug("Scanner permission denied on %s: %s", path, msg)
   else:
       logger.warning("Scanner error [%s] on %s: %s", error_type, path, msg)
   ```
   This guarantees that during `smart-drive clean` or `smart-drive scan`, permission denied errors from system directories or locked SQL Server logs will never spam the user's terminal.

---

## 4. Interaction with Other Subsystems

### 4.1 Relationship with `smart_drive clean` (`cmd_clean.py` and `junk_detector.py`)
- `JunkDetector` initializes `FastDirectoryScanner(exclude_dirs=scanner_excludes)`.
- In `FastDirectoryScanner.__init__`, `self.exclude_dirs` merges `DEFAULT_EXCLUDE_DIRS` with custom exclusions.
- Expanding `DEFAULT_EXCLUDE_DIRS` in `config.py` means `cmd_clean` and `JunkDetector` will automatically skip `WindowsApps`, `WpSystem`, `MSSQL16.MSSQLSERVER`, and game libraries, completely preventing accidental deletion of files in those directories.

### 4.2 Relationship with `SecurityGuard` and `PurgeEngine`
- `SecurityGuard.is_protected(target_path)` checks `is_protected_root_dir(root_segment)`.
- By including all system dirs, game dirs, and database dirs in `PROTECTED_ROOT_DIRS`, `SecurityGuard.validate_deletion()` will raise `SecurityViolationError` if any operation attempts to delete them.

### 4.3 Relationship with R3 (`AcademicClassifier`)
- In R3, `AcademicClassifier` classifies student course folders (e.g. `DBI202_VuPT`, `wed201c`) into `02_Learning_Knowledge/FPTU/`.
- Crucial distinction: `DBI202_VuPT` as course material should be categorized, BUT if it contains `MSSQL16.MSSQLSERVER` (the live SQL Server 2022 instance installed by students), that database subdirectory must remain untouched and excluded!
- By adding `MSSQL16.MSSQLSERVER` and `DBI202_VuPT/MSSQL16.MSSQLSERVER` to `PROTECTED_ROOT_DIRS` and `DEFAULT_EXCLUDE_DIRS`, `AutoZoner` and `AcademicClassifier` can safely leave the SQL database in place or only categorize academic document files while preserving the database engine.

---

## 5. Review of Existing Tests & New Test Specifications

### 5.1 Current Test Suite Baseline
- Verified via `python3 -m unittest discover tests`:
  **697 tests passed cleanly (0 failures, 0 errors, 11 skipped)** in 35.3s.
- Existing relevant test suites:
  - `tests/test_auto_zoner.py`: 8 tests
  - `tests/test_scanner.py`: 9 tests
  - `tests/test_cleaner.py`: 8 tests
  - `tests/test_launchers_and_invariants_tier5.py`: 18 tests
  - `tests/test_internal_vault.py`: 12 tests

### 5.2 Required New Test Specifications for R2
To be added to `tests/test_auto_zoner.py` and `tests/test_scanner.py` (or a dedicated `tests/test_autozoner_defense.py`):

1. **`test_autozoner_self_defense_execution_root`**:
   - Create a mock repository directory structure representing `smart-drive-os` with `.git/`, `pyproject.toml`, and `smart_drive/`.
   - Call `AutoZoner.classify_item()` on this directory.
   - Assert returns `None`.
   - Call `AutoZoner.classify_item()` on the actual execution path `Path(__file__).resolve().parents[2]`.
   - Assert returns `None`.
   - Generate plan and assert `smart-drive-os` is not in the plan.
   - Test that `apply_plan` explicitly skips any action containing self execution path.

2. **`test_autozoner_protected_windows_apps_and_games`**:
   - Create directories for each required target: `WindowsApps`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `Program Files`, `$Recycle.Bin`, `SteamLibrary`, `Riot Games`, `LDPlayer`, `SQL2022`, `Downloads`, `32837`, `fo4`, `MSSQL16.MSSQLSERVER`.
   - Populate with files or indicator files (e.g. `SteamLibrary` with `.git` or binaries).
   - Assert `is_protected_root_dir(name)` is `True` for each.
   - Assert `AutoZoner.classify_item()` returns `None` for each.
   - Assert `generate_plan()` produces zero actions for these folders.

3. **`test_security_guard_blocks_deletion_of_protected_apps`**:
   - Instantiate `SecurityGuard(mock_root)`.
   - For each target folder (`WindowsApps`, `SteamLibrary`, etc.), assert `guard.is_protected(path)[0]` is `True`.
   - Assert `guard.validate_deletion(path)` raises `SecurityViolationError`.

4. **`test_scanner_pre_probe_exclusion_and_permission_denied_quiet`**:
   - Construct a directory tree with `WindowsApps` and `MSSQL16.MSSQLSERVER`.
   - Run `FastDirectoryScanner`.
   - Assert entries inside `WindowsApps` and `MSSQL16.MSSQLSERVER` are never emitted.
   - Mock a `PermissionError` when scanning a system directory.
   - Assert that no `logger.warning` is emitted for `PERMISSION_DENIED` on system directories (or logged at `DEBUG`).
   - Assert `scanner.stats.error_count` records the event without crashing.

5. **`test_config_invariants_and_compound_paths`**:
   - Verify `PROTECTED_ROOT_DIRS` contains all required entries in lowercase.
   - Verify `DEFAULT_EXCLUDE_DIRS` contains all required entries.
   - Verify `is_protected_root_dir("DBI202_VuPT/MSSQL16.MSSQLSERVER")` returns `True`.

---

## 6. Recommendations for Implementer Agent

1. **File Edits Scoped to R2**:
   - `smart_drive/core/config.py`: Expand `PROTECTED_ROOT_DIRS` and `DEFAULT_EXCLUDE_DIRS`. Update `is_protected_root_dir()` to check compound paths.
   - `smart_drive/core/auto_zoner.py`: Add `_CURRENT_FILE` / `_SMART_DRIVE_REPO_DIR` checks in `AutoZoner.__init__`, `classify_item()`, and `apply_plan()`.
   - `smart_drive/core/scanner.py`: Move exclusion check to top of loop before syscalls; tune logging level in `_record_error()` for permission-denied system directories.
2. **Maintain 100% Backward Compatibility**:
   - All existing 697 tests must continue to pass without regression.
   - Zero external pip dependencies (`dependencies = []` in `pyproject.toml`).
   - Pure Python standard library implementation (`pathlib`, `os`, `logging`).
