"""tests/test_in_use.py - find what is open below a folder that Windows refuses to rename.

Regression: `organize --apply` could not move `E:\\project` (17 repositories) and printed `[WinError 5] Access is
denied`, and the hint named no holder. The holder was a handle on one nested folder,
`E:\\project\\datathon-2026-round-1\\Nop_bai\\Images`: NTFS refuses to rename a directory while anything beneath it is
open, even when that entry could be renamed on its own, so every ancestor was blocked. `find_in_use` finds that entry by
trying to open every folder and file exclusively (the open fails with ERROR_SHARING_VIOLATION when another handle is
open) and reports the deepest entries that fail.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional
from unittest.mock import patch

from smart_drive.core import in_use
from smart_drive.core.in_use import ERROR_SHARING_VIOLATION, MAX_REPORTED, _extended, deepest, find_in_use
from tests.open_handles import EXPLORER_WATCH_ACCESS, OpenHandle

ON_WINDOWS = sys.platform == "win32"
WALK_THREAD = "smart-drive-in-use"  # the name find_in_use gives the thread of its walk


def _force_remove(func: Any, path: str, _exc: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


@contextlib.contextmanager
def thread_waits_limited_to(limit: float) -> Iterator[List[Optional[float]]]:
    """Waits for a thread the way Python 3.9 does on Windows, whatever Python this is.

    There, joining a thread for more than ``threading.TIMEOUT_MAX`` seconds (4_294_967, about 49 days) raises
    OverflowError ("timeout value is too large"), while newer versions take far longer waits. ``limit`` stands in for
    TIMEOUT_MAX, so that a budget of a few seconds already lies above it. Yields the waits asked for by the thread of
    a walk, in order.
    """
    real_join = threading.Thread.join
    asked: List[Optional[float]] = []

    def join(self: threading.Thread, timeout: Optional[float] = None) -> None:
        if timeout is not None and timeout > threading.TIMEOUT_MAX:
            raise OverflowError("timeout value is too large")
        if self.name == WALK_THREAD:
            asked.append(timeout)
        real_join(self, timeout)

    with patch.object(threading, "TIMEOUT_MAX", limit), patch.object(threading.Thread, "join", join):
        yield asked


# How a file system may hand out the entries of a folder: NTFS lists them by name, APFS and ext4 by a hash of the name,
# others by age. Nothing a walk finds may depend on which it is.
LISTING_ORDERS: Dict[str, Callable[[List[Any]], List[Any]]] = {
    "by name": lambda entries: sorted(entries, key=lambda entry: entry.name),
    "by name, backwards": lambda entries: sorted(entries, key=lambda entry: entry.name, reverse=True),
    "files before folders": lambda entries: sorted(entries, key=lambda entry: entry.is_dir()),
    "folders before files": lambda entries: sorted(entries, key=lambda entry: not entry.is_dir()),
}


class Listing:
    """The result of os.scandir with the entries in a chosen order: an iterator that is also a context manager."""

    def __init__(self, entries: Iterable[Any]) -> None:
        self._entries = iter(entries)

    def __iter__(self) -> "Listing":
        return self

    def __next__(self) -> Any:
        return next(self._entries)

    def __enter__(self) -> "Listing":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        pass


@contextlib.contextmanager
def listed_in_order(arrange: Callable[[List[Any]], List[Any]]) -> Iterator[None]:
    """Makes os.scandir hand out the entries of every folder in the order that ``arrange`` gives."""
    real_scandir = os.scandir

    def scandir(path: Any = ".") -> Listing:
        with real_scandir(path) as scanner:
            return Listing(arrange(list(scanner)))

    with patch.object(os, "scandir", scandir):
        yield


class FakeDisk:
    """An opener that answers as Windows would: the held paths are open elsewhere, everything else opens fine."""

    def __init__(self, held: Iterable[str] = (), other_errors: Optional[Dict[str, int]] = None) -> None:
        self.held = {os.path.normcase(str(path)) for path in held}
        self.other = {os.path.normcase(str(path)): code for path, code in (other_errors or {}).items()}
        self.tried: List[str] = []

    def __call__(self, path: str) -> int:
        self.tried.append(path)
        key = os.path.normcase(path)
        if key in self.held:
            return ERROR_SHARING_VIOLATION
        return self.other.get(key, 0)

    def tried_relative(self, base: str) -> List[str]:
        return [os.path.relpath(path, base) for path in self.tried]


class TreeCase(unittest.TestCase):
    """A temp folder to build trees in."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_inuse_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, onerror=_force_remove)

    def make(self, *relative_files: str) -> None:
        """Creates the files (and so the folders) named by slash-separated paths below the base."""
        for relative in relative_files:
            path = self.base.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")

    def p(self, relative: str) -> str:
        return str(self.base.joinpath(*relative.split("/")))


