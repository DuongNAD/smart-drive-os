"""tests.test_beta_testing_doc - docs/BETA_TESTING.md stays a checklist a tester can follow, and one that cannot hurt.

The first beta testers follow docs/BETA_TESTING.md line by line, on a Mac, on Linux and on Windows, each with a drive of
their own. Nothing here can run the steps on their machines (CI installs nothing and has no drive to spare), so the
checks pin what a later edit could break without anything else noticing:

* the file exists, its numbered steps carry their minutes and add up to the 15 the checklist promises, and the notes for
  Windows, macOS, Linux and exFAT drives are there with something in them;
* every `smart-drive` command the checklist shows, in a code block or in a code span, is accepted by the CLI's own
  argument parser, so a renamed option fails here and not on a stranger's terminal in the first minute. So is every such
  command in SECURITY.md, CONTRIBUTING.md and the issue forms (read as text: CI has no YAML library), which send the
  reader to the same commands. A reader that finds nothing would pass anything, so each of them has to find some;
* it cannot touch the real drive: every `--apply` line names the scratch folder with `--root "$SCRATCH"` and never
  `$DRIVE`, every command that reads or changes a drive names it with `--root` (an environment variable must not decide
  which drive an `--apply` hits), and the snapshot that proves the round trip names the folders it covers (without
  `--partitions` it covers none of the copies and says `Files: 0`);
* its links land: the files it links exist, the anchor into the README is a heading of the README, the issue-form links
  name forms of .github/ISSUE_TEMPLATE/, and a link to a blank issue only works while config.yml keeps blank issues on;
* GitHub's Markdown hides a word in angle brackets that is not code (`<user>`), and a code span with a backtick missing
  would hide commands from the checks above, so neither may be left in the text.

The readers and the checkers are tried on samples first: one that stops finding things, or stops refusing them, fails.
"""

from __future__ import annotations

import contextlib
import io
import re
import shlex
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from smart_drive.cli.main import build_parser

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "BETA_TESTING.md"
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
ISSUES_URL = "https://github.com/DuongNAD/smart-drive-os/issues/new"

# The other documents that send the reader to `smart-drive` commands: they go stale like the checklist does.
OTHER_DOCUMENTS = ("SECURITY.md", "CONTRIBUTING.md")

MIN_COMMANDS = 8  # the checklist shows at least this many `smart-drive` commands
MIN_STEPS = 5
BUDGET_MINUTES = 15  # what the introduction promises
NOTES_HEADING = "Notes for your system"
REQUIRED_NOTES = ("Windows", "macOS", "Linux", "exFAT drives")

FENCE = "```"
CODE_SPAN = re.compile(r"`([^`\n]+)`")
STEP_HEADING = re.compile(r"^## (\d+)\. (.+?)\s*$")
MINUTES = re.compile(r"\((\d+) min\b")
URL_OR_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:|//", re.IGNORECASE)


def split_fences(markdown: str) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    """The fenced code blocks of the text as (language, lines) and the lines outside them.

    A line that starts with three backticks opens a block and the next such line closes it, so a `## ` or a backtick
    inside a block is no heading and no code span of the text around it.
    """
    blocks: List[Tuple[str, List[str]]] = []
    prose: List[str] = []
    language: Optional[str] = None
    current: List[str] = []
    for line in markdown.splitlines():
        if line.strip().startswith(FENCE):
            if language is None:
                language, current = line.strip()[len(FENCE) :].strip().lower(), []
            else:
                blocks.append((language, current))
                language = None
            continue
        if language is None:
            prose.append(line)
        else:
            current.append(line)
    if language is not None:
        raise AssertionError("a fenced code block is never closed")
    return blocks, prose


def code_lines(markdown: str) -> List[str]:
    """The lines inside the fenced code blocks, stripped, without the blank ones."""
    blocks, _ = split_fences(markdown)
    return [line.strip() for _, body in blocks for line in body if line.strip()]


