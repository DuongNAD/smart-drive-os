# R3 Survey & Investigation Report: Comprehensive Test Verification & Zero-Dependency Invariant

**Agent**: Survey Agent 3 (Testing & Invariant Explorer)  
**Date**: 2026-09-29  
**Target Milestone**: R3: Comprehensive Test Verification & Zero-Dependency Invariant  
**Project Root**: `d:\teamwork_projects\smart_drive_os`  
**Working Directory**: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_r3_1`  

---

## 1. Executive Summary & Core Findings

SmartDrive-OS is an autonomous drive operating suite engineered specifically for external SSDs (exFAT geometry, 512KB cluster size) and internal secondary SSDs. The codebase strictly enforces a **zero external runtime pip dependency** invariant, relying 100% on the Python Standard Library.

### Key Discoveries:
1. **Existing Test Suite Baseline**:
   - The test suite contains **29 test files** in `tests/`, yielding **436 tests** (plus 55 parameterized subtests).
   - All tests pass cleanly (`436 passed in 44.60s` via `pytest`, and compatible with standard library `unittest.TestCase`).
   - The test fixtures in `tests/helpers.py` provide robust reference oracles for cluster slack math, junk detection, and whitelist protection.
2. **Current `tests/test_mcp_server.py` Limitations**:
   - The current MCP test file is minimal: only **9 tests across 171 lines**.
   - It verifies only basic catalog definitions, basic JSON-RPC 2.0 lifecycle (`initialize`, `ping`, `tools/list`), and 3 tool dispatches (`ssd_status`, `ssd_audit`, `ssd_check_safety`).
   - It completely lacks test coverage for rate limiting, input boundary sanitization across all 8 tools, tool hint assertions (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`), and dispatching of the remaining 5 tools.
3. **Privacy Policy Gaps (R1 & R3)**:
   - `PRIVACY.md` does not currently exist in the repository root.
   - Neither `README.md` nor `README_VN.md` link to `PRIVACY.md`.
   - `PRIVACY.md` is currently missing from `PROTECTED_ROOT_FILES` in `smart_drive/core/config.py` and `tests/helpers.py`.
   - Zero automated tests exist to verify privacy document structure, required compliance clauses, or code-level data isolation guarantees.
4. **Rate Limiting Gaps (R2 & R3)**:
   - `smart_drive/mcp/server.py` currently has **no rate limiting mechanism**.
   - No tests exist for burst capacity, sliding-window throttling, token refill / window reset, or multi-threaded concurrency.
5. **Input Sanitization & Boundary Gaps (R2 & R3)**:
   - MCP tools accepting paths (`directory`, `sub_dir`, `path`) do not validate that resolved paths remain strictly within `self.root`, presenting potential path traversal vulnerabilities.
   - Missing boundary tests for integer clamping (`limit` and `offset` in `ssd_search`), tier range validation (`ssd_clean`), and query sanitization.
6. **Zero-Dependency Invariant**:
   - `pyproject.toml` line 45 confirms `dependencies = []`. Only `dev = ["pytest>=7.0"]` exists under `optional-dependencies`.
   - An automated test must be added to permanently lock down this invariant.
7. **SSD Safety Invariants**:
   - 512KB cluster slack protection, whitelist immutability, and exFAT illegal character rules are heavily tested at the core library level (`test_geometry.py`, `test_cleaner.py`, `test_exfat_compat.py`), but are not validated end-to-end through the MCP server interface.

---

## 2. Current Test Suite State & Execution Dynamics

