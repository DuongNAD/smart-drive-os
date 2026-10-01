# Project: SmartDrive-OS Workstation Hybrid & AcademicClassifier Upgrade

## Architecture
- **Zero-Dependency Invariant**: 100% Python Standard Library. Zero runtime pip dependencies (`dependencies = []` in `pyproject.toml`).
- **Hardware & Filesystem Abstraction Layer**: Dynamic drive inspection via `inspect_drive()` determining NTFS vs exFAT hardware and cluster characteristics dynamically instead of hardcoding host assumptions; resilient `_safe_home_dir()` home resolution in `registrar.py` and `offloader.py` for Python 3.13 stripped environments; elevation-aware Windows symlink test skipping.
- **Dual-Layer AutoZoner Self-Defense**: Inviolable immunity for the executing SmartDrive-OS repository (`Path(__file__).resolve().parents[2]`), its packages, and ancestor directories in `AutoZoner.classify_item()` and `AutoZoner.apply_plan()` preventing the tool from ever moving itself.
- **Windows System & Service Protection**: Comprehensive expansion of `PROTECTED_ROOT_DIRS` and `DEFAULT_EXCLUDE_DIRS` covering Windows system directories (`WindowsApps`, `WpSystem`, `DeliveryOptimization`, etc.), game/app libraries (`SteamLibrary`, `Riot Games`, `LDPlayer`, `SQL2022`, `fo4`, etc.), and active database instances (`DBI202_VuPT\MSSQL16.MSSQLSERVER`); compound path resolution in `is_protected_root_dir()`.
- **Pre-Probe Scanner Optimization & Quiet Errors**: Move directory exclusion check to the very top of `FastDirectoryScanner` entry loop before any filesystem syscalls (`is_symlink`, `is_dir`); tune `_record_error()` to log permission-denied errors on proprietary system directories at `DEBUG` level rather than `WARNING` log spam.
- **Workstation-Hybrid Profile**: Dedicated preset profile in `DriveInitializer` and `AutoZoner` optimized for fixed internal secondary SSDs (NTFS, Drive D:) with specialized sub-taxonomies (`02_Learning_Knowledge/FPTU`, `Personal_Books`, `05_Dev_Toolbox/OEM_Drivers`, `Installers`).
- **AcademicClassifier Engine**: Zero-dependency classifier recognizing university course codes (`[A-Z]{2,4}\d{3}[a-z]?`), Vietnamese academic keywords (`hoc ky`, `ky`, `fptu`, `pe_`, `lab`, `bai tap`, `de thi`, `on luyen`), FPTU curriculum-to-semester mapping (`Ky_1` .. `Ky_7`), mojibake repair (`'h?c k? 3 fptu'` -> `'Hoc_Ky_3_FPTU'`), illegal character sanitization, and automated routing.
- **CLI Self-Path-Check & Portable Execution**: `smart-drive self-path-check` providing actionable PowerShell, CMD, and GUI instructions to configure Scripts into User PATH; launcher scripts prioritizing `python -m smart_drive` syntax.
- **Comprehensive Quality Assurance**: 100% tests pass (700+ tests, 0 failures, 0 errors) with full adversarial and forensic integrity gating.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Dynamic Drive Inspection in Tests | Replace hardcoded D: assertions (exFAT/524KB) in `test_drive_detector.py` with dynamic `inspect_drive` checks | M1 | explorer_survey_hybrid_1 |
| 2 | ExFAT Junction Probe Hardening | Only probe `mklink /J` rejection in `test_adversarial_filesystem.py` if D: is confirmed `FilesystemType.EXFAT` | M1 | explorer_survey_hybrid_1 |
| 3 | Safe Home Directory Resolution | Implement `_safe_home_dir()` in `registrar.py` (and `offloader.py`) with 5-stage fallback for Python 3.13 env wipe | M1 | explorer_survey_hybrid_1 |
| 4 | Windows Symlink Skip Guard | Add `@unittest.skipIf(sys.platform == "win32", ...)` to `test_broken_junction_detection_on_posix` | M1 | explorer_survey_hybrid_1 |
| 5 | Execution Root Self-Defense | Prevent AutoZoner from moving SmartDrive-OS code via dynamic `Path(__file__)` ancestor/package checks | M2 | explorer_survey_hybrid_2 |
| 6 | Protected Root Dirs Expansion | Add Windows system, game, app, and active database dirs to `PROTECTED_ROOT_DIRS` in `config.py` | M2 | explorer_survey_hybrid_2 |
| 7 | Default Exclude Dirs Expansion | Add WindowsApps, WpSystem, SteamLibrary, MSSQL16.MSSQLSERVER to `DEFAULT_EXCLUDE_DIRS` in `config.py` | M2 | explorer_survey_hybrid_2 |
| 8 | Compound Protected Path Resolution | Upgrade `is_protected_root_dir()` to check relative segments (e.g. `DBI202_VuPT/MSSQL16.MSSQLSERVER`) | M2 | explorer_survey_hybrid_2 |
| 9 | Pre-Probe Scanner Exclusion | Move directory exclusion check before `entry.is_symlink()` / `entry.is_dir()` in `scanner.py` | M2 | explorer_survey_hybrid_2 |
| 10 | Scanner Permission Log Suppression | Tune `_record_error()` in `scanner.py` to log permission-denied errors on system dirs at `DEBUG` | M2 | explorer_survey_hybrid_2 |
| 11 | Workstation-Hybrid Profile Definition | Add `workstation-hybrid` profile with 6 taxonomies and subdirs to `initializer.py` & `cmd_init.py` | M3 | explorer_survey_hybrid_3 |
| 12 | AcademicClassifier Module | Implement `academic_classifier.py` with course regex, semester map, and personal books/tools routing | M3 | explorer_survey_hybrid_3 |
| 13 | Vietnamese Academic & Mojibake Normalization | Decode corrupted folder names (`h?c k? 3 fptu`, `k 1 fptu`, `n luy?n pe dbi202`) and sanitize illegal chars | M3 | explorer_survey_hybrid_3 |
| 14 | AutoZoner Academic Integration | Integrate `AcademicClassifier` into `AutoZoner.classify_item()` and auto-detect `workstation-hybrid` profile | M3 | explorer_survey_hybrid_3 |
| 15 | Launcher Scripts Profile Update | Add `[4] Workstation Hybrid` to interactive menus in `.bat` and `.ps1` launchers | M3 | explorer_survey_hybrid_3 |
| 16 | CLI Subcommand `self-path-check` | Implement `smart-drive self-path-check` guiding Windows users to add Scripts to User PATH | M4 | explorer_survey_hybrid_3 |
| 17 | Test Suites for Hybrid & Classifier | Comprehensive unit and integration tests for AcademicClassifier, workstation-hybrid, self-defense | M4 | survey |
| 18 | Regression & Zero-Dependency Invariant | 100% tests pass (700+ tests, 0 failures, 0 errors, zero external pip dependencies) | M4 | survey |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | R1: Hardware & Filesystem Abstraction | `smart_drive/mcp/registrar.py`, `smart_drive/core/offloader.py`, `tests/test_drive_detector.py`, `tests/test_adversarial_filesystem.py`, `tests/test_mcp_adversarial_challenger1.py` | none | IN_PROGRESS |
| M2 | R2: AutoZoner Self-Defense & System/Game Protection | `smart_drive/core/config.py`, `smart_drive/core/auto_zoner.py`, `smart_drive/core/scanner.py`, `tests/test_autozoner_defense.py` | M1 | PLANNED |
| M3 | R3: Workstation-Hybrid Profile & AcademicClassifier | `smart_drive/core/academic_classifier.py`, `smart_drive/core/initializer.py`, `smart_drive/cli/cmd_init.py`, `smart_drive/core/auto_zoner.py`, launchers, tests | M2 | PLANNED |
| M4 | R4: CLI Utilities & Full Verification Gate | `smart_drive/cli/cmd_self_path_check.py`, `smart_drive/cli/main.py`, comprehensive test suite execution, reviewer, challenger & auditor verification | M1, M2, M3 | PLANNED |

