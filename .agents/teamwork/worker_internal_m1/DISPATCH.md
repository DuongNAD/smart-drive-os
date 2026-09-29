## 2026-09-26T09:40:19Z
You are Worker M1 (Secondary Drive Detector & Filesystem Adapter Implementer).
Your working directory is: d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_internal_m1
Project root: d:\teamwork_projects\smart_drive_os
Read ORIGINAL_REQUEST.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md (specifically the latest entry under '## 2026-09-26T09:30:26Z').
Read PROJECT.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\orchestrator_internal\PROJECT.md
Read Explorer 2's detailed analysis at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_internal_survey_2\analysis.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

File ownership:
You EXCLUSIVELY own:
- smart_drive/core/drive_detector.py
- tests/test_drive_detector.py

Your tasks:
1. Git branch check: Verify git branch is 'internal-secondary-drive'. If on 'main', create and switch to 'internal-secondary-drive' (git checkout -b internal-secondary-drive).
2. Implement smart_drive/core/drive_detector.py in 100% pure Python standard library:
   - System drive detection & strict auto-exclusion (GetSystemDirectoryW, os.environ).
   - Drive letter enumeration (GetLogicalDrives).
   - Hardware drive type detection: Fixed Internal (NVMe/SATA SSD) vs Removable External (USB SSD) using IOCTL_STORAGE_QUERY_PROPERTY via unprivileged Volume Handle, with PowerShell / WMI fallback and unittesting mock backends.
   - Filesystem adaptation: NTFS (4KB cluster allocation, Directory Junctions support, transparent compression, selective index, TRIM verification) vs exFAT (cluster slack guard, anti-symlink policy).
   - Functions and dataclasses conforming to PROJECT.md § Interface Contracts: DriveType, FilesystemType, DriveInfo, get_system_drive_letter(), list_secondary_drives(), inspect_drive(drive_spec).
3. Implement tests/test_drive_detector.py with comprehensive unit tests:
   - System drive C: exclusion.
   - Real drive inspection on host (C:, D:, E:, etc.).
   - Mocked drive backends for CI/CD: simulating NVMe internal, SATA internal, USB external SSD, NTFS volume, exFAT volume, cluster slack calculation, etc.
4. Run 'python -m unittest discover tests' and ensure all tests pass (314 existing + new tests).
5. Write your handoff report to d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_internal_m1\handoff.md and send a message when finished.
