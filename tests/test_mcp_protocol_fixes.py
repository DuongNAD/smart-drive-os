"""tests/test_mcp_protocol_fixes.py - Small MCP gaps that made the server look healthy while misbehaving.

Regressions:
- `instructions` sat inside serverInfo; the spec (and every client) looks for it at the top of the
  initialize result, so the agent rules were never delivered.
- A JSON-RPC batch (an array) was silently dropped, and a request that made the server raise left the
  client waiting forever. Batches are answered with one array; a server bug becomes an "Internal error".
- Every client mistake (unknown tool, path outside the drive) logged a full traceback.
- `ssd_check_safety` called `.git/HEAD` safe while PurgeEngine treats everything inside `.git` as inviolable.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, List
from unittest.mock import patch

from smart_drive.core.config import is_protected_root_dir
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.mcp.server import MAX_BATCH_SIZE, SERVER_INSTRUCTIONS, SmartDriveMCPServer


class _Server(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_mcp_fix_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def talk(self, *chunks: str) -> List[Any]:
        """Feeds raw stdin chunks to the real stdio loop and returns every JSON document it wrote."""
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO("".join(chunks))), patch("sys.stdout", stdout):
            self.server.run_stdio()
        return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]

    @staticmethod
    def line(message: Any) -> str:
        return json.dumps(message) + "\n"


class TestInitializeInstructions(_Server):
    def test_instructions_are_at_the_top_of_the_result_as_the_spec_says(self) -> None:
        (reply,) = self.talk(self.line({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
        result = reply["result"]
        self.assertEqual(result["instructions"], SERVER_INSTRUCTIONS)
        self.assertEqual(set(result["serverInfo"]), {"name", "version"})
        self.assertIn("ssd_search", result["instructions"])


class TestBatches(_Server):
    PING = {"jsonrpc": "2.0", "id": 7, "method": "ping"}
    NOTE = {"jsonrpc": "2.0", "method": "notifications/initialized"}

    def test_a_batch_is_answered_with_one_array_in_order(self) -> None:
        batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": "b", "method": "tools/list"}]
        (reply,) = self.talk(self.line(batch))
        self.assertIsInstance(reply, list)
        self.assertEqual([r["id"] for r in reply], [1, "b"])
        self.assertIn("tools", reply[1]["result"])

    def test_notifications_in_a_batch_get_no_reply_of_their_own(self) -> None:
        (reply,) = self.talk(self.line([self.NOTE, self.PING]))
        self.assertEqual([r["id"] for r in reply], [7])

    def test_a_batch_of_only_notifications_gets_no_reply_at_all(self) -> None:
        self.assertEqual(self.talk(self.line([self.NOTE, self.NOTE])), [])

    def test_an_empty_batch_is_an_invalid_request(self) -> None:
        (reply,) = self.talk(self.line([]))
        self.assertEqual(reply["error"]["code"], -32600)
        self.assertIsNone(reply["id"])

    def test_items_that_are_not_objects_get_an_error_without_spoiling_the_rest(self) -> None:
        (reply,) = self.talk(self.line([5, self.PING]))
        self.assertEqual(reply[0]["error"]["code"], -32600)
        self.assertEqual(reply[1]["id"], 7)
        self.assertIn("result", reply[1])

    def test_a_batch_at_the_limit_is_answered_and_one_over_it_is_refused_whole(self) -> None:
        def pings(count: int) -> list:
            return [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(count)]

        (answered,) = self.talk(self.line(pings(MAX_BATCH_SIZE)))
        self.assertEqual(len(answered), MAX_BATCH_SIZE)
        (refused,) = self.talk(self.line(pings(MAX_BATCH_SIZE + 1)))
        self.assertEqual(refused["error"]["code"], -32600)
        self.assertIsNone(refused["id"])
        self.assertIn(str(MAX_BATCH_SIZE), refused["error"]["message"])

    def test_a_single_request_still_gets_a_single_object(self) -> None:
        (reply,) = self.talk(self.line(self.PING))
        self.assertIsInstance(reply, dict)
        self.assertEqual(reply["id"], 7)

    def test_batches_work_with_content_length_framing_too(self) -> None:
        body = json.dumps([self.PING])
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO(f"Content-Length: {len(body)}\r\n\r\n{body}")), patch("sys.stdout", stdout):
            self.server.run_stdio()
        header, _, payload = stdout.getvalue().partition("\r\n\r\n")
        self.assertTrue(header.startswith("Content-Length:"))
        self.assertEqual([r["id"] for r in json.loads(payload)], [7])

    def test_one_bad_message_does_not_stop_the_messages_after_it(self) -> None:
        replies = self.talk("[not json\n", self.line(self.PING))
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["id"], 7)


class TestServerBugsAreAnswered(_Server):
    def test_a_request_that_makes_the_server_raise_gets_an_internal_error(self) -> None:
        with patch.object(self.server, "handle_request", side_effect=RuntimeError("boom")):
            with self.assertLogs("smart_drive.mcp.server", level="ERROR"):
                (reply,) = self.talk(self.line({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}))
        self.assertEqual(reply["id"], 3)
        self.assertEqual(reply["error"]["code"], -32603)
        self.assertNotIn("boom", json.dumps(reply))  # the detail stays in our log

    def test_a_notification_that_raises_stays_silent(self) -> None:
        with patch.object(self.server, "handle_request", side_effect=RuntimeError("boom")):
            with self.assertLogs("smart_drive.mcp.server", level="ERROR"):
                replies = self.talk(self.line({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(replies, [])

    def test_inside_a_batch_only_the_failing_request_becomes_an_error(self) -> None:
        real = self.server.handle_request

        def flaky(message: dict) -> Any:
            if message.get("id") == 1:
                raise RuntimeError("boom")
            return real(message)

        batch = [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
        with patch.object(self.server, "handle_request", side_effect=flaky):
            with self.assertLogs("smart_drive.mcp.server", level="ERROR"):
                (reply,) = self.talk(self.line(batch))
        self.assertEqual(reply[0]["error"]["code"], -32603)
        self.assertIn("result", reply[1])


class TestClientMistakesDoNotLogTracebacks(_Server):
    def call(self, name: str, arguments: dict) -> dict:
        (reply,) = self.talk(
            self.line({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        )
        return reply["result"]

    def test_an_unknown_tool_is_one_warning_line(self) -> None:
        with self.assertLogs("smart_drive.mcp.server", level="WARNING") as logs:
            result = self.call("no_such_tool", {})
        self.assertTrue(result["isError"])
        self.assertEqual([r.levelname for r in logs.records], ["WARNING"])
        self.assertIsNone(logs.records[0].exc_info)

    def test_a_path_outside_the_drive_is_one_warning_line(self) -> None:
        with self.assertLogs("smart_drive.mcp.server", level="WARNING") as logs:
            result = self.call("ssd_audit", {"sub_dir": "../../outside"})
        self.assertTrue(result["isError"])
        self.assertEqual([r.levelname for r in logs.records], ["WARNING"])
        self.assertIsNone(logs.records[0].exc_info)

    def test_encoding_and_json_failures_inside_a_tool_are_bugs_not_refusals(self) -> None:
        """UnicodeError and JSONDecodeError are ValueErrors, but nobody deliberately raises them to refuse a call."""
        for error in (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte"), json.JSONDecodeError("bad", "x", 0)):
            with self.subTest(error=type(error).__name__):
                with patch.object(self.server, "dispatch_tool", side_effect=error):
                    with self.assertLogs("smart_drive.mcp.server", level="WARNING") as logs:
                        self.assertTrue(self.call("ssd_status", {})["isError"])
                self.assertEqual([r.levelname for r in logs.records], ["ERROR"])

    def test_a_lone_surrogate_in_the_clients_own_input_still_gets_an_answer(self) -> None:
        """Echoing it back used to fail to encode as UTF-8; now the reply is sent with the character escaped."""
        raw = io.BytesIO()
        stdout = io.TextIOWrapper(raw, encoding="utf-8", newline="\n")
        request = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "bad\udcff", "arguments": {}}}
        with patch("sys.stdin", io.StringIO(json.dumps(request) + "\n")), patch("sys.stdout", stdout):
            with self.assertLogs("smart_drive.mcp.server", level="WARNING"):
                self.server.run_stdio()
        stdout.flush()
        reply = json.loads(raw.getvalue().decode("utf-8"))
        self.assertEqual(reply["id"], 5)
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("Unknown tool", reply["result"]["content"][0]["text"])

    def test_a_real_bug_keeps_its_traceback(self) -> None:
        with patch.object(self.server, "dispatch_tool", side_effect=RuntimeError("boom")):
            with self.assertLogs("smart_drive.mcp.server", level="WARNING") as logs:
                result = self.call("ssd_status", {})
        self.assertTrue(result["isError"])
        self.assertEqual([r.levelname for r in logs.records], ["ERROR"])
        self.assertIsNotNone(logs.records[0].exc_info)


class TestGitInternalsAreNeverCalledSafe(_Server):
    GIT_PATHS = (".git", ".git/HEAD", "project/.git/config", "project/.GIT/hooks/pre-commit", ".git\\objects\\ab")
    ORDINARY = (".gitignore", ".github/workflows/ci.yml", "project/git/notes.md", "backup.git", "01_AI_Models/new.gguf")

    def test_git_paths_are_unsafe(self) -> None:
        for path in self.GIT_PATHS:
            with self.subTest(path=path):
                result = self.server.handle_ssd_check_safety({"path": path})
                self.assertFalse(result["is_safe"])
                self.assertTrue(result["is_protected_root_dir"])

    def test_similar_looking_paths_stay_safe(self) -> None:
        for path in self.ORDINARY:
            with self.subTest(path=path):
                self.assertTrue(self.server.handle_ssd_check_safety({"path": path})["is_safe"])

    def test_the_safety_check_and_the_purge_engine_agree_about_git(self) -> None:
        guard = PurgeEngine(str(self.root)).guard
        for path in self.GIT_PATHS:
            with self.subTest(path=path):
                self.assertTrue(guard.is_protected(path)[0])
                self.assertFalse(self.server.handle_ssd_check_safety({"path": path})["is_safe"])
                self.assertTrue(is_protected_root_dir(path))


if __name__ == "__main__":
    unittest.main()
