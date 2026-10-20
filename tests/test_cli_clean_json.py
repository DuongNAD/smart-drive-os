"""tests/test_cli_clean_json.py - `clean --apply` must purge whichever way the result is printed.

Regression: with --json the command printed the detection report with "dry_run": false and returned
before reaching the purge code, so scripts and agents were told a cleanup happened when nothing did.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

from smart_drive.cli import cmd_clean
from smart_drive.cli.main import build_parser, main

JUNK = (".DS_Store", "Thumbs.db", "scratch.tmp", "__pycache__/mod.pyc")
KEEPERS = ("notes.txt", "important.bak", ".env", ".git/HEAD", "src/main.py")


class TestCleanApply(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_clean_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in JUNK + KEEPERS:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")

    def run_clean(self, *extra: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["clean", "--root", str(self.root), "--tier", "3", *extra])
        return code, out.getvalue(), err.getvalue()

    def present(self, names: Tuple[str, ...]) -> List[str]:
        return [n for n in names if (self.root / n).exists()]

    def test_dry_run_json_changes_nothing(self) -> None:
        code, out, _ = self.run_clean("--json")
        report: Dict[str, Any] = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(report["dry_run"])
        self.assertGreaterEqual(report["junk_count"], 4)
        self.assertNotIn("deleted_count", report)
        self.assertEqual(self.present(JUNK), [n for n in JUNK])

    def test_apply_json_really_deletes_and_reports_it(self) -> None:
        code, out, _ = self.run_clean("--apply", "--json")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertFalse(report["dry_run"])
        self.assertEqual(self.present(("scratch.tmp", ".DS_Store", "Thumbs.db")), [])
        self.assertFalse((self.root / "__pycache__").exists())
        self.assertEqual(report["deleted_count"], 3)
        self.assertEqual(report["quarantined_count"], 1)
        self.assertGreater(report["freed_bytes"], 0)
        self.assertGreater(report["quarantined_bytes"], 0)
        self.assertTrue(report["batch_id"])
        self.assertEqual(
            report["deleted_count"]
            + report["quarantined_count"]
            + report["blocked_count"]
            + report["failed_count"],
            len(report["results"]),
        )
        self.assertEqual(report["failed_count"], 0)
        self.assertTrue(all({"rel_path", "deleted", "quarantined", "status", "reason"} <= set(r) for r in report["results"]))

    def test_apply_json_never_touches_files_that_only_look_like_junk(self) -> None:
        self.run_clean("--apply", "--json")
        self.assertEqual(self.present(KEEPERS), list(KEEPERS))

    def test_apply_json_honours_the_log_option(self) -> None:
        log = self.root.parent / f"{self.root.name}_audit.json"
        self.addCleanup(lambda: log.unlink() if log.exists() else None)
        _, out, _ = self.run_clean("--apply", "--json", "--log", str(log))
        self.assertEqual(json.loads(out)["log"], str(log))
        self.assertTrue(log.is_file(), "the audit log was silently ignored in JSON mode")
        self.assertIn("scratch.tmp", log.read_text(encoding="utf-8"))

    def test_apply_text_mode_still_purges(self) -> None:
        code, out, _ = self.run_clean("--apply")
        self.assertEqual(code, 0)
        self.assertIn("Deleted 3 item(s), freed", out)
        self.assertIn(
            "Quarantined 1 item(s), 0.00 MB — space is freed only after: smart-drive clean --purge-quarantine --older-than 30 --apply",
            out,
        )
        self.assertIn("Restore with: smart-drive clean --restore", out)
        self.assertNotIn("Purged", out)
        self.assertEqual(self.present(("scratch.tmp", ".DS_Store", "Thumbs.db")), [])
        self.assertEqual(self.present(KEEPERS), list(KEEPERS))

    def test_apply_text_mode_only_quarantined_never_prints_purged_or_deleted(self) -> None:
        for rel in (".DS_Store", "Thumbs.db", "__pycache__"):
            p = self.root / rel
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        code, out, _ = self.run_clean("--apply")
        self.assertEqual(code, 0)
        self.assertNotIn("Purged", out)
        self.assertNotIn("Deleted", out)
        self.assertIn(
            "Quarantined 1 item(s), 0.00 MB — space is freed only after: smart-drive clean --purge-quarantine --older-than 30 --apply",
            out,
        )
        self.assertIn("Restore with: smart-drive clean --restore", out)

    def test_apply_json_on_a_clean_drive_reports_zero(self) -> None:
        self.run_clean("--apply", "--json")
        _, out, _ = self.run_clean("--apply", "--json")
        report = json.loads(out)
        self.assertEqual((report["junk_count"], report["deleted_count"], report["quarantined_count"]), (0, 0, 0))


def _can_ignore_permissions() -> bool:
    return sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


@unittest.skipIf(_can_ignore_permissions(), "needs a POSIX user that directory permissions apply to")
class TestCleanReportsFailuresHonestly(unittest.TestCase):
    """A deletion the OS refuses used to be printed as "✓ Purged 1 junk files successfully." with exit code 0."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_clean_fail_")).resolve()
        (self.root / "open").mkdir()
        (self.root / "open" / ".DS_Store").write_text("x", encoding="utf-8")
        (self.root / "locked").mkdir()
        (self.root / "locked" / "Thumbs.db").write_text("x", encoding="utf-8")
        (self.root / "locked").chmod(0o555)  # entries in it cannot be removed
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup((self.root / "locked").chmod, 0o755)  # runs first, so the cleanup above can delete it

    def run_clean(self, *extra: str) -> Tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["clean", "--root", str(self.root), "--apply", *extra])
        return code, out.getvalue()

    def test_text_mode_says_what_failed_and_exits_nonzero(self) -> None:
        code, out = self.run_clean()
        self.assertEqual(code, 1)
        self.assertNotIn("successfully", out)
        self.assertIn("Deleted 1 of 2 item(s), freed", out)
        self.assertIn("locked/Thumbs.db", out.replace("\\", "/"))
        self.assertFalse((self.root / "open" / ".DS_Store").exists())
        self.assertTrue((self.root / "locked" / "Thumbs.db").exists())

    def test_json_mode_counts_the_failure_and_exits_nonzero(self) -> None:
        code, out = self.run_clean("--json")
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual((report["deleted_count"], report["blocked_count"], report["failed_count"]), (1, 0, 1))
        failed = [r for r in report["results"] if r["status"] == "FAILED"]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["deleted"])
        self.assertTrue(failed[0]["reason"])


