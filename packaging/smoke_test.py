#!/usr/bin/env python3
"""smoke_test.py - runs an installed SmartDrive-OS the way a user and a coding agent do.

The unit tests import the source tree. This script never imports ``smart_drive``: it starts the program as a
separate process, so it can vouch for what a user actually gets from a release.

    python packaging/smoke_test.py --wheel DIST_DIR
        Installs the single wheel in DIST_DIR into a fresh virtual environment (``pip install --no-index``, so a
        dependency the wheel does not ship fails here and not on a user's machine) and checks the ``smart-drive``
        console script it installs. The wheel must also report the version of the code it carries.
    python packaging/smoke_test.py --uvx DIST_DIR
        Runs ``uvx smart-drive-os``, the way an MCP config can start the server with nothing installed, resolved
        offline from the single wheel in DIST_DIR. uv's cache, tool and Python folders live in the sandbox. The
        entry that ``mcp register`` writes must start ``uvx smart-drive-os mcp`` and must not name an interpreter
        inside uv's cache, which uv may delete.
    python packaging/smoke_test.py --pipx DIST_DIR
        ``pipx install`` of the single wheel into a sandboxed PIPX_HOME, then checks the ``smart-drive`` app that
        pipx puts in PIPX_BIN_DIR. The registered entry must run that pipx environment's Python with
        ``-m smart_drive mcp``.
    python packaging/smoke_test.py -- COMMAND [ARG...]
        Checks any other way to start the CLI, for example ``-- python -m smart_drive`` in a source checkout
        (put the checkout on PYTHONPATH: the environment's PYTHONPATH is kept in this mode).

uv and pipx are test tools: ``python -m pip install -r packaging/requirements-smoke.txt`` installs them.

Checks, each printing ``ok: ...``. The first failure prints the command, its exit code, stdout and stderr and
exits 1; a usage error exits 2.
    1. ``--help`` exits 0.
    2. ``status --json`` names the sandbox drive as its root.
    3. ``organize --dry-run --json`` plans moves and moves nothing.
    4. ``mcp register --workspace`` writes a ``.mcp.json`` with a launchable ``smart-drive`` entry.
    5. That entry, started the way an MCP client starts it (newline-delimited JSON-RPC 2.0 on stdin/stdout, from an
       unrelated working directory), answers ``initialize`` and ``tools/list`` and exits when stdin closes.
Installing a wheel or pipx app is a check of its own, and so are the extra rules for the entry in --uvx and --pipx.

The sandbox. ``mcp register`` writes ``.mcp.json`` but also the global config of every coding agent it detects
(Claude Desktop, ~/.claude.json, Cursor, Antigravity, Windsurf; all of them when it detects none), and ``organize``
reads those files to preview how the agents would follow its moves. So every command here runs with a temporary
home folder: HOME, USERPROFILE, APPDATA, LOCALAPPDATA and XDG_CONFIG_HOME point into it, and ANTIGRAVITY_HOME and
GEMINI_HOME are removed. Before the first program runs, Python is asked where it thinks the home folder is, and the
run stops if the answer is not the sandbox: a platform that ignored these variables must not be allowed to edit
somebody's real configs. uv and pipx get their own state folders inside the sandbox too. The sample drive that
``organize`` looks at lives there as well, and the whole sandbox is deleted at the end.

Standard library only, Python 3.9+.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# A hung server or a command waiting for input has to fail the run, not stall the CI job around it.
COMMAND_TIMEOUT = 180  # seconds per command

SERVER_NAME = "smart-drive"  # the console script, the MCP server's own name and its key in .mcp.json
DIST_NAME = "smart-drive-os"  # the distribution on PyPI; it also names the console script that uvx runs
REQUIRED_TOOLS = ("ssd_search", "ssd_auto_organize")
INSTALL_HINT = "python -m pip install -r packaging/requirements-smoke.txt"
# What `organize` creates when it really moves things (01_AI_Models ... 06_Archives_Storage); a dry run must not.
TAXONOMY_DIR = re.compile(r"^0[1-6]_")

# A drive with loose items for `organize` to plan: a model weight file and a project folder. The contents are
# tiny text; only the names and extensions matter to the classifier.
SAMPLE_FILES = {
    "My Project/package.json": "{}",
    "My Project/src/main.py": "print('hello')\n",
    "model.gguf": "not a real model\n",
    "Downloads/notes.pdf": "not a real pdf\n",
}

MCP_MESSAGES: List[Dict[str, Any]] = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]


@dataclass
class Result:
    """What one command did. ``returncode`` is None when it never started."""

    argv: List[str]
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False


class SmokeFailure(Exception):
    """A check failed. ``result`` is the command whose output explains it, when there is one."""

    def __init__(self, message: str, result: Optional[Result] = None) -> None:
        super().__init__(message)
        self.result = result


def format_command(argv: List[str]) -> str:
    """One copy-and-paste line for a command, quoted the way the platform's shell wants it."""
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def same_path(first: Any, second: Any) -> bool:
    """Whether two paths name the same place whatever their spelling (letter case on Windows, /var on macOS)."""
    if not isinstance(first, (str, os.PathLike)) or not isinstance(second, (str, os.PathLike)):
        return False
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def same_file(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def is_inside(path: str, folder: Path) -> bool:
    """Whether ``path`` (absolute) lies below ``folder``.

    The folders are resolved but the file itself is not: the python of a POSIX venv is a symlink to the base
    interpreter, and following it would put it outside the venv it belongs to.
    """
    inner = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
    outer = os.path.realpath(folder)
    return os.path.normcase(inner).startswith(os.path.normcase(outer) + os.sep)


def _text(data: Optional[bytes]) -> str:
    return (data or b"").decode("utf-8", errors="replace")


def kill_process_tree(process: "subprocess.Popen[bytes]") -> None:
    """Ends a command and everything it started.

    The ``python.exe`` of a Windows venv, pip's ``.exe`` launchers and ``uvx`` are stubs that start the real
    program as a child; killing only the stub leaves that child holding the output pipes, and reading them would hang.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # the command runs in its own process group
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def run_command(
    argv: List[str],
    *,
    env: Dict[str, str],
    cwd: Path,
    stdin: Optional[bytes] = None,
    timeout: float = COMMAND_TIMEOUT,
) -> Result:
    """Runs one command to the end and returns its output decoded as UTF-8 (undecodable bytes are replaced)."""
    extra: Dict[str, Any] = {}
    if os.name != "nt":
        extra["start_new_session"] = True  # so a timeout can kill the whole tree, see kill_process_tree
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,  # nothing may wait on a terminal
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            **extra,
        )
    except OSError as err:
        raise SmokeFailure(f"could not start the command: {err}", Result(argv, None, "", str(err))) from err
    timed_out = False
    try:
        out, err = process.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(process)
        try:
            out, err = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    return Result(argv, process.returncode, _text(out), _text(err), timed_out)


def require_ok(result: Result, what: str) -> None:
    if result.timed_out:
        raise SmokeFailure(f"{what}: no answer within {COMMAND_TIMEOUT} seconds", result)
    if result.returncode != 0:
        raise SmokeFailure(f"{what}: exit code {result.returncode}, expected 0", result)


def json_object(result: Result, what: str) -> Dict[str, Any]:
    try:
        data = json.loads(result.stdout)
    except ValueError as err:
        raise SmokeFailure(f"{what}: stdout is not JSON ({err})", result) from err
    if not isinstance(data, dict):
        raise SmokeFailure(f"{what}: stdout is JSON but not an object", result)
    return data


class Sandbox:
    """The folders of one run, all below one temporary folder: home, a sample drive, a workspace and a cwd."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.home = base / "home"
        self.drive = base / "drive"
        self.workspace = base / "ws"
        self.cwd = base / "cwd"
        self.appdata = self.home / "AppData" / "Roaming"
        self.localappdata = self.home / "AppData" / "Local"
        self.xdg_config = self.home / ".config"
        for folder in (self.home, self.appdata, self.localappdata, self.xdg_config, self.drive, self.workspace, self.cwd):
            folder.mkdir(parents=True, exist_ok=True)
        for name, text in SAMPLE_FILES.items():
            path = self.drive / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def env(self, *, keep_python_env: bool) -> Dict[str, str]:
        """The environment every command gets: this process' own, with the user's home replaced by the sandbox."""
        # SMART_DRIVE_* and KINGSTON_SSD_ROOT would change which drive the program works on or whether its MCP
        # server asks for a token. UV, UV_* and PIPX_* say where uv and pipx keep their state, whether they may
        # use the network and which Python they pick. The user's own settings must not decide what is tested.
        dropped = {"ANTIGRAVITY_HOME", "GEMINI_HOME", "KINGSTON_SSD_ROOT", "UV"}
        if not keep_python_env:
            # An installed copy has to be found where its installer put it: a PYTHONPATH that points at a checkout
            # would make the test pass on code that is not in the wheel.
            dropped |= {"PYTHONPATH", "PYTHONHOME"}
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in dropped and not key.upper().startswith(("SMART_DRIVE_", "UV_", "PIPX_"))
        }
        env.update(
            {
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),  # what Path.home() reads on Windows
                "APPDATA": str(self.appdata),
                "LOCALAPPDATA": str(self.localappdata),
                "XDG_CONFIG_HOME": str(self.xdg_config),
                # Without a marker folder the program probes the attached drives for a SmartDrive root; the result
                # must not depend on which drives this machine has.
                "SMART_DRIVE_NO_PROBE": "1",
            }
        )
        return env


