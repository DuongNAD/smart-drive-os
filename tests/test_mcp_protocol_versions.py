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
- `tools/list` and `tools/call` ignored the revision, so tools had no display names and a result carried only its JSON
  text. `tools/list` now adds `annotations.title` (2025-03-26) and a top-level `title` (2025-06-18) to a copy of the
  catalog, and a successful `tools/call` adds `structuredContent` (2025-06-18), the object the text holds. TOOLS stays
  the 2024-11-05 catalog and is never changed, error results are the same in every revision, and a 2024-11-05 client
  (or one that never initialized) gets the bytes it always got.
- MCP 2026-07-28 has no `initialize`: each request names its revision and the client's capabilities in `params._meta`.
  The server now serves such a request beside the handshake of the older revisions, in the same process and without
  keeping any state for it. Its results carry `resultType` and the server's identity in `_meta`, `server/discover`
  lists the revisions served, `tools/list` and `server/discover` carry `ttlMs` and `cacheScope`, a request that names
  another revision gets -32022 and one with a missing or malformed envelope -32602, `ping` no longer exists, and
  nothing a 2026-07-28 request does reads or changes what `initialize` negotiated.
"""

from __future__ import annotations

import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import smart_drive.mcp as mcp_package
from smart_drive.mcp import server as server_module
from smart_drive.mcp.server import (
    CACHE_TTL_MS,
    LATEST_PROTOCOL_VERSION,
    LEGACY_PROTOCOL_VERSIONS,
    META_CLIENT_CAPABILITIES,
    META_PROTOCOL_VERSION,
    META_SERVER_INFO,
    MODERN_PROTOCOL_VERSIONS,
    PROTOCOL_VERSION,
    SERVER_DESCRIPTION,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    SERVER_TITLE,
    SERVER_VERSION,
    SERVER_WEBSITE_URL,
    SUPPORTED_PROTOCOL_VERSIONS,
    TOOL_TITLES,
    TOOLS,
    UNSUPPORTED_PROTOCOL_VERSION_CODE,
    SmartDriveMCPServer,
    negotiate_protocol_version,
    server_info,
    tools_for_version,
)

SERVER_JSON = Path(__file__).resolve().parent.parent / "server.json"
_ABSENT = object()  # "leave the key out", which None cannot say
MODERN = "2026-07-28"
# What every 2026-07-28 request carries in params._meta: the revision and the client's capabilities (none, here).
ENVELOPE: Dict[str, Any] = {META_PROTOCOL_VERSION: MODERN, META_CLIENT_CAPABILITIES: {}}

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
# 2026-07-28 names the server in the _meta of every result, with every field 2025-11-25 added.
MODERN_SERVER_INFO: Dict[str, str] = SERVER_INFO_BY_REVISION["2025-11-25"]

# What each revision adds to the tools: a title inside each tool's annotations, a title at the top of each tool, and
# `structuredContent` in a successful tools/call result. A revision keeps everything the one before it added.
ADDITIONS_BY_REVISION: Dict[str, Dict[str, bool]] = {
    "2024-11-05": {"annotations_title": False, "title": False, "structured_content": False},
    "2025-03-26": {"annotations_title": True, "title": False, "structured_content": False},
    "2025-06-18": {"annotations_title": True, "title": True, "structured_content": True},
    "2025-11-25": {"annotations_title": True, "title": True, "structured_content": True},
}


def without_titles(tool: Dict[str, Any]) -> Dict[str, Any]:
    """A tool as 2024-11-05 lists it: the entry minus the titles a later revision added."""
    plain = {key: value for key, value in tool.items() if key != "title"}
    plain["annotations"] = {key: value for key, value in tool["annotations"].items() if key != "title"}
    return plain


class _Server(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_mcp_ver_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def restart(self, **options: Any) -> None:
        """A new server process on the same drive: it has negotiated nothing yet."""
        self.server = SmartDriveMCPServer(root=str(self.root), **{"rate_limit_enabled": False, **options})

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

    def list_line(self, msg_id: int = 2) -> str:
        return self.line({"jsonrpc": "2.0", "id": msg_id, "method": "tools/list"})

    def call_line(self, name: str, arguments: Dict[str, Any], msg_id: int = 2) -> str:
        params = {"name": name, "arguments": arguments}
        return self.line({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params})

    def modern_line(
        self, method: str, params: Optional[Dict[str, Any]] = None, msg_id: Any = 1, meta: Any = _ABSENT
    ) -> str:
        """A request in the 2026-07-28 style: the revision and capabilities ride in `params._meta`, no initialize.

        `meta` replaces the whole `_meta` (to break the envelope or to add a token); `msg_id=_ABSENT` sends no id.
        """
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if msg_id is not _ABSENT:
            message["id"] = msg_id
        message["params"] = {**(params or {}), "_meta": dict(ENVELOPE) if meta is _ABSENT else meta}
        return self.line(message)


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


class TestToolCatalog(_Server):
    def listed(self, version: Any = _ABSENT) -> List[Dict[str, Any]]:
        """The tools `tools/list` answers, after an `initialize` for `version` unless there is none."""
        if version is not _ABSENT:
            self.initialize({"protocolVersion": version})
        (reply,) = self.talk(self.list_line())
        return reply["result"]["tools"]

    def test_every_revision_has_a_row(self) -> None:
        self.assertEqual(set(ADDITIONS_BY_REVISION), set(LEGACY_PROTOCOL_VERSIONS))

    def test_2024_11_05_is_tools_itself_and_before_initialize_the_server_answers_with_it(self) -> None:
        self.assertIs(tools_for_version("2024-11-05"), TOOLS)
        self.assertEqual(self.listed(), TOOLS)

    def test_the_tools_list_line_for_2024_11_05_is_what_it_was_before_the_negotiation(self) -> None:
        expected = json.dumps(
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": TOOLS}}, separators=(",", ":"), ensure_ascii=False
        )
        preludes = (
            ("before initialize", ()),
            ("after initialize 2024-11-05", (self.initialize_line({"protocolVersion": "2024-11-05"}),)),
            ("after initialize naming nothing", (self.initialize_line({}),)),
        )
        for label, prelude in preludes:
            with self.subTest(client=label):
                self.restart()
                lines = self.talk_raw(*prelude, self.list_line()).splitlines()
                self.assertEqual(len(lines), len(prelude) + 1)
                self.assertEqual(lines[-1], expected)

    def test_each_revision_adds_titles_and_nothing_else(self) -> None:
        for version, adds in ADDITIONS_BY_REVISION.items():
            with self.subTest(version=version):
                tools = self.listed(version)
                self.assertEqual(tools, json.loads(json.dumps(tools_for_version(version))))
                self.assertEqual([tool["name"] for tool in tools], [tool["name"] for tool in TOOLS])
                for tool, original in zip(tools, TOOLS):
                    self.assertEqual(without_titles(tool), original)  # the hints stay at the top level too
                    self.assertEqual("title" in tool["annotations"], adds["annotations_title"])
                    self.assertEqual("title" in tool, adds["title"])
                    if adds["annotations_title"]:
                        self.assertEqual(tool["annotations"]["title"], TOOL_TITLES[original["name"]])
                        self.assertEqual(list(tool["annotations"]), ["title"] + list(original["annotations"]))
                    if adds["title"]:
                        self.assertEqual(tool["title"], TOOL_TITLES[original["name"]])
                        self.assertEqual(list(tool)[:2], ["name", "title"])
                    self.assertEqual([key for key in tool if key != "title"], list(original))

    def test_building_a_catalog_never_changes_tools(self) -> None:
        before = copy.deepcopy(TOOLS)
        for version in LEGACY_PROTOCOL_VERSIONS:
            with self.subTest(version=version):
                catalog = tools_for_version(version)
                if catalog is TOOLS:  # 2024-11-05 hands out the catalog itself, which is not for editing
                    continue
                self.assertIsNot(catalog, tools_for_version(version))  # nobody's edits can reach the next reply
                for entry, original in zip(catalog, TOOLS):
                    self.assertIsNot(entry, original)
                    self.assertIsNot(entry["annotations"], original["annotations"])
                    entry["annotations"]["title"] = "edited"
                    entry["name"] = "edited"
                    entry.pop("title", None)
                catalog.clear()
        self.assertEqual(TOOLS, before)

    def test_titles_cover_exactly_the_tools(self) -> None:
        self.assertEqual(set(TOOL_TITLES), {tool["name"] for tool in TOOLS})
        for name, title in TOOL_TITLES.items():
            with self.subTest(tool=name):
                self.assertIsInstance(title, str)
                self.assertTrue(title.strip())
        self.assertEqual(len(set(TOOL_TITLES.values())), len(TOOL_TITLES))  # a title tells two tools apart


class TestToolResults(_Server):
    SAFE_PATH = {"path": "01_AI_Models/model.gguf"}

    def call(self, name: str, arguments: Dict[str, Any], version: Any = _ABSENT) -> Dict[str, Any]:
        """The result of one `tools/call`, after an `initialize` for `version` unless there is none."""
        if version is not _ABSENT:
            self.initialize({"protocolVersion": version})
        (reply,) = self.talk(self.call_line(name, arguments))
        return reply["result"]

    def test_a_successful_result_gains_structured_content_in_the_revisions_that_define_it(self) -> None:
        baseline = self.call("ssd_check_safety", self.SAFE_PATH, "2024-11-05")
        self.assertEqual(list(baseline), ["content"])
        self.assertEqual([item["type"] for item in baseline["content"]], ["text"])
        for version, adds in ADDITIONS_BY_REVISION.items():
            with self.subTest(version=version):
                result = self.call("ssd_check_safety", self.SAFE_PATH, version)
                self.assertEqual(result["content"], baseline["content"])  # the text item is the one it always was
                if adds["structured_content"]:
                    self.assertEqual(list(result), ["content", "structuredContent"])
                    self.assertIsInstance(result["structuredContent"], dict)
                    self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
                    self.assertIs(result["structuredContent"]["is_safe"], True)
                else:
                    self.assertEqual(list(result), ["content"])

    def test_before_initialize_a_result_is_only_content(self) -> None:
        self.assertEqual(list(self.call("ssd_check_safety", self.SAFE_PATH)), ["content"])

    def test_the_result_line_before_2025_06_18_is_what_it_was_before_the_negotiation(self) -> None:
        report = self.server.handle_ssd_check_safety(self.SAFE_PATH)
        text = json.dumps(report, separators=(",", ":"), ensure_ascii=False)
        expected = json.dumps(
            {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": text}]}},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        preludes = (
            ("before initialize", ()),
            ("after initialize 2024-11-05", (self.initialize_line({"protocolVersion": "2024-11-05"}),)),
            ("after initialize 2025-03-26", (self.initialize_line({"protocolVersion": "2025-03-26"}),)),
        )
        for label, prelude in preludes:
            with self.subTest(client=label):
                self.restart()
                lines = self.talk_raw(*prelude, self.call_line("ssd_check_safety", self.SAFE_PATH)).splitlines()
                self.assertEqual(len(lines), len(prelude) + 1)
                self.assertEqual(lines[-1], expected)

    def test_error_results_are_the_same_in_every_revision(self) -> None:
        calls = (("no_such_tool", {}), ("ssd_audit", {"sub_dir": "../../outside"}))
        first: Dict[str, Dict[str, Any]] = {}
        for version in LEGACY_PROTOCOL_VERSIONS:
            for name, arguments in calls:
                with self.subTest(version=version, tool=name):
                    with self.assertLogs("smart_drive.mcp.server", level="WARNING"):
                        result = self.call(name, arguments, version)
                    self.assertEqual(list(result), ["isError", "content"])
                    self.assertIs(result["isError"], True)
                    self.assertEqual(result, first.setdefault(name, result))


class TestModernDiscover(_Server):
    def test_discover_says_what_is_served_and_how_long_the_answer_may_be_kept(self) -> None:
        (reply,) = self.talk(self.modern_line("server/discover", msg_id="d-1"))
        self.assertEqual(reply["id"], "d-1")
        result = reply["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], ["2026-07-28"])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["instructions"], SERVER_INSTRUCTIONS)
        self.assertEqual(result["ttlMs"], CACHE_TTL_MS)
        self.assertIsInstance(result["ttlMs"], int)
        self.assertGreaterEqual(result["ttlMs"], 0)
        self.assertEqual(result["cacheScope"], "public")
        self.assertEqual(result["_meta"], {META_SERVER_INFO: MODERN_SERVER_INFO})

    def test_the_discover_line_is_exactly_this(self) -> None:
        expected = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "resultType": "complete",
                    "supportedVersions": ["2026-07-28"],
                    "capabilities": {"tools": {}},
                    "instructions": SERVER_INSTRUCTIONS,
                    "ttlMs": CACHE_TTL_MS,
                    "cacheScope": "public",
                    "_meta": {"io.modelcontextprotocol/serverInfo": MODERN_SERVER_INFO},
                },
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.assertEqual(self.talk_raw(self.modern_line("server/discover")), expected + "\n")

    def test_discover_accepts_any_object_as_the_client_capabilities(self) -> None:
        for capabilities in ({}, {"roots": {"listChanged": True}}, {"experimental": {"x": 1}}):
            with self.subTest(capabilities=capabilities):
                meta = {META_PROTOCOL_VERSION: MODERN, META_CLIENT_CAPABILITIES: capabilities}
                (reply,) = self.talk(self.modern_line("server/discover", meta=meta))
                self.assertEqual(reply["result"]["resultType"], "complete")


class TestModernEnvelope(_Server):
    def error_reply(self, method: str = "tools/list", **options: Any) -> Dict[str, Any]:
        """The one reply to a 2026-07-28 request, which must be an error response.

        `tools/list` is the default method because, if the request were not taken for a 2026-07-28 one, it would
        succeed; a `server/discover` would be refused for its missing envelope, which looks much the same.
        """
        (reply,) = self.talk(self.modern_line(method, **options))
        self.assertNotIn("result", reply)
        return reply

    def test_a_revision_not_served_this_way_is_32022_with_the_ones_that_are(self) -> None:
        """A legacy revision is reached through `initialize`, so naming one in the envelope is unsupported too."""
        for version in ("2099-01-01", "2025-11-25", "2025-06-18", "2024-11-05", "1.0.0", ""):
            with self.subTest(version=version):
                reply = self.error_reply(msg_id="req-7", meta={**ENVELOPE, META_PROTOCOL_VERSION: version})
                self.assertEqual(
                    reply,
                    {
                        "jsonrpc": "2.0",
                        "id": "req-7",
                        "error": {
                            "code": -32022,
                            "message": "Unsupported protocol version",
                            "data": {"supported": ["2026-07-28"], "requested": version},
                        },
                    },
                )

    def test_every_method_has_its_revision_checked_first(self) -> None:
        for method in ("tools/list", "tools/call", "ping", "auth/handshake", "no/such/method"):
            with self.subTest(method=method):
                reply = self.error_reply(method, meta={**ENVELOPE, META_PROTOCOL_VERSION: "2099-01-01"})
                self.assertEqual(reply["error"]["code"], -32022)

    def test_a_revision_that_is_not_a_string_is_an_invalid_param_naming_the_key(self) -> None:
        for version in (None, 20260728, True, ["2026-07-28"], {"version": "2026-07-28"}):
            with self.subTest(version=version):
                reply = self.error_reply(msg_id=3, meta={**ENVELOPE, META_PROTOCOL_VERSION: version})
                self.assertEqual(reply["id"], 3)
                self.assertEqual(reply["error"]["code"], -32602)
                self.assertTrue(reply["error"]["message"].startswith("Invalid params"))
                self.assertIn(META_PROTOCOL_VERSION, reply["error"]["message"])
                self.assertIn("must be a string", reply["error"]["message"])
                self.assertNotIn("data", reply["error"])

    def test_client_capabilities_missing_or_not_an_object_are_an_invalid_param_naming_the_key(self) -> None:
        only_version = {META_PROTOCOL_VERSION: MODERN}
        cases = (
            ("missing", only_version),
            ("null", {**only_version, META_CLIENT_CAPABILITIES: None}),
            ("a string", {**only_version, META_CLIENT_CAPABILITIES: "roots"}),
            ("a list", {**only_version, META_CLIENT_CAPABILITIES: []}),
            ("a number", {**only_version, META_CLIENT_CAPABILITIES: 1}),
        )
        for label, meta in cases:
            with self.subTest(capabilities=label):
                reply = self.error_reply(msg_id=4, meta=meta)
                self.assertEqual(reply["id"], 4)
                self.assertEqual(reply["error"]["code"], -32602)
                self.assertTrue(reply["error"]["message"].startswith("Invalid params"))
                self.assertIn(META_CLIENT_CAPABILITIES, reply["error"]["message"])
                self.assertIn("must be an object", reply["error"]["message"])

    def test_the_revision_is_checked_before_the_rest_of_the_envelope(self) -> None:
        """The envelope of a revision this server does not know may follow rules it does not know."""
        self.assertEqual(self.error_reply(meta={META_PROTOCOL_VERSION: "2099-01-01"})["error"]["code"], -32022)
        reply = self.error_reply(meta={META_PROTOCOL_VERSION: 5})
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertIn(META_PROTOCOL_VERSION, reply["error"]["message"])

    def test_a_request_without_the_revision_key_is_not_a_2026_request_and_is_answered_as_before(self) -> None:
        cases = (
            ("only the capabilities", {META_CLIENT_CAPABILITIES: {}}),
            ("an empty _meta", {}),
            ("a progress token", {"progressToken": "p1"}),
            ("a string", MODERN),
            ("a list", [META_PROTOCOL_VERSION]),
            ("null", None),
        )
        for label, meta in cases:
            with self.subTest(_meta=label):
                (reply,) = self.talk(self.modern_line("tools/list", meta=meta))
                self.assertEqual(reply["result"], {"tools": TOOLS})

    def test_a_request_without_an_id_gets_no_reply_whatever_is_wrong_with_it(self) -> None:
        broken_version = {**ENVELOPE, META_PROTOCOL_VERSION: "2099-01-01"}
        requests = (
            self.modern_line("notifications/cancelled", msg_id=_ABSENT),
            self.modern_line("tools/list", msg_id=None),  # a null id is no id either
            self.modern_line("server/discover", msg_id=_ABSENT, meta=broken_version),
            self.modern_line("server/discover", msg_id=_ABSENT, meta={META_PROTOCOL_VERSION: 5}),
            self.modern_line("server/discover", msg_id=_ABSENT, meta={META_PROTOCOL_VERSION: MODERN}),
        )
        self.assertEqual(self.talk(*requests), [])

    def test_a_request_without_an_id_is_not_carried_out(self) -> None:
        message = {"name": "ssd_status", "arguments": {}}
        with patch.object(self.server, "dispatch_tool") as dispatch:
            replies = self.talk(self.modern_line("tools/call", message, msg_id=_ABSENT))
        self.assertEqual(replies, [])
        dispatch.assert_not_called()
        self.assertIsNone(self.server.handle_request(json.loads(self.modern_line("tools/list", msg_id=_ABSENT))))


class TestModernResults(_Server):
    def test_tools_list_is_the_2026_catalog_with_the_cache_hints(self) -> None:
        (reply,) = self.talk(self.modern_line("tools/list"))
        result = reply["result"]
        self.assertEqual(list(result), ["resultType", "tools", "ttlMs", "cacheScope", "_meta"])
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["tools"], json.loads(json.dumps(tools_for_version(MODERN))))
        self.assertEqual(result["ttlMs"], CACHE_TTL_MS)
        self.assertEqual(result["cacheScope"], "public")
        self.assertEqual(result["_meta"], {META_SERVER_INFO: MODERN_SERVER_INFO})
        for tool in result["tools"]:
            self.assertEqual(tool["title"], TOOL_TITLES[tool["name"]])
            self.assertEqual(tool["annotations"]["title"], TOOL_TITLES[tool["name"]])

    def test_a_successful_call_has_the_structured_content_and_the_identity(self) -> None:
        arguments = {"path": "01_AI_Models/model.gguf"}
        report = self.server.handle_ssd_check_safety(arguments)
        (reply,) = self.talk(self.modern_line("tools/call", {"name": "ssd_check_safety", "arguments": arguments}))
        result = reply["result"]
        self.assertEqual(list(result), ["resultType", "content", "structuredContent", "_meta"])
        self.assertEqual(result["resultType"], "complete")
        text = json.dumps(report, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(result["content"], [{"type": "text", "text": text}])
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertEqual(result["_meta"], {META_SERVER_INFO: MODERN_SERVER_INFO})

    def test_an_unknown_tool_is_a_complete_result_that_says_it_is_an_error(self) -> None:
        with self.assertLogs("smart_drive.mcp.server", level="WARNING"):
            (reply,) = self.talk(self.modern_line("tools/call", {"name": "no_such_tool", "arguments": {}}))
        result = reply["result"]
        self.assertEqual(list(result), ["resultType", "isError", "content", "_meta"])
        self.assertEqual(result["resultType"], "complete")
        self.assertIs(result["isError"], True)
        self.assertIn("Unknown tool", result["content"][0]["text"])
        self.assertEqual(result["_meta"], {META_SERVER_INFO: MODERN_SERVER_INFO})

    def test_error_responses_are_not_decorated(self) -> None:
        cases = (
            ("an unknown method", "no/such/method", {}, -32601),
            ("ping", "ping", {}, -32601),
            ("arguments that are not an object", "tools/call", {"name": "ssd_status", "arguments": [1]}, -32602),
        )
        for label, method, params, code in cases:
            with self.subTest(error=label):
                (reply,) = self.talk(self.modern_line(method, params))
                self.assertEqual(set(reply), {"jsonrpc", "id", "error"})
                self.assertEqual(reply["error"]["code"], code)

    def test_ping_is_gone_in_2026_07_28_but_still_answered_in_the_older_revisions(self) -> None:
        (modern,) = self.talk(self.modern_line("ping", msg_id=7))
        unhandled = {"code": -32601, "message": "Unhandled method 'ping'"}
        self.assertEqual(modern, {"jsonrpc": "2.0", "id": 7, "error": unhandled})
        for version in LEGACY_PROTOCOL_VERSIONS:
            with self.subTest(version=version):
                self.initialize({"protocolVersion": version})
                (legacy,) = self.talk(self.line({"jsonrpc": "2.0", "id": 8, "method": "ping"}))
                self.assertEqual(legacy, {"jsonrpc": "2.0", "id": 8, "result": {}})
                (still_gone,) = self.talk(self.modern_line("ping", msg_id=9))  # a 2026 ping stays unknown
                self.assertEqual(still_gone, {"jsonrpc": "2.0", "id": 9, "error": unhandled})

    def test_handle_request_returns_the_response_it_sent(self) -> None:
        lines = (
            self.modern_line("server/discover"),
            self.modern_line("tools/list"),
            self.modern_line("ping"),
            self.modern_line("server/discover", meta={**ENVELOPE, META_PROTOCOL_VERSION: "2099-01-01"}),
        )
        for index, line in enumerate(lines):
            with self.subTest(request=index):
                stdout = io.StringIO()
                with patch("sys.stdout", stdout):
                    returned = self.server.handle_request(json.loads(line))
                self.assertEqual(json.loads(stdout.getvalue()), returned)


class TestModernBesideLegacy(_Server):
    def test_a_modern_request_neither_reads_nor_changes_what_initialize_negotiated(self) -> None:
        for version in LEGACY_PROTOCOL_VERSIONS:
            with self.subTest(version=version):
                self.restart()
                _, modern_list, legacy_list = self.talk(
                    self.initialize_line({"protocolVersion": version}, msg_id=1),
                    self.modern_line("tools/list", msg_id=2),
                    self.list_line(msg_id=3),
                )
                self.assertEqual(modern_list["result"]["resultType"], "complete")
                self.assertEqual(modern_list["result"]["tools"], json.loads(json.dumps(tools_for_version(MODERN))))
                self.assertNotIn("resultType", legacy_list["result"])
                self.assertEqual(legacy_list["result"]["tools"], json.loads(json.dumps(tools_for_version(version))))
                self.assertEqual(self.server.protocol_version, version)

    def test_after_initialize_2024_11_05_the_legacy_reply_is_still_exactly_the_old_one(self) -> None:
        _, modern_list, legacy_list = self.talk(
            self.initialize_line({"protocolVersion": "2024-11-05"}, msg_id=1),
            self.modern_line("tools/list", msg_id=2),
            self.list_line(msg_id=3),
        )
        self.assertEqual(modern_list["result"]["resultType"], "complete")
        self.assertEqual(legacy_list["result"], {"tools": TOOLS})
        self.assertEqual(self.server.protocol_version, "2024-11-05")

    def test_a_modern_request_before_initialize_leaves_the_server_on_the_default_revision(self) -> None:
        self.talk(self.modern_line("tools/list"), self.modern_line("server/discover", msg_id=2))
        self.assertEqual(self.server.protocol_version, PROTOCOL_VERSION)
        (reply,) = self.talk(self.list_line())
        self.assertEqual(reply["result"], {"tools": TOOLS})

    def test_initialize_after_modern_requests_negotiates_as_usual(self) -> None:
        self.talk(self.modern_line("tools/list"))
        result = self.initialize({"protocolVersion": "2025-06-18"})
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(self.server.protocol_version, "2025-06-18")

    def test_an_initialize_carrying_the_envelope_is_still_the_handshake(self) -> None:
        """It selects an older revision, so it is negotiated from params.protocolVersion and never validated as 2026."""
        unsupported = {**ENVELOPE, META_PROTOCOL_VERSION: "2099-01-01"}
        cases = (
            ({"protocolVersion": "2025-06-18", "_meta": dict(ENVELOPE)}, "2025-06-18"),
            ({"protocolVersion": "2024-11-05", "_meta": unsupported}, "2024-11-05"),
            ({"_meta": dict(ENVELOPE)}, PROTOCOL_VERSION),
        )
        for params, expected in cases:
            with self.subTest(params=params):
                result = self.initialize(params)
                self.assertEqual(list(result), ["protocolVersion", "serverInfo", "capabilities", "instructions"])
                self.assertEqual(result["protocolVersion"], expected)
                self.assertEqual(self.server.protocol_version, expected)

    def test_a_server_discover_without_the_envelope_is_an_invalid_param(self) -> None:
        discover = {"jsonrpc": "2.0", "id": 5, "method": "server/discover"}
        for params in (_ABSENT, None, {}, {"_meta": {META_CLIENT_CAPABILITIES: {}}}):
            with self.subTest(params=params):
                message = dict(discover) if params is _ABSENT else {**discover, "params": params}
                (reply,) = self.talk(self.line(message))
                self.assertEqual(reply["id"], 5)
                self.assertEqual(reply["error"]["code"], -32602)
                self.assertNotIn("result", reply)
                self.assertIn(META_PROTOCOL_VERSION, reply["error"]["message"])
                self.assertIn(META_CLIENT_CAPABILITIES, reply["error"]["message"])
        self.assertEqual(self.talk(self.line({"jsonrpc": "2.0", "method": "server/discover"})), [])

    def test_legacy_replies_are_still_what_they_were(self) -> None:
        refusal = "Error executing tool 'no_such_tool': Unknown tool 'no_such_tool'"
        unknown_tool = '{"jsonrpc":"2.0","id":10,"result":{"isError":true,"content":[{"type":"text","text":"%s"}]}}'
        cases = (
            ({"jsonrpc": "2.0", "id": 7, "method": "ping"}, '{"jsonrpc":"2.0","id":7,"result":{}}'),
            (
                {"jsonrpc": "2.0", "id": 8, "method": "no/such"},
                '{"jsonrpc":"2.0","id":8,"error":{"code":-32601,"message":"Unhandled method \'no/such\'"}}',
            ),
            (
                {"jsonrpc": "2.0", "id": 9, "method": "auth/handshake"},
                '{"jsonrpc":"2.0","id":9,"result":{"status":"authenticated","authenticated":true}}',
            ),
            (
                {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "no_such_tool"}},
                unknown_tool % refusal,
            ),
        )
        with self.assertLogs("smart_drive.mcp.server", level="WARNING"):  # the unknown tool is logged as a refusal
            for request, expected in cases:
                with self.subTest(method=request["method"]):
                    self.restart()
                    self.assertEqual(self.talk_raw(self.line(request)), expected + "\n")


class TestModernAuthAndLimits(_Server):
    TOKEN = "s3cret-token"

    def lock(self, **options: Any) -> None:
        self.restart(auth_token=self.TOKEN, require_auth=True, **options)

    def test_discover_needs_no_token_but_the_other_methods_do(self) -> None:
        self.lock()
        discover, refused, allowed = self.talk(
            self.modern_line("server/discover", msg_id=1),
            self.modern_line("tools/list", msg_id=2),
            self.modern_line("tools/list", msg_id=3, meta={**ENVELOPE, "authToken": self.TOKEN}),
        )
        self.assertEqual(discover["result"]["resultType"], "complete")
        self.assertEqual(
            refused,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "error": {
                    "code": -32001,
                    "message": "Authentication required: Missing or invalid authentication token",
                    "data": {"authenticated": False},
                },
            },
        )
        self.assertEqual(allowed["result"]["resultType"], "complete")
        self.assertIn("tools", allowed["result"])

    def test_a_token_in_a_modern_request_unlocks_the_whole_process(self) -> None:
        self.lock()
        self.talk(self.modern_line("tools/list", meta={**ENVELOPE, "authToken": self.TOKEN}))
        (legacy,) = self.talk(self.list_line())  # no token, and not in the 2026 style
        self.assertEqual(legacy["result"], {"tools": TOOLS})

    def test_a_modern_handshake_is_answered_and_its_result_decorated(self) -> None:
        self.lock()
        wrong, right = self.talk(
            self.modern_line("auth/handshake", msg_id=1, meta={**ENVELOPE, "token": "wrong"}),
            self.modern_line("auth/handshake", msg_id=2, meta={**ENVELOPE, "token": self.TOKEN}),
        )
        self.assertEqual(set(wrong), {"jsonrpc", "id", "error"})
        self.assertEqual(wrong["error"]["code"], -32001)
        self.assertEqual(
            right["result"],
            {
                "resultType": "complete",
                "status": "authenticated",
                "authenticated": True,
                "_meta": {META_SERVER_INFO: MODERN_SERVER_INFO},
            },
        )
        self.restart()  # with no token configured the handshake needs none
        (open_server,) = self.talk(self.modern_line("auth/handshake"))
        self.assertEqual(open_server["result"]["resultType"], "complete")
        self.assertEqual(open_server["result"]["status"], "authenticated")

    def test_a_server_discover_without_the_envelope_is_refused_for_its_shape_not_for_a_missing_token(self) -> None:
        self.lock()
        (reply,) = self.talk(self.line({"jsonrpc": "2.0", "id": 1, "method": "server/discover"}))
        self.assertEqual(reply["error"]["code"], -32602)

    def test_the_token_is_read_before_the_envelope_is_checked(self) -> None:
        self.lock()
        meta = {**ENVELOPE, META_PROTOCOL_VERSION: "2099-01-01", "authToken": self.TOKEN}
        refused, legacy = self.talk(self.modern_line("server/discover", msg_id=1, meta=meta), self.list_line(msg_id=2))
        self.assertEqual(refused["error"]["code"], -32022)
        self.assertEqual(legacy["result"], {"tools": TOOLS})  # the refused request's token still unlocked the process

    def test_the_rate_limit_comes_before_the_envelope_checks(self) -> None:
        self.restart(rate_limit_enabled=True, rate_limit_requests=1, rate_limit_window=60.0)
        first, second = self.talk(
            self.modern_line("server/discover", msg_id=1),
            self.modern_line("server/discover", msg_id=2, meta={**ENVELOPE, META_PROTOCOL_VERSION: "2099-01-01"}),
        )
        self.assertEqual(first["result"]["resultType"], "complete")
        self.assertEqual(second["id"], 2)
        self.assertEqual(second["error"]["code"], -32000)
        self.assertEqual(set(second), {"jsonrpc", "id", "error"})


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

    def test_modern_revisions_are_the_ones_without_initialize_and_supported_is_both_kinds(self) -> None:
        self.assertEqual(MODERN_PROTOCOL_VERSIONS, ("2026-07-28",))
        self.assertTrue(set(MODERN_PROTOCOL_VERSIONS).isdisjoint(LEGACY_PROTOCOL_VERSIONS))
        self.assertEqual(SUPPORTED_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS + LEGACY_PROTOCOL_VERSIONS)
        self.assertEqual(LATEST_PROTOCOL_VERSION, "2026-07-28")

    def test_the_names_the_2026_07_28_specification_gives_the_envelope_and_the_error(self) -> None:
        self.assertEqual(META_PROTOCOL_VERSION, "io.modelcontextprotocol/protocolVersion")
        self.assertEqual(META_CLIENT_CAPABILITIES, "io.modelcontextprotocol/clientCapabilities")
        self.assertEqual(META_SERVER_INFO, "io.modelcontextprotocol/serverInfo")
        self.assertEqual(UNSUPPORTED_PROTOCOL_VERSION_CODE, -32022)

    def test_the_cache_time_is_a_whole_number_of_milliseconds(self) -> None:
        self.assertIsInstance(CACHE_TTL_MS, int)
        self.assertGreaterEqual(CACHE_TTL_MS, 0)

    def test_a_later_revision_keeps_every_field_the_earlier_ones_added(self) -> None:
        self.assertEqual(server_info(MODERN), MODERN_SERVER_INFO)
        self.assertEqual(list(server_info(MODERN)), list(MODERN_SERVER_INFO))
        for tool in tools_for_version(MODERN):
            self.assertEqual(tool["title"], TOOL_TITLES[tool["name"]])
            self.assertEqual(tool["annotations"]["title"], TOOL_TITLES[tool["name"]])

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
            "MODERN_PROTOCOL_VERSIONS",
            "SUPPORTED_PROTOCOL_VERSIONS",
            "SERVER_TITLE",
            "SERVER_DESCRIPTION",
            "SERVER_WEBSITE_URL",
            "META_PROTOCOL_VERSION",
            "META_CLIENT_CAPABILITIES",
            "META_SERVER_INFO",
            "UNSUPPORTED_PROTOCOL_VERSION_CODE",
            "CACHE_TTL_MS",
            "TOOL_TITLES",
            "negotiate_protocol_version",
            "server_info",
            "tools_for_version",
        ):
            with self.subTest(public=name):
                self.assertIn(name, server_module.__all__)


if __name__ == "__main__":
    unittest.main()
