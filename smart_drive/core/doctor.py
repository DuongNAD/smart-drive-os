"""smart_drive.core.doctor - Read-only, offline environment report for bug reports (`smart-drive doctor`).

It is a command of its own because `status` and `checkup` cannot serve: both need a resolved drive root (they exit 2
without one), `status --json` has a schema agents already read, and `checkup` scans the whole drive (about 40 s on 167 GB)
and writes `checkup.json`, while a bug report must work when the root cannot be found and must be instant.

What the report holds: the SmartDrive-OS version and how it was installed, Python and its SQLite, the operating system,
the drive root and its filesystem, the coding agents and IDEs found on this computer and whether each has the smart-drive
MCP entry, the latest move batches and the last checkup score. It is built for pasting into a public issue:

- Read-only and offline: nothing is written (`.smart_drive/` is never created), the drive is never scanned, and this
  module opens no socket and starts no process.
- Every printed path shows the home folder as `~` and the user name as `<user>` (`Redactor`); `collect_report` runs it over
  the whole report as a last safety net.
- No file or folder name from the drive, no token and no content of an agent's config file is ever read into the report,
  only derived facts (registered yes/no, launcher kind, command found yes/no).

Where the collectors look (home, AppData, PATH, environment, redaction) is passed in, and the renderers only format a
report, so tests run all of it on a fake computer under any operating system.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import getpass
import importlib.metadata
import json
import locale
import os
from pathlib import Path
import platform
import re
import shutil
import site
import sqlite3
import struct
import sys
import unicodedata
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import tomllib
except ImportError:  # Python < 3.11: a Codex entry is then found by its table header alone
    tomllib = None  # type: ignore[assignment]

import smart_drive
from smart_drive.core import relink
from smart_drive.core.fsinfo import cluster_size_text, detect_filesystem, slack_cluster
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.sentinel import get_default_db_path
from smart_drive.mcp.registrar import _UV_ARCHIVE_BUCKET, DIST_NAME, build_mcp_entry

REPORT_VERSION = 1
ISSUES_URL = "https://github.com/DuongNAD/smart-drive-os/issues/new/choose"
BUG_REPORT_HINT = (
    f"To report a bug, run `smart-drive doctor --report` and paste its output into a new issue: {ISSUES_URL}"
)
REPORT_COMMENT = (
    "<!-- smart-drive doctor --report: paths are redacted (~ is the home folder, <user> the user name); "
    "no file names from the drive, tokens or config contents are included. -->"
)

REDACTED_HOME = "~"
REDACTED_USER = "<user>"

OUR_SERVER_NAME = "smart-drive"
CHECKUP_FILE = "checkup.json"
RECENT_BATCHES = 5
JOURNAL_TOOLS = ("organize", "classify", "undo", "relink", "paths")

# The environment variables worth knowing about; only their names are reported, never their values.
ENV_VAR_PREFIX = "SMART_DRIVE_"
ENV_VAR_NAMES = frozenset({
    "KINGSTON_SSD_ROOT", "ANTIGRAVITY_HOME", "GEMINI_HOME", "CONSTELLATION_HOME", "SD_SELFTEST", "CLAUDECODE",
})

# Agents whose MCP entry lives in a TOML file (every other one is JSON)
TOML_AGENTS = ("Codex",)

_JOURNAL_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
_CHECKUP_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z?")
_EXECUTABLE_SUFFIX = re.compile(r"\.(?:exe|cmd|bat)$", re.IGNORECASE)
_TOML_OUR_TABLE = re.compile(
    r"""^[ \t]*\[[ \t]*mcp_servers[ \t]*\.[ \t]*(?:smart-drive|"smart-drive"|'smart-drive')[ \t]*\]""",
    re.MULTILINE,
)

# What may follow a path or a name inside a text without belonging to it
_AFTER_NAME = r"""[\s"'`,;:)\]}>]"""
# A word of a free text; the ones that look like paths are searched for the user name inside
_WORD = re.compile(r"""[^\s"'`]+""")
# A Markdown code span: a run of backticks, the text, the same run again
_CODE_SPAN = re.compile(r"(`+).+?\1", re.DOTALL)


# ----------------------------------------------------------------------------------------------------------------------
# Redaction
# ----------------------------------------------------------------------------------------------------------------------

def _spellings(text: str) -> List[str]:
    """The text as given and in composed and decomposed Unicode form (macOS lists names decomposed)."""
    forms = [text]
    for form in ("NFC", "NFD"):
        other = unicodedata.normalize(form, text)
        if other not in forms:
            forms.append(other)
    return forms


class Redactor:
    """Shows the home folder as `~` and the user's name as `<user>` in the paths and texts of a report.

    A path in the home folder loses its prefix (`C:\\Users\\Alice\\x` -> `~\\x`). The user name is then replaced wherever
    it is a whole path component (`/media/alice/T7`, `D:\\Users\\alice\\x`, `\\\\server\\share\\alice`), as the 8.3 short
    form of a long Windows name (`RUNNER~1`), and, for names of three or more characters, anywhere inside a path-like
    word (`/Volumes/alice-ssd`). Plain words are left alone: with the user `ubuntu`, `Ubuntu 24.04 LTS` stays as it is.
    """

    def __init__(
        self,
        homes: Sequence[str],
        user_names: Sequence[str],
        windows: bool,
        case_insensitive: Optional[bool] = None,
    ) -> None:
        self.windows = bool(windows)
        self.case_insensitive = self.windows if case_insensitive is None else bool(case_insensitive)
        self._separators = "\\/" if self.windows else "/"
        separator = r"[\\/]" if self.windows else "/"
        after = "(?=$|" + separator + "|" + _AFTER_NAME + ")"

        before_home = r"(?<![\w.~\-\\/])" if self.windows else r"(?<![\w.~\-/])"
        flags = re.IGNORECASE if self.case_insensitive else 0
        self._home_rules = [
            re.compile(before_home + self._home_body(home, separator) + after, flags)
            for home in self._clean_homes(homes)
        ]

        names = self._clean_names(user_names)
        # A whole path component: the part after a separator that ends at the next one (or at the end)
        self._component_rule = (
            re.compile(r"(?<=[\\/])(?:" + "|".join(re.escape(n) for n in names) + ")" + after, re.IGNORECASE)
            if names else None
        )
        # The 8.3 short form Windows gives a long name or one with a space or a dot: RUNNER~1 for `runneradmin`
        prefixes = self._short_prefixes(names) if self.windows else []
        self._short_rule = (
            re.compile(r"(?<=[\\/])(?:" + "|".join(re.escape(p) for p in prefixes) + ")~[0-9]+" + after, re.IGNORECASE)
            if prefixes else None
        )
        # Inside a path-like word, for names that are long enough not to hit unrelated words; a `<user>` that an earlier
        # step wrote stays as it is (a user called `user` must not turn it into `<<user>>`)
        inner = [n for n in names if len(n) >= 3]
        self._inner_rule = (
            re.compile("(?P<keep>" + re.escape(REDACTED_USER) + ")|(?:" + "|".join(re.escape(n) for n in inner) + ")",
                       re.IGNORECASE)
            if inner else None
        )

    def _clean_homes(self, homes: Sequence[str]) -> List[str]:
        """Home folders without trailing separators, root-like and duplicate ones, longest first."""
        cleaned: List[str] = []
        seen = set()
        for raw in homes:
            home = str(raw or "").rstrip(self._separators)
            if not home or (self.windows and re.fullmatch(r"[A-Za-z]:", home)):
                continue  # a root ("/", "C:\\", "\\") would turn every path into `~...`
            for spelling in _spellings(home):
                key = spelling.casefold() if self.case_insensitive else spelling
                if key not in seen:
                    seen.add(key)
                    cleaned.append(spelling)
        return sorted(cleaned, key=len, reverse=True)

    @staticmethod
    def _clean_names(user_names: Sequence[str]) -> List[str]:
        """User names without empty and duplicate ones (case-insensitively), longest first."""
        cleaned: List[str] = []
        seen = set()
        for raw in user_names:
            name = str(raw or "").strip()
            if not name:
                continue
            for spelling in _spellings(name):
                if spelling.casefold() not in seen:
                    seen.add(spelling.casefold())
                    cleaned.append(spelling)
        return sorted(cleaned, key=len, reverse=True)

    def _home_body(self, home: str, separator: str) -> str:
        """The pattern for one home folder: its characters escaped, every run of separators any run of them."""
        body: List[str] = []
        for char in home:
            if char in self._separators:
                if not body or body[-1] != separator + "+":
                    body.append(separator + "+")
            else:
                body.append(re.escape(char))
        return "".join(body)

    @staticmethod
    def _short_prefixes(names: Sequence[str]) -> List[str]:
        """The first six characters (spaces and dots left out) of the names Windows shortens to PREFIX~1."""
        prefixes: List[str] = []
        for name in names:
            if len(name) > 8 or " " in name or "." in name:
                prefix = name.replace(" ", "").replace(".", "")[:6]
                if prefix and prefix not in prefixes:
                    prefixes.append(prefix)
        return prefixes

    def _structure(self, value: str) -> str:
        """The home folder and the whole-component forms of the user name; these never need a word to look like a path."""
        for rule in self._home_rules:
            value = rule.sub(REDACTED_HOME, value)
        if self._component_rule is not None:
            value = self._component_rule.sub(REDACTED_USER, value)
        if self._short_rule is not None:
            value = self._short_rule.sub(REDACTED_USER, value)
        return value

    def _inner(self, value: str) -> str:
        if self._inner_rule is None:
            return value
        return self._inner_rule.sub(lambda match: match.group("keep") or REDACTED_USER, value)

    def _word(self, match: "re.Match[str]") -> str:
        word = match.group(0)
        if word.startswith("~") or any(separator in word for separator in self._separators):
            return self._inner(word)
        return word

    def path(self, value: str) -> str:
        """A path; the whole value counts as one path-like word, since paths may contain spaces."""
        return self._inner(self._structure(str(value)))

    def text(self, value: str) -> str:
        """A free text such as an error message: only its path-like words are searched for the user name."""
        return _WORD.sub(self._word, self._structure(str(value)))

    def scrub(self, data: Any) -> Any:
        """A copy of dicts and lists with `text()` applied to every string (keys are left as they are)."""
        if isinstance(data, str):
            return self.text(data)
        if isinstance(data, dict):
            return {key: self.scrub(value) for key, value in data.items()}
        if isinstance(data, (list, tuple)):
            return [self.scrub(item) for item in data]
        return data

    @classmethod
    def from_environment(cls) -> "Redactor":
        """A Redactor for the person running this: the home folder, the login name, and the home's own name when it is
        the user's (`/home/alice`, `C:\\Users\\Alice`) and not just a folder called `home` (a sandbox)."""
        windows = os.name == "nt"
        homes: List[str] = []
        try:
            homes.append(str(Path.home()))
        except Exception:  # noqa: BLE001 - no home folder known: the user name is still redacted
            pass
        for home in list(homes):
            try:
                homes.append(os.path.realpath(home))
            except (OSError, ValueError):
                pass
        names: List[str] = []
        try:
            names.append(getpass.getuser())
        except Exception:  # noqa: BLE001 - getpass raises when neither the environment nor the user database has a name
            pass
        for variable in ("USERNAME", "USER", "LOGNAME"):
            value = os.environ.get(variable)
            if value:
                names.append(value)
        for home in homes:
            folder = Path(home)
            if folder.parent.name.lower() in ("users", "home"):
                names.append(folder.name)
        return cls(homes, names, windows, windows or sys.platform == "darwin")


# ----------------------------------------------------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------------------------------------------------

def _guarded(build: Callable[[], Any], redactor: Redactor) -> Any:
    """A section's data, or {"error": ...} when building it raised: one broken section never costs the report."""
    try:
        return build()
    except Exception as err:  # noqa: BLE001 - a diagnostic command must report on a broken machine, not crash on it
        return {"error": f"{type(err).__name__}: {redactor.text(str(err))}"}


def _failure(section: Any) -> Optional[str]:
    """The error text of a section that could not be collected ({"error": ...} and nothing else), else None."""
    if isinstance(section, dict) and set(section) == {"error"}:
        return str(section["error"])
    return None


def _checked_time(value: Any, pattern: "re.Pattern[str]") -> Optional[str]:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def windows_name(release: str, version: str) -> str:
    """'Windows 11' for release 10 or 11 at build 22000 or later (older Pythons call Windows 11 release 10)."""
    build = (version or "").rsplit(".", 1)[-1]
    if release in ("10", "11") and build.isdigit() and int(build) >= 22000:
        return "Windows 11"
    return f"Windows {release}" if release else "Windows"


def os_release_name(text: str) -> Optional[str]:
    """PRETTY_NAME of an os-release file ('Ubuntu 24.04 LTS'), or None when it has none."""
    for line in text.splitlines():
        key, separator, value = line.strip().partition("=")
        if key == "PRETTY_NAME" and separator:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return re.sub(r"\\([\"\\$`])", r"\1", value) or None
    return None


def _linux_name() -> str:
    for candidate in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                name = os_release_name(handle.read())
        except OSError:
            continue
        if name:
            return name
    return "Linux"


def _has_fts5() -> bool:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _same_path(first: str, second: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))
    except (OSError, ValueError):
        return False


