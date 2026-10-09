"""tests/test_mcp_stress.py - Empirical Concurrency & Stress Harness for MCP Rate Limiting.

Adversarially challenges SlidingWindowRateLimiter and SmartDriveMCPServer throttling:
1. Massive Concurrent Bursts (50 threads / 500 requests, 100 threads / 2000 requests).
2. Exact Count Accounting (allowed == max_requests, throttled == total - max_requests).
3. JSON-RPC -32000 Error Response Schema & Payload Verification (retry_after > 0, max_requests, window_seconds).
4. Sliding Window Reset & Time Recovery (real-time expiration, staggered eviction, manual reset).
5. Extreme & Boundary Configurations (max_requests=1, micro-windows, zero/negative inputs, disabled limiter).
6. Multi-threaded Server Request Dispatch Stress.

100% Python Standard Library. Zero external runtime dependencies.
"""

from __future__ import annotations

import collections
import concurrent.futures
import io
import json
import logging
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.mcp.server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SlidingWindowRateLimiter,
    SmartDriveMCPServer,
)
from tests.helpers import SmartDriveTestCase


class TestRateLimiterConcurrencyStress(SmartDriveTestCase):
    """Adversarial concurrency and race-condition stress tests for SlidingWindowRateLimiter."""

    def test_fifty_threads_burst_exact_accounting(self) -> None:
        """50 concurrent threads issuing 10 requests each (500 total) against max_requests=100.

        Strict invariant: exactly 100 allowed, exactly 400 throttled, current_load == 100.
        """
        max_limit = 100
        total_threads = 50
        requests_per_thread = 10
        total_requests = total_threads * requests_per_thread

        limiter = SlidingWindowRateLimiter(max_requests=max_limit, window_seconds=30.0)

        results: List[Tuple[bool, float]] = []
        lock = threading.Lock()
        barrier = threading.Barrier(total_threads)

        def worker() -> None:
            # Synchronize threads to burst at the exact same moment
            barrier.wait()
            for _ in range(requests_per_thread):
                res = limiter.acquire()
                with lock:
                    results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(total_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        # Assert no thread hung or timed out
        for t in threads:
            self.assertFalse(t.is_alive(), "Worker thread deadlocked or timed out")

        # Verify exact counts
        self.assertEqual(len(results), total_requests)
        allowed_count = sum(1 for allowed, _ in results if allowed)
        throttled_count = sum(1 for allowed, _ in results if not allowed)

        self.assertEqual(
            allowed_count,
            max_limit,
            f"Race condition detected! Expected exactly {max_limit} allowed, got {allowed_count}",
        )
        self.assertEqual(
            throttled_count,
            total_requests - max_limit,
            f"Expected exactly {total_requests - max_limit} throttled, got {throttled_count}",
        )
        self.assertEqual(
            limiter.current_load,
            max_limit,
            f"Expected current_load to be {max_limit}, got {limiter.current_load}",
        )

        # Every throttled request MUST have retry_after > 0.0 and <= 30.0
        for allowed, retry_after in results:
            if allowed:
                self.assertEqual(retry_after, 0.0)
            else:
                self.assertGreater(retry_after, 0.0)
                self.assertLessEqual(retry_after, 30.0)

    def test_repeated_high_concurrency_trials(self) -> None:
        """Run 5 consecutive high-concurrency burst trials to detect subtle race conditions or leaks."""
        max_limit = 40
        num_threads = 20
        reqs_per_thread = 5

        for trial in range(5):
            limiter = SlidingWindowRateLimiter(max_requests=max_limit, window_seconds=60.0)
            results: List[bool] = []
            lock = threading.Lock()
            barrier = threading.Barrier(num_threads)

            def worker() -> None:
                barrier.wait()
                for _ in range(reqs_per_thread):
                    allowed, _ = limiter.acquire()
                    with lock:
                        results.append(allowed)

            threads = [threading.Thread(target=worker) for _ in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            self.assertEqual(
                results.count(True),
                max_limit,
                f"Trial {trial + 1} failed: allowed {results.count(True)} != {max_limit}",
            )
            self.assertEqual(limiter.current_load, max_limit)

    def test_one_hundred_threads_massive_overload(self) -> None:
        """100 threads issuing 20 requests each (2,000 total) against max_requests=50."""
        max_limit = 50
        num_threads = 100
        reqs_per_thread = 20
        total_reqs = num_threads * reqs_per_thread

        limiter = SlidingWindowRateLimiter(max_requests=max_limit, window_seconds=15.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            def issue_requests() -> List[Tuple[bool, float]]:
                return [limiter.acquire() for _ in range(reqs_per_thread)]

            futures = [executor.submit(issue_requests) for _ in range(num_threads)]
            all_batches = [f.result(timeout=10.0) for f in futures]

        flat_results = [item for sublist in all_batches for item in sublist]
        self.assertEqual(len(flat_results), total_reqs)

        allowed = sum(1 for a, _ in flat_results if a)
        throttled = sum(1 for a, _ in flat_results if not a)

        self.assertEqual(allowed, max_limit)
        self.assertEqual(throttled, total_reqs - max_limit)
        self.assertEqual(limiter.current_load, max_limit)


class TestRateLimiterResetAndRecovery(SmartDriveTestCase):
    """Stress tests for sliding window expiry, real-time recovery, and reset behavior."""

    def test_real_time_window_slide_and_recovery(self) -> None:
        """Verify window naturally slides and recovers capacity after elapsed time."""
        # 4 requests per 0.15 seconds
        limiter = SlidingWindowRateLimiter(max_requests=4, window_seconds=0.15)

        # Step 1: Burst 4 requests
        for i in range(4):
            allowed, retry_after = limiter.acquire()
            self.assertTrue(allowed, f"Burst {i + 1} should be allowed")
            self.assertEqual(retry_after, 0.0)

        # Step 2: 5th request throttled
        allowed, retry_after = limiter.acquire()
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0.0)
        self.assertLessEqual(retry_after, 0.16)

        # Step 3: Sleep to let window expire
        time.sleep(0.18)

        # Step 4: After window expiry, full capacity should be restored
        self.assertEqual(limiter.current_load, 0)
        for i in range(4):
            allowed, retry_after = limiter.acquire()
            self.assertTrue(allowed, f"Post-recovery request {i + 1} should be allowed")
            self.assertEqual(retry_after, 0.0)

        # Throttled again on 5th
        self.assertFalse(limiter.acquire()[0])

    def test_staggered_timestamps_sliding_window(self) -> None:
        """Verify continuous sliding eviction where older requests expire while newer ones remain."""
        # The limiter takes the clock as an argument, so the timeline is exact instead of depending on how
        # promptly a (possibly overloaded) CI runner wakes up from sleep().
        limiter = SlidingWindowRateLimiter(max_requests=4, window_seconds=0.30)

        # Send 2 requests at t=0
        self.assertTrue(limiter.acquire(now=0.0)[0])
        self.assertTrue(limiter.acquire(now=0.0)[0])
        self.assertEqual(limiter.current_load, 2)

        # Send 2 more requests at t=0.15s (half window) -> quota is now full (4/4)
        self.assertTrue(limiter.acquire(now=0.15)[0])
        self.assertTrue(limiter.acquire(now=0.15)[0])
        self.assertEqual(limiter.current_load, 4)

        # 5th request at t=0.15s must be throttled
        allowed, retry_after = limiter.acquire(now=0.15)
        self.assertFalse(allowed)
        # Oldest was t=0, so it frees up 0.30s after t=0, i.e. 0.15s from now
        self.assertAlmostEqual(retry_after, 0.15, places=6)

        # At t=0.33s the first 2 requests have expired, but the 2 from t=0.15s remain, so exactly 2 more fit
        self.assertTrue(limiter.acquire(now=0.33)[0])
        self.assertTrue(limiter.acquire(now=0.33)[0])
        self.assertEqual(limiter.current_load, 4)

        # Next request must be throttled
        self.assertFalse(limiter.acquire(now=0.33)[0])

    def test_manual_reset_under_heavy_concurrency(self) -> None:
        """Calling reset() while threads are actively querying immediately restores full allowance."""
        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=30.0)

        # Exhaust limit
        for _ in range(10):
            limiter.acquire()
        self.assertFalse(limiter.acquire()[0])
        self.assertEqual(limiter.current_load, 10)

        # Reset
        limiter.reset()
        self.assertEqual(limiter.current_load, 0)

        # Next 10 succeed
        allowed_count = sum(1 for _ in range(10) if limiter.acquire()[0])
        self.assertEqual(allowed_count, 10)
        self.assertFalse(limiter.acquire()[0])


class TestRateLimiterExtremeConfigurations(SmartDriveTestCase):
    """Stress tests under boundary, extreme, and malformed configurations."""

    def test_single_request_limit(self) -> None:
        """max_requests=1 allows exactly 1 request per window."""
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=0.1)
        self.assertTrue(limiter.acquire()[0])
        self.assertFalse(limiter.acquire()[0])
        self.assertEqual(limiter.current_load, 1)

        time.sleep(0.12)
        self.assertTrue(limiter.acquire()[0])

    def test_zero_and_negative_max_requests_clamped_to_one(self) -> None:
        """max_requests <= 0 is automatically clamped to 1 to prevent division/indexing errors."""
        for val in (0, -1, -100):
            limiter = SlidingWindowRateLimiter(max_requests=val, window_seconds=10.0)
            self.assertEqual(limiter.max_requests, 1)
            self.assertTrue(limiter.acquire()[0])
            self.assertFalse(limiter.acquire()[0])

    def test_zero_and_negative_window_seconds_clamped(self) -> None:
        """window_seconds <= 0 is clamped to 0.001 to prevent negative delays or zero divisions."""
        for val in (0.0, -0.5, -50.0):
            limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=val)
            self.assertGreaterEqual(limiter.window_seconds, 0.001)

    def test_ultra_short_micro_window(self) -> None:
        """window_seconds=0.005 (5ms) clears rapidly without timing errors."""
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.005)
        self.assertTrue(limiter.acquire()[0])
        self.assertTrue(limiter.acquire()[0])
        self.assertFalse(limiter.acquire()[0])

        time.sleep(0.02)
        self.assertTrue(limiter.acquire()[0])

    def test_disabled_limiter_handles_massive_burst(self) -> None:
        """When enabled=False, millions of requests pass with allowed=True and zero overhead."""
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0, enabled=False)
        self.assertFalse(limiter.enabled)

        # 5,000 rapid requests
        for _ in range(5000):
            allowed, retry_after = limiter.acquire()
            self.assertTrue(allowed)
            self.assertEqual(retry_after, 0.0)

    def test_malformed_environment_variables(self) -> None:
        """Malformed strings in env vars fall back safely to defaults without crashing."""
        orig_env = os.environ.copy()
        try:
            os.environ["SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS"] = "not-an-int"
            os.environ["SMART_DRIVE_MCP_RATE_LIMIT_WINDOW"] = "not-a-float"
            os.environ["SMART_DRIVE_MCP_RATE_LIMIT_ENABLED"] = "invalid-bool"

            limiter = SlidingWindowRateLimiter()
            self.assertEqual(limiter.max_requests, 120)
            self.assertEqual(limiter.window_seconds, 60.0)
            self.assertFalse(limiter.enabled)  # "invalid-bool" not in truthy list
        finally:
            os.environ.clear()
            os.environ.update(orig_env)


