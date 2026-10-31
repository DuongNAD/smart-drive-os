"""tests/test_workspace_sync.py - Unit & Integration Tests for Workspace Sync & Health/MCP.

Milestone M4 Verification Suite:
1. Test `sync_workspaces` with relative and absolute paths in `*.code-workspace`.
2. Test `.bak.smart-drive` backup creation.
3. Test `sync_roadmap_kanban` updating markdown table and link paths.
4. Test `projects --organize --apply` triggering workspace sync.
5. Test `smart-drive checkup` WIP overload and unorganized projects detection.
6. Test MCP `ssd_auto_organize` with `subzone_projects=True` and `subzone_projects=False`.

100% Python Standard Library unittest.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.cli.main import main as cli_main
from smart_drive.core.health import (
    SSDHealthReport,
    check_drive_health,
    evaluate_health_warnings,
    inspect_dev_projects_health,
)
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from smart_drive.core.workspace_sync import WorkspaceSynchronizer
from smart_drive.mcp.server import SmartDriveMCPServer
import smart_drive.core.relink as relink_module
from tests.helpers import SmartDriveTestCase


class TestWorkspaceSyncUnit(SmartDriveTestCase):
    """Unit tests for WorkspaceSynchronizer (code-workspace & ROADMAP_KANBAN.md)."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.test_dir / "mock_drive"
        self.root.mkdir()
        self.dev_dir = self.root / "03_Development_Projects"
        self.dev_dir.mkdir()

    def test_sync_workspaces_relative_path(self) -> None:
        """Updates relative paths inside *.code-workspace when projects move."""
        ws_file = self.dev_dir / "Wave1_Focus.code-workspace"
        orig_data = {
            "folders": [
                {"path": "reflex_bench"},
                {"path": "./plasmid_relay"},
                {"path": "unrelated_other"},
            ],
            "settings": {},
        }
        ws_file.write_text(json.dumps(orig_data, indent=2), encoding="utf-8")

        sync = WorkspaceSynchronizer(str(self.root))
        moves = [
            (
                str(self.dev_dir / "reflex_bench"),
                str(self.dev_dir / "01_Active_Focus" / "reflex_bench"),
            ),
            (
                str(self.dev_dir / "plasmid_relay"),
                str(self.dev_dir / "02_Wave_Incubator" / "plasmid_relay"),
            ),
        ]
        res = sync.sync_workspaces(moves)

        self.assertEqual(res["updated_count"], 1)
        self.assertIn(str(ws_file), res["updated_workspaces"])

        # Check backup was created
        backup_file = Path(str(ws_file) + ".bak.smart-drive")
        self.assertTrue(backup_file.is_file())
        self.assertEqual(backup_file.read_text(encoding="utf-8"), json.dumps(orig_data, indent=2))

        # Check updated content
        updated_data = json.loads(ws_file.read_text(encoding="utf-8"))
        paths = [f["path"] for f in updated_data["folders"]]
        self.assertEqual(paths[0], "01_Active_Focus/reflex_bench")
        self.assertEqual(paths[1], "./02_Wave_Incubator/plasmid_relay")
        self.assertEqual(paths[2], "unrelated_other")

    def test_sync_workspaces_submodule_relative_path(self) -> None:
        """Updates relative paths pointing to subdirectories of a moved project."""
        ws_file = self.dev_dir / "Project.code-workspace"
        orig_data = {
            "folders": [
                {"path": "reflex_bench/core"},
                {"path": "./reflex_bench/frontend"},
            ]
        }
        ws_file.write_text(json.dumps(orig_data, indent=2), encoding="utf-8")

        sync = WorkspaceSynchronizer(str(self.root))
        moves = [
            (
                str(self.dev_dir / "reflex_bench"),
                str(self.dev_dir / "01_Active_Focus" / "reflex_bench"),
            ),
        ]
        sync.sync_workspaces(moves)

        updated_data = json.loads(ws_file.read_text(encoding="utf-8"))
        paths = [f["path"] for f in updated_data["folders"]]
        self.assertEqual(paths[0], "01_Active_Focus/reflex_bench/core")
        self.assertEqual(paths[1], "./01_Active_Focus/reflex_bench/frontend")

    def test_sync_workspaces_absolute_path_forward_and_back_slash(self) -> None:
        """Preserves forward slash vs backslash formatting for absolute paths."""
        ws_file = self.dev_dir / "Absolute.code-workspace"

        old_p1 = str(self.dev_dir / "proj_fwd").replace("\\", "/")
        old_p2 = str(self.dev_dir / "proj_back").replace("/", "\\")

        orig_data = {
            "folders": [
                {"path": old_p1},
                {"path": old_p2},
            ]
        }
        ws_file.write_text(json.dumps(orig_data, indent=4), encoding="utf-8")

        new_p1 = str(self.dev_dir / "01_Active_Focus" / "proj_fwd")
        new_p2 = str(self.dev_dir / "04_Hackathons_Pitch" / "proj_back")

        sync = WorkspaceSynchronizer(str(self.root))
        moves = [
            (str(self.dev_dir / "proj_fwd"), new_p1),
            (str(self.dev_dir / "proj_back"), new_p2),
        ]
        res = sync.sync_workspaces(moves)
        self.assertEqual(res["updated_count"], 1)

        updated_data = json.loads(ws_file.read_text(encoding="utf-8"))
        updated_paths = [f["path"] for f in updated_data["folders"]]

        self.assertEqual(updated_paths[0], new_p1.replace("\\", "/"))
        self.assertEqual(updated_paths[1], new_p2.replace("/", "\\"))

    def test_sync_workspaces_relative_backslash_paths(self) -> None:
        """Preserves backslash formatting for relative paths across all OS platforms."""
        ws_file = self.dev_dir / "RelativeBackslash.code-workspace"
        orig_data = {
            "folders": [
                {"path": ".\\plasmid_relay"},
                {"path": "reflex_bench\\core"},
            ]
        }
        ws_file.write_text(json.dumps(orig_data, indent=2), encoding="utf-8")

        sync = WorkspaceSynchronizer(str(self.root))
        moves = [
            (
                str(self.dev_dir / "plasmid_relay"),
                str(self.dev_dir / "02_Wave_Incubator" / "plasmid_relay"),
            ),
            (
                str(self.dev_dir / "reflex_bench"),
                str(self.dev_dir / "01_Active_Focus" / "reflex_bench"),
            ),
        ]
        res = sync.sync_workspaces(moves)
        self.assertEqual(res["updated_count"], 1)

        updated_data = json.loads(ws_file.read_text(encoding="utf-8"))
        updated_paths = [f["path"] for f in updated_data["folders"]]
        self.assertEqual(updated_paths[0], ".\\02_Wave_Incubator\\plasmid_relay")
        self.assertEqual(updated_paths[1], "01_Active_Focus\\reflex_bench\\core")

    def test_no_changes_creates_no_backup(self) -> None:
        """When workspace does not reference moved folders, no write or backup occurs."""
        ws_file = self.dev_dir / "Unrelated.code-workspace"
        orig_content = json.dumps({"folders": [{"path": "other_proj"}]}, indent=2)
        ws_file.write_text(orig_content, encoding="utf-8")

        sync = WorkspaceSynchronizer(str(self.root))
        moves = [(str(self.dev_dir / "nonexistent"), str(self.dev_dir / "somewhere"))]
        res = sync.sync_workspaces(moves)

        self.assertEqual(res["updated_count"], 0)
        self.assertEqual(res["updated_workspaces"], [])
        backup_file = Path(str(ws_file) + ".bak.smart-drive")
        self.assertFalse(backup_file.exists())
        self.assertEqual(ws_file.read_text(encoding="utf-8"), orig_content)