def command_lines(markdown: str) -> List[str]:
    """The lines of the code blocks that start a `smart-drive` command."""
    return [line for line in code_lines(markdown) if line.startswith("smart-drive ")]


def inline_commands(markdown: str) -> List[str]:
    """The code spans outside the code blocks that are a `smart-drive` command, like `smart-drive doctor --report`."""
    _, prose = split_fences(markdown)
    return [span for span in CODE_SPAN.findall("\n".join(prose)) if span.startswith("smart-drive ")]


def form_commands(text: str) -> List[str]:
    """The commands in an issue form, read as text: the code spans of its descriptions and its placeholder line."""
    spans = [span for span in CODE_SPAN.findall(text) if span.startswith("smart-drive ")]
    return spans + re.findall(r"^\s*placeholder:\s*(smart-drive .+?)\s*$", text, re.MULTILINE)


def parse_problem(command: str) -> Optional[str]:
    """Why the CLI's argument parser refuses the command, or None when it takes it.

    The line is split the way a shell splits it (a trailing `# comment` goes) and the parser gets everything after
    `smart-drive`. argparse prints its complaint and exits: both are caught here, and the complaint is the answer.
    """
    try:
        words = shlex.split(command, comments=True)
    except ValueError as err:
        return f"a shell cannot split it into words ({err})"
    if not words or words[0] != "smart-drive":
        return "it does not start with smart-drive"
    complaint = io.StringIO()
    try:
        with contextlib.redirect_stderr(complaint), contextlib.redirect_stdout(io.StringIO()):
            build_parser().parse_args(words[1:])
    except SystemExit:
        said = [line for line in complaint.getvalue().splitlines() if line.strip()]
        return said[-1] if said else "the parser exited without a complaint"
    return None


def option_value(words: List[str], option: str) -> Optional[str]:
    """The value of `--option value` or `--option=value` among the words; None when it is not there."""
    for number, word in enumerate(words):
        if word == option:
            return words[number + 1] if number + 1 < len(words) else None
        if word.startswith(option + "="):
            return word[len(option) + 1 :]
    return None


def safety_problems(lines: List[str]) -> List[str]:
    """What the code-block lines could do to the real drive: each problem names the line.

    A line with `--apply` must name the scratch folder, with `--root`, and never the drive. A `smart-drive` command
    that reads or changes a drive (all but `mcp register`, which edits agent configs) must name it with `--root`. The
    other lines (`$DRIVE = "E:\\"`, `Copy-Item ...`) are no commands of ours, and a shell may not even split them.
    """
    problems: List[str] = []
    for line in lines:
        if "--apply" not in line and not line.startswith("smart-drive "):
            continue
        try:
            words = shlex.split(line, comments=True)
        except ValueError as err:
            problems.append(f"{line}: a shell cannot split it into words ({err})")
            continue
        root = option_value(words, "--root")
        if "--apply" in line:
            if "$SCRATCH" not in line:
                problems.append(f"{line}: --apply without $SCRATCH")
            if "$DRIVE" in line:
                problems.append(f"{line}: --apply with $DRIVE")
            if root != "$SCRATCH":
                problems.append(f"{line}: --apply whose --root is not $SCRATCH")
        if line.startswith("smart-drive ") and words[1:3] != ["mcp", "register"] and root is None:
            problems.append(f"{line}: it does not name its drive or folder with --root")
        if words[1:3] == ["snapshot", "create"] and option_value(words, "--partitions") is None:
            problems.append(f"{line}: the snapshot covers none of the copies without --partitions")
    return problems


def github_slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces turned into hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def anchors(markdown: str) -> List[str]:
    """The anchors of the headings of a Markdown text (the lines of code blocks are no headings)."""
    _, prose = split_fences(markdown)
    headings = [re.match(r"#{1,6} (.+?)\s*$", line) for line in prose]
    return [github_slug(match.group(1)) for match in headings if match]


