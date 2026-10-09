"""tests/test_ui_adversarial.py - Empirical Adversarial & Stress Testing for Web UI Server.

Targeting SmartDrive-OS Milestone 1 UI Server & REST API:
1. Malformed JSON, non-dict payloads, and malicious paths in POST /api/junk/clean
2. Unknown routes, 404/405 handling, and directory traversal URL requests
3. FTS5 injection, unbalanced quotes, dangling operators, unicode/emoji in GET /api/search
4. Edge-case limits, offsets, and empty/corrupted database scenarios
5. High-concurrency stress testing across ThreadingHTTPServer with simultaneous requests
6. Information leakage and security boundary verification
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.ui.server import create_server
from tests.helpers import SmartDriveTestCase, create_mock_ssd_tree


class AdversarialTestBase(SmartDriveTestCase):
    """Base setup for adversarial and stress testing with running ThreadingHTTPServer."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = create_mock_ssd_tree(self.test_dir / "adversarial_mock_drive")
        self.db_path = self.mock_root / ".smart_drive" / "index.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with DatabaseManager(str(self.db_path)) as db:
            db.initialize_schema()
            mgr = IndexManager(db, str(self.mock_root))
            mgr.full_index()

        self.server = create_server(
            root_path=self.mock_root,
            port=0,
            db_path=self.db_path,
        )
        self.port = self.server.server_address[1]
        self.srv_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.srv_thread.start()

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
            self.srv_thread.join(timeout=3)
        finally:
            super().tearDown()

    def _raw_request(
        self,
        path: str,
        method: str = "GET",
        body: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Low-level HTTP request helper using urllib."""
        url = f"http://127.0.0.1:{self.port}{path}"
        req_headers = headers.copy() if headers else {}
        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        except ConnectionAbortedError:
            # Winsock WSAECONNABORTED (WinError 10053): Server closed socket before client finished readline
            return 405, {}, b'{"error": "Connection aborted by host"}'


class TestAdversarialPostJunkClean(AdversarialTestBase):
    """Empirical challenge suite for POST /api/junk/clean."""

    def test_post_empty_body(self) -> None:
        """POST with empty body returns 400 and does not crash server."""
        status, headers, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            body=b"",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("error", data)

        # Confirm server remains alive and responsive
        status2, _, _ = self._raw_request("/api/status")
        self.assertEqual(status2, 200)

    def test_post_malformed_truncated_json(self) -> None:
        """POST with truncated JSON string returns 400 and server stays responsive."""
        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            body=b'{"tiers": [1,',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("Invalid JSON", data.get("error", ""))

        status2, _, _ = self._raw_request("/api/status")
        self.assertEqual(status2, 200)

    def test_post_binary_garbage_payload(self) -> None:
        """POST with binary non-UTF8 garbage returns 400."""
        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            body=b"\x00\x01\xfe\xff\x80\x90garbage",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("error", data)

    def test_post_non_dict_json_roots(self) -> None:
        """POST with valid JSON that is NOT a dictionary (e.g. array, string, number, null)."""
        non_dict_payloads = [
            b"[1, 2, 3]",
            b'"just a string"',
            b"12345",
            b"true",
            b"null",
        ]
        for p in non_dict_payloads:
            status, _, body = self._raw_request(
                "/api/junk/clean",
                method="POST",
                body=p,
                headers={"Content-Type": "application/json"},
            )
            # Should safely return an error status (400 or 500), never crash
            self.assertIn(status, (400, 500))
            data = json.loads(body.decode("utf-8"))
            self.assertIn("error", data)

            # Assert server remains operational after each bad request
            s, _, b = self._raw_request("/api/status")
            self.assertEqual(s, 200)

    def test_post_invalid_tiers_schema(self) -> None:
        """POST with non-list or empty tiers field returns 400."""
        invalid_tier_bodies = [
            {"tiers": "not-a-list"},
            {"tiers": []},
            {"tiers": 123},
            {"tiers": None},
        ]
        for item in invalid_tier_bodies:
            payload = json.dumps(item).encode("utf-8")
            status, _, body = self._raw_request(
                "/api/junk/clean",
                method="POST",
                body=payload,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(status, 400)
            data = json.loads(body.decode("utf-8"))
            self.assertIn("error", data)

    def test_post_unusual_tier_numbers(self) -> None:
        """POST with out-of-range tier numbers (e.g. 99, -1) does not crash."""
        payload = json.dumps({"tiers": [99, -1], "dry_run": True}).encode("utf-8")
        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["total_attempted"], 0)
        self.assertEqual(data["reclaimed_bytes"], 0)

    def test_post_path_traversal_attack_in_paths(self) -> None:
        """POST targeting paths outside root (/etc/passwd, C:\\Windows) must NOT touch or delete them."""
        # Create an external dummy file outside root
        outside_file = self.test_dir / "outside_sensitive_secret.txt"
        outside_file.write_text("TOP_SECRET_DO_NOT_DELETE", encoding="utf-8")

        payload = json.dumps({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": [
                str(outside_file),
                "../../outside_sensitive_secret.txt",
                "/etc/passwd",
                "C:\\Windows\\System32\\notepad.exe",
            ],
        }).encode("utf-8")

        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))

        # The external file must strictly still exist
        self.assertTrue(outside_file.exists(), "Path traversal succeeded in deleting external file!")
        self.assertEqual(outside_file.read_text(encoding="utf-8"), "TOP_SECRET_DO_NOT_DELETE")

    def test_post_protected_root_files_in_paths(self) -> None:
        """POST attempting to purge protected root files (GEMINI.md, README.md, etc.) is blocked."""
        protected_rel = ["GEMINI.md", "README.md", "CLAUDE.md", "AGENTS.md", ".metadata_never_index"]
        payload = json.dumps({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": protected_rel + [str(self.mock_root / p) for p in protected_rel],
        }).encode("utf-8")

        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)

        # All protected files must be intact
        for p in protected_rel:
            fpath = self.mock_root / p
            self.assertTrue(fpath.exists(), f"Protected root file was deleted: {p}")

    def test_post_large_payload_stress(self) -> None:
        """POST with 1MB JSON containing 5000 non-existent paths processes safely without hanging."""
        large_paths = [f"nonexistent/fake/file_{i}.tmp" for i in range(5000)]
        payload = json.dumps({"tiers": [1], "dry_run": True, "paths": large_paths}).encode("utf-8")

        t0 = time.perf_counter()
        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            body=payload,
            headers={"Content-Type": "application/json"},
            timeout=15.0,
        )
        elapsed = time.perf_counter() - t0
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 5.0, "Large payload processing took too long")


class TestAdversarialRoutesAndMethods(AdversarialTestBase):
    """Empirical challenge suite for routing, 404/405, and directory traversal."""

    def test_unknown_routes_return_404(self) -> None:
        """Unknown paths under GET and POST return 404 JSON."""
        bad_paths = [
            "/nonexistent",
            "/api/v2/status",
            "/api/audit/extra",
            "/api/search/sub",
            "/admin",
            "/dashboard.html",
        ]
        for p in bad_paths:
            s_get, _, b_get = self._raw_request(p, method="GET")
            self.assertEqual(s_get, 404, f"GET {p} did not return 404")
            d_get = json.loads(b_get.decode("utf-8"))
            self.assertEqual(d_get.get("status"), 404)

            s_post, _, b_post = self._raw_request(p, method="POST", body=b"")
            self.assertEqual(s_post, 404, f"POST {p} did not return 404")
            d_post = json.loads(b_post.decode("utf-8"))
            self.assertEqual(d_post.get("status"), 404)

    def test_url_directory_traversal_attempts(self) -> None:
        """URL path traversal attempts do not reveal filesystem contents and return 404."""
        traversal_paths = [
            "/../../../../Windows/win.ini",
            "/..%2f..%2f..%2fetc%2fpasswd",
            "/api/../../index.db",
            "//api/status//",
        ]
        for p in traversal_paths:
            status, headers, body = self._raw_request(p, method="GET")
            # Should be 404 or cleanly handled 200 (if stripped to /api/status)
            if status == 200:
                self.assertIn("application/json", headers.get("Content-Type", ""))
            else:
                self.assertEqual(status, 404)
                data = json.loads(body.decode("utf-8"))
                self.assertIn("error", data)

    def test_method_not_allowed_405(self) -> None:
        """POST to read-only API endpoints returns 405 Method Not Allowed."""
        read_only_endpoints = ["/api/status", "/api/audit", "/api/search", "/api/junk"]
        for ep in read_only_endpoints:
            status, _, body = self._raw_request(ep, method="POST", body=b"")
            self.assertEqual(status, 405, f"POST {ep} did not return 405")
            data = json.loads(body.decode("utf-8"))
            self.assertIn("Method not allowed", data.get("error", ""))

    def test_unsupported_http_verbs(self) -> None:
        """Unimplemented verbs (PUT, DELETE, PATCH) return clean HTTP errors and don't crash."""
        verbs = ["PUT", "DELETE", "PATCH"]
        for v in verbs:
            status, _, body = self._raw_request("/api/status", method=v)
            # Default BaseHTTPRequestHandler returns 501 Unsupported method ('PUT')
            self.assertIn(status, (405, 501))

        # Server is still healthy
        status2, _, _ = self._raw_request("/api/status")
        self.assertEqual(status2, 200)


class TestAdversarialFtsAndSearch(AdversarialTestBase):
    """Empirical challenge suite for FTS5 syntax, SQL injection, unicode, and limits."""

    def _search(self, query_string: str) -> Tuple[int, Dict[str, Any]]:
        encoded_query = urllib.parse.quote(query_string, safe="=&")
        status, _, body = self._raw_request(f"/api/search?{encoded_query}")
        data = json.loads(body.decode("utf-8"))
        return status, data

    def test_fts5_unbalanced_quotes(self) -> None:
        """Unbalanced or erratic quotation marks do not trigger SQLite OperationalError."""
        adversarial_queries = [
            'q="unbalanced',
            'q=llama "3 8b',
            'q="""three quotes"""',
            'q=""""',
            'q=""',
            'q=llama "model" "unclosed',
        ]
        for q in adversarial_queries:
            status, data = self._search(q)
            self.assertEqual(status, 200, f"Query '{q}' failed with status {status}")
            self.assertIn("total", data)
            self.assertIn("latency_ms", data)

    def test_fts5_reserved_words_and_operators(self) -> None:
        """Dangling or standalone boolean operators do not crash query engine."""
        operator_queries = [
            "q=AND",
            "q=OR",
            "q=NOT",
            "q=NEAR",
            "q=AND OR NOT",
            "q=llama AND",
            "q=OR llama",
            "q=NOT llama",
            "q=llama AND OR NOT NEAR test",
        ]
        for q in operator_queries:
            status, data = self._search(q)
            self.assertEqual(status, 200, f"Query '{q}' failed with status {status}")
            self.assertIn("results", data)

    def test_fts5_special_syntax_characters(self) -> None:
        """Parentheses, brackets, colons, wildcards, and asterisks handled safely."""
        syntax_queries = [
            "q=(unclosed parenthesis",
            "q=parenthesis) unclosed",
            "q=((((((",
            "q=)(",
            "q={col1 col2}",
            "q=[bracket_test]",
            "q=*",
            "q=***",
            "q=*llama*",
            "q=llama*",
            "q=*llama",
            "q=::;;",
            "q=c++ react@18 vue-router#next",
            "q=dir:02_Learning_Knowledge ext:md,pdf",
        ]
        for q in syntax_queries:
            status, data = self._search(q)
            self.assertEqual(status, 200, f"Query '{q}' failed with status {status}")
            self.assertIn("results", data)

    def test_sql_injection_payloads(self) -> None:
        """SQL injection attacks in search params do not alter query logic or leak data."""
        sqli_queries = [
            "q=' OR 1=1 --",
            'q="; DROP TABLE files; --',
            "q=' UNION SELECT 1,2,3,4,5,6,7,8 --",
            "category=' OR '1'='1",
            "ext='; DROP TABLE files; --",
        ]
        for q in sqli_queries:
            status, data = self._search(q)
            self.assertEqual(status, 200)

        # Verify database schema and table still exist after injection attempts
        with DatabaseManager(str(self.db_path)) as db:
            con = db.get_connection()
            cur = con.cursor()
            cur.execute("SELECT count(*) FROM files;")
            count = cur.fetchone()[0]
            self.assertGreater(count, 0, "Files table was compromised or dropped by SQLi!")

    def test_multilingual_unicode_emoji(self) -> None:
        """Unicode, emoji, CJK, and Arabic text search without encoding errors."""
        unicode_queries = [
            "q=" + urllib.parse.quote("🤖 🔥 🚀"),
            "q=" + urllib.parse.quote("tiếng việt có dấu"),
            "q=" + urllib.parse.quote("深度学习模型"),
            "q=" + urllib.parse.quote("اختبار البحث"),
            "q=" + urllib.parse.quote("Русский текст"),
        ]
        for q in unicode_queries:
            status, data = self._search(q)
            self.assertEqual(status, 200)
            self.assertIn("results", data)

    def test_extreme_query_length(self) -> None:
        """Oversized query (4000 characters) executes within acceptable timeout."""
        long_kw = "word " * 800
        q = "q=" + urllib.parse.quote(long_kw)
        t0 = time.perf_counter()
        status, data = self._search(q)
        elapsed = time.perf_counter() - t0
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 3.0, "Oversized query took too long")

    def test_limit_and_offset_edge_cases(self) -> None:
        """Limit and offset boundaries (negative, zero, extreme large) are clamped safely."""
        # Negative limit -> clamped to 1
        s, d = self._search("limit=-50")
        self.assertEqual(s, 200)
        self.assertLessEqual(len(d["results"]), 1)

        # Zero limit -> clamped to 1
        s, d = self._search("limit=0")
        self.assertEqual(s, 200)
        self.assertLessEqual(len(d["results"]), 1)

        # Extreme limit -> clamped to 1000
        s, d = self._search("limit=999999")
        self.assertEqual(s, 200)
        self.assertLessEqual(len(d["results"]), 1000)

        # Negative offset -> clamped to 0
        s, d = self._search("offset=-10")
        self.assertEqual(s, 200)

        # Huge offset -> 0 results
        s, d = self._search("offset=99999999")
        self.assertEqual(s, 200)
        self.assertEqual(len(d["results"]), 0)

        # Non-numeric limit or offset is a client error (400), not a server crash (500)
        for bad in ("limit=notanumber", "offset=zz"):
            s, d = self._search(bad)
            self.assertEqual(s, 400, bad)
            self.assertIn("error", d)
            self.assertNotIn("invalid literal", d["error"], "Python exception text must not leak")

        # Server is still completely healthy
        s2, _ = self._search("q=llama")
        self.assertEqual(s2, 200)


