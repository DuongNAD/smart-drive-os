"""tests.test_security_policy - SECURITY.md says where to report a problem privately, and the other documents point to it.

GitHub shows SECURITY.md on the repository's Security tab, and a reporter who lost data or saw a path escape is sent
there. A policy that sends the report nowhere is the worst way for it to fail, so these checks pin what a later edit
could break without anything else noticing:

* the headings are there, and every link to a heading and to another file of the repository still lands;
* the two private channels are real: this repository's private-report form, and the address pyproject.toml publishes as
  the author's (PyPI shows it to everyone), so a typo in either cannot go unnoticed;
* the four kinds of security issue are named in bold, because the README, PRIVACY.md and the issue forms refer to them
  by those names;
* the supported-versions table marks the current minor series of pyproject.toml and no other one, so a release that
  bumps the version without revisiting the policy fails here;
* README.md, README_VN.md and PRIVACY.md link to SECURITY.md in the section that talks about security, and PRIVACY.md
  names the same private channels, so the documents cannot send a reporter to different places.

Everything is read as text: tomllib does not exist before Python 3.11 and CI installs nothing.
"""

from __future__ import annotations

import re
import unicodedata
import unittest
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent

ADVISORY_URL = "https://github.com/DuongNAD/smart-drive-os/security/advisories/new"
HEADINGS = ("Report a problem privately", "Supported versions", "What counts as a security issue")

# The four kinds of security issue, as SECURITY.md writes them in bold.
KINDS = ("Data loss.", "Path escape in MCP tools.", "Writing outside the drive.", "Config corruption.")

# The documents that link SECURITY.md, each with the heading of the section that has to carry the link.
LINKING_SECTIONS = (
    ("README.md", "Privacy, Security & Data Isolation"),
    ("README_VN.md", "Bảo Mật & Quyền Riêng Tư Dữ Liệu"),
    ("PRIVACY.md", "Security Vulnerability Reporting"),
)

SUPPORTED = "✅"  # white heavy check mark
UNSUPPORTED = "❌"  # cross mark

HEADING_LINE = re.compile(r"^#{1,6} (.+?)\s*$", re.MULTILINE)
LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
URL_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def read(name: str) -> str:
    """A repository file as NFC text: the Vietnamese headings are matched letter by letter."""
    return unicodedata.normalize("NFC", (ROOT / name).read_text(encoding="utf-8"))


def section(markdown: str, title: str) -> str:
    """The text from the `## ` heading that contains `title` up to the next `## ` heading, or to the end.

    A line inside a fenced code block is no heading: a shell comment such as `## note` does not end a section.
    """
    lines = markdown.splitlines()
    start = None
    fenced = False
    for number, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            if start is not None:
                return "\n".join(lines[start:number])
            if title in line:
                start = number
    if start is None:
        raise AssertionError(f"no '## ' heading contains {title!r}")
    return "\n".join(lines[start:])


def table_rows(markdown: str) -> List[List[str]]:
    """The body rows of the Markdown table in the text, each as its stripped cells; header and rule row are left out."""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in markdown.splitlines()
        if line.lstrip().startswith("|")
    ]
    if len(rows) < 3 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        raise AssertionError("expected a table with a header row, a rule row and at least one row")
    return rows[2:]


