"""tests/test_relink.py - Unit and E2E tests for smart_drive.core.relink and smart_drive.cli.cmd_relink.

100% isolated: every test uses a fake home and fake AppData inside a temporary directory and never
reads or writes real coding agent configuration files on the host machine.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.relink import (
    BACKUP_SUFFIX,
    PathMapper,
    agent_files,
    claude_slug,
    find_moved,
    journal_path,
    last_moves,
    record_moves,
    relink,
    running_agents,
)
from tests.helpers import SmartDriveTestCase, run_smart_drive_cli
import smart_drive.core.relink as relink_core

# setUp replaces the process listing; tests that need the real parser restore this one
_REAL_RUNNING_PROCESSES = relink_core._running_processes

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]


class TestRelink(SmartDriveTestCase):
    """Comprehensive tests for PathMapper, agent config relinking, history, git worktrees, venvs, and CLI."""

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
        # The suite itself may run inside a coding agent, which --wait would then skip
        self._caller_patcher = patch("smart_drive.cli.cmd_relink.calling_agents", return_value=set())
        self._caller_patcher.start()

    def tearDown(self) -> None:
        if hasattr(self, "_caller_patcher"):
            self._caller_patcher.stop()
        if hasattr(self, "_temp_patcher"):
            self._temp_patcher.stop()
        if hasattr(self, "_proc_patcher"):
            self._proc_patcher.stop()
        super().tearDown()

    def _cli_env(self) -> dict[str, str]:
        return {
            "USERPROFILE": str(self.fake_home),
            "HOME": str(self.fake_home),
            "APPDATA": str(self.fake_appdata),
            "LOCALAPPDATA": str(self.fake_home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.fake_appdata),
            "TMP": str(self.fake_temp),
            "TEMP": str(self.fake_temp),
            "TMPDIR": str(self.fake_temp),
            "CONSTELLATION_HOME": "",
            "ANTIGRAVITY_HOME": "",
            "GEMINI_HOME": "",
        }

    # 1. PathMapper
    def test_01_path_mapper_table_and_nested_and_pathsep(self) -> None:
        mapper = PathMapper([("D:\\proj", "D:\\03_Development_Projects\\proj")])
        self.assertTrue(bool(mapper))
        self.assertFalse(bool(PathMapper([])))
        self.assertFalse(bool(PathMapper([("D:\\", "D:\\x"), ("relative", "D:\\x")])))

        self.assertEqual(mapper.map("D:\\proj"), "D:\\03_Development_Projects\\proj")
        self.assertEqual(mapper.map("D:/proj/dist/x.mjs"), "D:/03_Development_Projects/proj/dist/x.mjs")
        self.assertEqual(mapper.map("D:\\proj\\"), "D:\\03_Development_Projects\\proj\\")

        for untouched in (
            "D:\\proj-old",
            "D:\\other\\proj",
            "--root=D:\\proj",
            "file:///d%3A/proj",
            "\\\\server\\proj",
            "//server/proj",
            "proj/sub",
        ):
            self.assertIsNone(mapper.map(untouched), f"Should not map {untouched!r}")

        # Nested moves: deepest moved folder wins.
        nested = PathMapper([
            ("D:\\proj", "D:\\03_Development_Projects\\proj"),
            ("D:\\proj\\sub", "D:\\05_Dev_Toolbox\\sub_tool"),
        ])
        self.assertEqual(nested.map("D:\\proj\\main.py"), "D:\\03_Development_Projects\\proj\\main.py")
        self.assertEqual(nested.map("D:\\proj\\sub\\cli.py"), "D:\\05_Dev_Toolbox\\sub_tool\\cli.py")

        # os.pathsep list
        sep_list = os.pathsep.join(["D:\\proj\\src", "C:\\other\\lib"])
        expected_list = os.pathsep.join(["D:\\03_Development_Projects\\proj\\src", "C:\\other\\lib"])
        self.assertEqual(mapper.map_value(sep_list), expected_list)
        self.assertIsNone(mapper.map_value(os.pathsep.join(["C:\\a", "C:\\b"])))

        # mentions() pre-check
        self.assertTrue(mapper.mentions('{"cwd": "D:\\\\proj"}'))
        self.assertFalse(mapper.mentions('{"cwd": "D:\\\\unrelated"}'))

    @unittest.skipUnless(sys.platform in ("win32", "darwin"), "Case-insensitive path matching is win32/darwin only")
    def test_01b_path_mapper_case_insensitive(self) -> None:
        mapper = PathMapper([("D:\\proj", "D:\\03_Development_Projects\\proj")])
        self.assertEqual(mapper.map("d:\\proj\\sub"), "d:\\03_development_projects\\proj\\sub")
        self.assertEqual(mapper.map("D:\\PROJ\\Sub"), "D:\\03_Development_Projects\\proj\\Sub")

    # 2. JSON collision
    def test_02_json_key_collision_merge(self) -> None:
        claude_json = self.fake_home / ".claude.json"
        payload = {
            "projects": {
                "D:/proj": {"a": 1, "allowedTools": ["x"]},
                "D:/03_Development_Projects/proj": {"allowedTools": []},
            }
        }
        claude_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        report = relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertTrue(report.changed)
        updated_data = json.loads(claude_json.read_text(encoding="utf-8"))
        projects = updated_data["projects"]
        self.assertEqual(list(projects.keys()), ["D:/03_Development_Projects/proj"])
        entry = projects["D:/03_Development_Projects/proj"]
        self.assertEqual(entry["a"], 1)
        self.assertEqual(entry["allowedTools"], ["x"])

    # 3. End-to-end fixtures & format preservation
    def test_03_end_to_end_agent_configs_and_formats(self) -> None:
        drive = self.test_dir / "drive"
        old_dir = drive / "proj"
        sibling_dir = drive / "proj-old"
        new_dir = drive / "03_Development_Projects" / "proj"
        old_dir.mkdir(parents=True)
        sibling_dir.mkdir(parents=True)
        (new_dir.parent).mkdir(parents=True, exist_ok=True)

        old_str = str(old_dir)
        new_str = str(new_dir)
        sibling_str = str(sibling_dir)
        old_posix = old_dir.as_posix()
        new_posix = new_dir.as_posix()

        # 1) .claude.json: indent 2, NO final newline
        claude_file = self.fake_home / ".claude.json"
        claude_obj = {
            "numStartups": 42,
            "projects": {
                old_str: {"allowedTools": ["bash"], "hasTrustDialogAccepted": True},
                sibling_str: {"allowedTools": []},
            },
            "mcpServers": {
                "custom-srv": {
                    "command": "node",
                    "args": [f"{old_posix}/dist/index.mjs", "--flag=" + old_str],
                }
            },
        }
        claude_raw = json.dumps(claude_obj, indent=2)  # no trailing newline
        claude_file.write_bytes(claude_raw.encode("utf-8"))

        # 2) Antigravity mcp_config.json: CRLF with final newline
        ag_file = self.fake_home / ".gemini" / "antigravity" / "mcp_config.json"
        ag_file.parent.mkdir(parents=True, exist_ok=True)
        ag_obj = {
            "mcpServers": {
                "proj-mcp": {
                    "command": "python",
                    "args": ["-m", "srv"],
                    "env": {"PYTHONPATH": old_str},
                }
            }
        }
        ag_raw = json.dumps(ag_obj, indent=2).replace("\n", "\r\n") + "\r\n"
        ag_file.write_bytes(ag_raw.encode("utf-8"))

        # 3) Codex config.toml: lowercase table and command path
        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        old_lower = old_str.lower()
        expected_codex_proj = new_str.lower() if old_str != old_str.lower() else new_str
        escaped_old = str(old_dir / "run.py").replace("\\", "\\\\")
        codex_raw = (
            f"[projects.'{old_lower}']\n"
            f'trust_level = "trusted"\n'
            f'command = "{escaped_old}"\n'
        )
        codex_file.write_bytes(codex_raw.encode("utf-8"))

        # 4) Compact session file (no backup allowed)
        session_file = self.fake_appdata / "Claude" / "claude-code-sessions" / "2026" / "sess1.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        sess_obj = {
            "sessionId": "s-1",
            "cwd": old_str,
            "originCwd": old_str,
            "gitAnchors": [{"gitRoot": old_str}],
        }
        sess_raw = json.dumps(sess_obj, separators=(",", ":"))
        session_file.write_bytes(sess_raw.encode("utf-8"))

        # 5) git-worktrees.json: tab indent with final newline
        wt_file = self.fake_appdata / "Claude" / "git-worktrees.json"
        wt_obj = {"worktrees": [str(old_dir / ".claude" / "worktrees" / "wt1"), sibling_str]}
        wt_raw = json.dumps(wt_obj, indent="\t") + "\n"
        wt_file.write_bytes(wt_raw.encode("utf-8"))

        # 6) Constellation ecosystem.json
        eco_file = self.fake_home / ".constellation" / "ecosystem.json"
        eco_file.parent.mkdir(parents=True, exist_ok=True)
        eco_obj = {
            "version": "1",
            "servers": [
                {
                    "name": "proj-mcp",
                    "transport": "stdio",
                    "command": "node",
                    "args": [f"{old_posix}/dist/server.mjs"],
                    "env": {},
                    "agents": ["claude"],
                }
            ],
        }
        eco_file.write_bytes((json.dumps(eco_obj, indent=2) + "\n").encode("utf-8"))

        # 7) Root .mcp.json
        root_mcp = drive / ".mcp.json"
        root_mcp_obj = {
            "mcpServers": {
                "local": {"command": f"{old_posix}/bin/run", "args": []}
            }
        }
        root_mcp.write_bytes((json.dumps(root_mcp_obj, indent=2) + "\n").encode("utf-8"))

        # Rename the folder for real, then relink
        os.rename(old_dir, new_dir)
        report = relink(
            [(old_str, new_str)],
            root=str(drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
        )

        # Verify .claude.json values and formatting (indent 2, no trailing newline)
        claude_bytes = claude_file.read_bytes()
        self.assertFalse(claude_bytes.endswith(b"\n"))
        claude_after = json.loads(claude_bytes.decode("utf-8"))
        self.assertEqual(claude_after["numStartups"], 42)
        self.assertIn(new_str, claude_after["projects"])
        self.assertNotIn(old_str, claude_after["projects"])
        self.assertIn(sibling_str, claude_after["projects"])
        self.assertEqual(
            claude_after["mcpServers"]["custom-srv"]["args"],
            [f"{new_posix}/dist/index.mjs", "--flag=" + old_str],
        )

        # Verify Antigravity CRLF and trailing newline
        ag_bytes = ag_file.read_bytes()
        self.assertIn(b"\r\n", ag_bytes)
        self.assertTrue(ag_bytes.endswith(b"\r\n"))
        ag_after = json.loads(ag_bytes.decode("utf-8"))
        self.assertEqual(ag_after["mcpServers"]["proj-mcp"]["env"]["PYTHONPATH"], new_str)

        # Verify Codex config.toml
        codex_after = codex_file.read_text(encoding="utf-8")
        self.assertIn(f"[projects.'{expected_codex_proj}']", codex_after)
        escaped_new = str(new_dir / "run.py").replace("\\", "\\\\")
        self.assertIn(f'command = "{escaped_new}"', codex_after)

        # Verify compact session file & no backup
        sess_bytes = session_file.read_bytes()
        self.assertNotIn(b"\n", sess_bytes)
        sess_after = json.loads(sess_bytes.decode("utf-8"))
        self.assertEqual(sess_after["cwd"], new_str)
        self.assertEqual(sess_after["originCwd"], new_str)
        self.assertEqual(sess_after["gitAnchors"][0]["gitRoot"], new_str)
        self.assertFalse(Path(str(session_file) + BACKUP_SUFFIX).exists())

        # Verify git-worktrees.json tab indent
        wt_text = wt_file.read_text(encoding="utf-8")
        self.assertIn('\n\t"worktrees":', wt_text)
        wt_after = json.loads(wt_text)
        self.assertEqual(wt_after["worktrees"], [str(new_dir / ".claude" / "worktrees" / "wt1"), sibling_str])

        # Verify Constellation & root .mcp.json
        eco_after = json.loads(eco_file.read_text(encoding="utf-8"))
        self.assertEqual(eco_after["servers"][0]["args"], [f"{new_posix}/dist/server.mjs"])
        root_after = json.loads(root_mcp.read_text(encoding="utf-8"))
        self.assertEqual(root_after["mcpServers"]["local"]["command"], f"{new_posix}/bin/run")

        # Verify backups exist for all except session file
        for backed_up in (claude_file, ag_file, codex_file, wt_file, eco_file, root_mcp):
            self.assertTrue(
                Path(str(backed_up) + BACKUP_SUFFIX).is_file(),
                f"Expected backup for {backed_up}",
            )

        # Verify report.agents
        self.assertEqual(
            report.agents,
            {
                "Antigravity": 1,
                "Claude Code": 2,
                "Claude desktop": 4,
                "Codex": 2,
                "Constellation": 1,
                "SSD .mcp.json": 1,
            },
        )

    # 4. Claude history
    def test_04_claude_history_move_and_conflict_merge(self) -> None:
        old_path = "D:\\proj"
        new_path = "D:\\03_Development_Projects\\proj"
        old_s = claude_slug(old_path)
        new_s = claude_slug(new_path)
        self.assertEqual(old_s, "D--proj")
        self.assertEqual(new_s, "D--03-Development-Projects-proj")
        self.assertIsNone(claude_slug("D:\\" + "a" * 200))

        projects_dir = self.fake_home / ".claude" / "projects"
        src_hist = projects_dir / old_s
        (src_hist / "memory").mkdir(parents=True)
        (src_hist / "s1.jsonl").write_text('{"msg": 1}\n', encoding="utf-8")
        (src_hist / "memory" / "MEMORY.md").write_text("old memory\n", encoding="utf-8")

        report = relink(
            [(old_path, new_path)],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        dst_hist = projects_dir / new_s
        self.assertFalse(src_hist.exists())
        self.assertTrue((dst_hist / "s1.jsonl").is_file())
        self.assertEqual((dst_hist / "memory" / "MEMORY.md").read_text(encoding="utf-8"), "old memory\n")
        self.assertEqual(report.history, [{"from": old_s, "to": new_s}])

        # Now test merging when dst/memory/MEMORY.md already exists
        (src_hist / "memory").mkdir(parents=True)
        (src_hist / "s2.jsonl").write_text('{"msg": 2}\n', encoding="utf-8")
        (src_hist / "memory" / "MEMORY.md").write_text("older memory that must not overwrite\n", encoding="utf-8")
        (dst_hist / "memory" / "MEMORY.md").write_text("newer memory at dst\n", encoding="utf-8")

        report2 = relink(
            [(old_path, new_path)],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertTrue((dst_hist / "s2.jsonl").is_file())
        self.assertEqual((dst_hist / "memory" / "MEMORY.md").read_text(encoding="utf-8"), "newer memory at dst\n")
        self.assertEqual((src_hist / "memory" / "MEMORY.md").read_text(encoding="utf-8"), "older memory that must not overwrite\n")
        self.assertEqual(len(report2.warnings), 1)
        self.assertIn("MEMORY.md", report2.warnings[0])

    # 5. Idempotent
    def test_05_idempotent_second_relink(self) -> None:
        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(json.dumps({"projects": {"D:\\proj": {}}}, indent=2), encoding="utf-8")
        moves = [("D:\\proj", "D:\\03_Development_Projects\\proj")]

        first = relink(moves, home=self.fake_home, app_data=self.fake_appdata)
        self.assertTrue(first.changed)
        content_after_first = claude_file.read_bytes()

        # Replace backup content so we can verify the second run does not touch the file or backup
        bak = Path(str(claude_file) + BACKUP_SUFFIX)
        bak.write_bytes(b"sentinel")

        second = relink(moves, home=self.fake_home, app_data=self.fake_appdata)
        self.assertFalse(second.changed)
        self.assertEqual(second.updated, [])
        self.assertEqual(claude_file.read_bytes(), content_after_first)
        self.assertEqual(bak.read_bytes(), b"sentinel")

    # 6. Dry run
    def test_06_dry_run_writes_nothing(self) -> None:
        old_path = "D:\\proj"
        new_path = "D:\\03_Development_Projects\\proj"
        claude_file = self.fake_home / ".claude.json"
        original_bytes = json.dumps({"projects": {old_path: {}}}, indent=2).encode("utf-8")
        claude_file.write_bytes(original_bytes)

        src_hist = self.fake_home / ".claude" / "projects" / claude_slug(old_path)
        src_hist.mkdir(parents=True)
        (src_hist / "s1.jsonl").write_text("{}\n", encoding="utf-8")

        dry = relink(
            [(old_path, new_path)],
            dry_run=True,
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertTrue(dry.dry_run)
        self.assertTrue(dry.changed)
        self.assertEqual(claude_file.read_bytes(), original_bytes)
        self.assertFalse(Path(str(claude_file) + BACKUP_SUFFIX).exists())
        self.assertTrue(src_hist.is_dir())

        real = relink(
            [(old_path, new_path)],
            dry_run=False,
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(dry.updated, real.updated)
        self.assertEqual(dry.history, real.history)

    # 7. Codex duplicate table
    def test_07_codex_duplicate_table_header_kept(self) -> None:
        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[projects.'d:\\proj']\n"
            'trust_level = "trusted"\n\n'
            "[projects.'d:\\03_development_projects\\proj']\n"
            'trust_level = "trusted"\n'
        )
        codex_file.write_text(content, encoding="utf-8")

        relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        after = codex_file.read_text(encoding="utf-8")
        self.assertEqual(after.count("[projects.'d:\\03_development_projects\\proj']"), 1)
        self.assertEqual(after.count("[projects.'d:\\proj']"), 1)

    # 8. git worktree repair
    @unittest.skipUnless(shutil.which("git"), "git is required for worktree repair test")
    def test_08_git_worktree_repair(self) -> None:
        old_repo = self.test_dir / "repo_orig"
        new_repo = self.test_dir / "03_Development_Projects" / "repo_orig"
        old_repo.mkdir(parents=True)
        new_repo.parent.mkdir(parents=True, exist_ok=True)

        def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", str(cwd), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )

        _git(old_repo, "init")
        _git(old_repo, "config", "user.email", "test@example.com")
        _git(old_repo, "config", "user.name", "Test User")
        (old_repo / "README.md").write_text("# Test\n", encoding="utf-8")
        _git(old_repo, "add", "README.md")
        _git(old_repo, "commit", "-m", "initial")

        wt_rel = Path(".claude") / "worktrees" / "wt"
        _git(old_repo, "worktree", "add", str(wt_rel), "-b", "wt")

        os.rename(old_repo, new_repo)
        report = relink(
            [(str(old_repo), str(new_repo))],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertIn(str(new_repo), report.worktrees)

        wt_new = new_repo / wt_rel
        status_proc = subprocess.run(
            ["git", "-C", str(wt_new), "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(status_proc.returncode, 0, status_proc.stderr)

        wt_list = _git(new_repo, "worktree", "list", "--porcelain").stdout
        self.assertIn(wt_new.resolve().as_posix(), wt_list.replace("\\", "/"))

    # 9. Python virtualenv .pth
    def test_09_virtualenv_pth_rewritten_and_warning(self) -> None:
        old_proj = self.test_dir / "my_pkg"
        new_proj = self.test_dir / "03_Development_Projects" / "my_pkg"
        venv_dir = new_proj / ".venv"
        if sys.platform == "win32":
            sp_dir = venv_dir / "Lib" / "site-packages"
        else:
            sp_dir = venv_dir / "lib" / "python3.11" / "site-packages"
        sp_dir.mkdir(parents=True, exist_ok=True)
        (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

        pth_file = sp_dir / "_proj.pth"
        pth_file.write_text(
            f"# editable install\nimport sys; pass\n{old_proj / 'src'}\n",
            encoding="utf-8",
        )

        report = relink(
            [(str(old_proj), str(new_proj))],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        pth_lines = pth_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(pth_lines[0], "# editable install")
        self.assertEqual(pth_lines[1], "import sys; pass")
        self.assertEqual(pth_lines[2], str(new_proj / "src"))
        self.assertEqual(report.agents.get("Python venv"), 1)
        self.assertTrue(any(str(venv_dir) in w for w in report.warnings))

    # 10. Journal
    def test_10_journal_record_and_corrupt_line_ignored(self) -> None:
        drive = self.test_dir / "drive"
        drive.mkdir()
        b1 = [("D:\\a", "D:\\03_Development_Projects\\a")]
        b2 = [("D:\\b", "D:\\03_Development_Projects\\b")]

        record_moves(str(drive), b1, "organize")
        record_moves(str(drive), b2, "classify")

        # Append a corrupt line at the end
        with open(journal_path(str(drive)), "a", encoding="utf-8") as f:
            f.write("{corrupt json line\n")

        latest = last_moves(str(drive))
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest["tool"], "classify")
        self.assertEqual(latest["moves"], b2)

    # 11. AutoZoner atomic rename failure handling
    def test_11_auto_zoner_rename_permission_error_leaves_source_intact(self) -> None:
        drive = self.create_mock_drive()
        proj = drive / "locked_app"
        proj.mkdir()
        (proj / "package.json").write_text('{"name": "locked"}\n', encoding="utf-8")
        (proj / "index.js").write_text("console.log(1);\n", encoding="utf-8")

        zoner = AutoZoner(str(drive))
        plan = [a for a in zoner.generate_plan() if os.path.basename(a.src_path) == "locked_app"]
        self.assertEqual(len(plan), 1)

        with patch("smart_drive.core.auto_zoner.os.rename", side_effect=PermissionError("Access is denied")):
            res = zoner.apply_plan(plan)

        self.assertEqual(res["moved_count"], 0)
        self.assertEqual(res["error_count"], 1)
        self.assertIn("close the editor, terminal or coding agent", res["errors"][0]["error"])
        self.assertTrue((proj / "package.json").is_file())
        self.assertTrue((proj / "index.js").is_file())
        self.assertFalse(Path(plan[0].dest_path).exists())

    # 12. CLI end-to-end
    def test_12_cli_organize_and_relink_end_to_end(self) -> None:
        drive = self.create_mock_drive()
        app_dir = drive / "my_app"
        app_dir.mkdir()
        (app_dir / "package.json").write_text('{"name": "my_app"}\n', encoding="utf-8")

        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(
            json.dumps({"projects": {str(app_dir): {"hasTrustDialogAccepted": True}}}, indent=2),
            encoding="utf-8",
        )

        env = self._cli_env()

        # Dry run first: verify preview
        dry_proc = run_smart_drive_cli(["organize", "--root", str(drive), "--dry-run"], env=env)
        self.assertEqual(dry_proc.returncode, 0, dry_proc.stderr)
        self.assertIn("Coding agents that will follow these moves:", dry_proc.stdout)
        self.assertIn("Claude Code: 1 path(s) would be updated", dry_proc.stdout)

        # Apply organize with --json
        apply_proc = run_smart_drive_cli(
            ["organize", "--root", str(drive), "--apply", "--json"],
            env=env,
        )
        self.assertEqual(apply_proc.returncode, 0, apply_proc.stderr)
        payload = json.loads(apply_proc.stdout)
        self.assertEqual(payload["zoning"]["moved_count"], 1)
        self.assertGreaterEqual(payload["relink"]["agents"].get("Claude Code", 0), 1)

        expected_new = str(drive / "03_Development_Projects" / "my_app")
        claude_after = json.loads(claude_file.read_text(encoding="utf-8"))
        self.assertIn(expected_new, claude_after["projects"])

        latest = last_moves(str(drive))
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest["moves"], [(str(app_dir), expected_new)])

        # relink <old> <new> --dry-run --json
        manual_proc = run_smart_drive_cli(
            ["relink", str(app_dir), expected_new, "--dry-run", "--json"],
            env=env,
        )
        self.assertEqual(manual_proc.returncode, 0, manual_proc.stderr)
        manual_data = json.loads(manual_proc.stdout)
        self.assertEqual(manual_data["updated"], [])
        self.assertEqual(manual_data["agents"], {})

        # relink with no arguments (uses journal)
        journal_proc = run_smart_drive_cli(["relink", "--root", str(drive)], env=env)
        self.assertEqual(journal_proc.returncode, 0, journal_proc.stderr)
        self.assertIn("Nothing left to change", journal_proc.stdout)

    def test_13_agent_files_ignores_env_overrides_when_home_passed(self) -> None:
        other_dir = self.test_dir / "env_override_dir"
        other_dir.mkdir()
        (other_dir / "mcp_config.json").write_text("{}", encoding="utf-8")
        (other_dir / "ecosystem.json").write_text("{}", encoding="utf-8")

        with patch.dict(
            os.environ,
            {
                "ANTIGRAVITY_HOME": str(other_dir),
                "GEMINI_HOME": str(other_dir),
                "CONSTELLATION_HOME": str(other_dir),
            },
        ):
            files = agent_files(home=self.fake_home, app_data=self.fake_appdata)
        self.assertEqual(files, [])

    def test_14_cli_classify_apply_and_relink_edge_cases(self) -> None:
        drive = self.create_mock_drive()
        env = self._cli_env()

        # No moves recorded yet -> exit 0 with message
        empty_proc = run_smart_drive_cli(["relink", "--root", str(drive)], env=env)
        self.assertEqual(empty_proc.returncode, 0)
        self.assertIn(
            "Nothing to relink yet: organize and classify have not moved any folder.",
            empty_proc.stdout,
        )

        # Single positional argument -> exit 2
        one_arg_proc = run_smart_drive_cli(["relink", str(drive / "old")], env=env)
        self.assertEqual(one_arg_proc.returncode, 2)

        # Non-existent destination -> exit 2
        missing_new_proc = run_smart_drive_cli(
            ["relink", str(drive / "old"), str(drive / "missing_new")],
            env=env,
        )
        self.assertEqual(missing_new_proc.returncode, 2)

        # classify --apply relinks moved project
        repo = drive / "loose_repo"
        repo.mkdir()
        (repo / "pyproject.toml").write_text('[project]\nname = "loose"\n', encoding="utf-8")
        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(json.dumps({"projects": {str(repo): {}}}), encoding="utf-8")

        cls_proc = run_smart_drive_cli(
            ["classify", "--root", str(drive), "--apply", "--json"],
            env=env,
        )
        self.assertEqual(cls_proc.returncode, 0, cls_proc.stderr)
        cls_data = json.loads(cls_proc.stdout)
        self.assertIsNotNone(cls_data["relink"])
        self.assertEqual(cls_data["relink"]["agents"].get("Claude Code"), 1)

    def test_15_lone_surrogate_in_json_serialized_cleanly(self) -> None:
        claude_file = self.fake_home / ".claude.json"
        raw_json = '{"projects": {"D:\\\\proj": {"note": "cut emoji \\ud83d"}}}\n'
        claude_file.write_bytes(raw_json.encode("utf-8"))

        report = relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(report.errors, [])
        self.assertTrue(report.changed)
        text_after = claude_file.read_bytes().decode("utf-8")
        self.assertIn("\\ud83d", text_after)
        parsed = json.loads(text_after)
        self.assertIn("D:\\03_Development_Projects\\proj", parsed["projects"])
        self.assertEqual(parsed["projects"]["D:\\03_Development_Projects\\proj"]["note"], "cut emoji \ud83d")

    def test_16_per_file_isolation_on_exception(self) -> None:
        # Deeply nested JSON that triggers RecursionError must not stop other agent files from being relinked.
        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text("[" * 2500 + '"D:\\\\proj"' + "]" * 2500, encoding="utf-8")

        cursor_file = self.fake_home / ".cursor" / "mcp.json"
        cursor_file.parent.mkdir(parents=True, exist_ok=True)
        cursor_file.write_text(
            json.dumps({"mcpServers": {"s": {"command": "D:\\proj\\run.exe"}}}),
            encoding="utf-8",
        )

        report = relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(len(report.errors), 1)
        self.assertEqual(report.errors[0]["file"], str(claude_file))
        self.assertEqual(report.agents, {"Cursor": 1})
        cursor_after = json.loads(cursor_file.read_text(encoding="utf-8"))
        self.assertEqual(
            cursor_after["mcpServers"]["s"]["command"],
            "D:\\03_Development_Projects\\proj\\run.exe",
        )

    def test_17_codex_header_with_trailing_comment_not_duplicated(self) -> None:
        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[projects.'d:\\proj'] # note\n"
            'trust_level = "trusted"\n\n'
            "[projects.'d:\\03_development_projects\\proj']\n"
            'trust_level = "trusted"\n'
        )
        codex_file.write_text(content, encoding="utf-8")

        relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        after = codex_file.read_text(encoding="utf-8")
        self.assertIn("[projects.'d:\\proj'] # note", after)
        self.assertEqual(after.count("[projects.'d:\\03_development_projects\\proj']"), 1)

    def test_18_permission_settings_never_rewritten(self) -> None:
        cfg_file = self.fake_appdata / "Claude" / "claude_desktop_config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "preferences": {
                "epitaxyPrefs": {
                    "epitaxy-folder-permission-mode.1": {"folder": "D:\\proj", "mode": "auto"},
                    "epitaxy-perm-mode-acks.1": ["D:\\proj:auto", "D:\\proj"],
                }
            },
            "mcpServers": {
                "srv": {"command": "node", "args": ["D:\\proj\\index.js"]},
            },
        }
        cfg_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        report = relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(report.agents, {"Claude desktop": 1})
        after = json.loads(cfg_file.read_text(encoding="utf-8"))
        prefs = after["preferences"]["epitaxyPrefs"]
        self.assertEqual(prefs["epitaxy-folder-permission-mode.1"], {"folder": "D:\\proj", "mode": "auto"})
        self.assertEqual(prefs["epitaxy-perm-mode-acks.1"], ["D:\\proj:auto", "D:\\proj"])
        self.assertEqual(
            after["mcpServers"]["srv"]["args"],
            ["D:\\03_Development_Projects\\proj\\index.js"],
        )

    def test_19_long_path_warning_only_for_moves_or_matching_history_folder(self) -> None:
        claude_file = self.fake_home / ".claude.json"
        long_mcp = "D:\\proj\\" + ("m" * 210) + "\\server.mjs"
        long_subproj = "D:\\proj\\" + ("s" * 210)
        claude_file.write_text(
            json.dumps({
                "projects": {long_subproj: {}},
                "mcpServers": {"srv": {"command": "node", "args": [long_mcp]}},
            }),
            encoding="utf-8",
        )

        # Without a matching ~/.claude/projects folder, neither long_mcp nor long_subproj warns
        r1 = relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(r1.warnings, [])

        # With a ~/.claude/projects folder matching the first 200 chars of long_subproj's slug, it warns
        claude_file.write_text(
            json.dumps({
                "projects": {long_subproj: {}},
                "mcpServers": {"srv": {"command": "node", "args": [long_mcp]}},
            }),
            encoding="utf-8",
        )
        prefix_200 = ("D--proj-" + ("s" * 210))[:200]
        hist_dir = self.fake_home / ".claude" / "projects" / f"{prefix_200}-9f8e7d"
        hist_dir.mkdir(parents=True, exist_ok=True)

        r2 = relink(
            [("D:\\proj", "D:\\03_Development_Projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(len(r2.warnings), 1)
        self.assertIn(long_subproj, r2.warnings[0])

        # And when the move's own old path exceeds 200 chars, it always warns
        long_move_old = "D:\\" + ("p" * 205)
        long_move_new = "D:\\03_Development_Projects\\" + ("p" * 205)
        r3 = relink(
            [(long_move_old, long_move_new)],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(len(r3.warnings), 1)
        self.assertIn(long_move_old, r3.warnings[0])

    def test_20_lowercase_path_keeps_new_case(self) -> None:
        mapper = PathMapper([("/tmp/t/drive/proj", "/tmp/t/drive/03_Development_Projects/proj")])
        self.assertEqual(
            mapper.map("/tmp/t/drive/proj/x"),
            "/tmp/t/drive/03_Development_Projects/proj/x",
        )
        if sys.platform in ("win32", "darwin"):
            ci_mapper = PathMapper([("D:\\Proj", "D:\\03_Development_Projects\\Proj")])
            self.assertEqual(
                ci_mapper.map("d:\\proj\\sub"),
                "d:\\03_development_projects\\proj\\sub",
            )

    def test_21_find_moved_same_name_ambiguous_renamed_not_found_unreadable_grouping_journal(self) -> None:
        drive = self.test_dir / "drive"
        drive.mkdir()

        # 1) same-name one level down (A/*/<name>) containing remembered direct subfolders (src, docs)
        one_down_new = drive / "03_Development_Projects" / "proj_one"
        (one_down_new / "src").mkdir(parents=True)
        (one_down_new / "docs").mkdir(parents=True)

        # 2) same-name two levels down (A/*/*/<name>) containing remembered direct subfolder (dist)
        two_down_new = drive / "03_Development_Projects" / "ai_agents" / "proj_two"
        (two_down_new / "dist").mkdir(parents=True)

        # 3) ambiguous (two candidates)
        amb_c1 = drive / "zone_a" / "proj_amb"
        amb_c2 = drive / "zone_b" / "sub" / "proj_amb"
        amb_c1.mkdir(parents=True)
        amb_c2.mkdir(parents=True)

        # 4) renamed parent (old_parent/sub_a and old_parent/sub_b remembered; new_parent has sub_a and sub_b)
        renamed_new = drive / "new_parent"
        (renamed_new / "sub_a").mkdir(parents=True)
        (renamed_new / "sub_b").mkdir(parents=True)

        # 5) journal beats same-name search
        journal_target = drive / "06_Archives_Storage" / "proj_journal_custom"
        journal_decoy = drive / "03_Development_Projects" / "proj_journal"
        journal_target.mkdir(parents=True)
        journal_decoy.mkdir(parents=True)
        record_moves(
            str(drive),
            [(str(drive / "proj_journal"), str(journal_target))],
            "organize",
        )

        # 6) grouped paths across multiple files (.claude.json and .codex/config.toml)
        grouped_old = drive / "proj_one"
        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(
            json.dumps({
                "projects": {
                    str(grouped_old): {},
                    str(grouped_old / "src" / "main.py"): {},
                    str(drive / "proj_two" / "dist" / "index.mjs"): {},
                    str(drive / "proj_amb"): {},
                    str(drive / "old_parent" / "sub_a"): {},
                    str(drive / "old_parent" / "sub_b"): {},
                    str(drive / "proj_journal" / "sub"): {},
                    str(drive / "vanished_proj"): {},
                },
                "badList": ["E:ProjectWithoutSlash"],
            }),
            encoding="utf-8",
        )

        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            f"[projects.'{str(grouped_old / 'docs')}']\ntrust_level = \"trusted\"\n",
            encoding="utf-8",
        )

        entries = find_moved(root=str(drive), home=self.fake_home, app_data=self.fake_appdata)
        by_old = {e.old: e for e in entries}

        # same-name 1 level down + grouped (3 paths across Claude Code and Codex)
        e_one = by_old[str(grouped_old)]
        self.assertEqual(e_one.kind, "same-name")
        self.assertEqual(e_one.new, str(one_down_new))
        self.assertEqual(e_one.paths, 3)
        self.assertEqual(e_one.agents, ["Claude Code", "Codex"])

        # same-name 2 levels down
        e_two = by_old[str(drive / "proj_two")]
        self.assertEqual(e_two.kind, "same-name")
        self.assertEqual(e_two.new, str(two_down_new))

        # ambiguous
        e_amb = by_old[str(drive / "proj_amb")]
        self.assertEqual(e_amb.kind, "ambiguous")
        self.assertIsNone(e_amb.new)
        self.assertEqual(e_amb.candidates, [str(amb_c1), str(amb_c2)])

        # renamed parent
        e_ren = by_old[str(drive / "old_parent")]
        self.assertEqual(e_ren.kind, "renamed")
        self.assertEqual(e_ren.new, str(renamed_new))
        self.assertEqual(e_ren.paths, 2)

        # journal beats same-name
        e_jrn = by_old[str(drive / "proj_journal")]
        self.assertEqual(e_jrn.kind, "journal")
        self.assertEqual(e_jrn.new, str(journal_target))

        # not-found
        e_nf = by_old[str(drive / "vanished_proj")]
        self.assertEqual(e_nf.kind, "not-found")
        self.assertIsNone(e_nf.new)

        # unreadable
        e_unr = by_old["E:ProjectWithoutSlash"]
        self.assertEqual(e_unr.kind, "unreadable")
        self.assertIsNone(e_unr.new)
        self.assertEqual(e_unr.agents, ["Claude Code"])

    def test_22_find_moved_ignores_perm_scratch_worktree_and_temp_paths(self) -> None:
        drive = self.test_dir / "drive"
        drive.mkdir()
        existing_repo = drive / "repo"
        existing_repo.mkdir()

        cfg_file = self.fake_appdata / "Claude" / "claude_desktop_config.json"
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        sys_temp_gone = self.fake_temp / "sd_nonexistent_temp_folder_999" / "work"
        home_temp_gone = self.fake_home / "AppData" / "Local" / "Temp" / "gone_temp"
        scratch_gone = self.fake_appdata / "Claude" / "scratch-workspaces" / "ws1" / "scratch-1"
        worktree_gone = existing_repo / ".claude" / "worktrees" / "wt-removed"

        projects = {
            str(scratch_gone): {},
            str(worktree_gone): {},
            str(sys_temp_gone): {},
        }
        if sys.platform == "win32":
            projects[str(home_temp_gone)] = {}

        cfg_file.write_text(
            json.dumps({
                "preferences": {
                    "epitaxyPrefs": {
                        "epitaxy-folder-permission-mode.1": {str(drive / "perm_gone"): "auto"},
                        "epitaxy-perm-mode-acks.1": [str(drive / "perm_gone_2")],
                    }
                },
                "projects": projects,
            }),
            encoding="utf-8",
        )

        entries = find_moved(root=str(drive), home=self.fake_home, app_data=self.fake_appdata)
        self.assertEqual(entries, [])

    def test_23_cli_relink_find_apply_json_and_manual_journal(self) -> None:
        drive = self.create_mock_drive()
        env = self._cli_env()

        # Empty find -> "No stale paths: every folder the coding agents remember exists."
        empty_proc = run_smart_drive_cli(["relink", "--find", "--root", str(drive)], env=env)
        self.assertEqual(empty_proc.returncode, 0, empty_proc.stderr)
        self.assertIn("No stale paths: every folder the coding agents remember exists.", empty_proc.stdout)

        # --find with OLD/NEW -> exit 2
        bad_proc = run_smart_drive_cli(["relink", "--find", "old", "new"], env=env)
        self.assertEqual(bad_proc.returncode, 2)

        # Setup sure (same-name and journal), renamed, ambiguous, not-found, unreadable
        same_new = drive / "03_Development_Projects" / "sure_app"
        same_new.mkdir(parents=True)
        jrn_new = drive / "03_Development_Projects" / "jrn_app_moved"
        jrn_new.mkdir(parents=True)
        record_moves(str(drive), [(str(drive / "jrn_app"), str(jrn_new))], "organize")

        ren_new = drive / "renamed_box"
        (ren_new / "part1").mkdir(parents=True)
        (ren_new / "part2").mkdir(parents=True)

        amb1 = drive / "03_Development_Projects" / "amb_app"
        amb2 = drive / "05_Dev_Toolbox" / "amb_app"
        amb1.mkdir(parents=True)
        amb2.mkdir(parents=True)

        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(
            json.dumps({
                "projects": {
                    str(drive / "sure_app"): {},
                    str(drive / "jrn_app"): {},
                    str(drive / "old_box" / "part1"): {},
                    str(drive / "old_box" / "part2"): {},
                    str(drive / "amb_app"): {},
                    str(drive / "missing_app"): {},
                },
                "extra": ["D:BadRelPath"],
            }, indent=2),
            encoding="utf-8",
        )

        # CLI --find text output
        find_proc = run_smart_drive_cli(["relink", "--find", "--root", str(drive)], env=env)
        self.assertEqual(find_proc.returncode, 0, find_proc.stderr)
        self.assertIn(f"✓ {drive / 'sure_app'} -> {same_new}  (same-name; Claude Code)", find_proc.stdout)
        self.assertIn(f"✓ {drive / 'jrn_app'} -> {jrn_new}  (journal; Claude Code)", find_proc.stdout)
        self.assertIn(
            f'? {drive / "old_box"} -> {ren_new}  (renamed? check it, then run: smart-drive relink "{drive / "old_box"}" "{ren_new}")',
            find_proc.stdout,
        )
        self.assertIn(f"? {drive / 'amb_app'}: several candidates:", find_proc.stdout)
        self.assertIn(f"✗ {drive / 'missing_app'}: not found (Claude Code)", find_proc.stdout)
        self.assertIn("✗ D:BadRelPath: not an absolute path (Claude Code)", find_proc.stdout)
        self.assertIn("Run `smart-drive relink --find --apply` to relink the ✓ folders.", find_proc.stdout)

        # CLI --find --json (without --apply)
        json_proc = run_smart_drive_cli(["relink", "--find", "--root", str(drive), "--json"], env=env)
        self.assertEqual(json_proc.returncode, 0, json_proc.stderr)
        json_data = json.loads(json_proc.stdout)
        self.assertIsNone(json_data["relink"])
        self.assertEqual(len(json_data["entries"]), 6)

        # CLI --find --apply --dry-run --json
        dry_proc = run_smart_drive_cli(
            ["relink", "--find", "--apply", "--dry-run", "--root", str(drive), "--json"],
            env=env,
        )
        self.assertEqual(dry_proc.returncode, 0, dry_proc.stderr)
        dry_data = json.loads(dry_proc.stdout)
        self.assertIsNotNone(dry_data["relink"])
        self.assertTrue(dry_data["relink"]["dry_run"])
        self.assertEqual(dry_data["relink"]["agents"], {"Claude Code": 2})

        # CLI --find --apply (real run): relinks only ✓ entries and journals them with tool "relink"
        apply_proc = run_smart_drive_cli(["relink", "--find", "--apply", "--root", str(drive)], env=env)
        self.assertEqual(apply_proc.returncode, 0, apply_proc.stderr)
        self.assertIn("Claude Code: 2 path(s) updated", apply_proc.stdout)

        after_projects = json.loads(claude_file.read_text(encoding="utf-8"))["projects"]
        self.assertIn(str(same_new), after_projects)
        self.assertIn(str(jrn_new), after_projects)
        self.assertNotIn(str(drive / "sure_app"), after_projects)
        self.assertNotIn(str(drive / "jrn_app"), after_projects)
        # Non-✓ entries untouched
        self.assertIn(str(drive / "old_box" / "part1"), after_projects)
        self.assertIn(str(drive / "amb_app"), after_projects)
        self.assertIn(str(drive / "missing_app"), after_projects)

        latest = last_moves(str(drive))
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest["tool"], "relink")
        self.assertEqual(
            set(latest["moves"]),
            {(str(drive / "sure_app"), str(same_new)), (str(drive / "jrn_app"), str(jrn_new))},
        )

        # Manual `relink OLD NEW` is also journaled with tool "relink"
        manual_run = run_smart_drive_cli(
            ["relink", str(drive / "old_box"), str(ren_new), "--root", str(drive)],
            env=env,
        )
        self.assertEqual(manual_run.returncode, 0, manual_run.stderr)
        latest_manual = last_moves(str(drive))
        self.assertIsNotNone(latest_manual)
        assert latest_manual is not None
        self.assertEqual(latest_manual["tool"], "relink")
        self.assertEqual(latest_manual["moves"], [(str(drive / "old_box"), str(ren_new))])

    def test_24_running_agents_and_warning_without_wait(self) -> None:
        with patch(
            "smart_drive.core.relink._running_processes",
            return_value={"Claude.exe", "/usr/bin/codex", "Cursor.exe"},
        ):
            self.assertEqual(
                running_agents(),
                {"Claude Code", "Claude desktop", "Codex", "Cursor"},
            )

        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(json.dumps({"projects": {"D:\\proj": {}}}), encoding="utf-8")

        with patch("smart_drive.core.relink._running_processes", return_value={"claude.exe"}):
            report = relink(
                [("D:\\proj", "D:\\03_Development_Projects\\proj")],
                home=self.fake_home,
                app_data=self.fake_appdata,
            )

        self.assertTrue(report.changed)
        expected_warn = (
            "Claude Code is running: it may write its old settings back when it quits. "
            "Quit it, then run `smart-drive relink --wait` (or `smart-drive relink` again)."
        )
        self.assertIn(expected_warn, report.warnings)

    def test_25_wait_polls_until_closed_then_writes_and_timeout_exits_3(self) -> None:
        drive = self.create_mock_drive()
        new_dir = drive / "03_Development_Projects" / "wait_app"
        new_dir.mkdir(parents=True, exist_ok=True)
        old_dir = drive / "wait_app"

        claude_file = self.fake_home / ".claude.json"
        initial_json = json.dumps({"projects": {str(old_dir): {}}}, indent=2)
        claude_file.write_text(initial_json, encoding="utf-8")

        sleeps: list[float] = []
        # 1st call (preview check): claude running; 2nd call (poll 1): claude running; 3rd call (poll 2): closed; 4th call (real relink): closed
        proc_seq = [{"Claude.exe"}, {"Claude.exe"}, set(), set()]
        clock = [0.0, 2.0, 4.0, 6.0]

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch("smart_drive.core.relink._running_processes", side_effect=proc_seq),
            patch("smart_drive.cli.cmd_relink.time.sleep", side_effect=lambda s: sleeps.append(s)),
            patch("smart_drive.cli.cmd_relink.time.monotonic", side_effect=clock),
            contextlib.redirect_stdout(out_buf),
            contextlib.redirect_stderr(err_buf),
        ):
            rc = cli_main(["relink", "--find", "--apply", "--wait", "--root", str(drive)])

        self.assertEqual(rc, 0, err_buf.getvalue())
        self.assertEqual(sleeps, [2, 2])
        self.assertIn(
            "Waiting for Claude Code to close. Quit them now (Ctrl+C cancels)...",
            err_buf.getvalue(),
        )
        updated_projects = json.loads(claude_file.read_text(encoding="utf-8"))["projects"]
        self.assertIn(str(new_dir), updated_projects)

        # Now test timeout -> exit 3 and no file changed
        claude_file.write_text(initial_json, encoding="utf-8")
        out_timeout = io.StringIO()
        err_timeout = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch("smart_drive.core.relink._running_processes", return_value={"Claude.exe"}),
            patch("smart_drive.cli.cmd_relink.time.sleep", side_effect=lambda s: None),
            patch("smart_drive.cli.cmd_relink.time.monotonic", side_effect=[0.0, 10.0, 35.0]),
            contextlib.redirect_stdout(out_timeout),
            contextlib.redirect_stderr(err_timeout),
        ):
            rc_timeout = cli_main(["relink", str(old_dir), str(new_dir), "--wait", "0.5", "--root", str(drive)])

        self.assertEqual(rc_timeout, 3)
        self.assertIn("timed out", (out_timeout.getvalue() + err_timeout.getvalue()).lower())
        self.assertEqual(claude_file.read_text(encoding="utf-8"), initial_json)

    def test_26_organize_apply_adds_running_agent_warning(self) -> None:
        drive = self.create_mock_drive()
        app_dir = drive / "busy_app"
        app_dir.mkdir()
        (app_dir / "package.json").write_text('{"name": "busy_app"}\n', encoding="utf-8")

        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(json.dumps({"projects": {str(app_dir): {}}}), encoding="utf-8")

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch("smart_drive.core.relink._running_processes", return_value={"claude.exe"}),
            contextlib.redirect_stdout(out_buf),
            contextlib.redirect_stderr(err_buf),
        ):
            rc = cli_main(["organize", "--root", str(drive), "--apply", "--json"])

        self.assertEqual(rc, 0, err_buf.getvalue())
        payload = json.loads(out_buf.getvalue())
        self.assertEqual(payload["zoning"]["moved_count"], 1)
        warnings = payload["relink"]["warnings"]
        self.assertTrue(
            any("Claude Code is running: it may write its old settings back when it quits." in w for w in warnings),
            f"Expected running-agent warning in {warnings!r}",
        )

    def test_27_msix_claude_desktop_files_relinked_and_deduplicated(self) -> None:
        roaming_claude = self.fake_appdata / "Claude"
        msix_claude = (
            self.fake_home
            / "AppData"
            / "Local"
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        roaming_claude.mkdir(parents=True, exist_ok=True)
        msix_claude.mkdir(parents=True, exist_ok=True)

        roaming_cfg = roaming_claude / "claude_desktop_config.json"
        msix_cfg = msix_claude / "claude_desktop_config.json"
        msix_wt = msix_claude / "git-worktrees.json"
        msix_sess = msix_claude / "claude-code-sessions" / "acc" / "org" / "local_1.json"
        msix_sess.parent.mkdir(parents=True, exist_ok=True)

        roaming_cfg.write_text(
            json.dumps({"mcpServers": {"a": {"args": ["D:\\proj\\roaming.js"]}}}),
            encoding="utf-8",
        )
        msix_cfg.write_text(
            json.dumps({"mcpServers": {"b": {"args": ["D:\\proj\\msix.js"]}}}),
            encoding="utf-8",
        )
        msix_wt.write_text(
            json.dumps({"worktrees": [{"path": "D:\\proj\\wt"}]}),
            encoding="utf-8",
        )
        msix_sess.write_text(
            json.dumps({"cwd": "D:\\proj\\sess"}),
            encoding="utf-8",
        )

        # Ignore external LOCALAPPDATA when home is passed
        env_local = self.test_dir / "env_local_appdata"
        env_msix = env_local / "Packages" / "Claude_other" / "LocalCache" / "Roaming" / "Claude"
        env_msix.mkdir(parents=True, exist_ok=True)
        (env_msix / "claude_desktop_config.json").write_text(
            json.dumps({"mcpServers": {"x": {"args": ["D:\\proj\\ignored.js"]}}}),
            encoding="utf-8",
        )

        with patch.object(sys, "platform", "win32"), patch.dict(os.environ, {"LOCALAPPDATA": str(env_local)}):
            report = relink(
                [("D:\\proj", "D:\\03_Development_Projects\\proj")],
                home=self.fake_home,
                app_data=self.fake_appdata,
            )

        self.assertEqual(report.agents, {"Claude desktop": 4})
        self.assertEqual(
            json.loads(roaming_cfg.read_text(encoding="utf-8"))["mcpServers"]["a"]["args"],
            ["D:\\03_Development_Projects\\proj\\roaming.js"],
        )
        self.assertEqual(
            json.loads(msix_cfg.read_text(encoding="utf-8"))["mcpServers"]["b"]["args"],
            ["D:\\03_Development_Projects\\proj\\msix.js"],
        )
        self.assertEqual(
            json.loads(msix_wt.read_text(encoding="utf-8"))["worktrees"][0]["path"],
            "D:\\03_Development_Projects\\proj\\wt",
        )
        self.assertEqual(
            json.loads(msix_sess.read_text(encoding="utf-8"))["cwd"],
            "D:\\03_Development_Projects\\proj\\sess",
        )
        self.assertTrue(Path(str(msix_cfg) + BACKUP_SUFFIX).is_file())
        self.assertFalse(Path(str(msix_sess) + BACKUP_SUFFIX).exists())

        # Same physical file reachable through both <AppData>\Claude and MSIX -> kept once (first wins) and processed once
        msix_wt.unlink()
        msix_sess.unlink()
        msix_cfg.unlink()
        roaming_cfg.write_text(
            json.dumps({"mcpServers": {"shared": {"args": ["D:\\proj2\\shared.js"]}}}),
            encoding="utf-8",
        )
        os.link(roaming_cfg, msix_cfg)

        with patch.object(sys, "platform", "win32"):
            files = agent_files(home=self.fake_home, app_data=self.fake_appdata)
            self.assertEqual([af.path for af in files], [roaming_cfg])
            r_shared = relink(
                [("D:\\proj2", "D:\\03_Development_Projects\\proj2")],
                home=self.fake_home,
                app_data=self.fake_appdata,
            )

        self.assertEqual(len(r_shared.updated), 1)
        self.assertEqual(r_shared.updated[0]["file"], str(roaming_cfg))
        self.assertEqual(r_shared.agents, {"Claude desktop": 1})

    def test_28_path_branch_nul_keys_and_values_rewritten(self) -> None:
        mapper = PathMapper([("D:\\teamwork_projects\\smart_drive_os", "D:\\03_Development_Projects\\smart_drive_os")])
        self.assertEqual(
            mapper.map_value("D:\\teamwork_projects\\smart_drive_os\x00feat/relink-agent-paths"),
            "D:\\03_Development_Projects\\smart_drive_os\x00feat/relink-agent-paths",
        )
        self.assertIsNone(
            mapper.map_value("D:\\unrelated\\repo\x00feat/relink-agent-paths")
        )

        sess_file = (
            self.fake_appdata
            / "Claude"
            / "claude-code-sessions"
            / "acc"
            / "org"
            / "local_session.json"
        )
        sess_file.parent.mkdir(parents=True, exist_ok=True)
        sess_file.write_text(
            json.dumps({
                "branches": {
                    "D:\\teamwork_projects\\smart_drive_os\x00feat/relink-agent-paths": {
                        "active": "D:\\teamwork_projects\\smart_drive_os\x00main",
                        "untouched": "D:\\other_repo\x00dev",
                    },
                    "D:\\other_repo\x00main": "D:\\other_repo\x00main",
                }
            }),
            encoding="utf-8",
        )

        report = relink(
            [("D:\\teamwork_projects\\smart_drive_os", "D:\\03_Development_Projects\\smart_drive_os")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )
        self.assertEqual(report.agents, {"Claude desktop": 2})

        after = json.loads(sess_file.read_text(encoding="utf-8"))["branches"]
        new_key = "D:\\03_Development_Projects\\smart_drive_os\x00feat/relink-agent-paths"
        self.assertIn(new_key, after)
        self.assertEqual(
            after[new_key]["active"],
            "D:\\03_Development_Projects\\smart_drive_os\x00main",
        )
        self.assertEqual(after[new_key]["untouched"], "D:\\other_repo\x00dev")
        self.assertEqual(after["D:\\other_repo\x00main"], "D:\\other_repo\x00main")

    def test_29_stricter_find_matches_and_less_noise(self) -> None:
        drive = self.create_mock_drive()

        # 1) E:\test -> E:\Sandbox case:
        # Remembered: <drive>/test/SWE and <drive>/test/test.
        # <drive>/Sandbox holds both SWE and test; <drive>/Sandbox/test is a same-name candidate without SWE.
        # Must report kind="renamed" -> <drive>/Sandbox, NOT kind="same-name" -> <drive>/Sandbox/test.
        sandbox = drive / "Sandbox"
        (sandbox / "SWE").mkdir(parents=True)
        (sandbox / "test").mkdir(parents=True)

        # 2) Same-name candidate holding all of S stays ✓ ("same-name")
        full_match_new = drive / "03_Development_Projects" / "full_app"
        (full_match_new / "api").mkdir(parents=True)
        (full_match_new / "web").mkdir(parents=True)

        # 3) Same-name candidate failing S check with no renamed match -> "ambiguous" with candidates
        partial_same = drive / "05_Dev_Toolbox" / "partial_app"
        partial_same.mkdir(parents=True)  # does NOT contain "core"

        # 4) Folder name containing " - " is not cut
        dash_new = drive / "02_Learning_Materials" / "Resource 16 - PE 18"
        dash_new.mkdir(parents=True)

        # 5) Custom skip_dirs target
        custom_skip = drive / "custom_ignored_zone"

        # Pick an unmounted drive letter on Windows (or patch _unmounted_root on POSIX)
        unmounted_drive = "Z:"
        for letter in "ZYXWVUTSRQPONMLKJIHGF":
            if not os.path.exists(f"{letter}:\\"):
                unmounted_drive = f"{letter}:"
                break

        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(
            json.dumps({
                "projects": {
                    str(drive / "test" / "SWE"): {},
                    str(drive / "test" / "test"): {},
                    str(drive / "full_app" / "api"): {},
                    str(drive / "full_app" / "web"): {},
                    str(drive / "partial_app" / "core"): {},
                    str(drive / "Resource 16 - PE 18"): {},
                    str(custom_skip / "gone_sub"): {},
                    # NUL-joined and control-char strings must be ignored by find_moved
                    f"{drive / 'vanished_nul'}\x00feat/relink-agent-paths": {},
                    f"{drive / 'vanished_ctrl'}\x07bell": {},
                    # Windows invalid characters in components must be ignored
                    "D:\\bad_colon\\foo:bar": {},
                    "D:\\bad_star\\foo*bar": {},
                    "D:\\bad_qmark\\foo?bar": {},
                    'D:\\bad_quote\\foo"bar': {},
                    "D:\\bad_lt\\foo<bar": {},
                    "D:\\bad_gt\\foo>bar": {},
                    "D:\\bad_pipe\\foo|bar": {},
                    "E:bad*unreadable": {},
                    # Unmounted drive paths
                    f"{unmounted_drive}\\portable_repo_1": {},
                    f"{unmounted_drive}\\portable_repo_2\\sub": {},
                }
            }),
            encoding="utf-8",
        )

        entries = find_moved(
            root=str(drive),
            home=self.fake_home,
            app_data=self.fake_appdata,
            skip_dirs=[str(custom_skip)],
        )
        by_old = {e.old: e for e in entries}

        # 1) E:\test -> E:\Sandbox is "renamed", not "same-name"
        e_test = by_old[str(drive / "test")]
        self.assertEqual(e_test.kind, "renamed")
        self.assertEqual(e_test.new, str(sandbox))

        # 2) Same-name candidate holding all of S stays "same-name" (✓)
        e_full = by_old[str(drive / "full_app")]
        self.assertEqual(e_full.kind, "same-name")
        self.assertEqual(e_full.new, str(full_match_new))

        # 3) Same-name candidate failing S check without renamed match -> "ambiguous"
        e_part = by_old[str(drive / "partial_app")]
        self.assertEqual(e_part.kind, "ambiguous")
        self.assertIsNone(e_part.new)
        self.assertEqual(e_part.candidates, [str(partial_same)])

        # 4) Name with " - " is preserved and matched as "same-name"
        e_dash = by_old[str(drive / "Resource 16 - PE 18")]
        self.assertEqual(e_dash.kind, "same-name")
        self.assertEqual(e_dash.new, str(dash_new))

        # 5) skip_dirs honoured
        self.assertNotIn(str(custom_skip), by_old)
        self.assertNotIn(str(custom_skip / "gone_sub"), by_old)

        # 6) NUL-joined, control-char, and invalid-character strings ignored
        for noisy in (
            str(drive / "vanished_nul"),
            f"{drive / 'vanished_nul'}\x00feat/relink-agent-paths",
            str(drive / "vanished_ctrl"),
            "E:bad*unreadable",
        ):
            self.assertNotIn(noisy, by_old)
        for bad_fragment in ("bad_colon", "bad_star", "bad_qmark", "bad_quote", "bad_lt", "bad_gt", "bad_pipe"):
            self.assertFalse(any(bad_fragment in k for k in by_old), f"Should ignore {bad_fragment}")

        # 7) Unmounted drive skipped and counted in skipped_unmounted, CLI text, and --json
        if sys.platform == "win32":
            self.assertEqual(entries.skipped_unmounted, {unmounted_drive: 2})

            env = self._cli_env()
            txt_proc = run_smart_drive_cli(["relink", "--find", "--root", str(drive)], env=env)
            self.assertEqual(txt_proc.returncode, 0, txt_proc.stderr)
            self.assertIn(
                f"Skipped 2 path(s) on drives that are not connected: {unmounted_drive}",
                txt_proc.stdout,
            )

            js_proc = run_smart_drive_cli(["relink", "--find", "--root", str(drive), "--json"], env=env)
            self.assertEqual(js_proc.returncode, 0, js_proc.stderr)
            js_payload = json.loads(js_proc.stdout)
            self.assertEqual(js_payload["skipped_unmounted"], {unmounted_drive: 2})

    def test_30_agent_files_deduplicates_in_linear_time_without_same_file(self) -> None:
        roaming_claude = self.fake_appdata / "Claude"
        msix_claude = (
            self.fake_home
            / "AppData"
            / "Local"
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
        )
        roaming_claude.mkdir(parents=True, exist_ok=True)
        msix_claude.mkdir(parents=True, exist_ok=True)

        roaming_cfg = roaming_claude / "claude_desktop_config.json"
        msix_cfg = msix_claude / "claude_desktop_config.json"
        roaming_cfg.write_text("{}", encoding="utf-8")
        os.link(roaming_cfg, msix_cfg)

        sessions_dir = roaming_claude / "claude-code-sessions" / "acc" / "org"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        expected_sessions: list[Path] = []
        for i in range(500):
            sf = sessions_dir / f"local_{i:04d}.json"
            sf.write_text("{}", encoding="utf-8")
            expected_sessions.append(sf)

        with (
            patch.object(sys, "platform", "win32"),
            patch("smart_drive.core.relink.same_file", side_effect=AssertionError("same_file must not be called")),
            patch("smart_drive.core.root.same_file", side_effect=AssertionError("same_file must not be called")),
        ):
            files = agent_files(home=self.fake_home, app_data=self.fake_appdata)

        paths = [af.path for af in files]
        self.assertEqual(paths[0], roaming_cfg)
        self.assertNotIn(msix_cfg, paths)
        self.assertEqual(paths[1:], expected_sessions)
        self.assertEqual(len(files), 501)

    def test_31_codex_toml_escapes_survive_the_rewrite(self) -> None:
        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            "[mcp_servers.x]\n"
            r'command = "D:\\proj\\Caf\u00e9\\run.exe"' "\n"
            r'cwd = "D:\\proj\\tab\there"' "\n",
            encoding="utf-8",
        )

        relink([("D:\\proj", "D:\\03_Development_Projects\\proj")], home=self.fake_home, app_data=self.fake_appdata)

        after = codex_file.read_text(encoding="utf-8")
        self.assertIn(r'command = "D:\\03_Development_Projects\\proj\\Café\\run.exe"', after)
        # A tab cannot be part of a Windows path, so this string is not one and stays as written
        self.assertIn(r'cwd = "D:\\proj\\tab\there"', after)
        if tomllib is not None:
            server = tomllib.loads(after)["mcp_servers"]["x"]
            self.assertEqual(server["command"], "D:\\03_Development_Projects\\proj\\Café\\run.exe")
            self.assertEqual(server["cwd"], "D:\\proj\\tab\there")

    def test_32_codex_toml_never_lists_a_project_twice(self) -> None:
        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        # The moved project is already listed under its new path, in each form TOML allows. Codex stores
        # Windows paths lowercased; the move is lowercase as well so that Linux, which compares paths
        # case-sensitively, exercises the guard too.
        cases = {
            "inline table": (
                "[projects]\n"
                "'d:\\proj' = { trust_level = \"trusted\" }\n"
                "'d:\\03_development_projects\\proj' = { trust_level = \"trusted\" }\n"
            ),
            "dotted keys": (
                "projects.'d:\\proj'.trust_level = \"trusted\"\n"
                "projects.'d:\\03_development_projects\\proj'.trust_level = \"trusted\"\n"
            ),
            "other quoting": (
                "[projects.'d:\\proj']\n"
                'trust_level = "trusted"\n\n'
                '[projects."d:\\\\03_development_projects\\\\proj"]\n'
                'trust_level = "trusted"\n'
            ),
        }
        for name, content in cases.items():
            with self.subTest(name):
                codex_file.write_text(content, encoding="utf-8")
                relink(
                    [("d:\\proj", "d:\\03_development_projects\\proj")],
                    home=self.fake_home,
                    app_data=self.fake_appdata,
                )
                self.assertEqual(codex_file.read_text(encoding="utf-8"), content)

    def test_33_codex_toml_hash_inside_a_quoted_header(self) -> None:
        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            "[projects.'d:\\c#\\app1']\n"
            'trust_level = "trusted"\n\n'
            "[projects.'d:\\c#\\app2'] # second app\n"
            'trust_level = "trusted"\n',
            encoding="utf-8",
        )

        relink([("d:\\c#", "d:\\03_development_projects\\c#")], home=self.fake_home, app_data=self.fake_appdata)

        after = codex_file.read_text(encoding="utf-8")
        self.assertIn("[projects.'d:\\03_development_projects\\c#\\app1']\n", after)
        self.assertIn("[projects.'d:\\03_development_projects\\c#\\app2'] # second app\n", after)
        self.assertNotIn("'d:\\c#\\", after)

    @unittest.skipIf(tomllib is None, "tomllib needs Python 3.11")
    def test_34_codex_toml_left_unchanged_when_the_rewrite_would_be_invalid(self) -> None:
        codex_file = self.fake_home / ".codex" / "config.toml"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        # Rewriting the dotted key defines projects.'d:\03_development_projects\proj' a second time
        content = (
            "projects.'d:\\proj'.trust_level = \"trusted\"\n\n"
            "[projects.'d:\\03_development_projects\\proj']\n"
            'model = "o3"\n'
        )
        codex_file.write_text(content, encoding="utf-8")

        report = relink(
            [("d:\\proj", "d:\\03_development_projects\\proj")],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )

        self.assertEqual(codex_file.read_text(encoding="utf-8"), content)
        self.assertNotIn("Codex", report.agents)
        self.assertTrue(any("would not be valid TOML" in w for w in report.warnings), report.warnings)

    def test_35_relinking_into_a_subfolder_again_changes_nothing(self) -> None:
        drive = self.test_dir / "drive_nested"
        old, new = drive / "AI", drive / "AI" / "legacy"
        (new / "proj1").mkdir(parents=True)
        mapper = PathMapper([(str(old), str(new))])
        once = mapper.map(str(old / "proj1"))
        self.assertEqual(once, str(new / "proj1"))
        self.assertIsNone(mapper.map(str(once)))

        claude_json = self.fake_home / ".claude.json"
        claude_json.write_text(json.dumps({"projects": {str(old / "proj1"): {}}}), encoding="utf-8")
        # A manual relink is journaled, and relink tells users to run `smart-drive relink` again later
        for argv in (["relink", str(old), str(new), "--root", str(drive)], ["relink", "--root", str(drive)]):
            with patch.dict(os.environ, self._cli_env()), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main(argv), 0)
        self.assertEqual(list(json.loads(claude_json.read_text(encoding="utf-8"))["projects"]), [str(new / "proj1")])

    def test_36_composed_and_decomposed_names_are_one_folder(self) -> None:
        name = unicodedata.normalize("NFC", "Dự án web")
        old = str(self.test_dir / name)
        new = str(self.test_dir / "03_Development_Projects" / name)
        # Claude Code stores paths composed (NFC), while macOS lists exFAT names decomposed (NFD)
        claude_json = self.fake_home / ".claude.json"
        claude_json.write_text(json.dumps({"projects": {old: {}}}, ensure_ascii=False), encoding="utf-8")
        history = self.fake_home / ".claude" / "projects"
        (history / claude_slug(old)).mkdir(parents=True)

        report = relink(
            [(unicodedata.normalize("NFD", old), unicodedata.normalize("NFD", new))],
            home=self.fake_home,
            app_data=self.fake_appdata,
        )

        self.assertEqual(report.agents, {"Claude Code": 1})
        self.assertEqual(list(json.loads(claude_json.read_text(encoding="utf-8"))["projects"]), [new])
        self.assertTrue((history / claude_slug(new)).is_dir())

    def test_37_claude_slug_counts_utf16_units_like_claude_code(self) -> None:
        # Claude Code runs path.replace(/[^a-zA-Z0-9]/g, "-") in JavaScript, which works per UTF-16 unit:
        # a character outside the Basic Multilingual Plane is two units, so two dashes
        self.assertEqual(claude_slug("/Volumes/SSD/\U0001F680 Rocket"), "-Volumes-SSD----Rocket")
        self.assertEqual(claude_slug("/a/Dự án"), "-a-D---n")
        self.assertEqual(len(claude_slug("/" + "a" * 197 + "\U0001F680")), 200)
        self.assertIsNone(claude_slug("/" + "a" * 198 + "\U0001F680"))

    def test_38_json_output_stays_json_while_waiting(self) -> None:
        drive = self.create_mock_drive()
        (drive / "03_Development_Projects" / "json_app").mkdir(parents=True, exist_ok=True)
        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(json.dumps({"projects": {str(drive / "json_app"): {}}}), encoding="utf-8")

        out_buf, err_buf = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            # preview check: running; first poll: closed; the relink itself: closed
            patch("smart_drive.core.relink._running_processes", side_effect=[{"Claude.exe"}, set(), set()]),
            patch("smart_drive.cli.cmd_relink.time.sleep"),
            patch("smart_drive.cli.cmd_relink.time.monotonic", side_effect=[0.0, 2.0]),
            contextlib.redirect_stdout(out_buf),
            contextlib.redirect_stderr(err_buf),
        ):
            rc = cli_main(["relink", "--find", "--apply", "--wait", "--json", "--root", str(drive)])

        self.assertEqual(rc, 0, err_buf.getvalue())
        self.assertEqual(json.loads(out_buf.getvalue())["relink"]["agents"], {"Claude Code": 1})
        self.assertIn("Waiting for Claude Code to close", err_buf.getvalue())

    def test_39_wait_keeps_waiting_while_the_process_list_cannot_be_read(self) -> None:
        drive = self.create_mock_drive()
        old_dir, new_dir = drive / "ps_app", drive / "03_Development_Projects" / "ps_app"
        new_dir.mkdir(parents=True, exist_ok=True)
        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(json.dumps({"projects": {str(old_dir): {}}}), encoding="utf-8")

        # What ps / tasklist answers on each call: Claude runs, the listing times out, Claude has closed
        listings: list = ["claude\n", subprocess.TimeoutExpired("ps", 10), "zsh\n", "zsh\n"]
        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] not in ("ps", "tasklist"):
                return real_run(cmd, *args, **kwargs)
            answer = listings.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return subprocess.CompletedProcess(cmd, 0, stdout=answer, stderr="")

        sleeps: list[float] = []
        err_buf = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch("smart_drive.core.relink._running_processes", new=_REAL_RUNNING_PROCESSES),
            patch("smart_drive.core.relink.subprocess.run", side_effect=fake_run),
            patch("smart_drive.cli.cmd_relink.time.sleep", side_effect=sleeps.append),
            patch("smart_drive.cli.cmd_relink.time.monotonic", side_effect=[0.0, 2.0, 4.0]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err_buf),
        ):
            rc = cli_main(["relink", str(old_dir), str(new_dir), "--wait", "--root", str(drive)])

        self.assertEqual(rc, 0, err_buf.getvalue())
        # The listing that timed out did not count as "closed": relink polled again before writing
        self.assertEqual(sleeps, [2, 2])
        self.assertIn(str(new_dir), json.loads(claude_file.read_text(encoding="utf-8"))["projects"])

    def test_40_wait_skips_the_agent_that_runs_relink(self) -> None:
        # `ps -A -o pid=,ppid=,comm=` as seen from a shell of Claude Code inside Claude desktop
        tree = (
            "1 0 /sbin/launchd\n"
            "500 1 /Applications/Claude.app/Contents/MacOS/Claude\n"
            "600 500 /Users/me/Library/Application Support/Claude/claude-code/claude.app/Contents/MacOS/claude\n"
            "650 1 /Applications/Cursor.app/Contents/MacOS/Cursor\n"
            f"700 600 /bin/zsh\n{os.getpid()} 700 python3\n"
        )
        listing = subprocess.CompletedProcess([], 0, stdout=tree, stderr="")
        with (
            patch.dict(os.environ, {"CLAUDECODE": ""}),
            patch.object(sys, "platform", "darwin"),
            patch("smart_drive.core.relink.subprocess.run", return_value=listing),
        ):
            self.assertEqual(relink_core.calling_agents(), {"Claude Code", "Claude desktop"})
        with patch.dict(os.environ, {"CLAUDECODE": "1"}), patch.object(sys, "platform", "win32"):
            self.assertEqual(relink_core.calling_agents(), {"Claude Code", "Claude desktop"})

        drive = self.create_mock_drive()
        old_dir, new_dir = drive / "self_app", drive / "03_Development_Projects" / "self_app"
        new_dir.mkdir(parents=True, exist_ok=True)
        claude_file = self.fake_home / ".claude.json"
        claude_file.write_text(json.dumps({"projects": {str(old_dir): {}}}), encoding="utf-8")

        sleeps: list[float] = []
        err_buf = io.StringIO()
        with (
            patch.dict(os.environ, self._cli_env()),
            patch("smart_drive.cli.cmd_relink.calling_agents", return_value={"Claude Code", "Claude desktop"}),
            patch("smart_drive.core.relink._running_processes", return_value={"claude"}),
            patch("smart_drive.cli.cmd_relink.time.sleep", side_effect=sleeps.append),
            patch("smart_drive.cli.cmd_relink.time.monotonic", side_effect=[0.0, 1e9]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err_buf),
        ):
            rc = cli_main(["relink", str(old_dir), str(new_dir), "--wait", "--root", str(drive)])

        self.assertEqual(rc, 0, err_buf.getvalue())
        self.assertEqual(sleeps, [])
        # Only Claude Code's own file changes here, so only Claude Code is named
        self.assertIn("runs inside Claude Code, so it cannot wait for it to close", err_buf.getvalue())
        self.assertIn(str(new_dir), json.loads(claude_file.read_text(encoding="utf-8"))["projects"])

    def test_41_find_moved_mcp_server_counts_files_and_hidden_folders(self) -> None:
        drive = self.test_dir / "drive_mcp"
        new_server = drive / "02_Code" / "mcp-notes"
        (new_server / ".venv" / "bin").mkdir(parents=True)
        (new_server / "main.py").write_text("print('notes')\n", encoding="utf-8")
        old_server = drive / "mcp-notes"
        (self.fake_home / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "notes": {
                            "command": str(old_server / ".venv" / "bin" / "python"),
                            "args": [str(old_server / "main.py")],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        entries = find_moved(root=str(drive), home=self.fake_home, skip_dirs=[])

        self.assertEqual([(e.old, e.new, e.kind) for e in entries], [(str(old_server), str(new_server), "same-name")])

    def test_42_find_moved_matches_names_in_either_unicode_form(self) -> None:
        drive = self.test_dir / "drive_nfd"
        # On disk the way macOS lists exFAT names (decomposed); agents remember them composed
        new_dir = drive / "02_Code" / unicodedata.normalize("NFD", "Dự án web")
        (new_dir / unicodedata.normalize("NFD", "Tài liệu")).mkdir(parents=True)
        old_dir = unicodedata.normalize("NFC", str(drive / "Dự án web"))
        (self.fake_home / ".claude.json").write_text(
            json.dumps({"projects": {old_dir: {}, unicodedata.normalize("NFC", old_dir + os.sep + "Tài liệu"): {}}}),
            encoding="utf-8",
        )

        entries = find_moved(root=str(drive), home=self.fake_home, skip_dirs=[])

        self.assertEqual([(e.old, e.new, e.kind) for e in entries], [(old_dir, str(new_dir), "same-name")])


if __name__ == "__main__":
    unittest.main()