# ----------------------------------------------------------------------------------------------------------------------
# SmartDrive-OS: version and how it was installed
# ----------------------------------------------------------------------------------------------------------------------

_LOOK_UP = object()  # install_method(dist=...) default: ask importlib.metadata


def _find_distribution() -> Any:
    try:
        return importlib.metadata.distribution(DIST_NAME)
    except Exception:  # noqa: BLE001 - not installed (PackageNotFoundError) or damaged metadata
        return None


def _dist_text(dist: Any, name: str) -> Optional[str]:
    try:
        text = dist.read_text(name)
    except Exception:  # noqa: BLE001
        return None
    return text if isinstance(text, str) else None


def _direct_url(dist: Any) -> Dict[str, Any]:
    """The distribution's direct_url.json (PEP 610): where pip installed it from, and whether editable."""
    text = _dist_text(dist, "direct_url.json")
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_editable_install_of(direct_url: Dict[str, Any], checkout: Path) -> bool:
    dir_info = direct_url.get("dir_info")
    if not (isinstance(dir_info, dict) and dir_info.get("editable")):
        return False
    target = relink._decode_file_uri(direct_url.get("url"))
    return target is not None and _same_path(target, str(checkout))


def _under_user_site(package_dir: Path) -> bool:
    try:
        user_site = site.getusersitepackages()
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(user_site, str) or not user_site:
        return False
    base = os.path.normcase(os.path.realpath(user_site)).rstrip("\\/")
    target = os.path.normcase(os.path.realpath(str(package_dir)))
    return target == base or target.startswith(base + os.sep)


