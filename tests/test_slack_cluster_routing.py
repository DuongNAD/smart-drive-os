"""tests/test_slack_cluster_routing.py - one helper decides the cluster size slack is worked out at.

Regression: "the volume's allocation unit when it was read, otherwise the 512 KB exFAT cluster" was written out five
times (in `audit`, `checkup`, `dup`, the MCP tool `ssd_find_duplicates` and AutoZoner, which `organize` and
`ssd_auto_organize` build), and `ssd_audit` had none of it. It now lives in `fsinfo.slack_cluster` and every caller asks
it. With the fallback patched to 64 KB and a volume whose cluster cannot be read, each of them works at 64 KB and says
"assumed", so a caller that decides for itself is caught here, and a static guard catches the expression being written
out again. `dup --json`, `checkup --json` and `ssd_find_duplicates` also say where the cluster size came from
(`cluster_size_source`), as `audit --json` and `ssd_audit` do.

The callers that size junk and snapshots never asked: `clean`, the MCP tool `ssd_clean`, `organize --clean`,
`ssd_auto_organize`'s clean step, `snapshot create` and `backup` built their JunkDetector, PurgeEngine and
SnapshotManager with the default 512 KB cluster on every drive, so on a 4 KB NTFS drive `clean --json` gave every file
of the junk 512 KB of `allocated_size` and `ssd_clean` called that the space the cleanup would free. They pass the
cluster `slack_cluster` gives now, and they are callers here too. `clean --json`, `ssd_clean` and the snapshot
manifests (`snapshot create --json`, `snapshot list --json`) say where the cluster size came from
(`cluster_size_source`), and a manifest from before it was recorded reads as 512 KB, assumed. `organize --clean` and
`ssd_auto_organize` work at the cluster their own plan reports, and `backup`, which reports no size, is given the real
cluster as well. Each caller has to leave the drive as it found it (a snapshot and a checkup keep state below it), so
that none shifts the figures of the next. A second static guard reads the source of `smart_drive/cli` and
`smart_drive/mcp` and fails on a call that builds one of those engines without `cluster_size=`.

`organize --json` and `ssd_auto_organize`, dry run and apply, reported that cluster without saying where it came from:
AutoZoner, which looks it up for both, dropped the source `slack_cluster` gave it. It keeps it now
(`cluster_size_source`; None when its caller gives the cluster size, as `checkup` does, which reports the source itself)
and both report it, so every caller here says where its cluster size came from.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import importlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Set, Tuple
from unittest.mock import patch

from smart_drive.cli.cmd_checkup import run_checkup
from smart_drive.cli.main import main
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import calculate_allocated_bytes, calculate_slack_bytes
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.fsinfo import FilesystemInfo
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine
from smart_drive.core.snapshot import BackupReport, SnapshotManager, SnapshotManifest
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex
from smart_drive.mcp.server import TOOLS, SmartDriveMCPServer

# `smart_drive.cli` re-exports the `main` function, so the attribute `smart_drive.cli.main` is that function and not the
# submodule; the modules whose names the code under test looks up are patched through the module objects instead.
AUDITOR = importlib.import_module("smart_drive.core.auditor")
AUTO_ZONER = importlib.import_module("smart_drive.core.auto_zoner")
CLI_AUDIT = importlib.import_module("smart_drive.cli.cmd_audit")
CLI_BACKUP = importlib.import_module("smart_drive.cli.cmd_backup")
CLI_CHECKUP = importlib.import_module("smart_drive.cli.cmd_checkup")
CLI_CLEAN = importlib.import_module("smart_drive.cli.cmd_clean")
CLI_MAIN = importlib.import_module("smart_drive.cli.main")
CLI_SNAPSHOT = importlib.import_module("smart_drive.cli.cmd_snapshot")
FSINFO = importlib.import_module("smart_drive.core.fsinfo")
MCP_SERVER = importlib.import_module("smart_drive.mcp.server")
RELINK = importlib.import_module("smart_drive.core.relink")

ROOT = Path(__file__).resolve().parent.parent

# Two copies of 5,000 bytes. 64 KB is neither a cluster size the tests detect (4 KB) nor the real fallback (512 KB), so a
# caller that keeps a fallback of its own cannot pass for one that asks the helper.
PAYLOAD = b"B" * 5000
DETECTED = 4096
FALLBACK = 65_536
ELSEWHERE = 8192  # what the volume says when `snapshot list` is asked, long after a snapshot was made at another size

# The Tier 1 junk the cleaners find: a file, and a folder, which is sized by the files inside it (that is where a wrong
# cluster size cost the most: on a 4 KB drive 570 MB of junk was given 16 GB)
THUMBS = b"T" * 3000     # a/Thumbs.db
BYTECODE = b"C" * 6000   # a/__pycache__/m.pyc
JUNK_BYTES = len(THUMBS) + len(BYTECODE)

# What a caller worked at, and the source its output gave
Worked = Tuple[int, str]

# The fallback written out by hand, as it was: `info.block_size if info.block_size > 0 else CLUSTER_SIZE_BYTES`
SPELLED_OUT = re.compile(r"block_size\s+if\s+[\w.]*block_size\s*>\s*0\s+else|block_size\s+or\s+CLUSTER_SIZE")

# What the commands and tools build to size things out, and so must give the cluster size (AutoZoner is not one of them:
# it asks `slack_cluster` itself when it is given none)
ENGINES = ("JunkDetector", "PurgeEngine", "SnapshotManager", "StorageAuditor", "DuplicateDetector")

# A snapshot manifest as it was written before the cluster was recorded: it has neither cluster field
OLD_MANIFEST: Dict[str, Any] = {
    "name": "old", "version": "1.1.0", "timestamp": "2026-01-01T00:00:00Z", "root": "D:/SSD", "partitions": ["a"],
    "file_count": 1, "total_logical_bytes": 5000, "total_allocated_bytes": 524_288, "total_slack_bytes": 519_288,
    "files": {"a/copy.bin": {"size": 5000, "allocated_size": 524_288, "mtime": 1.0, "sha256": "ab"}},
    "include_hidden": False, "excluded_dirs": [],
}


def volume(path: Any, block_size: int, fs_type: str = "ntfs") -> FilesystemInfo:
    return FilesystemInfo(str(path), fs_type, block_size, str(path), "win32" if fs_type != "unknown" else "unknown")


def slack(cluster: int, size: int = 5000) -> int:
    """The slack of one file at this cluster size: of a 5,000-byte copy, unless another size is given."""
    return calculate_slack_bytes(size, cluster)


def allocated(cluster: int, size: int = 5000) -> int:
    """The space one file takes at this cluster size: a 5,000-byte copy, unless another size is given."""
    return calculate_allocated_bytes(size, cluster)


def junk_allocated(cluster: int) -> int:
    """The space the junk takes at this cluster size: the Thumbs.db, and the .pyc in the folder."""
    return allocated(cluster, len(THUMBS)) + allocated(cluster, len(BYTECODE))


def drive_slack(cluster: int) -> int:
    """The slack of everything on the drive: the two copies and the two files of the junk."""
    return 2 * slack(cluster) + slack(cluster, len(THUMBS)) + slack(cluster, len(BYTECODE))


def engine_calls(source: str) -> List[Tuple[int, str, bool]]:
    """(line, engine, whether it is given `cluster_size=`) for each call in ``source`` that builds one of ENGINES."""
    calls = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in ENGINES:
                calls.append((node.lineno, name, any(keyword.arg == "cluster_size" for keyword in node.keywords)))
    return sorted(calls)


class _Drive(unittest.TestCase):
    """A drive holding two copies of a file (`a/copy.bin`, `b/copy.bin`) and some junk, with an MCP server on it."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_slack_routing_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "drive"
        self.write_files(("a/copy.bin", PAYLOAD), ("b/copy.bin", PAYLOAD))
        self.write_junk()
        refs = patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([]))
        refs.start()
        self.addCleanup(refs.stop)
        self.server = SmartDriveMCPServer(root=str(self.root), rate_limit_enabled=False)

    def write_files(self, *files: Tuple[str, bytes]) -> None:
        for rel, data in files:
            path = self.root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def write_junk(self) -> None:
        """The junk: a Thumbs.db and a __pycache__ folder with a .pyc (written again after a test that purged it)."""
        self.write_files(("a/Thumbs.db", THUMBS), ("a/__pycache__/m.pyc", BYTECODE))

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

    def cli_json(self, module: Any, detected: FilesystemInfo, *argv: str) -> Any:
        """A command with --json on ``detected``: the JSON it printed (``module`` looks the volume up)."""
        out = io.StringIO()
        with patch.object(module, "detect_filesystem", return_value=detected):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main([*argv, "--json"]), 0, out.getvalue())
        return json.loads(out.getvalue())

    def tree(self) -> List[str]:
        """Every path below the drive: what one caller leaves there would show in the figures of the ones after it."""
        return sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))

    def make_snapshot(self, detected: FilesystemInfo) -> Dict[str, Any]:
        """`snapshot create` of the two folders on ``detected``: the manifest it printed."""
        return self.cli_json(
            CLI_SNAPSHOT, detected, "snapshot", "create", "routing", "--root", str(self.root), "--partitions", "a,b"
        )

    def forget_state(self) -> None:
        """The tool keeps its own state below the drive (`.smart_drive`: snapshots, the last checkup): take it away."""
        shutil.rmtree(self.root / ".smart_drive", ignore_errors=True)

    @contextlib.contextmanager
    def quiet_machine(self) -> Iterator[None]:
        """No running programs and an empty home: what the relink preview of an `organize` plan reads."""
        home = self.base / "home"
        (home / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
        with patch.object(RELINK, "_running_processes", return_value=set()), \
                patch.object(RELINK, "_resolve_locations", return_value=(home, home / "AppData" / "Roaming", False)):
            yield

    # Each caller below works on `detected` and returns the cluster size its figures were worked out at, after checking
    # that the figures really are the slack, or the allocated size, at that size, and the source its output gives.

    def audit_json(self, detected: FilesystemInfo) -> Worked:
        out = io.StringIO()
        with patch.object(CLI_AUDIT, "detect_filesystem", return_value=detected), \
                patch.object(AUDITOR, "detect_filesystem", return_value=detected):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["audit", "--root", str(self.root), "--json"]), 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["total_slack_bytes"], drive_slack(data["cluster_size"]))
        return data["cluster_size"], data["cluster_size_source"]

    def ssd_audit(self, detected: FilesystemInfo) -> Worked:
        with patch.object(MCP_SERVER, "detect_filesystem", return_value=detected), \
                patch.object(AUDITOR, "detect_filesystem", return_value=detected):
            result = self.call_tool("ssd_audit")
        self.assertEqual(result["total_slack_bytes"], drive_slack(result["cluster_size"]))
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
                patch.object(CLI_CHECKUP, "JunkDetector", wraps=JunkDetector) as junk_class, \
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
        self.forget_state()  # (it keeps the score for the next run in <root>/.smart_drive/checkup.json)
        cluster = report["filesystem"]["cluster_size_bytes"]
        # the engines checkup builds work at it too
        for built in (auditor_class, junk_class, zoner_class, detector_class):
            (call,) = built.call_args_list
            self.assertEqual(call.kwargs["cluster_size"], cluster)
        return cluster, report["filesystem"]["cluster_size_source"]

    def clean_json(self, detected: FilesystemInfo) -> Worked:
        data = self.cli_json(CLI_CLEAN, detected, "clean", "--root", str(self.root))
        cluster = data["cluster_size"]
        sizes = {"Thumbs.db": len(THUMBS), "__pycache__": len(BYTECODE)}
        self.assertEqual({item["name"]: item["size"] for item in data["junk_items"]}, sizes)
        self.assertEqual(
            {item["name"]: item["allocated_size"] for item in data["junk_items"]},
            {name: allocated(cluster, size) for name, size in sizes.items()},
        )
        return cluster, data["cluster_size_source"]

    def ssd_clean(self, detected: FilesystemInfo) -> Worked:
        with patch.object(MCP_SERVER, "detect_filesystem", return_value=detected):
            result = self.call_tool("ssd_clean")
        cluster = result["cluster_size"]
        self.assertEqual(result["nominal_bytes_reclaimed"], JUNK_BYTES)
        self.assertEqual(result["slack_bytes_reclaimed"], junk_allocated(cluster))
        return cluster, result["cluster_size_source"]

    def snapshot_create_json(self, detected: FilesystemInfo) -> Worked:
        data = self.make_snapshot(detected)
        self.forget_state()
        cluster = data["cluster_size"]
        self.assertEqual(data["file_count"], 2)  # (the junk is left out of a snapshot)
        self.assertEqual({record["allocated_size"] for record in data["files"].values()}, {allocated(cluster)})
        self.assertEqual(data["total_allocated_bytes"], 2 * allocated(cluster))
        self.assertEqual(data["total_slack_bytes"], 2 * slack(cluster))
        return cluster, data["cluster_size_source"]

    def snapshot_list_json(self, detected: FilesystemInfo) -> Worked:
        self.make_snapshot(detected)
        # The volume says something else by the time the list is asked for: a snapshot keeps the cluster it was made at
        listed = self.cli_json(CLI_SNAPSHOT, volume(self.root, ELSEWHERE), "snapshot", "list", "--root", str(self.root))
        self.forget_state()
        (summary,) = listed
        cluster = summary["cluster_size"]
        self.assertEqual(summary["total_allocated_bytes"], 2 * allocated(cluster))
        return cluster, summary["cluster_size_source"]

    def organize_clean_json(self, detected: FilesystemInfo) -> Worked:
        with self.quiet_machine():
            data = self.cli_json(AUTO_ZONER, detected, "organize", "--root", str(self.root), "--clean")
        self.assertEqual(data["status"], "dry_run")
        cluster = data["cluster_size"]
        self.assertEqual(data["clean_preview"]["reclaimed_nominal_bytes"], JUNK_BYTES)
        self.assertEqual(data["clean_preview"]["reclaimed_slack_bytes"], junk_allocated(cluster))
        return cluster, data["cluster_size_source"]

    def ssd_auto_organize_clean(self, detected: FilesystemInfo) -> Worked:
        with self.quiet_machine(), \
                patch.object(AUTO_ZONER, "detect_filesystem", return_value=detected), \
                patch.object(MCP_SERVER, "JunkDetector", wraps=JunkDetector) as detector_class, \
                patch.object(MCP_SERVER, "PurgeEngine", wraps=PurgeEngine) as engine_class:
            result = self.call_tool("ssd_auto_organize", clean=True)
        self.assertEqual(result["status"], "dry_run")
        cluster = result["cluster_size"]
        for built in (detector_class, engine_class):  # the clean step builds both at the cluster its plan reports
            (call,) = built.call_args_list
            self.assertEqual(call.kwargs["cluster_size"], cluster)
        return cluster, result["cluster_size_source"]

    def auto_zoner(self, detected: FilesystemInfo) -> Worked:
        with patch.object(AUTO_ZONER, "detect_filesystem", return_value=detected) as detect:
            zoner = AutoZoner(str(self.root), system_refs=SystemRefIndex([]))
        detect.assert_called_once_with(str(self.root))
        return zoner.cluster_size, zoner.cluster_size_source

    def callers(self) -> List[Tuple[str, Callable[[FilesystemInfo], Worked]]]:
        """Everything that works slack or allocated sizes out, as (name, call) pairs."""
        return [
            ("audit --json", self.audit_json),
            ("ssd_audit", self.ssd_audit),
            ("dup --json", self.dup_json),
            ("ssd_find_duplicates", self.ssd_find_duplicates),
            ("checkup --json", self.checkup_json),
            ("clean --json", self.clean_json),
            ("ssd_clean", self.ssd_clean),
            ("snapshot create --json", self.snapshot_create_json),
            ("snapshot list --json", self.snapshot_list_json),
            ("organize --clean --json", self.organize_clean_json),
            ("ssd_auto_organize clean", self.ssd_auto_organize_clean),
            ("AutoZoner", self.auto_zoner),
        ]


