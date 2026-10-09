"""tests/test_cli_nested_options.py - `snapshot --root X <action>` must use X.

Regression: `snapshot` and each of its actions both defined --root and --json. argparse lets the action
parser's default overwrite what the parent already parsed, so `snapshot --root /Volumes/X create` silently
ignored the root (and `snapshot --json list` ignored --json) and worked on whichever drive discovery found,
writing the snapshot there. The action options now use default=SUPPRESS, so both orders work.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from smart_drive.cli.main import build_parser, main

ACTIONS = (["list"], ["create", "s1"], ["verify", "s1"])


class TestParsing(unittest.TestCase):
    def parse(self, *argv: str):
        return build_parser().parse_args(list(argv))

    def test_root_before_the_action_is_kept(self) -> None:
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assertEqual(self.parse("snapshot", "--root", "/tmp/x", *action).root, "/tmp/x")

    def test_root_after_the_action_is_kept(self) -> None:
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assertEqual(self.parse("snapshot", *action, "--root", "/tmp/x").root, "/tmp/x")

    def test_json_before_and_after_the_action(self) -> None:
        for action in ACTIONS:
            with self.subTest(action=action):
                self.assertTrue(self.parse("snapshot", "--json", *action).json)
                self.assertTrue(self.parse("snapshot", *action, "--json").json)

    def test_defaults_still_apply_when_neither_is_given(self) -> None:
        for action in ACTIONS:
            with self.subTest(action=action):
                args = self.parse("snapshot", *action)
                self.assertIsNone(args.root)
                self.assertFalse(args.json)

    def test_when_both_are_given_the_one_after_the_action_wins(self) -> None:
        self.assertEqual(self.parse("snapshot", "--root", "/tmp/a", "list", "--root", "/tmp/b").root, "/tmp/b")


class TestSnapshotWorksOnTheDriveItWasGiven(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="sd_nested_")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.drive = base / "drive"
        (self.drive / "02_Learning_Knowledge").mkdir(parents=True)
        (self.drive / "02_Learning_Knowledge" / "notes.md").write_text("notes", encoding="utf-8")
        self.elsewhere = base / "elsewhere"
        self.elsewhere.mkdir()
        previous = os.getcwd()
        os.chdir(self.elsewhere)  # nowhere near the drive, so discovery cannot find it by walking up
        self.addCleanup(os.chdir, previous)

    def cli(self, *argv: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_root_given_before_the_action_is_the_drive_that_is_snapshotted(self) -> None:
        code, _, err = self.cli("snapshot", "--root", str(self.drive), "create", "s1", "--partitions", "02_Learning_Knowledge")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.drive / ".smart_drive" / "snapshots" / "s1.json").is_file())
        self.assertEqual(list(self.elsewhere.iterdir()), [])

    def test_list_and_json_given_before_the_action_are_honoured(self) -> None:
        self.cli("snapshot", "create", "s1", "--root", str(self.drive), "--partitions", "02_Learning_Knowledge")
        code, out, _ = self.cli("snapshot", "--root", str(self.drive), "--json", "list")
        self.assertEqual(code, 0)
        self.assertIsInstance(json.loads(out), (list, dict))  # JSON, not the human table

    def test_verify_with_the_root_first(self) -> None:
        self.cli("snapshot", "create", "s1", "--root", str(self.drive), "--partitions", "02_Learning_Knowledge")
        code, out, err = self.cli("snapshot", "--root", str(self.drive), "verify", "s1", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out).get("name", "s1"), "s1")


if __name__ == "__main__":
    unittest.main()