class Smoke:
    """One run: the sandbox, the program under test and the number of checks that passed."""

    def __init__(self, sandbox: Sandbox, *, installed: bool) -> None:
        self.sandbox = sandbox
        self.installed = installed  # an installed copy: nothing may lean on PYTHONPATH
        self.base_env = sandbox.env(keep_python_env=not installed)  # a mode adds what its installer needs
        self.program: List[str] = []  # the start of the command line; set once the program exists
        # Extra rules for the registered MCP entry that only one way of installing has (--uvx, --pipx).
        self.entry_rules: Optional[Callable[[Dict[str, Any], Result], None]] = None
        self.checks = 0

    def run(
        self,
        argv: List[str],
        *,
        cwd: Optional[Path] = None,
        extra_env: Optional[Dict[str, str]] = None,
        stdin: Optional[bytes] = None,
    ) -> Result:
        env = dict(self.base_env)
        env.update(extra_env or {})
        return run_command(argv, env=env, cwd=cwd or self.sandbox.cwd, stdin=stdin)

    def cli(self, *args: str, cwd: Optional[Path] = None, extra_env: Optional[Dict[str, str]] = None) -> Result:
        return self.run(self.program + list(args), cwd=cwd, extra_env=extra_env)

    def ok(self, what: str) -> None:
        self.checks += 1
        print(f"ok: {what}", flush=True)


