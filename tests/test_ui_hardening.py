"""tests/test_ui_hardening.py - The dashboard API must only serve the dashboard that this process serves.

A web page on another site can reach 127.0.0.1 through the user's browser. These tests replay what
such a page (or a rebound DNS name) can do against a real server and check that it gets nothing:
- foreign Host / Origin headers are refused before any work happens,
- CORS is granted to the dashboard's own origin only (exact match, not a substring match),
- the destructive endpoint needs JSON, which a foreign page cannot send without a preflight,
- bad input is a 4xx (never a 500 with Python exception text), and responses carry security headers.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple
from unittest.mock import patch

from smart_drive.ui.dashboard import get_dashboard_html
from smart_drive.ui.server import create_server

EVIL_ORIGINS = (
    "http://evil.example",
    "http://localhost.evil.com",
    "http://127.0.0.1.attacker.net",
    "http://localhost:3000",  # another local app is a different origin
    "null",
)


class _ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="sd_ui_hard_")).resolve()
        (cls.tmp / "GEMINI.md").write_text("# anchor", encoding="utf-8")
        cls.server = create_server(root_path=str(cls.tmp), port=0, host="127.0.0.1")
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @property
    def own_origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        host: Optional[str] = None,
        content_length: Optional[str] = None,
    ) -> Tuple[int, Dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", host or f"127.0.0.1:{self.port}")
            for key, value in (headers or {}).items():
                conn.putheader(key, value)
            if content_length is not None:
                conn.putheader("Content-Length", content_length)
            elif body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body if content_length is None else None)
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def plant_junk(self) -> Tuple[Path, Path]:
        folder = self.tmp / "victim"
        folder.mkdir(exist_ok=True)
        ds_store, tmp_file = folder / ".DS_Store", folder / "scratch.tmp"
        ds_store.write_text("junk")
        tmp_file.write_text("junk")
        return ds_store, tmp_file


class TestHostAndOriginGuard(_ServerCase):
    def test_own_host_names_are_accepted(self) -> None:
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"LOCALHOST:{self.port}"):
            status, _, _ = self.request("GET", "/api/status", host=host)
            self.assertEqual(status, 200, host)

    def test_foreign_host_header_is_rejected(self) -> None:
        """DNS rebinding: the page reaches us under its own name, so Host is not ours."""
        for host in ("evil.example", f"evil.example:{self.port}", f"127.0.0.1.evil.com:{self.port}", "127.0.0.1:1"):
            status, _, body = self.request("GET", "/api/status", host=host)
            self.assertEqual(status, 403, host)
            self.assertIn("error", json.loads(body))

    def test_foreign_origin_is_rejected_and_granted_nothing(self) -> None:
        for origin in EVIL_ORIGINS:
            for path in ("/api/status", "/api/audit", "/"):
                status, headers, _ = self.request("GET", path, headers={"Origin": origin})
                self.assertEqual(status, 403, f"{origin} {path}")
                self.assertNotIn("access-control-allow-origin", headers, f"{origin} {path}")

    def test_own_origin_gets_a_cors_grant_for_exactly_that_origin(self) -> None:
        status, headers, _ = self.request("GET", "/api/status", headers={"Origin": self.own_origin})
        self.assertEqual(status, 200)
        self.assertEqual(headers["access-control-allow-origin"], self.own_origin)
        self.assertEqual(headers["vary"], "Origin")

    def test_no_origin_means_no_cors_headers(self) -> None:
        status, headers, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_preflight_is_answered_for_the_dashboard_only(self) -> None:
        ask = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
        status, headers, _ = self.request("OPTIONS", "/api/junk/clean", headers={"Origin": "http://localhost.evil.com", **ask})
        self.assertEqual(status, 403)
        self.assertNotIn("access-control-allow-origin", headers)
        status, headers, _ = self.request("OPTIONS", "/api/junk/clean", headers={"Origin": self.own_origin, **ask})
        self.assertEqual(status, 204)
        self.assertEqual(headers["access-control-allow-origin"], self.own_origin)
        self.assertIn("POST", headers["access-control-allow-methods"])

    def test_extra_origin_is_an_explicit_opt_in(self) -> None:
        dev_ui = "http://localhost:5173"
        status, _, _ = self.request("GET", "/api/status", headers={"Origin": dev_ui})
        self.assertEqual(status, 403)
        with patch.dict(os.environ, {"SMART_DRIVE_UI_ALLOWED_ORIGINS": dev_ui}):
            status, headers, _ = self.request("GET", "/api/status", headers={"Origin": dev_ui})
        self.assertEqual(status, 200)
        self.assertEqual(headers["access-control-allow-origin"], dev_ui)

    def test_the_opt_in_works_the_way_a_browser_really_sends_it(self) -> None:
        """A front-end on localhost:5173 calling the API is "same-site" to the browser, with its Origin set."""
        dev_ui = "http://localhost:5173"
        browser = {"Origin": dev_ui, "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
        with patch.dict(os.environ, {"SMART_DRIVE_UI_ALLOWED_ORIGINS": dev_ui}):
            status, headers, _ = self.request("GET", "/api/status", headers=browser)
            self.assertEqual(status, 200)
            self.assertEqual(headers["access-control-allow-origin"], dev_ui)
            preflight = dict(browser, **{"Access-Control-Request-Method": "POST"})
            status, headers, _ = self.request("OPTIONS", "/api/junk/clean", headers=preflight)
            self.assertIn(status, (200, 204))
            self.assertEqual(headers["access-control-allow-origin"], dev_ui)
        # without the opt-in the very same request is refused, and an origin that is not listed stays refused
        status, _, _ = self.request("GET", "/api/status", headers=browser)
        self.assertEqual(status, 403)
        with patch.dict(os.environ, {"SMART_DRIVE_UI_ALLOWED_ORIGINS": dev_ui}):
            other = dict(browser, Origin="http://localhost:3000")
            self.assertEqual(self.request("GET", "/api/status", headers=other)[0], 403)


class TestCrossSiteDeletionIsImpossible(_ServerCase):
    """The attack that used to work: a foreign page POSTs text/plain JSON and junk gets deleted."""

    PAYLOAD = json.dumps({"tiers": [1, 3], "dry_run": False}).encode()

    def test_cross_site_post_with_foreign_origin_deletes_nothing(self) -> None:
        victims = self.plant_junk()
        for origin in EVIL_ORIGINS:
            status, _, _ = self.request("POST", "/api/junk/clean", headers={"Origin": origin, "Content-Type": "text/plain"}, body=self.PAYLOAD)
            self.assertEqual(status, 403, origin)
        self.assertTrue(all(v.exists() for v in victims))

    def test_only_json_may_drive_the_destructive_endpoint(self) -> None:
        """Without Origin a browser cannot post, but the content type must still be JSON."""
        victims = self.plant_junk()
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", None):
            headers = {"Content-Type": ctype} if ctype else {}
            status, _, body = self.request("POST", "/api/junk/clean", headers=headers, body=self.PAYLOAD)
            self.assertEqual(status, 415, ctype)
            self.assertIn("application/json", json.loads(body)["error"])
        self.assertTrue(all(v.exists() for v in victims))

    def test_the_dashboard_itself_can_still_clean(self) -> None:
        victims = self.plant_junk()
        headers = {"Origin": self.own_origin, "Content-Type": "application/json; charset=UTF-8"}
        status, _, body = self.request("POST", "/api/junk/clean", headers=headers, body=self.PAYLOAD)
        self.assertEqual(status, 200, body)
        self.assertFalse(any(v.exists() for v in victims))

    def test_dashboard_page_posts_json(self) -> None:
        """The SPA must keep working under the JSON-only rule."""
        html = get_dashboard_html()
        self.assertGreaterEqual(html.count("'Content-Type': 'application/json'"), 2)


class TestInputValidation(_ServerCase):
    JSON = {"Content-Type": "application/json"}

    def post(self, payload: object) -> Tuple[int, dict]:
        status, _, body = self.request("POST", "/api/junk/clean", headers=self.JSON, body=json.dumps(payload).encode())
        return status, json.loads(body)

    def test_bad_query_parameters_are_400(self) -> None:
        for query in ("limit=abc", "offset=zz", "limit=1.5", "limit=1e3"):
            status, _, body = self.request("GET", f"/api/search?q=a&{query}")
            self.assertEqual(status, 400, query)
            self.assertNotIn(b"invalid literal", body)

    def test_bad_clean_payloads_are_400(self) -> None:
        bad = [
            [1, 2],
            "text",
            {"tiers": []},
            {"tiers": "1"},
            {"tiers": [1, "x"]},
            {"tiers": [True]},
            {"tiers": [1], "dry_run": "no"},
            {"tiers": [1], "dry_run": 0},
            {"tiers": [1], "paths": 123},
            {"tiers": [1], "paths": [1, 2]},
        ]
        for payload in bad:
            status, data = self.post(payload)
            self.assertEqual(status, 400, payload)
            self.assertIn("error", data)

    def test_out_of_range_tier_numbers_are_ignored_not_rejected(self) -> None:
        status, data = self.post({"tiers": [99, -1], "dry_run": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["total_attempted"], 0)

    def test_dry_run_is_the_default(self) -> None:
        victims = self.plant_junk()
        status, data = self.post({"tiers": [1, 3]})
        self.assertEqual(status, 200)
        self.assertTrue(data["dry_run"])
        self.assertTrue(all(v.exists() for v in victims))

    def test_bad_content_length_is_400_and_oversize_is_413(self) -> None:
        status, _, _ = self.request("POST", "/api/junk/clean", headers=self.JSON, content_length="abc")
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/junk/clean", headers=self.JSON, content_length="-5")
        self.assertEqual(status, 400)
        status, _, _ = self.request("POST", "/api/junk/clean", headers=self.JSON, content_length=str(50 * 1024 * 1024))
        self.assertEqual(status, 413)

    def test_unexpected_failures_do_not_leak_exception_text(self) -> None:
        with patch("smart_drive.ui.server.StorageAuditor", side_effect=RuntimeError("secret internal detail /Users/x")):
            status, _, body = self.request("GET", "/api/audit")
        self.assertEqual(status, 500)
        self.assertNotIn(b"secret internal detail", body)
        self.assertEqual(json.loads(body)["error"], "Internal server error")


class TestSecurityHeaders(_ServerCase):
    def test_every_kind_of_response_carries_defensive_headers(self) -> None:
        for path, host in (("/", None), ("/api/status", None), ("/api/nope", None), ("/api/status", "evil.example")):
            _, headers, _ = self.request("GET", path, host=host)
            self.assertEqual(headers.get("x-content-type-options"), "nosniff", (path, host))
            self.assertEqual(headers.get("x-frame-options"), "DENY", (path, host))
            self.assertEqual(headers.get("referrer-policy"), "no-referrer", (path, host))
            self.assertEqual(headers.get("cache-control"), "no-store", (path, host))

    def test_dashboard_page_has_a_content_security_policy(self) -> None:
        _, headers, _ = self.request("GET", "/")
        csp = headers["content-security-policy"]
        for directive in ("default-src 'none'", "connect-src 'self'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(directive, csp)

    def test_server_does_not_advertise_the_python_version(self) -> None:
        _, headers, _ = self.request("GET", "/api/status")
        self.assertNotIn("Python", headers.get("server", ""))


class TestFetchMetadata(_ServerCase):
    """A cross-site no-cors GET carries no Origin, but the browser labels it with Sec-Fetch-Site.

    Found by an independent review: an <img src="http://127.0.0.1:8765/api/audit"> on any site made the dashboard
    run a full-drive scan (the answer is unreadable to that page, so it was a denial of service, not a leak).
    """

    API_PATHS = ("/api/status", "/api/audit", "/api/junk", "/api/search?q=x")

    def test_cross_site_and_same_site_requests_to_the_api_are_refused(self) -> None:
        for site in ("cross-site", "same-site", "something-new"):
            for path in self.API_PATHS:
                with self.subTest(site=site, path=path):
                    status, headers, _ = self.request("GET", path, headers={"Sec-Fetch-Site": site})
                    self.assertEqual(status, 403)
                    self.assertNotIn("access-control-allow-origin", headers)

    def test_a_refused_request_does_no_work(self) -> None:
        with patch("smart_drive.ui.server.StorageAuditor") as auditor:
            status, _, _ = self.request("GET", "/api/audit", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        auditor.assert_not_called()

    def test_the_dashboards_own_requests_and_a_typed_address_are_allowed(self) -> None:
        for site in ("same-origin", "none"):
            for path in ("/", "/api/status"):
                with self.subTest(site=site, path=path):
                    status, _, _ = self.request("GET", path, headers={"Sec-Fetch-Site": site})
                    self.assertEqual(status, 200)

    def test_requests_that_carry_no_such_header_are_unchanged(self) -> None:
        """curl, scripts and older browsers do not send it."""
        for path in ("/", "/api/status"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 200)

    def test_a_link_from_another_site_may_open_the_page_but_never_the_api(self) -> None:
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.request("GET", "/", headers=navigation)[0], 200)
        self.assertEqual(self.request("GET", "/api/status", headers=navigation)[0], 403)
        # embedding the page (an iframe) is not a top-level navigation
        embedded = dict(navigation, **{"Sec-Fetch-Dest": "iframe"})
        self.assertEqual(self.request("GET", "/", headers=embedded)[0], 403)

    def test_a_cross_site_post_is_refused_too(self) -> None:
        status, _, _ = self.request(
            "POST", "/api/junk/clean", headers={"Sec-Fetch-Site": "cross-site", "Content-Type": "application/json"},
            body=b'{"tiers": [1], "dry_run": true}',
        )
        self.assertEqual(status, 403)


class TestSerializationFailuresStayInTheLog(_ServerCase):
    def test_an_unserialisable_answer_is_a_plain_500(self) -> None:
        def broken(handler: object) -> None:
            handler.send_json_response({"values": {1, 2, 3}})  # type: ignore[attr-defined]

        with patch("smart_drive.ui.server.SmartDriveRequestHandler.handle_api_status", broken):
            status, _, body = self.request("GET", "/api/status")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body)["error"], "Internal server error")
        self.assertNotIn(b"serializable", body)
        self.assertNotIn(b"set", body.lower().replace(b"internal server error", b""))


if __name__ == "__main__":
    unittest.main()
