"""tests/test_mcp_stdio_revisions.py - The real MCP server, started the way a client starts it, in every revision.

The other MCP tests drive `run_stdio` inside the test process. This file starts the program as a separate process, like
an MCP client does and like packaging/smoke_test.py does for a release: `python -m smart_drive mcp --root <drive>` from
a folder that is neither the checkout nor the drive, newline-delimited JSON-RPC 2.0 on stdin, stdin closed at the end.
So what an in-process test cannot see is covered for each revision the server speaks: the `-m smart_drive mcp` entry
point, the framing, a clean exit when stdin closes, and stdout carrying nothing but MCP messages (logs belong on stderr,
and no id may be answered twice).

Each run gets a temporary home folder (HOME, USERPROFILE, APPDATA, LOCALAPPDATA and XDG_CONFIG_HOME point into it), and
the test fails if the server created anything there: it must never touch the config files of a coding agent. The
environment is this process', minus the variables that change which drive the server works on, whether it asks for a
token, or where uv and pipx keep their state.

Sessions:
- One per revision that opens with `initialize`, and one for a revision the server does not know: initialize,
  notifications/initialized, tools/list, a tools/call and a batch. What the replies show depends on the revision: tool
  titles, structuredContent, and whether the batch is answered with an array or refused with one error.
- One in the 2026-07-28 style, with no initialize and the envelope on every request, including one that names a revision
  the server does not serve.
- One process that speaks both: requests before initialize, a handshake, and 2026-07-28 requests in between, none of
  which changes what the handshake chose.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from smart_drive.mcp.server import (
    LEGACY_PROTOCOL_VERSIONS,
    MODERN_PROTOCOL_VERSIONS,
    SERVER_DESCRIPTION,
    SERVER_NAME,
    SERVER_TITLE,
    SERVER_VERSION,
    SERVER_WEBSITE_URL,
    TOOLS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 120  # seconds for one server process: a hung server fails its test instead of stalling the run
MODERN = MODERN_PROTOCOL_VERSIONS[0]
UNKNOWN = "2099-01-01"
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
SAFE_CALL = {"name": "ssd_check_safety", "arguments": {"path": "01_AI_Models/model.gguf"}}
# The variables a program reads to find the user's home; the sandbox points all of them into its own folder.
HOME_VARIABLES = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME")

# What a client of each revision sees: a title in each tool's annotations, a title at the top of each tool,
# structuredContent in a tools/call result, and whether a JSON-RPC batch is answered (with an array) or refused.
SEES: Dict[str, Dict[str, bool]] = {
    "2024-11-05": {"annotation_title": False, "title": False, "structured": False, "batches": True},
    "2025-03-26": {"annotation_title": True, "title": False, "structured": False, "batches": True},
    "2025-06-18": {"annotation_title": True, "title": True, "structured": True, "batches": False},
    "2025-11-25": {"annotation_title": True, "title": True, "structured": True, "batches": False},
}
# 2026-07-28 names the server in the _meta of every result.
SERVER_IDENTITY = {
    "name": SERVER_NAME,
    "version": SERVER_VERSION,
    "title": SERVER_TITLE,
    "description": SERVER_DESCRIPTION,
    "websiteUrl": SERVER_WEBSITE_URL,
}


def request(msg_id: Any, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    message: Dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def modern(msg_id: Any, method: str, params: Optional[Dict[str, Any]] = None, version: str = MODERN) -> Dict[str, Any]:
    """A request in the 2026-07-28 style: the revision and the client's capabilities ride in `params._meta`."""
    return request(msg_id, method, {**(params or {}), "_meta": {VERSION_KEY: version, CAPABILITIES_KEY: {}}})


def handshake(msg_id: Any, version: str) -> Dict[str, Any]:
    client = {"name": "stdio-revisions-test", "version": "0"}
    return request(msg_id, "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": client})


def is_inside(path: Path, folder: Path) -> bool:
    """Whether `path` is `folder` itself or lies below it, both resolved; whole path components are compared."""
    inner, outer = os.path.normcase(str(path.resolve())), os.path.normcase(str(folder.resolve()))
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:  # a path on another drive (Windows)
        return False


