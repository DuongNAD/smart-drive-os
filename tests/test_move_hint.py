"""tests/test_move_hint.py - a move that Windows refuses says what is probably holding the folder.

Regression: `organize --apply` printed `[WinError 5] Access is denied: 'E:\\project' -> ...` and advised closing "the
editor, terminal or coding agent", although the holder was a git fsmonitor daemon that no editor or terminal owned.
A refused move now lists the usual holders (a program, a terminal whose current directory is inside the folder, an
Explorer window, a git fsmonitor daemon), names the daemons that still run and, where the Windows Restart Manager
can be reached, the processes that hold files of the folder open.

Regression: the next refusal of `E:\\project` named nothing at all, because the holder was a handle on a nested folder,
`E:\\project\\datathon-2026-round-1\\Nop_bai\\Images`, that no process could be named for. The hint now says which
entry is in use (found by trying every entry exclusively, see tests/test_in_use.py), carries it as `in_use` in the JSON
of organize, undo, classify and the MCP tool, and says how to find a holder that cannot be named.
"""

from __future__ import annotations

import contextlib
import errno
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
from typing import Any, List
from unittest.mock import MagicMock, patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core import move_hint
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.classifier import ClassifierEngine
from smart_drive.core.move_hint import (
    RefusedMove,
    explain_refused_move,
    holding_processes,
    inspect_refused_move,
    is_lock_error,
    move_failure_hint,
)
from smart_drive.core.relink import record_moves
from smart_drive.core.system_refs import SystemRefIndex
from smart_drive.mcp.server import SmartDriveMCPServer
from tests.helpers import SmartDriveTestCase
from tests.open_handles import EXPLORER_WATCH_ACCESS, OpenHandle

PLAIN = "close the editor, terminal or coding agent using it, then run organize again"
IMAGES = "E:\\project\\datathon-2026-round-1\\Nop_bai\\Images"  # what the user's real refusal came down to
UNNAMED = (
    "Windows does not say which program has it open: often explorer.exe (an open Explorer window or Quick Access), "
    "else a terminal or editor using it, a system service or an elevated program; to find it, open Resource Monitor "
    'as administrator (resmon) → CPU → Associated Handles → search "Images", or restart Windows, then run organize again'
)


def _force_remove(func: Any, path: str, _exc: Any) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


class WindowsStyleError(OSError):
    """An OSError as Windows raises it: the Win32 code is in `winerror` (settable on every platform here)."""

    def __init__(self, message: str, winerror: int) -> None:
        super().__init__(message)
        self.winerror = winerror


class TestWhichErrorsMeanTheFolderIsInUse(unittest.TestCase):
    def test_access_denied_sharing_violation_busy_and_permission_errors_do(self) -> None:
        in_use = [
            PermissionError(errno.EACCES, "Access is denied"),
            PermissionError("Access is denied"),
            OSError(errno.EACCES, "Permission denied"),
            OSError(errno.EPERM, "Operation not permitted"),
            OSError(errno.EBUSY, "Device or resource busy"),
            WindowsStyleError("[WinError 5] Access is denied", 5),
            WindowsStyleError("[WinError 32] The process cannot access the file", 32),
        ]
        for err in in_use:
            with self.subTest(err=repr(err)):
                self.assertTrue(is_lock_error(err))

    def test_other_failures_do_not(self) -> None:
        other = [
            FileNotFoundError(errno.ENOENT, "No such file"),
            FileExistsError(errno.EEXIST, "File exists"),
            OSError(errno.ENOSPC, "No space left on device"),
            OSError(errno.ENOTEMPTY, "Directory not empty"),
            OSError("some failure with no code"),
            WindowsStyleError("[WinError 183] Cannot create a file when that file already exists", 183),
            ValueError("not an OS error"),
            RuntimeError("boom"),
        ]
        for err in other:
            with self.subTest(err=repr(err)):
                self.assertFalse(is_lock_error(err))


