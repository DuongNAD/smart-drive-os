"""tests/test_mcp_absolute_paths.py - Absolute paths under the storage root, whatever kind of root it is.

Regression: with a drive-root storage (`E:\\` on Windows, `/` elsewhere) every absolute path was refused. The prefix
test glued a second separator onto the root (`E:/` + `/` = `E://`), so nothing could match: ssd_audit, ssd_gitignore and
the other path tools answered "path escapes storage root" for `E:\\project`, and ssd_check_safety called it "on a
different drive", while `project` worked. The comparison was also case sensitive, and ssd_check_safety cut the drive
letter off before comparing, so on Windows even a folder root refused every absolute path. Coding agents mostly send
absolute paths.

The drive-root cases use the anchor of the temp directory (`C:\\` or `/`) as the storage root and only look at paths
that do not exist there, so nothing is ever written to a drive root.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

from smart_drive.core.junction import create_directory_junction, remove_directory_junction
from smart_drive.mcp.server import SmartDriveMCPServer
from tests.helpers import SmartDriveTestCase

ON_WINDOWS = sys.platform == "win32"


def spellings(path: str) -> List[str]:
    """The ways a client may write one absolute path: as is, with / separators, and on Windows in other letter cases."""
    forms = [path, path.replace("\\", "/")]
    if ON_WINDOWS:
        lower_drive = path[0].lower() + path[1:]
        forms += [lower_drive, lower_drive.replace("\\", "/"), path.upper(), path.lower()]
    return list(dict.fromkeys(forms))


class _GuardTestCase(SmartDriveTestCase):
    """What both root kinds assert; the subclass sets `self.server`."""

    server: SmartDriveMCPServer

    def assertResolves(self, spelling: str, expected: str) -> None:
        resolved = self.server._resolve_safe_path(spelling)
        self.assertEqual(os.path.normcase(resolved), os.path.normcase(expected), spelling)

    def assertRefused(self, spelling: str) -> None:
        """Both guards say no: the tools raise, and ssd_check_safety calls the path unsafe."""
        with self.assertRaises(ValueError, msg=f"{spelling!r} must be refused"):
            self.server._resolve_safe_path(spelling)
        res = self.server.handle_ssd_check_safety({"path": spelling})
        self.assertFalse(res["is_safe"], f"{spelling!r} must be unsafe: {res}")

    def assertSafe(self, spelling: str) -> None:
        res = self.server.handle_ssd_check_safety({"path": spelling})
        self.assertTrue(res["is_safe"], f"{spelling!r} must be safe: {res}")
        self.assertNotIn("error", res)
        self.assertEqual(res["forbidden_character_violations"], [])


class TestAbsolutePathsUnderADriveRoot(_GuardTestCase):
    """The storage root is a whole drive: the anchor of the temp directory."""

    def setUp(self) -> None:
        anchor = Path(tempfile.gettempdir()).anchor
        if ON_WINDOWS and not os.path.splitdrive(anchor)[0].endswith(":"):
            self.skipTest("the temp directory is not on a lettered drive")
        super().setUp()
        self.anchor = anchor
        self.server = SmartDriveMCPServer(root=anchor, rate_limit_enabled=False)
        # None of these exist: they are only ever looked at, never created.
        self.project = os.path.join(anchor, "sd_abs_regression", "project")
        self.source = os.path.join(self.project, "main.py")

    def test_the_storage_root_is_a_drive_root(self) -> None:
        root = self.server._resolve_safe_path(None)
        self.assertEqual(os.path.dirname(root), root)

    def test_an_absolute_path_inside_is_accepted(self) -> None:
        for spelling in spellings(self.source):
            with self.subTest(path=spelling):
                self.assertResolves(spelling, os.path.realpath(self.source))

    def test_the_drive_root_itself_is_accepted(self) -> None:
        for spelling in spellings(self.anchor):
            with self.subTest(path=spelling):
                self.assertResolves(spelling, os.path.realpath(self.anchor))

    def test_a_relative_path_still_resolves_under_the_root(self) -> None:
        self.assertResolves("sd_abs_regression/project", os.path.realpath(self.project))

    def test_a_parent_reference_at_the_top_stays_on_the_root(self) -> None:
        """Nothing is above a drive root, so `..` cannot escape it (the escape cases run on a folder root)."""
        above = os.path.join(self.anchor, "..", "..", "sd_abs_regression")
        self.assertResolves(above, os.path.realpath(os.path.join(self.anchor, "sd_abs_regression")))

    def test_check_safety_calls_an_absolute_path_inside_safe(self) -> None:
        for spelling in spellings(self.source):
            with self.subTest(path=spelling):
                self.assertSafe(spelling)

    def test_check_safety_applies_its_rules_to_the_part_below_the_root(self) -> None:
        taxonomy = os.path.join(self.anchor, "01_AI_Models")
        for spelling in spellings(taxonomy):
            with self.subTest(path=spelling):
                res = self.server.handle_ssd_check_safety({"path": spelling})
                self.assertTrue(res["is_protected_root_dir"], res)
                self.assertFalse(res["is_safe"])
                self.assertNotIn("error", res)  # protected, not "escapes the root"
        self.assertSafe(os.path.join(taxonomy, "model.gguf"))
        res = self.server.handle_ssd_check_safety({"path": os.path.join(self.project, "bad*name.txt")})
        self.assertFalse(res["is_safe"])
        self.assertEqual(res["forbidden_character_violations"], ["FORBIDDEN_CHAR:*"])
        self.assertNotIn("error", res)

    @unittest.skipUnless(ON_WINDOWS, "drive letters only exist on Windows")
    def test_another_drive_is_refused(self) -> None:
        other = "Z:" if os.path.splitdrive(self.anchor)[0].upper() != "Z:" else "Y:"
        for spelling in (other + "\\project", other + "/project", other.lower() + "\\project", other + "\\"):
            with self.subTest(path=spelling):
                self.assertRefused(spelling)
                error = self.server.handle_ssd_check_safety({"path": spelling})["error"]
                self.assertIn("different drive", error.lower())

    def test_unc_and_device_namespace_paths_are_refused(self) -> None:
        for spelling in (
            "\\\\server\\share\\project",
            "//server/share/project",
            "\\\\?\\" + self.project,
            "\\\\.\\" + self.project,
            "\\??\\" + self.project,
        ):
            with self.subTest(path=spelling):
                self.assertRefused(spelling)

    def test_null_bytes_are_refused(self) -> None:
        self.assertRefused(self.source + "\x00.txt")


class TestAbsolutePathsUnderAFolderRoot(_GuardTestCase):
    """The storage root is an ordinary folder: its own guards must not change."""

    def setUp(self) -> None:
        super().setUp()
        self.mock_root = self.create_mock_drive()
        self.root = str(self.mock_root)
        self.server = SmartDriveMCPServer(root=self.root, rate_limit_enabled=False)
        self.models = os.path.join(self.root, "01_AI_Models")

    def test_an_absolute_path_inside_is_accepted(self) -> None:
        gguf = os.path.join(self.models, "GGUF")
        for spelling in spellings(gguf):
            with self.subTest(path=spelling):
                self.assertResolves(spelling, os.path.realpath(gguf))
                self.assertSafe(os.path.join(spelling, "model.bin"))

    def test_the_root_itself_is_accepted_with_or_without_a_trailing_separator(self) -> None:
        for spelling in spellings(self.root) + [self.root + os.sep, self.root.replace("\\", "/") + "/"]:
            with self.subTest(path=spelling):
                self.assertResolves(spelling, os.path.realpath(self.root))

    def test_a_parent_reference_that_stays_inside_is_accepted(self) -> None:
        detour = os.path.join(self.models, "GGUF", "..", "HuggingFace")
        self.assertResolves(detour, os.path.join(self.models, "HuggingFace"))

    def test_check_safety_applies_its_rules_to_the_part_below_the_root(self) -> None:
        for spelling in spellings(self.models):
            with self.subTest(path=spelling):
                res = self.server.handle_ssd_check_safety({"path": spelling})
                self.assertTrue(res["is_protected_root_dir"], res)
                self.assertNotIn("error", res)
        res = self.server.handle_ssd_check_safety({"path": os.path.join(self.root, "AGENTS.md")})
        self.assertTrue(res["is_protected_root_file"], res)
        self.assertNotIn("error", res)

    def test_a_parent_reference_cannot_leave_the_root(self) -> None:
        # All of them start with the root's own text, which is what ssd_check_safety once took for "inside" (POSIX).
        for spelling in (
            os.path.join(self.root, "..", "outside.txt"),
            os.path.join(self.root, "..", ".."),
            os.path.join(self.models, "..", "..", "outside.txt"),
            self.root + "\\..\\outside.txt",
            self.root.replace("\\", "/") + "/sub/../../outside.txt",
        ):
            with self.subTest(path=spelling):
                self.assertRefused(spelling)
                self.assertIn("escapes", self.server.handle_ssd_check_safety({"path": spelling})["error"].lower())

    def test_a_sibling_that_shares_the_root_name_as_a_prefix_is_outside(self) -> None:
        for spelling in (self.root + "_evil", self.root + "_evil/secret.txt", self.root + "2", self.root + "x\\y"):
            with self.subTest(path=spelling):
                self.assertRefused(spelling)

    @unittest.skipUnless(ON_WINDOWS, "drive letters only exist on Windows")
    def test_another_drive_is_refused(self) -> None:
        other = "Z:" if os.path.splitdrive(self.root)[0].upper() != "Z:" else "Y:"
        for spelling in (other + "\\project", other.lower() + "/project", other + "\\"):
            with self.subTest(path=spelling):
                self.assertRefused(spelling)

    @unittest.skipUnless(ON_WINDOWS, "a drive-less or drive-relative path only means something on Windows")
    def test_paths_that_depend_on_the_working_directory_are_refused(self) -> None:
        drive = os.path.splitdrive(self.root)[0]
        rooted = self.root[len(drive):]
        for spelling in (rooted, rooted.replace("\\", "/"), drive + "relative", drive):
            with self.subTest(path=spelling):
                with self.assertRaises(ValueError):
                    self.server._resolve_safe_path(spelling)

    def test_unc_and_device_namespace_paths_are_refused(self) -> None:
        for spelling in (
            "\\\\server\\share\\x",
            "//server/share/x",
            "\\\\?\\" + self.root,
            "\\\\.\\" + self.root,
            "\\??\\" + self.root,
        ):
            with self.subTest(path=spelling):
                self.assertRefused(spelling)

    def test_a_link_leading_out_of_the_root_is_outside(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="sd_abs_outside_")).resolve()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "secret.txt").write_text("outside the root", encoding="utf-8")
        link = self.mock_root / "escape_link"
        if not create_directory_junction(link, outside):
            self.skipTest("this machine cannot create directory links")
        try:
            for spelling in spellings(str(link)) + [os.path.join(str(link), "secret.txt")]:
                with self.subTest(path=spelling):
                    self.assertRefused(spelling)
        finally:
            remove_directory_junction(link)

    def test_every_path_tool_takes_an_absolute_path(self) -> None:
        server = self.server
        server.handle_ssd_update_index({})  # ssd_search needs an index
        expected_files = server.handle_ssd_audit({"sub_dir": "01_AI_Models"})["total_files"]
        expected_hits = server.handle_ssd_search({"query": "llama", "directory": "01_AI_Models"})["total_count"]
        self.assertGreater(expected_files, 0)
        self.assertGreater(expected_hits, 0)
        resolved = os.path.normcase(os.path.realpath(self.models))
        for spelling in spellings(self.models):
            with self.subTest(path=spelling):
                self.assertEqual(server.handle_ssd_audit({"sub_dir": spelling})["total_files"], expected_files)
                search = server.handle_ssd_search({"query": "llama", "directory": spelling})
                self.assertEqual(search["total_count"], expected_hits)
                clean = server.handle_ssd_clean({"sub_dir": spelling})
                self.assertTrue(clean["dry_run"])
                self.assertEqual(os.path.normcase(clean["target_root"]), resolved)
                dups = server.handle_ssd_find_duplicates({"sub_dir": spelling})
                self.assertEqual(os.path.normcase(dups["root"]), resolved)
                server.handle_ssd_update_index({"directory": spelling})
                audit = server.handle_ssd_gitignore({"action": "audit", "repo": spelling})
                self.assertEqual(audit["summary"]["repos_audited"], 0)

    def test_the_path_tools_still_refuse_an_absolute_path_outside(self) -> None:
        outside = os.path.join(os.path.dirname(self.root), "outside_dir")
        calls = (
            lambda: self.server.handle_ssd_audit({"sub_dir": outside}),
            lambda: self.server.handle_ssd_clean({"sub_dir": outside}),
            lambda: self.server.handle_ssd_find_duplicates({"sub_dir": outside}),
            lambda: self.server.handle_ssd_update_index({"directory": outside}),
            lambda: self.server.handle_ssd_search({"query": "x", "directory": outside}),
            lambda: self.server.handle_ssd_gitignore({"action": "audit", "repo": outside}),
        )
        for call in calls:
            with self.assertRaises(ValueError) as caught:
                call()
            self.assertIn("Access denied", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
