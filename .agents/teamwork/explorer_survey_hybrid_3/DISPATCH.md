## 2026-10-01T10:11:23Z
[Message] timestamp=2026-10-01T10:11:23Z sender=7b5524b5-f368-4c9e-9c61-3310d53f6752 priority=MESSAGE_PRIORITY_HIGH content=You are Explorer 3 (Survey: Workstation-Hybrid Profile & AcademicClassifier).
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/explorer_survey_hybrid_3
Your parent is orchestrator_3 (Conversation ID: 7b5524b5-f368-4c9e-9c61-3310d53f6752).

MANDATORY INSTRUCTIONS:
1. Read /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (especially section ## 2026-10-01T10:09:26Z, R3, and R4).
2. Investigate the codebase specifically for R3 and R4:
   - smart_drive/cli/cmd_init.py & DriveInitializer: How are profiles defined and handled? How to add 'workstation-hybrid' profile for fixed internal SSD NTFS drives? What folder structure and shields should it generate?
   - AutoZoner: How does it support profiles or interact with 'workstation-hybrid'?
   - smart_drive/core/academic_classifier.py (new module): Requirements:
     * Course code regex ([A-Z]{2,4}\d{3}[a-z]?, e.g. DBI202, WED201c, SWE202c, OSG, PRN211, PRJ301, LAB1_sp26, PE_WED201c,...).
     * Vietnamese academic keywords: hoc ky, ky, fptu, pe_, lab, bai tap, de thi, on luyen.
     * Smart taxonomy: 02_Learning_Knowledge/FPTU/Ky_X/<Course_Code>/.
     * Mojibake decoder / normalization for folder names (e.g. 'h?c k? 3 fptu' -> 'Hoc_Ky_3_FPTU', 'k 1 fptu' -> 'Ky_1_FPTU', 'n luy?n pe dbi202' -> 'On_Luyen_PE_DBI202').
     * Personal books & tool installers: 'dich truyen' -> 02_Learning_Knowledge/Personal_Books/, 'LENOVO' -> 05_Dev_Toolbox/OEM_Drivers/, 'SQL2022' -> 05_Dev_Toolbox/Installers/.
   - smart_drive/cli/: Where to register the CLI command `smart-drive self-path-check`? What should it inspect and guide Windows users to configure Scripts into User PATH?
   - Launcher scripts (.bat, .ps1): Inspect existing launcher scripts in the repository and ensure they prioritize 'python -m smart_drive <cmd>' syntax to run independently of PATH.
3. Write your comprehensive survey report to /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/explorer_survey_hybrid_3/report.md.
4. Update your progress.md and send a completion message with send_message to orchestrator_3 (Recipient: 7b5524b5-f368-4c9e-9c61-3310d53f6752).
