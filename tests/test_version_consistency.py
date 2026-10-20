"""tests.test_version_consistency - the package, the MCP server, the READMEs and the changelog name one version.

The number lives in pyproject.toml, smart_drive/__init__.py and smart_drive/mcp/server.py (SERVER_VERSION, which an MCP
client sees in serverInfo). A release that bumps only some of them tells coding agents, pip and readers different
things, so they are compared here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import smart_drive
from smart_drive.mcp.server import SERVER_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def pyproject_version() -> str:
    """The `version` of the [project] table (tomllib only exists from Python 3.11, so the table is cut out by hand)."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("\n[project]", 1)[1].split("\n[", 1)[0]
    match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
    if match is None:
        raise AssertionError("pyproject.toml has no version in its [project] table")
    return match.group(1)


class TestVersionConsistency(unittest.TestCase):
    def test_pyproject_package_and_mcp_server_versions_agree(self) -> None:
        versions = {
            "pyproject.toml": pyproject_version(),
            "smart_drive.__version__": smart_drive.__version__,
            "smart_drive.mcp.server.SERVER_VERSION": SERVER_VERSION,
        }
        self.assertEqual(len(set(versions.values())), 1, f"the three version strings differ: {versions}")
        for source, version in versions.items():
            with self.subTest(source=source):
                self.assertRegex(version, SEMVER)

    def test_readme_release_badges_show_that_version(self) -> None:
        version = smart_drive.__version__
        for name in ("README.md", "README_VN.md"):
            with self.subTest(readme=name):
                text = (REPO_ROOT / name).read_text(encoding="utf-8")
                self.assertIn(f"-v{version}-blue.svg", text)
                self.assertIn(f"/releases/tag/v{version})", text)
                # No badge may still show another version.
                self.assertEqual(set(re.findall(r"/releases/tag/v([0-9.]+)\)", text)), {version})

    def test_changelog_latest_release_is_that_version(self) -> None:
        text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        releases = [name for name in re.findall(r"^## \[([^\]]+)\]", text, re.MULTILINE) if name != "Unreleased"]
        self.assertTrue(releases, "CHANGELOG.md has no released version")
        self.assertEqual(releases[0], smart_drive.__version__)
        self.assertEqual(len(releases), len(set(releases)), "a version appears twice in CHANGELOG.md")


if __name__ == "__main__":
    unittest.main()
