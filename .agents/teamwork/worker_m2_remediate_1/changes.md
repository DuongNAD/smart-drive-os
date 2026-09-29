# Remediation Changes Report — worker_m2_remediate_1

**Timestamp**: 2026-09-29T14:25:30Z  
**Target Files Modified**:
- `smart_drive/mcp/server.py`
- `tests/test_mcp_stress.py`
- `tests/test_mcp_adversarial_challenger2.py`

---

## 1. Summary of Changes

### 1.1 `smart_drive/mcp/server.py`
1. **Sub-5ms Micro-Window Display Rounding Fix**:
   - In `SmartDriveMCPServer.handle_request()` (lines ~772–784), clamped the presentation value of `retry_after` to strictly at least `0.01` seconds using:
     ```python
     retry_after_display = max(0.01, round(retry_after, 2))
     ```
   - Updated both the human-readable error message and the structured `data["retry_after"]` payload to use `retry_after_display`.
   - **Rationale**: Eliminates the 0.00s bug when requests hit the rate limit in the narrow 4.99ms tail of a window or under micro-windows (e.g. 1ms), preventing client busy-wait retry loops and adhering to the invariant `retry_after > 0.0`.

2. **Float Infinity Overflow Fix**:
   - In `SmartDriveMCPServer._parse_int()` (line ~478), updated the exception tuple to catch `OverflowError`:
     ```python
     except (ValueError, TypeError, OverflowError):
         res = default
     ```
   - **Rationale**: Python raises `OverflowError` (which inherits from `ArithmeticError`, not `ValueError`) when attempting `int(float('inf'))` or `int(float('-inf'))`. Catching `OverflowError` ensures graceful fallback to `default` instead of crashing tool calls.

3. **Prefix Relative Traversal Escape Detection**:
   - In `SmartDriveMCPServer.handle_ssd_check_safety()` (lines ~668–688):
     - Normalized path candidates using `os.path.realpath(os.path.abspath(os.path.join(canonical_root, clean_path)))`.
     - Used `os.path.commonpath([canonical_root, normalized_target]) != canonical_root` to robustly detect when relative paths with subfolder prefixes (e.g. `01_AI_Models/../../outside_root.txt`) escape the storage root.
     - Handled cross-drive and UNC paths cleanly by catching `ValueError` from `commonpath` and returning appropriate error dictionaries with `is_safe: False`.
     - Safely derived `rel_path` from `normalized_target` relative to `canonical_root` when inside the root.
   - **Rationale**: Closes the informational auditing bypass in `ssd_check_safety` where relative traversal without leading `..` was evaluated as `is_safe=True`.

### 1.2 `tests/test_mcp_stress.py`
1. **Removed `@unittest.expectedFailure`**:
   - Removed decorator from `test_sub_five_millisecond_retry_after_rounding_edge_case` (line ~479).
   - This test now executes as a standard passing assertion, verifying that `retry_after` is strictly greater than `0.0` under 1ms micro-windows.
2. **Windows Timer Resolution Compensation**:
   - In `test_ultra_short_micro_window`, adjusted sleep from `0.01s` to `0.02s` to ensure reliable expiration across Windows 15.6ms scheduler intervals.

### 1.3 `tests/test_mcp_adversarial_challenger2.py`
1. **Remediation Alignment for Relative Traversal**:
   - In `test_check_safety_relative_traversal_vulnerability`, updated assertion from `self.assertTrue(res.get("is_safe"))` (which documented the vulnerability prior to remediation) to `self.assertFalse(res.get("is_safe"))` and verified `res["error"]` contains `"escapes"`.
2. **Remediation Alignment for Float Infinity Overflow**:
   - In `test_infinity_overflow_in_parse_int`, updated assertion from `self.assertFalse(overflow_handled)` to `self.assertTrue(overflow_handled)` and verified `res == 25`.

---

## 2. Verification Summary

| Suite | Command | Result |
|-------|---------|--------|
| MCP Concurrency & Stress | `python -m pytest tests/test_mcp_stress.py -v` | **17 passed** in 0.86s |
| MCP Adversarial Boundaries | `python -m pytest tests/test_mcp_adversarial_challenger2.py -v` | **13 passed** in 10.31s |
| Combined MCP Tests | `python -m pytest tests/test_mcp_server.py tests/test_mcp_hardening.py tests/test_mcp_stress.py tests/test_mcp_adversarial_challenger2.py -v` | **80 passed** in 2.02s |
| Full Repository Suite | `python -m unittest discover tests` | **523 passed** in 58.10s |
| Full Pytest Repository Suite | `python -m pytest` | **523 passed** in 58.54s |
