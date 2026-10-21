"""tests/test_duplicates_real_drive.py - `dup` must not open the files of dependency trees and installed apps.

Regression: `smart-drive dup --json` ran for 15 minutes on a 167 GB NTFS drive (342k files, 40k folders) and
printed nothing. It grouped files by size, then opened and hashed every file of every same-size group, and it walked
into dependency trees full of small files that are copies of one another by design: node_modules (about a thousand
packages), Python virtualenvs and site-packages, caches, and into installed applications. With Windows Defender
scanning each opened file that is ~16 files a second. Such "duplicates" are expected and are never offered for
reclamation.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

import smart_drive.core.duplicates as duplicates
from smart_drive.cli.main import main
from smart_drive.core.duplicates import DEPENDENCY_DIR_NAMES, DuplicateDetector
from smart_drive.core.learning_units import clear_unit_cache
from smart_drive.core.system_refs import SystemRef, SystemRefIndex

SAME = b"the same bytes in every copy\n" * 20
OTHER = b"a different payload, shared by another set of copies\n" * 10


class _Tree(unittest.TestCase):
    """A temp tree; the machine's own Registry, PATH and shortcuts are never read (an index is injected)."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_dup_deps_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        collect = patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect",
            side_effect=AssertionError(
                "SystemRefIndex.collect() must not be called in unit tests; inject system_refs=SystemRefIndex([])"
            ),
        )
        collect.start()
        self.addCleanup(collect.stop)

    def write(self, rel: str, data: bytes = SAME) -> Path:
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def rel(self, path: str) -> str:
        return Path(path).relative_to(self.root).as_posix()

    def detector(self, root: Optional[Path] = None, **kwargs: Any) -> DuplicateDetector:
        kwargs.setdefault("system_refs", SystemRefIndex([]))
        return DuplicateDetector(str(root or self.root), **kwargs)

    def reported(self, plan: Dict[str, Any]) -> List[str]:
        return sorted(self.rel(f) for g in plan["duplicate_groups"] for f in g["files"])

    def opened_by(self, run: Callable[[], Any]) -> List[str]:
        """The files `run()` opened to hash them (partial or full), as paths relative to the root."""
        opened: List[str] = []
        real_partial, real_full = duplicates.compute_partial_hash, duplicates.compute_full_sha256

        def partial(path: str, *args: Any, **kwargs: Any) -> str:
            opened.append(self.rel(path))
            return real_partial(path, *args, **kwargs)

        def full(path: str, *args: Any, **kwargs: Any) -> str:
            opened.append(self.rel(path))
            return real_full(path, *args, **kwargs)

        with patch.object(duplicates, "compute_partial_hash", partial), patch.object(
            duplicates, "compute_full_sha256", full
        ):
            run()
        return opened

    def run_dup(self, *argv: str) -> Tuple[int, str, str]:
        """`smart-drive dup --root <tree> ...` in-process: (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with patch(
            "smart_drive.core.system_refs.SystemRefIndex.collect", return_value=SystemRefIndex([])
        ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["dup", "--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()


class TestDependencyTreesAreNotScanned(_Tree):
    # The folders the bug report names, plus the other caches a developer drive collects
    TREES = (
        "node_modules", "bower_components", "jspm_packages", ".pnpm-store", "site-packages", "dist-packages",
        "__pycache__", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".next", ".nuxt",
    )

    def test_every_listed_tree_is_in_the_skip_list(self) -> None:
        self.assertTrue(set(self.TREES) <= DEPENDENCY_DIR_NAMES)
        self.assertIn(".git", DEPENDENCY_DIR_NAMES)

    def test_copies_inside_dependency_trees_are_not_duplicates(self) -> None:
        for i, tree in enumerate(self.TREES):
            self.write(f"app{i}/{tree}/pkg/data.bin")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")

        plan = self.detector().generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["backup/copy.bin", "docs/original.bin"])
        self.assertEqual(plan["duplicate_group_count"], 1)
        self.assertEqual(plan["skipped_dependency_dirs"], len(self.TREES))

    def test_the_names_match_whatever_the_letter_case(self) -> None:
        self.write("a/Node_Modules/x/data.bin")
        self.write("b/NODE_MODULES/x/data.bin")
        self.write("c/Site-Packages/x/data.bin")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 0)
        self.assertEqual(plan["skipped_dependency_dirs"], 3)

    def test_a_virtualenv_is_skipped_whatever_it_is_called(self) -> None:
        for env in ("venv", ".venv", "py311-env"):
            self.write(f"proj/{env}/pyvenv.cfg", b"home = /usr/bin\n")
            self.write(f"proj/{env}/Lib/some/module.py")
        self.write("proj/src/module.py")  # the project's own copy
        self.write("archive/module.py")

        plan = self.detector().generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["archive/module.py", "proj/src/module.py"])
        self.assertEqual(plan["skipped_dependency_dirs"], 3)

    def test_a_folder_without_pyvenv_cfg_is_not_taken_for_a_virtualenv(self) -> None:
        self.write("proj/venv/Lib/some/module.py")
        self.write("proj/src/module.py")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["proj/src/module.py", "proj/venv/Lib/some/module.py"])
        self.assertEqual(plan["skipped_dependency_dirs"], 0)

    def test_a_cache_directory_tagged_with_cachedir_tag_is_skipped(self) -> None:
        tag = duplicates.CACHE_TAG_SIGNATURE + b"\n# This file is a cache directory tag created by cargo.\n"
        for crate in ("one", "two"):
            self.write(f"{crate}/target/CACHEDIR.TAG", tag)  # what Cargo writes into a target/ folder
            self.write(f"{crate}/target/debug/deps/lib.rlib")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")

        plan = self.detector().generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["backup/copy.bin", "docs/original.bin"])
        self.assertEqual(plan["skipped_dependency_dirs"], 2)

    def test_a_cachedir_tag_without_the_signature_is_only_a_file(self) -> None:
        self.write("one/target/CACHEDIR.TAG", b"just a note")
        self.write("one/target/lib.rlib")
        self.write("two/target/CACHEDIR.TAG", b"")
        self.write("two/target/lib.rlib")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["one/target/lib.rlib", "two/target/lib.rlib"])
        self.assertEqual(plan["skipped_dependency_dirs"], 0)

    def test_the_folder_asked_for_is_scanned_even_when_it_is_a_tagged_cache(self) -> None:
        self.write("target/CACHEDIR.TAG", duplicates.CACHE_TAG_SIGNATURE)
        self.write("target/a/lib.rlib")
        self.write("target/b/lib.rlib")
        plan = self.detector(self.root / "target").generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)
        plan = self.detector(skip_dependency_dirs=False).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)  # (and a switch that turns the rule off)

    def test_git_internals_are_not_scanned(self) -> None:
        self.write("repo/.git/objects/aa/blob")
        self.write("other/.git/objects/aa/blob")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 0)

    def test_the_files_of_a_dependency_tree_are_never_opened(self) -> None:
        for i in range(5):
            self.write(f"app{i}/node_modules/pkg/data.bin")
            self.write(f"app{i}/.venv/pyvenv.cfg", b"home = /usr/bin\n")
            self.write(f"app{i}/.venv/Lib/site-packages/pkg/data.bin")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")

        opened = self.opened_by(lambda: self.detector().generate_reclamation_plan())

        self.assertTrue(opened)  # the two ordinary copies were compared
        self.assertEqual(sorted(set(opened)), ["backup/copy.bin", "docs/original.bin"])

    def test_dependency_trees_are_scanned_when_asked_to(self) -> None:
        self.write("a/node_modules/x/data.bin")
        self.write("b/node_modules/x/data.bin")
        plan = self.detector(skip_dependency_dirs=False).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["a/node_modules/x/data.bin", "b/node_modules/x/data.bin"])
        self.assertEqual(plan["skipped_dependency_dirs"], 0)

    def test_the_folder_asked_for_is_scanned_even_when_it_is_a_dependency_tree(self) -> None:
        self.write("node_modules/a/data.bin")
        self.write("node_modules/b/data.bin")
        plan = self.detector(self.root / "node_modules").generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)

        self.write("env/pyvenv.cfg", b"home = /usr/bin\n")
        self.write("env/x/data.bin")
        self.write("env/y/data.bin")
        plan = self.detector(self.root / "env").generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)


class TestFoldersUsedBySystemAreNotScanned(_Tree):
    """Windows and installed apps depend on these folders: nothing in them is ever offered for reclamation."""

    def test_folders_windows_or_installed_apps_depend_on_are_not_scanned(self) -> None:
        for rel in ("App/a.bin", "App/sub/b.bin", "Svc/data/c.bin", "tool/d.bin", "Tools/e.bin"):
            self.write(rel)
        self.write("tool/unins000.exe", b"MZ")  # an uninstaller marks an installed app, no registry entry needed
        self.write("docs/original.bin")
        self.write("backup/copy.bin")
        index = SystemRefIndex([
            SystemRef(kind="installed_app", source='installed app "Demo"', target=str(self.root / "App")),
            SystemRef(kind="service", source="service DemoSvc", target=str(self.root / "Svc" / "Binn" / "svc.exe")),
            SystemRef(kind="path", source="PATH (User)", target=str(self.root / "Tools" / "bin")),
        ])

        plan = self.detector(system_refs=index).generate_reclamation_plan()

        # the copies inside the protected folders do not make a group
        self.assertEqual(self.reported(plan), ["backup/copy.bin", "docs/original.bin"])
        self.assertEqual(plan["skipped_protected_dirs"], 4)
        why = {Path(e["path"]).name: e["refs"][0]["kind"] for e in plan["protected_by_system"]}
        self.assertEqual(why, {"App": "installed_app", "Svc": "service", "tool": "uninstaller", "Tools": "path"})

    def test_a_protected_folder_is_not_entered_so_its_files_are_never_opened(self) -> None:
        for i in range(6):
            self.write(f"App/lib{i}/data.bin")
        self.write("docs/original.bin")
        self.write("backup/copy.bin")
        shortcut = SystemRef(kind="shortcut", source="Demo.lnk", target=str(self.root / "App" / "demo.exe"))
        index = SystemRefIndex([shortcut])

        opened = self.opened_by(lambda: self.detector(system_refs=index).generate_reclamation_plan())

        self.assertEqual(sorted(set(opened)), ["backup/copy.bin", "docs/original.bin"])

    def test_references_to_other_places_protect_nothing(self) -> None:
        self.write("a/x.bin")
        self.write("b/x.bin")
        other = Path(tempfile.gettempdir()) / "sd_dup_elsewhere_never_created"
        index = SystemRefIndex([
            SystemRef(kind="installed_app", source='installed app "Far"', target=str(other)),
            SystemRef(kind="path", source="PATH", target=str(other / "bin")),
        ])
        plan = self.detector(system_refs=index).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["a/x.bin", "b/x.bin"])
        self.assertEqual((plan["skipped_protected_dirs"], plan["protected_by_system"]), (0, []))

    def test_the_scanned_root_is_judged_only_by_what_lies_below_it(self) -> None:
        # A drive root that is itself on PATH: its folders and documents are still scanned, but a script Windows can
        # start by name from there is pinned, as `organize` pins it
        self.write("a.bin")
        self.write("sub/a_copy.bin")
        self.write("launch.ps1", OTHER)
        self.write("sub/launch_copy.ps1", OTHER)
        index = SystemRefIndex([SystemRef(kind="path", source="PATH (User)", target=str(self.root))])

        plan = self.detector(system_refs=index).generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["a.bin", "sub/a_copy.bin"])
        self.assertEqual([Path(e["path"]).name for e in plan["protected_by_system"]], ["launch.ps1"])
        self.assertEqual(plan["skipped_protected_dirs"], 0)  # a file, not a folder

    def test_references_are_collected_once_for_the_scanned_root(self) -> None:
        for rel in ("a/x.bin", "b/x.bin", "c/d/x.bin"):
            self.write(rel)
        fake = SystemRefIndex([SystemRef(kind="installed_app", source="app", target=str(self.root / "c"))])
        with patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=fake) as collect:
            det = DuplicateDetector(str(self.root))
            first = det.generate_reclamation_plan()
            second = det.generate_reclamation_plan()
        collect.assert_called_once_with(shortcut_roots=[str(self.root)])
        self.assertEqual(self.reported(first), ["a/x.bin", "b/x.bin"])
        self.assertEqual(self.reported(second), ["a/x.bin", "b/x.bin"])
        self.assertEqual(len(second["protected_by_system"]), 1)  # reported per run, not piled up

    def test_nothing_is_collected_when_an_index_is_given(self) -> None:
        self.write("a/x.bin")
        self.write("b/x.bin")
        self.detector().generate_reclamation_plan()  # the guard in setUp would raise


class TestMinSize(_Tree):
    """Cheap work first: files below the minimum are dropped by size alone, before anything is opened."""

    def seed(self) -> None:
        for rel in ("a/small1", "b/small2"):
            self.write(rel, b"s" * 40)
        for rel in ("a/big1", "b/big2"):
            self.write(rel, b"B" * 5000)
        for rel in ("a/empty1", "b/empty2"):
            self.write(rel, b"")

    def test_files_below_the_minimum_are_neither_grouped_nor_opened(self) -> None:
        self.seed()
        plan: Dict[str, Any] = {}
        opened = self.opened_by(lambda: plan.update(self.detector(min_size=1000).generate_reclamation_plan()))

        self.assertEqual(self.reported(plan), ["a/big1", "b/big2"])
        self.assertEqual(sorted(set(opened)), ["a/big1", "b/big2"])
        self.assertEqual((plan["min_size"], plan["files_below_min_size"]), (1000, 4))
        self.assertEqual(plan["total_reclaimable_bytes"], 5000)

    def test_by_default_empty_files_are_grouped_but_never_opened(self) -> None:
        self.seed()
        plan: Dict[str, Any] = {}
        opened = self.opened_by(lambda: plan.update(self.detector().generate_reclamation_plan()))

        self.assertEqual(sorted(g["size"] for g in plan["duplicate_groups"]), [0, 40, 5000])
        empty = next(g for g in plan["duplicate_groups"] if g["size"] == 0)
        self.assertEqual((empty["reclaimable_bytes"], empty["reclaimable_slack"]), (0, 0))
        self.assertFalse([name for name in opened if "empty" in name])
        self.assertEqual((plan["min_size"], plan["files_below_min_size"]), (0, 0))

    def test_a_minimum_of_one_byte_leaves_out_the_empty_files(self) -> None:
        self.seed()
        plan = self.detector(min_size=1).generate_reclamation_plan()
        self.assertEqual(sorted(g["size"] for g in plan["duplicate_groups"]), [40, 5000])
        self.assertEqual(plan["files_below_min_size"], 2)

    def test_a_negative_minimum_means_no_minimum(self) -> None:
        self.assertEqual(self.detector(min_size=-5).min_size, 0)


class TestDupCommandMinSize(_Tree):
    def seed(self) -> None:
        for rel in ("a/small1", "b/small2"):
            self.write(rel, b"s" * 40)
        for rel in ("a/big1", "b/big2"):
            self.write(rel, b"B" * 5000)

    def test_min_size_flag_filters_the_json_plan(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--json", "--min-size", "1000")
        self.assertEqual(code, 0)
        plan = json.loads(out)
        self.assertEqual(self.reported(plan), ["a/big1", "b/big2"])
        self.assertEqual(plan["min_size"], 1000)

    def test_every_file_counts_without_the_flag(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--json")
        self.assertEqual(code, 0)
        self.assertEqual(self.reported(json.loads(out)), ["a/big1", "a/small1", "b/big2", "b/small2"])

    def test_min_size_flag_in_text_mode(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--min-size", "1000")
        self.assertEqual(code, 0)
        self.assertIn("Found 1 duplicate groups (2 files)", out)

    def test_a_negative_minimum_is_ignored_and_a_word_is_refused(self) -> None:
        self.seed()
        code, out, _ = self.run_dup("--json", "--min-size", "-5")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["min_size"], 0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
            self.run_dup("--min-size", "lots")
        self.assertEqual(refused.exception.code, 2)


class TestPartialHashSamples(_Tree):
    """The second phase hashes a file's first 8KB and, above 16KB, its last 8KB too, as the tool description says."""

    def partial(self, name: str, data: bytes) -> str:
        return duplicates.compute_partial_hash(str(self.write(name, data)))

    def test_the_head_and_the_tail_of_a_file_over_16kb_decide_the_hash(self) -> None:
        head, tail = b"H" * 8192, b"T" * 8192
        whole = self.partial("whole.bin", head + b"m" * 10000 + tail)
        self.assertEqual(self.partial("other_middle.bin", head + b"x" * 10000 + tail), whole)
        self.assertNotEqual(self.partial("other_head.bin", b"h" + head[1:] + b"m" * 10000 + tail), whole)
        self.assertNotEqual(self.partial("other_tail.bin", head + b"m" * 10000 + tail[:-1] + b"t"), whole)

    def test_a_file_of_16kb_or_less_is_hashed_on_its_head_alone(self) -> None:
        self.assertEqual(self.partial("a.bin", b"a" * 16384), self.partial("b.bin", b"a" * 8192 + b"z" * 8192))
        self.assertNotEqual(self.partial("c.bin", b"a" * 16385), self.partial("d.bin", b"a" * 16384 + b"z"))


