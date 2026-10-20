"""smart_drive.core.system_refs - Detect folders referenced by OS services, PATH, installed apps, startups & shortcuts.

Prevents AutoZoner (`organize`) and ClassifierEngine (`classify`) from relocating directories that
Windows or installed applications actively depend on (such as `E:\\Git`, `E:\\SQL`, or folders with
active `.lnk` shortcuts, services, registry `InstallLocation` entries, or top-level uninstallers).

Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import ntpath
import os
from pathlib import Path
import re
import struct
import sys
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-Windows platforms
    winreg = None  # type: ignore[assignment]


MAX_SHORTCUT_FILES: int = 5000
MAX_DESKTOP_WALK_DEPTH: int = 3

_EXEC_EXT_RE = re.compile(
    r"\.(?:exe|sys|dll|bat|cmd|com|ps1|msc)(?=$|[\s,]|/)",
    re.IGNORECASE,
)
_ABSOLUTE_START_RE = re.compile(r'^"?(?:[A-Za-z]:[\\/]|\\\\)')
_ARG_PATH_START_RE = re.compile(r'(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s"]+[\\/])')
_ARG_TOKEN_RE = re.compile(r'[^\s"]+')
_ICON_INDEX_RE = re.compile(r",\s*-?\d+\s*$")
_ENV_VAR_RE = re.compile(r"%([^%]+)%")

_LNK_HEADER_SIZE = 0x4C
_MY_COMPUTER_CLSID = bytes.fromhex("e04fd020ea3a6910a2d808002b30309d")
_SHELL_EXT_SIGNATURE = b"\x04\x00\xef\xbe"  # extension block 0xBEEF0004 of a file/folder shell item
# Where the Unicode long name starts inside that block, by block version (as written by Windows 8 and 10/11)
_SHELL_EXT_NAME_OFFSETS = {8: 42, 9: 46}

# Windows' default PATHEXT, plus .ps1 and .dll (DLLs are searched for through PATH as well); the machine's own
# PATHEXT is added on top of these
_DEFAULT_PATH_EXECUTABLE_EXTS = (
    ".com", ".exe", ".bat", ".cmd", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msc", ".cpl", ".ps1", ".dll",
)

_UNINSTALLER_EXACT_NAMES = frozenset({
    "unins000.exe",
    "uninstall.exe",
    "uninst.exe",
})


@dataclass(frozen=True)
class SystemRef:
    """A reference from the operating system or an application to a path on disk."""

    # "path" | "env" | "service" | "installed_app" | "startup" | "shortcut" | "shortcut_dep" | "uninstaller"
    # ("env": an environment variable other than Path, e.g. JAVA_HOME, whose value is an absolute path)
    # ("shortcut_dep": a folder or file a shortcut starts in or passes to its program, not the shortcut's target)
    kind: str
    source: str  # e.g. "PATH (User)", "HKLM\\...\\Services\\MSSQLSERVER", "C:\\Users\\X\\Desktop\\Git Bash.lnk"
    target: str  # filesystem path pointed to by the reference

    def to_dict(self) -> Dict[str, str]:
        return {
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
        }


# ==============================================================================
# 1. Registry Abstraction (Testable on All Platforms)
# ==============================================================================

class RegistryReader:
    """Thin abstraction over Windows Registry reading so tests can inject a mock/dict reader."""

    def __init__(
        self,
        tree: Optional[Mapping[str, Any]] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._tree = dict(tree) if tree is not None else None
        self._env = dict(env) if env is not None else None

    def expand_env(self, value: str) -> str:
        """Expands %VAR% placeholders in a registry string."""
        if not value or "%" not in value:
            return value
        if self._env is not None:
            env_lower = {k.lower(): v for k, v in self._env.items()}
            return _ENV_VAR_RE.sub(lambda m: env_lower.get(m.group(1).lower(), m.group(0)), value)
        if winreg is not None and self._tree is None:
            try:
                return winreg.ExpandEnvironmentStrings(value)
            except OSError:
                pass
        env_lower = {k.lower(): v for k, v in os.environ.items()}
        return _ENV_VAR_RE.sub(lambda m: env_lower.get(m.group(1).lower(), m.group(0)), value)

    def get_values(self, key_path: str) -> Dict[str, Any]:
        """Returns {value_name: value_data} for a registry key, or {} on error."""
        if self._tree is not None:
            node = self._lookup_mock_node(key_path)
            if callable(node):
                node = node()
            if isinstance(node, Mapping):
                return {
                    k: v for k, v in node.items()
                    if not isinstance(v, Mapping) and not k.startswith("__")
                }
            return {}

        if winreg is None:
            return {}

        hive, subkey = self._split_hive(key_path)
        if hive is None:
            return {}

        out: Dict[str, Any] = {}
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as k:
                idx = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(k, idx)
                        out[name] = val
                        idx += 1
                    except OSError:
                        break
        except OSError:
            return {}
        return out

    def list_subkeys(self, key_path: str) -> List[str]:
        """Returns subkey names under `key_path`, or [] on error."""
        if self._tree is not None:
            node = self._lookup_mock_node(key_path)
            if callable(node):
                node = node()
            if isinstance(node, Mapping):
                subkeys = node.get("__subkeys__")
                if isinstance(subkeys, Sequence) and not isinstance(subkeys, (str, bytes)):
                    return [str(s) for s in subkeys]
                return [
                    k for k, v in node.items()
                    if (isinstance(v, Mapping) or callable(v)) and not k.startswith("__")
                ]
            return []

        if winreg is None:
            return []

        hive, subkey = self._split_hive(key_path)
        if hive is None:
            return []

        names: List[str] = []
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as k:
                idx = 0
                while True:
                    try:
                        names.append(winreg.EnumKey(k, idx))
                        idx += 1
                    except OSError:
                        break
        except OSError:
            return []
        return names

    def _lookup_mock_node(self, key_path: str) -> Any:
        if self._tree is None:
            return None
        norm_target = key_path.strip("\\/").replace("/", "\\").lower()
        for k, v in self._tree.items():
            norm_k = k.strip("\\/").replace("/", "\\").lower()
            if norm_k == norm_target:
                return v
            if norm_target.startswith(norm_k + "\\"):
                rem = norm_target[len(norm_k) + 1:]
                curr_sub: Any = v
                for part in [p for p in rem.split("\\") if p]:
                    if callable(curr_sub):
                        curr_sub = curr_sub()
                    if not isinstance(curr_sub, Mapping):
                        curr_sub = None
                        break
                    found = None
                    for sk, sv in curr_sub.items():
                        if sk.lower() == part.lower():
                            found = sv
                            break
                    curr_sub = found
                    if curr_sub is None:
                        break
                if curr_sub is not None:
                    return curr_sub
        parts = [p for p in key_path.strip("\\/").replace("/", "\\").split("\\") if p]
        curr: Any = self._tree
        for part in parts:
            if callable(curr):
                curr = curr()
            if not isinstance(curr, Mapping):
                return None
            matched = None
            for k, v in curr.items():
                if k.lower() == part.lower():
                    matched = v
                    break
            if matched is None:
                return None
            curr = matched
        return curr

    @staticmethod
    def _split_hive(key_path: str) -> Tuple[Optional[Any], str]:
        if winreg is None:
            return None, ""
        norm = key_path.strip("\\/").replace("/", "\\")
        parts = norm.split("\\", 1)
        hive_name = parts[0].upper()
        subkey = parts[1] if len(parts) > 1 else ""
        hive_map = {
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKEY_CURRENT_USER": winreg.HKEY_CURRENT_USER,
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
            "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE,
        }
        return hive_map.get(hive_name), subkey


# ==============================================================================
# 2. Pure Parsers: Command Lines, ImagePath, PATH, and MS-SHLLINK (.lnk)
# ==============================================================================

def _unescape_quoted_command(s: str) -> str:
    """Turns `"\\"C:\\x\\a.exe\\" --startup"` (a command line wrapped in quotes, inner quotes escaped) into `"C:\\x\\a.exe" --startup`."""
    if s.startswith('"\\"'):
        inner = s[1:-1] if s.endswith('"') and len(s) > 3 else s[1:]
        return inner.replace('\\"', '"')
    if s.startswith('\\"'):
        return s.replace('\\"', '"')
    return s


def _split_unquoted_program(s: str) -> Optional[str]:
    """Returns the program path at the start of an unquoted command line, or None when it names no known extension.

    The first `.exe`/`.dll`/... followed by a space is not always the end: in `E:\\My.com Tools\\app.exe -k` the
    text after `.com` is still a relative path, so the program runs on to `app.exe`.
    """
    first: Optional[str] = None
    for m in _EXEC_EXT_RE.finditer(s):
        candidate = s[:m.end()]
        if first is None:
            first = candidate
        rest = s[m.end():].lstrip()
        token = rest.split(None, 1)[0] if rest else ""
        if "\\" in token and not _ABSOLUTE_START_RE.match(token):
            continue
        return candidate
    return first


def _expand_env_text(s: str, reader: Optional[RegistryReader]) -> str:
    """Expands %VAR% placeholders through `reader`, or through the process environment without one."""
    if reader is not None:
        return reader.expand_env(s)
    if "%" in s:
        env_lower = {k.lower(): v for k, v in os.environ.items()}
        return _ENV_VAR_RE.sub(lambda m: env_lower.get(m.group(1).lower(), m.group(0)), s)
    return s


def _prepare_command_line(raw: str, reader: Optional[RegistryReader]) -> Optional[str]:
    """Expands %VAR%, unwraps escaped quotes and drops a trailing icon index from a registry command line."""
    if not raw or not isinstance(raw, str):
        return None

    s = raw.strip()
    if not s:
        return None

    s = _expand_env_text(s, reader)
    s = _unescape_quoted_command(s)

    # Strip icon index suffix like `,0` or `,-1`
    return _ICON_INDEX_RE.sub("", s).strip()


def _split_command_line(s: str) -> Tuple[str, str, bool]:
    """Splits a prepared command line into (program, arguments, split_found).

    `split_found` is False when no quote or known extension marks where the program ends; the whole
    line is then returned as both program and arguments.
    """
    # Handle quoted executable at the start: `"C:\Path\app.exe" --arg`
    if s.startswith('"'):
        end_quote = s.find('"', 1)
        if end_quote > 1:
            return s[1:end_quote].strip(), s[end_quote + 1:], True
        return s[1:].strip(), "", True

    program = _split_unquoted_program(s)
    if program:
        return program.strip(), s[len(program):], True
    return s, s, False


def extract_executable_path(
    raw: str,
    system_root: Optional[str] = None,
    reader: Optional[RegistryReader] = None,
) -> Optional[str]:
    """Extracts a clean filesystem path from a Windows service ImagePath, DisplayIcon, UninstallString, or Run entry."""
    s = _prepare_command_line(raw, reader)
    if not s:
        return None
    program, _args, _found = _split_command_line(s)
    return _normalize_program_path(program, system_root, reader)


def _normalize_program_path(
    program: str,
    system_root: Optional[str],
    reader: Optional[RegistryReader],
) -> Optional[str]:
    """Turns the program part of a command line into an absolute path, or None when it is not one."""
    s = program

    # Strip NT namespace prefix `\??\` or `\\?\`
    if s.startswith("\\??\\"):
        s = s[4:]
    elif s.startswith("\\\\?\\"):
        s = s[4:]

    # Strip surrounding quotes again if `\??\"C:\..."` was used
    s = s.strip('"').strip()
    if not s:
        return None

    sys_root = (
        system_root
        or (reader.expand_env("%SystemRoot%") if reader is not None else None)
        or os.environ.get("SystemRoot")
        or r"C:\Windows"
    )
    if sys_root == "%SystemRoot%":
        sys_root = r"C:\Windows"

    s_norm = s.replace("/", "\\")
    s_lower = s_norm.lower()
    if s_lower.startswith("\\systemroot\\"):
        s_norm = sys_root.rstrip("\\/") + "\\" + s_norm[len("\\systemroot\\"):]
    elif s_lower.startswith("systemroot\\"):
        s_norm = sys_root.rstrip("\\/") + "\\" + s_norm[len("systemroot\\"):]
    elif s_lower.startswith(("system32\\", "syswow64\\")):
        s_norm = sys_root.rstrip("\\/") + "\\" + s_norm

    # Must look like an absolute path (Windows drive letter, UNC path, or POSIX absolute path)
    if not (
        (len(s_norm) >= 3 and s_norm[0].isalpha() and s_norm[1] == ":" and s_norm[2] == "\\")
        or s_norm.startswith("\\\\")
        or s.startswith("/")
    ):
        return None

    return s_norm if "\\" in s_norm else s


def _clean_argument_path(candidate: str) -> Optional[str]:
    """Normalizes one absolute path found in a command-line argument, or None when nothing usable is left."""
    c = candidate.strip().strip('"').strip()
    c = re.split(r";(?=[A-Za-z]:[\\/])", c)[0]
    if "," in c:  # `x.dll,Start`: what follows the last comma is an entry point, not part of the path
        head, _, tail = c.rpartition(",")
        if tail and "\\" not in tail and "/" not in tail and re.search(r"\.\w{1,5}$", head):
            c = head
    c = c.rstrip(" ,;").replace("/", "\\")
    if c.startswith("\\\\?\\"):
        c = c[4:]
    c = re.sub(r"(?<=.)\\{2,}", r"\\", c)  # `E:\\Folder` -> `E:\Folder`; a leading UNC `\\` stays
    if "%" in c or not re.match(r"^(?:[A-Za-z]:\\|\\\\)", c):
        return None
    if len([part for part in c.split("\\") if part]) < 2:
        return None
    return c


def _absolute_paths_in_text(text: str) -> List[str]:
    """Finds the absolute Windows paths (drive letter or UNC) written anywhere in a command-line fragment.

    A path inside quotes ends at the closing quote. An unquoted one ends at whitespace, but it may carry
    spaces too (`... 128 C:\\Program Files\\X\\a.inf`), so the text up to the next option, path or quoted
    argument is reported as a second candidate.
    """
    found: List[str] = []
    for m in _ARG_PATH_START_RE.finditer(text):
        start = m.start()
        candidates: List[str] = []
        if text.count('"', 0, start) % 2 == 1:
            close = text.find('"', start)
            candidates.append(text[start:close if close != -1 else len(text)])
        else:
            tokens = list(_ARG_TOKEN_RE.finditer(text, start))
            if not tokens:
                continue
            candidates.append(tokens[0].group(0))
            end = tokens[0].end()
            for tok in tokens[1:]:
                word = tok.group(0)
                if text[tok.start() - 1] == '"' or word[0] in "-/" or _ARG_PATH_START_RE.match(word):
                    break
                end = tok.end()
            if end > tokens[0].end():
                candidates.append(text[start:end])
        for candidate in candidates:
            cleaned = _clean_argument_path(candidate)
            if cleaned and cleaned not in found:
                found.append(cleaned)
    return found


def extract_argument_paths(raw: str, reader: Optional[RegistryReader] = None) -> List[str]:
    """Absolute paths a command line passes to its program, e.g. the script in `pythonw.exe "E:\\tool\\tray.py"`.

    Covers quoted and unquoted arguments, `--opt=PATH` / `/opt:PATH` forms and `rundll32.exe X.dll,Entry`.
    When the program is not an absolute path (`wscript.exe E:\\x.vbs`, `python E:\\x.py`) the whole line is scanned.
    """
    s = _prepare_command_line(raw, reader)
    if not s:
        return []
    program, args, found_split = _split_command_line(s)
    if found_split and _normalize_program_path(program, None, reader) is not None:
        return _absolute_paths_in_text(args)
    return _absolute_paths_in_text(s)


def split_and_expand_path(
    raw_path: str,
    sep: Optional[str] = None,
    reader: Optional[RegistryReader] = None,
) -> List[str]:
    """Splits a PATH string, expands %VAR% placeholders, and strips quotes/empty segments."""
    if not raw_path:
        return []
    expanded = reader.expand_env(raw_path) if reader is not None else os.path.expandvars(raw_path)
    delimiter = sep or (";" if ";" in expanded or sys.platform == "win32" else os.pathsep)
    entries: List[str] = []
    for part in expanded.split(delimiter):
        cleaned = part.strip().strip('"').strip()
        if not cleaned or "%" in cleaned:
            continue
        entries.append(cleaned)
    return entries


@dataclass(frozen=True)
class LnkDetails:
    """What a Windows shortcut says about the files it uses."""

    target: Optional[str] = None       # LinkInfo LocalBasePath, else the path in the ID list, else the relative path
    working_dir: Optional[str] = None  # the "Start in" folder
    arguments: Optional[str] = None    # command-line arguments passed to the target


def parse_lnk_details(data: bytes, lnk_dir: Optional[str] = None) -> Optional[LnkDetails]:
    """Parses a Windows Shell Link (.lnk) binary buffer (MS-SHLLINK); None when it holds nothing usable.

    The target is the LinkInfo LocalBasePath when there is one. Many shortcuts are written without a LinkInfo
    block and name their target only in the ID list, or in the relative path (resolved against `lnk_dir`, the
    folder holding the .lnk, when given); both are used as fallbacks.
    """
    if not data or len(data) < _LNK_HEADER_SIZE:
        return None

    try:
        if struct.unpack_from("<I", data, 0x00)[0] != _LNK_HEADER_SIZE:
            return None
        link_flags = struct.unpack_from("<I", data, 0x14)[0]

        pos = _LNK_HEADER_SIZE
        id_items: List[bytes] = []
        if link_flags & 0x00000001:  # HasLinkTargetIDList
            if pos + 2 > len(data):
                return None
            id_list_size = struct.unpack_from("<H", data, pos)[0]
            id_items = _read_id_list_items(data, pos + 2, min(pos + 2 + id_list_size, len(data)))
            pos += 2 + id_list_size

        link_info_target: Optional[str] = None
        strings_reachable = True
        if link_flags & 0x00000002:  # HasLinkInfo
            link_info_size = struct.unpack_from("<I", data, pos)[0] if pos + 4 <= len(data) else 0
            if link_info_size < 28 or pos + link_info_size > len(data):
                strings_reachable = False
            else:
                link_info_target = _link_info_local_path(data, pos, link_info_size)
                pos += link_info_size

        strings = _read_lnk_string_data(data, pos, link_flags) if strings_reachable else {}
    except (struct.error, ValueError, IndexError):
        return None

    target = link_info_target or _id_list_path(id_items)
    relative = strings.get("relative_path")
    if not target and relative and lnk_dir:
        target = _resolve_relative_target(relative, lnk_dir)

    details = LnkDetails(
        target=target or None,
        working_dir=strings.get("working_dir") or None,
        arguments=strings.get("arguments") or None,
    )
    return details if (details.target or details.working_dir or details.arguments) else None


def parse_lnk_bytes(data: bytes) -> Optional[str]:
    """Parses a Windows Shell Link (.lnk) binary buffer and returns the target path it points to."""
    details = parse_lnk_details(data)
    return details.target if details else None


def _link_info_local_path(data: bytes, pos: int, link_info_size: int) -> Optional[str]:
    """The LocalBasePath (+ CommonPathSuffix) of the LinkInfo block that starts at `pos`, or None."""
    link_info_header_size, link_info_flags = struct.unpack_from("<II", data, pos + 4)

    # VolumeIDAndLocalBasePath bit (0x1)
    if not (link_info_flags & 0x00000001):
        return None

    local_base_offset = struct.unpack_from("<I", data, pos + 16)[0]
    common_path_offset = struct.unpack_from("<I", data, pos + 24)[0]

    # Prefer Unicode LocalBasePath when LinkInfoHeaderSize >= 0x24
    if link_info_header_size >= 0x24 and pos + 36 <= len(data):
        local_base_offset_uni, common_path_offset_uni = struct.unpack_from("<II", data, pos + 28)
        if local_base_offset_uni > 0 and pos + local_base_offset_uni < len(data):
            base_uni = _read_utf16le_z(data, pos + local_base_offset_uni, pos + link_info_size)
            suffix_uni = (
                _read_utf16le_z(data, pos + common_path_offset_uni, pos + link_info_size)
                if common_path_offset_uni > 0 and pos + common_path_offset_uni < len(data)
                else ""
            )
            combined = _join_base_and_suffix(base_uni, suffix_uni)
            if combined:
                return combined

    if local_base_offset > 0 and pos + local_base_offset < len(data):
        base_ansi = _read_ansi_z(data, pos + local_base_offset, pos + link_info_size)
        suffix_ansi = (
            _read_ansi_z(data, pos + common_path_offset, pos + link_info_size)
            if common_path_offset > 0 and pos + common_path_offset < len(data)
            else ""
        )
        combined = _join_base_and_suffix(base_ansi, suffix_ansi)
        if combined:
            return combined
    return None


def _shortcut_dependency_paths(details: LnkDetails, reader: Optional[RegistryReader]) -> List[str]:
    """Absolute paths a shortcut depends on besides its target: its "Start in" folder and paths in its arguments."""
    paths: List[str] = []
    if details.working_dir:
        working_dir = _clean_argument_path(_expand_env_text(details.working_dir, reader))
        if working_dir:
            paths.append(working_dir)
    if details.arguments:
        for arg_path in _absolute_paths_in_text(_expand_env_text(details.arguments, reader)):
            if arg_path not in paths:
                paths.append(arg_path)
    return paths


def _read_lnk_string_data(data: bytes, pos: int, link_flags: int) -> Dict[str, str]:
    """Reads the StringData section: name, relative path, working dir, arguments, icon location (those present)."""
    is_unicode = bool(link_flags & 0x00000080)
    width = 2 if is_unicode else 1
    out: Dict[str, str] = {}
    for bit, key in (
        (0x00000004, "name"),
        (0x00000008, "relative_path"),
        (0x00000010, "working_dir"),
        (0x00000020, "arguments"),
        (0x00000040, "icon_location"),
    ):
        if not link_flags & bit:
            continue
        if pos + 2 > len(data):
            break
        count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        end = pos + count * width
        if end > len(data):
            break
        raw = data[pos:end]
        out[key] = raw.decode("utf-16-le", errors="ignore") if is_unicode else _decode_ansi(raw)
        pos = end
    return out


def _read_id_list_items(data: bytes, start: int, end: int) -> List[bytes]:
    """Splits an ID list into its shell items (each keeps its 2-byte size prefix); stops at the terminator."""
    items: List[bytes] = []
    pos = start
    while pos + 2 <= end:
        size = struct.unpack_from("<H", data, pos)[0]
        if size < 2 or pos + size > end:
            break
        items.append(data[pos:pos + size])
        pos += size
    return items


def _id_list_path(items: Sequence[bytes]) -> Optional[str]:
    """The path `X:\\folder\\file` an ID list rooted at My Computer leads to, or None for anything else."""
    if len(items) < 2:
        return None
    root, volume = items[0], items[1]
    if len(root) < 20 or root[2] & 0x7F != 0x1F or root[4:20] != _MY_COMPUTER_CLSID:
        return None
    if len(volume) < 6 or volume[2] & 0x70 != 0x20:
        return None
    drive = volume[3:].split(b"\x00", 1)[0].decode("cp1252", errors="ignore")
    if not re.fullmatch(r"[A-Za-z]:\\?", drive):
        return None

    names: List[str] = []
    for item in items[2:]:
        name = _shell_item_name(item)
        if name is None:
            return None
        names.append(name)
    return drive[:2] + "\\" + "\\".join(names)


def _shell_item_name(item: bytes) -> Optional[str]:
    """The file or folder name of a file-system shell item (class 0x30-0x3F), or None."""
    if len(item) < 16 or item[2] & 0x70 != 0x30:
        return None

    # The item ends with the offset of its 0xBEEF0004 extension block, which carries the Unicode long name
    ext_off = struct.unpack_from("<H", item, len(item) - 2)[0]
    if 14 <= ext_off <= len(item) - 12 and item[ext_off + 4:ext_off + 8] == _SHELL_EXT_SIGNATURE:
        version = struct.unpack_from("<H", item, ext_off + 2)[0]
        name_off = _SHELL_EXT_NAME_OFFSETS.get(version)
        if name_off is not None and ext_off + name_off < len(item) - 2:
            long_name = _read_utf16le_z(item, ext_off + name_off, len(item) - 2)
            if _is_valid_file_name(long_name):
                return long_name

    # Otherwise the primary name (ANSI, or UTF-16 when the class has bit 0x04); a `NAME~1` alias is not the real name
    if item[2] & 0x04:
        primary = _read_utf16le_z(item, 14, len(item))
    else:
        end = item.find(b"\x00", 14)
        primary = _decode_ansi(item[14:end]) if end > 14 else ""
    return primary if _is_valid_file_name(primary) and "~" not in primary else None


def _is_valid_file_name(name: str) -> bool:
    return bool(name) and all(ord(ch) >= 32 and ch not in '\\/:*?"<>|' for ch in name)


def _resolve_relative_target(relative: str, lnk_dir: str) -> Optional[str]:
    """Resolves the shortcut's relative path against the folder that holds the .lnk file."""
    joined = ntpath.normpath(ntpath.join(lnk_dir, relative.strip()))
    return joined if re.match(r"^[A-Za-z]:\\", joined) else None


