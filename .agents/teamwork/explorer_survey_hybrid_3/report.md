# Survey Report: Workstation-Hybrid Profile & AcademicClassifier Architecture

**Surveyor**: Explorer 3 (`explorer_survey_hybrid_3`)  
**Scope**: Requirements R3 & R4 from `ORIGINAL_REQUEST.md` (Milestone: Workstation Hybrid & Academic Classification)  
**Date**: 2026-10-01  
**Project**: SmartDrive-OS (`smart-drive-os`)  
**Target Platform**: Fixed Internal Secondary SSD (NTFS, Drive D:) & External SSD (exFAT), Cross-Platform (Windows & macOS)

---

## 1. Executive Summary

SmartDrive-OS was originally architected for portable external SSDs (e.g. Kingston XS2000 2TB exFAT, 512KB cluster allocation). The new milestone requirement transitions the OS into a **Workstation-Hybrid** powerhouse capable of managing secondary fixed internal SSDs (e.g. `D:\` formatted as NTFS) on Windows workstations. These internal drives typically hold a heterogeneous mixture of:
1. Windows system services & apps (`WindowsApps`, `MSSQL16.MSSQLSERVER`, `SteamLibrary`, etc.).
2. University coursework and academic materials (specifically Vietnamese FPT University curriculum: course codes like `DBI202`, `WED201c`, `SWE202c`, `OSG`, practical exams `PE_...`, labs, and folder names corrupted with question-mark mojibake like `h?c k? 3 fptu`).
3. Personal libraries and OEM toolkits (`dich truyen`, `LENOVO` drivers, `SQL2022` installers).
4. Developer workspaces and offloaded heavy caches (`04_System_Offload_Caches`).

This survey analyzes the exact implementation plan for:
- **R3.1**: Adding the `workstation-hybrid` profile to `DriveInitializer` (`smart_drive/core/initializer.py`, `smart_drive/cli/cmd_init.py`, and `smart_drive/cli/main.py`).
- **R3.2**: Integrating `workstation-hybrid` awareness into `AutoZoner` (`smart_drive/core/auto_zoner.py` and `smart_drive/cli/cmd_organize.py`).
- **R3.3**: Implementing the new core engine `AcademicClassifier` (`smart_drive/core/academic_classifier.py`) featuring FPTU course code regex, semester mappings, mojibake decoding, illegal Windows character remediation, and automated routing.
- **R4.1**: Registering and implementing the CLI utility `smart-drive self-path-check` (`smart_drive/cli/cmd_self_path_check.py`).
- **R4.2**: Auditing launcher scripts (`.bat`, `.ps1`) to ensure strict `python -m smart_drive` priority and adding `workstation-hybrid` options.

---

## 2. Profile `workstation-hybrid` Architecture & `DriveInitializer`

### 2.1 Existing Profile Infrastructure
Profiles are centrally declared in `smart_drive/core/initializer.py` in the `PROFILES` dictionary (lines 18–104). Currently, four profiles exist:
- `general-workspace`
- `ai-developer`
- `data-science`
- `internal-developer-vault`

In `smart_drive/cli/main.py` (lines 176–180), the `--profile` argument choices are currently hardcoded to:
```python
choices=["general-workspace", "ai-developer", "data-science", "internal-developer-vault"]
```

### 2.2 Adding `workstation-hybrid` Profile
In `smart_drive/core/initializer.py`, we define `workstation-hybrid` with the following taxonomy and directory tree:

```python
    "workstation-hybrid": {
        "description": "Fixed internal secondary SSD (NTFS) hybrid workstation for games, Windows apps, university courses, and active dev projects",
        "taxonomies": [
            "01_AI_Models",
            "02_Learning_Knowledge",
            "03_Development_Projects",
            "04_System_Offload_Caches",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ],
        "subdirs": [
            "01_AI_Models/weights",
            "02_Learning_Knowledge/FPTU",
            "02_Learning_Knowledge/Personal_Books",
            "02_Learning_Knowledge/Courses",
            "02_Learning_Knowledge/Notes",
            "03_Development_Projects/active",
            "03_Development_Projects/archive",
            "04_System_Offload_Caches/huggingface",
            "04_System_Offload_Caches/pip",
            "04_System_Offload_Caches/uv",
            "05_Dev_Toolbox/Installers",
            "05_Dev_Toolbox/OEM_Drivers",
            "05_Dev_Toolbox/scripts",
            "06_Archives_Storage/backups",
        ],
    },
```

### 2.3 Taxonomies and Subdirectories Rationales
| Directory / Subdirectory | Purpose in Workstation-Hybrid |
|---|---|
| `01_AI_Models/weights` | LLM weights (GGUF, safetensors, ONNX) used by local AI agents (Ollama, LM Studio). |
| `02_Learning_Knowledge/FPTU` | Destination for organized university courses, semester folders (`Ky_1` .. `Ky_9`), exam prep (`PE_...`), and labs. |
| `02_Learning_Knowledge/Personal_Books` | Destination for personal reading, translated web novels (`dich truyen`), PDFs, and ePubs. |
| `02_Learning_Knowledge/Courses` & `Notes` | General self-study programming courses, certificates, and Markdown notes. |
| `03_Development_Projects/active` & `archive` | Software source repositories, web projects, Antigravity workspaces. |
| `04_System_Offload_Caches` | Offloaded caches from drive `C:\` (HuggingFace models, pip wheels, uv cache, npm) via NTFS directory junctions. |
| `05_Dev_Toolbox/Installers` | Dedicated folder for downloaded software installers (e.g. `SQL2022`, VS Code setup, JDK installers). |
| `05_Dev_Toolbox/OEM_Drivers` | Manufacturer driver backups (e.g. `LENOVO`, chipset, audio, graphics drivers). |
| `05_Dev_Toolbox/scripts` | Automation PowerShell/batch scripts and maintenance routines. |
| `06_Archives_Storage/backups` | Compressed zip/tar archives and snapshot recovery points. |

### 2.4 Anti-Indexing Shields and Manifests on Fixed NTFS Drives
- **Shields**: `check_and_heal_shields(root, auto_heal=True)` generates `.metadata_never_index` and `.fseventsd/no_log`. Even on NTFS, these files prevent macOS Spotlight/FSEvents from thrashing when the drive is shared over SMB or dual-booted.
- **Manifests**: `DriveInitializer` writes `AGENTS.md`, `GEMINI.md`, and `CLAUDE.md`. For `workstation-hybrid`, the manifests guide autonomous agents that the drive is a hybrid NTFS environment with protected game and system services directories.
- **Search DB**: Initializes `.smart_drive/index.db` SQLite FTS5 database schema.
- **Profile Record**: Write `.smart_drive/profile.json` storing `{"profile": "workstation-hybrid", "initialized_at": "..."}` so `AutoZoner` can automatically detect the active profile without needing manual CLI flags.

---

## 3. AutoZoner Integration & Profile Support

### 3.1 Existing `AutoZoner` Mechanics
`AutoZoner` (`smart_drive/core/auto_zoner.py`) performs 2-phase autonomous organization:
1. `generate_plan()`: Scans items at drive root or target folder, filters out items matching `is_protected_root_dir()` or `is_protected_root_file()`, and calls `classify_item(path)`.
2. `apply_plan()`: Moves classified items to target taxonomy destinations with conflict resolution (`resolve_destination_conflict`).

### 3.2 Key Deficiencies in Current `AutoZoner`
1. **No Profile Awareness**: Currently assumes fixed hardcoded rules for all drives.
2. **Missing Academic & Vietnamese Detection**: Any folder named `DBI202_VuPT`, `wed201c`, `PE_WED201c_SP26`, `h?c k? 3 fptu`, `k 1 fptu`, or `dich truyen` fails all existing checks (e.g. `learn`, `course`, `book`) and is left unorganized at root.
3. **No Mojibake Correction**: Folder names containing `?` (illegal on Windows NTFS) are ignored or cause filesystem exceptions.

### 3.3 Enhanced `AutoZoner` Design
1. **Constructor**:
   ```python
   class AutoZoner:
       def __init__(self, root: str, profile: Optional[str] = None) -> None:
           self.root = os.path.abspath(root)
           self.profile = profile or self._detect_profile()
           self.compat = ExFatEngine()
           self.academic_classifier = AcademicClassifier()
   ```
2. **Profile Detection**:
   `_detect_profile()` reads `.smart_drive/profile.json` if present; otherwise defaults to `"general-workspace"`.
3. **Classification Priority in `classify_item(item_path)`**:
   - **Step 1: Whitelist Guard**: Check `is_protected_root_dir` (including self-defense and Windows system/game dirs like `WindowsApps`, `SteamLibrary`, etc.).
   - **Step 2: Academic & Specialized Classification**:
     Call `self.academic_classifier.classify(item_path)`.
     If a match is returned:
     - Relocate FPTU courses to `02_Learning_Knowledge/FPTU/Ky_X/<Course_Code>/` (or clean normalized folder).
     - Relocate `dich truyen` to `02_Learning_Knowledge/Personal_Books/`.
     - Relocate `LENOVO` to `05_Dev_Toolbox/OEM_Drivers/`.
     - Relocate `SQL2022` to `05_Dev_Toolbox/Installers/`.
   - **Step 3: Standard Indicators**: Fall back to git/project indicators, AI model weights, generic learning, and dev toolbox.

---

## 4. `AcademicClassifier` Engine Design (`smart_drive/core/academic_classifier.py`)

This is a brand-new, zero-dependency module implementing Vietnamese academic classification, FPTU course code recognition, mojibake decoding, and taxonomy normalization.

### 4.1 FPTU Course Code Recognition Regex
The FPT University curriculum uses course codes conforming to standard patterns:
- 2 to 4 uppercase letters followed by 3 digits, with an optional lowercase/uppercase letter suffix (e.g., `DBI202`, `WED201c`, `SWE202c`, `PRN211`, `PRJ301`, `CSD201`, `PRO192`, `PRF192`, `MAS291`).
- Special case: `OSG` (representing Operating Systems `OSG209`).
- Prefixes or suffixes commonly added by students: `PE_` (Practical Exam, e.g. `PE_WED201c`, `PE_WED201c_SP26`), `LAB` (e.g. `LAB1_sp26`, `LAB211`), `On_Luyen_PE_...`.

**Regex Pattern**:
```python
COURSE_CODE_PATTERN = re.compile(
    r'(?:^|[\W_])(?P<code>[A-Za-z]{2,4}\d{3}[a-zA-Z]?|OSG)(?:[\W_]|$)',
    re.IGNORECASE
)
```

### 4.2 FPTU Curriculum & Semester Mapping
FPT University Software Engineering (SE) standard semester map:
```python
FPTU_COURSE_TO_SEMESTER: Dict[str, str] = {
    # Semester 1 (Ky 1)
    "PRF192": "Ky_1", "CEA201": "Ky_1", "CSI104": "Ky_1", "MAE101": "Ky_1", "SSL101C": "Ky_1",
    # Semester 2 (Ky 2)
    "PRO192": "Ky_2", "MAD101": "Ky_2", "OSG209": "Ky_2", "OSG": "Ky_2", "NWC203C": "Ky_2", "SSG102": "Ky_2",
    # Semester 3 (Ky 3)
    "CSD201": "Ky_3", "DBI202": "Ky_3", "MAS291": "Ky_3", "LAB211": "Ky_3", "JPD113": "Ky_3",
    # Semester 4 (Ky 4)
    "WED201C": "Ky_4", "SWE202C": "Ky_4", "IOT102": "Ky_4", "JPD123": "Ky_4",
    # Semester 5 (Ky 5)
    "PRJ301": "Ky_5", "SWP391": "Ky_5", "SWR302": "Ky_5", "SWP301": "Ky_5",
    # Semester 6 (Ky 6)
    "PRN211": "Ky_6", "PRN221": "Ky_6", "SWD392": "Ky_6",
    # Semester 7 (Ky 7)
    "PRN231": "Ky_7", "MLN122": "Ky_7", "PRM392": "Ky_7",
}
```

### 4.3 Vietnamese Academic Keywords
Keywords recognized:
- Semester indicators: `hoc ky`, `học kỳ`, `h?c k?`, `ky`, `kỳ`, `k <digit>`
- Institution: `fptu`, `fpt`
- Assessments & materials: `pe_`, `pe`, `lab`, `bai tap`, `bài tập`, `b?i t?p`, `de thi`, `đề thi`, `d? thi`, `on luyen`, `ôn luyện`, `n luy?n`, `?n luy?n`

### 4.4 Mojibake Decoder & Folder Normalizer
When Vietnamese filenames are unpacked or handled in non-UTF-8 Windows consoles, accented diacritics often degrade into `?` (e.g. `học kỳ` -> `h?c k?`, `ôn luyện` -> `n luy?n` or `?n luy?n`).

The decoder executes two-stage repair:
1. **Encoding repair**: Tests `cp1252`/`latin1` byte re-decoding into UTF-8.
2. **Vietnamese Pattern Replacement**:
   - `h?c k?` / `hoc ky` / `học kỳ` -> `Hoc_Ky`
   - `k \d+` / `ky \d+` / `kỳ \d+` -> `Ky_\1`
   - `n luy?n` / `?n luy?n` / `on luyen` / `ôn luyện` -> `On_Luyen`
   - `b?i t?p` / `bai tap` / `bài tập` -> `Bai_Tap`
   - `d? thi` / `de thi` / `đề thi` -> `De_Thi`
   - `fptu` -> `FPTU`
   - `pe` / `pe_` -> `PE`
3. **Illegal Character Stripping**: Removes characters forbidden by Windows NTFS/exFAT (`\ / : * ? " < > |`).
4. **Pascal_Snake_Case Formatting**:
   - Tokenizes on spaces/underscores and capitalizes tokens while keeping acronyms uppercase (`FPTU`, `PE`, `OEM`, `SQL`) and course codes normalized (e.g. `DBI202`, `WED201c`).

**Test Cases**:
- `'h?c k? 3 fptu'` ➔ `'Hoc_Ky_3_FPTU'`
- `'k 1 fptu'` ➔ `'Ky_1_FPTU'`
- `'n luy?n pe dbi202'` ➔ `'On_Luyen_PE_DBI202'`

### 4.5 Personal Books & Tool Installers Rules
| Pattern / Input | Destination Taxonomy | Subpath | Category |
|---|---|---|---|
| `dich truyen`, `dịch truyện`, `truyen`, `novel`, `manga` | `02_Learning_Knowledge` | `Personal_Books/<Normalized_Name>` | `PERSONAL_BOOKS` |
| `LENOVO`, `thinkpad`, `dell`, `hp`, `asus`, `oem`, `driver` | `05_Dev_Toolbox` | `OEM_Drivers/<Normalized_Name>` | `OEM_DRIVERS` |
| `SQL2022`, `installer`, `installers`, `setup`, `softwares` | `05_Dev_Toolbox` | `Installers/<Normalized_Name>` | `INSTALLERS` |

### 4.6 Routing Logic into Smart Taxonomy
When `AcademicClassifier.classify(path)` inspects a path:
1. **Personal Books / OEM / Installers Check**:
   If matched, route directly to the designated sub-taxonomy.
2. **FPTU Semester Root Folder**:
   If folder matches semester patterns (e.g. `Hoc_Ky_3_FPTU`, `Ky_1_FPTU`), route to `02_Learning_Knowledge/FPTU/<Normalized_Name>`.
3. **FPTU Course Item / Folder**:
   - Extract course code (e.g. `DBI202`, `WED201c`).
   - Extract or look up semester:
     * If parent path or folder name specifies semester (e.g. `ky 3`), use `Ky_3`.
     * Otherwise check `FPTU_COURSE_TO_SEMESTER` (e.g. `DBI202` -> `Ky_3`, `WED201c` -> `Ky_4`).
     * Fallback: `Ky_General`.
   - Normalize target subpath:
     * Exact course code (e.g. `wed201c`): `02_Learning_Knowledge/FPTU/Ky_4/WED201c`
     * Course with teacher/suffix (e.g. `DBI202_VuPT`): `02_Learning_Knowledge/FPTU/Ky_3/DBI202_VuPT`
     * Exam practice (e.g. `PE_WED201c_SP26`): `02_Learning_Knowledge/FPTU/Ky_4/WED201c/PE_WED201c_SP26`
     * Mojibake practice (e.g. `n luy?n pe dbi202`): `02_Learning_Knowledge/FPTU/Ky_3/DBI202/On_Luyen_PE_DBI202`

---

## 5. CLI Command `smart-drive self-path-check`

### 5.1 Motivation
On Windows systems, when a user installs SmartDrive-OS using `pip install -e .` or `pip install .` without adding Python's `Scripts` directory to the user `PATH` environment variable, typing `smart-drive` in PowerShell or CMD fails with:
`'smart-drive' is not recognized as an internal or external command`.

### 5.2 Implementation Specification
Create `smart_drive/cli/cmd_self_path_check.py` and register the subcommand `self-path-check` (alias: `self_path_check`) in `smart_drive/cli/main.py`.

**Inspection Mechanics**:
1. Identify Python Scripts Directory:
   - Determine `scripts_dir = sysconfig.get_path("scripts")`.
   - Also inspect `os.path.join(sys.prefix, "Scripts")` and `os.path.join(site.getuserbase(), "PythonXX", "Scripts")`.
2. Inspect System & User PATH:
   - Split `os.environ.get("PATH", "")` using `os.pathsep`.
   - Check if `scripts_dir` is present in `PATH`.
   - Verify if `shutil.which("smart-drive")` resolves.
3. Remediation Output:
   - Provide exact PowerShell command for persistent Windows configuration:
     ```powershell
     [Environment]::SetEnvironmentVariable("Path", $env:Path + ";<scripts_path>", "User")
     ```
   - Provide CMD / setx command:
     ```cmd
     setx PATH "%PATH%;<scripts_path>"
     ```
   - Provide Windows GUI navigation steps:
     `System Properties -> Advanced -> Environment Variables -> User variables -> Path -> Edit -> New -> <scripts_path>`.
   - Remind the user of the zero-PATH portable command:
     `python -m smart_drive <subcommand>`

**CLI Options**:
- `--json`: Outputs structured diagnostic data (`in_path`, `executable_found`, `scripts_dir`, `python_version`, `fix_command`).
- `--fix`: Outputs only the exact PowerShell command ready for clipboard or piped execution.

---

## 6. Audit of Launcher Scripts (.bat, .ps1)

### 6.1 Current Status
All existing launcher scripts were audited:
- Root `.bat`: `Quick_Audit.bat`, `Quick_Clean.bat`, `Quick_Search.bat`, `Setup_SSD.bat`, `SmartDrive.bat`
- Root `.ps1`: `Quick_Audit.ps1`, `Quick_Clean.ps1`, `Quick_Search.ps1`, `Setup_SSD.ps1`, `SmartDrive.ps1`
- Mirror folder `launchers/`: Contains identical copies of each script.

### 6.2 Priority Verification
The audit confirmed:
- Every `.bat` file discovers Python dynamically (`python`, `py`, `python3`, `py -3`) and executes via:
  `%PYTHON_CMD% -m smart_drive <subcommand>`
- Every `.ps1` file executes via:
  `& $PythonCmd -m smart_drive @Arguments`
- **Result**: Launchers already strictly prioritize `python -m smart_drive` syntax and operate 100% independently of the Windows system `PATH`!

### 6.3 Required Launcher Enhancements
In `SmartDrive.bat`, `SmartDrive.ps1`, `Setup_SSD.bat`, `Setup_SSD.ps1` (and their `launchers/` mirrors):
Add profile option `[4] Workstation Hybrid`:
```bat
echo Select a Preset Profile for your drive:
echo   [1] AI Developer      (Models, checkpoints, GGUF, agent workspaces)
echo   [2] Data Science      (EDA notebooks, pipelines, parquet/raw data)
echo   [3] General Workspace (Universal code, docs, notes, toolbox)
echo   [4] Workstation Hybrid (Fixed internal SSD, FPTU courses, dev & tools)
```
Map option `4` to `--profile workstation-hybrid`.

---

## 7. Test Verification & Invariant Strategy

### 7.1 Proposed Test Suites
1. `tests/test_academic_classifier.py`:
   - Test FPTU course code extraction on `DBI202_VuPT`, `wed201c`, `SWE202c`, `OSG`, `PRN211`, `PRJ301`, `LAB1_sp26`, `PE_WED201c_SP26`.
   - Test Vietnamese academic keywords (`hoc ky`, `ky`, `fptu`, `pe_`, `lab`, `bai tap`, `de thi`, `on luyen`).
   - Test mojibake decoding:
     * `'h?c k? 3 fptu' -> 'Hoc_Ky_3_FPTU'`
     * `'k 1 fptu' -> 'Ky_1_FPTU'`
     * `'n luy?n pe dbi202' -> 'On_Luyen_PE_DBI202'`
   - Test personal books and tool installers:
     * `'dich truyen' -> '02_Learning_Knowledge/Personal_Books/'`
     * `'LENOVO' -> '05_Dev_Toolbox/OEM_Drivers/'`
     * `'SQL2022' -> '05_Dev_Toolbox/Installers/'`
2. `tests/test_workstation_hybrid.py`:
   - Test `DriveInitializer.initialize(profile="workstation-hybrid")` creates all 6 taxonomies, subdirs (`FPTU`, `Personal_Books`, `OEM_Drivers`, `Installers`), shields, manifests, and search database.
   - Test `AutoZoner` with `workstation-hybrid` profile organizes mixed folders into `02_Learning_Knowledge/FPTU/` with clean names.
3. `tests/test_self_path_check.py`:
   - Test `smart-drive self-path-check` execution in console and `--json` mode.
   - Test `--fix` option outputs valid PowerShell command.

### 7.2 Hard Invariants
- **Zero Runtime Dependencies**: 100% Python Standard Library (`re`, `sys`, `os`, `pathlib`, `sysconfig`, `shutil`, `json`, `dataclasses`).
- **Zero Data Loss**: Whitelist guard preserves all system/game/service directories.
- **Cross-Platform**: Operates cleanly on macOS, Linux, and Windows.

---

## 8. Summary Table of Proposed File Changes

| File | Type | Changes / Purpose |
|---|---|---|
| `smart_drive/core/initializer.py` | Modify | Add `workstation-hybrid` profile definition to `PROFILES` with subdirs. |
| `smart_drive/cli/cmd_init.py` | Modify | Support `workstation-hybrid` in validation and reporting. |
| `smart_drive/cli/main.py` | Modify | Add `"workstation-hybrid"` to `p_init` `--profile` choices; register `self-path-check` subcommand. |
| `smart_drive/core/academic_classifier.py` | **Create** | New core module: course regex, curriculum map, mojibake decoder, personal books/tools routing. |
| `smart_drive/core/auto_zoner.py` | Modify | Integrate `AcademicClassifier` and `workstation-hybrid` profile support into `classify_item`. |
| `smart_drive/cli/cmd_self_path_check.py` | **Create** | New CLI handler for `smart-drive self-path-check`. |
| `SmartDrive.bat`, `Setup_SSD.bat` (and `launchers/`) | Modify | Add option `[4] Workstation Hybrid` to interactive profile menu. |
| `SmartDrive.ps1`, `Setup_SSD.ps1` (and `launchers/`) | Modify | Add option `[4] Workstation Hybrid` to interactive profile menu. |
| `tests/test_academic_classifier.py` | **Create** | Complete unit test suite for academic classification & mojibake normalization. |
| `tests/test_workstation_hybrid.py` | **Create** | Complete integration test suite for `workstation-hybrid` profile. |
| `tests/test_self_path_check.py` | **Create** | Complete CLI test suite for `smart-drive self-path-check`. |
