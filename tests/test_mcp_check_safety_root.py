"""tests/test_mcp_check_safety_root.py - ssd_check_safety refuses drive-relative paths and protects the storage root.

Regression, found by calling the tool as a coding agent does (JSON-RPC tools/call): with the storage root `E:\\`, the
paths `E:x`, `e:notes.txt`, `E:01_AI_Models\\llama.gguf` and `E:` came back is_safe true. The handler cut the `E:` off
and joined the rest onto the storage root, but Windows reads a drive letter that no separator follows against the
current folder of that drive, which is process state and not the storage root. The other path tools already refused
these paths, only with "path escapes storage root", which says nothing about what to send instead. The storage root
itself (`E:\\`, `.`, `x/..`, or the folder root in any spelling) came back safe as well: the one folder an agent must
never delete or move was the only one not reported protected, although the six taxonomy folders below it are. Every
tool now refuses a drive-relative path with an error that says why and what to send, and the root is reported
protected (is_storage_root).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from smart_drive.mcp.server import TOOLS, SmartDriveMCPServer
from tests.helpers import SmartDriveTestCase

ON_WINDOWS = sys.platform == "win32"
ROOT = Path(__file__).resolve().parent.parent

# Every answer that carries is_safe has these keys (a refusal adds `error`, and so does an escape)
ANSWER_KEYS = {
    "path",
    "is_safe",
    "is_symlink",
    "forbidden_character_violations",
    "is_protected_root_file",
    "is_protected_root_dir",
    "is_storage_root",
}
# Ways to name the storage root without writing it out
RELATIVE_ROOTS = (".", "./", ".\\", "  .  ", "sub/..", "sub\\..", "a/b/../..")


def spellings(path: str) -> List[str]:
    """The ways a client may write one absolute path: as is, with / separators, and on Windows in other letter cases."""
    forms = [path, path.replace("\\", "/")]
    if ON_WINDOWS:
        lower_drive = path[0].lower() + path[1:]
        forms += [lower_drive, lower_drive.replace("\\", "/"), path.upper(), path.lower()]
    return list(dict.fromkeys(forms))


class _Check(SmartDriveTestCase):
    """Calls tools as an agent does; the subclass sets `self.server`, `self.root` and `self.root_forms`."""

    server: SmartDriveMCPServer
    root: str
    root_forms: List[str]
    calls = 0

    def call(self, name: str, **arguments: Any) -> Dict[str, Any]:
        """A JSON-RPC tools/call; returns the reply's result object (content, and isError when the tool refused)."""
        self.calls += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.calls,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        with contextlib.redirect_stdout(io.StringIO()):  # the server also writes each reply to its stdout
            reply = self.server.handle_request(request)
        self.assertIsNotNone(reply)
        return reply["result"]

    def check(self, path: str) -> Dict[str, Any]:
        result = self.call("ssd_check_safety", path=path)
        self.assertNotIn("isError", result, result)
        return json.loads(result["content"][0]["text"])

    def assertStorageRoot(self, path: str) -> None:
        res = self.check(path)
        self.assertFalse(res["is_safe"], f"{path!r} is the storage root and must not be safe: {res}")
        self.assertTrue(res["is_storage_root"], f"{path!r}: {res}")
        self.assertTrue(res["is_protected_root_dir"], f"{path!r}: {res}")
        self.assertNotIn("error", res)  # protected, not invalid
        self.assertEqual(res["forbidden_character_violations"], [])

    def other_drive(self) -> str:
        return "Z:" if os.path.splitdrive(self.root)[0].upper() != "Z:" else "Y:"


class _FolderRoot(_Check):
    """The storage root is an ordinary folder."""

    def setUp(self) -> None:
        super().setUp()
        self.root = str(self.create_mock_drive())
        self.root_forms = spellings(self.root) + [self.root + os.sep, self.root.replace("\\", "/") + "/"]
        self.server = SmartDriveMCPServer(root=self.root, rate_limit_enabled=False)
        self.refused = ["../outside.txt", "..", os.path.join(self.root, "..", "outside.txt")]


class _DriveRoot(_Check):
    """The storage root is a whole drive, the anchor of the temp directory. Nothing is created there, only looked at."""

    def setUp(self) -> None:
        anchor = Path(tempfile.gettempdir()).anchor
        if ON_WINDOWS and not os.path.splitdrive(anchor)[0].endswith(":"):
            self.skipTest("the temp directory is not on a lettered drive")
        super().setUp()
        self.root = anchor
        self.root_forms = spellings(anchor)
        self.server = SmartDriveMCPServer(root=anchor, rate_limit_enabled=False)
        self.refused = []  # (nothing is above a drive root: `..` stays on it)