class Sandbox:
    """The folders of one server run: a temporary home, a drive to manage and a working directory that is neither."""

    def __init__(self, base: Path) -> None:
        self.home = base / "home"
        self.drive = base / "drive"
        self.cwd = base / "cwd"
        self.appdata = self.home / "AppData" / "Roaming"
        self.localappdata = self.home / "AppData" / "Local"
        self.xdg_config = self.home / ".config"
        for folder in (self.appdata, self.localappdata, self.xdg_config, self.drive, self.cwd):
            folder.mkdir(parents=True)
        self.home_before = self.entries(self.home)

    @staticmethod
    def entries(folder: Path) -> List[str]:
        """Everything below `folder`, as paths relative to it."""
        return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))

    def env(self) -> Dict[str, str]:
        """This process' environment with the user's home replaced by the sandbox's and the checkout on PYTHONPATH."""
        # SMART_DRIVE_* and KINGSTON_SSD_ROOT would change which drive the server works on or whether it asks for a
        # token. UV, UV_* and PIPX_* say where uv and pipx keep their state. The other two name folders of coding
        # agents. The user's own settings must not decide what is tested.
        dropped = {"ANTIGRAVITY_HOME", "GEMINI_HOME", "KINGSTON_SSD_ROOT", "UV"}
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in dropped and not key.upper().startswith(("SMART_DRIVE_", "UV_", "PIPX_"))
        }
        python_path = os.environ.get("PYTHONPATH")
        env.update(
            {
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),  # what Path.home() reads on Windows
                "APPDATA": str(self.appdata),
                "LOCALAPPDATA": str(self.localappdata),
                "XDG_CONFIG_HOME": str(self.xdg_config),
                # Without a marker folder the server probes the attached drives for a SmartDrive root; the result must
                # not depend on which drives this machine has.
                "SMART_DRIVE_NO_PROBE": "1",
                # The working directory is not the checkout, so the package is found through the path.
                "PYTHONPATH": os.pathsep.join([str(REPO_ROOT)] + ([python_path] if python_path else [])),
            }
        )
        return env


class _StdioCase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_mcp_stdio_")).resolve()
        self.addCleanup(shutil.rmtree, str(self.base), ignore_errors=True)

    def session(self, messages: List[Any]) -> Tuple[List[Any], Dict[Any, Dict[str, Any]]]:
        """Runs one server process over these messages; returns what it wrote to stdout and its replies by id.

        Fails when the process does not exit with 0, when a line of stdout is not an MCP message, when an id is answered
        twice, or when the server created anything in the temporary home folder. It starts nothing unless every
        variable that names the user's home points into the sandbox.
        """
        sandbox = Sandbox(Path(tempfile.mkdtemp(prefix="run-", dir=str(self.base))))
        env = sandbox.env()
        for name in HOME_VARIABLES:
            where = env[name]
            self.assertTrue(is_inside(Path(where), sandbox.home), f"{name} does not point into the sandbox: {where!r}")
        stdin = "".join(json.dumps(message) + "\n" for message in messages).encode("utf-8")
        process = subprocess.run(
            [sys.executable, "-m", "smart_drive", "mcp", "--root", str(sandbox.drive)],
            input=stdin,
            capture_output=True,
            cwd=str(sandbox.cwd),
            env=env,
            timeout=TIMEOUT,
        )
        stderr = process.stderr.decode("utf-8", errors="replace")
        self.assertEqual(process.returncode, 0, f"the server exited with {process.returncode}; stderr:\n{stderr}")
        documents, replies = self.read_stdout(process.stdout.decode("utf-8"), stderr)
        self.assertEqual(
            Sandbox.entries(sandbox.home), sandbox.home_before, "the server created something in the home folder"
        )
        return documents, replies

    def read_stdout(self, stdout: str, stderr: str) -> Tuple[List[Any], Dict[Any, Dict[str, Any]]]:
        """The JSON documents on stdout, one per line, and the messages in them by id."""
        documents: List[Any] = []
        replies: Dict[Any, Dict[str, Any]] = {}
        for number, line in enumerate(stdout.split("\n"), 1):
            line = line.rstrip("\r")  # a Windows text stream ends its lines with \r\n
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except ValueError:
                self.fail(f"stdout line {number} is not JSON: {line[:200]!r}\nstderr:\n{stderr}")
            messages = document if isinstance(document, list) else [document]  # a batch is answered with an array
            self.assertTrue(messages, f"stdout line {number} is an empty array")
            for message in messages:
                self.assertIsInstance(message, dict, f"stdout line {number} holds no JSON-RPC message: {line[:200]!r}")
                self.assertEqual(message.get("jsonrpc"), "2.0", f"stdout line {number} is not JSON-RPC 2.0: {line!r}")
                msg_id = message.get("id")
                if msg_id is not None:  # an error with no request to answer has the id null
                    self.assertNotIn(msg_id, replies, f"the id {msg_id!r} was answered twice")
                    replies[msg_id] = message
            documents.append(document)
        return documents, replies


