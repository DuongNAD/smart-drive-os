"""tests/test_fsinfo.py - Reports must say what the volume really is instead of claiming exFAT.

Before: `sentinel` printed "(exFAT, 512 KB cluster)" for any drive and under a hard-coded
"KINGSTON XS2000" banner, `status`/`audit` reported 80% slack waste on APFS as if it were exFAT
facts, `health` answered "Must contain a valid drive letter (A-Z)" on macOS and `offload` talked about
NTFS junctions everywhere. The slack model (512 KB clusters) is unchanged; reports now state the
detected filesystem and when the model does not apply.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple
from unittest.mock import patch

from smart_drive.cli import cmd_sentinel
from smart_drive.cli.main import main
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.core.fsinfo import (
    FilesystemInfo,
    best_mount,
    detect_filesystem,
    normalize_fs_name,
    parse_mount_output,
    parse_proc_mounts,
    slack_model_note,
)
from smart_drive.core.health import check_drive_health
from smart_drive.core.offloader import LINK_NAME, offload_cache, scan_caches

MACOS_MOUNT = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse, protect, root data)
/dev/disk4s1 on /Volumes/KINGSTON (exfat, local, nodev, nosuid, noowners)
/dev/disk5s1 on /Volumes/My Drive (2) (msdos, local, nodev, nosuid, noowners)
/dev/disk6s1 on /Volumes/Paragon (ufsd_NTFS, local, nodev, nosuid, read-only, noowners)
map auto_home on /System/Volumes/Data/home (autofs, automounted, nobrowse)
"""

LINUX_MOUNTS = """\
overlay / overlay rw,relatime 0 0
/dev/sda1 /mnt/My\\040Drive exfat rw,nosuid,nodev 0 0
/dev/nvme0n1p2 /boot ext4 rw,relatime 0 0
tmpfs /run tmpfs rw,nosuid 0 0
"""


class TestMountParsers(unittest.TestCase):
    def test_macos_mount_output(self) -> None:
        mounts = dict(parse_mount_output(MACOS_MOUNT))
        self.assertEqual(mounts["/"], "apfs")
        self.assertEqual(mounts["/Volumes/KINGSTON"], "exfat")
        self.assertEqual(mounts["/Volumes/My Drive (2)"], "msdos")  # spaces and parentheses in the name
        self.assertEqual(mounts["/Volumes/Paragon"], "ntfs")  # vendor prefix dropped
        self.assertEqual(len(mounts), 6)

    def test_linux_proc_mounts(self) -> None:
        mounts = dict(parse_proc_mounts(LINUX_MOUNTS))
        self.assertEqual(mounts["/mnt/My Drive"], "exfat")  # \040 is a space
        self.assertEqual(mounts["/boot"], "ext4")
        self.assertEqual(mounts["/"], "overlay")

    def test_longest_mount_point_wins_and_siblings_do_not_match(self) -> None:
        mounts = parse_mount_output(MACOS_MOUNT)
        self.assertEqual(best_mount("/Volumes/KINGSTON/projects/x", mounts), ("/Volumes/KINGSTON", "exfat"))
        self.assertEqual(best_mount("/Users/me/file.txt", mounts), ("/", "apfs"))
        self.assertEqual(best_mount("/System/Volumes/Data/Users/me", mounts), ("/System/Volumes/Data", "apfs"))
        # "KINGSTON2" merely shares a name prefix with the "/Volumes/KINGSTON" mount
        self.assertEqual(best_mount("/Volumes/KINGSTON2/x", mounts), ("/", "apfs"))
        self.assertIsNone(best_mount("/anything", []))

    def test_normalize_fs_name(self) -> None:
        self.assertEqual(normalize_fs_name("ufsd_NTFS"), "ntfs")
        self.assertEqual(normalize_fs_name("exFAT"), "exfat")
        self.assertEqual(normalize_fs_name(""), "unknown")