def _decode_ansi(raw: bytes) -> str:
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="ignore")


def _read_ansi_z(buf: bytes, start: int, limit: int) -> str:
    end = buf.find(b"\x00", start, min(limit, len(buf)))
    return _decode_ansi(buf[start:end] if end != -1 else buf[start:min(limit, len(buf))])


def _read_utf16le_z(buf: bytes, start: int, limit: int) -> str:
    max_end = min(limit, len(buf))
    idx = start
    while idx + 1 < max_end:
        if buf[idx] == 0 and buf[idx + 1] == 0:
            break
        idx += 2
    return buf[start:idx].decode("utf-16-le", errors="ignore")


def _join_base_and_suffix(base: str, suffix: str) -> str:
    b = base.strip()
    s = suffix.strip()
    if not b:
        return s
    if not s:
        return b
    return b.rstrip("\\/") + "\\" + s.lstrip("\\/")


def read_lnk_details(path: Union[str, Path]) -> Optional[LnkDetails]:
    """Reads a `.lnk` file on disk and returns what it points to and starts in, or None on error."""
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
    except OSError:
        return None
    return parse_lnk_details(data, lnk_dir=os.path.dirname(os.path.abspath(path)))


def parse_lnk_file(path: Union[str, Path]) -> Optional[str]:
    """Reads a `.lnk` file on disk and returns its target path, or None on error."""
    details = read_lnk_details(path)
    return details.target if details else None