class _RootCases:
    """What both kinds of root must answer."""

    def test_the_root_in_every_spelling_is_protected(self) -> None:
        for spelling in [*self.root_forms, *RELATIVE_ROOTS, os.path.join(self.root, "x", "..")]:
            with self.subTest(path=spelling):
                self.assertStorageRoot(spelling)

    def test_what_lies_below_the_root_is_safe_and_is_not_the_root(self) -> None:
        source = os.path.join(self.root, "sd_check_root_regression", "project", "main.py")
        for spelling in ["project", "project/main.py", "01_AI_Models/model.gguf", *spellings(source)]:
            with self.subTest(path=spelling):
                res = self.check(spelling)
                self.assertTrue(res["is_safe"], res)
                self.assertFalse(res["is_storage_root"])
                self.assertFalse(res["is_protected_root_dir"])
                self.assertNotIn("error", res)

    def test_a_taxonomy_below_the_root_is_protected_and_is_not_the_root(self) -> None:
        for spelling in ["01_AI_Models", os.path.join(self.root, "01_AI_Models")]:
            with self.subTest(path=spelling):
                res = self.check(spelling)
                self.assertFalse(res["is_safe"])
                self.assertTrue(res["is_protected_root_dir"])
                self.assertFalse(res["is_storage_root"])
                self.assertNotIn("error", res)

    def test_every_refusal_says_it_is_not_the_root(self) -> None:
        other = self.other_drive()
        for spelling in [
            "folder/file\x00.txt",
            "\\\\server\\share\\x",
            "//server/share/x",
            other + "\\project",
            other + "/project",
            *self.refused,
        ]:
            with self.subTest(path=spelling):
                res = self.check(spelling)
                self.assertFalse(res["is_safe"])
                self.assertFalse(res["is_storage_root"], res)
                self.assertTrue(res["error"])

    def test_every_answer_has_the_same_keys(self) -> None:
        other = self.other_drive()
        samples = [
            self.root,  # the root
            "project/main.py",  # safe
            "01_AI_Models",  # a protected folder
            "AGENTS.md",  # a protected file
            "bad*name.txt",  # forbidden characters
            "folder/file\x00.txt",  # null byte
            "//server/share/x",  # UNC
            other + "\\project",  # another drive
            *self.refused,
        ]
        for sample in samples:
            with self.subTest(path=sample):
                res = self.check(sample)
                self.assertEqual(set(res) - {"error"}, ANSWER_KEYS)
                self.assertIsInstance(res["is_storage_root"], bool)


class TestUnderAFolderRoot(_RootCases, _FolderRoot):
    def test_the_parent_of_the_root_is_outside_it(self) -> None:
        res = self.check(os.path.dirname(self.root))
        self.assertFalse(res["is_safe"])
        self.assertFalse(res["is_storage_root"])
        self.assertIn("escapes", res["error"].lower())


class TestUnderADriveRoot(_RootCases, _DriveRoot):
    pass