class TestTheHintText(unittest.TestCase):
    def hint(
        self,
        err: BaseException,
        daemons: List[str],
        holders: List[str],
        retry: str = "organize",
        in_use: Any = (),
    ) -> str:
        with patch.object(move_hint, "watching_repositories", return_value=daemons), patch.object(
            move_hint, "holding_processes", return_value=holders
        ), patch.object(move_hint, "find_in_use", return_value=list(in_use)):
            return move_failure_hint(err, "E:\\project", retry)

    def test_a_refused_move_lists_the_usual_holders_and_ends_with_how_to_retry(self) -> None:
        text = self.hint(PermissionError(errno.EACCES, "Access is denied"), [], [])
        self.assertEqual(
            text,
            "a program, a terminal whose current directory is inside it, an Explorer window or a git fsmonitor "
            "daemon is using the folder; " + PLAIN,
        )

    def test_the_retry_command_is_the_callers(self) -> None:
        text = self.hint(PermissionError("denied"), [], [], retry="smart-drive undo --apply")
        self.assertTrue(text.endswith("then run smart-drive undo --apply again"), text)

    def test_daemons_that_still_run_are_named_with_the_command_that_stops_them(self) -> None:
        text = self.hint(WindowsStyleError("[WinError 5] Access is denied", 5), ["E:\\project\\Anima-Engine"], [])
        self.assertIn("a git fsmonitor daemon is still running in E:\\project\\Anima-Engine", text)
        self.assertIn("git -C <repository> fsmonitor--daemon stop", text)
        self.assertTrue(text.endswith(PLAIN), text)

    def test_processes_holding_files_are_named_and_the_list_is_capped(self) -> None:
        holders = [f"App{n}.exe (pid {n})" for n in range(1, 8)]
        text = self.hint(PermissionError("denied"), [], holders)
        self.assertIn("files in it are held open by App1.exe (pid 1), App2.exe (pid 2), App3.exe (pid 3), "
                      "App4.exe (pid 4), App5.exe (pid 5) and 2 more", text)
        self.assertNotIn("App6", text)

    def test_another_failure_gets_only_the_plain_advice(self) -> None:
        for err in (OSError("disk on fire"), FileNotFoundError(errno.ENOENT, "gone"), ValueError("x")):
            with self.subTest(err=repr(err)):
                with patch.object(move_hint, "watching_repositories", side_effect=AssertionError("not a lock")), patch.object(
                    move_hint, "holding_processes", side_effect=AssertionError("not a lock")
                ), patch.object(move_hint, "find_in_use", side_effect=AssertionError("not a lock")):
                    self.assertEqual(move_failure_hint(err, "E:\\project"), PLAIN)

    def test_a_lookup_that_fails_leaves_the_rest_of_the_hint(self) -> None:
        with patch.object(move_hint, "watching_repositories", side_effect=RuntimeError("git broke")), patch.object(
            move_hint, "holding_processes", return_value=[]
        ), patch.object(move_hint, "find_in_use", return_value=[]):
            text = move_failure_hint(PermissionError("denied"), "E:\\project")
        self.assertIn("is using the folder", text)
        self.assertTrue(text.endswith(PLAIN), text)
        with patch.object(move_hint, "watching_repositories", return_value=[]), patch.object(
            move_hint, "holding_processes", side_effect=OSError("no api")
        ), patch.object(move_hint, "find_in_use", return_value=[]):
            text = move_failure_hint(PermissionError("denied"), "E:\\project")
        self.assertTrue(text.endswith(PLAIN), text)

    def test_a_probe_that_fails_leaves_the_rest_of_the_hint(self) -> None:
        with patch.object(move_hint, "watching_repositories", return_value=["E:\\project\\repo"]), patch.object(
            move_hint, "holding_processes", return_value=["Code (pid 9)"]
        ), patch.object(move_hint, "find_in_use", side_effect=RuntimeError("probe broke")):
            refused = inspect_refused_move(PermissionError("denied"), "E:\\project")
        self.assertEqual(refused.in_use, [])
        self.assertIn("is using the folder", refused.hint)
        self.assertIn("a git fsmonitor daemon is still running in E:\\project\\repo", refused.hint)
        self.assertIn("files in it are held open by Code (pid 9)", refused.hint)
        self.assertTrue(refused.hint.endswith(PLAIN), refused.hint)

    def test_the_entry_that_is_in_use_is_named_and_the_hint_says_how_to_find_the_program(self) -> None:
        text = self.hint(WindowsStyleError("[WinError 5] Access is denied", 5), [], [], in_use=[IMAGES])
        self.assertEqual(text, f"in use: {IMAGES}; {UNNAMED}")

    def test_what_was_found_replaces_the_list_of_usual_suspects(self) -> None:
        text = self.hint(PermissionError("denied"), [], [], in_use=[IMAGES])
        self.assertNotIn("a program, a terminal whose current directory", text)
        self.assertTrue(text.startswith("in use: "), text)

    def test_the_tip_names_explorer_and_the_way_to_a_system_holder(self) -> None:
        text = self.hint(PermissionError("denied"), [], [], in_use=[IMAGES])
        for part in ("explorer.exe (an open Explorer window or Quick Access)", "a system service or an elevated program",
                     "Resource Monitor as administrator (resmon)", "CPU → Associated Handles", 'search "Images"',
                     "or restart Windows"):
            self.assertIn(part, text)

    def test_several_open_entries_are_listed_and_the_tip_searches_the_first_name(self) -> None:
        text = self.hint(PermissionError("denied"), [], [], in_use=["E:\\p\\one", "E:\\p\\two\\Deep", "E:\\p\\three.db"])
        self.assertTrue(text.startswith("in use: E:\\p\\one, E:\\p\\two\\Deep, E:\\p\\three.db; "), text)
        self.assertIn("which program has them open", text)
        self.assertIn('search "one"', text)

    def test_the_tip_ends_with_the_command_of_the_caller(self) -> None:
        text = self.hint(PermissionError("denied"), [], [], retry="smart-drive undo --apply", in_use=[IMAGES])
        self.assertTrue(text.endswith("or restart Windows, then run smart-drive undo --apply again"), text)

    def test_a_named_process_takes_the_place_of_the_tip(self) -> None:
        text = self.hint(PermissionError("denied"), [], ["Code (pid 9)"], in_use=["E:\\project\\repo\\main.py"])
        self.assertEqual(
            text,
            "in use: E:\\project\\repo\\main.py; files in it are held open by Code (pid 9); " + PLAIN,
        )
        self.assertNotIn("Resource Monitor", text)

    def test_a_daemon_that_still_runs_explains_what_is_open(self) -> None:
        text = self.hint(PermissionError("denied"), ["E:\\project\\repo"], [], in_use=["E:\\project\\repo"])
        self.assertEqual(
            text,
            "a git fsmonitor daemon is still running in E:\\project\\repo "
            "(stop it with `git -C <repository> fsmonitor--daemon stop`); in use: E:\\project\\repo; " + PLAIN,
        )

    def test_nothing_found_open_leaves_the_text_of_before(self) -> None:
        text = self.hint(PermissionError("denied"), [], [], in_use=[])
        self.assertEqual(
            text,
            "a program, a terminal whose current directory is inside it, an Explorer window or a git fsmonitor "
            "daemon is using the folder; " + PLAIN,
        )
        self.assertNotIn("in use:", text)

    def test_only_files_are_given_to_the_restart_manager(self) -> None:
        folder = Path(tempfile.mkdtemp(prefix="sd_hint_")).resolve()
        self.addCleanup(shutil.rmtree, folder, onerror=_force_remove)
        (folder / "sub").mkdir()
        (folder / "sub" / "data.db").write_text("x", encoding="utf-8")
        asked = MagicMock(return_value=["Code (pid 9)"])
        with patch.object(move_hint, "watching_repositories", return_value=[]), patch.object(
            move_hint, "holding_processes", asked
        ), patch.object(move_hint, "find_in_use", return_value=[str(folder / "sub"), str(folder / "sub" / "data.db")]):
            inspect_refused_move(PermissionError("denied"), str(folder))
        asked.assert_called_once_with(str(folder), also=[str(folder / "sub" / "data.db")])

    def test_the_findings_come_back_with_the_hint(self) -> None:
        with patch.object(move_hint, "watching_repositories", return_value=[]), patch.object(
            move_hint, "holding_processes", return_value=[]
        ), patch.object(move_hint, "find_in_use", return_value=[IMAGES]):
            refused = inspect_refused_move(PermissionError(errno.EACCES, "Access is denied"), "E:\\project", "organize")
        self.assertIsInstance(refused, RefusedMove)
        self.assertEqual(refused.in_use, [IMAGES])
        self.assertTrue(refused.lock)
        self.assertEqual(refused.hint, f"in use: {IMAGES}; {UNNAMED}")
        self.assertEqual(refused.explained(), f"{refused.error} ({refused.hint})")

    def test_another_failure_has_nothing_in_use_and_looks_for_nothing(self) -> None:
        with patch.object(move_hint, "watching_repositories", side_effect=AssertionError("not a lock")), patch.object(
            move_hint, "holding_processes", side_effect=AssertionError("not a lock")
        ), patch.object(move_hint, "find_in_use", side_effect=AssertionError("not a lock")):
            refused = inspect_refused_move(OSError("disk on fire"), "E:\\project")
        self.assertEqual((refused.in_use, refused.lock, refused.hint), ([], False, PLAIN))
        self.assertEqual(refused.explained(), "disk on fire")
        self.assertEqual(refused.explained(always=True), f"disk on fire ({PLAIN})")

    def test_the_explanation_keeps_the_error_and_adds_the_hint_only_for_a_lock(self) -> None:
        err = PermissionError(errno.EACCES, "Access is denied")
        with patch.object(move_hint, "watching_repositories", return_value=[]), patch.object(
            move_hint, "holding_processes", return_value=[]
        ), patch.object(move_hint, "find_in_use", return_value=[]):
            text = explain_refused_move(err, "E:\\project", "organize")
        self.assertTrue(text.startswith(f"{err} (a program, "), text)
        self.assertTrue(text.endswith(f"{PLAIN})"), text)
        self.assertEqual(explain_refused_move(OSError("disk on fire"), "E:\\project", "organize"), "disk on fire")


