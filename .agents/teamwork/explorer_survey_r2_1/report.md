# Comprehensive Technical Survey Report: MCP Server Defensive Hardening & In-Memory Rate Limiting (R2)

**Document Reference**: `.agents/teamwork/explorer_survey_r2_1/report.md`  
**Target Milestone**: R2 (MCP Server Defensive Hardening & In-Memory Rate Limiting)  
**Author**: Survey Agent 2 (MCP Architecture & Hardening Explorer)  
**Integrity Mode**: Development / Read-Only Investigation  
**Date**: 2026-09-29  

---

## 1. Executive Summary

This investigation surveys the Model Context Protocol (MCP) subsystem of SmartDrive-OS located in `smart_drive/mcp/`, focusing on `smart_drive/mcp/server.py`. SmartDrive-OS exposes 8 high-performance tools over JSON-RPC 2.0 stdio to autonomous coding agents.

The core objective of Milestone R2 is to harden this interface against accidental flood, denial of service (DoS), path traversal attacks, and malformed inputs while adhering strictly to the **100% Python Standard Library zero-external-dependency invariant**.

### Key Findings Summary:
1. **Critical Path Traversal Vulnerabilities**: In `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, and `ssd_update_index`, user-provided path parameters (`sub_dir`, `directory`) are combined via naive `os.path.join(self.root, sub_dir)` without boundary validation. Attackers or confused agents can supply `../../` or absolute paths (e.g. `C:\Windows`), causing tools to scan, hash, index, or discover junk files completely outside the SSD drive root.
2. **Windows Cross-Drive Crash Bug**: In `ssd_check_safety`, `os.path.relpath(path_str, self.root)` crashes with an unhandled `ValueError` when `path_str` resides on a different drive mount (e.g. `C:\...` vs `D:\...`).
3. **Boolean Coercion Trap in Destructive Handlers**: In `ssd_clean` and `ssd_auto_organize`, string boolean inputs like `{"apply": "false"}` evaluate to `True` via Python's built-in `bool("false") == True`. An agent intending a harmless dry-run can accidentally trigger immediate file deletions or live relocations.
4. **Unbounded and Malformed Inputs**: `ssd_search` accepts negative limits (e.g. `limit=-10`), which translates to invalid or full-table SQLite queries; queries lack character length bounds; non-dict arguments cause unhandled `AttributeError`.
5. **Tool Hint Annotations Status**: All 8 tools declare `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint` in both the top-level tool dict and `"annotations": {...}`. Their semantic values are correct (all have `openWorldHint=False` and `idempotentHint=True`), but **zero unit tests exist to enforce these contracts**, creating high regression risk.
6. **Pure Standard Library Rate Limiter**: A thread-safe sliding-window rate limiter using `collections.deque`, `threading.Lock`, and `time.monotonic` can be seamlessly integrated into `SmartDriveMCPServer.handle_request`. It natively provides burst allowance, throttle triggers, window resets, and environment variable configurability (`SMART_DRIVE_MCP_RATE_LIMIT_*`).

---

## 2. Examination of `smart_drive/mcp/` Architecture

The `smart_drive/mcp/` package contains four modules:

1. **`smart_drive/mcp/server.py`** (636 lines, 25.5 KB):
   - Implements `SmartDriveMCPServer`, a zero-dependency JSON-RPC 2.0 stdio server supporting both newline-delimited JSON and `Content-Length:` header framing.
   - Houses the `TOOLS` catalog list defining the 8 tool schemas.
   - Houses individual tool dispatch handlers (`handle_ssd_*`) and request lifecycle management (`handle_request`, `dispatch_tool`, `run_stdio`).
2. **`smart_drive/mcp/proxy.py`** (90 lines, 3.2 KB):
   - Implements `SmartDriveProxy` for dynamic SSD mount point discovery across macOS (`/Volumes/KINGSTON`), Windows (`D:\` drive letters, `GEMINI.md`/`AGENTS.md` markers), and Linux (`/media`, `/mnt`).
   - Latency benchmarked at <100ms.
3. **`smart_drive/mcp/registrar.py`** (131 lines, 4.2 KB):
   - Multi-IDE configuration registrar that detects and updates JSON configuration files for Google Antigravity 2.0 (`~/.gemini/antigravity/mcp_config.json`), Claude Desktop (`claude_desktop_config.json`), Cursor (`~/.cursor/mcp.json`), Windsurf (`~/.codeium/windsurf/mcp_config.json`), and local workspace `.mcp.json`.
4. **`smart_drive/mcp/__init__.py`** (19 lines, 553 bytes):
   - Exposes public exports (`SmartDriveMCPServer`, `SmartDriveProxy`, `TOOLS`, `PROTOCOL_VERSION`, `SERVER_NAME`, `SERVER_VERSION`, `register_ide_configs`).

---

## 3. Enumeration of All 8 MCP Tools

Below is the complete enumeration of all 8 MCP tools, their parameters, data types, defaults, and schemas defined in `server.py`:

| # | Tool Name | Description Summary | Schema Properties | Required Params | Return Data Type |
|---|---|---|---|---|---|
| 1 | `ssd_search` | Sub-10ms multi-criteria search over 500,000+ files via SQLite FTS5 | `query` (str), `ext` (str), `category` (str), `size` (str), `directory` (str), `limit` (int, default: 25), `offset` (int, default: 0), `compact` (bool, default: true) | None (`[]`) | Dict with `matches`, `total_count`, `returned`, `offset`, `limit`, `has_more`, `next_offset`, `elapsed_ms`, `truncated_to_token_limit` |
| 2 | `ssd_audit` | Storage breakdown & 512KB cluster slack metrics | `sub_dir` (str) | None (`[]`) | Dict with `root_path`, `total_files`, `total_directories`, `total_logical_bytes`, `total_allocated_bytes`, `total_slack_bytes`, `total_slack_percentage`, `taxonomies`, `categories`, `top_slack_directories` |
| 3 | `ssd_clean` | Junk file cleaner (Tier 1 safe OS metadata, Tier 2 dev caches) | `dry_run` (bool, default: true), `apply` (bool, default: false), `sub_dir` (str), `tier` (int, default: 1) | None (`[]`) | Dict with `target_root`, `dry_run`, `tier`, `detected_count`, `purged_count`, `nominal_bytes_reclaimed`, `slack_bytes_reclaimed` |
| 4 | `ssd_find_duplicates` | 3-phase duplicate detection (Size -> 8KB Hash -> SHA-256) | `sub_dir` (str) | None (`[]`) | Dict with duplicate groups, reclaimable bytes, and candidate removal plan |
| 5 | `ssd_update_index` | Incremental mtime/size search index sync into SQLite FTS5 | `directory` (str) | None (`[]`) | Dict with `scanned_files`, `added_files`, `updated_files`, `deleted_files`, `elapsed_seconds` |
| 6 | `ssd_check_safety` | exFAT safety guard (forbidden characters, symlinks, whitelist) | `path` (str) | `["path"]` | Dict with `path`, `is_safe`, `forbidden_character_violations`, `is_protected_root_file`, `is_protected_root_dir` |
| 7 | `ssd_status` | Mount status, anti-indexing shield integrity & taxonomy health | None (`{}`) | None (`[]`) | Dict with `mount`, `anti_indexing_shields`, `taxonomies`, `integrity_status` |
| 8 | `ssd_auto_organize` | Auto-zoning, loose file relocation & anti-slack rebalancing | `apply` (bool, default: false), `clean` (bool, default: false) | None (`[]`) | Dict with `status`, `plan_count`, `actions` (or `result` if applied) |

---

## 4. Boundary Checks & Input Sanitization Vulnerability Audit

Our deep inspection of `server.py` uncovered several critical vulnerabilities and robustness gaps:

### 4.1. Path Traversal & Unbounded Subdirectory Resolution
**Affected Handlers**:
- `handle_ssd_audit` (line 384): `target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root`
- `handle_ssd_clean` (line 405): `target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root`
- `handle_ssd_find_duplicates` (line 430): `target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root`
- `handle_ssd_update_index` (line 436): `target_root = os.path.join(self.root, target_dir) if target_dir else self.root`

**Vulnerability Mechanics**:
1. In Python, if `sub_dir` is an absolute path (e.g. `C:\Windows` or `/etc`), `os.path.join(self.root, sub_dir)` discards `self.root` and resolves to the absolute path!
2. If `sub_dir` contains relative traversal elements (e.g. `../../../../some_dir`), `os.path.join` traverses up past `self.root`.
3. Consequently, an agent calling `ssd_audit(sub_dir="../../../../Users")` or `ssd_find_duplicates(sub_dir="C:\\Users\\Admin\\.ssh")` causes the server to inspect and process directories completely outside the SSD boundary.
4. While `SecurityGuard` in `smart_drive/core/purge_engine.py` protects against unlinking files outside root during `purge_batch`, `JunkDetector` in `ssd_clean` still reads and lists files from `target_root` before `SecurityGuard` is invoked.

**Remediation**:
Implement a centralized, strict validator: `_resolve_safe_path(self, user_path: Optional[str], must_exist: bool = False) -> str`.
The validator must:
- Canonicalize via `os.path.realpath(os.path.abspath(...))`.
- Enforce that the canonical path equals `self.root` or starts with `self.root + os.sep` (handling case-insensitivity on Windows and macOS).
- Reject any path with `\x00` (null bytes).
- Reject any path that resolves outside `self.root` with a clear, safe `ValueError`.

### 4.2. Windows Cross-Drive Mount Crash (`ValueError`)
**Affected Handler**: `handle_ssd_check_safety` (line 448):
```python
rel_path = os.path.relpath(path_str, self.root) if os.path.isabs(path_str) else path_str
```
**Vulnerability Mechanics**:
On Windows, when `path_str` specifies a different drive letter than `self.root` (for instance, `path_str = "C:\\Windows\\System32"` while `self.root = "D:\\teamwork_projects\\smart_drive_os"`), `os.path.relpath` raises:
`ValueError: path is on mount 'C:', start on mount 'D:'`.
This causes an unhandled 500 error in JSON-RPC handling.
**Remediation**:
Wrap path resolution in a `try...except ValueError` block. When cross-mount or outside-root paths are supplied, flag them immediately as unsafe without crashing:
```python
"is_safe": False,
"error": "Path escapes drive root or resides on a different drive mount"
```

### 4.3. String Boolean Coercion Trap for Destructive Actions
**Affected Handlers**:
- `handle_ssd_clean` (line 403):
  ```python
  dry_run = args.get("dry_run", True)
  if "apply" in args:
      dry_run = not bool(args["apply"])
  ```
- `handle_ssd_auto_organize` (line 469):
  ```python
  apply_mode = bool(args.get("apply", False))
  ```

**Vulnerability Mechanics**:
In Python, `bool("false")` evaluates to `True`, and `bool("0")` evaluates to `True`.
If an LLM agent produces JSON `{"apply": "false"}` or `{"apply": "0"}`, Python's `bool()` evaluates this to `True`.
As a direct consequence, `apply_mode` activates and **executes destructive purging or file relocations when the caller explicitly stated false**!
**Remediation**:
Implement `_parse_bool(val: Any, default: bool = False) -> bool`:
Treat strings `"false"`, `"0"`, `"no"`, `"n"`, `"f"`, and `None` as `False`.

### 4.4. SQL / FTS5 Query Parameter Boundary Issues
**Affected Handler**: `handle_ssd_search` (lines 301-331):
- `limit`: `min(int(args.get("limit", 25)), 100)`. If a client passes `limit=-10`, `min(-10, 100)` is `-10`. In SQLite, `LIMIT -10` can trigger unexpected behavior or return all rows.
  - Fix: `max(1, min(int(args.get("limit", 25)), 100))`.
- `offset`: `max(int(args.get("offset", 0)), 0)`. If a client passes non-integer strings (`limit="abc"`), `int()` throws an uncaught `ValueError`.
  - Fix: Implement safe `_parse_int(val, default, min_val, max_val)`.
- `query` length: An unbounded 1MB query string can exhaust CPU in regex parsing.
  - Fix: Bound query length: `query_str = str(args.get("query") or "")[:1000]`.
- `directory` filter: If `directory` contains SQL LIKE wildcards (`%`, `_`), it could match arbitrary path patterns unintentionally. It should be sanitized and validated within root.

### 4.5. Malformed Request Arguments Object
In `handle_request` (lines 538-540):
```python
tool_name = params.get("name")
arguments = params.get("arguments", {})
```
If a client sends `{"arguments": null}` or `{"arguments": "str"}`, `dispatch_tool` attempts `args.get(...)`, raising `AttributeError`.
- Fix: Ensure `arguments` is converted to an empty dict if not `isinstance(arguments, dict)`.

---

## 5. Tool Hint Annotations Audit

The MCP specification (version 2024-11-05) defines behavioral hints for client agents to determine whether an operation is safe, read-only, repeatable, or connects to external environments.

### 5.1. Annotation Verification Matrix

| Tool Name | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` | Status | Rationale |
|---|:---:|:---:|:---:|:---:|:---:|---|
| `ssd_search` | `True` | `False` | `True` | `False` | **VERIFIED** | Queries SQLite index only; leaves filesystem unchanged; idempotent; local-only |
| `ssd_audit` | `True` | `False` | `True` | `False` | **VERIFIED** | Computes statistics and slack; read-only; idempotent; local-only |
| `ssd_clean` | `False` | `True` | `True` | `False` | **VERIFIED** | Can permanently delete junk files; destructive; idempotent; local-only |
| `ssd_find_duplicates` | `True` | `False` | `True` | `False` | **VERIFIED** | Generates duplicate report; does not delete; read-only; idempotent; local-only |
| `ssd_update_index` | `False` | `False` | `True` | `False` | **VERIFIED** | Writes to SQLite index DB; mutates index state but non-destructive to source files; idempotent; local-only |
| `ssd_check_safety` | `True` | `False` | `True` | `False` | **VERIFIED** | Validates filenames against rules; read-only; idempotent; local-only |
| `ssd_status` | `True` | `False` | `True` | `False` | **VERIFIED** | Checks mount and health without mutating; read-only; idempotent; local-only |
| `ssd_auto_organize` | `False` | `True` | `True` | `False` | **VERIFIED** | Moves/relocates loose files into taxonomies; destructive/state-altering; idempotent; local-only |

