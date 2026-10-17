"""smart_drive.mcp.bootstrap - One-command setup of the MCP ecosystem around SmartDrive-OS.

``smart-drive setup`` is what a coding agent runs once on a fresh clone, on any machine:

1. probe     Node >= 22.4, git, and which coding agents are installed
2. resolve   each server: in Constellation's manifest > registered in an agent config > in <dest> > missing
3. install   ``git clone --depth 1`` every missing server into <dest> (default ``~/.smart-drive/mcp``)
4. manifest  add the servers and SmartDrive itself to ``~/.constellation/ecosystem.json``
             (entries that already exist are never rewritten, except with ``--repair``)
5. register  ``constellation register <names> --agents=...``. Constellation owns the Claude Code,
             Antigravity, Cursor and Codex configs (CLI calls, CRLF, backups), so this module never
             edits those files itself
6. verify    ``constellation health <servers>`` starts every server over stdio

Safe to re-run: what is already set up is left alone, and nothing is written when nothing changed.
The only network access is ``git clone`` (skipped by ``--no-clone``). Every server runs from a
``dist/*.mjs`` bundle that is committed in its repository, so no npm install or build is needed.

Windsurf and Claude Desktop are not managed by Constellation; ``smart-drive mcp register`` still
registers SmartDrive itself there.
"""

from __future__ import annotations

import copy
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from smart_drive.mcp import registrar
from smart_drive.mcp.ecosystem import (
    CONTROL_PLANE,
    DEFAULT_AGENTS,
    MEMBERS,
    MIN_NODE,
    SELF_DESCRIPTION,
    SELF_NAME,
    SELF_REPO,
    Member,
)

RunFn = Callable[[Sequence[str], float, Mapping[str, str]], Tuple[int, str, str]]
LogFn = Callable[[str], None]

PROBE_TIMEOUT = 15.0
CLONE_TIMEOUT = 300.0
CONSTELLATION_TIMEOUT = 180.0
HEALTH_TIMEOUT_MS = 20000
MANIFEST_BACKUP_SUFFIX = ".bak.constellation"

#: The agents Constellation manages; the ecosystem is registered in these.
VALID_AGENTS: Tuple[str, ...] = ("claude", "antigravity", "cursor", "codex")
_AGENT_LABELS = {
    "claude": "Claude Code",
    "antigravity": "Google Antigravity",
    "cursor": "Cursor",
    "codex": "OpenAI Codex",
}
_AGENT_BINARIES = {"claude": "claude", "cursor": "cursor", "codex": "codex"}

#: Where Node usually lives when an agent's PATH is thin (GUI apps on macOS). Tests patch this.
POSIX_NODE_PATHS: Tuple[str, ...] = ("/opt/homebrew/bin/node", "/usr/local/bin/node")

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
#: Server states in which the bundle is on disk and the server can be registered.
_RESOLVED = ("ok", "adopted", "found", "installed")


# --------------------------------------------------------------------------- host


@dataclass
class Host:
    """Everything setup reads from the machine; tests substitute a fake one."""

    system: str  # "windows" | "macos" | "linux"
    home: Path
    env: Dict[str, str]
    which: Callable[[str], Optional[str]]
    run: RunFn


def _run_process(argv: Sequence[str], timeout: float, env: Mapping[str, str]) -> Tuple[int, str, str]:
    """Runs argv without a shell and returns (returncode, stdout, stderr). Never raises."""
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout:.0f}s"
    except OSError as err:
        return 127, "", str(err)
    return done.returncode, done.stdout or "", done.stderr or ""


def default_host() -> Host:
    """The real machine."""
    if sys.platform == "win32":
        system = "windows"
    elif sys.platform == "darwin":
        system = "macos"
    else:
        system = "linux"
    env = dict(os.environ)
    # Same lookup order as Constellation's getHomeDir(), so both tools agree on ~/.constellation.
    home_text = env.get("USERPROFILE") or env.get("HOME")
    home = Path(home_text) if home_text else registrar._safe_home_dir()
    return Host(system=system, home=home, env=env, which=shutil.which, run=_run_process)


def default_dest(host: Host) -> Path:
    """Where the servers are cloned: the host's home, never the (exFAT) drive this repo may live on."""
    override = host.env.get("SMART_DRIVE_MCP_HOME")
    return Path(override) if override else host.home / ".smart-drive" / "mcp"