class _DriveRelativeCases:
    """`E:x` means x in the current folder of drive E:, which is not the storage root."""

    def spellings_of_drive_relative(self) -> List[str]:
        drive = os.path.splitdrive(self.root)[0]
        return [
            drive + "x",
            drive.lower() + "x",
            drive.lower() + "notes.txt",
            drive,
            drive + "sub\\x",
            drive + "01_AI_Models\\llama.gguf",
            drive + "01_AI_Models/model.gguf",
            drive + " x",
            "  " + drive.lower() + "x  ",
        ]

    def test_check_safety_refuses_them_and_says_why(self) -> None:
        drive = os.path.splitdrive(self.root)[0].upper()
        for spelling in self.spellings_of_drive_relative():
            with self.subTest(path=spelling):
                res = self.check(spelling)
                self.assertFalse(res["is_safe"], res)
                self.assertFalse(res["is_storage_root"])
                self.assertFalse(res["is_protected_root_dir"])
                error = res["error"]
                self.assertIn("drive-relative", error.lower())
                self.assertIn(f"'{spelling.strip()}'", error)  # it quotes the spelling that was sent
                self.assertIn(f"drive {drive},", error)
                self.assertIn("absolute path", error)
                self.assertIn("storage root", error)
                self.assertNotIn("escapes", error.lower())

    def test_forbidden_characters_are_still_reported(self) -> None:
        drive = os.path.splitdrive(self.root)[0]
        res = self.check(drive + "bad*name.txt")
        self.assertFalse(res["is_safe"])
        self.assertEqual(res["forbidden_character_violations"], ["FORBIDDEN_CHAR:*"])
        self.assertIn("drive-relative", res["error"].lower())

    def test_the_other_tools_refuse_them_with_the_same_words(self) -> None:
        for spelling in self.spellings_of_drive_relative():
            with self.subTest(path=spelling):
                with self.assertRaises(ValueError) as caught:
                    self.server._resolve_safe_path(spelling)
                message = str(caught.exception)
                self.assertTrue(message.startswith("Access denied"), message)
                self.assertIn("drive-relative", message.lower())
                self.assertIn(f"'{spelling.strip()}'", message)
                self.assertNotIn("escapes", message.lower())
                # one wording for both, so that they cannot drift apart
                self.assertEqual(message, "Access denied: " + self.check(spelling)["error"])

    def test_a_tool_call_comes_back_as_a_tool_error_that_says_so(self) -> None:
        sent = os.path.splitdrive(self.root)[0].lower() + "notes.txt"
        arguments = (
            ("ssd_audit", {"sub_dir": sent}),
            ("ssd_clean", {"sub_dir": sent}),
            ("ssd_find_duplicates", {"sub_dir": sent}),
            ("ssd_update_index", {"directory": sent}),
            ("ssd_search", {"query": "x", "directory": sent}),
            ("ssd_gitignore", {"action": "audit", "repo": sent}),
        )
        with self.assertLogs("smart_drive.mcp.server", level="WARNING"):  # (each refusal is one warning line)
            for name, tool_arguments in arguments:
                with self.subTest(tool=name):
                    result = self.call(name, **tool_arguments)
                    self.assertTrue(result.get("isError"), result)
                    text = result["content"][0]["text"]
                    self.assertIn("Access denied", text)
                    self.assertIn("drive-relative", text.lower())
                    self.assertIn(f"'{sent}'", text)  # it quotes the spelling that was sent

    def test_an_absolute_path_with_the_drive_is_still_accepted(self) -> None:
        source = os.path.join(self.root, "project", "main.py")
        for spelling in spellings(source):
            with self.subTest(path=spelling):
                resolved = self.server._resolve_safe_path(spelling)
                self.assertEqual(os.path.normcase(resolved), os.path.normcase(os.path.realpath(source)))
                self.assertTrue(self.check(spelling)["is_safe"])


@unittest.skipUnless(ON_WINDOWS, "drive letters only mean something on Windows")
class TestDriveRelativeUnderAFolderRoot(_DriveRelativeCases, _FolderRoot):
    pass


@unittest.skipUnless(ON_WINDOWS, "drive letters only mean something on Windows")
class TestDriveRelativeUnderADriveRoot(_DriveRelativeCases, _DriveRoot):
    pass


class TestNoDrivesOutsideWindows(_FolderRoot):
    @unittest.skipIf(ON_WINDOWS, "on Windows a drive letter is a drive")
    def test_a_drive_letter_path_stays_refused(self) -> None:
        for spelling in ("E:x", "e:notes.txt", "E:", "E:\\x", "E:/x"):
            with self.subTest(path=spelling):
                res = self.check(spelling)
                self.assertFalse(res["is_safe"])
                self.assertFalse(res["is_storage_root"])
                self.assertTrue(res["error"])


class TestDocumentation(unittest.TestCase):
    def test_the_tool_description_names_both_rules(self) -> None:
        description = next(tool["description"] for tool in TOOLS if tool["name"] == "ssd_check_safety")
        self.assertIn("storage root", description)
        self.assertIn("is_storage_root", description)
        self.assertIn("E:x", description)
        self.assertIn("drive-relative", description.lower())

    def test_both_readmes_say_the_same(self) -> None:
        for name in ("README.md", "README_VN.md"):
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            line = next(line for line in lines if line.startswith("- `ssd_check_safety`:"))
            with self.subTest(readme=name):
                self.assertIn("`is_storage_root`", line)
                self.assertIn("`E:x`", line)


if __name__ == "__main__":
    unittest.main()
