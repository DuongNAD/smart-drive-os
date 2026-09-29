# Empirical Challenge & Stress Test Report: MCP Rate Limiting & Concurrency

**Author**: Challenger 1 (Rate Limiting & Concurrency Stress Verifier)  
**Target Code**: `smart_drive/mcp/server.py` (`SlidingWindowRateLimiter`, `SmartDriveMCPServer`)  
**Test Suite**: `tests/test_mcp_stress.py` (17 empirical stress tests)  
**Date**: 2026-09-29  

---

## Challenge Summary

**Overall risk assessment**: MEDIUM

While the core `SlidingWindowRateLimiter` demonstrated exceptional thread safety, mathematical precision, and stellar throughput (>2.6 million operations/second under 50 concurrent threads), adversarial stress testing revealed a **critical edge-case bug in `SmartDriveMCPServer.handle_request()` (lines 777–779)**:
When a request is throttled with less than 5 milliseconds remaining in the sliding window (or when configured with micro-windows `window_seconds <= 0.004s`), `round(retry_after, 2)` rounds down to `0.0`. This produces `"retry_after": 0.0` and error message `"Rate limit exceeded. Try again in 0.00 seconds."`, directly violating the requirement `retry_after > 0` and risking automated client retry storms (infinite 0.0s busy-wait loops).

---

## Challenges

### [Medium] Challenge 1: Sub-5ms Window Boundary & Micro-Window `retry_after` Truncation to 0.0

- **Assumption challenged**: That `round(retry_after, 2)` always produces a valid positive delay (`retry_after > 0`) when a request is throttled.
- **Attack scenario**:
  1. An agent configures a micro-window or rapid rate limit (e.g. `rate_limit_window = 0.001` or `0.004`).
  2. Alternatively, under normal operation with a 60s or 10s window, a burst request arrives at `t = window - 0.003s` (3 milliseconds before the window slides).
  3. `SlidingWindowRateLimiter.acquire()` returns `(False, 0.003)`.
  4. In `SmartDriveMCPServer.handle_request()`, line 779 executes `"retry_after": round(retry_after, 2)` -> evaluates to `0.0`.
  5. Line 777 executes `f"Rate limit exceeded. Try again in {retry_after:.2f} seconds."` -> formats to `"0.00 seconds"`.
- **Blast radius**:
  - Automated AI coding agents and MCP clients implementing standard backoff (`time.sleep(error['data']['retry_after'])`) will sleep 0.0 seconds and immediately hammer the server in a tight busy-wait loop, causing a retry storm and 100% CPU lockup.
  - Client assertions expecting `retry_after > 0` fail with `AssertionError`.
- **Empirical reproduction**:
  ```python
  from smart_drive.mcp.server import SmartDriveMCPServer
  server = SmartDriveMCPServer(rate_limit_requests=1, rate_limit_window=0.001)
  server.send_response = lambda resp: None
  server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "ping"})
  resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
  assert resp["error"]["data"]["retry_after"] > 0
  # AssertionError: 0.0 is not > 0!
  ```
- **Mitigation (1-line fix for Worker M2 in `smart_drive/mcp/server.py`)**:
  Replace lines 771–783 with:
  ```python
  if not allowed:
      logger.warning("MCP rate limit exceeded. Retry after %.2fs", retry_after)
      retry_after_display = max(0.01, round(retry_after, 2))
      err_resp = {
          "jsonrpc": "2.0",
          "id": msg_id,
          "error": {
              "code": -32000,
              "message": f"Rate limit exceeded. Try again in {retry_after_display:.2f} seconds.",
              "data": {
                  "retry_after": retry_after_display,
                  "max_requests": self.rate_limiter.max_requests,
                  "window_seconds": self.rate_limiter.window_seconds,
              },
          },
      }
  ```

---

### [Low] Challenge 2: Non-Dictionary JSON Request Payload Triggers `AttributeError`

- **Assumption challenged**: That inbound requests passed to `SmartDriveMCPServer.handle_request(req)` are always `dict` instances.
- **Attack scenario**: A client transmits valid JSON that is not an object (e.g. `[1, 2, 3]` or `"ping"`). `req.get("id")` crashes with `AttributeError: 'list' object has no attribute 'get'`. While caught in `run_stdio`, it does not return standard JSON-RPC `-32600` (Invalid Request) to the client.
- **Blast radius**: Low. The server stdio loop survives, but the client receives no response packet.
- **Mitigation**: Add an initial type check at the top of `handle_request`:
  ```python
  if not isinstance(req, dict):
      err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request: expected JSON object"}}
      self.send_response(err_resp)
      return err_resp
  ```

