"""tests/test_relink_ide.py - Unit and E2E tests for VS Code-family and JetBrains IDE relinking (T8).

100% isolated: every test uses a fake home and fake AppData inside a temporary directory and never
reads or writes real IDE or coding agent configuration files on the host machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from smart_drive.core.relink import (
    BACKUP_SUFFIX,
    PathMapper,
    find_moved,
    relink,
    running_agents,
)
from tests.helpers import SmartDriveTestCase
from tests.test_system_refs import _build_lnk_bytes


class TestRelinkIDE(SmartDriveTestCase):
    """Tests for PathMapper.map_uri, VS Code-family storage.json/state.vscdb, and JetBrains XML relinking."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
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

    def test_01_map_uri_preserves_colon_encoding_drive_case_and_ignores_other_schemes(self) -> None:
        mapper = PathMapper([
            ("E:\\project", "E:\\03_Development_Projects\\project"),
            ("/Users/alice/proj", "/Users/alice/03_Development_Projects/proj"),
        ])

        # POSIX URI
        self.assertEqual(
            mapper.map_uri("file:///Users/alice/proj/sub"),
            "file:///Users/alice/03_Development_Projects/proj/sub",
        )
        self.assertEqual(
            mapper.map_uri("file:///Users/alice/proj/my%20app/index.ts"),
            "file:///Users/alice/03_Development_Projects/proj/my%20app/index.ts",
        )

        if sys.platform in ("win32", "darwin"):
            # Lowercase drive + %3A
            self.assertEqual(
                mapper.map_uri("file:///e%3A/project/x"),
                "file:///e%3A/03_Development_Projects/project/x",
            )
            # Uppercase drive + %3A
            self.assertEqual(
                mapper.map_uri("file:///E%3A/project/x"),
                "file:///E%3A/03_Development_Projects/project/x",
            )
            # Lowercase drive + %3a
            self.assertEqual(
                mapper.map_uri("file:///e%3a/project/x"),
                "file:///e%3a/03_Development_Projects/project/x",
            )
            # Literal colon + uppercase/lowercase drive
            self.assertEqual(
                mapper.map_uri("file:///E:/project/x"),
                "file:///E:/03_Development_Projects/project/x",
            )
            self.assertEqual(
                mapper.map_uri("file:///e:/project/x"),
                "file:///e:/03_Development_Projects/project/x",
            )
            # Percent-encoded space in subpath
            self.assertEqual(
                mapper.map_uri("file:///e%3A/project/Denso%20Hackathon/app"),
                "file:///e%3A/03_Development_Projects/project/Denso%20Hackathon/app",
            )

        # Other URI schemes and unmoved paths are untouched
        for untouched in (
            "vscode-remote://ssh-remote+box/e/project/x",
            "vscode-vfs://github/owner/project",
            "https://example.com/e/project/x",
            "http://localhost:3000/project",
            "file:///e%3A/unrelated/x",
            "E:\\project\\x",
        ):
            self.assertIsNone(mapper.map_uri(untouched), f"Should not map_uri({untouched!r})")

    def test_02_vscode_storage_json_vscdb_and_jetbrains_xml_dry_run_and_apply(self) -> None:
        # Prepare moved folders
        if sys.platform in ("win32", "darwin"):
            old_proj = "E:\\project"
            new_proj = "E:\\03_Development_Projects\\project"
            old_uri_pct = "file:///e%3A/project/openclaw"
            new_uri_pct = "file:///e%3A/03_Development_Projects/project/openclaw"
            old_uri_colon = "file:///E:/project/demo/main.py"
            new_uri_colon = "file:///E:/03_Development_Projects/project/demo/main.py"
            old_ws_uri = "file:///e%3A/project/multi.code-workspace"
            new_ws_uri = "file:///e%3A/03_Development_Projects/project/multi.code-workspace"
            untouched_uri = "file:///e%3A/other_folder/keep"
            jb_old_slash = "E:/project/Project_NWC"
            jb_new_slash = "E:/03_Development_Projects/project/Project_NWC"
            jb_old_bslash = "E:\\project\\Project_NWC"
            jb_new_bslash = "E:\\03_Development_Projects\\project\\Project_NWC"
            jb_untouched = "E:/other_folder/keep"
        else:
            old_proj = "/mnt/ssd/project"
            new_proj = "/mnt/ssd/03_Development_Projects/project"
            old_uri_pct = "file:///mnt/ssd/project/openclaw"
            new_uri_pct = "file:///mnt/ssd/03_Development_Projects/project/openclaw"
            old_uri_colon = "file:///mnt/ssd/project/demo/main.py"
            new_uri_colon = "file:///mnt/ssd/03_Development_Projects/project/demo/main.py"
            old_ws_uri = "file:///mnt/ssd/project/multi.code-workspace"
            new_ws_uri = "file:///mnt/ssd/03_Development_Projects/project/multi.code-workspace"
            untouched_uri = "file:///mnt/ssd/other_folder/keep"
            jb_old_slash = "/mnt/ssd/project/Project_NWC"
            jb_new_slash = "/mnt/ssd/03_Development_Projects/project/Project_NWC"
            jb_old_bslash = "/mnt/ssd/project/Project_NWC"
            jb_new_bslash = "/mnt/ssd/03_Development_Projects/project/Project_NWC"
            jb_untouched = "/mnt/ssd/other_folder/keep"

        home_old = str(self.fake_home / "IdeaProjects" / "Demo")
        home_new = str(self.fake_home / "03_Development_Projects" / "IdeaProjects" / "Demo")

        # 1) VS Code storage.json
        code_gs = self.fake_appdata / "Code" / "User" / "globalStorage"
        code_gs.mkdir(parents=True, exist_ok=True)
        storage_file = code_gs / "storage.json"
        storage_payload = {
            "profileAssociations": {
                "workspaces": {
                    old_uri_pct: "__default__profile__",
                    untouched_uri: "__default__profile__",
                    "vscode-remote://wsl+Ubuntu/home/user/project": "__default__profile__",
                }
            },
            "backupWorkspaces": {
                "folders": [{"folderUri": old_uri_pct}, {"folderUri": untouched_uri}],
                "workspaces": [],
            },
        }
        storage_raw = json.dumps(storage_payload, indent=2) + "\n"
        storage_file.write_text(storage_raw, encoding="utf-8")

        # 2) VS Code state.vscdb (SQLite)
        vscdb_file = code_gs / "state.vscdb"
        vscdb_entries = {
            "entries": [
                {"folderUri": old_uri_pct},
                {"fileUri": old_uri_colon},
                {"workspace": {"id": "w1", "configPath": old_ws_uri}},
                {"folderUri": untouched_uri},
                {"folderUri": "vscode-remote://ssh-remote+mybox/project"},
            ]
        }
        conn = sqlite3.connect(str(vscdb_file))
        with conn:
            conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
            conn.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                ("history.recentlyOpenedPathsList", json.dumps(vscdb_entries)),
            )
            conn.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                ("unrelated.key", '{"keep": true}'),
            )
        conn.close()

        # 3) JetBrains recentProjects.xml and trusted-paths.xml
        jb_opts = self.fake_appdata / "JetBrains" / "IntelliJIdea2025.3" / "options"
        jb_opts.mkdir(parents=True, exist_ok=True)
        recent_xml = jb_opts / "recentProjects.xml"
        recent_xml_content = (
            "<application>\n"
            '  <component name="RecentProjectsManager">\n'
            '    <option name="additionalInfo">\n'
            "      <map>\n"
            f'        <entry key="{jb_old_slash}">\n'
            "          <value>\n"
            '            <RecentProjectMetaInfo frameTitle="Project_NWC" projectWorkspaceId="id1">\n'
            '              <option name="binFolder" value="$APPLICATION_HOME_DIR$/bin" />\n'
            "            </RecentProjectMetaInfo>\n"
            "          </value>\n"
            "        </entry>\n"
            '        <entry key="$USER_HOME$/IdeaProjects/Demo/sub">\n'
            "          <value />\n"
            "        </entry>\n"
            f'        <entry key="{jb_untouched}">\n'
            "          <value />\n"
            "        </entry>\n"
            "      </map>\n"
            "    </option>\n"
            "  </component>\n"
            "</application>\n"
        )
        recent_xml.write_text(recent_xml_content, encoding="utf-8")

        trusted_xml = jb_opts / "trusted-paths.xml"
        trusted_xml_content = (
            "<application>\n"
            '  <component name="Trusted.Paths">\n'
            '    <option name="TRUSTED_PROJECT_PATHS">\n'
            "      <map>\n"
            f'        <entry key="{jb_old_bslash}" value="true" />\n'
            f'        <entry key="{jb_untouched}" value="true" />\n'
            "      </map>\n"
            "    </option>\n"
            "  </component>\n"
            "</application>\n"
        )
        trusted_xml.write_text(trusted_xml_content, encoding="utf-8")

        moves = [
            (old_proj, new_proj),
            (home_old, home_new),
        ]

        # Dry-run first: nothing written, no backups created
        dry = relink(moves, dry_run=True, home=self.fake_home, app_data=self.fake_appdata)
        self.assertTrue(dry.dry_run)
        self.assertTrue(dry.changed)
        self.assertEqual(dry.agents, {"JetBrains": 3, "VS Code": 5})
        self.assertEqual(storage_file.read_text(encoding="utf-8"), storage_raw)
        self.assertEqual(recent_xml.read_text(encoding="utf-8"), recent_xml_content)
        self.assertFalse(Path(str(storage_file) + BACKUP_SUFFIX).exists())
        self.assertFalse(Path(str(vscdb_file) + BACKUP_SUFFIX).exists())
        self.assertFalse(Path(str(recent_xml) + BACKUP_SUFFIX).exists())

        # Apply relink
        real = relink(moves, dry_run=False, home=self.fake_home, app_data=self.fake_appdata)
        self.assertFalse(real.dry_run)
        self.assertEqual(real.agents, {"JetBrains": 3, "VS Code": 5})
        self.assertEqual(real.errors, [])

        # Verify backups exist for all 4 files
        for f in (storage_file, vscdb_file, recent_xml, trusted_xml):
            self.assertTrue(Path(str(f) + BACKUP_SUFFIX).is_file(), f"Missing backup for {f}")

        # Verify storage.json
        storage_after = json.loads(storage_file.read_text(encoding="utf-8"))
        ws_assoc = storage_after["profileAssociations"]["workspaces"]
        self.assertIn(new_uri_pct, ws_assoc)
        self.assertNotIn(old_uri_pct, ws_assoc)
        self.assertIn(untouched_uri, ws_assoc)
        self.assertIn("vscode-remote://wsl+Ubuntu/home/user/project", ws_assoc)
        self.assertEqual(
            storage_after["backupWorkspaces"]["folders"],
            [{"folderUri": new_uri_pct}, {"folderUri": untouched_uri}],
        )

        # Verify state.vscdb
        conn = sqlite3.connect(str(vscdb_file))
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            ("history.recentlyOpenedPathsList",),
        ).fetchone()
        unrelated_row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            ("unrelated.key",),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        vscdb_after = json.loads(row[0])
        self.assertEqual(
            vscdb_after["entries"],
            [
                {"folderUri": new_uri_pct},
                {"fileUri": new_uri_colon},
                {"workspace": {"id": "w1", "configPath": new_ws_uri}},
                {"folderUri": untouched_uri},
                {"folderUri": "vscode-remote://ssh-remote+mybox/project"},
            ],
        )
        self.assertEqual(unrelated_row[0], '{"keep": true}')

        # Verify JetBrains recentProjects.xml and trusted-paths.xml
        recent_after = recent_xml.read_text(encoding="utf-8")
        self.assertIn(f'<entry key="{jb_new_slash}">', recent_after)
        self.assertNotIn(f'<entry key="{jb_old_slash}">', recent_after)
        self.assertIn('<entry key="$USER_HOME$/03_Development_Projects/IdeaProjects/Demo/sub">', recent_after)
        self.assertIn(f'<entry key="{jb_untouched}">', recent_after)
        self.assertIn('value="$APPLICATION_HOME_DIR$/bin"', recent_after)

        trusted_after = trusted_xml.read_text(encoding="utf-8")
        self.assertIn(f'<entry key="{jb_new_bslash}" value="true" />', trusted_after)
        self.assertIn(f'<entry key="{jb_untouched}" value="true" />', trusted_after)

    def test_03_locked_vscdb_emits_warning_not_error(self) -> None:
        cursor_gs = self.fake_appdata / "Cursor" / "User" / "globalStorage"
        cursor_gs.mkdir(parents=True, exist_ok=True)
        vscdb_file = cursor_gs / "state.vscdb"
        vscdb_file.write_bytes(b"locked")

        with patch("smart_drive.core.relink.sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")) as mock_conn:
            report = relink(
                [("D:\\proj", "D:\\03_Development_Projects\\proj")],
                dry_run=True,
                home=self.fake_home,
                app_data=self.fake_appdata,
            )

        self.assertEqual(report.errors, [])
        self.assertEqual(len(report.warnings), 1)
        self.assertIn("database is locked", report.warnings[0])
        mock_conn.assert_called_once()
        db_arg = mock_conn.call_args[0][0]
        self.assertTrue(db_arg.endswith("?mode=ro"), db_arg)
        self.assertTrue(mock_conn.call_args[1].get("uri"), mock_conn.call_args)

    def test_04_running_agents_detects_vscode_and_jetbrains_processes(self) -> None:
        with patch(
            "smart_drive.core.relink._running_processes",
            return_value={"Code.exe", "pycharm64.exe", "idea64.exe"},
        ):
            active = running_agents()
        self.assertIn("VS Code", active)
        self.assertIn("JetBrains", active)

    def test_05_find_moved_discovers_vscode_and_jetbrains_stale_paths(self) -> None:
        drive = self.test_dir / "drive"
        new_loc = drive / "03_Development_Projects" / "moved_ide_proj"
        new_loc.mkdir(parents=True)
        old_loc = drive / "moved_ide_proj"

        if sys.platform == "win32":
            old_uri = "file:///" + old_loc.as_posix().replace(":", "%3A", 1)
        else:
            old_uri = "file://" + old_loc.as_posix()

        ag_gs = self.fake_appdata / "Antigravity" / "User" / "globalStorage"
        ag_gs.mkdir(parents=True, exist_ok=True)
        vscdb_file = ag_gs / "state.vscdb"
        conn = sqlite3.connect(str(vscdb_file))
        with conn:
            conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
            conn.execute(
                "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
                ("history.recentlyOpenedPathsList", json.dumps({"entries": [{"folderUri": old_uri}]})),
            )
        conn.close()

        jb_opts = self.fake_appdata / "JetBrains" / "PyCharm2026.1" / "options"
        jb_opts.mkdir(parents=True, exist_ok=True)
        (jb_opts / "recentProjects.xml").write_text(
            f'<application><entry key="{old_loc.as_posix()}" /></application>\n',
            encoding="utf-8",
        )

        real_connect = sqlite3.connect
        connect_calls = []

        def spy_connect(database, *args, **kwargs):
            connect_calls.append((database, kwargs))
            return real_connect(database, *args, **kwargs)

        with patch("smart_drive.core.relink.sqlite3.connect", side_effect=spy_connect):
            entries = find_moved(root=str(drive), home=self.fake_home, app_data=self.fake_appdata)
            dry_report = relink(
                [(str(old_loc), str(new_loc))],
                dry_run=True,
                home=self.fake_home,
                app_data=self.fake_appdata,
            )

        self.assertEqual(len(connect_calls), 2)
        for db_uri, kw in connect_calls:
            self.assertTrue(str(db_uri).endswith("?mode=ro"), db_uri)
            self.assertTrue(kw.get("uri"), kw)
        self.assertEqual(dry_report.agents.get("Antigravity"), 1)
        by_old = {e.old: e for e in entries}
        self.assertIn(str(old_loc), by_old)
        found = by_old[str(old_loc)]
        self.assertEqual(found.kind, "same-name")
        self.assertEqual(found.new, str(new_loc))
        self.assertEqual(found.agents, ["Antigravity", "JetBrains"])

    def test_06_obsidian_vault_relinked_and_process_detected(self) -> None:
        obs_dir = self.fake_appdata / "obsidian"
        obs_dir.mkdir(parents=True, exist_ok=True)
        obs_file = obs_dir / "obsidian.json"

        old_vault = "E:\\Obsidian" if sys.platform == "win32" else "/mnt/ssd/Obsidian"
        new_vault = (
            "E:\\02_Learning_Materials\\Obsidian"
            if sys.platform == "win32"
            else "/mnt/ssd/02_Learning_Materials/Obsidian"
        )
        keep_vault = "E:\\KeepNotes" if sys.platform == "win32" else "/mnt/ssd/KeepNotes"

        payload = {
            "vaults": {
                "a1b2c3d4": {"path": old_vault, "ts": 1700000000, "open": True},
                "e5f6g7h8": {"path": keep_vault, "ts": 1700000001},
            }
        }
        obs_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        dry = relink([(old_vault, new_vault)], dry_run=True, home=self.fake_home, app_data=self.fake_appdata)
        self.assertEqual(dry.agents, {"Obsidian": 1})
        self.assertFalse(Path(str(obs_file) + BACKUP_SUFFIX).exists())

        real = relink([(old_vault, new_vault)], dry_run=False, home=self.fake_home, app_data=self.fake_appdata)
        self.assertEqual(real.agents, {"Obsidian": 1})
        self.assertTrue(Path(str(obs_file) + BACKUP_SUFFIX).is_file())

        after = json.loads(obs_file.read_text(encoding="utf-8"))
        self.assertEqual(after["vaults"]["a1b2c3d4"]["path"], new_vault)
        self.assertEqual(after["vaults"]["e5f6g7h8"]["path"], keep_vault)

        with patch("smart_drive.core.relink._running_processes", return_value={"Obsidian.exe"}):
            self.assertIn("Obsidian", running_agents())

    def test_07_windows_shortcuts_repointed_mocked_subprocess(self) -> None:
        import smart_drive.core.relink as relink_mod

        self.assertFalse(hasattr(relink_mod, "_ps_quote"), "_ps_quote must not exist; paths must not be interpolated")

        desktop = self.fake_home / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        moved_lnk = desktop / "MyTool.lnk"
        tricky_lnk = desktop / "Tricky ’ ‘ ‚ ' $( ` ;.lnk"
        viet_lnk = desktop / "VietApp.lnk"
        keep_lnk = desktop / "KeepApp.lnk"

        old_tricky_dir = "E:\\tool ’ ‘ ‚ ' $( ` ;"
        new_tricky_dir = "E:\\05_Dev_Toolbox\\tool ’ ‘ ‚ ' $( ` ;"
        old_viet_dir = "E:\\Dự án ‛"
        new_viet_dir = "E:\\03_Development_Projects\\Dự án ‛"

        moved_lnk.write_bytes(_build_lnk_bytes(r"E:\tool\bin\run.exe", use_unicode=True))
        tricky_lnk.write_bytes(_build_lnk_bytes(old_tricky_dir + r"\bin\run.exe", use_unicode=True))
        viet_lnk_orig_bytes = _build_lnk_bytes(old_viet_dir + r"\app.exe", use_unicode=True)
        viet_lnk.write_bytes(viet_lnk_orig_bytes)
        keep_lnk.write_bytes(_build_lnk_bytes(r"E:\keep\app.exe", use_unicode=True))

        moves = [
            (r"E:\tool", r"E:\05_Dev_Toolbox\tool"),
            (old_tricky_dir, new_tricky_dir),
            (old_viet_dir, new_viet_dir),
        ]

        # 1) Dry-run lists the shortcuts to be modified without calling subprocess.run or creating backup
        with (
            patch.object(sys, "platform", "win32"),
            patch("smart_drive.core.relink.subprocess.run") as mock_run,
        ):
            dry = relink(moves, dry_run=True, home=self.fake_home, app_data=self.fake_appdata)

        mock_run.assert_not_called()
        self.assertEqual(dry.agents, {"Windows Shortcuts": 3})
        self.assertFalse(Path(str(moved_lnk) + BACKUP_SUFFIX).exists())

        # 2) Real run with mocked PowerShell COM command:
        #    - For moved_lnk and tricky_lnk, simulated Save() writes the exact new TargetPath.
        #    - For viet_lnk ("Dự án ‛"), simulated WScript.Shell mangles Unicode characters to '?' / '''.
        def fake_ps_run(cmd, *args, **kwargs):
            ps_script = cmd[-1]
            self.assertIn("$ErrorActionPreference = 'Stop'", ps_script)
            self.assertIn("$env:SD_LNK", ps_script)
            self.assertIn("$env:SD_TARGET", ps_script)
            env = kwargs.get("env") or {}
            lnk_file = env["SD_LNK"]
            target_val = env["SD_TARGET"]
            # No path may ever be embedded inside the PowerShell command text
            self.assertNotIn(lnk_file, ps_script)
            self.assertNotIn(target_val, ps_script)
            if "Dự án" in target_val or "\u201b" in target_val:
                # Simulate WScript.Shell silently corrupting non-ANSI characters on Save()
                mangled = target_val.replace("ự", "?").replace("\u201b", "'")
                Path(lnk_file).write_bytes(_build_lnk_bytes(mangled, use_unicode=True))
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            Path(lnk_file).write_bytes(_build_lnk_bytes(target_val, use_unicode=True))
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch.object(sys, "platform", "win32"),
            patch("smart_drive.core.relink.shutil.which", return_value="powershell.exe"),
            patch("smart_drive.core.relink.subprocess.run", side_effect=fake_ps_run) as mock_run,
        ):
            real = relink(moves, dry_run=False, home=self.fake_home, app_data=self.fake_appdata)

        self.assertEqual(mock_run.call_count, 3)
        # Only the 2 shortcuts whose TargetPath verified cleanly after Save() are marked updated
        updated_files = {entry["file"] for entry in real.updated if entry["agent"] == "Windows Shortcuts"}
        self.assertEqual(updated_files, {str(moved_lnk), str(tricky_lnk)})
        self.assertNotIn(str(viet_lnk), updated_files)
        # The shortcut whose characters could not be preserved emits a warning with "cần sửa tay" and restores original bytes
        self.assertTrue(any("cần sửa tay" in w and str(viet_lnk) in w for w in real.warnings), real.warnings)
        self.assertEqual(viet_lnk.read_bytes(), viet_lnk_orig_bytes)

        # Also verify that if a path string contains '"' (e.g. on a non-Windows mock or injected string), it is passed via env
        mapper_quote = PathMapper([('E:\\dir"quote', 'E:\\05_Dev_Toolbox\\dir"quote')])
        self.assertIsNone(mapper_quote.map('E:\\dir"quote\\app.exe'))

        # 3) Missing PowerShell emits warning, not error
        with (
            patch.object(sys, "platform", "win32"),
            patch("smart_drive.core.relink.shutil.which", return_value=None),
        ):
            no_ps = relink(moves, dry_run=False, home=self.fake_home, app_data=self.fake_appdata)
        self.assertEqual(no_ps.errors, [])
        self.assertTrue(any("PowerShell" in w for w in no_ps.warnings))

    @unittest.skipUnless(
        sys.platform == "win32" and os.environ.get("SMART_DRIVE_TEST_LNK") == "1",
        "Real PowerShell COM .lnk test runs only on win32 with SMART_DRIVE_TEST_LNK=1",
    )
    def test_08_windows_shortcuts_real_powershell_com(self) -> None:
        # 1) Folder with curly quotes and shell metacharacters that CP1252/WScript.Shell can represent
        old_dir = self.test_dir / "tool ’ ‘ ‚ ' $( ` ;"
        new_dir = self.test_dir / "05_Dev_Toolbox" / "tool ’ ‘ ‚ ' $( ` ;"
        old_dir.mkdir(parents=True, exist_ok=True)
        new_dir.mkdir(parents=True, exist_ok=True)
        old_exe = old_dir / "app.exe"
        new_exe = new_dir / "app.exe"
        old_exe.write_bytes(b"MZ")
        new_exe.write_bytes(b"MZ")

        # 2) Folder with Vietnamese characters ("Dự án ‛") that WScript.Shell cannot preserve
        old_vn = self.test_dir / "old_vn_app"
        new_vn = self.test_dir / "03_Development_Projects" / "Dự án ‛"
        old_vn.mkdir(parents=True, exist_ok=True)
        new_vn.mkdir(parents=True, exist_ok=True)
        old_vn_exe = old_vn / "app.exe"
        new_vn_exe = new_vn / "app.exe"
        old_vn_exe.write_bytes(b"MZ")
        new_vn_exe.write_bytes(b"MZ")

        desktop = self.fake_home / "Desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        lnk_path = desktop / "RealApp.lnk"
        vn_lnk_path = desktop / "VnApp.lnk"

        create_script = (
            "$ErrorActionPreference = 'Stop'; "
            "$ws = New-Object -ComObject WScript.Shell; "
            "$sc = $ws.CreateShortcut($env:SD_LNK); "
            "$sc.TargetPath = $env:SD_TARGET; "
            "$sc.WorkingDirectory = $env:SD_WD; "
            "$sc.IconLocation = \"$($env:SD_TARGET),0\"; "
            "$sc.Save()"
        )
        for p_lnk, p_exe, p_wd in ((lnk_path, old_exe, old_dir), (vn_lnk_path, old_vn_exe, old_vn)):
            env = dict(os.environ, SD_LNK=str(p_lnk), SD_TARGET=str(p_exe), SD_WD=str(p_wd))
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", create_script],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        report = relink(
            [(str(old_dir), str(new_dir)), (str(old_vn), str(new_vn))],
            dry_run=False,
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        # Only lnk_path succeeds; vn_lnk_path emits a "cần sửa tay" warning and is not reported as updated
        self.assertEqual(report.agents.get("Windows Shortcuts"), 1)
        self.assertEqual([u["file"] for u in report.updated], [str(lnk_path)])
        self.assertTrue(any("cần sửa tay" in w and str(vn_lnk_path) in w for w in report.warnings), report.warnings)

        read_script = (
            "$ErrorActionPreference = 'Stop'; "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$ws = New-Object -ComObject WScript.Shell; "
            "$sc = $ws.CreateShortcut($env:SD_LNK); "
            "Write-Output $sc.TargetPath; "
            "Write-Output $sc.WorkingDirectory; "
            "Write-Output $sc.IconLocation"
        )
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", read_script],
            env=dict(os.environ, SD_LNK=str(lnk_path)),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
        self.assertEqual(os.path.normcase(lines[0]), os.path.normcase(str(new_exe)))
        self.assertEqual(os.path.normcase(lines[1]), os.path.normcase(str(new_dir)))
        self.assertEqual(os.path.normcase(lines[2]), os.path.normcase(f"{new_exe},0"))


if __name__ == "__main__":
    unittest.main()