def install_method(
    package_dir: "os.PathLike[str] | str | None" = None,
    prefix: "os.PathLike[str] | str | None" = None,
    base_prefix: "os.PathLike[str] | str | None" = None,
    dist: Any = _LOOK_UP,
) -> Dict[str, Any]:
    """How this copy of SmartDrive-OS was installed, from where it runs and the metadata pip left.

    ``dist`` is the importlib.metadata distribution of smart-drive-os (None when it is not installed); it needs only
    ``.version`` and ``.read_text(name)``, so tests pass fakes. Returns install_method, install_detail, installer,
    installed_version and git_commit; the url an editable install points at is compared and never reported.
    """
    package = Path(package_dir) if package_dir is not None else Path(smart_drive.__file__).resolve().parent
    env_prefix = Path(sys.prefix if prefix is None else prefix)
    env_base = Path(sys.base_prefix if base_prefix is None else base_prefix)
    if dist is _LOOK_UP:
        dist = _find_distribution()
    direct_url = _direct_url(dist) if dist is not None else {}

    if not any(part.lower() in ("site-packages", "dist-packages") for part in package.parts):
        if dist is None:
            method, detail = "source", "checkout run directly (not installed)"
        elif _is_editable_install_of(direct_url, package.parent):
            method, detail = "source", "editable install of this checkout"
        else:
            method, detail = "source", "checkout run directly; another installed copy exists"
    elif _UV_ARCHIVE_BUCKET.fullmatch(env_prefix.parent.name):
        method, detail = "uvx", "uvx (temporary uv environment)"
    elif relink._is_file(env_prefix / "pipx_metadata.json"):
        if env_prefix.parent.name == ".cache":
            method, detail = "pipx", "pipx run (temporary environment)"
        else:
            method, detail = "pipx", "pipx install"
    elif relink._is_file(env_prefix / "uv-receipt.toml"):
        method, detail = "uv tool", "uv tool install"
    elif relink._is_dir(env_prefix / "conda-meta"):
        method, detail = "pip", "conda environment"
    elif os.path.normcase(str(env_prefix)) != os.path.normcase(str(env_base)):
        method, detail = "pip", "virtual environment"
    elif _under_user_site(package):
        method, detail = "pip", "user site-packages (--user)"
    else:
        method, detail = "pip", "system Python"

    installer = (_dist_text(dist, "INSTALLER") or "").strip() or None if dist is not None else None
    installed_version = getattr(dist, "version", None) if dist is not None else None
    vcs_info = direct_url.get("vcs_info")
    commit = vcs_info.get("commit_id") if isinstance(vcs_info, dict) else None
    return {
        "install_method": method,
        "install_detail": detail,
        "installer": installer,
        "installed_version": installed_version if isinstance(installed_version, str) else None,
        "git_commit": commit[:12] if isinstance(commit, str) and commit else None,
    }


# ----------------------------------------------------------------------------------------------------------------------
# Section collectors: Python, operating system, drive
# ----------------------------------------------------------------------------------------------------------------------

def _collect_python(redactor: Redactor) -> Dict[str, Any]:
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "bits": struct.calcsize("P") * 8,
        "executable": redactor.path(sys.executable or ""),
        "virtual_env": sys.prefix != sys.base_prefix,
        "sqlite_version": sqlite3.sqlite_version,
        "fts5": _has_fts5(),
        "encoding": {
            "filesystem": sys.getfilesystemencoding(),
            "preferred": locale.getpreferredencoding(False),
            "utf8_mode": bool(sys.flags.utf8_mode),
        },
    }


def _collect_os() -> Dict[str, Any]:
    # Never platform.node(), platform.uname() or, on Linux, platform.version(): they carry host and builder names
    system = platform.system()
    release = platform.release()
    if system == "Windows":
        win_release, win_version = platform.win32_ver()[:2]
        name, version = windows_name(win_release, win_version), win_version or release
    elif system == "Darwin":
        name, version = f"macOS {platform.mac_ver()[0]}".strip(), release
    elif system == "Linux":
        name, version = _linux_name(), release
    else:
        name, version = system or "unknown", release
    return {
        "system": system,
        "name": name,
        "version": version,
        "machine": platform.machine(),
        "wsl": system == "Linux" and "microsoft" in release.lower(),
    }


def _collect_root(root: Optional[str], redactor: Redactor) -> Tuple[Dict[str, Any], Optional[str]]:
    """The root section and the root's real path (None when none was found; it is never reported unredacted)."""
    try:
        resolved = resolve_drive_root(root)
    except DriveRootNotFound as err:
        section = {
            "found": False, "path": None, "found_by": None, "error": redactor.text(str(err)), "is_mount_point": None,
        }
        return section, None
    section = {
        "found": True,
        "path": redactor.path(resolved.path),
        "found_by": resolved.source,
        "error": None,
        "is_mount_point": os.path.ismount(resolved.path),
    }
    return section, resolved.path


