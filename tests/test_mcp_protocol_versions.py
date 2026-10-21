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
"""

from __future__ import annotations

import copy
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
    TOOL_TITLES,
    TOOLS,
    SmartDriveMCPServer,
    negotiate_protocol_version,
    server_info,
    tools_for_version,
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

    def restart(self) -> None:
        """A new server process on the same drive: it has negotiated nothing yet."""
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

    def list_line(self, msg_id: int = 2) -> str:
        return self.line({"jsonrpc": "2.0", "id": msg_id, "method": "tools/list"})

    def call_line(self, name: str, arguments: Dict[str, Any], msg_id: int = 2) -> str:
        params = {"name": name, "arguments": arguments}
        return self.line({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": params})


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
            "TOOL_TITLES",
            "negotiate_protocol_version",
            "server_info",
            "tools_for_version",
        ):
            with self.subTest(public=name):
                self.assertIn(name, server_module.__all__)


if __name__ == "__main__":
    unittest.main()
