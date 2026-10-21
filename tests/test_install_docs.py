"""tests.test_install_docs - what the READMEs tell to paste works, and the release guide holds what a first release needs.

README.md is also the PyPI project description, and PyPI cannot load a relative image: the banner was `docs/banner.jpg`
and showed as a broken picture on the project page. The READMEs also hand the reader MCP entries to paste into a coding
agent's config; one that drifts from what `mcp register` writes (the command `uvx`, the arguments `smart-drive-os mcp`)
installs a server that never starts, so each entry is parsed and compared with the package name in the registrar.
docs/RELEASING.md is the only place that tells the owner what to enter at PyPI and GitHub once: a pending publisher
registered under another name never matches the workflow's token, and an environment that nobody has to approve lets the
first tag publish unreviewed. Those terms are pinned here, and so is every file the guide sends the owner to.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import List

try:
    import tomllib
except ImportError:  # Python < 3.11: tomli when it happens to be installed, else the TOML test is skipped
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

_NO_TOML = "TOML parsing needs Python 3.11+ (tomllib) or the tomli package"

ROOT = Path(__file__).resolve().parent.parent
README_NAMES = ("README.md", "README_VN.md")
RELEASING = ROOT / "docs" / "RELEASING.md"

# The three ways in, as the install section types them.
INSTALL_COMMANDS = ("uvx smart-drive-os", "pipx install smart-drive-os", "pip install -e .")

# The notes the install section carried before the package reached PyPI (English, Vietnamese). It has been on PyPI since
# 1.4.2, so uvx and pipx work today: a README that says they wait for a release sends the reader to a clone instead.
WAITING_FOR_FIRST_RELEASE = ("after the first PyPI release", "sau bản phát hành PyPI đầu tiên")

# What the owner types into PyPI and GitHub, the registry's tool, and the signing route the guide leaves to the owner.
SETUP_TERMS = ("release.yml", "pypi", "pending publisher", "Required reviewers", "mcp-publisher", "SignPath")

# A repository path in backticks: at least one folder, an extension, no placeholder (`<version>`) and no glob (`**`).
BACKTICKED_PATH = re.compile(r"`([\w.-]+(?:/[\w.-]+)+\.\w+)`")

# A fenced code block with a language, and an image: HTML `<img src="...">` or Markdown `![alt](url)`. The target of a
# badge, `[![alt](image)](target)`, is a link and not an image: only what follows `![alt]` is matched.
FENCED_BLOCK = re.compile(r"^```(\w+)[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
HTML_IMAGE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
MARKDOWN_IMAGE = re.compile(r"!\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*<?([^)\s>]+)")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def fenced_blocks(markdown: str, language: str) -> List[str]:
    """The bodies of the code blocks written in `language`."""
    return [body for lang, body in FENCED_BLOCK.findall(markdown) if lang.lower() == language]


def image_urls(markdown: str) -> List[str]:
    """The target of every image outside code blocks: relative paths and absolute URLs alike."""
    text = FENCED_BLOCK.sub("", markdown)
    return HTML_IMAGE.findall(text) + MARKDOWN_IMAGE.findall(text)


class TestInstallSection(unittest.TestCase):
    def test_both_readmes_show_the_three_ways_to_install(self) -> None:
        for name in README_NAMES:
            text = read(name)
            for command in INSTALL_COMMANDS:
                with self.subTest(readme=name, command=command):
                    self.assertIn(command, text)

    def test_neither_readme_says_uvx_or_pipx_waits_for_the_first_pypi_release(self) -> None:
        for name in README_NAMES:
            text = read(name).lower()
            waiting = [note for note in WAITING_FOR_FIRST_RELEASE if note.lower() in text]
            with self.subTest(readme=name):
                self.assertEqual(waiting, [], "the package is on PyPI since 1.4.2: uvx and pipx wait for no release")


class TestMcpEntries(unittest.TestCase):
    """The entries a reader pastes start the package the way `mcp register` writes it: `uvx smart-drive-os mcp`."""

    @staticmethod
    def expected_args() -> List[str]:
        from smart_drive.mcp.registrar import DIST_NAME  # imported here so a missing constant fails this test only

        return [DIST_NAME, "mcp"]

    def test_every_json_entry_runs_the_package_through_uvx(self) -> None:
        for name in README_NAMES:
            blocks = [block for block in fenced_blocks(read(name), "json") if "mcpServers" in block]
            with self.subTest(readme=name):
                self.assertTrue(blocks, "the README has no JSON example with an mcpServers entry")
                for block in blocks:
                    entry = json.loads(block)["mcpServers"]["smart-drive"]
                    self.assertEqual(entry["command"], "uvx")
                    self.assertEqual(entry["args"], self.expected_args())

    @unittest.skipIf(tomllib is None, _NO_TOML)
    def test_the_toml_entry_runs_the_package_through_uvx(self) -> None:
        for name in README_NAMES:
            blocks = [block for block in fenced_blocks(read(name), "toml") if "mcp_servers" in block]
            with self.subTest(readme=name):
                self.assertTrue(blocks, "the README has no TOML example with an mcp_servers entry")
                for block in blocks:
                    entry = tomllib.loads(block)["mcp_servers"]["smart-drive"]
                    self.assertEqual(entry["command"], "uvx")
                    self.assertEqual(entry["args"], self.expected_args())


class TestPypiDescription(unittest.TestCase):
    """README.md is what PyPI shows as the project description."""

    def test_the_image_check_tells_relative_paths_from_absolute_urls(self) -> None:
        sample = (
            '<p align="center"><img src="docs/banner.jpg" alt="banner" width="100%"></p>\n'
            "![logo](docs/logo.png) and ![](other.png)\n"
            "[![CI](https://img.shields.io/a.svg?branch=main)](LICENSE)\n"
            "```bash\n![in a code block](x.png)\n```\n"
        )
        self.assertEqual(
            sorted(image_urls(sample)),
            sorted(["docs/banner.jpg", "docs/logo.png", "other.png", "https://img.shields.io/a.svg?branch=main"]),
        )

    def test_every_image_has_an_absolute_url(self) -> None:
        urls = image_urls(read("README.md"))
        self.assertTrue(urls, "README.md has no image at all: the pattern no longer finds them")
        relative = [url for url in urls if not url.startswith("https://")]
        self.assertEqual(relative, [], "PyPI cannot load a relative image: use an https:// URL")


class TestReleasingGuide(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(RELEASING.is_file(), "docs/RELEASING.md is missing")
        self.text = RELEASING.read_text(encoding="utf-8")

    def test_it_names_what_the_one_time_setup_depends_on(self) -> None:
        for term in SETUP_TERMS:
            with self.subTest(term=term):
                self.assertIn(term, self.text)

    def test_it_registers_the_publisher_under_the_names_the_workflow_uses(self) -> None:
        for field in ("`smart-drive-os`", "`DuongNAD`", "`release.yml`", "`pypi`"):
            with self.subTest(field=field):
                self.assertIn(field, self.text)

    def test_every_file_it_names_exists(self) -> None:
        named = sorted(set(BACKTICKED_PATH.findall(self.text)))
        self.assertTrue(named, "the guide names no file at all: the pattern no longer finds them")
        missing = [path for path in named if not (ROOT / path).is_file()]
        self.assertEqual(missing, [], "docs/RELEASING.md names files that do not exist")


if __name__ == "__main__":
    unittest.main()
