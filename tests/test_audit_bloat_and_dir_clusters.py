"""tests/test_audit_bloat_and_dir_clusters.py - Directory cluster overhead, dev-bloat rollup, and checkup guidance."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.fsinfo import FilesystemInfo
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from smart_drive.mcp.server import SmartDriveMCPServer


class TestAuditBloatAndDirClusters(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sd_audit_bloat_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "drive"
        self.root.mkdir(parents=True)

    def _write(self, rel: str, data: bytes = b"x") -> Path:
        p = self.root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_audit_reports_directory_allocated_bytes_and_no_double_count_bloat(self) -> None:
        self._write("app/node_modules/a/index.js", b"1" * 100)
        self._write("app/node_modules/a/node_modules/b/index.js", b"2" * 100)
        self._write("rust/target/debug/lib.rlib", b"3" * 200)
        self._write("docs/readme.txt", b"hello")

        cluster = 524_288
        rep = StorageAuditor(str(self.root), cluster_size=cluster).run_audit()

        # Total files = 4; total_allocated_bytes remains file-only for backwards compatibility
        self.assertEqual(rep["total_files"], 4)
        self.assertEqual(rep["total_allocated_bytes"], 4 * cluster)
        dir_count = rep["total_directories"]
        self.assertEqual(rep["directory_allocated_bytes"], dir_count * cluster)
        self.assertEqual(
            rep["estimated_total_allocated_bytes"],
            rep["total_allocated_bytes"] + rep["directory_allocated_bytes"],
        )

        bloat = rep["dev_bloat_summary"]
        # app/node_modules/a/node_modules/b must not be counted separately inside app/node_modules
        self.assertEqual(bloat["total_dirs_matched"], 2)
        self.assertEqual(bloat["total_files"], 3)
        self.assertIn("node-modules", bloat["by_kind"])
        self.assertIn("rust-target", bloat["by_kind"])

        ascii_rep = rep.to_ascii_table()
        self.assertIn("Directory Cluster Cost:", ascii_rep)

    def test_mcp_ssd_audit_passes_through_dir_overhead_and_dev_bloat(self) -> None:
        self._write("app/node_modules/pkg/index.js", b"1")
        server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)
        res = server.handle_ssd_audit({"compact": True})
        self.assertIn("directory_allocated_bytes", res)
        self.assertIn("estimated_total_allocated_bytes", res)
        self.assertIn("directory_allocated_formatted", res)
        self.assertIn("dev_bloat_summary", res)
        self.assertEqual(res["dev_bloat_summary"]["total_dirs_matched"], 1)

    def test_checkup_surfaces_exfat_shield_and_dev_bloat_in_vi_and_en(self) -> None:
        # > 1000 files in target would take time; instead create 1000? Wait: 1000 micro-files at 512 KB cluster = ~500 MiB!
        # Even faster: create 1,000 tiny files (~0.5 GiB physical at 512 KB cluster), or patch audit bloat bytes cleanly.
        for i in range(1000):
            self._write(f"rust/target/obj_{i}.o", b"o")
        self._write("docs/notes.txt", b"hi")

        home = self.tmp / "home"
        app_data = home / "AppData" / "Roaming"
        fake_temp = self.tmp / "sys_temp"
        app_data.mkdir(parents=True)
        fake_temp.mkdir()

        exfat_512k = FilesystemInfo(str(self.root), "exfat", 524_288, str(self.root), "mock")

        for lang, expected_shield, expected_bloat in (
            ("vi", "smart-drive shield", "smart-drive bloat"),
            ("en", "smart-drive shield", "smart-drive bloat"),
        ):
            with self.subTest(lang=lang):
                with patch("smart_drive.cli.cmd_checkup.detect_filesystem", return_value=exfat_512k), \
                        patch.dict("os.environ", {}, clear=True), \
                        patch("smart_drive.core.relink._running_processes", return_value=set()), \
                        patch("smart_drive.core.relink.tempfile.gettempdir", return_value=str(fake_temp)), \
                        patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])):
                    rep = run_checkup(
                        root=str(self.root),
                        lang=lang,
                        home=home,
                        app_data=app_data,
                        reader=RegistryReader(tree={}),
                        scan_c_caches=False,
                    )

                self.assertIn("dev_bloat", rep)
                self.assertIn("exfat_shield", rep)
                self.assertGreater(rep["storage"]["directory_allocated_bytes"], 0)
                self.assertTrue(rep["exfat_shield"]["needs_attention"])
                cmds = [s["command"] for s in rep["next_steps"]]
                self.assertIn(expected_shield, cmds)
                self.assertIn(expected_bloat, cmds)


if __name__ == "__main__":
    unittest.main()
