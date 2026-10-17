"""tests/test_mcp_setup.py - `smart-drive setup`: catalogue, probes, resolution and manifest merging.

Hermetic by design: no network, no real node / git / claude, and a throw-away home directory.
The process layer (`bootstrap.Host.run`) is replaced by a scripted fake (tests/mcp_setup_helpers.py),
so the same suite runs on Windows, macOS and Linux and can also simulate the macOS PATH situation.
The end-to-end runs live in tests/test_mcp_setup_run.py.

100% Python Standard Library unittest.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.mcp import bootstrap
from smart_drive.mcp.ecosystem import CONTROL_PLANE, MEMBERS, MIN_NODE, SELF_NAME
from tests.mcp_setup_helpers import MEMBER_NAMES, FakeRun, TempRootCase, make_host, touch


class TestCatalogue(unittest.TestCase):
    def test_servers_are_unique_and_the_control_plane_comes_first(self) -> None:
        self.assertEqual(len(MEMBER_NAMES), len(set(MEMBER_NAMES)))
        self.assertEqual(MEMBER_NAMES[0], CONTROL_PLANE)
        self.assertNotIn(SELF_NAME, MEMBER_NAMES)

    def test_every_server_is_a_committed_bundle_of_the_owner(self) -> None:
        for member in MEMBERS:
            self.assertTrue(member.repo.startswith("https://github.com/DuongNAD/"), member.repo)
            self.assertRegex(member.entry, r"^dist/[a-z]+\.mjs$")
            self.assertTrue(member.description)

    def test_node_floor_matches_what_the_servers_declare(self) -> None:
        self.assertEqual(MIN_NODE, (22, 4))


class TestParseVersion(unittest.TestCase):
    def test_extracts_the_first_version(self) -> None:
        self.assertEqual(bootstrap.parse_version("v22.4.1\n"), (22, 4, 1))
        self.assertEqual(bootstrap.parse_version("git version 2.55.0.windows.3"), (2, 55, 0))
        self.assertEqual(bootstrap.parse_version("v20.11"), (20, 11, 0))
        self.assertIsNone(bootstrap.parse_version("no digits here"))


class TestNodeProbe(TempRootCase):
    def test_path_node_is_used_when_new_enough(self) -> None:
        tool = bootstrap.probe_node(make_host(self.root, FakeRun(node_version="v22.4.0")))
        self.assertTrue(tool.ok)
        self.assertEqual(tool.version, "22.4.0")
        self.assertEqual(tool.path, str(self.root / "bin" / "node"))

    def test_an_old_node_is_reported_as_too_old(self) -> None:
        host = make_host(self.root, FakeRun(node_version="v20.11.0"))
        with patch.object(bootstrap, "POSIX_NODE_PATHS", ()):
            tool = bootstrap.probe_node(host)
        self.assertFalse(tool.ok)
        self.assertIn("too old", tool.detail)

    def test_a_missing_node_is_reported(self) -> None:
        host = make_host(self.root, FakeRun(), on_path=())
        with patch.object(bootstrap, "POSIX_NODE_PATHS", ()):
            tool = bootstrap.probe_node(host)
        self.assertFalse(tool.ok)
        self.assertIn("not found", tool.detail)

    def test_a_gui_style_path_falls_back_to_the_homebrew_node(self) -> None:
        brew = touch(self.root / "opt" / "homebrew" / "bin" / "node")
        host = make_host(self.root, FakeRun(node_version="v22.9.0"), system="macos", on_path=("git",))
        with patch.object(bootstrap, "POSIX_NODE_PATHS", (str(brew),)):
            tool = bootstrap.probe_node(host)
        self.assertTrue(tool.ok)
        self.assertEqual(tool.path, str(brew))

    def test_an_old_node_on_path_does_not_hide_a_newer_one(self) -> None:
        brew = touch(self.root / "opt" / "homebrew" / "bin" / "node")
        on_path = str(self.root / "bin" / "node")
        run = FakeRun(node_by_path={on_path: "v18.19.0", str(brew): "v22.9.0"})
        host = make_host(self.root, run, system="macos")
        with patch.object(bootstrap, "POSIX_NODE_PATHS", (str(brew),)):
            tool = bootstrap.probe_node(host)
        self.assertTrue(tool.ok)
        self.assertEqual(tool.path, str(brew))

    def test_windows_never_searches_posix_locations(self) -> None:
        brew = touch(self.root / "opt" / "homebrew" / "bin" / "node")
        host = make_host(self.root, FakeRun(), system="windows", on_path=())
        with patch.object(bootstrap, "POSIX_NODE_PATHS", (str(brew),)):
            tool = bootstrap.probe_node(host)
        self.assertFalse(tool.ok)

    def test_nvm_prefers_the_newest_installed_version(self) -> None:
        for version in ("v20.11.0", "v22.5.0", "v22.12.1"):
            touch(self.home / ".nvm" / "versions" / "node" / version / "bin" / "node")
        host = make_host(self.root, FakeRun(node_version="v22.12.1"), system="macos", on_path=())
        with patch.object(bootstrap, "POSIX_NODE_PATHS", ()):
            tool = bootstrap.probe_node(host)
        self.assertTrue(tool.ok)
        self.assertIn("v22.12.1", tool.path)


class TestGitProbe(TempRootCase):
    def test_missing_git(self) -> None:
        tool = bootstrap.probe_git(make_host(self.root, FakeRun(), on_path=("node",)))
        self.assertFalse(tool.ok)
        self.assertIn("not found", tool.detail)

    def test_macos_git_shim_without_developer_tools(self) -> None:
        tool = bootstrap.probe_git(make_host(self.root, FakeRun(git_ok=False), system="macos"))
        self.assertFalse(tool.ok)
        self.assertIn("xcode-select --install", tool.detail)


class TestAgentDetection(TempRootCase):
    def test_detects_by_home_markers_and_by_binary(self) -> None:
        host = make_host(self.root, FakeRun(), on_path=("node", "git", "codex"))
        touch(self.home / ".claude.json", "{}")
        (self.home / ".cursor").mkdir()
        found = {a["id"]: a for a in bootstrap.detect_agents(host)}
        self.assertEqual(found["claude"]["via"], "~/.claude.json")
        self.assertTrue(found["cursor"]["detected"])
        self.assertEqual(found["codex"]["via"], "PATH:codex")
        self.assertFalse(found["antigravity"]["detected"])


class TestResolution(TempRootCase):
    def setUp(self) -> None:
        super().setUp()
        self.member = next(m for m in MEMBERS if m.name == "polaris")
        self.host = make_host(self.root, FakeRun())

    def resolve(
        self, registered: Optional[Dict[str, Dict[str, Any]]] = None, repair: bool = False
    ) -> bootstrap.MemberState:
        return bootstrap.resolve_member(self.host, self.member, registered or {}, self.dest, "/n/node", repair)

    def bundle(self, base: Path) -> Path:
        return touch(base / self.member.entry, "// bundle\n")

    def test_manifest_entry_with_existing_files_is_ok(self) -> None:
        checkout = self.root / "elsewhere" / "polaris_mcp"
        bundle = self.bundle(checkout)
        state = self.resolve({"polaris": {"name": "polaris", "command": "node", "args": [str(bundle)]}})
        self.assertEqual(state.status, "ok")
        self.assertEqual(Path(state.root), checkout)
        self.assertIsNone(state.entry)

    def test_manifest_entry_with_missing_files_is_stale_until_repair(self) -> None:
        gone = self.root / "gone" / "dist" / "polaris.mjs"
        registered = {"polaris": {"name": "polaris", "command": "node", "args": [str(gone)]}}
        self.assertEqual(self.resolve(registered).status, "stale")
        repaired = self.resolve(registered, repair=True)
        self.assertEqual(repaired.status, "missing")
        self.assertTrue(repaired.replace)

    def test_checkout_in_an_agent_config_is_adopted_verbatim_without_secrets(self) -> None:
        bundle = self.bundle(self.root / "mine" / "polaris_mcp")
        touch(
            self.home / ".claude.json",
            json.dumps(
                {
                    "mcpServers": {
                        "polaris": {
                            "command": "node",
                            "args": [str(bundle)],
                            "env": {"SEMANTIC_SCHOLAR_API_KEY": "s3cret"},
                        }
                    }
                }
            ),
        )
        state = self.resolve()
        self.assertEqual(state.status, "adopted")
        self.assertEqual(state.source, "agent:claude")
        self.assertIsNotNone(state.entry)
        self.assertEqual(state.entry["command"], "node")
        self.assertEqual(state.entry["args"], [str(bundle)])
        self.assertNotIn("env", state.entry)
        self.assertNotIn("s3cret", json.dumps(state.entry))

    def test_checkout_from_an_earlier_run_is_found_in_dest(self) -> None:
        bundle = self.bundle(self.dest / "polaris-mcp")
        state = self.resolve()
        self.assertEqual(state.status, "found")
        self.assertIsNotNone(state.entry)
        self.assertEqual(state.entry["command"], "/n/node")
        self.assertEqual(state.entry["args"], [bundle.as_posix()])

    def test_nothing_anywhere_is_missing(self) -> None:
        state = self.resolve()
        self.assertEqual(state.status, "missing")
        self.assertFalse(state.replace)


class TestManifest(TempRootCase):
    def test_a_missing_manifest_is_empty(self) -> None:
        manifest, error = bootstrap.load_manifest(self.manifest_file)
        self.assertEqual((manifest["servers"], error), ([], ""))

    def test_a_bom_is_tolerated(self) -> None:
        self.manifest_file.parent.mkdir(parents=True)
        payload = json.dumps({"version": "1", "servers": [{"name": "a"}]}).encode("utf-8")
        self.manifest_file.write_bytes(b"\xef\xbb\xbf" + payload)
        manifest, error = bootstrap.load_manifest(self.manifest_file)
        self.assertEqual(error, "")
        self.assertEqual(manifest["servers"], [{"name": "a"}])

    def test_garbage_is_an_error(self) -> None:
        touch(self.manifest_file, "[1, 2")
        _manifest, error = bootstrap.load_manifest(self.manifest_file)
        self.assertIn("not readable JSON", error)

    def test_merge_appends_and_never_rewrites_existing_entries(self) -> None:
        manifest = {"version": "1", "servers": [{"name": "polaris", "command": "python", "args": ["old"]}]}
        added = {"name": "vega", "command": "node", "args": ["v"]}
        changed = bootstrap.merge_manifest(
            manifest, [({"name": "polaris", "command": "node", "args": ["new"]}, False), (added, False)]
        )
        self.assertEqual(changed, ["vega"])
        self.assertEqual(manifest["servers"][0]["command"], "python")
        self.assertEqual(manifest["servers"][1], added)

    def test_merge_replaces_in_place_when_asked(self) -> None:
        manifest = {"version": "1", "servers": [{"name": "a"}, {"name": "polaris", "args": ["old"]}, {"name": "z"}]}
        changed = bootstrap.merge_manifest(manifest, [({"name": "polaris", "args": ["new"]}, True)])
        self.assertEqual(changed, ["polaris"])
        self.assertEqual([s["name"] for s in manifest["servers"]], ["a", "polaris", "z"])
        self.assertEqual(manifest["servers"][1]["args"], ["new"])

    def test_write_keeps_a_backup_of_the_previous_manifest(self) -> None:
        touch(self.manifest_file, json.dumps({"version": "1", "servers": [{"name": "old"}]}))
        bootstrap.write_manifest(self.manifest_file, {"version": "1", "servers": [{"name": "new"}]})
        backup = Path(str(self.manifest_file) + ".bak.constellation")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["servers"], [{"name": "old"}])
        text = self.manifest_file.read_text(encoding="utf-8")
        self.assertEqual(json.loads(text)["servers"], [{"name": "new"}])
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
