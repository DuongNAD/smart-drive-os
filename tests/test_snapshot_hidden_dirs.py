"""tests/test_snapshot_hidden_dirs.py - Snapshots and backups must say what they leave out.

Regression for a silent gap: both walked the partitions with `not d.startswith(".")`, so every hidden
folder (.git, .github, .vscode ...) was skipped without a word, and nothing told the user that only
three of the six taxonomy folders (and none of the loose root files) were covered. The defaults stay
(a .git folder is thousands of tiny objects), but runs now report what they left out and
`--include-hidden` opts in.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple

from smart_drive.cli.main import main
from smart_drive.core.snapshot import SnapshotManager, SnapshotManifest

PROJ = "03_Development_Projects/proj"
HIDDEN = (f"{PROJ}/.git/HEAD", f"{PROJ}/.git/objects/aa/bbcc", f"{PROJ}/.github/workflows/ci.yml", f"{PROJ}/.vscode/settings.json")
VISIBLE = (f"{PROJ}/src/a.py", "02_Learning_Knowledge/notes.md")
SYSTEM = f"{PROJ}/$RECYCLE.BIN/junk.txt"


def _touch(root: Path, rel: str, text: str = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp(prefix="sd_snap_hidden_")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root = base / "drive"
        self.target = base / "backup"
        for rel in VISIBLE + HIDDEN + (SYSTEM, "01_AI_Models/model.bin", "AGENTS.md", "README.md", "loose_note.txt"):
            _touch(self.root, rel, rel)
        self.mgr = SnapshotManager(self.root)

    def cli(self, *argv: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestSnapshotHiddenFolders(_Fixture):
    def test_default_snapshot_skips_hidden_folders_and_records_them(self) -> None:
        manifest = self.mgr.create_snapshot("s1")
        self.assertFalse(manifest.include_hidden)
        for rel in VISIBLE:
            self.assertIn(rel, manifest.files)
        for rel in HIDDEN + (SYSTEM,):
            self.assertNotIn(rel, manifest.files)
        self.assertEqual(sorted(manifest.excluded_dirs), [f"{PROJ}/.git", f"{PROJ}/.github", f"{PROJ}/.vscode"])

    def test_include_hidden_covers_git_and_friends_but_never_system_folders(self) -> None:
        manifest = self.mgr.create_snapshot("s2", include_hidden=True)
        self.assertTrue(manifest.include_hidden)
        for rel in HIDDEN + VISIBLE:
            self.assertIn(rel, manifest.files)
        self.assertNotIn(SYSTEM, manifest.files)
        self.assertEqual(manifest.excluded_dirs, [])
        reloaded = SnapshotManifest.from_json(manifest.to_json())
        self.assertTrue(reloaded.include_hidden)

    def test_verification_walks_exactly_what_the_snapshot_walked(self) -> None:
        self.mgr.create_snapshot("with_hidden", include_hidden=True)
        (self.root / HIDDEN[0]).unlink()
        _touch(self.root, f"{PROJ}/.git/brand_new", "n")
        report = self.mgr.verify_snapshot("with_hidden")
        self.assertIn(HIDDEN[0], report.missing_files)
        self.assertIn(f"{PROJ}/.git/brand_new", report.untracked_files)

        self.mgr.create_snapshot("plain")
        _touch(self.root, f"{PROJ}/.git/another_new", "n")
        plain = self.mgr.verify_snapshot("plain")
        self.assertNotIn(f"{PROJ}/.git/another_new", plain.untracked_files)

    def test_manifest_written_before_this_change_still_loads(self) -> None:
        old = {
            "name": "old", "version": "1.1.0", "timestamp": "2026-01-01T00:00:00Z", "root": str(self.root),
            "partitions": ["02_Learning_Knowledge"], "file_count": 0, "total_logical_bytes": 0,
            "total_allocated_bytes": 0, "total_slack_bytes": 0, "files": {},
        }
        manifest = SnapshotManifest.from_dict(old)
        self.assertFalse(manifest.include_hidden)
        self.assertEqual(manifest.excluded_dirs, [])


class TestBackupHiddenFolders(_Fixture):
    def test_default_backup_leaves_git_out_and_reports_it(self) -> None:
        report = self.mgr.incremental_backup(self.target)
        self.assertTrue((self.target / VISIBLE[0]).is_file())
        self.assertFalse((self.target / HIDDEN[0]).exists())
        self.assertEqual(sorted(report.excluded_dirs), [f"{PROJ}/.git", f"{PROJ}/.github", f"{PROJ}/.vscode"])
        self.assertFalse(report.include_hidden)

    def test_include_hidden_backs_up_git_byte_for_byte(self) -> None:
        report = self.mgr.incremental_backup(self.target, include_hidden=True)
        for rel in HIDDEN:
            self.assertEqual((self.target / rel).read_text(encoding="utf-8"), (self.root / rel).read_text(encoding="utf-8"))
        self.assertEqual(report.excluded_dirs, [])
        self.assertFalse((self.target / SYSTEM).exists())
        again = self.mgr.incremental_backup(self.target, include_hidden=True)
        self.assertEqual(again.copied_count, 0)


class TestArchivedCopiesOfManagedDrives(_Fixture):
    """A nested .smart_drive is another managed drive's state, archived inside a partition: user data."""

    NESTED = "02_Learning_Knowledge/old_drive/.smart_drive/snapshots/s1.json"

    def setUp(self) -> None:
        super().setUp()
        _touch(self.root, self.NESTED, "{}")
        _touch(self.root, "02_Learning_Knowledge/old_drive/.smart_drive_manager/index.db", "db")

    def test_default_snapshot_reports_them_instead_of_dropping_them_silently(self) -> None:
        manifest = self.mgr.create_snapshot("s1")
        self.assertNotIn(self.NESTED, manifest.files)
        self.assertIn("02_Learning_Knowledge/old_drive/.smart_drive", manifest.excluded_dirs)
        self.assertIn("02_Learning_Knowledge/old_drive/.smart_drive_manager", manifest.excluded_dirs)

    def test_include_hidden_keeps_them(self) -> None:
        manifest = self.mgr.create_snapshot("s2", include_hidden=True)
        self.assertIn(self.NESTED, manifest.files)
        self.assertIn("02_Learning_Knowledge/old_drive/.smart_drive_manager/index.db", manifest.files)

    def test_include_hidden_backup_copies_them(self) -> None:
        report = self.mgr.incremental_backup(self.target, include_hidden=True)
        self.assertEqual((self.target / self.NESTED).read_text(encoding="utf-8"), "{}")
        self.assertEqual(report.excluded_dirs, [])

    def test_default_backup_names_them_in_what_it_left_out(self) -> None:
        report = self.mgr.incremental_backup(self.target)
        self.assertFalse((self.target / self.NESTED).exists())
        self.assertIn("02_Learning_Knowledge/old_drive/.smart_drive", report.excluded_dirs)