### 5.2. Schema Location & Client Compatibility
In `smart_drive/mcp/server.py`, all 8 tools declare hints in **two** locations simultaneously:
1. Inside `"annotations": {"readOnlyHint": ..., "destructiveHint": ..., "idempotentHint": ..., "openWorldHint": ...}`.
2. Directly at the tool dictionary root (`"readOnlyHint": ...`, etc.).

This dual-declaration is intentional and beneficial: it provides backwards compatibility with older client implementations while adhering to the official MCP 2024-11-05 schema.

### 5.3. Identified Gap in Test Suite
While the declarations in `server.py` are present and correct, **there is currently zero test coverage verifying them**.
In `tests/test_mcp_server.py`:
```python
def test_eight_standard_tools_cataloged(self) -> None:
    # Only checks name, description, inputSchema, type == 'object'
    # DOES NOT assert readOnlyHint, destructiveHint, idempotentHint, openWorldHint
```
If a future developer accidentally modifies or omits an annotation, the test suite would pass without warning.
**Action Item for Worker**: Add strict assertion tests in `tests/test_mcp_server.py` verifying all 4 boolean hints on all 8 tools.

---

## 6. Pure Standard Library In-Memory Rate Limiter Specification

### 6.1. Architecture & Algorithm Selection
To satisfy the zero-external-dependency invariant, we implement a **Sliding-Window Log Rate Limiter** using only:
- `time.monotonic` (high-resolution, monotonic clock immune to system clock shifts)
- `collections.deque` (O(1) double-ended queue for tracking timestamps)
- `threading.Lock` (thread safety for concurrent client calls)

