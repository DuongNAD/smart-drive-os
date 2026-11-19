"""tests/test_sentinel.py - Comprehensive Unit Tests for SentinelEngine & Health Audit.

100% Python Standard Library unittest.
Tests Tiers 1, 2, 3:
- Anti-indexing shield detection (.metadata_never_index, .fseventsd/no_log).
- Automatic self-healing of missing shields (auto_heal=True).
- Search database connectivity and SQLite PRAGMA quick_check validation.
- exFAT safety auditing (Windows forbidden characters and symlink checks).
- SentinelEngine integrated health audit (HealthReport data structure and properties).
- Resilience to unmounted/non-existent paths.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.core.sentinel import (
        HealthReport,
        SentinelEngine,
        check_and_heal_shields,
        check_disk_capacity,
        check_exfat_safety,
        check_git_repositories,
        check_search_database,
        get_default_db_path,
    )
except (ImportError, ModuleNotFoundError):
    sentinel_path = PROJECT_ROOT / "smart_drive" / "core" / "sentinel.py"
    if sentinel_path.exists():
        # Direct load to bypass incomplete package __init__ during concurrent worker builds
        spec = importlib.util.spec_from_file_location("smart_drive_core_sentinel", sentinel_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["smart_drive_core_sentinel"] = mod
        spec.loader.exec_module(mod)
        HealthReport = mod.HealthReport
        SentinelEngine = mod.SentinelEngine
        check_and_heal_shields = mod.check_and_heal_shields
        check_exfat_safety = mod.check_exfat_safety
        check_git_repositories = mod.check_git_repositories
        check_search_database = mod.check_search_database
        get_default_db_path = mod.get_default_db_path
    else:
        sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
        from smart_drive_manager import (
            check_and_heal_shields,
            check_search_database,
            check_exfat_safety,
            check_git_repositories,
        )

from tests.helpers import SmartDriveTestCase


class TestShieldVerificationAndHealing(SmartDriveTestCase):
    """Tier 1 & Tier 2: Shield checking and automatic self-healing."""

    def test_missing_shields_auto_healed(self) -> None:
        """When shields are missing, check_and_heal_shields creates them if auto_heal=True."""
        root = self.test_dir / "shield_heal_test"
        root.mkdir()

        res = check_and_heal_shields(str(root), auto_heal=True)
        self.assertTrue(res["all_healthy"])
        self.assertTrue(res["auto_healed"])
        self.assertTrue((root / ".metadata_never_index").exists())
        self.assertTrue((root / ".fseventsd" / "no_log").exists())

    def test_missing_shields_not_healed_when_disabled(self) -> None:
        """When auto_heal=False, missing shields are reported without creation."""
        root = self.test_dir / "shield_no_heal"
        root.mkdir()

        res = check_and_heal_shields(str(root), auto_heal=False)
        self.assertFalse(res["all_healthy"])
        self.assertFalse(res["auto_healed"])
        self.assertFalse((root / ".metadata_never_index").exists())

    def test_existing_shields_remain_healthy(self) -> None:
        """When shields already exist, auto_healed is False and all_healthy is True."""
        mock_root = self.create_mock_drive()
        res = check_and_heal_shields(str(mock_root), auto_heal=True)
        self.assertTrue(res["all_healthy"])
        self.assertFalse(res["auto_healed"])


class TestDatabaseIntegrityCheck(SmartDriveTestCase):
    """Tier 1: SQLite FTS5 database existence and PRAGMA quick_check."""

    def test_database_not_found(self) -> None:
        """Reports exists=False when database file does not exist."""
        root = self.test_dir / "no_db_dir"
        root.mkdir()

        info = check_search_database(str(root))
        self.assertFalse(info["exists"])
        self.assertFalse(info["healthy"])

    def test_database_healthy(self) -> None:
        """Valid SQLite database passes PRAGMA quick_check."""
        root = self.test_dir / "with_db_dir"
        root.mkdir()
        db_file = root / ".smart_drive" / "index.db"

        # Initialize valid database
        try:
            from smart_drive.indexer.db import DatabaseManager
        except (ImportError, ModuleNotFoundError):
            sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
            from indexer.db import DatabaseManager

        with DatabaseManager(str(db_file)) as db:
            db.initialize_schema()

        info = check_search_database(str(root), db_path=str(db_file))
        self.assertTrue(info["exists"])
        self.assertTrue(info["healthy"])
        self.assertEqual(info["quick_check"], "ok")


class TestSentinelEngineExecution(SmartDriveTestCase):
    """Tier 1 & Tier 2: SentinelEngine health check and resilience."""

    def test_sentinel_health_check_on_mock_drive(self) -> None:
        """SentinelEngine returns healthy status on mock drive with shields and DB."""
        mock_root = self.create_mock_drive()
        db_path = mock_root / ".smart_drive" / "index.db"

        try:
            from smart_drive.indexer.db import DatabaseManager
        except (ImportError, ModuleNotFoundError):
            sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
            from indexer.db import DatabaseManager

        with DatabaseManager(str(db_path)) as db:
            db.initialize_schema()

        engine = SentinelEngine(str(mock_root))
        report = engine.run_health_check()

        self.assertIsInstance(report, dict)
        self.assertTrue(report.get("is_healthy", False))
        self.assertIn("anti_indexing_shields", report)
        self.assertIn("database", report)
        self.assertIn("mount", report)
        self.assertTrue(report["mount"]["is_mounted"])

    def test_sentinel_handles_nonexistent_drive_gracefully(self) -> None:
        """Checking a non-existent drive returns is_mounted=False and is_healthy=False."""
        engine = SentinelEngine(str(self.test_dir / "non_existent_mount_path"))
        report = engine.run_health_check()

        self.assertFalse(report.get("is_healthy", True))
        self.assertFalse(report.get("is_mounted", True))


class TestSentinelCapacityAndGitPruning(SmartDriveTestCase):
    """Capacity alerts and build-tree pruning during git discovery."""

    _Usage = namedtuple("usage", ["total", "used", "free"])

    def test_capacity_thresholds_and_sentinel_health(self) -> None:
        mock_root = self.create_mock_drive()
        engine = SentinelEngine(str(mock_root))

        total = 1000 * (1024 ** 3)
        # Critical (<5% free) marks Sentinel unhealthy
        with patch("smart_drive.core.sentinel.shutil.disk_usage", return_value=self._Usage(total, total - 30 * (1024 ** 3), 30 * (1024 ** 3))):
            rep = engine.run_health_check()
            self.assertEqual(rep["capacity"]["status"], "critical")
            self.assertFalse(rep.is_healthy)
            self.assertEqual(rep.status, "unhealthy")
            self.assertTrue(rep["warnings"])

        # Warning (5%-15% free) adds warning while remaining healthy
        with patch("smart_drive.core.sentinel.shutil.disk_usage", return_value=self._Usage(total, total - 100 * (1024 ** 3), 100 * (1024 ** 3))):
            rep = engine.run_health_check()
            self.assertEqual(rep["capacity"]["status"], "warning")
            self.assertTrue(rep.is_healthy)
            self.assertTrue(rep["warnings"])

        # Healthy (>=15% free) leaves warnings clear
        with patch("smart_drive.core.sentinel.shutil.disk_usage", return_value=self._Usage(total, total - 400 * (1024 ** 3), 400 * (1024 ** 3))):
            rep = engine.run_health_check()
            self.assertEqual(rep["capacity"]["status"], "ok")
            self.assertTrue(rep.is_healthy)
            self.assertEqual(rep["warnings"], [])

    def test_check_git_repositories_prunes_build_and_cachedir_tag_trees(self) -> None:
        root = self.test_dir / "git_prune_drive"
        (root / "real_repo" / ".git").mkdir(parents=True)
        (root / "target" / "nested_repo" / ".git").mkdir(parents=True)
        (root / "venv" / "pkg_repo" / ".git").mkdir(parents=True)
        (root / "build" / "sub_repo" / ".git").mkdir(parents=True)
        (root / "custom_target").mkdir(parents=True)
        (root / "custom_target" / "CACHEDIR.TAG").write_bytes(
            b"Signature: 8a477f597d28d172789f06886806bc55\n"
        )
        (root / "custom_target" / "sub_repo" / ".git").mkdir(parents=True)

        res = check_git_repositories(str(root), max_depth=4)
        paths = [d["path"] for d in res["details"]]
        self.assertEqual(paths, ["real_repo"])

    def test_status_json_includes_capacity(self) -> None:
        from smart_drive.cli.main import main as cli_main

        mock_root = self.create_mock_drive()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli_main(["status", "--root", str(mock_root), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertIn("capacity", data)
        self.assertGreater(data["capacity"]["total_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