class TestEveryCallerAsksTheHelper(_Drive):
    def test_a_cluster_that_cannot_be_read_is_worked_out_at_the_one_fallback(self) -> None:
        before = self.tree()
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            for name, call in self.callers():
                for fs_type in ("unknown", "ntfs"):
                    with self.subTest(caller=name, fs_type=fs_type):
                        worked = call(volume(self.root, 0, fs_type))
                        self.assertEqual(worked, (FALLBACK, "assumed"))
                        self.assertEqual(self.tree(), before, "the caller left something on the drive")

    def test_a_detected_cluster_is_used_as_it_is_by_all_of_them(self) -> None:
        before = self.tree()
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            for name, call in self.callers():
                with self.subTest(caller=name):
                    worked = call(volume(self.root, DETECTED))
                    self.assertEqual(worked, (DETECTED, "detected"))
                    self.assertEqual(self.tree(), before, "the caller left something on the drive")

    def test_an_explicit_cluster_size_is_used_without_asking_the_volume(self) -> None:
        with patch.object(AUTO_ZONER, "detect_filesystem") as detect:
            zoner = AutoZoner(str(self.root), cluster_size=8192, system_refs=SystemRefIndex([]))
        detect.assert_not_called()
        self.assertEqual(zoner.cluster_size, 8192)
        # Only the caller knows where a size it gives came from (checkup reports its own), so the zoner names no source
        self.assertIsNone(zoner.cluster_size_source)