class TestHashingRunsInParallel(_Tree):
    """Opening a file is what costs (Windows Defender scans it): the opens overlap, the answer does not change."""

    def seed_mixed_tree(self) -> None:
        head, tail = b"H" * 8192, b"T" * 8192
        for i in range(12):  # real duplicates of different sizes, three copies each
            payload = (f"payload {i} ".encode() * (50 + 400 * i))[: 300 + 2500 * i]
            for copy in range(3):
                self.write(f"set{i % 4}/dir{copy}/file{i}.bin", payload)
        for i in range(6):  # same size, same head and tail, different middle: only the full hash tells them apart
            self.write(f"lookalikes/a{i}.bin", head + bytes([i]) * 20000 + tail)
            self.write(f"lookalikes/b{i}.bin", head + bytes([i + 100]) * 20000 + tail)
        for i in range(5):
            self.write(f"unique/u{i}.bin", b"u" * (i + 1))

    def expected_groups(self) -> List[List[str]]:
        """Duplicate groups worked out independently of the detector: whole-file SHA-256 of every file."""
        import hashlib

        by_digest: Dict[str, List[str]] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                by_digest.setdefault(digest, []).append(path.relative_to(self.root).as_posix())
        return sorted(sorted(files) for files in by_digest.values() if len(files) > 1)

    def test_the_groups_are_the_same_whatever_the_number_of_workers(self) -> None:
        self.seed_mixed_tree()
        sequential = self.detector(workers=1).generate_reclamation_plan()
        parallel = self.detector(workers=8).generate_reclamation_plan()
        self.assertGreaterEqual(sequential.pop("elapsed_seconds"), 0)
        self.assertGreaterEqual(parallel.pop("elapsed_seconds"), 0)
        self.assertEqual(sequential, parallel)  # same groups, same order, same counts
        found = sorted(sorted(self.rel(f) for f in g["files"]) for g in parallel["duplicate_groups"])
        self.assertEqual(found, self.expected_groups())
        self.assertEqual(parallel["duplicate_group_count"], 12)

    def test_what_was_opened_is_reported(self) -> None:
        self.write("u1", b"x" * 11)
        self.write("u2", b"x" * 12)  # alone with its size
        self.write("d1", b"d" * 3000)
        self.write("d2", b"d" * 3000)  # real duplicates: hashed on the head, then in full
        self.write("e1", b"1" * 20000)
        self.write("e2", b"2" * 20000)  # same size, different head: the partial hash settles it
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(plan["files_scanned"], 6)
        self.assertEqual(plan["size_candidate_files"], 4)
        self.assertEqual(plan["files_partially_hashed"], 4)
        self.assertEqual(plan["files_fully_hashed"], 2)

    def test_several_files_are_hashed_at_the_same_time(self) -> None:
        for name in ("a1", "a2"):
            self.write(f"x/{name}.bin", b"a" * 500)
        for name in ("b1", "b2"):
            self.write(f"y/{name}.bin", b"b" * 600)
        meet = threading.Barrier(2, timeout=20)  # two files must be inside the hash function together, or this breaks
        real = duplicates.compute_partial_hash

        def partial(path: str, *args: Any, **kwargs: Any) -> str:
            meet.wait()
            return real(path, *args, **kwargs)

        with patch.object(duplicates, "compute_partial_hash", partial):
            plan = self.detector(workers=2).generate_reclamation_plan()

        self.assertEqual(plan["duplicate_group_count"], 2)

    def test_results_keep_the_order_of_the_files_whatever_finishes_first(self) -> None:
        paths = [f"p{i}" for i in range(10)]
        release = threading.Event()

        def hash_one(path: str) -> str:
            if path == "p0":
                self.assertTrue(release.wait(20))  # the head of the line is the last to finish
            elif path == "p9":
                release.set()
            return "digest-" + path

        got = list(self.detector(workers=4)._hash_each(hash_one, paths))
        self.assertEqual(got, [(p, "digest-" + p) for p in paths])

    def test_one_slow_file_does_not_stop_the_others_from_being_hashed(self) -> None:
        paths = [f"p{i}" for i in range(300)]
        finished = []
        enough = threading.Event()

        def hash_one(path: str) -> str:
            if path == "p0":
                self.assertTrue(enough.wait(20))  # blocked until a hundred others are done, or the test fails
            else:
                finished.append(path)
                if len(finished) >= 100:
                    enough.set()
            return "d"

        got = list(self.detector(workers=4)._hash_each(hash_one, paths))
        self.assertEqual([p for p, _ in got], paths)

    def test_an_abandoned_run_does_not_wait_for_the_files_being_hashed(self) -> None:
        started = threading.Barrier(3, timeout=20)
        stopped: List[bool] = []

        def hash_one(path: str) -> str:
            if path == "fast":
                return "d"
            started.wait()  # both slow files are being "read"
            stopped.append(duplicates._worker_state.abandoned.wait(20))  # until the run is abandoned
            return ""

        run = self.detector(workers=3)._hash_each(hash_one, ["fast", "slow1", "slow2"])
        self.assertEqual(next(run), ("fast", "d"))
        started.wait()
        began = time.monotonic()
        run.close()  # what Ctrl-C or an error in the caller's loop amounts to
        self.assertLess(time.monotonic() - began, 10)  # it did not sit out the slow files
        deadline = time.monotonic() + 20
        while len(stopped) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(stopped, [True, True])  # both were told to stop

    def test_a_big_file_is_not_read_to_the_end_once_the_run_is_abandoned(self) -> None:
        big = self.write("big.bin", b"x" * 300000)
        expected = duplicates.compute_full_sha256(str(big))
        self.assertEqual(len(expected), 64)
        abandoned = threading.Event()
        abandoned.set()
        duplicates._worker_state.abandoned = abandoned
        try:
            self.assertEqual(duplicates.compute_full_sha256(str(big)), "")
        finally:
            del duplicates._worker_state.abandoned
        self.assertEqual(duplicates.compute_full_sha256(str(big)), expected)

    def test_a_file_that_cannot_be_read_is_left_out_of_its_group(self) -> None:
        for name in ("a", "b", "bad"):
            self.write(f"{name}.bin", b"same" * 100)
        real = duplicates.compute_partial_hash

        def partial(path: str, *args: Any, **kwargs: Any) -> str:
            return "" if path.endswith("bad.bin") else real(path, *args, **kwargs)  # what an OSError turns into

        with patch.object(duplicates, "compute_partial_hash", partial):
            plan = self.detector().generate_reclamation_plan()

        self.assertEqual(self.reported(plan), ["a.bin", "b.bin"])

    def test_the_number_of_workers_is_at_least_one(self) -> None:
        self.assertEqual(self.detector().workers, duplicates.HASH_WORKERS)
        self.assertEqual(self.detector(workers=0).workers, 1)
        self.assertEqual(self.detector(workers=-4).workers, 1)
        self.assertEqual(self.detector(workers=3).workers, 3)