class TestDeepest(unittest.TestCase):
    R = os.path.abspath(os.sep + "root")

    def at(self, *parts: str) -> str:
        return os.path.join(self.R, *parts)

    def test_an_entry_with_an_open_entry_below_it_is_left_out(self) -> None:
        found = deepest([self.R, self.at("a"), self.at("a", "b"), self.at("c")])
        self.assertEqual(found, [self.at("a", "b"), self.at("c")])

    def test_the_order_given_is_kept_and_at_most_three_are_returned(self) -> None:
        paths = [self.at(f"f{n}") for n in range(5)]
        self.assertEqual(MAX_REPORTED, 3)
        self.assertEqual(deepest(paths), paths[:3])
        self.assertEqual(deepest(paths, limit=5), paths)
        self.assertEqual(deepest(paths, limit=0), [])

    def test_a_name_that_only_starts_like_another_is_not_below_it(self) -> None:
        self.assertEqual(deepest([self.at("a"), self.at("ab")]), [self.at("a"), self.at("ab")])

    def test_a_path_given_twice_counts_once_and_nothing_gives_nothing(self) -> None:
        self.assertEqual(deepest([self.at("a"), self.at("a")]), [self.at("a")])
        self.assertEqual(deepest([]), [])

    @unittest.skipUnless(os.name == "nt", "paths are case-insensitive on Windows only")
    def test_paths_are_compared_without_regard_to_case_on_windows(self) -> None:
        self.assertEqual(deepest([self.at("A"), self.at("a", "B")]), [self.at("a", "B")])


class TestExtendedPaths(unittest.TestCase):
    def test_short_paths_are_left_alone(self) -> None:
        self.assertEqual(_extended("C:\\a\\b"), "C:\\a\\b")

    def test_long_paths_get_the_extended_length_prefix(self) -> None:
        long_path = "C:\\" + "\\".join(["x" * 50] * 6)
        self.assertEqual(_extended(long_path), "\\\\?\\" + long_path)

    def test_a_network_path_uses_the_unc_form(self) -> None:
        long_path = "\\\\server\\share\\" + "\\".join(["x" * 50] * 6)
        self.assertEqual(_extended(long_path), "\\\\?\\UNC\\server\\share\\" + "\\".join(["x" * 50] * 6))

    def test_a_path_that_has_the_prefix_does_not_get_it_twice(self) -> None:
        prefixed = "\\\\?\\C:\\" + "\\".join(["x" * 50] * 6)
        self.assertEqual(_extended(prefixed), prefixed)