class TestTheLiveStateFolderIsNeverSnapshotted(_Fixture):
    """With `--partitions .` the drive root itself is walked, and with --include-hidden that reached the live
    .smart_drive: the search index was recorded and the manifest being written showed up as untracked."""

    def setUp(self) -> None:
        super().setUp()
        _touch(self.root, ".smart_drive/index.db", "database")
        _touch(self.root, ".smart_drive/snapshots/earlier.json", "{}")

    def test_the_drive_root_as_a_partition_skips_it_even_with_include_hidden(self) -> None:
        manifest = self.mgr.create_snapshot("whole", partitions=["."], include_hidden=True)
        self.assertFalse([f for f in manifest.files if f.startswith(".smart_drive")], sorted(manifest.files)[:5])
        self.assertIn(HIDDEN[0], manifest.files)  # the user's own hidden folders are in
        report = self.mgr.verify_snapshot("whole")
        self.assertEqual([f for f in report.untracked_files if f.startswith(".smart_drive")], [])

    def test_the_backup_of_the_drive_root_skips_it_too(self) -> None:
        self.mgr.incremental_backup(self.target, partitions=["."], include_hidden=True)
        self.assertFalse((self.target / ".smart_drive").exists())
        self.assertTrue((self.target / HIDDEN[0]).is_file())

    def test_a_nested_copy_is_still_user_data(self) -> None:
        _touch(self.root, "02_Learning_Knowledge/old_drive/.smart_drive/index.db", "archived")
        manifest = self.mgr.create_snapshot("nested", partitions=["."], include_hidden=True)
        self.assertIn("02_Learning_Knowledge/old_drive/.smart_drive/index.db", manifest.files)


