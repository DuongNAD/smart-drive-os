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

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

Move = Tuple[str, str]
BACKUP_SUFFIX = ".bak.smart-drive"
JOURNAL_NAME = "moves.jsonl"

_CASE_INSENSITIVE = sys.platform in ("win32", "darwin")
_TOML_QUOTED_RE = re.compile(r"'([^'\r\n]*)'|\"((?:[^\"\\\r\n]|\\.)*)\"")
_CLAUDE_SLUG_RE = re.compile(r"[^A-Za-z0-9]")


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
    if not isinstance(text, str) or not text:
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
    return anchor, names


def _key(anchor: str, names: Sequence[str]) -> Tuple[str, ...]:
    """Normalized comparison key for (anchor, names); case-insensitive on Windows and macOS."""
    if _CASE_INSENSITIVE:
        return (anchor.casefold(), *(n.casefold() for n in names))
    return (anchor, *names)


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


class PathMapper:
    """Rewrites paths under moved folders while preserving separator and casing style."""

    def __init__(self, moves: Iterable[Move]) -> None:
        rules: List[Tuple[Tuple[str, ...], Tuple[str, List[str]], Tuple[str, List[str]]]] = []
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
            rules.append((old_k, (old_anchor, old_names), (new_anchor, new_names)))
            last_name = old_names[-1]
            if not last_name.isascii():
                has_non_ascii = True
            else:
                needles.add(last_name.casefold())

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
        for old_key, (old_anchor, old_names), (new_anchor, new_names) in self._rules:
            if key[:len(old_key)] != old_key:
                continue
            sep = "/" if anchor == "/" or "\\" not in text else "\\"
            tail = sep.join(new_names + names[len(old_key) - 1:])
            mapped = "/" + tail if new_anchor == "/" else new_anchor + sep + tail
            if text[-1] in "\\/" and len(text) > len(anchor) + 1:
                mapped += sep
            # Lowercase only when the agent lowercased a path that originally had capitals (e.g. Codex/Gemini).
            lowered = text.islower() and (anchor, names[:len(old_names)]) != (old_anchor, old_names)
            result = mapped.lower() if lowered else mapped
            return None if result == text else result
        return None

    def map_value(self, text: str) -> Optional[str]:
        """Rewrites one path string, or several paths joined by os.pathsep (e.g. PYTHONPATH)."""
        mapped, _ = self._map_value_with_originals(text)
        return mapped

    def _map_value_with_originals(self, text: str) -> Tuple[Optional[str], List[str]]:
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


def claude_slug(path: str) -> Optional[str]:
    """Converts a project path into its Claude Code project folder slug, or None if > 200 chars."""
    if not path or len(path) > 200:
        return None
    return _CLAUDE_SLUG_RE.sub("-", path)


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

    claude_dir = resolved_app_data / "Claude"
    candidates: List[AgentFile] = [
        AgentFile(resolved_home / ".claude.json", "Claude Code", "json", True),
        AgentFile(claude_dir / "claude_desktop_config.json", "Claude desktop", "json", True),
        AgentFile(claude_dir / "git-worktrees.json", "Claude desktop", "json", True),
    ]

    sessions_dir = claude_dir / "claude-code-sessions"
    if _is_dir(sessions_dir):
        try:
            for session_file in sorted(sessions_dir.rglob("*.json")):
                if _is_file(session_file) and not session_file.name.endswith(BACKUP_SUFFIX):
                    candidates.append(AgentFile(session_file, "Claude desktop", "json", False))
        except OSError:
            pass

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

    if root:
        candidates.append(AgentFile(Path(root) / ".mcp.json", "SSD .mcp.json", "json", True))

    return [af for af in candidates if _is_file(af.path)]


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
            if isinstance(k, str) and _split(k) is None and "perm" in k.casefold():
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
    existing_headers: Set[str] = set()
    for line in lines:
        if line.strip().startswith("["):
            existing_headers.add(line.split("#", 1)[0].strip())

    total_count = 0
    out_lines: List[str] = []

    for line in lines:
        is_header = line.strip().startswith("[")
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
            if double is not None:
                unescaped = re.sub(r"\\(.)", r"\1", double)
                mapped, origs = mapper._map_value_with_originals(unescaped)
                if mapped is not None:
                    line_count += len(origs)
                    escaped = mapped.replace("\\", "\\\\").replace('"', '\\"')
                    return f'"{escaped}"'
            return match.group(0)

        new_line = _TOML_QUOTED_RE.sub(_replace_match, line)
        if line_count > 0 and is_header:
            new_header = new_line.split("#", 1)[0].strip()
            if new_header in existing_headers:
                # Duplicate table header would make the TOML invalid; keep original header unchanged.
                out_lines.append(line)
                continue
            existing_headers.add(new_header)

        out_lines.append(new_line)
        total_count += line_count

    if total_count == 0:
        return None, 0

    prefix = b"\xef\xbb\xbf" if has_bom else b""
    return prefix + "".join(out_lines).encode("utf-8"), total_count


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
        with open(tmp_path, "wb") as f:
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
            full_old_slug = _CLAUDE_SLUG_RE.sub("-", old_clean)
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