class TestWhichEntriesAreReported(TreeCase):
    def test_the_nested_folder_is_reported_and_not_the_folders_above_it(self) -> None:
        self.make("top/datathon/Nop_bai/Images/pic.png", "top/other/x.txt")
        images = self.p("top/datathon/Nop_bai/Images")
        # NTFS refuses to rename a folder while anything below it is open, so a real holder shows up as one entry
        disk = FakeDisk(held=[images])
        self.assertEqual(find_in_use(self.p("top"), opener=disk), [images])

    def test_an_open_folder_with_an_open_file_below_it_gives_the_file(self) -> None:
        self.make("top/a/b/data.db")
        disk = FakeDisk(held=[self.p("top/a"), self.p("top/a/b"), self.p("top/a/b/data.db")])
        self.assertEqual(find_in_use(self.p("top"), opener=disk), [self.p("top/a/b/data.db")])

    def test_at_most_three_paths_are_reported(self) -> None:
        names = [f"f{n}.txt" for n in range(5)]
        self.make(*[f"top/{name}" for name in names])
        disk = FakeDisk(held=[self.p(f"top/{name}") for name in names])
        found = find_in_use(self.p("top"), opener=disk)
        self.assertEqual(len(found), 3)
        self.assertTrue(set(found) <= {self.p(f"top/{name}") for name in names}, found)
        self.assertEqual(len(find_in_use(self.p("top"), opener=disk, limit=5)), 5)

    def test_only_a_sharing_violation_means_in_use(self) -> None:
        self.make("top/a/x.txt", "top/b/y.txt")
        disk = FakeDisk(
            other_errors={
                self.p("top"): -1,
                self.p("top/a"): 5,  # access denied
                self.p("top/a/x.txt"): 2,  # file not found
                self.p("top/b"): 3,  # path not found
                self.p("top/b/y.txt"): 123,  # invalid name
            }
        )
        self.assertEqual(find_in_use(self.p("top"), opener=disk), [])
        self.assertEqual(len(disk.tried), 5, "every entry was tried")

    def test_the_folder_itself_is_reported_when_nothing_below_it_is_open(self) -> None:
        self.make("top/a/x.txt")
        disk = FakeDisk(held=[self.p("top")])
        self.assertEqual(find_in_use(self.p("top"), opener=disk), [self.p("top")])

    def test_a_file_is_tried_as_it_is(self) -> None:
        self.make("loose.pdf")
        disk = FakeDisk(held=[self.p("loose.pdf")])
        self.assertEqual(find_in_use(self.p("loose.pdf"), opener=disk), [self.p("loose.pdf")])
        self.assertEqual(disk.tried, [self.p("loose.pdf")])

    def test_a_folder_that_is_gone_gives_nothing_and_tries_nothing(self) -> None:
        disk = FakeDisk()
        self.assertEqual(find_in_use(self.p("missing"), opener=disk), [])
        self.assertEqual(disk.tried, [])

    def test_an_entry_that_cannot_be_tried_does_not_stop_the_walk(self) -> None:
        self.make("top/a.txt", "top/b.txt", "top/c.txt")
        held = self.p("top/c.txt")

        def opener(path: str) -> int:
            if path.endswith("a.txt"):
                raise OSError("boom")
            return ERROR_SHARING_VIOLATION if path == held else 0

        self.assertEqual(find_in_use(self.p("top"), opener=opener), [held])


