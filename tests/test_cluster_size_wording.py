"""tests/test_cluster_size_wording.py - every report that names a cluster size names it the way `audit` does.

Regression: `fsinfo.cluster_size_text` gives a cluster size as the reports give it: `4 KB`, `512 KB`, or `512 B` and
`1536 B` when it is not a whole number of KB (exFAT allows clusters from 512 B up). Some commands worked that text out
themselves, mostly by dividing by 1024, so in `checkup` a cluster of 512 B or of 1536 B read "1 KB". Each class below
runs one of those places at such clusters and checks the line it prints.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.cli.main import main
from smart_drive.core.fsinfo import FilesystemInfo
from smart_drive.core.health import SSDHealthReport, evaluate_health_warnings
from smart_drive.core.system_refs import RegistryReader

# The modules whose names the code under test looks up are patched through the module objects and not through dotted
# strings: `smart_drive.cli` re-exports the `main` function, so a string target that goes through the package can end
# at a function and not at the module.
CLI_CHECKUP = importlib.import_module("smart_drive.cli.cmd_checkup")
CLI_HEALTH = importlib.import_module("smart_drive.cli.cmd_health")
RELINK = importlib.import_module("smart_drive.core.relink")

# The text each cluster has to be given. It is written out here and not taken from `cluster_size_text`, the helper under
# test, so that a helper which goes wrong cannot make the reports agree with it.
WORDING: Dict[int, str] = {512: "512 B", 1536: "1536 B", 4096: "4 KB", 524_288: "512 KB"}

# The line of the `checkup` text report that names the filesystem and its cluster, in each language (the cluster's text
# goes where the `{}` is)
CHECKUP_LINES: Dict[str, str] = {
    "vi": "  - Hệ tệp & Cluster:   exFAT (cluster {})",
    "en": "  - Filesystem:         exFAT ({} cluster)",
}

# The exFAT notice of `health` is for clusters of 64 KB and more: what it has to call the ones tried here (66,048 B is
# 64 KB and 512 B)
NOTICE_WORDING: Dict[int, str] = {65_536: "64 KB", 66_048: "66048 B", 524_288: "512 KB"}


def volume(path: Any, block_size: int, fs_type: str = "exfat") -> FilesystemInfo:
    return FilesystemInfo(str(path), fs_type, block_size, str(path), "win32")


class TestCheckupTextReport(unittest.TestCase):
    """The text report of `checkup` gave the cluster as `size // 1024 or 1` KB: 512 B and 1536 B both read "1 KB"."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_cluster_wording_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "drive"
        self.root.mkdir()
        (self.root / "notes.txt").write_bytes(b"x" * 20)
        # What the broken-path scan of `checkup` reads off the machine: an empty home, an empty temp folder and no
        # running programs
        self.home = self.base / "home"
        self.app_data = self.home / "AppData" / "Roaming"
        self.app_data.mkdir(parents=True)
        fake_temp = self.base / "sys_temp"
        fake_temp.mkdir()
        for patcher in (
            patch.object(RELINK, "_running_processes", return_value=set()),
            patch.object(RELINK.tempfile, "gettempdir", return_value=str(fake_temp)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def checkup(self, cluster: int, lang: str) -> Dict[str, Any]:
        """`run_checkup` on the drive as a volume with this cluster, reporting in this language."""
        with patch.object(CLI_CHECKUP, "detect_filesystem", return_value=volume(self.root, cluster)):
            return run_checkup(
                root=str(self.root),
                lang=lang,
                home=self.home,
                app_data=self.app_data,
                reader=RegistryReader(tree={}),
                scan_c_caches=False,
            )

    def printed(self, report: Dict[str, Any]) -> List[str]:
        """The lines `checkup` prints for this report."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            CLI_CHECKUP._print_checkup(report)
        return out.getvalue().splitlines()

    def test_the_filesystem_line_names_the_cluster(self) -> None:
        for cluster, wording in WORDING.items():
            for lang, line in CHECKUP_LINES.items():
                with self.subTest(cluster=cluster, lang=lang):
                    report = self.checkup(cluster, lang)
                    self.assertEqual(report["lang"], lang)
                    # `checkup --json` prints this report as it is: it still gives the cluster in bytes
                    self.assertEqual(report["filesystem"]["cluster_size_bytes"], cluster)
                    self.assertIn(line.format(wording), self.printed(report))


class TestHealthCommand(unittest.TestCase):
    """The `Cluster Size:` line of `health` put KB after the byte count below 1 KB and dropped the rest above it.

    A cluster of 512 B read "512 bytes (512 KB)" and one of 1536 B read "1,536 bytes (1 KB)".
    """

    def health(self, cluster: int, *flags: str) -> str:
        """What `smart-drive health E:` prints for a volume with this cluster (the volume is the patched report)."""
        report = SSDHealthReport(
            drive_letter="E:",
            filesystem="exFAT",
            trim_enabled=True,
            trim_status_message="TRIM is enabled (DisableDeleteNotify = 0)",
            total_bytes=2000 * 1024 ** 3,
            free_bytes=1400 * 1024 ** 3,
            free_percent=70.0,
            cluster_size_bytes=cluster,
            warnings=[],
        )
        out = io.StringIO()
        with patch.object(CLI_HEALTH, "check_drive_health", return_value=report):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["health", "E:", *flags]), 0, out.getvalue())
        return out.getvalue()

    def test_the_cluster_size_line_names_the_cluster(self) -> None:
        for cluster, wording in WORDING.items():
            with self.subTest(cluster=cluster):
                self.assertIn(f"Cluster Size:        {cluster:,} bytes ({wording})", self.health(cluster).splitlines())

    def test_the_json_report_still_gives_the_cluster_in_bytes(self) -> None:
        for cluster in WORDING:
            with self.subTest(cluster=cluster):
                self.assertEqual(json.loads(self.health(cluster, "--json"))["cluster_size_bytes"], cluster)


class TestHealthClusterNotice(unittest.TestCase):
    """The exFAT notice of `health` wrote the cluster as `size // 1024` and "KB" with no space: "512KB clusters".

    A cluster that is not a whole number of KB lost what was left over (66,048 B read "64KB"). The notice only appears
    for clusters of 64 KB and more, so a cluster of 512 B or of 1536 B never reaches it.
    """

    def warnings(self, cluster: int) -> List[str]:
        """What `evaluate_health_warnings` says about an exFAT volume with this cluster and plenty of free space."""
        return evaluate_health_warnings(
            free_percent=70.0,
            free_bytes=1400 * 1024 ** 3,
            total_bytes=2000 * 1024 ** 3,
            trim_enabled=True,
            filesystem="exFAT",
            cluster_size_bytes=cluster,
        )

    def test_the_notice_names_the_cluster(self) -> None:
        for cluster, wording in NOTICE_WORDING.items():
            with self.subTest(cluster=cluster):
                warnings = self.warnings(cluster)
                self.assertEqual(len(warnings), 1, warnings)
                prefix = f"NOTICE: Drive is formatted as exFAT with {wording} clusters. "
                self.assertTrue(warnings[0].startswith(prefix), warnings[0])

    def test_a_cluster_below_64_kb_has_no_notice(self) -> None:
        for cluster in (512, 1536, 4096, 32_768):
            with self.subTest(cluster=cluster):
                self.assertEqual(self.warnings(cluster), [])


if __name__ == "__main__":
    unittest.main()