#### Why Sliding-Window Log over Token-Bucket?
1. **Exact Window Guarantee**: Exactly enforces "no more than $N$ requests in any rolling window of $W$ seconds."
2. **Transparent Burst Allowance**: An agent can issue up to $N$ requests immediately in succession (burst).
3. **Deterministic Reset**: When $W$ seconds pass without calls, the deque naturally empties.
4. **Clean Testing**: By providing an optional `now` argument to `acquire(now=...)`, unit tests can test burst, throttling, and reset deterministically without sleeping.

### 6.2. Component Design & Interface Contract

```python
class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter using Python standard library.
    
    Protects MCP endpoints against accidental flood or DoS attacks.
    """

    def __init__(
        self,
        max_requests: int = 120,
        window_seconds: float = 60.0,
        enabled: bool = True,
    ) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(0.001, float(window_seconds))
        self.enabled = bool(enabled)
        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def acquire(self, now: Optional[float] = None) -> Tuple[bool, float]:
        """Attempts to acquire a slot in the current sliding window.

        Args:
            now: Optional current timestamp (for deterministic testing).

        Returns:
            Tuple of (allowed: bool, retry_after: float).
            If allowed is True, retry_after is 0.0.
            If allowed is False, retry_after is the seconds until the oldest slot frees.
        """
        if not self.enabled:
            return True, 0.0

        current_time = time.monotonic() if now is None else now

        with self._lock:
            # Evict timestamps older than current_time - window_seconds
            cutoff = current_time - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(current_time)
                return True, 0.0

            # Throttled: calculate delay until the oldest recorded request exits the window
            oldest = self._timestamps[0]
            retry_after = max(0.001, (oldest + self.window_seconds) - current_time)
            return False, retry_after

    def reset(self) -> None:
        """Clears all recorded timestamps, resetting the window immediately."""
        with self._lock:
            self._timestamps.clear()

    @property
    def current_load(self) -> int:
        """Returns the number of active requests in the current window."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)
```

