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
