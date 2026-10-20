"""tests.test_gitattributes - .gitattributes pins the line endings of the launchers.

Without it, core.autocrlf=true on Windows writes the .sh and .command launchers with CRLF (macOS and Linux then stop
with "bad interpreter" on the first line), while a ZIP of the repository holds the .bat files with bare LF line
endings, which cmd.exe can mis-read around `goto` labels.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GITATTRIBUTES = REPO_ROOT / ".gitattributes"

# The line ending git must write for each kind of launcher, whatever core.autocrlf says.
EXPECTED_EOL = {
    ".sh": "lf",
    ".command": "lf",
    ".bat": "crlf",
    ".cmd": "crlf",
    ".ps1": "crlf",
}


def parse_gitattributes(text: str) -> dict[str, list[str]]:
    """Maps each pattern of a .gitattributes file to its attributes, in file order; comments and blanks are skipped."""
    rules: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *attributes = line.split()
        rules.setdefault(pattern, []).extend(attributes)
    return rules


def launcher_paths(*extensions: str) -> list[str]:
    """Launcher scripts of the repository root and of launchers/, as paths relative to the root with forward slashes."""
    wanted = extensions or tuple(EXPECTED_EOL)
    paths = []
    for folder in (REPO_ROOT, REPO_ROOT / "launchers"):
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix in wanted:
                paths.append(path.relative_to(REPO_ROOT).as_posix())
    return paths


def run_git(*args: str) -> subprocess.CompletedProcess:
    """Runs git in the repository; the output stays bytes."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )


def check_attr(paths: list[str]) -> dict[str, dict[str, str]]:
    """What git says the `text` and `eol` attributes of each path are: {path: {"text": "set", "eol": "lf"}}."""
    proc = run_git("check-attr", "-z", "text", "eol", "--", *paths)
    if proc.returncode != 0:
        raise AssertionError(f"git check-attr failed: {proc.stderr.decode('utf-8', 'replace')}")
    fields = proc.stdout.decode("utf-8").split("\0")
    result: dict[str, dict[str, str]] = {}
    for path, attribute, value in zip(fields[0::3], fields[1::3], fields[2::3]):
        result.setdefault(path, {})[attribute] = value
    return result


class TestGitattributesRules(unittest.TestCase):
    """The rules written in .gitattributes."""

    def setUp(self) -> None:
        self.assertTrue(GITATTRIBUTES.is_file(), ".gitattributes must sit at the repository root")
        self.rules = parse_gitattributes(GITATTRIBUTES.read_text(encoding="utf-8"))

    def test_text_is_detected_automatically_by_the_catch_all_rule(self) -> None:
        self.assertIn("text=auto", self.rules.get("*", []))
        # Later rules override earlier ones, so the catch-all rule has to come first.
        self.assertEqual(next(iter(self.rules)), "*")

    def test_shell_launchers_are_lf(self) -> None:
        for pattern in ("*.sh", "*.command"):
            with self.subTest(pattern=pattern):
                self.assertIn("text", self.rules.get(pattern, []))
                self.assertIn("eol=lf", self.rules.get(pattern, []))

    def test_windows_scripts_are_crlf(self) -> None:
        for pattern in ("*.bat", "*.cmd", "*.ps1"):
            with self.subTest(pattern=pattern):
                self.assertIn("text", self.rules.get(pattern, []))
                self.assertIn("eol=crlf", self.rules.get(pattern, []))

    def test_images_are_binary(self) -> None:
        for pattern in ("*.jpg", "*.png"):
            with self.subTest(pattern=pattern):
                self.assertIn("binary", self.rules.get(pattern, []))


class TestLauncherLineEndings(unittest.TestCase):
    """What git does with the rules, asked of git itself. Skipped without git or outside a git work tree."""

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("git") is None:
            raise unittest.SkipTest("git is not available")
        probe = run_git("rev-parse", "--is-inside-work-tree")
        if probe.returncode != 0 or probe.stdout.strip() != b"true":
            raise unittest.SkipTest("not inside a git work tree (a source archive?)")

    def test_git_resolves_every_launcher_to_its_line_ending(self) -> None:
        paths = launcher_paths()
        self.assertTrue(paths, "no launcher found")
        attrs = check_attr(paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(attrs[path]["text"], "set")
                self.assertEqual(attrs[path]["eol"], EXPECTED_EOL[Path(path).suffix])

    def test_shell_launchers_on_disk_have_no_carriage_returns(self) -> None:
        paths = launcher_paths(".sh", ".command")
        self.assertTrue(paths, "no shell launcher found")
        attrs = check_attr(paths)
        # The check only means something while git applies eol=lf to every one of them.
        self.assertEqual({path: attrs[path]["eol"] for path in paths}, {path: "lf" for path in paths})
        with_cr = [path for path in paths if b"\r" in (REPO_ROOT / path).read_bytes()]
        self.assertEqual(
            with_cr,
            [],
            "These launchers hold CR bytes, so macOS and Linux fail on them with 'bad interpreter'. They were checked "
            "out before .gitattributes existed, and git leaves unchanged files alone. Write them again with these two "
            "commands:\n  git rm --cached -q -- '*.sh' '*.command'\n  git checkout HEAD -- '*.sh' '*.command'",
        )

    def test_git_archive_writes_each_launcher_with_its_own_line_ending(self) -> None:
        """A ZIP or tar of the repository (GitHub's Download ZIP) must hold CRLF .bat/.ps1 and LF .sh/.command."""
        proc = run_git("archive", "--worktree-attributes", "--format=tar", "HEAD")
        if proc.returncode != 0:
            self.skipTest(f"git archive failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
        checked = 0
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
            for member in archive.getmembers():
                suffix = Path(member.name).suffix
                if not member.isfile() or suffix not in EXPECTED_EOL:
                    continue
                data = archive.extractfile(member).read()
                with self.subTest(path=member.name):
                    if EXPECTED_EOL[suffix] == "lf":
                        self.assertNotIn(b"\r", data)
                    else:
                        self.assertEqual(data.count(b"\r\n"), data.count(b"\n"), "a line ends without CR")
                checked += 1
        self.assertGreater(checked, 0, "the archive held no launcher")


if __name__ == "__main__":
    unittest.main()
