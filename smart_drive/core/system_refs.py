"""smart_drive.core.system_refs - Detect folders referenced by OS services, PATH, installed apps, startups & shortcuts.

Prevents AutoZoner (`organize`) and ClassifierEngine (`classify`) from relocating directories that
Windows or installed applications actively depend on (such as `E:\\Git`, `E:\\SQL`, or folders with
active `.lnk` shortcuts, services, registry `InstallLocation` entries, or top-level uninstallers).

Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import struct
import sys
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-Windows platforms
    winreg = None  # type: ignore[assignment]


MAX_SHORTCUT_FILES: int = 5000
MAX_DESKTOP_WALK_DEPTH: int = 3

_EXEC_EXT_RE = re.compile(
    r"^(?P<path>.+?\.(?:exe|sys|dll|bat|cmd|com|ps1|msc))(?=$|[\s,]|/)",
    re.IGNORECASE,
)
_ICON_INDEX_RE = re.compile(r",\s*-?\d+\s*$")
_ENV_VAR_RE = re.compile(r"%([^%]+)%")

_UNINSTALLER_EXACT_NAMES = frozenset({
    "unins000.exe",
    "uninstall.exe",
    "uninst.exe",
})


@dataclass(frozen=True)
class SystemRef:
    """A reference from the operating system or an application to a path on disk."""

    kind: str    # "path" | "service" | "installed_app" | "startup" | "shortcut" | "uninstaller"
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

def extract_executable_path(
    raw: str,
    system_root: Optional[str] = None,
    reader: Optional[RegistryReader] = None,
) -> Optional[str]:
    """Extracts a clean filesystem path from a Windows service ImagePath, DisplayIcon, UninstallString, or Run entry."""
    if not raw or not isinstance(raw, str):
        return None

    s = raw.strip()
    if not s:
        return None

    if reader is not None:
        s = reader.expand_env(s)
    elif "%" in s:
        env_lower = {k.lower(): v for k, v in os.environ.items()}
        s = _ENV_VAR_RE.sub(lambda m: env_lower.get(m.group(1).lower(), m.group(0)), s)

    # Strip icon index suffix like `,0` or `,-1`
    s = _ICON_INDEX_RE.sub("", s).strip()

    # Handle quoted executable at the start: `"C:\Path\app.exe" --arg`
    if s.startswith('"'):
        end_quote = s.find('"', 1)
        if end_quote > 1:
            s = s[1:end_quote].strip()
        else:
            s = s[1:].strip()
    else:
        m = _EXEC_EXT_RE.match(s)
        if m:
            s = m.group("path").strip()

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


def parse_lnk_bytes(data: bytes) -> Optional[str]:
    """Parses a Windows Shell Link (.lnk) binary buffer (MS-SHLLINK) and extracts LocalBasePath."""
    if not data or len(data) < 0x4C:
        return None

    try:
        header_size = struct.unpack_from("<I", data, 0x00)[0]
        if header_size != 0x0000004C:
            return None

        link_flags = struct.unpack_from("<I", data, 0x14)[0]
        has_id_list = bool(link_flags & 0x00000001)
        has_link_info = bool(link_flags & 0x00000002)

        pos = 0x4C
        if has_id_list:
            if pos + 2 > len(data):
                return None
            id_list_size = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + id_list_size

        if not has_link_info or pos + 28 > len(data):
            return None

        link_info_size, link_info_header_size, link_info_flags = struct.unpack_from("<III", data, pos)
        if link_info_size < 28 or pos + link_info_size > len(data):
            return None

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
    except (struct.error, ValueError, IndexError):
        return None

    return None


def _read_ansi_z(buf: bytes, start: int, limit: int) -> str:
    end = buf.find(b"\x00", start, min(limit, len(buf)))
    raw = buf[start:end] if end != -1 else buf[start:min(limit, len(buf))]
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="ignore")


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


def parse_lnk_file(path: Union[str, Path]) -> Optional[str]:
    """Reads a `.lnk` file on disk and returns its target path, or None on error."""
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
        return parse_lnk_bytes(data)
    except OSError:
        return None


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
    ) -> "SystemRefIndex":
        """Collects system references from PATH, services, installed apps, startup entries, and shortcuts."""
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
        if sys.platform == "win32" or home is not None or reg_reader is not None:
            cls._collect_shortcuts(home, refs, reader=reg_reader)

        return cls(refs)

    @classmethod
    def _collect_registry_path(cls, reader: RegistryReader, out: List[SystemRef]) -> None:
        path_sources = (
            (r"HKCU\Environment", "PATH (User)"),
            (r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "PATH (System)"),
        )
        for key_path, label in path_sources:
            try:
                vals = reader.get_values(key_path)
                for k, v in vals.items():
                    if k.lower() == "path" and isinstance(v, str):
                        for entry in split_and_expand_path(v, sep=";", reader=reader):
                            out.append(SystemRef(kind="path", source=label, target=entry))
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
                        target = parse_lnk_file(lnk_path)
                        if target:
                            out.append(SystemRef(kind="shortcut", source=lnk_path, target=target))
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
        if effective_is_file and n >= 2:
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

    services = [r.source for r in refs if r.kind == "service"]
    for svc in dict.fromkeys(services):
        parts.append(svc)

    apps = [r.source for r in refs if r.kind == "installed_app"]
    for app in dict.fromkeys(apps):
        parts.append(app)

    startups = [r.source for r in refs if r.kind == "startup"]
    for st in dict.fromkeys(startups):
        parts.append(st)

    shortcut_count = sum(1 for r in refs if r.kind == "shortcut")
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
    "RegistryReader",
    "SystemRef",
    "SystemRefIndex",
    "extract_executable_path",
    "format_protected_by_system_entry",
    "looks_like_installed_app",
    "parse_lnk_bytes",
    "parse_lnk_file",
    "split_and_expand_path",
    "summarize_system_refs",
]