def confirm_sandboxed_home(smoke: Smoke) -> None:
    """Stops the run unless Python takes the sandbox for the user's home folder (see the module docstring)."""
    probe = "import os, pathlib; print(pathlib.Path.home()); print(os.path.expanduser('~'))"
    result = smoke.run([sys.executable, "-I", "-c", probe])
    require_ok(result, "asking Python for the home folder")
    seen = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(seen) != 2 or not all(same_path(path, smoke.sandbox.home) for path in seen):
        raise SmokeFailure(
            f"refusing to run: Python reports {seen} as the home folder instead of the sandbox "
            f"{str(smoke.sandbox.home)!r}, so the real configs of the coding agents could be edited",
            result,
        )


def find_wheel(dist_dir: Path) -> Path:
    """The only wheel in ``dist_dir``, which has to be absolute: the commands that get its path run elsewhere."""
    if not dist_dir.is_dir():
        raise SmokeFailure(f"{dist_dir} is not a folder")
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        found = ", ".join(wheel.name for wheel in wheels) or "none"
        raise SmokeFailure(f"expected exactly one .whl in {dist_dir}, found {len(wheels)}: {found}")
    return wheels[0]


def find_tool(name: str) -> Optional[str]:
    """The tool ``name`` that belongs to this Python: next to it first, then on PATH.

    A virtual environment that is not activated keeps its tools in its Scripts or bin folder, which is not on PATH.
    Looking there first also picks the version that requirements-smoke.txt pinned over any other copy on PATH.
    """
    folders = [sysconfig.get_path("scripts"), os.path.dirname(sys.executable)]
    search = os.pathsep.join([folder for folder in folders if folder] + [os.environ.get("PATH", "")])
    found = shutil.which(name, path=search)
    return os.path.abspath(found) if found else None