def _repair_worktrees(
    new_candidates: Sequence[str],
    mapper: PathMapper,
    report: RelinkReport,
) -> None:
    """Repairs git worktrees for moved repositories and linked worktrees."""
    unique_dirs: List[Path] = []
    seen: Set[str] = set()
    for path_str in new_candidates:
        if not path_str:
            continue
        norm = path_str.rstrip("\\/") if len(path_str) > 3 else path_str
        key = norm.casefold() if _CASE_INSENSITIVE else norm
        if key in seen:
            continue
        seen.add(key)
        p = Path(norm)
        if _is_dir(p):
            unique_dirs.append(p)

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
        for venv_dir in (base, base / ".venv", base / "venv", base / "env"):
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
                    files = sorted(sp.glob("*.pth")) + sorted(sp.glob("*.egg-link"))
                except OSError:
                    files = []
                for f in files:
                    if not _is_file(f) or f.name.endswith(BACKUP_SUFFIX):
                        continue
                    af = AgentFile(path=f, agent="Python venv", kind="lines", backup=True)
                    try:
                        new_bytes, count = _process_lines_file(af, mapper, report)
                        if new_bytes is not None and count > 0:
                            if _write_agent_file(af, new_bytes, report):
                                report.updated.append({"file": str(af.path), "agent": af.agent, "paths": count})
                    except Exception as err:
                        report.errors.append({"file": str(af.path), "error": str(err)})

            report.warnings.append(
                f"Python venv {venv_dir}: console scripts of that venv may still start the old path; "
                "fix with `uv sync --reinstall` or a fresh venv."
            )


def relink(
    moves: Sequence[Move],
    root: Optional[str] = None,
    dry_run: bool = False,
    home: Optional[Path] = None,
    app_data: Optional[Path] = None,
) -> RelinkReport:
    """Rewrites paths remembered by coding agents after folders have been moved."""
    report = RelinkReport(dry_run=dry_run)
    mapper = PathMapper(moves)
    if not mapper:
        return report

    resolved_home, _, _ = _resolve_locations(home, app_data)
    claude_originals: List[str] = []

    for af in agent_files(root=root, home=home, app_data=app_data):
        try:
            if af.kind == "json":
                new_bytes, count = _process_json_file(af, mapper, report, claude_originals)
            elif af.kind == "toml":
                new_bytes, count = _process_toml_file(af, mapper, report)
            elif af.kind == "lines":
                new_bytes, count = _process_lines_file(af, mapper, report)
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

    if not dry_run:
        worktree_candidates = [new for _, new in moves]
        for cand in history_candidates:
            mapped_cand = mapper.map(cand)
            if mapped_cand:
                worktree_candidates.append(mapped_cand)
        _repair_worktrees(worktree_candidates, mapper, report)
        _relink_virtualenvs(moves, mapper, report)

    return report


def journal_path(root: str) -> str:
    """Path to the append-only move journal on the managed drive."""
    return os.path.join(os.path.abspath(root), ".smart_drive", JOURNAL_NAME)


def record_moves(root: str, moves: Sequence[Move], tool: str) -> None:
    """Appends one batch of moves to <root>/.smart_drive/moves.jsonl."""
    if not moves:
        return
    path = journal_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "tool": tool,
        "moves": [[old, new] for old, new in moves],
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        raw_moves = data.get("moves")
        if not isinstance(raw_moves, list) or not raw_moves:
            continue
        parsed_moves: List[Move] = []
        valid = True
        for item in raw_moves:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], str)
            ):
                parsed_moves.append((item[0], item[1]))
            else:
                valid = False
                break
        if valid and parsed_moves:
            result = dict(data)
            result["moves"] = parsed_moves
            return result
    return None


def after_moves(root: str, moves: Sequence[Move], tool: str) -> Optional[RelinkReport]:
    """Journals a completed batch of moves and runs relink. Never raises."""
    if not moves:
        return None
    journal_warning: Optional[str] = None
    try:
        record_moves(root, moves, tool)
    except OSError as err:
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
    "PathMapper",
    "claude_slug",
    "AgentFile",
    "agent_files",
    "RelinkReport",
    "relink",
    "journal_path",
    "record_moves",
    "last_moves",
    "after_moves",
]
