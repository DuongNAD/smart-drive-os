# Handoff Report: MCP Server Defensive Hardening & In-Memory Rate Limiting (R2)

**From**: Survey Agent 2 (MCP Architecture & Hardening Explorer)  
**To**: Worker / Implementation Agent & Reviewers  
**Working Directory**: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_r2_1`  
**Report Artifact**: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_r2_1\report.md`  
**Date**: 2026-09-29  

---

## 1. Observation

1. **Test Suite Baseline**:
   - Command: `pytest` in `d:\teamwork_projects\smart_drive_os`.
   - Result: `436 passed in 45.85s`. Zero failures. Zero external runtime dependencies in `pyproject.toml` (`dependencies = []`).
2. **Path Traversal Vulnerabilities in `smart_drive/mcp/server.py`**:
   - Line 384: `target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root` (`handle_ssd_audit`)
   - Line 405: `target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root` (`handle_ssd_clean`)
   - Line 430: `target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root` (`handle_ssd_find_duplicates`)
   - Line 436: `target_root = os.path.join(self.root, target_dir) if target_dir else self.root` (`handle_ssd_update_index`)
   - Direct behavior: When `sub_dir` is an absolute path (e.g. `C:\Windows`) or relative path with `../`, `os.path.join` escapes `self.root` without validation.
3. **Windows Cross-Drive Crash in `smart_drive/mcp/server.py`**:
   - Line 448: `rel_path = os.path.relpath(path_str, self.root) if os.path.isabs(path_str) else path_str` (`handle_ssd_check_safety`).
   - On Windows, if `path_str` is on `C:\` and `self.root` is on `D:\`, `os.path.relpath` throws `ValueError: path is on mount 'C:', start on mount 'D:'`.
4. **Destructive Boolean Coercion Trap in `smart_drive/mcp/server.py`**:
   - Line 403: `if "apply" in args: dry_run = not bool(args["apply"])` (`handle_ssd_clean`)
   - Line 469: `apply_mode = bool(args.get("apply", False))` (`handle_ssd_auto_organize`)
   - In Python, `bool("false") == True` and `bool("0") == True`. When an LLM client supplies `{"apply": "false"}`, `apply_mode` activates live file destruction/relocation.
5. **Tool Hint Annotations in `smart_drive/mcp/server.py`**:
   - Lines 47-278: All 8 tools (`ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`) declare `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint` in both `"annotations": {...}` and top-level keys.
   - All have `openWorldHint: False` and `idempotentHint: True`.
   - Inspection of `tests/test_mcp_server.py`: Lines 34-58 only test tool names and input schemas; **no unit tests assert hint annotations**.
6. **Rate Limiting Status**:
   - Currently, `SmartDriveMCPServer` in `smart_drive/mcp/server.py` has **no rate limiter**.
   - `handle_request` directly executes any method or tool without throttling.

---

## 2. Logic Chain

1. **From Observation 1**: The codebase has an extensive passing test suite (436 tests) and a strict zero-external-dependency invariant (`dependencies = []`). All new hardening and rate limiting must strictly use Python Standard Library (`time`, `collections`, `threading`, `os`, `pathlib`).
2. **From Observation 2**: Because `os.path.join(self.root, sub_dir)` returns `sub_dir` when `sub_dir` is absolute, and resolves parent steps when containing `..`, an unauthenticated client can point `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, and `ssd_update_index` to sensitive host paths. Therefore, a centralized path sanitizer `_resolve_safe_path` that resolves canonical realpaths, checks boundary containment within `self.root`, and rejects null bytes is required.
3. **From Observation 3**: `os.path.relpath` is not cross-drive safe on Windows. Wrapping path checks in `handle_ssd_check_safety` with exception handling prevents 500 crashes and gracefully reports out-of-boundary paths.
4. **From Observation 4**: String-to-boolean conversions in JSON-RPC tool parameters are inherently vulnerable to the `bool("false") == True` trap. A dedicated `_parse_bool` helper is required to ensure `"false"`, `"0"`, `"no"`, and `False` are never coerced to `True` during destructive calls.
5. **From Observation 5**: The schema annotations exist and are semantically accurate, but lack regression test coverage. Adding automated schema annotation tests in `tests/test_mcp_server.py` prevents accidental regressions.
6. **From Observation 6**: Adding a thread-safe `SlidingWindowRateLimiter` using `collections.deque` and `threading.Lock` hooked into `SmartDriveMCPServer.handle_request` provides burst allowance ($N$ requests in window), immediate throttle triggering (returning JSON-RPC error code `-32000` with `retry_after`), and automatic window resetting.