def find_uvx() -> str:
    found = find_tool("uvx")
    if found is None:
        raise SmokeFailure(f"uvx was not found next to this Python or on PATH; install the test tools with: {INSTALL_HINT}")
    return found


def find_pipx() -> List[str]:
    """How to run pipx: as a module of this Python when it is installed there, else the pipx on PATH."""
    try:
        if importlib.util.find_spec("pipx") is not None:
            return [sys.executable, "-m", "pipx"]
    except (ImportError, ValueError):
        pass
    found = find_tool("pipx")
    if found is None:
        raise SmokeFailure(
            "pipx is not installed for this Python and was not found on PATH (it needs Python 3.11 or newer); "
            f"install the test tools with: {INSTALL_HINT}"
        )
    return [found]


def install_wheel(smoke: Smoke, wheel: Path) -> List[str]:
    """Installs the wheel into a fresh venv and returns the command line of its console script."""
    venv_dir = smoke.sandbox.base / "venv"
    scripts = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    python = scripts / f"python{suffix}"
    console_script = scripts / f"{SERVER_NAME}{suffix}"

    require_ok(smoke.run([sys.executable, "-m", "venv", str(venv_dir)]), "creating a virtual environment")
    require_ok(
        smoke.run(
            [str(python), "-m", "pip", "install", "--no-index", "--no-cache-dir", "--disable-pip-version-check", str(wheel)]
        ),
        f"pip install {wheel.name}",
    )
    if not console_script.is_file():
        raise SmokeFailure(f"the wheel installed no {console_script.name} console script into {scripts}")

    # -I: neither PYTHONPATH nor the current directory may put another copy of the package ahead of the installed one.
    probe = (
        "import importlib.metadata, json, smart_drive; "
        "print(json.dumps({'dist': importlib.metadata.version('smart-drive-os'), "
        "'module': smart_drive.__version__, 'file': smart_drive.__file__}))"
    )
    result = smoke.run([str(python), "-I", "-c", probe])
    require_ok(result, "importing smart_drive from the installed wheel")
    info = json_object(result, "the version probe")
    if info.get("dist") != info.get("module"):
        raise SmokeFailure(
            f"the wheel's metadata says smart-drive-os {info.get('dist')} but smart_drive.__version__ is {info.get('module')}",
            result,
        )
    imported_from = info.get("file")
    if not isinstance(imported_from, str) or not is_inside(imported_from, venv_dir):
        raise SmokeFailure(f"smart_drive was imported from {imported_from!r}, not from the new virtual environment", result)
    smoke.ok(f"the wheel installs into a fresh virtual environment as smart-drive-os {info['dist']}, the version of smart_drive")
    return [str(console_script)]


