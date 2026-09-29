## 2026-09-29T13:46:50Z

You are Worker M2 (MCP Server Defensive Hardening & In-Memory Rate Limiting) for SmartDrive-OS.
Your working directory is: d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_m2_1
You MUST read:
1. ORIGINAL_REQUEST.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md
2. PROJECT.md at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\PROJECT.md
3. Survey Report at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_r2_1\report.md
4. Survey Handoff at: d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_r2_1\handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

File Ownership:
You EXCLUSIVELY own:
- `smart_drive/mcp/server.py`
DO NOT touch any other files (do NOT touch `pyproject.toml`, `PRIVACY.md`, `README.md`, or test files).

Tasks to implement in `smart_drive/mcp/server.py`:
1. Implement `SlidingWindowRateLimiter`:
   - Pure Python Standard Library: `time.monotonic`, `collections.deque`, `threading.Lock`. Zero external dependencies!
   - Thread-safe sliding window algorithm.
   - Configurable:
     - `max_requests`: default 120 (reads `os.getenv("SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS", "120")`)
     - `window_seconds`: default 60.0 (reads `os.getenv("SMART_DRIVE_MCP_RATE_LIMIT_WINDOW", "60.0")`)
     - `enabled`: default True (reads `os.getenv("SMART_DRIVE_MCP_RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes")`)
   - Methods:
     - `acquire(now=None) -> Tuple[bool, float]`: returns `(allowed, retry_after)`. If allowed, records timestamp and returns `(True, 0.0)`. If throttled, returns `(False, retry_after)`.
     - `reset() -> None`: clears deque.
     - `current_load -> int`: returns count of active requests in current window.
2. Integrate Rate Limiter into `SmartDriveMCPServer`:
   - Initialize `self.rate_limiter = SlidingWindowRateLimiter()` in `__init__`.
   - In `handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]`:
     - Check `self.rate_limiter.acquire()`. If not allowed:
       return JSON-RPC error response with code `-32000` (Server error: Rate limit exceeded), message `"Rate limit exceeded. Try again in {retry_after:.2f} seconds."`, and `data={"retry_after": retry_after, "max_requests": ..., "window_seconds": ...}`.
     - Ensure `arguments` field in tool calls is safely validated to be a `dict` (handling null or invalid types gracefully with `-32602` Invalid params).
3. Defensive Input Sanitizers & Boundary Confining:
   - Implement `_resolve_safe_path(self, sub_path: Optional[str], must_exist: bool = False) -> str`:
     - If `sub_path` is empty or None, return `self.root`.
     - Reject null bytes (`\x00`).
     - Resolve canonical realpath: `target = os.path.realpath(os.path.abspath(os.path.join(self.root, sub_path.strip())))`
     - Verify boundary containment: ensure `target` is within `self.root` (e.g. `os.path.commonpath([self.root, target]) == self.root`). If not, raise `ValueError("Access denied: path escapes storage root")`.
     - If `must_exist` and `not os.path.exists(target)`, raise `FileNotFoundError`.
   - Implement `_parse_bool(val: Any, default: bool = False) -> bool`:
     - Prevents string `"false"` or `"0"` from being truthy!
     - If already `bool`, return `val`. If `str`, check lowercased against `("true", "1", "yes", "on", "apply")`.
   - Implement `_parse_int(val: Any, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int`:
     - Prevents negative limits or overflows. Clamps between `min_val` and `max_val`.
4. Harden all 8 MCP Tool Handlers:
   - `handle_ssd_audit`: use `_resolve_safe_path(args.get("sub_dir"))`.
   - `handle_ssd_clean`: use `_resolve_safe_path(args.get("sub_dir"))`, use `_parse_bool` for `apply` / `dry_run`.
   - `handle_ssd_find_duplicates`: use `_resolve_safe_path(args.get("sub_dir"))`, `_parse_int` for min_size.
   - `handle_ssd_update_index`: use `_resolve_safe_path(args.get("target_dir"))`.
   - `handle_ssd_search`: use `_parse_int` for `limit` (clamped 1 to 100), validate `directory` with `_resolve_safe_path`.
   - `handle_ssd_check_safety`: catch `ValueError` from `os.path.relpath` (Windows cross-drive errors) and handle paths outside drive gracefully without throwing uncaught 500 exceptions.
   - `handle_ssd_auto_organize`: use `_parse_bool` for `apply`.
   - `handle_ssd_status`: robust exception handling.
5. Tool Hint Annotations:
   - Ensure all 8 tools have explicit boolean declarations for `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint` in both tool schema dictionaries and annotations dicts.
6. Verification:
   - Run tests: `python -m pytest tests/test_mcp_server.py` and `python -m pytest`.
   - Ensure all tests pass.