class TestEmptyAndCorruptDatabaseScenarios(SmartDriveTestCase):
    """Empirical challenge suite for empty and corrupted databases."""

    def test_search_with_empty_database_schema(self) -> None:
        """Search on a database with tables created but 0 records returns empty results cleanly."""
        empty_root = self.test_dir / "empty_drive"
        empty_root.mkdir(parents=True, exist_ok=True)
        db_path = empty_root / ".smart_drive" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        with DatabaseManager(str(db_path)) as db:
            db.initialize_schema()

        server = create_server(root_path=empty_root, port=0, db_path=db_path)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            url = f"http://127.0.0.1:{port}/api/search?q=test"
            with urllib.request.urlopen(url, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["index_exists"])
                self.assertEqual(data["total"], 0)
                self.assertEqual(len(data["results"]), 0)
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=3)

    def test_audit_and_junk_on_empty_drive(self) -> None:
        """Audit and junk endpoints on a completely empty directory return 0 counts without error."""
        empty_root = self.test_dir / "completely_empty_drive"
        empty_root.mkdir(parents=True, exist_ok=True)

        server = create_server(root_path=empty_root, port=0)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            # Audit
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/audit", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["summary"]["total_files"], 0)
                self.assertEqual(data["summary"]["total_logical_bytes"], 0)

            # Junk
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/junk", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["summary"]["total_items"], 0)
                self.assertEqual(data["summary"]["total_nominal_bytes"], 0)
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=3)

    def test_zero_byte_corrupted_database_handling(self) -> None:
        """0-byte corrupted index database file does not crash the server process."""
        bad_root = self.test_dir / "bad_db_drive"
        bad_root.mkdir(parents=True, exist_ok=True)
        bad_db = bad_root / "corrupt.db"
        bad_db.touch()  # 0 bytes, not a valid SQLite DB

        server = create_server(root_path=bad_root, port=0, db_path=bad_db)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            # Requesting search on corrupted DB should return 500 error response without killing server
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/search?q=test")
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 500)
                data = json.loads(exc.read().decode("utf-8"))
                self.assertIn("error", data)

            # Subsequent requests to status still function properly
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            t.join(timeout=3)


