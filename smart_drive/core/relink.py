"""smart_drive.core.relink - Keep Coding Agents Working After Folder Relocations.

When ``smart-drive organize --apply``, ``smart-drive classify --apply``, or ``ssd_auto_organize``
moves folders on an exFAT SSD, no symlink or junction can be left at the old path. Coding agents
remember projects, sessions, worktrees, and MCP server bundles by absolute path.

This module rewrites remembered paths under moved folders across supported coding agent configs
(Claude Code, Claude desktop, Codex, Gemini CLI, Antigravity, Cursor, Windsurf, Constellation,
and workspace ``.mcp.json``), carries Claude Code project history and memory over to the new
project slug, repairs git worktrees, and updates moved Python virtualenv ``.pth`` / ``.egg-link``
entries.

Safety invariants:
- Rolling backup (``<file>.bak.smart-drive``) before modifying config files (except Claude desktop
  session files, where extra files must not be left).
- Atomic temp-file write + fsync + replace on the resolved real path (preserving dotfiles symlinks).
- The SSD is never scanned: only known agent configuration paths and moved folders are inspected.
- 100% Python Standard Library (Python >= 3.9).
"""

from __future__ import annotations

import ast
import csv
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union
import urllib.parse

try:
    import tomllib
except ImportError:  # Python < 3.11: only the line-level duplicate guard protects TOML files
    tomllib = None  # type: ignore[assignment]

from smart_drive.core.processes import normalize_process_name, probe_running_processes
from smart_drive.core.root import same_file
from smart_drive.core.system_refs import RegistryReader, SystemRefIndex, parse_lnk_file

Move = Tuple[str, str]
BACKUP_SUFFIX = ".bak.smart-drive"
JOURNAL_NAME = "moves.jsonl"

_CASE_INSENSITIVE = sys.platform in ("win32", "darwin")
_TOML_QUOTED_RE = re.compile(r"'([^'\r\n]*)'|\"((?:[^\"\\\r\n]|\\.)*)\"")
_XML_QUOTED_RE = re.compile(r'"([^"\r\n]*)"|\'([^\'\r\n]*)\'')
_UNREADABLE_DRIVE_RE = re.compile(r"^[A-Za-z]:[^\\/]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]")
_WIN_INVALID_COMP_RE = re.compile(r'[:*?"<>|]')
_REPARSE_POINT = 0x400

VSCODE_FAMILY_APPS: Tuple[Tuple[str, str], ...] = (
    ("Code", "VS Code"),
    ("Code - Insiders", "Code - Insiders"),
    ("VSCodium", "VSCodium"),
    ("Cursor", "Cursor"),
    ("Windsurf", "Windsurf"),
    ("Antigravity", "Antigravity"),
)

AGENT_PROCESSES: Dict[str, Set[str]] = {
    "claude": {"Claude desktop", "Claude Code"},
    "codex": {"Codex"},
    "antigravity": {"Antigravity"},
    "cursor": {"Cursor"},
    "windsurf": {"Windsurf"},
    "code": {"VS Code"},
    "code - insiders": {"Code - Insiders"},
    "code-insiders": {"Code - Insiders"},
    "vscodium": {"VSCodium"},
    "codium": {"VSCodium"},
    "idea64": {"JetBrains"},
    "idea": {"JetBrains"},
    "pycharm64": {"JetBrains"},
    "pycharm": {"JetBrains"},
    "webstorm64": {"JetBrains"},
    "webstorm": {"JetBrains"},
    "clion64": {"JetBrains"},
    "clion": {"JetBrains"},
    "rider64": {"JetBrains"},
    "rider": {"JetBrains"},
    "goland64": {"JetBrains"},
    "goland": {"JetBrains"},
    "phpstorm64": {"JetBrains"},
    "phpstorm": {"JetBrains"},
    "datagrip64": {"JetBrains"},
    "datagrip": {"JetBrains"},
    "rustrover64": {"JetBrains"},
    "rustrover": {"JetBrains"},
    "studio64": {"JetBrains"},
    "studio": {"JetBrains"},
    "obsidian": {"Obsidian"},
}


def _running_processes() -> Optional[Set[str]]:
    """Returns running process names from `tasklist` (Windows) or `ps` (POSIX), or None if unreadable."""
    return probe_running_processes(run_fn=subprocess.run)


def _labels_of(process_name: str) -> Set[str]:
    """The coding agent labels a process name such as claude, Claude.exe or /usr/bin/codex stands for."""
    return set(AGENT_PROCESSES.get(normalize_process_name(process_name), ()))


def _running_agent_labels() -> Optional[Set[str]]:
    """Labels of the coding agents whose application is running, or None if the process list is unreadable."""
    try:
        raw_names = _running_processes()
    except Exception:
        return None
    if raw_names is None:
        return None
    active: Set[str] = set()
    for raw in raw_names:
        active |= _labels_of(raw)
    return active


def calling_agents() -> Set[str]:
    """Labels of the coding agents this process runs under (POSIX process tree; CLAUDECODE everywhere).

    relink cannot wait for these to close: they are waiting for relink.
    """
    labels = set(AGENT_PROCESSES["claude"]) if os.environ.get("CLAUDECODE") else set()
    if sys.platform == "win32":
        return labels
    try:
        done = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return labels
    parents: Dict[int, Tuple[int, str]] = {}
    for line in done.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            parents[int(fields[0])] = (int(fields[1]), fields[2])
    pid = os.getpid()
    seen: Set[int] = set()
    while pid in parents and pid not in seen:
        seen.add(pid)
        pid = parents[pid][0]
        if pid in parents:
            labels |= _labels_of(parents[pid][1])
    return labels


def running_agents() -> Set[str]:
    """Returns the set of coding agent labels whose application is currently running."""
    return _running_agent_labels() or set()


def agents_still_running(agents: Iterable[str]) -> List[str]:
    """Those of `agents` whose application is running; all of them while the process list is unreadable."""
    wanted = set(agents)
    running = _running_agent_labels()
    return sorted(wanted if running is None else wanted & running)


def _safe_home_dir() -> Path:
    """Safely determines user home directory with fallbacks for stripped environments."""
    for env_key in ("USERPROFILE", "HOME"):
        val = os.environ.get(env_key)
        if val:
            return Path(val)
    try:
        return Path.home()
    except Exception:
        return Path("C:/Users/Default") if sys.platform == "win32" else Path("/tmp")


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _split(text: str) -> Optional[Tuple[str, List[str]]]:
    """Splits an absolute path string into (anchor, components), or None if not a path."""
    if not isinstance(text, str) or not text or _CONTROL_CHAR_RE.search(text):
        return None
    if len(text) >= 2 and text[0].isalpha() and text[1] == ":" and (len(text) == 2 or text[2] in "\\/"):
        anchor = text[:2]
        rest = text[2:]
    elif text[0] == "/" and (len(text) == 1 or text[1] not in "\\/"):
        anchor = "/"
        rest = text[1:]
    else:
        return None
    names = [part for part in re.split(r"[\\/]+", rest) if part]
    if anchor != "/" and any(_WIN_INVALID_COMP_RE.search(part) for part in names):
        return None
    return anchor, names


def _nfc(names: Sequence[str]) -> List[str]:
    """names in composed Unicode form: macOS lists exFAT names decomposed, agents store them composed."""
    return [unicodedata.normalize("NFC", n) for n in names]


def _fold(name: str) -> str:
    """One folder name for case- and Unicode-form-insensitive comparison."""
    return unicodedata.normalize("NFC", name).casefold()


def _key(anchor: str, names: Sequence[str]) -> Tuple[str, ...]:
    """Normalized comparison key for (anchor, names); Unicode NFC, case-insensitive on Windows and macOS."""
    if _CASE_INSENSITIVE:
        return (anchor.casefold(), *(n.casefold() for n in _nfc(names)))
    return (anchor, *_nfc(names))


def _split_pathsep(text: str) -> List[str]:
    """Splits text on os.pathsep while preserving Windows drive letters (X:\\ or X:/) when pathsep is ':'."""
    if os.pathsep not in text:
        return [text]
    raw_parts = text.split(os.pathsep)
    if os.pathsep != ":":
        return raw_parts
    merged: List[str] = []
    i = 0
    while i < len(raw_parts):
        part = raw_parts[i]
        if (
            len(part) == 1
            and part.isalpha()
            and i + 1 < len(raw_parts)
            and (not raw_parts[i + 1] or raw_parts[i + 1][0] in "\\/")
        ):
            merged.append(part + ":" + raw_parts[i + 1])
            i += 2
        else:
            merged.append(part)
            i += 1
    return merged


def _decode_file_uri(uri: str) -> Optional[str]:
    """Decodes a file:/// URI into a plain filesystem path, or None if not a file:/// URI."""
    if not isinstance(uri, str) or uri[:8].lower() != "file:///":
        return None
    rest = uri[8:]
    if not rest:
        return None
    if len(rest) >= 4 and rest[0].isalpha() and rest[1:4] in ("%3A", "%3a") and (len(rest) == 4 or rest[4] == "/"):
        return f"{rest[0]}:{urllib.parse.unquote(rest[4:]) or '/'}"
    if len(rest) >= 2 and rest[0].isalpha() and rest[1] == ":" and (len(rest) == 2 or rest[2] == "/"):
        return f"{rest[0]}:{urllib.parse.unquote(rest[2:]) or '/'}"
    return "/" + urllib.parse.unquote(rest)