def setup_uvx(smoke: Smoke, uvx: str, wheel: Path) -> List[str]:
    """Points uv at the sandbox and at the wheel, and returns the command line of ``uvx smart-drive-os``."""
    base = smoke.sandbox.base
    uv_cache = base / "uv-cache"
    smoke.base_env.update(
        {
            # uv keeps its cache, its installed tools and the Pythons it downloads in the user's profile.
            "UV_CACHE_DIR": str(uv_cache),
            "UV_TOOL_DIR": str(base / "uv-tools"),
            "UV_TOOL_BIN_DIR": str(base / "uv-bin"),
            "UV_PYTHON_INSTALL_DIR": str(base / "uv-python"),
            # uv looks for its own configuration through the profile API, which USERPROFILE does not redirect.
            "UV_NO_CONFIG": "1",
            # Offline and without an index: the only `smart-drive-os` uv can find is the wheel under test, not the
            # last release on PyPI.
            "UV_OFFLINE": "1",
            "UV_NO_INDEX": "1",
            "UV_FIND_LINKS": str(wheel.parent),  # absolute: main() resolved DIST_DIR, and uv runs in the sandbox
            # The Python that runs this script, and no download of another one.
            "UV_PYTHON": sys.executable,
            "UV_PYTHON_PREFERENCE": "only-system",
        }
    )
    print(f"smoke test of {wheel.name} through uvx: {uvx}", flush=True)

    def entry_rules(entry: Dict[str, Any], result: Result) -> None:
        command, args = entry["command"], entry["args"]
        # The bug this guards against: `mcp register` recorded the python.exe inside uv's cache, a folder uv may
        # delete (`uv cache clean`, `uv cache prune`), and the agent's server vanished with it.
        if os.path.isabs(command) and is_inside(command, uv_cache):
            raise SmokeFailure(
                f"the registered command {command!r} lies inside uv's cache {str(uv_cache)!r}, which uv may delete", result
            )
        if args[-2:] != [DIST_NAME, "mcp"]:
            raise SmokeFailure(f"the registered args {args!r} do not end with {[DIST_NAME, 'mcp']!r}", result)
        runs_uvx = same_file(command, uvx)
        runs_uv = Path(command).stem.lower() == "uv" and args[:2] == ["tool", "run"]
        if not (runs_uvx or runs_uv):
            raise SmokeFailure(
                f"the registered command {command!r} is neither the uvx that ran ({uvx}) nor a uv with `tool run`", result
            )
        smoke.ok(f"the registered entry starts {Path(command).name} {' '.join(args)}, not an interpreter in uv's cache")

    smoke.entry_rules = entry_rules
    return [uvx, DIST_NAME]


def setup_pipx(smoke: Smoke, pipx: List[str], wheel: Path) -> List[str]:
    """Installs the wheel with pipx into the sandbox and returns the command line of the app pipx exposes."""
    base = smoke.sandbox.base
    pipx_home, pipx_bin = base / "pipx-home", base / "pipx-bin"
    smoke.base_env.update(
        {
            "PIPX_HOME": str(pipx_home),
            "PIPX_BIN_DIR": str(pipx_bin),
            "PIPX_MAN_DIR": str(base / "pipx-man"),
            "PIPX_DEFAULT_PYTHON": sys.executable,
            # pipx runs pip, which would cache what it downloads in the user's profile.
            "PIP_CACHE_DIR": str(base / "pip-cache"),
        }
    )
    print(f"smoke test of {wheel.name} installed by pipx: {format_command(pipx)}", flush=True)
    require_ok(smoke.run(pipx + ["install", str(wheel)]), f"pipx install {wheel.name}")
    app = pipx_bin / ("smart-drive.exe" if os.name == "nt" else "smart-drive")
    if not app.exists():
        raise SmokeFailure(f"pipx install put no {app.name} into {pipx_bin}")
    smoke.ok(f"pipx installs the wheel and puts {app.name} into its bin folder")

    venv = pipx_home / "venvs" / DIST_NAME

    def entry_rules(entry: Dict[str, Any], result: Result) -> None:
        command, args = entry["command"], entry["args"]
        if not os.path.isabs(command) or not is_inside(command, venv):
            raise SmokeFailure(f"the registered command {command!r} is not the Python of the pipx environment {str(venv)!r}", result)
        if args != ["-m", "smart_drive", "mcp"]:
            raise SmokeFailure(f"the registered args {args!r} are not {['-m', 'smart_drive', 'mcp']!r}", result)
        smoke.ok("the registered entry runs the pipx environment's Python with -m smart_drive mcp")

    smoke.entry_rules = entry_rules
    return [str(app)]