def manifest_path(host: Host) -> Path:
    """Constellation's manifest, resolved the way Constellation resolves it."""
    override = host.env.get("CONSTELLATION_HOME")
    base = Path(override) if override else host.home / ".constellation"
    return base / "ecosystem.json"


# --------------------------------------------------------------------------- options & results


@dataclass
class Options:
    """What the caller asked for."""

    dest: Optional[Path] = None
    agents: Optional[List[str]] = None
    only: Optional[List[str]] = None
    dry_run: bool = False
    check: bool = False
    no_clone: bool = False
    no_health: bool = False
    repair: bool = False


@dataclass
class Tool:
    """Result of probing one prerequisite."""

    name: str
    ok: bool
    path: str = ""
    version: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "path": self.path, "version": self.version, "detail": self.detail}


@dataclass
class MemberState:
    """Where one server stands: ok | adopted | found | installed | missing | stale | failed."""

    name: str
    status: str = "missing"
    root: str = ""
    source: str = ""  # manifest | agent:<id> | dest | clone
    detail: str = ""
    entry: Optional[Dict[str, Any]] = field(default=None, repr=False)  # manifest entry to add
    replace: bool = False  # overwrite the stale manifest entry in place (--repair)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "path": self.root,
            "source": self.source,
            "detail": self.detail,
        }


# --------------------------------------------------------------------------- probes