class TestWhatIsWalked(TreeCase):
    def test_dependency_repository_and_environment_trees_are_tried_but_not_entered(self) -> None:
        self.make(
            "top/app/node_modules/pkg/index.js",
            "top/app/.git/objects/pack/p.idx",
            "top/app/.venv/Lib/site-packages/x/y.py",
            "top/app/venv/Scripts/python.exe",
            "top/app/site-packages/z/w.py",
            "top/app/myenv/pyvenv.cfg",  # a virtual environment under any name has this file
            "top/app/myenv/Scripts/python.exe",
            "top/app/src/main.py",
        )
        disk = FakeDisk()
        find_in_use(self.p("top"), opener=disk)
        tried = {os.path.normcase(path) for path in disk.tried_relative(self.p("top"))}

        def seen(relative: str) -> bool:
            return os.path.normcase(os.path.normpath(relative)) in tried

        for entered in ("app", "app/node_modules", "app/.git", "app/.venv", "app/venv", "app/site-packages",
                        "app/myenv", "app/src", "app/src/main.py"):
            self.assertTrue(seen(entered), f"{entered} is tried")
        for skipped in ("app/node_modules/pkg", "app/node_modules/pkg/index.js", "app/.git/objects",
                        "app/.venv/Lib", "app/venv/Scripts", "app/site-packages/z", "app/myenv/Scripts",
                        "app/myenv/pyvenv.cfg"):
            self.assertFalse(seen(skipped), f"{skipped} is not entered")

    def test_the_folder_that_is_moved_is_entered_even_when_it_is_named_like_a_dependency_tree(self) -> None:
        self.make("node_modules/pkg/index.js", "env/pyvenv.cfg", "env/Scripts/python.exe")
        for name, expected in (("node_modules", "pkg"), ("env", "Scripts")):
            with self.subTest(root=name):
                disk = FakeDisk()
                find_in_use(self.p(name), opener=disk)
                self.assertIn(self.p(f"{name}/{expected}"), disk.tried)

    def test_the_walk_stops_at_the_depth_limit(self) -> None:
        self.make("top/" + "/".join(f"l{n}" for n in range(1, 11)) + "/leaf.txt")
        top = self.p("top")
        disk = FakeDisk()
        find_in_use(top, opener=disk)
        depths = [len(os.path.relpath(path, top).split(os.sep)) for path in disk.tried if path != top]
        self.assertEqual(max(depths), 8, "folders up to 8 levels below the folder are tried")
        shallow = FakeDisk()
        find_in_use(top, opener=shallow, max_depth=2)
        self.assertEqual(max(len(os.path.relpath(p, top).split(os.sep)) for p in shallow.tried if p != top), 2)

    def test_the_walk_stops_at_the_entry_limit(self) -> None:
        self.make(*[f"top/f{n:02d}.txt" for n in range(30)])
        disk = FakeDisk()
        find_in_use(self.p("top"), opener=disk, max_entries=10)
        self.assertEqual(len(disk.tried), 10)
        everything = FakeDisk()
        find_in_use(self.p("top"), opener=everything)
        self.assertEqual(len(everything.tried), 31, "the folder and its 30 files")

    def test_the_walk_stops_when_the_time_is_up(self) -> None:
        self.make(*[f"top/f{n:02d}.txt" for n in range(30)])
        ticks = itertools.count()

        def clock() -> float:  # every look at the clock takes a second
            return float(next(ticks))

        disk = FakeDisk()
        find_in_use(self.p("top"), opener=disk, budget=6.5, clock=clock)
        self.assertTrue(0 < len(disk.tried) < 31, len(disk.tried))
        no_limit = FakeDisk()
        find_in_use(self.p("top"), opener=no_limit, budget=1e9)
        self.assertEqual(len(no_limit.tried), 31)

    def test_folders_are_tried_before_files_so_a_short_budget_goes_to_the_likeliest_holders(self) -> None:
        # No folder holds more entries than the budget. A folder is read in the order its file system lists it and only
        # its first max_entries entries are read, so more would leave what is found to that order (NTFS lists by name,
        # APFS by a hash) and not to the walk. The walk is run with the entries listed in four orders to show it.
        self.make(*[f"top/f{n:02d}.txt" for n in range(10)], "top/a/b/c/d/Images/pic.png")
        held = self.p("top/a/b/c/d/Images")
        for order, arrange in LISTING_ORDERS.items():
            with self.subTest(listing=order), listed_in_order(arrange):
                disk = FakeDisk(held=[held])
                found = find_in_use(self.p("top"), opener=disk, max_entries=12)
                reached = "the folder five levels down is reached although 10 files lie above it"
                self.assertEqual(found, [held], f"{reached}; tried: {disk.tried}")
                kinds = ["dir" if os.path.isdir(path) else "file" for path in disk.tried]
                self.assertEqual(kinds, sorted(kinds), "every folder was tried before the first file")

    def test_links_are_neither_followed_nor_tried(self) -> None:
        self.make("outside/secret.txt", "top/real/x.txt")
        link = self.p("top/link")
        try:
            os.symlink(self.p("outside"), link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("this account cannot create symlinks here")
        disk = FakeDisk()
        find_in_use(self.p("top"), opener=disk)
        self.assertNotIn(link, disk.tried)
        self.assertFalse([p for p in disk.tried if p.startswith(self.p("outside"))], disk.tried)
        self.assertIn(self.p("top/real/x.txt"), disk.tried)

    @unittest.skipUnless(ON_WINDOWS, "junctions are a Windows thing")
    def test_junctions_are_neither_followed_nor_tried(self) -> None:
        # Unlike a symlink, a junction needs no privilege; to Python's is_dir() it is a plain folder
        self.make("outside/secret.txt", "top/real/x.txt")
        junction = self.p("top/junction")
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", junction, self.p("outside")], capture_output=True, check=False
        )
        if made.returncode != 0:
            self.skipTest("no junction could be made here")
        self.addCleanup(os.rmdir, junction)  # the link only, never what it points to
        disk = FakeDisk()
        find_in_use(self.p("top"), opener=disk)
        self.assertNotIn(junction, disk.tried)
        self.assertFalse([p for p in disk.tried if p.startswith(self.p("outside"))], disk.tried)
        self.assertIn(self.p("top/real/x.txt"), disk.tried)


