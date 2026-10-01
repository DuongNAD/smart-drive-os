# Handoff Report: Survey of Workstation-Hybrid Profile & AcademicClassifier

**From**: Explorer 3 (`explorer_survey_hybrid_3`)  
**To**: Orchestrator 3 (`orchestrator_3` / `7b5524b5-f368-4c9e-9c61-3310d53f6752`)  
**Milestone**: Survey for Workstation-Hybrid Profile & AcademicClassifier Architecture (R3, R4)  
**Date**: 2026-10-01  

---

## 1. Observation

1. **`smart_drive/core/initializer.py` & `smart_drive/cli/cmd_init.py` Profile Architecture**:
   - `PROFILES` dictionary defined at `smart_drive/core/initializer.py:18-104` currently contains 4 profiles: `"general-workspace"`, `"ai-developer"`, `"data-science"`, and `"internal-developer-vault"`.
   - In `smart_drive/cli/main.py:178-180`, `--profile` argument choices are currently constrained to:
     `choices=["general-workspace", "ai-developer", "data-science", "internal-developer-vault"]`.
   - Initializer creates: (1) Taxonomies & subdirectories, (2) Anti-indexing shields (`.metadata_never_index`, `.fseventsd/no_log`), (3) Manifests (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`), and (4) SQLite FTS5 database at `.smart_drive/index.db`.

2. **`smart_drive/core/auto_zoner.py` & Directory Classification**:
   - In `smart_drive/core/auto_zoner.py:131-192`, `classify_item` checks `PROJECT_INDICATORS`, `("model", "weights", "checkpoint", "gguf")`, `("learn", "course", "book", "note", "tutorial")`, `("tool", "utility", "script")`, and `("archive", "backup", "dump", "old_")`.
   - Any directory named `DBI202_VuPT`, `wed201c`, `PE_WED201c_SP26`, `h?c k? 3 fptu`, `k 1 fptu`, `dich truyen`, `LENOVO`, or `SQL2022` returns `None` and is completely ignored by `AutoZoner`.
   - Folder names containing `?` (e.g. `h?c k? 3 fptu`) contain illegal Windows filesystem characters (`\ / : * ? " < > |`), causing errors on Windows NTFS.

3. **Absence of `AcademicClassifier` Module**:
   - File `smart_drive/core/academic_classifier.py` currently does not exist.
   - Grep search for `AcademicClassifier` returned zero matches across the entire codebase.

4. **CLI Subcommand Registration in `smart_drive/cli/main.py`**:
   - `build_parser()` currently registers 18 subcommands (`init`, `status`, `audit`, `clean`, `search`, `organize`, `sentinel`, `mcp`, `mcp-config`, `dup`, `index`, `update`, `ui`, `snapshot`, `backup`, `classify`, `offload`, `health`).
   - `smart-drive self-path-check` is not yet registered.

5. **Launcher Scripts (`.bat`, `.ps1`) Discovery & Execution**:
   - Audited root scripts (`SmartDrive.bat`, `Quick_Audit.bat`, `Quick_Clean.bat`, `Quick_Search.bat`, `Setup_SSD.bat`, and corresponding `.ps1` files) and `launchers/` mirrors.
   - Line 58 of `Quick_Audit.bat`: `%PYTHON_CMD% -m smart_drive audit`.
   - Line 63 of `SmartDrive.ps1`: `& py -3 -m smart_drive @Arguments` / `& $PythonCmd -m smart_drive @Arguments`.
   - All launchers dynamically discover Python and execute with `python -m smart_drive <subcommand>`, operating 100% independently of PATH.
   - `SmartDrive.bat` (lines 89-96) and `SmartDrive.ps1` (lines 94-100) currently offer options 1-3 (AI Developer, Data Science, General Workspace) and lack `workstation-hybrid`.

6. **Current Test Baseline**:
   - Running `python3 -m unittest discover tests` executed **697 tests in 35.260s** with 100% pass rate (`OK (skipped=11)`).

---

## 2. Logic Chain

1. From Observation 1, adding the `workstation-hybrid` profile requires defining the profile entry in `PROFILES` in `smart_drive/core/initializer.py`, updating `--profile` choices in `smart_drive/cli/main.py`, and recording `.smart_drive/profile.json` during initialization so downstream tools can auto-detect the drive profile.
2. From Observation 2, `AutoZoner` cannot recognize university course materials, Vietnamese academic terminology, mojibake folders, personal books, or OEM installers because it lacks an academic and localization classification tier.
3. Therefore, implementing `smart_drive/core/academic_classifier.py` (Observation 3) with course regex (`[A-Za-z]{2,4}\d{3}[a-zA-Z]?|OSG`), semester mapping (`FPTU_COURSE_TO_SEMESTER`), mojibake decoder (`h?c k? 3 fptu` -> `Hoc_Ky_3_FPTU`, `k 1 fptu` -> `Ky_1_FPTU`, `n luy?n pe dbi202` -> `On_Luyen_PE_DBI202`), and routing for `dich truyen` (`02_Learning_Knowledge/Personal_Books/`), `LENOVO` (`05_Dev_Toolbox/OEM_Drivers/`), and `SQL2022` (`05_Dev_Toolbox/Installers/`) provides the exact intelligence needed.
4. Integrating `AcademicClassifier` into `AutoZoner.classify_item` directly resolves the requirement to organize unorganized FPTU items into `02_Learning_Knowledge/FPTU/Ky_X/<Course_Code>/` while repairing font corruptions and removing Windows illegal characters.
5. From Observation 4, adding `smart_drive/cli/cmd_self_path_check.py` and registering `self-path-check` in `main.py` provides Windows users with automated detection of whether Python `Scripts` is on `PATH`, coupled with one-click PowerShell remediation commands and fallback reminders.
6. From Observation 5, existing launchers already satisfy the requirement of prioritizing `python -m smart_drive`, but updating their profile selection menus to include `[4] Workstation Hybrid` provides a seamless 1-touch experience.
7. From Observation 6, implementing these changes with dedicated unit and integration test suites will expand test coverage beyond 700+ tests while preserving 100% pass rate and zero external dependencies.

---

## 3. Caveats

1. **Course Code Overlap**: Some short 3-letter codes like `OSG` require word-boundary anchors (`\bOSG\b`) to avoid false-matching common substrings inside ordinary words.
2. **Mojibake Variations**: Vietnamese diacritics corrupted with `?` can appear with varying numbers of question marks (e.g. `?n luy?n` vs `n luy?n`). The regex pattern must use `(?:\?*n|\bon)\s*luy\?n` to capture both variants.
3. **Active SQL Server Protection**: Per R2, active Microsoft SQL Server instance folders (e.g. `DBI202_VuPT\MSSQL16.MSSQLSERVER`) must not be moved or disturbed by `AutoZoner`. `AcademicClassifier` must respect root whitelist guards and only relocate course materials when safe.

---

## 4. Conclusion

The architectural design for R3 and R4 is completely formulated, validated against existing code, and documented in detail at `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/explorer_survey_hybrid_3/report.md`.

Key components ready for implementation:
1. `smart_drive/core/initializer.py`: Add `workstation-hybrid` profile.
2. `smart_drive/core/academic_classifier.py`: Create new zero-dependency module with FPTU regex, semester mapping, mojibake decoder, and smart taxonomy routing.
3. `smart_drive/core/auto_zoner.py`: Integrate `AcademicClassifier` and profile awareness.
4. `smart_drive/cli/cmd_self_path_check.py`: Create Windows Scripts PATH inspector with PowerShell remediation.
5. `smart_drive/cli/main.py`: Register `self-path-check` and expand `--profile` choices.
6. `SmartDrive.bat` & `SmartDrive.ps1` (and `launchers/`): Add profile option `[4] Workstation Hybrid`.
7. Test suites: `tests/test_academic_classifier.py`, `tests/test_workstation_hybrid.py`, `tests/test_self_path_check.py`.

---

## 5. Verification Method

1. **Inspect Survey Report**:
   ```bash
   cat /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/explorer_survey_hybrid_3/report.md
   ```
2. **Verify Baseline Test Suite**:
   ```bash
   python3 -m unittest discover tests
   ```
   Must pass 697 tests cleanly with 0 failures and 0 errors.
3. **Invalidation Conditions**:
   - If `AcademicClassifier` requires external pip packages (violates zero-dependency invariant).
   - If folder normalization fails on `'h?c k? 3 fptu' -> 'Hoc_Ky_3_FPTU'`, `'k 1 fptu' -> 'Ky_1_FPTU'`, or `'n luy?n pe dbi202' -> 'On_Luyen_PE_DBI202'`.
   - If `AutoZoner` moves protected system or active service folders.