class PathMapper:
    """Rewrites paths under moved folders while preserving separator and casing style."""

    def __init__(self, moves: Iterable[Move]) -> None:
        rules: List[Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, List[str]], Tuple[str, List[str]]]] = []
        seen_keys: Set[Tuple[str, ...]] = set()
        needles: Set[str] = set()
        has_non_ascii = False

        for old_raw, new_raw in moves:
            old_parsed = _split(old_raw)
            new_parsed = _split(new_raw)
            if old_parsed is None or new_parsed is None:
                continue
            old_anchor, old_names = old_parsed
            new_anchor, new_names = new_parsed
            # Ignore drive roots or empty component lists.
            if not old_names or not new_names:
                continue
            old_k = _key(old_anchor, old_names)
            new_k = _key(new_anchor, new_names)
            if old_k == new_k or old_k in seen_keys:
                continue
            seen_keys.add(old_k)
            rules.append((old_k, new_k, (old_anchor, old_names), (new_anchor, new_names)))
            last_name = old_names[-1]
            if not last_name.isascii():
                has_non_ascii = True
            else:
                needles.add(last_name.casefold())
                needles.add(urllib.parse.quote(last_name).casefold())
                needles.add(html.escape(last_name, quote=True).casefold())
                needles.add(html.escape(last_name, quote=False).replace("'", "&apos;").casefold())

        # Deepest moved folder wins when moves nest.
        rules.sort(key=lambda item: len(item[0]), reverse=True)
        self._rules = rules
        self._needles = tuple(needles)
        self._has_non_ascii = has_non_ascii

    def __bool__(self) -> bool:
        return bool(self._rules)

    def mentions(self, text: str) -> bool:
        """Cheap pre-check: True if text could possibly mention any moved folder."""
        if not self._rules:
            return False
        if self._has_non_ascii:
            return True
        folded = text.casefold()
        return any(needle in folded for needle in self._needles)

    def map(self, text: str) -> Optional[str]:
        """Rewrites one path string if it is inside a moved folder, else returns None."""
        parsed = _split(text)
        if parsed is None:
            return None
        anchor, names = parsed
        if not names:
            return None
        key = _key(anchor, names)
        for old_key, new_key, (old_anchor, old_names), (new_anchor, new_names) in self._rules:
            if key[:len(old_key)] != old_key:
                continue
            if len(new_key) > len(old_key) and new_key[:len(old_key)] == old_key and key[:len(new_key)] == new_key:
                # The new place lies inside the old one (OLD -> OLD/sub) and this path is there already:
                # mapping it again would add one more level each time relink runs.
                return None
            sep = "/" if anchor == "/" or "\\" not in text else "\\"
            tail = sep.join(new_names + names[len(old_key) - 1:])
            mapped = "/" + tail if new_anchor == "/" else new_anchor + sep + tail
            if text[-1] in "\\/" and len(text) > len(anchor) + 1:
                mapped += sep
            # Lowercase only when the agent lowercased a path that originally had capitals (e.g. Codex/Gemini).
            lowered = text.islower() and (anchor, _nfc(names[:len(old_names)])) != (old_anchor, _nfc(old_names))
            result = mapped.lower() if lowered else mapped
            if not result.isascii():
                # Write the Unicode form the agent used: decomposed only if its own string was.
                decomposed = unicodedata.is_normalized("NFD", text) and not unicodedata.is_normalized("NFC", text)
                result = unicodedata.normalize("NFD" if decomposed else "NFC", result)
            return None if result == text else result
        return None

    def map_uri(self, uri: str) -> Optional[str]:
        """Rewrites a file:/// URI if its path is inside a moved folder, preserving URI style."""
        mapped_uri, _ = self._map_uri_with_original(uri)
        return mapped_uri

    def _map_uri_with_original(self, uri: str) -> Tuple[Optional[str], Optional[str]]:
        if not isinstance(uri, str) or uri[:8].lower() != "file:///":
            return None, None
        scheme = uri[:8]
        rest = uri[8:]
        if not rest:
            return None, None

        if len(rest) >= 4 and rest[0].isalpha() and rest[1:4] in ("%3A", "%3a") and (len(rest) == 4 or rest[4] == "/"):
            orig_drive: Optional[str] = rest[0]
            orig_colon = rest[1:4]
            raw_tail = rest[4:]
        elif len(rest) >= 2 and rest[0].isalpha() and rest[1] == ":" and (len(rest) == 2 or rest[2] == "/"):
            orig_drive = rest[0]
            orig_colon = ":"
            raw_tail = rest[2:]
        else:
            orig_drive = None
            orig_colon = ""
            raw_tail = "/" + rest

        unquoted_tail = urllib.parse.unquote(raw_tail)
        if orig_drive is not None:
            decoded_path = f"{orig_drive}:{unquoted_tail or '/'}"
            # Use uppercase drive letter when probing self.map() so VS Code's standard lowercase
            # drive letter ('e%3A') does not cause map() to lowercase the entire mapped path.
            probe_path = f"{orig_drive.upper()}:{unquoted_tail or '/'}"
            mapped = self.map(probe_path)
            if mapped is None:
                return None, None
            if unquoted_tail and unquoted_tail.islower() and mapped[2:].lower() != mapped[2:]:
                parsed_probe = _split(probe_path)
                if parsed_probe is not None:
                    p_key = _key(*parsed_probe)
                    for old_key, (_, old_names), _ in self._rules:
                        if p_key[: len(old_key)] == old_key:
                            if parsed_probe[1][: len(old_names)] != old_names:
                                mapped = mapped[:2] + mapped[2:].lower()
                            break
            mapped_drive = mapped[0].upper() if orig_drive.isupper() else mapped[0].lower()
            mapped_tail = mapped[2:].replace("\\", "/")
            if not raw_tail and mapped_tail == "/":
                mapped_tail = ""
        else:
            decoded_path = unquoted_tail
            mapped = self.map(decoded_path)
            if mapped is None:
                return None, None
            mapped_drive = ""
            mapped_tail = mapped.replace("\\", "/")

        if "%" in raw_tail or any(ord(ch) <= 0x20 or ch in "#?%" for ch in mapped_tail):
            encoded_tail = urllib.parse.quote(mapped_tail, safe="/:@!$&'()*+,;=-._~")
        else:
            encoded_tail = mapped_tail

        if orig_drive is not None:
            result_uri = f"{scheme}{mapped_drive}{orig_colon}{encoded_tail}"
        else:
            result_uri = f"{scheme}{encoded_tail.lstrip('/')}"

        return (None, None) if result_uri == uri else (result_uri, decoded_path)

    def map_value(self, text: str) -> Optional[str]:
        """Rewrites one path string, or several paths joined by os.pathsep (e.g. PYTHONPATH)."""
        mapped, _ = self._map_value_with_originals(text)
        return mapped

    def _map_value_with_originals(self, text: str) -> Tuple[Optional[str], List[str]]:
        if "\x00" in text:
            head, tail = text.split("\x00", 1)
            mapped_head = self.map(head)
            if mapped_head is not None:
                return f"{mapped_head}\x00{tail}", [head]
            return None, []
        if text[:8].lower() == "file:///":
            mapped_uri, orig_path = self._map_uri_with_original(text)
            if mapped_uri is not None and orig_path is not None:
                return mapped_uri, [orig_path]
            return None, []
        if os.pathsep in text and not text.startswith(("file:", "http:", "https:", "-")):
            pieces = _split_pathsep(text)
            if len(pieces) > 1:
                changed = False
                out: List[str] = []
                originals: List[str] = []
                for piece in pieces:
                    m = self.map(piece)
                    if m is not None:
                        out.append(m)
                        originals.append(piece)
                        changed = True
                    else:
                        out.append(piece)
                if changed:
                    return os.pathsep.join(out), originals
                return None, []
        single = self.map(text)
        if single is not None:
            return single, [text]
        return None, []


def _claude_slug_text(path: str) -> str:
    """path with each UTF-16 unit outside [A-Za-z0-9] replaced by '-', as Claude Code's JavaScript does it."""
    return "".join(ch if ch.isascii() and ch.isalnum() else "--" if ord(ch) > 0xFFFF else "-" for ch in path)


def claude_slug(path: str) -> Optional[str]:
    """Converts a project path into its Claude Code project folder slug, or None if > 200 UTF-16 units."""
    slug = _claude_slug_text(path) if path else ""
    if not slug or len(slug) > 200:
        return None
    return slug


@dataclass(frozen=True)
class AgentFile:
    """Represents a configuration or metadata file belonging to a coding agent."""

    path: Path
    agent: str
    kind: str = "json"
    backup: bool = True


def _resolve_locations(
    home: Optional[Path] = None,
    app_data: Optional[Path] = None,
) -> Tuple[Path, Path, bool]:
    """Resolves (home, app_data, use_env). When home is passed, env overrides are ignored."""
    if home is not None:
        resolved_home = Path(home)
        use_env = False
        if app_data is not None:
            resolved_app_data = Path(app_data)
        elif sys.platform == "win32":
            resolved_app_data = resolved_home / "AppData" / "Roaming"
        elif sys.platform == "darwin":
            resolved_app_data = resolved_home / "Library" / "Application Support"
        else:
            resolved_app_data = resolved_home / ".config"
        return resolved_home, resolved_app_data, use_env

    use_env = True
    home_env = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    resolved_home = Path(home_env) if home_env else _safe_home_dir()

    if app_data is not None:
        resolved_app_data = Path(app_data)
    elif sys.platform == "win32":
        appdata_env = os.environ.get("APPDATA")
        resolved_app_data = Path(appdata_env) if appdata_env else (resolved_home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        resolved_app_data = resolved_home / "Library" / "Application Support"
    else:
        xdg_env = os.environ.get("XDG_CONFIG_HOME")
        resolved_app_data = Path(xdg_env) if xdg_env else (resolved_home / ".config")

    return resolved_home, resolved_app_data, use_env


def _resolve_local_app_data(resolved_home: Path, use_env: bool) -> Path:
    """Resolves Windows LocalAppData (<home>\\AppData\\Local when home is passed, else %LOCALAPPDATA%)."""
    if use_env:
        local_env = os.environ.get("LOCALAPPDATA")
        if local_env:
            return Path(local_env)
    return resolved_home / "AppData" / "Local"


def _msix_claude_dirs(local_app_data: Path) -> List[Path]:
    """Returns existing MSIX Claude desktop Roaming\\Claude directories under <LocalAppData>\\Packages\\Claude_*."""
    packages_dir = local_app_data / "Packages"
    if not _is_dir(packages_dir):
        return []
    try:
        pkg_dirs = sorted(packages_dir.glob("Claude_*"))
    except OSError:
        return []
    out: List[Path] = []
    for pkg_dir in pkg_dirs:
        claude_dir = pkg_dir / "LocalCache" / "Roaming" / "Claude"
        if _is_dir(claude_dir):
            out.append(claude_dir)
    return out


def _add_claude_desktop_files(claude_dir: Path, candidates: List[AgentFile]) -> None:
    """Appends Claude desktop config, git-worktrees, and session files under claude_dir to candidates."""
    candidates.append(AgentFile(claude_dir / "claude_desktop_config.json", "Claude desktop", "json", True))
    candidates.append(AgentFile(claude_dir / "git-worktrees.json", "Claude desktop", "json", True))
    sessions_dir = claude_dir / "claude-code-sessions"
    if _is_dir(sessions_dir):
        try:
            for session_file in sorted(sessions_dir.rglob("*.json")):
                if not session_file.name.endswith(BACKUP_SUFFIX):
                    candidates.append(AgentFile(session_file, "Claude desktop", "json", False))
        except OSError:
            pass


def agent_files(
    root: Optional[str] = None,
    home: Optional[Path] = None,
    app_data: Optional[Path] = None,
) -> List[AgentFile]:
    """Returns existing coding agent configuration files that hold remembered paths."""
    resolved_home, resolved_app_data, use_env = _resolve_locations(home, app_data)

    if use_env:
        ag_override = os.environ.get("ANTIGRAVITY_HOME") or os.environ.get("GEMINI_HOME")
        ag_mcp = (Path(ag_override) / "mcp_config.json") if ag_override else (
            resolved_home / ".gemini" / "antigravity" / "mcp_config.json"
        )
        const_override = os.environ.get("CONSTELLATION_HOME")
        const_eco = (Path(const_override) / "ecosystem.json") if const_override else (
            resolved_home / ".constellation" / "ecosystem.json"
        )
    else:
        ag_mcp = resolved_home / ".gemini" / "antigravity" / "mcp_config.json"
        const_eco = resolved_home / ".constellation" / "ecosystem.json"

    candidates: List[AgentFile] = [
        AgentFile(resolved_home / ".claude.json", "Claude Code", "json", True),
    ]
    _add_claude_desktop_files(resolved_app_data / "Claude", candidates)
    if sys.platform == "win32":
        local_app_data = _resolve_local_app_data(resolved_home, use_env)
        for msix_claude in _msix_claude_dirs(local_app_data):
            _add_claude_desktop_files(msix_claude, candidates)

    gemini_dir = resolved_home / ".gemini"
    candidates.extend([
        AgentFile(resolved_home / ".codex" / "config.toml", "Codex", "toml", True),
        AgentFile(gemini_dir / "settings.json", "Gemini CLI", "json", True),
        AgentFile(gemini_dir / "projects.json", "Gemini CLI", "json", True),
        AgentFile(gemini_dir / "trustedFolders.json", "Gemini CLI", "json", True),
        AgentFile(ag_mcp, "Antigravity", "json", True),
        AgentFile(resolved_home / ".cursor" / "mcp.json", "Cursor", "json", True),
        AgentFile(resolved_home / ".codeium" / "windsurf" / "mcp_config.json", "Windsurf", "json", True),
        AgentFile(const_eco, "Constellation", "json", True),
    ])

    for app_dir_name, agent_label in VSCODE_FAMILY_APPS:
        gs_dir = resolved_app_data / app_dir_name / "User" / "globalStorage"
        candidates.append(AgentFile(gs_dir / "storage.json", agent_label, "json", True))
        candidates.append(AgentFile(gs_dir / "state.vscdb", agent_label, "vscdb", True))

    jb_root = resolved_app_data / "JetBrains"
    if _is_dir(jb_root):
        try:
            jb_dirs = sorted(jb_root.iterdir(), key=lambda p: p.name.casefold())
        except OSError:
            jb_dirs = []
        for prod_dir in jb_dirs:
            if not _is_dir(prod_dir):
                continue
            opts_dir = prod_dir / "options"
            candidates.append(AgentFile(opts_dir / "recentProjects.xml", "JetBrains", "jetbrains_xml", True))
            candidates.append(AgentFile(opts_dir / "trusted-paths.xml", "JetBrains", "jetbrains_xml", True))

    candidates.append(AgentFile(resolved_app_data / "obsidian" / "obsidian.json", "Obsidian", "json", True))

    if root:
        candidates.append(AgentFile(Path(root) / ".mcp.json", "SSD .mcp.json", "json", True))

    seen_ids: Set[Tuple[Any, ...]] = set()
    deduped: List[AgentFile] = []
    for af in candidates:
        try:
            st = os.stat(af.path)
        except (OSError, ValueError):
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_ino:
            file_id: Tuple[Any, ...] = (st.st_dev, st.st_ino)
        else:
            try:
                file_id = ("path", os.path.normcase(os.path.realpath(af.path)))
            except (OSError, ValueError, RuntimeError):
                continue
        if file_id in seen_ids:
            continue
        seen_ids.add(file_id)
        deduped.append(af)
    return deduped


@dataclass
class RelinkReport:
    """Summary of path updates, history migrations, worktree repairs, warnings, and errors."""

    dry_run: bool = False
    updated: List[Dict[str, Any]] = field(default_factory=list)
    history: List[Dict[str, str]] = field(default_factory=list)
    worktrees: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)

    @property
    def agents(self) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for entry in self.updated:
            agent = str(entry["agent"])
            totals[agent] = totals.get(agent, 0) + int(entry["paths"])
        return {k: totals[k] for k in sorted(totals)}

    @property
    def changed(self) -> bool:
        return bool(self.updated or self.history or self.worktrees)

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["agents"] = self.agents
        return data


