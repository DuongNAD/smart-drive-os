# Handoff Report: AutoZoner Self-Defense & Windows Services/Apps Protection (R2)

## 1. Observation

1. **AutoZoner Discovery & Classification Mechanism**:
   - Location: `smart_drive/core/auto_zoner.py:220-251` (`generate_plan`) and `lines 131-192` (`classify_item`).
   - In `generate_plan()`: Iterates over immediate entries in `self.root` using `os.scandir(target_dir)` and calls `self.classify_item(entry.path)`.
   - In `classify_item()`: If `is_dir` is True, it calls `entries = {e.name.lower() for e in os.scandir(item_path)}`.
   - Lines 158–160:
     ```python
     if any(ind in entries for ind in self.PROJECT_INDICATORS):
         return ("03_Development_Projects", os.path.join("03_Development_Projects", base_name), "Detected development project indicator")
     ```
     where `PROJECT_INDICATORS = frozenset({".git", "package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml", "cmakelists.txt"})`.
   - When SmartDrive-OS is situated at `D:\smart-drive-os`, `base_name` is `"smart-drive-os"`. Because `"smart-drive-os"` is missing from `PROTECTED_ROOT_DIRS` in `config.py`, AutoZoner matches `.git` and `pyproject.toml`, creating a `ZoningAction` to move `D:\smart-drive-os` to `03_Development_Projects/smart-drive-os`.
   - In `apply_plan()` (lines 288–301), `shutil.move(action.src_path, dest)` executes on the active directory of the running Python process.

2. **Configuration Constants**:
   - Location: `smart_drive/core/config.py:140-161` (`PROTECTED_ROOT_DIRS`) and `lines 542-549` (`DEFAULT_EXCLUDE_DIRS`).
   - `PROTECTED_ROOT_DIRS` contains standard business taxonomies and basic metadata (`system volume information`, `.fseventsd`, `.agents`, etc.), but completely lacks Windows system directories (`WindowsApps`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `Program Files`, `$Recycle.Bin`), game/apps directories (`SteamLibrary`, `Riot Games`, `LDPlayer`, `SQL2022`, `Downloads`, `32837`, `fo4`), active databases (`DBI202_VuPT\MSSQL16.MSSQLSERVER`), and self-protection strings (`smart-drive-os`, `smart_drive_os`, `smart_drive`).
   - `DEFAULT_EXCLUDE_DIRS` only contains `{"$RECYCLE.BIN", "System Volume Information", ".Spotlight-V100", ".Trashes", ".git", ".agents"}`.

3. **Scanner Error Logging and Traversal Flaw**:
   - Location: `smart_drive/core/scanner.py:175-187` (`_record_error`) and `lines 223-254` (`scan_iter`).
   - Line 186 verbatim:
     ```python
     logger.warning("Scanner error [%s] on %s: %s", error_type, path, msg)
     ```
   - In `scan_iter()`:
     Lines 225–241: `entry.is_symlink()` and `entry.is_dir(...)` are called first.
     Lines 252–254:
     ```python
     if is_d:
         if entry.name in self.exclude_dirs or entry.name.lower() in self._exclude_dirs_lower:
             continue
     ```
     Because syscalls (`is_symlink`, `is_dir`) precede the exclusion check, any permission-locked directory like `WindowsApps` raises `PermissionError` immediately, triggering `_record_error(entry.path, "STAT_FAILED", e)` and flooding logs with `WARNING` messages before exclusion can occur.

4. **Test Suite Baseline**:
   - Executed: `python3 -m unittest discover tests`
   - Result: `Ran 697 tests in 35.313s. OK (skipped=11). 0 failures, 0 errors.`
   - No current tests verify AutoZoner self-defense or Windows apps/services protection.

---

## 2. Logic Chain

