"""tests/test_mcp_setup_run.py - `smart-drive setup` end to end on a scripted machine.

Hermetic by design: no network, no real node / git / claude, and a throw-away home directory.
The process layer (`bootstrap.Host.run`) is replaced by the scripted fake in
tests/mcp_setup_helpers.py. Unit-level tests live in tests/test_mcp_setup.py.

100% Python Standard Library unittest.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.cli import cmd_setup
from smart_drive.cli.main import main
from smart_drive.core import initializer
from smart_drive.mcp import bootstrap
from smart_drive.mcp.ecosystem import CONTROL_PLANE, MEMBERS, SELF_NAME
from tests.mcp_setup_helpers import MEMBER_NAMES, FakeRun, TempRootCase, make_host, touch


class TestRunSetup(TempRootCase):
    def host(self, run: FakeRun, **kwargs: Any) -> bootstrap.Host:
        host = make_host(self.root, run, **kwargs)
        touch(self.home / ".claude.json", "{}")  # Claude Code is installed ...
        (self.home / ".cursor").mkdir(exist_ok=True)  # ... and so is Cursor
        return host

    def options(self, **kwargs: Any) -> bootstrap.Options:
        return bootstrap.Options(dest=self.dest, **kwargs)

    def manifest(self) -> Dict[str, Any]:
        return json.loads(self.manifest_file.read_text(encoding="utf-8"))

    def test_fresh_machine_clones_registers_and_verifies(self) -> None:
        run = FakeRun()
        report = bootstrap.run_setup(self.options(), host=self.host(run))

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(bootstrap.exit_code(report), 0)

        clones = [(argv, env) for argv, env in run.calls if "clone" in argv[1:4]]
        self.assertEqual(len(clones), len(MEMBERS))
        for argv, env in clones:
            self.assertEqual(argv[1:3], ["-c", "core.fsmonitor=false"])  # never starts a git fsmonitor daemon
            self.assertEqual(argv[3:7], ["clone", "--depth", "1", "--quiet"])
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

        servers = self.manifest()["servers"]
        self.assertEqual([s["name"] for s in servers], MEMBER_NAMES + [SELF_NAME])
        node = (self.root / "bin" / "node").as_posix()
        for server in servers[: len(MEMBERS)]:
            self.assertEqual(server["command"], node)
            self.assertNotIn("env", server)
            self.assertTrue(Path(server["args"][0]).is_file(), server["args"])

        register = run.matching("register")
        self.assertEqual(len(register), 1)
        argv = register[0]
        self.assertEqual(set(argv[3:-2]), set(MEMBER_NAMES) | {SELF_NAME})
        self.assertEqual(argv[-2:], ["--agents=claude,cursor", "--json"])
        self.assertNotIn("--dry-run", argv)

        health = run.matching("health")
        self.assertEqual(len(health), 1)
        self.assertEqual({n for n in health[0][3:] if not n.startswith("--")}, set(MEMBER_NAMES))

    def test_second_run_changes_nothing(self) -> None:
        run = FakeRun()
        host = self.host(run)
        bootstrap.run_setup(self.options(), host=host)
        before = self.manifest_file.read_bytes()
        clones_before = len(run.matching("clone"))

        report = bootstrap.run_setup(self.options(), host=host)

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(len(run.matching("clone")), clones_before)
        self.assertEqual(self.manifest_file.read_bytes(), before)
        self.assertFalse(Path(str(self.manifest_file) + ".bak.constellation").exists())
        self.assertEqual({m["status"] for m in report["members"]}, {"ok"})
        self.assertEqual(report["manifest"]["added"], [])
        self.assertEqual(report["next_steps"], [])

    def test_dry_run_changes_nothing(self) -> None:
        run = FakeRun()
        report = bootstrap.run_setup(self.options(dry_run=True), host=self.host(run))
        self.assertEqual(run.matching("clone"), [])
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.manifest_file.exists())
        self.assertTrue(report["needs_setup"])
        self.assertEqual(bootstrap.exit_code(report), 0)
        self.assertEqual({m["status"] for m in report["members"]}, {"missing"})

    def test_a_relative_destination_is_made_absolute(self) -> None:
        options = bootstrap.Options(dest=Path("relative_mcp"), dry_run=True)
        report = bootstrap.run_setup(options, host=self.host(FakeRun()))
        self.assertTrue(Path(report["dest"]).is_absolute(), report["dest"])

    def test_check_reports_pending_work_with_exit_code_3(self) -> None:
        run = FakeRun()
        report = bootstrap.run_setup(self.options(check=True), host=self.host(run))
        self.assertEqual(bootstrap.exit_code(report), 3)
        self.assertFalse(report["ok"])
        self.assertFalse(self.manifest_file.exists())
        self.assertEqual(run.matching("clone"), [])

    def test_check_is_clean_after_a_real_setup(self) -> None:
        run = FakeRun()
        host = self.host(run)
        bootstrap.run_setup(self.options(), host=host)
        report = bootstrap.run_setup(self.options(check=True), host=host)
        self.assertFalse(report["needs_setup"], report["pending"])
        self.assertEqual(bootstrap.exit_code(report), 0)
        # the check asked Constellation for a dry run, never for a real registration
        self.assertIn("--dry-run", run.matching("register")[-1])

    def test_pending_registration_changes_fail_the_check(self) -> None:
        run = FakeRun()
        host = self.host(run)
        bootstrap.run_setup(self.options(), host=host)
        run.register_diffs = [
            {"agent": "Cursor", "file": "mcp.json", "action": "create", "changes": ["+ polaris"]}
        ]
        report = bootstrap.run_setup(self.options(check=True), host=host)
        self.assertEqual(bootstrap.exit_code(report), 3)
        self.assertTrue(any(p.startswith("agents:") for p in report["pending"]), report["pending"])

    def test_a_failed_clone_does_not_stop_the_others(self) -> None:
        run = FakeRun(clone_fails={"vega-mcp"})
        report = bootstrap.run_setup(self.options(), host=self.host(run))
        status = {m["name"]: m["status"] for m in report["members"]}
        self.assertEqual(status["vega"], "failed")
        self.assertEqual({v for k, v in status.items() if k != "vega"}, {"installed"})
        self.assertEqual(bootstrap.exit_code(report), 1)
        self.assertNotIn("vega", [s["name"] for s in self.manifest()["servers"]])
        self.assertNotIn("vega", run.matching("register")[0])

    def test_missing_git_is_a_prerequisite_failure(self) -> None:
        run = FakeRun(git_ok=False)
        report = bootstrap.run_setup(self.options(), host=self.host(run))
        self.assertTrue(report["blocked"])
        self.assertEqual(bootstrap.exit_code(report), 2)
        self.assertEqual(run.matching("clone"), [])

    def test_git_is_not_needed_when_everything_is_already_on_disk(self) -> None:
        for member in MEMBERS:
            touch(self.dest / member.repo_name / member.entry, "// bundle\n")
        run = FakeRun(git_ok=False)
        report = bootstrap.run_setup(self.options(), host=self.host(run))
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual({m["status"] for m in report["members"]}, {"found"})

    def test_missing_node_blocks_everything(self) -> None:
        run = FakeRun()
        host = self.host(run, on_path=("git",))
        with patch.object(bootstrap, "POSIX_NODE_PATHS", ()):
            report = bootstrap.run_setup(self.options(), host=host)
        self.assertEqual(bootstrap.exit_code(report), 2)
        self.assertEqual(run.matching("clone"), [])
        self.assertEqual(run.matching("register"), [])
        self.assertTrue(any("Node" in step for step in report["next_steps"]), report["next_steps"])

    def test_no_clone_never_runs_git_clone(self) -> None:
        run = FakeRun()
        report = bootstrap.run_setup(self.options(no_clone=True), host=self.host(run))
        self.assertEqual(run.matching("clone"), [])
        self.assertEqual({m["status"] for m in report["members"]}, {"missing"})
        self.assertEqual(bootstrap.exit_code(report), 1)
        self.assertEqual(run.matching("register"), [])

    def test_existing_registrations_are_reused_and_never_cloned_twice(self) -> None:
        run = FakeRun()
        host = self.host(run)
        bundle = touch(self.root / "mine" / "polaris_mcp" / "dist" / "polaris.mjs", "// bundle\n")
        touch(
            self.home / ".claude.json",
            json.dumps({"mcpServers": {"polaris": {"command": "node", "args": [str(bundle)]}}}),
        )
        report = bootstrap.run_setup(self.options(), host=host)
        status = {m["name"]: m for m in report["members"]}
        self.assertEqual(status["polaris"]["status"], "adopted")
        self.assertEqual(Path(status["polaris"]["path"]), bundle.parent.parent)
        cloned = [argv[-2].rsplit("/", 1)[-1] for argv in run.matching("clone")]
        self.assertNotIn("polaris-mcp", cloned)
        entry = next(s for s in self.manifest()["servers"] if s["name"] == "polaris")
        self.assertEqual(entry["args"], [str(bundle)])

    def test_a_stale_manifest_entry_is_kept_unless_repair_is_given(self) -> None:
        gone = self.root / "gone" / "dist" / "polaris.mjs"
        foreign = {
            "name": "obsidian",
            "command": "node",
            "args": ["x.js"],
            "env": {"API_TOKEN": "${secret:API_TOKEN}"},
        }
        stale = {"name": "polaris", "command": "node", "args": [str(gone)]}
        touch(self.manifest_file, json.dumps({"version": "1", "servers": [foreign, stale]}))
        run = FakeRun()
        host = self.host(run)

        first = bootstrap.run_setup(self.options(), host=host)
        self.assertEqual({m["name"]: m["status"] for m in first["members"]}["polaris"], "stale")
        self.assertEqual(bootstrap.exit_code(first), 1)
        servers = {s["name"]: s for s in self.manifest()["servers"]}
        self.assertEqual(servers["polaris"], stale)
        self.assertEqual(servers["obsidian"], foreign)

        second = bootstrap.run_setup(self.options(repair=True), host=host)
        self.assertEqual({m["name"]: m["status"] for m in second["members"]}["polaris"], "installed")
        servers = {s["name"]: s for s in self.manifest()["servers"]}
        expected = (self.dest / "polaris-mcp" / "dist" / "polaris.mjs").as_posix()
        self.assertEqual(servers["polaris"]["args"], [expected])
        self.assertEqual(servers["obsidian"], foreign)
        self.assertTrue(Path(str(self.manifest_file) + ".bak.constellation").is_file())

    def test_foreign_manifest_entries_survive_untouched(self) -> None:
        foreign = {
            "name": "obsidian",
            "command": "node",
            "args": ["vault.js"],
            "env": {"API_TOKEN": "${secret:API_TOKEN}"},
        }
        touch(self.manifest_file, json.dumps({"version": "1", "servers": [foreign]}, indent=2))
        run = FakeRun()
        bootstrap.run_setup(self.options(), host=self.host(run))
        servers = self.manifest()["servers"]
        self.assertEqual(servers[0], foreign)
        self.assertEqual(len(servers), 1 + len(MEMBERS) + 1)
        # Constellation was never asked to touch it
        self.assertNotIn("obsidian", run.matching("register")[0])

    def test_macos_without_node_on_path_registers_the_absolute_homebrew_path(self) -> None:
        brew = touch(self.root / "opt" / "homebrew" / "bin" / "node")
        run = FakeRun()
        host = self.host(run, system="macos", on_path=("git",))
        with patch.object(bootstrap, "POSIX_NODE_PATHS", (str(brew),)):
            report = bootstrap.run_setup(self.options(), host=host)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(self.manifest()["servers"][0]["command"], brew.as_posix())
        # health ran the very binary that the agents will be told to start
        self.assertEqual(run.matching("health")[0][0], str(brew))

    def test_agents_flag_limits_the_registration_targets(self) -> None:
        run = FakeRun()
        bootstrap.run_setup(self.options(agents=["cursor"]), host=self.host(run))
        self.assertIn("--agents=cursor", run.matching("register")[0])

    def test_no_detected_agent_skips_registration(self) -> None:
        run = FakeRun()
        host = make_host(self.root, run)  # neither ~/.claude.json nor ~/.cursor exist
        report = bootstrap.run_setup(self.options(), host=host)
        self.assertEqual(run.matching("register"), [])
        self.assertIn("agent", report["registration"]["skipped"])
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(any("--agents" in step for step in report["next_steps"]), report["next_steps"])

    def test_only_limits_the_servers_but_still_installs_the_control_plane(self) -> None:
        run = FakeRun()
        report = bootstrap.run_setup(self.options(only=["polaris"]), host=self.host(run))
        cloned = sorted(argv[-2].rsplit("/", 1)[-1] for argv in run.matching("clone"))
        self.assertEqual(cloned, ["constellation-mcp", "polaris-mcp"])
        self.assertEqual(run.matching("register")[0][3:-2], ["polaris", SELF_NAME])
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual([s["name"] for s in self.manifest()["servers"]], ["polaris", SELF_NAME])

    def test_unknown_server_or_agent_is_rejected(self) -> None:
        host = self.host(FakeRun())
        with self.assertRaises(ValueError):
            bootstrap.run_setup(self.options(only=["nope"]), host=host)
        with self.assertRaises(ValueError):
            bootstrap.run_setup(self.options(agents=["emacs"]), host=host)

    def test_an_unreadable_manifest_is_never_overwritten(self) -> None:
        touch(self.manifest_file, "{ this is not json")
        run = FakeRun()
        report = bootstrap.run_setup(self.options(), host=self.host(run))
        self.assertEqual(self.manifest_file.read_text(encoding="utf-8"), "{ this is not json")
        self.assertEqual(run.matching("register"), [])
        self.assertEqual(bootstrap.exit_code(report), 1)

    def test_a_server_that_does_not_answer_fails_the_setup(self) -> None:
        run = FakeRun(health_fails={"orion"})
        report = bootstrap.run_setup(self.options(), host=self.host(run))
        self.assertEqual(bootstrap.exit_code(report), 1)
        self.assertTrue(any(e.startswith("orion:") for e in report["errors"]), report["errors"])

    def test_no_health_skips_the_stdio_check(self) -> None:
        run = FakeRun()
        report = bootstrap.run_setup(self.options(no_health=True), host=self.host(run))
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(run.matching("health"), [])

    def test_report_is_json_serialisable_and_renders_in_every_mode(self) -> None:
        for flags in ({"check": True}, {"dry_run": True}, {}):
            report = bootstrap.run_setup(self.options(**flags), host=self.host(FakeRun()))
            json.dumps(report)
            self.assertIn("SmartDrive MCP ecosystem setup", bootstrap.format_report(report))


class TestCli(TempRootCase):
    """The CLI plans first and asks before it installs. pytest has no terminal, so a run is that of a coding
    agent unless ``answers`` is given: then a person at a terminal types those answers."""

    def run_cli(
        self,
        argv: List[str],
        run: FakeRun,
        answers: Optional[List[str]] = None,
        **host_kwargs: Any,
    ) -> Tuple[int, str, str]:
        host = make_host(self.root, run, **host_kwargs)
        touch(self.home / ".claude.json", "{}")
        self.prompts: List[str] = []
        typed = list(answers or [])

        def ask(prompt: str) -> str:
            self.prompts.append(prompt)
            return typed.pop(0) if typed else ""

        out, err = io.StringIO(), io.StringIO()
        with patch.object(bootstrap, "default_host", return_value=host):
            with patch.object(cmd_setup, "_is_interactive", return_value=answers is not None):
                with patch.object(cmd_setup, "_ask", side_effect=ask):
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def apply(self, *flags: str) -> List[str]:
        return ["setup", *flags, "--dest", str(self.dest)]

    def cloned(self, run: FakeRun) -> List[str]:
        return sorted(argv[-2].rsplit("/", 1)[-1] for argv in run.matching("clone"))

    def registered(self, run: FakeRun) -> List[str]:
        """Servers named by the last real (not dry-run) registration."""
        real = [argv for argv in run.matching("register") if "--dry-run" not in argv]
        return real[-1][3:-2]

    def assert_untouched(self, run: FakeRun) -> None:
        self.assertEqual(run.matching("clone"), [])
        self.assertEqual(run.matching("register"), [])
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.manifest_file.exists())

    def test_check_json_reports_pending_work(self) -> None:
        code, out, _err = self.run_cli(["setup", "--check", "--json", "--dest", str(self.dest)], FakeRun())
        self.assertEqual(code, 3)
        report = json.loads(out)
        self.assertEqual(report["mode"], "check")
        self.assertTrue(report["needs_setup"])
        self.assertEqual(report["members"][0]["name"], CONTROL_PLANE)
        self.assertNotIn("needs_consent", report)

    def test_dry_run_never_asks(self) -> None:
        run = FakeRun()
        code, out, _err = self.run_cli(self.apply("--dry-run"), run, answers=["a"])
        self.assertEqual(code, 0)
        self.assertEqual(self.prompts, [])
        self.assertIn("[dry-run]", out)
        self.assert_untouched(run)

    def test_apply_accepts_yes_and_streams_progress_to_stderr(self) -> None:
        run = FakeRun()
        code, out, err = self.run_cli(self.apply("--yes"), run)
        self.assertEqual(code, 0, out + err)
        self.assertIn("git clone", err)
        self.assertIn("SmartDrive MCP ecosystem setup", out)
        self.assertEqual(len(run.matching("clone")), len(MEMBERS))

    def test_unknown_server_exits_with_2(self) -> None:
        code, _out, err = self.run_cli(["setup", "--only", "nope"], FakeRun())
        self.assertEqual(code, 2)
        self.assertIn("unknown server", err)

    # -- a coding agent: no terminal, no --yes ---------------------------------------------------

    def test_without_a_terminal_and_without_yes_nothing_is_installed(self) -> None:
        run = FakeRun()
        code, out, err = self.run_cli(self.apply("--json"), run)
        self.assertEqual(code, 4, out + err)
        self.assert_untouched(run)
        self.assertEqual(self.prompts, [])
        self.assertEqual(err, "")

        report = json.loads(out)
        self.assertTrue(report["needs_consent"])
        self.assertFalse(report["ok"])
        self.assertEqual([item["name"] for item in report["consent"]], MEMBER_NAMES)
        for item in report["consent"]:
            self.assertEqual(item["action"], "download and register")
            self.assertTrue(item["repo"].startswith("https://github.com/"), item)
            self.assertTrue(item["description"], item)
            self.assertEqual(item["status"], "missing")
            self.assertEqual(item["agents"], ["Claude Code"])
        self.assertEqual([i["name"] for i in report["consent"] if i["control_plane"]], [CONTROL_PLANE])
        self.assertEqual(report["next_steps"][0], bootstrap.CONSENT_STEP)
        self.assertIn("setup --only <names> --yes", report["next_steps"][0])
        self.assertIn("Never pass --yes without the user's answer", report["next_steps"][0])
        self.assertNotIn(bootstrap.APPLY_HINT, report["next_steps"])

    def test_text_output_for_an_agent_lists_every_server_and_the_instruction(self) -> None:
        run = FakeRun()
        code, out, _err = self.run_cli(self.apply(), run)
        self.assertEqual(code, 4)
        self.assert_untouched(run)
        self.assertIn("nothing was installed", out)
        self.assertIn("1. constellation", out)
        self.assertIn("(required by the other servers)", out)
        self.assertIn("2. polaris - Evidence-based", out)
        self.assertIn("download and register: https://github.com/DuongNAD/polaris-mcp", out)
        self.assertIn("Next      Ask the user which of these MCP servers to install", out)
        for name in MEMBER_NAMES:
            self.assertIn(name, out)

    def test_json_never_prompts_even_at_a_terminal(self) -> None:
        run = FakeRun()
        code, _out, _err = self.run_cli(self.apply("--json"), run, answers=["a"])
        self.assertEqual(code, 4)
        self.assertEqual(self.prompts, [])
        self.assert_untouched(run)

    def test_servers_already_on_disk_still_need_consent_to_be_registered(self) -> None:
        for member in MEMBERS:
            touch(self.dest / member.repo_name / member.entry, "// bundle\n")
        run = FakeRun()
        code, out, _err = self.run_cli(self.apply("--json"), run)
        self.assertEqual(code, 4)
        report = json.loads(out)
        self.assertEqual({item["action"] for item in report["consent"]}, {"register"})
        self.assertTrue(all("--dry-run" in argv for argv in run.matching("register")))
        self.assertFalse(self.manifest_file.exists())

    def test_no_clone_has_nothing_to_download_so_nothing_to_approve(self) -> None:
        run = FakeRun()
        code, _out, _err = self.run_cli(self.apply("--no-clone", "--json"), run)
        self.assertEqual(self.prompts, [])
        self.assertEqual(code, 1)  # as before: the servers are missing, and nothing was fetched
        self.assertEqual(run.matching("clone"), [])

    def test_missing_node_blocks_without_writing_anything(self) -> None:
        run = FakeRun()
        with patch.object(bootstrap, "POSIX_NODE_PATHS", ()):
            code, out, _err = self.run_cli(self.apply("--json"), run, answers=["a"], on_path=("git",))
        self.assertEqual(code, 2)
        self.assertEqual(self.prompts, [])
        self.assert_untouched(run)
        report = json.loads(out)
        self.assertTrue(report["blocked"])
        self.assertNotIn("needs_consent", report)
        self.assertTrue(any("Node" in step for step in report["next_steps"]), report["next_steps"])

    # -- --yes ----------------------------------------------------------------------------------

    def test_yes_skips_the_question_at_a_terminal(self) -> None:
        run = FakeRun()
        code, out, err = self.run_cli(self.apply("--yes"), run, answers=["n"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.prompts, [])
        self.assertEqual(len(run.matching("clone")), len(MEMBERS))

    def test_yes_with_only_installs_just_those_and_the_control_plane(self) -> None:
        run = FakeRun()
        code, out, err = self.run_cli(self.apply("--only", "polaris,vega", "--yes"), run)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.cloned(run), ["constellation-mcp", "polaris-mcp", "vega-mcp"])
        self.assertEqual(self.registered(run), ["polaris", "vega", SELF_NAME])

    # -- a person at a terminal -----------------------------------------------------------------

    def test_a_number_installs_that_server_with_the_control_plane(self) -> None:
        run = FakeRun()
        code, out, err = self.run_cli(self.apply(), run, answers=["2"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("[a]ll / [n]one", self.prompts[0])
        self.assertIn("1. constellation", out)
        self.assertIn("constellation is added too", out)
        self.assertEqual(self.cloned(run), ["constellation-mcp", "polaris-mcp"])
        self.assertEqual(self.registered(run), ["constellation", "polaris", SELF_NAME])
        names = [s["name"] for s in json.loads(self.manifest_file.read_text(encoding="utf-8"))["servers"]]
        self.assertEqual(names, ["constellation", "polaris", SELF_NAME])

    def test_names_pick_servers_too(self) -> None:
        run = FakeRun()
        code, out, err = self.run_cli(self.apply(), run, answers=["Vega, polaris"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.cloned(run), ["constellation-mcp", "polaris-mcp", "vega-mcp"])

    def test_all_installs_everything(self) -> None:
        for answer in ("a", "all", "yes"):
            with self.subTest(answer=answer):
                shutil.rmtree(self.dest, ignore_errors=True)
                shutil.rmtree(self.home / ".constellation", ignore_errors=True)
                run = FakeRun()
                code, out, err = self.run_cli(self.apply(), run, answers=[answer])
                self.assertEqual(code, 0, out + err)
                self.assertEqual(len(run.matching("clone")), len(MEMBERS))
                self.assertNotIn("is added too", out)

    def test_an_empty_answer_installs_nothing(self) -> None:
        for answer in ("", "n", "No", "none"):
            with self.subTest(answer=answer):
                run = FakeRun()
                code, out, err = self.run_cli(self.apply(), run, answers=[answer])
                self.assertEqual(code, 0, out + err)
                self.assertIn("Nothing was installed. Run `smart-drive setup` again any time.", out)
                self.assert_untouched(run)

    def test_end_of_input_installs_nothing(self) -> None:
        run = FakeRun()
        code, _out, _err = self.run_cli(self.apply(), run, answers=[])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.prompts), 1)
        self.assert_untouched(run)

    def test_an_unknown_name_is_rejected_and_asked_again(self) -> None:
        run = FakeRun()
        code, out, err = self.run_cli(self.apply(), run, answers=["banana", "9", "polaris"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.prompts), 3)
        self.assertIn("Not understood: banana", out)
        self.assertIn("Not understood: 9", out)
        self.assertEqual(self.cloned(run), ["constellation-mcp", "polaris-mcp"])

    def test_three_unclear_answers_count_as_none(self) -> None:
        run = FakeRun()
        code, out, _err = self.run_cli(self.apply(), run, answers=["x", "q", "z", "polaris"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.prompts), 3)
        self.assertIn("Nothing was installed", out)
        self.assert_untouched(run)

    def test_servers_that_are_already_set_up_are_not_asked_about_again(self) -> None:
        run = FakeRun()
        self.assertEqual(self.run_cli(self.apply(), run, answers=["polaris"])[0], 0)
        self.assertEqual(self.cloned(run), ["constellation-mcp", "polaris-mcp"])

        # later, without a terminal: constellation and polaris are done, the other five still need consent
        code, out, _err = self.run_cli(self.apply("--json"), run)
        self.assertEqual(code, 4)
        asked = [item["name"] for item in json.loads(out)["consent"]]
        self.assertEqual(asked, [n for n in MEMBER_NAMES if n not in ("constellation", "polaris")])

        code, out, err = self.run_cli(self.apply(), run, answers=["vega"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.cloned(run), ["constellation-mcp", "polaris-mcp", "vega-mcp"])
        self.assertNotIn("is added too", out)  # constellation is already installed
        self.assertEqual(self.registered(run), ["constellation", "polaris", "vega", SELF_NAME])

    # -- servers that are downloaded and in the manifest, but not yet in every coding agent ------------

    def downloaded(self, in_agents: Dict[str, List[str]]) -> FakeRun:
        """Every server is downloaded and in the manifest ("ok"); the agents hold only ``in_agents``."""
        (self.home / ".cursor").mkdir(parents=True, exist_ok=True)
        run = FakeRun()
        self.assertEqual(self.run_cli(self.apply("--yes"), run)[0], 0)
        run.agent_servers = {agent: set(names) | {SELF_NAME} for agent, names in in_agents.items()}
        run.calls.clear()
        return run

    def missing_polaris_and_vega(self) -> FakeRun:
        """Claude Code lacks polaris; Cursor lacks polaris and vega."""
        return self.downloaded(
            {
                "claude": [n for n in MEMBER_NAMES if n != "polaris"],
                "cursor": [n for n in MEMBER_NAMES if n not in ("polaris", "vega")],
            }
        )

    def assert_nothing_registered(self, run: FakeRun, before: bytes) -> None:
        self.assertTrue(all("--dry-run" in argv for argv in run.matching("register")))
        self.assertNotIn("polaris", run.agent_servers["claude"])
        self.assertNotIn("polaris", run.agent_servers["cursor"])
        self.assertNotIn("vega", run.agent_servers["cursor"])
        self.assertEqual(self.manifest_file.read_bytes(), before)

    def test_ok_servers_that_would_be_added_to_an_agent_need_consent(self) -> None:
        run = self.missing_polaris_and_vega()
        before = self.manifest_file.read_bytes()

        code, out, err = self.run_cli(self.apply("--json"), run)

        self.assertEqual(code, 4, out + err)
        self.assertEqual(self.prompts, [])
        self.assertEqual(err, "")
        self.assert_nothing_registered(run, before)
        report = json.loads(out)
        self.assertTrue(report["needs_consent"])
        self.assertFalse(report["ok"])
        consent = {item["name"]: item for item in report["consent"]}
        self.assertEqual(list(consent), ["polaris", "vega"])
        self.assertEqual(consent["polaris"]["agents"], ["Claude Code", "Cursor IDE"])
        self.assertEqual(consent["vega"]["agents"], ["Cursor IDE"])
        for item in consent.values():
            self.assertEqual((item["status"], item["action"]), ("ok", "register"))
            self.assertFalse(item["control_plane"])
        self.assertEqual(report["registration"]["added"], {n: i["agents"] for n, i in consent.items()})
        self.assertEqual(report["next_steps"][0], bootstrap.CONSENT_STEP)

    def test_the_text_output_says_which_agents_would_get_the_server(self) -> None:
        run = self.missing_polaris_and_vega()
        code, out, _err = self.run_cli(self.apply(), run)
        self.assertEqual(code, 4)
        self.assertIn("1. polaris - Evidence-based", out)
        self.assertIn("2. vega - ", out)
        self.assertIn("already downloaded: https://github.com/DuongNAD/polaris-mcp", out)
        self.assertIn("add to: Claude Code, Cursor IDE", out)
        self.assertIn("add to: Cursor IDE", out)
        self.assertNotIn("download and register", out)
        self.assertIn("Next      Ask the user which of these MCP servers to install", out)

    def test_yes_with_only_registers_just_the_approved_ones(self) -> None:
        run = self.missing_polaris_and_vega()
        code, out, err = self.run_cli(self.apply("--only", "polaris", "--yes"), run)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.registered(run), ["polaris", SELF_NAME])
        self.assertIn("polaris", run.agent_servers["claude"])
        self.assertIn("polaris", run.agent_servers["cursor"])
        self.assertNotIn("vega", run.agent_servers["cursor"])

    def test_a_pick_at_the_terminal_registers_only_the_chosen_servers(self) -> None:
        run = self.missing_polaris_and_vega()
        code, out, err = self.run_cli(self.apply(), run, answers=["1"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.prompts), 1)
        self.assertIn("add to: Claude Code, Cursor IDE", out)
        self.assertEqual(run.matching("clone"), [])
        self.assertIn("polaris", self.registered(run))
        self.assertNotIn("vega", self.registered(run))
        self.assertIn("polaris", run.agent_servers["cursor"])
        self.assertNotIn("vega", run.agent_servers["cursor"])

    def test_declining_at_the_terminal_registers_nothing(self) -> None:
        run = self.missing_polaris_and_vega()
        before = self.manifest_file.read_bytes()
        code, out, err = self.run_cli(self.apply(), run, answers=["n"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("Nothing was installed", out)
        self.assert_nothing_registered(run, before)

    def test_an_installed_constellation_is_registered_only_when_picked(self) -> None:
        run = self.downloaded(
            {
                "claude": [n for n in MEMBER_NAMES if n not in ("constellation", "polaris")],
                "cursor": MEMBER_NAMES,
            }
        )
        code, out, err = self.run_cli(self.apply(), run, answers=["polaris"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("1. constellation", out)
        self.assertNotIn("is added too", out)  # it is installed already: nothing to set up first
        self.assertEqual(self.registered(run).count("polaris"), 1)
        self.assertNotIn("constellation", self.registered(run))
        self.assertNotIn("constellation", run.agent_servers["claude"])

    def test_nothing_to_create_needs_no_consent(self) -> None:
        run = self.downloaded({"claude": MEMBER_NAMES, "cursor": MEMBER_NAMES})
        code, out, err = self.run_cli(self.apply("--json"), run)
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("needs_consent", json.loads(out))
        code, out, err = self.run_cli(self.apply(), run, answers=["n"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.prompts, [])
        real = [argv for argv in run.matching("register") if "--dry-run" not in argv]
        self.assertEqual(len(real), 2)  # both runs went through

    def test_a_change_to_an_entry_an_agent_already_has_stays_automatic(self) -> None:
        run = FakeRun()
        self.assertEqual(self.run_cli(self.apply("--yes"), run)[0], 0)
        run.register_diffs = [
            {"agent": "Cursor IDE", "file": "mcp.json", "action": "update", "changes": ["~ Update server 'polaris'"]}
        ]
        code, out, err = self.run_cli(self.apply("--json"), run)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.prompts, [])
        report = json.loads(out)
        self.assertNotIn("needs_consent", report)
        self.assertEqual(report["registration"]["files"][0]["update"], 1)
        self.assertEqual(report["registration"]["added"], {})

    def test_a_create_that_names_no_server_asks_about_every_server(self) -> None:
        run = FakeRun()
        self.assertEqual(self.run_cli(self.apply("--yes"), run)[0], 0)
        run.register_diffs = [
            {"agent": "Cursor IDE", "file": "mcp.json", "action": "create", "changes": ["a wording we do not know"]}
        ]
        code, out, _err = self.run_cli(self.apply("--json"), run)
        self.assertEqual(code, 4)
        consent = json.loads(out)["consent"]
        self.assertEqual([item["name"] for item in consent], MEMBER_NAMES)
        for item in consent:
            self.assertEqual(item["agents"], ["Cursor IDE"])

    def test_check_and_dry_run_still_only_report(self) -> None:
        run = self.missing_polaris_and_vega()
        before = self.manifest_file.read_bytes()
        code, out, _err = self.run_cli(self.apply("--check", "--json"), run, answers=["a"])
        self.assertEqual(code, 3)
        self.assertNotIn("needs_consent", json.loads(out))
        code, out, _err = self.run_cli(self.apply("--dry-run"), run, answers=["a"])
        self.assertEqual(code, 0)
        self.assertEqual(self.prompts, [])
        self.assert_nothing_registered(run, before)

    # -- after a full install -------------------------------------------------------------------

    def test_a_second_run_needs_no_consent(self) -> None:
        run = FakeRun()
        self.assertEqual(self.run_cli(self.apply("--yes"), run)[0], 0)
        clones = len(run.matching("clone"))
        before = self.manifest_file.read_bytes()

        code, out, err = self.run_cli(self.apply("--json"), run)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.prompts, [])
        self.assertNotIn("needs_consent", json.loads(out))
        self.assertEqual(len(run.matching("clone")), clones)
        self.assertEqual(self.manifest_file.read_bytes(), before)

        code, out, err = self.run_cli(self.apply(), run, answers=["n"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.prompts, [])


class TestConsentHelpers(unittest.TestCase):
    NAMES = ["constellation", "polaris", "vega"]

    def test_answers(self) -> None:
        cases = [
            ("", []),
            (" N ", []),
            ("none", []),
            ("A", self.NAMES),
            ("yes", self.NAMES),
            ("2", ["polaris"]),
            ("3, 2", ["polaris", "vega"]),
            ("vega polaris", ["polaris", "vega"]),
            ("1,1,constellation", ["constellation"]),
        ]
        for answer, expected in cases:
            with self.subTest(answer=answer):
                self.assertEqual(cmd_setup._parse_answer(answer, self.NAMES), (expected, ""))
        for answer in ("0", "4", "polaris,nope", "-1", "all,polaris"):
            with self.subTest(answer=answer):
                chosen, error = cmd_setup._parse_answer(answer, self.NAMES)
                self.assertEqual(chosen, [])
                self.assertIn("Not understood", error)

    def test_end_of_input_and_ctrl_c_are_an_empty_answer(self) -> None:
        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with patch("builtins.input", side_effect=error), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cmd_setup._ask("? "), "")

    def test_consent_items_follow_the_plan(self) -> None:
        plan = {
            "members": [
                {"name": "constellation", "status": "ok"},
                {"name": "polaris", "status": "missing"},
                {"name": "aurora-slides", "status": "found"},
                {"name": "vega", "status": "adopted"},
                {"name": "orion", "status": "stale"},
                {"name": "sirius", "status": "failed"},
            ]
        }
        names = [i["name"] for i in bootstrap.consent_items(plan)]
        self.assertEqual(names, ["polaris", "aurora-slides", "vega"])
        no_clone = [i["name"] for i in bootstrap.consent_items(plan, no_clone=True)]
        self.assertEqual(no_clone, ["aurora-slides", "vega"])
        self.assertIn("orion", [i["name"] for i in bootstrap.consent_items(plan, repair=True)])

    def test_consent_items_include_ok_servers_that_would_be_added_to_an_agent(self) -> None:
        plan = {
            "target_agents": ["claude", "cursor"],
            "members": [
                {"name": "constellation", "status": "ok"},
                {"name": "polaris", "status": "ok"},
                {"name": "vega", "status": "ok"},
                {"name": "orion", "status": "missing"},
                {"name": "sirius", "status": "found"},
            ],
            "registration": {"added": {"polaris": ["Cursor IDE", "OpenAI Codex"], "sirius": ["Cursor IDE"]}},
        }
        items = {i["name"]: i for i in bootstrap.consent_items(plan)}
        self.assertEqual(list(items), ["polaris", "orion", "sirius"])
        self.assertEqual(items["polaris"]["agents"], ["Cursor IDE", "OpenAI Codex"])
        self.assertEqual((items["polaris"]["status"], items["polaris"]["action"]), ("ok", "register"))
        self.assertEqual(items["orion"]["agents"], ["Claude Code", "Cursor"])  # every agent setup targets
        self.assertEqual(items["sirius"]["agents"], ["Cursor IDE"])
        self.assertEqual([i["name"] for i in bootstrap.consent_items(plan, no_clone=True)], ["polaris", "sirius"])

    def test_consent_lines_name_the_agents(self) -> None:
        item = {
            "name": "polaris",
            "description": "Research",
            "repo": "https://example.com/polaris",
            "action": "register",
            "control_plane": False,
            "agents": ["Cursor IDE", "OpenAI Codex"],
        }
        self.assertEqual(
            bootstrap.format_consent([item]),
            [
                "  1. polaris - Research",
                "       register: https://example.com/polaris",
                "       add to: Cursor IDE, OpenAI Codex",
            ],
        )
        self.assertEqual(len(bootstrap.format_consent([{**item, "agents": []}])), 2)
        # nothing is fetched for a server that is downloaded already
        ok = bootstrap.format_consent([{**item, "status": "ok"}])
        self.assertEqual(ok[1], "       already downloaded: https://example.com/polaris")

    def test_summarise_diffs_names_the_servers_a_registration_would_add(self) -> None:
        def diff(agent: str, action: str, *changes: str) -> Dict[str, Any]:
            return {"agent": agent, "file": agent + ".json", "action": action, "changes": list(changes)}

        payload = {
            "diffs": [
                diff("Claude Code", "create", "+ Add server 'polaris': {}"),
                diff("Claude Code", "noop", "No change for server 'vega'"),
                diff("Cursor IDE", "create", "+ Add server 'polaris': {}"),
                diff("Cursor IDE", "create", "+ Add server 'vega': {}"),
                diff("Cursor IDE", "update", "~ Update server 'orion'"),
                diff("Cursor IDE", "delete", "- Remove server 'sirius'"),
            ]
        }
        summary = bootstrap._summarise_diffs(payload)
        self.assertEqual(summary["added"], {"polaris": ["Claude Code", "Cursor IDE"], "vega": ["Cursor IDE"]})
        self.assertEqual(summary["counts"], {"create": 3, "update": 1, "noop": 1, "delete": 1})
        # a create that names no server is blamed on every candidate
        unnamed = {"diffs": [diff("Cursor IDE", "create", "something else")]}
        self.assertEqual(
            bootstrap._summarise_diffs(unnamed, ["polaris", "vega"])["added"],
            {"polaris": ["Cursor IDE"], "vega": ["Cursor IDE"]},
        )
        self.assertEqual(bootstrap._summarise_diffs(unnamed)["added"], {})
        self.assertEqual(bootstrap._summarise_diffs(None)["added"], {})

    def test_exit_code_4_means_the_user_must_decide(self) -> None:
        plan = {"blocked": False, "errors": [], "mode": "dry-run", "needs_setup": True, "next_steps": ["x"]}
        report = bootstrap.consent_report(plan, [])
        self.assertEqual(bootstrap.exit_code(report), 4)
        self.assertEqual(report["next_steps"], [bootstrap.CONSENT_STEP, "x"])
        self.assertEqual(bootstrap.exit_code({**report, "blocked": True}), 2)
        self.assertIn("4", bootstrap.exit_code.__doc__ or "")


class TestAgentInstructions(unittest.TestCase):
    ASK_FIRST = (
        "It installs nothing until the user agrees",
        "exits 4",
        "the coding agents it would be added to",
        "your own question/confirmation prompt",
        "--only <approved,names> --yes",
        "Never pass `--yes` without the user's answer",
    )

    def assert_asks_first(self, text: str, command: str, label: str) -> None:
        self.assertIn(command, text, label)
        for phrase in self.ASK_FIRST:
            self.assertIn(phrase, text, label)
        self.assertNotIn("no prompts", text.lower(), label)

    def test_root_manifests_tell_agents_how_to_set_up(self) -> None:
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("smart_drive setup", text, name)
            self.assert_asks_first(text, "smart_drive setup --json", name)
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("setup --check --json", agents)
        self.assertIn("exit code 3", agents)

    def test_init_templates_carry_the_same_instruction(self) -> None:
        templates = (
            initializer.AGENTS_MD_TEMPLATE,
            initializer.CLAUDE_MD_TEMPLATE,
            initializer.GEMINI_MD_TEMPLATE,
        )
        for template in templates:
            self.assertIn("smart-drive setup", template)
            self.assert_asks_first(template, "smart-drive setup --json", template[:20])

    def test_readmes_explain_the_question(self) -> None:
        for name in ("README.md", "README_VN.md"):
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("setup --only <", text, name)
            self.assertIn("--yes", text, name)

    def test_privacy_policy_discloses_the_one_network_access(self) -> None:
        text = (PROJECT_ROOT / "PRIVACY.md").read_text(encoding="utf-8")
        self.assertIn("smart-drive setup", text)
        self.assertIn("--no-clone", text)
        self.assertIn("Zero External Network Transmission", text)
        self.assertIn("It asks first", text)
        self.assertIn("--yes", text)


if __name__ == "__main__":
    unittest.main()
