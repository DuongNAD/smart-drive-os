"""tests/test_junk_mcp_and_process_protection.py - MCP servers and live running executables are never purged."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.core.config import JunkTier
from smart_drive.core.junk_detector import (
    KEPT_ACTIVE_PROCESS,
    KEPT_MCP_SERVER,
    JunkDetector,
)
from smart_drive.core.processes import executables_under_path, running_executable_paths
from smart_drive.core.system_refs import SystemRefIndex


class TestProcessExecutableHelpers(unittest.TestCase):
    def test_executables_under_path_matches_exact_directory_boundary(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="sd_proc_helper_")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        target = base / "App" / "Target"
        inside = target / "Release" / "server"
        sibling = base / "App" / "Target2" / "Release" / "other"
        unrelated = base / "System" / "cmd"
        running = [str(inside), str(sibling), str(unrelated)]

        self.assertEqual(executables_under_path(str(target), running_exes=running), [str(inside)])
        self.assertEqual(executables_under_path(str(inside), running_exes=running), [str(inside)])
        # Sibling directory sharing a string prefix must NOT match
        self.assertEqual(executables_under_path(str(target), running_exes=[str(sibling)]), [])

    @unittest.skipUnless(
        sys.platform == "win32",
        "Windows drive-letter paths and case-insensitivity apply on Windows only",
    )
    def test_executables_under_path_windows_long_prefix_and_case_insensitive(self) -> None:
        running = [
            r"\\?\D:\App\Target\Release\server.exe",
            r"D:\App\Target2\Release\other.exe",
            r"C:\Windows\System32\cmd.exe",
        ]
        matched = executables_under_path(r"d:/app/target", running_exes=running)
        self.assertEqual(len(matched), 1)
        self.assertTrue(matched[0].lower().endswith(r"\app\target\release\server.exe"))

        # Sibling directory sharing a string prefix must NOT match
        self.assertEqual(executables_under_path(r"D:\App\Target", running_exes=[r"D:\App\Target2\other.exe"]), [])

    def test_running_executable_paths_returns_non_empty_set_for_current_process(self) -> None:
        paths = running_executable_paths()
        self.assertIsInstance(paths, set)
        self.assertTrue(len(paths) > 0)


class TestJunkDetectorMcpAndProcessProtection(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_mcp_proc_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _write(self, rel: str, text: str = "x") -> Path:
        p = self.root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_registered_mcp_dist_is_kept_while_unregistered_dist_is_reported(self) -> None:
        self._write("polaris_mcp/package.json", "{}")
        entry = self._write("polaris_mcp/dist/index.mjs", "console.log('ok');")
        self._write("polaris_mcp/.ruff_cache/cache.bin", "cache")
        self._write("web_app/package.json", "{}")
        self._write("web_app/dist/bundle.js", "console.log('web');")

        # Write drive .mcp.json using cwd + relative arg to verify resolution
        mcp_cfg = {
            "mcpServers": {
                "polaris": {
                    "command": "node",
                    "args": ["dist/index.mjs"],
                    "cwd": str(self.root / "polaris_mcp"),
                }
            }
        }
        (self.root / ".mcp.json").write_text(json.dumps(mcp_cfg), encoding="utf-8")

        fake_home = self.root.parent / f"empty_home_{self.root.name}"
        fake_home.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)

        with patch("smart_drive.core.relink._resolve_locations", return_value=(fake_home, fake_home, False)):
            detector = JunkDetector(
                str(self.root),
                max_tier=JunkTier.TIER_2_DEV_CACHE,
                system_refs=SystemRefIndex([]),
                running_exes=[],
            )
            found = {item.rel_path.replace("\\", "/") for item in detector.find_junk()}

        self.assertEqual(found, {"web_app/dist", "polaris_mcp/.ruff_cache"})
        self.assertEqual(detector.kept.get(KEPT_MCP_SERVER), 1)
        self.assertTrue(entry.is_file())

    def test_running_executable_protects_build_and_dist_dirs(self) -> None:
        self._write("daemon_app/package.json", "{}")
        live_exe = self._write("daemon_app/build/worker.exe", "MZ")
        self._write("other_app/package.json", "{}")
        self._write("other_app/build/old.exe", "MZ")

        detector = JunkDetector(
            str(self.root),
            max_tier=JunkTier.TIER_2_DEV_CACHE,
            system_refs=SystemRefIndex([]),
            mcp_paths=[],
            running_exes=[str(live_exe)],
        )
        found = {item.rel_path.replace("\\", "/") for item in detector.find_junk()}
        self.assertEqual(found, {"other_app/build"})
        self.assertEqual(detector.kept.get(KEPT_ACTIVE_PROCESS), 1)

    def test_mcp_and_process_probes_are_lazy_when_nothing_looks_like_junk(self) -> None:
        self._write("docs/readme.txt", "hello")
        with patch("smart_drive.core.junk_detector.collect_registered_mcp_paths") as mock_mcp, \
                patch("smart_drive.core.junk_detector.running_executable_paths") as mock_proc, \
                patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])):
            items = JunkDetector(str(self.root)).find_junk()
        self.assertEqual(items, [])
        mock_mcp.assert_not_called()
        mock_proc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