class TestSessionPerRevision(_StdioCase):
    PING_BATCH = [request(4, "ping")]

    def messages(self, requested: str) -> List[Any]:
        return [
            handshake(1, requested),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            request(2, "tools/list"),
            request(3, "tools/call", SAFE_CALL),
            self.PING_BATCH,
        ]

    def check(self, requested: str, negotiated: str) -> None:
        sees = SEES[negotiated]
        documents, replies = self.session(self.messages(requested))
        self.assertEqual(len(documents), 4)  # the notification got no reply of its own

        initialized = replies[1]["result"]
        self.assertEqual(initialized["protocolVersion"], negotiated)
        self.assertEqual(initialized["serverInfo"]["name"], SERVER_NAME)
        self.assertEqual("title" in initialized["serverInfo"], sees["title"])

        tools = replies[2]["result"]["tools"]
        self.assertEqual(len(tools), 9)
        self.assertEqual({tool["name"] for tool in tools}, {tool["name"] for tool in TOOLS})
        for tool in tools:
            self.assertEqual("title" in tool, sees["title"], tool["name"])
            self.assertEqual("title" in tool["annotations"], sees["annotation_title"], tool["name"])

        called = replies[3]["result"]
        self.assertEqual("structuredContent" in called, sees["structured"])
        self.assertIs(json.loads(called["content"][0]["text"])["is_safe"], True)
        if sees["structured"]:
            self.assertEqual(json.loads(called["content"][0]["text"]), called["structuredContent"])

        arrays = [document for document in documents if isinstance(document, list)]
        if sees["batches"]:
            self.assertEqual(arrays, [[{"jsonrpc": "2.0", "id": 4, "result": {}}]])
        else:
            self.assertEqual(arrays, [])
            refusal = documents[-1]
            self.assertEqual(set(refusal), {"jsonrpc", "id", "error"})
            self.assertIsNone(refusal["id"])
            self.assertEqual(refusal["error"]["code"], -32600)
            self.assertNotIn(4, replies)  # the ping inside the refused batch never ran

    def test_every_revision_that_opens_with_initialize_has_a_row(self) -> None:
        self.assertEqual(set(SEES), set(LEGACY_PROTOCOL_VERSIONS))

    def test_a_session_in_each_revision_and_in_one_the_server_does_not_know(self) -> None:
        for requested in LEGACY_PROTOCOL_VERSIONS + (UNKNOWN,):
            negotiated = requested if requested in SEES else "2025-11-25"  # the latest revision with an initialize
            with self.subTest(requested=requested):
                self.check(requested, negotiated)