class TestLearningUnitIsAskedOncePerFolder(_Tree):
    """The learning-unit lookup resolves the path (about a millisecond on Windows): once per folder is enough."""

    def setUp(self) -> None:
        super().setUp()
        clear_unit_cache()
        self.addCleanup(clear_unit_cache)

    def test_copies_in_two_folders_cost_two_lookups(self) -> None:
        for i in range(25):
            payload = f"payload {i} ".encode() * (20 + i)
            self.write(f"left/file{i}.bin", payload)
            self.write(f"right/file{i}.bin", payload)
        asked: List[str] = []
        real = duplicates.is_in_learning_unit

        def counting(path: Any, *args: Any, **kwargs: Any) -> bool:
            asked.append(os.path.dirname(str(path)))
            return real(path, *args, **kwargs)

        with patch.object(duplicates, "is_in_learning_unit", counting):
            plan = self.detector().generate_reclamation_plan()

        self.assertEqual(plan["duplicate_group_count"], 25)
        self.assertEqual(sorted(asked), sorted([str(self.root / "left"), str(self.root / "right")]))

    def test_every_file_of_a_unit_is_still_kept_first_and_never_reclaimable(self) -> None:
        self.write("deck/deck.json", b'{"type": "deck"}')
        for name, folder in (("a.png", "deck"), ("b.png", "deck/images"), ("c.png", "deck/images")):
            payload = name.encode() * 400
            self.write(f"{folder}/{name}", payload)
            self.write(f"copies/{name}", payload)

        plan = self.detector().generate_reclamation_plan()

        self.assertEqual(plan["duplicate_group_count"], 3)
        for group in plan["duplicate_groups"]:
            first, second = (self.rel(f) for f in group["files"])
            self.assertTrue(first.startswith("deck/"), first)  # the unit's copy is the one that is kept
            self.assertTrue(second.startswith("copies/"), second)
            self.assertEqual(group["reclaimable_bytes"], group["size"])  # only the outside copy is offered