class TestNestedJunkIsReportedOnce(unittest.TestCase):
    """The scanner does not prune junk directories, and on POSIX the entries inside one matched its rule too:
    `.pytest_cache` and `.pytest_cache/pkg` were both reported, sized and counted as deleted."""

    def test_items_inside_a_reported_junk_directory_are_dropped(self) -> None:
        from smart_drive.core.config import JunkTier
        from smart_drive.core.junk_detector import JunkItem, _drop_items_inside_junk_dirs

        root = os.path.abspath(os.path.join(os.sep, "drive"))

        def item(rel: str, is_dir: bool) -> JunkItem:
            return JunkItem(path=os.path.join(root, *rel.split("/")), rel_path=rel, name=rel.split("/")[-1],
                            size=0, allocated_size=0, is_dir=is_dir, tier=JunkTier.TIER_2_DEV_CACHE,
                            description="test", os_source="Dev")

        items = [
            item("proj/.pytest_cache", True),
            item("proj/.pytest_cache/pkg", True),
            item("proj/.pytest_cache/pkg/file.js", False),
            item("proj/.pytest_cache_backup", True),  # a sibling sharing the name prefix is not inside it
            item("Thumbs.db", False),
        ]
        kept = [i.rel_path for i in _drop_items_inside_junk_dirs(items)]
        self.assertEqual(kept, ["proj/.pytest_cache", "proj/.pytest_cache_backup", "Thumbs.db"])

    def test_clean_lists_a_junk_tree_once(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sd_clean_once_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        nested = root / "proj" / ".pytest_cache" / "pkg"
        nested.mkdir(parents=True)
        (nested / "file.js").write_text("x", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["clean", "--root", str(root), "--tier", "2", "--json"])
        report = json.loads(out.getvalue())
        self.assertEqual(code, 0, out.getvalue())
        listed = [j["rel_path"].replace("\\", "/") for j in report["junk_items"]]
        self.assertEqual(listed, ["proj/.pytest_cache"], out.getvalue())


@unittest.skipIf(_can_ignore_permissions(), "needs a POSIX user that directory permissions apply to")
class TestDirectoryPurgesAreVerified(unittest.TestCase):
    """shutil.rmtree reports problems only through its callback, which swallowed them: a directory that was
    not removed was still reported as DELETED ("Purged 1 junk files successfully", exit 0), and the callback
    had set the folder's mode to 0600, so what was left behind was unusable as well."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_clean_dirs_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_clean(self, *extra: str) -> Tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(["clean", "--root", str(self.root), "--tier", "2", "--apply", *extra])
        return code, out.getvalue()

    def test_a_read_only_folder_inside_a_junk_tree_does_not_stop_the_purge(self) -> None:
        pkg = self.root / "proj" / ".pytest_cache" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "file.js").write_text("x", encoding="utf-8")
        pkg.chmod(0o555)
        self.addCleanup(lambda: pkg.chmod(0o755) if pkg.exists() else None)
        code, out = self.run_clean("--json")
        report = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertEqual((report["deleted_count"], report["failed_count"]), (1, 0), out)
        self.assertFalse((self.root / "proj" / ".pytest_cache").exists())
        self.assertTrue((self.root / "proj").is_dir())

    def test_a_tree_that_cannot_be_removed_is_a_failure_and_keeps_its_permissions(self) -> None:
        locked = self.root / "locked"
        cache = locked / ".pytest_cache"
        cache.mkdir(parents=True)
        (cache / "a.txt").write_text("x", encoding="utf-8")
        mode_before = stat.S_IMODE(cache.stat().st_mode)
        locked.chmod(0o555)  # the folder cannot be removed from its parent
        self.addCleanup(locked.chmod, 0o755)
        code, out = self.run_clean("--json")
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual((report["deleted_count"], report["failed_count"]), (0, 1))
        self.assertEqual(report["results"][0]["status"], "FAILED")
        self.assertIn("could not be fully removed", report["results"][0]["reason"])
        self.assertTrue(cache.is_dir())
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), mode_before)  # not 0600
        self.assertEqual(stat.S_IMODE(locked.stat().st_mode), 0o555)  # the folder above is never touched

    def test_folders_that_cannot_even_be_listed_are_opened_up_and_removed(self) -> None:
        """mode 000 / 100: rmtree's failure is in os.open/os.scandir, which cannot simply be called again."""
        for index, mode in enumerate((0o000, 0o100, 0o300)):
            with self.subTest(mode=oct(mode)):
                cache = self.root / f"proj{index}" / ".pytest_cache"
                hidden = cache / "d"
                hidden.mkdir(parents=True)
                (hidden / "file.js").write_text("x", encoding="utf-8")
                hidden.chmod(mode)
                self.addCleanup(lambda h=hidden: h.chmod(0o755) if h.exists() else None)
                code, out = self.run_clean("--json")
                report = json.loads(out)
                self.assertEqual(code, 0, out)
                self.assertEqual(report["failed_count"], 0)
                self.assertFalse(cache.exists())

    def test_a_dotdot_path_is_refused_and_nothing_is_touched(self) -> None:
        """Opening a tree up must never chmod a folder that is not the one being deleted."""
        from smart_drive.core.purge_engine import PurgeEngine

        elsewhere, tree = self.root / "elsewhere", self.root / "tree"
        (tree / "ro").mkdir(parents=True)
        elsewhere.mkdir()
        os.symlink(elsewhere, self.root / "lnk")
        (tree / "ro").chmod(0o555)
        self.addCleanup((tree / "ro").chmod, 0o755)
        with self.assertRaises(OSError):
            PurgeEngine(str(self.root), dry_run=False)._make_writable_and_remove_dir(str(self.root / "lnk" / ".." / "tree"))
        self.assertEqual(stat.S_IMODE((tree / "ro").stat().st_mode), 0o555)
        self.assertTrue((tree / "ro").is_dir())

    def test_a_link_given_as_the_tree_root_is_refused_and_its_target_survives(self) -> None:
        from smart_drive.core.purge_engine import PurgeEngine

        target = self.root / "real"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        os.symlink(target, self.root / "lnk")
        with self.assertRaises(OSError):
            PurgeEngine(str(self.root), dry_run=False)._make_writable_and_remove_dir(str(self.root / "lnk"))
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_the_text_output_does_not_claim_success_either(self) -> None:
        locked = self.root / "locked"
        (locked / ".pytest_cache").mkdir(parents=True)
        (locked / ".pytest_cache" / "a.txt").write_text("x", encoding="utf-8")
        locked.chmod(0o555)
        self.addCleanup(locked.chmod, 0o755)
        code, out = self.run_clean()
        self.assertEqual(code, 1)
        self.assertNotIn("successfully", out)
        self.assertIn("Deleted 0 of 1 item(s), freed", out)


class TestBlockedIsNotFailed(unittest.TestCase):
    """Items the safety guard refuses are expected behaviour, so they are reported but are not an error."""

    def test_tally_separates_deleted_blocked_and_failed(self) -> None:
        results = [
            {"rel_path": "a", "deleted": True, "status": "DELETED", "reason": ""},
            {"rel_path": "b", "deleted": False, "status": "BLOCKED", "reason": "protected"},
            {"rel_path": "c", "deleted": False, "status": "FAILED", "reason": "denied"},
        ]
        self.assertEqual(cmd_clean._tally(results), (1, 1, 1))

    def test_a_run_with_only_blocked_items_exits_zero_and_says_so(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sd_clean_blocked_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".DS_Store").write_text("x", encoding="utf-8")
        blocked = [{"rel_path": ".DS_Store", "deleted": False, "status": "BLOCKED", "reason": "protected"}]
        out = io.StringIO()
        with patch.object(cmd_clean, "_purge", return_value=(0, blocked)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = main(["clean", "--root", str(root), "--apply"])
        self.assertEqual(code, 0)
        self.assertIn("1 item(s) kept by the safety guard", out.getvalue())
        self.assertIn("Deleted 0 of 1 item(s), freed", out.getvalue())


class TestReclaimableSpaceCountsEveryItem(unittest.TestCase):
    def test_the_total_is_not_limited_to_the_thirty_items_listed(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sd_clean_total_")).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for i in range(40):
            (root / f"junk_{i:02d}.tmp").write_bytes(b"x" * 1024 * 1024)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(["clean", "--root", str(root), "--tier", "3"])
        self.assertIn("Found 40 junk items. Reclaimable space: 40.00 MB", out.getvalue())
        self.assertIn("and 10 more items", out.getvalue())


class TestCleanQuarantineCLI(unittest.TestCase):
    """T11 CLI tests: Tier 3 quarantine vs Tier 1-2 permanent delete, --list-quarantine, --restore, --purge-quarantine, --no-quarantine."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_clean_q_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel in JUNK + ("x.tmp",) + KEEPERS:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("content_" + rel.replace("/", "_"), encoding="utf-8")

    def run_cmd(self, *args: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["clean", "--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def make_batch(self) -> Path:
        """Quarantines the Tier 3 files and returns the folder of the batch that holds them."""
        _, out, _ = self.run_cmd("--tier", "3", "--apply", "--json")
        batch_dir = self.root / ".smart_drive" / "quarantine" / json.loads(out)["batch_id"]
        self.assertTrue((batch_dir / "scratch.tmp").is_file())
        return batch_dir

    def test_tier1_only_run_does_not_create_quarantine_dir(self) -> None:
        code, out, _ = self.run_cmd("--tier", "1", "--apply", "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertIsNone(report["batch_id"])
        self.assertFalse((self.root / ".DS_Store").exists())
        self.assertFalse((self.root / "Thumbs.db").exists())
        self.assertFalse((self.root / ".smart_drive" / "quarantine").exists())

    def test_apply_quarantines_only_tier3_and_restore_brings_files_back_without_overwriting(self) -> None:
        code, out, _ = self.run_cmd("--tier", "3", "--apply", "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["quarantine"])
        batch_id = report["batch_id"]
        self.assertTrue(batch_id)

        batch_dir = self.root / ".smart_drive" / "quarantine" / batch_id
        self.assertTrue((batch_dir / "manifest.json").is_file())
        # Tier 3 items (scratch.tmp, x.tmp) are quarantined
        self.assertTrue((batch_dir / "scratch.tmp").is_file())
        self.assertTrue((batch_dir / "x.tmp").is_file())
        # Tier 1 (Thumbs.db, .DS_Store) and Tier 2 (__pycache__) are permanently deleted, NOT in quarantine
        self.assertFalse((batch_dir / "Thumbs.db").exists())
        self.assertFalse((batch_dir / ".DS_Store").exists())
        self.assertFalse((batch_dir / "__pycache__").exists())
        manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({m["original"] for m in manifest}, {"scratch.tmp", "x.tmp"})

        # --list-quarantine (JSON + text)
        code_l, out_l, _ = self.run_cmd("--list-quarantine", "--json")
        self.assertEqual(code_l, 0)
        list_rep = json.loads(out_l)
        self.assertEqual(list_rep["total_batches"], 1)
        self.assertEqual(list_rep["batches"][0]["batch_id"], batch_id)
        self.assertTrue(list_rep["batches"][0].get("local_time"))

        code_lt, out_lt, _ = self.run_cmd("--list-quarantine")
        self.assertEqual(code_lt, 0)
        self.assertIn(batch_id, out_lt)
        self.assertIn(list_rep["batches"][0]["local_time"], out_lt)

        # Recreate scratch.tmp at original path to verify --restore never overwrites
        (self.root / "scratch.tmp").write_text("NEW_USER_CONTENT", encoding="utf-8")

        code_r, out_r, _ = self.run_cmd("--restore", "latest", "--json")
        self.assertEqual(code_r, 0)
        rest_rep = json.loads(out_r)
        self.assertEqual(rest_rep["skipped_count"], 1)
        self.assertEqual(rest_rep["restored_count"], 1)
        self.assertEqual((self.root / "scratch.tmp").read_text(encoding="utf-8"), "NEW_USER_CONTENT")
        self.assertTrue((self.root / "x.tmp").is_file())
        # Conflicted file remains in quarantine
        self.assertTrue((batch_dir / "scratch.tmp").is_file())

    def test_no_quarantine_permanently_deletes_without_creating_quarantine_dir(self) -> None:
        code, out, _ = self.run_cmd("--tier", "3", "--apply", "--no-quarantine", "--json")
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertFalse(report["quarantine"])
        self.assertIsNone(report["batch_id"])
        self.assertFalse((self.root / ".DS_Store").exists())
        self.assertFalse((self.root / "scratch.tmp").exists())
        self.assertFalse((self.root / ".smart_drive" / "quarantine").exists())

    def test_purge_quarantine_cli_dry_run_and_apply_with_older_than(self) -> None:
        self.run_cmd("--tier", "3", "--apply", "--json")
        q_root = self.root / ".smart_drive" / "quarantine"
        batches = [p for p in q_root.iterdir() if p.is_dir()]
        self.assertEqual(len(batches), 1)
        batch_dir = batches[0]

        import time as _time
        future_now = _time.time() + 10 * 86400.0
        with patch("smart_drive.core.purge_engine.time.time", return_value=future_now):
            # Dry-run by default: does not delete batch
            code_p, out_p, _ = self.run_cmd("--purge-quarantine", "--older-than", "7", "--json")
            self.assertEqual(code_p, 0)
            prev_rep = json.loads(out_p)
            self.assertTrue(prev_rep["dry_run"])
            self.assertEqual(prev_rep["matched_count"], 1)
            self.assertTrue(batch_dir.is_dir())

            # With --apply: deletes batch older than 7 days
            code_a, out_a, _ = self.run_cmd("--purge-quarantine", "--older-than", "7", "--apply", "--json")
            self.assertEqual(code_a, 0)
            app_rep = json.loads(out_a)
            self.assertFalse(app_rep["dry_run"])
            self.assertEqual(app_rep["purged_count"], 1)
            self.assertFalse(batch_dir.exists())

    def test_purge_quarantine_without_an_age_keeps_a_batch_younger_than_30_days(self) -> None:
        batch_dir = self.make_batch()
        code, out, _ = self.run_cmd("--purge-quarantine", "--apply", "--json")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((report["purged_count"], report["kept_count"], report["older_than_days"]), (0, 1, 30.0))
        self.assertTrue((batch_dir / "scratch.tmp").is_file())
        self.assertTrue((batch_dir / "manifest.json").is_file())

    def test_purge_quarantine_without_an_age_removes_a_batch_once_it_is_older_than_30_days(self) -> None:
        purge_engine = importlib.import_module("smart_drive.core.purge_engine")
        batch_dir = self.make_batch()
        for days, purged in ((29, 0), (31, 1)):
            with self.subTest(days=days):
                with patch.object(purge_engine.time, "time", return_value=time.time() + days * 86400.0):
                    code, out, _ = self.run_cmd("--purge-quarantine", "--apply", "--json")
                report = json.loads(out)
                self.assertEqual(code, 0)
                self.assertEqual((report["purged_count"], report["kept_count"]), (purged, 1 - purged))
                self.assertEqual(batch_dir.exists(), not purged)

    def test_older_than_zero_purges_even_a_batch_made_a_moment_ago(self) -> None:
        batch_dir = self.make_batch()
        code, out, _ = self.run_cmd("--purge-quarantine", "--older-than", "0", "--apply", "--json")
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((report["purged_count"], report["kept_count"], report["older_than_days"]), (1, 0, 0.0))
        self.assertFalse(batch_dir.exists())

    def test_a_negative_age_is_refused_and_no_batch_is_touched(self) -> None:
        batch_dir = self.make_batch()
        code, out, err = self.run_cmd("--purge-quarantine", "--older-than", "-1", "--apply")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("--older-than must be 0 or more days", err)
        self.assertTrue((batch_dir / "scratch.tmp").is_file())

        code, out, _ = self.run_cmd("--purge-quarantine", "--older-than", "-1", "--apply", "--json")
        report = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "error")
        self.assertIn("--older-than must be 0 or more days", report["error"])
        self.assertTrue((batch_dir / "scratch.tmp").is_file())

    def test_purge_quarantine_batches_keeps_a_fresh_batch_when_it_is_given_no_age(self) -> None:
        purge_engine = importlib.import_module("smart_drive.core.purge_engine")
        batch_dir = self.make_batch()
        report = purge_engine.purge_quarantine_batches(str(self.root), dry_run=False)
        self.assertEqual(purge_engine.QUARANTINE_RETENTION_DAYS, 30.0)
        self.assertIn("QUARANTINE_RETENTION_DAYS", purge_engine.__all__)
        self.assertEqual((report["purged_count"], report["kept_count"], report["older_than_days"]), (0, 1, 30.0))
        self.assertTrue((batch_dir / "scratch.tmp").is_file())

    def test_the_text_report_says_how_many_newer_batches_were_kept_and_how_to_purge_them(self) -> None:
        purge_engine = importlib.import_module("smart_drive.core.purge_engine")
        self.make_batch()
        _, out, _ = self.run_cmd("--purge-quarantine")
        self.assertIn("[DRY RUN] Quarantine purge (older than 30 day(s)): 0 batch(es) would be removed, 1 kept.", out)
        self.assertIn("  1 newer batch(es) kept; --older-than 0 purges every batch.", out)
        # nothing is said when no batch is younger than the age, or when the age is 0 (every batch matches)
        _, out, _ = self.run_cmd("--purge-quarantine", "--older-than", "0")
        self.assertIn("1 batch(es) would be removed, 0 kept.", out)
        self.assertNotIn("newer batch(es) kept", out)
        with patch.object(purge_engine.time, "time", return_value=time.time() + 31 * 86400.0):
            _, out, _ = self.run_cmd("--purge-quarantine")
        self.assertIn("1 batch(es) would be removed, 0 kept.", out)
        self.assertNotIn("newer batch(es) kept", out)

    def test_the_help_of_clean_says_that_the_default_age_is_30_days(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["clean", "--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("default: 30", " ".join(out.getvalue().split()))


if __name__ == "__main__":
    unittest.main()
