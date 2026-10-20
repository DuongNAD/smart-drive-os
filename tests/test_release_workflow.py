"""tests.test_release_workflow - the release workflow cannot publish by accident.

.github/workflows/release.yml builds the sdist and the wheel and smoke-tests the wheel on every push or pull request
that touches the packages, and publishes them to PyPI when a v* tag is pushed. PyPI takes a version once and never
gives it back, so these tests pin the guarantees that keep that last step deliberate:

* only a pushed v* tag reaches the publish job, and only after the build and every smoke test passed;
* the publish job is the only one that can get an OIDC token (Trusted Publishing) and it runs in the `pypi`
  environment, where the owner approves the deployment; it runs no script of its own, and no job can write to the
  repository;
* the files that get published are the ones the smoke tests installed, the three ways users install them;
* no PyPI token or password exists anywhere in the workflow;
* every action is pinned to a commit SHA, so a tag that is moved to other code cannot change what runs with that power;
* the repository files the workflow runs are there.

The standard library has no YAML parser, so the file is read as text: comments are dropped, the top-level keys are
split by indentation and the jobs are the 2-space-indented keys under `jobs:`. That is enough for the block-style YAML
of .github/workflows/tests.yml and release.yml, and it is all this reader understands.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

# The job that publishes, the jobs that have to succeed before it starts, and the action that uploads to PyPI.
PUBLISH_JOB = "publish-pypi"
SMOKE_JOB = "wheel-smoke"
BUILD_JOB = "dist"
PUBLISH_ACTION = "pypa/gh-action-pypi-publish"
NEEDED_BEFORE_PUBLISHING = (BUILD_JOB, SMOKE_JOB)

# What a job's `if:` has to say to be reachable only by a pushed v* tag (joined by `&&`, nothing that widens it).
TAG_PUSH_CONDITIONS = ("github.event_name == 'push'", "startsWith(github.ref, 'refs/tags/v')")

# The ways packaging/smoke_test.py installs the wheel: a virtual environment, uvx and pipx.
SMOKE_MODES = ("--wheel", "--uvx", "--pipx")

# Repository files the workflow runs, as paths from the repository root.
RUN_FILES = (
    "packaging/requirements-dist.txt",
    "packaging/requirements-smoke.txt",
    "packaging/smoke_test.py",
)

USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>.*))?$")
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
SMOKE_RUN = re.compile(r"packaging/smoke_test\.py\s+(--[a-z]+)\s+(\S+)")


def code_lines(text: str) -> list[str]:
    """The lines of a YAML file without blank lines and full-line comments; the indentation stays."""
    return [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def indent_of(line: str) -> int:
    """How many spaces a line is indented by."""
    return len(line) - len(line.lstrip(" "))


def child_blocks(lines: list[str], indent: int) -> dict[str, list[str]]:
    """Splits lines into the blocks of the `key:` entries written at exactly `indent` spaces.

    A block starts with its `key:` line and runs up to the next key at that indentation; deeper lines (a mapping, a
    `- item`, the text of a `run: |` script) stay in the block they belong to.
    """
    key = re.compile("^" + " " * indent + r"([A-Za-z_][\w.-]*):(?:\s|$)")
    blocks: dict[str, list[str]] = {}
    current = None
    for line in lines:
        match = key.match(line)
        if match:
            current = match.group(1)
            blocks[current] = [line]
        elif current is not None:
            blocks[current].append(line)
    return blocks


def strip_comment(text: str) -> str:
    """Drops a trailing ` # comment` (a ` #` inside a quoted value is not understood) and the surrounding spaces."""
    return re.sub(r"\s+#.*$", "", text).strip()


def unquote(text: str) -> str:
    """Removes one pair of matching quotes around a scalar."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def inline_value(block: list[str]) -> str:
    """What follows the colon on the `key: value` line that opens a block, without a comment or quotes."""
    return unquote(strip_comment(block[0].split(":", 1)[1]))


def condition(block: list[str]) -> str:
    """The expression of an `if:` line, without a trailing comment or a `${{ }}` wrapper (quotes in it stay)."""
    value = strip_comment(block[0].split(":", 1)[1])
    wrapped = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", value)
    return wrapped.group(1) if wrapped else value


def sequence(block: list[str]) -> list[str]:
    """The items under a key, written inline (`[a, "b"]`), as one bare value, or as `- item` lines."""
    inline = inline_value(block)
    if inline.startswith("[") and inline.endswith("]"):
        return [unquote(part.strip()) for part in inline[1:-1].split(",") if part.strip()]
    if inline:
        return [inline]
    return [unquote(strip_comment(line.strip()[2:])) for line in block[1:] if line.strip().startswith("- ")]