def _collect_filesystem(root: str, redactor: Redactor) -> Dict[str, Any]:
    info = detect_filesystem(root)
    cluster, cluster_source = slack_cluster(info)
    try:
        usage = shutil.disk_usage(root)
        total, free = usage.total, usage.free
    except OSError:
        total = free = None
    return {
        "type": info.fs_type,
        "name": info.display_name,
        "allocation_unit_bytes": cluster,
        "cluster_size_source": cluster_source,
        "detected_via": info.source,
        "mount_point": redactor.path(info.mount_point) if info.mount_point else None,
        "total_bytes": total,
        "free_bytes": free,
    }


def _collect_search_index(root: str) -> Dict[str, Any]:
    database = get_default_db_path(root)
    present = os.path.isfile(database)
    return {"present": present, "size_bytes": os.path.getsize(database) if present else 0}


def _collect_checkup(root: str) -> Dict[str, Any]:
    """The score and time of the last checkup. The file also lists folder names, so nothing else is read from it."""
    path = os.path.join(root, ".smart_drive", CHECKUP_FILE)
    if not os.path.isfile(path):
        return {"status": "not run", "score": None, "timestamp": None}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:  # noqa: BLE001 - unreadable, not UTF-8, not JSON, too deeply nested ...
        data = None
    if not isinstance(data, dict):
        return {"status": "unreadable", "score": None, "timestamp": None}
    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        score = None
    return {"status": "ok", "score": score, "timestamp": _checked_time(data.get("timestamp"), _CHECKUP_TIME)}


def _collect_move_journal(root: str) -> Dict[str, Any]:
    """Status, size and the latest batches of the move journal; times, tools and counts only, never a path."""
    path = relink.journal_path(root)
    if not os.path.isfile(path):
        return {"status": "empty", "batch_count": 0, "recent": []}
    batches = 0
    lines = 0
    latest: "deque[Dict[str, Any]]" = deque(maxlen=RECENT_BATCHES)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    lines += 1
                batch = relink._parse_batch_line(line)
                if batch is None:
                    continue
                batches += 1
                latest.append({
                    "time": batch.get("time"),
                    "tool": batch.get("tool"),
                    "moves": len(batch["moves"]),
                    "undoes": batch.get("undoes"),
                })
    except OSError:
        return {"status": "unreadable", "batch_count": 0, "recent": []}
    if batches == 0:
        return {"status": "unreadable" if lines else "empty", "batch_count": 0, "recent": []}

    newest_first = list(latest)[::-1]
    recent = []
    for position, batch in enumerate(newest_first):
        tool = batch["tool"] if isinstance(batch["tool"], str) and batch["tool"] in JOURNAL_TOOLS else "other"
        undone = tool in relink.UNDOABLE_TOOLS and any(
            isinstance(later["undoes"], str) and later["undoes"] == batch["time"] for later in newest_first[:position]
        )
        recent.append({
            "time": _checked_time(batch["time"], _JOURNAL_TIME) or "?",
            "tool": tool,
            "moves": batch["moves"],
            "undoes": None if batch["undoes"] is None else _checked_time(batch["undoes"], _JOURNAL_TIME) or "?",
            "undone": undone,
        })
    return {"status": "ok", "batch_count": batches, "recent": recent}


# ----------------------------------------------------------------------------------------------------------------------
# Coding agents and IDEs: found, and whether they hold the smart-drive MCP entry
# ----------------------------------------------------------------------------------------------------------------------

@dataclass
class _Machine:
    """Where the coding agents keep their files on this computer, and how programs are looked up."""

    home: Path
    app_data: Path
    local_app_data: Path
    use_env: bool
    environ: Mapping[str, str]
    which: Callable[[str], Optional[str]]
    expected: Optional[Dict[str, Any]]  # the entry `smart-drive mcp register` would write for this install

    def on_path(self, program: str) -> bool:
        try:
            return self.which(program) is not None
        except Exception:  # noqa: BLE001
            return False


@dataclass
class _Agent:
    name: str
    files: List[Path] = field(default_factory=list)     # any of these files means it is installed
    dirs: List[Path] = field(default_factory=list)      # any of these folders means it is installed
    programs: List[str] = field(default_factory=list)   # any of these on PATH means it is installed
    configs: List[Path] = field(default_factory=list)   # where its MCP entry would be
    kind: str = ""                                       # "json", "toml", "claude-code"; "" for IDEs with no MCP check


def _agents_of(machine: _Machine) -> List[_Agent]:
    home, app = machine.home, machine.app_data
    override = (machine.environ.get("ANTIGRAVITY_HOME") or machine.environ.get("GEMINI_HOME")) if machine.use_env else None
    antigravity = Path(override) if override else home / ".gemini" / "antigravity"
    claude_dirs = [app / "Claude"]
    if sys.platform == "win32":
        claude_dirs += relink._msix_claude_dirs(machine.local_app_data)  # the Microsoft Store build keeps its own copy
    app_dir = {label: name for name, label in relink.VSCODE_FAMILY_APPS}
    windsurf = home / ".codeium" / "windsurf"
    return [
        _Agent("Claude Code", files=[home / ".claude.json"], dirs=[home / ".claude"], programs=["claude"],
               configs=[home / ".claude.json"], kind="claude-code"),
        _Agent("Claude desktop", dirs=claude_dirs, configs=[d / "claude_desktop_config.json" for d in claude_dirs],
               kind="json"),
        _Agent("Codex", dirs=[home / ".codex"], programs=["codex"], configs=[home / ".codex" / "config.toml"],
               kind="toml"),
        _Agent("Cursor", dirs=[home / ".cursor", app / app_dir.get("Cursor", "Cursor")], programs=["cursor"],
               configs=[home / ".cursor" / "mcp.json"], kind="json"),
        _Agent("Windsurf", dirs=[windsurf, app / app_dir.get("Windsurf", "Windsurf")], programs=["windsurf"],
               configs=[windsurf / "mcp_config.json"], kind="json"),
        _Agent("Antigravity", dirs=[antigravity, app / app_dir.get("Antigravity", "Antigravity")],
               programs=["antigravity"], configs=[antigravity / "mcp_config.json"], kind="json"),
        _Agent("Gemini CLI", files=[home / ".gemini" / "settings.json"], programs=["gemini"],
               configs=[home / ".gemini" / "settings.json"], kind="json"),
        _Agent("VS Code", dirs=[app / app_dir.get("VS Code", "Code")], programs=["code"]),
        _Agent("VS Code Insiders", dirs=[app / app_dir.get("Code - Insiders", "Code - Insiders")],
               programs=["code-insiders"]),
        _Agent("VSCodium", dirs=[app / app_dir.get("VSCodium", "VSCodium")], programs=["codium"]),
        _Agent("JetBrains", dirs=[app / "JetBrains"]),
        _Agent("Obsidian", dirs=[app / "obsidian"]),
    ]