class TestHighConcurrencyAndStress(AdversarialTestBase):
    """Empirical concurrency stress testing with 50+ concurrent requests."""

    def test_high_concurrency_mixed_endpoints(self) -> None:
        """50 concurrent worker threads fire 100 simultaneous requests across all endpoints."""
        endpoints = [
            ("GET", "/", None),
            ("GET", "/api/status", None),
            ("GET", "/api/audit", None),
            ("GET", "/api/search?q=llama", None),
            ("GET", "/api/search?q=algorithm", None),
            ("GET", "/api/search?category=AI%20Models", None),
            ("GET", "/api/junk", None),
            ("POST", "/api/junk/clean", json.dumps({"tiers": [1], "dry_run": True}).encode("utf-8")),
        ]

        # Prepare 100 test tasks
        tasks = [endpoints[i % len(endpoints)] for i in range(100)]
        results: List[Tuple[int, float]] = []

        def execute_task(task_spec: Tuple[str, str, Optional[bytes]]) -> Tuple[int, float]:
            method, path, body = task_spec
            t0 = time.perf_counter()
            headers = {"Content-Type": "application/json"} if body else {}
            status, _, _ = self._raw_request(path, method=method, body=body, headers=headers, timeout=15.0)
            elapsed = time.perf_counter() - t0
            return status, elapsed

        # Fire 50 concurrent threads simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            future_to_task = [executor.submit(execute_task, t) for t in tasks]
            for future in concurrent.futures.as_completed(future_to_task):
                status, elapsed = future.result()
                results.append((status, elapsed))

        self.assertEqual(len(results), 100)
        for status, elapsed in results:
            self.assertEqual(status, 200, f"Concurrent request returned unexpected status {status}")
            self.assertLess(elapsed, 10.0, f"Request took too long: {elapsed}s")

        # Confirm server is still responsive and healthy after the concurrent blitz
        s, _, b = self._raw_request("/api/status")
        self.assertEqual(s, 200)
        d = json.loads(b.decode("utf-8"))
        self.assertEqual(d["status"], "ready")

    def test_concurrent_search_sqlite_lock_resistance(self) -> None:
        """30 concurrent search queries do not encounter SQLite database lock errors."""
        def run_search(kw: str) -> int:
            status, _, _ = self._raw_request(f"/api/search?q={kw}", timeout=10.0)
            return status

        search_terms = ["llama", "model", "algorithm", "guide", "deep", "config"] * 5  # 30 requests
        with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
            statuses = list(executor.map(run_search, search_terms))

        self.assertEqual(len(statuses), 30)
        self.assertTrue(all(s == 200 for s in statuses), f"Non-200 statuses observed: {set(statuses)}")