### 6.3. Configurability & Environment Variables
The rate limiter must be configurable both via `SmartDriveMCPServer.__init__` parameters and standard environment variables:

| Environment Variable | Type | Default | Description |
|---|---|---|---|
| `SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS` | Integer | `120` | Maximum requests permitted within the rolling window |
| `SMART_DRIVE_MCP_RATE_LIMIT_WINDOW` | Float | `60.0` | Duration of the sliding window in seconds |
| `SMART_DRIVE_MCP_RATE_LIMIT_ENABLED` | Boolean | `true` | Enables or disables rate limiting (`0`/`false` disables) |

In `SmartDriveMCPServer.__init__`:
```python
def __init__(
    self,
    root: Optional[str] = None,
    rate_limit_requests: Optional[int] = None,
    rate_limit_window: Optional[float] = None,
    rate_limit_enabled: Optional[bool] = None,
) -> None:
    ...
    # Rate limiter configuration resolution
    env_req = os.environ.get("SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS")
    req_limit = rate_limit_requests if rate_limit_requests is not None else (
        int(env_req) if env_req and env_req.isdigit() else 120
    )

    env_win = os.environ.get("SMART_DRIVE_MCP_RATE_LIMIT_WINDOW")
    try:
        win_limit = rate_limit_window if rate_limit_window is not None else (
            float(env_win) if env_win else 60.0
        )
    except ValueError:
        win_limit = 60.0

    env_en = os.environ.get("SMART_DRIVE_MCP_RATE_LIMIT_ENABLED", "true").lower()
    enabled = rate_limit_enabled if rate_limit_enabled is not None else (
        env_en not in ("0", "false", "no", "off")
    )

    self.rate_limiter = SlidingWindowRateLimiter(
        max_requests=req_limit,
        window_seconds=win_limit,
        enabled=enabled,
    )
```

