"""tests/test_junk_dir_sizes.py - a junk directory weighs what is inside it.

Regression: `clean --json` on a real drive listed 3,804 `__pycache__` directories with `size: 0` (the first 300
alone held 30.8 MB), so the report, `checkup` ("Clean 4940 safe junk item(s) (131.1 KB)"), and the freed-space totals
of `clean --apply` and the MCP `ssd_clean` tool all ignored them: the scanner reports a directory with size 0 and the
purge engine recorded 0 for every directory it removed.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from smart_drive.cli.main import main
from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.core.scanner import measure_tree
from smart_drive.mcp.server import SmartDriveMCPServer

CLUSTER = 4096
# Two files: 1,000 B take one 4 KB cluster, 5,000 B take two.
NOMINAL = 6000
ALLOCATED = 3 * CLUSTER


def _make_pycache(root: Path, parent: str = "pkg") -> Path:
    cache = root / parent / "__pycache__"
    (cache / "sub").mkdir(parents=True)
    (cache / "a.pyc").write_bytes(b"x" * 1000)
    (cache / "sub" / "b.pyc").write_bytes(b"y" * 5000)
    return cache


class JunkDirSizeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_junk_size_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.cache = _make_pycache(self.root)


class TestMeasureTree(JunkDirSizeCase):
    def test_sums_nominal_and_allocated_bytes_of_every_file_below(self) -> None:
        self.assertEqual(measure_tree(str(self.cache), CLUSTER), (NOMINAL, ALLOCATED))

    def test_empty_or_missing_directory_is_zero(self) -> None:
        (self.root / "empty").mkdir()
        self.assertEqual(measure_tree(str(self.root / "empty"), CLUSTER), (0, 0))
        self.assertEqual(measure_tree(str(self.root / "missing"), CLUSTER), (0, 0))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_does_not_follow_links_out_of_the_tree(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "big.bin").write_bytes(b"z" * 100_000)
        try:
            os.symlink(str(outside), str(self.cache / "link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need privileges here")
        self.assertEqual(measure_tree(str(self.cache), CLUSTER), (NOMINAL, ALLOCATED))


class TestDetectorSizesDirectories(JunkDirSizeCase):
    def test_junk_directory_carries_the_size_of_its_files(self) -> None:
        items = JunkDetector(str(self.root), max_tier=JunkTier.TIER_1_SAFE, cluster_size=CLUSTER).find_junk()
        (item,) = [i for i in items if i.name == "__pycache__"]
        self.assertTrue(item.is_dir)
        self.assertEqual(item.size, NOMINAL)
        self.assertEqual(item.allocated_size, ALLOCATED)

    def test_a_junk_directory_inside_another_is_counted_once(self) -> None:
        outer = self.root / ".pytest_cache"
        (outer / "v").mkdir(parents=True)
        (outer / "v" / "state").write_bytes(b"s" * 2000)
        inner = outer / "v" / "__pycache__"
        inner.mkdir()
        (inner / "c.pyc").write_bytes(b"c" * 3000)

        items = JunkDetector(str(self.root), max_tier=JunkTier.TIER_2_DEV_CACHE, cluster_size=CLUSTER).find_junk()
        dirs = {i.rel_path: i for i in items if i.is_dir}
        self.assertIn(".pytest_cache", dirs)
        self.assertNotIn(".pytest_cache/v/__pycache__", dirs)
        self.assertEqual(dirs[".pytest_cache"].size, 5000)
        self.assertEqual(dirs[".pytest_cache"].allocated_size, 2 * CLUSTER)


class TestCleanReportsDirectorySizes(JunkDirSizeCase):
    def run_clean(self, *extra: str) -> Dict[str, Any]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["clean", "--root", str(self.root), "--json", *extra])
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_dry_run_json_counts_directories_in_the_reclaimable_total(self) -> None:
        report = self.run_clean()
        (item,) = [i for i in report["junk_items"] if i["name"] == "__pycache__"]
        self.assertEqual(item["size"], NOMINAL)
        self.assertEqual(report["total_reclaimable_bytes"], NOMINAL)
        self.assertTrue(self.cache.exists())

    def test_apply_json_reports_the_bytes_it_freed(self) -> None:
        report = self.run_clean("--apply")
        self.assertEqual(report["deleted_count"], 1)
        self.assertEqual(report["freed_bytes"], NOMINAL)
        self.assertEqual(report["results"][0]["size"], NOMINAL)
        self.assertFalse(self.cache.exists())


class TestPurgeEngineRecordsDirectorySizes(JunkDirSizeCase):
    def engine(self, **kwargs: Any) -> PurgeEngine:
        return PurgeEngine(str(self.root), cluster_size=CLUSTER, **kwargs)

    def test_dry_run_simulates_with_the_real_size(self) -> None:
        engine = self.engine(dry_run=True)
        rec = engine.delete_dir(str(self.cache), tier=1)
        self.assertEqual((rec.status, rec.size, rec.allocated_size), ("SIMULATED", NOMINAL, ALLOCATED))
        self.assertTrue(self.cache.exists())

    def test_deleted_directory_is_recorded_with_the_size_it_had(self) -> None:
        rec = self.engine(dry_run=False).delete_dir(str(self.cache), tier=1)
        self.assertEqual((rec.status, rec.size, rec.allocated_size), ("DELETED", NOMINAL, ALLOCATED))
        self.assertFalse(self.cache.exists())

    def test_quarantined_directory_keeps_its_size_in_the_manifest(self) -> None:
        engine = self.engine(dry_run=False)
        rec = engine.delete_dir(str(self.cache), tier=3)
        self.assertEqual((rec.status, rec.size), ("QUARANTINED", NOMINAL))
        self.assertEqual([m["size"] for m in engine.quarantine_manifest], [NOMINAL])

    def test_a_size_measured_by_the_caller_is_not_measured_again(self) -> None:
        rec = self.engine(dry_run=True).delete_dir(str(self.cache), tier=1, size=7, allocated_size=9)
        self.assertEqual((rec.size, rec.allocated_size), (7, 9))

    def test_purge_summary_counts_freed_bytes_of_directories(self) -> None:
        items = JunkDetector(str(self.root), max_tier=JunkTier.TIER_1_SAFE, cluster_size=CLUSTER).find_junk()
        summary = self.engine(dry_run=False).purge_items(items)
        self.assertEqual(summary.deleted_count, 1)
        self.assertEqual(summary.freed_bytes, NOMINAL)
        self.assertEqual(summary.nominal_bytes_reclaimed, NOMINAL)
        self.assertEqual(summary.allocated_bytes_reclaimed, ALLOCATED)

    def test_mcp_ssd_clean_reports_freed_bytes_of_directories(self) -> None:
        server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)
        preview = server.handle_ssd_clean({"dry_run": True})
        self.assertEqual(preview["nominal_bytes_reclaimed"], NOMINAL)
        applied = server.handle_ssd_clean({"apply": True})
        self.assertEqual(applied["freed_bytes"], NOMINAL)
        self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
