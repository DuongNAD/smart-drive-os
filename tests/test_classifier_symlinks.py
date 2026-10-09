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
import shutil
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


@unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
class TestLinksAboveTheFileAndOnTheDestinationSide(unittest.TestCase):
    """The first fix only looked at the last path component; a link in a parent folder or in the
    destination still let `classify --apply` move files out of, or write files outside of, the drive."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.drive = base / "drive"
        self.outside = base / "outside"
        self.elsewhere = base / "elsewhere"
        for folder in (self.drive, self.outside, self.elsewhere):
            folder.mkdir()
        (self.outside / "dir").mkdir()
        (self.outside / "dir" / "inner.csv").write_text(CSV_BODY)
        (self.outside / "solo.csv").write_text(CSV_BODY)
        os.symlink(self.outside, self.drive / "linkdir")  # a folder inside the drive that really lives outside
        self.base = base
        self.engine = ClassifierEngine(root_path=self.drive)

    def _outside_untouched(self) -> None:
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()), ["dir", "solo.csv"])
        self.assertEqual((self.outside / "dir" / "inner.csv").read_text(), CSV_BODY)
        self.assertEqual((self.outside / "solo.csv").read_text(), CSV_BODY)

    # --- sources reached through a linked parent

    def test_a_folder_typed_inside_the_drive_that_leads_outside_is_refused(self) -> None:
        self.assertEqual(self.engine.scan_and_classify(target_dir=self.drive / "linkdir" / "dir"), [])
        self.assertEqual(self.engine.scan_and_classify(target_dir=self.drive / "linkdir"), [])
        self.assertTrue(self.engine.skipped_links)
        self._outside_untouched()

    def test_a_file_typed_inside_the_drive_that_leads_outside_is_refused(self) -> None:
        self.assertEqual(self.engine.scan_and_classify(target_dir=self.drive / "linkdir" / "solo.csv"), [])
        self.assertIsNone(self.engine.inspect_path(self.drive / "linkdir" / "solo.csv"))
        self._outside_untouched()

    def test_cli_apply_on_such_a_path_moves_nothing(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["classify", str(self.drive / "linkdir" / "dir"), "--root", str(self.drive), "--apply", "--json"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["actions"], [])
        self.assertIn("symlink", err.getvalue())
        self._outside_untouched()
        self.assertFalse((self.drive / "01_AI_Models").exists())

    def test_a_parent_folder_swapped_for_a_link_after_the_scan_is_not_moved_through(self) -> None:
        (self.drive / "sub").mkdir()
        (self.drive / "sub" / "f.csv").write_text(CSV_BODY)
        (self.outside / "f.csv").write_text(CSV_BODY)  # what the swapped folder will expose
        results = [r for r in self.engine.scan_and_classify(recursive=True) if r.name == "f.csv"]
        self.assertEqual(len(results), 1)
        os.rename(self.drive / "sub", self.drive / "sub_real")
        os.symlink(self.outside, self.drive / "sub")  # same path, now leading outside
        actions = self.engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["SKIPPED_SYMLINK"])
        self.assertIn("symlink", actions[0].error_message)
        self.assertTrue((self.outside / "f.csv").is_file(), "an outside file was moved into the drive")
        self.assertFalse((self.drive / "01_AI_Models").exists())

    # --- destinations

    def test_a_taxonomy_folder_that_is_a_link_is_not_written_through(self) -> None:
        (self.drive / "data.csv").write_text(CSV_BODY)
        os.symlink(self.elsewhere, self.drive / "01_AI_Models")
        results = self.engine.scan_and_classify(recursive=True)
        self.assertEqual([r.name for r in results], ["data.csv"])
        actions = self.engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["SKIPPED_SYMLINK"])
        self.assertIn("destination", actions[0].error_message)
        self.assertEqual(list(self.elsewhere.iterdir()), [], "a file was written outside the drive")
        self.assertTrue((self.drive / "data.csv").is_file())

    def test_a_dangling_link_where_the_file_would_go_is_not_written_through(self) -> None:
        (self.drive / "data.csv").write_text(CSV_BODY)
        datasets = self.drive / "01_AI_Models" / "Datasets"
        datasets.mkdir(parents=True)
        victim = self.base / "outside" / "new_victim.csv"
        os.symlink(victim, datasets / "data.csv")  # dangling: the target does not exist
        results = self.engine.scan_and_classify(recursive=True)
        actions = self.engine.execute_relocation(results, dry_run=False)
        self.assertFalse(victim.exists(), "the move created a file at the link's target")
        self.assertEqual([a.status for a in actions], ["MOVED"])
        self.assertTrue(actions[0].collision_resolved, "the dangling link should count as occupying the name")
        self.assertTrue((datasets / "data_1.csv").is_file())
        self.assertTrue(os.path.islink(datasets / "data.csv"))

    def test_the_dry_run_plan_shows_the_same_skips_as_apply(self) -> None:
        (self.drive / "data.csv").write_text(CSV_BODY)
        os.symlink(self.elsewhere, self.drive / "01_AI_Models")
        results = self.engine.scan_and_classify(recursive=True)
        plan = self.engine.execute_relocation(results, dry_run=True)
        self.assertEqual([a.status for a in plan], ["SKIPPED_SYMLINK"])

    def test_text_mode_apply_says_what_it_left_alone(self) -> None:
        (self.drive / "data.csv").write_text(CSV_BODY)
        os.symlink(self.elsewhere, self.drive / "01_AI_Models")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["classify", "--root", str(self.drive), "--apply"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("Left alone (symlink/junction): data.csv", out.getvalue())
        self.assertIn("1 left alone because of symlinks", out.getvalue())

    # --- the drive itself reached through a link

    def test_a_root_given_through_a_link_is_still_classified(self) -> None:
        (self.drive / "data.csv").write_text(CSV_BODY)
        os.symlink(self.drive, self.base / "drive_link")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["classify", "--root", str(self.base / "drive_link"), "--apply", "--json"])
        self.assertEqual(code, 0, err.getvalue())
        report = json.loads(out.getvalue())
        self.assertEqual([a["status"] for a in report["actions"]], ["MOVED"])
        self.assertTrue((self.drive / "01_AI_Models" / "Datasets" / "data.csv").is_file())
        self.assertNotIn("Refusing", err.getvalue())

    def test_a_link_inside_the_drive_that_stays_inside_is_harmless_as_a_scan_target(self) -> None:
        (self.drive / "real").mkdir()
        (self.drive / "real" / "data.csv").write_text(CSV_BODY)
        os.symlink(self.drive / "real", self.drive / "shortcut")
        results = self.engine.scan_and_classify(target_dir=self.drive / "shortcut")
        self.assertEqual([r.name for r in results], ["data.csv"])
        self.assertEqual(results[0].source_path, self.drive / "real" / "data.csv")  # the real location

    # --- deliberate imports stay possible

    def test_a_location_outside_the_drive_that_was_named_as_such_can_still_be_imported(self) -> None:
        incoming = self.base / "incoming"
        incoming.mkdir()
        (incoming / "data.csv").write_text(CSV_BODY)
        results = self.engine.scan_and_classify(target_dir=incoming)
        self.assertEqual([r.name for r in results], ["data.csv"])
        actions = self.engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["MOVED"])
        self.assertTrue((self.drive / "01_AI_Models" / "Datasets" / "data.csv").is_file())

    def test_links_found_while_importing_are_still_left_alone(self) -> None:
        incoming = self.base / "incoming"
        incoming.mkdir()
        (incoming / "data.csv").write_text(CSV_BODY)
        os.symlink(self.outside / "solo.csv", incoming / "link.csv")
        results = self.engine.scan_and_classify(target_dir=incoming)
        self.assertEqual([r.name for r in results], ["data.csv"])
        self.assertEqual({p.name for p in self.engine.skipped_links}, {"link.csv"})


@unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
class TestTheDriveSpelledAnotherWay(unittest.TestCase):
    """The link check compared the typed path with the resolved root letter by letter, so any other
    spelling of the same drive (macOS' /var -> /private/var, a link to the drive, another letter case)
    counted as "outside, named on purpose" and `classify <drive>/linkdir/dir --apply` moved outside files in.
    The existing tests resolve() their temp dir, which is why they never saw it."""

    def setUp(self) -> None:
        # Deliberately NOT resolved: on macOS mkdtemp() lives under /var, an alias of /private/var.
        self.base = Path(tempfile.mkdtemp(prefix="sd_alias_"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.drive = self.base / "Drive"
        self.outside = self.base / "outside"
        for folder in (self.drive, self.outside, self.outside / "dir"):
            folder.mkdir(parents=True)
        (self.outside / "dir" / "inner.csv").write_text(CSV_BODY)
        (self.outside / "solo.csv").write_text(CSV_BODY)
        os.symlink(self.outside, self.drive / "linkdir")

    def _outside_untouched(self) -> None:
        self.assertEqual((self.outside / "dir" / "inner.csv").read_text(), CSV_BODY)
        self.assertEqual((self.outside / "solo.csv").read_text(), CSV_BODY)

    def _refused(self, typed: Path, root: Path) -> None:
        engine = ClassifierEngine(root_path=root)
        self.assertEqual(engine.scan_and_classify(target_dir=typed), [], typed)
        self.assertTrue(engine.skipped_links)
        self.assertIsNone(engine.inspect_path(typed / "inner.csv") if typed.is_dir() else engine.inspect_path(typed))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["classify", str(typed), "--root", str(root), "--apply", "--json"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(json.loads(out.getvalue())["actions"], [])
        self._outside_untouched()
        self.assertFalse((self.drive / "01_AI_Models").exists())

    def test_the_unresolved_temp_path_is_the_same_drive(self) -> None:
        """The reported repro: the drive and the typed path both under an alias of the real directory."""
        self._refused(self.drive / "linkdir" / "dir", self.drive)
        self._refused(self.drive / "linkdir" / "solo.csv", self.drive)

    def test_a_link_that_points_at_the_drive_is_the_same_drive(self) -> None:
        alias = self.base / "ssd"
        os.symlink(self.drive, alias)
        self._refused(alias / "linkdir" / "dir", self.drive)
        self._refused(alias / "linkdir" / "solo.csv", alias)  # and with the root given through the link as well

    def test_a_chain_of_links_to_the_drive_is_the_same_drive(self) -> None:
        os.symlink(self.drive, self.base / "hop1")
        os.symlink(self.base / "hop1", self.base / "hop2")
        self._refused(self.base / "hop2" / "linkdir" / "dir", self.drive)

    def test_another_letter_case_of_the_drive_is_the_same_drive(self) -> None:
        shouted = self.base / "DRIVE"
        try:
            same = os.path.samefile(shouted, self.drive)
        except OSError:
            same = False
        if not same:
            self.skipTest("this volume is case-sensitive")
        self._refused(shouted / "linkdir" / "dir", self.drive)

    def test_importing_from_a_named_outside_location_still_works_with_an_aliased_root(self) -> None:
        alias = self.base / "ssd"
        os.symlink(self.drive, alias)
        incoming = self.base / "incoming"
        incoming.mkdir()
        (incoming / "data.csv").write_text(CSV_BODY)
        engine = ClassifierEngine(root_path=alias)
        results = engine.scan_and_classify(target_dir=incoming)
        self.assertEqual([r.name for r in results], ["data.csv"])
        actions = engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["MOVED"])
        self.assertTrue((self.drive / "01_AI_Models" / "Datasets" / "data.csv").is_file())

    def test_the_answer_cache_lives_for_one_scan_only(self) -> None:
        """Files in one folder share the "is it under the drive" answer during a scan; nothing may outlive it."""
        engine = ClassifierEngine(root_path=self.drive)
        (self.drive / "real" / "dir").mkdir(parents=True)
        (self.drive / "real" / "dir" / "data.csv").write_text(CSV_BODY)
        self.assertEqual(len(engine.scan_and_classify(target_dir=self.drive / "real" / "dir")), 1)
        self.assertIsNone(engine._under_root_cache)
        shutil.rmtree(self.drive / "real")
        os.symlink(self.outside, self.drive / "real")  # the same typed path now leads outside
        self.assertEqual(engine.scan_and_classify(target_dir=self.drive / "real" / "dir"), [])
        self._outside_untouched()

    def test_a_folder_inside_the_drive_reached_through_an_alias_is_classified_normally(self) -> None:
        alias = self.base / "ssd"
        os.symlink(self.drive, alias)
        (self.drive / "real").mkdir()
        (self.drive / "real" / "data.csv").write_text(CSV_BODY)
        engine = ClassifierEngine(root_path=self.drive)
        results = engine.scan_and_classify(target_dir=alias / "real")
        self.assertEqual([r.name for r in results], ["data.csv"])


if __name__ == "__main__":
    unittest.main()
