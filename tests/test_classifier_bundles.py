"""tests/test_classifier_bundles.py - `classify --recursive` never takes an app, a tool or a project apart.

Regression: a recursive scan sorted every file by its own format, so EOSClient.exe left the DLLs it loads, composer.bat
left the composer.phar it runs and SEB's launcher scripts left their cert folder. A folder that directly holds
programs or libraries, launcher scripts, a project manifest or a .git entry (or that the system depends on) is a
bundle: it is reported with the reason, none of its files is sorted and nothing below it is looked at.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Dict, List

from smart_drive.core.classifier import ClassifierEngine, bundle_marker
from smart_drive.core.system_refs import SystemRef, SystemRefIndex
from tests.classifier_fixtures import (
    APP_FOLDER,
    BUNDLE_FOLDERS,
    DOCS_FOLDER,
    LOOSE_AT_ROOT,
    SCRIPT_FOLDER,
    SEB_FOLDERS,
    DriveCase,
    snapshot,
    write,
)


class TestBundleMarker(unittest.TestCase):
    """What makes a folder a bundle, judged from the names it directly holds."""

    PLAIN_FILES = (
        "notes.md", "paper.pdf", "data.csv", "model.gguf", "archive.zip", "photo.png", "config.json", "script.py",
        "requirements.txt", "package-lock.json", "readme.txt", "setup.exe.txt", "libfoo.so.txt", "thing.app",
    )

    def test_a_plain_folder_of_documents_is_not_a_bundle(self) -> None:
        self.assertIsNone(bundle_marker(["slides", "images"], self.PLAIN_FILES))
        self.assertIsNone(bundle_marker([], []))

    def test_programs_and_libraries_make_a_bundle(self) -> None:
        for ext in (".exe", ".dll", ".sys", ".msi", ".so", ".dylib", ".jar"):
            with self.subTest(ext=ext):
                self.assertEqual(bundle_marker([], ["notes.md", "thing" + ext]), "program or library thing" + ext)

    def test_versioned_shared_libraries_count_as_libraries(self) -> None:
        self.assertEqual(bundle_marker([], ["libz.so.1.2.11"]), "program or library libz.so.1.2.11")
        self.assertEqual(bundle_marker([], ["libfoo.so.6"]), "program or library libfoo.so.6")

    def test_an_app_folder_makes_a_bundle_but_a_file_with_that_name_does_not(self) -> None:
        self.assertEqual(bundle_marker(["Tool.app", "docs"], []), "program or library Tool.app")
        self.assertIsNone(bundle_marker([], ["Tool.app"]))

    def test_launcher_scripts_make_a_bundle(self) -> None:
        for ext in (".bat", ".cmd", ".ps1", ".sh", ".command", ".vbs", ".phar"):
            with self.subTest(ext=ext):
                self.assertEqual(bundle_marker([], ["notes.md", "run" + ext]), "launcher script run" + ext)

    def test_project_manifests_make_a_bundle(self) -> None:
        names = (
            "package.json", "pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml", "go.mod", "pom.xml",
            "build.gradle", "build.gradle.kts", "App.csproj", "Solution.sln", "CMakeLists.txt", "Makefile",
            "composer.json", "Gemfile",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(bundle_marker([], ["notes.md", name]), f"project manifest {name}")

    def test_a_git_entry_makes_a_bundle_as_a_folder_or_as_a_file(self) -> None:
        self.assertEqual(bundle_marker([".git"], ["notes.md"]), "repository folder .git")
        self.assertEqual(bundle_marker([], [".git"]), "repository folder .git")  # worktrees and submodules

    def test_an_obsidian_vault_makes_a_bundle(self) -> None:
        self.assertEqual(bundle_marker([".obsidian", "Daily"], ["idea.md"]), "Obsidian vault folder .obsidian")

    def test_names_compare_case_insensitively(self) -> None:
        self.assertEqual(bundle_marker([], ["SETUP.EXE"]), "program or library SETUP.EXE")
        self.assertEqual(bundle_marker([], ["Run.BAT"]), "launcher script Run.BAT")
        self.assertEqual(bundle_marker([], ["MAKEFILE"]), "project manifest MAKEFILE")
        self.assertEqual(bundle_marker(["TOOL.APP"], []), "program or library TOOL.APP")

    def test_macos_sidecar_files_are_not_scripts(self) -> None:
        self.assertIsNone(bundle_marker([], ["._windows.bat", "._macos.command", "._notes.sh"]))

    def test_the_strongest_kind_decides_and_the_first_name_is_quoted_whatever_the_listing_order(self) -> None:
        names: List[str] = ["pom.xml", "run.bat", "b.dll", "A.exe", "c.dll"]
        self.assertEqual(bundle_marker([".git"], names), "program or library A.exe")
        self.assertEqual(bundle_marker([".git"], list(reversed(names))), "program or library A.exe")
        self.assertEqual(bundle_marker([".git"], ["pom.xml", "run.bat"]), "launcher script run.bat")
        self.assertEqual(bundle_marker([".git"], ["pom.xml"]), "project manifest pom.xml")

    def test_any_iterable_will_do(self) -> None:
        self.assertEqual(bundle_marker(iter([".git"]), iter(["a.txt"])), "repository folder .git")


class TestRecursiveScanKeepsBundlesWhole(DriveCase):
    def bundles(self, data: Dict) -> Dict[str, str]:
        return {Path(item["path"]).relative_to(self.drive).as_posix(): item["reason"] for item in data["skipped_bundles"]}

    def test_each_real_case_is_skipped_and_reported_with_its_reason(self) -> None:
        bundles = self.bundles(self.plan("--recursive"))
        self.assertEqual(sorted(bundles), sorted(BUNDLE_FOLDERS))
        self.assertEqual(bundles[APP_FOLDER], "program or library EncryptData.dll")
        self.assertEqual(bundles[SCRIPT_FOLDER], "launcher script composer.bat")
        for seb in SEB_FOLDERS:
            self.assertEqual(bundles[seb], "launcher script macos.command")

    def test_no_file_of_a_bundle_is_planned_and_nothing_below_it_is_entered(self) -> None:
        data = self.plan("--recursive")
        sources = [Path(a["source_path"]).relative_to(self.drive).as_posix() for a in data["actions"]]
        for folder in BUNDLE_FOLDERS:
            self.assertEqual([s for s in sources if s.startswith(folder + "/")], [], folder)
        self.assertNotIn("mitmproxy-ca-cert.pem", [Path(s).name for s in sources], "the cert folder is below a bundle")
        self.assertFalse(any(a["collision_resolved"] for a in data["actions"]), "no windows_1.bat style renames")

    def test_a_plain_nested_folder_of_documents_is_still_sorted(self) -> None:
        data = self.plan("--recursive")
        targets = {Path(a["source_path"]).name: a["relative_target"] for a in data["actions"]}
        self.assertEqual(targets["lecture.md"], "02_Learning_Knowledge/Notes/lecture.md")
        self.assertEqual(targets["deck.md"], "02_Learning_Knowledge/Notes/deck.md")
        self.assertEqual(targets["reading.pdf"], "02_Learning_Knowledge/Papers/reading.pdf")
        self.assertEqual(sorted(targets), sorted(["lecture.md", "deck.md", "reading.pdf", *LOOSE_AT_ROOT]))

    def test_apply_with_recursive_leaves_every_bundle_byte_for_byte_alone(self) -> None:
        before = {folder: snapshot(self.drive / folder) for folder in ("APP", APP_FOLDER, "LuuTru_Desktop")}
        code, out, err = self.classify("--recursive", "--apply", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual({folder: snapshot(self.drive / folder) for folder in before}, before)
        moved = sorted(Path(a["source_path"]).name for a in json.loads(out)["actions"] if a["status"] == "MOVED")
        self.assertEqual(moved, sorted(["deck.md", "lecture.md", "reading.pdf", *LOOSE_AT_ROOT]))

    def test_the_default_scan_reports_no_bundles(self) -> None:
        self.assertEqual(self.plan()["skipped_bundles"], [])

    def test_the_text_output_lists_the_bundles_with_their_reason(self) -> None:
        _, out, _ = self.classify("--recursive", "--dry-run")
        self.assertIn(f"Skipped {self.drive / SCRIPT_FOLDER} — kept whole (launcher script composer.bat)", out)
        self.assertIn("kept whole (program or library EncryptData.dll)", out)
        self.assertIn("app, library and project folders are kept whole", out)

    def test_the_report_of_apply_organization_carries_the_bundles(self) -> None:
        engine = ClassifierEngine(root_path=self.drive, system_refs=SystemRefIndex([]))
        results = engine.scan_and_classify(self.drive, recursive=True)
        report = engine.apply_organization(results, dry_run=True)
        self.assertEqual(report.skipped_bundles, engine.skipped_bundles)
        self.assertEqual(len(report.to_dict()["skipped_bundles"]), len(BUNDLE_FOLDERS))
        engine.scan_and_classify(self.drive)
        self.assertEqual(engine.skipped_bundles, [], "a scan starts from a clean list")


class TestWhatCountsAsABundleInAScan(DriveCase):
    def test_the_system_depends_on_a_folder_so_it_is_a_bundle_and_is_listed_once(self) -> None:
        tool = self.drive / "Portable Tool"
        write(tool / "notes.txt", b"text")
        self.refs = SystemRefIndex([
            SystemRef(kind="shortcut", source=r"C:\Users\X\Desktop\Tool.lnk", target=str(tool / "tool.exe")),
        ])
        data = self.plan("--recursive")
        self.assertEqual(
            [item["path"] for item in data["protected_by_system"]],
            [str(tool)],
        )
        reasons = {Path(item["path"]).name: item["reason"] for item in data["skipped_bundles"]}
        self.assertTrue(reasons["Portable Tool"].startswith("used by the system: 1 shortcut"), reasons)
        self.assertNotIn("notes.txt", [Path(a["source_path"]).name for a in data["actions"]])

        _, out, _ = self.classify("--recursive", "--dry-run")
        self.assertEqual(out.count(str(tool)), 1, "the same folder is not announced twice")

    def test_a_repo_is_still_moved_whole_and_a_manifest_without_a_repo_is_a_bundle(self) -> None:
        write(self.drive / "scratch" / "my_project" / "package.json", b"{}")
        write(self.drive / "scratch" / "my_project" / "run.bat", b"@echo off\r\n")
        write(self.drive / "scratch" / "my_project" / "src" / "index.js", b"console.log(1)\n")
        write(self.drive / "scratch" / "java_tool" / "pom.xml", b"<project/>")
        write(self.drive / "scratch" / "java_tool" / "notes.md", b"# not sorted on its own\n")

        data = self.plan("--recursive")
        folders = [r for r in data["results"] if r["is_dir"]]
        self.assertEqual([(r["name"], r["format_type"]) for r in folders], [("my_project", "Node Project")])
        planned = [Path(a["source_path"]).relative_to(self.drive).as_posix() for a in data["actions"]]
        self.assertFalse([p for p in planned if "/my_project/" in p or "/java_tool/" in p], planned)
        bundles = {Path(item["path"]).name: item["reason"] for item in data["skipped_bundles"]}
        self.assertEqual(bundles["java_tool"], "project manifest pom.xml")
        self.assertNotIn("my_project", bundles, "a repo is a unit of its own, not a skipped bundle")

    def test_an_app_folder_is_a_bundle_even_when_it_sits_directly_in_the_scanned_folder(self) -> None:
        write(self.drive / "Tool.app" / "Contents" / "Resources" / "help.md", b"# help\n")
        write(self.drive / "Tool.app" / "Contents" / "Info.plist", b"<plist/>")
        data = self.plan("--recursive")
        bundles = {Path(item["path"]).name: item["reason"] for item in data["skipped_bundles"]}
        self.assertEqual(bundles["Tool.app"], "macOS app folder")
        self.assertNotIn("help.md", [Path(a["source_path"]).name for a in data["actions"]])

    def test_an_obsidian_vault_is_a_bundle(self) -> None:
        write(self.drive / "Notes" / "Vault" / ".obsidian" / "app.json", b"{}")
        write(self.drive / "Notes" / "Vault" / "Daily" / "idea.md", b"# idea\n")
        data = self.plan("--recursive")
        bundles = {Path(item["path"]).name: item["reason"] for item in data["skipped_bundles"]}
        self.assertEqual(bundles["Vault"], "Obsidian vault folder .obsidian")
        self.assertNotIn("idea.md", [Path(a["source_path"]).name for a in data["actions"]])

    def test_the_scanned_folder_itself_is_never_judged(self) -> None:
        """The drive root and a folder named on the command line may hold programs: their loose files are sorted."""
        downloads = self.drive / "inbox"
        write(downloads / "installer.exe", b"MZ\x90\x00")
        write(downloads / "paper.pdf", b"%PDF-1.7\n%%EOF\n")
        for argv in ((), ("--recursive",)):
            with self.subTest(argv=argv):
                code, out, err = self.classify(str(downloads), *argv, "--dry-run", "--json")
                self.assertEqual(code, 0, err)
                data = json.loads(out)
                self.assertEqual(sorted(Path(a["source_path"]).name for a in data["actions"]), ["installer.exe", "paper.pdf"])
                self.assertEqual(data["skipped_bundles"], [])

    def test_sidecar_folders_zip_tools_leave_behind_are_not_reported(self) -> None:
        write(self.drive / "unzipped" / "__MACOSX" / "SEB" / "._windows.bat", b"\x00\x05\x16\x07")
        data = self.plan("--recursive")
        self.assertEqual([item for item in data["skipped_bundles"] if "__MACOSX" in item["path"]], [])


if __name__ == "__main__":
    unittest.main()