def _mentions_us(entry: Dict[str, Any]) -> bool:
    args = entry.get("args")
    words = [str(entry.get("command"))] + ([str(arg) for arg in args] if isinstance(args, list) else [])
    text = " ".join(words).lower()
    return "smart_drive" in text or "smart-drive" in text


def _find_our_entry(servers: Any) -> Optional[Dict[str, Any]]:
    """Our entry of an MCP server table: the key `smart-drive`, else any entry whose command or args name us."""
    if not isinstance(servers, dict):
        return None
    if OUR_SERVER_NAME in servers:
        entry = servers[OUR_SERVER_NAME]
        return entry if isinstance(entry, dict) else {}
    for entry in servers.values():
        if isinstance(entry, dict) and _mentions_us(entry):
            return entry
    return None


def _launcher_of(entry: Dict[str, Any]) -> str:
    command = entry.get("command")
    args = entry.get("args")
    args = list(args) if isinstance(args, list) else []
    name = ""
    if isinstance(command, str):
        name = _EXECUTABLE_SUFFIX.sub("", command.replace("\\", "/").rsplit("/", 1)[-1].lower())
    if args[:2] == ["-m", "smart_drive"]:
        return "python -m smart_drive"
    if (name == "uvx" and DIST_NAME in args) or (name == "uv" and args[:2] == ["tool", "run"]):
        return "uvx"
    if name == "pipx" and args[:1] == ["run"]:
        return "pipx run"
    if name in ("smart-drive", "smart-drive-os", "smart_drive", "smart-drive-manager"):
        return "smart-drive command"
    return "other"


def _command_found(command: Any, machine: _Machine) -> Optional[bool]:
    if not isinstance(command, str):
        return None
    try:
        if os.path.isabs(command):
            return os.path.isfile(command)
        return machine.which(command) is not None
    except (OSError, ValueError):
        return False


def _is_this_install(entry: Dict[str, Any], expected: Optional[Dict[str, Any]]) -> Optional[bool]:
    """Whether the entry starts the same command with the same arguments as `smart-drive mcp register` writes here."""
    if expected is None:
        return None
    command, args = entry.get("command"), entry.get("args")
    if not isinstance(command, str) or not isinstance(args, list):
        return False
    return (
        os.path.normcase(command) == os.path.normcase(str(expected.get("command")))
        and args == list(expected.get("args") or [])
    )


def _registered(
    entry: Optional[Dict[str, Any]], scopes: List[str], project_count: int, machine: _Machine
) -> Dict[str, Any]:
    """The MCP result for a registered entry; entry is None when only its presence is known (TOML without tomllib)."""
    if entry is None:
        launcher = found = same = None
    else:
        launcher = _launcher_of(entry)
        found = _command_found(entry.get("command"), machine)
        same = _is_this_install(entry, machine.expected)
    return {
        "status": "registered",
        "scopes": scopes,
        "project_count": project_count,
        "launcher": launcher,
        "command_found": found,
        "matches_this_install": same,
    }


def _load_json_object(path: Path) -> Tuple[str, Optional[Dict[str, Any]]]:
    """("missing" | "unreadable" | "ok", the parsed top-level object)."""
    if not relink._is_file(path):
        return "missing", None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:  # noqa: BLE001 - unreadable, not UTF-8, not JSON, too deeply nested ...
        return "unreadable", None
    return ("ok", data) if isinstance(data, dict) else ("unreadable", None)


def _unavailable(state: str) -> Dict[str, Any]:
    return {"status": "no config file" if state == "missing" else "unreadable config"}


def _check_json_config(path: Path, scope: str, machine: _Machine) -> Dict[str, Any]:
    state, data = _load_json_object(path)
    if data is None:
        return _unavailable(state)
    entry = _find_our_entry(data.get("mcpServers"))
    if entry is None:
        return {"status": "not registered"}
    return _registered(entry, [scope], 0, machine)


def _check_claude_code(path: Path, machine: _Machine) -> Dict[str, Any]:
    """`mcpServers` at the top of ~/.claude.json is the user scope; each project may hold its own table as well."""
    state, data = _load_json_object(path)
    if data is None:
        return _unavailable(state)
    user_entry = _find_our_entry(data.get("mcpServers"))
    project_entries = []
    projects = data.get("projects")
    if isinstance(projects, dict):
        for project in projects.values():
            entry = _find_our_entry(project.get("mcpServers")) if isinstance(project, dict) else None
            if entry is not None:
                project_entries.append(entry)
    entry = user_entry if user_entry is not None else (project_entries[0] if project_entries else None)
    if entry is None:
        return {"status": "not registered"}
    return _registered(entry, ["user"] if user_entry is not None else [], len(project_entries), machine)


def _check_toml_config(path: Path, scope: str, machine: _Machine) -> Dict[str, Any]:
    if not relink._is_file(path):
        return {"status": "no config file"}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
        data = tomllib.loads(text) if tomllib is not None else None
    except Exception:  # noqa: BLE001 - unreadable, not UTF-8, or TOML that tomllib rejects
        return {"status": "unreadable config"}
    if data is None:  # no TOML parser: the table header alone says whether the entry exists
        if _TOML_OUR_TABLE.search(text):
            return _registered(None, [scope], 0, machine)
        return {"status": "not registered"}
    entry = _find_our_entry(data.get("mcp_servers"))
    if entry is None:
        return {"status": "not registered"}
    return _registered(entry, [scope], 0, machine)


_STATUS_RANK = ("registered", "unreadable config", "not registered", "no config file")


def _check_mcp(agent: _Agent, machine: _Machine) -> Dict[str, Any]:
    if agent.kind == "claude-code":
        return _check_claude_code(agent.configs[0], machine)
    check = _check_toml_config if agent.kind == "toml" else _check_json_config
    results = [check(path, "user", machine) for path in agent.configs]
    return min(results, key=lambda result: _STATUS_RANK.index(result["status"]))