# ==============================================================================
# 3. Uninstaller Heuristic (`looks_like_installed_app`)
# ==============================================================================

def looks_like_installed_app(folder: Union[str, Path]) -> Optional[str]:
    """Returns the top-level uninstaller executable name if `folder` looks like an installed app."""
    try:
        if not os.path.isdir(folder):
            return None
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                name = entry.name
                lower = name.lower()
                if lower in _UNINSTALLER_EXACT_NAMES:
                    return name
                if lower.endswith(".exe") and (lower.startswith("uninstall") or lower.startswith("unins")):
                    return name
    except OSError:
        return None
    return None


# ==============================================================================
# 4. Path Boundary Matching & SystemRefIndex
# ==============================================================================

@lru_cache(maxsize=8)
def _executable_extensions(pathext: str) -> FrozenSet[str]:
    exts = set(_DEFAULT_PATH_EXECUTABLE_EXTS)
    for ext in pathext.split(";"):
        ext = ext.strip().lower()
        if len(ext) > 1 and ext.startswith("."):
            exts.add(ext)
    return frozenset(exts)


def is_path_executable(filename: str) -> bool:
    """Whether a file sitting in a PATH folder can be run or loaded by name from there.

    Windows only starts a bare name from PATH when its extension is in PATHEXT, and DLLs are found through
    PATH too. A PDF, archive or model file in a PATH folder (such as the drive root) depends on nothing.
    """
    ext = os.path.splitext(filename)[1].lower()
    return bool(ext) and ext in _executable_extensions(os.environ.get("PATHEXT", ""))


