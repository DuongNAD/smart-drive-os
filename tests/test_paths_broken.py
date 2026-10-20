"""tests/test_paths_broken.py - Unit and CLI tests for `smart-drive paths --broken` (T16).

100% isolated: uses temporary directories, fake home/AppData, and fake RegistryReader.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from smart_drive.cli.cmd_paths import inspect_broken_paths
from smart_drive.cli.main import main as cli_main
from smart_drive.core.relink import last_moves
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from tests.helpers import SmartDriveTestCase
from tests.test_system_refs import _build_lnk_bytes


class TestPathsBroken(SmartDriveTestCase):
    """Tests for `inspect_broken_paths` and `smart-drive paths --broken [--fix] [--dry-run] [--json]`."""

    def setUp(self) -> None:
        super().setUp()
        self.drive = self.create_mock_drive()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = (
            self.fake_home / "Library" / "Application Support"
            if sys.platform == "darwin"
            else self.fake_home / "AppData" / "Roaming"
        )
        self.fake_temp = self.test_dir / "sys_temp"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.fake_appdata.mkdir(parents=True, exist_ok=True)
        self.fake_temp.mkdir(parents=True, exist_ok=True)
        self._proc_patcher = patch("smart_drive.core.relink._running_processes", return_value=set())
        self._proc_patcher.start()
        self._temp_patcher = patch("smart_drive.core.relink.tempfile.gettempdir", return_value=str(self.fake_temp))
        self._temp_patcher.start()

    def tearDown(self) -> None:
        if hasattr(self, "_temp_patcher"):
            self._temp_patcher.stop()
        if hasattr(self, "_proc_patcher"):
            self._proc_patcher.stop()
        super().tearDown()

    def _cli_env(self) -> dict[str, str]:
        return {
            "HOME": str(self.fake_home),
            "USERPROFILE": str(self.fake_home),
            "APPDATA": str(self.fake_appdata),
            "LOCALAPPDATA": str(self.fake_home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.fake_appdata),
        }

    def _paths_cli(self, reader: RegistryReader, *flags: str) -> tuple[int, str]:
        """Runs `paths --broken *flags` on the mock drive with `reader` as the registry; returns (exit code, stdout)."""
        orig_collect = SystemRefIndex.collect
        out = io.StringIO()
        with patch.dict(os.environ, self._cli_env()):
            with patch.object(
                SystemRefIndex, "collect", side_effect=lambda **kw: orig_collect(home=self.fake_home, reader=reader)
            ):
                with contextlib.redirect_stdout(out):
                    rc = cli_main(["paths", "--broken", *flags, "--root", str(self.drive)])
        return rc, out.getvalue()

    def test_01_inspect_broken_paths_ide_shortcuts_and_path_entries(self) -> None:
        # 1. Create moved and valid directories on the mock drive
        moved_proj_new = self.drive / "03_Development_Projects" / "moved_proj"
        (moved_proj_new / "src").mkdir(parents=True, exist_ok=True)
        moved_proj_old = self.drive / "moved_proj"
        deleted_proj_old = self.drive / "aptech-learning"

        moved_tool_new = self.drive / "05_Dev_Toolbox" / "moved_tool"
        moved_tool_new.mkdir(parents=True, exist_ok=True)
        (moved_tool_new / "app.exe").write_bytes(b"MZ")
        moved_tool_old = self.drive / "moved_tool"

        valid_app_dir = self.drive / "05_Dev_Toolbox" / "valid_app"
        valid_app_dir.mkdir(parents=True, exist_ok=True)
        valid_app_exe = valid_app_dir / "valid.exe"
        valid_app_exe.write_bytes(b"MZ")

        # 2. Fake VS Code storage.json with moved, deleted, and valid project URIs
        code_gs = self.fake_appdata / "Code" / "User" / "globalStorage"
        code_gs.mkdir(parents=True, exist_ok=True)
        storage_file = code_gs / "storage.json"

        def _to_uri(p: Path) -> str:
            if sys.platform == "win32":
                return "file:///" + p.as_posix().replace(":", "%3A", 1)
            return "file://" + p.as_posix()

        storage_payload = {
            "backupWorkspaces": {
                "folders": [
                    {"folderUri": _to_uri(moved_proj_old)},
                    {"folderUri": _to_uri(moved_proj_old / "src")},
                    {"folderUri": _to_uri(deleted_proj_old)},
                    {"folderUri": _to_uri(valid_app_dir)},
                ],
                "workspaces": [],
            }
        }
        storage_file.write_text(json.dumps(storage_payload, indent=2), encoding="utf-8")

        # 3. Fake Desktop shortcuts (.lnk) when target paths are Windows-style
        desktop = self.fake_home / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        moved_lnk = desktop / "MovedTool.lnk"
        orphan_lnk = desktop / "F99.lnk"
        valid_lnk = desktop / "ValidApp.lnk"

        if sys.platform == "win32":
            moved_lnk_target = str(moved_tool_old / "app.exe")
            orphan_lnk_target = str(self.drive / "F99" / "F99.exe")
            valid_lnk_target = str(valid_app_exe)
            moved_lnk.write_bytes(_build_lnk_bytes(moved_lnk_target, use_unicode=True))
            orphan_lnk.write_bytes(_build_lnk_bytes(orphan_lnk_target, use_unicode=True))
            valid_lnk.write_bytes(_build_lnk_bytes(valid_lnk_target, use_unicode=True))

        # 4. Fake RegistryReader with PATH entries (moved, missing, valid)
        missing_cli = str(self.drive / "missing_cli" / "bin")
        moved_cli_old = str(self.drive / "moved_tool")
        reader = RegistryReader(
            tree={
                r"HKCU\Environment": {
                    "Path": ";".join([moved_cli_old, missing_cli, str(valid_app_dir)]),
                }
            }
        )

        report = inspect_broken_paths(
            root=str(self.drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
        )

        ide_by_old = {item["old"]: item for item in report["ide_and_agents"]}
        self.assertIn(str(moved_proj_old), ide_by_old)
        self.assertEqual(ide_by_old[str(moved_proj_old)]["kind"], "same-name")
        self.assertEqual(ide_by_old[str(moved_proj_old)]["new"], str(moved_proj_new))
        self.assertIn("VS Code", ide_by_old[str(moved_proj_old)]["agents"])

        self.assertIn(str(deleted_proj_old), ide_by_old)
        self.assertEqual(ide_by_old[str(deleted_proj_old)]["kind"], "not-found")
        self.assertIsNone(ide_by_old[str(deleted_proj_old)]["new"])

        path_by_target = {item["target"]: item for item in report["path_entries"]}
        self.assertIn(moved_cli_old, path_by_target)
        # Nothing lies below a PATH folder to check the same-named folder against: only the name matches
        self.assertEqual(path_by_target[moved_cli_old]["kind"], "name-only")
        self.assertEqual(path_by_target[moved_cli_old]["suggested_target"], str(moved_tool_new))
        self.assertIn(missing_cli, path_by_target)
        self.assertEqual(path_by_target[missing_cli]["kind"], "not-found")
        self.assertNotIn(str(valid_app_dir), path_by_target)

        if sys.platform == "win32":
            sc_by_path = {item["shortcut"]: item for item in report["shortcuts"]}
            self.assertIn(str(moved_lnk), sc_by_path)
            self.assertEqual(sc_by_path[str(moved_lnk)]["kind"], "same-name")
            self.assertEqual(
                sc_by_path[str(moved_lnk)]["suggested_target"],
                str(moved_tool_new / "app.exe"),
            )
            self.assertIn(str(orphan_lnk), sc_by_path)
            self.assertEqual(sc_by_path[str(orphan_lnk)]["kind"], "not-found")
            self.assertIn("Orphan shortcut", sc_by_path[str(orphan_lnk)]["suggestion"])
            self.assertNotIn(str(valid_lnk), sc_by_path)

    def test_02_cli_paths_broken_dry_run_and_fix_preserves_orphan_shortcuts(self) -> None:
        moved_proj_new = self.drive / "03_Development_Projects" / "Java_Learning"
        (moved_proj_new / "src").mkdir(parents=True, exist_ok=True)
        moved_proj_old = self.drive / "Java_Learning"
        stale_proj_old = self.drive / "Learning_Machine_AI"

        def _to_uri(p: Path) -> str:
            if sys.platform == "win32":
                return "file:///" + p.as_posix().replace(":", "%3A", 1)
            return "file://" + p.as_posix()

        code_gs = self.fake_appdata / "Code" / "User" / "globalStorage"
        code_gs.mkdir(parents=True, exist_ok=True)
        storage_file = code_gs / "storage.json"
        orig_storage = json.dumps(
            {
                "backupWorkspaces": {
                    "folders": [
                        {"folderUri": _to_uri(moved_proj_old / "src")},
                        {"folderUri": _to_uri(stale_proj_old)},
                    ],
                    "workspaces": [],
                }
            },
            indent=2,
        )
        storage_file.write_text(orig_storage, encoding="utf-8")

        desktop = self.fake_home / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        orphan_lnk = desktop / "GearUPBooster.lnk"
        if sys.platform == "win32":
            orphan_lnk.write_bytes(_build_lnk_bytes(str(self.drive / "GearUPBooster" / "GearUP.exe"), use_unicode=True))
        else:
            orphan_lnk.write_bytes(b"placeholder")

        # 1. Dry-run fix does not modify storage.json or delete orphan shortcut
        orig_collect = SystemRefIndex.collect
        out_dry = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch.object(
                SystemRefIndex,
                "collect",
                side_effect=lambda **kw: orig_collect(home=self.fake_home, reader=RegistryReader(tree={})),
            ),
        ):
            with contextlib.redirect_stdout(out_dry):
                rc_dry = cli_main(["paths", "--broken", "--fix", "--dry-run", "--root", str(self.drive), "--json"])

        self.assertEqual(rc_dry, 0)
        dry_payload = json.loads(out_dry.getvalue())
        self.assertGreaterEqual(dry_payload["total_broken"], 2)
        self.assertEqual(dry_payload["fixable_count"], 1)
        self.assertTrue(dry_payload["relink"]["dry_run"])
        self.assertEqual(storage_file.read_text(encoding="utf-8"), orig_storage)
        self.assertTrue(orphan_lnk.is_file(), "Orphan shortcut must never be deleted")

        # 2. Apply --fix updates sure matches and still preserves orphan shortcut
        out_fix = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch.object(
                SystemRefIndex,
                "collect",
                side_effect=lambda **kw: orig_collect(home=self.fake_home, reader=RegistryReader(tree={})),
            ),
        ):
            with contextlib.redirect_stdout(out_fix):
                rc_fix = cli_main(["paths", "--broken", "--fix", "--root", str(self.drive), "--json"])

        self.assertEqual(rc_fix, 0)
        fix_payload = json.loads(out_fix.getvalue())
        self.assertFalse(fix_payload["relink"]["dry_run"])
        self.assertEqual(fix_payload["relink"]["agents"].get("VS Code"), 1)
        updated_storage = storage_file.read_text(encoding="utf-8")
        self.assertIn(_to_uri(moved_proj_new), updated_storage)
        self.assertNotIn(_to_uri(moved_proj_old), updated_storage)
        self.assertIn(_to_uri(stale_proj_old), updated_storage)
        self.assertTrue(orphan_lnk.is_file(), "Orphan shortcut must never be deleted by --fix")

    def test_03_path_entry_is_never_matched_to_a_folder_inside_a_package(self) -> None:
        # ~/bin is gone, and npm's playwright-core ships a bin/ of its own: that one is not the missing folder
        (self.fake_home / "node_modules" / "playwright-core" / "bin").mkdir(parents=True)
        missing_bin = self.fake_home / "bin"
        reader = RegistryReader(tree={r"HKCU\Environment": {"Path": str(missing_bin)}})

        report = inspect_broken_paths(
            root=str(self.drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
        )

        path_by_target = {item["target"]: item for item in report["path_entries"]}
        self.assertIn(str(missing_bin), path_by_target)
        self.assertEqual(path_by_target[str(missing_bin)]["kind"], "not-found")
        self.assertIsNone(path_by_target[str(missing_bin)]["suggested_target"])
        self.assertEqual(path_by_target[str(missing_bin)]["candidates"], [])

    def test_04_path_entry_inside_a_dependency_tree_is_matched_inside_dependency_trees(self) -> None:
        # web/node_modules/tool is gone; the package now sits one node_modules deeper, under host
        node_modules = self.drive / "web" / "node_modules"
        moved_bin = node_modules / "host" / "node_modules" / "tool" / "bin"
        moved_bin.mkdir(parents=True)
        missing_bin = node_modules / "tool" / "bin"
        reader = RegistryReader(tree={r"HKCU\Environment": {"Path": str(missing_bin)}})

        report = inspect_broken_paths(
            root=str(self.drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
        )

        path_by_target = {item["target"]: item for item in report["path_entries"]}
        self.assertIn(str(missing_bin), path_by_target)
        self.assertEqual(path_by_target[str(missing_bin)]["kind"], "same-name")
        self.assertEqual(path_by_target[str(missing_bin)]["suggested_target"], str(moved_bin))

    def test_05_path_entry_matched_by_its_name_alone_is_only_a_guess(self) -> None:
        # Nothing lies below a PATH folder to check a same-named folder against: it may be another program's
        missing = self.drive / "cli_tool"
        found = self.drive / "04_Apps" / "cli_tool"
        found.mkdir(parents=True)
        reader = RegistryReader(tree={r"HKCU\Environment": {"Path": str(missing)}})
        suggestion = (
            f"Only the folder name matches; if you moved it there, update the PATH (User) entry to {found}, "
            "otherwise remove it"
        )

        report = inspect_broken_paths(
            root=str(self.drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
        )

        entry = {item["target"]: item for item in report["path_entries"]}[str(missing)]
        self.assertEqual(entry["kind"], "name-only")
        self.assertEqual(entry["suggested_folder"], str(found))
        self.assertEqual(entry["suggested_target"], str(found))
        self.assertEqual(entry["candidates"], [])
        self.assertEqual(entry["suggestion"], suggestion)
        # A guess is listed, never repaired
        self.assertEqual(report["fixable_count"], 0)
        self.assertEqual(report["sure_moves"], [])

        rc, out = self._paths_cli(reader, "--json")
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertEqual([(i["target"], i["kind"]) for i in payload["path_entries"]], [(str(missing), "name-only")])
        self.assertEqual(payload["fixable_count"], 0)

        rc, text = self._paths_cli(reader)
        self.assertEqual(rc, 0, text)
        self.assertIn(f"  ? [PATH (User)] {missing} -> maybe moved to {found} ({suggestion})", text)
        self.assertNotIn("-> moved to", text)
        self.assertNotIn("paths --broken --fix", text)

    def test_06_several_same_named_folders_only_one_that_holds_what_lay_below_is_sure(self) -> None:
        # The PATH entry lies inside the missing folder: a same-named folder is sure only if it holds that too
        apps, toolbox = self.drive / "04_Apps", self.drive / "05_Dev_Toolbox"
        (apps / "tool3" / "bin").mkdir(parents=True)
        (toolbox / "tool3").mkdir()
        (apps / "tool4").mkdir()
        (toolbox / "tool4").mkdir()
        sure_bin, ambiguous_bin = self.drive / "tool3" / "bin", self.drive / "tool4" / "bin"
        reader = RegistryReader(tree={r"HKCU\Environment": {"Path": ";".join([str(sure_bin), str(ambiguous_bin)])}})

        report = inspect_broken_paths(
            root=str(self.drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
        )

        path_by_target = {item["target"]: item for item in report["path_entries"]}
        sure = path_by_target[str(sure_bin)]
        self.assertEqual(sure["kind"], "same-name")
        self.assertEqual(sure["suggested_target"], str(apps / "tool3" / "bin"))
        ambiguous = path_by_target[str(ambiguous_bin)]
        self.assertEqual(ambiguous["kind"], "ambiguous")
        self.assertEqual(ambiguous["candidates"], [str(apps / "tool4"), str(toolbox / "tool4")])
        self.assertIsNone(ambiguous["suggested_target"])

    @unittest.skipUnless(sys.platform == "win32", "Windows shortcuts (.lnk) are read on Windows only")
    def test_07_shortcut_matched_by_its_name_alone_is_never_a_sure_move(self) -> None:
        # A shortcut to a folder that is gone: the only folder of that name may be another program's
        guessed_old, guessed_new = self.drive / "tool", self.drive / "04_Apps" / "tool"
        guessed_new.mkdir(parents=True)
        # Control: a shortcut to a file that the same-named folder holds too is sure
        sure_old, sure_new = self.drive / "tool2", self.drive / "04_Apps" / "tool2"
        sure_new.mkdir()
        (sure_new / "app.exe").write_bytes(b"MZ")
        desktop = self.fake_home / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        guessed_lnk, sure_lnk = desktop / "Tool.lnk", desktop / "Tool2.lnk"
        guessed_lnk.write_bytes(_build_lnk_bytes(str(guessed_old), use_unicode=True))
        sure_lnk.write_bytes(_build_lnk_bytes(str(sure_old / "app.exe"), use_unicode=True))

        report = inspect_broken_paths(
            root=str(self.drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=RegistryReader(tree={}),
        )

        sc_by_path = {item["shortcut"]: item for item in report["shortcuts"]}
        guessed = sc_by_path[str(guessed_lnk)]
        self.assertEqual(guessed["kind"], "name-only")
        self.assertEqual(guessed["suggested_folder"], str(guessed_new))
        self.assertIn(f'smart-drive relink "{guessed_old}" "{guessed_new}"', guessed["suggestion"])
        self.assertNotIn("--fix", guessed["suggestion"])
        self.assertEqual(sc_by_path[str(sure_lnk)]["kind"], "same-name")
        self.assertIn([str(sure_old), str(sure_new)], report["sure_moves"])
        self.assertNotIn([str(guessed_old), str(guessed_new)], report["sure_moves"])
        self.assertEqual(report["fixable_count"], 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows shortcuts (.lnk) are read on Windows only")
    def test_08_fix_never_repoints_a_shortcut_matched_by_its_name_alone(self) -> None:
        old, new = self.drive / "tool", self.drive / "04_Apps" / "tool"
        new.mkdir(parents=True)
        desktop = self.fake_home / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        lnk = desktop / "Tool.lnk"
        lnk.write_bytes(_build_lnk_bytes(str(old), use_unicode=True))
        original = lnk.read_bytes()
        reader = RegistryReader(tree={})

        rc, text = self._paths_cli(reader)
        self.assertEqual(rc, 0, text)
        self.assertIn(f"  ? {lnk}: {old} -> maybe moved to {new}  (", text)
        self.assertNotIn("paths --broken --fix", text)

        rc, out = self._paths_cli(reader, "--fix", "--json")
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["fixable_count"], 0)
        self.assertEqual(payload["relink"]["updated"], [])
        self.assertEqual(lnk.read_bytes(), original)
        self.assertIsNone(last_moves(str(self.drive)))

    def test_09_agent_entry_matched_by_its_name_alone_is_listed_as_maybe_moved(self) -> None:
        # Nothing is remembered inside drive/demo, so the demo folder found elsewhere may be another project
        old, new = self.drive / "demo", self.drive / "02_Code" / "client" / "demo"
        new.mkdir(parents=True)
        (self.fake_home / ".claude.json").write_text(json.dumps({"projects": {str(old): {}}}), encoding="utf-8")

        rc, text = self._paths_cli(RegistryReader(tree={}))

        self.assertEqual(rc, 0, text)
        self.assertIn(
            f"  ? {old} -> maybe moved to {new}  (Claude Code; only the name matches; if you moved it there, run: "
            f'smart-drive relink "{old}" "{new}")',
            text,
        )
        self.assertNotIn(f"{old}: not found", text)
        self.assertNotIn("paths --broken --fix", text)


if __name__ == "__main__":
    unittest.main()
