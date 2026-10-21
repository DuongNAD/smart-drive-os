"""tests/test_organize_dry_run_writes_nothing.py - `organize` changes nothing on the drive until `--apply`.

Regression: `smart-drive organize --dry-run --json`, a plain `organize` (a dry run by default) and `organize --clean
--json` (also a dry run) created `.metadata_never_index`, `.fseventsd/` and `.fseventsd/no_log` in the drive root, and
their JSON then reported both markers as present. The command wrote the anti-indexing markers before it knew whether
this was a dry run, although a dry run promises to change nothing, and `status`, `clean` (a dry run) and the MCP tool
`ssd_auto_organize` left the root as it was. Found by comparing every path, size and SHA-256 below a fresh root before
and after. Only `--apply` writes the markers now; the JSON of a dry run says which are on the drive
(`anti_indexing_shields`) and which `--apply` would create (`anti_indexing_shields_to_create`), and its text preview
names them.

100% isolated like the other organize tests: a temporary drive, a fake home and AppData, and stand-ins for the list of
running programs and the machine's system references. Nothing on the real machine is read or written.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
from pathlib import Path
from typing import Dict, Sequence
import unittest
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.system_refs import SystemRefIndex
from smart_drive.mcp.server import SmartDriveMCPServer
from tests.helpers import SmartDriveTestCase, TreeState, snapshot_tree, tree_changes

RELINK = importlib.import_module("smart_drive.core.relink")

MARKERS = [".metadata_never_index", ".fseventsd/no_log"]
CREATE_LINE = "Anti-indexing markers that --apply will create: "

# What the JSON of a dry run held before the markers were added
DRY_RUN_KEYS = {
    "status", "cluster_size", "clean_preview", "planned_actions", "protected_by_system", "busy_apps",
    "busy_apps_unknown", "fsmonitor_to_stop", "relink_preview", "anti_indexing_shields", "note",
}


class _Machine(SmartDriveTestCase):
    """A temporary machine: a fake home and AppData, no running programs and no system references."""

    drives = 0

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_appdata.mkdir(parents=True)
        for patcher in (
            patch.object(RELINK, "_running_processes", return_value=set()),
            patch.object(RELINK, "_resolve_locations", return_value=(self.fake_home, self.fake_appdata, False)),
            patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_drive(self, markers: Sequence[str] = (), loose: bool = True) -> Path:
        """A fresh drive root. By default it holds loose files and folders worth organizing and a junk file."""
        self.drives += 1
        drive = self.test_dir / f"drive{self.drives}"
        drive.mkdir()
        if loose:
            (drive / "my_service").mkdir()
            (drive / "my_service" / "pyproject.toml").write_text("[project]\nname = 'my_service'\n", encoding="utf-8")
            (drive / "random_folder").mkdir()
            (drive / "random_folder" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            (drive / "notes.txt").write_text("some notes\n", encoding="utf-8")
            (drive / "loose_weights.safetensors").write_bytes(b"WEIGHTS" * 100)
            (drive / "Thumbs.db").write_bytes(b"junk")
        for marker in markers:
            (drive / marker).parent.mkdir(parents=True, exist_ok=True)
            (drive / marker).write_text("", encoding="utf-8")
        return drive

    def run_cli(self, *argv: str) -> str:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(list(argv))
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue()

    def organize(self, drive: Path, *flags: str) -> str:
        return self.run_cli("organize", "--root", str(drive), *flags)

    def assertUnchanged(self, before: TreeState, after: TreeState) -> None:
        self.assertEqual(tree_changes(before, after), ([], [], []), "the drive was changed")


class TestADryRunWritesNothing(_Machine):
    def test_organize_leaves_the_drive_byte_for_byte_as_it_was(self) -> None:
        for flags in (["--dry-run", "--json"], [], ["--clean", "--json"], ["--dry-run"], ["--clean"]):
            with self.subTest(flags=flags):
                drive = self.new_drive()
                before = snapshot_tree(drive)
                out = self.organize(drive, *flags)
                self.assertUnchanged(before, snapshot_tree(drive))
                if "--json" in flags:  # (it did preview something: this is not an empty plan)
                    data = json.loads(out)
                    self.assertTrue(data["planned_actions"])
                    self.assertEqual(data["clean_preview"] is not None, "--clean" in flags)

    def test_a_drive_with_nothing_to_organize_stays_empty(self) -> None:
        for flags in (["--json"], []):
            with self.subTest(flags=flags):
                drive = self.new_drive(loose=False)
                self.organize(drive, *flags)
                self.assertEqual(snapshot_tree(drive), {})

    def test_the_other_previews_stay_silent_too(self) -> None:
        for argv in (["status", "--json"], ["clean", "--json"]):
            with self.subTest(command=argv[0]):
                drive = self.new_drive()
                before = snapshot_tree(drive)
                self.run_cli(*argv, "--root", str(drive))
                self.assertUnchanged(before, snapshot_tree(drive))
        drive = self.new_drive()
        before = snapshot_tree(drive)
        server = SmartDriveMCPServer(root=str(drive), rate_limit_enabled=False)
        self.assertEqual(server.handle_ssd_auto_organize({"apply": False})["status"], "dry_run")
        self.assertUnchanged(before, snapshot_tree(drive))


class TestTheDryRunJsonSaysWhatApplyWouldWrite(_Machine):
    def preview(self, drive: Path) -> Dict[str, object]:
        return json.loads(self.organize(drive, "--dry-run", "--json"))

    def test_a_fresh_drive_has_no_markers_and_apply_would_create_both(self) -> None:
        data = self.preview(self.new_drive())
        self.assertEqual(data["anti_indexing_shields"], {".metadata_never_index": False, ".fseventsd/no_log": False})
        self.assertEqual(data["anti_indexing_shields_to_create"], MARKERS)

    def test_markers_on_the_drive_are_reported_present_and_are_not_to_be_created(self) -> None:
        data = self.preview(self.new_drive(MARKERS))
        self.assertEqual(data["anti_indexing_shields"], {".metadata_never_index": True, ".fseventsd/no_log": True})
        self.assertEqual(data["anti_indexing_shields_to_create"], [])

    def test_only_the_missing_marker_is_to_be_created(self) -> None:
        data = self.preview(self.new_drive([".metadata_never_index"]))
        self.assertEqual(data["anti_indexing_shields"], {".metadata_never_index": True, ".fseventsd/no_log": False})
        self.assertEqual(data["anti_indexing_shields_to_create"], [".fseventsd/no_log"])

    def test_every_key_it_had_is_still_there_and_the_shields_keep_their_type(self) -> None:
        data = self.preview(self.new_drive())
        self.assertLessEqual(DRY_RUN_KEYS, set(data))
        self.assertEqual(set(data) - DRY_RUN_KEYS, {"anti_indexing_shields_to_create"})
        shields = data["anti_indexing_shields"]
        self.assertIsInstance(shields, dict)
        self.assertEqual(list(shields), MARKERS)
        self.assertTrue(all(isinstance(present, bool) for present in shields.values()))
        self.assertTrue(all(isinstance(marker, str) for marker in data["anti_indexing_shields_to_create"]))


class TestTheTextPreviewNamesThem(_Machine):
    def test_a_fresh_drive_names_both_before_the_closing_hint(self) -> None:
        out = self.organize(self.new_drive())
        line = CREATE_LINE + ", ".join(MARKERS)
        self.assertIn(line, out.splitlines())
        self.assertLess(out.index(line), out.index("Run with --apply"))

    def test_only_the_missing_marker_is_named(self) -> None:
        out = self.organize(self.new_drive([".metadata_never_index"]))
        self.assertIn(CREATE_LINE + ".fseventsd/no_log", out.splitlines())

    def test_nothing_is_said_when_both_are_on_the_drive(self) -> None:
        out = self.organize(self.new_drive(MARKERS))
        self.assertNotIn("Anti-indexing", out)
        self.assertIn("Run with --apply", out)

    def test_a_drive_with_nothing_to_organize_names_them_too(self) -> None:
        out = self.organize(self.new_drive(loose=False))
        self.assertIn("No loose or unorganized items detected", out)
        self.assertIn(CREATE_LINE + ", ".join(MARKERS), out.splitlines())
        self.assertNotIn("Anti-indexing", self.organize(self.new_drive(MARKERS, loose=False)))


class TestApplyStillWritesThem(_Machine):
    def test_apply_creates_both_markers_and_reports_them(self) -> None:
        drive = self.new_drive()
        data = json.loads(self.organize(drive, "--apply", "--json"))
        self.assertEqual(data["status"], "applied")
        self.assertEqual(data["anti_indexing_shields"], {".metadata_never_index": True, ".fseventsd/no_log": True})
        self.assertNotIn("anti_indexing_shields_to_create", data)
        self.assertTrue((drive / ".metadata_never_index").is_file())
        self.assertTrue((drive / ".fseventsd" / "no_log").is_file())
        self.assertGreater(data["zoning"]["moved_count"], 0)

    def test_apply_on_a_drive_with_nothing_to_organize_creates_them_as_well(self) -> None:
        drive = self.new_drive(loose=False)
        data = json.loads(self.organize(drive, "--apply", "--json"))
        self.assertEqual(data["anti_indexing_shields"], {".metadata_never_index": True, ".fseventsd/no_log": True})
        self.assertTrue((drive / ".metadata_never_index").is_file())
        self.assertTrue((drive / ".fseventsd" / "no_log").is_file())

    def test_apply_keeps_a_marker_that_is_there(self) -> None:
        drive = self.new_drive()
        (drive / ".metadata_never_index").write_text("mine\n", encoding="utf-8")
        data = json.loads(self.organize(drive, "--apply", "--json"))
        self.assertEqual(data["anti_indexing_shields"], {".metadata_never_index": True, ".fseventsd/no_log": True})
        self.assertEqual((drive / ".metadata_never_index").read_text(encoding="utf-8"), "mine\n")

    def test_a_dry_run_followed_by_apply_ends_with_the_markers_on_the_drive(self) -> None:
        drive = self.new_drive()
        self.organize(drive, "--dry-run")
        self.assertFalse((drive / ".metadata_never_index").exists())
        self.organize(drive, "--apply")
        self.assertTrue((drive / ".metadata_never_index").is_file())
        self.assertTrue((drive / ".fseventsd" / "no_log").is_file())


if __name__ == "__main__":
    unittest.main()