def repo_links(markdown: str) -> List[str]:
    """The targets of the Markdown links that point into the repository: no scheme, no `//`."""
    _, prose = split_fences(markdown)
    targets = re.findall(r"\]\(([^)\s]+)\)", "\n".join(prose))
    return [target for target in targets if not URL_OR_SCHEME.match(target)]


def template_links(markdown: str) -> List[str]:
    """The names of the issue forms the text links: `.../issues/new?template=<name>`."""
    return re.findall(re.escape(ISSUES_URL) + r"\?template=([\w.-]+)", markdown)


def links_blank_issue(markdown: str) -> bool:
    """Whether the text links the blank issue page: `.../issues/new)`."""
    return re.search(re.escape(ISSUES_URL) + r"\)", markdown) is not None


def blank_issues_enabled(config: str) -> bool:
    """Whether the text of config.yml turns blank issues on."""
    return re.search(r"^blank_issues_enabled:\s*true\s*$", config, re.MULTILINE) is not None


def steps(markdown: str) -> List[Tuple[int, str]]:
    """The numbered step headings (`## 3. Preview an organize (2 min, read-only)`) as (number, rest of the heading)."""
    _, prose = split_fences(markdown)
    found = [STEP_HEADING.match(line) for line in prose]
    return [(int(match.group(1)), match.group(2)) for match in found if match]


def minutes(heading: str) -> Optional[int]:
    """The `N` of the `(N min` in a step heading; None when it has none."""
    match = MINUTES.search(heading)
    return int(match.group(1)) if match else None


def notes(markdown: str) -> Dict[str, List[str]]:
    """The `### ` subsections of the `## Notes for your system` section, each with its non-blank lines."""
    _, prose = split_fences(markdown)
    sections: Dict[str, List[str]] = {}
    inside, current = False, None
    for line in prose:
        if line.startswith("## "):
            inside, current = line[3:].strip() == NOTES_HEADING, None
        elif inside and line.startswith("### "):
            current = line[4:].strip()
            sections[current] = []
        elif inside and current is not None and line.strip():
            sections[current].append(line.strip())
    return sections


def hidden_tags(markdown: str) -> List[str]:
    """The `<word>` tags outside code: GitHub's Markdown drops them from the page."""
    _, prose = split_fences(markdown)
    return re.findall(r"<[A-Za-z/][^>\n]*>", CODE_SPAN.sub("", "\n".join(prose)))


def open_code_spans(markdown: str) -> List[str]:
    """The lines of prose with an odd number of backticks: a span left open pairs the wrong backticks."""
    _, prose = split_fences(markdown)
    return [line for line in prose if line.count("`") % 2]


def read(path: Path) -> str:
    """A repository file as text; an empty text when it is missing (the existence check says so)."""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


SAMPLE = (
    "# Title\n"
    "\n"
    "Run `smart-drive doctor --report`, not `smart-drive` alone or `pipx install smart-drive-os`.\n"
    "\n"
    "## 1. Look (2 min, read-only)\n"
    "\n"
    "```bash\n"
    "DRIVE=/Volumes/T7\n"
    'smart-drive checkup --root "$DRIVE"   # a comment\n'
    '  smart-drive undo --root "$SCRATCH" --apply\n'
    "pipx install smart-drive-os\n"
    "## not a heading, and `not a span\n"
    "```\n"
    "\n"
    "## 2. Move (3 min)\n"
    "\n"
    "```\n"
    'smart-drive organize --root "$SCRATCH"\n'
    "```\n"
    "\n"
    "## Notes for your system\n"
    "\n"
    "### Windows\n"
    "\n"
    "- Use PowerShell. See [the README](../README.md#use-with-coding-agents-mcp)"
    " and [the web](https://example.test/x).\n"
    "\n"
    "### macOS\n"
    "\n"
    "- Brew.\n"
)