### 6.4. JSON-RPC Protocol Hook & Error Response
In `handle_request`:
```python
def handle_request(self, req: Dict[str, Any]) -> None:
    msg_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    # Exclude notifications/initialized (no response expected)
    if method != "notifications/initialized":
        allowed, retry_after = self.rate_limiter.acquire()
        if not allowed:
            logger.warning("MCP rate limit exceeded. Retry after %.2fs", retry_after)
            if msg_id is not None:
                self.send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": (
                            f"Rate limit exceeded: maximum {self.rate_limiter.max_requests} "
                            f"requests per {self.rate_limiter.window_seconds:.1f}s. "
                            f"Retry after {retry_after:.2f}s."
                        ),
                        "data": {
                            "retry_after": round(retry_after, 2),
                            "max_requests": self.rate_limiter.max_requests,
                            "window_seconds": self.rate_limiter.window_seconds,
                        },
                    },
                })
            return
```
This intercepts floods at the door before CPU-intensive file system traversals, SQLite queries, or hash computations are triggered.

---

## 7. Concrete Design Recommendations & Interface Contracts for Worker

### 7.1. Helper Contracts in `server.py`

#### 1. Path Boundary Validator:
```python
def _resolve_safe_path(self, sub_path: Optional[str], must_exist: bool = False) -> str:
    """Validates and resolves sub_path strictly within self.root.
    
    Raises:
        ValueError: If path escapes self.root or contains null bytes.
        FileNotFoundError: If must_exist is True and path does not exist.
    """
```
#### 2. Safe Boolean Coercion:
```python
def _parse_bool(val: Any, default: bool = False) -> bool:
    """Safely coerces strings ('false', '0', 'true') avoiding Python bool('false') == True."""
```
#### 3. Safe Integer Coercion:
```python
def _parse_int(val: Any, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """Safely parses integers with bounds clamping and fallback."""
```