class TestSlackModelNote(unittest.TestCase):
    def info(self, fs_type: str, block: int) -> FilesystemInfo:
        return FilesystemInfo("/x", fs_type, block, "/x", "mount")

    def test_no_note_only_for_exfat_with_the_modelled_cluster(self) -> None:
        self.assertIsNone(slack_model_note(self.info("exfat", CLUSTER_SIZE_BYTES), CLUSTER_SIZE_BYTES))
        self.assertIsNone(slack_model_note(self.info("exfat", 0), CLUSTER_SIZE_BYTES))

    def test_other_cluster_sizes_filesystems_and_unknown_get_an_explanation(self) -> None:
        small = slack_model_note(self.info("exfat", 32768), CLUSTER_SIZE_BYTES)
        self.assertIn("32 KB", small)
        self.assertIn("512 KB", small)
        apfs = slack_model_note(self.info("apfs", 4096), CLUSTER_SIZE_BYTES)
        self.assertIn("APFS (4 KB allocation unit)", apfs)
        self.assertIn("what-if", apfs)
        unknown = slack_model_note(FilesystemInfo("/x", "unknown", 0, "", "unknown"), CLUSTER_SIZE_BYTES)
        self.assertIn("Could not detect", unknown)

    def test_summary_and_dict(self) -> None:
        info = self.info("apfs", 4096)
        self.assertEqual(info.summary(), "APFS (4 KB allocation unit)")
        self.assertEqual(info.to_dict()["display_name"], "APFS")
        self.assertEqual(self.info("exfat", 524288).summary(), "exFAT (512 KB allocation unit)")


class TestDetectOnThisMachine(unittest.TestCase):
    def test_detects_a_real_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = detect_filesystem(tmp)
        self.assertTrue(info.known, f"could not identify the filesystem of the temp dir: {info}")
        self.assertGreater(info.block_size, 0)
        self.assertTrue(info.mount_point)

    def test_never_raises_for_odd_input(self) -> None:
        for odd in ("/definitely/not/here", "", "x" * 5000):
            self.assertIsInstance(detect_filesystem(odd), FilesystemInfo)


class _Root(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_fsinfo_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "GEMINI.md").write_text("# anchor", encoding="utf-8")
        (self.root / "01_AI_Models").mkdir()
        (self.root / "01_AI_Models" / "a.txt").write_text("a", encoding="utf-8")
        self.info = detect_filesystem(self.root)
        self.note = slack_model_note(self.info, CLUSTER_SIZE_BYTES)

    def cli(self, *argv: str) -> Tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        return code, out.getvalue()


class TestReportsNameTheRealFilesystem(_Root):
    def test_status_json_and_text(self) -> None:
        _, out = self.cli("status", "--root", str(self.root), "--json")
        data: Dict[str, Any] = json.loads(out)
        self.assertEqual(data["filesystem"]["type"], self.info.fs_type)
        self.assertEqual(data["slack_model_note"], self.note)
        self.assertEqual(data["cluster_size_bytes"], CLUSTER_SIZE_BYTES)  # the model itself is unchanged
        _, text = self.cli("status", "--root", str(self.root))
        self.assertIn(f"Filesystem: {self.info.summary()}", text)
        self.assertEqual("Note:" in text, self.note is not None)

    def test_audit_reports_the_filesystem_in_json_text_and_markdown(self) -> None:
        result = StorageAuditor(str(self.root)).run_audit()
        data = json.loads(result.to_json())
        self.assertEqual(data["filesystem"]["type"], self.info.fs_type)
        self.assertEqual(data["slack_model_note"], self.note)
        self.assertIn(f"Filesystem: {self.info.summary()}", result.to_ascii_table())
        self.assertIn(f"**Filesystem**: `{self.info.summary()}`", result.to_markdown())

    def test_sentinel_no_longer_claims_exfat_or_a_drive_model(self) -> None:
        _, out = self.cli("sentinel", "--root", str(self.root), "--no-heal", "--json")
        mount = json.loads(out)["mount"]
        self.assertEqual(mount["filesystem"], self.info.display_name)
        self.assertEqual(mount["filesystem_type"], self.info.fs_type)
        self.assertEqual(mount["cluster_size_bytes"], CLUSTER_SIZE_BYTES)
        _, text = self.cli("sentinel", "--root", str(self.root), "--no-heal")
        self.assertIn("SMARTDRIVE-OS SSD HEALTH & SELF-HEALING AUDIT", text)
        self.assertNotIn("KINGSTON XS2000", text)
        self.assertIn(f"({self.info.display_name}", text)
        if not self.info.is_exfat:
            self.assertNotIn("(exFAT", text)

    def test_sentinel_colours_only_for_a_terminal_that_has_not_opted_out(self) -> None:
        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        with patch.object(sys, "stdout", Tty()), patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_COLOR", None)
            self.assertTrue(cmd_sentinel._use_color())
            os.environ["NO_COLOR"] = "1"
            self.assertFalse(cmd_sentinel._use_color())
        self.assertFalse(cmd_sentinel._use_color())  # captured, not a terminal
        _, text = self.cli("sentinel", "--root", str(self.root), "--no-heal")
        self.assertNotIn("\x1b[", text)
        self.assertIn("\x1b[", cmd_sentinel.format_ansi_report({"drive_root": "x", "mount": {}}, color=True))