def check_help(smoke: Smoke) -> None:
    result = smoke.cli("--help")
    require_ok(result, "--help")
    if SERVER_NAME not in result.stdout:
        raise SmokeFailure("--help did not print the usage text", result)
    smoke.ok("--help exits 0 and prints the usage text")


def check_status(smoke: Smoke) -> None:
    drive = smoke.sandbox.drive
    # SMART_DRIVE_ROOT is how a launcher or an MCP client points the program at a drive; the cwd is the drive too.
    result = smoke.cli("status", "--json", cwd=drive, extra_env={"SMART_DRIVE_ROOT": str(drive)})
    require_ok(result, "status --json")
    report = json_object(result, "status --json")
    if not same_path(report.get("root"), drive):
        raise SmokeFailure(f"status --json reports the root {report.get('root')!r}, expected {str(drive)!r}", result)
    smoke.ok("status --json reports the sandbox drive as its root")


def check_organize_dry_run(smoke: Smoke) -> None:
    drive = smoke.sandbox.drive
    result = smoke.cli("organize", "--dry-run", "--root", str(drive), "--json")
    require_ok(result, "organize --dry-run")
    plan = json_object(result, "organize --dry-run")
    if plan.get("status") != "dry_run":
        raise SmokeFailure(f"organize --dry-run reports the status {plan.get('status')!r}, expected 'dry_run'", result)
    actions = plan.get("planned_actions")
    if not isinstance(actions, list) or not actions:
        raise SmokeFailure("organize --dry-run planned no move for a drive with a model file and a project folder", result)
    missing = [name for name in SAMPLE_FILES if not (drive / name).is_file()]
    if missing:
        raise SmokeFailure(f"organize --dry-run moved or removed sample files: {', '.join(missing)}", result)
    created = sorted(name for name in os.listdir(drive) if TAXONOMY_DIR.match(name))
    if created:
        raise SmokeFailure(f"organize --dry-run created taxonomy folders: {', '.join(created)}", result)
    smoke.ok(f"organize --dry-run plans {len(actions)} move(s) and moves nothing")


def check_mcp_register(smoke: Smoke) -> Dict[str, Any]:
    """Registers the workspace and returns the ``smart-drive`` entry that was written."""
    workspace = smoke.sandbox.workspace
    result = smoke.cli("mcp", "register", "--workspace", "--target-dir", str(workspace), "--json")
    require_ok(result, "mcp register --workspace")
    config_path = workspace / ".mcp.json"
    if not config_path.is_file():
        raise SmokeFailure(f"mcp register --workspace wrote no {config_path.name} into {workspace}", result)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except ValueError as err:
        raise SmokeFailure(f"{config_path} is not valid JSON ({err})", result) from err
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    entry = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        raise SmokeFailure(f"{config_path} has no mcpServers[{SERVER_NAME!r}] entry: {config!r}", result)
    command, args, env = entry.get("command"), entry.get("args"), entry.get("env")
    if not isinstance(command, str) or not command:
        raise SmokeFailure(f"the entry's \"command\" is not a string: {entry!r}", result)
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise SmokeFailure(f"the entry's \"args\" is not a list of strings: {entry!r}", result)
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise SmokeFailure(f"the entry's \"env\" is not an object of strings: {entry!r}", result)
    if smoke.installed and "PYTHONPATH" in env:
        raise SmokeFailure(f"an installed copy registered itself with a PYTHONPATH: {entry!r}", result)
    note = " (no PYTHONPATH)" if smoke.installed else ""
    smoke.ok(f"mcp register --workspace writes a smart-drive entry with command, args and env{note}")
    if smoke.entry_rules is not None:
        smoke.entry_rules(entry, result)
    return entry


def parse_replies(stdout: str) -> Dict[Any, Dict[str, Any]]:
    """The JSON-RPC replies in a server's stdout by id; lines that are not a JSON object are ignored."""
    replies: Dict[Any, Dict[str, Any]] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and "id" in message:
            replies[message["id"]] = message
    return replies


