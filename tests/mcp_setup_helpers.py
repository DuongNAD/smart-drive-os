"""tests/mcp_setup_helpers.py - Fakes shared by the `smart-drive setup` test modules.

No test cases live here (the file name is not collected by unittest or pytest).
100% Python Standard Library.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from smart_drive.mcp import bootstrap
from smart_drive.mcp.ecosystem import MEMBERS

MEMBER_NAMES = [m.name for m in MEMBERS]

#: How Constellation names the agents in its diffs.
AGENT_DISPLAY = {
    "claude": "Claude Code",
    "antigravity": "Google Antigravity",
    "cursor": "Cursor IDE",
    "codex": "OpenAI Codex",
}


class FakeRun:
    """Scripted stand-in for subprocess: answers like node, git and Constellation's CLI."""

    def __init__(
        self,
        node_version: str = "v22.4.1",
        node_by_path: Optional[Dict[str, str]] = None,
        git_ok: bool = True,
        clone_fails: Iterable[str] = (),
        health_fails: Iterable[str] = (),
        register_diffs: Optional[List[Dict[str, Any]]] = None,
        agent_servers: Optional[Dict[str, Iterable[str]]] = None,
    ) -> None:
        self.calls: List[Tuple[List[str], Dict[str, str]]] = []
        self.node_version = node_version
        self.node_by_path = dict(node_by_path or {})
        self.git_ok = git_ok
        self.clone_fails = set(clone_fails)
        self.health_fails = set(health_fails)
        self.register_diffs = register_diffs if register_diffs is not None else []
        # When set, Constellation behaves like the real one: ``register`` creates an entry for every
        # (agent, server) that the agent's config does not hold yet, and keeps it after a real run.
        self.agent_servers = None if agent_servers is None else {a: set(n) for a, n in agent_servers.items()}

    def matching(self, *needles: str) -> List[List[str]]:
        """Recorded argv lists that contain every needle as an element."""
        return [argv for argv, _env in self.calls if all(n in argv for n in needles)]

    def _register_diffs(self, command: List[str]) -> List[Dict[str, Any]]:
        """The diffs for ``constellation register <names> --agents=<ids> [--dry-run]``."""
        if self.agent_servers is None:
            return self.register_diffs
        names = [a for a in command[3:] if not a.startswith("--")]
        agents = next(a[len("--agents="):] for a in command if a.startswith("--agents=")).split(",")
        diffs: List[Dict[str, Any]] = []
        for agent in agents:
            held = self.agent_servers.setdefault(agent, set())
            for name in names:
                create = name not in held
                diffs.append(
                    {
                        "agent": AGENT_DISPLAY[agent],
                        "file": f"{agent}.json",
                        "action": "create" if create else "noop",
                        "changes": [f"+ Add server '{name}': {{}}" if create else f"No change for server '{name}'"],
                    }
                )
                if create and "--dry-run" not in command:
                    held.add(name)
        return diffs

    def __call__(self, argv: Sequence[str], timeout: float, env: Dict[str, str]) -> Tuple[int, str, str]:
        command = list(argv)
        self.calls.append((command, dict(env)))
        exe = Path(command[0]).name.lower()
        if exe.startswith("node") and command[1:] == ["--version"]:
            return 0, self.node_by_path.get(command[0], self.node_version) + "\n", ""
        if exe.startswith("git"):
            if command[1:] == ["--version"]:
                if self.git_ok:
                    return 0, "git version 2.43.0\n", ""
                return 1, "", "xcode-select: note: no developer tools were found"
            if command[1] == "clone":
                repo_name = command[-2].rsplit("/", 1)[-1]
                if repo_name in self.clone_fails:
                    return 128, "", "fatal: repository not found"
                member = next(m for m in MEMBERS if m.repo_name == repo_name)
                bundle = Path(command[-1]) / member.entry
                bundle.parent.mkdir(parents=True, exist_ok=True)
                bundle.write_text("// fake bundle\n", encoding="utf-8")
                return 0, "", ""
        if len(command) > 2 and command[1].replace("\\", "/").endswith("constellation.mjs"):
            if command[2] == "register":
                return 0, json.dumps({"diffs": self._register_diffs(command), "summary": "ok"}), ""
            if command[2] == "health":
                names = [a for a in command[3:] if not a.startswith("--")]
                rows = [
                    {
                        "name": n,
                        "status": "error" if n in self.health_fails else "ok",
                        "toolCount": 3,
                        "error": "no reply" if n in self.health_fails else None,
                    }
                    for n in names
                ]
                failed = any(n in self.health_fails for n in names)
                return (1 if failed else 0), json.dumps(rows), ""
        return 127, "", f"unexpected command: {command}"


def make_host(
    root: Path,
    run: FakeRun,
    system: str = "linux",
    on_path: Iterable[str] = ("node", "git"),
) -> bootstrap.Host:
    """A machine whose PATH holds exactly the given tools and whose home lives under ``root``."""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    table = {name: str(root / "bin" / name) for name in on_path}
    return bootstrap.Host(system=system, home=home, env={}, which=table.get, run=run)


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TempRootCase(unittest.TestCase):
    """A throw-away machine: home, clone destination and Constellation manifest all under one temp dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / "home"
        self.dest = self.root / "mcp"
        self.manifest_file = self.home / ".constellation" / "ecosystem.json"