class TestOrganizeSaysWhereItsClusterIsFrom(_Drive):
    """`organize --json` and `ssd_auto_organize` gain `cluster_size_source` and keep every field they had."""

    # The fields each output held before it gained the source
    ORGANIZE_DRY_RUN = {
        "status", "cluster_size", "clean_preview", "planned_actions", "protected_by_system", "busy_apps",
        "busy_apps_unknown", "fsmonitor_to_stop", "relink_preview", "anti_indexing_shields",
        "anti_indexing_shields_to_create", "note",
    }
    ORGANIZE_APPLY = {
        "status", "cluster_size", "clean", "zoning", "protected_by_system", "busy_apps", "busy_apps_unknown", "relink",
        "index_update", "anti_indexing_shields",
    }
    TOOL_DRY_RUN = {"status", "cluster_size", "plan_count", "actions", "protected_by_system", "relink_preview"}
    TOOL_APPLY = {"status", "cluster_size", "result", "protected_by_system", "relink", "shields_created", "index_sync"}

    # A model file loose in the drive root: the zoner moves it into 01_AI_Models, so an apply has something to move
    LOOSE_MODEL = "weights.safetensors"

    def check_fields(self, out: Dict[str, Any], before: Set[str], expected: Worked) -> None:
        """``out`` keeps every field in ``before``, gains only the source, and gives the cluster ``expected``."""
        self.assertEqual(set(out) - before, {"cluster_size_source"})
        self.assertLessEqual(before, set(out))
        self.assertEqual((out["cluster_size"], out["cluster_size_source"]), expected)

    def test_the_organize_dry_run_only_gains_the_source(self) -> None:
        with self.quiet_machine():
            data = self.cli_json(AUTO_ZONER, volume(self.root, DETECTED), "organize", "--root", str(self.root))
        self.assertEqual(data["status"], "dry_run")
        self.check_fields(data, self.ORGANIZE_DRY_RUN, (DETECTED, "detected"))

    def test_organize_apply_only_gains_the_source(self) -> None:
        self.write_files((self.LOOSE_MODEL, b"W" * 100))
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            # The first apply moves the loose model file into 01_AI_Models (its relink step runs on the fake home of
            # `quiet_machine`); the second finds nothing left to move. Both say where their cluster size came from.
            for detected, expected, moved in (
                (volume(self.root, DETECTED), (DETECTED, "detected"), 1),
                (volume(self.root, 0, "unknown"), (FALLBACK, "assumed"), 0),
            ):
                with self.subTest(cluster=expected[0]):
                    with self.quiet_machine():
                        data = self.cli_json(AUTO_ZONER, detected, "organize", "--root", str(self.root), "--apply")
                    self.assertEqual(data["status"], "applied")
                    self.assertEqual(data["zoning"]["moved_count"], moved)
                    self.check_fields(data, self.ORGANIZE_APPLY, expected)
        self.assertFalse((self.root / self.LOOSE_MODEL).exists())

    def test_ssd_auto_organize_only_gains_the_source(self) -> None:
        self.write_files((self.LOOSE_MODEL, b"W" * 100))
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            # (as above: the first apply moves the loose model file and the second finds nothing left to move; the dry
            # run before each plans what that apply moves)
            for detected, expected, moved in (
                (volume(self.root, DETECTED), (DETECTED, "detected"), 1),
                (volume(self.root, 0, "unknown"), (FALLBACK, "assumed"), 0),
            ):
                with self.subTest(cluster=expected[0]):
                    with self.quiet_machine(), patch.object(AUTO_ZONER, "detect_filesystem", return_value=detected):
                        dry_run = self.call_tool("ssd_auto_organize")
                        applied = self.call_tool("ssd_auto_organize", apply=True)
                    self.assertEqual(dry_run["status"], "dry_run")
                    self.assertEqual(dry_run["plan_count"], moved)
                    self.check_fields(dry_run, self.TOOL_DRY_RUN, expected)
                    self.assertEqual(applied["status"], "applied")
                    self.assertEqual(applied["result"]["moved_count"], moved)
                    self.check_fields(applied, self.TOOL_APPLY, expected)
        self.assertFalse((self.root / self.LOOSE_MODEL).exists())

    def test_the_ssd_auto_organize_description_says_what_the_cluster_fields_are(self) -> None:
        """An agent reads the description to decide what the numbers mean, so it must match the result."""
        tool = next(tool for tool in TOOLS if tool["name"] == "ssd_auto_organize")
        description = tool["description"]
        with self.quiet_machine(), \
                patch.object(AUTO_ZONER, "detect_filesystem", return_value=volume(self.root, DETECTED)):
            result = self.call_tool("ssd_auto_organize")
        self.assertEqual((result["cluster_size"], result["cluster_size_source"]), (DETECTED, "detected"))
        self.assertIn("real cluster size", description)
        # Every name it gives is a field of the result (or an argument of the tool), and it gives the two that matter
        named = set(re.findall(r"[a-z]+(?:_[a-z]+)+", description))
        self.assertEqual(named - set(result) - set(tool["inputSchema"]["properties"]), set())
        self.assertEqual({"cluster_size", "cluster_size_source"} - named, set())


