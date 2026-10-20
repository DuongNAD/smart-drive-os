"""tests/test_classifier_targets.py - `classify` never sorts inside an app, library or project folder, wherever it is aimed.

Regression: `classify E:\\APP` still split composer.bat from composer.phar, because only the folders a recursive walk
meets were judged, not the folder named on the command line. The target, every folder above it below the drive root
(and, for a file, the folder it lies in) are judged now: when one of them is a bundle, holds a program folder or is used
by the system, nothing is sorted and that folder is reported in skipped_bundles. The drive root itself keeps sorting its
loose files, as before.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from smart_drive.core.classifier import ClassifierEngine
from smart_drive.core.system_refs import SystemRef, SystemRefIndex
from tests.classifier_fixtures import (
    APP_FOLDER,
    APP_SUBFOLDER,
    DOCS_FOLDER,
    INSTALLER_DOCS,
    LOOSE_AT_ROOT,
    SCRIPT_FOLDER,
    SEB_FOLDERS,
    SEB_WRAPPERS,
    SONAR_FOLDER,
    DriveCase,
    add_docs_with_installer,
    add_sonarqube_like,
    snapshot,
    write,
)

EOS_REASON = "program or library EncryptData.dll"
SEB_REASON = "holds program folder SEB"


class TestAFolderNamedOnTheCommandLine(DriveCase):
    def assert_kept_whole(self, target: str, bundle: str, reason: str) -> None:
        """Nothing is planned or moved for ``target`` in any mode, and ``bundle`` is the one reported."""
        path = self.drive / target
        for flags in ((), ("--recursive",)):
            with self.subTest(target=target, flags=flags):
                data = self.plan(str(path), *flags)
                self.assertEqual((data["results"], data["actions"]), ([], []))
                self.assertEqual(self.bundles(data), {bundle: reason})
                code, out, err = self.classify(str(path), *flags, "--dry-run")
                self.assertEqual(code, 0, err)
                self.assertIn(f"Skipped {self.drive / bundle} — kept whole ({reason})", out)
        with self.subTest(target=target, mode="apply"):
            before = snapshot(self.drive)
            code, out, err = self.classify(str(path), "--recursive", "--apply", "--json")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["actions"], [])
            self.assertEqual(snapshot(self.drive), before)

    def test_a_bundle_named_as_the_target_moves_nothing(self) -> None:
        self.assert_kept_whole(SCRIPT_FOLDER, SCRIPT_FOLDER, "launcher script composer.bat")
        self.assert_kept_whole(APP_FOLDER, APP_FOLDER, EOS_REASON)

    def test_a_folder_or_a_file_inside_a_bundle_moves_nothing(self) -> None:
        self.assert_kept_whole(APP_SUBFOLDER, APP_FOLDER, EOS_REASON)
        self.assert_kept_whole(APP_SUBFOLDER + "/exam_notes.md", APP_FOLDER, EOS_REASON)
        self.assert_kept_whole(SCRIPT_FOLDER + "/composer.phar", SCRIPT_FOLDER, "launcher script composer.bat")

    def test_the_outermost_folder_to_keep_whole_is_the_one_reported(self) -> None:
        for target in (SEB_FOLDERS[0] + "/cert", SEB_FOLDERS[0], SEB_WRAPPERS[0]):
            self.assert_kept_whole(target, SEB_WRAPPERS[0], SEB_REASON)
        self.assert_kept_whole(SEB_FOLDERS[1] + "/cert", SEB_WRAPPERS[1], SEB_REASON)

    def test_a_folder_that_holds_an_installer_subfolder_is_kept_whole(self) -> None:
        add_docs_with_installer(self.drive)
        reason = "holds program folder installer"
        self.assert_kept_whole(INSTALLER_DOCS, INSTALLER_DOCS, reason)
        self.assert_kept_whole(INSTALLER_DOCS + "/week1", INSTALLER_DOCS, reason)
        self.assert_kept_whole(INSTALLER_DOCS + "/week1/lecture2.md", INSTALLER_DOCS, reason)

    def test_a_sonarqube_like_app_and_every_folder_in_it_move_nothing(self) -> None:
        add_sonarqube_like(self.drive)
        reason = "holds program folder lib"
        for target in ("", "/conf", "/data/es8/_state", "/logs/sonar.log", "/bin/windows"):
            self.assert_kept_whole(SONAR_FOLDER + target, SONAR_FOLDER, reason)

    def test_a_plain_folder_of_documents_is_still_sorted(self) -> None:
        week1 = self.plan(str(self.drive / DOCS_FOLDER / "week1"))
        self.assertEqual(sorted(Path(a["source_path"]).name for a in week1["actions"]), ["lecture.md", "reading.pdf"])
        self.assertEqual(week1["skipped_bundles"], [])
        tree = self.plan(str(self.drive / DOCS_FOLDER), "--recursive")
        self.assertEqual(
            sorted(Path(a["source_path"]).name for a in tree["actions"]), ["deck.md", "lecture.md", "reading.pdf"]
        )
        one = self.plan(str(self.drive / DOCS_FOLDER / "slides" / "deck.md"))
        self.assertEqual([Path(a["source_path"]).name for a in one["actions"]], ["deck.md"])

    def test_the_drive_root_keeps_sorting_its_loose_files_whatever_it_holds(self) -> None:
        write(self.drive / "helper_tool.exe", b"MZ\x90\x00")
        for target in (str(self.drive), str(self.drive) + "/"):
            with self.subTest(target=target):
                data = self.plan(target)
                self.assertEqual(sorted(self.planned(data)), sorted([*LOOSE_AT_ROOT, "helper_tool.exe"]))
                self.assertEqual(data["skipped_bundles"], [])

    def test_a_folder_used_by_the_system_and_everything_in_it_move_nothing(self) -> None:
        tool = self.drive / "Portable Tool"
        write(tool / "docs" / "guide.md", b"# guide\n")
        self.refs = SystemRefIndex([
            SystemRef(kind="shortcut", source=r"C:\Users\X\Desktop\Tool.lnk", target=str(tool / "tool.exe")),
        ])
        for target in ("Portable Tool", "Portable Tool/docs", "Portable Tool/docs/guide.md"):
            with self.subTest(target=target):
                data = self.plan(str(self.drive / target))
                self.assertEqual(data["actions"], [])
                self.assertEqual(self.bundles(data), {"Portable Tool": "used by the system: 1 shortcut"})
                self.assertEqual([self.relative(item["path"]) for item in data["protected_by_system"]], ["Portable Tool"])
                _, out, _ = self.classify(str(self.drive / target), "--dry-run")
                self.assertEqual(out.count(str(tool)), 1, "the same folder is not announced twice")

    def test_a_top_level_folder_is_not_judged_by_what_the_system_uses_inside_it(self) -> None:
        """Games, Downloads, 05_Dev_Toolbox... hold many apps side by side: the app folder below them is the unit."""
        write(self.drive / "Games" / "Notes" / "todo.md", b"# todo\n")
        write(self.drive / "Games" / "Saves" / "slot1.sav", b"\x01\x02")
        self.refs = SystemRefIndex([
            SystemRef(kind="shortcut", source=r"C:\Users\X\Desktop\Play.lnk", target=str(self.drive / "Games" / "Saves" / "play.bin")),
        ])
        notes = self.plan(str(self.drive / "Games" / "Notes"))
        self.assertEqual(self.planned(notes), ["Games/Notes/todo.md"])
        self.assertEqual((notes["skipped_bundles"], notes["protected_by_system"]), ([], []))
        saves = self.plan(str(self.drive / "Games" / "Saves"))
        self.assertEqual(saves["actions"], [])
        self.assertEqual(self.bundles(saves), {"Games/Saves": "used by the system: 1 shortcut"})

    def test_a_folder_outside_the_drive_is_judged_by_what_it_holds(self) -> None:
        inbox = self.drive.parent / "inbox"
        write(inbox / "paper.pdf", b"%PDF-1.7\n%%EOF\n")
        write(inbox / "tool.exe", b"MZ\x90\x00")
        refused = self.plan(str(inbox))
        self.assertEqual(refused["actions"], [])
        self.assertEqual([item["path"] for item in refused["skipped_bundles"]], [str(inbox)])
        (inbox / "tool.exe").unlink()
        imported = self.plan(str(inbox))
        self.assertEqual([Path(a["source_path"]).name for a in imported["actions"]], ["paper.pdf"])
        self.assertEqual(imported["skipped_bundles"], [])

    def test_the_engine_refuses_too_while_inspect_path_stays_the_primitive(self) -> None:
        engine = ClassifierEngine(root_path=self.drive, system_refs=SystemRefIndex([]))
        self.assertEqual(engine.scan_and_classify(self.drive / SCRIPT_FOLDER), [])
        self.assertEqual([b["reason"] for b in engine.skipped_bundles], ["launcher script composer.bat"])
        self.assertEqual(engine.scan_and_classify(self.drive / SCRIPT_FOLDER / "composer.phar"), [])
        self.assertIsNotNone(engine.inspect_path(self.drive / SCRIPT_FOLDER / "composer.phar"))
        self.assertEqual(len(engine.scan_and_classify(self.drive / DOCS_FOLDER / "week1")), 2)
        self.assertEqual(engine.skipped_bundles, [])


if __name__ == "__main__":
    unittest.main()