### 7.2. Updates to Tool Handlers

1. **`handle_ssd_audit`**:
   Replace: `target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root`  
   With: `target_root = self._resolve_safe_path(args.get("sub_dir"), must_exist=True)` (catch `ValueError` / `FileNotFoundError` and return `{"error": str(e)}`).
2. **`handle_ssd_clean`**:
   - Resolve `target_root` via `self._resolve_safe_path`.
   - Parse `dry_run` using `_parse_bool(args.get("dry_run"), default=True)`.
   - If `"apply" in args`: `dry_run = not _parse_bool(args["apply"], default=False)`.
   - Parse `tier` using `_parse_int(args.get("tier"), default=1, min_val=1, max_val=3)`.
3. **`handle_ssd_find_duplicates`**:
   - Resolve `target_root` via `self._resolve_safe_path(args.get("sub_dir"), must_exist=True)`.
4. **`handle_ssd_update_index`**:
   - Resolve `target_root` via `self._resolve_safe_path(args.get("directory"), must_exist=True)`.
5. **`handle_ssd_search`**:
   - Sanitize `query` length: `query[:1000]`.
   - Parse `limit` with `_parse_int(args.get("limit"), default=25, min_val=1, max_val=100)`.
   - Parse `offset` with `_parse_int(args.get("offset"), default=0, min_val=0)`.
   - Parse `compact` with `_parse_bool(args.get("compact"), default=True)`.
   - If `directory` is provided, validate with `self._resolve_safe_path(dir_arg)` and extract relative path.
6. **`handle_ssd_check_safety`**:
   - Validate `path` is a non-empty string without null bytes.
   - Handle cross-drive Windows path safely without unhandled `ValueError`.
7. **`handle_ssd_auto_organize`**:
   - Parse `apply_mode = _parse_bool(args.get("apply"), default=False)`.
   - Parse `clean_mode = _parse_bool(args.get("clean"), default=False)`.
   - If `clean_mode` is enabled, chain clean step as advertised in schema.
8. **`handle_request`**:
   - Sanitize `arguments = params.get("arguments", {})` -> ensure it is a dict (`if not isinstance(arguments, dict): arguments = {}`).

---

## 8. Test Verification Plan (R3 Alignment)

In `tests/test_mcp_server.py`, the worker must implement:

1. **`TestMCPRateLimiter`**:
   - **Burst Allowance**: Send `max_requests` in 0s, assert all return `allowed=True`.
   - **Throttle Trigger**: Send `max_requests + 1`, assert returns `allowed=False` with `retry_after > 0`.
   - **Window Reset**: Advance monotonic time by `window_seconds + 0.1` (or call `reset()`), assert new requests succeed.
   - **Thread Safety**: Run 10 concurrent threads hammering `acquire()`, assert no race conditions or deadlock.
   - **Configuration**: Verify `SMART_DRIVE_MCP_RATE_LIMIT_*` env vars and `SmartDriveMCPServer` constructor parameters.
   - **JSON-RPC Protocol Throttling**: Send requests via `server.handle_request`, assert code `-32000` is returned when throttled.
2. **`TestMCPDefensiveHardening`**:
   - Path traversal in `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index` with `../../` returns error dictionary or rejection.
   - String boolean values `{"apply": "false"}` in `ssd_clean` and `ssd_auto_organize` remain in dry-run mode.
   - Cross-drive path in `ssd_check_safety` does not raise `ValueError`.
   - Negative limit (`limit=-5`) in `ssd_search` clamped to `1`.
   - Malformed/non-dict arguments handled gracefully.
3. **`TestMCPToolAnnotations`**:
   - Verify all 8 tools contain `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`.
   - Assert all values are instances of `bool`.
   - Assert exact truth values match the specification matrix.

---
*End of Report.*