def _normalize_for_match(path: str) -> Tuple[str, ...]:
    """Splits a path into casefolded segments for exact folder boundary matching."""
    cleaned = path.strip().strip('"').replace("/", "\\")
    parts = [p.lower() for p in cleaned.split("\\") if p and p != "."]
    return tuple(parts)


class SystemRefIndex:
    """Index of OS/application references pointing to paths on disk."""

    _UNINSTALL_KEYS: Tuple[str, ...] = (
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    )

    _RUN_KEYS: Tuple[str, ...] = (
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\RunOnce",
        r"HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
        r"HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce",
    )

    _USER_SHELL_FOLDERS_KEYS: Tuple[str, ...] = (
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    )

    _COMMON_SHELL_FOLDERS_KEYS: Tuple[str, ...] = (
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    )

    def __init__(self, refs: Optional[Iterable[SystemRef]] = None) -> None:
        dedup: List[SystemRef] = []
        seen = set()
        for ref in refs or ():
            if not ref.target:
                continue
            key = (ref.kind, ref.source, _normalize_for_match(ref.target))
            if key not in seen:
                seen.add(key)
                dedup.append(ref)
        self.refs: List[SystemRef] = dedup
        self._normalized_targets: List[Tuple[Tuple[str, ...], SystemRef]] = [
            (_normalize_for_match(r.target), r) for r in self.refs
        ]

    @classmethod
    def collect(
        cls,
        home: Optional[Path] = None,
        reader: Optional[Union[RegistryReader, Mapping[str, Any]]] = None,
        shortcut_roots: Optional[Sequence[Union[str, Path]]] = None,
    ) -> "SystemRefIndex":
        """Collects system references from PATH, services, installed apps, startup entries, and shortcuts.

        `shortcut_roots` are drive roots (or any folder) whose own `.lnk` files, not those in subfolders, count too.
        """
        refs: List[SystemRef] = []
        reg_reader: Optional[RegistryReader]
        if isinstance(reader, RegistryReader):
            reg_reader = reader
        elif isinstance(reader, Mapping):
            reg_reader = RegistryReader(tree=reader)
        elif sys.platform == "win32":
            reg_reader = RegistryReader()
        else:
            reg_reader = None

        # 1. Process environment PATH (all platforms) when no explicit mock registry reader overrides it
        if reader is None:
            env_path = os.environ.get("PATH", "")
            for entry in split_and_expand_path(env_path):
                refs.append(SystemRef(kind="path", source="PATH", target=entry))

        # 2. Windows Registry sources (or injected RegistryReader in tests on any platform)
        if reg_reader is not None:
            cls._collect_registry_path(reg_reader, refs)
            cls._collect_services(reg_reader, refs)
            cls._collect_installed_apps(reg_reader, refs)
            cls._collect_startup_entries(reg_reader, refs)

        # 3. Shortcut (.lnk) files in Start Menu, Desktop, and Taskbar
        if sys.platform == "win32" or home is not None or reg_reader is not None or shortcut_roots:
            cls._collect_shortcuts(home, refs, reader=reg_reader, extra_dirs=shortcut_roots)

        return cls(refs)

    @classmethod
    def _collect_registry_path(cls, reader: RegistryReader, out: List[SystemRef]) -> None:
        path_sources = (
            (r"HKCU\Environment", "User"),
            (r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "System"),
        )
        for key_path, scope in path_sources:
            try:
                vals = reader.get_values(key_path)
                for k, v in vals.items():
                    if not isinstance(v, str):
                        continue
                    if k.lower() == "path":
                        for entry in split_and_expand_path(v, sep=";", reader=reader):
                            out.append(SystemRef(kind="path", source=f"PATH ({scope})", target=entry))
                        continue
                    # JAVA_HOME, PT8HOME, a PyCharm bin folder...: any variable that holds an absolute path
                    for entry in split_and_expand_path(v, sep=";", reader=reader):
                        env_path = _clean_argument_path(entry)
                        if env_path:
                            out.append(SystemRef(kind="env", source=f"{k} ({scope})", target=env_path))
            except Exception:
                continue

    @classmethod
    def _collect_services(cls, reader: RegistryReader, out: List[SystemRef]) -> None:
        services_root = r"HKLM\SYSTEM\CurrentControlSet\Services"
        for svc_name in reader.list_subkeys(services_root):
            svc_key = f"{services_root}\\{svc_name}"
            try:
                vals = reader.get_values(svc_key)
                raw_img = None
                for k, v in vals.items():
                    if k.lower() == "imagepath" and isinstance(v, str):
                        raw_img = v
                        break
                if not raw_img:
                    continue
                exe = extract_executable_path(raw_img, reader=reader)
                if exe:
                    out.append(SystemRef(kind="service", source=f"service {svc_name}", target=exe))
                for arg_path in extract_argument_paths(raw_img, reader=reader):
                    out.append(SystemRef(kind="service", source=f"service {svc_name}", target=arg_path))
            except Exception:
                continue

    @classmethod
    def _collect_installed_apps(cls, reader: RegistryReader, out: List[SystemRef]) -> None:
        for uninstall_root in cls._UNINSTALL_KEYS:
            for sub in reader.list_subkeys(uninstall_root):
                sub_key = f"{uninstall_root}\\{sub}"
                try:
                    vals = reader.get_values(sub_key)
                    lower_vals = {k.lower(): v for k, v in vals.items()}
                    display_name = str(lower_vals.get("displayname") or sub).strip()
                    source_label = f'installed app "{display_name}"'

                    install_loc = lower_vals.get("installlocation")
                    if isinstance(install_loc, str) and install_loc.strip():
                        loc = reader.expand_env(install_loc.strip().strip('"'))
                        if loc:
                            out.append(SystemRef(kind="installed_app", source=source_label, target=loc))

                    display_icon = lower_vals.get("displayicon")
                    if isinstance(display_icon, str) and display_icon.strip():
                        icon_path = extract_executable_path(display_icon, reader=reader)
                        if icon_path:
                            out.append(SystemRef(kind="installed_app", source=source_label, target=icon_path))

                    uninstall_str = lower_vals.get("uninstallstring")
                    if isinstance(uninstall_str, str) and uninstall_str.strip():
                        uninst_path = extract_executable_path(uninstall_str, reader=reader)
                        if uninst_path and not os.path.basename(uninst_path).lower().startswith(("msiexec", "rundll32")):
                            out.append(SystemRef(kind="installed_app", source=source_label, target=uninst_path))
                        for arg_path in extract_argument_paths(uninstall_str, reader=reader):
                            out.append(SystemRef(kind="installed_app", source=source_label, target=arg_path))
                except Exception:
                    continue

    @classmethod
    def _collect_startup_entries(cls, reader: RegistryReader, out: List[SystemRef]) -> None:
        for run_key in cls._RUN_KEYS:
            try:
                vals = reader.get_values(run_key)
                for name, val in vals.items():
                    if not isinstance(val, str):
                        continue
                    exe = extract_executable_path(val, reader=reader)
                    if exe:
                        out.append(SystemRef(kind="startup", source=f"startup {name}", target=exe))
                    for arg_path in extract_argument_paths(val, reader=reader):
                        out.append(SystemRef(kind="startup", source=f"startup {name}", target=arg_path))
            except Exception:
                continue

    @classmethod
    def _resolve_shell_folder(
        cls,
        reader: Optional[RegistryReader],
        keys: Sequence[str],
        value_name: str,
    ) -> Optional[Path]:
        if reader is None:
            return None
        val_lower = value_name.lower()
        for key_path in keys:
            try:
                vals = reader.get_values(key_path)
                for k, v in vals.items():
                    if k.lower() == val_lower and isinstance(v, str) and v.strip():
                        expanded = reader.expand_env(v.strip().strip('"'))
                        if expanded and "%" not in expanded:
                            if os.sep == "/" and expanded.startswith("/"):
                                expanded = expanded.replace("\\", "/")
                            return Path(expanded)
            except Exception:
                continue
        return None

    @classmethod
    def _collect_shortcuts(
        cls,
        home: Optional[Path],
        out: List[SystemRef],
        reader: Optional[RegistryReader] = None,
        extra_dirs: Optional[Sequence[Union[str, Path]]] = None,
    ) -> None:
        dirs_to_scan: List[Tuple[Path, Optional[int]]] = []
        reg_desktop = cls._resolve_shell_folder(reader, cls._USER_SHELL_FOLDERS_KEYS, "Desktop")
        reg_common_desktop = cls._resolve_shell_folder(reader, cls._COMMON_SHELL_FOLDERS_KEYS, "Common Desktop")

        if home is not None:
            h = Path(home)
            if reg_desktop is not None:
                dirs_to_scan.append((reg_desktop, MAX_DESKTOP_WALK_DEPTH))
            else:
                dirs_to_scan.append((h / "Desktop", MAX_DESKTOP_WALK_DEPTH))
            if reg_common_desktop is not None:
                dirs_to_scan.append((reg_common_desktop, MAX_DESKTOP_WALK_DEPTH))
            dirs_to_scan.extend([
                (h / "Start Menu", None),
                (h / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu", None),
                (
                    h / "AppData" / "Roaming" / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar",
                    None,
                ),
            ])
        else:
            appdata = os.environ.get("APPDATA")
            programdata = os.environ.get("ProgramData")
            userprofile = os.environ.get("USERPROFILE")
            public_dir = os.environ.get("PUBLIC")
            if appdata:
                dirs_to_scan.append((Path(appdata) / "Microsoft" / "Windows" / "Start Menu", None))
                dirs_to_scan.append((
                    Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar",
                    None,
                ))
            if programdata:
                dirs_to_scan.append((Path(programdata) / "Microsoft" / "Windows" / "Start Menu", None))
            if reg_desktop is not None:
                dirs_to_scan.append((reg_desktop, MAX_DESKTOP_WALK_DEPTH))
            elif userprofile:
                dirs_to_scan.append((Path(userprofile) / "Desktop", MAX_DESKTOP_WALK_DEPTH))
            if reg_common_desktop is not None:
                dirs_to_scan.append((reg_common_desktop, MAX_DESKTOP_WALK_DEPTH))
            elif public_dir:
                dirs_to_scan.append((Path(public_dir) / "Desktop", MAX_DESKTOP_WALK_DEPTH))

        for extra in extra_dirs or ():
            dirs_to_scan.append((Path(extra), 1))  # only the .lnk files lying directly in the folder

        seen_dirs = set()
        for root_dir, max_depth in dirs_to_scan:
            try:
                if not root_dir.is_dir():
                    continue
                norm_dir = os.path.normcase(os.path.abspath(str(root_dir)))
                if norm_dir in seen_dirs:
                    continue
                seen_dirs.add(norm_dir)

                dir_scanned = 0
                stop_current_dir = False
                for dirpath, dirnames, filenames in os.walk(root_dir):
                    if max_depth is not None:
                        rel = os.path.relpath(dirpath, root_dir)
                        depth = 1 if rel == "." else len(Path(rel).parts) + 1
                        if depth >= max_depth:
                            dirnames[:] = []
                    for fname in filenames:
                        if not fname.lower().endswith(".lnk"):
                            continue
                        dir_scanned += 1
                        if dir_scanned > MAX_SHORTCUT_FILES:
                            stop_current_dir = True
                            break
                        lnk_path = os.path.join(dirpath, fname)
                        details = read_lnk_details(lnk_path)
                        if details is None:
                            continue
                        if details.target:
                            out.append(SystemRef(kind="shortcut", source=lnk_path, target=details.target))
                        for dep in _shortcut_dependency_paths(details, reader):
                            out.append(SystemRef(kind="shortcut_dep", source=lnk_path, target=dep))
                    if stop_current_dir:
                        break
            except OSError:
                continue

    def refs_under(self, folder: Union[str, Path], is_file: Optional[bool] = None) -> List[SystemRef]:
        """Returns all references whose target equals `folder` or resides inside `folder` (and for files, direct parent PATH entries)."""
        p_str = str(folder)
        folder_parts = _normalize_for_match(p_str)
        if not folder_parts:
            return []
        n = len(folder_parts)
        matched: List[SystemRef] = []
        for target_parts, ref in self._normalized_targets:
            if len(target_parts) >= n and target_parts[:n] == folder_parts:
                matched.append(ref)

        effective_is_file = os.path.isfile(p_str) if is_file is None else is_file
        if effective_is_file and n >= 2 and is_path_executable(folder_parts[-1]):
            parent_parts = folder_parts[:-1]
            for target_parts, ref in self._normalized_targets:
                if ref.kind == "path" and target_parts == parent_parts and ref not in matched:
                    matched.append(ref)

        return matched

    def check_folder(self, folder: Union[str, Path], is_file: Optional[bool] = None) -> List[SystemRef]:
        """Returns all system references under `folder`, plus a synthetic `uninstaller` ref if present on disk."""
        matched = list(self.refs_under(folder, is_file=is_file))
        if not is_file:
            uninst = looks_like_installed_app(folder)
            if uninst:
                matched.append(
                    SystemRef(
                        kind="uninstaller",
                        source=uninst,
                        target=os.path.join(str(folder), uninst),
                    )
                )
        return matched

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_refs": len(self.refs),
            "refs": [r.to_dict() for r in self.refs],
        }