def permissions(keys: dict[str, list[str]]) -> dict[str, str]:
    """The `permissions:` of the workflow or of a job as {scope: level}; a bare value is {"*": value}."""
    block = keys.get("permissions")
    if block is None:
        return {}
    inline = inline_value(block)
    if inline:
        return {"*": inline}
    if len(block) == 1:
        return {}
    return {scope: inline_value(sub) for scope, sub in child_blocks(block[1:], indent_of(block[1])).items()}


def environment_name(job: dict[str, list[str]]) -> str | None:
    """The name of the environment a job deploys to (`environment: pypi` or `environment:` with `name: pypi`)."""
    block = job.get("environment")
    if block is None:
        return None
    inline = inline_value(block)
    if inline or len(block) == 1:
        return inline or None
    named = child_blocks(block[1:], indent_of(block[1])).get("name")
    return inline_value(named) if named else None


def steps_of(job: dict[str, list[str]]) -> list[list[str]]:
    """The steps of a job, each as its lines with the `- ` line first."""
    steps: list[list[str]] = []
    for line in job.get("steps", [])[1:]:
        if re.match(r"^ {6}-(?:\s|$)", line):
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return steps


def lines_of(job: dict[str, list[str]]) -> list[str]:
    """All the lines of a job."""
    return [line for block in job.values() for line in block]


def uses_in(lines: list[str]) -> list[tuple[str, str]]:
    """Every `uses:` among the lines as (reference, trailing comment)."""
    found = []
    for line in lines:
        match = USES_LINE.match(line)
        if match:
            found.append((unquote(match.group("ref")), (match.group("comment") or "").strip()))
    return found


def with_input(step: list[str], key: str) -> str | None:
    """The value of one input under the `with:` of a step (written at 10 spaces, the way this workflow is)."""
    for line in step:
        match = re.match(r"^ {10}" + re.escape(key) + r":\s*(.*)$", line)
        if match:
            return unquote(strip_comment(match.group(1)))
    return None


def step_if(step: list[str]) -> str | None:
    """The `if:` of a step, or None."""
    for line in step:
        if re.match(r"^ {8}if:", line):
            return condition([line])
    return None


def python_range(pyproject: str) -> tuple[str, str]:
    """The oldest Python the package supports (requires-python) and the newest one it names in a classifier."""
    oldest = re.search(r'requires-python\s*=\s*">=\s*(3\.\d+)', pyproject)
    newest = re.findall(r'"Programming Language :: Python :: (3\.\d+)"', pyproject)
    if oldest is None or not newest:
        raise AssertionError("pyproject.toml has no requires-python or no Python classifier")
    return oldest.group(1), max(newest, key=lambda version: int(version.split(".")[1]))


class Workflow:
    """The release workflow as text: the lines that are not comments, the top-level keys and the jobs."""

    def __init__(self, text: str) -> None:
        self.raw = text
        self.lines = code_lines(text)
        self.code = "\n".join(self.lines)
        self.top = child_blocks(self.lines, 0)
        jobs = child_blocks(self.top.get("jobs", ["jobs:"])[1:], 2)
        self.jobs = {name: child_blocks(block[1:], 4) for name, block in jobs.items()}

    def triggers(self) -> dict[str, dict[str, list[str]]]:
        """The events under `on:`, each with its own keys (`tags`, `branches`, `paths`) as blocks."""
        events = child_blocks(self.top.get("on", ["on:"])[1:], 2)
        return {event: child_blocks(block[1:], 4) for event, block in events.items()}

    def uses(self) -> list[tuple[str, str]]:
        """Every `uses:` of the workflow as (reference, trailing comment)."""
        return uses_in(self.lines)

    def artifact_names(self, job_name: str, action: str) -> list[str | None]:
        """The `name:` input of every step of a job that uses the given artifact action."""
        names = []
        for step in steps_of(self.jobs[job_name]):
            if any(reference.startswith(action + "@") for reference, _ in uses_in(step)):
                names.append(with_input(step, "name"))
        return names


class WorkflowCase(unittest.TestCase):
    """Reads the workflow once for the tests of a class."""

    workflow: Workflow

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.workflow = Workflow(WORKFLOW.read_text(encoding="utf-8"))

    def assert_tag_push_only(self, expression: str) -> None:
        """Asserts that an `if:` is true for a pushed v* tag and for nothing else."""
        for required in TAG_PUSH_CONDITIONS:
            self.assertIn(required, expression)
        self.assertNotIn("||", expression, "an `or` can make the job reachable without a tag")
        for status in ("always()", "failure()", "cancelled()"):
            self.assertNotIn(status, expression, "a status function lets the job run after a failed job")


