"""tests/test_doctor.py - `smart-drive doctor`: a read-only, offline environment report for bug reports.

Beta testers paste `smart-drive doctor --report` into public GitHub issues, so what the report may show is pinned here,
rule by rule:

- every path it prints shows the home folder as `~` and the user name as `<user>` (the `Redactor`, tested on Windows,
  macOS and Linux style paths whatever system runs the tests);
- it never prints a file or folder name from the drive, a token, or anything from an agent's config file but derived
  facts (registered yes/no, launcher kind, command found yes/no);
- it makes no network call, starts no process, writes nothing (`.smart_drive/` is never created) and lists no folder of
  the drive;
- a drive root that cannot be found is part of the report (exit code 0), and a section that breaks becomes
  `{"error": ...}` without costing the others.

The fake computer is a temporary folder: a home named after a distinctive user, agent configs with a fake secret in
them, and a drive root that holds a distinctive folder name. Nothing on the real machine is read or written.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import io
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from unittest.mock import patch

import smart_drive
from smart_drive.cli.main import build_parser, main
from smart_drive.core import doctor
from smart_drive.core.doctor import (
    BUG_REPORT_HINT,
    ISSUES_URL,
    REDACTED_HOME,
    REDACTED_USER,
    REPORT_COMMENT,
    Redactor,
    collect_report,
    install_method,
    os_release_name,
    render_markdown,
    render_text,
    windows_name,
)
from tests.helpers import run_smart_drive_cli

CMD_DOCTOR = importlib.import_module("smart_drive.cli.cmd_doctor")
ROOT = Path(__file__).resolve().parent.parent
MODULES = ("smart_drive/core/doctor.py", "smart_drive/cli/cmd_doctor.py")

PYTHON = sys.executable  # a file that exists: an entry that starts it counts as "command found"
USER = "sdtestuser"
SECRET_NAME = "SECRET_PROJECT_X"
SECRET_TOKEN = "sk-SECRET-TOKEN-123"
AUTH_TOKEN = "tok-SECRET-456"
OTHER_SERVER = "other-secret-server"

SECTIONS = [
    "report_version", "redaction", "smartdrive", "python", "os", "root", "filesystem", "search_index", "checkup",
    "move_journal", "agents", "workspace_mcp", "env_vars_set", "problems",
]
AGENT_NAMES = [
    "Claude Code", "Claude desktop", "Codex", "Cursor", "Windsurf", "Antigravity", "Gemini CLI", "VS Code",
    "VS Code Insiders", "VSCodium", "JetBrains", "Obsidian",
]
ENVIRONMENT_LABELS = [
    "SmartDrive-OS", "Python", "OS", "Drive root", "Filesystem", "Search index", "Last checkup",
    "Environment variables set",
]


def no_programs(name: str) -> Optional[str]:
    """A `which` for a computer with nothing on PATH."""
    return None


def launch_entry(command: str = PYTHON) -> Dict[str, Any]:
    """The entry `smart-drive mcp register` writes for a pip or source install."""
    return {"command": command, "args": ["-m", "smart_drive", "mcp"], "env": {"PYTHONIOENCODING": "utf-8"}}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def tree_state(base: Path) -> Dict[str, Tuple[int, int]]:
    """Every path below `base` with its size (-1 for a folder) and modification time."""
    state: Dict[str, Tuple[int, int]] = {".": (-1, os.stat(base).st_mtime_ns)}
    for current, folders, files in os.walk(base):
        for name in folders + files:
            full = os.path.join(current, name)
            stat = os.stat(full)
            state[os.path.relpath(full, base)] = (stat.st_size if name in files else -1, stat.st_mtime_ns)
    return state


@contextlib.contextmanager
def watching_listings(folder: Path, violations: List[str]) -> Iterator[None]:
    """Records every attempt to list a folder below `folder` (a scan of the drive) and lets the call go through."""
    prefix = os.path.normcase(os.path.realpath(str(folder)))

    def inside(target: Any) -> bool:
        try:
            resolved = os.path.normcase(os.path.realpath(os.fspath(target)))
        except (TypeError, ValueError, OSError):
            return False
        return resolved == prefix or resolved.startswith(prefix + os.sep)

    def watched(label: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if args and inside(args[0]):
                violations.append(f"{label}({args[0]})")
            return original(*args, **kwargs)
        return wrapper

    with contextlib.ExitStack() as stack:
        for owner, name in ((os, "listdir"), (os, "scandir"), (os, "walk"), (Path, "iterdir"), (Path, "glob"),
                            (Path, "rglob")):
            stack.enter_context(patch.object(owner, name, watched(name, getattr(owner, name))))
        yield


def run_main(argv: List[str]) -> Tuple[int, str, str]:
    """main(argv) with its stdout and stderr captured: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class FakeMachine:
    """A temporary computer: a home named after a distinctive user, AppData, agent configs and a drive root."""

    def __init__(self, case: unittest.TestCase) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_doctor_")).resolve()
        case.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.home = self.base / "Users" / USER
        self.app_data = self.home / "AppData" / "Roaming"
        self.local_app_data = self.home / "AppData" / "Local"
        self.root = self.base / "media" / USER / "T7"  # the user name is a component of the drive's path
        self.redactor = Redactor([str(self.home)], [USER], windows=os.name == "nt")
        self.environ = {"SMART_DRIVE_MCP_AUTH_TOKEN": AUTH_TOKEN, "CLAUDECODE": "1", "UNRELATED_VARIABLE": "sk-unrelated"}
        self.fake_python = str(self.home / ".pyenv" / "bin" / "python")
        for folder in (self.home, self.app_data, self.local_app_data):
            folder.mkdir(parents=True, exist_ok=True)
        self.populate_agents()
        self.populate_drive()

    def populate_agents(self) -> None:
        entry = launch_entry()
        other = {"command": "npx", "args": ["-y", "some-tool"], "env": {"API_KEY": SECRET_TOKEN}}
        write_json(self.home / ".claude.json", {
            "mcpServers": {"smart-drive": entry, OTHER_SERVER: other},
            "projects": {
                str(self.base / SECRET_NAME / "a"): {"mcpServers": {"smart-drive": entry}},
                str(self.base / SECRET_NAME / "b"): {"mcpServers": {"smart-drive": entry}},
                str(self.base / SECRET_NAME / "c"): {"mcpServers": {OTHER_SERVER: other}},
            },
        })
        write_json(self.app_data / "Claude" / "claude_desktop_config.json", {
            "mcpServers": {"smart-drive": {"command": "uvx", "args": ["smart-drive-os", "mcp"]}},
        })
        (self.home / ".codex").mkdir()
        (self.home / ".codex" / "config.toml").write_text(
            "[mcp_servers.smart-drive]\n"
            f"command = '{PYTHON}'\n"
            'args = ["-m", "smart_drive", "mcp"]\n',
            encoding="utf-8",
        )
        write_json(self.home / ".cursor" / "mcp.json", {
            "mcpServers": {"smart-drive": launch_entry(str(self.base / "gone" / "python"))},
        })
        windsurf = self.home / ".codeium" / "windsurf"
        windsurf.mkdir(parents=True)
        (windsurf / "mcp_config.json").write_text("{ this is not json", encoding="utf-8")
        (self.home / ".gemini" / "antigravity").mkdir(parents=True)  # installed, no config file
        (self.app_data / "Code").mkdir()  # VS Code: an IDE, no MCP check

    def populate_drive(self) -> None:
        secret = self.root / SECRET_NAME
        secret.mkdir(parents=True)
        (self.root / f"{SECRET_NAME}.txt").write_text("private", encoding="utf-8")
        state = self.root / ".smart_drive"
        state.mkdir()
        (state / "index.db").write_bytes(b"x" * 123)
        write_json(state / "checkup.json", {
            "score": 85,
            "timestamp": "2026-10-21T10:00:00Z",
            "root": str(self.root),
            "unorganized": {"items": [SECRET_NAME, f"{SECRET_NAME}.txt"]},
        })
        moves = [[str(secret / "a"), str(secret / "b")], [str(secret / "c"), str(secret / "d")]]
        lines: List[Any] = [
            {"time": "2026-10-21T10:00:00", "tool": "organize", "moves": moves + moves[:1]},
            {"time": "2026-10-21T10:05:00", "tool": "undo", "undoes": "2026-10-21T10:00:00", "moves": moves},
            {"time": "2026-10-21T11:00:00", "tool": "classify", "moves": moves},
            "{ this line is not json",
            {"time": "2026-10-21T12:00:00", "tool": "relink", "moves": moves[:1]},
            {"time": "2026-10-21T12:30:00", "tool": "organize", "moves": moves + moves},
            {"time": "2026-10-21T13:00:00", "tool": "paths", "moves": moves[:1]},
            {"time": "2026-10-21T13:05:00", "tool": "undo", "undoes": "2026-10-21T12:30:00", "moves": moves + moves},
        ]
        (state / "moves.jsonl").write_text(
            "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n", encoding="utf-8"
        )
        write_json(self.root / ".mcp.json", {"mcpServers": {"smart-drive": launch_entry()}})

    def report(self, root: Optional[str] = None, **overrides: Any) -> Dict[str, Any]:
        """The report of this computer (of `root` instead of its own drive root), with what depends on the machine
        running the tests held still."""
        install = {
            "install_method": "pipx", "install_detail": "pipx install", "installer": "pip",
            "installed_version": smart_drive.__version__, "git_commit": None,
        }
        arguments: Dict[str, Any] = {
            "home": self.home, "app_data": self.app_data, "local_app_data": self.local_app_data,
            "redactor": self.redactor, "which": no_programs, "environ": self.environ,
        }
        arguments.update(overrides)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(doctor, "build_mcp_entry", return_value=launch_entry()))
            stack.enter_context(patch.object(doctor, "install_method", return_value=install))
            stack.enter_context(patch.object(doctor, "_has_fts5", return_value=True))
            stack.enter_context(patch.object(sys, "executable", self.fake_python))
            return collect_report(str(self.root) if root is None else root, **arguments)

    @staticmethod
    def outputs(report: Dict[str, Any]) -> Dict[str, str]:
        return {
            "json": json.dumps(report, indent=2, ensure_ascii=False),
            "markdown": render_markdown(report),
            "text": render_text(report),
        }


# ----------------------------------------------------------------------------------------------------------------------
# Redaction
# ----------------------------------------------------------------------------------------------------------------------

class TestRedactorWindowsPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.redactor = Redactor([r"C:\Users\Alice"], ["Alice"], windows=True)

    def test_the_home_folder_becomes_a_tilde(self) -> None:
        path = self.redactor.path
        self.assertEqual(
            path(r"C:\Users\Alice\AppData\Local\Programs\Python\Python313\python.exe"),
            r"~\AppData\Local\Programs\Python\Python313\python.exe",
        )
        self.assertEqual(path(r"C:\Users\Alice"), "~")
        self.assertEqual(path(r"c:\users\alice\x"), r"~\x", "Windows paths are case-insensitive")
        self.assertEqual(path("C:/Users/Alice/x"), "~/x", "either slash may separate the components")
        self.assertEqual(path(r"C:\\Users\\Alice\\x"), r"~\\x", "a repr()ed path in an error message doubles them")

    def test_a_home_given_with_a_trailing_backslash_is_the_same_home(self) -> None:
        redactor = Redactor(["C:\\Users\\Alice\\"], ["Alice"], windows=True)
        self.assertEqual(redactor.path(r"C:\Users\Alice\AppData"), r"~\AppData")
        self.assertEqual(redactor.path(r"C:\Users\Alice"), "~")

    def test_another_drive_keeps_its_letter_and_loses_the_user_name(self) -> None:
        self.assertEqual(self.redactor.path(r"D:\Users\alice\proj"), r"D:\Users\<user>\proj")
        self.assertEqual(self.redactor.path("E:\\"), "E:\\")
        self.assertEqual(self.redactor.path(r"\\server\share\alice\x"), r"\\server\share\<user>\x")

    def test_the_8_3_short_form_of_a_long_user_name_is_found(self) -> None:
        redactor = Redactor([r"C:\Users\runneradmin"], ["runneradmin"], windows=True)
        self.assertEqual(redactor.path(r"C:\Users\RUNNER~1\AppData\Local\Temp"), r"C:\Users\<user>\AppData\Local\Temp")
        self.assertEqual(redactor.path(r"C:\Users\runneradmin\AppData"), r"~\AppData")
        spaced = Redactor([], ["John Smith"], windows=True)
        self.assertEqual(spaced.path(r"D:\Users\JOHNSM~2\x"), r"D:\Users\<user>\x", "a name with a space is shortened too")

    def test_a_longer_name_is_not_the_home(self) -> None:
        result = self.redactor.path(r"C:\Users\Alicex\x")
        self.assertEqual(result, r"C:\Users\<user>x\x", "only the name inside is hidden")
        self.assertNotIn("~", result)

    def test_a_root_is_never_taken_for_a_home(self) -> None:
        for home in ("C:\\", "C:", "\\", "/", ""):
            with self.subTest(home=home):
                redactor = Redactor([home], ["alice"], windows=True)
                self.assertEqual(redactor.path(r"C:\Windows\System32"), r"C:\Windows\System32")
                self.assertEqual(redactor.path(r"C:\Users\alice\x"), r"C:\Users\<user>\x")


class TestRedactorPosixPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.redactor = Redactor(["/home/alice"], ["alice"], windows=False)

    def test_the_home_folder_becomes_a_tilde(self) -> None:
        path = self.redactor.path
        self.assertEqual(path("/home/alice/.local/bin/python3"), "~/.local/bin/python3")
        self.assertEqual(path("/home/alice"), "~")
        self.assertEqual(path("/home/bob/x"), "/home/bob/x")

    def test_a_folder_that_only_ends_in_the_home_is_not_the_home(self) -> None:
        self.assertEqual(self.redactor.path("/mnt/home/alice/x"), "/mnt/home/<user>/x")
        self.assertEqual(self.redactor.path("/home/alicex/y"), "/home/<user>x/y")

    def test_the_user_name_is_hidden_wherever_it_is_a_whole_component(self) -> None:
        path = self.redactor.path
        self.assertEqual(path("/media/alice/T7"), "/media/<user>/T7")
        self.assertEqual(path("/run/media/alice/T7"), "/run/media/<user>/T7")
        self.assertEqual(path("/Volumes/alice-ssd"), "/Volumes/<user>-ssd", "inside a path-like value, a long name goes")

    def test_linux_is_case_sensitive_for_the_home_but_the_name_is_hidden_all_the_same(self) -> None:
        self.assertEqual(self.redactor.path("/HOME/ALICE/x"), "/HOME/<user>/x")

    def test_on_macos_the_home_is_found_in_any_case(self) -> None:
        redactor = Redactor(["/Users/Alice"], ["Alice"], windows=False, case_insensitive=True)
        self.assertEqual(redactor.path("/users/alice/Library"), "~/Library")
        self.assertEqual(redactor.path("/Users/Alice"), "~")

    def test_a_short_user_name_is_only_hidden_as_a_whole_component(self) -> None:
        redactor = Redactor(["/home/pi"], ["pi"], windows=False)
        self.assertEqual(redactor.path("/home/pi/x"), "~/x")
        self.assertEqual(redactor.path("/media/pi/SSD"), "/media/<user>/SSD")
        self.assertEqual(redactor.path("/opt/pip/x"), "/opt/pip/x")
        self.assertEqual(redactor.path("/usr/lib/python3/pickle.py"), "/usr/lib/python3/pickle.py")

    def test_a_root_is_never_taken_for_a_home(self) -> None:
        redactor = Redactor(["/", "//"], ["alice"], windows=False)
        self.assertEqual(redactor.path("/usr/bin/python3"), "/usr/bin/python3")
        self.assertEqual(redactor.path("/home/alice/x"), "/home/<user>/x")

    def test_the_longest_home_goes_first_and_duplicates_are_dropped(self) -> None:
        homes = ["/home/alice", "/data/home/alice", "/home/alice/", "/home/alice"]
        redactor = Redactor(homes, ["alice"], windows=False)
        self.assertEqual(redactor.path("/data/home/alice/x"), "~/x")
        self.assertEqual(redactor.path("/home/alice/y"), "~/y")

    def test_names_and_homes_with_special_characters_are_matched_literally(self) -> None:
        redactor = Redactor(["/Users/John Smith (work)"], ["John Smith (work)", "c++", "a.b"], windows=False)
        self.assertEqual(redactor.path("/Users/John Smith (work)/Documents"), "~/Documents")
        self.assertEqual(redactor.path("/media/c++/T7"), "/media/<user>/T7")
        self.assertEqual(redactor.path("/media/a.b/T7"), "/media/<user>/T7")
        self.assertEqual(redactor.path("/media/aXb/T7"), "/media/aXb/T7", "a dot is not a wildcard")

    def test_decomposed_and_composed_unicode_names_are_both_found(self) -> None:
        composed = "/home/D\u01b0\u01a1ng"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(composed, decomposed)
        redactor = Redactor([composed], ["D\u01b0\u01a1ng"], windows=False)
        self.assertEqual(redactor.path(composed + "/x"), "~/x")
        self.assertEqual(redactor.path(decomposed + "/x"), "~/x")
        self.assertEqual(redactor.path("/media/" + unicodedata.normalize("NFD", "D\u01b0\u01a1ng") + "/T7"), "/media/<user>/T7")


class TestRedactorText(unittest.TestCase):
    def test_a_home_inside_an_error_message_becomes_a_tilde(self) -> None:
        redactor = Redactor(["/home/alice"], ["alice"], windows=False)
        self.assertEqual(
            redactor.text("Explicit root path '/home/alice/missing' does not exist"),
            "Explicit root path '~/missing' does not exist",
        )
        self.assertEqual(redactor.text("a:/home/alice,b:/home/alice;c"), "a:~,b:~;c", "punctuation ends a path")

    def test_the_path_inside_an_oserror_message_is_redacted(self) -> None:
        redactor = Redactor([r"C:\Users\Alice"], ["Alice"], windows=True)
        message = str(PermissionError(13, "Permission denied", r"C:\Users\Alice\x"))  # [Errno 13] ...: 'C:\\Users\\Alice\\x'
        result = redactor.text(message)
        self.assertNotIn("Alice", result)
        self.assertIn(r"'~\\x'", result)

    def test_plain_words_are_never_taken_for_the_user_name(self) -> None:
        redactor = Redactor(["/home/ubuntu"], ["ubuntu"], windows=False)
        self.assertEqual(redactor.text("Ubuntu 24.04 LTS"), "Ubuntu 24.04 LTS")
        self.assertEqual(redactor.text("/media/ubuntu/T7"), "/media/<user>/T7")
        self.assertEqual(redactor.text("running on ubuntu now"), "running on ubuntu now")

    def test_the_name_inside_a_path_like_word_of_a_text_goes(self) -> None:
        redactor = Redactor([], ["alice"], windows=False)
        self.assertEqual(redactor.text("see /Volumes/alice-ssd now"), "see /Volumes/<user>-ssd now")
        self.assertEqual(redactor.text("see ~/alice-notes now"), "see ~/<user>-notes now")

    def test_the_result_is_stable_even_for_a_user_called_user(self) -> None:
        redactor = Redactor(["/home/user"], ["user"], windows=False)
        once = redactor.path("/media/user/T7-user")
        self.assertEqual(once, "/media/<user>/T7-<user>")
        self.assertEqual(redactor.text(once), once)
        self.assertEqual(redactor.path(once), once)

    def test_scrub_copies_dicts_and_lists_and_leaves_keys_and_numbers(self) -> None:
        redactor = Redactor(["/home/alice"], ["alice"], windows=False)
        data = {"/home/alice": ["/home/alice/x", {"deep": "/media/alice/T7", "n": 3, "f": 1.5, "b": True, "none": None}]}
        scrubbed = redactor.scrub(data)
        self.assertEqual(
            scrubbed,
            {"/home/alice": ["~/x", {"deep": "/media/<user>/T7", "n": 3, "f": 1.5, "b": True, "none": None}]},
        )
        self.assertEqual(data["/home/alice"][0], "/home/alice/x", "the input is not changed")

    def test_the_constants_are_the_documented_ones(self) -> None:
        self.assertEqual((REDACTED_HOME, REDACTED_USER), ("~", "<user>"))


