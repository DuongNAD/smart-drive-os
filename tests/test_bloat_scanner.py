"""tests/test_bloat_scanner.py - Tests for Dev-Artifact Bloat Scanner and Safe Purge."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.bloat_scanner import BloatScanner, purge_bloat
from smart_drive.core.system_refs import SystemRefIndex


class TestBloatScanner(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_bloat_test_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _write(self, rel: str, data: bytes = b"x") -> Path:
        p = self.root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_detects_all_six_kinds_including_custom_cargo_tag(self) -> None:
        cargo_tag = b"Signature: 8a477f597d28d172789f06886806bc55\n# Created by cargo.\n"
        self._write("rust_app/Cargo.toml", b"[package]\nname='rust_app'\n")
        self._write("rust_app/target/CACHEDIR.TAG", cargo_tag)
        self._write("rust_app/target/debug/lib.rlib", b"A" * 100)
        self._write("ox_claude/target-196/CACHEDIR.TAG", cargo_tag)
        self._write("ox_claude/target-196/debug/app.bin", b"B" * 200)
        self._write("web/package.json", b"{}")
        self._write("web/node_modules/pkg/index.js", b"C" * 50)
        self._write("web/.next/cache/chunk.js", b"D" * 50)
        self._write("php/composer.json", b"{}")
        self._write("php/vendor/autoload.php", b"E" * 50)
        self._write("py/.venv/pyvenv.cfg", b"home = /usr/bin\n")
        self._write("py/.venv/Lib/mod.py", b"F" * 50)
        self._write(".rustup/toolchains/stable/share/doc/rust/html/index.html", b"G" * 50)

        scanner = BloatScanner(
            str(self.root),
            cluster_size=524_288,
            system_refs=SystemRefIndex([]),
            mcp_paths=[],
            running_exes=[],
        )
        rep = scanner.scan()
        kinds_found = {f["kind"] for f in rep["findings"]}
        rels_found = {f["rel_path"] for f in rep["findings"]}

        self.assertEqual(
            kinds_found,
            {"rust-target", "node-modules", "composer-vendor", "python-venv", "web-build", "toolchain-docs"},
        )
        self.assertIn("rust_app/target", rels_found)
        self.assertIn("ox_claude/target-196", rels_found)
        self.assertIn("web/node_modules", rels_found)
        self.assertIn("web/.next", rels_found)
        self.assertIn("php/vendor", rels_found)
        self.assertIn("py/.venv", rels_found)
        self.assertIn(".rustup/toolchains/stable/share/doc/rust/html", rels_found)

    def test_cluster_math_includes_directory_cluster_overhead(self) -> None:
        self._write("web/package.json", b"{}")
        self._write("web/node_modules/a/b/file1.js", b"x")
        self._write("web/node_modules/a/b/file2.js", b"y")

        rep_512k = BloatScanner(
            str(self.root), cluster_size=524_288, system_refs=SystemRefIndex([]), mcp_paths=[], running_exes=[]
        ).scan()
        rep_4k = BloatScanner(
            str(self.root), cluster_size=4096, system_refs=SystemRefIndex([]), mcp_paths=[], running_exes=[]
        ).scan()

        f512 = rep_512k["findings"][0]
        f4 = rep_4k["findings"][0]
        self.assertEqual(f512["file_count"], 2)
        self.assertEqual(f512["dir_count"], 3)  # node_modules, a, b
        self.assertEqual(f512["allocated_bytes"], 2 * 524_288)
        self.assertEqual(f512["dir_allocated_bytes"], 3 * 524_288)
        self.assertEqual(f512["total_physical_bytes"], 5 * 524_288)
        self.assertEqual(f4["total_physical_bytes"], 5 * 4096)

    def test_active_process_splits_rust_target_by_profile(self) -> None:
        self._write("ox/Cargo.toml", b"[package]\nname='ox'\n")
        live_bin = self._write("ox/target/release/ox.exe", b"MZ")
        self._write("ox/target/debug/incremental/data.bin", b"Z" * 100)

        scanner = BloatScanner(
            str(self.root),
            cluster_size=4096,
            system_refs=SystemRefIndex([]),
            mcp_paths=[],
            running_exes=[str(live_bin)],
        )
        rep = scanner.scan()
        by_rel = {f["rel_path"]: f for f in rep["findings"]}

        self.assertIn("ox/target/release", by_rel)
        self.assertIn("ox/target/debug", by_rel)
        self.assertFalse(by_rel["ox/target/release"]["reclaimable"])
        self.assertEqual(by_rel["ox/target/release"]["blocked_reason"], "active_process")
        self.assertTrue(by_rel["ox/target/debug"]["reclaimable"])

        # Dry run changes nothing; apply deletes only debug and preserves running release
        dry = purge_bloat(rep, str(self.root), dry_run=True)
        self.assertEqual(dry["planned_count"], 1)
        self.assertEqual(dry["items"], ["ox/target/debug"])
        self.assertTrue((self.root / "ox/target/debug").exists())

        applied = purge_bloat(rep, str(self.root), dry_run=False)
        self.assertEqual(applied["deleted_count"], 1)
        self.assertEqual(applied["failed_count"], 0)
        self.assertFalse((self.root / "ox/target/debug").exists())
        self.assertTrue(live_bin.is_file())

    def test_mcp_and_git_tracked_items_are_blocked_and_never_deleted(self) -> None:
        mcp_nm = self._write("mcp/node_modules/pkg/index.js", b"1")
        self._write("mcp/package.json", b"{}")
        free_nm = self._write("scratch/node_modules/pkg/index.js", b"2")
        self._write("scratch/package.json", b"{}")

        repo = self.root / "php_repo"
        self._write("php_repo/composer.json", b"{}")
        self._write("php_repo/vendor/autoload.php", b"<?php")
        if shutil.which("git"):
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "add", "composer.json", "vendor/autoload.php"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        scanner = BloatScanner(
            str(self.root),
            cluster_size=4096,
            system_refs=SystemRefIndex([]),
            mcp_paths=[str(self.root / "mcp/dist/index.mjs"), str(mcp_nm)],
            running_exes=[],
        )
        rep = scanner.scan()
        by_rel = {f["rel_path"]: f for f in rep["findings"]}

        self.assertFalse(by_rel["mcp/node_modules"]["reclaimable"])
        self.assertEqual(by_rel["mcp/node_modules"]["blocked_reason"], "mcp_server")
        self.assertTrue(by_rel["scratch/node_modules"]["reclaimable"])
        if shutil.which("git"):
            self.assertFalse(by_rel["php_repo/vendor"]["reclaimable"])
            self.assertEqual(by_rel["php_repo/vendor"]["blocked_reason"], "git_tracked")

        purge_res = purge_bloat(rep, str(self.root), dry_run=False)
        self.assertFalse(free_nm.exists())
        self.assertTrue(mcp_nm.is_file())
        if shutil.which("git"):
            self.assertTrue((repo / "vendor/autoload.php").is_file())
            self.assertGreaterEqual(purge_res["skipped_blocked"], 2)

    def test_cli_bloat_kind_filter_and_json_output(self) -> None:
        self._write("app/package.json", b"{}")
        self._write("app/node_modules/a.js", b"x")
        self._write("rust/Cargo.toml", b"[package]\nname='r'\n")
        self._write("rust/target/debug/a.o", b"y")

        out = io.StringIO()
        with patch("smart_drive.core.bloat_scanner.collect_registered_mcp_paths", return_value=set()), \
                patch("smart_drive.core.bloat_scanner.running_executable_paths", return_value=set()), \
                patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])):
            with contextlib.redirect_stdout(out):
                rc = cli_main(["bloat", "--root", str(self.root), "--kind", "node-modules", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual([f["rel_path"] for f in data["findings"]], ["app/node_modules"])


if __name__ == "__main__":
    unittest.main()