class TestTriggers(WorkflowCase):
    """When the workflow runs."""

    def test_a_pushed_version_tag_runs_it(self) -> None:
        """Every v* tag push starts the workflow; GitHub does not apply the path filter to tags."""
        push = self.workflow.triggers().get("push")
        self.assertIsNotNone(push, "there is no push trigger")
        self.assertIn("tags", push)
        self.assertIn("v*", sequence(push["tags"]))

    def test_a_manual_run_is_possible(self) -> None:
        """workflow_dispatch builds and tests; the publish job refuses it (see the publishing tests)."""
        self.assertIn("workflow_dispatch", self.workflow.triggers())

    def test_pull_request_target_is_not_used(self) -> None:
        """pull_request_target would give code from a fork a write token and secrets; pull_request does not."""
        events = self.workflow.triggers()
        self.assertIn("pull_request", events)
        self.assertNotIn("pull_request_target", events)

    def test_branch_pushes_and_pull_requests_follow_the_same_packaging_files(self) -> None:
        """Only a change to a packaged file (or the workflow) starts a branch run, and the filter names real files."""
        events = self.workflow.triggers()
        self.assertIn("paths", events["push"])
        self.assertIn("paths", events["pull_request"])
        paths = sequence(events["push"]["paths"])
        self.assertEqual(paths, sequence(events["pull_request"]["paths"]))
        for needed in ("pyproject.toml", "smart_drive/**", "packaging/**", ".github/workflows/release.yml"):
            self.assertIn(needed, paths)
        for pattern in paths:
            with self.subTest(path=pattern):
                literal = pattern.split("*", 1)[0]
                self.assertTrue((REPO_ROOT / literal).exists(), f"the path filter {pattern!r} points at nothing")


class TestPermissions(WorkflowCase):
    """What the jobs are allowed to do."""

    def test_the_workflow_only_reads_the_repository(self) -> None:
        """The top-level token is `contents: read` and nothing else; a job that needs more asks for it itself."""
        self.assertEqual(permissions(self.workflow.top), {"contents": "read"})

    def test_only_the_pypi_job_can_get_an_oidc_token(self) -> None:
        """Exactly one job has `id-token: write`: the one in the pypi environment that only a pushed v* tag starts."""
        holders = [name for name, job in self.workflow.jobs.items() if permissions(job).get("id-token") == "write"]
        self.assertEqual(holders, [PUBLISH_JOB])
        job = self.workflow.jobs[PUBLISH_JOB]
        self.assertEqual(environment_name(job), "pypi")
        self.assertIn("if", job, "the publish job has no `if:`")
        self.assert_tag_push_only(condition(job["if"]))

    def test_no_job_asks_to_write_to_the_repository(self) -> None:
        """Nothing here pushes, tags or creates a release: no job, and not the workflow, asks for contents: write."""
        self.assertNotEqual(permissions(self.workflow.top).get("contents"), "write")
        for name, job in self.workflow.jobs.items():
            with self.subTest(job=name):
                self.assertNotEqual(permissions(job).get("contents"), "write")
        # The text itself too, which also catches a spelling the reader does not parse, such as `{contents: write}`.
        self.assertNotRegex(self.workflow.code, r"contents\s*:\s*write")
        self.assertNotIn("write-all", self.workflow.code)

    def test_the_oidc_token_is_the_only_write_permission_anywhere(self) -> None:
        """No job asks for packages, actions, pull-requests or any other write scope."""
        writes = [
            scope
            for job in self.workflow.jobs.values()
            for scope, level in permissions(job).items()
            if level.startswith("write")
        ]
        self.assertEqual(writes, ["id-token"])