class _FakeClock:
    """A clock that moves one second per reading, so a run's "time" depends on the work done, not on the machine."""

    def __init__(self, step: float = 1.0) -> None:
        self.now = 0.0
        self.step = step

    def monotonic(self) -> float:
        self.now += self.step
        return self.now


class TestProgressIsReported(_Tree):
    """A long scan says how far it got (stderr, every ~2 s), and the report on stdout stays clean."""

    def seed(self, groups: int = 6) -> None:
        for i in range(groups):
            payload = f"group {i} ".encode() * (30 + 10 * i)
            for copy in range(3):
                self.write(f"copy{copy}/file{i}.bin", payload)
        self.write("copy0/unique.bin", b"nobody else has my size")

    def test_progress_is_reported_through_the_callback_with_the_phases_in_order(self) -> None:
        self.seed()
        seen: List[Dict[str, Any]] = []
        self.detector(progress=seen.append, progress_interval=0).generate_reclamation_plan()

        phases = [info["phase"] for info in seen]
        self.assertEqual(sorted(set(phases), key=phases.index), ["scan", "partial", "full"])
        self.assertEqual(phases, sorted(phases, key=["scan", "partial", "full"].index))  # never back to an earlier one
        self.assertTrue(all(isinstance(info[key], int) for info in seen for key in (
            "folders_scanned", "files_scanned", "size_candidate_files", "files_partially_hashed", "files_fully_hashed",
            "hash_total", "skipped_dependency_dirs", "skipped_protected_dirs",
        )))
        partial = [info for info in seen if info["phase"] == "partial"]
        self.assertEqual({info["hash_total"] for info in partial}, {18})  # the files that share a size
        done = [info["files_partially_hashed"] for info in partial]
        self.assertEqual(done, sorted(done))

    def test_reports_come_at_most_once_per_interval(self) -> None:
        self.seed(groups=10)
        clock = _FakeClock()
        reported_at: List[float] = []
        det = self.detector(progress=lambda info: reported_at.append(clock.now), progress_interval=2.0)
        with patch.object(duplicates, "time", clock):
            det.generate_reclamation_plan()

        self.assertGreater(len(reported_at), 3)
        gaps = [later - earlier for earlier, later in zip(reported_at, reported_at[1:])]
        self.assertTrue(all(gap >= 2.0 for gap in gaps), gaps)
        self.assertGreaterEqual(reported_at[0], 2.0)  # the first report comes one interval after the start

    def test_a_quick_run_reports_nothing(self) -> None:
        self.seed()
        seen: List[Dict[str, Any]] = []
        with patch.object(duplicates, "time", _FakeClock(step=0.0)):  # no time passes
            self.detector(progress=seen.append).generate_reclamation_plan()
        self.assertEqual(seen, [])

    def test_the_detector_prints_nothing_by_itself(self) -> None:
        self.seed()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.detector(progress_interval=0).generate_reclamation_plan()
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_the_default_interval_is_about_two_seconds(self) -> None:
        self.assertEqual(duplicates.PROGRESS_INTERVAL_SECONDS, 2.0)
        self.assertEqual(self.detector().progress_interval, 2.0)

    def test_the_progress_clock_starts_over_with_each_run(self) -> None:
        self.seed()
        clock = _FakeClock()
        reported: List[float] = []
        det = self.detector(progress=lambda info: reported.append(clock.now), progress_interval=2.0)
        with patch.object(duplicates, "time", clock):
            det.generate_reclamation_plan()
            first_run = len(reported)
            clock.now += 1000.0  # a long pause, then the same detector runs again
            second_start = clock.now
            det.generate_reclamation_plan()
        second_run = reported[first_run:]
        self.assertTrue(second_run)
        # a report one interval into the run, not an instant one because the old schedule was long overdue
        self.assertGreaterEqual(second_run[0] - second_start, 3.0)

    def test_json_mode_keeps_stdout_clean_and_sends_progress_to_stderr(self) -> None:
        self.seed()
        with patch.object(duplicates, "PROGRESS_INTERVAL_SECONDS", 0):
            code, out, err = self.run_dup("--json")
        self.assertEqual(code, 0)
        plan = json.loads(out)  # nothing but the plan on stdout
        self.assertEqual(plan["duplicate_group_count"], 6)
        lines = err.strip().splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("dup: ") for line in lines), lines)
        self.assertTrue(any("scanning:" in line for line in lines))
        self.assertTrue(any("comparing the start and end" in line for line in lines))
        self.assertTrue(any("hashing the files that still match" in line for line in lines))

    def test_text_mode_sends_progress_to_stderr_too(self) -> None:
        self.seed()
        with patch.object(duplicates, "PROGRESS_INTERVAL_SECONDS", 0):
            code, out, err = self.run_dup()
        self.assertEqual(code, 0)
        self.assertIn("Found 6 duplicate groups (18 files)", out)
        self.assertNotIn("dup: ", out)
        self.assertIn("dup: scanning:", err)

    def test_a_quick_command_prints_nothing_on_stderr(self) -> None:
        self.seed()
        with patch.object(duplicates, "time", _FakeClock(step=0.0)):  # no time passes
            _, _, err = self.run_dup("--json")
        self.assertEqual(err, "")

    def test_text_mode_says_what_was_scanned_and_what_was_not(self) -> None:
        self.seed()
        self.write("app/node_modules/pkg/data.bin")
        self.write("other/node_modules/pkg/data.bin")
        code, out, _ = self.run_dup()
        self.assertEqual(code, 0)
        summary = next(line for line in out.splitlines() if line.startswith("Scanned "))
        self.assertIn("2 dependency/cache folders", summary)
        self.assertIn("0 folders used by Windows or installed apps", summary)

    def test_the_plan_says_how_long_the_run_took(self) -> None:
        self.seed()
        with patch.object(duplicates, "time", _FakeClock(step=0.5)):
            plan = self.detector().generate_reclamation_plan()
        self.assertGreater(plan["elapsed_seconds"], 0)


class TestExistingScanOptionsKeepWorking(_Tree):
    def test_exclude_dirs_replaces_the_default_exclusions_as_before(self) -> None:
        self.write("skipme/a.bin")
        self.write("keep/b.bin")
        self.write("$RECYCLE.BIN/c.bin")  # excluded by default, but not when the caller passes its own set
        plan = self.detector(exclude_dirs={"SkipMe"}).generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["$RECYCLE.BIN/c.bin", "keep/b.bin"])

    def test_the_default_exclusions_still_apply(self) -> None:
        self.write("$RECYCLE.BIN/a.bin")
        self.write("keep/b.bin")
        self.write("keep2/c.bin")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual(self.reported(plan), ["keep/b.bin", "keep2/c.bin"])

    def test_folders_and_files_scanned_are_reported(self) -> None:
        self.write("a/one.bin", b"1")
        self.write("a/b/two.bin", b"22")
        self.write("c/three.bin", b"333")
        plan = self.detector().generate_reclamation_plan()
        self.assertEqual((plan["folders_scanned"], plan["files_scanned"]), (4, 3))  # the root counts as a folder


if __name__ == "__main__":
    unittest.main()
