"""tests/test_audit_cluster_source.py - `audit` and `ssd_audit` work cluster slack out at the drive's real cluster size.

Regression: `ssd_audit` built its StorageAuditor with the default 512 KB exFAT cluster on every drive, while
`smart-drive audit` asked `detect_filesystem` for the volume's own allocation unit. On an NTFS drive (4 KB clusters) one
folder holding 1.35 MB showed 157 KB of slack from `audit` and 34.7 MB from the tool an agent actually calls, and
nothing in the result said which cluster size the figure had been worked out at. Both now take the size from
`fsinfo.slack_cluster` (the volume's own allocation unit, the 512 KB exFAT cluster only when it cannot be read) and say
where it came from: `cluster_size_source` is "detected" or "assumed" in `audit --json` and in `ssd_audit`, which also
returns `cluster_size`, `filesystem` and `slack_model_note`.
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
from typing import Any, Callable, Dict, List
from unittest.mock import patch

from smart_drive.cli.main import main
from smart_drive.core.config import CLUSTER_SIZE_BYTES, calculate_slack_bytes
from smart_drive.core.fsinfo import (
    CLUSTER_SIZE_ASSUMED,
    CLUSTER_SIZE_DETECTED,
    FilesystemInfo,
    detect_filesystem,
    slack_cluster,
)
from smart_drive.mcp.server import TOOLS, SmartDriveMCPServer

# `smart_drive.cli` re-exports the `main` function, so the attribute `smart_drive.cli.main` is that function and not the
# submodule; the modules whose names the code under test looks up are patched through the module objects instead.
CLI_AUDIT = importlib.import_module("smart_drive.cli.cmd_audit")
AUDITOR = importlib.import_module("smart_drive.core.auditor")
FSINFO = importlib.import_module("smart_drive.core.fsinfo")
MCP_SERVER = importlib.import_module("smart_drive.mcp.server")

ROOT = Path(__file__).resolve().parent.parent

# A file of 5,000 bytes takes two 4 KB clusters (8,192 B, 3,192 B of them slack) or one 512 KB cluster (519,288 B slack)
PAYLOAD = b"B" * 5000
ALLOCATED_4K = 8192
SLACK_4K = 3192
SLACK_512K = 524_288 - 5000
FILES = 2  # the tree below holds two of them

# What `ssd_audit` returned before the cluster fields were added: all of it stays, in both modes
KEYS_BEFORE = (
    "root_path", "total_files", "total_directories", "total_logical_bytes", "total_allocated_bytes",
    "total_slack_bytes", "total_slack_percentage", "taxonomies", "categories", "top_slack_directories",
    "micro_file_hotspots", "total_recoverable_slack", "learning", "learning_summary",
)
COMPACT_KEYS_BEFORE = (
    "compact", "total_logical_formatted", "total_slack_formatted", "total_recoverable_slack_formatted",
)
CLUSTER_KEYS = ("cluster_size", "cluster_size_source", "filesystem", "slack_model_note")
# The keys whose values do not depend on the cluster size the slack is worked out at
CLUSTER_FREE_KEYS = (
    "root_path", "total_files", "total_directories", "total_logical_bytes", "learning", "learning_summary",
    "compact", "total_logical_formatted",
)

Detector = Callable[[str], FilesystemInfo]


def volume(path: Any, block_size: int, fs_type: str = "ntfs") -> FilesystemInfo:
    return FilesystemInfo(str(path), fs_type, block_size, str(path), "win32" if fs_type != "unknown" else "unknown")


def everywhere(block_size: int, fs_type: str = "ntfs") -> Detector:
    """A detect_filesystem stand-in that finds the same kind of volume wherever it is asked."""
    return lambda path: volume(path, block_size, fs_type)


class _Tree(unittest.TestCase):
    """A drive holding two files of 5,000 bytes, `top.bin` and `sub/inner.bin`, and an MCP server on it."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_audit_cluster_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "drive"
        self.sub = self.root / "sub"
        for path in (self.root / "top.bin", self.sub / "inner.bin"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(PAYLOAD)
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def ssd_audit(self, **arguments: Any) -> Dict[str, Any]:
        """ssd_audit as an agent calls it: a JSON-RPC tools/call, the result parsed out of the text content."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ssd_audit", "arguments": arguments},
        }
        with contextlib.redirect_stdout(io.StringIO()):  # the server also writes each reply to its stdout
            reply = self.server.handle_request(request)
        self.assertIsNotNone(reply)
        result = reply["result"]
        self.assertNotIn("isError", result, result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])


class TestSlackCluster(unittest.TestCase):
    def test_a_volume_that_reports_its_cluster_gives_that_cluster(self) -> None:
        self.assertEqual(slack_cluster(volume("drive", 4096)), (4096, CLUSTER_SIZE_DETECTED))
        self.assertEqual(slack_cluster(volume("drive", 131_072, "exfat")), (131_072, CLUSTER_SIZE_DETECTED))

    def test_a_cluster_that_cannot_be_read_gives_the_exfat_cluster_the_tool_was_built_for(self) -> None:
        for fs_type in ("unknown", "ntfs"):
            with self.subTest(fs_type=fs_type):
                self.assertEqual(slack_cluster(volume("drive", 0, fs_type)), (CLUSTER_SIZE_BYTES, CLUSTER_SIZE_ASSUMED))
        self.assertEqual(CLUSTER_SIZE_BYTES, 524_288)

    def test_the_sources_are_the_words_the_reports_carry(self) -> None:
        self.assertEqual((CLUSTER_SIZE_DETECTED, CLUSTER_SIZE_ASSUMED), ("detected", "assumed"))

    def test_the_fallback_is_looked_up_when_asked_for(self) -> None:
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", 262_144):
            self.assertEqual(slack_cluster(volume("drive", 0)), (262_144, CLUSTER_SIZE_ASSUMED))
            self.assertEqual(slack_cluster(volume("drive", 4096)), (4096, CLUSTER_SIZE_DETECTED))


class TestAuditCommand(_Tree):
    def audit(self, detected: FilesystemInfo, *flags: str) -> str:
        out = io.StringIO()
        with patch.object(CLI_AUDIT, "detect_filesystem", return_value=detected) as detect, \
                patch.object(AUDITOR, "detect_filesystem", return_value=detected):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["audit", "--root", str(self.root), *flags])
        self.assertEqual(code, 0)
        detect.assert_called_once_with(str(self.root))
        return out.getvalue()

    def report(self, detected: FilesystemInfo) -> Dict[str, Any]:
        return json.loads(self.audit(detected, "--json"))

    def test_a_detected_cluster_is_named_as_detected(self) -> None:
        data = self.report(volume(self.root, 4096))
        self.assertEqual((data["cluster_size"], data["cluster_size_source"]), (4096, "detected"))
        self.assertEqual(data["total_logical_bytes"], FILES * 5000)
        self.assertEqual(data["total_allocated_bytes"], FILES * ALLOCATED_4K)
        self.assertEqual(data["total_slack_bytes"], FILES * SLACK_4K)

    def test_an_exfat_volume_is_worked_out_at_its_own_cluster(self) -> None:
        data = self.report(volume(self.root, 131_072, "exfat"))
        self.assertEqual((data["cluster_size"], data["cluster_size_source"]), (131_072, "detected"))
        self.assertEqual(data["total_slack_bytes"], FILES * calculate_slack_bytes(5000, 131_072))

    def test_a_cluster_size_that_cannot_be_read_is_named_as_assumed(self) -> None:
        for detected in (volume(self.root, 0), volume(self.root, 0, "unknown")):
            with self.subTest(fs_type=detected.fs_type):
                data = self.report(detected)
                self.assertEqual((data["cluster_size"], data["cluster_size_source"]), (524_288, "assumed"))
                self.assertEqual(data["total_slack_bytes"], FILES * SLACK_512K)

    def test_every_json_the_command_writes_carries_the_source(self) -> None:
        detected = volume(self.root, 4096)
        export, html = self.base / "out" / "audit.json", self.base / "out" / "audit.html"
        self.audit(detected, "--json", "--export", str(export))
        written = json.loads(export.read_text(encoding="utf-8"))
        with_html = json.loads(self.audit(detected, "--html", str(html), "--json"))
        self.assertTrue(html.is_file())
        for name, data in (("--export", written), ("--html --json", with_html)):
            with self.subTest(form=name):
                self.assertEqual((data["cluster_size"], data["cluster_size_source"]), (4096, "detected"))
        self.assertEqual(with_html["html_report_path"], str(html))


class TestMcpAudit(_Tree):
    def call(self, detect: Detector, **arguments: Any) -> Dict[str, Any]:
        """ssd_audit on a drive whose volumes `detect` describes; `self.asked` lists what the tool asked it about."""
        self.asked: List[str] = []

        def detect_for_the_tool(path: str) -> FilesystemInfo:
            self.asked.append(path)
            return detect(path)

        # The tool asks for the cluster itself, and the auditor asks again for the `filesystem` it reports
        with patch.object(MCP_SERVER, "detect_filesystem", side_effect=detect_for_the_tool), \
                patch.object(AUDITOR, "detect_filesystem", side_effect=detect):
            return self.ssd_audit(**arguments)

    def test_a_detected_cluster_is_worked_out_and_named_as_detected(self) -> None:
        result = self.call(everywhere(4096))
        self.assertEqual((result["cluster_size"], result["cluster_size_source"]), (4096, "detected"))
        self.assertEqual(result["total_logical_bytes"], FILES * 5000)
        self.assertEqual(result["total_allocated_bytes"], FILES * ALLOCATED_4K)
        self.assertEqual(result["total_slack_bytes"], FILES * SLACK_4K)
        self.assertEqual(result["filesystem"]["allocation_unit_bytes"], 4096)
        self.assertEqual(result["filesystem"]["type"], "ntfs")
        self.assertIsNone(result["slack_model_note"])
        self.assertEqual(self.asked, [str(self.root)])

    def test_an_exfat_volume_is_worked_out_at_its_own_cluster(self) -> None:
        result = self.call(everywhere(131_072, "exfat"))
        self.assertEqual((result["cluster_size"], result["cluster_size_source"]), (131_072, "detected"))
        self.assertEqual(result["total_slack_bytes"], FILES * calculate_slack_bytes(5000, 131_072))
        self.assertIsNone(result["slack_model_note"])  # (the figures are the volume's own: nothing to explain)

    def test_a_cluster_size_that_cannot_be_read_is_named_as_assumed(self) -> None:
        for fs_type in ("ntfs", "unknown"):
            with self.subTest(fs_type=fs_type):
                result = self.call(everywhere(0, fs_type))
                self.assertEqual((result["cluster_size"], result["cluster_size_source"]), (524_288, "assumed"))
                self.assertEqual(result["total_allocated_bytes"], FILES * 524_288)
                self.assertEqual(result["total_slack_bytes"], FILES * SLACK_512K)
                self.assertIsNotNone(result["slack_model_note"])

    def test_a_sub_dir_is_worked_out_at_the_cluster_detected_for_that_folder(self) -> None:
        def volumes(path: str) -> FilesystemInfo:  # the sub-folder is a volume of its own, mounted into the drive
            return volume(path, 131_072, "exfat") if Path(path) == self.sub else volume(path, 4096)

        whole = self.call(volumes)
        self.assertEqual(self.asked, [str(self.root)])
        self.assertEqual((whole["cluster_size"], whole["total_files"]), (4096, FILES))
        for argument in ("sub_dir", "directory"):
            with self.subTest(argument=argument):
                part = self.call(volumes, **{argument: "sub"})
                self.assertEqual(self.asked, [str(self.sub)])
                self.assertEqual((part["cluster_size"], part["cluster_size_source"]), (131_072, "detected"))
                self.assertEqual((part["root_path"], part["total_files"]), (str(self.sub), 1))
                self.assertEqual(part["total_slack_bytes"], calculate_slack_bytes(5000, 131_072))
                self.assertEqual(part["filesystem"]["type"], "exfat")

    def test_every_key_the_result_had_is_still_there_in_both_modes(self) -> None:
        compact, full = self.call(everywhere(4096)), self.call(everywhere(4096), compact=False)
        for key in KEYS_BEFORE + CLUSTER_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, compact)
                self.assertIn(key, full)
        for key in COMPACT_KEYS_BEFORE:
            with self.subTest(key=key):
                self.assertIn(key, compact)
                self.assertNotIn(key, full)
        for key in CLUSTER_KEYS:
            with self.subTest(key=key):
                self.assertEqual(compact[key], full[key])

    def test_the_cluster_fields_follow_the_root_path(self) -> None:
        for compact in (True, False):
            with self.subTest(compact=compact):
                keys = list(self.call(everywhere(4096), compact=compact))
                self.assertEqual(keys[:5], ["root_path", *CLUSTER_KEYS])

    def test_nothing_that_is_not_about_the_cluster_changes(self) -> None:
        small, large = self.call(everywhere(4096)), self.call(everywhere(0))
        for key in CLUSTER_FREE_KEYS:
            with self.subTest(key=key):
                self.assertEqual(small[key], large[key])
        self.assertNotEqual(small["total_allocated_bytes"], large["total_allocated_bytes"])


class TestAuditAndToolAgree(_Tree):
    """Nothing is patched: this runs on whatever volume the temp folder is on, and no CI runner has 512 KB clusters."""

    def test_the_command_and_the_tool_work_slack_out_at_the_same_cluster(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["audit", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0)
        command, tool = json.loads(out.getvalue()), self.ssd_audit()

        cluster, source = slack_cluster(detect_filesystem(str(self.root)))
        self.assertEqual((command["cluster_size"], command["cluster_size_source"]), (cluster, source))
        for key in (
            "cluster_size", "cluster_size_source", "total_logical_bytes", "total_allocated_bytes", "total_slack_bytes",
            "filesystem", "slack_model_note",
        ):
            with self.subTest(key=key):
                self.assertEqual(tool[key], command[key])
        self.assertEqual(tool["total_slack_bytes"], FILES * calculate_slack_bytes(5000, cluster))


class TestDocumentation(unittest.TestCase):
    def test_the_tool_description_no_longer_claims_a_512_kb_cluster(self) -> None:
        description = next(tool["description"] for tool in TOOLS if tool["name"] == "ssd_audit")
        self.assertNotIn("512KB exFAT cluster slack", description)
        self.assertIn("real cluster size", description)
        self.assertIn("cluster_size_source", description)

    def test_both_readmes_say_the_slack_is_worked_out_at_the_drives_real_cluster(self) -> None:
        for name in ("README.md", "README_VN.md"):
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            line = next(line for line in lines if line.startswith("- `ssd_audit`:"))
            for word in ("cluster_size", "cluster_size_source"):
                with self.subTest(readme=name, word=word):
                    self.assertIn(word, line)


if __name__ == "__main__":
    unittest.main()