class TestPublishing(WorkflowCase):
    """The only path to PyPI."""

    def test_only_the_publish_job_uses_the_publish_action(self) -> None:
        """No other job can upload to PyPI, so the checks on the publish job cover every way there."""
        users = [
            name
            for name, job in self.workflow.jobs.items()
            if any(reference.startswith(PUBLISH_ACTION + "@") for reference, _ in uses_in(lines_of(job)))
        ]
        self.assertEqual(users, [PUBLISH_JOB])

    def test_the_publish_job_runs_only_for_a_pushed_version_tag(self) -> None:
        """A branch push, a pull request or a manual run never publishes."""
        job = self.workflow.jobs[PUBLISH_JOB]
        self.assertIn("if", job, "the publish job has no `if:`")
        self.assert_tag_push_only(condition(job["if"]))

    def test_publishing_waits_for_the_build_and_every_smoke_test(self) -> None:
        """Nothing is published unless the packages built and the wheel passed on every system and Python."""
        needs = sequence(self.workflow.jobs[PUBLISH_JOB]["needs"])
        for name in NEEDED_BEFORE_PUBLISHING:
            self.assertIn(name, needs)
        # The smoke tests test the wheel the build job made, not an earlier one.
        self.assertIn(BUILD_JOB, sequence(self.workflow.jobs[SMOKE_JOB]["needs"]))

    def test_the_published_files_are_the_ones_the_smoke_tests_installed(self) -> None:
        """The build job uploads one artifact; the smoke tests and the publish job download that same one."""
        uploaded = self.workflow.artifact_names(BUILD_JOB, "actions/upload-artifact")
        self.assertEqual(len(uploaded), 1, f"the build job uploads {len(uploaded)} artifacts")
        self.assertIsNotNone(uploaded[0], "the uploaded artifact has no name")
        self.assertEqual(self.workflow.artifact_names(SMOKE_JOB, "actions/download-artifact"), uploaded)
        self.assertEqual(self.workflow.artifact_names(PUBLISH_JOB, "actions/download-artifact"), uploaded)

    def test_the_publish_job_runs_no_script(self) -> None:
        """The job that holds the OIDC token only downloads the tested files and calls the pinned publish action."""
        job = self.workflow.jobs[PUBLISH_JOB]
        self.assertFalse(
            any(re.match(r"^\s*(?:-\s+)?run:", line) for line in lines_of(job)),
            "a `run:` script would run with the power to publish",
        )
        actions = [reference.split("@")[0] for reference, _ in uses_in(lines_of(job))]
        self.assertEqual(actions, ["actions/download-artifact", PUBLISH_ACTION])

    def test_a_run_for_a_tag_is_never_cancelled(self) -> None:
        """A release that is stopped halfway cannot simply be repeated, because PyPI never takes a version twice."""
        concurrency = child_blocks(self.workflow.top["concurrency"][1:], 2)
        self.assertIn("cancel-in-progress", concurrency)
        expression = inline_value(concurrency["cancel-in-progress"]).replace(" ", "")
        self.assertEqual(expression, "${{!startsWith(github.ref,'refs/tags/')}}")

    def test_there_is_no_pypi_token_or_password(self) -> None:
        """Trusted Publishing needs neither: no `password:` input, no secret anywhere, no twine upload."""
        code = self.workflow.code
        self.assertNotRegex(code, r"(?m)^\s*(?:-\s+)?password\s*:")
        self.assertNotIn("secrets.", code)
        self.assertNotRegex(code, r"(?i)twine\s+upload")


