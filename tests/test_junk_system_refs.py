"""tests/test_junk_system_refs.py - junk is never reported inside a folder Windows or an installed app depends on.

Regression: `clean --tier 2` and `checkup` flagged `Arduno/Arduino IDE/resources/app/plugins/*/extension/dist`,
`Arduno/Arduino IDE/resources/app/lib/build` and `Git/usr/share/vim/vim91/{pack,autoload,import}/dist` for permanent
deletion. E:\\Arduno and E:\\Git were already protected_by_system for `organize` and `classify` (PATH, installed apps,
shortcuts, services), but the junk detector never asked the SystemRefIndex.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.cli.main import main
from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import KEPT_SYSTEM_REFERENCED, JunkDetector
from smart_drive.core.system_refs import RegistryReader, SystemRef, SystemRefIndex
from tests.helpers import SmartDriveTestCase


class RefCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_refs_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel: str, data: str = "x") -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def ref(self, rel: str, kind: str = "installed_app", source: str = 'installed app "Demo"') -> SystemRef:
        return SystemRef(kind=kind, source=source, target=str(self.root.joinpath(*rel.split("/"))))

    def detector(self, *refs: SystemRef, tier: JunkTier = JunkTier.TIER_3_SENSITIVE) -> JunkDetector:
        return JunkDetector(str(self.root), max_tier=tier, system_refs=SystemRefIndex(list(refs)))

    def found(self, detector: JunkDetector) -> Set[str]:
        return {item.rel_path.replace("\\", "/") for item in detector.find_junk()}

    def make_application(self) -> None:
        """An installed application whose own folders are named like junk."""
        self.write("Arduno/IDE/package.json")
        self.write("Arduno/IDE/resources/lib/build/x.js")
        self.write("Arduno/IDE/plugins/ext/package.json")
        self.write("Arduno/IDE/plugins/ext/dist/index.js")
        self.write("Arduno/IDE/__pycache__/m.pyc")
        self.write("Arduno/IDE/Thumbs.db")
        self.write("Arduno/IDE/scratch.tmp")

    def make_project(self) -> None:
        self.write("work/package.json")
        self.write("work/dist/app.js")
        self.write("work/__pycache__/m.pyc")


class TestFoldersTheSystemDependsOn(RefCase):
    def test_nothing_of_any_tier_is_reported_inside_an_installed_application(self) -> None:
        self.make_application()
        self.make_project()
        detector = self.detector(self.ref("Arduno/IDE/IDE.exe"))
        self.assertEqual(self.found(detector), {"work/dist", "work/__pycache__"})
        self.assertEqual(detector.kept, {KEPT_SYSTEM_REFERENCED: 5})

    def test_without_references_the_same_tree_is_reported(self) -> None:
        self.make_application()
        found = self.found(self.detector())
        self.assertTrue({"Arduno/IDE/__pycache__", "Arduno/IDE/Thumbs.db", "Arduno/IDE/scratch.tmp"} <= found)

    def test_the_outermost_folder_that_holds_a_reference_is_the_one_listed(self) -> None:
        self.make_application()
        detector = self.detector(self.ref("Arduno/IDE/IDE.exe", source='installed app "Arduino IDE 2.3"'))
        detector.find_junk()
        self.assertEqual(len(detector.protected_by_system), 1)
        (entry,) = detector.protected_by_system
        self.assertEqual(Path(entry["path"]), self.root / "Arduno")
        self.assertEqual(entry["kept_items"], 5)
        self.assertEqual(entry["refs"], [{"kind": "installed_app", "source": 'installed app "Arduino IDE 2.3"'}])
        self.assertIn("Arduino IDE 2.3", entry["used_by"])

    def test_every_kind_of_reference_protects(self) -> None:
        for kind in ("path", "service", "startup", "shortcut", "shortcut_dep", "env"):
            with self.subTest(kind=kind):
                self.write("app/bin/tool.exe")
                self.write("app/__pycache__/m.pyc")
                detector = self.detector(self.ref("app/bin/tool.exe", kind=kind))
                self.assertNotIn("app/__pycache__", self.found(detector))
                shutil.rmtree(self.root / "app")

    def test_a_folder_that_holds_a_referenced_file_is_kept_whole(self) -> None:
        self.write("proj/package.json")
        self.write("proj/dist/app.exe")  # a shortcut points at the program built here
        self.write("proj/__pycache__/m.pyc")
        self.make_project()
        detector = self.detector(self.ref("proj/dist/app.exe", kind="shortcut"))
        self.assertEqual(self.found(detector), {"work/dist", "work/__pycache__"})
        self.assertEqual(detector.kept, {KEPT_SYSTEM_REFERENCED: 2})

    def test_a_referenced_file_in_the_drive_root_is_kept_and_its_neighbours_are_not(self) -> None:
        self.write("Thumbs.db")
        self.write("other.tmp")
        detector = self.detector(self.ref("other.tmp", kind="startup"))
        self.assertEqual(self.found(detector), {"Thumbs.db"})
        self.assertEqual(detector.kept, {KEPT_SYSTEM_REFERENCED: 1})

    def test_folders_that_nothing_refers_to_are_not_protected(self) -> None:
        self.make_application()
        self.make_project()
        self.write("elsewhere/Thumbs.db")
        found = self.found(self.detector(self.ref("Arduno/IDE/IDE.exe")))
        self.assertIn("elsewhere/Thumbs.db", found)
        self.assertIn("work/__pycache__", found)

    def test_a_path_entry_on_the_drive_root_does_not_protect_the_whole_drive(self) -> None:
        self.make_project()
        self.write("tool.exe")
        detector = self.detector(
            SystemRef(kind="path", source="PATH (User)", target=str(self.root)),
            self.ref("tool.exe", kind="path"),
        )
        self.assertEqual(self.found(detector), {"work/dist", "work/__pycache__"})
        self.assertEqual(detector.kept, {})

    def test_a_folder_holding_an_uninstaller_is_an_installed_application(self) -> None:
        self.write("myapp/unins000.exe")
        self.write("myapp/__pycache__/m.pyc")
        self.write("myapp/Thumbs.db")
        self.make_project()
        detector = self.detector()  # no registry entry, no shortcut: the uninstaller alone says it
        self.assertEqual(self.found(detector), {"work/dist", "work/__pycache__"})
        self.assertEqual(detector.kept, {KEPT_SYSTEM_REFERENCED: 2})

    def test_the_polaris_cache_of_a_learning_unit_is_kept_in_such_a_folder_too(self) -> None:
        self.write("course/polaris/brief.json", "{}")
        self.write("course/polaris/.cache/api.json", "{}")
        self.write("other/polaris/brief.json", "{}")
        self.write("other/polaris/.cache/api.json", "{}")
        detector = self.detector(self.ref("course/notes.exe", kind="shortcut"), tier=JunkTier.TIER_2_DEV_CACHE)
        self.assertEqual(self.found(detector), {"other/polaris/.cache"})


class TestTheIndexIsBuiltOnce(RefCase):
    def test_it_is_read_from_the_computer_only_when_something_looks_like_junk(self) -> None:
        self.write("docs/readme.txt")
        with patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])) as collect:
            self.assertEqual(JunkDetector(str(self.root)).find_junk(), [])
        collect.assert_not_called()

    def test_it_is_read_once_for_the_whole_scan_with_the_drive_as_a_shortcut_root(self) -> None:
        for i in range(5):
            self.write(f"pkg{i}/__pycache__/m.pyc")
            self.write(f"pkg{i}/Thumbs.db")
        with patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])) as collect:
            detector = JunkDetector(str(self.root))
            self.assertEqual(len(detector.find_junk()), 10)
            detector.find_junk()
        collect.assert_called_once_with(shortcut_roots=[str(self.root)])

    def test_a_given_index_is_used_as_it_is(self) -> None:
        self.write("pkg/__pycache__/m.pyc")
        index = SystemRefIndex([])
        with patch.object(SystemRefIndex, "collect", side_effect=AssertionError("collected despite a given index")):
            detector = JunkDetector(str(self.root), system_refs=index)
            self.assertEqual(len(detector.find_junk()), 1)
        self.assertIs(detector.system_refs, index)

    def test_each_folder_is_asked_about_once(self) -> None:
        for i in range(20):
            self.write(f"pkg{i}/sub/__pycache__/m.pyc")
        asked: List[str] = []
        real = SystemRefIndex.check_folder

        def counting(index: SystemRefIndex, folder: Any, is_file: Optional[bool] = None) -> Any:
            asked.append(str(folder))
            return real(index, folder, is_file=is_file)

        with patch.object(SystemRefIndex, "check_folder", autospec=True, side_effect=counting):
            detector = JunkDetector(str(self.root), system_refs=SystemRefIndex([]))
            self.assertEqual(len(detector.find_junk()), 20)
        # pkgN, pkgN/sub and the __pycache__ folder itself, each once; the drive root never
        self.assertEqual(len(asked), 60)
        self.assertEqual(len(set(asked)), 60)

    def test_a_folder_inside_a_protected_one_is_not_looked_at_again(self) -> None:
        self.write("app/bin/tool.exe")
        for i in range(10):
            self.write(f"app/part{i}/__pycache__/m.pyc")
        asked: List[str] = []
        real = SystemRefIndex.check_folder

        def counting(index: SystemRefIndex, folder: Any, is_file: Optional[bool] = None) -> Any:
            asked.append(str(folder))
            return real(index, folder, is_file=is_file)

        with patch.object(SystemRefIndex, "check_folder", autospec=True, side_effect=counting):
            detector = JunkDetector(str(self.root), system_refs=SystemRefIndex([self.ref("app/bin/tool.exe")]))
            self.assertEqual(detector.find_junk(), [])
        self.assertEqual(asked, [str(self.root / "app")])
        self.assertEqual(detector.kept, {KEPT_SYSTEM_REFERENCED: 10})

    def test_references_elsewhere_on_the_computer_do_not_matter_to_the_drive(self) -> None:
        self.make_project()
        elsewhere = SystemRef(kind="service", source="service Demo", target=str(self.root.parent / "elsewhere" / "svc.exe"))
        sibling = SystemRef(kind="path", source="PATH", target=str(self.root) + "_sibling")  # shares the name prefix
        detector = self.detector(elsewhere, sibling, self.ref("work/tool.exe"), tier=JunkTier.TIER_1_SAFE)
        self.assertEqual(self.found(detector), set())  # work/ holds a reference, so its pycache is kept
        self.assertEqual([r.source for r in detector._drive_refs().refs], ['installed app "Demo"'])
        self.assertEqual(len(detector.system_refs.refs), 3)


class TestCleanCommandKeepsApplications(RefCase):
    def run_clean(self, index: SystemRefIndex, *extra: str) -> Dict[str, Any]:
        out = io.StringIO()
        with patch.object(SystemRefIndex, "collect", return_value=index):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["clean", "--root", str(self.root), "--json", *extra])
        self.assertEqual(code, 0, out.getvalue())
        return json.loads(out.getvalue())

    def test_dry_run_lists_only_what_nothing_depends_on(self) -> None:
        self.make_application()
        self.make_project()
        report = self.run_clean(SystemRefIndex([self.ref("Arduno/IDE/IDE.exe")]), "--tier", "3")
        listed = {j["rel_path"].replace("\\", "/") for j in report["junk_items"]}
        self.assertEqual(listed, {"work/dist", "work/__pycache__"})

    def test_apply_leaves_the_application_alone(self) -> None:
        self.make_application()
        self.make_project()
        report = self.run_clean(SystemRefIndex([self.ref("Arduno/IDE/IDE.exe")]), "--tier", "3", "--apply")
        self.assertEqual(report["failed_count"], 0)
        self.assertFalse((self.root / "work" / "dist").exists())
        for rel in ("resources/lib/build/x.js", "plugins/ext/dist/index.js", "__pycache__/m.pyc", "Thumbs.db", "scratch.tmp"):
            self.assertTrue((self.root / "Arduno" / "IDE" / rel).is_file(), rel)


class TestCheckupSharesTheIndex(SmartDriveTestCase):
    def test_the_junk_scan_and_the_zoner_share_one_index_and_skip_applications(self) -> None:
        drive = self.create_mock_drive()
        app = drive / "Arduno" / "IDE"
        (app / "__pycache__").mkdir(parents=True)
        (app / "__pycache__" / "m.pyc").write_bytes(b"x" * 100)
        (app / "Thumbs.db").write_bytes(b"x")
        (app / "IDE.exe").write_bytes(b"MZ")
        home = self.test_dir / "home"
        (home / "AppData" / "Roaming").mkdir(parents=True)
        fake_temp = self.test_dir / "sys_temp"
        fake_temp.mkdir()
        index = SystemRefIndex([SystemRef(kind="installed_app", source='installed app "Arduino IDE"', target=str(app / "IDE.exe"))])

        def checkup() -> Dict[str, Any]:
            with patch.object(SystemRefIndex, "collect", return_value=index), \
                    patch("smart_drive.core.relink._running_processes", return_value=set()), \
                    patch("smart_drive.core.relink.tempfile.gettempdir", return_value=str(fake_temp)):
                return run_checkup(
                    root=str(drive),
                    lang="en",
                    home=home,
                    app_data=home / "AppData" / "Roaming",
                    reader=RegistryReader(tree={}),
                    scan_c_caches=False,
                )

        with patch("smart_drive.cli.cmd_checkup.JunkDetector", wraps=JunkDetector) as detector_class:
            with_application = checkup()["junk"]["tier_1_2_count"]
        self.assertIs(detector_class.call_args.kwargs["system_refs"], index)

        shutil.rmtree(app)  # the same drive without the application: its two junk items were not counted
        self.assertEqual(checkup()["junk"]["tier_1_2_count"], with_application)


if __name__ == "__main__":
    unittest.main()
