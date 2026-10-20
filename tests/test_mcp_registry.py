"""tests/test_mcp_registry.py - server.json lets the official MCP Registry list the PyPI package.

100% Python Standard Library unittest. Zero external dependencies. Nothing here publishes or needs the network.
The MCP Registry (registry.modelcontextprotocol.io) lists a server from the server.json at the repository
root, which points at a package on PyPI. Verifies:
1. The file: parses as JSON, names the 2025-12-11 schema, and keeps `name`, `title` and `description` inside
   the limits that schema sets (the name pattern; 100 characters for title and description). The namespace
   of the name is `io.github.<owner>/`, spelled like the owner in the repository URL: the registry keeps
   the case of the GitHub login.
2. Versions: the top-level `version` and the package `version` equal pyproject.toml's version, so a release
   bump cannot forget this file (the registry takes a concrete version, never a range or "latest").
3. The package entry: a `pypi` package on https://pypi.org (the only PyPI base URL the registry accepts);
   `identifier` is the distribution name and `[project.scripts]` has a script of that name (`uvx
   smart-drive-os` runs it); stdio transport; `mcp` as the one positional argument, the CLI's MCP subcommand.
4. Ownership: the registry accepts a PyPI package only when its description, which is README.md, contains
   `mcp-name: <server name>` followed by whitespace or `-->`.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    import tomllib
except ImportError:  # Python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:  # no TOML parser available: the tests that read pyproject.toml are skipped
        tomllib = None  # type: ignore

_NO_TOML = "TOML parsing needs Python 3.11+ (tomllib) or the tomli package"

SERVER_JSON_PATH = PROJECT_ROOT / "server.json"
README_PATH = PROJECT_ROOT / "README.md"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"

# The latest released schema of the registry's server.json.
SCHEMA_URL = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
# What that schema requires of `name`, and how long it lets `title` and `description` be.
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")
MAX_TEXT_LENGTH = 100


def load_server_json() -> Dict[str, Any]:
    with open(SERVER_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def mcp_name_marker(server_name: str) -> "re.Pattern[str]":
    """The `mcp-name: <server name>` text the registry looks for in the description of a PyPI package.

    The name must be followed by whitespace or `-->` (the marker sits in an HTML comment), so a longer name such as
    `<server name>-extra` does not vouch for this one.
    """
    return re.compile("mcp-name: " + re.escape(server_name) + r"(?:\s|-->)")


class TestServerJson(unittest.TestCase):
    """What server.json states, checked on its own."""

    def setUp(self) -> None:
        self.assertTrue(SERVER_JSON_PATH.is_file(), "server.json not found at the repository root")
        self.server = load_server_json()
        self.package = (self.server.get("packages") or [{}])[0]

    def test_parses_as_one_json_object(self) -> None:
        """server.json is UTF-8 JSON holding a single object."""
        self.assertIsInstance(self.server, dict)

    def test_schema_is_the_2025_12_11_release(self) -> None:
        """`$schema` names the latest released server.json schema, so editors and the registry check against it."""
        self.assertEqual(self.server.get("$schema"), SCHEMA_URL)

    def test_name_matches_the_registry_pattern(self) -> None:
        """`name` is `<reverse-DNS namespace>/<server>`, 3 to 200 characters, like the schema's pattern says."""
        name = self.server.get("name")
        self.assertIsInstance(name, str, "server.json needs a name")
        self.assertRegex(name, NAME_PATTERN)
        self.assertTrue(3 <= len(name) <= 200, f"name is {len(name)} characters, the schema allows 3 to 200")

    def test_name_namespace_is_the_github_owner_of_the_repository(self) -> None:
        """A GitHub login publishes under `io.github.<login>/`, and the registry keeps the case of the login."""
        repository = self.server.get("repository", {})
        self.assertEqual(repository.get("source"), "github")
        url = repository.get("url", "")
        owner = re.match(r"^https://github\.com/([A-Za-z0-9-]+)/[A-Za-z0-9._-]+/?$", url)
        self.assertIsNotNone(owner, f"repository.url must be a github.com repository URL, got {url!r}")
        self.assertEqual(self.server["name"].split("/")[0], f"io.github.{owner.group(1)}")

    def test_title_and_description_fit_the_registry_limit(self) -> None:
        """The schema allows 1 to 100 characters for `title` and for `description`."""
        for key in ("title", "description"):
            with self.subTest(field=key):
                value = self.server.get(key)
                self.assertIsInstance(value, str, f"server.json needs a {key}")
                self.assertTrue(
                    1 <= len(value) <= MAX_TEXT_LENGTH,
                    f"{key} is {len(value)} characters, the schema allows 1 to {MAX_TEXT_LENGTH}",
                )

    def test_describes_exactly_one_package(self) -> None:
        """The one package is the PyPI release; the tests below read packages[0]."""
        packages = self.server.get("packages")
        self.assertIsInstance(packages, list)
        self.assertEqual(len(packages), 1, f"expected one package, found {len(packages)}")

    def test_package_is_the_one_on_pypi_org(self) -> None:
        """`registryType` is `pypi` and the base URL is https://pypi.org, the only one the registry accepts."""
        self.assertEqual(self.package.get("registryType"), "pypi")
        self.assertEqual(self.package.get("registryBaseUrl"), "https://pypi.org")

    def test_package_starts_with_uvx_over_stdio(self) -> None:
        """An MCP client runs `uvx smart-drive-os mcp` and talks to it over stdin and stdout."""
        self.assertEqual(self.package.get("runtimeHint"), "uvx")
        self.assertEqual(self.package.get("transport"), {"type": "stdio"})

    def test_package_argument_is_the_mcp_subcommand(self) -> None:
        """The one positional argument is `mcp`: `smart-drive mcp` serves the MCP server on stdio."""
        self.assertEqual(self.package.get("packageArguments"), [{"type": "positional", "value": "mcp"}])

    def test_readme_carries_the_mcp_name_marker(self) -> None:
        """The registry verifies a PyPI package by finding `mcp-name: <server name>` in its description."""
        self.assertTrue(README_PATH.is_file(), "README.md not found at the repository root")
        name = self.server.get("name")
        readme = README_PATH.read_text(encoding="utf-8")
        self.assertIsNotNone(
            mcp_name_marker(name).search(readme),
            f"README.md needs a line `<!-- mcp-name: {name} -->` (the registry refuses a PyPI package without it)",
        )