class TestCleanAndSsdClean(_Drive):
    def test_apply_purges_at_the_cluster_the_scan_used_and_says_which(self) -> None:
        """The audit log `clean --apply --log` writes is the engine's own: its allocated sizes follow the cluster."""
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            for detected, expected in (
                (volume(self.root, DETECTED), (DETECTED, "detected")),
                (volume(self.root, 0, "unknown"), (FALLBACK, "assumed")),
            ):
                with self.subTest(cluster=expected[0]):
                    self.write_junk()
                    log = self.base / f"audit_{expected[0]}.json"
                    data = self.cli_json(
                        CLI_CLEAN, detected, "clean", "--root", str(self.root), "--apply", "--log", str(log)
                    )
                    self.assertEqual((data["cluster_size"], data["cluster_size_source"]), expected)
                    self.assertEqual((data["dry_run"], data["deleted_count"]), (False, 2))
                    self.assertEqual(data["freed_bytes"], JUNK_BYTES)
                    self.assertFalse((self.root / "a" / "Thumbs.db").exists())
                    self.assertFalse((self.root / "a" / "__pycache__").exists())
                    reclaimed = json.loads(log.read_text(encoding="utf-8"))["total_reclaimed_allocated"]
                    self.assertEqual(reclaimed, junk_allocated(expected[0]))

    def test_apply_in_text_mode_purges_at_the_same_cluster(self) -> None:
        log = self.base / "audit_text.json"
        with patch.object(CLI_CLEAN, "detect_filesystem", return_value=volume(self.root, DETECTED)):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["clean", "--root", str(self.root), "--apply", "--log", str(log)]), 0)
        reclaimed = json.loads(log.read_text(encoding="utf-8"))["total_reclaimed_allocated"]
        self.assertEqual(reclaimed, junk_allocated(DETECTED))

    def test_apply_hands_the_cluster_to_the_purge(self) -> None:
        with patch.object(CLI_CLEAN, "PurgeEngine", wraps=PurgeEngine) as engine_class:
            self.cli_json(CLI_CLEAN, volume(self.root, DETECTED), "clean", "--root", str(self.root), "--apply")
        (call,) = engine_class.call_args_list
        self.assertEqual(call.kwargs["cluster_size"], DETECTED)

    def test_the_quarantine_commands_do_not_ask_the_volume(self) -> None:
        """The cluster is looked up on the scan path only: listing, restoring and purging the quarantine need none."""
        for flags in (["--list-quarantine"], ["--restore", "latest"], ["--purge-quarantine"]):
            with self.subTest(flags=flags):
                with patch.object(CLI_CLEAN, "detect_filesystem") as detect:
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        main(["clean", "--root", str(self.root), *flags, "--json"])
                detect.assert_not_called()

    def test_the_scan_keeps_every_field_it_had(self) -> None:
        data = self.cli_json(CLI_CLEAN, volume(self.root, DETECTED), "clean", "--root", str(self.root))
        before = {
            "root", "dry_run", "quarantine", "only_tier", "junk_count", "junk_items", "total_reclaimable_bytes", "kept",
            "protected_by_system",
        }
        self.assertEqual(set(data) - before, {"cluster_size", "cluster_size_source"})
        self.assertLessEqual(before, set(data))

    def test_ssd_clean_keeps_every_field_it_had(self) -> None:
        with patch.object(MCP_SERVER, "detect_filesystem", return_value=volume(self.root, DETECTED)):
            result = self.call_tool("ssd_clean")
        before = {
            "target_root", "dry_run", "tier", "detected_count", "purged_count", "deleted_count", "freed_bytes",
            "quarantined_count", "quarantined_bytes", "batch_id", "nominal_bytes_reclaimed", "slack_bytes_reclaimed",
            "kept", "protected_by_system", "scan_skipped", "breakdown_by_type", "sample_preview",
        }
        self.assertEqual(set(result) - before, {"cluster_size", "cluster_size_source"})
        self.assertLessEqual(before, set(result))

    def test_a_sub_dir_is_sized_at_the_cluster_of_the_folder_cleaned(self) -> None:
        with patch.object(MCP_SERVER, "detect_filesystem", return_value=volume(self.root, DETECTED)) as detect:
            result = self.call_tool("ssd_clean", sub_dir="a")
        detect.assert_called_once_with(str(self.root / "a"))
        self.assertEqual(result["cluster_size"], DETECTED)
        self.assertEqual(result["slack_bytes_reclaimed"], junk_allocated(DETECTED))

    def test_the_description_says_what_the_sizes_are(self) -> None:
        """An agent reads the description to decide what the numbers mean, so it must match the result."""
        tool = next(tool for tool in TOOLS if tool["name"] == "ssd_clean")
        description = tool["description"]
        with patch.object(MCP_SERVER, "detect_filesystem", return_value=volume(self.root, DETECTED)):
            result = self.call_tool("ssd_clean")
        self.assertEqual((result["cluster_size"], result["cluster_size_source"]), (DETECTED, "detected"))
        self.assertIn("real cluster size", description)
        # Every name it gives is a field of the result (or an argument of the tool), and it gives the ones that matter
        named = set(re.findall(r"[a-z]+(?:_[a-z]+)+", description))
        self.assertEqual(named - set(result) - set(tool["inputSchema"]["properties"]), set())
        needed = {"nominal_bytes_reclaimed", "slack_bytes_reclaimed", "cluster_size", "cluster_size_source"}
        self.assertEqual(needed - named, set())


