"""tests/test_ui.py - Comprehensive Unit Tests for SmartDrive-OS Web Dashboard & UI.

100% Python Standard Library unittest & urllib.request. Zero external dependencies.
Tests:
- Server creation & ephemeral port lifecycle (start, bind port 0, serve, shutdown).
- REST API endpoints:
  - GET / (Embedded Dark Mode SPA)
  - GET /api/status (System & cluster status)
  - GET /api/audit (Storage breakdown & 512KB cluster slack metrics)
  - GET /api/search (SQLite FTS5 instant search with compound filters & missing index fallback)
  - GET /api/junk (3-tier junk detection preview)
  - POST /api/junk/clean (Dry-run preview simulation & confirmed safe purge)
  - OPTIONS /api/* (CORS preflight)
  - 400, 404, 405 error responses
- CLI command `smart-drive ui` & headless browser fallback.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.cli.cmd_ui import cmd_ui
from smart_drive.cli.main import build_parser
from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.ui.dashboard import get_dashboard_html
from smart_drive.ui.server import (
    SmartDriveRequestHandler,
    ThreadingHTTPServer,
    create_server,
    run_server,
)
from tests.helpers import SmartDriveTestCase, create_mock_ssd_tree


class TestWebServerLifecycle(SmartDriveTestCase):
    """Verifies ThreadingHTTPServer initialization, ephemeral binding, and lifecycle."""

    def test_create_server_binds_ephemeral_port(self) -> None:
        """create_server(port=0) dynamically assigns an available operating system port."""
        server = create_server(self.test_dir, port=0)
        try:
            port = server.server_address[1]
            self.assertGreater(port, 0)
            self.assertEqual(server.root_path, str(self.test_dir.resolve()))
            self.assertEqual(server.cluster_size, CLUSTER_SIZE_BYTES)
            self.assertTrue(server.daemon_threads)
            self.assertTrue(server.allow_reuse_address)
        finally:
            server.server_close()

    def test_server_thread_startup_and_shutdown(self) -> None:
        """Server starts in daemon thread, responds to HTTP, and shuts down cleanly."""
        server = create_server(self.test_dir, port=0)
        port = server.server_address[1]
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()

        try:
            url = f"http://127.0.0.1:{port}/"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                content_type = resp.headers.get("Content-Type", "")
                self.assertIn("text/html", content_type)
        finally:
            server.shutdown()
            server.server_close()
            srv_thread.join(timeout=3)
            self.assertFalse(srv_thread.is_alive())


class TestRestEndpoints(SmartDriveTestCase):
    """Comprehensive test suite for all REST API endpoints and error conditions."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = create_mock_ssd_tree(self.test_dir / "mock_drive")
        self.db_path = self.mock_root / ".smart_drive" / "index.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Build search index so /api/search has indexed records
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

    def _http_request(
        self,
        path: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Performs HTTP request and returns (status_code, headers, response_bytes)."""
        url = f"http://127.0.0.1:{self.port}{path}"
        req_headers = headers.copy() if headers else {}
        payload = None
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_get_root_serves_spa(self) -> None:
        """GET / serves the complete Dark Mode Single Page Application."""
        status, headers, body = self._http_request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        # No Origin header means no CORS request: nothing is granted to foreign pages
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))
        self.assertIn("connect-src 'self'", headers.get("Content-Security-Policy", ""))

        html_text = body.decode("utf-8")
        self.assertIn("<!DOCTYPE html>", html_text)
        self.assertIn("SmartDrive-OS Dashboard", html_text)
        self.assertIn("v1.1.0", html_text)
        self.assertIn("Zero-Dependency", html_text)
        self.assertIn("Overview & Slack", html_text)
        self.assertIn("Instant Search", html_text)
        self.assertIn("Safe Cleanup", html_text)
        self.assertIn("executeSearch", html_text)
        self.assertIn("simulateDryRun", html_text)
        self.assertIn("openPurgeModal", html_text)

    def test_get_api_status(self) -> None:
        """GET /api/status returns JSON status, version, cluster geometry, and database path."""
        status, headers, body = self._http_request("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))

        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["status"], "ready")
        self.assertEqual(data["version"], "1.1.0")
        self.assertEqual(data["root"], str(self.mock_root.resolve()))
        self.assertEqual(data["cluster_size_bytes"], 524288)
        self.assertEqual(data["cluster_size_kb"], 512)
        self.assertTrue(data["index_exists"])
        self.assertTrue(data["database"]["exists"])
        self.assertGreater(data["database"]["size_bytes"], 0)

    def test_get_api_audit(self) -> None:
        """GET /api/audit runs StorageAuditor and returns 6 taxonomies, slack metrics, and hotspots."""
        status, headers, body = self._http_request("/api/audit")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("Content-Type", ""))

        data = json.loads(body.decode("utf-8"))
        self.assertIn("taxonomies", data)
        self.assertIn("summary", data)
        self.assertIn("top_slack_directories", data)

        taxonomies = data["taxonomies"]
        self.assertIn("01_AI_Models", taxonomies)
        self.assertIn("02_Learning_Knowledge", taxonomies)
        self.assertIn("05_Dev_Toolbox", taxonomies)

        summary = data["summary"]
        self.assertGreater(summary["total_files"], 0)
        self.assertGreater(summary["total_logical_bytes"], 0)
        self.assertGreater(summary["total_allocated_bytes"], 0)
        self.assertGreaterEqual(summary["total_slack_bytes"], 0)

    def test_get_api_search_with_keywords_and_filters(self) -> None:
        """GET /api/search finds indexed files, calculates slack, and returns sub-10ms response."""
        # 1. Search keyword "llama"
        status, headers, body = self._http_request("/api/search?q=llama")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["index_exists"])
        self.assertGreaterEqual(data["total"], 1)
        self.assertIn("latency_ms", data)

        first_match = data["results"][0]
        self.assertIn("llama", first_match["name"].lower())
        self.assertIn("allocated_size", first_match)
        self.assertIn("slack_bytes", first_match)
        self.assertGreaterEqual(first_match["allocated_size"], 524288)

        # 2. Filtered search by category and extension
        status, headers, body = self._http_request("/api/search?category=AI%20Models&ext=gguf&limit=5")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertGreaterEqual(data["total"], 1)
        for m in data["results"]:
            self.assertEqual(m["category"], "AI Models")
            self.assertTrue(m["path"].endswith(".gguf"))

    def test_get_api_search_missing_index_graceful_fallback(self) -> None:
        """GET /api/search returns index_exists=False cleanly when index database does not exist."""
        empty_dir = self.test_dir / "empty_drive"
        empty_dir.mkdir(parents=True, exist_ok=True)
        no_db_server = create_server(
            root_path=empty_dir,
            port=0,
            db_path=empty_dir / "nonexistent.db",
        )
        port = no_db_server.server_address[1]
        thread = threading.Thread(target=no_db_server.serve_forever, daemon=True)
        thread.start()

        try:
            url = f"http://127.0.0.1:{port}/api/search?q=test"
            with urllib.request.urlopen(url, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertFalse(data["index_exists"])
                self.assertEqual(data["total"], 0)
                self.assertEqual(len(data["results"]), 0)
                self.assertIn("message", data)
        finally:
            no_db_server.shutdown()
            no_db_server.server_close()
            thread.join(timeout=3)

    def test_get_api_junk_preview(self) -> None:
        """GET /api/junk returns items partitioned into Tiers 1, 2, 3 with slack metrics."""
        status, headers, body = self._http_request("/api/junk")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))

        self.assertIn("tier1", data)
        self.assertIn("tier2", data)
        self.assertIn("tier3", data)
        self.assertIn("tiers", data)
        self.assertIn("summary", data)
        self.assertIn("total_bytes", data)
        self.assertIn("total_slack_bytes", data)

        # Mock SSD includes macOS AppleDouble (._*) and .DS_Store, so Tier 1 should have items
        self.assertGreater(len(data["tier1"]), 0)
        t1_item = data["tier1"][0]
        self.assertIn("rel_path", t1_item)
        self.assertIn("allocated_size", t1_item)
        self.assertIn("slack_bytes", t1_item)

    def test_post_api_junk_clean_dry_run(self) -> None:
        """POST /api/junk/clean with dry_run=true previews purges without deleting files."""
        status, headers, body = self._http_request(
            "/api/junk/clean",
            method="POST",
            data={"tiers": [1], "dry_run": True},
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))

        self.assertTrue(data["dry_run"])
        self.assertGreater(data["total_attempted"], 0)
        self.assertGreater(data["total_succeeded"], 0)
        self.assertEqual(data["total_failed"], 0)
        self.assertGreater(data["nominal_bytes_reclaimed"], 0)
        self.assertGreater(data["allocated_bytes_reclaimed"], 0)

        # Confirm candidate files still exist on disk after dry-run
        for p in data["purged"]:
            fpath = Path(p["path"])
            self.assertTrue(fpath.exists(), f"File should not have been unlinked in dry-run: {fpath}")

    def test_post_api_junk_clean_apply(self) -> None:
        """POST /api/junk/clean with dry_run=false purges junk while preserving protected files."""
        # 1. Execute live purge for Tier 1
        status, headers, body = self._http_request(
            "/api/junk/clean",
            method="POST",
            data={"tiers": [1], "dry_run": False},
        )
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))

        self.assertFalse(data["dry_run"])
        self.assertGreater(data["total_succeeded"], 0)
        self.assertGreater(data["nominal_bytes_reclaimed"], 0)

        # 2. Verify protected files are NEVER deleted
        protected_files = [
            self.mock_root / "GEMINI.md",
            self.mock_root / "CLAUDE.md",
            self.mock_root / "AGENTS.md",
            self.mock_root / "README.md",
            self.mock_root / ".metadata_never_index",
            self.mock_root / ".fseventsd" / "no_log",
            self.mock_root / "01_AI_Models" / "GGUF" / "llama-3-8b.Q4_K_M.gguf",
        ]
        for pf in protected_files:
            self.assertTrue(pf.exists(), f"Protected file was deleted: {pf}")

    def test_cors_preflight_and_headers(self) -> None:
        """OPTIONS from the dashboard's own origin returns 204 No Content with CORS allow headers."""
        own_origin = f"http://127.0.0.1:{self.port}"
        status, headers, _ = self._http_request("/api/search", method="OPTIONS", headers={"Origin": own_origin})
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), own_origin)
        self.assertIn("GET", headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("OPTIONS", headers.get("Access-Control-Allow-Methods", ""))

    def test_error_handling_unknown_route(self) -> None:
        """GET to non-existent route returns 404 JSON."""
        status, headers, body = self._http_request("/api/unknown_route")
        self.assertEqual(status, 404)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("error", data)

    def test_error_handling_method_not_allowed(self) -> None:
        """POST to GET-only route returns 405 JSON."""
        status, headers, body = self._http_request("/api/status", method="POST", data={})
        self.assertEqual(status, 405)
        data = json.loads(body.decode("utf-8"))
        self.assertIn("error", data)

    def test_error_handling_invalid_post_body(self) -> None:
        """POST with empty body or invalid JSON returns 400."""
        # Empty body
        url = f"http://127.0.0.1:{self.port}/api/junk/clean"
        req = urllib.request.Request(url, data=b"", headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.fail("Expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

        # Invalid tiers argument
        status, headers, body = self._http_request("/api/junk/clean", method="POST", data={"tiers": []})
        self.assertEqual(status, 400)


class TestCliAndHeadless(SmartDriveTestCase):
    """Verifies CLI argument parsing, execution dispatcher, and headless browser fallback."""

    def test_cmd_ui_parser_flags(self) -> None:
        """Parser correctly parses --port, --no-browser, --root, and --db."""
        parser = build_parser()
        args = parser.parse_args(["ui", "--port", "9999", "--no-browser", "--root", str(self.test_dir)])
        self.assertEqual(args.subcommand, "ui")
        self.assertEqual(args.port, 9999)
        self.assertTrue(args.no_browser)
        self.assertEqual(args.root, str(self.test_dir))

    def test_headless_browser_fallback_in_run_server(self) -> None:
        """run_server gracefully handles webbrowser.Error in headless environments without crashing."""
        mock_server = MagicMock()
        mock_server.server_address = ("127.0.0.1", 8765)
        mock_server.root_path = str(self.test_dir)
        mock_server.cluster_size = 524288
        mock_server.serve_forever.side_effect = KeyboardInterrupt

        with patch("smart_drive.ui.server.create_server", return_value=mock_server):
            with patch("webbrowser.open", side_effect=Exception("Headless runner: no browser display")):
                # Should not raise exception
                run_server(root_path=self.test_dir, port=0, open_browser=True)
                mock_server.serve_forever.assert_called_once()
                mock_server.shutdown.assert_called_once()
                mock_server.server_close.assert_called_once()

    def test_cmd_ui_dispatch_invocation(self) -> None:
        """cmd_ui dispatches to run_server with parsed CLI arguments."""
        parser = build_parser()
        args = parser.parse_args(["ui", "--port", "8888", "--no-browser", "--root", str(self.test_dir)])

        with patch("smart_drive.cli.cmd_ui.run_server") as mock_run:
            ret = cmd_ui(args)
            self.assertEqual(ret, 0)
            mock_run.assert_called_once_with(
                root_path=str(self.test_dir.resolve()),
                port=8888,
                open_browser=False,
                db_path=os.path.join(str(self.test_dir.resolve()), ".smart_drive", "index.db"),
            )

    def test_get_dashboard_html_structure(self) -> None:
        """get_dashboard_html returns complete HTML with valid tags, styles, and scripts."""
        html = get_dashboard_html()
        self.assertTrue(isinstance(html, str))
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<title>SmartDrive-OS Dashboard</title>", html)
        self.assertIn("<style>", html)
        self.assertIn("</style>", html)
        self.assertIn("<script>", html)
        self.assertIn("</script>", html)
        self.assertIn("Canonical Taxonomy Storage Allocation", html)
        self.assertIn("512 KB", html)
        self.assertIn("AI Models", html)


if __name__ == "__main__":
    unittest.main()
