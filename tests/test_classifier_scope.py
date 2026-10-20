"""tests/test_classifier_scope.py - `classify` looks only at the files directly in the scanned folder by default.

Regression: `classify` used to walk every subfolder, so a dry run on a real drive planned 861 moves that pulled files
out of the folders they belong to (an app away from its DLLs, composer.bat away from composer.phar, SEB's launcher
scripts away from their cert folder). Only the loose files of the scanned folder are looked at now; looking inside
subfolders is opt-in with --recursive, and --no-recursive stays accepted for scripts that already pass it.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from smart_drive.cli.main import main
from smart_drive.core.classifier import ClassifierEngine
from smart_drive.core.system_refs import SystemRef, SystemRefIndex
from tests.classifier_fixtures import (
    APP_FOLDER,
    DOCS_FOLDER,
    LOOSE_AT_ROOT,
    SCRIPT_FOLDER,
    DriveCase,
    snapshot,
)


class TestEngineScope(DriveCase):
    def test_the_engine_looks_at_the_loose_files_of_the_folder_only(self) -> None:
        engine = ClassifierEngine(root_path=self.drive, system_refs=SystemRefIndex([]))
        self.assertEqual(sorted(r.name for r in engine.scan_and_classify(self.drive)), sorted(LOOSE_AT_ROOT))
        self.assertFalse(engine.scanned_recursively)

    def test_recursive_walks_the_subfolders_as_well(self) -> None:
        engine = ClassifierEngine(root_path=self.drive, system_refs=SystemRefIndex([]))
        names = {r.name for r in engine.scan_and_classify(self.drive, recursive=True)}
        self.assertTrue({"lecture.md", "reading.pdf", "deck.md", *LOOSE_AT_ROOT} <= names)
        self.assertTrue(engine.scanned_recursively)

    def test_subfolders_are_neither_entered_nor_reported_by_the_default_scan(self) -> None:
        project = self.drive / "my_project"
        project.mkdir()
        (project / "package.json").write_text("{}", encoding="utf-8")
        refs = SystemRefIndex([SystemRef(kind="path", source="PATH (User)", target=str(self.drive / APP_FOLDER))])
        engine = ClassifierEngine(root_path=self.drive, system_refs=refs)

        results = engine.scan_and_classify(self.drive)
        self.assertFalse(any(r.is_dir for r in results), "a repo folder is only a unit once subfolders are scanned")
        self.assertEqual(engine.protected_by_system, [], "folders that are not looked at are not reported either")

        results = engine.scan_and_classify(self.drive, recursive=True)
        self.assertIn("my_project", [r.name for r in results if r.is_dir])
        self.assertEqual([Path(item["path"]).name for item in engine.protected_by_system], [APP_FOLDER])


class TestCliScope(DriveCase):
    def test_the_default_plan_moves_only_the_files_lying_in_the_scanned_folder(self) -> None:
        data = self.plan()
        sources = [Path(a["source_path"]) for a in data["actions"]]
        self.assertEqual(sorted(p.name for p in sources), sorted(LOOSE_AT_ROOT))
        self.assertTrue(all(p.parent == self.drive for p in sources))
        self.assertFalse(any(a["collision_resolved"] for a in data["actions"]), "no windows_1.bat style renames")
        self.assertIs(data["recursive"], False)

    def test_dry_run_and_apply_leave_every_folder_whole(self) -> None:
        folders = (APP_FOLDER, SCRIPT_FOLDER, "LuuTru_Desktop", DOCS_FOLDER)
        before = {name: snapshot(self.drive / name) for name in folders}
        self.plan()
        code, out, err = self.classify("--apply", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual([a["status"] for a in json.loads(out)["actions"]], ["MOVED", "MOVED"])
        self.assertEqual({name: snapshot(self.drive / name) for name in folders}, before)
        self.assertTrue((self.drive / "01_AI_Models" / "Weights" / "loose_model.gguf").is_file())
        self.assertTrue((self.drive / "02_Learning_Knowledge" / "Notes" / "loose_notes.md").is_file())

    def test_no_recursive_is_still_accepted_and_is_what_happens_anyway(self) -> None:
        default = self.plan()
        legacy = self.plan("--no-recursive")
        self.assertEqual(legacy["actions"], default["actions"])
        self.assertIs(legacy["recursive"], False)

    def test_recursive_and_no_recursive_cannot_be_combined(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            main(["classify", "--root", str(self.drive), "--recursive", "--no-recursive"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("not allowed with", err.getvalue())

    def test_recursive_looks_inside_subfolders(self) -> None:
        data = self.plan("--recursive")
        names = {Path(a["source_path"]).name for a in data["actions"]}
        self.assertTrue({"lecture.md", "reading.pdf", "deck.md", *LOOSE_AT_ROOT} <= names)
        self.assertIs(data["recursive"], True)

    def test_the_text_output_says_how_far_the_scan_reached(self) -> None:
        for argv in ((), ("--dry-run",)):
            with self.subTest(argv=argv):
                self.assertIn("Add '--recursive'", self.classify(*argv)[1])
                self.assertIn("and its subfolders", self.classify(*argv, "--recursive")[1])


if __name__ == "__main__":
    unittest.main()