class TestSmokeTests(WorkflowCase):
    """What the wheel goes through before it can be published."""

    def test_the_wheel_is_installed_the_three_ways_the_readme_recommends(self) -> None:
        """A virtual environment, uvx and pipx each run the smoke test on the downloaded wheel; uv and pipx come first."""
        steps = steps_of(self.workflow.jobs[SMOKE_JOB])
        runs: dict[str, tuple[int, str]] = {}
        tools = None
        downloaded = None
        for index, step in enumerate(steps):
            text = "\n".join(step)
            for found in SMOKE_RUN.finditer(text):
                runs[found.group(1)] = (index, found.group(2))
            if re.search(r"\bpip install\b.*\s-r\s+packaging/requirements-smoke\.txt", text):
                tools = index
            if any(reference.startswith("actions/download-artifact@") for reference, _ in uses_in(step)):
                downloaded = with_input(step, "path")
        self.assertEqual(sorted(runs), sorted(SMOKE_MODES))
        self.assertIsNotNone(downloaded, "the smoke job downloads no artifact")
        for mode, (_index, folder) in runs.items():
            with self.subTest(mode=mode):
                self.assertEqual(folder, downloaded, "the mode looks for the wheel somewhere else than it was put")
        self.assertIsNotNone(tools, "the pinned uv and pipx are never installed")
        for mode in ("--uvx", "--pipx"):
            with self.subTest(mode=mode):
                self.assertLess(tools, runs[mode][0], "the tools have to be installed before the mode runs")

    def test_pipx_is_skipped_on_python_3_9(self) -> None:
        """pipx 1.18.4 needs Python 3.11 or newer, so the 3.9 entries of the matrix do not run its step."""
        pipx_steps = [
            step for step in steps_of(self.workflow.jobs[SMOKE_JOB]) if re.search(r"smoke_test\.py\s+--pipx\b", "\n".join(step))
        ]
        self.assertEqual(len(pipx_steps), 1)
        guard = step_if(pipx_steps[0])
        self.assertIsNotNone(guard, "the pipx step has no `if:`")
        self.assertIn("matrix.python-version", guard)
        self.assertIn("!=", guard)
        self.assertIn("'3.9'", guard)

    def test_the_smoke_tests_cover_three_systems_and_the_supported_pythons(self) -> None:
        """Linux, macOS and Windows, with the oldest Python in requires-python and the newest in the classifiers."""
        job = self.workflow.jobs[SMOKE_JOB]
        self.assertIn("strategy", job)
        strategy = child_blocks(job["strategy"][1:], 6)
        self.assertIn("matrix", strategy)
        matrix = child_blocks(strategy["matrix"][1:], 8)
        self.assertIn("os", matrix)
        self.assertIn("python-version", matrix)
        systems = sequence(matrix["os"])
        for family in ("ubuntu", "macos", "windows"):
            with self.subTest(system=family):
                self.assertTrue(any(system.startswith(family) for system in systems), f"no {family} runner in {systems}")
        oldest, newest = python_range((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        versions = sequence(matrix["python-version"])
        self.assertIn(oldest, versions)
        self.assertIn(newest, versions)


class TestSupplyChain(WorkflowCase):
    """What the workflow runs with the power to publish."""

    def test_every_action_is_pinned_to_a_full_commit_sha(self) -> None:
        """A tag such as v4 can be moved to other code; a 40-digit commit SHA cannot."""
        used = self.workflow.uses()
        self.assertTrue(used, "the workflow uses no action at all, so the reader missed them")
        for reference, _comment in used:
            with self.subTest(uses=reference):
                self.assertRegex(reference, PINNED_ACTION)

    def test_every_pinned_action_names_its_release_in_a_comment(self) -> None:
        """The SHA says nothing to a reader, so the release it stands for sits behind it as `# v1.2.3`."""
        for reference, comment in self.workflow.uses():
            with self.subTest(uses=reference):
                self.assertRegex(comment, r"^v\d+(?:\.\d+)*$")

    def test_every_checkout_drops_the_token(self) -> None:
        """persist-credentials: false keeps the token out of .git/config, where a later step could read it."""
        checkouts = 0
        for name, job in self.workflow.jobs.items():
            for step in steps_of(job):
                if not any(reference.startswith("actions/checkout@") for reference, _ in uses_in(step)):
                    continue
                checkouts += 1
                with self.subTest(job=name):
                    self.assertTrue(any(re.match(r"^\s*persist-credentials:\s*false$", line) for line in step))
        self.assertGreater(checkouts, 0, "the workflow checks nothing out, so the reader missed the steps")

    def test_every_job_has_a_timeout(self) -> None:
        """A hung job would hold the runner, and for the publish job the environment, until GitHub's 6-hour limit."""
        for name, job in self.workflow.jobs.items():
            with self.subTest(job=name):
                self.assertIn("timeout-minutes", job)
                self.assertRegex(inline_value(job["timeout-minutes"]), r"^\d+$")


class TestRepositoryFiles(WorkflowCase):
    """The workflow only fails on a tag if a file it names is missing; catch that here instead."""

    def test_the_files_the_workflow_runs_exist(self) -> None:
        """Each file is still used by the workflow and is in the repository."""
        for relative in RUN_FILES:
            with self.subTest(file=relative):
                self.assertIn(relative, self.workflow.code, "the workflow no longer uses it")
                self.assertTrue((REPO_ROOT / relative).is_file(), f"{relative} is missing")

    def test_every_packaging_file_the_workflow_names_exists(self) -> None:
        """Not only the known files: any packaging/... path written in the workflow has to exist."""
        named = {path.rstrip(".") for path in re.findall(r"\bpackaging/[A-Za-z0-9_./-]+", self.workflow.code)}
        self.assertTrue(named, "the workflow names no packaging file, so the reader missed them")
        for relative in sorted(named):
            with self.subTest(file=relative):
                self.assertTrue((REPO_ROOT / relative).exists(), f"{relative} is missing")

    def test_the_documents_named_in_the_comments_exist(self) -> None:
        """The comments send the reader to docs/RELEASING.md for what cannot be written in YAML."""
        named = set(re.findall(r"\bdocs/[A-Za-z0-9_.-]+\.md\b", self.workflow.raw))
        self.assertTrue(named, "no comment points at a document")
        for relative in sorted(named):
            with self.subTest(file=relative):
                self.assertTrue((REPO_ROOT / relative).is_file(), f"{relative} is missing")


if __name__ == "__main__":
    unittest.main()