class TestRoadmapKanbanSync(SmartDriveTestCase):
    """Unit tests for ROADMAP_KANBAN.md link and table path rewriting."""

    def setUp(self) -> None:
        super().setUp()
        self.root = self.test_dir / "mock_drive"
        self.root.mkdir()
        self.dev_dir = self.root / "03_Development_Projects"
        self.dev_dir.mkdir()

    def test_sync_roadmap_kanban_links_and_tables(self) -> None:
        """Rewrites markdown links, subpath links, table cells, and backticks in ROADMAP_KANBAN.md."""
        kanban = self.dev_dir / "ROADMAP_KANBAN.md"
        orig_text = (
            "# Project Roadmap & Kanban\n\n"
            "## Active Focus\n"
            "- [Reflex Bench](reflex_bench): Primary focus project.\n"
            "- [Reflex Docs](./reflex_bench/docs/README.md): Architecture docs.\n\n"
            "## Tracking Table\n"
            "| Project | Stage | Path | Notes |\n"
            "|---|---|---|---|\n"
            "| Reflex Bench | Active | reflex_bench | Primary benchmark |\n"
            "| Plasmid Relay | Incubator | `plasmid_relay` | Design phase |\n"
            "| Codon Lint | Dormant | `codon_lint/src` | Library |\n"
        )
        kanban.write_text(orig_text, encoding="utf-8")

        sync = WorkspaceSynchronizer(str(self.root))
        moves = [
            (
                str(self.dev_dir / "reflex_bench"),
                str(self.dev_dir / "01_Active_Focus" / "reflex_bench"),
            ),
            (
                str(self.dev_dir / "plasmid_relay"),
                str(self.dev_dir / "02_Wave_Incubator" / "plasmid_relay"),
            ),
            (
                str(self.dev_dir / "codon_lint"),
                str(self.dev_dir / "99_Archive_Dormant" / "codon_lint"),
            ),
        ]

        updated = sync.sync_roadmap_kanban(moves)
        self.assertTrue(updated)

        # Check backup file created
        backup = Path(str(kanban) + ".bak.smart-drive")
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_text(encoding="utf-8"), orig_text)

        # Verify new content
        new_text = kanban.read_text(encoding="utf-8")
        self.assertIn("[Reflex Bench](01_Active_Focus/reflex_bench)", new_text)
        self.assertIn("[Reflex Docs](./01_Active_Focus/reflex_bench/docs/README.md)", new_text)
        self.assertIn("| Reflex Bench | Active | 01_Active_Focus/reflex_bench |", new_text)
        self.assertIn("`02_Wave_Incubator/plasmid_relay`", new_text)
        self.assertIn("`99_Archive_Dormant/codon_lint/src`", new_text)

    def test_sync_roadmap_kanban_nonexistent_returns_false(self) -> None:
        """Returns False safely when ROADMAP_KANBAN.md does not exist."""
        sync = WorkspaceSynchronizer(str(self.root))
        moves = [(str(self.dev_dir / "a"), str(self.dev_dir / "b"))]
        self.assertFalse(sync.sync_roadmap_kanban(moves))