def github_slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces turned into hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def project_table() -> str:
    """The [project] table of pyproject.toml, cut out by hand (tomllib only exists from Python 3.11)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return text.split("\n[project]", 1)[1].split("\n[", 1)[0]


def pyproject_version() -> str:
    """The `version` of the [project] table."""
    match = re.search(r'^version\s*=\s*"([^"]+)"', project_table(), re.MULTILINE)
    if match is None:
        raise AssertionError("pyproject.toml has no version in its [project] table")
    return match.group(1)


def author_emails() -> List[str]:
    """The `email` of every entry of `authors = [...]` in the [project] table."""
    block = re.search(r"^authors\s*=\s*\[(.*?)^\]", project_table(), re.MULTILINE | re.DOTALL)
    if block is None:
        raise AssertionError("pyproject.toml has no authors list in its [project] table")
    return re.findall(r'email\s*=\s*"([^"]+)"', block.group(1))


def contact_address(text: str) -> str:
    """The one address SECURITY.md opens in a mail program."""
    contacts = set(re.findall(r"\(mailto:([^)\s]+)\)", text))
    if len(contacts) != 1:
        raise AssertionError(f"SECURITY.md should link exactly one address, found {sorted(contacts)}")
    return contacts.pop()


class TestSecurityPolicy(unittest.TestCase):
    """What SECURITY.md itself has to say."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.path = ROOT / "SECURITY.md"
        cls.text = read("SECURITY.md") if cls.path.is_file() else ""

    def test_the_file_exists_at_the_repository_root(self) -> None:
        """GitHub only shows SECURITY.md from the root, from .github/ or from docs/; the root is where people look."""
        self.assertTrue(self.path.is_file(), f"missing {self.path}")

    def test_the_headings_are_there(self) -> None:
        """Someone who skims for 'how do I report this' finds the three parts, under exactly these names."""
        headings = re.findall(r"^## (.+?)\s*$", self.text, re.MULTILINE)
        for title in HEADINGS:
            with self.subTest(heading=title):
                self.assertIn(title, headings)

    def test_every_link_inside_the_page_points_at_a_heading(self) -> None:
        """`#what-counts-as-a-security-issue` goes nowhere once the heading is renamed."""
        slugs = {github_slug(title) for title in HEADING_LINE.findall(self.text)}
        anchors = [target[1:] for target in LINK_TARGET.findall(self.text) if target.startswith("#")]
        self.assertTrue(anchors, "the page links none of its headings, so this check read nothing")
        for anchor in anchors:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, slugs)

    def test_every_relative_link_points_at_a_file_of_the_repository(self) -> None:
        """PRIVACY.md is linked for the privacy promise; a renamed file would leave a 404 on GitHub."""
        targets = [
            target
            for target in LINK_TARGET.findall(self.text)
            if not target.startswith("#") and not URL_SCHEME.match(target)
        ]
        self.assertIn("PRIVACY.md", targets)
        for target in targets:
            with self.subTest(target=target):
                self.assertTrue((ROOT / target).is_file(), f"{target} is not a file of the repository")

    def test_the_private_report_form_of_this_repository_is_linked(self) -> None:
        """The first way to report is GitHub's private form, and it sits in the reporting section."""
        self.assertIn(ADVISORY_URL, section(self.text, "Report a problem privately"))

    def test_the_contact_address_is_the_one_pyproject_publishes(self) -> None:
        """The fallback is the author's address of the package metadata, linked under its own name."""
        contact = contact_address(self.text)
        self.assertIn(f"[{contact}](mailto:{contact})", section(self.text, "Report a problem privately"))
        emails = author_emails()
        self.assertTrue(emails, "pyproject.toml has no author address, so this check read nothing")
        self.assertIn(contact, emails, "PyPI shows the authors' address; the policy has to name the same one")

    def test_the_four_kinds_of_security_issue_are_named_in_bold(self) -> None:
        """The README, PRIVACY.md and the issue forms refer to these four kinds, so the names must not drift."""
        kinds = section(self.text, "What counts as a security issue")
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertIn(f"- **{kind}**", kinds)

    def test_the_supported_versions_table_marks_the_current_minor_series_only(self) -> None:
        """Fixes are for the latest minor version: bumping 1.4.x to 1.5.x without the table fails here."""
        version = pyproject_version()
        major, minor = version.split(".")[:2]
        series = f"{major}.{minor}.x"
        rows = table_rows(section(self.text, "Supported versions"))
        supported = [cells[0] for cells in rows if SUPPORTED in cells[1]]
        self.assertEqual(supported, [series], f"pyproject.toml is at {version}: only {series} may be marked supported")
        for cells in rows:
            with self.subTest(row=cells[0]):
                self.assertTrue(SUPPORTED in cells[1] or UNSUPPORTED in cells[1], "a row is neither supported nor not")


class TestDocumentsPointToThePolicy(unittest.TestCase):
    """The places where a reader looks for how to report a problem."""

    def test_the_security_section_of_each_document_links_the_policy(self) -> None:
        """README.md, README_VN.md and PRIVACY.md all send a reporter to SECURITY.md from their security section."""
        for name, title in LINKING_SECTIONS:
            with self.subTest(file=name):
                self.assertRegex(section(read(name), title), r"\[[^\]]+\]\(SECURITY\.md\)")

    def test_privacy_md_names_the_same_private_channels(self) -> None:
        """Two documents that say how to report must not say different things: same form, same address."""
        body = section(read("PRIVACY.md"), "Security Vulnerability Reporting")
        self.assertIn(ADVISORY_URL, body)
        self.assertIn(contact_address(read("SECURITY.md")), body)


class TestHelpers(unittest.TestCase):
    """The text readers behind the checks above, on small examples."""

    MARKDOWN = (
        "# Title\n\n## One\n\nbody one\n\n```bash\n## not a heading\n```\n\n## Two\n\nbody two\n\n"
        "| Version | Supported |\n|---|---|\n| 1.4.x | yes |\n| older | no |\n\n## Three\n\nbody three\n"
    )

    def test_section_stops_at_the_next_heading_and_ignores_fenced_comments(self) -> None:
        one = section(self.MARKDOWN, "One")
        self.assertIn("body one", one)
        self.assertIn("## not a heading", one)
        self.assertNotIn("body two", one)
        self.assertIn("body three", section(self.MARKDOWN, "Three"))

    def test_section_of_the_last_heading_runs_to_the_end_and_a_missing_one_is_an_error(self) -> None:
        self.assertTrue(section(self.MARKDOWN, "Three").endswith("body three"))
        with self.assertRaises(AssertionError):
            section(self.MARKDOWN, "Four")

    def test_table_rows_drops_the_header_and_the_rule(self) -> None:
        self.assertEqual(table_rows(section(self.MARKDOWN, "Two")), [["1.4.x", "yes"], ["older", "no"]])
        with self.assertRaises(AssertionError):
            table_rows("| only | a header |\n")

    def test_github_slug_matches_the_anchors_github_makes(self) -> None:
        self.assertEqual(github_slug("What counts as a security issue"), "what-counts-as-a-security-issue")
        self.assertEqual(github_slug("Privacy, Security & Data Isolation"), "privacy-security--data-isolation")


if __name__ == "__main__":
    unittest.main()
