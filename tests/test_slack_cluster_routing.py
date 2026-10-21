"""tests/test_slack_cluster_routing.py - one helper decides the cluster size slack is worked out at.

Regression: "the volume's allocation unit when it was read, otherwise the 512 KB exFAT cluster" was written out five
times (in `audit`, `checkup`, `dup`, the MCP tool `ssd_find_duplicates` and AutoZoner, which `organize` and
`ssd_auto_organize` build), and `ssd_audit` had none of it. It now lives in `fsinfo.slack_cluster` and every caller asks
it. With the fallback patched to 64 KB and a volume whose cluster cannot be read, each of them works at 64 KB and says
"assumed", so a caller that decides for itself is caught here, and a static guard catches the expression being written
out again. `dup --json`, `checkup --json` and `ssd_find_duplicates` also say where the cluster size came from
(`cluster_size_source`), as `audit --json` and `ssd_audit` do.
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
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.cli.main import main
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import calculate_slack_bytes
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.fsinfo import FilesystemInfo
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from smart_drive.mcp.server import SmartDriveMCPServer

# `smart_drive.cli` re-exports the `main` function, so the attribute `smart_drive.cli.main` is that function and not the
# submodule; the modules whose names the code under test looks up are patched through the module objects instead.
AUDITOR = importlib.import_module("smart_drive.core.auditor")
AUTO_ZONER = importlib.import_module("smart_drive.core.auto_zoner")
CLI_AUDIT = importlib.import_module("smart_drive.cli.cmd_audit")
CLI_CHECKUP = importlib.import_module("smart_drive.cli.cmd_checkup")
CLI_MAIN = importlib.import_module("smart_drive.cli.main")
FSINFO = importlib.import_module("smart_drive.core.fsinfo")
MCP_SERVER = importlib.import_module("smart_drive.mcp.server")
RELINK = importlib.import_module("smart_drive.core.relink")

ROOT = Path(__file__).resolve().parent.parent

# Two copies of 5,000 bytes. 64 KB is neither a cluster size the tests detect (4 KB) nor the real fallback (512 KB), so a
# caller that keeps a fallback of its own cannot pass for one that asks the helper.
PAYLOAD = b"B" * 5000
DETECTED = 4096
FALLBACK = 65_536

# What a caller worked at, and the source it reported (None where its output reports none)
Worked = Tuple[int, Optional[str]]

# The fallback written out by hand, as it was: `info.block_size if info.block_size > 0 else CLUSTER_SIZE_BYTES`
SPELLED_OUT = re.compile(r"block_size\s+if\s+[\w.]*block_size\s*>\s*0\s+else|block_size\s+or\s+CLUSTER_SIZE")


def volume(path: Any, block_size: int, fs_type: str = "ntfs") -> FilesystemInfo:
    return FilesystemInfo(str(path), fs_type, block_size, str(path), "win32" if fs_type != "unknown" else "unknown")


def slack(cluster: int) -> int:
    """The slack of one 5,000-byte file at this cluster size."""
    return calculate_slack_bytes(5000, cluster)


class _Drive(unittest.TestCase):
    """A drive holding two copies of a file, `a/copy.bin` and `b/copy.bin`, and an MCP server on it."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_slack_routing_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "drive"
        for rel in ("a/copy.bin", "b/copy.bin"):
            path = self.root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(PAYLOAD)
        refs = patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([]))
        refs.start()
        self.addCleanup(refs.stop)
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def call_tool(self, name: str, **arguments: Any) -> Dict[str, Any]:
        """A tool as an agent calls it: a JSON-RPC tools/call, the result parsed out of the text content."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        with contextlib.redirect_stdout(io.StringIO()):  # the server also writes each reply to its stdout
            reply = self.server.handle_request(request)
        self.assertIsNotNone(reply)
        result = reply["result"]
        self.assertNotIn("isError", result, result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    # Each caller below works on `detected` and returns the cluster size its figures were worked out at, after checking
    # that the figures really are the slack at that size, and the source its output gives for it.

    def audit_json(self, detected: FilesystemInfo) -> Worked:
        out = io.StringIO()
        with patch.object(CLI_AUDIT, "detect_filesystem", return_value=detected), \
                patch.object(AUDITOR, "detect_filesystem", return_value=detected):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["audit", "--root", str(self.root), "--json"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["total_slack_bytes"], 2 * slack(data["cluster_size"]))
        return data["cluster_size"], data["cluster_size_source"]

    def ssd_audit(self, detected: FilesystemInfo) -> Worked:
        with patch.object(MCP_SERVER, "detect_filesystem", return_value=detected), \
                patch.object(AUDITOR, "detect_filesystem", return_value=detected):
            result = self.call_tool("ssd_audit")
        self.assertEqual(result["total_slack_bytes"], 2 * slack(result["cluster_size"]))
        return result["cluster_size"], result["cluster_size_source"]

    def dup_json(self, detected: FilesystemInfo) -> Worked:
        out = io.StringIO()
        with patch.object(CLI_MAIN, "detect_filesystem", return_value=detected):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["dup", "--root", str(self.root), "--json"]), 0)
        plan = json.loads(out.getvalue())
        self.assertEqual(plan["total_reclaimable_slack"], slack(plan["cluster_size"]))
        return plan["cluster_size"], plan["cluster_size_source"]

    def ssd_find_duplicates(self, detected: FilesystemInfo) -> Worked:
        with patch.object(MCP_SERVER, "detect_filesystem", return_value=detected):
            result = self.call_tool("ssd_find_duplicates")
        self.assertEqual(result["total_reclaimable_slack"], slack(result["cluster_size"]))
        return result["cluster_size"], result["cluster_size_source"]

    def checkup_json(self, detected: FilesystemInfo) -> Worked:
        home = self.base / "home"
        (home / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
        fake_temp = self.base / "sys_temp"
        fake_temp.mkdir(exist_ok=True)
        with patch.object(CLI_CHECKUP, "detect_filesystem", return_value=detected), \
                patch.object(CLI_CHECKUP, "StorageAuditor", wraps=StorageAuditor) as auditor_class, \
                patch.object(CLI_CHECKUP, "AutoZoner", wraps=AutoZoner) as zoner_class, \
                patch.object(CLI_CHECKUP, "DuplicateDetector", wraps=DuplicateDetector) as detector_class, \
                patch.object(RELINK, "_running_processes", return_value=set()), \
                patch.object(RELINK.tempfile, "gettempdir", return_value=str(fake_temp)):
            report = run_checkup(
                root=str(self.root),
                include_dup=True,
                lang="en",
                home=home,
                app_data=home / "AppData" / "Roaming",
                reader=RegistryReader(tree={}),
                scan_c_caches=False,
            )
        cluster = report["filesystem"]["cluster_size_bytes"]
        for built in (auditor_class, zoner_class, detector_class):  # the three engines checkup builds work at it too
            (call,) = built.call_args_list
            self.assertEqual(call.kwargs["cluster_size"], cluster)
        return cluster, report["filesystem"]["cluster_size_source"]

    def auto_zoner(self, detected: FilesystemInfo) -> Worked:
        with patch.object(AUTO_ZONER, "detect_filesystem", return_value=detected) as detect:
            zoner = AutoZoner(str(self.root), system_refs=SystemRefIndex([]))
        detect.assert_called_once_with(str(self.root))
        return zoner.cluster_size, None

    def callers(self) -> List[Tuple[str, Callable[[FilesystemInfo], Worked], bool]]:
        """Everything that works cluster slack out, and whether its output says where the cluster size came from."""
        return [
            ("audit --json", self.audit_json, True),
            ("ssd_audit", self.ssd_audit, True),
            ("dup --json", self.dup_json, True),
            ("ssd_find_duplicates", self.ssd_find_duplicates, True),
            ("checkup --json", self.checkup_json, True),
            ("AutoZoner", self.auto_zoner, False),
        ]


class TestEveryCallerAsksTheHelper(_Drive):
    def test_a_cluster_that_cannot_be_read_is_worked_out_at_the_one_fallback(self) -> None:
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            for name, call, reports_source in self.callers():
                for fs_type in ("unknown", "ntfs"):
                    with self.subTest(caller=name, fs_type=fs_type):
                        worked = call(volume(self.root, 0, fs_type))
                        self.assertEqual(worked, (FALLBACK, "assumed" if reports_source else None))

    def test_a_detected_cluster_is_used_as_it_is_by_all_of_them(self) -> None:
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            for name, call, reports_source in self.callers():
                with self.subTest(caller=name):
                    worked = call(volume(self.root, DETECTED))
                    self.assertEqual(worked, (DETECTED, "detected" if reports_source else None))

    def test_an_explicit_cluster_size_is_used_without_asking_the_volume(self) -> None:
        with patch.object(AUTO_ZONER, "detect_filesystem") as detect:
            zoner = AutoZoner(str(self.root), cluster_size=8192, system_refs=SystemRefIndex([]))
        detect.assert_not_called()
        self.assertEqual(zoner.cluster_size, 8192)


class TestTheFallbackIsWrittenOnce(unittest.TestCase):
    def test_the_pattern_finds_the_spellings_it_is_meant_to_find(self) -> None:
        for spelled in (
            "real_cluster = fs_info.block_size if fs_info.block_size > 0 else CLUSTER_SIZE_BYTES",
            "self.cluster_size = info.block_size if info.block_size > 0 else CLUSTER_SIZE_BYTES",
            "cluster = (\n    info.block_size\n    if info.block_size > 0\n    else CLUSTER_SIZE_BYTES\n)",
            "cluster = info.block_size or CLUSTER_SIZE_BYTES",
        ):
            with self.subTest(spelled=spelled):
                self.assertTrue(SPELLED_OUT.search(spelled))
        for fine in (
            "cluster_size_bytes=info.block_size,",
            '"allocation_unit_bytes": fs_info.block_size,',
            "if self.block_size >= 1024 and self.block_size % 1024 == 0:",
            "cluster, source = slack_cluster(detect_filesystem(root))",
        ):
            with self.subTest(fine=fine):
                self.assertFalse(SPELLED_OUT.search(fine))

    def test_no_module_but_fsinfo_spells_the_fallback_out(self) -> None:
        package = ROOT / "smart_drive"
        offenders = []
        for path in sorted(package.rglob("*.py")):
            if path == package / "core" / "fsinfo.py":
                continue
            text = path.read_text(encoding="utf-8")
            for found in SPELLED_OUT.finditer(text):
                line = text.count("\n", 0, found.start()) + 1
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
        self.assertEqual(offenders, [], "ask fsinfo.slack_cluster() for the cluster size instead of deciding it here")


class TestReadmes(unittest.TestCase):
    def test_both_readmes_name_the_source_next_to_the_cluster_size_of_the_duplicate_finders(self) -> None:
        for name in ("README.md", "README_VN.md"):
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            for start in ("| `dup` |", "- `ssd_find_duplicates`:"):
                line = next(line for line in lines if line.startswith(start))
                for word in ("`cluster_size`", "`cluster_size_source`"):
                    with self.subTest(readme=name, line=start, word=word):
                        self.assertIn(word, line)


if __name__ == "__main__":
    unittest.main()