class TestHoldingProcesses(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="sd_hint_")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, onerror=_force_remove)
        for name in ("a.txt", "b.txt", "c.txt"):
            (self.folder / name).write_text("x", encoding="utf-8")
        (self.folder / "sub").mkdir()
        (self.folder / "sub" / "deep.txt").write_text("x", encoding="utf-8")

    def test_it_is_silent_off_windows(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.object(
            move_hint, "_restart_manager_holders", side_effect=AssertionError("Windows only")
        ):
            self.assertEqual(holding_processes(str(self.folder)), [])

    def test_only_the_folders_own_files_are_asked_about_and_only_up_to_the_cap(self) -> None:
        asked: List[List[str]] = []

        def fake(files: List[str]) -> List[str]:
            asked.append(files)
            return ["Code (pid 7)"]

        with patch.object(sys, "platform", "win32"), patch.object(move_hint, "_restart_manager_holders", fake):
            self.assertEqual(holding_processes(str(self.folder)), ["Code (pid 7)"])
            self.assertEqual(holding_processes(str(self.folder), max_files=2), ["Code (pid 7)"])
        self.assertEqual(sorted(os.path.basename(f) for f in asked[0]), ["a.txt", "b.txt", "c.txt"])
        self.assertTrue(all(os.path.isabs(f) for f in asked[0]))
        self.assertEqual(len(asked[1]), 2)

    def test_a_folder_without_files_asks_nothing(self) -> None:
        (self.folder / "empty").mkdir()
        with patch.object(sys, "platform", "win32"), patch.object(
            move_hint, "_restart_manager_holders", side_effect=AssertionError("nothing to ask about")
        ):
            self.assertEqual(holding_processes(str(self.folder / "empty")), [])

    def test_the_files_found_open_are_asked_about_as_well_and_once(self) -> None:
        asked: List[List[str]] = []

        def fake(files: List[str]) -> List[str]:
            asked.append(files)
            return ["Code (pid 7)"]

        deep = str(self.folder / "sub" / "deep.txt")
        direct = str(self.folder / "a.txt")
        with patch.object(sys, "platform", "win32"), patch.object(move_hint, "_restart_manager_holders", fake):
            self.assertEqual(holding_processes(str(self.folder), max_files=1, also=[deep, direct]), ["Code (pid 7)"])
        names = sorted(os.path.basename(f) for f in asked[0])
        self.assertIn("deep.txt", names, "a file below the folder is asked about when it was found open")
        self.assertEqual(names.count("a.txt"), 1, "a file that is also a direct file is asked about once")

    def test_a_folder_that_is_a_file_still_asks_about_the_files_found_open(self) -> None:
        asked: List[List[str]] = []
        loose = str(self.folder / "a.txt")
        with patch.object(sys, "platform", "win32"), patch.object(
            move_hint, "_restart_manager_holders", lambda files: asked.append(files) or ["Reader (pid 3)"]
        ):
            self.assertEqual(holding_processes(loose, also=[loose]), ["Reader (pid 3)"])
        self.assertEqual([os.path.basename(f) for f in asked[0]], ["a.txt"])

    def test_an_unreachable_api_or_folder_gives_nothing_and_raises_nothing(self) -> None:
        for failure in (OSError("rstrtmgr.dll not found"), AttributeError("no WinDLL"), RuntimeError("x")):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(sys, "platform", "win32"), patch.object(
                    move_hint, "_restart_manager_holders", side_effect=failure
                ):
                    self.assertEqual(holding_processes(str(self.folder)), [])
        with patch.object(sys, "platform", "win32"):
            self.assertEqual(holding_processes(str(self.folder / "gone")), [])

    @unittest.skipUnless(sys.platform == "win32", "the Restart Manager is a Windows API")
    def test_the_restart_manager_names_this_process_when_it_holds_a_file_open(self) -> None:
        # Tolerant on purpose: where the API cannot be used (a locked-down session) the answer is empty
        with open(self.folder / "a.txt", "r", encoding="utf-8"):
            names = holding_processes(str(self.folder))
        self.assertIsInstance(names, list)
        if names:
            self.assertTrue(any(f"pid {os.getpid()})" in name for name in names), names)
        self.assertIsInstance(holding_processes(str(self.folder / "sub")), list)


class TestRefusedMovesAreExplained(SmartDriveTestCase):
    """The three ways smart-drive moves a folder give the same advice when Windows refuses."""

    def setUp(self) -> None:
        super().setUp()
        fake_home = self.test_dir / "fake_home"
        fake_appdata = fake_home / "AppData" / "Roaming"
        fake_appdata.mkdir(parents=True, exist_ok=True)
        for patcher in (
            patch("smart_drive.core.relink._resolve_locations", return_value=(fake_home, fake_appdata, False)),
            patch("smart_drive.core.relink._running_processes", return_value=set()),
            patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=SystemRefIndex([])),
            patch.object(move_hint, "watching_repositories", return_value=["E:\\p\\r"]),
            patch.object(move_hint, "holding_processes", return_value=["Code (pid 9)"]),
            patch.object(move_hint, "find_in_use", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_explained(self, text: str, retry: str) -> None:
        self.assertIn("Access is denied", text)
        self.assertIn("a program, a terminal whose current directory is inside it, an Explorer window or a git "
                      "fsmonitor daemon is using the folder", text)
        self.assertIn("a git fsmonitor daemon is still running in E:\\p\\r", text)
        self.assertIn("files in it are held open by Code (pid 9)", text)
        self.assertTrue(text.endswith(f"close the editor, terminal or coding agent using it, then run {retry} again)"), text)

    def test_organize_keeps_the_error_and_explains_it(self) -> None:
        drive = self.create_mock_drive()
        proj = drive / "locked_app"
        proj.mkdir()
        (proj / "package.json").write_text('{"name": "locked"}\n', encoding="utf-8")
        zoner = AutoZoner(str(drive), system_refs=SystemRefIndex([]))
        plan = [a for a in zoner.generate_plan() if os.path.basename(a.src_path) == "locked_app"]
        with patch("smart_drive.core.auto_zoner.os.rename", side_effect=PermissionError(errno.EACCES, "Access is denied")):
            res = zoner.apply_plan(plan)
        self.assertEqual(res["moved_count"], 0)
        self.assert_explained(res["errors"][0]["error"], "organize")
        self.assertTrue((proj / "package.json").is_file())

    def test_organize_gives_the_old_advice_for_other_failures(self) -> None:
        drive = self.create_mock_drive()
        proj = drive / "broken_app"
        proj.mkdir()
        (proj / "package.json").write_text('{"name": "broken"}\n', encoding="utf-8")
        zoner = AutoZoner(str(drive), system_refs=SystemRefIndex([]))
        plan = [a for a in zoner.generate_plan() if os.path.basename(a.src_path) == "broken_app"]
        with patch("smart_drive.core.auto_zoner.os.rename", side_effect=OSError("disk on fire")):
            res = zoner.apply_plan(plan)
        self.assertEqual(res["errors"][0]["error"], f"disk on fire ({PLAIN})")

    def undo_report(self, drive: Path, err: BaseException) -> Any:
        """The JSON `undo --apply` prints for the batch recorded on ``drive`` when moving it back raises ``err``."""
        out = io.StringIO()
        with patch("smart_drive.cli.cmd_undo._move", side_effect=err), contextlib.redirect_stdout(out), contextlib.redirect_stderr(
            io.StringIO()
        ):
            code = cli_main(["undo", "--root", str(drive), "--apply", "--json"])
        self.assertEqual(code, 1)
        report = json.loads(out.getvalue())
        self.assertEqual(report["status"], "failed")
        return report

    def undo_error(self, drive: Path, err: BaseException) -> str:
        """The error `undo --apply` reports for the batch recorded on ``drive`` when moving it back raises ``err``."""
        return str(self.undo_report(drive, err)["errors"][0]["error"])

    def recorded_batch(self) -> Path:
        """A drive with one organize batch to undo; returns the drive."""
        drive = self.create_mock_drive()
        moved = drive / "03_Development_Projects" / "app"
        moved.mkdir(parents=True)
        (moved / "package.json").write_text("{}", encoding="utf-8")
        record_moves(str(drive), [(str(drive / "app"), str(moved))], "organize")
        return drive

    def test_undo_explains_a_refused_restore_and_leaves_other_errors_alone(self) -> None:
        drive = self.recorded_batch()
        self.assert_explained(
            self.undo_error(drive, PermissionError(errno.EACCES, "Access is denied")), "smart-drive undo --apply"
        )
        self.assertEqual(self.undo_error(drive, OSError("disk on fire")), "disk on fire")

    def test_classify_explains_a_refused_relocation(self) -> None:
        drive = self.test_dir / "classify_drive"
        incoming = drive / "staging" / "web_app"
        incoming.mkdir(parents=True)
        (incoming / "package.json").write_text("{}", encoding="utf-8")
        engine = ClassifierEngine(root_path=drive, system_refs=SystemRefIndex([]))
        results = engine.scan_and_classify(incoming.parent, recursive=True)
        with patch("smart_drive.core.classifier.release_git_locks", return_value=[]), patch(
            "smart_drive.core.classifier.shutil.move", side_effect=PermissionError(errno.EACCES, "Access is denied")
        ):
            actions = engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["ERROR"])
        self.assert_explained(actions[0].error_message or "", "smart-drive classify --apply")
        self.assertTrue((incoming / "package.json").is_file())

    # What is in use is a line of the message and a field of the result, in organize, undo, classify and the MCP tool.

    def planned_app(self, name: str = "locked_app") -> Any:
        """A drive with one project folder that organize plans to move; returns (drive, folder, zoner, plan)."""
        drive = self.create_mock_drive()
        proj = drive / name
        proj.mkdir()
        (proj / "package.json").write_text('{"name": "locked"}\n', encoding="utf-8")
        zoner = AutoZoner(str(drive), system_refs=SystemRefIndex([]))
        plan = [a for a in zoner.generate_plan() if os.path.basename(a.src_path) == name]
        self.assertEqual(len(plan), 1)
        return drive, proj, zoner, plan

    def found_in_use(self, paths: List[str]) -> Any:
        """Patches the probe to find ``paths`` open, and nothing else to know about the folder."""
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(move_hint, "find_in_use", return_value=list(paths)))
        stack.enter_context(patch.object(move_hint, "holding_processes", return_value=[]))
        stack.enter_context(patch.object(move_hint, "watching_repositories", return_value=[]))
        self.addCleanup(stack.close)
        return stack

    def test_organize_carries_what_is_in_use_in_the_error_and_as_a_field(self) -> None:
        _drive, proj, zoner, plan = self.planned_app()
        with self.found_in_use([IMAGES]), patch(
            "smart_drive.core.auto_zoner.os.rename", side_effect=PermissionError(errno.EACCES, "Access is denied")
        ):
            res = zoner.apply_plan(plan)
        entry = res["errors"][0]
        self.assertEqual(entry["in_use"], [IMAGES])
        self.assertTrue(entry["error"].startswith("[Errno 13] Access is denied"), entry["error"])
        self.assertTrue(entry["error"].endswith(f"(in use: {IMAGES}; {UNNAMED})"), entry["error"])
        self.assertTrue((proj / "package.json").is_file())

    def test_organize_has_nothing_in_use_when_none_was_found_and_for_other_failures(self) -> None:
        for number, err in enumerate((PermissionError(errno.EACCES, "Access is denied"), OSError("disk on fire"))):
            with self.subTest(err=repr(err)):
                _drive, _proj, zoner, plan = self.planned_app(f"locked_app_{number}")
                with patch("smart_drive.core.auto_zoner.os.rename", side_effect=err):
                    res = zoner.apply_plan(plan)
                self.assertEqual(res["errors"][0]["in_use"], [])

    def test_organize_never_fails_a_move_because_of_the_probe(self) -> None:
        _drive, _proj, zoner, plan = self.planned_app()
        with patch.object(move_hint, "find_in_use", side_effect=RuntimeError("probe broke")), patch(
            "smart_drive.core.auto_zoner.os.rename", side_effect=PermissionError(errno.EACCES, "Access is denied")
        ):
            res = zoner.apply_plan(plan)  # still reports the refusal, with the usual hint
        self.assertEqual((res["error_count"], res["errors"][0]["in_use"]), (1, []))
        self.assertTrue(res["errors"][0]["error"].endswith(f"{PLAIN})"), res["errors"][0]["error"])

    def test_the_probe_only_runs_after_a_refusal(self) -> None:
        _drive, proj, zoner, plan = self.planned_app()
        with patch.object(move_hint, "find_in_use", side_effect=AssertionError("nothing was refused")):
            res = zoner.apply_plan(plan)
        self.assertEqual((res["moved_count"], res["error_count"]), (1, 0))
        self.assertFalse(proj.exists())

    def test_organize_json_and_text_show_what_is_in_use(self) -> None:
        drive, _proj, _zoner, _plan = self.planned_app()
        refuse = patch("smart_drive.core.auto_zoner.os.rename", side_effect=PermissionError(errno.EACCES, "Access is denied"))
        with self.found_in_use([IMAGES]), refuse:
            as_json = io.StringIO()
            with contextlib.redirect_stdout(as_json):
                cli_main(["organize", "--root", str(drive), "--apply", "--json"])
            as_text = io.StringIO()
            with contextlib.redirect_stdout(as_text):
                cli_main(["organize", "--root", str(drive), "--apply"])
        errors = json.loads(as_json.getvalue())["zoning"]["errors"]
        self.assertTrue(errors)
        for entry in errors:
            self.assertEqual(entry["in_use"], [IMAGES])
        lines = [line for line in as_text.getvalue().splitlines() if "Could not move" in line]
        self.assertTrue(lines)
        for line in lines:
            self.assertIn(f"in use: {IMAGES}; {UNNAMED})", line, "the whole hint is on the line of the item")

    def test_undo_carries_what_is_in_use_in_the_error_and_as_a_field(self) -> None:
        drive = self.recorded_batch()
        with self.found_in_use([IMAGES]):
            entry = self.undo_report(drive, PermissionError(errno.EACCES, "Access is denied"))["errors"][0]
        self.assertEqual(entry["in_use"], [IMAGES])
        self.assertTrue(entry["error"].endswith(f"(in use: {IMAGES}; {UNNAMED.replace('organize', 'smart-drive undo --apply')})"))
        self.assertEqual(self.undo_report(drive, OSError("disk on fire"))["errors"][0]["in_use"], [])

    def test_classify_carries_what_is_in_use_on_the_action(self) -> None:
        drive = self.test_dir / "classify_drive"
        incoming = drive / "staging" / "web_app"
        incoming.mkdir(parents=True)
        (incoming / "package.json").write_text("{}", encoding="utf-8")
        engine = ClassifierEngine(root_path=drive, system_refs=SystemRefIndex([]))
        results = engine.scan_and_classify(incoming.parent, recursive=True)
        planned = engine.execute_relocation(results, dry_run=True)
        self.assertEqual([a.to_dict()["in_use"] for a in planned], [[]], "an action that was not refused has none")
        with self.found_in_use([IMAGES]), patch("smart_drive.core.classifier.release_git_locks", return_value=[]), patch(
            "smart_drive.core.classifier.shutil.move", side_effect=PermissionError(errno.EACCES, "Access is denied")
        ):
            actions = engine.execute_relocation(results, dry_run=False)
        self.assertEqual([a.status for a in actions], ["ERROR"])
        self.assertEqual(actions[0].in_use, [IMAGES])
        self.assertEqual(actions[0].to_dict()["in_use"], [IMAGES])
        self.assertIn(f"in use: {IMAGES}; ", actions[0].error_message or "")

    def test_the_mcp_tool_carries_what_is_in_use_in_its_errors(self) -> None:
        drive, _proj, _zoner, _plan = self.planned_app()
        server = SmartDriveMCPServer(root=str(drive))
        with self.found_in_use([IMAGES]), patch(
            "smart_drive.core.auto_zoner.os.rename", side_effect=PermissionError(errno.EACCES, "Access is denied")
        ):
            res = server.dispatch_tool("ssd_auto_organize", {"apply": True})
        errors = res["result"]["errors"]
        self.assertTrue(errors)
        for entry in errors:
            self.assertEqual(entry["in_use"], [IMAGES])
            self.assertIn(f"in use: {IMAGES}; ", entry["error"])
        json.dumps(res)  # still plain JSON for the client