---

## 3. Caveats

- The MCP stdio server currently executes within a single-process event loop in `run_stdio`. While `threading.Lock` protects against multi-threaded callers (such as test harnesses or future multi-worker stdio dispatchers), it is process-local and does not share state across multiple separate OS processes. This is by design for an in-memory MCP server.
- Search queries using `directory` filter in `ssd_search` match against relative paths in the SQLite database; validating that `directory` exists physically on disk before querying allows rejecting nonexistent scopes early, but must permit valid subpaths that may currently have 0 indexed files.

---

## 4. Conclusion

Milestone R2 requires the following concrete implementation tasks in `smart_drive/mcp/server.py`:
1. **Implement `SlidingWindowRateLimiter`**:
   - Pure stdlib: `collections.deque`, `threading.Lock`, `time.monotonic`.
   - Methods: `acquire(now=None) -> (bool, float)`, `reset()`, `current_load`.
   - Configurable via `SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS` (default 120), `SMART_DRIVE_MCP_RATE_LIMIT_WINDOW` (default 60.0), and `SMART_DRIVE_MCP_RATE_LIMIT_ENABLED` (default true).
   - Hooked into `SmartDriveMCPServer.handle_request` returning error code `-32000` with `retry_after` on throttle.
2. **Implement Input Sanitizers in `server.py`**:
   - `_resolve_safe_path(self, sub_path, must_exist=False) -> str` enforcing `self.root` containment and rejecting traversal/null bytes.
   - `_parse_bool(val, default=False) -> bool` preventing `"false" == True`.
   - `_parse_int(val, default, min_val, max_val) -> int` clamping negative limits.
3. **Harden Tool Handlers**:
   - Apply `_resolve_safe_path` to `handle_ssd_audit`, `handle_ssd_clean`, `handle_ssd_find_duplicates`, `handle_ssd_update_index`, and `handle_ssd_search`.
   - Apply `_parse_bool` to `handle_ssd_clean`, `handle_ssd_auto_organize`, and `handle_ssd_search`.
   - Catch cross-drive errors in `handle_ssd_check_safety`.
   - Sanitize `arguments` in `handle_request` to ensure it is always a dict.
4. **Expand Test Suite (`tests/test_mcp_server.py`)**:
   - Add unit tests for rate limiting (burst, throttle, reset, thread safety, env vars).
   - Add unit tests for defensive boundary checks (path traversal, null bytes, string boolean safety).
   - Add unit tests verifying all 4 boolean annotations on all 8 tools.

---

## 5. Verification Method

To verify the investigation and downstream implementation:

1. **Verify Baseline Tests**:
   ```bash
   pytest
   ```
   All 436 tests must pass without errors.
2. **Verify Target Files**:
   - Comprehensive survey report: `view_file` on `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_r2_1\report.md`.
   - Server source code: `view_file` on `d:\teamwork_projects\smart_drive_os\smart_drive\mcp\server.py`.
   - Existing MCP tests: `view_file` on `d:\teamwork_projects\smart_drive_os\tests\test_mcp_server.py`.
3. **Downstream Worker Verification Command**:
   ```bash
   pytest tests/test_mcp_server.py -v
   ```
4. **Invalidation Conditions**:
   - Any introduction of third-party pip dependencies in `pyproject.toml` or imports.
   - Any rate limiting throttling that blocks standard sequential agent commands (e.g. limit too small or window too long).
   - Any regression on existing exFAT cluster slack or whitelist protections.