def _merge(primary: Any, fallback: Any) -> Any:
    """Merges two JSON values on key collision; primary (new place) wins, fallback fills gaps."""
    if isinstance(primary, dict) and isinstance(fallback, dict):
        merged = dict(fallback)
        for key, value in primary.items():
            merged[key] = _merge(value, fallback[key]) if key in fallback else value
        return merged
    return fallback if primary in (None, "", [], {}) else primary


def _rewrite_json_value(
    value: Any,
    mapper: PathMapper,
    originals: List[str],
) -> Tuple[Any, int]:
    """Recursively rewrites strings and object keys in parsed JSON data."""
    if isinstance(value, str):
        mapped, origs = mapper._map_value_with_originals(value)
        if mapped is not None:
            originals.extend(origs)
            return mapped, len(origs)
        return value, 0

    if isinstance(value, list):
        total = 0
        out_list: List[Any] = []
        for item in value:
            new_item, c = _rewrite_json_value(item, mapper, originals)
            out_list.append(new_item)
            total += c
        return out_list, total

    if isinstance(value, dict):
        total = 0
        out_dict: Dict[str, Any] = {}
        # Track whether an entry in out_dict came from an unmoved (primary) key or a moved (fallback) key.
        is_primary_key: Dict[str, bool] = {}

        for k, v in value.items():
            # Permission settings are never rewritten (e.g. epitaxy-folder-permission-mode / epitaxy-perm-mode-acks).
            if isinstance(k, str) and "\x00" not in k and _split(k) is None and "perm" in k.casefold():
                if k not in out_dict:
                    out_dict[k] = v
                    is_primary_key[k] = True
                else:
                    out_dict[k] = _merge(v, out_dict[k])
                    is_primary_key[k] = True
                continue

            new_v, c_val = _rewrite_json_value(v, mapper, originals)
            total += c_val

            if isinstance(k, str):
                mapped_k, origs = mapper._map_value_with_originals(k)
            else:
                mapped_k, origs = None, []

            if mapped_k is not None:
                originals.extend(origs)
                total += len(origs)
                target_k = mapped_k
                moved_key = True
            else:
                target_k = k
                moved_key = False

            if target_k not in out_dict:
                out_dict[target_k] = new_v
                is_primary_key[target_k] = not moved_key
            else:
                if not moved_key:
                    # Current entry is the agent's own entry for the new place (primary).
                    out_dict[target_k] = _merge(new_v, out_dict[target_k])
                    is_primary_key[target_k] = True
                elif is_primary_key.get(target_k, False):
                    # Existing entry in out_dict is primary; current moved entry is fallback.
                    out_dict[target_k] = _merge(out_dict[target_k], new_v)
                else:
                    # Both were moved to the same key; earlier entry acts as primary.
                    out_dict[target_k] = _merge(out_dict[target_k], new_v)

        return out_dict, total

    return value, 0


def _serialize_json_like(data: Any, original_text: str, has_bom: bool) -> bytes:
    """Serializes JSON matching the original file's compactness, indentation, line endings, newline, and BOM."""
    eol = "\r\n" if "\r\n" in original_text else "\n"
    trailing_nl = original_text.endswith(("\r\n", "\n"))
    body = original_text.rstrip("\r\n")

    compact = "\n" not in body
    indent: Union[int, str] = 2
    if not compact:
        for line in body.splitlines()[1:]:
            stripped = line.lstrip(" \t")
            if stripped and len(stripped) < len(line):
                indent = line[: len(line) - len(stripped)]
                break

    def _dump(ensure_ascii: bool) -> str:
        if compact:
            dumped = json.dumps(data, ensure_ascii=ensure_ascii, separators=(",", ":"))
        else:
            dumped = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
        if eol == "\r\n":
            dumped = dumped.replace("\r\n", "\n").replace("\n", "\r\n")
        if trailing_nl:
            dumped += eol
        return dumped

    prefix = b"\xef\xbb\xbf" if has_bom else b""
    try:
        return prefix + _dump(False).encode("utf-8")
    except UnicodeEncodeError:
        return prefix + _dump(True).encode("utf-8")


def _process_json_file(
    af: AgentFile,
    mapper: PathMapper,
    report: RelinkReport,
    claude_originals: List[str],
) -> Tuple[Optional[bytes], int]:
    """Reads and rewrites a JSON AgentFile. Returns (new_bytes_or_None, rewritten_count)."""
    try:
        raw_bytes = af.path.read_bytes()
    except OSError as err:
        report.errors.append({"file": str(af.path), "error": str(err)})
        return None, 0

    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        report.warnings.append(f"{af.path}: invalid UTF-8 ({err})")
        return None, 0

    if not mapper.mentions(text):
        return None, 0

    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        report.warnings.append(f"{af.path}: invalid JSON ({err})")
        return None, 0

    originals: List[str] = []
    new_data, count = _rewrite_json_value(data, mapper, originals)
    if count == 0:
        return None, 0

    serialized = _serialize_json_like(new_data, text, has_bom)
    if af.agent in ("Claude Code", "Claude desktop"):
        claude_originals.extend(originals)

    return serialized, count


_TOML_UNESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
_TOML_ESCAPES = {value: "\\" + name for name, value in _TOML_UNESCAPES.items()}