1. From Observation 1, `AutoZoner.classify_item()` inspects directories based on whitelist checking (`is_protected_root_dir(base_name)`) and indicator presence (`PROJECT_INDICATORS`).
2. Because the execution root (`Path(__file__).resolve().parents[2]`, e.g. `smart-drive-os`) contains `.git` and `pyproject.toml`, and because its name is not currently protected in `PROTECTED_ROOT_DIRS`, AutoZoner classifies its own repository as an unorganized development project.
3. Therefore, running `smart-drive organize --apply` on a drive root containing `smart-drive-os` attempts to move the running application code, causing interpreter failure and workspace corruption.
4. Hence, AutoZoner must automatically inspect `Path(__file__).resolve()` and its ancestors, and immediately return `None` (refusing relocation) whenever an item equals, contains, or is contained by the execution root path.
5. From Observation 2, `PROTECTED_ROOT_DIRS` and `DEFAULT_EXCLUDE_DIRS` omit system directories (`WindowsApps`, `WpSystem`, etc.), game folders (`SteamLibrary`, `Riot Games`, etc.), and active databases (`DBI202_VuPT\MSSQL16.MSSQLSERVER`).
6. Expanding both sets ensures `is_protected_root_dir()` blocks relocation/deletion across `AutoZoner`, `SecurityGuard`, and `PurgeEngine`, while `DEFAULT_EXCLUDE_DIRS` prevents `FastDirectoryScanner` and `JunkDetector` from traversing into restricted or active services.
7. From Observation 3, `FastDirectoryScanner` probes `entry.is_symlink()` and `entry.is_dir()` before testing `entry.name in self.exclude_dirs`, which trips `PermissionError` on locked system folders like `WindowsApps`. Furthermore, `_record_error()` unconditionally logs at `WARNING`.
8. Moving the name check ahead of syscalls eliminates unnecessary permission errors for excluded directories, and adjusting `_record_error()` to log permission errors at `DEBUG` prevents log spam during `smart-drive clean` and `scan`.

---

## 3. Caveats

- **Active SQL Server DB vs Course Documents**: `DBI202_VuPT` may contain both course material (PDFs/notes) and the active instance `MSSQL16.MSSQLSERVER`. Protecting `MSSQL16.MSSQLSERVER` and `DBI202_VuPT/MSSQL16.MSSQLSERVER` shields the live database engine and locked transaction logs while allowing R3 (`AcademicClassifier`) to organize academic files.
- **Elevation on Windows**: Accessing certain attributes inside `WindowsApps` or `$Recycle.Bin` on Windows requires administrator elevation. The design deliberately skips traversing into these folders entirely rather than trying to elevate permissions.
- **Zero External Dependencies**: All mechanisms must remain 100% standard library (`pathlib`, `os`, `logging`).

---

## 4. Conclusion

Requirement R2 requires coordinated, localized updates across three core modules:
1. `smart_drive/core/auto_zoner.py`: Add execution root path containment guards (`Path(__file__).resolve()`) in `AutoZoner.__init__`, `classify_item()`, and `apply_plan()`.
2. `smart_drive/core/config.py`: Expand `PROTECTED_ROOT_DIRS` and `DEFAULT_EXCLUDE_DIRS` with system folders, game libraries, and SQL Server instances, and upgrade `is_protected_root_dir()` to check compound path segments.
3. `smart_drive/core/scanner.py`: Re-order `scan_iter()` to check `entry.name` exclusions before making syscalls, and tune `_record_error()` to log `PERMISSION_DENIED` at `DEBUG` level.

---

## 5. Verification Method

1. **Test Execution Command**:
   ```bash
   python3 -m unittest discover tests
   ```
   Assert all 697 baseline tests continue to pass with 0 failures and 0 errors.

2. **Verify AutoZoner Self-Defense**:
   - Inspect `tests/test_auto_zoner.py`.
   - Add test case verifying that `AutoZoner(mock_root).classify_item(str(Path(__file__).resolve().parents[2]))` returns `None`.
   - Verify that a folder named `smart-drive-os` with `.git` and `pyproject.toml` returns `None` and is not included in `generate_plan()`.

3. **Verify Protected Directories**:
   - Inspect `is_protected_root_dir()` for all required targets: `WindowsApps`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `Program Files`, `$Recycle.Bin`, `System Volume Information`, `SteamLibrary`, `Riot Games`, `LDPlayer`, `SQL2022`, `Downloads`, `32837`, `fo4`, `MSSQL16.MSSQLSERVER`, and `DBI202_VuPT/MSSQL16.MSSQLSERVER`. All must return `True`.

4. **Verify Scanner Quiet Error Handling**:
   - In `tests/test_scanner.py`, test directory exclusion without calling `is_symlink` / `is_dir`.
   - Test that simulated `PermissionError` records into `scanner.errors` and `scanner.stats` but emits no `logger.warning`.

5. **Invalidation Conditions**:
   - Any external dependency added to `pyproject.toml`.
   - Regression in any of the 697 existing tests.
   - Any `ZoningAction` generated for `smart-drive-os` or system/game/database folders.