class TestCliIntegrationWorkspaceSync(SmartDriveTestCase):
    """Integration tests verifying CLI commands invoke WorkspaceSynchronizer."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_appdata.mkdir(parents=True)

        self.drive_root = self.test_dir / "mock_ssd"
        self.drive_root.mkdir()
        (self.drive_root / "AGENTS.md").write_text("# Manifest\n", encoding="utf-8")
        (self.drive_root / "GEMINI.md").write_text("# Manifest\n", encoding="utf-8")
        (self.drive_root / ".metadata_never_index").write_text("", encoding="utf-8")

        self.dev_dir = self.drive_root / "03_Development_Projects"
        self.dev_dir.mkdir()

        # Patch running processes and locations
        self._patchers = [
            patch.object(relink_module, "_running_processes", return_value=set()),
            patch.object(
                relink_module,
                "_resolve_locations",
                return_value=(self.fake_home, self.fake_appdata, False),
            ),
            patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])),
            patch("smart_drive.cli.cmd_projects.busy_apps_of", return_value=([], False)),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        super().tearDown()

    def _seed_git_repo(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        git_dir = path / ".git"
        git_dir.mkdir()
        (path / "README.md").write_text("# Project\n", encoding="utf-8")
        (path / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (path / "utils.py").write_text("def f(): pass\n", encoding="utf-8")
        (path / "core.py").write_text("class C: pass\n", encoding="utf-8")

    def test_projects_organize_apply_triggers_workspace_sync(self) -> None:
        """`smart-drive projects --organize --apply` relocates project and updates workspace file."""
        proj = self.dev_dir / "daily_app"
        self._seed_git_repo(proj)

        # Create workspace file referencing daily_app
        ws_file = self.dev_dir / "Active.code-workspace"
        ws_file.write_text(json.dumps({"folders": [{"path": "daily_app"}]}), encoding="utf-8")

        # Create ROADMAP_KANBAN.md
        kanban = self.dev_dir / "ROADMAP_KANBAN.md"
        kanban.write_text("- [daily_app](daily_app)\n", encoding="utf-8")

        # Run projects --organize --apply
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            ret = cli_main(["projects", "--root", str(self.drive_root), "--organize", "--apply"])
        self.assertEqual(ret, 0)

        # Confirm relocated to 01_Active_Focus
        target_path = self.dev_dir / "01_Active_Focus" / "daily_app"
        self.assertTrue(target_path.is_dir())
        self.assertFalse(proj.exists())

        # Confirm workspace file updated
        ws_data = json.loads(ws_file.read_text(encoding="utf-8"))
        self.assertEqual(ws_data["folders"][0]["path"], "01_Active_Focus/daily_app")

        # Confirm ROADMAP_KANBAN.md updated
        self.assertIn("[daily_app](01_Active_Focus/daily_app)", kanban.read_text(encoding="utf-8"))

    def test_stage_apply_triggers_workspace_sync(self) -> None:
        """`smart-drive stage <project> <stage> --apply` updates workspace file."""
        proj = self.dev_dir / "scaffold_tool"
        self._seed_git_repo(proj)

        ws_file = self.dev_dir / "Incubator.code-workspace"
        ws_file.write_text(json.dumps({"folders": [{"path": "scaffold_tool"}]}), encoding="utf-8")

        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            ret = cli_main(["stage", "scaffold_tool", "incubator", "--root", str(self.drive_root), "--apply"])
        self.assertEqual(ret, 0)

        target_path = self.dev_dir / "02_Wave_Incubator" / "scaffold_tool"
        self.assertTrue(target_path.is_dir())

        ws_data = json.loads(ws_file.read_text(encoding="utf-8"))
        self.assertEqual(ws_data["folders"][0]["path"], "02_Wave_Incubator/scaffold_tool")


class TestCheckupHealthWipAndUnorganized(SmartDriveTestCase):
    """Tests health checkup detection for WIP overload and loose unorganized development projects."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_home.mkdir(parents=True)
        self.fake_appdata.mkdir(parents=True)

        self.drive_root = self.test_dir / "mock_drive"
        self.drive_root.mkdir()
        (self.drive_root / "AGENTS.md").write_text("# Manifest\n", encoding="utf-8")
        (self.drive_root / "GEMINI.md").write_text("# Manifest\n", encoding="utf-8")
        (self.drive_root / ".metadata_never_index").write_text("", encoding="utf-8")

        self.dev_dir = self.drive_root / "03_Development_Projects"
        self.dev_dir.mkdir()

    def test_inspect_dev_projects_health_function(self) -> None:
        """Direct verification of inspect_dev_projects_health helper."""
        # Clean state
        unorg, wip = inspect_dev_projects_health(str(self.drive_root))
        self.assertEqual(unorg, 0)
        self.assertFalse(wip)

        # Seed 6 unorganized projects directly under 03_Development_Projects
        for i in range(1, 7):
            (self.dev_dir / f"loose_project_{i}").mkdir()

        # Seed 4 active projects under 01_Active_Focus
        active_dir = self.dev_dir / "01_Active_Focus"
        active_dir.mkdir()
        for i in range(1, 5):
            (active_dir / f"active_proj_{i}").mkdir()

        unorg, wip = inspect_dev_projects_health(str(self.drive_root))
        self.assertEqual(unorg, 6)
        self.assertTrue(wip)

    def test_checkup_reports_wip_overload_and_recommends_actions(self) -> None:
        """run_checkup detects WIP overload (> 3 active) and loose projects (> 5), recommending commands."""
        # Seed 6 unorganized projects
        for i in range(1, 7):
            p = self.dev_dir / f"proj_loose_{i}"
            p.mkdir()
            (p / "main.py").write_text("x = 1\n", encoding="utf-8")

        # Seed 4 active focus projects (> 3 WIP overload)
        active_dir = self.dev_dir / "01_Active_Focus"
        active_dir.mkdir()
        for i in range(1, 5):
            p = active_dir / f"proj_active_{i}"
            p.mkdir()
            (p / "app.py").write_text("y = 2\n", encoding="utf-8")

        reader = RegistryReader(tree={})
        report = run_checkup(
            root=str(self.drive_root),
            lang="en",
            home=self.fake_home,
            app_data=self.fake_appdata,
            reader=reader,
            scan_c_caches=False,
        )

        self.assertEqual(report["unorganized_dev_projects"], 6)
        self.assertTrue(report["wip_overload"])
        self.assertEqual(report["health"]["unorganized_dev_projects"], 6)
        self.assertTrue(report["health"]["wip_overload"])

        # Check candidate steps include recommendations
        commands = [s["command"] for s in report["next_steps"]]
        self.assertTrue(
            any("smart-drive stage" in cmd for cmd in commands),
            f"Expected smart-drive stage in next_steps, got {commands}",
        )
        self.assertTrue(
            any("smart-drive projects --organize" in cmd for cmd in commands),
            f"Expected smart-drive projects --organize in next_steps, got {commands}",
        )

    def test_evaluate_health_warnings_includes_dev_projects(self) -> None:
        """evaluate_health_warnings appends warnings for WIP overload and unorganized dev projects."""
        warnings = evaluate_health_warnings(
            free_percent=80.0,
            free_bytes=500 * (1024 ** 3),
            total_bytes=1000 * (1024 ** 3),
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
            unorganized_dev_projects=7,
            wip_overload=True,
        )
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("WIP overload" in w for w in warnings))
        self.assertTrue(any("unorganized development projects" in w for w in warnings))