### 2.1 Test Suite Inventory
The repository has 29 test modules in `d:\teamwork_projects\smart_drive_os\tests\`:

| Test Module | Size (Bytes) | Primary Scope / Invariant Verified |
|---|---|---|
| `test_adversarial_filesystem.py` | 25,215 | Extreme cluster sizes (512B-32MB), slack math boundaries, anti-symlink rules |
| `test_adversarial_m3.py` | 30,652 | Corrupted binary headers (GGUF, Safetensors), name collisions, protection guards |
| `test_adversarial_snapshot.py` | 30,030 | Corrupted snapshot DBs, hash tampering, streaming SHA-256 integrity |
| `test_auditor.py` | 6,910 | Storage allocation breakdown, 512KB cluster slack metrics across taxonomies |
| `test_auto_zoner.py` | 6,500 | Drive auto-zoning, rebalancing loose files, plan generation |
| `test_classifier.py` | 34,974 | Extension & magic byte detection, category routing |
| `test_cleaner.py` | 8,496 | 3-tier junk detection, whitelist protection, dry-run vs apply execution |
| `test_cli_e2e.py` | 13,195 | End-to-end CLI commands (`audit`, `clean`, `organize`, `status`) |
| `test_cli_internal_e2e.py` | 20,884 | Internal secondary SSD commands, TRIM queries, junction handling |
| `test_drive_detector.py` | 34,749 | Windows drive letters, mount point discovery, exFAT vs NTFS geometry |
| `test_exfat_compat.py` | 10,314 | 9 Win32 forbidden chars, 22 DOS stems, path normalization, symlink prevention |
| `test_geometry.py` | 8,228 | 512KB cluster math, 0-byte, 1-byte, exact boundary, multi-cluster, gigabyte scale |
| `test_health.py` | 17,671 | TRIM output parsing, SSD health warnings, capacity thresholds |
| `test_indexer.py` | 6,865 | SQLite FTS5 database creation, incremental mtime/size indexing |
| `test_initializer.py` | 7,882 | Standard taxonomy directory tree initialization, shield creation |
| `test_internal_vault.py` | 11,624 | Secondary drive vault creation, configuration tracking |
| `test_junction.py` | 8,496 | Windows NTFS directory junctions (`mklink /J`), dangle protection |
| `test_mcp_proxy.py` | 10,974 | Dynamic mount point discovery (<100ms), multi-IDE MCP configuration registrar |
| `test_mcp_server.py` | 6,494 | JSON-RPC 2.0 stdio server, tool catalog, basic dispatch (9 tests) |
| `test_offloader.py` | 17,116 | Cache offloader from C: to D:, junction linking, safety checks |
| `test_scanner.py` | 7,361 | Filesystem scanner, ignore rules, speed benchmarks |
| `test_search.py` | 8,339 | SQLite FTS5 multi-criteria search, size filters, category filters |
| `test_sentinel.py` | 6,538 | Anti-indexing shield validation (`.metadata_never_index`, `.fseventsd/no_log`) |
| `test_snapshot.py` | 18,867 | Point-in-time snapshot manifests, incremental backups |
| `test_ui.py` | 18,444 | Embedded HTTP web server, REST API endpoints, SPA delivery |
| `test_ui_adversarial.py` | 27,555 | HTTP fuzzing, path traversal, injection payloads, empty DB handling |
| `test_ui_security_m1_2.py` | 22,791 | Inviolable root file protection under HTTP API clean requests |
| `helpers.py` | 13,876 | Authoritative reference oracles, mock drive generators, base test cases |
| `__init__.py` | 72 | Package marker |

### 2.2 Execution Mechanisms
Tests can be executed cleanly using either `pytest` or Python's native `unittest` module:
- **Pytest command**:
  ```bash
  python -m pytest
  ```
  Result: `436 passed, 55 subtests passed in 44.60s`.
- **Targeted Pytest**:
  ```bash
  python -m pytest tests/test_mcp_server.py
  ```
  Result: `9 passed in 0.27s`.
- **Unittest runner (Zero Extra Dependencies)**:
  ```bash
  python -m unittest tests/test_mcp_server.py
  python -m unittest discover -s tests -p "test_*.py"
  ```
  Result: Executes cleanly without any third-party test runners, upholding the 100% standard library principle.

### 2.3 Fixture Architecture in `tests/helpers.py`
All test suites inherit from `SmartDriveTestCase` which provides:
- Isolated temporary workspaces via `tempfile.TemporaryDirectory` with automatic teardown in `tearDown()`.
- `create_mock_drive()`: Generates a complete mock SSD with 6 taxonomies, root control files, anti-indexing shields, realistic models, duplicates, junk files, and cluster boundary test files (0-byte, 1-byte, exact 512KB cluster, boundary+1).
- Mathematical reference oracles:
  - `oracle_cluster_allocation(size, cluster_size)`
  - `oracle_full_sha256(filepath)`
  - `oracle_is_protected(target_path, drive_root)`
  - `oracle_is_junk(target_path)`

---

## 3. Gap Analysis Relative to Requirements

### 3.1 Gap 1: Privacy Policy Structure & Assertions (R1 & R3)
#### Current Status:
- `PRIVACY.md` does not exist in the repository root (`GetFileAttributesEx d:/teamwork_projects/smart_drive_os/PRIVACY.md: The system cannot find the file specified`).
- `README.md` and `README_VN.md` do not contain markdown links to `PRIVACY.md`.
- `smart_drive/core/config.py` line 179 (`PROTECTED_ROOT_FILES`) does not include `"privacy.md"`.
- There are no tests asserting privacy compliance.

#### Required Test Assertions:
1. **File Existence & Placement**:
   - `test_privacy_policy_file_exists`: Asserts `(PROJECT_ROOT / "PRIVACY.md").is_file()` is True.
   - Asserts file size is substantive (> 1,000 bytes / characters).
2. **Core Compliance Clauses**:
   - `test_privacy_policy_sections`: Asserts presence of required compliance headers and topics:
     - Local-Only Operations / Strict Data Isolation (zero cloud uploads, zero remote relays).
     - Zero Telemetry / Analytics (no tracking IDs, no heartbeat beacons, no usage metrics).
     - Zero Personal Data Logging (no PII, credentials, or file payloads persisted in logs).
     - Zero External Network Transmission (no outbound HTTP/HTTPS sockets; only localhost loopback 127.0.0.1 for embedded UI).
     - Storage Architecture & Retention (local exFAT/NTFS volumes only).
     - Directory & Marketplace Standards (OpenAI MCP, Claude MCP, M8ven directory compliance).
3. **Cross-Reference Integrity**:
   - `test_readme_privacy_links`: Asserts `README.md` and `README_VN.md` contain clickable links `PRIVACY.md` (e.g. `[PRIVACY.md](PRIVACY.md)`).
4. **Whitelist Immutability**:
   - `test_privacy_file_inviolable`: Asserts `is_protected_root_file("PRIVACY.md")` returns `True`, preventing accidental deletion during junk cleaning or auto-zoning.
5. **Static Code Telemetry Audit**:
   - `test_no_external_network_libraries`: Scans all Python files in `smart_drive/` to verify absence of external networking libraries (`requests`, `urllib3`, `aiohttp`, `httpx`, `telemetry`, `posthog`, `sentry_sdk`) and outbound `urllib.request.urlopen`.

---

### 3.2 Gap 2: MCP Server In-Memory Rate Limiting (R2 & R3)
#### Current Status:
- `smart_drive/mcp/server.py` contains `SmartDriveMCPServer` which directly dispatches tools without any rate limiting or throttling mechanism.
- No rate limiting classes exist in `smart_drive/mcp/` or `smart_drive/core/`.

#### Required Behavior & Test Coverage:
1. **Burst Allowance**:
   - An agent sending a legitimate burst of requests (e.g., up to 10 or 30 requests within a second/minute) must not be throttled.
   - Test `test_rate_limiter_burst_allowance`: Send $N$ rapid requests (where $N = \text{burst limit}$); all $N$ requests return success (`allowed=True`).
2. **Throttle Trigger**:
   - Request $N+1$ exceeding the burst capacity within the active sliding window or token bucket must be blocked (`allowed=False` or raises `RateLimitExceededError`).
   - Test `test_rate_limiter_throttle_trigger`: Send $N+1$ requests; request $N+1$ is rejected with a rate limit message and retry-after estimate.
3. **Window Reset / Token Replenishment**:
   - After the rate limit window expires (or tokens replenish over time), subsequent requests must be permitted again.
   - Test `test_rate_limiter_window_reset`: Verify that advancing time (using mock `time.monotonic` or short window) allows new requests once the window resets.
4. **Thread Safety & Concurrency**:
   - MCP servers can handle concurrent calls or background worker tasks. The rate limiter must use `threading.Lock` to prevent race conditions during token decrement.
   - Test `test_rate_limiter_concurrency`: Launch 10 threads each issuing requests; total allowed requests must not exceed the configured burst ceiling.
5. **Configuration Flexibility**:
   - Rate limiting must be configurable via constructor parameters (`rate_limit_per_minute`, `max_burst`) or environment variables (`SMART_DRIVE_MCP_RATE_LIMIT`), and allow complete disabling when set to `0` or `None`.
   - Test `test_rate_limiter_disabled_mode`: When disabled, arbitrary high volumes of requests pass without restriction.
6. **JSON-RPC Protocol Integration**:
   - In `SmartDriveMCPServer.handle_request`, when a `tools/call` request is throttled:
     - Returns a clean JSON-RPC response with `isError: True` and informative message: `"Rate limit exceeded. Please wait before retrying."` or standard JSON-RPC error.
     - Crucially, non-tool protocol requests (`initialize`, `ping`, `tools/list`) should remain unthrottled or have higher thresholds so the connection is not dropped.

---

### 3.3 Gap 3: Input Sanitization Boundaries Across All 8 MCP Tools (R2 & R3)
#### Current Status:
In `smart_drive/mcp/server.py`:
- `handle_ssd_search`: Does not clamp negative `limit` (`int(args.get("limit", 25))` allows negative integers if passed), does not clamp negative `offset`, does not sanitize `directory` against path traversal escaping `self.root`.
- `handle_ssd_audit`: Uses `os.path.join(self.root, sub_dir)` without verifying if `sub_dir` is an absolute path or contains `..` traversal escaping `self.root`.
- `handle_ssd_clean`: Same path traversal issue on `sub_dir`/`directory`; tier is not clamped to valid `[1, 3]`.
- `handle_ssd_find_duplicates`: Same path traversal issue on `sub_dir`.
- `handle_ssd_update_index`: Same path traversal issue on `directory`.
- `handle_ssd_check_safety`: Does not validate DOS reserved device names or symlinks in the MCP layer.
- `handle_ssd_status`: Does not test unexpected arguments.
- `handle_ssd_auto_organize`: Does not sanitize non-boolean types.
- Catalog Annotations: `TOOLS` contains hints, but no tests assert that all 8 tools declare `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint` in both `annotations` and top-level schema.

#### Required Boundary Test Matrix (All 8 MCP Tools):

| Tool | Argument | Adversarial Input / Boundary Case | Expected Safe Behavior |
|---|---|---|---|
| **ssd_search** | `limit` | `-1`, `0`, `99999`, `"abc"` | Clamp to `[1, 100]`, fallback to default 25 on invalid string |
| | `offset` | `-50`, `"invalid"` | Clamp to `0`, fallback to default 0 |
| | `directory` | `../../../../Windows`, `C:\` | Reject traversal or constrain strictly within `self.root` |
| | `query` | Unmatched quote (`"llama`), SQL injection (`' OR 1=1--`), FTS syntax (`AND NOT *`) | Sanitize cleanly; never raise SQLite `OperationalError` |
| | `size` | Malformed specs (`>>10MB`, `xyz`, `<=0`) | Handle gracefully without crashing; return empty or clean error |
| | `ext` | Leading dots, extra spaces (`.py,  ,, .gguf`) | Strip leading dots and empty entries cleanly |
| **ssd_audit** | `sub_dir` | `../../../../`, `..\\..\\Windows`, `/etc/passwd`, `C:\` | Block path traversal escaping `self.root`; return error dict |
| | `sub_dir` | Non-existent path (`non_existent_folder_xyz`) | Return safe error report or zero files without crashing |
| | `sub_dir` | Non-string type (`123`, `['a']`, `True`) | Convert or reject safely |
| **ssd_clean** | `sub_dir` | Path traversal (`../../`) | Block traversal; prevent purging files outside root |
| | `tier` | `-1`, `0`, `4`, `99`, `"super_clean"` | Clamp to valid range `[1, 3]`; default to Tier 1 SAFE |
| | `apply` | `None`, `"false"`, `0` | Default to `dry_run=True`; require explicit boolean `apply=True` |
| | Whitelist | Protected files (`GEMINI.md`, `README.md`, `PRIVACY.md`) | `PurgeEngine` and `SecurityGuard` block deletion with error |
| **ssd_find_duplicates** | `sub_dir` | Path traversal (`../../../`) | Block traversal; constrain within `self.root` |
| | `sub_dir` | Non-string type | Reject safely |
| **ssd_update_index** | `directory` | Path traversal (`../../`) | Block traversal outside `self.root` |
| | `directory` | Inaccessible or non-existent path | Return descriptive error without crashing server |
| **ssd_check_safety** | `path` | Empty string `""`, `None`, missing key | Return `{"error": "Missing required argument 'path'"}` |
| | `path` | Forbidden Win32 chars (`test:file*name?.txt`) | `is_safe=False`, violations returned |
| | `path` | DOS reserved stems (`CON.txt`, `aux.py`, `NUL`) | Flag as unsafe or reserved stem violation |
| | `path` | Trailing spaces/dots (`name.txt `, `data...`) | Flag as unsafe |
| | `path` | Null byte injection (`clean.txt\x00malicious`) | Reject safely |
| | `path` | Long path (> 4096 chars) | Handle gracefully without buffer overflow |
| **ssd_status** | `args` | Unexpected arguments (`{"unknown_key": "val"}`) | Safely ignored; returns normal status |
| | Environment | Drive root temporarily unmounted | Returns clean degraded status rather than unhandled exception |
| **ssd_auto_organize** | `apply` / `clean` | String inputs (`"yes"`, `"1"`, `"true"`) | Parse safely to boolean; default to simulation (`dry_run`) |
| | Whitelist | Loose protected files in root | Never move or rename protected root manifests |

---

## 4. Hard Invariant Verification: Zero Runtime Dependencies

### 4.1 Specification Verification
The `pyproject.toml` file at `d:\teamwork_projects\smart_drive_os\pyproject.toml` was directly inspected:
- Line 45: `dependencies = []`
- Line 47-50:
  ```toml
  [project.optional-dependencies]
  dev = [
      "pytest>=7.0",
  ]
  ```
- **Zero runtime dependencies** is verified.

### 4.2 Standard Library Verification
All modules in `smart_drive/` utilize standard library components:
- System & OS: `os`, `sys`, `pathlib`, `platform`, `subprocess`, `shutil`, `tempfile`
- Data & Types: `dataclasses`, `typing`, `enum`, `collections`, `struct`, `json`
- Storage & Math: `sqlite3`, `hashlib`, `math`, `fnmatch`
- Networking & Protocol: `http.server`, `urllib.parse`
- Concurrency & Time: `threading`, `time`
- CLI & Logging: `argparse`, `logging`

### 4.3 Automated Invariant Test Specification
To ensure zero regressions occur during development, a dedicated test case must be added:
```python
class TestZeroDependencyInvariant(unittest.TestCase):
    def test_pyproject_runtime_dependencies_are_empty(self) -> None:
        """pyproject.toml must have dependencies = []."""
        pyproject_path = PROJECT_ROOT / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        # Ensure dependencies = [] exists
        self.assertRegex(content, r"dependencies\s*=\s*\[\s*\]")

    def test_no_external_runtime_imports(self) -> None:
        """Every import across smart_drive must be in the Python Standard Library."""
        import ast
        import sys
        
        stdlib_modules = getattr(sys, "stdlib_module_names", set())
        smart_drive_dir = PROJECT_ROOT / "smart_drive"
        
        for py_file in smart_drive_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top_pkg = alias.name.split(".")[0]
                        if top_pkg != "smart_drive":
                            self.assertIn(top_pkg, stdlib_modules, f"Non-stdlib import '{top_pkg}' in {py_file}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        top_pkg = node.module.split(".")[0]
                        if top_pkg != "smart_drive":
                            self.assertIn(top_pkg, stdlib_modules, f"Non-stdlib import '{top_pkg}' in {py_file}")
```

---

## 5. SSD Safety Invariants & Existing Test Coverage

### 5.1 Invariant 1: 512KB Cluster Slack Protection
- **Hardware Physics**: External SSDs formatted as exFAT default to 524,288 bytes per allocation unit.
- **Formulas**:
  $$\text{Allocated} = \begin{cases} 0 & \text{if size} = 0 \\ \left\lceil \frac{\text{size}}{524288} \right\rceil \times 524288 & \text{if size} > 0 \end{cases}$$
  $$\text{Slack} = \text{Allocated} - \text{size}$$
  $$\text{Slack Ratio} = \frac{\text{Slack}}{\text{Allocated}}$$
- **Existing Coverage in `tests/test_geometry.py`**:
  - `test_cluster_size_bytes_equals_512kb`: Confirms `524_288`.
  - `test_zero_byte_file`: Confirms 0 bytes allocated, 0 slack.
  - `test_one_byte_file`: Confirms 524,288 allocated, 524,287 slack (99.9998%).
  - `test_exact_cluster_boundary`: Confirms 524,288 bytes -> 0 slack.
  - `test_boundary_plus_one_byte`: Confirms 524,289 bytes -> 1,048,576 allocated.
  - `test_large_model_file_allocation`: Tests 4.37GB model allocations.
  - `test_negative_nominal_size_raises_value_error`: Exception handling.
- **Assessment**: Thoroughly tested. No regression risk if existing test suite passes.

### 5.2 Invariant 2: Whitelist Immutability
- **Protection Scope**:
  - Root manifests: `GEMINI.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `.mcp.json`.
  - Root launchers: `Setup_Win.bat`, `Setup_Mac.command`, `check_ssd_status.ps1`, `sync_repos.ps1`, `clean_mac_junk.bat`, etc.
  - Anti-indexing shields: `.metadata_never_index`, `.fseventsd/no_log`.
  - 6 Standard Taxonomies: `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects`, `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage`.
- **Existing Coverage**:
  - `tests/test_cleaner.py`: `TestInviolableWhitelistGuards` tests `SecurityGuard.is_protected` and `validate_deletion` raising `SecurityViolationError`.
  - `tests/test_ui_security_m1_2.py`: Tests clean endpoint cannot delete protected files even with direct path parameters.
  - `tests/test_adversarial_m3.py`: Tests classifier cannot relocate protected files under `--apply`.
- **Identified Gap**:
  - `PRIVACY.md` must be added to `smart_drive/core/config.py` `PROTECTED_ROOT_FILES` and `tests/helpers.py` `INVIOLABLE_ROOT_FILES`.
  - MCP tool `ssd_clean` must have a specific test verifying that passing `sub_dir: "PRIVACY.md"` or targeting `PRIVACY.md` is strictly blocked.

### 5.3 Invariant 3: exFAT Illegal Characters & Win32 Compatibility
- **Rules**:
  - 9 forbidden Win32 chars: `\`, `/`, `:`, `*`, `?`, `"`, `<`, `>`, `|`.
  - 22 Windows 16-bit DOS reserved device stems: `CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9` (case-insensitive, with any extension).
  - Trailing spaces and trailing dots forbidden.
  - ASCII control characters (0x00 - 0x1F) forbidden.
  - Symlinks strictly prohibited on exFAT.
- **Existing Coverage in `tests/test_exfat_compat.py`**:
  - `test_all_nine_forbidden_chars_individually_detected`: Tested.
  - `test_all_22_dos_device_stems_cataloged`: Tested.
  - `test_trailing_space_and_dot_detected`: Tested.
  - `test_sanitize_forbidden_chars_replaced_with_underscore`: Tested.
  - `test_sanitize_idempotency`: Tested.
  - `test_is_symlink_regular_file_returns_false`: Tested.
- **Identified Gap in MCP Server**:
  - In `smart_drive/mcp/server.py`, `handle_ssd_check_safety` only checks `ExFatEngine.audit_forbidden_characters` on `os.path.basename(path_str)`.
  - It does NOT check DOS reserved device stems or symlinks.
  - Dedicated tests in the MCP test suite must verify `ssd_check_safety` with DOS device names, symlinks, and directory traversal paths.

---

## 6. Comprehensive Recommendations & Test Implementation Blueprint

To achieve 100% test verification and top-tier directory trust compliance, we recommend structuring the new tests either by expanding `tests/test_mcp_server.py` or providing a companion test module `tests/test_mcp_hardening.py`. Expanding `tests/test_mcp_server.py` keeps all MCP server testing cohesive.

### Proposed Test Hierarchy

```text
tests/test_mcp_server.py
├── TestMCPToolsCatalog (Existing + Expanded)
│   ├── test_eight_standard_tools_cataloged (Existing)
│   ├── test_all_tools_declare_standard_hint_annotations (NEW: R2/R3)
│   │   └── Verifies readOnlyHint, destructiveHint, idempotentHint, openWorldHint
│   └── test_tools_schema_properties_and_types (NEW: R2/R3)
│
├── TestMCPToolDispatching (Existing + Expanded to all 8 tools)
│   ├── test_dispatch_ssd_status (Existing)
│   ├── test_dispatch_ssd_audit (Existing)
│   ├── test_dispatch_ssd_check_safety (Existing)
│   ├── test_dispatch_ssd_search (NEW)
│   ├── test_dispatch_ssd_clean (NEW)
│   ├── test_dispatch_ssd_find_duplicates (NEW)
│   ├── test_dispatch_ssd_update_index (NEW)
│   ├── test_dispatch_ssd_auto_organize (NEW)
│   └── test_dispatch_unknown_tool_raises_value_error (Existing)
│
├── TestMCPRateLimiter (NEW: R2/R3)
│   ├── test_burst_allowance_permits_rapid_calls
│   ├── test_throttle_trigger_blocks_excess_calls
│   ├── test_window_reset_restores_allowance
│   ├── test_concurrent_requests_thread_safe
│   ├── test_configurable_limits_via_args_or_env
│   └── test_disabled_rate_limiter_allows_all
│
├── TestMCPInputSanitizationBoundaries (NEW: R2/R3 - All 8 Tools)
│   ├── test_ssd_search_path_traversal_blocked
│   ├── test_ssd_search_limit_and_offset_clamped
│   ├── test_ssd_search_fts_injection_resilience
│   ├── test_ssd_audit_path_traversal_blocked
│   ├── test_ssd_audit_invalid_types_handled
│   ├── test_ssd_clean_path_traversal_blocked
│   ├── test_ssd_clean_tier_clamping_and_default_dry_run
│   ├── test_ssd_clean_inviolable_whitelist_enforced
│   ├── test_ssd_find_duplicates_path_traversal_blocked
│   ├── test_ssd_update_index_path_traversal_blocked
│   ├── test_ssd_check_safety_dos_reserved_names
│   ├── test_ssd_check_safety_null_bytes_and_boundaries
│   ├── test_ssd_status_robust_against_unexpected_params
│   └── test_ssd_auto_organize_type_coercion_and_safeguards
│
├── TestPrivacyPolicyAndCompliance (NEW: R1/R3)
│   ├── test_privacy_md_exists_and_substantive
│   ├── test_privacy_md_required_sections
│   ├── test_readme_privacy_links_clickable
│   ├── test_readme_vn_privacy_links_clickable
│   ├── test_privacy_md_inviolable_in_whitelist
│   └── test_pyproject_marketplace_metadata
│
├── TestZeroDependencyInvariant (NEW: R3)
│   ├── test_pyproject_dependencies_strictly_empty
│   └── test_codebase_imports_are_100_percent_stdlib
│
└── TestMCPProtocolJSONRPC (Existing + Expanded)
    ├── test_protocol_initialize (Existing)
    ├── test_protocol_ping (Existing)
    ├── test_protocol_tools_list (Existing)
    ├── test_protocol_unhandled_method_returns_error_32601 (Existing)
    ├── test_protocol_tools_call_success (NEW)
    ├── test_protocol_tools_call_rate_limited (NEW)
    └── test_protocol_content_length_mode (NEW)
```

### Verification Commands
1. **Targeted MCP & Compliance Suite**:
   ```bash
   python -m pytest tests/test_mcp_server.py -v
   ```
2. **Complete Zero-Regression Test Run**:
   ```bash
   python -m pytest -q
   ```
   (Must pass 100% of tests: > 460 tests including new R3 tests).
3. **Pure Standard Library Unittest Verification**:
   ```bash
   python -m unittest discover -s tests -p "test_*.py"
   ```
   (Validates execution with zero pip test runners).

---

## 7. Synthesis & Handoff Summary
All investigation objectives have been fully explored:
- Baseline test suite is robust (436 tests, 100% pass rate).
- Zero-dependency invariant confirmed in `pyproject.toml` (`dependencies = []`).
- Gaps in Privacy Policy assertions, Rate Limiting, and Input Sanitization boundaries are mapped to concrete test specifications.
- SSD safety invariants (512KB slack, whitelist, exFAT rules) are verified and integration requirements identified.
- Detailed implementation recommendations are documented for Worker agents.
