"""tests/test_busy_apps_unknown.py - organize, undo and Smart_Organize say when open coding agents cannot be checked.

relink() reports `busy_apps == []` and remembers `_unreadable_processes` when `tasklist`/`ps` fails or times out, so an
empty `busy_apps` alone does not mean that no coding agent or IDE is open. Verifies:
1. `organize` and `undo` JSON carry `busy_apps_unknown`, and their text dry runs tell the person to quit open coding
   agents and IDEs themselves, only when the list of running programs could not be read.
2. The guided Smart_Organize flow stops at step 2 with its own message and the same prompt (Enter re-checks, C goes on,
   Q cancels) instead of moving at once.
3. `--apply` is not changed: organize and undo still move, since nothing is at risk and `smart-drive relink` can be
   run again.

100% isolated: a temporary drive, a fake home and AppData, and a stand-in for the list of running programs. The real
process list, registry and coding agent configuration files are never read.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Tuple
import unittest
from unittest.mock import patch

from smart_drive.cli.cmd_organize import busy_apps_of
from smart_drive.cli.main import main as cli_main
from smart_drive.cli.smart_organize_runner import _extract_relink_dict, get_log_path, run_smart_organize, t
from smart_drive.core.relink import RelinkReport, record_moves
from smart_drive.core.system_refs import SystemRefIndex
from tests.helpers import SmartDriveTestCase

RELINK = importlib.import_module("smart_drive.core.relink")

UNKNOWN_NOTE = "Could not read the list of running programs, so open coding agents and IDEs cannot be detected"


class FakeMachineTestCase(SmartDriveTestCase):
    """A temporary drive with one movable folder, a fake home and a stand-in for the list of running programs."""

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_appdata.mkdir(parents=True, exist_ok=True)
        self.drive = self.test_dir / "drive"
        self.project = self.drive / "my_service"
        self.project.mkdir(parents=True)
        (self.project / "pyproject.toml").write_text("[project]\nname = 'my_service'\n", encoding="utf-8")
        self.processes = self._start(patch.object(RELINK, "_running_processes", return_value=set()))
        self._start(patch.object(RELINK, "_resolve_locations", return_value=(self.fake_home, self.fake_appdata, False)))
        self._start(patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])))

    def _start(self, patcher: Any) -> Any:
        """Starts a patcher for the length of the test and returns its mock."""
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def _remember(self, folder: Path) -> None:
        """Makes Claude Code remember `folder`, so that a relink of it has something to update."""
        config = {"projects": {str(folder): {"hasTrustDialogAccepted": True}}}
        (self.fake_home / ".claude.json").write_text(json.dumps(config), encoding="utf-8")

    def _run_cli(self, argv: List[str]) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(argv)
        return code, out.getvalue(), err.getvalue()


class TestOrganizeBusyAppsUnknown(FakeMachineTestCase):
    """`organize` reports whether the open coding agents could be checked."""

    def test_dry_run_json_says_when_the_agents_could_not_be_checked(self) -> None:
        self.processes.return_value = None
        code, out, err = self._run_cli(["organize", "--root", str(self.drive), "--dry-run", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["status"], "dry_run")
        self.assertEqual(len(data["planned_actions"]), 1)
        self.assertEqual(data["busy_apps"], [])
        self.assertIs(data["busy_apps_unknown"], True)

        self.processes.return_value = {"Code.exe"}
        code, out, err = self._run_cli(["organize", "--root", str(self.drive), "--dry-run", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["busy_apps"], ["VS Code"])
        self.assertIs(data["busy_apps_unknown"], False)

        self.processes.return_value = set()
        code, out, err = self._run_cli(["organize", "--root", str(self.drive), "--dry-run", "--json"])
        data = json.loads(out)
        self.assertEqual(data["busy_apps"], [])
        self.assertIs(data["busy_apps_unknown"], False)

    def test_nothing_to_organize_still_says_when_the_agents_could_not_be_checked(self) -> None:
        shutil.rmtree(self.project)
        self.processes.return_value = None
        code, out, err = self._run_cli(["organize", "--root", str(self.drive), "--dry-run", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["planned_actions"], [])
        self.assertEqual(data["busy_apps"], [])
        self.assertIs(data["busy_apps_unknown"], True)

        # no move is planned, so there is no agent to quit before --apply and the text says nothing about it
        code, out, err = self._run_cli(["organize", "--root", str(self.drive)])
        self.assertEqual(code, 0, err)
        self.assertIn("No loose or unorganized items detected", out)
        self.assertNotIn("Could not read the list of running programs", out)

    def test_dry_run_text_prints_the_note_only_when_the_agents_could_not_be_checked(self) -> None:
        self._remember(self.project)

        self.processes.return_value = None
        code, out, err = self._run_cli(["organize", "--root", str(self.drive)])
        self.assertEqual(code, 0, err)
        self.assertIn(f"  ! {UNKNOWN_NOTE}: quit them before --apply.", out)
        # after the block of agents that follow the move, before the closing hint
        self.assertLess(out.index("Coding agents that will follow these moves:"), out.index(UNKNOWN_NOTE))
        self.assertLess(out.index(UNKNOWN_NOTE), out.index("Run with --apply"))

        self.processes.return_value = set()
        code, out, err = self._run_cli(["organize", "--root", str(self.drive)])
        self.assertEqual(code, 0, err)
        self.assertNotIn("Could not read the list of running programs", out)

        self.processes.return_value = {"Code.exe"}
        code, out, err = self._run_cli(["organize", "--root", str(self.drive)])
        self.assertEqual(code, 0, err)
        self.assertNotIn("Could not read the list of running programs", out)
        self.assertIn("Close these before --apply: VS Code", out)

    def test_apply_still_moves_and_says_when_the_agents_could_not_be_checked(self) -> None:
        self.processes.return_value = None
        code, out, err = self._run_cli(["organize", "--root", str(self.drive), "--apply", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["status"], "applied")
        self.assertEqual(data["zoning"]["moved_count"], 1)
        self.assertEqual(data["busy_apps"], [])
        self.assertIs(data["busy_apps_unknown"], True)
        self.assertTrue((self.drive / "03_Development_Projects" / "my_service" / "pyproject.toml").is_file())
        self.assertFalse(self.project.exists())

    def test_apply_with_a_readable_list_is_not_unknown(self) -> None:
        self.processes.return_value = {"Code.exe"}
        code, out, err = self._run_cli(["organize", "--root", str(self.drive), "--apply", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["busy_apps"], ["VS Code"])
        self.assertIs(data["busy_apps_unknown"], False)


class TestUndoBusyAppsUnknown(FakeMachineTestCase):
    """`undo` reports whether the open coding agents could be checked."""

    def _journal_a_move(self) -> Path:
        """Moves the project into 03_Development_Projects by hand and journals it, as an earlier `organize` did."""
        moved = self.drive / "03_Development_Projects" / "my_service"
        moved.parent.mkdir(parents=True)
        self.project.rename(moved)
        record_moves(str(self.drive), [(str(self.project), str(moved))], "organize")
        return moved

    def test_dry_run_json_says_when_the_agents_could_not_be_checked(self) -> None:
        self._journal_a_move()

        self.processes.return_value = None
        code, out, err = self._run_cli(["undo", "--root", str(self.drive), "--dry-run", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["status"], "dry_run")
        self.assertEqual(data["planned_count"], 1)
        self.assertEqual(data["busy_apps"], [])
        self.assertIs(data["busy_apps_unknown"], True)

        self.processes.return_value = set()
        code, out, err = self._run_cli(["undo", "--root", str(self.drive), "--dry-run", "--json"])
        data = json.loads(out)
        self.assertEqual(data["busy_apps"], [])
        self.assertIs(data["busy_apps_unknown"], False)

        self.processes.return_value = {"Code.exe"}
        code, out, err = self._run_cli(["undo", "--root", str(self.drive), "--dry-run", "--json"])
        data = json.loads(out)
        self.assertEqual(data["busy_apps"], ["VS Code"])
        self.assertIs(data["busy_apps_unknown"], False)

    def test_nothing_to_undo_json_carries_the_key_too(self) -> None:
        self.processes.return_value = None
        code, out, err = self._run_cli(["undo", "--root", str(self.drive), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["status"], "nothing_to_undo")
        self.assertEqual(data["busy_apps"], [])
        self.assertIs(data["busy_apps_unknown"], True)

        self.processes.return_value = {"Code.exe"}
        code, out, err = self._run_cli(["undo", "--root", str(self.drive), "--json"])
        data = json.loads(out)
        self.assertEqual(data["busy_apps"], ["VS Code"])
        self.assertIs(data["busy_apps_unknown"], False)

    def test_dry_run_text_prints_the_note_only_when_there_is_something_to_restore(self) -> None:
        moved = self._journal_a_move()
        self._remember(moved)

        self.processes.return_value = None
        code, out, err = self._run_cli(["undo", "--root", str(self.drive)])
        self.assertEqual(code, 0, err)
        self.assertIn(f"  ! {UNKNOWN_NOTE}: quit them before --apply.", out)
        self.assertLess(out.index("Coding agents that will follow these moves:"), out.index(UNKNOWN_NOTE))
        self.assertLess(out.index(UNKNOWN_NOTE), out.index("Run with --apply"))

        self.processes.return_value = set()
        code, out, err = self._run_cli(["undo", "--root", str(self.drive)])
        self.assertNotIn("Could not read the list of running programs", out)

        # the moved folder is gone: the batch has nothing left to restore, so there is nothing to warn about
        moved_away = moved.parent / "elsewhere"
        moved.rename(moved_away)
        self.processes.return_value = None
        code, out, err = self._run_cli(["undo", "--root", str(self.drive)])
        self.assertEqual(code, 0, err)
        self.assertIn("No items can be restored from this batch.", out)
        self.assertNotIn("Could not read the list of running programs", out)

    def test_apply_still_restores_when_the_agents_could_not_be_checked(self) -> None:
        moved = self._journal_a_move()
        self.processes.return_value = None
        code, out, err = self._run_cli(["undo", "--root", str(self.drive), "--apply", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["status"], "applied")
        self.assertEqual(data["restored_count"], 1)
        self.assertTrue((self.project / "pyproject.toml").is_file())
        self.assertFalse(moved.exists())


class TestBusyAppsOf(FakeMachineTestCase):
    """`busy_apps_of` reads a relink report, or looks at the running programs itself when there is none."""

    def test_a_report_says_what_relink_saw(self) -> None:
        seen = RelinkReport(dry_run=True)
        seen.busy_apps = ["Cursor"]
        seen._unreadable_processes = False
        self.assertEqual(busy_apps_of(seen), (["Cursor"], False))

        unreadable = RelinkReport(dry_run=True)
        unreadable._unreadable_processes = True
        self.assertEqual(busy_apps_of(unreadable), ([], True))

        # a report relink returned early (no move to follow) never looked at the programs
        self.assertEqual(busy_apps_of(RelinkReport()), ([], False))

    def test_without_a_report_it_looks_at_the_running_programs(self) -> None:
        self.processes.return_value = None
        self.assertEqual(busy_apps_of(None), ([], True))
        self.processes.return_value = {"Cursor.exe", "Code.exe", "notepad.exe"}
        self.assertEqual(busy_apps_of(None), (["Cursor", "VS Code"], False))
        self.processes.return_value = set()
        self.assertEqual(busy_apps_of(None), ([], False))


class TestSmartOrganizeUnknownAgents(FakeMachineTestCase):
    """The guided flow asks to close the coding agents when the list of running programs could not be read."""

    UNKNOWN = {"busy_apps": [], "busy_apps_unknown": True, "changed": []}
    CLEAR = {"busy_apps": [], "busy_apps_unknown": False, "changed": []}

    def _guided(
        self,
        answers: List[str],
        previews: Optional[List[Dict[str, Any]]] = None,
        lang: str = "en",
    ) -> Tuple[int, str, List[str], List[bool]]:
        """Runs the guided flow; `previews` are what the dry-run relink returns in turn (the last one repeats).

        Without `previews` the real relink answers. Returns (exit code, output, prompts asked, dry-run flags seen).
        """
        prompts: List[str] = []
        pending = iter(answers)
        seen: List[bool] = []

        def input_fn(prompt: str) -> str:
            prompts.append(prompt)
            return next(pending)

        def preview(root: str, pairs: Any, tool: str, dry_run: bool = False) -> Dict[str, Any]:
            seen.append(dry_run)
            return previews[min(len(seen), len(previews)) - 1]

        out = io.StringIO()
        rc = run_smart_organize(
            str(self.drive),
            lang=lang,
            input_fn=input_fn,
            output_stream=out,
            after_moves_fn=preview if previews else None,
        )
        return rc, out.getvalue(), prompts, seen

    def _log(self) -> str:
        return Path(get_log_path(str(self.drive))).read_text(encoding="utf-8")

    def _assert_nothing_moved(self) -> None:
        self.assertTrue((self.project / "pyproject.toml").is_file())
        self.assertFalse((self.drive / "03_Development_Projects" / "my_service").exists())

    def test_an_unreadable_list_stops_at_step_2_and_a_recheck_goes_on_to_the_yes_prompt(self) -> None:
        rc, out, prompts, seen = self._guided(["", "no"], [self.UNKNOWN, self.CLEAR])

        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count(t("step2_unknown_title", "en")), 1)
        self.assertIn(t("step2_unknown_hint", "en"), out)
        self.assertNotIn("Open coding agents / IDEs detected", out)
        self.assertEqual(seen, [True, True], "the preview and one re-check, both dry runs")
        self.assertEqual(
            prompts,
            [t("step2_busy_prompt", "en"), t("step3_confirm_prompt", "en", count=1)],
            "Enter re-checks, then the flow asks for YES",
        )
        # the all-clear line only comes once both the agents and the readability are clear
        self.assertIn(t("step2_clear", "en"), out)
        self.assertLess(out.index(t("step2_unknown_title", "en")), out.index(t("step2_clear", "en")))
        self.assertIn(t("cancelled", "en"), out)
        self._assert_nothing_moved()
        self.assertIn("BUSY_APPS | unknown", self._log())

    def test_q_at_the_unknown_prompt_cancels_and_nothing_moves(self) -> None:
        rc, out, prompts, seen = self._guided(["q"], [self.UNKNOWN])

        self.assertEqual(rc, 0, out)
        self.assertIn(t("step2_unknown_title", "en"), out)
        self.assertIn(t("cancelled", "en"), out)
        self.assertNotIn(t("step2_clear", "en"), out)
        self.assertEqual(prompts, [t("step2_busy_prompt", "en")], "no YES prompt after Q")
        self._assert_nothing_moved()
        self.assertIn("CANCELLED_AT_BUSY_APPS | unknown", self._log())

    def test_c_continues_anyway_without_the_all_clear_line(self) -> None:
        rc, out, prompts, seen = self._guided(["c", "no"], [self.UNKNOWN])

        self.assertEqual(rc, 0, out)
        self.assertEqual(seen, [True], "C does not check again")
        self.assertEqual(prompts, [t("step2_busy_prompt", "en"), t("step3_confirm_prompt", "en", count=1)])
        self.assertNotIn(t("step2_clear", "en"), out)
        self._assert_nothing_moved()

    def test_the_prompt_comes_back_while_the_list_stays_unreadable(self) -> None:
        rc, out, prompts, seen = self._guided(["", "", "q"], [self.UNKNOWN])

        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count(t("step2_unknown_title", "en")), 3)
        self.assertEqual(seen, [True, True, True])
        self._assert_nothing_moved()

    def test_open_agents_keep_their_own_message_and_unknown_does_not_replace_it(self) -> None:
        busy = {"busy_apps": ["Cursor"], "busy_apps_unknown": False, "changed": []}
        rc, out, prompts, seen = self._guided(["c", "no"], [busy])

        self.assertEqual(rc, 0, out)
        self.assertIn(t("step2_busy_title", "en", apps="Cursor"), out)
        self.assertNotIn(t("step2_unknown_title", "en"), out)
        self.assertIn("BUSY_APPS | Cursor", self._log())

    def test_a_recheck_that_finds_an_open_agent_after_an_unreadable_list_asks_again(self) -> None:
        busy = {"busy_apps": ["Cursor"], "busy_apps_unknown": False, "changed": []}
        rc, out, prompts, seen = self._guided(["", "q"], [self.UNKNOWN, busy])

        self.assertEqual(rc, 0, out)
        self.assertLess(out.index(t("step2_unknown_title", "en")), out.index(t("step2_busy_title", "en", apps="Cursor")))
        self.assertEqual(prompts, [t("step2_busy_prompt", "en")] * 2)
        self._assert_nothing_moved()

    def test_the_vietnamese_flow_uses_the_vietnamese_messages(self) -> None:
        rc, out, prompts, seen = self._guided(["q"], [self.UNKNOWN], lang="vi")

        self.assertEqual(rc, 0, out)
        self.assertIn(t("step2_unknown_title", "vi"), out)
        self.assertIn(t("step2_unknown_hint", "vi"), out)
        self.assertNotEqual(t("step2_unknown_title", "vi"), t("step2_unknown_title", "en"))
        self.assertNotEqual(t("step2_unknown_hint", "vi"), t("step2_unknown_hint", "en"))

    def test_the_real_relink_report_stops_the_flow_when_the_process_list_is_unreadable(self) -> None:
        self.processes.return_value = None
        rc, out, prompts, seen = self._guided(["q"])

        self.assertEqual(rc, 0, out)
        self.assertIn(t("step2_unknown_title", "en"), out)
        self.assertEqual(prompts, [t("step2_busy_prompt", "en")])
        self._assert_nothing_moved()

        self.processes.return_value = set()
        rc, out, prompts, seen = self._guided(["no"])
        self.assertEqual(rc, 0, out)
        self.assertNotIn(t("step2_unknown_title", "en"), out)
        self.assertIn(t("step2_clear", "en"), out)
        self.assertEqual(prompts, [t("step3_confirm_prompt", "en", count=1)])

    def test_extract_relink_dict_carries_the_unreadable_flag_of_report_objects(self) -> None:
        report = RelinkReport(dry_run=True)
        self.assertIs(_extract_relink_dict(report)["busy_apps_unknown"], False)
        report._unreadable_processes = True
        extracted = _extract_relink_dict(report)
        self.assertIs(extracted["busy_apps_unknown"], True)
        self.assertEqual(extracted["busy_apps"], [])

        class Bare:
            busy_apps = ["Cursor"]
            changed: List[Any] = []
            _unreadable_processes = True

        bare = _extract_relink_dict(Bare())
        self.assertIs(bare["busy_apps_unknown"], True)
        self.assertEqual(bare["busy_apps"], ["Cursor"])

        # a dict passes through as it is, so it may carry the key itself
        as_dict = {"busy_apps": [], "busy_apps_unknown": True, "changed": []}
        self.assertIs(_extract_relink_dict(as_dict), as_dict)
        self.assertNotIn("busy_apps_unknown", _extract_relink_dict(None))


if __name__ == "__main__":
    unittest.main()
