## 2026-10-01T11:53:08Z
You are Worker M3 (Implementation: R3 Profile 'workstation-hybrid' & 'AcademicClassifier').
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_hybrid_m3
Your parent is orchestrator_3 (Conversation ID: 7b5524b5-f368-4c9e-9c61-3310d53f6752).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

MANDATORY INSTRUCTIONS:
1. You MUST read /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (specifically section ## 2026-10-01T10:09:26Z and R3) before starting work.
2. Read /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/PROJECT.md and /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/explorer_survey_hybrid_3/report.md for complete background, curriculum mapping, regex patterns, mojibake repair rules, and test specifications.
3. Your exclusive file write ownership is strictly:
   - smart_drive/core/academic_classifier.py
   - smart_drive/core/initializer.py
   - smart_drive/cli/cmd_init.py
   - smart_drive/cli/main.py (for --profile choices only, do not implement self-path-check yet which belongs to M4)
   - smart_drive/core/auto_zoner.py (academic classifier integration & profile detection)
   - SmartDrive.bat, Setup_SSD.bat, SmartDrive.ps1, Setup_SSD.ps1 (and their mirrors in launchers/)
   - tests/test_academic_classifier.py
   - tests/test_workstation_hybrid.py
4. Implement the required features:
   - smart_drive/core/academic_classifier.py:
     * Course code regex ([A-Z]{2,4}\d{3}[a-z]?, e.g., DBI202, WED201c, SWE202c, OSG, PRN211, PRJ301, LAB1_sp26, PE_WED201c, etc.).
     * Vietnamese academic keywords: hoc ky, ky, fptu, pe_, lab, bai tap, de thi, on luyen.
     * FPTU course-to-semester curriculum map (Ky_1 to Ky_7).
     * Smart taxonomy routing: 02_Learning_Knowledge/FPTU/Ky_X/<Course_Code>/.
     * Mojibake decoder / folder normalizer:
       - 'h?c k? 3 fptu' -> 'Hoc_Ky_3_FPTU'
       - 'k 1 fptu' -> 'Ky_1_FPTU'
       - 'n luy?n pe dbi202' -> 'On_Luyen_PE_DBI202'
       - Stripping invalid Windows characters (\ / : * ? " < > |).
     * Personal books & tool installers routing:
       - 'dich truyen' (and related) -> 02_Learning_Knowledge/Personal_Books/
       - 'LENOVO' (and related) -> 05_Dev_Toolbox/OEM_Drivers/
       - 'SQL2022' (and related) -> 05_Dev_Toolbox/Installers/
   - smart_drive/core/initializer.py:
     * Add 'workstation-hybrid' profile to PROFILES with all 6 taxonomies and subdirs (FPTU, Personal_Books, OEM_Drivers, Installers, etc.).
     * Write profile marker .smart_drive/profile.json so AutoZoner can auto-detect the active profile.
   - smart_drive/cli/cmd_init.py & smart_drive/cli/main.py:
     * Allow 'workstation-hybrid' as a valid --profile option.
   - smart_drive/core/auto_zoner.py:
     * Integrate AcademicClassifier into AutoZoner: classify_item() should check AcademicClassifier and route matches before generic heuristics.
     * Support profile auto-detection from .smart_drive/profile.json or explicit profile parameter.
   - Launcher scripts (.bat, .ps1):
     * Add option '[4] Workstation Hybrid' in the profile selection menu.
   - Tests:
     * Implement comprehensive tests in tests/test_academic_classifier.py and tests/test_workstation_hybrid.py.
5. Run the test suite:
   python3 -m unittest discover tests
   Ensure all existing and new tests pass cleanly with 0 failures and 0 errors.
6. Write your handoff report to /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_hybrid_m3/handoff.md with Observation, Logic Chain, Caveats, Conclusion, and Verification Command & Outputs.
7. Update progress.md and send a completion message with send_message to orchestrator_3 (Recipient: 7b5524b5-f368-4c9e-9c61-3310d53f6752).
