"""tests.test_batch_launchers - the .bat launchers must do under cmd.exe what their text says.

Plain text is not safe in an `echo`, a `title` or the prompt of a `set /p`. cmd.exe takes an unescaped `&` for the end
of the command and runs the rest of the line as a new one ("'Cluster' is not recognized as an internal or external
command"); `>` and `<` redirect (`echo size:>10MB` wrote a file named 10MB instead of printing the line); `|` pipes;
and inside a ( ... ) block a bare `)` closes the block early ("after was unexpected at this time."). The text needs
`^&`, `^>`, `^<`, `^|` or `^)`, or "double quotes" around it.

The static tests run on every system. The empirical ones run each launcher under cmd.exe against a fake `smart_drive`
package that only records how it was called; they answer 0 or n, or end the launcher at its first prompt, so no
apply path of any launcher is ever started and nothing real is touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_launchers_and_invariants_tier5 import (
    EXPECTED_LAUNCHER_BASES,
    LAUNCHERS_DIR,
    REPO_ROOT,
    _drive_prompts,
)

BAT_NAMES = [f"{base}.bat" for base in EXPECTED_LAUNCHER_BASES]
BAT_PATHS = [folder / name for folder in (REPO_ROOT, LAUNCHERS_DIR) for name in BAT_NAMES]


# ---------------------------------------------------------------------------------------------------------------
# A reader for the text lines of a batch file
# ---------------------------------------------------------------------------------------------------------------

# Commands whose arguments are free text up to the end of the line, found where a command can start: at the
# beginning of the line or after "@", "(", "&" or "|", and not as part of a longer word such as "%title%".
_TEXT_COMMAND = re.compile(r"(?i)(?<![^\s(@&|])(echo|title|set\s+/p)(?![^\s.:;,=+/\\\[\]^(])")


def _cmd_syntax_positions(line: str) -> list[int]:
    """Indexes of the characters cmd.exe reads as syntax: outside "double quotes" and not the target of a ^ escape."""
    positions = []
    quoted = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            quoted = not quoted
        elif not quoted:
            if char == "^":
                index += 2  # the ^ and the character it escapes are plain text
                continue
            positions.append(index)
        index += 1
    return positions


def find_cmd_text_problems(text: str) -> list[tuple[int, str, str]]:
    """(line number, what is wrong, the line) for each echo/title/set /p text that cmd.exe would not print as written.

    The text of such a command runs to the end of the line, so an operator in it that is neither escaped with ^ nor
    inside double quotes is not text: `&` `|` `<` `>` always, and `)` while a ( ... ) block is open.
    """
    problems = []
    depth = 0  # ( ... ) blocks that are open, counted from lines that end with "(" and lines that start with ")"
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(":") or re.match(r"(?i)rem(\s|$)", line):
            continue  # blank lines, labels and comments ("::" is a label that never runs)
        if line.startswith(")"):
            depth = max(depth - 1, 0)
        syntax = _cmd_syntax_positions(line)
        found = set(syntax)
        command = next((match for match in _TEXT_COMMAND.finditer(line) if match.start() in found), None)
        if command is None:
            if syntax and line[syntax[-1]] == "(":
                depth += 1
            continue
        for index in syntax:
            if index < command.end():
                continue
            if line[index] in "&|<>":
                problems.append((number, f"unescaped {line[index]}", raw))
            elif line[index] == ")" and depth:
                problems.append((number, "bare ) inside a ( ) block", raw))
    return problems


class TestBatchTextScanner(unittest.TestCase):
    """The reader above finds what cmd.exe would split or redirect, and leaves safe text alone."""

    UNSAFE = (
        ("echo   SmartDrive-OS: Storage Breakdown & 512KB Slack Audit", "&"),
        ("title SmartDrive-OS - Storage & Cluster Slack Audit", "&"),
        ("    echo   - project size:>10MB", ">"),
        ("echo from <here", "<"),
        ("echo this | that", "|"),
        ("echo first & echo second", "&"),
        ("set /p CHOICE=Pick a or b & c: ", "&"),
        ('echo "quoted" and then & more', "&"),
        ("@echo done & pause", "&"),
    )
    SAFE = (
        "echo   Storage ^& Cluster",
        "title Storage ^& Cluster",
        "echo   - project size:^>10MB",
        "echo from ^<here ^| there",
        'echo   "Storage & Cluster"',
        'set /p CHOICE="Pick a or b & c: "',
        "echo   Done (all ok)",  # a ( ) pair outside a block is plain text
        "echo.",
        "echo(",
        "@echo off",
        "chcp 65001 >nul",  # not text: a real redirect
        "%PYTHON_CMD% -m smart_drive mcp register 2>nul || %PYTHON_CMD% -m smart_drive mcp-config",
        ":: Flow: dry-run -> wait & more",  # a comment is a label that never runs
        "rem copy & paste > nowhere",
        'set "A=b & c"',
        'if /i "%X%"=="y" (',
    )

    def test_an_unescaped_operator_in_text_is_found(self):
        for line, char in self.UNSAFE:
            with self.subTest(line=line):
                problems = find_cmd_text_problems(line)
                self.assertEqual([reason for _, reason, _ in problems], [f"unescaped {char}"])

    def test_escaped_quoted_and_non_text_lines_are_left_alone(self):
        for line in self.SAFE:
            with self.subTest(line=line):
                self.assertEqual(find_cmd_text_problems(line), [])

    def test_a_bare_closing_parenthesis_matters_only_inside_a_block(self):
        text = "\n".join(
            [
                "echo top (level) is fine",  # 1
                "if 1==1 (",  # 2
                "    echo yes (really)",  # 3: closes the block early
                "    echo yes ^(escaped^)",  # 4
                '    echo yes "(quoted)"',  # 5
                ") else (",  # 6
                "    echo no (really)",  # 7: the else block is open
                ")",  # 8
                "echo after (the block) is fine",  # 9
            ]
        )
        problems = [(number, reason) for number, reason, _ in find_cmd_text_problems(text)]
        self.assertEqual(problems, [(3, "bare ) inside a ( ) block"), (7, "bare ) inside a ( ) block")])

    def test_a_text_line_that_ends_with_an_open_parenthesis_does_not_open_a_block(self):
        text = "echo Usage: smart-drive (\necho and a second (line)\n"
        self.assertEqual(find_cmd_text_problems(text), [])


# ---------------------------------------------------------------------------------------------------------------
# What the launcher files say
# ---------------------------------------------------------------------------------------------------------------


class TestBatchFilesAreSafeForCmd(unittest.TestCase):
    """Reads the 12 .bat files (root and launchers/) the way cmd.exe will."""

    def test_the_launchers_to_check_are_all_there(self):
        self.assertEqual(len(BAT_PATHS), 12)
        for path in BAT_PATHS:
            self.assertTrue(path.is_file(), f"missing {path}")

    def test_no_echo_title_or_prompt_text_holds_an_unescaped_operator(self):
        problems = []
        for path in BAT_PATHS:
            for number, why, raw in find_cmd_text_problems(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(REPO_ROOT)}:{number}: {why}: {raw.strip()}")
        self.assertEqual(
            problems,
            [],
            "cmd.exe would split, redirect or pipe these lines instead of printing them. Write ^& ^> ^< ^| ^) "
            'for the character, or put the text in "double quotes".',
        )


# ---------------------------------------------------------------------------------------------------------------
# What cmd.exe does with them
# ---------------------------------------------------------------------------------------------------------------

# A stand-in for `python -m smart_drive ...` (and for the guided runner): it records how it was called and where,
# and says so on stdout. A launcher that finds it can never reach the real smart-drive.
FAKE_MODULE = (
    "import json\n"
    "import os\n"
    "import sys\n"
    "record = {'script': os.path.basename(sys.argv[0]), 'args': sys.argv[1:], 'cwd': os.getcwd()}\n"
    "with open(os.environ['FAKE_SD_LOG'], 'a', encoding='utf-8') as handle:\n"
    "    handle.write(json.dumps(record) + '\\n')\n"
    "print('FAKE ' + record['script'] + ' ' + ' '.join(record['args']))\n"
)

MENU_PROMPT = "Select an option [0-11]: "
PYTHON_MISSING_LINES = (
    "SmartDrive-OS requires Python 3.9 or higher.",
    'Make sure to check "Add Python to PATH" during install.',
)

# How far each launcher is walked, and nowhere an apply path could start: n and 0 turn the cleaning, organizing and
# relinking down, and None ends Setup_SSD.bat at its first prompt, before it can run `init`. Per launcher: the answers
# (see _drive_prompts), the whole lines it must print, and the calls the fake smart_drive must have seen. The `pause`
# at the end of a launcher needs no key: once the answers are given stdin is closed, and `pause` then goes on.
WALKS = {
    "Quick_Audit.bat": (
        [],
        ["SmartDrive-OS: Storage Breakdown & 512KB Slack Audit"],
        [("__main__.py", ["audit"])],
    ),
    "Quick_Clean.bat": (
        [("[y/N]: ", "n\r\n")],
        ["SmartDrive-OS: Safe Junk Cleaner (Dry-Run Preview)", "Safe purge aborted. No files were modified."],
        [("__main__.py", ["clean", "--dry-run"])],
    ),
    "Quick_Search.bat": (
        # An empty query shows the syntax examples, then the launcher asks again.
        [("dir:): ", "\r\n"), ("Enter query: ", "llama\r\n")],
        [
            "SmartDrive-OS: Instant SQLite FTS5 Search",
            "No query entered. Showing search syntax examples:",
            "- llama ext:gguf",
            "- project size:>10MB",
            "- cat:code dir:Development_Projects",
        ],
        [("__main__.py", ["search", "llama"])],
    ),
    "Setup_SSD.bat": (
        [("(Default: 3): ", None)],
        [
            "[1] AI Developer      (Models, checkpoints, GGUF, agent workspaces)",
            "[2] Data Science      (EDA notebooks, pipelines, parquet/raw data)",
            "[3] General Workspace (Universal code, docs, notes, toolbox)",
        ],
        [],
    ),
    "SmartDrive.bat": (
        [(MENU_PROMPT, "0\r\n")],
        [
            "[1] Initialize Drive & Presets (smart-drive init)",
            "[2] Sentinel & Integrity Health Check (smart-drive sentinel)",
            "[3] SQLite FTS5 File Search (smart-drive search)",
            "[4] Storage & Cluster Slack Audit (smart-drive audit)",
            "[6] Auto-Zoning & Anti-Slack Rebalancer (smart-drive organize)",
            "[9] Drive Health Checkup & Score (smart-drive checkup)",
            "[11] Relink Coding Agents to the Latest Move (smart-drive relink)",
        ],
        [],
    ),
    "Smart_Organize.bat": ([], [], [("smart_organize_runner.py", [])]),
}


def _same_path(first, second) -> bool:
    return os.path.normcase(os.path.realpath(str(first))) == os.path.normcase(os.path.realpath(str(second)))


@unittest.skipUnless(sys.platform == "win32", "cmd.exe batch execution")
class TestBatchLaunchersUnderCmd(unittest.TestCase):
    """Runs the real launcher files under cmd.exe, each in a folder of its own beside a fake smart_drive."""

    def _scratch(self) -> Path:
        scratch = Path(tempfile.mkdtemp(prefix="test_bat_"))
        self.addCleanup(shutil.rmtree, scratch, True)
        (scratch / "elsewhere").mkdir()  # the working folder: it holds no smart_drive, so a failed `cd` shows
        (scratch / "safe_root").mkdir()
        return scratch

    def _env(self, scratch: Path, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in ("SMART_DRIVE_ROOT", "PYTHONPATH", "SD_SELFTEST")}
        # Whatever happens, a smart_drive that is not the fake could only ever look at an empty temp folder.
        env["SMART_DRIVE_ROOT"] = str(scratch / "safe_root")
        env["FAKE_SD_LOG"] = str(scratch / "calls.jsonl")
        env["PATH"] = str(Path(sys.executable).resolve().parent) + os.pathsep + env.get("PATH", "")
        env.update(extra)
        return env

    @staticmethod
    def _command(launcher: Path) -> str:
        """The command line for cmd.exe. The doubled quotes keep a path with a space, & or ( ) in it in one piece."""
        return f'cmd.exe /d /s /c ""{launcher}""'

    @staticmethod
    def _install(scratch: Path, name: str, folder_name: str) -> tuple[Path, Path]:
        """Copies a launcher into a folder called `folder_name`, next to a fake smart_drive. Returns (launcher, folder)."""
        folder = scratch / folder_name
        package = folder / "smart_drive"
        (package / "cli").mkdir(parents=True)
        for relative in ("__init__.py", "cli/__init__.py"):
            (package / relative).write_text("", encoding="utf-8")
        for relative in ("__main__.py", "cli/smart_organize_runner.py"):
            (package / relative).write_text(FAKE_MODULE, encoding="utf-8")
        launcher = folder / name
        shutil.copy2(LAUNCHERS_DIR / name, launcher)
        return launcher, folder

    @staticmethod
    def _calls(scratch: Path) -> list[dict]:
        log = scratch / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def _lines(stdout: str) -> list[str]:
        return [line.strip() for line in stdout.splitlines()]

    def _assert_clean_run(self, name: str, folder: Path, stdout: str, stderr: str) -> None:
        self.assertEqual(stderr.strip(), "", f"{name} must not run pieces of its own text as commands")
        self.assertNotIn("is not recognized", stdout + stderr)
        self.assertEqual(
            sorted(os.listdir(folder)),
            sorted([name, "smart_drive"]),
            f"{name} left a file behind: a > in its text redirected a line into a file instead of printing it",
        )

    def _check_walk(self, name: str, folder_name: str) -> None:
        """Walks launcher `name` in a folder called `folder_name` and checks what it printed, ran and left behind."""
        steps, expected_lines, expected_calls = WALKS[name]
        scratch = self._scratch()
        launcher, folder = self._install(scratch, name, folder_name)
        returncode, stdout, stderr = _drive_prompts(
            self._command(launcher),
            self._env(scratch, SD_SELFTEST="1"),  # SD_SELFTEST: Smart_Organize.bat ends without a pause
            str(scratch / "elsewhere"),
            steps,
            timeout=60.0,
        )
        if name != "Setup_SSD.bat":  # that one was ended at its prompt on purpose
            self.assertEqual(returncode, 0, f"{stdout}\n{stderr}")
        self._assert_clean_run(name, folder, stdout, stderr)
        printed = self._lines(stdout)
        for line in expected_lines:
            self.assertIn(line, printed)
        calls = self._calls(scratch)
        self.assertEqual([(call["script"], call["args"]) for call in calls], expected_calls)
        for call in calls:
            self.assertTrue(_same_path(call["cwd"], folder), f"{name} ran in {call['cwd']}, not in {folder}")

    def test_each_launcher_prints_its_banner_whole_and_runs_nothing_it_did_not_mean_to(self):
        self.assertEqual(sorted(WALKS), sorted(BAT_NAMES))
        for name in WALKS:
            with self.subTest(launcher=name):
                self._check_walk(name, "repo")

    def test_every_launcher_says_python_is_missing_in_one_piece(self):
        """With no Python on PATH the error banner is printed whole and the launcher stops with exit code 1."""
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        for name in BAT_NAMES:
            with self.subTest(launcher=name):
                scratch = self._scratch()
                launcher, folder = self._install(scratch, name, "repo")
                proc = subprocess.run(
                    self._command(launcher),
                    input=b"x\r\n",  # the key that ends `pause`
                    capture_output=True,
                    env=self._env(scratch, PATH=str(system32), SD_SELFTEST="1"),
                    cwd=str(scratch / "elsewhere"),
                    timeout=60,
                    check=False,
                )
                stdout = proc.stdout.decode("utf-8", "replace")
                stderr = proc.stderr.decode("utf-8", "replace")
                self.assertEqual(proc.returncode, 1, f"{stdout}\n{stderr}")
                self._assert_clean_run(name, folder, stdout, stderr)
                for line in PYTHON_MISSING_LINES:
                    self.assertIn(line, self._lines(stdout))
                self.assertEqual(self._calls(scratch), [])


if __name__ == "__main__":
    unittest.main()