@unittest.skipUnless(sys.platform == "win32", "an open handle and the NTFS rename rule are Windows things")
class TestARealRefusedMove(SmartDriveTestCase):
    """No fakes: Windows itself refuses the rename because a handle is open somewhere below the folder."""

    def setUp(self) -> None:
        super().setUp()
        fake_home = self.test_dir / "fake_home"
        fake_appdata = fake_home / "AppData" / "Roaming"
        fake_appdata.mkdir(parents=True, exist_ok=True)
        for patcher in (
            patch("smart_drive.core.relink._resolve_locations", return_value=(fake_home, fake_appdata, False)),
            patch("smart_drive.core.relink._running_processes", return_value=set()),
            patch("smart_drive.core.system_refs.SystemRefIndex.collect", return_value=SystemRefIndex([])),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_organize_names_the_folder_that_a_handle_keeps_open_and_moves_it_once_that_is_closed(self) -> None:
        drive = self.create_mock_drive()
        app = drive / "locked_app"
        images = app / "datathon-2026-round-1" / "Nop_bai" / "Images"
        images.mkdir(parents=True)
        (app / "package.json").write_text('{"name": "locked"}\n', encoding="utf-8")
        zoner = AutoZoner(str(drive), system_refs=SystemRefIndex([]))
        plan = [a for a in zoner.generate_plan() if os.path.basename(a.src_path) == "locked_app"]
        self.assertEqual(len(plan), 1)

        with OpenHandle(str(images), access=EXPLORER_WATCH_ACCESS):  # what Explorer keeps on a Quick Access folder
            res = zoner.apply_plan(plan)
        self.assertEqual(res["moved_count"], 0)
        entry = res["errors"][0]
        self.assertIn("[WinError 5] Access is denied", entry["error"])
        self.assertEqual(entry["in_use"], [str(images)])
        self.assertIn(f"in use: {images}", entry["error"])
        self.assertTrue(app.is_dir(), "a refused move leaves the folder where it was")

        for _attempt in range(5):  # a virus scanner may still have a fresh file open for a moment
            res = zoner.apply_plan(plan)
            if res["moved_count"]:
                break
            time.sleep(0.2)
        self.assertEqual((res["moved_count"], res["error_count"]), (1, 0), res["errors"])
        self.assertFalse(app.exists())


if __name__ == "__main__":
    unittest.main()