def check_mcp_server(smoke: Smoke, entry: Dict[str, Any]) -> None:
    """Starts the registered entry like an MCP client and talks to it over stdio."""
    # A client starts its servers from a directory that has nothing to do with the program.
    client_cwd = Path(tempfile.mkdtemp(prefix="mcp-client-", dir=str(smoke.sandbox.base)))
    stdin = "".join(json.dumps(message) + "\n" for message in MCP_MESSAGES).encode("utf-8")
    argv = [entry["command"], *entry["args"]]
    result = smoke.run(argv, cwd=client_cwd, extra_env=entry["env"], stdin=stdin)
    require_ok(result, "the registered MCP server")
    replies = parse_replies(result.stdout)

    initialized = replies.get(1)
    result_1 = initialized.get("result") if initialized else None
    server_info = result_1.get("serverInfo") if isinstance(result_1, dict) else None
    name = server_info.get("name") if isinstance(server_info, dict) else None
    if name != SERVER_NAME:
        raise SmokeFailure(f"the reply to initialize names the server {name!r}, expected {SERVER_NAME!r}: {initialized!r}", result)

    listed = replies.get(2)
    result_2 = listed.get("result") if listed else None
    tools = result_2.get("tools") if isinstance(result_2, dict) else None
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)] if isinstance(tools, list) else []
    absent = [tool for tool in REQUIRED_TOOLS if tool not in names]
    if absent:
        raise SmokeFailure(f"the reply to tools/list lacks {', '.join(absent)}; it lists {names!r}", result)
    smoke.ok(f"the registered entry starts an MCP server that answers initialize and lists {' and '.join(REQUIRED_TOOLS)}")


def run_checks(smoke: Smoke) -> None:
    check_help(smoke)
    check_status(smoke)
    check_organize_dry_run(smoke)
    entry = check_mcp_register(smoke)
    check_mcp_server(smoke, entry)


def _make_writable_and_retry(function: Callable[[str], Any], path: str, _error: Any) -> None:
    """rmtree's error handler: a read-only file or folder is what makes a delete fail on Windows (uv's cache has some)."""
    try:
        os.chmod(path, stat.S_IRWXU)
        function(path)
    except FileNotFoundError:
        pass  # already gone, which is the goal


def remove_tree(folder: str) -> None:
    """Deletes the sandbox without ever failing a run that passed.

    Windows can keep a file of a program that just exited locked for a moment (a virus scanner, a launcher stub),
    and Python 3.9's TemporaryDirectory has neither a retry nor ignore_cleanup_errors, so this retries by hand.
    """
    error: Optional[OSError] = None
    for _ in range(5):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(folder, onexc=_make_writable_and_retry)  # onerror is deprecated there
            else:
                shutil.rmtree(folder, onerror=_make_writable_and_retry)
            return
        except OSError as err:
            error = err
            time.sleep(1)
    print(f"warning: could not remove the sandbox {folder}: {error}", file=sys.stderr)


def report_failure(failure: SmokeFailure) -> None:
    out = sys.stderr
    print(f"FAILED: {failure}", file=out)
    result = failure.result
    if result is None:
        return
    print(f"command: {format_command(result.argv)}", file=out)
    if result.timed_out:
        print(f"exit code: none, the command was killed after {COMMAND_TIMEOUT} seconds", file=out)
    else:
        print(f"exit code: {result.returncode if result.returncode is not None else 'none, the command did not start'}", file=out)
    print("--- stdout ---", file=out)
    print(result.stdout.rstrip(), file=out)
    print("--- stderr ---", file=out)
    print(result.stderr.rstrip(), file=out)
    print("--- end ---", file=out, flush=True)