class TestReaders(unittest.TestCase):
    """The functions that read the documents, on a sample whose answer is known."""

    def test_command_lines_are_the_lines_of_code_blocks_that_start_a_command(self) -> None:
        self.assertEqual(
            command_lines(SAMPLE),
            [
                'smart-drive checkup --root "$DRIVE"   # a comment',
                'smart-drive undo --root "$SCRATCH" --apply',
                'smart-drive organize --root "$SCRATCH"',
            ],
        )

    def test_inline_commands_are_the_code_spans_outside_code_blocks_that_start_a_command(self) -> None:
        self.assertEqual(inline_commands(SAMPLE), ["smart-drive doctor --report"])

    def test_a_block_that_is_never_closed_is_refused(self) -> None:
        with self.assertRaises(AssertionError):
            split_fences("```bash\nsmart-drive checkup\n")

    def test_a_form_is_read_as_text(self) -> None:
        text = (
            '      description: "Run `smart-drive doctor --report` and paste `<user>` here."\n'
            "      placeholder: smart-drive organize --root /Volumes/T7 --apply\n"
            "      description: The command smart-drive organize, without backticks\n"
        )
        self.assertEqual(
            form_commands(text),
            ["smart-drive doctor --report", "smart-drive organize --root /Volumes/T7 --apply"],
        )

    def test_the_step_headings_and_the_notes_are_found(self) -> None:
        self.assertEqual(steps(SAMPLE), [(1, "Look (2 min, read-only)"), (2, "Move (3 min)")])
        self.assertEqual([minutes(rest) for _, rest in steps(SAMPLE)], [2, 3])
        self.assertIsNone(minutes("Look (soon)"))
        self.assertEqual(list(notes(SAMPLE)), ["Windows", "macOS"])
        self.assertEqual(notes(SAMPLE)["macOS"], ["- Brew."])

    def test_links_into_the_repository_are_told_from_urls(self) -> None:
        self.assertEqual(repo_links(SAMPLE), ["../README.md#use-with-coding-agents-mcp"])

    def test_the_slug_is_the_one_github_gives(self) -> None:
        self.assertEqual(github_slug("Use with coding agents (MCP)"), "use-with-coding-agents-mcp")
        self.assertIn("use-with-coding-agents-mcp", anchors("## Use with coding agents (MCP)\n\ntext\n"))
        self.assertNotIn("not-a-heading", anchors(SAMPLE))

    def test_issue_links_are_read(self) -> None:
        text = f"[a]({ISSUES_URL}?template=bug_report.yml) and [b]({ISSUES_URL}) and [c]({ISSUES_URL}/choose)"
        self.assertEqual(template_links(text), ["bug_report.yml"])
        self.assertTrue(links_blank_issue(text))
        self.assertFalse(links_blank_issue(f"[a]({ISSUES_URL}?template=bug_report.yml)"))
        self.assertTrue(blank_issues_enabled("# note\nblank_issues_enabled: true\n"))
        self.assertFalse(blank_issues_enabled("blank_issues_enabled: false\n"))
        self.assertFalse(blank_issues_enabled("# blank_issues_enabled: true\n"))

    def test_angle_bracket_words_and_open_spans_are_found_only_outside_code(self) -> None:
        self.assertEqual(hidden_tags("as <user>, or as `<user>`\n```\n<tag>\n```\n"), ["<user>"])
        self.assertEqual(open_code_spans("fine `a` and `b`\nopen `a\n```\nin a block `\n```\n"), ["open `a"])