def _collect_agent(agent: _Agent, machine: _Machine) -> Dict[str, Any]:
    detected = (
        any(relink._is_file(path) for path in agent.files)
        or any(relink._is_dir(path) for path in agent.dirs)
        or any(machine.on_path(program) for program in agent.programs)
    )
    mcp: Optional[Dict[str, Any]] = None
    if agent.kind and (detected or any(relink._is_file(path) for path in agent.configs)):
        mcp = _check_mcp(agent, machine)
    return {"name": agent.name, "detected": detected, "mcp": mcp}


def _collect_agents(machine: _Machine, redactor: Redactor) -> List[Dict[str, Any]]:
    entries = []
    for agent in _agents_of(machine):
        try:
            entries.append(_collect_agent(agent, machine))
        except Exception as err:  # noqa: BLE001 - one agent that cannot be inspected must not hide the others
            entries.append({
                "name": agent.name,
                "detected": False,
                "mcp": {"error": f"{type(err).__name__}: {redactor.text(str(err))}"},
            })
    return entries


def _expected_entry() -> Optional[Dict[str, Any]]:
    try:
        return build_mcp_entry()
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------------------------------------------------
# The report
# ----------------------------------------------------------------------------------------------------------------------

def _problems_of(report: Dict[str, Any]) -> List[str]:
    """Short sentences, without paths, about what looks wrong."""
    problems: List[str] = []
    root = report.get("root")
    if isinstance(root, dict) and root.get("found") is False:
        problems.append("No drive root found: pass --root <path> or set SMART_DRIVE_ROOT.")
    python = report.get("python")
    if isinstance(python, dict) and python.get("fts5") is False:
        problems.append("This Python's SQLite has no FTS5, so `smart-drive index` and `search` cannot work.")
    smartdrive = report.get("smartdrive")
    if isinstance(smartdrive, dict) and smartdrive.get("installed_version") not in (None, smartdrive.get("version")):
        problems.append(
            f"The installed smart-drive-os package says version {smartdrive.get('installed_version')}, "
            f"but the code that is running is version {smartdrive.get('version')}."
        )
    agents = report.get("agents")
    for agent in agents if isinstance(agents, list) else []:
        name, mcp = agent.get("name"), agent.get("mcp")
        if not isinstance(mcp, dict):
            continue
        if _failure(mcp) is not None:
            problems.append(f"{name}: could not be checked ({_failure(mcp)}).")
        if mcp.get("status") == "unreadable config":
            fmt = "TOML" if name in TOML_AGENTS else "JSON"
            problems.append(f"{name}: its MCP config file cannot be read as {fmt}.")
        if mcp.get("command_found") is False:
            problems.append(
                f"{name}: the smart-drive entry starts a command that is not on this computer; "
                "run `smart-drive mcp register` again."
            )
    workspace = report.get("workspace_mcp")
    if isinstance(workspace, dict):
        if workspace.get("status") == "unreadable config":
            problems.append("The drive root's .mcp.json cannot be read as JSON.")
        if workspace.get("command_found") is False:
            problems.append(
                "The smart-drive entry of the drive root's .mcp.json starts a command that is not on this computer; "
                "run `smart-drive mcp register` again."
            )
    journal = report.get("move_journal")
    if isinstance(journal, dict) and journal.get("status") == "unreadable":
        problems.append("The move journal holds no batch that can be read.")
    for key, section in report.items():
        if _failure(section) is not None:
            problems.append(f"The {key} section could not be collected ({_failure(section)}).")
    return problems


def collect_report(
    root: Optional[str] = None,
    *,
    home: "os.PathLike[str] | str | None" = None,
    app_data: "os.PathLike[str] | str | None" = None,
    local_app_data: "os.PathLike[str] | str | None" = None,
    redactor: Optional[Redactor] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """The environment report as plain data, with every path and message redacted.

    ``home`` and ``app_data`` work as in ``relink.agent_files`` (a given home ignores overrides such as
    ANTIGRAVITY_HOME). Every section is built on its own: one that fails becomes {"error": ...} and the others stay.
    Sections about the drive are None when no drive root was found.
    """
    redactor = redactor if redactor is not None else Redactor.from_environment()
    which = which if which is not None else shutil.which
    environ = environ if environ is not None else os.environ

    real_root: Optional[str] = None
    root_section: Any
    try:
        root_section, real_root = _collect_root(root, redactor)
    except Exception as err:  # noqa: BLE001
        root_section = {"error": f"{type(err).__name__}: {redactor.text(str(err))}"}

    def drive_section(build: Callable[[str], Any]) -> Any:
        if real_root is None:
            return None
        drive = real_root
        return _guarded(lambda: build(drive), redactor)

    def machine() -> _Machine:
        resolved_home, resolved_app_data, use_env = relink._resolve_locations(
            Path(home) if home is not None else None, Path(app_data) if app_data is not None else None
        )
        local = Path(local_app_data) if local_app_data is not None else relink._resolve_local_app_data(
            resolved_home, use_env
        )
        return _Machine(resolved_home, resolved_app_data, local, use_env, environ, which, _expected_entry())

    report: Dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "redaction": {"home": REDACTED_HOME, "user": REDACTED_USER},
        "smartdrive": _guarded(lambda: {"version": smart_drive.__version__, **install_method()}, redactor),
        "python": _guarded(lambda: _collect_python(redactor), redactor),
        "os": _guarded(_collect_os, redactor),
        "root": root_section,
        "filesystem": drive_section(lambda path: _collect_filesystem(path, redactor)),
        "search_index": drive_section(_collect_search_index),
        "checkup": drive_section(_collect_checkup),
        "move_journal": drive_section(_collect_move_journal),
        "agents": _guarded(lambda: _collect_agents(machine(), redactor), redactor),
        "workspace_mcp": drive_section(
            lambda drive: _check_json_config(Path(drive) / ".mcp.json", "workspace", machine())
        ),
        "env_vars_set": _guarded(
            lambda: sorted(
                name for name in environ if name.startswith(ENV_VAR_PREFIX) or name in ENV_VAR_NAMES
            ),
            redactor,
        ),
    }
    report["problems"] = _guarded(lambda: _problems_of(report), redactor)
    return redactor.scrub(report)


# ----------------------------------------------------------------------------------------------------------------------
# Renderers
# ----------------------------------------------------------------------------------------------------------------------

TEXT_WIDTH = 110  # the width the text report is wrapped at

_FOUND_BY = {
    "explicit": "from --root",
    "env": "from SMART_DRIVE_ROOT",
    "anchor": "found above the current folder",
    "probe": "found on an attached drive",
}


