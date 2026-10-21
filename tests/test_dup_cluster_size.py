"""tests/test_dup_cluster_size.py - duplicate slack is worked out at the volume's real allocation unit.

Regression: `dup`, `checkup --dup` and the MCP tool `ssd_find_duplicates` built their DuplicateDetector with the default
512 KB exFAT cluster, so `total_reclaimable_slack` was a made-up figure on an NTFS volume (4 KB clusters), while `audit`
and `checkup` already used the detected allocation unit. They now ask `detect_filesystem` and fall back to 512 KB only
when the cluster size cannot be read. The description of `ssd_find_duplicates` kept promising "512KB physical cluster
space savings" after that; it now says what the tool returns, and a test holds it to the result.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.cli.main import main
from smart_drive.core.config import CLUSTER_SIZE_BYTES, calculate_slack_bytes
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.fsinfo import FilesystemInfo
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from smart_drive.mcp.server import TOOLS, SmartDriveMCPServer
from tests.helpers import SmartDriveTestCase

# `smart_drive.cli` re-exports the `main` function, so the attribute `smart_drive.cli.main` is that function and not the
# submodule. Python 3.9 resolves patch("smart_drive.cli.main.x") by walking attributes and lands on the function; 3.11+
# imports the dotted name and lands on the module. Patching the module object itself works on both.
CLI_MAIN = importlib.import_module("smart_drive.cli.main")

# Two copies of 5,000 bytes: they take two 4 KB clusters (3,192 B of slack) or one 512 KB cluster (519,288 B of it)
PAYLOAD = b"B" * 5000
SLACK_4K = 3192
SLACK_512K = 524_288 - 5000


def volume(root: Path, block_size: int, fs_type: str = "ntfs") -> FilesystemInfo:
    return FilesystemInfo(str(root), fs_type, block_size, str(root), "win32" if fs_type != "unknown" else "unknown")


class _Drive(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_dup_cluster_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in ("a/copy.bin", "b/copy.bin"):
            path = self.root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(PAYLOAD)
        refs = patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([]))
        refs.start()
        self.addCleanup(refs.stop)


class TestDupCommand(_Drive):
    def run_dup(self, detected: FilesystemInfo, *flags: str) -> Tuple[str, Any]:
        out = io.StringIO()
        with patch.object(CLI_MAIN, "detect_filesystem", return_value=detected) as detect:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["dup", "--root", str(self.root), *flags])
        self.assertEqual(code, 0)
        detect.assert_called_once_with(str(self.root))
        return out.getvalue(), detect

    def plan(self, detected: FilesystemInfo) -> Dict[str, Any]:
        out, _ = self.run_dup(detected, "--json")
        return json.loads(out)

    def test_an_ntfs_volume_is_worked_out_at_4_kb(self) -> None:
        plan = self.plan(volume(self.root, 4096))
        self.assertEqual(plan["cluster_size"], 4096)
        self.assertEqual(plan["total_reclaimable_slack"], SLACK_4K)
        self.assertEqual(plan["duplicate_groups"][0]["reclaimable_slack"], SLACK_4K)
        self.assertEqual(plan["total_reclaimable_bytes"], 5000)
        self.assertEqual(plan["total_reclaimable_physical"], 5000 + SLACK_4K)

    def test_an_exfat_volume_is_worked_out_at_its_own_cluster(self) -> None:
        plan = self.plan(volume(self.root, 524_288, "exfat"))
        self.assertEqual((plan["cluster_size"], plan["total_reclaimable_slack"]), (524_288, SLACK_512K))
        plan = self.plan(volume(self.root, 131_072, "exfat"))
        self.assertEqual(plan["cluster_size"], 131_072)
        self.assertEqual(plan["total_reclaimable_slack"], calculate_slack_bytes(5000, 131_072))

    def test_a_cluster_size_that_cannot_be_read_falls_back_to_512_kb(self) -> None:
        for detected in (volume(self.root, 0), volume(self.root, 0, "unknown")):
            with self.subTest(detected=detected.fs_type):
                plan = self.plan(detected)
                self.assertEqual(plan["cluster_size"], CLUSTER_SIZE_BYTES)
                self.assertEqual(plan["total_reclaimable_slack"], SLACK_512K)

    def test_the_text_report_says_which_cluster_the_slack_is_at(self) -> None:
        out, _ = self.run_dup(volume(self.root, 4096))
        line = next(line for line in out.splitlines() if line.startswith("Reclaimable Cluster Slack"))
        self.assertEqual(line, "Reclaimable Cluster Slack: 0.00 MB (at 4 KB clusters)")
        out, _ = self.run_dup(volume(self.root, 0, "unknown"))
        line = next(line for line in out.splitlines() if line.startswith("Reclaimable Cluster Slack"))
        self.assertEqual(line, "Reclaimable Cluster Slack: 0.50 MB (at 512 KB clusters)")

    def test_an_odd_cluster_size_is_given_in_bytes(self) -> None:
        out, _ = self.run_dup(volume(self.root, 1536))
        self.assertIn("(at 1536 B clusters)", out)

    def test_nothing_else_in_the_plan_depends_on_the_cluster(self) -> None:
        small, large = self.plan(volume(self.root, 4096)), self.plan(volume(self.root, 524_288, "exfat"))
        for plan in (small, large):
            for key in ("elapsed_seconds", "cluster_size", "total_reclaimable_slack", "total_reclaimable_physical"):
                plan.pop(key)
            for group in plan["duplicate_groups"]:
                group.pop("reclaimable_slack")
        self.assertEqual(small, large)


class TestDetectorCarriesItsCluster(_Drive):
    def test_the_plan_names_the_cluster_it_was_worked_out_at(self) -> None:
        for cluster in (4096, CLUSTER_SIZE_BYTES):
            with self.subTest(cluster=cluster):
                plan = DuplicateDetector(str(self.root), cluster_size=cluster).generate_reclamation_plan()
                self.assertEqual(plan["cluster_size"], cluster)

    def test_the_default_is_still_the_exfat_cluster(self) -> None:
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual((plan["cluster_size"], plan["total_reclaimable_slack"]), (CLUSTER_SIZE_BYTES, SLACK_512K))


class TestCheckupDup(SmartDriveTestCase):
    def checkup(self, detected_block_size: int, include_dup: bool = True) -> Tuple[Any, Dict[str, Any]]:
        drive = self.create_mock_drive()
        home = self.test_dir / "home"
        (home / "AppData" / "Roaming").mkdir(parents=True)
        fake_temp = self.test_dir / "sys_temp"
        fake_temp.mkdir()
        detected = volume(drive, detected_block_size)
        with patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])), \
                patch("smart_drive.cli.cmd_checkup.detect_filesystem", return_value=detected), \
                patch("smart_drive.cli.cmd_checkup.DuplicateDetector", wraps=DuplicateDetector) as detector_class, \
                patch("smart_drive.core.relink._running_processes", return_value=set()), \
                patch("smart_drive.core.relink.tempfile.gettempdir", return_value=str(fake_temp)):
            report = run_checkup(
                root=str(drive),
                include_dup=include_dup,
                lang="en",
                home=home,
                app_data=home / "AppData" / "Roaming",
                reader=RegistryReader(tree={}),
                scan_c_caches=False,
            )
        return detector_class, report

    def test_the_duplicate_scan_uses_the_detected_cluster(self) -> None:
        detector_class, report = self.checkup(4096)
        (call,) = detector_class.call_args_list
        self.assertEqual(call.kwargs["cluster_size"], 4096)
        self.assertEqual(report["filesystem"]["cluster_size_bytes"], 4096)  # the figure the rest of the report uses
        self.assertTrue(report["duplicates"]["enabled"])
        self.assertGreater(report["duplicates"]["duplicate_group_count"], 0)

    def test_a_cluster_size_that_cannot_be_read_falls_back_to_512_kb(self) -> None:
        detector_class, report = self.checkup(0)
        (call,) = detector_class.call_args_list
        self.assertEqual(call.kwargs["cluster_size"], CLUSTER_SIZE_BYTES)
        self.assertEqual(report["filesystem"]["cluster_size_bytes"], CLUSTER_SIZE_BYTES)

    def test_without_the_flag_no_duplicate_scan_is_made(self) -> None:
        detector_class, report = self.checkup(4096, include_dup=False)
        detector_class.assert_not_called()
        self.assertFalse(report["duplicates"]["enabled"])


class TestMcpFindDuplicates(_Drive):
    def setUp(self) -> None:
        super().setUp()
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def find(self, detected: FilesystemInfo, **args: Any) -> Dict[str, Any]:
        with patch("smart_drive.mcp.server.detect_filesystem", return_value=detected) as detect:
            res = self.server.handle_ssd_find_duplicates(args)
        detect.assert_called_once_with(str(self.root))  # (the storage root, whichever folder is scanned)
        return res

    def test_an_ntfs_volume_is_worked_out_at_4_kb(self) -> None:
        res = self.find(volume(self.root, 4096))
        self.assertEqual(res["cluster_size"], 4096)
        self.assertEqual(res["total_reclaimable_slack"], SLACK_4K)
        self.assertEqual(res["total_reclaimable_physical"], 5000 + SLACK_4K)
        self.assertEqual(res["duplicate_groups"][0]["reclaimable_slack"], SLACK_4K)

    def test_a_cluster_size_that_cannot_be_read_falls_back_to_512_kb(self) -> None:
        for detected in (volume(self.root, 0), volume(self.root, 0, "unknown")):
            with self.subTest(detected=detected.fs_type):
                res = self.find(detected)
                self.assertEqual(res["cluster_size"], CLUSTER_SIZE_BYTES)
                self.assertEqual(res["total_reclaimable_slack"], SLACK_512K)

    def test_a_sub_dir_is_worked_out_at_the_cluster_of_the_storage_root(self) -> None:
        res = self.find(volume(self.root, 4096), sub_dir="a")
        self.assertEqual(res["duplicate_group_count"], 0)  # one copy in a/, the other is in b/
        sub = self.root / "a" / "inner"
        sub.mkdir()
        (sub / "again.bin").write_bytes(PAYLOAD)
        res = self.find(volume(self.root, 4096), sub_dir="a")
        self.assertEqual((res["cluster_size"], res["total_reclaimable_slack"]), (4096, SLACK_4K))

    def test_the_other_fields_keep_their_values(self) -> None:
        res = self.find(volume(self.root, 4096), limit=5)
        self.assertEqual((res["limit"], res["offset"], res["total_groups"], res["duplicate_file_count"]), (5, 0, 1, 2))
        self.assertEqual((res["has_more"], res["next_offset"], res["total_reclaimable_bytes"]), (False, None, 5000))

    def test_the_description_says_what_the_result_holds(self) -> None:
        """An agent reads the description to decide what the numbers mean, so it must match the result."""
        tool = next(tool for tool in TOOLS if tool["name"] == "ssd_find_duplicates")
        description = tool["description"]
        res = self.find(volume(self.root, 4096))
        self.assertEqual((res["cluster_size"], res["cluster_size_source"]), (4096, "detected"))
        self.assertNotIn("512KB physical", description)
        self.assertIn("real cluster size", description)
        # Every name it gives is a field of the result (or an argument of the tool), and it gives the ones that matter
        named = set(re.findall(r"[a-z]+(?:_[a-z]+)+", description))
        self.assertEqual(named - set(res) - set(tool["inputSchema"]["properties"]), set())
        needed = {"total_reclaimable_bytes", "total_reclaimable_slack", "cluster_size", "cluster_size_source"}
        self.assertEqual(needed - named, set())


if __name__ == "__main__":
    unittest.main()