def parse_version(text: str) -> Optional[Tuple[int, int, int]]:
    """First ``major.minor[.patch]`` found in text, e.g. ``v22.4.1`` or ``git version 2.43.0``."""
    match = _VERSION_RE.search(text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _node_candidates(host: Host) -> List[Tuple[str, bool]]:
    """(path, must_exist) in preference order: a PATH hit is trusted, the fallbacks are checked."""
    found: List[Tuple[str, bool]] = []
    override = host.env.get("SMART_DRIVE_NODE")
    if override:
        found.append((override, True))
    on_path = host.which("node")
    if on_path:
        found.append((on_path, False))
    if host.system != "windows":
        found.extend((candidate, True) for candidate in POSIX_NODE_PATHS)
        found.append((str(host.home / ".volta" / "bin" / "node"), True))
        nvm = host.home / ".nvm" / "versions" / "node"
        try:
            installed = [d for d in nvm.iterdir() if d.is_dir()]
        except OSError:
            installed = []
        installed.sort(key=lambda d: parse_version(d.name) or (0, 0, 0), reverse=True)
        found.extend((str(d / "bin" / "node"), True) for d in installed)
    return found


def probe_node(host: Host) -> Tool:
    """Finds a Node >= MIN_NODE. The path found is the one registered, so it must be absolute."""
    need = f"{MIN_NODE[0]}.{MIN_NODE[1]}"
    too_old: Optional[Tool] = None
    seen = set()
    for path, must_exist in _node_candidates(host):
        if path in seen:
            continue
        seen.add(path)
        if must_exist and not os.path.isfile(path):
            continue
        code, out, _err = host.run([path, "--version"], PROBE_TIMEOUT, host.env)
        version = parse_version(out) if code == 0 else None
        if version is None:
            continue
        text = ".".join(str(n) for n in version)
        if version[:2] >= MIN_NODE:
            return Tool("node", True, path, text)
        if too_old is None:
            too_old = Tool("node", False, path, text, f"Node {text} is too old; Node >= {need} is required")
    return too_old or Tool("node", False, detail=f"Node.js >= {need} not found (install it from https://nodejs.org)")


def probe_git(host: Host) -> Tool:
    """Finds a git that actually runs (on macOS the /usr/bin/git shim exists without the developer tools)."""
    git = host.which("git")
    if not git:
        return Tool("git", False, detail="git not found on PATH (install it from https://git-scm.com)")
    code, out, _err = host.run([git, "--version"], PROBE_TIMEOUT, host.env)
    if code != 0:
        hint = " On macOS run `xcode-select --install`." if host.system == "macos" else ""
        return Tool("git", False, git, detail=f"git is installed but cannot run.{hint}")
    version = parse_version(out)
    return Tool("git", True, git, ".".join(str(n) for n in version) if version else out.strip())


def detect_agents(host: Host) -> List[Dict[str, Any]]:
    """Which of the Constellation-managed coding agents look installed (read-only)."""
    home = host.home
    custom = host.env.get("ANTIGRAVITY_HOME") or host.env.get("GEMINI_HOME")
    markers: Dict[str, List[Tuple[str, Path]]] = {
        "claude": [("~/.claude.json", home / ".claude.json"), ("~/.claude", home / ".claude")],
        "antigravity": [("~/.gemini/antigravity", home / ".gemini" / "antigravity")],
        "cursor": [("~/.cursor", home / ".cursor")],
        "codex": [("~/.codex", home / ".codex")],
    }
    if custom:
        markers["antigravity"].insert(0, ("$ANTIGRAVITY_HOME", Path(custom)))
    result: List[Dict[str, Any]] = []
    for agent_id in VALID_AGENTS:
        via = ""
        for label, path in markers[agent_id]:
            if _exists(path):
                via = label
                break
        binary = _AGENT_BINARIES.get(agent_id)
        if not via and binary and host.which(binary):
            via = f"PATH:{binary}"
        result.append(
            {"id": agent_id, "label": _AGENT_LABELS[agent_id], "detected": bool(via), "via": via}
        )
    return result


def agent_config_files(host: Host) -> List[Tuple[str, Path]]:
    """(agent id, JSON file with an ``mcpServers`` object) that may already point at a checkout."""
    home = host.home
    custom = host.env.get("ANTIGRAVITY_HOME") or host.env.get("GEMINI_HOME")
    antigravity = Path(custom) if custom else home / ".gemini" / "antigravity"
    return [
        ("claude", home / ".claude.json"),
        ("antigravity", antigravity / "mcp_config.json"),
        ("antigravity", home / ".gemini" / "config" / "mcp_config.json"),
        ("antigravity", home / ".gemini" / "antigravity-ide" / "mcp_config.json"),
        ("cursor", home / ".cursor" / "mcp.json"),
    ]


# --------------------------------------------------------------------------- manifest


def load_manifest(path: Path) -> Tuple[Dict[str, Any], str]:
    """Returns (manifest, error). A missing file is an empty manifest; an unreadable one is an error."""
    empty: Dict[str, Any] = {"version": "1", "servers": []}
    if not _is_file(path):
        return empty, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as err:
        return empty, f"{path} is not readable JSON ({err}); fix or move it aside"
    if not isinstance(data, dict) or not isinstance(data.get("servers", []), list):
        return empty, f"{path} does not look like a Constellation manifest; fix or move it aside"
    data.setdefault("version", "1")
    data.setdefault("servers", [])
    return data, ""


def _servers_by_name(manifest: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        s["name"]: s
        for s in manifest.get("servers", [])
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    }


def merge_manifest(manifest: Dict[str, Any], additions: Sequence[Tuple[Dict[str, Any], bool]]) -> List[str]:
    """Appends new entries (or, when the flag is set, replaces the existing one in place).

    Entries that are not named in ``additions`` are left exactly as they are. Returns the changed names.
    """
    changed: List[str] = []
    servers: List[Any] = manifest["servers"]
    index = {
        s["name"]: i
        for i, s in enumerate(servers)
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    }
    for entry, replace in additions:
        name = entry["name"]
        if name in index:
            if replace and servers[index[name]] != entry:
                servers[index[name]] = entry
                changed.append(name)
            continue
        servers.append(entry)
        index[name] = len(servers) - 1
        changed.append(name)
    return changed


def write_manifest(path: Path, manifest: Dict[str, Any]) -> None:
    """Atomic write with Constellation's own backup convention (``<file>.bak.constellation``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_file(path):
        shutil.copy2(str(path), str(path) + MANIFEST_BACKUP_SUFFIX)
    registrar.save_json_config(path, manifest)


def _manifest_entry(member: Member, root: Path, command: str, args: List[str]) -> Dict[str, Any]:
    """Manifest entry for one member. No ``env``: secrets never travel through setup."""
    return {
        "name": member.name,
        "description": member.description,
        "repo": member.repo,
        "path": root.as_posix(),
        "transport": "stdio",
        "command": command,
        "args": args,
        "agents": list(DEFAULT_AGENTS),
        "health": {"timeoutMs": HEALTH_TIMEOUT_MS},
    }


def self_manifest_entry() -> Dict[str, Any]:
    """SmartDrive's own manifest entry: the exact command `smart-drive mcp register` writes."""
    launch = registrar.build_mcp_entry()
    entry: Dict[str, Any] = {"name": SELF_NAME, "description": SELF_DESCRIPTION, "repo": SELF_REPO}
    checkout = registrar._source_checkout_root()
    if checkout is not None:
        entry["path"] = checkout.as_posix()
    entry["transport"] = "stdio"
    entry["command"] = launch["command"]
    entry["args"] = list(launch["args"])
    entry["env"] = dict(launch["env"])
    entry["agents"] = list(DEFAULT_AGENTS)
    entry["health"] = {"timeoutMs": HEALTH_TIMEOUT_MS}
    return entry


# --------------------------------------------------------------------------- members


def _entry_in_args(args: Any, member: Member) -> Optional[Path]:
    """The absolute, existing bundle path inside a command's args, if there is one."""
    if not isinstance(args, list):
        return None
    suffix = "/" + member.entry
    for item in args:
        if not isinstance(item, str) or not os.path.isabs(item):
            continue
        if item.replace("\\", "/").endswith(suffix) and _is_file(Path(item)):
            return Path(item)
    return None


def _root_of(entry_file: Path, member: Member) -> Path:
    """Checkout directory of a bundle: ``<root>/dist/x.mjs`` -> ``<root>``."""
    return entry_file.parents[len(PurePosixPath(member.entry).parts) - 1]


def _read_servers(path: Path) -> Dict[str, Any]:
    servers = registrar.load_json_config(path).get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def resolve_member(
    host: Host,
    member: Member,
    registered: Mapping[str, Dict[str, Any]],
    dest: Path,
    node_command: str,
    repair: bool,
) -> MemberState:
    """Decides where a server stands without touching anything."""
    state = MemberState(member.name)
    stale = False

    entry = registered.get(member.name)
    if entry is not None:
        bundle = _entry_in_args(entry.get("args"), member)
        if bundle is None and isinstance(entry.get("path"), str):
            candidate = Path(entry["path"]) / member.entry
            bundle = candidate if _is_file(candidate) else None
        if bundle is not None:
            state.status, state.root, state.source = "ok", str(_root_of(bundle, member)), "manifest"
            return state
        stale = True
        if not repair:
            state.status, state.source = "stale", "manifest"
            state.detail = f"the manifest points at a {member.entry} that no longer exists (run with --repair)"
            return state

    # A checkout that an agent config already points at is reused as it is, never cloned twice.
    for agent_id, config in agent_config_files(host):
        server = _read_servers(config).get(member.name)
        if not isinstance(server, dict):
            continue
        bundle = _entry_in_args(server.get("args"), member)
        command = server.get("command")
        if bundle is None or not isinstance(command, str):
            continue
        root = _root_of(bundle, member)
        state.status, state.root, state.source = "adopted", str(root), f"agent:{agent_id}"
        state.entry = _manifest_entry(member, root, command, [str(a) for a in server["args"]])
        state.replace = stale
        return state

    # A checkout left by an earlier run.
    target = dest / member.repo_name
    if _is_file(target / member.entry):
        state.status, state.root, state.source = "found", str(target), "dest"
        state.entry = _manifest_entry(member, target, node_command, [(target / member.entry).as_posix()])
        state.replace = stale
        return state

    state.status = "missing"
    state.replace = stale
    return state


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "no output")[:300]