@unittest.skipIf(sys.platform == "win32", "path-based volumes are the non-Windows way to name a drive")
class TestHealthOnPaths(_Root):
    def test_a_path_is_a_valid_volume_specifier(self) -> None:
        report = check_drive_health(str(self.root))
        self.assertEqual(report.filesystem, self.info.display_name)
        self.assertGreater(report.total_bytes, 0)
        self.assertGreater(report.cluster_size_bytes, 0)
        self.assertIsNone(report.trim_enabled)
        self.assertFalse(any("drive letter" in w for w in report.warnings), report.warnings)

    def test_cli_health_json_works_for_a_path(self) -> None:
        code, out = self.cli("health", str(self.root), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["filesystem"], self.info.display_name)

    def test_missing_target_gets_a_message_that_names_the_real_problem(self) -> None:
        none_given = check_drive_health(None)
        self.assertIn("path", " ".join(none_given.warnings))
        gone = check_drive_health(str(self.root / "nope" / "missing"))
        self.assertIn("does not exist", " ".join(gone.warnings))
        self.assertNotIn("drive letter", " ".join(gone.warnings))


@unittest.skipIf(sys.platform == "win32", "caches under the home directory are the non-Windows layout")
class TestOffloadOffWindows(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sd_offload_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        pip_dir = (self.home / "Library" / "Caches" / "pip") if sys.platform == "darwin" else (self.home / ".cache" / "pip")
        for rel in (pip_dir, self.home / ".npm" / "_cacache", self.home / ".cache" / "huggingface"):
            rel.mkdir(parents=True)
            (rel / "blob.bin").write_bytes(b"x" * 2048)
        self.env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(self.home),
        }

    def test_package_manager_caches_are_found_in_their_native_locations(self) -> None:
        with patch.dict(os.environ, self.env):
            found = {t.name: t for t in scan_caches()}
        for name in ("pip", "npm", "huggingface"):
            self.assertEqual(found[name].status, "FOUND", f"{name}: {found[name].source_path}")
            self.assertGreaterEqual(found[name].size_bytes, 2048)

    def test_messages_name_a_symbolic_link_not_an_ntfs_junction(self) -> None:
        self.assertEqual(LINK_NAME, "symbolic link")
        with patch.dict(os.environ, self.env):
            result = offload_cache("huggingface", self.tmp / "target", dry_run=True)
        self.assertIn("symbolic link", result["message"])
        self.assertNotIn("NTFS", result["message"])

    def test_cli_scan_does_not_show_windows_paths_for_missing_caches(self) -> None:
        with patch.dict(os.environ, self.env):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(["offload", "--scan"])
        text = out.getvalue()
        self.assertNotIn("appdata", text.lower())
        self.assertIn("symbolic link", text)
        self.assertIn("n/a (Windows location)", text)  # docker_wsl only exists on Windows


if __name__ == "__main__":
    unittest.main()