class TestTimeBox(TreeCase):
    def test_a_single_open_that_hangs_cannot_hold_the_caller_up(self) -> None:
        self.make("top/a/hang.txt")
        release = threading.Event()
        self.addCleanup(release.set)  # lets the abandoned walk end
        held_folder = self.p("top/a")

        def opener(path: str) -> int:
            if path.endswith("hang.txt"):
                release.wait(30)
                return 0
            return ERROR_SHARING_VIOLATION if path == held_folder else 0

        started = time.monotonic()
        with patch.object(in_use, "_STUCK_GRACE", 0.2):
            found = find_in_use(self.p("top"), opener=opener, budget=0.2)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(found, [held_folder], "what was found before the hang is kept (folders go first)")

    def test_a_budget_longer_than_a_thread_can_be_waited_for_is_cut_down_to_what_it_can_be(self) -> None:
        # Python 3.9 on Windows raises OverflowError for a join of more than threading.TIMEOUT_MAX seconds, and
        # find_in_use took that for a failure: for a budget of 1e9 it gave nothing before the walk had got anywhere
        self.make(*[f"top/f{n:02d}.txt" for n in range(30)])
        held = self.p("top/f07.txt")
        for budget in (1e9, float("inf")):
            with self.subTest(budget=budget):
                disk = FakeDisk(held=[held])
                with thread_waits_limited_to(10.0) as waited:
                    found = find_in_use(self.p("top"), opener=disk, budget=budget)
                self.assertEqual(found, [held])
                self.assertEqual(len(disk.tried), 31, "the folder and its 30 files")
                self.assertEqual(waited, [10.0], "the longest wait that a thread can be given")

    def test_a_budget_that_a_thread_can_be_waited_for_is_waited_for_as_it_is(self) -> None:
        self.make("top/a.txt")
        with thread_waits_limited_to(10.0) as waited:
            find_in_use(self.p("top"), opener=FakeDisk(), budget=2.0)
        self.assertEqual(waited, [2.0 + in_use._STUCK_GRACE])

    def test_it_is_silent_off_windows(self) -> None:
        self.make("top/a.txt")
        with patch.object(sys, "platform", "linux"), patch.object(
            in_use, "_win32_opener", side_effect=AssertionError("Windows only")
        ):
            self.assertEqual(find_in_use(self.p("top")), [])

    def test_an_api_that_cannot_be_reached_gives_nothing_and_raises_nothing(self) -> None:
        self.make("top/a.txt")
        for failure in (OSError("no kernel32"), AttributeError("no WinDLL"), RuntimeError("x")):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(sys, "platform", "win32"), patch.object(in_use, "_win32_opener", side_effect=failure):
                    self.assertEqual(find_in_use(self.p("top")), [])

    def test_a_walk_that_cannot_start_gives_nothing(self) -> None:
        self.make("top/a.txt")
        with patch.object(in_use.threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            self.assertEqual(find_in_use(self.p("top"), opener=FakeDisk(held=[self.p("top")])), [])


@unittest.skipUnless(ON_WINDOWS, "an exclusive open and the NTFS rename rule are Windows things")
class TestWithRealHandles(TreeCase):
    """The same folder tree as the user's, with a real handle held open the way Explorer or a service does it."""

    def setUp(self) -> None:
        super().setUp()
        self.top = self.base / "project"
        self.images = self.top / "datathon-2026-round-1" / "Nop_bai" / "Images"
        self.images.mkdir(parents=True)
        (self.images / "pic.png").write_bytes(b"x")
        (self.top / "repo").mkdir()
        (self.top / "repo" / "main.py").write_text("print()\n", encoding="utf-8")

    def probe(self, expected: List[str], attempts: int = 5) -> List[str]:
        """find_in_use, asked again a few times: a virus scanner may hold a fresh file for a moment."""
        found: List[str] = []
        for _ in range(attempts):
            found = find_in_use(str(self.top))
            if found == expected:
                break
            time.sleep(0.2)
        return found

    def rename_top(self, attempts: int = 5) -> None:
        last: Optional[OSError] = None
        for _ in range(attempts):
            try:
                os.rename(self.top, self.base / "moved")
                return
            except PermissionError as err:  # a scanner may still have a file open for a moment
                last = err
                time.sleep(0.2)
        raise AssertionError(f"the folder could not be renamed after the handle was closed: {last}")

    def test_a_handle_on_a_nested_folder_is_found_and_it_is_what_blocks_the_rename(self) -> None:
        with OpenHandle(str(self.images)):
            self.assertEqual(self.probe([str(self.images)]), [str(self.images)])
            # The NTFS rule: nothing below the folder may be open, although the nested folder itself could be renamed
            with self.assertRaises(PermissionError) as refused:
                os.rename(self.top, self.base / "moved")
            self.assertEqual(refused.exception.winerror, 5)
            self.assertTrue(self.images.is_dir())
        self.assertEqual(self.probe([]), [])
        self.rename_top()
        self.assertTrue((self.base / "moved" / "datathon-2026-round-1" / "Nop_bai" / "Images").is_dir())

    def test_explorers_watch_handle_is_found(self) -> None:
        # Explorer keeps a handle with access mask 0x00120189 on every Quick Access folder
        with OpenHandle(str(self.images), access=EXPLORER_WATCH_ACCESS):
            self.assertEqual(self.probe([str(self.images)]), [str(self.images)])
            with self.assertRaises(PermissionError):
                os.rename(self.top, self.base / "moved")
        self.rename_top()

    def test_a_file_that_a_program_has_open_is_found(self) -> None:
        main = self.top / "repo" / "main.py"
        with open(main, "r", encoding="utf-8"):
            self.assertEqual(self.probe([str(main)]), [str(main)])
        self.assertEqual(self.probe([]), [])

    def test_the_deepest_entries_win_over_the_folders_above_them(self) -> None:
        nop_bai = self.images.parent
        picture = self.images / "pic.png"
        with OpenHandle(str(nop_bai)), OpenHandle(str(self.images)), open(picture, "rb"):
            self.assertEqual(self.probe([str(picture)]), [str(picture)])

    def test_two_separate_holders_are_both_reported(self) -> None:
        main = self.top / "repo" / "main.py"
        with OpenHandle(str(self.images)), open(main, "r", encoding="utf-8"):
            found = self.probe([str(self.images), str(main)])
            self.assertEqual(sorted(found), sorted([str(self.images), str(main)]))

    def test_a_path_past_260_characters_is_walked_too(self) -> None:
        deep = self.top
        for number in range(6):
            deep = deep / f"long_segment_{'x' * 40}_{number}"
        self.assertGreater(len(str(deep)), 270)
        self.addCleanup(shutil.rmtree, "\\\\?\\" + str(self.base), True)  # runs before the plain cleanup
        os.makedirs("\\\\?\\" + str(deep))
        with OpenHandle("\\\\?\\" + str(deep)):
            self.assertEqual(self.probe([str(deep)]), [str(deep)])


if __name__ == "__main__":
    unittest.main()
