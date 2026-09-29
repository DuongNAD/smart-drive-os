# Project: SmartDrive-OS Comprehensive Trust & Defensive Hardening

## Architecture
- **Zero-Dependency Core**: 100% Python Standard Library. Zero runtime pip dependencies (`dependencies = []`).
- **Directory Compliance Layer**: `PRIVACY.md`, `README.md`, `README_VN.md`, `pyproject.toml`, and root whitelist in `smart_drive/core/config.py`.
- **MCP Defensive Hardening Layer**: `smart_drive/mcp/server.py` introducing thread-safe `SlidingWindowRateLimiter`, input sanitizers (`_resolve_safe_path`, `_parse_bool`, `_parse_int`), Windows cross-drive safety, and 8 hardened MCP tools.
- **Verification Layer**: Comprehensive test suites (`tests/test_compliance.py`, `tests/test_mcp_hardening.py`, `tests/test_mcp_server.py`, `tests/test_mcp_stress.py`, `tests/test_mcp_adversarial_challenger2.py`) with 523 tests passing cleanly (100% pass rate) under both `pytest` and native standard library `unittest`.

## Feature Inventory
Every feature from the Survey phase appears here with its assigned milestone.
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | PRIVACY.md Creation | 100% local-only storage, zero telemetry, zero PII logging, air-gapped security | M1 | survey_r1 |
| 2 | README Privacy References | Bilingual documentation updates (README.md, README_VN.md) with privacy shields and sections | M1 | survey_r1 |
| 3 | pyproject.toml Marketplace Metadata | Homepage, documentation, repo, issues, changelog, author email, 22 keywords, 25 classifiers | M1 | survey_r1 |
| 4 | Protected Root Whitelist | Add privacy.md to PROTECTED_ROOT_FILES in smart_drive/core/config.py | M1 | survey_r1 |
| 5 | Sliding-Window Rate Limiter | Pure stdlib (deque, Lock, monotonic) rate limiter with burst, throttle, window reset | M2 | survey_r2 |
| 6 | Rate Limiter Configuration | Configurable via params and SMART_DRIVE_MCP_RATE_LIMIT_* env vars, throttle error -32000 | M2 | survey_r2 |
| 7 | Path Traversal Sanitization | _resolve_safe_path preventing root escape, null bytes across ssd_audit, clean, duplicates, index, search | M2 | survey_r2 |
| 8 | Parameter & Type Sanitization | _parse_bool (handling 'false'/'0'), _parse_int clamping, dict arguments validation | M2 | survey_r2 |
| 9 | Cross-Drive & OS Boundary Safety | Windows cross-drive ValueError handling in ssd_check_safety | M2 | survey_r2 |
| 10 | MCP Tool Hint Annotations | Ensure and verify readOnlyHint, destructiveHint, idempotentHint, openWorldHint on all 8 tools | M2 | survey_r2 |
| 11 | Compliance & Privacy Tests | Automated test suite for PRIVACY.md existence, contents, links, whitelist protection | M3 | survey_r3 |
| 12 | Rate Limiting Test Suite | Unit tests for burst allowance, throttle trigger, window reset, concurrency, env vars | M3 | survey_r3 |
| 13 | Boundary & Sanitization Tests | Adversarial tests for traversal, null bytes, bool trap, negative limits across all 8 tools | M3 | survey_r3 |
| 14 | Tool Annotations Test Suite | Assertions for all 4 hint annotations on all 8 tools | M3 | survey_r3 |
| 15 | Zero-Dependency & SSD Invariants | Automated verification of dependencies = [] and 100% test pass (523 tests) | M3 | survey_r3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Directory & Marketplace Compliance | PRIVACY.md, README.md, README_VN.md, pyproject.toml, smart_drive/core/config.py | none | DONE |
| M2 | MCP Defensive Hardening & Rate Limiter | smart_drive/mcp/server.py (SlidingWindowRateLimiter, sanitizers, 8 tools) | none | DONE |
| M3 | Comprehensive Test Verification & Invariants | tests/test_compliance.py, tests/test_mcp_hardening.py, 523 tests pass | M1, M2 | DONE |

## Interface Contracts
### SlidingWindowRateLimiter
- `class SlidingWindowRateLimiter(max_requests: int = 120, window_seconds: float = 60.0, enabled: bool = True)`
- `acquire(now: Optional[float] = None) -> Tuple[bool, float]`: Returns `(allowed, retry_after)`.
- `reset() -> None`: Clears timestamp history.
- `current_load -> int`: Returns active requests in window (supports property and callable).

### Sanitization Helpers
- `_resolve_safe_path(self, sub_path: Optional[str], must_exist: bool = False) -> str`: Resolves canonical path, verifies containment within `self.root`, rejects null bytes and directory escapes.
- `_parse_bool(val: Any, default: bool = False) -> bool`: Safe boolean parsing preventing `"false"` or `"0"` being truthy.
- `_parse_int(val: Any, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int`: Safe integer parsing and bounds clamping with `OverflowError` handling.

### Tool Hint Annotations
All 8 MCP tools declare:
- `readOnlyHint`: bool
- `destructiveHint`: bool
- `idempotentHint`: bool (True for all)
- `openWorldHint`: bool (False for all)

## Gate Result
- Gate Status: **PASS** (Reviewer 1 APPROVE, Reviewer 2 APPROVE, Challenger 1 v2 APPROVE, Challenger 2 APPROVE, Forensic Auditor CLEAN).
- Total Tests: **523 passed** across all 32 test modules.
- Runtime Dependencies: strictly `dependencies = []` (100% Python Standard Library).
