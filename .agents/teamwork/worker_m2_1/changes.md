# Changes Report: MCP Server Defensive Hardening & In-Memory Rate Limiting (Milestone M2)

**Author**: Worker M2  
**Date**: 2026-09-29  
**Target File**: `smart_drive/mcp/server.py`  
**Integrity Mode**: Standard Library Zero-Dependency Invariant Maintained (100% pure Python standard library)

---

## 1. Summary of Changes

In accordance with Milestone M2 specifications, `smart_drive/mcp/server.py` was defensively hardened to protect Model Context Protocol (MCP) endpoints against flooding, denial of service (DoS), path traversal attacks, cross-drive filesystem crashes, and malformed inputs.

All modifications strictly adhere to the project's zero-dependency invariant, utilizing only `time.monotonic`, `collections.deque`, `threading.Lock`, `os`, and `json`.

---

## 2. Detailed Modifications in `smart_drive/mcp/server.py`

### 2.1. In-Memory `SlidingWindowRateLimiter`
- **Algorithm**: Thread-safe sliding window log using `collections.deque` and `threading.Lock`.
- **Clock**: `time.monotonic` immune to wall-clock skew, with support for simulated timestamps (`now: Optional[float] = None`) to enable deterministic unit testing.
- **Configurability**:
  - `max_requests`: Defaults to 120 or `SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS`.
  - `window_seconds`: Defaults to 60.0 or `SMART_DRIVE_MCP_RATE_LIMIT_WINDOW`.
  - `enabled`: Defaults to True or `SMART_DRIVE_MCP_RATE_LIMIT_ENABLED` (`"true"`, `"1"`, `"yes"`, `"on"`).
- **Methods**:
  - `acquire(now: Optional[float] = None) -> Tuple[bool, float]`: Evicts timestamps older than `now - window_seconds`. Returns `(True, 0.0)` if within budget; otherwise returns `(False, retry_after)`.
  - `reset() -> None`: Clears all active timestamps.
  - `current_load -> int`: Returns current active requests in rolling window. Implemented via dual `_LoadInt` allowing access either as an integer property (`rl.current_load`) or as a callable (`rl.current_load()`).

### 2.2. Rate Limiting & Parameter Validation in `handle_request`
- Hooked into `SmartDriveMCPServer.handle_request`:
  - Intercepts requests prior to expensive disk I/O, hash computations, or database queries.
  - Returns standard JSON-RPC 2.0 error response with code `-32000` (Server error: Rate limit exceeded), message `"Rate limit exceeded. Try again in {retry_after:.2f} seconds."`, and structured `data={"retry_after": ..., "max_requests": ..., "window_seconds": ...}`.
  - Safely validates `arguments` in `tools/call`. If `arguments` is null/omitted, defaults to `{}`. If non-dict (e.g. string or array), returns JSON-RPC error code `-32602` (Invalid params).
  - Handles programmatic callers and stdio streaming by both calling `send_response` and returning the response dictionary.

### 2.3. Defensive Sanitizers & Boundary Confining Helpers
- **`_resolve_safe_path(self, sub_path: Optional[str], must_exist: bool = False) -> str`**:
  - Returns `self.root` if `sub_path` is None or whitespace.
  - Rejects null bytes (`\x00`) with `ValueError`.
  - Resolves canonical realpath via `os.path.realpath(os.path.abspath(os.path.join(canonical_root, sub_path.strip())))`.
  - Verifies boundary containment via `os.path.commonpath([canonical_root, target])`. If target escapes `canonical_root` or resides on a different drive mount (Windows), raises `ValueError("Access denied: path escapes storage root")`.
  - Enforces existence if `must_exist=True` by raising `FileNotFoundError`.
- **`_parse_bool(val: Any, default: bool = False) -> bool`**:
  - Resolves the boolean coercion trap where Python's `bool("false") == True`.
  - Accurately checks lowercase strings against `("true", "1", "yes", "on", "apply")` vs `("false", "0", "no", "off", "dry_run")`.
- **`_parse_int(val: Any, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int`**:
  - Safely parses integers, protecting against invalid types and clamping within `[min_val, max_val]`.

### 2.4. Hardened Tool Handlers
1. **`handle_ssd_search`**:
   - Bounds query string to 1000 characters.
   - Clamps `limit` between 1 and 100 using `_parse_int`.
   - Sanitizes `offset` (min 0) and `compact` mode via `_parse_bool`.
   - Validates `directory` filter with `_resolve_safe_path`.
2. **`handle_ssd_audit`**:
   - Confines `sub_dir` using `_resolve_safe_path`.
3. **`handle_ssd_clean`**:
   - Confines `sub_dir` / `directory` using `_resolve_safe_path`.
   - Uses `_parse_bool` for `dry_run` and `apply` parameters to prevent unintended live deletions.
   - Uses `_parse_int` for `tier` (clamped 1 to 3).
4. **`handle_ssd_find_duplicates`**:
   - Confines `sub_dir` using `_resolve_safe_path`.
   - Uses `_parse_int` for optional `min_size` filtering.
5. **`handle_ssd_update_index`**:
   - Confines `target_dir` / `directory` using `_resolve_safe_path`.
6. **`handle_ssd_check_safety`**:
   - Rejects null bytes in paths.
   - Catches Windows cross-drive `ValueError` from `os.path.relpath` gracefully without uncaught 500 exceptions, returning `is_safe: False` and error explanation.
   - Identifies paths escaping root (`..`).
7. **`handle_ssd_status`**:
   - Wraps health checks in robust try-except to return fallback diagnostic report with error details on exception.
8. **`handle_ssd_auto_organize`**:
   - Uses `_parse_bool` for `apply` and `clean` parameters.
   - Safely executes Tier 1 safe cleanup if `clean` is enabled.

### 2.5. Tool Hint Annotations Verification
- Verified all 8 MCP tools declare explicit boolean values for `readOnlyHint`, `destructiveHint`, `idempotentHint` (all True), and `openWorldHint` (all False) in both top-level tool schemas and `"annotations"` sub-dictionaries.

### 2.6. Public Exports
- Exported `SlidingWindowRateLimiter` in `__all__` alongside `SmartDriveMCPServer`, `TOOLS`, `PROTOCOL_VERSION`, `SERVER_NAME`, and `SERVER_VERSION`.

---

## 3. Verification & Test Results
- `python -m pytest tests/test_mcp_server.py`: **9 passed in 0.18s** (100% pass rate).
- `python -m pytest`: **436 passed in 42.04s** (100% pass rate, zero regressions across entire codebase).
- Standalone verification script: verified burst allowance, throttle triggering, sliding window eviction, thread concurrency, env var overrides, input boundary escapes, null byte rejections, and all 8 tool hint annotations.