class TestSnapshotsRecordTheirCluster(_Drive):
    def write_old_manifest(self) -> None:
        folder = self.root / ".smart_drive" / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "old.json").write_text(json.dumps(OLD_MANIFEST), encoding="utf-8")

    def run_text(self, detected: FilesystemInfo, *argv: str) -> List[str]:
        out = io.StringIO()
        with patch.object(CLI_SNAPSHOT, "detect_filesystem", return_value=detected):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["snapshot", *argv, "--root", str(self.root)]), 0)
        return out.getvalue().splitlines()

    def test_a_manifest_written_before_the_cluster_was_recorded_reads_as_512_kb_assumed(self) -> None:
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):  # (not what such a file was worked out at)
            manifest = SnapshotManifest.from_dict(dict(OLD_MANIFEST))
        self.assertEqual((manifest.cluster_size, manifest.cluster_size_source), (524_288, "assumed"))
        self.assertEqual(manifest.to_dict()["cluster_size"], 524_288)

    def test_snapshot_list_shows_that_for_it_whatever_the_volume_says_today(self) -> None:
        self.write_old_manifest()
        listed = self.cli_json(CLI_SNAPSHOT, volume(self.root, DETECTED), "snapshot", "list", "--root", str(self.root))
        (summary,) = listed
        self.assertEqual(summary["name"], "old")
        self.assertEqual((summary["cluster_size"], summary["cluster_size_source"]), (524_288, "assumed"))
        self.assertEqual(summary["total_allocated_bytes"], 524_288)

    def test_a_manifest_keeps_the_cluster_through_a_save_and_a_load(self) -> None:
        data = self.make_snapshot(volume(self.root, DETECTED))
        loaded = SnapshotManifest.load(self.root / ".smart_drive" / "snapshots" / f"{data['name']}.json")
        self.assertEqual((loaded.cluster_size, loaded.cluster_size_source), (DETECTED, "detected"))
        again = SnapshotManifest.from_json(loaded.to_json())
        self.assertEqual((again.cluster_size, again.cluster_size_source), (DETECTED, "detected"))
        self.assertEqual(again.total_allocated_bytes, 2 * allocated(DETECTED))

    def test_a_manager_that_is_given_no_cluster_works_at_the_assumed_512_kb(self) -> None:
        manifest = SnapshotManager(self.root).create_snapshot("plain", partitions=["a", "b"])
        self.assertEqual((manifest.cluster_size, manifest.cluster_size_source), (524_288, "assumed"))
        self.assertEqual(manifest.total_allocated_bytes, 2 * allocated(524_288))
        given = SnapshotManager(self.root, cluster_size=DETECTED, cluster_size_source="detected")
        manifest = given.create_snapshot("given", partitions=["a", "b"])
        self.assertEqual((manifest.cluster_size, manifest.cluster_size_source), (DETECTED, "detected"))
        self.assertEqual(manifest.total_allocated_bytes, 2 * allocated(DETECTED))

    def test_snapshot_create_says_which_cluster_the_allocated_size_is_at(self) -> None:
        for detected, expected in (
            (volume(self.root, DETECTED), "  Allocated:  16.0 KB (at 4 KB clusters)"),
            (volume(self.root, 1536), "  Allocated:  12.0 KB (at 1536 B clusters)"),
            (
                volume(self.root, 0, "unknown"),
                "  Allocated:  1.00 MB (at 512 KB clusters, assumed: the drive's own could not be read)",
            ),
        ):
            with self.subTest(expected=expected):
                lines = self.run_text(detected, "create", "text", "--partitions", "a,b")
                self.forget_state()
                self.assertIn(expected, lines)
                self.assertNotIn("512KB clusters)", " ".join(lines))

    def test_snapshot_create_names_the_fallback_it_used(self) -> None:
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            lines = self.run_text(volume(self.root, 0), "create", "text", "--partitions", "a,b")
        self.assertIn("  Allocated:  128.0 KB (at 64 KB clusters, assumed: the drive's own could not be read)", lines)

    def test_snapshot_list_has_a_cluster_column_and_rules_that_fit_it(self) -> None:
        self.run_text(volume(self.root, DETECTED), "create", "at_4k", "--partitions", "a,b")
        self.run_text(volume(self.root, 0, "unknown"), "create", "at_fallback", "--partitions", "a,b")
        self.write_old_manifest()
        lines = self.run_text(volume(self.root, ELSEWHERE), "list")
        header = next(line for line in lines if line.startswith("Snapshot Name"))
        self.assertEqual(header.split()[-2:], ["Allocated", "Cluster"])
        self.assertNotIn("512K", header)
        rows = {line.split()[0]: line.rstrip() for line in lines if line.startswith(("at_4k", "at_fallback", "old"))}
        clusters = {name: re.split(r"\s{2,}", row)[-1] for name, row in rows.items()}  # (the last column)
        self.assertEqual(clusters, {"at_4k": "4 KB", "at_fallback": "512 KB (assumed)", "old": "512 KB (assumed)"})
        rules = [line for line in lines if line and set(line) <= {"=", "-"}]
        self.assertEqual(len(rules), 3)
        for rule in rules:
            self.assertGreaterEqual(len(rule), max(len(row) for row in rows.values()))
        self.assertIn("Total: 3 snapshot(s) stored.", lines)