class TestFoldersOnTheExclusionListAreReported(_Fixture):
    """Downloads, game libraries ... are always skipped by snapshot and backup, but never silently."""

    KEPT_OUT = "02_Learning_Knowledge/Downloads/keep.pdf"

    def setUp(self) -> None:
        super().setUp()
        _touch(self.root, self.KEPT_OUT, "pdf")
        _touch(self.root, "Downloads/loose.txt", "x")

    def test_a_nested_downloads_folder_is_skipped_and_named_in_the_report(self) -> None:
        for include_hidden in (False, True):
            with self.subTest(include_hidden=include_hidden):
                manifest = self.mgr.create_snapshot(f"s{include_hidden}", include_hidden=include_hidden)
                self.assertNotIn(self.KEPT_OUT, manifest.files)
                self.assertIn("02_Learning_Knowledge/Downloads", manifest.excluded_dirs)

    def test_backup_leaves_it_out_and_says_so(self) -> None:
        report = self.mgr.incremental_backup(self.target, include_hidden=True)
        self.assertFalse((self.target / self.KEPT_OUT).exists())
        self.assertEqual(report.excluded_dirs, ["02_Learning_Knowledge/Downloads"])

    def test_the_notes_separate_hidden_folders_from_the_exclusion_list(self) -> None:
        manifest = self.mgr.create_snapshot("s1")
        notes = "\n".join(self.mgr.coverage_notes(manifest.partitions, manifest.excluded_dirs, False))
        self.assertIn("3 hidden folder(s)", notes)  # .git, .github, .vscode: --include-hidden brings these back
        self.assertIn("Also left out: 1 folder(s) on the tool's exclusion list (Downloads)", notes)
        with_hidden = "\n".join(self.mgr.coverage_notes(manifest.partitions, ["02_Learning_Knowledge/Downloads"], True))
        self.assertNotIn("hidden folder", with_hidden)
        self.assertIn("Also left out", with_hidden)  # --include-hidden does not change this one

    def test_a_downloads_folder_in_the_drive_root_counts_as_not_covered(self) -> None:
        folders, loose = self.mgr.uncovered_entries(["02_Learning_Knowledge", "03_Development_Projects"])
        self.assertIn("Downloads", folders)
        self.assertEqual(loose, 3)

    def test_os_bookkeeping_stays_silent(self) -> None:
        manifest = self.mgr.create_snapshot("s2")
        self.assertFalse([d for d in manifest.excluded_dirs if "RECYCLE" in d.upper()])
        folders, _ = self.mgr.uncovered_entries(["02_Learning_Knowledge", "03_Development_Projects"])
        self.assertFalse([f for f in folders if f.startswith("$")])

    def test_the_cli_output_carries_the_note(self) -> None:
        code, out, _ = self.cli("backup", "--root", str(self.root), "--target", str(self.target))
        self.assertEqual(code, 0)
        self.assertIn("Also left out: 1 folder(s) on the tool's exclusion list (Downloads)", out)


class TestCoverageReporting(_Fixture):
    def test_uncovered_entries_lists_folders_and_loose_files(self) -> None:
        partitions = ["02_Learning_Knowledge", "03_Development_Projects"]
        folders, loose = self.mgr.uncovered_entries(partitions)
        self.assertEqual(folders, ["01_AI_Models"])
        self.assertEqual(loose, 3)  # AGENTS.md, README.md, loose_note.txt

    def test_coverage_notes_name_what_is_left_out_and_how_to_include_it(self) -> None:
        manifest = self.mgr.create_snapshot("s1")
        notes = "\n".join(self.mgr.coverage_notes(manifest.partitions, manifest.excluded_dirs, False))
        self.assertIn("Not covered: 01_AI_Models", notes)
        self.assertIn("3 loose file(s)", notes)
        self.assertIn("--partitions", notes)
        self.assertIn("3 hidden folder(s)", notes)
        self.assertIn("--include-hidden", notes)
        with_hidden = "\n".join(self.mgr.coverage_notes(manifest.partitions, [], True))
        self.assertNotIn("hidden folder", with_hidden)

    def test_backup_cli_reports_coverage_in_text_and_json(self) -> None:
        code, out, _ = self.cli("backup", "--root", str(self.root), "--target", str(self.target))
        self.assertEqual(code, 0)
        self.assertIn("Partitions:  02_Learning_Knowledge, 03_Development_Projects", out)
        self.assertIn("Not covered: 01_AI_Models", out)
        self.assertIn("Left out: 3 hidden folder(s)", out)

        code, out, _ = self.cli("backup", "--root", str(self.root), "--target", str(self.target), "--json")
        data: Dict[str, Any] = json.loads(out)
        self.assertEqual(data["not_covered"], {"folders": ["01_AI_Models"], "loose_files": 3})
        self.assertEqual(len(data["excluded_dirs"]), 3)

    def test_backup_cli_include_hidden_copies_git_and_drops_the_warning(self) -> None:
        code, out, _ = self.cli("backup", "--root", str(self.root), "--target", str(self.target), "--include-hidden")
        self.assertEqual(code, 0)
        self.assertNotIn("Left out", out)
        self.assertTrue((self.target / HIDDEN[0]).is_file())

    def test_snapshot_cli_reports_coverage_and_accepts_the_flag(self) -> None:
        code, out, _ = self.cli("snapshot", "create", "c1", "--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("Not covered: 01_AI_Models", out)
        self.assertIn("--include-hidden", out)

        code, out, _ = self.cli("snapshot", "create", "c2", "--root", str(self.root), "--include-hidden", "--json")
        data = json.loads(out)
        self.assertTrue(data["include_hidden"])
        self.assertIn(HIDDEN[0], data["files"])
        self.assertEqual(data["not_covered"]["folders"], ["01_AI_Models"])


if __name__ == "__main__":
    unittest.main()
