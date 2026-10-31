"""tests/test_cmd_projects.py - Unit & Integration Tests for `projects` and `stage` CLI.

100% Python Standard Library unittest.
Tests:
- Read-only table output and --json format for `smart-drive projects`.
- WIP count calculation in 01_Active_Focus and warning banner when WIP > 3.
- `smart-drive projects --organize` dry-run writes ZERO bytes to disk.
- `smart-drive projects --organize --apply` relocates projects to subzones and relinks.
- `smart-drive undo --apply` reverts moves journaled by `projects --organize`!
- `smart-drive stage <project> <stage>` validation, `.smart_drive_stage` writer, and `--apply` move.
- `smart-drive undo --apply` reverts moves journaled by `stage`!
- Isolated hermetic testing: mock environment, zero disk leakage.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.project_classifier import LIFECYCLE_ZONES
from smart_drive.core.relink import journal_path, last_undoable_moves
from smart_drive.core.system_refs import SystemRef, SystemRefIndex
from tests.helpers import SmartDriveTestCase, snapshot_tree, tree_changes

# Module references for patching
import smart_drive.core.relink as relink_module
import smart_drive.cli.cmd_projects as cmd_projects_module


class _IsolatedProjectsTestCase(SmartDriveTestCase):
    """Hermetic test harness for projects & stage CLI commands."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_appdata.mkdir(parents=True)

        # Drive root with anchor manifests
        self.drive_root = self.test_dir / "mock_ssd"
        self.drive_root.mkdir()
        (self.drive_root / "AGENTS.md").write_text("# Agents Manifest\n", encoding="utf-8")
        (self.drive_root / "GEMINI.md").write_text("# Gemini Manifest\n", encoding="utf-8")
        (self.drive_root / ".metadata_never_index").write_text("", encoding="utf-8")

        self.dev_dir = self.drive_root / "03_Development_Projects"
        self.dev_dir.mkdir()

        # Patch running processes, locations and system reference collection
        self._patchers = [
            patch.object(relink_module, "_running_processes", return_value=set()),
            patch.object(
                relink_module,
                "_resolve_locations",
                return_value=(self.fake_home, self.fake_appdata, False),
            ),
            patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patchers):
            p.stop()
        super().tearDown()

    def run_cli(self, *argv: str, expected_codes: tuple[int, ...] = (0,)) -> tuple[int, str, str]:
        """Runs cli_main in-process with captured stdout and stderr."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(list(argv))
        self.assertIn(code, expected_codes, f"Command {argv} failed with code {code}: {err.getvalue()}")
        return code, out.getvalue(), err.getvalue()

    def create_mock_project(
        self,
        name: str,
        files: dict[str, str],
        subzone: str | None = None,
        mtime_offset_days: float = 0.0,
    ) -> Path:
        """Creates a mock project structure with optional subzone placement and file timestamps."""
        if subzone:
            zone_dir = self.dev_dir / subzone
            zone_dir.mkdir(parents=True, exist_ok=True)
            proj_dir = zone_dir / name
        else:
            proj_dir = self.dev_dir / name

        proj_dir.mkdir(parents=True, exist_ok=True)
        file_mtime = time.time() - (mtime_offset_days * 86400.0)

        for rel_path, content in files.items():
            f_path = proj_dir / rel_path
            f_path.parent.mkdir(parents=True, exist_ok=True)
            f_path.write_text(content, encoding="utf-8")
            os.utime(str(f_path), (file_mtime, file_mtime))

        os.utime(str(proj_dir), (file_mtime, file_mtime))
        return proj_dir


# ==============================================================================
# 1. READ-ONLY INSPECTION & JSON TESTS
# ==============================================================================

class TestProjectsInspectionTableAndJson(_IsolatedProjectsTestCase):
    """Verifies `smart-drive projects` table and JSON outputs."""

    def test_empty_dev_projects_directory(self) -> None:
        code, out, err = self.run_cli("projects", "--root", str(self.drive_root))
        self.assertEqual(code, 0)
        self.assertIn("No development projects found in 03_Development_Projects", out)

    def test_table_output_columns_and_formatting(self) -> None:
        # Create active project (> 2 source files and recent mtime)
        self.create_mock_project(
            "active_alpha",
            {
                "main.py": "print('alpha')\n",
                "utils.py": "def u(): pass\n",
                "models.py": "class M: pass\n",
                "README.md": "# Alpha\n",
            },
            mtime_offset_days=1.0,
        )
        # Create hackathon project
        self.create_mock_project(
            "hackathon_beta",
            {"solve.py": "print('solve')\n", "README.md": "# Beta\n"},
            mtime_offset_days=5.0,
        )

        code, out, err = self.run_cli("projects", "--root", str(self.drive_root))
        self.assertEqual(code, 0)

        # Check required columns are present in table header
        self.assertIn("Project", out)
        self.assertIn("Current Zone", out)
        self.assertIn("Suggested Zone", out)
        self.assertIn("Last Active", out)
        self.assertIn("Git Status", out)
        self.assertIn("Size (Slack)", out)
        self.assertIn("Referenced By", out)

        # Check project rows
        self.assertIn("active_alpha", out)
        self.assertIn("hackathon_beta", out)
        self.assertIn("01_Active_Focus", out)
        self.assertIn("04_Hackathons_Pitch", out)

    def test_json_output_structure(self) -> None:
        self.create_mock_project(
            "bionexus_core",
            {
                "main.py": "print('bionexus')\n",
                "utils.py": "def u(): pass\n",
                "models.py": "class B: pass\n",
                "README.md": "# Bio\n",
            },
            mtime_offset_days=2.0,
        )

        code, out, err = self.run_cli("projects", "--root", str(self.drive_root), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)

        self.assertEqual(data["status"], "ok")
        self.assertIn("projects", data)
        self.assertEqual(data["total_projects"], 1)
        self.assertEqual(data["wip_count"], 1)
        self.assertFalse(data["wip_overload"])

        proj = data["projects"][0]
        self.assertEqual(proj["project"], "bionexus_core")
        self.assertEqual(proj["suggested_zone"], "01_Active_Focus")
        self.assertIn("last_active", proj)
        self.assertIn("git_status", proj)
        self.assertIn("size_slack", proj)
        self.assertIn("referenced_by", proj)


# ==============================================================================
# 2. WIP CALCULATION & OVERLOAD BANNER TESTS
# ==============================================================================

class TestProjectsWipOverloadBanner(_IsolatedProjectsTestCase):
    """Verifies WIP calculation and warning banner when active projects > 3."""

    def test_no_warning_when_wip_under_limit(self) -> None:
        # Create 2 active projects (<= 3)
        self.create_mock_project("proj_1", {"main.py": "p1"}, mtime_offset_days=1.0)
        self.create_mock_project("proj_2", {"main.py": "p2"}, mtime_offset_days=2.0)

        code, out, err = self.run_cli("projects", "--root", str(self.drive_root))
        self.assertEqual(code, 0)
        self.assertNotIn("WARNING: WIP OVERLOAD", out)
        self.assertIn("Active Focus WIP: 2/3", out)

    def test_warning_banner_emitted_when_wip_exceeds_3(self) -> None:
        # Create 4 active projects (> 3)
        self.create_mock_project("proj_1", {"main.py": "p1"}, mtime_offset_days=1.0)
        self.create_mock_project("proj_2", {"main.py": "p2"}, mtime_offset_days=2.0)
        self.create_mock_project("proj_3", {"main.py": "p3"}, mtime_offset_days=3.0)
        self.create_mock_project("proj_4", {"main.py": "p4"}, mtime_offset_days=4.0)

        code, out, err = self.run_cli("projects", "--root", str(self.drive_root))
        self.assertEqual(code, 0)
        self.assertIn("WARNING: WIP OVERLOAD IN 01_Active_Focus!", out)
        self.assertIn("Current WIP: 4 > Limit: 3", out)
        self.assertIn("smart-drive stage <project_name> incubator --apply", out)

    def test_json_reports_wip_overload_flag(self) -> None:
        for i in range(5):
            self.create_mock_project(f"active_{i}", {"app.py": f"print({i})"}, mtime_offset_days=1.0)

        code, out, err = self.run_cli("projects", "--root", str(self.drive_root), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)

        self.assertEqual(data["wip_count"], 5)
        self.assertTrue(data["wip_overload"])


# ==============================================================================
# 3. DRY-RUN SAFEGUARD (WRITES ZERO BYTES)
# ==============================================================================

class TestProjectsOrganizeDryRunWritesNothing(_IsolatedProjectsTestCase):
    """Verifies that `smart-drive projects --organize` dry-run writes 0 bytes."""

    def test_dry_run_leaves_drive_byte_for_byte_identical(self) -> None:
        # Create loose unorganized projects
        self.create_mock_project("active_proj", {"main.py": "a"}, mtime_offset_days=1.0)
        self.create_mock_project("hackathon_proj", {"main.py": "h"}, mtime_offset_days=5.0)
        self.create_mock_project("archive_dormant_proj", {"main.py": "d"}, mtime_offset_days=90.0)

        before = snapshot_tree(self.drive_root)

        # Run organize dry-run (default without --apply)
        code, out, err = self.run_cli("projects", "--root", str(self.drive_root), "--organize")
        self.assertEqual(code, 0)
        self.assertIn("Project Sub-Zoning Plan (Dry-Run Preview)", out)
        self.assertIn("Total planned relocations: 3 projects", out)

        after = snapshot_tree(self.drive_root)
        self.assertEqual(tree_changes(before, after), ([], [], []), "Dry run modified disk files!")

    def test_dry_run_json_output_writes_nothing(self) -> None:
        self.create_mock_project("pitch_deck", {"slide.py": "s"}, mtime_offset_days=2.0)

        before = snapshot_tree(self.drive_root)
        code, out, err = self.run_cli(
            "projects", "--root", str(self.drive_root), "--organize", "--json"
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["status"], "dry_run")
        self.assertEqual(len(data["planned_moves"]), 1)

        after = snapshot_tree(self.drive_root)
        self.assertEqual(tree_changes(before, after), ([], [], []))


# ==============================================================================
# 4. MOVE EXECUTION & RELINKING TESTS (`projects --organize --apply`)
# ==============================================================================

class TestProjectsOrganizeApplyExecution(_IsolatedProjectsTestCase):
    """Verifies project sub-zoning move execution, journaling, and relinking."""

    def test_organize_apply_relocates_to_canonical_subzones(self) -> None:
        # 1. Active Focus project
        self.create_mock_project("reflex_bench", {"main.py": "code"}, mtime_offset_days=1.0)
        # 2. Incubator project (scaffold <= 2 source files, older than 14d)
        self.create_mock_project(
            "plasmid_relay",
            {"README.md": "# Relay", "package.json": "{}", "index.ts": "export {}"},
            mtime_offset_days=20.0,
        )
        # 3. Hackathon project
        self.create_mock_project("hackerrank_orchestrate", {"solve.py": "code"}, mtime_offset_days=5.0)
        # 4. Dormant project (90 days old)
        self.create_mock_project("dormant_legacy", {"app.py": "code"}, mtime_offset_days=90.0)

        code, out, err = self.run_cli(
            "projects", "--root", str(self.drive_root), "--organize", "--apply"
        )
        self.assertEqual(code, 0)
        self.assertIn("Project sub-zoning applied successfully", out)
        self.assertIn("Moved projects: 4", out)

        # Verify filesystem paths after moves
        self.assertTrue((self.dev_dir / "01_Active_Focus" / "reflex_bench" / "main.py").exists())
        self.assertTrue((self.dev_dir / "02_Wave_Incubator" / "plasmid_relay" / "index.ts").exists())
        self.assertTrue((self.dev_dir / "04_Hackathons_Pitch" / "hackerrank_orchestrate" / "solve.py").exists())
        self.assertTrue((self.dev_dir / "99_Archive_Dormant" / "dormant_legacy" / "app.py").exists())

        # Verify old flat folders no longer exist
        self.assertFalse((self.dev_dir / "reflex_bench").exists())
        self.assertFalse((self.dev_dir / "plasmid_relay").exists())
        self.assertFalse((self.dev_dir / "hackerrank_orchestrate").exists())
        self.assertFalse((self.dev_dir / "dormant_legacy").exists())

        # Verify moves journal was written with tool="projects"
        journal_file = journal_path(str(self.drive_root))
        self.assertTrue(os.path.isfile(journal_file))
        batch = last_undoable_moves(str(self.drive_root))
        self.assertIsNotNone(batch)
        self.assertEqual(batch.get("tool"), "projects")
        self.assertEqual(len(batch.get("moves", [])), 4)

    def test_pinned_mcp_and_system_projects_are_not_moved(self) -> None:
        # Create regular project
        self.create_mock_project("regular_proj", {"main.py": "reg"}, mtime_offset_days=2.0)

        # Create MCP server project
        mcp_proj = self.create_mock_project("polaris_mcp", {"server.py": "mcp"}, mtime_offset_days=50.0)

        # Pin via mock mcp config
        mcp_config = self.fake_home / ".gemini" / "antigravity" / "mcp_config.json"
        mcp_config.parent.mkdir(parents=True, exist_ok=True)
        mcp_config.write_text(json.dumps({
            "mcpServers": {
                "polaris": {
                    "command": "python",
                    "args": [str(mcp_proj / "server.py")],
                }
            }
        }), encoding="utf-8")

        code, out, err = self.run_cli(
            "projects", "--root", str(self.drive_root), "--organize", "--apply"
        )
        self.assertEqual(code, 0)

        # regular_proj moved to 01_Active_Focus
        self.assertTrue((self.dev_dir / "01_Active_Focus" / "regular_proj").exists())

        # polaris_mcp pinned and remained at original location
        self.assertTrue((self.dev_dir / "polaris_mcp").exists())


# ==============================================================================
# 5. UNDO RESTORATION TESTS (`undo --apply` reversibility)
# ==============================================================================

class TestProjectsUndoReversibility(_IsolatedProjectsTestCase):
    """Verifies that `smart-drive undo --apply` completely restores projects moved by organize."""

    def test_undo_restores_all_relocated_projects_to_original_paths(self) -> None:
        p1 = self.create_mock_project("project_one", {"code.py": "1"}, mtime_offset_days=1.0)
        p2 = self.create_mock_project("hackathon_two", {"solve.py": "2"}, mtime_offset_days=4.0)

        initial_state = snapshot_tree(self.dev_dir)

        # 1. Execute organize --apply
        code, out, err = self.run_cli(
            "projects", "--root", str(self.drive_root), "--organize", "--apply"
        )
        self.assertEqual(code, 0)
        self.assertTrue((self.dev_dir / "01_Active_Focus" / "project_one").exists())
        self.assertTrue((self.dev_dir / "04_Hackathons_Pitch" / "hackathon_two").exists())

        # 2. Execute undo --apply
        code_undo, out_undo, err_undo = self.run_cli("undo", "--root", str(self.drive_root), "--apply")
        self.assertEqual(code_undo, 0)
        self.assertIn("Undo applied", out_undo)
        self.assertIn("Restored items: 2", out_undo)

        # 3. Verify folders restored to original locations
        self.assertTrue(p1.exists())
        self.assertTrue(p2.exists())
        self.assertTrue((p1 / "code.py").exists())
        self.assertTrue((p2 / "solve.py").exists())

        # Lifecycle subzone folders are empty of projects
        self.assertFalse((self.dev_dir / "01_Active_Focus" / "project_one").exists())
        self.assertFalse((self.dev_dir / "04_Hackathons_Pitch" / "hackathon_two").exists())


# ==============================================================================
# 6. LIFECYCLE STAGE COMMAND TESTS (`smart-drive stage`)
# ==============================================================================

class TestCmdStage(_IsolatedProjectsTestCase):
    """Verifies `smart-drive stage <project_name> <stage>` functionality."""

    def test_invalid_stage_name_returns_error_code_1(self) -> None:
        self.create_mock_project("my_proj", {"main.py": "code"})
        code, out, err = self.run_cli(
            "stage", "my_proj", "invalid_stage", "--root", str(self.drive_root), expected_codes=(1,)
        )
        self.assertEqual(code, 1)
        self.assertIn("Invalid stage 'invalid_stage'", err)

    def test_unknown_project_name_returns_error_code_1(self) -> None:
        code, out, err = self.run_cli(
            "stage", "non_existent_proj", "active", "--root", str(self.drive_root), expected_codes=(1,)
        )
        self.assertEqual(code, 1)
        self.assertIn("Project 'non_existent_proj' not found", err)

    def test_stage_dry_run_writes_zero_bytes(self) -> None:
        proj = self.create_mock_project("my_app", {"main.py": "code"})
        before = snapshot_tree(self.drive_root)

        code, out, err = self.run_cli(
            "stage", "my_app", "incubator", "--root", str(self.drive_root), "--dry-run"
        )
        self.assertEqual(code, 0)
        self.assertIn("would be staged to 'incubator'", out)

        after = snapshot_tree(self.drive_root)
        self.assertEqual(tree_changes(before, after), ([], [], []))

    def test_stage_without_apply_writes_marker_only(self) -> None:
        proj = self.create_mock_project("my_app", {"main.py": "code"})
        code, out, err = self.run_cli(
            "stage", "my_app", "incubator", "--root", str(self.drive_root)
        )
        self.assertEqual(code, 0)
        self.assertIn("stage marked as 'incubator'", out)

        # Marker written inside original folder
        marker = proj / ".smart_drive_stage"
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), "incubator")

        # Folder was NOT moved
        self.assertTrue(proj.exists())
        self.assertFalse((self.dev_dir / "02_Wave_Incubator" / "my_app").exists())

    def test_stage_with_apply_writes_marker_and_moves_folder(self) -> None:
        proj = self.create_mock_project("synapse_rocq", {"main.py": "print('synapse')"})
        code, out, err = self.run_cli(
            "stage", "synapse_rocq", "active", "--root", str(self.drive_root), "--apply"
        )
        self.assertEqual(code, 0)
        self.assertIn("staged to 'active' (01_Active_Focus)", out)

        # Folder moved to target zone
        new_loc = self.dev_dir / "01_Active_Focus" / "synapse_rocq"
        self.assertTrue(new_loc.exists())
        self.assertFalse(proj.exists())

        # Marker written inside moved folder
        marker = new_loc / ".smart_drive_stage"
        self.assertTrue(marker.exists())
        self.assertEqual(marker.read_text(encoding="utf-8").strip(), "active")

        # Moves journaled with tool="stage"
        batch = last_undoable_moves(str(self.drive_root))
        self.assertIsNotNone(batch)
        self.assertEqual(batch.get("tool"), "stage")

    def test_stage_apply_is_reversible_via_undo(self) -> None:
        proj = self.create_mock_project("tcell_sieve", {"app.py": "tcell"})

        # Move to archive via stage --apply
        code, out, err = self.run_cli(
            "stage", "tcell_sieve", "archive", "--root", str(self.drive_root), "--apply"
        )
        self.assertEqual(code, 0)
        self.assertTrue((self.dev_dir / "99_Archive_Dormant" / "tcell_sieve").exists())

        # Revert via undo --apply
        code_undo, out_undo, err_undo = self.run_cli(
            "undo", "--root", str(self.drive_root), "--apply"
        )
        self.assertEqual(code_undo, 0)

        # Project restored to original location
        self.assertTrue(proj.exists())
        self.assertTrue((proj / "app.py").exists())
        self.assertFalse((self.dev_dir / "99_Archive_Dormant" / "tcell_sieve").exists())

    def test_stage_aliases_map_correctly(self) -> None:
        proj = self.create_mock_project("alias_proj", {"main.py": "test"})
        aliases = [
            ("focus", "active", "01_Active_Focus"),
            ("wave", "incubator", "02_Wave_Incubator"),
            ("agent", "mcp", "03_MCP_Agents"),
            ("pitch", "hackathon", "04_Hackathons_Pitch"),
            ("dormant", "archive", "99_Archive_Dormant"),
        ]
        for alias_input, expected_canonical, expected_zone in aliases:
            with self.subTest(alias=alias_input):
                code, out, err = self.run_cli(
                    "stage", "alias_proj", alias_input, "--root", str(self.drive_root), "--apply"
                )
                self.assertEqual(code, 0)
                self.assertIn(f"staged to '{expected_canonical}' ({expected_zone})", out)
                # Verify project folder is now in expected subzone
                curr_loc = self.dev_dir / expected_zone / "alias_proj"
                self.assertTrue(curr_loc.exists())

    def test_stage_subzone_to_subzone_transition(self) -> None:
        # Start project in incubator
        proj = self.create_mock_project("trans_proj", {"main.py": "t"}, subzone="02_Wave_Incubator")
        self.assertTrue(proj.exists())

        # Stage directly to active
        code, out, err = self.run_cli(
            "stage", "trans_proj", "active", "--root", str(self.drive_root), "--apply"
        )
        self.assertEqual(code, 0)
        self.assertFalse((self.dev_dir / "02_Wave_Incubator" / "trans_proj").exists())
        self.assertTrue((self.dev_dir / "01_Active_Focus" / "trans_proj").exists())

    def test_stage_already_in_target_subzone(self) -> None:
        proj = self.create_mock_project("already_focus", {"main.py": "af"}, subzone="01_Active_Focus")
        code, out, err = self.run_cli(
            "stage", "already_focus", "active", "--root", str(self.drive_root), "--apply"
        )
        self.assertEqual(code, 0)
        self.assertTrue((self.dev_dir / "01_Active_Focus" / "already_focus").exists())
        self.assertTrue((self.dev_dir / "01_Active_Focus" / "already_focus" / ".smart_drive_stage").exists())

    def test_stage_json_output_mode(self) -> None:
        self.create_mock_project("json_stage_proj", {"app.py": "jsp"})
        code, out, err = self.run_cli(
            "stage", "json_stage_proj", "mcp", "--root", str(self.drive_root), "--apply", "--json"
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["status"], "applied")
        self.assertEqual(data["project"], "json_stage_proj")
        self.assertEqual(data["stage"], "mcp")
        self.assertEqual(data["target_zone"], "03_MCP_Agents")
        self.assertTrue(data["marker_written"])
        self.assertTrue(data["moved"])


class TestProjectsSubprocessAndCliIntegration(_IsolatedProjectsTestCase):
    """Integration tests running smart_drive CLI commands via subprocess."""

    def test_projects_cli_help(self) -> None:
        from tests.helpers import run_smart_drive_cli
        proc = run_smart_drive_cli(["projects", "--help"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--organize", proc.stdout)
        self.assertIn("--apply", proc.stdout)
        self.assertIn("--wait", proc.stdout)

    def test_stage_cli_help(self) -> None:
        from tests.helpers import run_smart_drive_cli
        proc = run_smart_drive_cli(["stage", "--help"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("project_name", proc.stdout)
        self.assertIn("stage", proc.stdout)
        self.assertIn("--apply", proc.stdout)

    def test_projects_cli_subprocess_json(self) -> None:
        from tests.helpers import run_smart_drive_cli
        self.create_mock_project(
            "subproc_proj",
            {"main.py": "1\n", "u.py": "2\n", "m.py": "3\n"},
            mtime_offset_days=1.0,
        )
        proc = run_smart_drive_cli(["projects", "--root", str(self.drive_root), "--json"])
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["total_projects"], 1)


if __name__ == "__main__":
    unittest.main()