class TestBackupIsGivenTheCluster(_Drive):
    def backup(self, detected: FilesystemInfo) -> Tuple[Dict[str, Any], Any]:
        """`backup --json` of the two folders on ``detected``: its report, and the call that built its manager."""
        out = io.StringIO()
        target = str(self.base / "copy")
        argv = ["backup", "--root", str(self.root), "--target", target, "--partitions", "a,b", "--json"]
        with patch.object(CLI_BACKUP, "detect_filesystem", return_value=detected), \
                patch.object(CLI_BACKUP, "SnapshotManager", wraps=SnapshotManager) as manager_class:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 0, out.getvalue())
        (call,) = manager_class.call_args_list
        return json.loads(out.getvalue()), call

    def test_the_manager_is_given_the_cluster_the_volume_has(self) -> None:
        _, call = self.backup(volume(self.root, DETECTED))
        self.assertEqual((call.kwargs["cluster_size"], call.kwargs["cluster_size_source"]), (DETECTED, "detected"))

    def test_a_cluster_that_cannot_be_read_is_given_as_the_one_fallback(self) -> None:
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            _, call = self.backup(volume(self.root, 0, "unknown"))
        self.assertEqual((call.kwargs["cluster_size"], call.kwargs["cluster_size_source"]), (FALLBACK, "assumed"))

    def test_the_report_has_no_size_for_the_cluster_to_describe_so_it_gains_no_field(self) -> None:
        data, _ = self.backup(volume(self.root, DETECTED))
        fields = {field.name for field in dataclasses.fields(BackupReport)}
        self.assertEqual(set(data), fields | {"partitions", "not_covered"})
        self.assertEqual(data["copied_count"], 2)


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