def install_member(host: Host, member: Member, dest: Path, git: str) -> Tuple[bool, str]:
    """Shallow-clones one server into ``dest``. Returns (done, detail)."""
    target = dest / member.repo_name
    if _exists(target):
        if _is_file(target / member.entry):
            return True, "already present"
        return False, f"{target} exists but has no {member.entry}; remove it or pass another --dest"
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        return False, f"cannot create {dest}: {err}"
    env = dict(host.env)
    env["GIT_TERMINAL_PROMPT"] = "0"
    code, out, err = host.run(
        [git, "clone", "--depth", "1", "--quiet", member.repo, str(target)], CLONE_TIMEOUT, env
    )
    if code != 0:
        return False, f"git clone failed: {_last_line(err or out)}"
    if not _is_file(target / member.entry):
        return False, f"cloned, but {member.entry} is missing (did the repository layout change?)"
    return True, "cloned"


# --------------------------------------------------------------------------- constellation


def _first_json(text: str) -> Any:
    """The first JSON object or array in text, or None."""
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char in "{[":
            try:
                value, _end = decoder.raw_decode(text[start:])
            except ValueError:
                continue
            return value
    return None


def _constellation(host: Host, node: str, script: Path, argv: List[str]) -> Tuple[int, Any, str]:
    code, out, err = host.run([node, str(script), *argv], CONSTELLATION_TIMEOUT, host.env)
    return code, _first_json(out), (err.strip() or out.strip())