@unittest.skipIf(tomllib is None, _NO_TOML)
class TestServerJsonMatchesPyproject(unittest.TestCase):
    """server.json repeats facts that pyproject.toml owns; they must not drift apart."""

    def setUp(self) -> None:
        self.assertTrue(SERVER_JSON_PATH.is_file(), "server.json not found at the repository root")
        self.server = load_server_json()
        self.package = (self.server.get("packages") or [{}])[0]
        with open(PYPROJECT_PATH, "rb") as f:
            self.project = tomllib.load(f).get("project", {})

    def test_versions_equal_the_pyproject_version(self) -> None:
        """A release bump must touch server.json too: the registry installs the version it names, never a range."""
        version = self.project.get("version")
        self.assertIsInstance(version, str, "[project] needs a version")
        self.assertEqual(self.server.get("version"), version, "server.json version differs from pyproject.toml")
        self.assertEqual(
            self.package.get("version"),
            version,
            "server.json packages[0].version differs from pyproject.toml",
        )

    def test_identifier_is_the_distribution_name_and_a_script_has_that_name(self) -> None:
        """`uvx smart-drive-os` installs the PyPI package of that name, then runs the script named like it."""
        name = self.project.get("name")
        self.assertEqual(self.package.get("identifier"), name)
        scripts = self.project.get("scripts", {})
        message = f"[project.scripts] needs a script named {name!r} for uvx, has {list(scripts)}"
        self.assertIn(name, scripts, message)

    def test_readme_is_the_package_description(self) -> None:
        """PyPI shows `[project].readme` as the description, so that is the file that must carry the marker."""
        readme = self.project.get("readme")
        readme_file = readme.get("file") if isinstance(readme, dict) else readme
        self.assertEqual(readme_file, README_PATH.name)


class TestMcpNameMarker(unittest.TestCase):
    """mcp_name_marker must accept the registry's spelling and refuse near misses, or the README test is void."""

    NAME = "io.github.DuongNAD/smart-drive-os"

    def test_accepts_an_html_comment_and_a_plain_line(self) -> None:
        marker = mcp_name_marker(self.NAME)
        for text in (
            f"<!-- mcp-name: {self.NAME} -->",
            f"<!-- mcp-name: {self.NAME}-->",
            f"mcp-name: {self.NAME}\n",
            f"mcp-name: {self.NAME}\r\nnext line",
            f"# Title\n\n<!-- mcp-name: {self.NAME} -->\n\ntext",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(marker.search(text))

    def test_refuses_near_misses(self) -> None:
        marker = mcp_name_marker(self.NAME)
        for text in (
            "",
            f"mcp-name:{self.NAME} ",  # no space after the colon
            f"mcp_name: {self.NAME} ",  # not the marker's spelling
            f"mcp-name: {self.NAME}-extra ",  # a longer name does not vouch for this one
            f"mcp-name: {self.NAME.lower()} ",  # the case of the login matters
            f"mcp-name: io.github.DuongNAD/other {self.NAME}",  # another server's marker
        ):
            with self.subTest(text=text):
                self.assertIsNone(marker.search(text))


if __name__ == "__main__":
    unittest.main()
