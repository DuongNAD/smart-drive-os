"""tests/test_relink.py - Unit and E2E tests for smart_drive.core.relink and smart_drive.cli.cmd_relink.

100% isolated: every test uses a fake home and fake AppData inside a temporary directory and never
reads or writes real coding agent configuration files on the host machine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.relink import (
    BACKUP_SUFFIX,
    PathMapper,
    agent_files,
    claude_slug,
    journal_path,
    last_moves,
    record_moves,
    relink,
)
from tests.helpers import SmartDriveTestCase, run_smart_drive_cli


class TestRelink(SmartDriveTestCase):
    """Comprehensive tests for PathMapper, agent config relinking, history, git worktrees, venvs, and CLI."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.fake_appdata.mkdir(parents=True, exist_ok=True)

    def _cli_env(self) -> dict[str, str]:
        return {
            "USERPROFILE": str(self.fake_home),
            "HOME": str(self.fake_home),
            "APPDATA": str(self.fake_appdata),
            "XDG_CONFIG_HOME": str(self.fake_appdata),
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


if __name__ == "__main__":
    unittest.main()
