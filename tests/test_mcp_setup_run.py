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
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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

        clones = [(argv, env) for argv, env in run.calls if len(argv) > 1 and argv[1] == "clone"]
        self.assertEqual(len(clones), len(MEMBERS))
        for argv, env in clones:
            self.assertEqual(argv[1:5], ["clone", "--depth", "1", "--quiet"])
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
    def run_cli(self, argv: List[str], run: FakeRun) -> Tuple[int, str, str]:
        host = make_host(self.root, run)
        touch(self.home / ".claude.json", "{}")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(bootstrap, "default_host", return_value=host):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_check_json_reports_pending_work(self) -> None:
        code, out, _err = self.run_cli(["setup", "--check", "--json", "--dest", str(self.dest)], FakeRun())
        self.assertEqual(code, 3)
        report = json.loads(out)
        self.assertEqual(report["mode"], "check")
        self.assertTrue(report["needs_setup"])
        self.assertEqual(report["members"][0]["name"], CONTROL_PLANE)

    def test_apply_accepts_yes_and_streams_progress_to_stderr(self) -> None:
        code, out, err = self.run_cli(["setup", "--yes", "--dest", str(self.dest)], FakeRun())
        self.assertEqual(code, 0, out + err)
        self.assertIn("git clone", err)
        self.assertIn("SmartDrive MCP ecosystem setup", out)

    def test_unknown_server_exits_with_2(self) -> None:
        code, _out, err = self.run_cli(["setup", "--only", "nope"], FakeRun())
        self.assertEqual(code, 2)
        self.assertIn("unknown server", err)


class TestAgentInstructions(unittest.TestCase):
    def test_root_manifests_tell_agents_how_to_set_up(self) -> None:
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("smart_drive setup", text, name)

    def test_init_templates_carry_the_same_instruction(self) -> None:
        templates = (
            initializer.AGENTS_MD_TEMPLATE,
            initializer.CLAUDE_MD_TEMPLATE,
            initializer.GEMINI_MD_TEMPLATE,
        )
        for template in templates:
            self.assertIn("smart-drive setup", template)

    def test_privacy_policy_discloses_the_one_network_access(self) -> None:
        text = (PROJECT_ROOT / "PRIVACY.md").read_text(encoding="utf-8")
        self.assertIn("smart-drive setup", text)
        self.assertIn("--no-clone", text)
        self.assertIn("Zero External Network Transmission", text)


if __name__ == "__main__":
    unittest.main()
