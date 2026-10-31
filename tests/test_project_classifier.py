"""tests/test_project_classifier.py - Comprehensive Unit Tests for ProjectClassifier Engine.

100% Python Standard Library unittest.
Tests:
- All 5 canonical lifecycle zones (01_Active_Focus .. 99_Archive_Dormant).
- Precedence hierarchy: Rules 0 through 5.
- Explicit markers (.smart_drive_stage and .project.json overrides).
- MCP server infrastructure detection and protected_by_mcp = True pinning.
- System reference protection (protected_by_system) via SystemRefIndex.
- Hackathon and pitch keyword token matching.
- Incubator scaffolding threshold (<= 2 executable source files vs > 2).
- Git activity recency & uncommitted changes (<= 14d or dirty) vs dormant (>= 60d clean).
- Inviolable lifecycle zone roots are never treated as movable projects.
- WIP count calculation in 01_Active_Focus.
- Hermetic tests: temporary directories and isolated mock/empty SystemRefIndex.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.project_classifier import (
    ProjectClassifier,
    ProjectZoneResult,
    LIFECYCLE_ZONES,
    STAGE_NAME_TO_ZONE,
    ZONE_TO_STAGE_NAME,
    HACKATHON_KEYWORDS,
    EXECUTABLE_SOURCE_EXTENSIONS,
    LIFECYCLE_ZONE_ROOTS,
)
from smart_drive.core.system_refs import SystemRef, SystemRefIndex
from tests.helpers import SmartDriveTestCase


class _IsolatedClassifierTestCase(SmartDriveTestCase):
    """Base test case ensuring unit tests never scan the host Registry/Start Menu/Desktop."""

    def setUp(self) -> None:
        super().setUp()
        self._collect_patcher = patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect",
            side_effect=AssertionError(
                "SystemRefIndex.collect() must not be called in unit tests; inject system_refs=SystemRefIndex([])"
            ),
        )
        self._collect_patcher.start()
        self.empty_sys_refs = SystemRefIndex([])

    def tearDown(self) -> None:
        self._collect_patcher.stop()
        super().tearDown()

    def make_classifier(
        self,
        mcp_paths: set[str] | None = None,
        system_refs: SystemRefIndex | None = None,
    ) -> ProjectClassifier:
        """Helper to create an isolated ProjectClassifier instance."""
        return ProjectClassifier(
            root=str(self.test_dir),
            system_refs=system_refs if system_refs is not None else self.empty_sys_refs,
            mcp_paths=mcp_paths if mcp_paths is not None else set(),
        )


class TestLifecycleZonesDefinition(_IsolatedClassifierTestCase):
    """Verifies canonical zone constants and ProjectZoneResult data contract."""

    def test_canonical_zones_exist(self) -> None:
        self.assertEqual(len(LIFECYCLE_ZONES), 5)
        self.assertIn("01_Active_Focus", LIFECYCLE_ZONES)
        self.assertIn("02_Wave_Incubator", LIFECYCLE_ZONES)
        self.assertIn("03_MCP_Agents", LIFECYCLE_ZONES)
        self.assertIn("04_Hackathons_Pitch", LIFECYCLE_ZONES)
        self.assertIn("99_Archive_Dormant", LIFECYCLE_ZONES)

    def test_project_zone_result_contract(self) -> None:
        res = ProjectZoneResult(
            project_name="my_proj",
            project_path="/mock/my_proj",
            current_zone=None,
            suggested_zone="01_Active_Focus",
            reason="Active project (activity within 14d)",
            is_explicit=False,
            protected_by_mcp=False,
            protected_by_system=False,
            last_activity_time=1700000000.0,
            age_days=3.5,
            git_status={"clean": True, "changes": 0, "unpushed": 0, "summary": "clean"},
            source_file_count=10,
        )
        self.assertEqual(res.project_name, "my_proj")
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertEqual(res.last_active_time, 1700000000.0)
        d = res.to_dict()
        self.assertEqual(d["project_name"], "my_proj")
        self.assertEqual(d["suggested_zone"], "01_Active_Focus")
        self.assertEqual(d["age_days"], 3.5)
        self.assertTrue(d["git_status"]["clean"])


class TestExplicitMarkers(_IsolatedClassifierTestCase):
    """Rule 1: Explicit user stage markers in .smart_drive_stage or .project.json."""

    def test_smart_drive_stage_active(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "my_active_proj"
        proj.mkdir()
        (proj / ".smart_drive_stage").write_text("active\n", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertTrue(res.is_explicit)
        self.assertEqual(res.reason, "Explicit project stage marker")

    def test_smart_drive_stage_all_standard_words(self) -> None:
        classifier = self.make_classifier()
        mappings = [
            ("active", "01_Active_Focus"),
            ("focus", "01_Active_Focus"),
            ("incubator", "02_Wave_Incubator"),
            ("wave", "02_Wave_Incubator"),
            ("draft", "02_Wave_Incubator"),
            ("mcp", "03_MCP_Agents"),
            ("agent", "03_MCP_Agents"),
            ("agents", "03_MCP_Agents"),
            ("hackathon", "04_Hackathons_Pitch"),
            ("pitch", "04_Hackathons_Pitch"),
            ("pitching", "04_Hackathons_Pitch"),
            ("contest", "04_Hackathons_Pitch"),
            ("archive", "99_Archive_Dormant"),
            ("dormant", "99_Archive_Dormant"),
            ("01_Active_Focus", "01_Active_Focus"),
            ("02_Wave_Incubator", "02_Wave_Incubator"),
        ]
        for word, expected_zone in mappings:
            proj = self.test_dir / f"proj_{word}"
            proj.mkdir()
            (proj / ".smart_drive_stage").write_text(f"  {word.upper()}  \n", encoding="utf-8")
            res = classifier.classify_project(str(proj))
            self.assertEqual(res.suggested_zone, expected_zone, f"Failed for stage word '{word}'")
            self.assertTrue(res.is_explicit)

    def test_project_json_stage_and_wave(self) -> None:
        classifier = self.make_classifier()

        # {"stage": "incubator"}
        p1 = self.test_dir / "p1_incubator"
        p1.mkdir()
        (p1 / ".project.json").write_text(json.dumps({"stage": "incubator"}), encoding="utf-8")
        res1 = classifier.classify_project(str(p1))
        self.assertEqual(res1.suggested_zone, "02_Wave_Incubator")
        self.assertTrue(res1.is_explicit)

        # {"wave": 1} -> 01_Active_Focus
        p2 = self.test_dir / "p2_wave1"
        p2.mkdir()
        (p2 / ".project.json").write_text(json.dumps({"wave": 1}), encoding="utf-8")
        res2 = classifier.classify_project(str(p2))
        self.assertEqual(res2.suggested_zone, "01_Active_Focus")
        self.assertTrue(res2.is_explicit)

        # {"wave": 2} -> 02_Wave_Incubator
        p3 = self.test_dir / "p3_wave2"
        p3.mkdir()
        (p3 / ".project.json").write_text(json.dumps({"wave": 2}), encoding="utf-8")
        res3 = classifier.classify_project(str(p3))
        self.assertEqual(res3.suggested_zone, "02_Wave_Incubator")
        self.assertTrue(res3.is_explicit)

    def test_smart_drive_stage_takes_precedence_over_project_json(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "conflict_proj"
        proj.mkdir()
        (proj / ".smart_drive_stage").write_text("active\n", encoding="utf-8")
        (proj / ".project.json").write_text(json.dumps({"stage": "archive"}), encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertTrue(res.is_explicit)

    def test_invalid_stage_marker_falls_through(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "invalid_stage_proj"
        proj.mkdir()
        (proj / ".smart_drive_stage").write_text("nonexistent_stage_foo\n", encoding="utf-8")
        # Give it a hackathon name so Rule 3 picks it up
        proj_renamed = self.test_dir / "hackathon_fallback"
        proj.rename(proj_renamed)

        res = classifier.classify_project(str(proj_renamed))
        self.assertEqual(res.suggested_zone, "04_Hackathons_Pitch")
        self.assertFalse(res.is_explicit)


class TestMCPServerProtection(_IsolatedClassifierTestCase):
    """Rule 2: MCP server infrastructure protection (03_MCP_Agents & protected_by_mcp = True)."""

    def test_direct_mcp_server_directory(self) -> None:
        proj = self.test_dir / "my_mcp_server"
        proj.mkdir()
        (proj / "server.py").write_text("print('mcp')", encoding="utf-8")

        classifier = self.make_classifier(mcp_paths={str(proj)})
        res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "03_MCP_Agents")
        self.assertTrue(res.protected_by_mcp)
        self.assertEqual(res.reason, "Active MCP server infrastructure (protected_by_mcp)")

    def test_direct_subfolder_mcp_server(self) -> None:
        proj = self.test_dir / "ai_agents"
        polaris = proj / "polaris_mcp"
        polaris.mkdir(parents=True)
        (polaris / "index.js").write_text("console.log('polaris')", encoding="utf-8")

        classifier = self.make_classifier(mcp_paths={str(polaris)})
        res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "03_MCP_Agents")
        self.assertTrue(res.protected_by_mcp)

    def test_mcp_protection_prevents_dormant_archiving(self) -> None:
        """Edge Case 3: MCP server with old files and clean git must not be archived."""
        proj = self.test_dir / "polaris_service"
        proj.mkdir()
        src = proj / "main.py"
        src.write_text("print('polaris')", encoding="utf-8")

        # Set modification time to 120 days ago
        old_time = time.time() - (120 * 86400)
        os.utime(src, (old_time, old_time))
        os.utime(proj, (old_time, old_time))

        classifier = self.make_classifier(mcp_paths={str(proj)})
        res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "03_MCP_Agents")
        self.assertTrue(res.protected_by_mcp)
        self.assertNotEqual(res.suggested_zone, "99_Archive_Dormant")


class TestSystemReferenceProtection(_IsolatedClassifierTestCase):
    """System reference protection via SystemRefIndex.check_folder()."""

    def test_protected_by_system_flagged(self) -> None:
        proj = self.test_dir / "sys_tool"
        proj.mkdir()
        (proj / "tool.py").write_text("print(1)", encoding="utf-8")

        # Inject system ref pointing to this project folder
        ref = SystemRef(kind="path", source="PATH", target=str(proj))
        mock_index = SystemRefIndex([ref])

        classifier = self.make_classifier(system_refs=mock_index)
        res = classifier.classify_project(str(proj))

        self.assertTrue(res.protected_by_system)
        self.assertIsNotNone(res.system_ref_summary)
        self.assertIn("PATH", res.system_ref_summary)
        self.assertTrue(len(res.references) > 0)


class TestHackathonKeywords(_IsolatedClassifierTestCase):
    """Rule 3: Hackathon / Pitch keywords in folder name."""

    def test_all_hackathon_keywords_matched(self) -> None:
        classifier = self.make_classifier()
        test_names = [
            ("hackathon_2026", "04_Hackathons_Pitch"),
            ("hackerrank-orchestrate", "04_Hackathons_Pitch"),
            ("Gamota_NextGenAI_Pitching_BAN_CUOI", "04_Hackathons_Pitch"),
            ("coding_contest_oct", "04_Hackathons_Pitch"),
            ("ai_challenge_runner", "04_Hackathons_Pitch"),
            ("code_rush_proj", "04_Hackathons_Pitch"),
            ("orchestrate_submission", "04_Hackathons_Pitch"),
            ("kaggle_comp_v1", "04_Hackathons_Pitch"),
        ]
        for folder_name, expected_zone in test_names:
            proj = self.test_dir / folder_name
            proj.mkdir()
            (proj / "solve.py").write_text("print('hack')", encoding="utf-8")
            res = classifier.classify_project(str(proj))
            self.assertEqual(res.suggested_zone, expected_zone, f"Failed for name '{folder_name}'")
            self.assertEqual(res.reason, "Detected hackathon/pitch keywords in folder name")

    def test_non_hackathon_name_does_not_trigger_rule_3(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "bionexus_core"
        proj.mkdir()
        # 3 source files so Rule 4 (<= 2) doesn't catch it
        (proj / "a.py").write_text("1", encoding="utf-8")
        (proj / "b.py").write_text("2", encoding="utf-8")
        (proj / "c.py").write_text("3", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertNotEqual(res.suggested_zone, "04_Hackathons_Pitch")


class TestIncubatorScaffolding(_IsolatedClassifierTestCase):
    """Rule 4: Scaffolded project structure with <= 2 executable source files."""

    def test_incubator_scaffold_with_zero_source_files(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "draft_spec_proj"
        proj.mkdir()
        (proj / "README.md").write_text("# Draft Spec", encoding="utf-8")
        (proj / ".gitignore").write_text("*.log\n", encoding="utf-8")
        (proj / "docs").mkdir()
        (proj / "docs" / "design.md").write_text("Architecture draft", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertEqual(res.source_file_count, 0)
        self.assertEqual(res.reason, "Scaffolded project with <= 2 source files")

    def test_incubator_scaffold_with_two_source_files(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "small_scaffold"
        proj.mkdir()
        (proj / "README.md").write_text("# Readme", encoding="utf-8")
        (proj / "src").mkdir()
        (proj / "src" / "index.ts").write_text("export const x = 1;", encoding="utf-8")
        (proj / "src" / "types.ts").write_text("export type X = number;", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertEqual(res.source_file_count, 2)
        self.assertEqual(res.reason, "Scaffolded project with <= 2 source files")

    def test_scaffold_with_three_source_files_falls_through_to_rule_5(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "active_multi_file_proj"
        proj.mkdir()
        (proj / "README.md").write_text("# Readme", encoding="utf-8")
        (proj / "src").mkdir()
        (proj / "src" / "f1.py").write_text("a = 1", encoding="utf-8")
        (proj / "src" / "f2.py").write_text("b = 2", encoding="utf-8")
        (proj / "src" / "f3.py").write_text("c = 3", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.source_file_count, 3)
        # Because files were created just now (age <= 14d), Rule 5 routes to 01_Active_Focus
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertIn("Active project", res.reason)

    def test_ignored_directories_not_counted_as_source_files(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "scaffold_with_pruned_dirs"
        proj.mkdir()
        (proj / "README.md").write_text("# Readme", encoding="utf-8")
        (proj / "main.py").write_text("print(1)", encoding="utf-8")

        # Create node_modules and .venv containing many source files
        nm = proj / "node_modules" / "dep"
        nm.mkdir(parents=True)
        for i in range(10):
            (nm / f"dep_{i}.js").write_text("module.exports = {};", encoding="utf-8")

        venv = proj / ".venv" / "Lib"
        venv.mkdir(parents=True)
        for i in range(10):
            (venv / f"mod_{i}.py").write_text("pass", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        # Only main.py is counted!
        self.assertEqual(res.source_file_count, 1)
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")


class TestGitActivityAndRecency(_IsolatedClassifierTestCase):
    """Rule 5: Git activity, commit timestamps, file mtimes, and dirty worktrees."""

    def test_active_recent_files_within_14_days(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "recent_proj"
        proj.mkdir()
        # 3 source files so it bypasses Rule 4
        for i in range(3):
            (proj / f"mod_{i}.py").write_text("print(1)", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertLessEqual(res.age_days, 1.0)
        self.assertIn("Active project", res.reason)

    def test_dormant_clean_project_older_than_60_days(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "old_dormant_proj"
        proj.mkdir()
        for i in range(3):
            f = proj / f"code_{i}.py"
            f.write_text("print('old')", encoding="utf-8")

        # Set mtime to 90 days ago
        old_time = time.time() - (90 * 86400)
        for f in proj.iterdir():
            os.utime(f, (old_time, old_time))
        os.utime(proj, (old_time, old_time))

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "99_Archive_Dormant")
        self.assertGreaterEqual(res.age_days, 89.0)
        self.assertEqual(res.reason, "Dormant project (inactive for >= 60d with clean git tree)")

    def test_old_project_with_uncommitted_git_changes_stays_active(self) -> None:
        """Edge Case 5: 90 days old but has dirty git changes -> 01_Active_Focus."""
        classifier = self.make_classifier()
        proj = self.test_dir / "dirty_old_proj"
        proj.mkdir()
        for i in range(3):
            (proj / f"code_{i}.py").write_text("print('old')", encoding="utf-8")

        # Mock git status returning 1 uncommitted change
        mock_copy = MagicMock()
        mock_copy.head_time = int(time.time() - (90 * 86400))
        mock_copy.changes = 1
        mock_copy.unpushed = 0
        mock_copy.branch = "main"
        mock_copy.error = ""

        # Make .git folder
        (proj / ".git").mkdir()

        with patch("smart_drive.core.repos.inspect_repository", return_value=mock_copy):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertEqual(res.git_status["changes"], 1)
        self.assertIn("uncommitted/unpushed git changes", res.reason)

    def test_old_project_with_unpushed_commits_stays_active(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "unpushed_old_proj"
        proj.mkdir()
        for i in range(3):
            (proj / f"code_{i}.py").write_text("print('old')", encoding="utf-8")

        mock_copy = MagicMock()
        mock_copy.head_time = int(time.time() - (80 * 86400))
        mock_copy.changes = 0
        mock_copy.unpushed = 2
        mock_copy.branch = "main"
        mock_copy.error = ""

        (proj / ".git").mkdir()

        with patch("smart_drive.core.repos.inspect_repository", return_value=mock_copy):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertEqual(res.git_status["unpushed"], 2)
        self.assertIn("uncommitted/unpushed git changes", res.reason)

    def test_intermediate_project_preserves_current_zone(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "intermediate_proj"
        proj.mkdir()
        for i in range(3):
            f = proj / f"code_{i}.py"
            f.write_text("print('med')", encoding="utf-8")

        # Set mtime to 30 days ago
        med_time = time.time() - (30 * 86400)
        for f in proj.iterdir():
            os.utime(f, (med_time, med_time))
        os.utime(proj, (med_time, med_time))

        # Classified with current_zone = 01_Active_Focus
        res = classifier.classify_project(str(proj), current_zone="01_Active_Focus")
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertIn("preserved current zone '01_Active_Focus'", res.reason)

    def test_intermediate_project_without_zone_assigned_to_incubator(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "flat_intermediate_proj"
        proj.mkdir()
        for i in range(3):
            f = proj / f"code_{i}.py"
            f.write_text("print('med')", encoding="utf-8")

        med_time = time.time() - (30 * 86400)
        for f in proj.iterdir():
            os.utime(f, (med_time, med_time))
        os.utime(proj, (med_time, med_time))

        res = classifier.classify_project(str(proj), current_zone=None)
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertIn("assigned to incubator", res.reason)


class TestZoneContainersProtection(_IsolatedClassifierTestCase):
    """Rule 0: Inviolable zone container roots are never treated as projects."""

    def test_is_zone_container_helper(self) -> None:
        self.assertTrue(ProjectClassifier.is_zone_container("01_Active_Focus"))
        self.assertTrue(ProjectClassifier.is_zone_container("02_Wave_Incubator"))
        self.assertTrue(ProjectClassifier.is_zone_container("03_MCP_Agents"))
        self.assertTrue(ProjectClassifier.is_zone_container("04_Hackathons_Pitch"))
        self.assertTrue(ProjectClassifier.is_zone_container("99_Archive_Dormant"))
        self.assertTrue(ProjectClassifier.is_zone_container("99_Archive"))
        self.assertFalse(ProjectClassifier.is_zone_container("smart_drive_os"))
        self.assertFalse(ProjectClassifier.is_zone_container("my_project"))

    def test_classify_project_on_zone_container_safeguard(self) -> None:
        classifier = self.make_classifier()
        container = self.test_dir / "01_Active_Focus"
        container.mkdir()

        res = classifier.classify_project(str(container))
        self.assertEqual(res.project_name, "01_Active_Focus")
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertEqual(res.reason, "Inviolable zone container")
        self.assertTrue(res.protected_by_system)

    def test_scan_all_projects_excludes_containers_and_scans_children(self) -> None:
        classifier = self.make_classifier()
        dev_dir = self.test_dir / "03_Development_Projects"
        dev_dir.mkdir()

        # Create zone container with 1 project inside
        active_zone = dev_dir / "01_Active_Focus"
        active_zone.mkdir()
        p1 = active_zone / "project_alpha"
        p1.mkdir()
        (p1 / "main.py").write_text("print('alpha')", encoding="utf-8")

        # Create another zone container with 1 project inside
        archive_zone = dev_dir / "99_Archive_Dormant"
        archive_zone.mkdir()
        p2 = archive_zone / "project_beta"
        p2.mkdir()
        (p2 / "main.py").write_text("print('beta')", encoding="utf-8")

        # Create 1 flat project directly in dev_dir
        p3 = dev_dir / "project_gamma_flat"
        p3.mkdir()
        (p3 / "main.py").write_text("print('gamma')", encoding="utf-8")

        # Create non-project files (must be ignored)
        (dev_dir / "ROADMAP_KANBAN.md").write_text("# Roadmap", encoding="utf-8")
        (dev_dir / "Wave1.code-workspace").write_text("{}", encoding="utf-8")

        results = classifier.scan_all_projects(str(dev_dir))

        # Zone containers (01_Active_Focus, 99_Archive_Dormant) must NOT be in results!
        result_names = [r.project_name for r in results]
        self.assertNotIn("01_Active_Focus", result_names)
        self.assertNotIn("99_Archive_Dormant", result_names)
        self.assertNotIn("ROADMAP_KANBAN.md", result_names)
        self.assertNotIn("Wave1.code-workspace", result_names)

        # Must find exactly 3 projects
        self.assertEqual(len(results), 3)
        self.assertIn("project_alpha", result_names)
        self.assertIn("project_beta", result_names)
        self.assertIn("project_gamma_flat", result_names)

        # Check current_zone tracking
        by_name = {r.project_name: r for r in results}
        self.assertEqual(by_name["project_alpha"].current_zone, "01_Active_Focus")
        self.assertEqual(by_name["project_beta"].current_zone, "99_Archive_Dormant")
        self.assertIsNone(by_name["project_gamma_flat"].current_zone)


class TestWipCountCalculation(_IsolatedClassifierTestCase):
    """WIP calculation in 01_Active_Focus."""

    def test_wip_count_calculation(self) -> None:
        classifier = self.make_classifier()

        results = [
            ProjectZoneResult(
                project_name="p1",
                project_path="/mock/p1",
                current_zone="01_Active_Focus",
                suggested_zone="01_Active_Focus",
                reason="active",
            ),
            ProjectZoneResult(
                project_name="p2",
                project_path="/mock/p2",
                current_zone=None,
                suggested_zone="01_Active_Focus",
                reason="active",
            ),
            ProjectZoneResult(
                project_name="p3",
                project_path="/mock/p3",
                current_zone="01_Active_Focus",
                suggested_zone="99_Archive_Dormant",
                reason="dormant",
            ),
            ProjectZoneResult(
                project_name="p4",
                project_path="/mock/p4",
                current_zone="02_Wave_Incubator",
                suggested_zone="02_Wave_Incubator",
                reason="incubator",
            ),
            ProjectZoneResult(
                project_name="p5",
                project_path="/mock/p5",
                current_zone=None,
                suggested_zone="04_Hackathons_Pitch",
                reason="hackathon",
            ),
        ]

        wip = classifier.get_wip_count(results)
        # p1 (current + suggested), p2 (suggested), p3 (current) -> 3 projects in WIP!
        self.assertEqual(wip, 3)


# ==============================================================================
# ADVERSARIAL STRESS TEST SUITES
# ==============================================================================

class TestAdversarialEmptyAndMalformedInputs(_IsolatedClassifierTestCase):
    """Stress tests: Empty directories, malformed stage markers, and corrupted JSON."""

    def test_completely_empty_directory(self) -> None:
        classifier = self.make_classifier()
        empty_dir = self.test_dir / "empty_proj"
        empty_dir.mkdir()

        res = classifier.classify_project(str(empty_dir))
        self.assertEqual(res.project_name, "empty_proj")
        self.assertEqual(res.source_file_count, 0)
        self.assertFalse(res.is_explicit)
        self.assertFalse(res.protected_by_mcp)
        # Because it was just created (age <= 14d), falls through to Rule 5 Active Focus
        self.assertEqual(res.suggested_zone, "01_Active_Focus")

    def test_empty_directory_older_than_60_days(self) -> None:
        classifier = self.make_classifier()
        old_empty = self.test_dir / "old_empty_proj"
        old_empty.mkdir()
        old_time = time.time() - (75 * 86400)
        os.utime(old_empty, (old_time, old_time))

        res = classifier.classify_project(str(old_empty))
        self.assertEqual(res.suggested_zone, "99_Archive_Dormant")
        self.assertGreaterEqual(res.age_days, 74.0)

    def test_empty_stage_marker_file_falls_through(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "empty_marker_proj"
        proj.mkdir()
        (proj / ".smart_drive_stage").write_text("", encoding="utf-8")
        (proj / "hackathon_solver.py").write_text("print('test')", encoding="utf-8")

        # Fallback to Rule 5 or Rule 3 depending on name
        res = classifier.classify_project(str(proj))
        self.assertFalse(res.is_explicit)

    def test_whitespace_and_newlines_in_stage_marker(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "whitespace_marker_proj"
        proj.mkdir()
        (proj / ".smart_drive_stage").write_text("   \n\t  \n  ", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertFalse(res.is_explicit)

    def test_stage_marker_with_inline_comments_and_surrounding_whitespace(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "commented_marker_proj"
        proj.mkdir()
        (proj / ".smart_drive_stage").write_text("   active   # sprint 42 focus\nsecond line ignored", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertTrue(res.is_explicit)
        self.assertEqual(res.suggested_zone, "01_Active_Focus")

    def test_unknown_stage_name_falls_through(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "unknown_stage_proj"
        proj.mkdir()
        (proj / ".smart_drive_stage").write_text("quantum_superposition\n", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertFalse(res.is_explicit)

    def test_malformed_json_syntax_in_project_json(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "corrupted_json_proj"
        proj.mkdir()
        (proj / ".project.json").write_text("{ stage: incubator, broken_json ", encoding="utf-8")

        # Must not raise JSONDecodeError; should safely ignore and fall through
        res = classifier.classify_project(str(proj))
        self.assertFalse(res.is_explicit)

    def test_empty_project_json(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "empty_json_proj"
        proj.mkdir()
        (proj / ".project.json").write_text("", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertFalse(res.is_explicit)

    def test_project_json_root_is_list_or_primitive(self) -> None:
        classifier = self.make_classifier()
        for payload in ["[1, 2, 3]", '"just_a_string"', "12345", "true", "null"]:
            proj = self.test_dir / f"json_non_dict_{hash(payload)}"
            proj.mkdir()
            (proj / ".project.json").write_text(payload, encoding="utf-8")
            res = classifier.classify_project(str(proj))
            self.assertFalse(res.is_explicit)

    def test_project_json_stage_is_non_string_type(self) -> None:
        classifier = self.make_classifier()
        # stage is a list: falls through
        p2 = self.test_dir / "stage_list"
        p2.mkdir()
        (p2 / ".project.json").write_text(json.dumps({"stage": ["active"]}), encoding="utf-8")
        res2 = classifier.classify_project(str(p2))
        self.assertFalse(res2.is_explicit)

        # stage is a dict: falls through
        p3 = self.test_dir / "stage_dict"
        p3.mkdir()
        (p3 / ".project.json").write_text(json.dumps({"stage": {"key": "val"}}), encoding="utf-8")
        res3 = classifier.classify_project(str(p3))
        self.assertFalse(res3.is_explicit)

        # stage is boolean False: falsy in 'or', falls through
        p4 = self.test_dir / "stage_false"
        p4.mkdir()
        (p4 / ".project.json").write_text(json.dumps({"stage": False}), encoding="utf-8")
        res4 = classifier.classify_project(str(p4))
        self.assertFalse(res4.is_explicit)

        # Note: boolean True in Python is an instance of int (isinstance(True, int) == True),
        # so {"stage": True} coerces to int 1 -> 01_Active_Focus.
        p5 = self.test_dir / "stage_true"
        p5.mkdir()
        (p5 / ".project.json").write_text(json.dumps({"stage": True}), encoding="utf-8")
        res5 = classifier.classify_project(str(p5))
        self.assertTrue(res5.is_explicit)
        self.assertEqual(res5.suggested_zone, "01_Active_Focus")

    def test_project_json_wave_numeric_variations(self) -> None:
        classifier = self.make_classifier()
        # wave 1 -> 01_Active_Focus
        p1 = self.test_dir / "wave_int_1"
        p1.mkdir()
        (p1 / ".project.json").write_text(json.dumps({"wave": 1}), encoding="utf-8")
        self.assertEqual(classifier.classify_project(str(p1)).suggested_zone, "01_Active_Focus")

        # wave "1" -> 01_Active_Focus
        p2 = self.test_dir / "wave_str_1"
        p2.mkdir()
        (p2 / ".project.json").write_text(json.dumps({"wave": "1"}), encoding="utf-8")
        self.assertEqual(classifier.classify_project(str(p2)).suggested_zone, "01_Active_Focus")

        # wave 2 -> 02_Wave_Incubator
        p3 = self.test_dir / "wave_int_2"
        p3.mkdir()
        (p3 / ".project.json").write_text(json.dumps({"wave": 2}), encoding="utf-8")
        self.assertEqual(classifier.classify_project(str(p3)).suggested_zone, "02_Wave_Incubator")

        # wave 3 -> 02_Wave_Incubator
        p4 = self.test_dir / "wave_int_3"
        p4.mkdir()
        (p4 / ".project.json").write_text(json.dumps({"wave": 3}), encoding="utf-8")
        self.assertEqual(classifier.classify_project(str(p4)).suggested_zone, "02_Wave_Incubator")

    def test_project_json_smart_drive_stage_key(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "proj_smart_drive_stage_key"
        proj.mkdir()
        (proj / ".project.json").write_text(json.dumps({"smart_drive_stage": "archive"}), encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "99_Archive_Dormant")
        self.assertTrue(res.is_explicit)


class TestAdversarialKeywordsAndTokenization(_IsolatedClassifierTestCase):
    """Stress tests: Case sensitivity, CamelCase splitting, and boundary token matches."""

    def test_case_variations_for_keywords(self) -> None:
        classifier = self.make_classifier()
        variations = [
            ("HACKATHON_2026", "04_Hackathons_Pitch"),
            ("HackathonRunner", "04_Hackathons_Pitch"),
            ("PITCH_DECK_FINAL", "04_Hackathons_Pitch"),
            ("MyPitchingApp", "04_Hackathons_Pitch"),
            ("CONTEST_LEADERBOARD", "04_Hackathons_Pitch"),
            ("ChallengeAccepted", "04_Hackathons_Pitch"),
            ("CodeRushV2", "04_Hackathons_Pitch"),
            ("OrchestrateWorkflow", "04_Hackathons_Pitch"),
            ("KaggleCompProject", "04_Hackathons_Pitch"),
        ]
        for name, expected_zone in variations:
            proj = self.test_dir / name
            proj.mkdir()
            (proj / "run.py").write_text("print('test')", encoding="utf-8")
            res = classifier.classify_project(str(proj))
            self.assertEqual(res.suggested_zone, expected_zone, f"Failed for {name}")

    def test_token_boundary_avoids_false_positives(self) -> None:
        classifier = self.make_classifier()
        false_positive_candidates = [
            "company_portal",      # contains 'comp', but full word is 'company'
            "computer_vision",     # contains 'comp', but full word is 'computer'
            "pitcher_stats",       # contains 'pitch', but full word is 'pitcher'
            "crush_game",          # contains 'rush', but full word is 'crush'
        ]
        for name in false_positive_candidates:
            proj = self.test_dir / name
            proj.mkdir()
            # 3 source files so Rule 4 doesn't catch it
            for i in range(3):
                (proj / f"src_{i}.py").write_text("pass", encoding="utf-8")

            res = classifier.classify_project(str(proj))
            self.assertNotEqual(
                res.suggested_zone,
                "04_Hackathons_Pitch",
                f"False positive triggered for folder name '{name}'"
            )


class TestAdversarialAgeDaysBoundaries(_IsolatedClassifierTestCase):
    """Stress tests: Exact floating point age day boundaries using fixed deterministic timestamps."""

    def test_boundary_13_9_days_is_active(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "boundary_13_9"
        proj.mkdir()
        for i in range(3):
            (proj / f"mod_{i}.py").write_text("x = 1", encoding="utf-8")

        fixed_now = 2000000000.0
        target_time = fixed_now - (13.9 * 86400)
        for f in proj.iterdir():
            os.utime(f, (target_time, target_time))
        os.utime(proj, (target_time, target_time))

        with patch("time.time", return_value=fixed_now):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertLessEqual(res.age_days, 14.0)

    def test_boundary_14_0_days_is_active(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "boundary_14_0"
        proj.mkdir()
        for i in range(3):
            (proj / f"mod_{i}.py").write_text("x = 1", encoding="utf-8")

        fixed_now = 2000000000.0
        target_time = fixed_now - (14.0 * 86400)
        for f in proj.iterdir():
            os.utime(f, (target_time, target_time))
        os.utime(proj, (target_time, target_time))

        with patch("time.time", return_value=fixed_now):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertAlmostEqual(res.age_days, 14.0, places=5)

    def test_boundary_14_1_days_transitions_to_incubator_if_flat(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "boundary_14_1"
        proj.mkdir()
        for i in range(3):
            (proj / f"mod_{i}.py").write_text("x = 1", encoding="utf-8")

        fixed_now = 2000000000.0
        target_time = fixed_now - (14.1 * 86400)
        for f in proj.iterdir():
            os.utime(f, (target_time, target_time))
        os.utime(proj, (target_time, target_time))

        with patch("time.time", return_value=fixed_now):
            res = classifier.classify_project(str(proj), current_zone=None)

        # 14.1 days is > 14.0 days, so clean project without current zone goes to 02_Wave_Incubator
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertAlmostEqual(res.age_days, 14.1, places=5)

    def test_boundary_59_9_days_stays_in_incubator(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "boundary_59_9"
        proj.mkdir()
        for i in range(3):
            (proj / f"mod_{i}.py").write_text("x = 1", encoding="utf-8")

        fixed_now = 2000000000.0
        target_time = fixed_now - (59.9 * 86400)
        for f in proj.iterdir():
            os.utime(f, (target_time, target_time))
        os.utime(proj, (target_time, target_time))

        with patch("time.time", return_value=fixed_now):
            res = classifier.classify_project(str(proj), current_zone=None)

        # Not yet 60 days -> must NOT be 99_Archive_Dormant
        self.assertNotEqual(res.suggested_zone, "99_Archive_Dormant")
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertAlmostEqual(res.age_days, 59.9, places=5)

    def test_boundary_60_0_days_archives_dormant(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "boundary_60_0"
        proj.mkdir()
        for i in range(3):
            (proj / f"mod_{i}.py").write_text("x = 1", encoding="utf-8")

        fixed_now = 2000000000.0
        target_time = fixed_now - (60.0 * 86400)
        for f in proj.iterdir():
            os.utime(f, (target_time, target_time))
        os.utime(proj, (target_time, target_time))

        with patch("time.time", return_value=fixed_now):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "99_Archive_Dormant")
        self.assertAlmostEqual(res.age_days, 60.0, places=5)

    def test_boundary_60_1_days_archives_dormant(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "boundary_60_1"
        proj.mkdir()
        for i in range(3):
            (proj / f"mod_{i}.py").write_text("x = 1", encoding="utf-8")

        fixed_now = 2000000000.0
        target_time = fixed_now - (60.1 * 86400)
        for f in proj.iterdir():
            os.utime(f, (target_time, target_time))
        os.utime(proj, (target_time, target_time))

        with patch("time.time", return_value=fixed_now):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "99_Archive_Dormant")
        self.assertAlmostEqual(res.age_days, 60.1, places=5)

    def test_future_modification_time_clamped_to_zero_days(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "future_proj"
        proj.mkdir()
        f = proj / "main.py"
        f.write_text("print(1)", encoding="utf-8")

        future_time = time.time() + (10 * 86400)
        os.utime(f, (future_time, future_time))

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.age_days, 0.0)


class TestAdversarialGitDirtyAndOldActivity(_IsolatedClassifierTestCase):
    """Stress tests: Simultaneous old activity timestamp and uncommitted/unpushed git state."""

    def test_ancient_project_with_uncommitted_changes_stays_in_active_focus(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "ancient_dirty_proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        for i in range(3):
            (proj / f"src_{i}.py").write_text("pass", encoding="utf-8")

        # 300 days old!
        ancient_time = time.time() - (300 * 86400)
        for f in proj.iterdir():
            if f.is_file():
                os.utime(f, (ancient_time, ancient_time))

        mock_copy = MagicMock()
        mock_copy.head_time = int(ancient_time)
        mock_copy.changes = 3
        mock_copy.unpushed = 0
        mock_copy.branch = "dev"
        mock_copy.error = ""

        with patch("smart_drive.core.repos.inspect_repository", return_value=mock_copy):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertEqual(res.git_status["changes"], 3)
        self.assertIn("uncommitted/unpushed git changes", res.reason)

    def test_ancient_project_with_unpushed_commits_stays_in_active_focus(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "ancient_unpushed_proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        for i in range(3):
            (proj / f"src_{i}.py").write_text("pass", encoding="utf-8")

        ancient_time = time.time() - (180 * 86400)
        for f in proj.iterdir():
            if f.is_file():
                os.utime(f, (ancient_time, ancient_time))

        mock_copy = MagicMock()
        mock_copy.head_time = int(ancient_time)
        mock_copy.changes = 0
        mock_copy.unpushed = 5
        mock_copy.branch = "feature"
        mock_copy.error = ""

        with patch("smart_drive.core.repos.inspect_repository", return_value=mock_copy):
            res = classifier.classify_project(str(proj))

        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertEqual(res.git_status["unpushed"], 5)

    def test_ancient_project_with_git_error_is_not_clean_so_not_dormant(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "git_error_proj"
        proj.mkdir()
        (proj / ".git").mkdir()
        for i in range(3):
            (proj / f"src_{i}.py").write_text("pass", encoding="utf-8")

        ancient_time = time.time() - (100 * 86400)
        for f in proj.iterdir():
            if f.is_file():
                os.utime(f, (ancient_time, ancient_time))

        mock_copy = MagicMock()
        mock_copy.head_time = int(ancient_time)
        mock_copy.changes = 0
        mock_copy.unpushed = 0
        mock_copy.branch = "main"
        mock_copy.error = "fatal: not a git repo or corrupted index"

        with patch("smart_drive.core.repos.inspect_repository", return_value=mock_copy):
            res = classifier.classify_project(str(proj), current_zone=None)

        self.assertFalse(res.git_status["clean"])
        # Because it is not clean, Rule 5 doesn't archive it to dormant
        self.assertNotEqual(res.suggested_zone, "99_Archive_Dormant")


class TestAdversarialSourceCountsAndSkeleton(_IsolatedClassifierTestCase):
    """Stress tests: Source file count thresholds (0, 1, 2, 3), extensions, and pruned folders."""

    def test_skeleton_with_0_source_files_is_incubator(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "skel_0"
        proj.mkdir()
        (proj / "README.md").write_text("# Title", encoding="utf-8")
        (proj / "package.json").write_text("{}", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertEqual(res.source_file_count, 0)

    def test_skeleton_with_1_source_file_is_incubator(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "skel_1"
        proj.mkdir()
        (proj / "README.md").write_text("# Title", encoding="utf-8")
        (proj / "index.ts").write_text("console.log('hi')", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertEqual(res.source_file_count, 1)

    def test_skeleton_with_2_source_files_is_incubator(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "skel_2"
        proj.mkdir()
        (proj / "pyproject.toml").write_text("[tool.poetry]", encoding="utf-8")
        (proj / "mod_a.py").write_text("a = 1", encoding="utf-8")
        (proj / "mod_b.py").write_text("b = 2", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")
        self.assertEqual(res.source_file_count, 2)

    def test_skeleton_with_3_source_files_bypasses_rule_4(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "skel_3"
        proj.mkdir()
        (proj / "README.md").write_text("# Title", encoding="utf-8")
        (proj / "a.py").write_text("1", encoding="utf-8")
        (proj / "b.py").write_text("2", encoding="utf-8")
        (proj / "c.py").write_text("3", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        # 3 source files > 2 -> Rule 4 does NOT match, falls through to Rule 5 Active Focus
        self.assertEqual(res.suggested_zone, "01_Active_Focus")
        self.assertEqual(res.source_file_count, 3)

    def test_all_supported_extensions_recognized(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "multi_lang_proj"
        proj.mkdir()

        exts = [".py", ".ts", ".js", ".rs", ".go", ".java", ".cpp", ".c", ".h", ".hpp", ".cs", ".v", ".rb", ".php", ".swift", ".kt"]
        for idx, ext in enumerate(exts):
            (proj / f"code_{idx}{ext}").write_text("// test", encoding="utf-8")

        # Also uppercase extensions
        (proj / "UPPER.PY").write_text("pass", encoding="utf-8")
        (proj / "CAPS.TS").write_text("let a = 1", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        self.assertEqual(res.source_file_count, len(exts) + 2)

    def test_pruned_directories_with_massive_file_trees_ignored(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "prune_stress_proj"
        proj.mkdir()
        (proj / "README.md").write_text("# Readme", encoding="utf-8")
        (proj / "index.js").write_text("console.log(1)", encoding="utf-8")

        # Populate heavily pruned directories
        for prune_name in ["node_modules", "site-packages", "__pycache__", "target", ".venv", "venv", "dist", "build"]:
            p_dir = proj / prune_name / "deep" / "nested"
            p_dir.mkdir(parents=True)
            for i in range(15):
                (p_dir / f"dep_{i}.py").write_text("pass", encoding="utf-8")
                (p_dir / f"dep_{i}.js").write_text("pass", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        # Only index.js in root should be counted!
        self.assertEqual(res.source_file_count, 1)
        self.assertEqual(res.suggested_zone, "02_Wave_Incubator")


class TestAdversarialScanningAndZoneContainersProtection(_IsolatedClassifierTestCase):
    """Stress tests: Deep nested structures, zone container safeguards, and WIP computation."""

    def test_zone_container_safeguards_all_canonical_roots(self) -> None:
        classifier = self.make_classifier()
        for root_name in ["01_Active_Focus", "02_Wave_Incubator", "03_MCP_Agents", "04_Hackathons_Pitch", "99_Archive_Dormant", "99_Archive"]:
            p = self.test_dir / root_name
            p.mkdir()
            res = classifier.classify_project(str(p))
            self.assertEqual(res.project_name, root_name)
            self.assertEqual(res.suggested_zone, root_name)
            self.assertEqual(res.reason, "Inviolable zone container")
            self.assertTrue(res.protected_by_system)
            self.assertTrue(res.is_explicit)

    def test_deep_subdirectories_inside_projects_not_treated_as_separate_projects(self) -> None:
        classifier = self.make_classifier()
        dev_dir = self.test_dir / "03_Development_Projects"
        dev_dir.mkdir()

        # Project 1: deep monorepo structure
        proj1 = dev_dir / "monorepo_core"
        (proj1 / "packages" / "frontend" / "src").mkdir(parents=True)
        (proj1 / "packages" / "frontend" / "src" / "App.tsx").write_text("export const App = 1", encoding="utf-8")
        (proj1 / "packages" / "backend" / "src").mkdir(parents=True)
        (proj1 / "packages" / "backend" / "src" / "main.py").write_text("print('backend')", encoding="utf-8")
        (proj1 / "packages" / "shared" / "src").mkdir(parents=True)
        (proj1 / "packages" / "shared" / "src" / "types.ts").write_text("export type X = string", encoding="utf-8")

        # Project 2 inside 01_Active_Focus
        active_zone = dev_dir / "01_Active_Focus"
        active_zone.mkdir()
        proj2 = active_zone / "bio_pipeline"
        (proj2 / "src" / "algo" / "deep").mkdir(parents=True)
        (proj2 / "src" / "algo" / "deep" / "kernel.py").write_text("x = 1", encoding="utf-8")

        results = classifier.scan_all_projects(str(dev_dir))
        result_names = {r.project_name for r in results}

        # Must only return top-level projects, not nested packages/src folders!
        self.assertIn("monorepo_core", result_names)
        self.assertIn("bio_pipeline", result_names)
        self.assertNotIn("packages", result_names)
        self.assertNotIn("frontend", result_names)
        self.assertNotIn("backend", result_names)
        self.assertNotIn("src", result_names)
        self.assertNotIn("01_Active_Focus", result_names)
        self.assertEqual(len(results), 2)

    def test_wip_count_calculation_zero_and_overload(self) -> None:
        classifier = self.make_classifier()

        # Empty
        self.assertEqual(classifier.get_wip_count([]), 0)

        # 4 active projects -> WIP = 4
        items = [
            ProjectZoneResult(project_name=f"p{i}", project_path=f"/p{i}", current_zone=None, suggested_zone="01_Active_Focus", reason="active")
            for i in range(4)
        ]
        self.assertEqual(classifier.get_wip_count(items), 4)

    def test_to_dict_json_serializability(self) -> None:
        classifier = self.make_classifier()
        proj = self.test_dir / "serializable_proj"
        proj.mkdir()
        (proj / "main.py").write_text("print(1)", encoding="utf-8")

        res = classifier.classify_project(str(proj))
        d = res.to_dict()
        # Must be 100% serializable by json.dumps
        serialized = json.dumps(d)
        self.assertIsInstance(serialized, str)
        self.assertIn("serializable_proj", serialized)


if __name__ == "__main__":
    unittest.main()

