"""tests/test_previews_write_nothing.py - a preview changes nothing on the drive, whichever command shows it.

Regression: the guided organizer (`Smart_Organize.*`) appended to `<root>/.smart_drive/smart-organize.log` from its first
line and created `.smart_drive/` to do it, so its dry-run preview and its `SD_SELFTEST=1` / `--selftest` mode, which the
launchers and the docstring called "read-only", left a log on a drive they promised not to touch. Found by running every
preview on a fresh drive and comparing each path, size and SHA-256 below its root before and after, once `organize
--dry-run` had been fixed the same way (see `tests/test_organize_dry_run_writes_nothing.py`). The guided organizer now
keeps its log lines in memory and writes them, in order and with the times they were recorded at, only once `YES` has
been typed and the moves start; a run that ends before that (nothing to move, `Q` at the busy-apps step, anything but
`YES`, the self-test) leaves the drive exactly as it was.

This file keeps the whole promise: every CLI preview, every MCP tool that previews and every way the guided organizer can
end before the moves runs on a fresh drive of its own (loose folders worth organizing, junk of every tier, duplicate
files, a repository, no `.smart_drive/`), and a command that writes anything there fails its own subtest. `sentinel`
without `--no-heal` is not a preview: it heals the anti-indexing markers, by design.

100% isolated like the other organize tests: temporary drives, a fake home and AppData, and stand-ins for the list of
running programs and the machine's system references. Nothing on the real machine is read or written.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union
import unittest
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.cli.smart_organize_runner import OrganizeLog, append_organize_log, get_log_path, run_smart_organize, t
from smart_drive.core.relink import record_moves
from smart_drive.core.system_refs import SystemRefIndex
from smart_drive.mcp.server import SmartDriveMCPServer
from tests.helpers import SmartDriveTestCase, TreeState, organize_log_events, snapshot_tree, tree_changes

# `smart_drive.cli` re-exports the `main` function, so the attribute `smart_drive.cli.main` is that function and not the
# submodule; the modules whose names the code under test looks up are patched through the module objects instead.
RELINK = importlib.import_module("smart_drive.core.relink")
RUNNER = importlib.import_module("smart_drive.cli.smart_organize_runner")
CMD_ORGANIZE = importlib.import_module("smart_drive.cli.cmd_organize")

ROOT = Path(__file__).resolve().parent.parent

# What a fresh drive holds, by path below its root. No `.smart_drive/`: that is made by the commands that keep state.
DRIVE_FILES: Dict[str, Union[str, bytes]] = {
    # Loose folders and files that `organize` would move
    "my_service/pyproject.toml": "[project]\nname = 'my_service'\n",
    "my_service/app.py": "print('hi')\n",
    "web_app/package.json": "{}\n",
    "random_folder/data.csv": "a,b\n1,2\n",
    "notes.txt": "some notes\n",
    "loose_weights.safetensors": b"WEIGHTS" * 100,
    # Junk of every tier: OS metadata (1), regenerable caches and build folders beside their project file (2),
    # temp files and dumps (3)
    ".DS_Store": b"junk",
    "Thumbs.db": b"junk",
    "._x": b"junk",
    "my_service/__pycache__/app.cpython-313.pyc": bytes(64),
    "web_app/dist/bundle.js": "x\n",
    "scratch.tmp": b"temp",
    "crash.dmp": b"dump",
    # Two identical files, for `dup`
    "random_folder/copy.bin": b"D" * 5000,
    "my_service/copy.bin": b"D" * 5000,
    # Looks like a git repository to `sentinel`, `repos` and `gitignore` (git itself cannot read it: it has no objects)
    "tools_repo/.git/HEAD": "ref: refs/heads/main\n",
    "tools_repo/README.md": "# tools\n",
}

# Every CLI preview as it is typed, with the exit codes it may end with: `sentinel` ends with 1 when the drive needs
# attention, which a drive without anti-indexing shields does, and `search` ends with 1 when there is no index to search.
CliPreview = Tuple[str, Callable[[Path], List[str]], Tuple[int, ...]]
CLI_PREVIEWS: List[CliPreview] = [
    ("status", lambda d: ["status", "--root", str(d)], (0,)),
    ("status --json", lambda d: ["status", "--root", str(d), "--json"], (0,)),
    ("audit", lambda d: ["audit", "--root", str(d)], (0,)),
    ("audit --json", lambda d: ["audit", "--root", str(d), "--json"], (0,)),
    ("audit --markdown", lambda d: ["audit", "--root", str(d), "--markdown"], (0,)),
    ("clean", lambda d: ["clean", "--root", str(d)], (0,)),
    ("clean --tier 2", lambda d: ["clean", "--root", str(d), "--tier", "2"], (0,)),
    ("clean --tier 3 --json", lambda d: ["clean", "--root", str(d), "--tier", "3", "--json"], (0,)),
    ("clean --only-tier 3 --json", lambda d: ["clean", "--root", str(d), "--only-tier", "3", "--json"], (0,)),
    ("clean --list-quarantine", lambda d: ["clean", "--root", str(d), "--list-quarantine"], (0,)),
    ("clean --purge-quarantine", lambda d: ["clean", "--root", str(d), "--purge-quarantine"], (0,)),
    ("organize", lambda d: ["organize", "--root", str(d)], (0,)),
    ("organize --dry-run --json", lambda d: ["organize", "--root", str(d), "--dry-run", "--json"], (0,)),
    ("organize --clean --json", lambda d: ["organize", "--root", str(d), "--clean", "--json"], (0,)),
    ("undo", lambda d: ["undo", "--root", str(d)], (0,)),
    ("undo --dry-run --json", lambda d: ["undo", "--root", str(d), "--dry-run", "--json"], (0,)),
    ("classify", lambda d: ["classify", str(d), "--root", str(d)], (0,)),
    ("classify --suggest --recursive", lambda d: ["classify", str(d), "--root", str(d), "--suggest", "--recursive"], (0,)),
    ("classify --dry-run --json", lambda d: ["classify", str(d), "--root", str(d), "--dry-run", "--json"], (0,)),
    ("dup", lambda d: ["dup", "--root", str(d)], (0,)),
    ("dup --json", lambda d: ["dup", "--root", str(d), "--json"], (0,)),
    ("sentinel --no-heal", lambda d: ["sentinel", "--root", str(d), "--no-heal"], (0, 1)),
    ("sentinel --no-heal --json", lambda d: ["sentinel", "--root", str(d), "--no-heal", "--json"], (0, 1)),
    ("relink --dry-run", lambda d: ["relink", "--root", str(d), "--dry-run"], (0,)),
    ("relink --find --json", lambda d: ["relink", "--root", str(d), "--find", "--json"], (0,)),
    ("paths --broken", lambda d: ["paths", "--root", str(d), "--broken"], (0,)),
    ("paths --broken --json", lambda d: ["paths", "--root", str(d), "--broken", "--json"], (0,)),
    ("paths --fix --dry-run", lambda d: ["paths", "--root", str(d), "--fix", "--dry-run"], (0,)),
    ("gitignore audit", lambda d: ["gitignore", "audit", "--root", str(d)], (0,)),
    ("gitignore audit --json", lambda d: ["gitignore", "audit", "--root", str(d), "--json"], (0,)),
    ("gitignore audit REPO", lambda d: ["gitignore", "audit", str(d / "tools_repo"), "--root", str(d)], (0,)),
    ("gitignore apply REPO", lambda d: ["gitignore", "--root", str(d), "apply", str(d / "tools_repo")], (0,)),
    ("gitignore apply REPO --json", lambda d: ["gitignore", "--root", str(d), "apply", str(d / "tools_repo"), "--json"], (0,)),
    ("snapshot list", lambda d: ["snapshot", "list", "--root", str(d)], (0,)),
    ("snapshot list --json", lambda d: ["snapshot", "list", "--root", str(d), "--json"], (0,)),
    ("search", lambda d: ["search", "notes", "--root", str(d)], (1,)),
]

# Every MCP tool that previews, as an agent calls it. `ssd_update_index` is not one: it builds the index.
McpPreview = Tuple[str, Dict[str, Any]]
MCP_PREVIEWS: List[McpPreview] = [
    ("ssd_status", {}),
    ("ssd_audit", {}),
    ("ssd_clean", {"dry_run": True}),
    ("ssd_clean", {"dry_run": True, "tier": 2}),
    ("ssd_clean", {"dry_run": True, "tier": 3}),
    ("ssd_find_duplicates", {}),
    ("ssd_check_safety", {"path": "notes.txt"}),
    ("ssd_search", {"query": "notes"}),  # no index yet: it says so and builds none
    ("ssd_gitignore", {"action": "audit"}),
    ("ssd_gitignore", {"action": "apply", "repo": "tools_repo"}),  # without apply=true it only simulates
    ("ssd_auto_organize", {"apply": False}),
    ("ssd_auto_organize", {"apply": False, "clean": True}),
]


class _Machine(SmartDriveTestCase):
    """A temporary machine: a fake home and AppData, no running programs and no system references."""

    drives = 0

    def setUp(self) -> None:
        super().setUp()
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_appdata.mkdir(parents=True)
        for patcher in (
            patch.object(RELINK, "_running_processes", return_value=set()),
            patch.object(RELINK, "_resolve_locations", return_value=(self.fake_home, self.fake_appdata, False)),
            patch.object(SystemRefIndex, "collect", return_value=SystemRefIndex([])),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_drive(self, loose: bool = True) -> Path:
        """A fresh drive root: `DRIVE_FILES` by default, an empty folder (nothing to organize) with `loose=False`."""
        self.drives += 1
        drive = self.test_dir / f"drive{self.drives}"
        drive.mkdir()
        if loose:
            for rel, content in DRIVE_FILES.items():
                path = drive.joinpath(*rel.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return drive

    def run_cli(self, *argv: str, codes: Sequence[int] = (0,)) -> str:
        """Runs `smart-drive <argv>` in this process and returns what it printed; it must end with one of `codes`."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli_main(list(argv))
            except SystemExit as exit_request:  # argparse refusing the command line
                self.fail(f"{argv[0]} did not take its arguments ({exit_request.code}): {err.getvalue()}")
        self.assertIn(code, codes, err.getvalue())
        return out.getvalue()

    def call_tool(self, drive: Path, name: str, **arguments: Any) -> Dict[str, Any]:
        """A tool as an agent calls it: a JSON-RPC tools/call to a server on `drive`, the result parsed out of the text."""
        server = SmartDriveMCPServer(root=str(drive), rate_limit_enabled=False)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        with contextlib.redirect_stdout(io.StringIO()):  # the server also writes each reply to its stdout
            reply = server.handle_request(request)
        self.assertIsNotNone(reply)
        result = reply["result"]
        self.assertNotIn("isError", result, result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def assertUnchanged(self, before: TreeState, after: TreeState, what: str = "the drive") -> None:
        self.assertEqual(tree_changes(before, after), ([], [], []), f"{what} was changed")


class TestCliPreviewsWriteNothing(_Machine):
    def test_every_cli_preview_leaves_the_drive_byte_for_byte_as_it_was(self) -> None:
        for label, argv, codes in CLI_PREVIEWS:
            with self.subTest(command=label):
                drive = self.new_drive()
                before = snapshot_tree(drive)
                self.run_cli(*argv(drive), codes=codes)
                self.assertUnchanged(before, snapshot_tree(drive))

    def test_the_drive_gives_the_previews_something_to_preview(self) -> None:
        """So that "nothing changed" is not just "nothing happened": each kind of preview has work to show."""
        drive = self.new_drive()
        root = str(drive)
        junk = json.loads(self.run_cli("clean", "--root", root, "--tier", "3", "--json"))["junk_items"]
        self.assertEqual({item["tier"] for item in junk}, {1, 2, 3})
        plan = json.loads(self.run_cli("organize", "--root", root, "--dry-run", "--json"))["planned_actions"]
        self.assertGreaterEqual(len(plan), 3)
        self.assertTrue(json.loads(self.run_cli("dup", "--root", root, "--json"))["duplicate_group_count"])
        self.assertTrue(json.loads(self.run_cli("classify", root, "--root", root, "--dry-run", "--json"))["actions"])
        health = json.loads(self.run_cli("sentinel", "--root", root, "--no-heal", "--json", codes=(0, 1)))
        self.assertEqual(health["git_status"]["repos_checked"], 1)
        audited = json.loads(self.run_cli("gitignore", "audit", "--root", root, "--json"))
        self.assertEqual([Path(entry["repo"]).name for entry in audited], ["tools_repo"])

    def test_undo_previews_a_journaled_batch_and_leaves_it_where_it_is(self) -> None:
        drive = self.new_drive()
        moved = drive / "03_Development_Projects" / "my_service"
        moved.parent.mkdir()
        (drive / "my_service").rename(moved)
        record_moves(str(drive), [(str(drive / "my_service"), str(moved))], "organize")
        for flags in ([], ["--dry-run", "--json"]):
            with self.subTest(flags=flags):
                before = snapshot_tree(drive)
                out = self.run_cli("undo", "--root", str(drive), *flags)
                self.assertUnchanged(before, snapshot_tree(drive))
                if "--json" in flags:  # (it did preview something: this is not "nothing to undo")
                    self.assertEqual(json.loads(out)["planned_count"], 1)

    def test_relink_previews_the_agent_paths_it_would_update_and_changes_nothing(self) -> None:
        drive = self.new_drive()
        old = drive / "old_place" / "my_service"
        new = drive / "03_Development_Projects" / "my_service"
        new.mkdir(parents=True)  # `relink OLD NEW` wants the new folder to be there
        remembered = {"projects": {str(old): {"hasTrustDialogAccepted": True}}}
        (self.fake_home / ".claude.json").write_text(json.dumps(remembered), encoding="utf-8")
        for flags in ([], ["--json"]):
            with self.subTest(flags=flags):
                before_drive, before_home = snapshot_tree(drive), snapshot_tree(self.fake_home)
                out = self.run_cli("relink", str(old), str(new), "--root", str(drive), "--dry-run", *flags)
                self.assertUnchanged(before_drive, snapshot_tree(drive))
                self.assertUnchanged(before_home, snapshot_tree(self.fake_home), "the coding agent's configuration")
                if "--json" in flags:  # (it did preview something)
                    self.assertEqual([entry["agent"] for entry in json.loads(out)["updated"]], ["Claude Code"])

    def test_backup_dry_run_changes_neither_the_drive_nor_the_folder_it_would_copy_to(self) -> None:
        for target_there in (True, False):
            with self.subTest(target_there=target_there):
                drive = self.new_drive()
                (drive / "02_Learning_Knowledge" / "notes").mkdir(parents=True)  # a partition, so there is something to copy
                (drive / "02_Learning_Knowledge" / "notes" / "lecture.md").write_text("# lecture\n", encoding="utf-8")
                target = self.test_dir / f"backup_target{self.drives}"
                if target_there:
                    target.mkdir()
                before = snapshot_tree(drive)
                before_target = snapshot_tree(target) if target_there else None
                out = self.run_cli("backup", "--root", str(drive), "--target", str(target), "--dry-run", "--json")
                self.assertEqual(json.loads(out)["copied_count"], 1)  # (it did preview something)
                self.assertUnchanged(before, snapshot_tree(drive))
                if before_target is None:
                    self.assertFalse(target.exists(), "a dry run made the folder it would have copied to")
                else:
                    self.assertUnchanged(before_target, snapshot_tree(target), "the folder the backup would copy to")

    @unittest.skipUnless(shutil.which("git"), "`repos` needs git on PATH")
    def test_repos_leaves_the_drive_alone(self) -> None:
        for flags in ([], ["--json"]):
            with self.subTest(flags=flags):
                drive = self.new_drive()
                before = snapshot_tree(drive)
                # The folder to scan is named, so that the person's own home folder is not walked as well
                self.run_cli("repos", str(drive), "--root", str(drive), *flags)
                self.assertUnchanged(before, snapshot_tree(drive))

    def test_commands_that_open_the_index_leave_it_and_the_drive_alone(self) -> None:
        drive = self.new_drive()
        self.run_cli("index", "--root", str(drive))  # builds `.smart_drive/index.db`: not a preview
        self.assertIn(".smart_drive/index.db", snapshot_tree(drive))
        for argv, codes in (
            (["search", "notes", "--root", str(drive), "--json"], (0,)),
            (["search", "ext:csv", "--root", str(drive)], (0,)),
            (["status", "--root", str(drive), "--json"], (0,)),
            (["sentinel", "--root", str(drive), "--no-heal", "--json"], (0, 1)),
            (["audit", "--root", str(drive), "--json"], (0,)),
        ):
            with self.subTest(command=" ".join(argv[:2])):
                before = snapshot_tree(drive)
                out = self.run_cli(*argv, codes=codes)
                self.assertUnchanged(before, snapshot_tree(drive))
                if argv[:2] == ["search", "notes"]:  # (it did search the index)
                    self.assertEqual([hit["path"] for hit in json.loads(out)], ["notes.txt"])


class TestMcpPreviewsWriteNothing(_Machine):
    def test_every_mcp_tool_that_previews_leaves_the_drive_byte_for_byte_as_it_was(self) -> None:
        for name, arguments in MCP_PREVIEWS:
            with self.subTest(tool=name, arguments=arguments):
                drive = self.new_drive()
                before = snapshot_tree(drive)
                self.call_tool(drive, name, **arguments)
                self.assertUnchanged(before, snapshot_tree(drive))

    def test_the_previews_have_work_to_show(self) -> None:
        drive = self.new_drive()
        plan = self.call_tool(drive, "ssd_auto_organize", apply=False)
        self.assertEqual(plan["status"], "dry_run")
        self.assertTrue(plan["actions"])
        self.assertTrue(self.call_tool(drive, "ssd_clean", dry_run=True, tier=3)["detected_count"])
        self.assertTrue(self.call_tool(drive, "ssd_find_duplicates")["duplicate_group_count"])

    def test_searching_and_checking_an_existing_index_leaves_it_and_the_drive_alone(self) -> None:
        drive = self.new_drive()
        self.call_tool(drive, "ssd_update_index")  # builds `.smart_drive/index.db`: not a preview
        before = snapshot_tree(drive)
        self.assertIn(".smart_drive/index.db", before)
        found = self.call_tool(drive, "ssd_search", query="notes")
        self.assertEqual([hit["path"] for hit in found["matches"]], ["notes.txt"])
        self.call_tool(drive, "ssd_status")
        self.assertUnchanged(before, snapshot_tree(drive))


class TestGuidedOrganizerWritesNothingBeforeYes(_Machine):
    """`Smart_Organize.*`: the preview, the busy-apps wait and the self-test change nothing; YES is where it starts."""

    BUSY = {"busy_apps": ["Cursor"], "busy_apps_unknown": False, "changed": []}

    def guided(
        self,
        drive: Path,
        answers: Sequence[str],
        lang: str = "en",
        selftest: bool = False,
        busy: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, str, List[str]]:
        """Runs the guided flow; its answers are typed in turn, and then stdin is closed.

        `busy` is what the dry-run relink reports about the open coding agents (the real relink answers without it).
        Returns (exit code, output, prompts asked).
        """
        prompts: List[str] = []
        pending = iter(answers)

        def input_fn(prompt: str) -> str:
            prompts.append(prompt)
            try:
                return next(pending)
            except StopIteration:
                raise EOFError from None

        def preview(root: str, pairs: Any, tool: str, dry_run: bool = False) -> Dict[str, Any]:
            return busy or {}

        out = io.StringIO()
        rc = run_smart_organize(
            str(drive),
            lang=lang,
            selftest=selftest,
            input_fn=input_fn,
            output_stream=out,
            after_moves_fn=preview if busy else None,
        )
        return rc, out.getvalue(), prompts

    def test_a_run_that_ends_before_the_moves_leaves_the_drive_exactly_as_it_was(self) -> None:
        cases = [
            ("nothing to move", dict(answers=[]), False),
            ("anything but YES", dict(answers=["no"]), True),
            ("YES spelled differently", dict(answers=["yes please"]), True),
            ("stdin closed at the YES prompt", dict(answers=[]), True),
            ("Q while apps are open", dict(answers=["q"], busy=self.BUSY), True),
            ("stdin closed while apps are open", dict(answers=[], busy=self.BUSY), True),
            ("the self-test", dict(answers=[], selftest=True), True),
            ("the self-test, in Vietnamese", dict(answers=[], selftest=True, lang="vi"), True),
        ]
        for label, options, loose in cases:
            with self.subTest(run=label):
                drive = self.new_drive(loose=loose)
                before = snapshot_tree(drive)
                rc, out, prompts = self.guided(drive, **options)
                self.assertEqual(rc, 0, out)
                self.assertUnchanged(before, snapshot_tree(drive))
                self.assertFalse((drive / ".smart_drive").exists())
                if not loose:
                    self.assertEqual(snapshot_tree(drive), {})
                    self.assertIn(t("no_planned_moves", "en"), out)
                    self.assertEqual(prompts, [], "there is nothing to ask when nothing moves")

    def test_each_of_those_runs_got_as_far_as_the_step_it_stops_at(self) -> None:
        """So that "nothing was written" does not just mean "it stopped early"."""
        drive = self.new_drive()
        planned = json.loads(self.run_cli("organize", "--root", str(drive), "--dry-run", "--json"))["planned_actions"]
        rc, out, prompts = self.guided(drive, ["no"])
        self.assertEqual(prompts, [t("step3_confirm_prompt", "en", count=len(planned))])
        self.assertIn(t("cancelled", "en"), out)
        rc, out, prompts = self.guided(drive, ["q"], busy=self.BUSY)
        self.assertEqual(prompts, [t("step2_busy_prompt", "en")])
        self.assertIn(t("step2_busy_title", "en", apps="Cursor"), out)
        rc, out, prompts = self.guided(drive, [], selftest=True)
        self.assertEqual(prompts, [])
        self.assertIn("[SELFTEST OK]", out)
        for action in planned:
            self.assertIn(Path(action["src_path"]).name, out)  # the preview listed what it would move

    def test_the_banner_says_the_log_is_written_once_the_moves_are_confirmed(self) -> None:
        drive = self.new_drive(loose=False)
        for lang in ("en", "vi"):
            with self.subTest(lang=lang):
                rc, out, prompts = self.guided(drive, [], lang=lang)
                self.assertIn("  " + t("log_path", lang, log_file=get_log_path(str(drive))), out.splitlines())
        self.assertIn("once you confirm the moves", t("log_path", "en", log_file="LOG"))
        self.assertIn("xác nhận", t("log_path", "vi", log_file="LOG"))

    def test_yes_is_where_the_log_starts_and_the_preview_is_in_it_by_then(self) -> None:
        drive = self.new_drive()
        at_apply: List[str] = []

        def apply(args: Any) -> int:  # stands in for `organize --apply`, and reads the log as the moves would begin
            log_file = Path(get_log_path(str(drive)))
            at_apply.append(log_file.read_text(encoding="utf-8") if log_file.is_file() else "")
            return 0

        with patch.object(CMD_ORGANIZE, "cmd_organize", apply):
            rc, out, prompts = self.guided(drive, ["YES", ""])
        self.assertEqual(rc, 0, out)
        self.assertEqual(organize_log_events(at_apply[0]), ["START", "DRY_RUN"])

    def test_a_confirmed_run_writes_the_whole_log_in_the_order_things_happened(self) -> None:
        drive = self.new_drive()
        rc, out, prompts = self.guided(drive, ["YES", ""])
        self.assertEqual(rc, 0, out)
        self.assertTrue((drive / "03_Development_Projects" / "my_service").is_dir())
        lines = Path(get_log_path(str(drive))).read_text(encoding="utf-8").splitlines()
        events = organize_log_events("\n".join(lines))
        self.assertEqual(events[:2], ["START", "DRY_RUN"])
        self.assertEqual(events[-4:], ["APPLY", "VERIFY", "ACTION", "END"])
        for line in lines:
            self.assertRegex(line, r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\] [A-Z_]+( \| .*)?$")
        stamps = [line[1:21] for line in lines]
        self.assertEqual(stamps, sorted(stamps))


class TestOrganizeLog(SmartDriveTestCase):
    def test_lines_wait_in_memory_until_flush_and_keep_the_time_of_their_event(self) -> None:
        root = self.test_dir / "drive"
        root.mkdir()
        stamps = iter(["2026-10-21T10:00:01Z", "2026-10-21T10:00:02Z", "2026-10-21T10:30:00Z", "2026-10-21T10:30:05Z"])
        with patch.object(RUNNER, "_utc_timestamp", side_effect=lambda: next(stamps)):
            log = OrganizeLog(str(root))
            log.add("START", "lang=en selftest=False root=X")
            log.add("DRY_RUN")
            self.assertEqual(snapshot_tree(root), {}, "nothing reaches the drive before flush()")

            log.flush()  # the person typed YES: the lines kept so far go in, in order, stamped when they happened
            log_file = Path(log.path)
            self.assertEqual(
                log_file.read_text(encoding="utf-8").splitlines(),
                ["[2026-10-21T10:00:01Z] START | lang=en selftest=False root=X", "[2026-10-21T10:00:02Z] DRY_RUN"],
            )

            log.add("APPLY", "moved=1")  # from then on an event is written as it happens
            self.assertEqual(
                log_file.read_text(encoding="utf-8").splitlines()[2:], ["[2026-10-21T10:30:00Z] APPLY | moved=1"]
            )

            log.flush()  # a second flush writes nothing again
            log.add("END", "kept")
        self.assertEqual(len(log_file.read_text(encoding="utf-8").splitlines()), 4)

    def test_a_log_that_is_never_flushed_leaves_nothing_on_the_drive(self) -> None:
        root = self.test_dir / "drive"
        root.mkdir()
        log = OrganizeLog(str(root))
        for event in ("START", "DRY_RUN", "CANCELLED_CONFIRM", "END"):
            log.add(event, "details")
        self.assertEqual(snapshot_tree(root), {})

    def test_the_log_file_and_its_line_format_are_what_they_were(self) -> None:
        root = self.test_dir / "drive"
        root.mkdir()
        self.assertEqual(OrganizeLog(str(root)).path, get_log_path(str(root)))
        self.assertEqual(get_log_path(str(root)), str(root / ".smart_drive" / "smart-organize.log"))
        self.assertEqual(append_organize_log(str(root), "END", "kept"), get_log_path(str(root)))
        append_organize_log(str(root), "DRY_RUN")
        first, second = Path(get_log_path(str(root))).read_text(encoding="utf-8").splitlines()
        self.assertRegex(first, r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\] END \| kept$")
        self.assertRegex(second, r"^\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\] DRY_RUN$")

    def test_a_log_that_cannot_be_written_does_not_stop_the_run(self) -> None:
        root = self.test_dir / "drive"
        root.mkdir()
        (root / ".smart_drive").write_text("a file where the folder should be", encoding="utf-8")
        log = OrganizeLog(str(root))
        log.add("START")
        log.flush()
        log.add("END")  # no exception: the log is a convenience


class TestTheDocsSayWhatTheOrganizerWrites(unittest.TestCase):
    def test_the_launchers_say_the_selftest_writes_nothing_and_the_log_waits_for_the_moves(self) -> None:
        for folder in (ROOT, ROOT / "launchers"):
            for extension in ("bat", "ps1", "command", "sh"):
                path = folder / f"Smart_Organize.{extension}"
                with self.subTest(launcher=path.relative_to(ROOT).as_posix()):
                    text = path.read_text(encoding="utf-8")
                    self.assertIn("SD_SELFTEST=1 (writes nothing)", text)
                    self.assertIn("once the moves are confirmed", text)
                    self.assertNotIn("read-only SD_SELFTEST", text)

    def test_both_readmes_say_the_preview_writes_nothing_and_when_the_log_is_written(self) -> None:
        for name in ("README.md", "README_VN.md"):
            with self.subTest(readme=name):
                bullets = [
                    line
                    for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
                    if line.startswith("- `Smart_Organize.*`:")
                ]
                self.assertEqual(len(bullets), 1)
                self.assertIn(".smart_drive/smart-organize.log", bullets[0])
                self.assertIn("`YES`", bullets[0])


if __name__ == "__main__":
    unittest.main()