class TestEveryEngineIsGivenTheCluster(unittest.TestCase):
    def test_the_guard_finds_the_calls_it_is_meant_to_find(self) -> None:
        for built in (
            "detector = JunkDetector(root, max_tier=tier)",
            "JunkDetector(root).find_junk()",
            "engine = PurgeEngine(guard, dry_run=False)",
            "manager = snapshot.SnapshotManager(root)",
            "auditor = StorageAuditor(root)",
            "plan = DuplicateDetector(root, min_size=1).generate_reclamation_plan()",
            "detector = JunkDetector(root, max_tier=tier, **options)",  # a keyword that is not written out is not seen
        ):
            with self.subTest(built=built):
                (call,) = engine_calls(built)
                self.assertFalse(call[2])
        for fine in (
            "detector = JunkDetector(root, max_tier=tier, cluster_size=cluster)",
            "engine = PurgeEngine(guard, cluster_size=zoner.cluster_size)",
            "manager = snapshot.SnapshotManager(root, cluster_size=size, cluster_size_source=source)",
            "plan = DuplicateDetector(\n    root, cluster_size=cluster, min_size=1\n).generate_reclamation_plan()",
        ):
            with self.subTest(fine=fine):
                (call,) = engine_calls(fine)
                self.assertTrue(call[2])
        for not_a_call_of_one in (
            "zoner = AutoZoner(root)",  # asks slack_cluster itself when it is given no cluster size
            "from smart_drive.core.junk_detector import JunkDetector",
            "def show(detector: JunkDetector) -> JunkDetector: ...",
            "assert isinstance(engine, PurgeEngine)",
            "cluster = measure(StorageAuditor)",
        ):
            with self.subTest(not_a_call_of_one=not_a_call_of_one):
                self.assertEqual(engine_calls(not_a_call_of_one), [])

    def test_no_command_or_tool_builds_an_engine_without_the_cluster(self) -> None:
        offenders: List[str] = []
        seen = set()
        for folder in ("cli", "mcp"):
            for path in sorted((ROOT / "smart_drive" / folder).rglob("*.py")):
                for line, engine, given in engine_calls(path.read_text(encoding="utf-8")):
                    seen.add(path.name)
                    if not given:
                        offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line} {engine}")
        self.assertEqual(
            offenders,
            [],
            "pass cluster_size= from fsinfo.slack_cluster(detect_filesystem(root)): the default is a 512 KB cluster",
        )
        # (the guard reads the files it is meant to read)
        read = {
            "cmd_audit.py", "cmd_backup.py", "cmd_checkup.py", "cmd_clean.py", "cmd_organize.py", "cmd_snapshot.py",
            "main.py", "server.py",
        }
        self.assertEqual(read - seen, set())


class TestReadmes(unittest.TestCase):
    def test_both_readmes_name_the_source_next_to_the_cluster_size_of_the_duplicate_finders(self) -> None:
        for name in ("README.md", "README_VN.md"):
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            for start in ("| `dup` |", "- `ssd_find_duplicates`:"):
                line = next(line for line in lines if line.startswith(start))
                for word in ("`cluster_size`", "`cluster_size_source`"):
                    with self.subTest(readme=name, line=start, word=word):
                        self.assertIn(word, line)

    def test_both_readmes_name_the_source_next_to_the_cluster_size_of_clean_and_snapshots(self) -> None:
        for name in ("README.md", "README_VN.md"):
            lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
            for start in ("- `ssd_clean`:", "| `clean` |", "| `snapshot create` |"):
                line = next(line for line in lines if line.startswith(start))
                for word in ("`cluster_size`", "`cluster_size_source`"):
                    with self.subTest(readme=name, line=start, word=word):
                        self.assertIn(word, line)


if __name__ == "__main__":
    unittest.main()