class TestMcpAutoOrganizeSubzoneProjects(SmartDriveTestCase):
    """Tests MCP tool `ssd_auto_organize` with subzone_projects=True and False."""

    def setUp(self) -> None:
        super().setUp()
        self.drive_root = self.test_dir / "mcp_drive"
        self.drive_root.mkdir()
        (self.drive_root / "AGENTS.md").write_text("# Manifest\n", encoding="utf-8")
        (self.drive_root / "GEMINI.md").write_text("# Manifest\n", encoding="utf-8")
        (self.drive_root / ".metadata_never_index").write_text("", encoding="utf-8")

        self.dev_dir = self.drive_root / "03_Development_Projects"
        self.dev_dir.mkdir()

        self.server = SmartDriveMCPServer(root=str(self.drive_root), rate_limit_enabled=False)

    def _seed_project(self, name: str, files: int = 4) -> Path:
        proj = self.dev_dir / name
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "README.md").write_text("# Project\n", encoding="utf-8")
        for i in range(files):
            (proj / f"mod_{i}.py").write_text(f"# module {i}\n", encoding="utf-8")
        return proj

    def test_mcp_auto_organize_default_false_preserves_behavior(self) -> None:
        """When subzone_projects is False (default), return dict does not have subzone_projects key."""
        self._seed_project("my_project")
        res = self.server.dispatch_tool("ssd_auto_organize", {"apply": False})
        self.assertEqual(res["status"], "dry_run")
        self.assertNotIn("subzone_projects", res)

    def test_mcp_auto_organize_subzone_projects_dry_run(self) -> None:
        """When subzone_projects=True and apply=False, returns simulated subzone move plan."""
        self._seed_project("hackathon_solver")
        res = self.server.dispatch_tool(
            "ssd_auto_organize",
            {"apply": False, "subzone_projects": True},
        )
        self.assertEqual(res["status"], "dry_run")
        self.assertIn("subzone_projects", res)
        sub_info = res["subzone_projects"]
        self.assertEqual(sub_info["status"], "dry_run")
        self.assertEqual(sub_info["planned_count"], 1)
        self.assertEqual(sub_info["planned_moves"][0]["project"], "hackathon_solver")
        self.assertEqual(sub_info["planned_moves"][0]["target_zone"], "04_Hackathons_Pitch")

    def test_mcp_auto_organize_subzone_projects_apply(self) -> None:
        """When subzone_projects=True and apply=True, moves projects and updates workspaces."""
        proj = self._seed_project("hackathon_solver")
        ws_file = self.dev_dir / "Team.code-workspace"
        ws_file.write_text(json.dumps({"folders": [{"path": "hackathon_solver"}]}), encoding="utf-8")

        res = self.server.dispatch_tool(
            "ssd_auto_organize",
            {"apply": True, "subzone_projects": True},
        )
        self.assertEqual(res["status"], "applied")
        self.assertIn("subzone_projects", res)
        sub_info = res["subzone_projects"]
        self.assertEqual(sub_info["status"], "applied")
        self.assertEqual(sub_info["moved_count"], 1)

        # Confirm directory moved
        target = self.dev_dir / "04_Hackathons_Pitch" / "hackathon_solver"
        self.assertTrue(target.is_dir())
        self.assertFalse(proj.exists())

        # Confirm workspace updated
        ws_data = json.loads(ws_file.read_text(encoding="utf-8"))
        self.assertEqual(ws_data["folders"][0]["path"], "04_Hackathons_Pitch/hackathon_solver")


if __name__ == "__main__":
    unittest.main()
