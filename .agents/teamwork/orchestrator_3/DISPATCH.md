# DISPATCH LOG

## 2026-10-01T10:10:22Z
You are the Project Orchestrator (orchestrator_3).
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/orchestrator_3
The project workspace root is: /Users/duongnad/Documents/tool/smart-drive-os

Your task is defined in the latest request section in /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-10-01T10:09:26Z):
"Nâng cấp kiến trúc SmartDrive-OS lên chuẩn Workstation Hybrid: giải quyết triệt để 7 vấn đề thực tế trên máy Windows (ổ D: fixed NTFS), tự bảo vệ chính mình khỏi AutoZoner, loại trừ thư mục dịch vụ/game nhạy cảm, tích hợp AcademicClassifier nhận diện môn học đại học FPTU & sửa lỗi font mojibake, và duy trì 100% tests pass đa nền tảng."

Key requirements to deliver:
R1. Hardware & Filesystem Abstraction:
- Update tests/test_drive_detector.py, tests/test_adversarial_filesystem.py to not hardcode drive D: as Kingston XS2000 USB exFAT 512KB. Use inspect_drive(drive_letter) to inspect actual hardware type and format (NTFS vs exFAT) before asserting.
- Wrap Path.home() in smart_drive/mcp/registrar.py with try...except, fallback to os.environ.get("USERPROFILE") or Path("C:/Users/Default") / Path("/tmp") when env vars are wiped on Python 3.13.
- Add @unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows") to test cases invoking os.symlink directly in tests/test_mcp_adversarial_challenger1.py.

R2. Absolute Self-Defense in AutoZoner & Windows Services/Apps Protection:
- Prevent AutoZoner from moving SmartDrive-OS itself: automatically include the execution root path of SmartDrive-OS (Path(__file__).resolve()) in the immutable protected list.
- Expand PROTECTED_ROOT_DIRS and DEFAULT_EXCLUDE_DIRS in smart_drive/core/config.py:
  - System dirs: WindowsApps, WpSystem, DeliveryOptimization, WUDownloadCache, Program Files, $Recycle.Bin, System Volume Information.
  - Game/apps: SteamLibrary, Riot Games, LDPlayer, SQL2022, Downloads, 32837, fo4.
  - Active services/databases: DBI202_VuPT\MSSQL16.MSSQLSERVER (SQL Server 2022).
- Add safe exception handling in smart_drive/core/scanner.py so [PERMISSION_DENIED] errors from proprietary system dirs do not spam logs.

R3. Profile 'workstation-hybrid' & 'AcademicClassifier' (Vietnam Localization):
- Add profile 'workstation-hybrid' in DriveInitializer (cmd_init.py) and AutoZoner optimized for fixed internal SSD NTFS drives holding games, apps, and learning/projects.
- Build AcademicClassifier in smart_drive/core/academic_classifier.py:
  - Course code regex ([A-Z]{2,4}\d{3}[a-z]?, e.g., DBI202, WED201c, SWE202c, OSG, PRN211, PRJ301, LAB1_sp26, PE_WED201c,...).
  - Vietnamese academic keywords: hoc ky, ky, fptu, pe_, lab, bai tap, de thi, on luyen.
  - Smart taxonomy: 02_Learning_Knowledge/FPTU/Ky_X/<Course_Code>/.
  - Mojibake decoder / normalization for folder names (e.g., 'h?c k? 3 fptu' -> 'Hoc_Ky_3_FPTU', 'k 1 fptu' -> 'Ky_1_FPTU', 'n luy?n pe dbi202' -> 'On_Luyen_PE_DBI202').
  - Personal books & tool installers: 'dich truyen' -> 02_Learning_Knowledge/Personal_Books/, 'LENOVO' -> 05_Dev_Toolbox/OEM_Drivers/, 'SQL2022' -> 05_Dev_Toolbox/Installers/.

R4. CLI Utilities & Comprehensive Quality Verification (100% Tests Pass):
- CLI command: smart-drive self-path-check guiding Windows users to configure Scripts into User PATH.
- Ensure .bat, .ps1 launchers prioritize 'python -m smart_drive <cmd>' syntax to run independently of PATH.
- Add comprehensive test suites for AcademicClassifier, workstation-hybrid, and AutoZoner self-defense.
- 100% tests pass (700+ tests) with 0 failures, 0 errors, zero external pip dependencies.