## Interface Contracts
### `smart_drive.mcp.registrar._safe_home_dir() -> Path`
- Returns user home directory with fallbacks: `Path.home()` -> `USERPROFILE` -> `HOME` -> `C:/Users/Default` (Windows) or `/tmp` (POSIX).

### `smart_drive.core.config.is_protected_root_dir(dir_name: str) -> bool`
- Checks base name, full normalized relative path, and path segments against `PROTECTED_ROOT_DIRS`.

### `smart_drive.core.academic_classifier.AcademicClassifier`
- Method `classify(item_path: str) -> Optional[AcademicClassificationResult]`
- Result fields: `category: str`, `target_taxonomy: str`, `relative_dest_path: str`, `normalized_name: str`, `course_code: Optional[str]`, `semester: Optional[str]`.
- Method `normalize_folder_name(name: str) -> str` repairs mojibake and cleans illegal characters.

### `smart_drive.core.initializer.PROFILES["workstation-hybrid"]`
- Contains taxonomies `01_AI_Models` .. `06_Archives_Storage` with specialized subdirs `02_Learning_Knowledge/FPTU`, `02_Learning_Knowledge/Personal_Books`, `05_Dev_Toolbox/Installers`, `05_Dev_Toolbox/OEM_Drivers`.

## Code Layout
- `smart_drive/core/config.py`: Protected directories, default exclusions, security constants.
- `smart_drive/core/auto_zoner.py`: Autonomous organization engine, self-defense checks, taxonomy routing.
- `smart_drive/core/academic_classifier.py`: Academic recognition, course regex, semester mapping, mojibake decoding.
- `smart_drive/core/scanner.py`: Fast directory traversal, pre-probe exclusion, quiet permission handling.
- `smart_drive/core/initializer.py`: Profile definitions, taxonomy structures, shields, manifests.
- `smart_drive/cli/cmd_self_path_check.py`: Diagnostic and fix helper for Windows PATH.
- `smart_drive/cli/cmd_init.py`: Drive initialization CLI command.
- `smart_drive/cli/main.py`: CLI dispatcher and argument parsing.
- `launchers/` & root launchers: Batch and PowerShell launcher scripts.
- `tests/`: Unit and integration test suites.