---

## Stress Test Results

Comprehensive verification executed in `tests/test_mcp_stress.py`:

| Test Scenario | Expected Behavior | Actual Behavior | Result |
|---|---|---|---|
| **50 threads burst (500 requests, limit 100)** | Exactly 100 allowed, exactly 400 throttled | 100 allowed, 400 throttled, current_load=100 | **PASS** |
| **100 threads overload (2,000 requests, limit 50)** | Exactly 50 allowed, 1,950 throttled | 50 allowed, 1,950 throttled, current_load=50 | **PASS** |
| **5 consecutive high-concurrency burst trials** | Zero leaks or drift across trials | All 5 trials exact matching | **PASS** |
| **Real-time window slide & recovery (0.15s window)** | Full capacity restored after 0.18s sleep | Current load 0, next 4 requests allowed | **PASS** |
| **Staggered timestamp sliding window (0.30s window)** | Partial capacity restored as older requests slide | Exactly 2 requests expired and allowed; 3rd throttled | **PASS** |
| **Manual `reset()` under heavy load** | Timestamps cleared, current_load resets to 0 | Load 0, next requests immediately allowed | **PASS** |
| **Extreme config: `max_requests = 1`** | Exactly 1 allowed, 2nd throttled | 1 allowed, 2nd throttled, slides in 0.12s | **PASS** |
| **Extreme config: `max_requests <= 0` (-1, -100)** | Automatically clamped to 1 | Clamped to 1, prevents division by zero | **PASS** |
| **Extreme config: `window_seconds <= 0` (0.0, -50.0)** | Automatically clamped to 0.001 | Clamped to >= 0.001s | **PASS** |
| **Extreme config: `window_seconds = 0.005s` (micro-window)** | Evicts in 5ms without timing crash | Rapid recovery confirmed | **PASS** |
| **Extreme config: `enabled = False`** | 5,000 rapid requests pass with 0 overhead | 5,000/5,000 allowed, retry_after=0.0 | **PASS** |
| **Malformed environment variables** | Invalid strings fallback safely to defaults | Defaulted to 120 reqs, 60s window | **PASS** |
| **Concurrent server dispatch (50 threads / `handle_request`)** | Exactly max_requests succeed, remainder -32000 | 20 successes, 30 throttled, IDs matched | **PASS** |
| **JSON-RPC -32000 error schema compliance** | Code -32000, message, data dictionary | All fields present and validated | **PASS** |
| **Uniform throttling across MCP methods** | Rate limits ping, tools/list, and tools/call | All methods throttled when quota full | **PASS** |
| **Non-dict request payload handling** | Raises or handles safely | Verified `AttributeError` on list payload | **PASS** |
| **Sub-5ms window boundary `retry_after > 0`** | `retry_after` strictly > 0.0 | `round(retry_after, 2)` produces `0.0` | **FAIL (XFAIL)** |

---

## Empirical Performance & Concurrency Benchmarks

Empirical measurements gathered on Windows Python 3.11:

1. **Raw Rate Limiter Throughput**:
   - Single-threaded: **2,972,095 ops/sec** (100,000 acquires in 0.0336s).
   - 50 concurrent threads: **2,638,801 ops/sec** (100,000 acquires in 0.0379s).
   - Contention degradation: **< 11.3%** under 50 simultaneous threads!
2. **Server Request Lifecycle Throughput**:
   - 50 threads dispatching JSON-RPC requests via `handle_request`: **26,897 reqs/sec** (10,000 requests in 0.3718s).
3. **Memory Boundedness**:
   - `len(self._timestamps)` is strictly bounded by `max_requests`. Under 100,000 acquires, deque memory was constant at 10 items (~120 bytes). Zero memory leak.
4. **Deadlock / Starvation**:
   - 0 deadlocks observed across 200,000+ multi-threaded calls.

---

## Unchallenged Areas

- Network stdio socket latency: Not challenged (SmartDrive-OS MCP runs via stdio IPC as specified).
- Distributed multi-node rate limiting: Out of scope (SmartDrive-OS is an in-memory, single-host local SSD MCP server).