class TestParseProblem(unittest.TestCase):
    """The check that the CLI takes a command: it takes the ones it has and refuses the others."""

    def test_it_accepts_commands_the_cli_has(self) -> None:
        for command in (
            'smart-drive organize --root "$SCRATCH" --apply',
            'smart-drive snapshot create before --root "$SCRATCH" --partitions "demo-project,my-notes"',
            'smart-drive snapshot verify before --root "$SCRATCH"',
            "smart-drive mcp register --all",
            "smart-drive doctor --report",
            "smart-drive doctor --report   # a trailing comment goes",
        ):
            with self.subTest(command=command):
                self.assertIsNone(parse_problem(command))

    def test_it_refuses_what_the_cli_refuses(self) -> None:
        for command in (
            "smart-drive nosuchcommand",
            'smart-drive organize --root "$SCRATCH" --nosuchoption',
            "smart-drive organize --root",
            "smart-drive snapshot delete before",
            'smart-drive organize --root "unclosed',
            "pipx install smart-drive-os",
            "",
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(parse_problem(command))

    def test_it_says_what_the_parser_said(self) -> None:
        self.assertIn("invalid choice", parse_problem("smart-drive nosuchcommand") or "")


class TestSafetyProblems(unittest.TestCase):
    """The check that the checklist cannot touch the real drive."""

    def test_the_scratch_folder_passes(self) -> None:
        lines = [
            'smart-drive organize --root "$SCRATCH"',
            'smart-drive organize --root "$SCRATCH" --apply',
            'smart-drive undo --root "$SCRATCH" --apply',
            'smart-drive snapshot create before --root "$SCRATCH" --partitions "demo-project,my-notes"',
            'smart-drive organize --apply --root="$SCRATCH"',
            'smart-drive checkup --root "$DRIVE"',
            "smart-drive mcp register --all",
            'Copy-Item -Recurse C:\\path\\to\\a-small-project "$SCRATCH\\demo-project"',
            '$DRIVE = "E:\\"',
            "pipx install smart-drive-os",
        ]
        self.assertEqual(safety_problems(lines), [])

    def test_the_drive_and_a_missing_root_do_not(self) -> None:
        cases = (
            ('smart-drive organize --root "$DRIVE" --apply', "--apply with $DRIVE"),
            ('smart-drive organize --root "$DRIVE" --apply', "--apply without $SCRATCH"),
            ("smart-drive organize --apply", "--apply without $SCRATCH"),
            ("smart-drive organize --apply", "does not name its drive"),
            ('smart-drive organize --apply # $SCRATCH', "--apply whose --root is not $SCRATCH"),
            ('smart-drive undo --root "$SCRATCH" --root "$DRIVE" --apply', "--apply with $DRIVE"),
            ('uvx smart-drive-os organize --root "$DRIVE" --apply', "--apply without $SCRATCH"),
            ("smart-drive checkup", "does not name its drive"),
            ('smart-drive snapshot create before --root "$SCRATCH"', "covers none of the copies"),
            ('smart-drive organize --root "unclosed --apply', "cannot split"),
        )
        for line, expected in cases:
            with self.subTest(line=line, expected=expected):
                problems = safety_problems([line])
                self.assertTrue(any(expected in problem for problem in problems), f"{expected!r} not in {problems}")


class TestChecklist(unittest.TestCase):
    """docs/BETA_TESTING.md itself."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.text = read(DOC)

    def test_the_file_exists(self) -> None:
        self.assertTrue(DOC.is_file(), f"missing {DOC}")

    def test_its_numbered_steps_add_up_to_the_minutes_it_promises(self) -> None:
        found = steps(self.text)
        self.assertGreaterEqual(len(found), MIN_STEPS, f"steps found: {found}")
        numbers = [number for number, _ in found]
        self.assertEqual(numbers, list(range(1, len(found) + 1)), "the steps are not numbered 1, 2, 3...")
        for number, rest in found:
            with self.subTest(step=number):
                self.assertIsNotNone(minutes(rest), f"step {number} has no '(N min' in its heading: {rest!r}")
        total = sum(minutes(rest) or 0 for _, rest in found)
        self.assertLessEqual(total, BUDGET_MINUTES, f"the steps add up to {total} minutes")

    def test_it_has_notes_for_each_system_with_something_in_them(self) -> None:
        sections = notes(self.text)
        for title in REQUIRED_NOTES:
            with self.subTest(notes=title):
                self.assertIn(title, sections, f"no '### {title}' under '## {NOTES_HEADING}' (found {list(sections)})")
                self.assertTrue(
                    any(line.startswith("- ") for line in sections.get(title, [])),
                    f"'### {title}' has no bullet",
                )

    def test_no_word_in_angle_brackets_is_left_outside_code(self) -> None:
        self.assertEqual(hidden_tags(self.text), [], "GitHub hides these: put them in backticks")

    def test_no_code_span_is_left_open(self) -> None:
        self.assertEqual(open_code_spans(self.text), [], "a backtick is missing in these lines")


class TestChecklistCommands(unittest.TestCase):
    """The commands the checklist shows are the CLI's."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.text = read(DOC)

    def test_it_shows_enough_commands_for_the_checks_below_to_mean_something(self) -> None:
        commands = command_lines(self.text) + inline_commands(self.text)
        self.assertGreaterEqual(len(commands), MIN_COMMANDS, f"commands found: {commands}")

    def test_every_command_line_of_a_code_block_is_accepted_by_the_cli(self) -> None:
        for line in command_lines(self.text):
            with self.subTest(line=line):
                self.assertIsNone(parse_problem(line), line)

    def test_every_command_in_a_code_span_is_accepted_by_the_cli(self) -> None:
        for span in inline_commands(self.text):
            with self.subTest(span=span):
                self.assertIsNone(parse_problem(span), span)


class TestOtherDocumentsCommands(unittest.TestCase):
    """The commands SECURITY.md, CONTRIBUTING.md and the issue forms hand to the reader are the CLI's too."""

    def test_every_command_is_accepted_by_the_cli(self) -> None:
        forms = sorted(path for path in TEMPLATES.glob("*.yml") if path.name != "config.yml")
        self.assertTrue(forms, f"no issue form in {TEMPLATES}")
        sources: List[Tuple[str, List[str]]] = []
        for name in OTHER_DOCUMENTS:
            text = read(ROOT / name)
            sources.append((name, command_lines(text) + inline_commands(text)))
        for path in forms:
            sources.append((path.name, form_commands(read(path))))
        for name, commands in sources:
            self.assertTrue(commands, f"{name} shows no smart-drive command: the reader found nothing")
            for command in commands:
                with self.subTest(file=name, command=command):
                    self.assertIsNone(parse_problem(command), command)


class TestChecklistSafety(unittest.TestCase):
    """Nothing in the checklist applies anything to the real drive."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.lines = code_lines(read(DOC))

    def test_it_applies_something_so_the_check_has_something_to_check(self) -> None:
        self.assertGreaterEqual(len([line for line in self.lines if "--apply" in line]), 2, f"lines: {self.lines}")

    def test_every_apply_line_names_the_scratch_folder_and_not_the_drive(self) -> None:
        self.assertEqual(safety_problems(self.lines), [])


class TestChecklistLinks(unittest.TestCase):
    """The links of the checklist land."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.text = read(DOC)

    def test_every_link_into_the_repository_lands(self) -> None:
        links = repo_links(self.text)
        self.assertTrue(links, "the checklist links no file of the repository: the reader found nothing")
        for target in links:
            path_part, _, fragment = target.partition("#")
            path = (DOC.parent / path_part).resolve() if path_part else DOC
            with self.subTest(link=target):
                self.assertTrue(path.is_file(), f"{target} points at {path}, which is not a file")
                if fragment:
                    self.assertIn(fragment, anchors(read(path)), f"{path.name} has no heading for #{fragment}")

    def test_every_issue_form_link_names_a_form_that_exists(self) -> None:
        names = template_links(self.text)
        self.assertIn("bug_report.yml", names, "step 7 sends a tester with a problem to the bug form")
        for name in names:
            with self.subTest(form=name):
                self.assertTrue((TEMPLATES / name).is_file(), f"{name} is not in {TEMPLATES}")

    def test_a_link_to_a_blank_issue_needs_blank_issues_to_be_on(self) -> None:
        if links_blank_issue(self.text):
            self.assertTrue(
                blank_issues_enabled(read(TEMPLATES / "config.yml")),
                "the checklist links a blank issue, but config.yml has blank_issues_enabled: false",
            )


if __name__ == "__main__":
    unittest.main()
