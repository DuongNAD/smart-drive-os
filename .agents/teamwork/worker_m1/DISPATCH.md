# DISPATCH: Worker M1 — Path Traversal Security & Core Cross-Platform Resolution

## Working Directory
`/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m1`

## Parent
`49720693-a82c-49f8-8742-35eba7ba1b1f` (Project Orchestrator)

## Mandatory Inputs & Rules
- Read `ORIGINAL_REQUEST.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md` (specifically under `## 2026-10-01T07:40:41Z`)
- Read Explorer 1 findings: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_1/handoff.md`
- Read Explorer 2 findings (section 5.3 & 5.4): `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_2/handoff.md`
- Read `PROJECT.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/orchestrator_2/PROJECT.md`
- Code Rules: `/Users/duongnad/Documents/tool/smart-drive-os/AGENTS.md`, `/Users/duongnad/Documents/tool/smart-drive-os/GEMINI.md`

## MANDATORY INTEGRITY WARNING
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Write Ownership (Exclusively Yours)
- `smart_drive/mcp/server.py` (specifically `_resolve_safe_path`, `handle_ssd_check_safety`, `handle_ssd_clean`, `handle_ssd_update_index`)
- `smart_drive/core/drive_detector.py`
- `smart_drive/mcp/proxy.py`
- `smart_drive/core/junction.py`
- `smart_drive/core/offloader.py`
- `smart_drive/ui/server.py`

## Implementation Tasks (Fixing All 14 Test Failures and 2 Errors)
1. **`smart_drive/mcp/server.py` Path Traversal Security**:
   - Universal Path Normalization in `_resolve_safe_path`:
     - Disallow null bytes (`\x00`).
     - Detect and reject Windows drive prefixes (`^[a-zA-Z]:`) on BOTH Windows and POSIX with `ValueError("Access denied: path escapes storage root")`.
     - Detect and reject UNC paths (`//` or `\\`) with `ValueError("Access denied: path escapes storage root")`.
     - Detect POSIX absolute root escapes (`/`).
     - Normalize backslashes `\` to `/` before boundary checking so `..\..\Windows` traversals are caught on POSIX.
     - Ensure `os.path.commonpath([canonical_root, target]) == canonical_root`.
   - `handle_ssd_check_safety`:
     - Strip Windows drive prefix (`re.sub(r'^[a-zA-Z]:', '', clean_path)`) before splitting path segments to avoid false positive colon violations on POSIX.
     - Detect UNC paths and set `is_safe=False` with error `"UNC network paths are not permitted"`.
     - Populate `"error"` key with `"different drive or escapes storage root"` when cross-drive or root escape occurs.
   - `handle_ssd_update_index`:
     - Ensure `db.initialize_schema()` is called to prevent `sqlite3.OperationalError: no such table: files`.
2. **`smart_drive/core/drive_detector.py`**:
   - In `get_system_drive_letter()`: pass `sys_dir` directly through `normalize_drive_letter(sys_dir)` so Windows paths (e.g. `"E:\\Windows\\System32"`) return `"E:"` even on POSIX hosts.
3. **`smart_drive/mcp/proxy.py`**:
   - In `SmartDriveProxy.detect_mount_point()`:
     - Check if `Path.cwd()` is mocked or starts with Windows drive before resolving.
     - Normalize constructed Windows candidate paths so `root_cand / "GEMINI.md"` matches `"e:/gemini.md"` without double slashes.
4. **`smart_drive/core/junction.py`**:
   - In `is_directory_junction(path)`: properly handle broken symlink/junction reparse points on POSIX.
5. **`smart_drive/core/offloader.py`**:
   - In `resolve_cache_path()`: use `.resolve()` on the result to resolve macOS `/var` -> `/private/var` symlink.
   - In `validate_target_drive()`: avoid double slashes when joining `04_System_Offload_Caches`.
6. **`smart_drive/ui/server.py`**:
   - Set `ThreadingHTTPServer.request_queue_size = 128` (or on class definition) to avoid TCP RST under burst concurrency.

## Verification Requirements
- Run the full test suite: `python3 -m unittest discover tests` (or `python3 -m unittest tests/test_mcp_adversarial_challenger2.py tests/test_mcp_hardening.py tests/test_mcp_grade_a.py tests/test_drive_detector.py tests/test_mcp_proxy.py tests/test_junction.py tests/test_offloader.py tests/test_ui_adversarial.py`).
- All 14 previously failing tests and 2 errors MUST PASS cleanly.
- Report exact test counts, commands run, and output in `handoff.md`.

## Deliverable
Write your completion report to `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m1/handoff.md`.
Notify parent via `send_message` when done.

## 2026-10-01T07:52:08Z
You are Worker M1.
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m1
Your parent is: 49720693-a82c-49f8-8742-35eba7ba1b1f (Project Orchestrator)

MANDATORY FIRST STEP: Read the user request at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-10-01T07:40:41Z) and your dispatch file at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m1/DISPATCH.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your task:
Implement genuine fixes for all 14 test failures and 2 errors identified in the Survey report:
1. `smart_drive/mcp/server.py`: Universal Path Normalization in `_resolve_safe_path` (reject null bytes, Windows drive prefixes `^[a-zA-Z]:`, UNC paths `//` or `\\`, root escapes `/`, normalize backslashes before checking `commonpath`), update `handle_ssd_check_safety` to strip drive prefix before forbidden char checks and populate error on cross-drive/escape, and call `db.initialize_schema()` in `handle_ssd_update_index`.
2. `smart_drive/core/drive_detector.py`: Ensure `get_system_drive_letter()` uses `normalize_drive_letter(sys_dir)` so Windows paths return drive letters on POSIX.
3. `smart_drive/mcp/proxy.py`: Handle mock paths and normalize Windows candidate slash joining in `detect_mount_point()`.
4. `smart_drive/core/junction.py`: Fix broken junction detection on POSIX.
5. `smart_drive/core/offloader.py`: Use `.resolve()` in `resolve_cache_path()` and fix Windows double slashes in `validate_target_drive()`.
6. `smart_drive/ui/server.py`: Set `ThreadingHTTPServer.request_queue_size = 128`.

Execute tests to verify that all 14 previously failing tests and 2 errors now PASS:
`python3 -m unittest discover tests`
Report test results, commands, and code changes in /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m1/handoff.md.
Notify parent via send_message when done.

