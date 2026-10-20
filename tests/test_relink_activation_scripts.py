"""tests/test_relink_activation_scripts.py - relink repoints the activation scripts of a moved virtual environment.

`activate`, `activate.bat`, `activate.fish`, ... hold the venv's path as text (VIRTUAL_ENV, and from it PATH). After a
move, activating the venv would put its old, missing folder on PATH. relink writes the new path into those files,
only where the whole old path stands, keeps their line endings and encoding, and keeps a backup of each.

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
import shutil
import subprocess
import sys
import unittest
import venv
from typing import Dict, List, Sequence, Tuple
from unittest.mock import patch

from smart_drive.cli.main import main as cli_main
from smart_drive.core.relink import BACKUP_SUFFIX, RelinkReport, relink
import smart_drive.core.relink as relink_core
from tests.helpers import SmartDriveTestCase

HAS_VENV = importlib.util.find_spec("ensurepip") is not None


def scripts_for(venv_path: str) -> Dict[str, bytes]:
    """Activation scripts as CPython's venv writes them (shortened), each with the venv's path in the way it does."""
    forward = venv_path.replace("\\", "/")
    return {
        # bash: LF, single quotes, once for Git Bash/Cygwin (cygpath) and once as it is
        "activate": (
            "# This file must be used with \"source bin/activate\" *from bash*\n"
            'if [ "${OSTYPE:-}" = "cygwin" ] || [ "${OSTYPE:-}" = "msys" ] ; then\n'
            f"    VIRTUAL_ENV=$(cygpath '{venv_path}')\n"
            "    export VIRTUAL_ENV\n"
            "else\n"
            f"    export VIRTUAL_ENV='{venv_path}'\n"
            "fi\n"
            'PATH="$VIRTUAL_ENV/"Scripts":$PATH"\n'
        ).encode("utf-8"),
        "activate.bat": (
            f'@echo off\r\nset "VIRTUAL_ENV={venv_path}"\r\nset "PATH=%VIRTUAL_ENV%\\Scripts;%PATH%"\r\n'
        ).encode("utf-8"),
        "activate.fish": f"# fish\r\nset -gx VIRTUAL_ENV '{venv_path}'\r\nset -gx PATH \"$VIRTUAL_ENV/\"Scripts $PATH\r\n".encode("utf-8"),
        "activate.csh": f"# csh\nsetenv VIRTUAL_ENV '{venv_path}'\nsetenv PATH \"$VIRTUAL_ENV/\"Scripts\":$PATH\"\n".encode("utf-8"),
        # virtualenv and uv write more of them; this one spells the path with forward slashes
        "activate.nu": f"# nushell\nlet virtual_env = '{forward}'\n".encode("utf-8"),
        # nothing in these two holds the path: they derive it when they run
        "Activate.ps1": b"\xef\xbb\xbf$VenvDir = $VenvExecDir.Parent.FullName\r\n$env:VIRTUAL_ENV = $VenvDir\r\n",
        "deactivate.bat": b"@echo off\r\nset VIRTUAL_ENV=\r\n",
    }


class TestRelinkActivationScripts(SmartDriveTestCase):
    """The activation scripts of a moved venv follow it, whole paths only, byte for byte otherwise."""

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
    def _venv(venv_dir: Path, scripts: Dict[str, bytes]) -> Path:
        """A virtualenv skeleton with the given activation scripts in Scripts; returns that folder."""
        folder = venv_dir / "Scripts"
        folder.mkdir(parents=True)
        (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        for name, content in scripts.items():
            (folder / name).write_bytes(content)
        return folder

    def test_01_the_activation_scripts_of_a_moved_venv_are_repointed_and_backed_up(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        venv_dir = new_proj / ".venv"
        before = scripts_for(str(old_proj / ".venv"))
        folder = self._venv(venv_dir, before)
        moves = [(str(old_proj), str(new_proj))]
        changed = ["activate", "activate.bat", "activate.csh", "activate.fish", "activate.nu"]

        first = self._relink(moves)

        self.assertEqual(first.venvs, [str(venv_dir)])  # no .pth file and no console script: the activation scripts list it
        self.assertEqual(first.activation_scripts, {str(venv_dir): 5})
        self.assertEqual(first.console_scripts, {})
        self.assertEqual(first.agents, {"Python activation scripts": 6})  # `activate` holds the path twice
        self.assertEqual(first.warnings, [])
        self.assertEqual(first.errors, [])
        new_text = str(venv_dir)
        for name, content in before.items():
            if name in changed:
                old_text = str(old_proj / ".venv")
                if name == "activate.nu":
                    old_text, new_text_here = old_text.replace("\\", "/"), new_text.replace("\\", "/")
                else:
                    new_text_here = new_text
                self.assertEqual((folder / name).read_bytes(), content.replace(old_text.encode(), new_text_here.encode()), name)
                self.assertEqual((folder / f"{name}{BACKUP_SUFFIX}").read_bytes(), content, name)
            else:  # nothing in it holds the path
                self.assertEqual((folder / name).read_bytes(), content, name)
                self.assertFalse((folder / f"{name}{BACKUP_SUFFIX}").exists(), name)
        # line endings and encoding are untouched
        self.assertEqual((folder / "activate.bat").read_bytes().count(b"\r\n"), 3)
        self.assertEqual((folder / "activate").read_bytes().count(b"\r"), 0)
        self.assertFalse(list(folder.glob("*.tmp")))

        written = {path: path.read_bytes() for path in folder.iterdir()}
        second = self._relink(moves)
        self.assertEqual((second.venvs, second.updated, second.warnings), ([], [], []))
        self.assertFalse(second.changed)
        self.assertEqual({path: path.read_bytes() for path in folder.iterdir()}, written)

    def test_02_dry_run_lists_what_it_would_repoint_and_agrees_with_the_real_run(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        before = scripts_for(str(old_proj / ".venv"))
        folder = self._venv(old_proj / ".venv", before)
        moves = [(str(old_proj), str(new_proj))]

        preview = self._relink(moves, dry_run=True)  # the folder is still where it was
        self.assertEqual(preview.activation_scripts, {str(new_proj / ".venv"): 5})
        self.assertEqual(
            sorted(entry["file"] for entry in preview.updated),
            sorted(str(new_proj / ".venv" / "Scripts" / name) for name in ("activate", "activate.bat", "activate.csh", "activate.fish", "activate.nu")),
        )
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, before)  # nothing written, no backup

        new_proj.parent.mkdir(parents=True)
        os.rename(old_proj, new_proj)
        real = self._relink(moves)
        self.assertEqual(real.activation_scripts, preview.activation_scripts)
        self.assertEqual(sorted((e["file"], e["paths"]) for e in real.updated), sorted((e["file"], e["paths"]) for e in preview.updated))

        dry, again = self._relink(moves, dry_run=True), self._relink(moves)
        for report in (dry, again):
            self.assertEqual((report.venvs, report.activation_scripts, report.updated, report.warnings), ([], {}, [], []))
            self.assertFalse(report.changed)
        self.assertEqual({**dry.to_dict(), "dry_run": False}, again.to_dict())

    def test_03_paths_with_spaces_and_non_ascii_names_and_a_byte_order_mark(self) -> None:
        old_proj = self.test_dir / "D\u1ef1 \u00e1n"  # a space and non-ASCII letters, in the old and the new place
        new_proj = self.test_dir / "03 Development" / "D\u1ef1 \u00e1n"
        old_venv, new_venv = str(old_proj / ".venv"), str(new_proj / ".venv")
        bom_script = b"\xef\xbb\xbf" + f"$env:VIRTUAL_ENV = '{old_venv}'\r\n".encode("utf-8")
        folder = self._venv(new_proj / ".venv", {**scripts_for(old_venv), "Activate.ps1": bom_script})

        report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual(report.activation_scripts, {new_venv: 6})
        self.assertEqual((folder / "Activate.ps1").read_bytes(), b"\xef\xbb\xbf" + f"$env:VIRTUAL_ENV = '{new_venv}'\r\n".encode("utf-8"))
        self.assertIn(f"set \"VIRTUAL_ENV={new_venv}\"\r\n".encode("utf-8"), (folder / "activate.bat").read_bytes())
        self.assertIn(f"export VIRTUAL_ENV='{new_venv}'\n".encode("utf-8"), (folder / "activate").read_bytes())
        for path in folder.iterdir():
            if not path.name.endswith(BACKUP_SUFFIX):
                self.assertNotIn(old_venv.encode("utf-8"), path.read_bytes(), path.name)

    def test_04_only_whole_paths_are_replaced(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        old_venv, new_venv = str(old_proj / ".venv"), str(new_proj / ".venv")
        sep = os.sep
        script = "\n".join(
            [
                f"echo '{old_venv}'",  # replaced
                f"PATH='{old_venv}{sep}Scripts':$PATH",  # replaced: the path goes on
                f"cd {old_venv}",  # replaced: ends the line
                f"echo '{old_venv}2' '{old_venv}-old' '{old_venv}.bak' '{old_venv}_x'",  # other folders next to it
                f"echo '{old_proj}{sep}.venv2{sep}Scripts' '{old_proj}{sep}src' '{old_proj}'",  # the project, not the venv
                f"echo 'X{old_venv}' '{self.test_dir}{sep}other{sep}{old_venv.lstrip(sep)}'",  # the end of a longer path
                "",
            ]
        ).encode("utf-8")
        folder = self._venv(new_proj / ".venv", {"activate": script})

        report = self._relink([(str(old_proj), str(new_proj))])

        lines = (folder / "activate").read_bytes().decode("utf-8").split("\n")
        self.assertEqual(lines[0], f"echo '{new_venv}'")
        self.assertEqual(lines[1], f"PATH='{new_venv}{sep}Scripts':$PATH")
        self.assertEqual(lines[2], f"cd {new_venv}")
        self.assertEqual(lines[3:6], script.decode("utf-8").split("\n")[3:6])  # all the others stay as they were
        self.assertEqual(report.agents, {"Python activation scripts": 3})

    def test_05_every_spelling_of_a_windows_path(self) -> None:
        old, new = "E:\\project\\IBM_Hackathon\\venv", "E:\\03 Dev\\project\\IBM_Hackathon\\venv"
        rewrite = relink_core._activation_rewriter(old, new)
        assert rewrite is not None
        text = "\n".join(
            [
                f"native '{old}' and '{old}\\Scripts'",
                "forward 'E:/project/IBM_Hackathon/venv' and 'E:/project/IBM_Hackathon/venv/Scripts'",
                "string \"E:\\\\project\\\\IBM_Hackathon\\\\venv\"",
                "msys '/e/project/IBM_Hackathon/venv' and '/e/project/IBM_Hackathon/venv/Scripts'",
                "cygwin '/cygdrive/e/project/IBM_Hackathon/venv'",
                # other folders, or the end of a longer path: left alone
                "no 'E:\\project\\IBM_Hackathon\\venv2' 'E:\\project\\IBM_Hackathon\\venv-old' '/e/project/IBM_Hackathon/venv.bak'",
                "no 'X:\\project\\IBM_Hackathon\\venv' 'E:\\project\\IBM_Hackathon' 'D:\\E:\\project\\IBM_Hackathon\\venv'",
                "",
            ]
        ).encode("utf-8")
        expected = "\n".join(
            [
                f"native '{new}' and '{new}\\Scripts'",
                "forward 'E:/03 Dev/project/IBM_Hackathon/venv' and 'E:/03 Dev/project/IBM_Hackathon/venv/Scripts'",
                "string \"E:\\\\03 Dev\\\\project\\\\IBM_Hackathon\\\\venv\"",
                "msys '/e/03 Dev/project/IBM_Hackathon/venv' and '/e/03 Dev/project/IBM_Hackathon/venv/Scripts'",
                "cygwin '/cygdrive/e/03 Dev/project/IBM_Hackathon/venv'",
            ]
            + text.decode("utf-8").split("\n")[5:]
        ).encode("utf-8")

        updated, count = rewrite(text)

        self.assertEqual(updated, expected)
        self.assertEqual(count, 8)
        if relink_core._CASE_INSENSITIVE:  # a drive and folders are spelled in any case on Windows and macOS
            self.assertEqual(rewrite(b"'e:\\PROJECT\\ibm_hackathon\\VENV\\x'"), (f"'{new}\\x'".encode(), 1))
        else:
            self.assertEqual(rewrite(b"'e:\\PROJECT\\ibm_hackathon\\VENV\\x'")[1], 0)
        # Nothing to write: the same path, or a name that is not valid Unicode
        self.assertIsNone(relink_core._activation_rewriter(old, old))
        self.assertIsNone(relink_core._activation_rewriter(old, "E:\\03\udc80\\venv"))
        # POSIX paths have the one spelling
        posix = relink_core._activation_rewriter("/Volumes/SSD/proj/.venv", "/Volumes/SSD/03_Dev/proj/.venv")
        assert posix is not None
        self.assertEqual(posix(b"export VIRTUAL_ENV='/Volumes/SSD/proj/.venv'\n"), (b"export VIRTUAL_ENV='/Volumes/SSD/03_Dev/proj/.venv'\n", 1))

    def test_06_a_failed_write_keeps_the_script_and_warns(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        before = scripts_for(str(old_proj / ".venv"))
        folder = self._venv(new_proj / ".venv", before)

        def refuse(af: object, content: bytes, report: RelinkReport) -> bool:
            report.errors.append({"file": str(getattr(af, "path")), "error": "denied"})
            return False

        with patch.object(relink_core, "_write_agent_file", side_effect=refuse):
            report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual((report.venvs, report.activation_scripts, report.updated), ([], {}, []))
        self.assertEqual(len(report.errors), 5)
        warnings = [w for w in report.warnings if "activation scripts" in w]
        self.assertEqual(len(warnings), 1, report.warnings)
        self.assertIn(str(new_proj / ".venv"), warnings[0])
        self.assertIn("activate.bat", warnings[0])
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, before)

    def test_07_a_console_script_that_starts_with_activate_is_not_an_activation_script(self) -> None:
        old_proj = self.test_dir / "proj"
        new_proj = self.test_dir / "03_Development_Projects" / "proj"
        old_python = str(old_proj / ".venv" / "Scripts" / "python.exe")
        new_python = str(new_proj / ".venv" / "Scripts" / "python.exe")
        folder = self._venv(new_proj / ".venv", {})
        # argcomplete's console script is called activate-global-python-argcomplete; its shebang is for the other step
        (folder / "activate-global-python-argcomplete").write_bytes(f"#!{old_python}\nprint('x')\n".encode("utf-8"))
        moves = [(str(old_proj), str(new_proj))]

        preview = self._relink(moves, dry_run=True)
        real = self._relink(moves)

        for report in (preview, real):  # counted once, as a console script, in a dry run and in the real run
            self.assertEqual(report.activation_scripts, {})
            self.assertEqual(report.console_scripts, {str(new_proj / ".venv"): 1})
        self.assertEqual((folder / "activate-global-python-argcomplete").read_bytes(), f"#!{new_python}\nprint('x')\n".encode("utf-8"))

    def test_08_cli_names_both_counts_and_the_second_run_has_nothing_left_to_change(self) -> None:
        drive = self.test_dir / "drive"
        old_proj = drive / "proj"
        new_proj = drive / "03_Development_Projects" / "proj"
        venv_dir = new_proj / ".venv"
        before = scripts_for(str(old_proj / ".venv"))
        folder = self._venv(venv_dir, {"activate": before["activate"], "activate.bat": before["activate.bat"]})
        (folder / "tool").write_bytes(f"#!{old_proj / '.venv' / 'Scripts' / 'python.exe'}\nx = 1\n".encode("utf-8"))
        move = ["relink", str(old_proj), str(new_proj), "--root", str(drive)]

        rc, preview = self._cli(move + ["--dry-run"])
        self.assertEqual(rc, 0, preview)
        self.assertIn(f"Python venv would be updated: {venv_dir} (1 console script and 2 activation scripts would be repointed)", preview)
        self.assertEqual((folder / "activate.bat").read_bytes(), before["activate.bat"])

        rc, out = self._cli(move)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"Python venv updated: {venv_dir} (1 console script and 2 activation scripts repointed)", out)
        self.assertIn("Python activation scripts: 3 path(s) updated", out)  # `activate` holds the path twice

        for argv in (["relink", "--root", str(drive), "--dry-run"], ["relink", "--root", str(drive)]):
            rc, out = self._cli(argv)
            self.assertEqual(rc, 0, out)
            self.assertIn("Nothing left to change: all coding agent paths are up to date.", out)
        rc, out = self._cli(["relink", "--root", str(drive), "--json"])
        payload = json.loads(out)
        self.assertEqual((payload["venvs"], payload["activation_scripts"], payload["console_scripts"]), ([], {}, {}))

        # Only activation scripts: the line names just those
        (folder / "tool").unlink()
        (folder / "activate.bat").write_bytes(before["activate.bat"])
        rc, out = self._cli(["relink", str(old_proj), str(new_proj), "--root", str(drive)])
        self.assertIn(f"Python venv updated: {venv_dir} (1 activation script repointed)", out)

    @unittest.skipUnless(HAS_VENV, "ensurepip is required to create a venv")
    def test_09_a_moved_real_venv_activates_with_its_new_path(self) -> None:
        old_proj = self.test_dir / "proj"
        # Windows: the new path gets a space, which the scripts must carry through; POSIX: no space in a shebang
        new_proj = self.test_dir / ("03 Dev" if sys.platform == "win32" else "03_Dev") / "proj"
        venv.EnvBuilder(with_pip=False).create(str(old_proj / ".venv"))
        new_proj.parent.mkdir(parents=True)
        os.rename(old_proj, new_proj)
        old_venv, new_venv = str(old_proj / ".venv"), str(new_proj / ".venv")
        folder = new_proj / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
        names = sorted(path.name for path in folder.iterdir() if relink_core._ACTIVATION_NAME_RE.match(path.name))
        self.assertIn("activate", names)
        holding = [name for name in names if old_venv.encode("utf-8") in (folder / name).read_bytes()]
        self.assertTrue(holding, "this Python writes no activation script that holds the venv's path")

        def virtual_env_after_activating() -> str:
            if sys.platform == "win32":
                check = self.test_dir / "check.bat"
                check.write_bytes(f'@echo off\r\ncall "{folder / "activate.bat"}"\r\necho %VIRTUAL_ENV%\r\n'.encode("utf-8"))
                done = subprocess.run(["cmd", "/c", str(check)], capture_output=True, text=True, timeout=60)
            else:
                bash = shutil.which("bash")
                if bash is None:
                    self.skipTest("bash is required to source the activate script")
                done = subprocess.run([bash, "-c", f'. "{folder / "activate"}" && echo "$VIRTUAL_ENV"'], capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            return done.stdout.strip().splitlines()[-1]

        self.assertEqual(virtual_env_after_activating(), old_venv)  # the scripts still name the folder it came from

        report = self._relink([(str(old_proj), str(new_proj))])

        self.assertEqual(report.activation_scripts, {new_venv: len(holding)}, report.to_dict())
        self.assertEqual(report.warnings, [])
        self.assertEqual(virtual_env_after_activating(), new_venv)
        for name in names:
            self.assertNotIn(old_venv.encode("utf-8"), (folder / name).read_bytes(), name)
        self.assertEqual(self._relink([(str(old_proj), str(new_proj))]).activation_scripts, {})


if __name__ == "__main__":
    unittest.main()
