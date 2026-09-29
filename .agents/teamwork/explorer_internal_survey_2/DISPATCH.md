## 2026-09-26T09:33:00Z

You are Explorer 2 (Hardware & Filesystem Explorer).
Your working directory is: d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_internal_survey_2
Project root: d:\teamwork_projects\smart_drive_os
Read ORIGINAL_REQUEST.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md (specifically the latest entry under '## 2026-09-26T09:30:26Z').

Your task:
1. Investigate how to implement Requirement R1 & R4 in 100% pure Python standard library on Windows:
   - Drive enumeration and auto-excluding C: / Windows system drive (os.environ.get('SystemDrive'), GetSystemDirectoryW, GetLogicalDriveStringsW, etc.).
   - Hardware drive type detection: Fixed Internal (NVMe/SATA SSD) vs Removable External (USB SSD). Research Windows APIs via ctypes.windll.kernel32 (GetDriveTypeW returns DRIVE_FIXED=3 vs DRIVE_REMOVABLE=2), IOCTL_STORAGE_QUERY_PROPERTY / StorageDeviceProperty bus type (NVMe, SATA vs USB), PowerShell / WMI fallback (Get-PhysicalDisk / Win32_DiskDrive), or fsutil.
   - Filesystem detection (GetVolumeInformationW -> NTFS vs exFAT).
   - NTFS optimizations: 4KB cluster check, file compression, selective search index, TRIM status check ('fsutil behavior query DisableDeleteNotify'), sector/cluster geometry.
   - exFAT adaptation: cluster slack guard, anti-symlink enforcement.
2. Design robust fallback mechanisms for testing in environments where secondary drives or admin permissions might be limited (mocking/simulating drives for unit tests).
3. Output your findings to d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_internal_survey_2\analysis.md and write a comprehensive handoff.md. Send a message when finished.
