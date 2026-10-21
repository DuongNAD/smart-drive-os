"""tests/test_mcp_protocol_versions.py - The MCP server speaks the revision the client asks for, and only adds to it.

Regressions:
- Every `initialize` was answered with 2024-11-05 whatever the client asked for, although MCP says to answer with the
  requested revision when it is supported. Clients of newer revisions were never told which one the server speaks, so
  the fields those revisions define could not be offered. `initialize` now negotiates among 2025-11-25, 2025-06-18,
  2025-03-26 and 2024-11-05: a supported revision is answered as asked, any other string with the latest, and a client
  that names none (nothing, null, a number) still gets 2024-11-05.
- The chosen revision lasts for the whole process (a second `initialize` negotiates again) and decides the shape of
  `serverInfo`: `name` and `version` in 2024-11-05, `title` added in 2025-06-18, `description` and `websiteUrl` added in
  2025-11-25. The 2024-11-05 reply stays byte for byte what it was before the server negotiated.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import smart_drive.mcp as mcp_package
from smart_drive.mcp import server as server_module
from smart_drive.mcp.server import (
    LATEST_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    PROTOCOL_VERSION,
    SERVER_DESCRIPTION,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_TITLE,
    SERVER_VERSION,
    SERVER_WEBSITE_URL,
    SUPPORTED_PROTOCOL_VERSIONS,
    SmartDriveMCPServer,
    negotiate_protocol_version,
    server_info,
)

SERVER_JSON = Path(__file__).resolve().parent.parent / "server.json"
_ABSENT = object()  # "leave the key out", which None cannot say

# What the serverInfo of each revision holds: every revision keeps the fields of the one before and adds its own.
SERVER_INFO_BY_REVISION: Dict[str, Dict[str, str]] = {
    "2024-11-05": {"name": SERVER_NAME, "version": SERVER_VERSION},
    "2025-03-26": {"name": SERVER_NAME, "version": SERVER_VERSION},
    "2025-06-18": {"name": SERVER_NAME, "version": SERVER_VERSION, "title": SERVER_TITLE},
    "2025-11-25": {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
        "title": SERVER_TITLE,
        "description": SERVER_DESCRIPTION,
        "websiteUrl": SERVER_WEBSITE_URL,
    },
}


class _Server(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_mcp_ver_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def talk_raw(self, *chunks: str) -> str:
        """Feeds raw stdin chunks to the real stdio loop and returns exactly what it wrote to stdout."""
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO("".join(chunks))), patch("sys.stdout", stdout):
            self.server.run_stdio()
        return stdout.getvalue()

    def talk(self, *chunks: str) -> List[Any]:
        """Feeds raw stdin chunks to the real stdio loop and returns every JSON document it wrote."""
        return [json.loads(line) for line in self.talk_raw(*chunks).splitlines() if line.strip()]

    @staticmethod
    def line(message: Any) -> str:
        return json.dumps(message) + "\n"

    def initialize_line(self, params: Any = _ABSENT, msg_id: int = 1) -> str:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": "initialize"}
        if params is not _ABSENT:
            message["params"] = params
        return self.line(message)

    def initialize(self, params: Any = _ABSENT) -> Dict[str, Any]:
        """The result of an `initialize` with these params, sent through the stdio loop."""
        (reply,) = self.talk(self.initialize_line(params))
        return reply["result"]


class TestNegotiation(_Server):
    def test_a_supported_revision_is_answered_as_asked(self) -> None:
        for version in LEGACY_PROTOCOL_VERSIONS:
            with self.subTest(version=version):
                result = self.initialize({"protocolVersion": version})
                self.assertEqual(result["protocolVersion"], version)
                self.assertEqual(self.server.protocol_version, version)

    def test_an_unknown_revision_is_answered_with_the_latest_one_that_has_initialize(self) -> None:
        """MCP: when the request is not supported, answer with the latest supported. 2026-07-28 has no `initialize`."""
        latest = LEGACY_PROTOCOL_VERSIONS[0]
        for version in ("2099-01-01", "2026-07-28", "1.0.0"):
            with self.subTest(version=version):
                result = self.initialize({"protocolVersion": version})
                self.assertEqual(result["protocolVersion"], latest)
                self.assertEqual(result["serverInfo"], SERVER_INFO_BY_REVISION[latest])
                self.assertEqual(self.server.protocol_version, latest)

    def test_a_client_that_names_no_revision_gets_the_oldest_one(self) -> None:
        cases = (
            ("no protocolVersion", {}),
            ("null", {"protocolVersion": None}),
            ("an integer", {"protocolVersion": 20241105}),
            ("a float", {"protocolVersion": 2024.1105}),
            ("a boolean", {"protocolVersion": True}),
            ("a list", {"protocolVersion": ["2025-11-25"]}),
            ("an object", {"protocolVersion": {"version": "2025-11-25"}}),
            ("params that are null", None),
            ("params that are a list", ["2025-11-25"]),
            ("params that are a string", "2025-11-25"),
            ("no params at all", _ABSENT),
        )
        for label, params in cases:
            with self.subTest(case=label):
                self.initialize({"protocolVersion": "2025-11-25"})  # so "the oldest" cannot be a leftover default
                result = self.initialize(params)
                self.assertEqual(result["protocolVersion"], PROTOCOL_VERSION)
                self.assertEqual(result["serverInfo"], SERVER_INFO_BY_REVISION[PROTOCOL_VERSION])
                self.assertEqual(self.server.protocol_version, PROTOCOL_VERSION)

    def test_before_initialize_the_server_speaks_the_oldest_revision(self) -> None:
        self.assertEqual(self.server.protocol_version, PROTOCOL_VERSION)

    def test_a_second_initialize_negotiates_again(self) -> None:
        first, second = self.talk(
            self.initialize_line({"protocolVersion": "2025-11-25"}, msg_id=1),
            self.initialize_line({"protocolVersion": "2025-03-26"}, msg_id=2),
        )
        self.assertEqual(first["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(set(first["result"]["serverInfo"]), set(SERVER_INFO_BY_REVISION["2025-11-25"]))
        self.assertEqual(second["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(second["result"]["serverInfo"], SERVER_INFO_BY_REVISION["2025-03-26"])
        self.assertEqual(self.server.protocol_version, "2025-03-26")

    def test_the_function_alone(self) -> None:
        cases = (
            ("2025-11-25", "2025-11-25"),
            ("2024-11-05", "2024-11-05"),
            ("2025-12-01", "2025-11-25"),
            ("2026-07-28", "2025-11-25"),
            (None, "2024-11-05"),
            (20250618, "2024-11-05"),
            (["2025-06-18"], "2024-11-05"),
        )
        for requested, expected in cases:
            with self.subTest(requested=requested):
                self.assertEqual(negotiate_protocol_version(requested), expected)


class TestServerInfo(_Server):
    def test_every_revision_has_a_row(self) -> None:
        self.assertEqual(set(SERVER_INFO_BY_REVISION), set(LEGACY_PROTOCOL_VERSIONS))

    def test_each_revision_adds_its_fields_after_the_old_ones(self) -> None:
        for version, expected in SERVER_INFO_BY_REVISION.items():
            with self.subTest(version=version):
                info = self.initialize({"protocolVersion": version})["serverInfo"]
                self.assertEqual(info, expected)
                self.assertEqual(list(info), list(expected))
                self.assertEqual(server_info(version), expected)
                self.assertEqual(list(server_info(version)), list(expected))


class TestIdentityMatchesServerJson(unittest.TestCase):
    def test_the_identity_is_the_one_server_json_gives_the_registry(self) -> None:
        with open(SERVER_JSON, encoding="utf-8") as f:
            entry = json.load(f)
        self.assertEqual(SERVER_TITLE, entry["title"])
        self.assertEqual(SERVER_DESCRIPTION, entry["description"])
        self.assertEqual(SERVER_WEBSITE_URL, entry["websiteUrl"])


class TestOldClientsSeeNoChange(_Server):
    EXPECTED_LINE = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}},
                "instructions": SERVER_INSTRUCTIONS,
            },
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )

    def test_the_initialize_line_for_2024_11_05_is_what_it_was_before_the_negotiation(self) -> None:
        for label, params in (("asks for it", {"protocolVersion": "2024-11-05"}), ("asks for nothing", {})):
            with self.subTest(client=label):
                self.assertEqual(self.talk_raw(self.initialize_line(params)), self.EXPECTED_LINE + "\n")


class TestRevisionLists(unittest.TestCase):
    def test_legacy_revisions_are_iso_dates_newest_first(self) -> None:
        """server_info compares revisions as strings, which only works for dates written year first."""
        for version in LEGACY_PROTOCOL_VERSIONS:
            self.assertRegex(version, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(list(LEGACY_PROTOCOL_VERSIONS), sorted(LEGACY_PROTOCOL_VERSIONS, reverse=True))

    def test_the_default_revision_is_the_oldest_one(self) -> None:
        self.assertEqual(PROTOCOL_VERSION, "2024-11-05")
        self.assertEqual(PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSIONS[-1])

    def test_the_latest_revision_is_the_newest_supported_one(self) -> None:
        self.assertEqual(LATEST_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS[0])
        self.assertEqual(LATEST_PROTOCOL_VERSION, max(SUPPORTED_PROTOCOL_VERSIONS))
        self.assertTrue(set(LEGACY_PROTOCOL_VERSIONS) <= set(SUPPORTED_PROTOCOL_VERSIONS))

    def test_the_package_exports_the_revision_lists(self) -> None:
        for name in ("LATEST_PROTOCOL_VERSION", "SUPPORTED_PROTOCOL_VERSIONS"):
            with self.subTest(name=name):
                self.assertIn(name, mcp_package.__all__)
                self.assertIs(getattr(mcp_package, name), getattr(server_module, name))

    def test_the_server_module_exports_what_it_lists(self) -> None:
        for name in server_module.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(server_module, name))
        for name in (
            "LATEST_PROTOCOL_VERSION",
            "LEGACY_PROTOCOL_VERSIONS",
            "SUPPORTED_PROTOCOL_VERSIONS",
            "SERVER_TITLE",
            "SERVER_DESCRIPTION",
            "SERVER_WEBSITE_URL",
            "negotiate_protocol_version",
            "server_info",
        ):
            with self.subTest(public=name):
                self.assertIn(name, server_module.__all__)


if __name__ == "__main__":
    unittest.main()