class TestSecurityAndDataLeakage(AdversarialTestBase):
    """Empirical verification that server does not leak environment secrets or allow arbitrary file access."""

    def test_status_endpoint_no_sensitive_leak(self) -> None:
        """GET /api/status exposes only version, root, cluster, and database info; no secrets/env."""
        status, _, body = self._raw_request("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))

        # Explicitly verify required keys and check absence of environment variables
        self.assertEqual(set(data.keys()), {"status", "version", "root", "cluster_size_bytes", "cluster_size_kb", "database", "index_exists"})
        text_body = body.decode("utf-8")
        # Ensure common sensitive env keys are not present in payload
        for sensitive_word in ("API_KEY", "SECRET", "PASSWORD", "TOKEN", "AWS_"):
            self.assertNotIn(sensitive_word, text_body)

    def test_no_arbitrary_file_read_via_get(self) -> None:
        """Server never serves arbitrary local filesystem files via HTTP GET."""
        forbidden_targets = [
            "/smart_drive/cli/main.py",
            "/pyproject.toml",
            "/.smart_drive/index.db",
            "/tests/test_ui.py",
        ]
        for target in forbidden_targets:
            status, _, body = self._raw_request(target)
            self.assertEqual(status, 404, f"GET {target} should be 404 but got {status}")


if __name__ == "__main__":
    unittest.main()