def resolve_command(command: List[str]) -> List[str]:
    """Makes the program absolute: every command runs from a sandbox folder, where a relative path would not resolve."""
    found = shutil.which(command[0])
    if found is None:
        raise SmokeFailure(f"{command[0]!r} is not an executable file or a command on PATH")
    return [os.path.abspath(found)] + command[1:]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smoke_test.py",
        usage="%(prog)s (--wheel DIST_DIR | --uvx DIST_DIR | --pipx DIST_DIR | -- COMMAND [ARG...])",
        description="Start an installed SmartDrive-OS like a user and a coding agent do, and check that it works.",
        epilog="After `--`: the command that starts the CLI, for example `python -m smart_drive` in a checkout.\n"
        "\n"
        "Examples:\n"
        "  python packaging/smoke_test.py --wheel dist\n"
        "  python packaging/smoke_test.py --uvx dist\n"
        "  python packaging/smoke_test.py --pipx dist\n"
        "  PYTHONPATH=. python packaging/smoke_test.py -- python -m smart_drive\n"
        "\n"
        f"--uvx and --pipx need the test tools: {INSTALL_HINT}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--wheel",
        metavar="DIST_DIR",
        help="install the single .whl in DIST_DIR into a fresh virtual environment and check its console script",
    )
    parser.add_argument(
        "--uvx",
        metavar="DIST_DIR",
        help="run `uvx smart-drive-os` offline from the single .whl in DIST_DIR (needs uv, see the examples)",
    )
    parser.add_argument(
        "--pipx",
        metavar="DIST_DIR",
        help="`pipx install` the single .whl in DIST_DIR and check the app (needs pipx and Python 3.11+, see the examples)",
    )
    # Only there to notice a command that was not put after `--` (main() splits the real one off first).
    parser.add_argument("command", nargs="*", help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # The output carries whatever the program printed, so it must never die on an encoding error, and a
    # consumer that decodes it as UTF-8 (the tests, CI log viewers) must see what was written.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass

    args_in = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    if "--" in args_in:  # everything after the first `--` is the command; argparse would try to read its options
        split = args_in.index("--")
        args_in, command = args_in[:split], args_in[split + 1 :]
    parser = build_parser()
    args = parser.parse_args(args_in)
    if args.command:
        parser.error("put the command after `--`, for example: -- python -m smart_drive")
    # Every command runs in a sandbox folder, so a relative DIST_DIR (`--wheel dist`, as the docs and the release
    # workflow write it) is made absolute here, once, against the folder this script was started from. The wheel,
    # UV_FIND_LINKS and the paths given to pip and pipx all derive from it. (The program of `-- COMMAND` gets the
    # same treatment in resolve_command.)
    folders = (("wheel", args.wheel), ("uvx", args.uvx), ("pipx", args.pipx))
    chosen = {mode: Path(os.path.abspath(folder)) for mode, folder in folders if folder}
    if len(chosen) + bool(command) != 1:
        parser.error("give exactly one of --wheel DIST_DIR, --uvx DIST_DIR, --pipx DIST_DIR or -- COMMAND [ARG...]")
    mode = next(iter(chosen), "command")

    sandbox_dir = ""
    checks = 0
    try:
        # Everything that can be refused is looked up before the first folder is created.
        wheel = find_wheel(chosen[mode]) if mode != "command" else None
        uvx = find_uvx() if mode == "uvx" else ""
        pipx = find_pipx() if mode == "pipx" else []
        program = resolve_command(command) if mode == "command" else []
        sandbox_dir = tempfile.mkdtemp(prefix="smart-drive-smoke-")
        # realpath: the program prints resolved paths (macOS /var -> /private/var, Windows 8.3 names).
        smoke = Smoke(Sandbox(Path(os.path.realpath(sandbox_dir))), installed=mode != "command")
        confirm_sandboxed_home(smoke)
        if wheel is None:
            smoke.program = program
            print(f"smoke test of: {format_command(program)}", flush=True)
        elif mode == "wheel":
            print(f"smoke test of {wheel.name} in a fresh virtual environment", flush=True)
            smoke.program = install_wheel(smoke, wheel)
        elif mode == "uvx":
            smoke.program = setup_uvx(smoke, uvx, wheel)
        else:
            smoke.program = setup_pipx(smoke, pipx, wheel)
        run_checks(smoke)
        checks = smoke.checks
    except SmokeFailure as failure:
        report_failure(failure)
        return 1
    finally:
        if sandbox_dir:
            remove_tree(sandbox_dir)
    print(f"smoke test passed: {checks} checks", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
