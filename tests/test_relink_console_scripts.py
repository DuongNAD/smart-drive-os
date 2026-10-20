"""tests/test_relink_console_scripts.py - relink repoints the console scripts of a moved virtual environment.

pip writes the path of the venv's python.exe into every console script: into the shebang line of a launcher
(`pip.exe` = launcher stub + `#!<python path>` line + a zip archive with __main__.py) or of a text script. After a
move those scripts start the old, missing path. relink rewrites just that path, keeps a backup of each file, and
leaves alone whatever it cannot parse.

100% isolated: every test uses a fake home and fake AppData inside a temporary directory and never reads or
writes real coding agent configuration files on the host machine.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import venv
import zipfile
from typing import Dict, List, Optional, Sequence, Tuple
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.relink import BACKUP_SUFFIX, PathMapper, RelinkReport, relink
import smart_drive.core.relink as relink_core
from tests.helpers import SmartDriveTestCase

HAS_ENSUREPIP = importlib.util.find_spec("ensurepip") is not None

# A launcher stub is a PE file that ends in whatever its linker left; a decoy `#!` inside it must not confuse relink
STUB = b"MZ\x90\x00" + b"\x00" * 64 + b"decoy: #!/usr/bin/env python\x00" + b"\x01\x02" * 40 + b"\x00end-of-stub"
MAIN = b"# -*- coding: utf-8 -*-\nimport sys\nif __name__ == '__main__':\n    sys.exit(0)\n"


def make_launcher(
    interpreter: str,
    quote: bool = False,
    eol: bytes = b"\n",
    stub: bytes = STUB,
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    """A pip/distlib console-script launcher: stub, `#!<interpreter>` line, zip archive with __main__.py."""
    path = interpreter.encode("utf-8")
    line = b"#!" + (b'"' + path + b'"' if quote else path) + eol
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression) as zf:
        zf.writestr("__main__.py", MAIN)
    return stub + line + archive.getvalue()


def split_launcher(path: Path) -> Tuple[bytes, bytes, bytes, bytes]:
    """(stub, `#!` line without its ending, the ending, zip archive) of a launcher file, found with zipfile."""
    data = path.read_bytes()
    with zipfile.ZipFile(path) as archive:  # it must open: that is how the launcher itself runs __main__.py
        zip_start = min(info.header_offset for info in archive.infolist())
        assert archive.read("__main__.py") == MAIN
    head = data[:zip_start]
    body = head.rstrip(b"\r\n")
    start = body.rindex(b"#!")
    return body[:start], body[start:], head[len(body) :], data[zip_start:]


class TestRelinkConsoleScripts(SmartDriveTestCase):
    """The console scripts of a moved venv follow it; what cannot be rewritten safely is left alone and reported."""

    def setUp(self) -> None:
        super().setUp()
        if " " in str(self.test_dir):
            self.addCleanup(self._temp_dir.cleanup)  # tearDown does not run for a skipped setUp
            self.skipTest("these tests decide where a path gets a space; the temp folder must not have one")
        self.fake_home = self.test_dir / "fake_home"
        self.fake_appdata = self.fake_home / "AppData" / "Roaming"
        self.fake_temp = self.test_dir / "sys_temp"
        self.fake_home.mkdir(parents=True, exist_ok=True)
        self.fake_appdata.mkdir(parents=True, exist_ok=True)
        self.fake_temp.mkdir(parents=True, exist_ok=True)
        self._proc_patcher = patch("smart_drive.core.relink._running_processes", return_value=set())
        self._proc_patcher.start()
        self._temp_patcher = patch("smart_drive.core.relink.tempfile.gettempdir", return_value=str(self.fake_temp))
        self._temp_patcher.start()
        # The suite itself may run inside a coding agent, which --wait would then skip
        self._caller_patcher = patch("smart_drive.cli.cmd_relink.calling_agents", return_value=set())
        self._caller_patcher.start()

    def tearDown(self) -> None:
        if hasattr(self, "_caller_patcher"):
            self._caller_patcher.stop()
        if hasattr(self, "_temp_patcher"):
            self._temp_patcher.stop()
        if hasattr(self, "_proc_patcher"):
            self._proc_patcher.stop()
        super().tearDown()

    def _cli_env(self) -> Dict[str, str]:
        return {
            "USERPROFILE": str(self.fake_home),
            "HOME": str(self.fake_home),
            "APPDATA": str(self.fake_appdata),
            "LOCALAPPDATA": str(self.fake_home / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(self.fake_appdata),
            "TMP": str(self.fake_temp),
            "TEMP": str(self.fake_temp),
            "TMPDIR": str(self.fake_temp),
            "CONSTELLATION_HOME": "",
            "ANTIGRAVITY_HOME": "",
            "GEMINI_HOME": "",
        }

    def _cli(self, argv: List[str]) -> Tuple[int, str]:
        out = io.StringIO()
        with patch.dict(os.environ, self._cli_env()), contextlib.redirect_stdout(out):
            rc = cli_main(argv)
        return rc, out.getvalue()

    def _relink(self, moves: Sequence[Tuple[str, str]], dry_run: bool = False) -> RelinkReport:
        return relink(list(moves), dry_run=dry_run, home=self.fake_home, app_data=self.fake_appdata)

    @staticmethod
    def _venv(venv_dir: Path) -> Path:
        """A virtualenv skeleton; returns its Scripts folder."""
        (venv_dir / "Scripts").mkdir(parents=True)
        (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        return venv_dir / "Scripts"

    @staticmethod
    def _python(venv_dir: Path) -> str:
        return str(venv_dir / "Scripts" / "python.exe")

    def _stale_warnings(self, report: RelinkReport) -> List[str]:
        return [w for w in report.warnings if "could not be repointed" in w]

    def test_01_the_launchers_of_a_moved_venv_are_repointed_and_backed_up(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        venv_dir = new_proj / ".venv"
        scripts = self._venv(venv_dir)
        old_python = self._python(old_proj / ".venv")
        new_python = self._python(venv_dir)
        pip = make_launcher(old_python)
        tool = make_launcher(old_python, eol=b"\r\n", compression=zipfile.ZIP_DEFLATED)
        (scripts / "pip.exe").write_bytes(pip)
        (scripts / "tool.exe").write_bytes(tool)
        (scripts / "python.exe").write_bytes(STUB)  # the venv's own launcher holds no path: nothing to do
        moves = [(str(old_proj), str(new_proj))]

        first = self._relink(moves)

        self.assertEqual(first.venvs, [str(venv_dir)])  # no .pth file in it: the console scripts alone list it
        self.assertEqual(first.console_scripts, {str(venv_dir): 2})
        self.assertEqual(first.agents, {"Python console scripts": 2})
        self.assertEqual(first.warnings, [])
        self.assertEqual(first.errors, [])
        stub, line, ending, archive = split_launcher(scripts / "pip.exe")
        self.assertEqual((stub, line, ending), (STUB, b"#!" + new_python.encode(), b"\n"))
        self.assertEqual(archive, pip[pip.index(b"PK\x03\x04") :])  # the archive is byte for byte the same
        stub, line, ending, archive = split_launcher(scripts / "tool.exe")
        self.assertEqual((stub, line, ending), (STUB, b"#!" + new_python.encode(), b"\r\n"))
        self.assertEqual(archive, tool[tool.index(b"PK\x03\x04") :])
        self.assertEqual((scripts / "python.exe").read_bytes(), STUB)
        self.assertEqual((scripts / f"pip.exe{BACKUP_SUFFIX}").read_bytes(), pip)
        self.assertEqual((scripts / f"tool.exe{BACKUP_SUFFIX}").read_bytes(), tool)
        self.assertFalse((scripts / f"python.exe{BACKUP_SUFFIX}").exists())
        self.assertFalse(list(scripts.glob("*.tmp")))

        written = {path: path.read_bytes() for path in scripts.iterdir()}
        second = self._relink(moves)
        self.assertEqual(second.venvs, [])
        self.assertEqual(second.updated, [])
        self.assertEqual(second.warnings, [])
        self.assertFalse(second.changed)
        self.assertEqual({path: path.read_bytes() for path in scripts.iterdir()}, written)

    def test_02_dry_run_lists_what_it_would_repoint_and_agrees_with_the_real_run(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        scripts = self._venv(old_proj / ".venv")
        launcher = make_launcher(self._python(old_proj / ".venv"))
        for name in ("pip.exe", "pip3.exe", "httpx.exe"):
            (scripts / name).write_bytes(launcher)
        moves = [(str(old_proj), str(new_proj))]
        new_scripts = new_proj / ".venv" / "Scripts"

        preview = self._relink(moves, dry_run=True)  # the folder is still where it was
        self.assertEqual(preview.console_scripts, {str(new_proj / ".venv"): 3})
        self.assertEqual(
            sorted(entry["file"] for entry in preview.updated),
            sorted(str(new_scripts / name) for name in ("pip.exe", "pip3.exe", "httpx.exe")),
        )
        self.assertEqual({path.name for path in scripts.iterdir()}, {"pip.exe", "pip3.exe", "httpx.exe"})  # no backups
        self.assertTrue(all(path.read_bytes() == launcher for path in scripts.glob("*.exe")))  # nothing written

        new_proj.parent.mkdir(parents=True)
        os.rename(old_proj, new_proj)
        real = self._relink(moves)
        self.assertEqual(real.console_scripts, preview.console_scripts)
        self.assertEqual(sorted(e["file"] for e in real.updated), sorted(e["file"] for e in preview.updated))

        dry, again = self._relink(moves, dry_run=True), self._relink(moves)
        for report in (dry, again):
            self.assertEqual((report.venvs, report.console_scripts, report.updated, report.warnings), ([], {}, [], []))
            self.assertFalse(report.changed)
        self.assertEqual({**dry.to_dict(), "dry_run": False}, again.to_dict())

    def test_03_a_quoted_shebang_and_paths_with_spaces_stay_valid(self) -> None:
        cases = (
            # (old folder, new folder, quote the old shebang, the new shebang is quoted)
            ("my proj", Path("03 Dev") / "my proj", True, True),  # spaces before and after: the quotes stay
            ("proj", Path("My Projects") / "proj", False, True),  # a space only after the move: quotes are added
            ("my proj", Path("03_Dev") / "my proj", True, True),  # quoted before: quoted after
            ("proj", Path("03_Dev") / "proj", False, False),  # no space anywhere: nothing to quote
        )
        for index, (old_name, new_name, quote, quoted_after) in enumerate(cases):
            with self.subTest(old=old_name, new=str(new_name), quote=quote):
                old_proj = self.test_dir / f"case{index}" / old_name
                new_proj = self.test_dir / f"case{index}" / new_name
                scripts = self._venv(new_proj / ".venv")
                (scripts / "pip.exe").write_bytes(make_launcher(self._python(old_proj / ".venv"), quote=quote))

                report = self._relink([(str(old_proj), str(new_proj))])

                new_python = self._python(new_proj / ".venv")
                expected = f'"{new_python}"' if quoted_after else new_python
                self.assertEqual(split_launcher(scripts / "pip.exe")[1], b"#!" + expected.encode())
                self.assertEqual(report.console_scripts, {str(new_proj / ".venv"): 1})
                self.assertEqual(report.warnings, [])

    def test_04_a_launcher_of_another_python_is_left_alone(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        scripts = self._venv(new_proj / ".venv")
        elsewhere = make_launcher(str(self.test_dir / "global_python" / "python.exe"))
        relative = make_launcher("<launcher_dir>\\python.exe")  # distlib's own way to follow a move
        (scripts / "global.exe").write_bytes(elsewhere)
        (scripts / "relative.exe").write_bytes(relative)

        report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual((report.venvs, report.console_scripts, report.updated, report.warnings), ([], {}, [], []))
        self.assertEqual((scripts / "global.exe").read_bytes(), elsewhere)
        self.assertEqual((scripts / "relative.exe").read_bytes(), relative)
        self.assertEqual(sorted(path.name for path in scripts.iterdir()), ["global.exe", "relative.exe"])  # no backups

    def test_05_a_launcher_that_cannot_be_parsed_is_left_alone_and_warned_about(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        venv_dir = new_proj / ".venv"
        scripts = self._venv(venv_dir)
        old_python = self._python(old_proj / ".venv")
        good = make_launcher(old_python)
        crc = bytearray(good)
        crc[good.index(b"import sys")] ^= 0xFF  # a stored byte of __main__.py no longer matches its checksum
        central = bytearray(good)
        central[good.rindex(b"PK\x01\x02") + 3] = 0x03  # not a central directory header any more
        broken = {
            "truncated.exe": good[:-10],  # the end of the archive is missing
            "central.exe": bytes(central),
            "crc.exe": bytes(crc),
            "latin1.exe": make_launcher(old_python).replace(old_python.encode(), old_python.encode() + b"\xff"),
            # uv writes its launcher in another format: the archive is followed by the python path and a trailer
            "uv.exe": STUB + good[good.index(b"PK\x03\x04") :] + old_python.encode() + struct.pack("<I", len(old_python)) + b"UVSC",
        }
        for name, content in broken.items():
            (scripts / name).write_bytes(content)
        (scripts / "fine.exe").write_bytes(good)
        moves = [(str(old_proj), str(new_proj))]

        report = self._relink(moves)

        for name, content in broken.items():
            self.assertEqual((scripts / name).read_bytes(), content, name)
            self.assertFalse((scripts / f"{name}{BACKUP_SUFFIX}").exists(), name)
        self.assertEqual(split_launcher(scripts / "fine.exe")[1], b"#!" + self._python(venv_dir).encode())
        self.assertEqual(report.console_scripts, {str(venv_dir): 1})
        warnings = self._stale_warnings(report)
        self.assertEqual(len(warnings), 1, report.warnings)
        self.assertIn(str(venv_dir), warnings[0])
        self.assertIn("console scripts", warnings[0])
        self.assertIn("may still start the old path", warnings[0])
        for name in broken:
            self.assertIn(name, warnings[0])
        self.assertNotIn("fine.exe", warnings[0])

        # Run again: the good launcher is done; the others are still reported, and still untouched
        again = self._relink(moves)
        self.assertEqual((again.venvs, again.console_scripts, again.updated), ([], {}, []))
        self.assertEqual(len(self._stale_warnings(again)), 1)
        self.assertEqual({name: (scripts / name).read_bytes() for name in broken}, broken)

    def test_06_a_failed_write_keeps_the_launcher_and_the_warning(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        scripts = self._venv(new_proj / ".venv")
        launcher = make_launcher(self._python(old_proj / ".venv"))
        (scripts / "pip.exe").write_bytes(launcher)

        def refuse(af: object, content: bytes, report: RelinkReport) -> bool:
            report.errors.append({"file": str(getattr(af, "path")), "error": "denied"})
            return False

        with patch.object(relink_core, "_write_agent_file", side_effect=refuse):
            report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual((report.venvs, report.console_scripts, report.updated), ([], {}, []))
        self.assertEqual([e["error"] for e in report.errors], ["denied"])
        self.assertEqual(len(self._stale_warnings(report)), 1)
        self.assertIn("pip.exe", report.warnings[0])
        self.assertEqual((scripts / "pip.exe").read_bytes(), launcher)

    def test_07_a_rewrite_that_does_not_check_out_is_never_written(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        scripts = self._venv(new_proj / ".venv")
        launcher = make_launcher(self._python(old_proj / ".venv"))
        (scripts / "pip.exe").write_bytes(launcher)
        real_archive = relink_core._launcher_archive
        calls: List[bytes] = []

        def archive_that_breaks_after_the_rewrite(data: bytes) -> Optional[Tuple[List[str], int]]:
            calls.append(data)
            return real_archive(data) if len(calls) == 1 else None  # the second look is at the rewritten bytes

        with patch.object(relink_core, "_launcher_archive", side_effect=archive_that_breaks_after_the_rewrite):
            report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual(len(calls), 2)
        self.assertEqual((report.venvs, report.updated), ([], []))
        self.assertEqual(len(self._stale_warnings(report)), 1)
        self.assertEqual((scripts / "pip.exe").read_bytes(), launcher)
        self.assertFalse((scripts / f"pip.exe{BACKUP_SUFFIX}").exists())

    def test_08_a_venv_with_pth_files_and_launchers_counts_both(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        venv_dir = new_proj / ".venv"
        scripts = self._venv(venv_dir)
        site = venv_dir / "Lib" / "site-packages"
        site.mkdir(parents=True)
        (site / "_proj.pth").write_text(f"{old_proj / 'src'}\n", encoding="utf-8")
        (scripts / "pip.exe").write_bytes(make_launcher(self._python(old_proj / ".venv")))

        report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual(report.venvs, [str(venv_dir)])
        self.assertEqual(report.agents, {"Python console scripts": 1, "Python venv": 1})
        self.assertEqual(report.console_scripts, {str(venv_dir): 1})
        self.assertEqual(report.warnings, [])

    def test_09_text_scripts_get_their_shebang_repointed(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        venv_dir = new_proj / ".venv"
        scripts = self._venv(venv_dir)
        old_python = self._python(old_proj / ".venv")
        new_python = self._python(venv_dir)
        body = "# -*- coding: utf-8 -*-\nimport sys\nprint('#!' + sys.executable)\n"
        files = {
            # pip rewrites `#!python` to the venv's interpreter when it installs a plain script (Windows: CRLF)
            "jp.py": (f"#!{old_python}\r\n{body}".encode(), f"#!{new_python}\r\n{body}".encode()),
            "tool": (f"#!{old_python} -E\n{body}".encode(), f"#!{new_python} -E\n{body}".encode()),
            # distlib's form for an interpreter path that a shebang cannot hold
            "long": (
                f"#!/bin/sh\n'''exec' \"{old_python}\" \"$0\" \"$@\"\n' '''\n{body}".encode(),
                f"#!/bin/sh\n'''exec' \"{new_python}\" \"$0\" \"$@\"\n' '''\n{body}".encode(),
            ),
            "env": (f"#!/usr/bin/env python3\n{body}".encode(), None),
            "other": (f"#!{self.test_dir / 'global_python' / 'python.exe'}\n{body}".encode(), None),
            "plain": (b"@echo off\r\necho %VIRTUAL_ENV%\r\n", None),  # not a script with a shebang
        }
        for name, (before, _after) in files.items():
            (scripts / name).write_bytes(before)
        if sys.platform != "win32":
            os.chmod(scripts / "tool", 0o755)

        report = self._relink([(str(old_proj), str(new_proj))])

        for name, (before, after) in files.items():
            self.assertEqual((scripts / name).read_bytes(), after or before, name)
            self.assertEqual((scripts / f"{name}{BACKUP_SUFFIX}").exists(), after is not None, name)
        self.assertEqual(report.console_scripts, {str(venv_dir): 3})
        self.assertEqual(report.warnings, [])
        if sys.platform != "win32":
            self.assertEqual(os.stat(scripts / "tool").st_mode & 0o777, 0o755)  # still executable
        self.assertEqual(self._relink([(str(old_proj), str(new_proj))]).console_scripts, {})

    def test_10_shebang_forms_on_posix_paths(self) -> None:
        mapper = PathMapper([("/Volumes/SSD/proj", "/Volumes/SSD/03_Dev/proj")])
        spaced = PathMapper([("/Volumes/SSD/proj", "/Volumes/My SSD/proj")])
        body = b"import sys\n"
        python = "/Volumes/SSD/proj/.venv/bin/python3"
        cases = [
            ("simple", mapper, f"#!{python}\n".encode(), ("new", b"#!/Volumes/SSD/03_Dev/proj/.venv/bin/python3\n")),
            ("with arguments", mapper, f"#!{python} -E -s\n".encode(), ("new", b"#!/Volumes/SSD/03_Dev/proj/.venv/bin/python3 -E -s\n")),
            ("env", mapper, b"#!/usr/bin/env python3\n", ("same", None)),
            ("not a shebang", mapper, b"import sys\n", ("same", None)),
            ("one line", mapper, f"#!{python}".encode(), ("same", None)),
            # a shebang cannot hold a space (nor quotes): the script stays as it is and is reported
            ("space after the move", spaced, f"#!{python}\n".encode(), ("stuck", None)),
            ("quoted shebang", mapper, f'#!"{python}"\n'.encode(), ("stuck", None)),
            # a form that is not understood still names the old place
            ("env -S", mapper, f"#!/usr/bin/env -S {python}\n".encode(), ("stuck", None)),
            (
                "sh form",
                mapper,
                f"#!/bin/sh\n'''exec' \"{python}\" \"$0\" \"$@\"\n' '''\n".encode(),
                ("new", b"#!/bin/sh\n'''exec' \"/Volumes/SSD/03_Dev/proj/.venv/bin/python3\" \"$0\" \"$@\"\n' '''\n"),
            ),
            (
                "sh form into a path with a space",
                spaced,
                f"#!/bin/sh\n'''exec' \"{python}\" \"$0\" \"$@\"\n' '''\n".encode(),
                ("new", b"#!/bin/sh\n'''exec' \"/Volumes/My SSD/proj/.venv/bin/python3\" \"$0\" \"$@\"\n' '''\n"),
            ),
            (
                "sh form with CRLF",
                mapper,
                f"#!/bin/sh\r\n'''exec' \"{python}\" \"$0\" \"$@\"\r\n' '''\r\n".encode(),
                ("new", b"#!/bin/sh\r\n'''exec' \"/Volumes/SSD/03_Dev/proj/.venv/bin/python3\" \"$0\" \"$@\"\r\n' '''\r\n"),
            ),
        ]
        for label, rules, before, (status, after) in cases:
            with self.subTest(label):
                rest = body if before.endswith(b"\n") else b""  # the rest of the script, if the shebang has a line end
                got_status, got = relink_core._repoint_script(before + rest, rules)
                self.assertEqual(got_status, status)
                self.assertEqual(got, None if after is None else after + rest)

    def test_11_the_interpreter_path_of_a_shebang_command(self) -> None:
        win = PathMapper([("E:\\my proj", "E:\\03 Dev\\my proj"), ("E:\\project", "E:\\03_Dev\\project")])
        cases = [
            ("E:\\project\\venv\\Scripts\\python.exe", ("new", "E:\\03_Dev\\project\\venv\\Scripts\\python.exe")),
            ("E:/project/venv/Scripts/python.exe -E", ("new", "E:/03_Dev/project/venv/Scripts/python.exe -E")),
            ('"E:\\project\\venv\\Scripts\\python.exe" -E', ("new", '"E:\\03_Dev\\project\\venv\\Scripts\\python.exe" -E')),
            # a folder with a space in its name, written without quotes (pip does that): the whole path is the interpreter
            ("E:\\my proj\\venv\\Scripts\\python.exe", ("new", "E:\\03 Dev\\my proj\\venv\\Scripts\\python.exe")),
            ("C:\\Python313\\python.exe", ("same", "C:\\Python313\\python.exe")),
            ("<launcher_dir>\\python.exe", ("same", "<launcher_dir>\\python.exe")),
            ('"E:\\project\\venv', ("same", '"E:\\project\\venv')),  # an unterminated quote is not understood
            ("", ("same", "")),
        ]
        for command, expected in cases:
            with self.subTest(command):
                self.assertEqual(relink_core._repoint_command(command, win, can_quote=True), expected)
        # without quotes available a moved path with a space cannot be written
        self.assertEqual(
            relink_core._repoint_command("/a/venv/bin/python", PathMapper([("/a", "/b c")]), can_quote=False),
            ("stuck", "/a/venv/bin/python"),
        )

    def test_12_a_new_path_that_is_not_valid_unicode_is_not_written(self) -> None:
        # A folder name that is a lone surrogate (not valid Unicode) cannot be written into a shebang
        mapper = PathMapper([("E:\\proj", "E:\\03\\proj\udc80")])
        launcher = make_launcher("E:\\proj\\.venv\\Scripts\\python.exe")
        self.assertEqual(relink_core._repoint_launcher(launcher, mapper), ("stuck", None))
        self.assertEqual(relink_core._repoint_script(b"#!E:\\proj\\.venv\\Scripts\\python.exe\r\nx\r\n", mapper), ("stuck", None))
        sh = b"#!/bin/sh\n'''exec' \"E:\\proj\\python.exe\" \"$0\" \"$@\"\n' '''\n"
        self.assertEqual(relink_core._repoint_script(sh, mapper), ("stuck", None))

    def test_13_backups_and_links_are_not_scripts(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        scripts = self._venv(new_proj / ".venv")
        old_python = self._python(old_proj / ".venv")
        stale = make_launcher(old_python)
        (scripts / f"old.exe{BACKUP_SUFFIX}").write_bytes(stale)  # a backup of an earlier run keeps its old path
        (scripts / "leftover.exe.smart-drive.tmp").write_bytes(stale)
        (scripts / "real.exe").write_bytes(stale)
        linked = True
        try:
            os.symlink(scripts / "real.exe", scripts / "link.exe")
        except (OSError, NotImplementedError, AttributeError):  # Windows without the privilege to create links
            linked = False

        report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual((scripts / f"old.exe{BACKUP_SUFFIX}").read_bytes(), stale)
        self.assertEqual((scripts / "leftover.exe.smart-drive.tmp").read_bytes(), stale)
        self.assertEqual(report.console_scripts, {str(new_proj / ".venv"): 1})  # real.exe only, once
        self.assertEqual(report.warnings, [])
        if linked:
            self.assertTrue((scripts / "link.exe").is_symlink())
            self.assertEqual(sorted(entry["file"] for entry in report.updated), [str(scripts / "real.exe")])

    def test_14_cli_names_the_count_and_the_second_run_has_nothing_left_to_change(self) -> None:
        drive = self.test_dir / "drive"
        old_proj = drive / "proj"
        new_proj = drive / "03_Development_Projects" / "proj"
        venv_dir = new_proj / ".venv"
        scripts = self._venv(venv_dir)
        launcher = make_launcher(self._python(old_proj / ".venv"))
        for name in ("pip.exe", "pip3.exe"):
            (scripts / name).write_bytes(launcher)
        move = ["relink", str(old_proj), str(new_proj), "--root", str(drive)]

        rc, preview = self._cli(move + ["--dry-run"])
        self.assertEqual(rc, 0, preview)
        self.assertIn(f"Python venv would be updated: {venv_dir} (2 console scripts would be repointed)", preview)
        self.assertEqual((scripts / "pip.exe").read_bytes(), launcher)

        rc, out = self._cli(move)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Python venv updated: {venv_dir} (2 console scripts repointed)", out)
        self.assertIn("Python console scripts: 2 path(s) updated", out)
        self.assertNotIn("may still start the old path", out)

        for argv in (["relink", "--root", str(drive), "--dry-run"], ["relink", "--root", str(drive)]):
            rc, out = self._cli(argv)
            self.assertEqual(rc, 0, out)
            self.assertIn("Nothing left to change: all coding agent paths are up to date.", out)
            self.assertNotIn("Python venv", out)
        rc, out = self._cli(["relink", "--root", str(drive), "--json"])
        payload = json.loads(out)
        self.assertEqual((payload["venvs"], payload["console_scripts"], payload["warnings"]), ([], {}, []))

        # One launcher reads as singular
        (scripts / "pip3.exe").unlink()
        (scripts / "pip.exe").write_bytes(launcher)
        rc, out = self._cli(["relink", str(old_proj), str(new_proj), "--root", str(drive)])
        self.assertIn(f"Python venv updated: {venv_dir} (1 console script repointed)", out)

    @unittest.skipUnless(HAS_ENSUREPIP, "ensurepip is required to create a venv with pip")
    def test_15_a_moved_real_venv_runs_its_pip_again(self) -> None:
        old_proj = self.test_dir / "proj"
        # Windows launchers need quotes around a path with a space, and the new path gets one there
        new_proj = self.test_dir / ("03 Dev" if sys.platform == "win32" else "03_Dev") / "proj"
        venv.EnvBuilder(with_pip=True).create(str(old_proj / ".venv"))
        new_proj.parent.mkdir(parents=True)
        os.rename(old_proj, new_proj)
        folder, name = ("Scripts", "pip.exe") if sys.platform == "win32" else ("bin", "pip")
        pip = new_proj / ".venv" / folder / name

        # Windows starts the launcher, which cannot start the old python and exits with an error. On Linux and macOS
        # the script begins with `#!<old venv>/bin/python`, which is gone, so the kernel cannot run it at all and
        # subprocess raises FileNotFoundError instead of returning a code. Either way the moved pip does not work.
        try:
            before = subprocess.run([str(pip), "--version"], capture_output=True, text=True, timeout=120)
            starts_before = before.returncode == 0
        except OSError:
            starts_before = False
        self.assertFalse(starts_before, "the moved pip should still start the old python")

        report = self._relink([(str(old_proj), str(new_proj))])

        self.assertGreaterEqual(report.console_scripts.get(str(new_proj / ".venv"), 0), 1, report.to_dict())
        self.assertEqual(self._stale_warnings(report), [])
        after = subprocess.run([str(pip), "--version"], capture_output=True, text=True, timeout=120)
        self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
        self.assertTrue(after.stdout.startswith("pip "), after.stdout)
        self.assertEqual(self._relink([(str(old_proj), str(new_proj))]).console_scripts, {})


if __name__ == "__main__":
    unittest.main()