class TestModernSession(_StdioCase):
    def test_a_session_without_initialize(self) -> None:
        messages = [
            modern(1, "server/discover"),
            modern(2, "tools/list"),
            modern(3, "tools/call", SAFE_CALL),
            modern(4, "tools/list", version=UNKNOWN),
        ]
        documents, replies = self.session(messages)
        self.assertEqual(len(documents), 4)

        for msg_id in (1, 2, 3):
            result = replies[msg_id]["result"]
            self.assertEqual(result["resultType"], "complete")
            self.assertEqual(result["_meta"], {SERVER_INFO_KEY: SERVER_IDENTITY})
        for msg_id in (1, 2):  # the two answers a client may cache
            result = replies[msg_id]["result"]
            self.assertIsInstance(result["ttlMs"], int)
            self.assertGreaterEqual(result["ttlMs"], 0)
            self.assertEqual(result["cacheScope"], "public")

        discovered = replies[1]["result"]
        self.assertEqual(discovered["supportedVersions"], [MODERN])
        self.assertEqual(discovered["capabilities"], {"tools": {}})
        self.assertTrue(discovered["instructions"])

        tools = replies[2]["result"]["tools"]
        self.assertEqual(len(tools), 9)
        for tool in tools:
            self.assertIn("title", tool, tool["name"])
            self.assertIn("title", tool["annotations"], tool["name"])

        called = replies[3]["result"]
        self.assertEqual(json.loads(called["content"][0]["text"]), called["structuredContent"])
        self.assertIs(called["structuredContent"]["is_safe"], True)

        unsupported = {"supported": [MODERN], "requested": UNKNOWN}
        self.assertEqual(
            replies[4],
            {
                "jsonrpc": "2.0",
                "id": 4,
                "error": {"code": -32022, "message": "Unsupported protocol version", "data": unsupported},
            },
        )


class TestOneProcessBothEras(_StdioCase):
    def test_2026_requests_beside_the_handshake_change_nothing_about_it(self) -> None:
        """The handshake is 2025-03-26, whose tools and batches differ from 2026-07-28's: a leak would show."""
        messages = [
            request(1, "tools/list"),  # before any initialize: the oldest revision, as it always was
            modern(2, "server/discover"),
            handshake(3, "2025-03-26"),
            request(4, "tools/list"),
            modern(5, "tools/list"),
            request(6, "tools/list"),  # still what the handshake chose
            request(7, "ping"),
            modern(8, "ping"),
            [request(9, "ping")],  # 2025-03-26 has batches, and 2026-07-28 does not
        ]
        documents, replies = self.session(messages)
        self.assertEqual(len(documents), 9)

        self.assertEqual(replies[1]["result"], {"tools": json.loads(json.dumps(TOOLS))})
        self.assertEqual(replies[2]["result"]["resultType"], "complete")
        self.assertEqual(replies[3]["result"]["protocolVersion"], "2025-03-26")

        negotiated = replies[4]["result"]
        self.assertEqual(list(negotiated), ["tools"])
        self.assertTrue(all("title" in tool["annotations"] and "title" not in tool for tool in negotiated["tools"]))
        self.assertEqual(replies[6]["result"], negotiated)

        listed = replies[5]["result"]
        self.assertEqual(listed["resultType"], "complete")
        self.assertEqual(listed["cacheScope"], "public")
        self.assertTrue(all("title" in tool for tool in listed["tools"]))

        self.assertEqual(replies[7]["result"], {})
        self.assertEqual(replies[8]["error"]["code"], -32601)
        self.assertEqual(documents[-1], [{"jsonrpc": "2.0", "id": 9, "result": {}}])


