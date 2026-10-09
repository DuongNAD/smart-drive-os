"""tests/test_classifier_symlinks.py - The classifier must never follow or move symlinks.

Regression tests for two defects:
- `classify --apply` resolved symlinks and relocated the file they pointed to, even when that file
  lived outside the drive root.
- A looping symlink made `Path.resolve()` raise and aborted the whole command with a traceback.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from smart_drive.cli.main import main
from smart_drive.core.classifier import ClassifierEngine

CSV_BODY = "id,label\n1,a\n2,b\n3,c\n"


@unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
class TestClassifierNeverFollowsLinks(unittest.TestCase):
    """Layout: <tmp>/drive (the managed root) and <tmp>/outside (must never be touched)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.drive = base / "drive"
        self.outside = base / "outside"
        self.drive.mkdir()
        self.outside.mkdir()

        (self.drive / "data.csv").write_text(CSV_BODY)  # a legitimate, classifiable file
        (self.outside / "secret.csv").write_text(CSV_BODY)
        (self.outside / "dir").mkdir()
        (self.outside / "dir" / "inner.csv").write_text(CSV_BODY)

        os.symlink(self.outside / "secret.csv", self.drive / "link_file.csv")
        os.symlink(self.outside / "dir", self.drive / "link_dir")
        os.symlink("loop_b", self.drive / "loop_a")
        os.symlink("loop_a", self.drive / "loop_b")
        self.engine = ClassifierEngine(root_path=self.drive)

    def _outside_untouched(self) -> None:
        self.assertEqual((self.outside / "secret.csv").read_text(), CSV_BODY)
        self.assertEqual((self.outside / "dir" / "inner.csv").read_text(), CSV_BODY)

    def test_scan_returns_only_real_files_and_survives_loops(self) -> None:
        results = self.engine.scan_and_classify(recursive=True)  # raised RuntimeError on the loop before
        self.assertEqual([r.name for r in results], ["data.csv"])
        skipped = {p.name for p in self.engine.skipped_links}
        self.assertEqual(skipped, {"link_file.csv", "link_dir", "loop_a", "loop_b"})

    def test_apply_moves_the_real_file_but_nothing_outside_the_root(self) -> None:
        results = self.engine.scan_and_classify(recursive=True)
        actions = self.engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["MOVED"])
        self.assertFalse((self.drive / "data.csv").exists())
        self._outside_untouched()
        for name in ("link_file.csv", "link_dir", "loop_a", "loop_b"):
            self.assertTrue(os.path.islink(self.drive / name), f"{name} must stay a symlink in place")

    def test_file_symlink_to_outside_is_not_relocated_into_the_drive(self) -> None:
        """The original escape, isolated from the loop crash that used to mask it."""
        os.remove(self.drive / "loop_a")
        os.remove(self.drive / "loop_b")
        results = self.engine.scan_and_classify(recursive=True)
        self.engine.execute_relocation(results, dry_run=False)
        self.assertTrue((self.outside / "secret.csv").is_file(), "outside file was moved into the drive")
        self.assertFalse(any(p.name == "secret.csv" for p in self.drive.rglob("*") if not p.is_symlink()))
        self._outside_untouched()

    def test_inspect_path_ignores_links_and_loops(self) -> None:
        for name in ("link_file.csv", "link_dir", "loop_a"):
            self.assertIsNone(self.engine.inspect_path(self.drive / name), name)

    def test_a_link_given_as_the_scan_target_is_refused(self) -> None:
        self.assertEqual(self.engine.scan_and_classify(target_dir=self.drive / "link_file.csv"), [])
        self.assertEqual(self.engine.scan_and_classify(target_dir=self.drive / "link_dir"), [])
        self._outside_untouched()

    def test_entry_swapped_for_a_link_after_the_scan_is_not_moved(self) -> None:
        results = self.engine.scan_and_classify(recursive=True)
        target = results[0].source_path
        target.unlink()
        os.symlink(self.outside / "secret.csv", target)  # replaced between scan and apply
        actions = self.engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["SKIPPED_SYMLINK"])
        self._outside_untouched()

    def test_cli_apply_leaves_outside_files_alone_and_reports_the_skips(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["classify", "--root", str(self.drive), "--apply", "--json"])
        self.assertEqual(code, 0, err.getvalue())
        report = json.loads(out.getvalue())
        self.assertEqual([a["status"] for a in report["actions"]], ["MOVED"])
        self.assertIn("symlink", err.getvalue())
        self._outside_untouched()


if __name__ == "__main__":
    unittest.main()
