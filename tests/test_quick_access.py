"""tests/test_quick_access.py - the folders that Explorer lists in Quick Access.

Regression: `E:\\project` could not be moved because `E:\\project\\datathon-2026-round-1\\Nop_bai\\Images` was pinned in
Explorer's Quick Access. Explorer keeps a change-notification handle on every Quick Access folder and opens them again
at each logon; the handle kept Windows from renaming every folder above. Unpinning did not release it and a restart of
Windows did not either (the folder was still pinned), restarting Windows Explorer did. The hint now says so when a
folder inside the one that cannot be moved is listed in Quick Access; this module reads that list with a short
PowerShell call (best effort, never raising) and picks the entries inside a folder.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
from unittest.mock import patch

from smart_drive.core import quick_access
from smart_drive.core.quick_access import _powershell, _run_shell, _script, pinned_inside, quick_access_folders

ON_WINDOWS = sys.platform == "win32"
QUICK_ACCESS_CLSID = "shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}"


def _force_remove(func: Any, path: str, _exc: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_qa_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, onerror=_force_remove)

    def p(self, *parts: str) -> str:
        return str(self.base.joinpath(*parts))


class TestReadingTheList(TmpCase):
    def answering(self, output: Optional[str]) -> Any:
        """A stand-in for the PowerShell call that records how it was called and answers ``output``."""
        calls: List[Tuple[List[str], float]] = []

        def run(command: Sequence[str], timeout: float) -> Optional[str]:
            calls.append((list(command), timeout))
            return output

        run.calls = calls  # type: ignore[attr-defined]
        return run

    def test_every_listed_folder_comes_back_once_in_the_order_listed(self) -> None:
        pinned = [self.p("Images"), self.p("work", "Nop_bai")]
        lines = [
            pinned[0],
            "",  # an item that has no path
            "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",  # an item that is no folder of a drive
            f"  {pinned[1]}  ",
            pinned[0] + os.sep,  # the same folder with a trailing separator
        ]
        if os.name == "nt":
            lines.append(pinned[0].upper())  # Windows paths do not care about case
        run = self.answering("\r\n".join(lines) + "\r\n")
        self.assertEqual(quick_access_folders(run=run), [os.path.normpath(path) for path in pinned])

    def test_nothing_or_a_failed_query_gives_nothing(self) -> None:
        for answer in (None, "", "\r\n\r\n", "::{GUID}\r\n"):
            with self.subTest(answer=answer):
                self.assertEqual(quick_access_folders(run=self.answering(answer)), [])

    def test_a_query_that_raises_gives_nothing_and_raises_nothing(self) -> None:
        for failure in (OSError("no powershell"), subprocess.TimeoutExpired("powershell", 5), RuntimeError("x")):
            with self.subTest(failure=type(failure).__name__):
                def run(_command: Sequence[str], _timeout: float, failure: Exception = failure) -> Optional[str]:
                    raise failure

                self.assertEqual(quick_access_folders(run=run), [])

    def test_the_query_is_one_short_powershell_call_for_the_quick_access_view(self) -> None:
        run = self.answering("")
        quick_access_folders(run=run)
        (command, timeout), = run.calls
        self.assertTrue(command[0].lower().endswith("powershell.exe"), command[0])
        self.assertEqual(command[1:4], ["-NoProfile", "-NonInteractive", "-Command"])
        self.assertEqual(timeout, 5.0, "the PowerShell call is given five seconds")
        script = command[4]
        self.assertIn(f"(New-Object -ComObject Shell.Application).Namespace('{QUICK_ACCESS_CLSID}').Items()", script)
        self.assertIn("% { $_.Path }", script)
        self.assertIn("UTF8Encoding $false", script, "paths with accents must survive the pipe")
        self.assertIn("$ErrorActionPreference='Stop'", script, "a query that fails must exit with an error")

    def test_a_quote_in_a_name_cannot_break_out_of_the_script(self) -> None:
        self.assertIn("Namespace('C:\\it''s here')", _script("C:\\it's here"))

    def test_the_timeout_is_the_callers(self) -> None:
        run = self.answering("")
        quick_access_folders(run=run, timeout=1.5)
        self.assertEqual(run.calls[0][1], 1.5)

    def test_it_is_silent_off_windows(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.object(
            quick_access, "_run_shell", side_effect=AssertionError("Windows only")
        ):
            self.assertEqual(quick_access_folders(), [])

    def test_without_powershell_nothing_is_run(self) -> None:
        with patch.object(sys, "platform", "win32"), patch.object(quick_access, "_powershell", return_value=None), patch.object(
            quick_access, "_run_shell", side_effect=AssertionError("no PowerShell to run")
        ):
            self.assertEqual(quick_access_folders(), [])

    def test_on_windows_the_default_call_goes_to_the_full_path_of_powershell(self) -> None:
        seen: List[Sequence[str]] = []

        def run(command: Sequence[str], _timeout: float) -> Optional[str]:
            seen.append(list(command))
            return self.p("pinned") + "\n"

        with patch.object(sys, "platform", "win32"), patch.object(
            quick_access, "_powershell", return_value="C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
        ), patch.object(quick_access, "_run_shell", run):
            self.assertEqual(quick_access_folders(), [self.p("pinned")])
        self.assertEqual(seen[0][0], "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe")


class TestPowerShellLookup(TmpCase):
    def test_powershell_is_found_by_its_full_path_below_the_system_root(self) -> None:
        exe = self.base / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        with patch.dict(os.environ, {"SystemRoot": str(self.base)}):
            self.assertEqual(_powershell(), str(exe))
        with patch.dict(os.environ, {"SystemRoot": str(self.base / "elsewhere")}):
            self.assertIsNone(_powershell())


class TestRunningThePowerShellCall(unittest.TestCase):
    def finished(self, returncode: int, stdout: bytes) -> "subprocess.CompletedProcess[bytes]":
        return subprocess.CompletedProcess(["powershell"], returncode, stdout=stdout, stderr=b"")

    def test_the_output_is_read_as_utf8_and_a_byte_order_mark_is_dropped(self) -> None:
        answer = "\ufeffE:\\Nộp bài\r\n".encode("utf-8")
        with patch.object(quick_access.subprocess, "run", return_value=self.finished(0, answer)) as run:
            self.assertEqual(_run_shell(["powershell", "-Command", "x"], 5.0), "E:\\Nộp bài\r\n")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["timeout"], 5.0)
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_a_failure_gives_nothing(self) -> None:
        for outcome in (
            self.finished(1, b"E:\\half\\an\\answer\r\n"),
            subprocess.TimeoutExpired("powershell", 5),
            FileNotFoundError("powershell"),
            PermissionError("blocked"),
        ):
            with self.subTest(outcome=repr(outcome)[:40]):
                if isinstance(outcome, BaseException):
                    patched = patch.object(quick_access.subprocess, "run", side_effect=outcome)
                else:
                    patched = patch.object(quick_access.subprocess, "run", return_value=outcome)
                with patched:
                    self.assertIsNone(_run_shell(["powershell"], 5.0))

    @unittest.skipUnless(ON_WINDOWS, "the window flag is a Windows thing")
    def test_no_console_window_flashes_up(self) -> None:
        with patch.object(quick_access.subprocess, "run", return_value=self.finished(0, b"")) as run:
            _run_shell(["powershell"], 5.0)
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)


class TestPinnedInside(TmpCase):
    def test_the_folder_itself_and_everything_below_it_count(self) -> None:
        pins = [self.p("moved", "a", "Images"), self.p("moved"), self.p("elsewhere", "c"), self.p("moved", "b")]
        self.assertEqual(
            pinned_inside(self.p("moved"), pins),
            [self.p("moved", "a", "Images"), self.p("moved"), self.p("moved", "b")],
        )

    def test_a_folder_above_it_or_one_that_only_starts_like_it_does_not(self) -> None:
        pins = [str(self.base), self.p("moved_not"), self.p("moved_not", "d"), self.p("mov")]
        self.assertEqual(pinned_inside(self.p("moved"), pins), [])

    def test_a_trailing_separator_and_dot_segments_do_not_matter(self) -> None:
        pins = [self.p("moved", "a") + os.sep, self.p("moved", ".", "b")]
        self.assertEqual(pinned_inside(self.p("moved") + os.sep, pins), [self.p("moved", "a"), self.p("moved", "b")])

    @unittest.skipUnless(os.name == "nt", "paths are case-insensitive on Windows only")
    def test_case_does_not_matter_on_windows(self) -> None:
        self.assertEqual(pinned_inside(self.p("Moved"), [self.p("MOVED", "A")]), [self.p("MOVED", "A")])

    def test_nothing_in_nothing_out(self) -> None:
        self.assertEqual(pinned_inside(self.p("moved"), []), [])


@unittest.skipUnless(ON_WINDOWS and _powershell() is not None, "needs Windows PowerShell")
class TestWithRealPowerShell(TmpCase):
    """The real command line, on a folder of our own instead of the user's Quick Access."""

    def test_the_call_lists_the_paths_of_a_shell_folder_with_their_accents_and_quotes(self) -> None:
        (self.base / "Nộp bài" / "Ảnh").mkdir(parents=True)
        (self.base / "it's here").mkdir()
        (self.base / "a.txt").write_text("x", encoding="utf-8")
        listed = quick_access_folders(namespace=str(self.base))
        self.assertEqual(
            sorted(listed, key=os.path.normcase),
            sorted([self.p("a.txt"), self.p("it's here"), self.p("Nộp bài")], key=os.path.normcase),
        )

    def test_a_query_that_fails_gives_nothing(self) -> None:
        self.assertEqual(quick_access_folders(namespace=self.p("does", "not", "exist")), [])

    def test_the_real_quick_access_list_is_a_list_of_absolute_paths(self) -> None:
        # Whatever this machine has pinned (nothing, on a build server)
        listed = quick_access_folders()
        self.assertIsInstance(listed, list)
        self.assertTrue(all(os.path.isabs(path) for path in listed), listed)


if __name__ == "__main__":
    unittest.main()