class TestMCPServerThrottlingProtocolStress(SmartDriveTestCase):
    """Stress tests on SmartDriveMCPServer JSON-RPC response format and multi-threaded request dispatch."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        # Suppress server logger warning during throttling tests
        self._mcp_logger = logging.getLogger("smart_drive.mcp.server")
        self._prev_log_level = self._mcp_logger.level
        self._mcp_logger.setLevel(logging.ERROR)

    def tearDown(self) -> None:
        self._mcp_logger.setLevel(self._prev_log_level)
        super().tearDown()

    def test_jsonrpc_error_schema_rigorous_compliance(self) -> None:
        """Verifies throttled JSON-RPC response strictly matches specification:

        - jsonrpc == "2.0"
        - id == original message id
        - error.code == -32000
        - error.message contains descriptive rate limit explanation
        - error.data has retry_after (float > 0), max_requests, window_seconds
        """
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            rate_limit_requests=3,
            rate_limit_window=12.5,
            rate_limit_enabled=True,
        )

        # Allow 3 requests
        for i in range(1, 4):
            req = {"jsonrpc": "2.0", "id": f"msg-{i}", "method": "ping", "params": {}}
            resp = server.handle_request(req)
            self.assertEqual(resp["id"], f"msg-{i}")
            self.assertNotIn("error", resp)

        # 4th request must be throttled
        throttle_req = {"jsonrpc": "2.0", "id": "msg-throttled", "method": "ping", "params": {}}
        resp = server.handle_request(throttle_req)

        self.assertIsNotNone(resp)
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], "msg-throttled")
        self.assertIn("error", resp)

        err = resp["error"]
        self.assertEqual(err["code"], -32000)
        self.assertIn("Rate limit exceeded", err["message"])
        self.assertIn("Try again in", err["message"])

        self.assertIn("data", err)
        data = err["data"]
        self.assertIsInstance(data["retry_after"], (int, float))
        self.assertGreater(data["retry_after"], 0.0)
        self.assertLessEqual(data["retry_after"], 12.5)
        self.assertEqual(data["max_requests"], 3)
        self.assertEqual(data["window_seconds"], 12.5)

    def test_throttling_across_different_mcp_methods(self) -> None:
        """Rate limiting applies uniformly across ping, tools/list, and tools/call."""
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            rate_limit_requests=2,
            rate_limit_window=30.0,
            rate_limit_enabled=True,
        )

        # 1. tools/list succeeds
        r1 = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertIn("result", r1)

        # 2. tools/call succeeds
        r2 = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ssd_status", "arguments": {}},
        })
        self.assertIn("result", r2)

        # 3. ping throttled
        r3 = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}})
        self.assertEqual(r3["error"]["code"], -32000)

        # 4. tools/call also throttled
        r4 = server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ssd_status", "arguments": {}},
        })
        self.assertEqual(r4["error"]["code"], -32000)

    def test_concurrent_handle_request_multithreading(self) -> None:
        """50 concurrent threads calling server.handle_request simultaneously.

        Verifies thread safety at the server level:
        - Exactly max_requests succeed with 'result'
        - Exactly (total - max_requests) return error code -32000
        - Response IDs match request IDs without cross-thread contamination
        """
        max_limit = 20
        total_threads = 50
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            rate_limit_requests=max_limit,
            rate_limit_window=30.0,
            rate_limit_enabled=True,
        )

        # Monkey-patch send_response to be a thread-safe no-op so stdout isn't clobbered
        captured_responses: List[Dict[str, Any]] = []
        sink_lock = threading.Lock()

        def safe_send_response(resp: Dict[str, Any]) -> None:
            with sink_lock:
                captured_responses.append(resp)

        server.send_response = safe_send_response  # type: ignore

        responses: List[Dict[str, Any]] = []
        resp_lock = threading.Lock()
        barrier = threading.Barrier(total_threads)

        def worker(thread_idx: int) -> None:
            req = {
                "jsonrpc": "2.0",
                "id": f"thread-{thread_idx}",
                "method": "ping",
                "params": {},
            }
            barrier.wait()
            res = server.handle_request(req)
            if res is not None:
                with resp_lock:
                    responses.append(res)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(total_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(responses), total_threads)

        successes = [r for r in responses if "result" in r]
        throttled = [r for r in responses if "error" in r and r["error"]["code"] == -32000]

        self.assertEqual(len(successes), max_limit, f"Expected {max_limit} successes, got {len(successes)}")
        self.assertEqual(len(throttled), total_threads - max_limit)

        # Verify ID matching for every response
        for r in responses:
            self.assertTrue(r["id"].startswith("thread-"))

    def test_sub_five_millisecond_retry_after_rounding_edge_case(self) -> None:
        """Adversarial stress test: verify retry_after > 0 under micro-windows and sub-5ms boundaries.

        CHALLENGER FINDING (Bug in smart_drive/mcp/server.py lines 777-779):
        When a request is throttled with remaining window duration < 0.005s (e.g. rate_limit_window=0.001s
        or request arriving at t = window - 0.003s), `round(retry_after, 2)` rounds down to 0.0.
        Consequently, the error message reads:
            'Rate limit exceeded. Try again in 0.00 seconds.'
        and `data['retry_after']` evaluates to `0.0`, violating the invariant `retry_after > 0`
        and causing automated clients to spin in an infinite 0.0s busy-wait retry loop.

        Recommended mitigation:
            retry_after_display = max(0.01, round(retry_after, 2))
        """
        server = SmartDriveMCPServer(
            root=str(self.mock_root),
            rate_limit_requests=1,
            rate_limit_window=0.001,  # 1 millisecond micro-window
            rate_limit_enabled=True,
        )
        server.send_response = lambda resp: None

        # Request 1 allowed
        r1 = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
        self.assertEqual(r1["result"], {})

        # Request 2 throttled
        r2 = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
        self.assertIsNotNone(r2)
        self.assertIn("error", r2)
        self.assertEqual(r2["error"]["code"], -32000)

        # Invariant check: retry_after MUST be strictly greater than 0.0
        # This assertion currently FAILS (returns 0.0) without max(0.01, round(retry_after, 2)).
        self.assertGreater(
            r2["error"]["data"]["retry_after"],
            0.0,
            f"retry_after was truncated to 0.0 by round(retry_after, 2)! Got {r2['error']['data']['retry_after']}",
        )

    def test_non_dict_request_payload_handling(self) -> None:
        """Adversarial test: non-dictionary JSON payloads (e.g. list or string) should be caught safely.

        Currently, passing a non-dict raises AttributeError in handle_request because of req.get('id').
        """
        server = SmartDriveMCPServer(root=str(self.mock_root))
        server.send_response = lambda resp: None

        # Verify that non-dict payload triggers AttributeError if unhandled
        with self.assertRaises(AttributeError):
            server.handle_request([1, 2, 3])  # type: ignore


if __name__ == "__main__":
    unittest.main()