class TestTheChecksCatchWhatTheyAreFor(_StdioCase):
    """The checks every session runs, shown to fail on a server that breaks the rules (a stand-in for the process)."""

    def session_of(self, stdout: bytes, returncode: int = 0) -> None:
        def stand_in(argv: List[str], **kwargs: Any) -> "subprocess.CompletedProcess[bytes]":
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=b"")

        with patch("subprocess.run", side_effect=stand_in):
            self.session([request(1, "ping")])

    def test_a_clean_stand_in_passes(self) -> None:
        self.session_of(b'{"jsonrpc":"2.0","id":1,"result":{}}\n[{"jsonrpc":"2.0","id":2,"result":{}}]\n')

    def test_a_line_that_is_not_json_fails(self) -> None:
        with self.assertRaises(AssertionError):
            self.session_of(b'hello\n{"jsonrpc":"2.0","id":1,"result":{}}\n')

    def test_a_json_line_that_is_not_a_message_fails(self) -> None:
        for stdout in (b'{"id":1,"result":{}}\n', b'{"jsonrpc":"1.0","id":1}\n', b"[1]\n", b"5\n", b"[]\n"):
            with self.subTest(stdout=stdout):
                with self.assertRaises(AssertionError):
                    self.session_of(stdout)

    def test_an_id_answered_twice_fails(self) -> None:
        reply = b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
        with self.assertRaises(AssertionError):
            self.session_of(reply + reply)
        with self.assertRaises(AssertionError):
            self.session_of(reply + b'[{"jsonrpc":"2.0","id":1,"result":{}}]\n')

    def test_errors_without_an_id_may_repeat(self) -> None:
        refusal = b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"x"}}\n'
        self.session_of(refusal + refusal)

    def test_a_failing_exit_code_fails(self) -> None:
        with self.assertRaises(AssertionError):
            self.session_of(b"", returncode=1)

    def test_something_created_in_the_home_folder_fails(self) -> None:
        real_run = subprocess.run

        def writes_a_stray_file(argv: List[str], **kwargs: Any) -> "subprocess.CompletedProcess[bytes]":
            home = Path(kwargs["env"]["HOME"])
            # This stand-in writes where the environment points, so it first makes sure that is inside the test's own
            # folder. It raises a RuntimeError, not an assertion: the AssertionError this test expects has to come from
            # the check under test.
            if not is_inside(home, self.base):
                raise RuntimeError(f"refusing to write into {str(home)!r}: it is not inside {str(self.base)!r}")
            (home / "stray-file-from-the-server.txt").write_text("x", encoding="utf-8")
            return real_run([sys.executable, "-c", "pass"], **kwargs)

        with patch("subprocess.run", side_effect=writes_a_stray_file):
            with self.assertRaisesRegex(AssertionError, "created something in the home folder"):
                self.session([request(1, "ping")])

    def test_a_session_starts_nothing_unless_the_home_points_into_the_sandbox(self) -> None:
        elsewhere = Path(tempfile.mkdtemp(prefix="sd_mcp_elsewhere_")).resolve()
        self.addCleanup(shutil.rmtree, str(elsewhere), ignore_errors=True)
        regressed = {**os.environ, **{name: str(elsewhere) for name in HOME_VARIABLES}}  # as if env() kept the user's
        with patch.object(Sandbox, "env", return_value=regressed), patch("subprocess.run") as run:
            with self.assertRaisesRegex(AssertionError, "does not point into the sandbox"):
                self.session([request(1, "ping")])
        run.assert_not_called()
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_the_sandbox_hides_the_users_settings_and_points_home_into_itself(self) -> None:
        sandbox = Sandbox(self.base / "env-check")
        self.assertTrue(is_inside(sandbox.home, self.base))
        with patch.dict(
            os.environ,
            {"SMART_DRIVE_ROOT": "x", "SMART_DRIVE_MCP_AUTH_TOKEN": "t", "KINGSTON_SSD_ROOT": "y", "UV_CACHE_DIR": "z",
             "PIPX_HOME": "p", "UV": "u", "ANTIGRAVITY_HOME": "a", "GEMINI_HOME": "g", "PYTHONPATH": "existing"},
        ):
            env = sandbox.env()
        for name in ("SMART_DRIVE_ROOT", "SMART_DRIVE_MCP_AUTH_TOKEN", "KINGSTON_SSD_ROOT", "UV_CACHE_DIR", "PIPX_HOME",
                     "UV", "ANTIGRAVITY_HOME", "GEMINI_HOME"):
            self.assertNotIn(name, env)
        for name in HOME_VARIABLES:
            self.assertTrue(is_inside(Path(env[name]), sandbox.home), name)
        self.assertEqual(env["SMART_DRIVE_NO_PROBE"], "1")
        self.assertEqual(env["PYTHONPATH"].split(os.pathsep), [str(REPO_ROOT), "existing"])


if __name__ == "__main__":
    unittest.main()