def format_protected_by_system_entry(folder: Union[str, Path], refs: Sequence[SystemRef]) -> Dict[str, Any]:
    """Formats a folder and up to 3 representative references for JSON output."""
    unique_refs: List[Dict[str, str]] = []
    seen = set()
    for r in refs:
        key = (r.kind, r.source)
        if key not in seen:
            seen.add(key)
            unique_refs.append({"kind": r.kind, "source": r.source})
            if len(unique_refs) >= 3:
                break
    return {
        "path": str(folder),
        "refs": unique_refs,
    }


def summarize_system_refs(refs: Sequence[SystemRef]) -> str:
    """Builds a human-readable summary like `PATH, 4 shortcuts, installed app "Git"`."""
    parts: List[str] = []
    has_path = any(r.kind == "path" for r in refs)
    if has_path:
        parts.append("PATH")

    env_vars = [r.source for r in refs if r.kind == "env"]
    for env_var in dict.fromkeys(env_vars):
        parts.append(f"environment variable {env_var}")

    services = [r.source for r in refs if r.kind == "service"]
    for svc in dict.fromkeys(services):
        parts.append(svc)

    apps = [r.source for r in refs if r.kind == "installed_app"]
    for app in dict.fromkeys(apps):
        parts.append(app)

    startups = [r.source for r in refs if r.kind == "startup"]
    for st in dict.fromkeys(startups):
        parts.append(st)

    shortcut_count = len({r.source for r in refs if r.kind in ("shortcut", "shortcut_dep")})
    if shortcut_count == 1:
        parts.append("1 shortcut")
    elif shortcut_count > 1:
        parts.append(f"{shortcut_count} shortcuts")

    uninstallers = [r.source for r in refs if r.kind == "uninstaller"]
    for un in dict.fromkeys(uninstallers):
        parts.append(f"uninstaller {un}")

    return ", ".join(parts) if parts else "system reference"


__all__ = [
    "MAX_DESKTOP_WALK_DEPTH",
    "MAX_SHORTCUT_FILES",
    "LnkDetails",
    "RegistryReader",
    "SystemRef",
    "SystemRefIndex",
    "extract_argument_paths",
    "extract_executable_path",
    "format_protected_by_system_entry",
    "is_path_executable",
    "looks_like_installed_app",
    "parse_lnk_bytes",
    "parse_lnk_details",
    "parse_lnk_file",
    "read_lnk_details",
    "split_and_expand_path",
    "summarize_system_refs",
]