def _size_text(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _code_markdown(value: str) -> str:
    """A code span; the fence is lengthened when the value holds backticks."""
    fence = "`"
    while fence in value:
        fence += "`"
    return f"{fence} {value} {fence}" if len(fence) > 1 else f"`{value}`"


def _plain(value: str) -> str:
    return value


def _wrap_parts(parts: Sequence[str], width: int) -> List[str]:
    """The parts joined with '; ' into lines of at most `width` characters; a part is never cut."""
    lines: List[str] = []
    current = ""
    for part in parts:
        if current and len(current) + 2 + len(part) > width:
            lines.append(current + ";")
            current = part
        else:
            current = f"{current}; {part}" if current else part
    if current:
        lines.append(current)
    return lines


def _cell(value: Any) -> str:
    """One table cell or list item. A pipe or a line break cannot break the table; a `<` outside a code span is escaped,
    or GitHub would take `<user>` for an HTML tag and drop it."""
    line = str(value).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    pieces: List[str] = []
    end = 0
    for span in _CODE_SPAN.finditer(line):
        pieces += [line[end:span.start()].replace("<", "\\<"), span.group(0)]
        end = span.end()
    pieces.append(line[end:].replace("<", "\\<"))
    return "".join(pieces).replace("|", "\\|")


def _mcp_text(mcp: Any) -> str:
    """One agent's MCP result in words: 'registered (user scope, 2 projects; python -m smart_drive; ...)'."""
    if not isinstance(mcp, dict):
        return "—"
    failure = _failure(mcp)
    if failure is not None:
        return f"error: {failure}"
    status = str(mcp.get("status", "?"))
    if status != "registered":
        return status
    where = [f"{scope} scope" for scope in mcp.get("scopes") or []]
    count = mcp.get("project_count") or 0
    if count:
        where.append(f"{count} project{'' if count == 1 else 's'}")
    parts = [", ".join(where)] if where else []
    if mcp.get("launcher"):
        parts.append(str(mcp["launcher"]))
    if mcp.get("command_found") is not None:
        parts.append("command found" if mcp["command_found"] else "command not found")
    if mcp.get("matches_this_install") is not None:
        parts.append("this install" if mcp["matches_this_install"] else "another install")
    return f"registered ({'; '.join(parts)})" if parts else "registered"


def _environment_rows(report: Dict[str, Any], code: Callable[[str], str]) -> List[Tuple[str, List[str]]]:
    """The facts of the first block as (label, parts); `code` marks literal values (backticks in Markdown)."""
    rows: List[Tuple[str, List[str]]] = []

    def add(label: str, section: Any, describe: Callable[[Dict[str, Any]], List[str]]) -> None:
        failure = _failure(section)
        if failure is not None:
            rows.append((label, [f"error: {failure}"]))
        else:
            rows.append((label, describe(section if isinstance(section, dict) else {})))

    def smartdrive(data: Dict[str, Any]) -> List[str]:
        parts = [str(data.get("version", "?"))]
        if data.get("install_method"):
            detail = f" ({data['install_detail']})" if data.get("install_detail") else ""
            parts.append(f"{data['install_method']}{detail}")
        if data.get("git_commit"):
            parts.append(f"git commit {data['git_commit']}")
        return parts

    def python(data: Dict[str, Any]) -> List[str]:
        encoding = data.get("encoding") if isinstance(data.get("encoding"), dict) else {}
        parts = [f"{data.get('version', '?')} ({data.get('implementation', '?')}, {data.get('bits', '?')}-bit)"]
        if data.get("executable"):
            parts.append(code(str(data["executable"])))
        parts.append(f"virtual environment: {'yes' if data.get('virtual_env') else 'no'}")
        parts.append(f"SQLite {data.get('sqlite_version', '?')}, FTS5: {'yes' if data.get('fts5') else 'no'}")
        parts.append(
            f"encoding: {encoding.get('filesystem', '?')} (files), {encoding.get('preferred', '?')} (locale)"
            + (", UTF-8 mode" if encoding.get("utf8_mode") else "")
        )
        return parts

    def operating_system(data: Dict[str, Any]) -> List[str]:
        parts = [str(data.get("name", "?")), f"version {data.get('version', '?')}", str(data.get("machine", "?"))]
        if data.get("wsl"):
            parts.append("WSL")
        return parts

    def drive_root(data: Dict[str, Any]) -> List[str]:
        if not data.get("found"):
            return [f"not found: {data.get('error') or 'no drive root'}"]
        parts = [code(str(data.get("path", "?"))), _FOUND_BY.get(str(data.get("found_by")), str(data.get("found_by")))]
        if data.get("is_mount_point"):
            parts.append("volume root")
        return parts

    def filesystem(data: Dict[str, Any]) -> List[str]:
        parts = [str(data.get("name", "unknown"))]
        unit = data.get("allocation_unit_bytes")
        if isinstance(unit, int):
            parts.append(f"allocation unit {cluster_size_text(unit)} ({data.get('cluster_size_source', '?')})")
        if data.get("detected_via"):
            parts.append(f"detected via {data['detected_via']}")
        total, free = data.get("total_bytes"), data.get("free_bytes")
        if isinstance(total, int) and isinstance(free, int) and total > 0:
            parts.append(f"{_size_text(total)}, {round(free * 100 / total)}% free")
        return parts

    def search_index(data: Dict[str, Any]) -> List[str]:
        return [f"built, {_size_text(int(data.get('size_bytes') or 0))}" if data.get("present") else "not built"]

    def checkup(data: Dict[str, Any]) -> List[str]:
        status = data.get("status")
        if status == "not run":
            return ["not run yet"]
        if status != "ok":
            return [str(status or "?")]
        if not isinstance(data.get("score"), int):
            return ["run, no score recorded"]
        when = f" at {data['timestamp']}" if data.get("timestamp") else ""
        return [f"{data['score']}/100{when}"]

    rows_for_drive = (
        ("Filesystem", "filesystem", filesystem),
        ("Search index", "search_index", search_index),
        ("Last checkup", "checkup", checkup),
    )
    add("SmartDrive-OS", report.get("smartdrive"), smartdrive)
    add("Python", report.get("python"), python)
    add("OS", report.get("os"), operating_system)
    add("Drive root", report.get("root"), drive_root)
    for label, key, describe in rows_for_drive:
        if report.get(key) is None:
            rows.append((label, ["no drive root"]))
        else:
            add(label, report.get(key), describe)
    names = report.get("env_vars_set")
    if _failure(names) is not None:
        rows.append(("Environment variables set", [f"error: {_failure(names)}"]))
    else:
        listed = ", ".join(code(str(name)) for name in names) if isinstance(names, list) and names else "none"
        rows.append(("Environment variables set", [listed]))
    return rows


def _agent_rows(report: Dict[str, Any]) -> List[Tuple[str, Optional[bool], Optional[str]]]:
    """(name, detected, MCP result in words or None when there is no MCP check) for each agent, IDEs included."""
    rows: List[Tuple[str, Optional[bool], Optional[str]]] = []
    agents = report.get("agents")
    if _failure(agents) is not None:
        return [("Coding agents and IDEs", None, f"error: {_failure(agents)}")]
    for agent in agents if isinstance(agents, list) else []:
        mcp = agent.get("mcp")
        rows.append((str(agent.get("name", "?")), agent.get("detected"), None if mcp is None else _mcp_text(mcp)))
    workspace = report.get("workspace_mcp")
    if workspace is not None:
        rows.append(("Drive root `.mcp.json`", None, _mcp_text(workspace)))
    return rows


def _journal_rows(journal: Any) -> List[Tuple[str, str, str, str]]:
    """(time, tool, moves, undone) of the recent batches; Undone is yes/no for organize and classify, else a note."""
    rows = []
    for batch in journal.get("recent") or []:
        if batch.get("tool") in relink.UNDOABLE_TOOLS:
            undone = "yes" if batch.get("undone") else "no"
        elif batch.get("undoes"):
            undone = f"undoes {batch['undoes']}"
        else:
            undone = "—"
        rows.append((str(batch.get("time", "?")), str(batch.get("tool", "?")), str(batch.get("moves", "?")), undone))
    return rows


def _batches_text(count: int, shown: int) -> str:
    """'7 batches recorded; the newest 5' or, when all are shown, '3 batches recorded'."""
    text = f"{count} batch{'' if count == 1 else 'es'} recorded"
    return f"{text}; the newest {shown}" if count > shown else text


def _problem_list(report: Dict[str, Any]) -> List[str]:
    problems = report.get("problems")
    if _failure(problems) is not None:
        return [f"The list of problems could not be built ({_failure(problems)})."]
    return [str(problem) for problem in problems] if isinstance(problems, list) else []


def render_markdown(report: Dict[str, Any]) -> str:
    """The report as a Markdown block to paste into a GitHub issue."""
    lines = [REPORT_COMMENT, "", "### Environment", "", "| Item | Value |", "| --- | --- |"]
    for label, parts in _environment_rows(report, _code_markdown):
        lines.append(f"| {_cell(label)} | {_cell('; '.join(parts))} |")

    lines += ["", "### Coding agents and IDEs", "", "| Agent / IDE | Detected | MCP entry |", "| --- | --- | --- |"]
    for name, detected, mcp in _agent_rows(report):
        found = "—" if detected is None else ("yes" if detected else "no")
        lines.append(f"| {_cell(name)} | {found} | {_cell(mcp if mcp is not None else '—')} |")

    lines += ["", "### Recent move batches", ""]
    journal = report.get("move_journal")
    if journal is None:
        lines.append("No drive root, so no move journal.")
    elif _failure(journal) is not None:
        lines.append(f"The move journal could not be read: {_failure(journal)}")
    elif journal.get("status") == "unreadable":
        lines.append("The move journal holds no batch that can be read.")
    elif not journal.get("batch_count"):
        lines.append("No move batches recorded.")
    else:
        rows = _journal_rows(journal)
        lines += [
            f"{_batches_text(journal['batch_count'], len(rows))}:",
            "",
            "| Time | Tool | Moves | Undone |",
            "| --- | --- | --- | --- |",
        ]
        lines += [f"| {' | '.join(_cell(part) for part in row)} |" for row in rows]

    lines += ["", "### Problems noticed", ""]
    problems = _problem_list(report)
    lines += [f"- {_cell(problem)}" for problem in problems] if problems else ["None."]
    return "\n".join(lines)


def render_text(report: Dict[str, Any]) -> str:
    """The report as plain lines for the person at the terminal, ending with how to file a bug report."""
    lines = [
        "SmartDrive-OS doctor: read-only and offline, nothing is written or sent anywhere.",
        f"Paths show {REDACTED_HOME} for your home folder and {REDACTED_USER} for your user name.",
        "",
    ]
    rows = _environment_rows(report, _plain)
    width = max(len(label) for label, _ in rows)
    for label, parts in rows:
        for index, line in enumerate(_wrap_parts(parts, max(40, TEXT_WIDTH - width - 2))):
            lines.append(f"{(label if index == 0 else '').ljust(width)}  {line}")

    lines += ["", "Coding agents and IDEs"]
    agent_rows = _agent_rows(report)
    name_width = max([len(name.replace("`", "")) for name, _, _ in agent_rows] + [1])
    for name, detected, mcp in agent_rows:
        if detected is None:  # not an agent: the drive root's .mcp.json, or a section that could not be collected
            mark, tail = " ", f"MCP: {mcp}"
        elif detected:
            mark, tail = "✓", (f"MCP: {mcp}" if mcp is not None else "detected")
        else:
            mark, tail = "·", "not found" + (f"; MCP: {mcp}" if mcp is not None else "")
        lines.append(f"  {mark} {name.replace('`', '').ljust(name_width)}  {tail}")

    journal = report.get("move_journal")
    lines.append("")
    if journal is None:
        lines.append("Move journal: no drive root, so none.")
    elif _failure(journal) is not None:
        lines.append(f"Move journal: could not be read ({_failure(journal)}).")
    elif journal.get("status") == "unreadable":
        lines.append("Move journal: holds no batch that can be read.")
    elif not journal.get("batch_count"):
        lines.append("Move journal: no batches recorded.")
    else:
        rows = _journal_rows(journal)
        lines.append(f"Move journal: {_batches_text(journal['batch_count'], len(rows))}, newest first")
        tool_width = max(len(row[1]) for row in rows)
        for time, tool, moves, undone in rows:
            note = "" if undone in ("no", "—") else ("  undone" if undone == "yes" else f"  {undone}")
            lines.append(f"  {time}  {tool.ljust(tool_width)}  {moves} move{'' if moves == '1' else 's'}{note}")

    problems = _problem_list(report)
    lines += ["", "Problems noticed" if problems else "No problems noticed."]
    lines += [f"  ! {problem}" for problem in problems]
    lines += ["", BUG_REPORT_HINT]
    return "\n".join(lines)


__all__ = [
    "BUG_REPORT_HINT",
    "ISSUES_URL",
    "REDACTED_HOME",
    "REDACTED_USER",
    "REPORT_COMMENT",
    "REPORT_VERSION",
    "Redactor",
    "collect_report",
    "install_method",
    "os_release_name",
    "render_markdown",
    "render_text",
    "windows_name",
]
