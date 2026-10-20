"""tests/test_initializer.py - Comprehensive Unit Tests for DriveInitializer & Preset Profiles.

100% Python Standard Library unittest.
Tests Tiers 1, 2, 3:
- 1-Touch drive initialization with 3 preset profiles (ai-developer, data-science, general-workspace).
- Creation of 6 canonical taxonomies and profile-specific subtrees.
- Seeding of anti-indexing shields (.metadata_never_index, .fseventsd/no_log).
- Generation of AI agent manifests (AGENTS.md, GEMINI.md, CLAUDE.md).
- Initial SQLite FTS5 index database creation and schema validation.
- Cross-feature verification: init followed by audit, clean, and search.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure smart_drive.core.config has STANDARD_TAXONOMIES attribute
if "smart_drive.core.config" not in sys.modules:
    cfg_p = PROJECT_ROOT / "smart_drive" / "core" / "config.py"
    if cfg_p.exists():
        spec = importlib.util.spec_from_file_location("smart_drive.core.config", cfg_p)
        cfg_m = importlib.util.module_from_spec(spec)
        sys.modules["smart_drive.core.config"] = cfg_m
        spec.loader.exec_module(cfg_m)
        if not hasattr(cfg_m, "STANDARD_TAXONOMIES"):
            cfg_m.STANDARD_TAXONOMIES = getattr(cfg_m, "PROTECTED_CORE_TAXONOMIES", ())
else:
    cfg_m = sys.modules["smart_drive.core.config"]
    if not hasattr(cfg_m, "STANDARD_TAXONOMIES"):
        cfg_m.STANDARD_TAXONOMIES = getattr(cfg_m, "PROTECTED_CORE_TAXONOMIES", ())

from smart_drive.core.initializer import DriveInitializer, PROFILES
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.indexer.db import DatabaseManager
from smart_drive.search.engine import SearchEngine
from smart_drive.search.parser import parse_search_query

from tests.helpers import SmartDriveTestCase


def _without_setup(manifest: str) -> str:
    """A manifest minus its MCP ecosystem setup instructions, which differ on purpose:
    a drive's copy names the installed `smart-drive setup`, this repository's copy
    `python3 -m smart_drive setup` run from a clone (see test_mcp_setup_run)."""
    kept = []
    in_setup = False
    for line in manifest.strip().splitlines():
        if line.startswith("## "):
            in_setup = "mcp ecosystem" in line.lower()
        if not in_setup and not line.startswith("- First time on a machine:"):
            kept.append(line)
    return "\n".join(kept)


class TestPresetProfiles(SmartDriveTestCase):
    """Tier 1: Validates available preset profiles and metadata."""

    def test_list_profiles_contains_all_three_presets(self) -> None:
        """list_profiles must contain ai-developer, data-science, and general-workspace."""
        profiles = DriveInitializer.list_profiles()
        self.assertIn("ai-developer", profiles)
        self.assertIn("data-science", profiles)
        self.assertIn("general-workspace", profiles)

        for p_name, desc in profiles.items():
            self.assertIsInstance(desc, str)
            self.assertGreater(len(desc), 10)


class TestDriveInitialization(SmartDriveTestCase):
    """Tier 1 & Tier 2: Drive setup across different profiles."""

    def test_initialize_ai_developer_profile(self) -> None:
        """ai-developer profile creates GGUF, checkpoints, safetensors, shields, and manifests."""
        target = self.test_dir / "init_ai_dev"
        init = DriveInitializer(str(target))
        res = init.initialize(profile="ai-developer")

        self.assertEqual(res["status"], "initialized")
        self.assertEqual(res["profile"], "ai-developer")

        # 6 core taxonomies
        self.assertTrue((target / "01_AI_Models").is_dir())
        self.assertTrue((target / "02_Learning_Knowledge").is_dir())
        self.assertTrue((target / "03_Development_Projects").is_dir())
        self.assertTrue((target / "04_System_Workspaces").is_dir())
        self.assertTrue((target / "05_Dev_Toolbox").is_dir())
        self.assertTrue((target / "06_Archives_Storage").is_dir())

        # AI developer subdirectories
        self.assertTrue((target / "01_AI_Models" / "gguf").is_dir())
        self.assertTrue((target / "01_AI_Models" / "checkpoints").is_dir())
        self.assertTrue((target / "01_AI_Models" / "safetensors").is_dir())

        # Anti-indexing shields
        self.assertTrue((target / ".metadata_never_index").exists())
        self.assertTrue((target / ".fseventsd" / "no_log").exists())

        # Manifests
        self.assertTrue((target / "AGENTS.md").exists())
        self.assertTrue((target / "GEMINI.md").exists())
        self.assertTrue((target / "CLAUDE.md").exists())
        for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md"):
            text = (target / name).read_text(encoding="utf-8")
            self.assertIn("gitignore audit", text)
            self.assertIn("regression test", text)
            self.assertIn("smart-drive relink", text)
            repo_text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            self.assertEqual(_without_setup(text), _without_setup(repo_text), name)

        # Search database
        self.assertTrue(res["database_initialized"])
        self.assertTrue((target / ".smart_drive" / "index.db").exists())

    def test_initialize_data_science_profile(self) -> None:
        """data-science profile creates notebooks, pipelines, and data subdirs."""
        target = self.test_dir / "init_ds"
        init = DriveInitializer(str(target))
        res = init.initialize(profile="data-science")

        self.assertEqual(res["profile"], "data-science")
        self.assertTrue((target / "03_Development_Projects" / "notebooks").is_dir())
        self.assertTrue((target / "03_Development_Projects" / "pipelines").is_dir())
        self.assertTrue((target / "03_Development_Projects" / "data_raw").is_dir())

    def test_initialize_general_workspace_profile(self) -> None:
        """general-workspace profile is default when unspecified or unknown."""
        target = self.test_dir / "init_general"
        init = DriveInitializer(str(target))
        res = init.initialize(profile="unknown_profile_xyz")

        self.assertEqual(res["profile"], "general-workspace")
        self.assertTrue((target / "01_AI_Models").is_dir())
        self.assertTrue((target / "02_Learning_Knowledge" / "Notes").is_dir())


class TestCrossFeatureInteractionsAfterInit(SmartDriveTestCase):
    """Tier 3: Interactions across init, audit, clean, and search."""

    def test_init_followed_by_audit(self) -> None:
        """StorageAuditor on freshly initialized drive reports files and taxonomies."""
        target = self.test_dir / "audit_after_init"
        init = DriveInitializer(str(target))
        init.initialize(profile="ai-developer")

        auditor = StorageAuditor(str(target))
        report = auditor.run_audit()

        # Files created are manifests and shields
        self.assertGreater(report["total_files"], 0)
        self.assertIn("01_AI_Models", report["taxonomies"])
        self.assertIn("02_Learning_Knowledge", report["taxonomies"])

    def test_init_followed_by_clean_dry_run_and_apply(self) -> None:
        """Clean on freshly initialized drive finds zero junk and deletes nothing."""
        target = self.test_dir / "clean_after_init"
        init = DriveInitializer(str(target))
        init.initialize(profile="ai-developer")

        detector = JunkDetector(str(target))
        junk = detector.find_junk()
        self.assertEqual(len(junk), 0, "Freshly initialized drive should have 0 junk items")

        engine = PurgeEngine(str(target), dry_run=False)
        summary = engine.purge_items(junk)
        self.assertEqual(summary.total_attempted, 0)
        self.assertEqual(summary.total_succeeded, 0)

        # Inviolable files remain 100% intact
        self.assertTrue((target / "AGENTS.md").exists())
        self.assertTrue((target / "GEMINI.md").exists())
        self.assertTrue((target / ".metadata_never_index").exists())

    def test_init_followed_by_search(self) -> None:
        """Search engine opens initialized database without error."""
        target = self.test_dir / "search_after_init"
        init = DriveInitializer(str(target))
        init.initialize(profile="ai-developer")

        db_path = target / ".smart_drive" / "index.db"
        with DatabaseManager(str(db_path)) as db:
            engine = SearchEngine(db)
            params = parse_search_query("test")
            result = engine.search(params)
            self.assertEqual(result.total_count, 0)
            self.assertEqual(result.matches, [])

    def test_init_from_repo_on_unmarked_drive_initializes_drive_root_not_repo(self) -> None:
        """Running init without --root from <drive>/repo on an unmarked drive initializes <drive>, never <drive>/repo."""
        import argparse
        import io
        from unittest.mock import patch
        from smart_drive.cli.cmd_init import cmd_init
        from smart_drive.core.fsinfo import FilesystemInfo

        fake_drive = self.test_dir / "fresh_unmarked_drive"
        repo_dir = fake_drive / "repo"
        (repo_dir / "smart_drive").mkdir(parents=True)
        (repo_dir / "smart_drive" / "__init__.py").write_text("", encoding="utf-8")

        fs_info = FilesystemInfo(
            path=str(repo_dir),
            fs_type="exfat",
            block_size=524288,
            mount_point=str(fake_drive),
            source="mock",
        )
        args = argparse.Namespace(
            path=None,
            root=None,
            profile="general-workspace",
            force=False,
            json=False,
        )

        old_cwd = os.getcwd()
        try:
            os.chdir(repo_dir)
            with patch.dict(
                os.environ,
                {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"},
                clear=False,
            ):
                with patch("smart_drive.cli.cmd_init.detect_filesystem", return_value=fs_info):
                    buf = io.StringIO()
                    with patch("sys.stdout", buf):
                        rc = cmd_init(args)
                    out = buf.getvalue()
        finally:
            os.chdir(old_cwd)

        self.assertEqual(rc, 0, f"cmd_init failed: {out}")
        self.assertIn(str(fake_drive), out)
        self.assertTrue((fake_drive / "01_AI_Models").is_dir())
        self.assertTrue((fake_drive / ".metadata_never_index").is_file())
        self.assertFalse((repo_dir / "01_AI_Models").exists(), "Must never initialize inside <drive>/repo")
        self.assertFalse((repo_dir / ".metadata_never_index").exists(), "Must never create shields inside <drive>/repo")

        # Direct initialization inside a directory containing smart_drive/ must be refused
        with self.assertRaises(ValueError):
            DriveInitializer(str(repo_dir)).initialize()

    def test_init_from_repo_on_the_os_drive_does_not_initialize_the_repo_parent_folder(self) -> None:
        """A repo that is not on an external volume has no drive root: init must stop, not pick its parent folder."""
        import argparse
        import io
        from unittest.mock import patch
        from smart_drive.cli.cmd_init import cmd_init, default_init_root
        from smart_drive.core.fsinfo import FilesystemInfo

        os_drive = self.test_dir / "os_drive"
        code_parent = os_drive / "Users" / "dev" / "code"
        repo_dir = code_parent / "smart-drive-os"
        (repo_dir / "smart_drive").mkdir(parents=True)
        (repo_dir / "smart_drive" / "__init__.py").write_text("", encoding="utf-8")

        fs_info = FilesystemInfo(
            path=str(repo_dir),
            fs_type="ntfs",
            block_size=4096,
            mount_point=str(os_drive),
            source="mock",
        )
        args = argparse.Namespace(path=None, root=None, profile="general-workspace", force=False, json=False)

        def is_os_system_root(path: str) -> bool:
            return os.path.abspath(path) == os.path.abspath(str(os_drive))

        old_cwd = os.getcwd()
        try:
            os.chdir(repo_dir)
            with patch.dict(
                os.environ,
                {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"},
                clear=False,
            ), patch("smart_drive.cli.cmd_init.detect_filesystem", return_value=fs_info), patch(
                "smart_drive.cli.cmd_init._is_os_system_root", side_effect=is_os_system_root
            ):
                self.assertIsNone(default_init_root(str(repo_dir)))
                out, err = io.StringIO(), io.StringIO()
                with patch("sys.stdout", out), patch("sys.stderr", err):
                    rc = cmd_init(args)
        finally:
            os.chdir(old_cwd)

        self.assertEqual(rc, 2, out.getvalue())
        self.assertIn("safe volume root", err.getvalue())
        self.assertEqual([p.name for p in code_parent.iterdir()], ["smart-drive-os"])
        self.assertFalse((code_parent / "01_AI_Models").exists())
        self.assertFalse((code_parent / ".metadata_never_index").exists())


if __name__ == "__main__":
    unittest.main()