def _summarise_diffs(payload: Any) -> Dict[str, Any]:
    """Condenses `constellation register --json` into per-action counts and per-file totals."""
    counts: Dict[str, int] = {"create": 0, "update": 0, "noop": 0, "delete": 0}
    files: Dict[Tuple[str, str], Dict[str, Any]] = {}
    diffs = payload.get("diffs") if isinstance(payload, dict) else None
    for diff in diffs if isinstance(diffs, list) else []:
        if not isinstance(diff, dict):
            continue
        action = str(diff.get("action", ""))
        counts[action] = counts.get(action, 0) + 1
        if action == "noop":
            continue
        agent, path = str(diff.get("agent", "")), str(diff.get("file", ""))
        slot = files.setdefault(
            (agent, path), {"agent": agent, "file": path, "create": 0, "update": 0, "delete": 0}
        )
        slot[action] = slot.get(action, 0) + 1
    return {"counts": counts, "files": list(files.values())}


def _summarise_health(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                rows.append(
                    {
                        "name": str(item.get("name", "")),
                        "status": str(item.get("status", "error")),
                        "tools": item.get("toolCount"),
                        "version": item.get("version"),
                        "error": str(item.get("error") or ""),
                    }
                )
    return rows


# --------------------------------------------------------------------------- driver


def _select_members(only: Optional[Sequence[str]]) -> List[Member]:
    if not only:
        return list(MEMBERS)
    wanted = set(only)
    unknown = sorted(wanted - {m.name for m in MEMBERS})
    if unknown:
        known = ", ".join(m.name for m in MEMBERS)
        raise ValueError(f"unknown server(s): {', '.join(unknown)}; choose from: {known}")
    return [m for m in MEMBERS if m.name in wanted]


def _check_agents(agents: Optional[Sequence[str]]) -> Optional[List[str]]:
    if agents is None:
        return None
    wanted = set(agents)
    unknown = sorted(wanted - set(VALID_AGENTS))
    if unknown:
        raise ValueError(f"unknown agent(s): {', '.join(unknown)}; choose from: {', '.join(VALID_AGENTS)}")
    return [a for a in VALID_AGENTS if a in wanted]


def run_setup(
    options: Options,
    host: Optional[Host] = None,
    log: Optional[LogFn] = None,
) -> Dict[str, Any]:
    """Runs the setup (or only plans it with ``check``/``dry_run``) and returns a JSON-serialisable report.

    Raises ``ValueError`` for an unknown server or agent name.
    """
    host = host or default_host()
    say: LogFn = log or (lambda _message: None)
    selected = _select_members(options.only)
    wanted_agents = _check_agents(options.agents)
    mode = "check" if options.check else ("dry-run" if options.dry_run else "apply")
    write = mode == "apply"

    errors: List[str] = []
    next_steps: List[str] = []
    dest = Path(os.path.abspath(str(options.dest or default_dest(host))))
    node = probe_node(host)
    git = probe_git(host)
    agents = detect_agents(host)
    if wanted_agents is not None:
        target_agents = wanted_agents
    else:
        target_agents = [a["id"] for a in agents if a["detected"]]

    manifest_file = manifest_path(host)
    manifest, manifest_error = load_manifest(manifest_file)
    if manifest_error:
        errors.append(manifest_error)
    registered = _servers_by_name(manifest)

    # The control plane is always operated: every registration and health check goes through it.
    control = next(m for m in MEMBERS if m.name == CONTROL_PLANE)
    operated = list(selected) if control in selected else list(selected) + [control]
    node_command = Path(node.path).as_posix() if node.ok else "node"
    states: Dict[str, MemberState] = {
        m.name: resolve_member(host, m, registered, dest, node_command, options.repair) for m in operated
    }

    blocked = not node.ok
    if not node.ok:
        errors.append(f"node: {node.detail}")

    # -- install ---------------------------------------------------------------------------
    missing = [m for m in operated if states[m.name].status == "missing"]
    if missing and options.no_clone:
        for m in missing:
            states[m.name].detail = "--no-clone was given"
    elif missing and node.ok:
        if not git.ok:
            blocked = True
            errors.append(f"git: {git.detail}")
        elif not write:
            for m in missing:
                states[m.name].detail = f"would clone {m.repo}"
        else:
            for index, m in enumerate(missing, 1):
                say(f"[{index}/{len(missing)}] {m.name}: git clone {m.repo}")
                done, detail = install_member(host, m, dest, git.path)
                state = states[m.name]
                if done:
                    root = dest / m.repo_name
                    state.status, state.root, state.source, state.detail = "installed", str(root), "clone", detail
                    state.entry = _manifest_entry(m, root, node_command, [(root / m.entry).as_posix()])
                else:
                    state.status, state.detail = "failed", detail
                    errors.append(f"{m.name}: {detail}")
    if write and not blocked:
        for m in operated:
            state = states[m.name]
            if state.status == "missing":
                errors.append(f"{m.name}: not installed ({state.detail or 'not found'})")
            elif state.status == "stale":
                errors.append(f"{m.name}: {state.detail}")

    # -- manifest --------------------------------------------------------------------------
    additions: List[Tuple[Dict[str, Any], bool]] = []
    for m in selected:
        state = states[m.name]
        if state.entry is not None and state.status in ("adopted", "found", "installed"):
            additions.append((state.entry, state.replace))
    if SELF_NAME not in registered:
        additions.append((self_manifest_entry(), False))
    planned = copy.deepcopy(manifest)
    changed = [] if manifest_error else merge_manifest(planned, additions)
    manifest_saved = False
    if write and changed:
        try:
            write_manifest(manifest_file, planned)
            manifest_saved = True
            say(f"manifest: {len(changed)} entr{'y' if len(changed) == 1 else 'ies'} written to {manifest_file}")
        except OSError as err:
            errors.append(f"manifest: cannot write {manifest_file}: {err}")
    in_manifest = set(registered)
    if manifest_saved:
        in_manifest |= set(changed)

    # -- register --------------------------------------------------------------------------
    script: Optional[Path] = None
    control_state = states[CONTROL_PLANE]
    if control_state.status in _RESOLVED and control_state.root:
        candidate = Path(control_state.root) / control.entry
        if _is_file(candidate):
            script = candidate

    ready = [m.name for m in selected if states[m.name].status in _RESOLVED and m.name in in_manifest]
    register_names = list(ready)
    if SELF_NAME in in_manifest:
        register_names.append(SELF_NAME)

    registration: Dict[str, Any] = {"ran": False, "dry_run": not write, "counts": {}, "files": [], "skipped": ""}
    if not node.ok:
        registration["skipped"] = "Node.js is not available"
    elif script is None:
        registration["skipped"] = "Constellation is not installed yet"
    elif manifest_error:
        registration["skipped"] = "the manifest is unreadable"
    elif not register_names:
        registration["skipped"] = "no server is in the manifest yet"
    elif not target_agents:
        registration["skipped"] = "no supported coding agent detected (pass --agents claude,cursor,...)"
    else:
        argv = ["register", *register_names, "--agents=" + ",".join(target_agents), "--json"]
        if not write:
            argv.append("--dry-run")
        say(f"constellation: register {' '.join(register_names)} -> {', '.join(target_agents)}")
        code, payload, text = _constellation(host, node.path, script, argv)
        if code != 0 or payload is None:
            errors.append(f"register: Constellation failed: {_last_line(text)}")
        else:
            registration.update(_summarise_diffs(payload))
            registration["ran"] = True

    # -- health ----------------------------------------------------------------------------
    health: List[Dict[str, Any]] = []
    if write and not options.no_health and node.ok and script is not None and ready:
        say(f"constellation: health {' '.join(ready)}")
        code, payload, text = _constellation(
            host, node.path, script, ["health", *ready, "--json", f"--timeout={HEALTH_TIMEOUT_MS}"]
        )
        health = _summarise_health(payload)
        if payload is None:
            errors.append(f"health: Constellation failed: {_last_line(text)}")
        for item in health:
            if item["status"] != "ok":
                errors.append(f"{item['name']}: health check failed: {item['error'] or 'no reply'}")

    # -- report ----------------------------------------------------------------------------
    pending: List[str] = []
    for m in operated:
        state = states[m.name]
        if state.status in ("missing", "stale", "failed"):
            pending.append(f"{m.name}: {state.status}" + (f" - {state.detail}" if state.detail else ""))
    counts = registration.get("counts") or {}
    waiting = counts.get("create", 0) + counts.get("update", 0)
    if not write and changed:
        pending.append("manifest: " + ", ".join(changed) + " to be written")
    if not write and waiting:
        pending.append(f"agents: {waiting} registration change(s) to apply")

    if not node.ok:
        next_steps.append(f"Install Node.js >= {MIN_NODE[0]}.{MIN_NODE[1]} (https://nodejs.org), then run setup again.")
    if any(state.status == "stale" for state in states.values()):
        next_steps.append("Run setup with --repair to replace manifest entries whose files are gone.")
    if not target_agents:
        next_steps.append("No coding agent detected: install one, or pass --agents claude,cursor,antigravity,codex.")
    if write and (registration["files"] or manifest_saved):
        next_steps.append("Restart your coding agent or open a new conversation so it loads the new MCP servers.")
    if not write and pending:
        next_steps.append("Run `smart-drive setup` without --check/--dry-run to apply.")

    needs_setup = bool(pending)
    return {
        "ok": not blocked and not errors and not (mode == "check" and needs_setup),
        "mode": mode,
        "blocked": blocked,
        "needs_setup": needs_setup,
        "platform": {"system": host.system, "python": platform.python_version()},
        "tools": {"node": node.to_dict(), "git": git.to_dict()},
        "dest": str(dest),
        "agents": agents,
        "target_agents": target_agents,
        "manifest": {
            "path": str(manifest_file),
            "added": [n for n in changed if n not in registered],
            "replaced": [n for n in changed if n in registered],
            "written": manifest_saved,
        },
        "members": [states[m.name].to_dict() for m in operated],
        "registration": registration,
        "health": health,
        "pending": pending,
        "errors": errors,
        "next_steps": next_steps,
    }


def exit_code(report: Mapping[str, Any]) -> int:
    """0 done, 1 something failed, 2 a prerequisite is missing, 3 (``--check``) setup is still needed."""
    if report["blocked"]:
        return 2
    if report["errors"]:
        return 1
    if report["mode"] == "check" and report["needs_setup"]:
        return 3
    return 0


def format_report(report: Mapping[str, Any]) -> str:
    """Renders the report as terminal text."""
    info = report["platform"]
    lines: List[str] = [
        f"SmartDrive MCP ecosystem setup [{report['mode']}] - {info['system']}, python {info['python']}"
    ]
    for key in ("node", "git"):
        tool = report["tools"][key]
        if tool["ok"]:
            lines.append(f"  {key:<8}{tool['version']}  {tool['path']}")
        else:
            lines.append(f"  {key:<8}MISSING - {tool['detail']}")
    lines.append(f"  agents  {', '.join(report['target_agents']) or 'none detected'}")
    lines.append(f"  clones  {report['dest']}")
    lines.append("")
    lines.append("Servers")
    for member in report["members"]:
        lines.append(f"  {member['name']:<15}{member['status']:<11}{member['path'] or member['detail']}")
    manifest = report["manifest"]
    lines.append("")
    lines.append(
        f"Manifest  {manifest['path']}  (+{len(manifest['added'])} added, {len(manifest['replaced'])} replaced)"
    )
    registration = report["registration"]
    if registration["ran"]:
        verb = "would change" if registration["dry_run"] else "changed"
        if registration["files"]:
            for item in registration["files"]:
                lines.append(
                    f"Register  {item['agent']}: {verb} {item['file']} "
                    f"({item.get('create', 0)} new, {item.get('update', 0)} updated)"
                )
        else:
            lines.append("Register  every selected agent is already up to date")
    elif registration["skipped"]:
        lines.append(f"Register  skipped: {registration['skipped']}")
    if report["health"]:
        answered = sum(1 for item in report["health"] if item["status"] == "ok")
        lines.append(f"Health    {answered}/{len(report['health'])} servers answered over stdio")
    for message in report["errors"]:
        lines.append(f"ERROR     {message}")
    for message in report["pending"]:
        lines.append(f"Pending   {message}")
    for message in report["next_steps"]:
        lines.append(f"Next      {message}")
    return "\n".join(lines)


__all__ = [
    "Host",
    "Options",
    "agent_config_files",
    "default_dest",
    "default_host",
    "detect_agents",
    "exit_code",
    "format_report",
    "install_member",
    "load_manifest",
    "manifest_path",
    "merge_manifest",
    "parse_version",
    "probe_git",
    "probe_node",
    "resolve_member",
    "run_setup",
    "self_manifest_entry",
    "write_manifest",
]