class TestRedactorFromEnvironment(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_doctor_env_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def build(self, home: Path, login: str = "login-name", **environment: str) -> Redactor:
        env = {"USERNAME": "", "USER": "", "LOGNAME": ""}  # the real user of the machine running the tests is not used
        env.update(environment)
        with patch.object(Path, "home", return_value=home), patch("getpass.getuser", return_value=login), \
                patch.dict(os.environ, env):
            return Redactor.from_environment()

    def test_it_knows_the_home_the_login_and_the_environment_names(self) -> None:
        home = self.base / "Users" / "carol"
        home.mkdir(parents=True)
        redactor = self.build(home, login="carolx", USERNAME="carolwin", USER="caroluser", LOGNAME="carollog")
        self.assertEqual(redactor.path(str(home / "x")), "~" + os.sep + "x")
        for name in ("carolx", "carolwin", "caroluser", "carollog"):
            with self.subTest(name=name):
                self.assertEqual(redactor.path(f"/media/{name}/T7"), "/media/<user>/T7")

    def test_the_folder_name_of_a_home_under_users_or_home_is_a_user_name(self) -> None:
        for parent in ("Users", "home", "HOME"):
            with self.subTest(parent=parent):
                home = self.base / parent / "erin"
                home.mkdir(parents=True, exist_ok=True)
                redactor = self.build(home)
                self.assertEqual(redactor.path("/media/erin/T7"), "/media/<user>/T7")

    def test_a_sandbox_home_called_home_does_not_make_home_a_user_name(self) -> None:
        home = self.base / "sdos_test_session_x" / "home"
        home.mkdir(parents=True)
        redactor = self.build(home)
        self.assertEqual(redactor.path(str(home / "x")), "~" + os.sep + "x")
        self.assertEqual(redactor.path("/opt/home/data"), "/opt/home/data")

    def test_the_system_decides_windows_and_case(self) -> None:
        redactor = self.build(self.base)
        self.assertEqual(redactor.windows, os.name == "nt")
        self.assertEqual(redactor.case_insensitive, os.name == "nt" or sys.platform == "darwin")

    def test_it_survives_a_machine_without_a_home_or_a_login(self) -> None:
        with patch.object(Path, "home", side_effect=RuntimeError("no home")), \
                patch("getpass.getuser", side_effect=OSError("no user")), patch.dict(os.environ, {"USER": "frank"}):
            redactor = Redactor.from_environment()
        self.assertEqual(redactor.path("/media/frank/T7"), "/media/<user>/T7")


# ----------------------------------------------------------------------------------------------------------------------
# How SmartDrive-OS was installed, and the system's name
# ----------------------------------------------------------------------------------------------------------------------

class FakeDist:
    """What install_method needs of an importlib.metadata distribution."""

    def __init__(self, version: str = "1.4.2", files: Optional[Dict[str, str]] = None) -> None:
        self.version = version
        self.files = files or {}

    def read_text(self, name: str) -> Optional[str]:
        return self.files.get(name)


class TestInstallMethod(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_doctor_install_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.elsewhere = self.base / "somewhere" / "else"  # where the user site is, unless a test says otherwise

    def installed(self, prefix: Path, base_prefix: Optional[Path] = None, dist: Any = None) -> Dict[str, Any]:
        """install_method for a copy in <prefix>/lib/python3.13/site-packages."""
        package = prefix / "lib" / "python3.13" / "site-packages" / "smart_drive"
        with patch("site.getusersitepackages", return_value=str(self.elsewhere)):
            return install_method(package, prefix, base_prefix or self.base / "python", dist or FakeDist())

    @staticmethod
    def kind(result: Dict[str, Any]) -> Tuple[str, str]:
        return result["install_method"], result["install_detail"]

    def test_uvx_runs_from_a_temporary_uv_environment(self) -> None:
        prefix = self.base / "cache" / "uv" / "archive-v0" / "aB3dE5"
        self.assertEqual(self.kind(self.installed(prefix)), ("uvx", "uvx (temporary uv environment)"))

    def test_pipx_run_uses_a_temporary_environment_in_its_cache(self) -> None:
        prefix = self.base / "pipx" / ".cache" / "0123abcd"
        prefix.mkdir(parents=True)
        (prefix / "pipx_metadata.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.kind(self.installed(prefix)), ("pipx", "pipx run (temporary environment)"))

    def test_pipx_install_keeps_a_venv_with_pipx_metadata(self) -> None:
        prefix = self.base / "pipx" / "venvs" / "smart-drive-os"
        prefix.mkdir(parents=True)
        (prefix / "pipx_metadata.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.kind(self.installed(prefix)), ("pipx", "pipx install"))

    def test_a_cache_folder_without_pipx_metadata_is_not_pipx(self) -> None:
        prefix = self.base / "x" / ".cache" / "0123abcd"
        prefix.mkdir(parents=True)
        self.assertEqual(self.kind(self.installed(prefix)), ("pip", "virtual environment"))

    def test_uv_tool_install_leaves_a_receipt(self) -> None:
        prefix = self.base / "uv" / "tools" / "smart-drive-os"
        prefix.mkdir(parents=True)
        (prefix / "uv-receipt.toml").write_text("", encoding="utf-8")
        self.assertEqual(self.kind(self.installed(prefix)), ("uv tool", "uv tool install"))

    def test_conda_is_told_by_its_meta_folder_even_when_it_is_the_base_environment(self) -> None:
        prefix = self.base / "miniconda3"
        (prefix / "conda-meta").mkdir(parents=True)
        self.assertEqual(self.kind(self.installed(prefix, base_prefix=prefix)), ("pip", "conda environment"))

    def test_a_virtual_environment_has_a_prefix_other_than_the_base(self) -> None:
        prefix = self.base / "project" / ".venv"
        prefix.mkdir(parents=True)
        self.assertEqual(self.kind(self.installed(prefix)), ("pip", "virtual environment"))

    def test_pip_install_user_lands_in_the_user_site(self) -> None:
        prefix = self.base / "python"
        user_site = self.base / "home" / ".local" / "lib" / "python3.13" / "site-packages"
        with patch("site.getusersitepackages", return_value=str(user_site)):
            result = install_method(user_site / "smart_drive", prefix, prefix, FakeDist())
        self.assertEqual(self.kind(result), ("pip", "user site-packages (--user)"))

    def test_otherwise_it_is_the_system_python(self) -> None:
        prefix = self.base / "python"
        self.assertEqual(self.kind(self.installed(prefix, base_prefix=prefix)), ("pip", "system Python"))

    def test_a_checkout_run_directly(self) -> None:
        checkout = self.base / "checkout"
        result = install_method(checkout / "smart_drive", self.base / "py", self.base / "py", None)
        self.assertEqual(self.kind(result), ("source", "checkout run directly (not installed)"))

    def test_an_editable_install_of_this_checkout(self) -> None:
        checkout = self.base / "my_unique_clone"
        direct_url = json.dumps({"url": checkout.as_uri(), "dir_info": {"editable": True}})
        dist = FakeDist(files={"direct_url.json": direct_url})
        result = install_method(checkout / "smart_drive", self.base / "py", self.base / "py", dist)
        self.assertEqual(self.kind(result), ("source", "editable install of this checkout"))
        self.assertNotIn(checkout.as_uri(), json.dumps(result), "the url an editable install points at is never reported")
        self.assertNotIn(checkout.name, json.dumps(result))

    def test_another_installed_copy_is_told_from_this_checkout(self) -> None:
        checkout = self.base / "checkout"
        other = self.base / "elsewhere"
        for direct_url in (
            {"url": other.as_uri(), "dir_info": {"editable": True}},       # an editable install of another checkout
            {"url": checkout.as_uri(), "dir_info": {}},                    # a copy installed from this one
            {"url": "https://example.invalid/x.whl", "archive_info": {}},  # a download
            None,                                                          # pip left no direct_url.json
        ):
            with self.subTest(direct_url=direct_url):
                files = {} if direct_url is None else {"direct_url.json": json.dumps(direct_url)}
                result = install_method(checkout / "smart_drive", self.base / "py", self.base / "py", FakeDist(files=files))
                self.assertEqual(self.kind(result), ("source", "checkout run directly; another installed copy exists"))

    def test_installer_version_and_git_commit_come_from_the_distribution(self) -> None:
        commit = "0123456789abcdef0123456789abcdef01234567"
        dist = FakeDist("1.3.9", {
            "INSTALLER": "uv\n",
            "direct_url.json": json.dumps({"url": "https://example.invalid/y", "vcs_info": {"vcs": "git", "commit_id": commit}}),
        })
        result = self.installed(self.base / "python", base_prefix=self.base / "python", dist=dist)
        self.assertEqual(result["installer"], "uv")
        self.assertEqual(result["installed_version"], "1.3.9")
        self.assertEqual(result["git_commit"], commit[:12])
        self.assertEqual(set(result), {"install_method", "install_detail", "installer", "installed_version", "git_commit"})

    def test_a_distribution_without_files_leaves_them_null(self) -> None:
        result = self.installed(self.base / "python", base_prefix=self.base / "python", dist=FakeDist("1.4.2"))
        self.assertEqual((result["installer"], result["git_commit"], result["installed_version"]), (None, None, "1.4.2"))

    def test_not_installed_leaves_everything_about_the_distribution_null(self) -> None:
        package = self.base / "python" / "lib" / "site-packages" / "smart_drive"
        with patch("site.getusersitepackages", return_value=str(self.elsewhere)):
            result = install_method(package, self.base / "python", self.base / "python", None)
        self.assertEqual((result["installer"], result["git_commit"], result["installed_version"]), (None, None, None))

    def test_broken_metadata_does_not_raise(self) -> None:
        class Broken:
            version = None

            def read_text(self, name: str) -> str:
                raise OSError("unreadable")

        result = install_method(self.base / "checkout" / "smart_drive", self.base / "py", self.base / "py", Broken())
        self.assertEqual(result["install_method"], "source")
        self.assertIsNone(result["installed_version"])

    def test_called_without_arguments_it_describes_this_run(self) -> None:
        result = install_method()
        self.assertIn(result["install_method"], {"source", "uvx", "pipx", "uv tool", "pip"})
        self.assertIsInstance(result["install_detail"], str)


class TestSystemNames(unittest.TestCase):
    def test_windows_11_is_told_by_its_build(self) -> None:
        self.assertEqual(windows_name("10", "10.0.22621"), "Windows 11", "older Pythons call Windows 11 release 10")
        self.assertEqual(windows_name("11", "10.0.26300"), "Windows 11")
        self.assertEqual(windows_name("10", "10.0.22000"), "Windows 11")
        self.assertEqual(windows_name("10", "10.0.19045"), "Windows 10")
        self.assertEqual(windows_name("10", "10.0.21999"), "Windows 10")

    def test_other_windows_releases_keep_their_release(self) -> None:
        self.assertEqual(windows_name("8.1", "6.3.9600"), "Windows 8.1")
        self.assertEqual(windows_name("2022Server", "10.0.20348"), "Windows 2022Server")
        self.assertEqual(windows_name("", ""), "Windows")
        self.assertEqual(windows_name("10", "garbage"), "Windows 10")

    def test_pretty_name_of_an_os_release_file(self) -> None:
        self.assertEqual(os_release_name('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\n'), "Ubuntu 24.04 LTS")
        self.assertEqual(os_release_name("PRETTY_NAME='Alpine Linux v3.20'"), "Alpine Linux v3.20")
        self.assertEqual(os_release_name("PRETTY_NAME=Arch"), "Arch")
        self.assertEqual(os_release_name(r'PRETTY_NAME="Say \"hi\""'), 'Say "hi"')
        self.assertIsNone(os_release_name('NAME="Ubuntu"\n'))
        self.assertIsNone(os_release_name('PRETTY_NAME=""\n'))
        self.assertIsNone(os_release_name(""))

    def test_the_os_section_of_this_machine(self) -> None:
        data = doctor._collect_os()
        self.assertEqual(set(data), {"system", "name", "version", "machine", "wsl"})
        self.assertEqual(data["system"], platform.system())
        self.assertEqual(data["machine"], platform.machine())
        self.assertIsInstance(data["wsl"], bool)
        if data["system"] == "Windows":
            self.assertTrue(data["name"].startswith("Windows "))

    def test_nothing_that_names_the_host_is_asked_for(self) -> None:
        """platform.node(), platform.uname(), platform.version() (on Linux a build string) and os.uname() carry host names."""
        tree = ast.parse((ROOT / MODULES[0]).read_text(encoding="utf-8"))
        asked = {
            f"{node.value.id}.{node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        }
        host_names = {"platform.node", "platform.uname", "platform.version", "platform.platform", "os.uname",
                      "socket.gethostname", "os.getlogin"}
        self.assertEqual(sorted(asked & host_names), [])


# ----------------------------------------------------------------------------------------------------------------------
# The report of a fake computer: shape, facts, privacy
# ----------------------------------------------------------------------------------------------------------------------

class TestReportOfAFakeComputer(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)
        self.report = self.machine.report()
        self.agents = {agent["name"]: agent for agent in self.report["agents"]}

    def test_the_sections_come_in_the_documented_order_with_the_documented_types(self) -> None:
        report = self.report
        self.assertEqual(list(report), SECTIONS)
        self.assertEqual(report["report_version"], 1)
        self.assertEqual(report["redaction"], {"home": "~", "user": "<user>"})
        self.assertEqual(set(report["smartdrive"]), {
            "version", "install_method", "install_detail", "installer", "installed_version", "git_commit"})
        self.assertEqual(report["smartdrive"]["version"], smart_drive.__version__)
        python = report["python"]
        self.assertEqual(set(python), {
            "version", "implementation", "bits", "executable", "virtual_env", "sqlite_version", "fts5", "encoding"})
        self.assertEqual(python["version"], platform.python_version())
        self.assertEqual(python["implementation"], platform.python_implementation())
        self.assertIn(python["bits"], (32, 64))
        self.assertIsInstance(python["virtual_env"], bool)
        self.assertIs(python["fts5"], True)
        self.assertEqual(set(python["encoding"]), {"filesystem", "preferred", "utf8_mode"})
        self.assertIsInstance(python["encoding"]["utf8_mode"], bool)
        self.assertEqual(set(report["os"]), {"system", "name", "version", "machine", "wsl"})
        self.assertEqual(set(report["root"]), {"found", "path", "found_by", "error", "is_mount_point"})
        self.assertEqual(set(report["filesystem"]), {
            "type", "name", "allocation_unit_bytes", "cluster_size_source", "detected_via", "mount_point",
            "total_bytes", "free_bytes"})
        self.assertIsInstance(report["filesystem"]["allocation_unit_bytes"], int)
        self.assertIn(report["filesystem"]["cluster_size_source"], ("detected", "assumed"))
        self.assertIsInstance(report["problems"], list)
        json.dumps(report)  # nothing but JSON types

    def test_the_drive_root_the_index_and_the_checkup(self) -> None:
        root = self.report["root"]
        self.assertTrue(root["found"])
        self.assertEqual(root["found_by"], "explicit")
        self.assertIsNone(root["error"])
        self.assertIs(root["is_mount_point"], False)
        self.assertEqual(self.report["search_index"], {"present": True, "size_bytes": 123})
        self.assertEqual(self.report["checkup"], {"status": "ok", "score": 85, "timestamp": "2026-10-21T10:00:00Z"})

    def test_the_move_journal_lists_the_newest_five_with_what_was_undone(self) -> None:
        journal = self.report["move_journal"]
        self.assertEqual(journal["status"], "ok")
        self.assertEqual(journal["batch_count"], 7, "the line that is not JSON is not a batch")
        self.assertEqual(journal["recent"], [
            {"time": "2026-10-21T13:05:00", "tool": "undo", "moves": 4, "undoes": "2026-10-21T12:30:00", "undone": False},
            {"time": "2026-10-21T13:00:00", "tool": "paths", "moves": 1, "undoes": None, "undone": False},
            {"time": "2026-10-21T12:30:00", "tool": "organize", "moves": 4, "undoes": None, "undone": True},
            {"time": "2026-10-21T12:00:00", "tool": "relink", "moves": 1, "undoes": None, "undone": False},
            {"time": "2026-10-21T11:00:00", "tool": "classify", "moves": 2, "undoes": None, "undone": False},
        ])

    def test_each_agent_is_found_and_its_mcp_entry_described(self) -> None:
        self.assertEqual(list(self.agents), AGENT_NAMES)
        self.assertEqual(self.agents["Claude Code"]["mcp"], {
            "status": "registered", "scopes": ["user"], "project_count": 2, "launcher": "python -m smart_drive",
            "command_found": True, "matches_this_install": True,
        })
        desktop = self.agents["Claude desktop"]["mcp"]
        self.assertEqual(
            (desktop["status"], desktop["launcher"], desktop["command_found"], desktop["matches_this_install"]),
            ("registered", "uvx", False, False),
        )
        cursor = self.agents["Cursor"]["mcp"]
        self.assertEqual(
            (cursor["status"], cursor["launcher"], cursor["command_found"]),
            ("registered", "python -m smart_drive", False),
        )
        self.assertEqual(self.agents["Windsurf"]["mcp"], {"status": "unreadable config"})
        self.assertEqual(self.agents["Antigravity"]["mcp"], {"status": "no config file"})
        self.assertEqual(self.report["workspace_mcp"], {
            "status": "registered", "scopes": ["workspace"], "project_count": 0, "launcher": "python -m smart_drive",
            "command_found": True, "matches_this_install": True,
        })

    @unittest.skipIf(doctor.tomllib is None, "TOML parsing needs Python 3.11+ (tomllib)")
    def test_codex_is_read_with_tomllib_when_there_is_one(self) -> None:
        self.assertEqual(self.agents["Codex"]["mcp"], {
            "status": "registered", "scopes": ["user"], "project_count": 0, "launcher": "python -m smart_drive",
            "command_found": True, "matches_this_install": True,
        })

    def test_codex_is_found_by_its_table_header_without_tomllib(self) -> None:
        with patch.object(doctor, "tomllib", None):
            report = self.machine.report()
        codex = next(agent for agent in report["agents"] if agent["name"] == "Codex")
        self.assertEqual(codex["mcp"], {
            "status": "registered", "scopes": ["user"], "project_count": 0, "launcher": None,
            "command_found": None, "matches_this_install": None,
        })
        self.assertEqual(report["problems"], self.report["problems"], "what is unknown is not a problem")

    def test_detected_and_undetected_agents_and_ides(self) -> None:
        detected = {name: agent["detected"] for name, agent in self.agents.items()}
        self.assertEqual(detected, {
            "Claude Code": True, "Claude desktop": True, "Codex": True, "Cursor": True, "Windsurf": True,
            "Antigravity": True, "Gemini CLI": False, "VS Code": True, "VS Code Insiders": False, "VSCodium": False,
            "JetBrains": False, "Obsidian": False,
        })
        for name in ("Gemini CLI", "VS Code", "VS Code Insiders", "VSCodium", "JetBrains", "Obsidian"):
            self.assertIsNone(self.agents[name]["mcp"], name)

    def test_only_the_names_of_the_variables_that_matter_are_listed(self) -> None:
        self.assertEqual(self.report["env_vars_set"], ["CLAUDECODE", "SMART_DRIVE_MCP_AUTH_TOKEN"])

    def test_the_problems_are_short_sentences(self) -> None:
        self.assertEqual(self.report["problems"], [
            "Claude desktop: the smart-drive entry starts a command that is not on this computer; "
            "run `smart-drive mcp register` again.",
            "Cursor: the smart-drive entry starts a command that is not on this computer; "
            "run `smart-drive mcp register` again.",
            "Windsurf: its MCP config file cannot be read as JSON.",
        ])

    def test_no_output_holds_a_path_a_name_or_a_secret_of_the_machine(self) -> None:
        home = self.machine.home
        forbidden = [
            str(home), home.as_posix(), str(home).replace("\\", "\\\\"), USER, SECRET_NAME, SECRET_TOKEN, AUTH_TOKEN,
            OTHER_SERVER, "sk-unrelated", str(self.machine.base / SECRET_NAME), (self.machine.base / SECRET_NAME).as_posix(),
        ]
        for form, output in self.machine.outputs(self.report).items():
            with self.subTest(form=form):
                self.assertIn(REDACTED_HOME, output)
                for text in forbidden:
                    self.assertNotIn(text.lower(), output.lower())

    def test_the_python_path_and_the_drive_path_are_redacted(self) -> None:
        self.assertEqual(self.report["python"]["executable"], os.sep.join(["~", ".pyenv", "bin", "python"]))
        path = self.report["root"]["path"]
        self.assertIn(os.sep + "<user>" + os.sep, path)
        self.assertTrue(path.endswith(os.sep.join(["<user>", "T7"])), path)

    def test_a_drive_root_inside_the_home_folder_shows_a_tilde(self) -> None:
        inside = self.machine.home / "drive"
        shutil.copytree(self.machine.root, inside)
        report = self.machine.report(root=str(inside))
        self.assertEqual(report["root"]["path"], os.sep.join(["~", "drive"]))
        for form, output in self.machine.outputs(report).items():
            with self.subTest(form=form):
                self.assertNotIn(str(self.machine.home), output)
                self.assertNotIn(USER, output)

    def test_nothing_is_written_and_the_drive_is_never_listed(self) -> None:
        before = tree_state(self.machine.base)
        violations: List[str] = []
        with watching_listings(self.machine.root, violations):
            report = self.machine.report()
            self.machine.outputs(report)
        self.assertEqual(violations, [], "the drive must not be scanned")
        self.assertEqual(tree_state(self.machine.base), before, "doctor must not create or change anything")

    def test_a_fresh_drive_gets_no_smart_drive_folder(self) -> None:
        bare = self.machine.base / "bare"
        bare.mkdir()
        report = self.machine.report(root=str(bare))
        self.assertEqual(list(bare.iterdir()), [], "no .smart_drive folder is created")
        self.assertEqual(report["checkup"]["status"], "not run")
        self.assertEqual(report["move_journal"], {"status": "empty", "batch_count": 0, "recent": []})
        self.assertEqual(report["search_index"], {"present": False, "size_bytes": 0})
        self.assertEqual(report["workspace_mcp"], {"status": "no config file"})

    def test_the_final_scrub_catches_a_path_a_collector_forgot(self) -> None:
        leak = {"system": "x", "name": str(self.machine.home / "leaky"), "version": "1", "machine": "x", "wsl": False}
        with patch.object(doctor, "_collect_os", return_value=leak):
            report = self.machine.report()
        self.assertEqual(report["os"]["name"], os.sep.join(["~", "leaky"]))

    def test_with_no_arguments_the_report_is_of_this_machine(self) -> None:
        report = collect_report()
        self.assertEqual(list(report), SECTIONS)
        self.assertTrue(report["root"]["found"], "tests/__init__ sets SMART_DRIVE_ROOT")
        self.assertEqual(report["root"]["found_by"], "env")
        self.assertEqual([agent["name"] for agent in report["agents"]], AGENT_NAMES)


class TestRootNotFound(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)
        self.missing = self.machine.base / "no" / USER / "such" / "drive"
        self.report = self.machine.report(root=str(self.missing))

    def test_the_report_says_so_and_the_drive_sections_are_null(self) -> None:
        root = self.report["root"]
        self.assertFalse(root["found"])
        self.assertIsNone(root["path"])
        self.assertIsNone(root["found_by"])
        self.assertIsNone(root["is_mount_point"])
        self.assertIn("does not exist", root["error"])
        self.assertNotIn(USER, root["error"])
        self.assertIn("<user>", root["error"], "the path in the message is redacted too")
        for section in ("filesystem", "search_index", "checkup", "move_journal", "workspace_mcp"):
            self.assertIsNone(self.report[section], section)
        self.assertEqual(len(self.report["agents"]), len(AGENT_NAMES), "the agents are reported all the same")
        self.assertEqual(self.report["problems"][0], "No drive root found: pass --root <path> or set SMART_DRIVE_ROOT.")

    def test_both_renderers_cope_and_say_what_is_missing(self) -> None:
        markdown = render_markdown(self.report)
        self.assertIn("not found: Explicit root path", markdown)
        self.assertIn("No drive root, so no move journal.", markdown)
        self.assertNotIn("Drive root `.mcp.json`", markdown)
        self.assertIn("| Filesystem | no drive root |", markdown)
        text = render_text(self.report)
        self.assertIn("not found: Explicit root path", text)
        self.assertIn("no drive root", text)
        for output in (markdown, text, json.dumps(self.report)):
            self.assertNotIn(USER, output.lower())

    def test_the_command_exits_zero_all_the_same(self) -> None:
        for flags in ([], ["--report"], ["--json"]):
            with self.subTest(flags=flags):
                code, out, err = run_main(["doctor", *flags, "--root", str(self.missing)])
                self.assertEqual((code, err), (0, ""))
                if "--json" in flags:
                    self.assertFalse(json.loads(out)["root"]["found"])
                else:
                    self.assertIn("not found", out)


class TestSectionsAreIndependent(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)

    def test_a_section_that_raises_becomes_an_error_and_the_rest_stays(self) -> None:
        boom = RuntimeError(f"boom at {self.machine.home / 'secret'}")
        with patch.object(doctor, "_collect_python", side_effect=boom):
            report = self.machine.report()
        self.assertEqual(report["python"], {"error": "RuntimeError: boom at " + os.sep.join(["~", "secret"])})
        self.assertEqual(report["checkup"]["score"], 85)
        self.assertEqual(len(report["agents"]), len(AGENT_NAMES))
        self.assertIn("The python section could not be collected", " ".join(report["problems"]))
        for output in self.machine.outputs(report).values():
            self.assertNotIn(USER, output)
        self.assertIn("error: RuntimeError", render_markdown(report))
        self.assertIn("error: RuntimeError", render_text(report))

    def test_every_section_can_fail_at_once_and_the_report_is_still_built(self) -> None:
        names = ["_collect_python", "_collect_os", "_collect_filesystem", "_collect_search_index", "_collect_checkup",
                 "_collect_move_journal", "_collect_agents", "_check_json_config"]
        with contextlib.ExitStack() as stack:
            for name in names:
                stack.enter_context(patch.object(doctor, name, side_effect=ValueError(name)))
            report = self.machine.report()
        for section in ("python", "os", "filesystem", "search_index", "checkup", "move_journal", "agents", "workspace_mcp"):
            self.assertEqual(set(report[section]), {"error"}, section)
        self.assertTrue(report["root"]["found"])
        for output in self.machine.outputs(report).values():
            self.assertTrue(output)

    def test_one_agent_that_cannot_be_inspected_does_not_hide_the_others(self) -> None:
        with patch.object(doctor, "_check_toml_config", side_effect=ValueError("toml broke")):
            report = self.machine.report()
        agents = {agent["name"]: agent for agent in report["agents"]}
        self.assertEqual(agents["Codex"]["mcp"], {"error": "ValueError: toml broke"})
        self.assertEqual(agents["Cursor"]["mcp"]["status"], "registered")
        self.assertIn("Codex: could not be checked (ValueError: toml broke).", report["problems"])
        self.assertIn("error: ValueError: toml broke", render_markdown(report))

    def test_the_root_resolver_failing_is_a_section_error(self) -> None:
        with patch.object(doctor, "resolve_drive_root", side_effect=RuntimeError("resolver broke")):
            report = self.machine.report()
        self.assertEqual(report["root"], {"error": "RuntimeError: resolver broke"})
        self.assertIsNone(report["filesystem"])
        self.assertIn("error: RuntimeError: resolver broke", render_text(report))


# ----------------------------------------------------------------------------------------------------------------------
# Pieces of the agent check
# ----------------------------------------------------------------------------------------------------------------------

class TestMcpEntries(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_doctor_mcp_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def machine(self, which: Callable[[str], Optional[str]] = no_programs) -> Any:
        return doctor._Machine(self.base, self.base, self.base, False, {}, which, None)

    def test_the_launcher_of_an_entry(self) -> None:
        cases: List[Tuple[Dict[str, Any], str]] = [
            ({"command": "python3", "args": ["-m", "smart_drive", "mcp"]}, "python -m smart_drive"),
            ({"command": r"C:\Python313\python.exe", "args": ["-m", "smart_drive", "mcp"]}, "python -m smart_drive"),
            ({"command": "uvx", "args": ["smart-drive-os", "mcp"]}, "uvx"),
            ({"command": "/home/a/.local/bin/uvx", "args": ["smart-drive-os", "mcp"]}, "uvx"),
            ({"command": r"C:\tools\UVX.EXE", "args": ["smart-drive-os", "mcp"]}, "uvx"),
            ({"command": "uv", "args": ["tool", "run", "smart-drive-os", "mcp"]}, "uvx"),
            ({"command": "pipx", "args": ["run", "smart-drive-os", "mcp"]}, "pipx run"),
            ({"command": "smart-drive", "args": ["mcp"]}, "smart-drive command"),
            ({"command": r"C:\x\smart-drive-os.exe", "args": ["mcp"]}, "smart-drive command"),
            ({"command": "smart_drive", "args": []}, "smart-drive command"),
            ({"command": "smart-drive-manager.cmd"}, "smart-drive command"),
            ({"command": "uvx", "args": ["something-else"]}, "other"),
            ({"command": "uv", "args": ["pip", "install"]}, "other"),
            ({"command": "pipx", "args": ["install", "smart-drive-os"]}, "other"),
            ({"command": "node", "args": ["server.js"]}, "other"),
            ({"command": 5, "args": "not a list"}, "other"),
            ({}, "other"),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(doctor._launcher_of(entry), expected)

    def test_our_entry_is_the_key_or_anything_that_names_us(self) -> None:
        find = doctor._find_our_entry
        by_key = {"command": "x"}
        self.assertIs(find({"smart-drive": by_key, "other": {"command": "smart-drive"}}), by_key)
        by_args = {"command": "python", "args": ["-m", "smart_drive", "mcp"]}
        self.assertIs(find({"renamed": by_args}), by_args)
        by_command = {"command": r"E:\Tools\Smart-Drive-OS\.venv\Scripts\python.exe"}
        self.assertIs(find({"x": by_command}), by_command)
        self.assertIsNone(find({"other": {"command": "npx", "args": ["tool"]}}))
        self.assertIsNone(find({"a": "not a dict", "b": None}))
        self.assertIsNone(find(["smart-drive"]))
        self.assertIsNone(find(None))
        self.assertEqual(find({"smart-drive": "not a dict"}), {}, "the key is there, so it is registered")

    def test_a_json_config_is_missing_unreadable_empty_or_registered(self) -> None:
        machine = self.machine()
        path = self.base / "mcp.json"

        def check() -> Dict[str, Any]:
            return doctor._check_json_config(path, "user", machine)

        self.assertEqual(check(), {"status": "no config file"})
        for text in ("{ not json", "[]", '"text"', "", "null"):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                self.assertEqual(check(), {"status": "unreadable config"})
        path.write_bytes(b"\xff\xfe\x00")  # not UTF-8
        self.assertEqual(check(), {"status": "unreadable config"})
        for data in ({}, {"mcpServers": []}, {"mcpServers": {}}, {"mcpServers": {"other": {"command": "npx"}}}):
            with self.subTest(data=data):
                write_json(path, data)
                self.assertEqual(check(), {"status": "not registered"})
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {"smart-drive": launch_entry()}}).encode())
        self.assertEqual(check()["status"], "registered", "a byte order mark is fine")

    def test_a_folder_in_place_of_the_config_is_not_a_config(self) -> None:
        (self.base / "mcp.json").mkdir()
        result = doctor._check_json_config(self.base / "mcp.json", "user", self.machine())
        self.assertEqual(result, {"status": "no config file"})

    def test_command_found_looks_at_the_file_or_the_path(self) -> None:
        machine = self.machine(which=lambda name: "/usr/bin/" + name if name == "uvx" else None)
        self.assertIs(doctor._command_found(PYTHON, machine), True)
        self.assertIs(doctor._command_found(str(self.base / "gone"), machine), False)
        self.assertIs(doctor._command_found(str(self.base), machine), False, "a folder is not a command")
        self.assertIs(doctor._command_found("uvx", machine), True)
        self.assertIs(doctor._command_found("nothere", machine), False)
        self.assertIsNone(doctor._command_found(None, machine))
        self.assertIsNone(doctor._command_found(["uvx"], machine))

    def test_matches_this_install_compares_command_and_arguments(self) -> None:
        expected = launch_entry(PYTHON)
        same = doctor._is_this_install
        self.assertIs(same(launch_entry(PYTHON), expected), True)
        self.assertIs(same(launch_entry(PYTHON.upper() if os.name == "nt" else PYTHON), expected), True)
        self.assertIs(same(launch_entry(PYTHON + "x"), expected), False)
        self.assertIs(same({"command": PYTHON, "args": ["-m", "smart_drive"]}, expected), False)
        self.assertIs(same({"command": PYTHON}, expected), False)
        self.assertIs(same({"command": 3, "args": []}, expected), False)
        self.assertIsNone(same(launch_entry(PYTHON), None))

    def test_a_project_only_registration_has_no_user_scope(self) -> None:
        path = self.base / ".claude.json"
        write_json(path, {"projects": {"p1": {"mcpServers": {"smart-drive": launch_entry()}}, "p2": 5, "p3": {}}})
        result = doctor._check_claude_code(path, self.machine())
        self.assertEqual((result["status"], result["scopes"], result["project_count"]), ("registered", [], 1))

    def test_claude_code_without_our_entry_anywhere(self) -> None:
        path = self.base / ".claude.json"
        write_json(path, {"mcpServers": {"o": {"command": "npx"}}, "projects": {"p": {"mcpServers": {"x": {"command": "y"}}}}})
        self.assertEqual(doctor._check_claude_code(path, self.machine()), {"status": "not registered"})
        self.assertEqual(doctor._check_claude_code(self.base / "gone.json", self.machine()), {"status": "no config file"})

    def test_a_toml_config_is_missing_or_without_our_table(self) -> None:
        machine = self.machine()
        path = self.base / "config.toml"
        self.assertEqual(doctor._check_toml_config(path, "user", machine), {"status": "no config file"})
        path.write_text("model = 'x'\n[mcp_servers.other]\ncommand = 'npx'\n", encoding="utf-8")
        self.assertEqual(doctor._check_toml_config(path, "user", machine), {"status": "not registered"})

    def test_without_tomllib_the_table_header_decides(self) -> None:
        machine = self.machine()
        path = self.base / "config.toml"
        headers = ["[mcp_servers.smart-drive]", '[mcp_servers."smart-drive"]', "[mcp_servers.'smart-drive']",
                   "  [ mcp_servers . smart-drive ]  "]
        with patch.object(doctor, "tomllib", None):
            for header in headers:
                with self.subTest(header=header):
                    path.write_text(f"model = 'x'\n{header}\ncommand = 'python'\n", encoding="utf-8")
                    self.assertEqual(doctor._check_toml_config(path, "user", machine)["status"], "registered")
            path.write_text("# [mcp_servers.smart-drive]\nx = 1\n", encoding="utf-8")
            self.assertEqual(doctor._check_toml_config(path, "user", machine), {"status": "not registered"}, "a comment")
            path.write_text("[mcp_servers.smart-drive-extra]\n", encoding="utf-8")
            self.assertEqual(doctor._check_toml_config(path, "user", machine), {"status": "not registered"}, "not ours")

    @unittest.skipIf(doctor.tomllib is None, "TOML parsing needs Python 3.11+ (tomllib)")
    def test_a_toml_config_that_does_not_parse_is_unreadable(self) -> None:
        path = self.base / "config.toml"
        path.write_text("[mcp_servers.smart-drive\ncommand = 1\n", encoding="utf-8")
        self.assertEqual(doctor._check_toml_config(path, "user", self.machine()), {"status": "unreadable config"})
        path.write_text("[mcp_servers.smart-drive]\ncommand = 'python'\nargs = ['-m', 'smart_drive', 'mcp']\n", encoding="utf-8")
        result = doctor._check_toml_config(path, "user", self.machine())
        self.assertEqual((result["status"], result["launcher"]), ("registered", "python -m smart_drive"))

    def test_the_problem_of_an_unreadable_config_names_its_format(self) -> None:
        report = {"agents": [{"name": "Codex", "detected": True, "mcp": {"status": "unreadable config"}},
                             {"name": "Cursor", "detected": True, "mcp": {"status": "unreadable config"}}]}
        self.assertEqual(doctor._problems_of(report), [
            "Codex: its MCP config file cannot be read as TOML.",
            "Cursor: its MCP config file cannot be read as JSON.",
        ])


class TestAgentDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)

    def agents(self, **overrides: Any) -> Dict[str, Dict[str, Any]]:
        return {agent["name"]: agent for agent in self.machine.report(**overrides)["agents"]}

    def test_a_program_on_path_counts_as_installed(self) -> None:
        shutil.rmtree(self.machine.home)
        self.machine.app_data.mkdir(parents=True)
        installed = {"claude", "gemini", "code", "codex"}
        agents = self.agents(which=lambda name: f"/usr/bin/{name}" if name in installed else None)
        self.assertTrue(agents["Claude Code"]["detected"])
        self.assertEqual(agents["Claude Code"]["mcp"], {"status": "no config file"})
        self.assertEqual(agents["Gemini CLI"]["mcp"], {"status": "no config file"})
        self.assertEqual(agents["Codex"]["mcp"], {"status": "no config file"})
        self.assertTrue(agents["VS Code"]["detected"])
        self.assertIsNone(agents["VS Code"]["mcp"])
        for name in ("Claude desktop", "Cursor", "Windsurf", "Antigravity", "VS Code Insiders", "VSCodium", "JetBrains",
                     "Obsidian"):
            self.assertEqual((agents[name]["detected"], agents[name]["mcp"]), (False, None), name)

    def test_a_folder_of_an_ide_in_appdata_counts_as_installed(self) -> None:
        for folder in ("Code - Insiders", "VSCodium", "JetBrains", "obsidian"):
            (self.machine.app_data / folder).mkdir()
        agents = self.agents()
        for name in ("VS Code Insiders", "VSCodium", "JetBrains", "Obsidian"):
            self.assertTrue(agents[name]["detected"], name)
            self.assertIsNone(agents[name]["mcp"], name)

    def test_gemini_cli_is_found_by_its_settings_file(self) -> None:
        write_json(self.machine.home / ".gemini" / "settings.json", {"mcpServers": {"smart-drive": launch_entry()}})
        agents = self.agents()
        self.assertTrue(agents["Gemini CLI"]["detected"])
        self.assertEqual(agents["Gemini CLI"]["mcp"]["status"], "registered")

    def test_a_given_home_ignores_the_antigravity_override(self) -> None:
        custom = self.machine.base / "custom_antigravity"
        write_json(custom / "mcp_config.json", {"mcpServers": {"smart-drive": launch_entry()}})
        agents = self.agents(environ={"ANTIGRAVITY_HOME": str(custom), "GEMINI_HOME": str(custom)})
        self.assertEqual(agents["Antigravity"]["mcp"], {"status": "no config file"})

    def test_without_a_given_home_the_override_names_the_antigravity_folder(self) -> None:
        custom = self.machine.base / "custom_antigravity"
        write_json(custom / "mcp_config.json", {"mcpServers": {"smart-drive": launch_entry()}})
        empty = str(self.machine.base / "empty")
        for variable in ("ANTIGRAVITY_HOME", "GEMINI_HOME"):
            with self.subTest(variable=variable), patch.dict(os.environ, {"USERPROFILE": empty, "HOME": empty}):
                report = collect_report(
                    str(self.machine.root), app_data=self.machine.base / "empty_appdata", redactor=self.machine.redactor,
                    which=no_programs, environ={variable: str(custom)},
                )
                antigravity = next(agent for agent in report["agents"] if agent["name"] == "Antigravity")
                self.assertTrue(antigravity["detected"])
                self.assertEqual(antigravity["mcp"]["status"], "registered")
                self.assertEqual(report["env_vars_set"], [variable])

    @unittest.skipUnless(sys.platform == "win32", "the Microsoft Store build of Claude desktop is Windows only")
    def test_the_microsoft_store_build_of_claude_desktop_has_its_own_config(self) -> None:
        shutil.rmtree(self.machine.app_data / "Claude")
        store = self.machine.local_app_data / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude"
        write_json(store / "claude_desktop_config.json", {"mcpServers": {"smart-drive": launch_entry()}})
        claude = self.agents()["Claude desktop"]
        self.assertTrue(claude["detected"])
        self.assertEqual(claude["mcp"]["status"], "registered")

    def test_the_environment_names_that_matter(self) -> None:
        environ = {
            "SMART_DRIVE_ROOT": "x", "SMART_DRIVE_ANYTHING": "y", "KINGSTON_SSD_ROOT": "z", "ANTIGRAVITY_HOME": "a",
            "GEMINI_HOME": "b", "CONSTELLATION_HOME": "c", "SD_SELFTEST": "1", "CLAUDECODE": "1", "PATH": "p",
            "smart_drive_lowercase": "q", "SMARTDRIVE_LANG": "vi",
        }
        report = self.machine.report(environ=environ)
        self.assertEqual(report["env_vars_set"], [
            "ANTIGRAVITY_HOME", "CLAUDECODE", "CONSTELLATION_HOME", "GEMINI_HOME", "KINGSTON_SSD_ROOT", "SD_SELFTEST",
            "SMART_DRIVE_ANYTHING", "SMART_DRIVE_ROOT",
        ])


# ----------------------------------------------------------------------------------------------------------------------
# What is read of the drive
# ----------------------------------------------------------------------------------------------------------------------

class TestDriveFacts(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_doctor_drive_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.state = self.base / ".smart_drive"
        self.state.mkdir()

    def checkup(self) -> Dict[str, Any]:
        return doctor._collect_checkup(str(self.base))

    def journal(self, text: str) -> Dict[str, Any]:
        (self.state / "moves.jsonl").write_text(text, encoding="utf-8")
        return doctor._collect_move_journal(str(self.base))

    def test_the_checkup_keeps_only_a_score_and_a_time(self) -> None:
        write_json(self.state / "checkup.json",
                   {"score": 51, "timestamp": "2026-10-20T10:48:13Z", "items": ["PRIVATE_FOLDER"], "root": "E:/x"})
        result = self.checkup()
        self.assertEqual(result, {"status": "ok", "score": 51, "timestamp": "2026-10-20T10:48:13Z"})
        self.assertNotIn("PRIVATE_FOLDER", json.dumps(result))

    def test_the_checkup_without_a_file_or_with_a_broken_one(self) -> None:
        self.assertEqual(self.checkup(), {"status": "not run", "score": None, "timestamp": None})
        for text in ("{ no", "[1, 2]", "", "null", "7"):
            with self.subTest(text=text):
                (self.state / "checkup.json").write_text(text, encoding="utf-8")
                self.assertEqual(self.checkup(), {"status": "unreadable", "score": None, "timestamp": None})

    def test_a_score_or_time_that_does_not_look_like_one_is_dropped(self) -> None:
        path = self.state / "checkup.json"
        for score in (True, 101, -1, 85.5, "85", None, [85]):
            with self.subTest(score=score):
                write_json(path, {"score": score, "timestamp": "2026-10-20T10:48:13Z"})
                self.assertIsNone(self.checkup()["score"])
        stamps = ("yesterday", "2026-10-20 10:48:13", "2026-10-20T10:48:13Z\nPRIVATE", 5, None, "C:\\private\\2026-10-20T10:00:00")
        for stamp in stamps:
            with self.subTest(stamp=stamp):
                write_json(path, {"score": 1, "timestamp": stamp})
                self.assertIsNone(self.checkup()["timestamp"])
        write_json(path, {"score": 0, "timestamp": "2026-10-20T10:48:13.123456"})
        self.assertEqual(self.checkup(), {"status": "ok", "score": 0, "timestamp": "2026-10-20T10:48:13.123456"})

    def test_the_search_index_is_found_in_either_place_and_no_path_is_reported(self) -> None:
        self.assertEqual(doctor._collect_search_index(str(self.base)), {"present": False, "size_bytes": 0})
        legacy = self.base / ".smart_drive_manager"
        legacy.mkdir()
        (legacy / "index.db").write_bytes(b"12345")
        self.assertEqual(doctor._collect_search_index(str(self.base)), {"present": True, "size_bytes": 5})

    def test_a_journal_that_is_missing_or_empty_is_empty(self) -> None:
        empty = {"status": "empty", "batch_count": 0, "recent": []}
        self.assertEqual(doctor._collect_move_journal(str(self.base)), empty)
        self.assertEqual(self.journal(""), empty)
        self.assertEqual(self.journal("\n  \n\n"), empty)

    def test_a_journal_without_one_valid_batch_is_unreadable(self) -> None:
        unreadable = {"status": "unreadable", "batch_count": 0, "recent": []}
        self.assertEqual(self.journal("not json\n{}\n"), unreadable)
        self.assertEqual(self.journal('{"time": "2026-10-20T10:00:00", "tool": "organize", "moves": []}\n'), unreadable)
        self.assertEqual(self.journal('{"time": "t", "moves": [["only one"]]}\n'), unreadable)
        self.assertEqual(doctor._problems_of({"move_journal": unreadable}), ["The move journal holds no batch that can be read."])

    def test_a_time_or_tool_that_looks_wrong_is_not_passed_on(self) -> None:
        text = (
            '{"time": "C:\\\\private\\\\2026", "tool": "format-the-disk", "undoes": "PRIVATE", "moves": [["a", "b"]]}\n'
            '{"time": 5, "tool": ["organize"], "moves": [["a", "b"], ["c", "d"]]}\n'
        )
        result = self.journal(text)
        self.assertEqual(result["recent"], [
            {"time": "?", "tool": "other", "moves": 2, "undoes": None, "undone": False},
            {"time": "?", "tool": "other", "moves": 1, "undoes": "?", "undone": False},
        ])
        self.assertNotIn("private", json.dumps(result).lower())

    def test_only_the_last_five_batches_are_listed_but_all_are_counted(self) -> None:
        lines = [
            json.dumps({"time": f"2026-10-21T10:00:{index:02d}", "tool": "organize", "moves": [["a", "b"]] * (index + 1)})
            for index in range(12)
        ]
        result = self.journal("\n".join(lines))
        self.assertEqual(result["batch_count"], 12)
        self.assertEqual([batch["moves"] for batch in result["recent"]], [12, 11, 10, 9, 8])
        self.assertEqual(result["recent"][0]["time"], "2026-10-21T10:00:11")

    def test_a_batch_is_undone_only_by_a_later_undo(self) -> None:
        text = "\n".join([
            json.dumps({"time": "2026-10-21T09:00:00", "tool": "undo", "undoes": "2026-10-21T10:00:00", "moves": [["a", "b"]]}),
            json.dumps({"time": "2026-10-21T10:00:00", "tool": "organize", "moves": [["a", "b"]]}),
            json.dumps({"time": "2026-10-21T10:30:00", "tool": "relink", "moves": [["a", "b"]]}),
            json.dumps({"time": "2026-10-21T11:00:00", "tool": "undo", "undoes": "2026-10-21T10:30:00", "moves": [["a", "b"]]}),
        ])
        recent = {batch["time"]: batch for batch in self.journal(text)["recent"]}
        self.assertFalse(recent["2026-10-21T10:00:00"]["undone"], "an undo that came before it undoes nothing")
        self.assertFalse(recent["2026-10-21T10:30:00"]["undone"], "only organize and classify can be undone")
        self.assertEqual(recent["2026-10-21T11:00:00"]["undoes"], "2026-10-21T10:30:00")


# ----------------------------------------------------------------------------------------------------------------------
# Renderers
# ----------------------------------------------------------------------------------------------------------------------

def table_rows(lines: List[str], header: str) -> List[str]:
    """The rows (without the separator line) of the table whose header line is `header`."""
    rows: List[str] = []
    for line in lines[lines.index(header) + 2:]:
        if not line.startswith("| "):
            break
        rows.append(line)
    return rows


class TestMarkdown(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)
        self.report = self.machine.report()
        self.markdown = render_markdown(self.report)
        self.lines = self.markdown.splitlines()

    def test_it_starts_with_the_comment_and_has_the_four_blocks_in_order(self) -> None:
        self.assertEqual(self.lines[0], REPORT_COMMENT)
        self.assertEqual(
            REPORT_COMMENT,
            "<!-- smart-drive doctor --report: paths are redacted (~ is the home folder, <user> the user name); "
            "no file names from the drive, tokens or config contents are included. -->",
        )
        headings = [line for line in self.lines if line.startswith("### ")]
        self.assertEqual(
            headings, ["### Environment", "### Coding agents and IDEs", "### Recent move batches", "### Problems noticed"]
        )
        for header in ("| Item | Value |", "| Agent / IDE | Detected | MCP entry |", "| Time | Tool | Moves | Undone |"):
            self.assertIn(header, self.lines)

    def test_the_environment_block_holds_the_facts(self) -> None:
        rows = {}
        for line in table_rows(self.lines, "| Item | Value |"):
            label, _, value = line[2:-2].partition(" | ")
            rows[label] = value
        self.assertEqual(list(rows), ENVIRONMENT_LABELS)
        self.assertEqual(rows["SmartDrive-OS"], f"{smart_drive.__version__}; pipx (pipx install)")
        self.assertIn(f"`{os.sep.join(['~', '.pyenv', 'bin', 'python'])}`", rows["Python"])
        self.assertIn("FTS5: yes", rows["Python"])
        self.assertTrue(rows["Drive root"].startswith("`"), rows["Drive root"])
        self.assertTrue(rows["Drive root"].endswith("; from --root"), rows["Drive root"])
        self.assertRegex(rows["Filesystem"], r"allocation unit (\d+ KB|\d+ B) \((detected|assumed)\)")
        self.assertEqual(rows["Search index"], "built, 123 B")
        self.assertEqual(rows["Last checkup"], "85/100 at 2026-10-21T10:00:00Z")
        self.assertEqual(rows["Environment variables set"], "`CLAUDECODE`, `SMART_DRIVE_MCP_AUTH_TOKEN`")

    def test_one_row_per_agent_and_one_for_the_workspace_file(self) -> None:
        rows = table_rows(self.lines, "| Agent / IDE | Detected | MCP entry |")
        self.assertEqual(len(rows), len(AGENT_NAMES) + 1)
        self.assertEqual(
            rows[0],
            "| Claude Code | yes | registered (user scope, 2 projects; python -m smart_drive; command found; this install) |",
        )
        self.assertIn("| Claude desktop | yes | registered (user scope; uvx; command not found; another install) |", rows)
        self.assertIn("| Windsurf | yes | unreadable config |", rows)
        self.assertIn("| Antigravity | yes | no config file |", rows)
        self.assertIn("| Gemini CLI | no | — |", rows)
        self.assertIn("| VS Code | yes | — |", rows)
        self.assertEqual(
            rows[-1],
            "| Drive root `.mcp.json` | — | registered (workspace scope; python -m smart_drive; command found; this install) |",
        )

    def test_the_move_batches_and_the_problems(self) -> None:
        self.assertEqual(table_rows(self.lines, "| Time | Tool | Moves | Undone |"), [
            "| 2026-10-21T13:05:00 | undo | 4 | undoes 2026-10-21T12:30:00 |",
            "| 2026-10-21T13:00:00 | paths | 1 | — |",
            "| 2026-10-21T12:30:00 | organize | 4 | yes |",
            "| 2026-10-21T12:00:00 | relink | 1 | — |",
            "| 2026-10-21T11:00:00 | classify | 2 | no |",
        ])
        self.assertIn("7 batches recorded; the newest 5:", self.lines)
        problems = self.lines[self.lines.index("### Problems noticed") + 2:]
        self.assertEqual(len(problems), 3)
        self.assertTrue(all(line.startswith("- ") for line in problems))

    def test_no_journal_and_no_problems_have_their_own_sentences(self) -> None:
        bare = self.machine.base / "bare"
        bare.mkdir()
        report = self.machine.report(root=str(bare))
        report["problems"] = []
        markdown = render_markdown(report)
        self.assertIn("No move batches recorded.", markdown)
        self.assertTrue(markdown.endswith("### Problems noticed\n\nNone."), markdown[-60:])
        self.assertIn("| Last checkup | not run yet |", markdown)
        self.assertIn("| Search index | not built |", markdown)

    def test_pipes_and_newlines_cannot_break_a_table(self) -> None:
        report = self.machine.report()
        report["python"]["executable"] = "~/a|b\nc"
        report["agents"][0]["name"] = "Name | with pipe"
        lines = render_markdown(report).splitlines()
        python_row = next(line for line in lines if line.startswith("| Python |"))
        self.assertIn("`~/a\\|b c`", python_row)
        self.assertIn("| Name \\| with pipe | yes |", "\n".join(lines))
        for header in ("| Item | Value |", "| Agent / IDE | Detected | MCP entry |", "| Time | Tool | Moves | Undone |"):
            columns = len(re.findall(r"(?<!\\)\|", header))
            for row in table_rows(lines, header):
                self.assertEqual(len(re.findall(r"(?<!\\)\|", row)), columns, row)

    def test_a_less_than_sign_outside_code_is_escaped_so_github_does_not_drop_it(self) -> None:
        """GitHub reads `<user>` or `<path>` in running text as an HTML tag and shows nothing; `\\<` shows it."""
        report = self.machine.report(root=str(self.machine.base / "no" / USER / "such"))
        markdown = render_markdown(report)
        self.assertIn("pass --root \\<path> or set SMART_DRIVE_ROOT.", markdown)
        self.assertIn("\\<user>", markdown, "the redacted user name inside the error of the missing root")
        body = markdown.split("\n", 1)[1]  # the first line is the comment, which GitHub does not show
        outside_code = re.sub(r"(`+).+?\1", "", body, flags=re.DOTALL)
        self.assertEqual(re.findall(r"(?<!\\)<", outside_code), [])
        self.assertEqual(doctor._cell("a|b <x> `<y|z>` c\nd"), "a\\|b \\<x> `<y\\|z>` c d")

    def test_a_path_with_a_backtick_gets_a_longer_fence(self) -> None:
        report = self.machine.report()
        report["python"]["executable"] = "~/we`ird"
        self.assertIn("`` ~/we`ird ``", render_markdown(report))

    def test_a_report_with_missing_keys_still_renders(self) -> None:
        reports: List[Dict[str, Any]] = [
            {},
            {"agents": "nonsense", "problems": 5},
            {"root": {"found": True}, "move_journal": {"status": "ok", "recent": [{}]}},
            {"move_journal": {"status": "ok", "batch_count": 1, "recent": [{}]}},
        ]
        for report in reports:
            with self.subTest(report=report):
                self.assertIn("### Problems noticed", render_markdown(report))
                self.assertTrue(render_text(report).endswith(ISSUES_URL))


class TestText(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)
        self.report = self.machine.report()
        self.text = render_text(self.report)
        self.lines = self.text.splitlines()

    def test_it_ends_with_how_to_file_a_bug_report(self) -> None:
        self.assertEqual(self.lines[-1], BUG_REPORT_HINT)
        self.assertEqual(
            BUG_REPORT_HINT,
            "To report a bug, run `smart-drive doctor --report` and paste its output into a new issue: "
            "https://github.com/DuongNAD/smart-drive-os/issues/new/choose",
        )
        self.assertEqual(ISSUES_URL, "https://github.com/DuongNAD/smart-drive-os/issues/new/choose")

    def test_it_says_what_it_is_and_how_paths_are_shown(self) -> None:
        self.assertIn("read-only and offline", self.lines[0])
        self.assertIn("~", self.lines[1])
        self.assertIn("<user>", self.lines[1])

    def test_detected_agents_get_a_tick_the_others_a_dot_and_problems_an_exclamation_mark(self) -> None:
        ticks = [line for line in self.lines if line.startswith("  ✓ ")]
        dots = [line for line in self.lines if line.startswith("  · ")]
        self.assertEqual(len(ticks), 7)  # Claude Code, Claude desktop, Codex, Cursor, Windsurf, Antigravity, VS Code
        self.assertRegex(ticks[0], r"^  ✓ Claude Code +MCP: registered \(user scope, 2 projects; python -m smart_drive;")
        self.assertEqual(len(dots), 5)   # Gemini CLI, VS Code Insiders, VSCodium, JetBrains, Obsidian
        self.assertTrue(any("Gemini CLI" in line and "not found" in line for line in dots))
        self.assertEqual(len([line for line in self.lines if line.startswith("  ! ")]), 3)

    def test_the_facts_are_aligned_lines(self) -> None:
        width = len(max(ENVIRONMENT_LABELS, key=len))
        facts = self.lines[3:]  # after the two lines saying what this is, and a blank one
        for label in ENVIRONMENT_LABELS:
            line = next(line for line in facts if line.startswith(label))
            with self.subTest(label=label):
                self.assertEqual(line[:width].rstrip(), label)
                self.assertEqual(line[width:width + 2], "  ")
                self.assertNotEqual(line[width + 2:width + 3], " ")
        last = next(line for line in facts if line.startswith("Last checkup"))
        self.assertTrue(last.endswith("85/100 at 2026-10-21T10:00:00Z"))

    def test_the_journal_lines(self) -> None:
        self.assertIn("Move journal: 7 batches recorded; the newest 5, newest first", self.lines)
        self.assertIn("  2026-10-21T12:30:00  organize  4 moves  undone", self.lines)
        self.assertIn("  2026-10-21T13:05:00  undo      4 moves  undoes 2026-10-21T12:30:00", self.lines)
        self.assertIn("  2026-10-21T13:00:00  paths     1 move", self.lines, "one move, not '1 moves'")

    def test_without_problems_it_says_so(self) -> None:
        report = self.machine.report()
        report["problems"] = []
        self.assertIn("No problems noticed.", render_text(report))

    def test_the_batch_count_is_worded_for_one_few_and_many(self) -> None:
        self.assertEqual(doctor._batches_text(1, 1), "1 batch recorded")
        self.assertEqual(doctor._batches_text(3, 3), "3 batches recorded")
        self.assertEqual(doctor._batches_text(7, 5), "7 batches recorded; the newest 5")
        bare = self.machine.base / "bare"
        (bare / ".smart_drive").mkdir(parents=True)
        (bare / ".smart_drive" / "moves.jsonl").write_text(
            json.dumps({"time": "2026-10-21T10:00:00", "tool": "organize", "moves": [["a", "b"]]}) + "\n", encoding="utf-8"
        )
        report = self.machine.report(root=str(bare))
        self.assertIn("1 batch recorded:", render_markdown(report))
        self.assertIn("Move journal: 1 batch recorded, newest first", render_text(report).splitlines())

    def test_parts_wrap_at_the_width_and_are_never_cut(self) -> None:
        self.assertEqual(doctor._wrap_parts(["alpha", "beta" * 10, "gamma"], 20), ["alpha;", "beta" * 10 + ";", "gamma"])
        self.assertEqual(doctor._wrap_parts(["a", "b", "c"], 20), ["a; b; c"])
        self.assertEqual(doctor._wrap_parts(["one", "two", "three"], 10), ["one; two;", "three"])
        self.assertEqual(doctor._wrap_parts([], 20), [])


# ----------------------------------------------------------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------------------------------------------------------

class TestCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)
        self.root = str(self.machine.root)

    def test_the_parser_accepts_doctor_and_its_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        self.assertEqual((args.subcommand, args.report, args.json, args.root), ("doctor", False, False, None))
        self.assertTrue(parser.parse_args(["doctor", "--report"]).report)
        self.assertTrue(parser.parse_args(["doctor", "--json"]).json)
        self.assertEqual(parser.parse_args(["doctor", "--root", "X"]).root, "X")
        both = parser.parse_args(["doctor", "--report", "--json", "--root", "Y"])
        self.assertTrue(both.report and both.json)

    def test_the_help_says_what_it_shows(self) -> None:
        text = " ".join(build_parser().format_help().split())
        self.assertIn(
            "Read-only, offline environment report for bug reports: version, install method, Python, OS, the drive's "
            "filesystem, coding agents and their MCP entries; --report prints Markdown to paste into an issue",
            text,
        )

    def test_json_prints_parsable_json(self) -> None:
        code, out, err = run_main(["doctor", "--json", "--root", self.root])
        self.assertEqual((code, err), (0, ""))
        data = json.loads(out)
        self.assertEqual(list(data), SECTIONS)
        self.assertTrue(data["root"]["found"])
        self.assertEqual(data["root"]["found_by"], "explicit")
        self.assertEqual(data["checkup"]["score"], 85)
        self.assertEqual(len(data["agents"]), len(AGENT_NAMES))
        self.assertNotIn(SECRET_NAME, out)

    def test_report_prints_the_markdown_block(self) -> None:
        code, out, _ = run_main(["doctor", "--report", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(REPORT_COMMENT))
        self.assertIn("### Environment", out)
        self.assertNotIn(SECRET_NAME, out)

    def test_json_wins_over_report(self) -> None:
        code, out, _ = run_main(["doctor", "--report", "--json", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["report_version"], 1)

    def test_the_default_output_ends_with_how_to_report_a_bug(self) -> None:
        code, out, _ = run_main(["doctor", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertTrue(out.rstrip().endswith(ISSUES_URL))
        self.assertIn("`smart-drive doctor --report`", out.splitlines()[-1])
        self.assertNotIn(SECRET_NAME, out)

    def test_without_a_root_option_the_root_comes_from_the_environment(self) -> None:
        code, out, _ = run_main(["doctor", "--json"])
        self.assertEqual(code, 0)
        root = json.loads(out)["root"]
        self.assertTrue(root["found"])
        self.assertEqual(root["found_by"], "env")

    def test_the_real_environment_is_redacted_too(self) -> None:
        home = Path.home()
        inside = home / "sd_doctor_cli_root"
        inside.mkdir()
        self.addCleanup(shutil.rmtree, inside, ignore_errors=True)
        shown = os.sep.join(["~", "sd_doctor_cli_root"])
        for flags in ([], ["--report"], ["--json"]):
            with self.subTest(flags=flags):
                code, out, _ = run_main(["doctor", *flags, "--root", str(inside)])
                self.assertEqual(code, 0)
                for form in (str(home), home.as_posix(), str(home).replace("\\", "\\\\")):
                    self.assertNotIn(form, out)
                if "--json" in flags:
                    self.assertEqual(json.loads(out)["root"]["path"], shown)
                else:
                    self.assertIn(shown, out)

    def test_it_runs_as_a_module_in_its_own_process(self) -> None:
        inside = Path.home() / "sd_doctor_proc_root"
        inside.mkdir()
        self.addCleanup(shutil.rmtree, inside, ignore_errors=True)
        done = run_smart_drive_cli(["doctor", "--report", "--root", str(inside)], timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith(REPORT_COMMENT))
        self.assertNotIn(str(Path.home()), done.stdout)
        self.assertIn(os.sep.join(["~", "sd_doctor_proc_root"]), done.stdout)

    def test_a_console_that_cannot_show_a_character_gets_a_question_mark(self) -> None:
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
        with contextlib.redirect_stdout(console):
            code = CMD_DOCTOR.cmd_doctor(argparse.Namespace(root=self.root, report=False, json=False))
        console.flush()
        self.assertEqual(code, 0)
        text = raw.getvalue().decode("ascii")
        self.assertIn("? Claude Code", text, "the tick or the dot of an agent line")
        self.assertTrue(text.rstrip().endswith(ISSUES_URL))

    def test_a_report_that_cannot_be_built_is_the_only_failure(self) -> None:
        err = io.StringIO()
        with patch.object(CMD_DOCTOR, "collect_report", side_effect=RuntimeError(f"broke at {Path.home() / 'x'}")), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()) as out:
            code = CMD_DOCTOR.cmd_doctor(argparse.Namespace(root=None, report=False, json=False))
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("could not build its report (RuntimeError: broke at ~", err.getvalue())
        self.assertNotIn(str(Path.home()), err.getvalue())


FORBIDDEN_IMPORTS = ("socket", "ssl", "http", "urllib.request", "ftplib", "smtplib", "subprocess")
WRITING_CALLS = {
    "write_text", "write_bytes", "mkdir", "makedirs", "touch", "unlink", "rmdir", "rmtree", "rename", "copyfile",
    "copytree", "copy2", "chmod", "utime", "truncate", "symlink_to", "symlink", "os.remove", "os.replace",
    "shutil.move", "shutil.copy",
}


def forbidden_imports_in(source: str, forbidden: Tuple[str, ...]) -> List[str]:
    """Which of `forbidden` the source imports (a module below one counts as the one)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names: List[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        for name in names:
            found.update(bad for bad in forbidden if name == bad or name.startswith(bad + "."))
    return sorted(found)


def writing_calls_in(source: str) -> List[str]:
    """The calls in `source` that create, change or delete a file or folder (str.replace is not one of them)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            dotted = f"{func.value.id}.{func.attr}" if isinstance(func.value, ast.Name) else func.attr
            if dotted in WRITING_CALLS or func.attr in WRITING_CALLS:
                found.add(dotted)
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if isinstance(mode, ast.Constant) and any(flag in str(mode.value) for flag in "wax+"):
                found.add(f"open(mode={mode.value!r})")
    return sorted(found)


class TestNoNetworkAndNoWrites(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = FakeMachine(self)

    def test_no_form_of_the_command_touches_the_network(self) -> None:
        def refuse(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("doctor must not use the network")

        before = tree_state(self.machine.base)
        with patch("socket.socket.connect", refuse), patch("socket.create_connection", refuse), \
                patch("socket.getaddrinfo", refuse):
            for flags in (["--report"], ["--json"], []):
                for root in (["--root", str(self.machine.root)], []):
                    with self.subTest(flags=flags, root=root):
                        code, out, _ = run_main(["doctor", *flags, *root])
                        self.assertEqual(code, 0)
                        self.assertTrue(out.strip())
        self.assertEqual(tree_state(self.machine.base), before)

    def test_the_modules_import_nothing_that_could_reach_out(self) -> None:
        for relative in MODULES:
            with self.subTest(module=relative):
                self.assertEqual(forbidden_imports_in((ROOT / relative).read_text(encoding="utf-8"), FORBIDDEN_IMPORTS), [])

    def test_the_import_check_finds_what_it_looks_for(self) -> None:
        cases = {
            "import socket": ["socket"],
            "import http.client": ["http"],
            "from urllib import request": ["urllib.request"],
            "from urllib.request import urlopen": ["urllib.request"],
            "import subprocess as sp": ["subprocess"],
            "from smtplib import SMTP": ["smtplib"],
            "import ssl, ftplib": ["ftplib", "ssl"],
            "import json\nfrom urllib.parse import quote\nimport os.path": [],
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(forbidden_imports_in(source, FORBIDDEN_IMPORTS), expected)

    def test_the_modules_contain_no_call_that_writes(self) -> None:
        for relative in MODULES:
            with self.subTest(module=relative):
                self.assertEqual(writing_calls_in((ROOT / relative).read_text(encoding="utf-8")), [], "doctor is read-only")

    def test_the_write_check_finds_what_it_looks_for(self) -> None:
        cases = {
            "Path('x').write_text('y')": ["write_text"],
            "os.makedirs('x')": ["os.makedirs"],
            "os.replace('a', 'b')": ["os.replace"],
            "open('x', 'w')": ["open(mode='w')"],
            "open('x', mode='a+')": ["open(mode='a+')"],
            "open('x', 'r', encoding='utf-8')": [],
            "'a'.replace('b', 'c')": [],
            "shutil.disk_usage('x')": [],
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(writing_calls_in(source), expected)


class TestDocumentation(unittest.TestCase):
    def test_both_readmes_count_and_list_the_command_after_checkup(self) -> None:
        for name, heading, counted in (
            ("README.md", "## CLI reference", r"All (\d+) commands"),
            ("README_VN.md", "## Bảng Tra Cứu Lệnh CLI", r"Toàn bộ (\d+) lệnh"),
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            section = text.split(heading, 1)[1].split("</details>", 1)[0]
            with self.subTest(readme=name):
                rows = [line for line in section.splitlines() if line.startswith("| `")]
                commands = [row.split("`")[1] for row in rows]
                found = re.search(counted, section)
                self.assertIsNotNone(found, "the summary says how many commands there are")
                self.assertEqual(int(found.group(1)), len({command.split()[0] for command in commands}))
                self.assertEqual(commands[commands.index("checkup") + 1], "doctor")
                doctor_row = rows[commands.index("doctor")]
                for option in ("`--report`", "`--json`"):
                    self.assertIn(option, doctor_row)

    def test_the_changelog_announces_it_first_under_added(self) -> None:
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        # The section that announces it: [Unreleased] until the release, then the release that shipped it
        section = next(s for s in text.split("\n## [")[1:] if "- `smart-drive doctor`" in s)
        added = section.split("### Added", 1)[1].split("\n### ", 1)[0]
        first_bullet = added.strip().splitlines()[0]
        self.assertTrue(first_bullet.startswith("- `smart-drive doctor`"), first_bullet)


if __name__ == "__main__":
    unittest.main()