def _toml_unescape(body: str) -> Optional[str]:
    """The value of a TOML basic string from its body, or None if the body has an escape TOML 1.0 rejects."""
    out: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        name = body[i + 1 : i + 2]
        if name in _TOML_UNESCAPES:
            out.append(_TOML_UNESCAPES[name])
            i += 2
        elif name in ("u", "U"):
            digits = body[i + 2 : i + (6 if name == "u" else 10)]
            if len(digits) != (4 if name == "u" else 8) or not all(c in "0123456789abcdefABCDEF" for c in digits):
                return None
            code = int(digits, 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                return None
            out.append(chr(code))
            i += 2 + len(digits)
        else:
            return None
    return "".join(out)


def _toml_escape(value: str) -> str:
    """value as the body of a TOML basic string."""
    return "".join(
        _TOML_ESCAPES.get(ch) or (f"\\u{ord(ch):04X}" if ord(ch) < 0x20 or ord(ch) == 0x7F else ch) for ch in value
    )


_TOML_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
_TOML_BASIC_RE = re.compile(r'"((?:[^"\\\r\n]|\\.)*)"')


def _toml_scan(line: str) -> Tuple[int, int]:
    """(where the line's comment starts, else len(line); where its first '=' is, else -1), outside quotes."""
    quote = ""
    equals = -1
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "#":
            return i, equals
        elif ch == "=" and equals < 0:
            equals = i
        i += 1
    return len(line), equals


def _toml_key(text: str) -> Optional[Tuple[str, ...]]:
    """The parts of a dotted TOML key such as projects.'d:\\proj', or None if text is not a key."""
    parts: List[str] = []
    rest = text.strip()
    while rest:
        part: Optional[str]
        if rest[0] == "'":
            end = rest.find("'", 1)
            if end < 0:
                return None
            part, rest = rest[1:end], rest[end + 1 :]
        else:
            match = (_TOML_BASIC_RE if rest[0] == '"' else _TOML_BARE_KEY_RE).match(rest)
            if match is None:
                return None
            part = _toml_unescape(match.group(1)) if rest[0] == '"' else match.group(0)
            rest = rest[match.end() :]
        if part is None:
            return None
        parts.append(part)
        rest = rest.lstrip()
        if rest:
            if rest[0] != "." or not rest[1:].strip():
                return None
            rest = rest[1:].lstrip()
    return tuple(parts) or None


def _toml_definition(
    line: str, table: Tuple[str, ...], index: int
) -> Tuple[Tuple[str, ...], Optional[Tuple[str, ...]]]:
    """For one TOML line: (the table later keys belong to, the table or key path this line defines)."""
    comment, equals = _toml_scan(line)
    code = line[:comment].strip()
    if code.startswith("[["):
        key = _toml_key(code[2:-2]) if code.endswith("]]") else None
        # Each [[array]] element is a table of its own, so its keys may repeat another element's.
        return ((*key, f"[[{index}]]") if key else table), None
    if code.startswith("["):
        key = _toml_key(code[1:-1]) if code.endswith("]") else None
        return (key or table), key
    key = _toml_key(line[:equals]) if equals >= 0 else None
    return table, ((*table, *key) if key else None)


def _toml_breaks(before: str, after: str) -> bool:
    """True if tomllib (Python 3.11+) reads before but not after; never True without tomllib."""
    if tomllib is None:
        return False
    try:
        tomllib.loads(before)
    except tomllib.TOMLDecodeError:
        return False
    try:
        tomllib.loads(after)
    except tomllib.TOMLDecodeError:
        return True
    return False


def _process_toml_file(
    af: AgentFile,
    mapper: PathMapper,
    report: RelinkReport,
) -> Tuple[Optional[bytes], int]:
    """Reads and rewrites quoted strings in a TOML file line by line."""
    try:
        raw_bytes = af.path.read_bytes()
    except OSError as err:
        report.errors.append({"file": str(af.path), "error": str(err)})
        return None, 0

    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        report.warnings.append(f"{af.path}: invalid UTF-8 ({err})")
        return None, 0

    if not mapper.mentions(text):
        return None, 0

    lines = text.splitlines(keepends=True)
    # Every table and key the file defines: a rewritten line must not define one of them again.
    defined: Set[Tuple[str, ...]] = set()
    table: Tuple[str, ...] = ()
    for index, line in enumerate(lines):
        table, path = _toml_definition(line, table, index)
        if path is not None:
            defined.add(path)

    total_count = 0
    out_lines: List[str] = []
    table = ()

    for index, line in enumerate(lines):
        line_count = 0

        def _replace_match(match: re.Match[str]) -> str:
            nonlocal line_count
            single = match.group(1)
            if single is not None:
                mapped, origs = mapper._map_value_with_originals(single)
                if mapped is not None and "'" not in mapped:
                    line_count += len(origs)
                    return f"'{mapped}'"
                return match.group(0)

            double = match.group(2)
            value = _toml_unescape(double) if double is not None else None
            if value is not None:
                mapped, origs = mapper._map_value_with_originals(value)
                if mapped is not None:
                    line_count += len(origs)
                    return f'"{_toml_escape(mapped)}"'
            return match.group(0)

        new_line = _TOML_QUOTED_RE.sub(_replace_match, line)
        if line_count > 0:
            old_path = _toml_definition(line, table, index)[1]
            new_path = _toml_definition(new_line, table, index)[1]
            if new_path != old_path and new_path in defined:
                # The project is already listed under its new path: defining it twice would make the
                # TOML invalid, so the stale entry stays as it is.
                new_line, line_count = line, 0
            elif new_path is not None:
                defined.add(new_path)
        table = _toml_definition(new_line, table, index)[0]

        out_lines.append(new_line)
        total_count += line_count

    if total_count == 0:
        return None, 0

    new_text = "".join(out_lines)
    if _toml_breaks(text, new_text):
        report.warnings.append(f"{af.path}: left unchanged, the rewritten file would not be valid TOML")
        return None, 0
    prefix = b"\xef\xbb\xbf" if has_bom else b""
    return prefix + new_text.encode("utf-8"), total_count


def _process_lines_file(
    af: AgentFile,
    mapper: PathMapper,
    report: RelinkReport,
) -> Tuple[Optional[bytes], int]:
    """Reads and rewrites path lines in a .pth or .egg-link file."""
    try:
        raw_bytes = af.path.read_bytes()
    except OSError as err:
        report.errors.append({"file": str(af.path), "error": str(err)})
        return None, 0

    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        report.warnings.append(f"{af.path}: invalid UTF-8 ({err})")
        return None, 0

    if not mapper.mentions(text):
        return None, 0

    total_count = 0
    out_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            body, eol = line[:-2], "\r\n"
        elif line.endswith(("\n", "\r")):
            body, eol = line[:-1], line[-1]
        else:
            body, eol = line, ""

        stripped = body.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(("import ", "import\t")):
            out_lines.append(line)
            continue

        mapped, origs = mapper._map_value_with_originals(stripped)
        if mapped is not None:
            total_count += len(origs)
            start = body.find(stripped)
            lead = body[:start]
            trail = body[start + len(stripped) :]
            out_lines.append(f"{lead}{mapped}{trail}{eol}")
        else:
            out_lines.append(line)

    if total_count == 0:
        return None, 0

    prefix = b"\xef\xbb\xbf" if has_bom else b""
    return prefix + "".join(out_lines).encode("utf-8"), total_count


_FINDER_ASSIGN_RE = re.compile(r"^(MAPPING|NAMESPACES)(\s*:[^=]*)?\s*=\s*(.+?)\s*$")


def _map_literal(value: Any, mapper: PathMapper) -> Tuple[Any, int]:
    """value (str, list or dict of them) with its paths mapped, and how many changed."""
    if isinstance(value, str):
        mapped = mapper.map_value(value)
        return (value, 0) if mapped is None else (mapped, 1)
    if isinstance(value, list):
        pairs = [_map_literal(item, mapper) for item in value]
        return [item for item, _ in pairs], sum(count for _, count in pairs)
    if isinstance(value, dict):
        pairs = {key: _map_literal(item, mapper) for key, item in value.items()}
        return {key: item for key, (item, _) in pairs.items()}, sum(count for _, count in pairs.values())
    return value, 0


def _process_finder_file(
    af: AgentFile,
    mapper: PathMapper,
    report: RelinkReport,
) -> Tuple[Optional[bytes], int]:
    """Rewrites MAPPING / NAMESPACES of a setuptools editable finder (__editable___*_finder.py).

    setuptools >= 64 installs a flat-layout project in editable mode with an import-only .pth and this
    module, which holds the project's absolute path; without it the moved venv cannot import the project.
    """
    try:
        text = af.path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as err:
        report.warnings.append(f"{af.path}: not read ({err})")
        return None, 0
    if not mapper.mentions(text):
        return None, 0

    total = 0
    out_lines: List[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        match = _FINDER_ASSIGN_RE.match(body)
        if match:
            try:
                value = ast.literal_eval(match.group(3))
            except (ValueError, SyntaxError):
                value = None
            new_value, count = _map_literal(value, mapper)
            if count:
                total += count
                line = f"{match.group(1)}{match.group(2) or ''} = {new_value!r}{line[len(body):]}"
        out_lines.append(line)
    if total == 0:
        return None, 0
    return "".join(out_lines).encode("utf-8"), total


def _expand_jetbrains_user_home(text: str, resolved_home: Path) -> str:
    """Expands leading $USER_HOME$ macro in a JetBrains XML path string."""
    if not text.startswith("$USER_HOME$"):
        return text
    suffix = text[len("$USER_HOME$") :]
    if suffix and suffix[0] not in "\\/":
        return text
    sep = "/" if "\\" not in text else "\\"
    home_str = str(resolved_home).rstrip("\\/")
    if sep == "/":
        home_str = home_str.replace("\\", "/")
    return home_str + suffix


def _map_jetbrains_value(
    text: str,
    mapper: PathMapper,
    resolved_home: Path,
) -> Optional[str]:
    """Maps a single JetBrains XML attribute value (supporting E:/..., $USER_HOME$/..., and file://...)."""
    if not text:
        return None
    if text.startswith("$USER_HOME$"):
        suffix = text[len("$USER_HOME$") :]
        if suffix and suffix[0] not in "\\/":
            return None
        sep = "/" if "\\" not in text else "\\"
        expanded = _expand_jetbrains_user_home(text, resolved_home)
        mapped = mapper.map(expanded)
        if mapped is None:
            return None
        home_parsed = _split(str(resolved_home).rstrip("\\/"))
        mapped_parsed = _split(mapped)
        if home_parsed and home_parsed[1] and mapped_parsed and _is_under(_key(*mapped_parsed), _key(*home_parsed)):
            rel_parts = mapped_parsed[1][len(home_parsed[1]) :]
            collapsed = "$USER_HOME$" + (sep + sep.join(rel_parts) if rel_parts else "")
            if text[-1] in "\\/" and not collapsed.endswith(sep):
                collapsed += sep
            return None if collapsed == text else collapsed
        return mapped.replace("\\", "/") if sep == "/" else mapped

    if text[:8].lower() == "file:///":
        return mapper.map_uri(text)

    if text[:7].lower() == "file://":
        mapped_inner = mapper.map(text[7:])
        if mapped_inner is not None:
            return text[:7] + mapped_inner
        return None

    return mapper.map(text)


def _escape_xml_attr(text: str, quote_char: str) -> str:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if quote_char == '"':
        return escaped.replace('"', "&quot;")
    return escaped.replace("'", "&apos;")


def _process_jetbrains_xml_file(
    af: AgentFile,
    mapper: PathMapper,
    resolved_home: Path,
    report: RelinkReport,
) -> Tuple[Optional[bytes], int]:
    """Reads and rewrites quoted path attributes in JetBrains recentProjects.xml / trusted-paths.xml."""
    try:
        raw_bytes = af.path.read_bytes()
    except OSError as err:
        report.errors.append({"file": str(af.path), "error": str(err)})
        return None, 0

    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        report.warnings.append(f"{af.path}: invalid UTF-8 ({err})")
        return None, 0

    if (
        not mapper.mentions(text)
        and not ("&" in text and mapper.mentions(html.unescape(text)))
        and "$USER_HOME$" not in text
    ):
        return None, 0

    total_count = 0

    def _replace_attr(match: re.Match[str]) -> str:
        nonlocal total_count
        double = match.group(1)
        if double is not None:
            quote_char = '"'
            raw_inner = double
        else:
            quote_char = "'"
            raw_inner = match.group(2) or ""

        unescaped = html.unescape(raw_inner) if "&" in raw_inner else raw_inner
        mapped = _map_jetbrains_value(unescaped, mapper, resolved_home)
        if mapped is None:
            return match.group(0)
        total_count += 1
        return f"{quote_char}{_escape_xml_attr(mapped, quote_char)}{quote_char}"

    new_text = _XML_QUOTED_RE.sub(_replace_attr, text)
    if total_count == 0:
        return None, 0

    prefix = b"\xef\xbb\xbf" if has_bom else b""
    return prefix + new_text.encode("utf-8"), total_count


def _connect_vscdb_ro(target: Path) -> sqlite3.Connection:
    """Opens a state.vscdb SQLite file in strict read-only URI mode (?mode=ro)."""
    abs_target = Path(os.path.abspath(str(target)))
    ro_uri = f"{abs_target.as_uri()}?mode=ro"
    return sqlite3.connect(ro_uri, timeout=1.0, uri=True)


def _process_vscdb_file(
    af: AgentFile,
    mapper: PathMapper,
    report: RelinkReport,
    dry_run: bool,
) -> int:
    """Reads and rewrites history.recentlyOpenedPathsList in a VS Code-family state.vscdb SQLite database."""
    try:
        real_target = Path(os.path.realpath(str(af.path)))
    except OSError as err:
        report.warnings.append(f"{af.path}: {err}")
        return 0

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect_vscdb_ro(real_target)
        cur = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            ("history.recentlyOpenedPathsList",),
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return 0
        raw_val = row[0]
        is_bytes = isinstance(raw_val, bytes)
        text_val = raw_val.decode("utf-8") if is_bytes else str(raw_val)
        if not mapper.mentions(text_val):
            return 0
        try:
            data = json.loads(text_val)
        except json.JSONDecodeError as err:
            report.warnings.append(f"{af.path}: invalid JSON in history.recentlyOpenedPathsList ({err})")
            return 0

        originals: List[str] = []
        new_data, count = _rewrite_json_value(data, mapper, originals)
        if count == 0:
            return 0
        if dry_run:
            return count

        new_bytes = _serialize_json_like(new_data, text_val, False)
        new_db_val: Union[bytes, str] = new_bytes if is_bytes else new_bytes.decode("utf-8")

        conn.close()
        conn = None

        if af.backup and _is_file(real_target):
            shutil.copy2(str(real_target), str(real_target) + BACKUP_SUFFIX)

        conn = sqlite3.connect(str(real_target), timeout=1.0)
        with conn:
            conn.execute(
                "UPDATE ItemTable SET value = ? WHERE key = ?",
                (new_db_val, "history.recentlyOpenedPathsList"),
            )
        return count
    except (sqlite3.Error, OSError, UnicodeDecodeError) as err:
        report.warnings.append(f"{af.path}: {err}")
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _write_agent_file(af: AgentFile, content: bytes, report: RelinkReport) -> bool:
    """Writes content atomically to the real path of af.path, creating a rolling backup when requested."""
    try:
        real_target = Path(os.path.realpath(str(af.path)))
    except OSError as err:
        report.errors.append({"file": str(af.path), "error": str(err)})
        return False

    tmp_path = real_target.parent / f"{real_target.name}.smart-drive.tmp"
    try:
        if af.backup and _is_file(real_target):
            shutil.copy2(str(real_target), str(real_target) + BACKUP_SUFFIX)
        # Configs such as ~/.claude.json are private (0600): the copy is created private too and only
        # gets the target's mode below, so it is never readable by others, even if copymode fails.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        try:
            shutil.copymode(str(real_target), str(tmp_path))
        except OSError:
            pass
        os.replace(str(tmp_path), str(real_target))
        return True
    except OSError as err:
        try:
            if _exists(tmp_path):
                tmp_path.unlink()
        except OSError:
            pass
        report.errors.append({"file": str(af.path), "error": str(err)})
        return False


def _merge_history_dirs(src: Path, dst: Path) -> List[str]:
    """Moves entries from src into existing dst without overwriting; returns relative paths left behind."""
    left_behind: List[str] = []

    def _walk(s_dir: Path, d_dir: Path, prefix: str) -> None:
        try:
            entries = sorted(s_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            rel = f"{prefix}/{entry.name}" if prefix else entry.name
            target = d_dir / entry.name
            if not _exists(target):
                os.rename(entry, target)
            elif _is_dir(entry) and _is_dir(target) and not os.path.islink(entry) and not os.path.islink(target):
                _walk(entry, target, rel)
                try:
                    if not any(entry.iterdir()):
                        entry.rmdir()
                except OSError:
                    pass
            else:
                left_behind.append(rel)

    _walk(src, dst, "")
    try:
        if not any(src.iterdir()):
            src.rmdir()
    except OSError:
        pass
    return left_behind


def _has_matching_history_dir(projects_base: Path, full_slug: str) -> bool:
    """True if projects_base contains a directory whose name starts with the first 200 chars of full_slug."""
    if not _is_dir(projects_base):
        return False
    prefix = full_slug[:200]
    if _CASE_INSENSITIVE:
        prefix_cf = prefix.casefold()
        try:
            return any(
                _is_dir(entry) and entry.name.casefold().startswith(prefix_cf)
                for entry in projects_base.iterdir()
            )
        except OSError:
            return False
    try:
        return any(
            _is_dir(entry) and entry.name.startswith(prefix)
            for entry in projects_base.iterdir()
        )
    except OSError:
        return False


def _relink_history(
    candidates: Sequence[str],
    moves: Sequence[Move],
    mapper: PathMapper,
    home: Path,
    dry_run: bool,
    report: RelinkReport,
) -> None:
    """Migrates ~/.claude/projects/<slug>/ directories for moved project paths."""
    projects_base = home / ".claude" / "projects"
    seen_slugs: Set[str] = set()
    move_old_keys: Set[Tuple[str, ...]] = set()
    for move_old, _ in moves:
        parsed_move = _split(move_old)
        if parsed_move is not None and parsed_move[1]:
            move_old_keys.add(_key(*parsed_move))

    for old_raw in candidates:
        old_clean = old_raw.rstrip("\\/") if len(old_raw) > 3 else old_raw
        new_mapped = mapper.map(old_clean)
        if new_mapped is None:
            continue
        new_clean = new_mapped.rstrip("\\/") if len(new_mapped) > 3 else new_mapped

        old_slug = claude_slug(old_clean)
        new_slug = claude_slug(new_clean)
        if old_slug is None or new_slug is None:
            parsed_old = _split(old_clean)
            is_move_old = parsed_old is not None and _key(*parsed_old) in move_old_keys
            full_old_slug = _claude_slug_text(old_clean)
            if is_move_old or _has_matching_history_dir(projects_base, full_old_slug):
                warn_key = old_clean.casefold()
                if warn_key not in seen_slugs:
                    seen_slugs.add(warn_key)
                    report.warnings.append(
                        f"Claude Code history slug for '{old_clean}' exceeds 200 characters and cannot be moved."
                    )
            continue

        slug_key = old_slug.casefold()
        if slug_key in seen_slugs or old_slug.casefold() == new_slug.casefold():
            continue
        seen_slugs.add(slug_key)

        if not _is_dir(projects_base):
            continue
        src = projects_base / old_slug
        if not _is_dir(src):
            continue
        dst = projects_base / new_slug

        if dry_run:
            report.history.append({"from": old_slug, "to": new_slug})
            continue

        try:
            if not _exists(dst):
                os.rename(src, dst)
                report.history.append({"from": old_slug, "to": new_slug})
            else:
                left = _merge_history_dirs(src, dst)
                report.history.append({"from": old_slug, "to": new_slug})
                if left:
                    report.warnings.append(
                        f"Claude Code history {old_slug} -> {new_slug}: kept existing files ({', '.join(left)})"
                    )
        except OSError as err:
            report.errors.append({"file": str(src), "error": str(err)})


def _run_git_repair(git_bin: str, repo_dir: Path, extra_args: Sequence[str], report: RelinkReport) -> bool:
    """Executes `git -C <repo_dir> worktree repair ...` and updates report."""
    try:
        done = subprocess.run(
            [git_bin, "-C", str(repo_dir), "worktree", "repair", *extra_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        report.warnings.append(f"git worktree repair failed in {repo_dir}: {err}")
        return False

    if done.returncode == 0:
        repo_str = str(repo_dir)
        if repo_str not in report.worktrees:
            report.worktrees.append(repo_str)
        return True

    stderr = (done.stderr or done.stdout or "").strip()
    report.warnings.append(f"git worktree repair failed in {repo_dir}: {stderr or f'exit {done.returncode}'}")
    return False


_WORKTREE_SKIP_DIRS: Set[str] = {
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "target",
    "dist",
    "build",
    "system volume information",
}
_MAX_WORKTREE_SCAN_DIRS = 200


def _collect_worktree_candidate_dirs(
    candidates: Sequence[str],
    max_depth: int = 2,
    max_dirs: int = _MAX_WORKTREE_SCAN_DIRS,
) -> List[Path]:
    """Returns existing directories from candidates plus nested subdirectories up to max_depth."""
    unique_dirs: List[Path] = []
    seen: Set[str] = set()

    def _add(p: Path) -> bool:
        norm = str(p).rstrip("\\/") if len(str(p)) > 3 else str(p)
        key = norm.casefold() if _CASE_INSENSITIVE else norm
        if key in seen:
            return False
        seen.add(key)
        unique_dirs.append(Path(norm))
        return True

    for path_str in candidates:
        if not path_str:
            continue
        norm = path_str.rstrip("\\/") if len(path_str) > 3 else path_str
        root_path = Path(norm)
        if not _is_dir(root_path):
            continue
        _add(root_path)

        scanned = 0
        queue: List[Tuple[Path, int]] = [(root_path, 0)]
        while queue and scanned < max_dirs:
            cur_dir, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            try:
                with os.scandir(cur_dir) as it:
                    entries = sorted(list(it), key=lambda e: e.name.casefold())
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if not name or name.startswith((".", "$")) or name.casefold() in _WORKTREE_SKIP_DIRS:
                    continue
                if _is_link_entry(entry):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                scanned += 1
                child_path = Path(entry.path)
                _add(child_path)
                if scanned >= max_dirs:
                    break
                if depth + 1 < max_depth:
                    queue.append((child_path, depth + 1))

    return unique_dirs


def _repair_worktrees(
    new_candidates: Sequence[str],
    mapper: PathMapper,
    report: RelinkReport,
) -> None:
    """Repairs git worktrees for moved repositories and linked worktrees (including nested repos up to depth 2)."""
    unique_dirs = _collect_worktree_candidate_dirs(new_candidates)

    main_repos: List[Tuple[Path, List[str]]] = []
    linked_worktrees: List[Path] = []

    for d in unique_dirs:
        dot_git = d / ".git"
        if _is_dir(dot_git):
            wt_root = dot_git / "worktrees"
            if _is_dir(wt_root):
                wt_dirs: List[str] = []
                try:
                    gitdir_files = sorted(wt_root.glob("*/gitdir"))
                except OSError:
                    gitdir_files = []
                for gf in gitdir_files:
                    if not _is_file(gf):
                        continue
                    try:
                        recorded = gf.read_text(encoding="utf-8", errors="replace").strip()
                    except OSError:
                        continue
                    if not recorded:
                        continue
                    mapped_rec = mapper.map(recorded) or recorded
                    parent_dir = Path(mapped_rec).parent
                    if _is_dir(parent_dir):
                        p_str = str(parent_dir)
                        if p_str not in wt_dirs:
                            wt_dirs.append(p_str)
                if wt_dirs:
                    main_repos.append((d, wt_dirs))
        elif _is_file(dot_git):
            linked_worktrees.append(d)

    if not main_repos and not linked_worktrees:
        return

    git_bin = shutil.which("git")
    if not git_bin:
        report.warnings.append("git was not found on PATH; could not repair git worktrees.")
        return

    repaired_wt_paths: Set[str] = set()
    for repo_dir, wt_dirs in main_repos:
        if _run_git_repair(git_bin, repo_dir, wt_dirs, report):
            for w in wt_dirs:
                repaired_wt_paths.add(w.casefold() if _CASE_INSENSITIVE else w)

    for wt_dir in linked_worktrees:
        wt_key = str(wt_dir).casefold() if _CASE_INSENSITIVE else str(wt_dir)
        if wt_key in repaired_wt_paths:
            continue
        # If the worktree's .git file points to an old main repo path, rewrite it before running repair.
        dot_git = wt_dir / ".git"
        try:
            content = dot_git.read_text(encoding="utf-8", errors="replace")
            if content.startswith("gitdir:"):
                raw_target = content[len("gitdir:") :].strip()
                mapped_target = mapper.map(raw_target)
                if mapped_target:
                    eol = "\r\n" if "\r\n" in content else "\n"
                    dot_git.write_text(f"gitdir: {mapped_target}{eol}", encoding="utf-8")
        except OSError:
            pass
        _run_git_repair(git_bin, wt_dir, [], report)


def _candidate_venv_dirs(base: Path, max_subdirs: int = _MAX_WORKTREE_SCAN_DIRS) -> List[Path]:
    """Returns candidate virtualenv paths at base and <base>/<child>/{.venv,venv,env} (depth 1)."""
    candidates: List[Path] = [base, base / ".venv", base / "venv", base / "env"]
    try:
        with os.scandir(base) as it:
            entries = sorted(list(it), key=lambda e: e.name.casefold())
    except OSError:
        return candidates

    scanned = 0
    for entry in entries:
        name = entry.name
        if not name or name.startswith((".", "$")) or name.casefold() in _WORKTREE_SKIP_DIRS:
            continue
        if _is_link_entry(entry):
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        scanned += 1
        child = Path(entry.path)
        candidates.extend((child, child / ".venv", child / "venv", child / "env"))
        if scanned >= max_subdirs:
            break
    return candidates


def _relink_virtualenvs(
    moves: Sequence[Move],
    mapper: PathMapper,
    report: RelinkReport,
) -> None:
    """Updates .pth and .egg-link files inside moved Python virtualenvs and warns about console scripts."""
    seen_venvs: Set[str] = set()
    for _, new_path in moves:
        base = Path(new_path)
        if not _is_dir(base):
            continue
        for venv_dir in _candidate_venv_dirs(base):
            key = str(venv_dir).casefold() if _CASE_INSENSITIVE else str(venv_dir)
            if key in seen_venvs:
                continue
            if not _is_file(venv_dir / "pyvenv.cfg"):
                continue
            seen_venvs.add(key)

            site_packages: List[Path] = []
            win_sp = venv_dir / "Lib" / "site-packages"
            if _is_dir(win_sp):
                site_packages.append(win_sp)
            lib_dir = venv_dir / "lib"
            if _is_dir(lib_dir):
                try:
                    for py_sp in sorted(lib_dir.glob("python*/site-packages")):
                        if _is_dir(py_sp) and py_sp not in site_packages:
                            site_packages.append(py_sp)
                except OSError:
                    pass

            for sp in site_packages:
                try:
                    files = (
                        sorted(sp.glob("*.pth"))
                        + sorted(sp.glob("*.egg-link"))
                        + sorted(sp.glob("__editable___*_finder.py"))
                    )
                except OSError:
                    files = []
                for f in files:
                    if not _is_file(f) or f.name.endswith(BACKUP_SUFFIX):
                        continue
                    finder = f.suffix == ".py"
                    af = AgentFile(path=f, agent="Python venv", kind="finder" if finder else "lines", backup=True)
                    try:
                        process = _process_finder_file if finder else _process_lines_file
                        new_bytes, count = process(af, mapper, report)
                        if new_bytes is not None and count > 0:
                            if _write_agent_file(af, new_bytes, report):
                                report.updated.append({"file": str(af.path), "agent": af.agent, "paths": count})
                    except Exception as err:
                        report.errors.append({"file": str(af.path), "error": str(err)})

            report.warnings.append(
                f"Python venv {venv_dir}: console scripts of that venv may still start the old path; "
                "fix with `uv sync --reinstall` or a fresh venv."
            )


_PY_QUOTED_STR_RE = re.compile(r"'((?:[^'\\\r\n]|\\.)*)'|\"((?:[^\"\\\r\n]|\\.)*)\"")


def _process_editable_finder_file(
    af: AgentFile,
    mapper: PathMapper,
    report: RelinkReport,
) -> Tuple[Optional[bytes], int]:
    """Reads and rewrites path literals in a PEP 660 __editable___*_finder.py file."""
    try:
        raw_bytes = af.path.read_bytes()
    except OSError as err:
        report.errors.append({"file": str(af.path), "error": str(err)})
        return None, 0

    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        report.warnings.append(f"{af.path}: invalid UTF-8 ({err})")
        return None, 0

    if not mapper.mentions(text):
        return None, 0

    total_count = 0

    def _replace_str(match: re.Match[str]) -> str:
        nonlocal total_count
        single = match.group(1)
        if single is not None:
            quote = "'"
            raw_inner = single
        else:
            quote = '"'
            raw_inner = match.group(2) or ""
        unescaped = raw_inner.replace("\\\\", "\\")
        mapped = mapper.map(unescaped)
        if mapped is None:
            return match.group(0)
        total_count += 1
        escaped = mapped.replace("\\", "\\\\") if "\\\\" in raw_inner else mapped
        return f"{quote}{escaped}{quote}"

    new_text = _PY_QUOTED_STR_RE.sub(_replace_str, text)
    if total_count == 0:
        return None, 0

    prefix = b"\xef\xbb\xbf" if has_bom else b""
    return prefix + new_text.encode("utf-8"), total_count


def _global_site_packages(
    resolved_home: Path,
    resolved_app_data: Path,
    use_env: bool,
    site_packages: Optional[Sequence[Union[str, Path]]] = None,
) -> List[Path]:
    """Discovers global and user Python site-packages directories (or returns injected site_packages)."""
    raw_candidates: List[Path] = []
    if site_packages is not None:
        raw_candidates.extend(Path(sp) for sp in site_packages)
    else:
        scan_system = use_env
        if scan_system:
            try:
                temp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
                home_norm = os.path.normcase(os.path.abspath(str(resolved_home)))
                if home_norm == temp_root or home_norm.startswith(temp_root + os.sep):
                    scan_system = False
            except OSError:
                scan_system = False

        if scan_system:
            prefixes: List[Path] = [Path(sys.base_prefix), Path(sys.prefix)]
            if sys.platform == "win32":
                py_bin = shutil.which("py")
                if py_bin:
                    try:
                        done = subprocess.run(
                            [py_bin, "-0p"],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=5,
                            stdin=subprocess.DEVNULL,
                            check=False,
                        )
                        if done.returncode == 0:
                            for line in done.stdout.splitlines():
                                m = re.search(r"([A-Za-z]:[\\/].+)$", line)
                                if m:
                                    cand = Path(m.group(1).strip().strip('"'))
                                    pref = cand.parent if cand.suffix.lower() == ".exe" or _is_file(cand) else cand
                                    prefixes.append(pref)
                    except (OSError, subprocess.SubprocessError):
                        pass

            for pref in prefixes:
                raw_candidates.append(pref / "Lib" / "site-packages")
                lib_dir = pref / "lib"
                if _is_dir(lib_dir):
                    try:
                        raw_candidates.extend(sorted(lib_dir.glob("python*/site-packages")))
                    except OSError:
                        pass

            try:
                import site as _site_mod

                user_sp = _site_mod.getusersitepackages()
                if isinstance(user_sp, str) and user_sp:
                    raw_candidates.append(Path(user_sp))
            except Exception:
                pass

        for py_root in (resolved_app_data / "Python", resolved_home / "AppData" / "Roaming" / "Python"):
            if _is_dir(py_root):
                try:
                    raw_candidates.extend(sorted(py_root.glob("Python*/site-packages")))
                except OSError:
                    pass
        local_lib = resolved_home / ".local" / "lib"
        if _is_dir(local_lib):
            try:
                raw_candidates.extend(sorted(local_lib.glob("python*/site-packages")))
            except OSError:
                pass
        mac_py = resolved_home / "Library" / "Python"
        if _is_dir(mac_py):
            try:
                raw_candidates.extend(sorted(mac_py.glob("*/lib/python/site-packages")))
            except OSError:
                pass

    deduped: List[Path] = []
    seen: Set[str] = set()
    for sp in raw_candidates:
        if not _is_dir(sp):
            continue
        norm = os.path.normcase(os.path.abspath(str(sp)))
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(sp)
    return deduped


def _relink_editable_installs(
    mapper: PathMapper,
    resolved_home: Path,
    resolved_app_data: Path,
    use_env: bool,
    dry_run: bool,
    report: RelinkReport,
    site_packages: Optional[Sequence[Union[str, Path]]] = None,
) -> None:
    """Rewrites editable install references (*.pth, __editable__*, direct_url.json, *.egg-link) in global/user site-packages."""
    for sp in _global_site_packages(resolved_home, resolved_app_data, use_env, site_packages=site_packages):
        candidates: List[AgentFile] = []
        try:
            for f in sorted(sp.glob("*.pth")) + sorted(sp.glob("*.egg-link")):
                if _is_file(f) and not f.name.endswith(BACKUP_SUFFIX):
                    candidates.append(AgentFile(path=f, agent="Python editable", kind="lines", backup=True))
            for f in sorted(sp.glob("__editable___*_finder.py")):
                if _is_file(f) and not f.name.endswith(BACKUP_SUFFIX):
                    candidates.append(AgentFile(path=f, agent="Python editable", kind="finder_py", backup=True))
            for f in sorted(sp.glob("*.dist-info/direct_url.json")):
                if _is_file(f) and not f.name.endswith(BACKUP_SUFFIX):
                    candidates.append(AgentFile(path=f, agent="Python editable", kind="json", backup=True))
        except OSError:
            continue

        for af in candidates:
            try:
                if af.kind == "lines":
                    new_bytes, count = _process_lines_file(af, mapper, report)
                elif af.kind == "finder_py":
                    new_bytes, count = _process_editable_finder_file(af, mapper, report)
                elif af.kind == "json":
                    new_bytes, count = _process_json_file(af, mapper, report, [])
                else:
                    continue
                if new_bytes is None or count == 0:
                    continue
                if dry_run:
                    report.updated.append({"file": str(af.path), "agent": af.agent, "paths": count})
                elif _write_agent_file(af, new_bytes, report):
                    report.updated.append({"file": str(af.path), "agent": af.agent, "paths": count})
                else:
                    report.warnings.append(
                        f"Could not update editable install {af.path}; run `pip install -e <new_path>`."
                    )
            except Exception as err:
                report.errors.append({"file": str(af.path), "error": str(err)})


_PS_UPDATE_SHORTCUT_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$ws = New-Object -ComObject WScript.Shell; "
    "$sc = $ws.CreateShortcut($env:SD_LNK); "
    "$sc.TargetPath = $env:SD_TARGET; "
    "$oldPrefix = $env:SD_OLD_PREFIX; "
    "$newPrefix = $env:SD_NEW_PREFIX; "
    "$wd = $sc.WorkingDirectory; "
    "if ($wd -and $oldPrefix -and $wd.StartsWith($oldPrefix, [System.StringComparison]::OrdinalIgnoreCase) "
    "-and ($wd.Length -eq $oldPrefix.Length -or $wd[$oldPrefix.Length] -in @('\\', '/'))) { "
    "$sc.WorkingDirectory = $newPrefix + $wd.Substring($oldPrefix.Length) }; "
    "$ic = $sc.IconLocation; "
    "if ($ic -and $oldPrefix -and $ic.StartsWith($oldPrefix, [System.StringComparison]::OrdinalIgnoreCase) "
    "-and ($ic.Length -eq $oldPrefix.Length -or $ic[$oldPrefix.Length] -in @('\\', '/', ','))) { "
    "$sc.IconLocation = $newPrefix + $ic.Substring($oldPrefix.Length) }; "
    "$sc.Save(); "
    "$verify = $ws.CreateShortcut($env:SD_LNK); "
    "if (-not [string]::Equals($verify.TargetPath, $env:SD_TARGET, [System.StringComparison]::OrdinalIgnoreCase)) { "
    "Write-Error \"TargetPath mismatch after Save: expected '$($env:SD_TARGET)', got '$($verify.TargetPath)'\"; "
    "exit 2 }"
)


def _same_shortcut_target(actual: Optional[str], expected: str) -> bool:
    if not actual:
        return False
    pa, pe = _split(actual), _split(expected)
    if pa is not None and pe is not None:
        return _key(*pa) == _key(*pe)
    return os.path.normcase(os.path.normpath(actual)) == os.path.normcase(os.path.normpath(expected))


def _relink_shortcuts(
    moves: Sequence[Move],
    mapper: PathMapper,
    resolved_home: Path,
    use_env: bool,
    dry_run: bool,
    report: RelinkReport,
) -> None:
    """Repoints Windows .lnk shortcuts found by SystemRefIndex that target moved folders."""
    if sys.platform != "win32":
        return

    effective_home: Optional[Path] = resolved_home
    if use_env:
        try:
            temp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))
            home_norm = os.path.normcase(os.path.abspath(str(resolved_home)))
            if not (home_norm == temp_root or home_norm.startswith(temp_root + os.sep)):
                effective_home = None
        except OSError:
            effective_home = None

    shortcut_refs: List[Any] = []
    shortcut_reader = RegistryReader(tree={}) if effective_home is not None else RegistryReader()
    SystemRefIndex._collect_shortcuts(effective_home, shortcut_refs, reader=shortcut_reader)
    ref_index = SystemRefIndex(shortcut_refs)
    matched_shortcuts: List[Tuple[Path, str, str, str]] = []
    seen_lnks: Set[str] = set()

    for old_dir, _ in moves:
        for ref in ref_index.refs_under(old_dir):
            if ref.kind != "shortcut":
                continue
            lnk_norm = os.path.normcase(os.path.abspath(ref.source))
            if lnk_norm in seen_lnks:
                continue
            new_target = mapper.map(ref.target)
            if new_target is None:
                continue
            parsed_target = _split(ref.target)
            old_prefix = old_dir.rstrip("\\/")
            new_prefix = mapper.map(old_prefix) or ""
            if parsed_target is not None:
                t_key = _key(*parsed_target)
                for old_k, (old_anchor, old_names), (new_anchor, new_names) in mapper._rules:
                    if t_key[: len(old_k)] == old_k:
                        old_prefix = _join_split(old_anchor, old_names)
                        new_prefix = _join_split(new_anchor, new_names)
                        break
            seen_lnks.add(lnk_norm)
            matched_shortcuts.append((Path(ref.source), new_target, old_prefix, new_prefix))

    if not matched_shortcuts:
        return

    if dry_run:
        for lnk_path, _, _, _ in matched_shortcuts:
            report.updated.append({"file": str(lnk_path), "agent": "Windows Shortcuts", "paths": 1})
        return

    ps_bin = shutil.which("powershell") or shutil.which("pwsh")
    if not ps_bin:
        report.warnings.append("PowerShell was not found on PATH; could not update Windows shortcuts.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = resolved_home / ".smart-drive" / "backups" / "shortcuts" / timestamp

    for lnk_path, new_target, old_prefix, new_prefix in matched_shortcuts:
        backup_path = backup_dir / lnk_path.name
        collision_idx = 1
        while backup_path.exists():
            backup_path = backup_dir / f"{lnk_path.stem}_{collision_idx}{lnk_path.suffix}"
            collision_idx += 1
        try:
            if _is_file(lnk_path):
                backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(lnk_path), str(backup_path))
        except OSError as err:
            report.warnings.append(f"Could not back up shortcut {lnk_path}: {err}")
            continue

        sub_env = dict(os.environ)
        sub_env["SD_LNK"] = str(lnk_path)
        sub_env["SD_TARGET"] = new_target
        sub_env["SD_OLD_PREFIX"] = old_prefix
        sub_env["SD_NEW_PREFIX"] = new_prefix
        cmd = [ps_bin, "-NoProfile", "-NonInteractive", "-Command", _PS_UPDATE_SHORTCUT_SCRIPT]
        try:
            done = subprocess.run(
                cmd,
                env=sub_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as err:
            report.warnings.append(f"Could not update shortcut {lnk_path} (cần sửa tay): {err}")
            continue

        saved_target = parse_lnk_file(lnk_path) if done.returncode == 0 else None
        if done.returncode == 0 and _same_shortcut_target(saved_target, new_target):
            report.updated.append({"file": str(lnk_path), "agent": "Windows Shortcuts", "paths": 1})
        else:
            try:
                if _is_file(backup_path):
                    shutil.copy2(str(backup_path), str(lnk_path))
            except OSError:
                pass
            err_msg = (done.stderr or done.stdout or f"exit {done.returncode}").strip()
            if done.returncode == 0 and not _same_shortcut_target(saved_target, new_target):
                err_msg = f"TargetPath after Save() was '{saved_target}', expected '{new_target}'"
            report.warnings.append(
                f"Could not update shortcut {lnk_path} -> {new_target} "
                f"(WScript.Shell could not preserve path characters; cần sửa tay): {err_msg}"
            )


def relink(
    moves: Sequence[Move],
    root: Optional[str] = None,
    dry_run: bool = False,
    home: Optional[Path] = None,
    app_data: Optional[Path] = None,
    site_packages: Optional[Sequence[Union[str, Path]]] = None,
) -> RelinkReport:
    """Rewrites paths remembered by coding agents after folders have been moved."""
    report = RelinkReport(dry_run=dry_run)
    mapper = PathMapper(moves)
    if not mapper:
        return report

    resolved_home, resolved_app_data, use_env = _resolve_locations(home, app_data)
    claude_originals: List[str] = []

    for af in agent_files(root=root, home=home, app_data=app_data):
        try:
            if af.kind == "vscdb":
                count = _process_vscdb_file(af, mapper, report, dry_run)
                if count > 0:
                    report.updated.append({"file": str(af.path), "agent": af.agent, "paths": count})
                continue

            if af.kind == "json":
                new_bytes, count = _process_json_file(af, mapper, report, claude_originals)
            elif af.kind == "toml":
                new_bytes, count = _process_toml_file(af, mapper, report)
            elif af.kind == "lines":
                new_bytes, count = _process_lines_file(af, mapper, report)
            elif af.kind == "jetbrains_xml":
                new_bytes, count = _process_jetbrains_xml_file(af, mapper, resolved_home, report)
            else:
                continue

            if new_bytes is None or count == 0:
                continue

            if dry_run:
                report.updated.append({"file": str(af.path), "agent": af.agent, "paths": count})
            elif _write_agent_file(af, new_bytes, report):
                report.updated.append({"file": str(af.path), "agent": af.agent, "paths": count})
        except Exception as err:
            report.errors.append({"file": str(af.path), "error": str(err)})

    history_candidates = [old for old, _ in moves] + claude_originals
    _relink_history(history_candidates, moves, mapper, resolved_home, dry_run, report)
    _relink_shortcuts(moves, mapper, resolved_home, use_env, dry_run, report)
    _relink_editable_installs(
        mapper,
        resolved_home,
        resolved_app_data,
        use_env,
        dry_run,
        report,
        site_packages=site_packages,
    )

    if not dry_run:
        worktree_candidates = [new for _, new in moves]
        for cand in history_candidates:
            mapped_cand = mapper.map(cand)
            if mapped_cand:
                worktree_candidates.append(mapped_cand)
        _repair_worktrees(worktree_candidates, mapper, report)
        _relink_virtualenvs(moves, mapper, report)
        if report.agents:
            busy = sorted(set(report.agents) & running_agents())
            if busy:
                apps = ", ".join(busy)
                report.warnings.append(
                    f"{apps} is running: it may write its old settings back when it quits. "
                    "Quit it, then run `smart-drive relink --wait` (or `smart-drive relink` again)."
                )

    return report


def journal_path(root: str) -> str:
    """Path to the append-only move journal on the managed drive."""
    return os.path.join(os.path.abspath(root), ".smart_drive", JOURNAL_NAME)


def record_moves(
    root: str,
    moves: Sequence[Move],
    tool: str,
    undoes: Optional[str] = None,
) -> None:
    """Appends one batch of moves to <root>/.smart_drive/moves.jsonl."""
    if not moves:
        return
    if tool == "undo" and undoes is None:
        target = last_undoable_moves(root)
        if target is not None and isinstance(target.get("time"), str):
            undoes = target["time"]
    path = journal_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record: Dict[str, Any] = {
        "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": tool,
    }
    if undoes is not None:
        record["undoes"] = undoes
    record["moves"] = [[old, new] for old, new in moves]
    line = json.dumps(record, ensure_ascii=False)
    try:
        line.encode("utf-8")
    except UnicodeEncodeError:
        # A name that is not valid Unicode (a lone surrogate) cannot be UTF-8: keep JSON escapes instead.
        line = json.dumps(record)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _parse_batch_line(line: str) -> Optional[Dict[str, Any]]:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw_moves = data.get("moves")
    if not isinstance(raw_moves, list) or not raw_moves:
        return None
    parsed_moves: List[Move] = []
    for item in raw_moves:
        if (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            parsed_moves.append((item[0], item[1]))
        else:
            return None
    if not parsed_moves:
        return None
    result = dict(data)
    result["moves"] = parsed_moves
    return result


def last_moves(root: str) -> Optional[Dict[str, Any]]:
    """Returns the latest valid batch from moves.jsonl with moves as a list of tuples, or None."""
    path = journal_path(root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None

    for line in reversed(lines):
        parsed = _parse_batch_line(line)
        if parsed is not None:
            return parsed
    return None


def last_undoable_moves(root: str) -> Optional[Dict[str, Any]]:
    """Returns the most recent move batch from moves.jsonl that has not yet been undone."""
    path = journal_path(root)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None

    undone_counts: Dict[str, int] = {}
    for line in reversed(lines):
        parsed = _parse_batch_line(line)
        if parsed is None:
            continue
        if parsed.get("tool") == "undo" or "undoes" in parsed:
            target_time = parsed.get("undoes")
            if isinstance(target_time, str) and target_time:
                undone_counts[target_time] = undone_counts.get(target_time, 0) + 1
            continue
        batch_time = parsed.get("time")
        if isinstance(batch_time, str) and undone_counts.get(batch_time, 0) > 0:
            undone_counts[batch_time] -= 1
            continue
        return parsed
    return None


def _all_move_batches(root: Optional[str]) -> List[List[Move]]:
    """Returns all valid move batches from <root>/.smart_drive/moves.jsonl, newest first."""
    if not root:
        return []
    path = journal_path(root)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []

    batches: List[List[Move]] = []
    for line in reversed(lines):
        parsed = _parse_batch_line(line)
        if parsed is not None:
            batches.append(list(parsed["moves"]))
    return batches


@dataclass
class MovedFolder:
    """One missing folder (or unreadable path value) remembered by coding agents."""

    old: str
    new: Optional[str] = None
    kind: str = "not-found"
    candidates: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    paths: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class MovedList(List[MovedFolder]):
    """List of MovedFolder entries returned by find_moved(), with skipped_unmounted counts."""

    def __init__(
        self,
        iterable: Iterable[MovedFolder] = (),
        skipped_unmounted: Optional[Dict[str, int]] = None,
    ) -> None:
        super().__init__(iterable)
        self.skipped_unmounted: Dict[str, int] = dict(skipped_unmounted or {})


def _is_under(key: Tuple[str, ...], prefix: Tuple[str, ...]) -> bool:
    return bool(prefix) and len(key) >= len(prefix) and key[: len(prefix)] == prefix


def _join_split(anchor: str, names: Sequence[str]) -> str:
    if anchor == "/":
        return "/" if not names else "/" + "/".join(names)
    return anchor + "\\" if not names else anchor + "\\" + "\\".join(names)


def _is_link_entry(entry: os.DirEntry[str]) -> bool:
    try:
        if entry.is_symlink():
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return True


def _list_subdirs(directory: Path, listings: Optional[Dict[str, List[Path]]] = None) -> List[Path]:
    """Lists direct subdirectories of directory, skipping hidden/system entries and symlinks/junctions.

    listings keeps each listing for one find_moved() run: missing folders that share an existing ancestor
    search the same folders, so each folder is read from disk once per run, not once per missing folder."""
    if listings is not None:
        key = str(directory)
        if key not in listings:
            listings[key] = _list_subdirs(directory)
        return listings[key]
    out: List[Path] = []
    try:
        with os.scandir(directory) as it:
            entries = list(it)
    except OSError:
        return out
    for entry in entries:
        name = entry.name
        if not name or name.startswith((".", "$")) or name.casefold() == "system volume information":
            continue
        # The listing usually tells folders from files; only folders pay for the link check (a stat on macOS and Linux)
        try:
            if not entry.is_dir(follow_symlinks=False) or _is_link_entry(entry):
                continue
        except OSError:
            continue
        out.append(Path(entry.path))
    out.sort(key=lambda p: p.name.casefold())
    return out


def _has_all_subs(folder: Path, subs: Set[str], listings: Optional[Dict[str, List[Path]]] = None) -> bool:
    """Returns True if folder contains every directory name in subs (case-insensitive)."""
    if not subs:
        return True
    present = {_fold(sub.name) for sub in _list_subdirs(folder, listings)}
    return subs.issubset(present)


def _holds_all(folder: Path, names: Set[str]) -> bool:
    """Returns True if folder holds an entry of any kind (file or folder, hidden or not) for every name."""
    if not names:
        return True
    try:
        with os.scandir(folder) as it:
            present = {_fold(entry.name) for entry in it}
    except OSError:
        return False
    return names.issubset(present)


def _is_unreadable_drive_path(text: str) -> bool:
    if not isinstance(text, str) or not text or _CONTROL_CHAR_RE.search(text):
        return False
    if not _UNREADABLE_DRIVE_RE.match(text):
        return False
    if any(_WIN_INVALID_COMP_RE.search(part) for part in re.split(r"[\\/]+", text[2:]) if part):
        return False
    return True


def _unmounted_root(anchor: str, names: Sequence[str]) -> Optional[str]:
    """Returns the drive or volume root string if that drive/volume is not connected, else None."""
    if anchor != "/":
        if not _exists(Path(anchor + "\\")):
            return anchor.upper()
        return None
    if len(names) >= 2 and names[0] == "Volumes" and _exists(Path("/Volumes")):
        vol = Path("/Volumes") / names[1]
        if not _exists(vol):
            return str(vol)
    elif len(names) >= 2 and names[0] == "mnt" and _exists(Path("/mnt")):
        vol = Path("/mnt") / names[1]
        if not _exists(vol):
            return str(vol)
    elif len(names) >= 3 and names[0] == "media" and _exists(Path("/media") / names[1]):
        vol = Path("/media") / names[1] / names[2]
        if not _exists(vol):
            return str(vol)
    elif len(names) >= 4 and list(names[:2]) == ["run", "media"] and _exists(Path("/run/media") / names[2]):
        vol = Path("/run/media") / names[2] / names[3]
        if not _exists(vol):
            return str(vol)
    return None


def _collect_json_strings(value: Any, agent: str, out: List[Tuple[str, str]]) -> None:
    if isinstance(value, str):
        out.append((value, agent))
        return
    if isinstance(value, list):
        for item in value:
            _collect_json_strings(item, agent, out)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and "\x00" not in k and _split(k) is None and "perm" in k.casefold():
                continue
            if isinstance(k, str):
                out.append((k, agent))
            _collect_json_strings(v, agent, out)


def _collect_toml_strings(text: str, agent: str, out: List[Tuple[str, str]]) -> None:
    for match in _TOML_QUOTED_RE.finditer(text):
        single = match.group(1)
        if single is not None:
            out.append((single, agent))
            continue
        double = match.group(2)
        if double is not None:
            out.append((re.sub(r"\\(.)", r"\1", double), agent))


def _collect_jetbrains_xml_strings(
    text: str,
    agent: str,
    resolved_home: Path,
    out: List[Tuple[str, str]],
) -> None:
    for match in _XML_QUOTED_RE.finditer(text):
        raw_inner = match.group(1) if match.group(1) is not None else (match.group(2) or "")
        if not raw_inner:
            continue
        unescaped = html.unescape(raw_inner) if "&" in raw_inner else raw_inner
        if unescaped.startswith("$USER_HOME$"):
            unescaped = _expand_jetbrains_user_home(unescaped, resolved_home)
        elif unescaped[:7].lower() == "file://" and unescaped[:8].lower() != "file:///":
            unescaped = unescaped[7:]
        out.append((unescaped, agent))


def _collect_vscdb_strings(path: Path, agent: str, out: List[Tuple[str, str]]) -> None:
    conn: Optional[sqlite3.Connection] = None
    try:
        real_target = Path(os.path.realpath(str(path)))
        conn = _connect_vscdb_ro(real_target)
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            ("history.recentlyOpenedPathsList",),
        ).fetchone()
        if not row or row[0] is None:
            return
        raw_val = row[0]
        text_val = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else str(raw_val)
        data = json.loads(text_val)
        _collect_json_strings(data, agent, out)
    except Exception:
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def find_moved(
    root: Optional[str] = None,
    home: Optional[Path] = None,
    app_data: Optional[Path] = None,
    skip_dirs: Optional[Sequence[str]] = None,
) -> MovedList:
    """Finds missing folders remembered by coding agents and locates where each folder went."""
    resolved_home, resolved_app_data, use_env = _resolve_locations(home, app_data)

    if skip_dirs is None:
        default_skips: List[str] = [
            tempfile.gettempdir(),
            str(resolved_app_data / "Claude" / "scratch-workspaces"),
        ]
        try:
            default_skips.append(os.path.realpath(tempfile.gettempdir()))
        except OSError:
            pass
        if sys.platform == "win32":
            default_skips.append(str(resolved_home / "AppData" / "Local" / "Temp"))
            local_app_data = _resolve_local_app_data(resolved_home, use_env)
            for msix_claude in _msix_claude_dirs(local_app_data):
                default_skips.append(str(msix_claude / "scratch-workspaces"))
        effective_skip_dirs: Sequence[str] = default_skips
    else:
        effective_skip_dirs = skip_dirs

    skip_keys: List[Tuple[str, ...]] = []
    for sd in effective_skip_dirs:
        sp = _split(str(sd))
        if sp and sp[1]:
            sk = _key(*sp)
            if sk not in skip_keys:
                skip_keys.append(sk)

    raw_items: List[Tuple[str, str]] = []
    for af in agent_files(root=root, home=home, app_data=app_data):
        if af.kind == "vscdb":
            _collect_vscdb_strings(af.path, af.agent, raw_items)
            continue
        try:
            raw_bytes = af.path.read_bytes()
            text = raw_bytes.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        if af.kind == "json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            _collect_json_strings(data, af.agent, raw_items)
        elif af.kind == "toml":
            _collect_toml_strings(text, af.agent, raw_items)
        elif af.kind == "jetbrains_xml":
            _collect_jetbrains_xml_strings(text, af.agent, resolved_home, raw_items)

    expanded_items: List[Tuple[str, str]] = []
    for raw_str, agent in raw_items:
        if not raw_str:
            continue
        if raw_str[:8].lower() == "file:///":
            decoded_uri = _decode_file_uri(raw_str)
            if decoded_uri:
                expanded_items.append((decoded_uri, agent))
            continue
        if _is_unreadable_drive_path(raw_str):
            expanded_items.append((raw_str, agent))
            continue
        if os.pathsep in raw_str and not raw_str.startswith(("file:", "http:", "https:", "-")):
            pieces = _split_pathsep(raw_str)
            if len(pieces) > 1 and all(_split(p) is not None for p in pieces if p):
                for p in pieces:
                    if p:
                        expanded_items.append((p, agent))
                continue
        expanded_items.append((raw_str, agent))

    unreadable_groups: Dict[str, Dict[str, Any]] = {}
    moved_groups: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    skipped_unmounted: Dict[str, int] = {}

    for text, agent in expanded_items:
        if _is_unreadable_drive_path(text):
            u_key = text.casefold() if _CASE_INSENSITIVE else text
            ug = unreadable_groups.get(u_key)
            if ug is None:
                unreadable_groups[u_key] = {
                    "old": text,
                    "agents": {agent},
                    "paths": 1,
                }
            else:
                ug["agents"].add(agent)
                ug["paths"] += 1
            continue

        parsed = _split(text)
        if parsed is None:
            continue
        anchor, names = parsed
        if not names:
            continue

        if sys.platform == "win32" and anchor == "/":
            continue

        path_key = _key(anchor, names)

        # Skip .claude/worktrees and skip_dirs
        if any(
            names[i].casefold() == ".claude" and names[i + 1].casefold() == "worktrees"
            for i in range(len(names) - 1)
        ):
            continue
        if any(_is_under(path_key, sk) for sk in skip_keys):
            continue
        if skip_dirs is None and any(
            names[i].casefold() == "claude" and names[i + 1].casefold() == "scratch-workspaces"
            for i in range(len(names) - 1)
        ):
            continue

        unmounted = _unmounted_root(anchor, names)
        if unmounted is not None:
            skipped_unmounted[unmounted] = skipped_unmounted.get(unmounted, 0) + 1
            continue

        candidate_path = _join_split(anchor, names)
        if _exists(Path(candidate_path)):
            continue

        if anchor == "/" and not _exists(Path("/" + names[0])):
            continue

        # Walk up while parent does not exist; A = first existing ancestor, M = A's child on that path.
        k = len(names) - 1
        while k > 0 and not _exists(Path(_join_split(anchor, names[:k]))):
            k -= 1

        m_names = names[: k + 1]
        m_key = _key(anchor, m_names)
        m_str = _join_split(anchor, m_names)
        a_path = Path(_join_split(anchor, names[:k]))

        mg = moved_groups.get(m_key)
        if mg is None:
            mg = {
                "old": m_str,
                "anchor": anchor,
                "m_names": m_names,
                "a_path": a_path,
                "agents": {agent},
                "paths": 1,
                "direct_subs": set(),
            }
            moved_groups[m_key] = mg
        else:
            if mg["old"].islower() and not m_str.islower():
                mg["old"] = m_str
                mg["m_names"] = m_names
            mg["agents"].add(agent)
            mg["paths"] += 1

        if len(names) > len(m_names):
            mg["direct_subs"].add(_fold(names[len(m_names)]))

    batches = _all_move_batches(root)
    results: List[MovedFolder] = []
    listings: Dict[str, List[Path]] = {}

    for m_key, mg in moved_groups.items():
        m_str = mg["old"]
        m_names = mg["m_names"]
        a_path = mg["a_path"]
        agents_sorted = sorted(mg["agents"])
        paths_count = mg["paths"]
        direct_subs: Set[str] = mg["direct_subs"]

        # 1. journal
        resolved_new: Optional[str] = None
        resolved_kind: Optional[str] = None
        candidates: List[str] = []

        for batch_moves in batches:
            matched_old: Optional[Tuple[str, List[str]]] = None
            for old_p, _ in batch_moves:
                old_parsed = _split(old_p)
                if old_parsed and old_parsed[1] and _is_under(m_key, _key(*old_parsed)):
                    matched_old = old_parsed
                    break
            if matched_old is not None:
                old_anchor, old_names = matched_old
                m_for_map = _join_split(old_anchor, list(old_names) + list(m_names[len(old_names) :]))
                mapped = PathMapper(batch_moves).map(m_for_map)
                if mapped and _exists(Path(mapped)):
                    resolved_new = mapped
                    resolved_kind = "journal"
                break

        # 2. same-name (at A/*/<name> and A/*/*/<name>, verified against direct_subs) or name-only, and 3. renamed
        if resolved_kind is None:
            target_cf = _fold(m_names[-1])
            same_all: List[Path] = []
            a_children = _list_subdirs(a_path, listings) if _is_dir(a_path) else []
            for c1 in a_children:
                c1_children = _list_subdirs(c1, listings)
                for c2 in c1_children:
                    if _fold(c2.name) == target_cf:
                        same_all.append(c2)
                    else:
                        for c3 in _list_subdirs(c2, listings):
                            if _fold(c3.name) == target_cf:
                                same_all.append(c3)

            # What agents remember inside a moved MCP server or venv is often a file (main.py) or a hidden
            # folder (.venv): the same-name folder must hold those too, not only subfolders.
            same_passing = [str(c) for c in same_all if _holds_all(c, direct_subs)]
            if len(same_all) == 1 and len(same_passing) == 1:
                resolved_new = same_passing[0]
                # A name alone proves nothing (the old folder may be deleted and this one another project):
                # only a folder that also holds what the agents remember inside the old one is sure.
                resolved_kind = "same-name" if direct_subs else "name-only"
            else:
                if len(direct_subs) >= 2:
                    renamed_matches = [str(c1) for c1 in a_children if _has_all_subs(c1, direct_subs, listings)]
                    if len(renamed_matches) == 1:
                        resolved_new = renamed_matches[0]
                        resolved_kind = "renamed"
                if resolved_kind is None and same_all:
                    resolved_kind = "ambiguous"
                    candidates = [str(c) for c in same_all]

        if resolved_kind is None:
            resolved_kind = "not-found"

        results.append(
            MovedFolder(
                old=m_str,
                new=resolved_new,
                kind=resolved_kind,
                candidates=candidates,
                agents=agents_sorted,
                paths=paths_count,
            )
        )

    for ug in unreadable_groups.values():
        results.append(
            MovedFolder(
                old=ug["old"],
                new=None,
                kind="unreadable",
                candidates=[],
                agents=sorted(ug["agents"]),
                paths=ug["paths"],
            )
        )

    sorted_unmounted = {k: skipped_unmounted[k] for k in sorted(skipped_unmounted)}
    return MovedList(results, skipped_unmounted=sorted_unmounted)


def after_moves(
    root: str,
    moves: Sequence[Move],
    tool: str,
    undoes: Optional[str] = None,
) -> Optional[RelinkReport]:
    """Journals a completed batch of moves and runs relink. Never raises."""
    if not moves:
        return None
    journal_warning: Optional[str] = None
    try:
        record_moves(root, moves, tool, undoes=undoes)
    except Exception as err:
        journal_warning = f"Could not write move journal: {err}"

    try:
        report = relink(moves, root=root)
    except Exception as err:
        report = RelinkReport(
            errors=[{"file": "relink", "error": f"{err} (run `smart-drive relink` again)"}]
        )

    if journal_warning:
        report.warnings.insert(0, journal_warning)
    return report


__all__ = [
    "Move",
    "BACKUP_SUFFIX",
    "JOURNAL_NAME",
    "AGENT_PROCESSES",
    "running_agents",
    "agents_still_running",
    "calling_agents",
    "PathMapper",
    "claude_slug",
    "AgentFile",
    "agent_files",
    "RelinkReport",
    "relink",
    "journal_path",
    "record_moves",
    "last_moves",
    "last_undoable_moves",
    "MovedFolder",
    "MovedList",
    "find_moved",
    "after_moves",
]
