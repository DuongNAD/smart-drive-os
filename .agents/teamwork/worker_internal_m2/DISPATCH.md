## 2026-09-26T10:04:40Z

You are Worker M2 (Cache Offloader & NTFS Directory Junction Engine Implementer).
Your working directory is: d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_internal_m2
Project root: d:\teamwork_projects\smart_drive_os
Read ORIGINAL_REQUEST.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md (specifically the latest entry under '## 2026-09-26T09:30:26Z').
Read PROJECT.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\orchestrator_internal\PROJECT.md
Read Explorer 3's analysis at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_internal_survey_3\analysis.md
Read TEST_INFRA.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\orchestrator_internal\TEST_INFRA.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

File ownership:
You EXCLUSIVELY own:
- smart_drive/core/junction.py
- smart_drive/core/offloader.py
- smart_drive/cli/cmd_offload.py
- smart_drive/cli/main.py (wire 'offload' subparser and dispatch)
- tests/test_junction.py
- tests/test_offloader.py

Requirements to implement (R2):
1. smart_drive/core/junction.py:
   - is_directory_junction(path): Checks if path is a directory and has FILE_ATTRIBUTE_REPARSE_POINT (0x0400).
   - get_junction_target(path): Uses os.readlink(path) and normalizes target (strips \\?\ prefix).
   - create_directory_junction(junction_path, target_path): Runs 'cmd /c mklink /J <junction_path> <target_path>'. Returns True on success, handles errors.
   - remove_directory_junction(junction_path): Safely removes reparse point link (os.unlink or os.rmdir) without deleting files inside target directory!
2. smart_drive/core/offloader.py:
   - Known cache catalog: huggingface, ollama, pytorch, pip, npm, uv, conda, gradle, docker_wsl.
   - Support environment overrides (HF_HOME, UV_CACHE_DIR, OLLAMA_MODELS, etc.).
   - scan_caches(): Scans disk size, checks if already offloaded (junction), calculates total bytes.
   - offload_cache(name, target_drive):
     * Reject C: as target drive (using is_system_drive or drive.upper() == 'C:' from drive_detector).
     * Check target drive free space.
     * Target directory: <target_drive>:\04_System_Offload_Caches\<name>.
     * Execute 7-phase transactional move: copy to temp on target -> rename to final -> rename source to .offload_bak -> create_directory_junction -> purge .offload_bak. Rollback on any failure!
   - revert_cache(name):
     * Verify source is a junction pointing to <target_drive>:\04_System_Offload_Caches\<name>.
     * Remove junction link.
     * Move data back to original C: location.
     * Clean up target folder on secondary drive.
3. smart_drive/cli/cmd_offload.py & main.py:
   - CLI flags: '--scan', '--move <name> --target <drive>', '--revert <name>', '--json'.
   - Human-readable table display and '--json' machine-readable output.
4. Tests in tests/test_junction.py and tests/test_offloader.py:
   - 100% pure Python standard library unittest.
   - Test junction creation, detection, target parsing, and safe unlinking.
   - Test cache scanning with simulated caches in temp directories.
   - Test rejection of C: target drive.
   - Test transactional move and rollback on error.
   - Test revert operation.
   - Run 'python -m unittest discover tests' and ensure all tests pass (existing + new + e2e tests in test_cli_internal_e2e.py).
5. Output handoff to d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_internal_m2\handoff.md and send a message when done.
