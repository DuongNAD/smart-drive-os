"""tests.test_issue_forms - the GitHub issue forms follow GitHub's schema, and the bug form asks for what a fix needs.

GitHub turns .github/ISSUE_TEMPLATE/*.yml into forms, and a form whose YAML or schema is wrong is dropped without a
word: the reporter lands on a blank issue and the owner never learns why. Nothing here can ask GitHub, so the checks
pin what its documentation says a form is, and what this project needs from each one:

* the loader of tests/yaml_subset.py, which reads the files without a YAML library (CI installs none), returns what a
  YAML parser returns for the constructs the forms use, and refuses the ones a parser would read differently: an
  unquoted `No` is a boolean to GitHub's parser, flow lists are not used here, a tab ends the file. Where PyYAML
  happens to be installed (it is not in CI) it has to read the three files exactly as the loader does;
* a form has the keys, element types, ids, labels, options and validations that the schema asks for and no key GitHub
  does not know (it rejects the form), and the validator that says so is itself tried on forms that break each rule;
  a description or a markdown element keeps a `<user>`-like word inside a code span, or GitHub's Markdown hides it;
* the bug form asks for the environment report (`smart-drive doctor --report`), the exact command, what happened and
  what was expected, because a report without them cannot be reproduced, and shows the command as a shell block;
* the feature form asks for the need and for the idea;
* config.yml sends security problems to SECURITY.md (GitHub shows that file at .../security/policy) and keeps blank
  issues on purpose;
* CONTRIBUTING.md sends a bug report to the form chooser, with the environment report, and a security problem to the
  policy.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Any, Callable, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.yaml_subset import load  # noqa: E402

try:
    import yaml  # PyYAML: installed on some development machines, never in CI
except ImportError:
    yaml = None  # type: ignore[assignment]

TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
CONFIG = TEMPLATES / "config.yml"
REPO_URL = "https://github.com/DuongNAD/smart-drive-os/"
POLICY_URL = REPO_URL + "security/policy"

# The labels every GitHub repository starts with: a form can only apply a label that exists.
DEFAULT_LABELS = (
    "bug",
    "documentation",
    "duplicate",
    "enhancement",
    "good first issue",
    "help wanted",
    "invalid",
    "question",
    "wontfix",
)

# The keys GitHub knows, at the top of a form, in a body element and in the attributes of each element type.
FORM_KEYS = frozenset({"name", "description", "body", "assignees", "labels", "projects", "title", "type"})
ELEMENT_KEYS = frozenset({"type", "id", "attributes", "validations"})
ATTRIBUTE_KEYS = {
    "markdown": frozenset({"value"}),
    "textarea": frozenset({"label", "description", "placeholder", "value", "render"}),
    "input": frozenset({"label", "description", "placeholder", "value"}),
    "dropdown": frozenset({"label", "description", "multiple", "options", "default"}),
    "checkboxes": frozenset({"label", "description", "options"}),
}
CHECKBOX_KEYS = frozenset({"label", "required"})
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
MAX_ELEMENTS = 7  # keep the forms short: a long form is a form nobody fills in

CONFIG_KEYS = frozenset({"blank_issues_enabled", "contact_links"})
CONTACT_LINK_KEYS = frozenset({"name", "url", "about"})


def read_yaml(path: Path) -> Any:
    """A repository file as the loader reads it; a ValueError of the loader names the file."""
    try:
        return load(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise ValueError(f"{path.name}: {err}") from err


def form_paths() -> List[Path]:
    """Every issue form: the .yml files of the template folder except config.yml."""
    return sorted(path for path in TEMPLATES.glob("*.yml") if path.name != "config.yml")


def non_empty(value: Any) -> bool:
    """A string with something in it."""
    return isinstance(value, str) and value.strip() != ""


def hidden_tags(markdown: str) -> List[str]:
    """The `<word>` tags of a Markdown text that are not inside a code span: GitHub drops them from the page."""
    return re.findall(r"<[A-Za-z/][^>]*>", re.sub(r"`[^`]*`", "", markdown))


def element_problems(element: Any, ids: Set[str], labels: Set[str]) -> List[str]:
    """What is wrong with one element of a form's body; `ids` and `labels` collect those of the elements before it."""
    if not isinstance(element, dict):
        return ["it is not a mapping"]
    problems = [f"unknown key {key!r}" for key in element if key not in ELEMENT_KEYS]
    kind = element.get("type")
    if kind not in ATTRIBUTE_KEYS:
        return problems + [f"type {kind!r} is not one of {', '.join(sorted(ATTRIBUTE_KEYS))}"]
    attributes = element.get("attributes")
    if not isinstance(attributes, dict):
        return problems + ["attributes must be a mapping"]
    problems += [f"a {kind} element has no attribute {key!r}" for key in attributes if key not in ATTRIBUTE_KEYS[kind]]
    if kind == "markdown":
        problems += [f"a markdown element cannot have {key}" for key in ("id", "validations") if key in element]
        if not non_empty(attributes.get("value")):
            problems.append("a markdown element needs a non-empty value")
        return problems
    label = attributes.get("label")
    if not non_empty(label):
        problems.append("the label must be a non-empty string")
    elif label in labels:
        problems.append(f"the label {label!r} is used twice")
    else:
        labels.add(label)
    identifier = element.get("id")
    if not (isinstance(identifier, str) and ID_PATTERN.fullmatch(identifier)):
        problems.append(f"the id {identifier!r} must be made of letters, digits, '-' and '_'")
    elif identifier in ids:
        problems.append(f"the id {identifier!r} is used twice")
    else:
        ids.add(identifier)
    if "validations" in element:
        validations = element["validations"]
        if not isinstance(validations, dict):
            problems.append("validations must be a mapping")
        else:
            problems += [f"unknown validation {key!r}" for key in validations if key != "required"]
            if "required" in validations and not isinstance(validations["required"], bool):
                problems.append("validations.required must be true or false")
    problems += [
        f"{key} must be a string"
        for key in ("description", "placeholder", "value", "render")
        if key in attributes and not isinstance(attributes[key], str)
    ]
    options = attributes.get("options")
    if kind == "dropdown":
        if not isinstance(options, list) or not options or not all(non_empty(option) for option in options):
            problems.append("options must be a non-empty list of strings")
        elif len(set(options)) != len(options):
            problems.append("the options must be distinct")
        if "multiple" in attributes and not isinstance(attributes["multiple"], bool):
            problems.append("multiple must be true or false")
        default = attributes.get("default")
        if "default" in attributes and not (
            isinstance(default, int) and not isinstance(default, bool) and 0 <= default < len(options or [])
        ):
            problems.append("default must be the index of one of the options")
    if kind == "checkboxes":
        if not isinstance(options, list) or not options:
            problems.append("options must be a non-empty list")
        else:
            for option in options:
                if not isinstance(option, dict) or not non_empty(option.get("label")):
                    problems.append("every option of checkboxes must be a mapping with a label")
                    continue
                problems += [f"unknown key {key!r} in a checkbox option" for key in option if key not in CHECKBOX_KEYS]
                if "required" in option and not isinstance(option["required"], bool):
                    problems.append("the required of a checkbox option must be true or false")
    return problems


def form_problems(form: Any) -> List[str]:
    """What is wrong with a form (the mapping its YAML file stands for), in GitHub's terms; empty when it is fine."""
    if not isinstance(form, dict):
        return ["the form is not a mapping"]
    problems = [f"unknown key {key!r}" for key in form if key not in FORM_KEYS]
    problems += [f"{key} must be a non-empty string" for key in ("name", "description") if not non_empty(form.get(key))]
    labels = form.get("labels")
    if "labels" in form:
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            problems.append("labels must be a list of strings")
        else:
            problems += [
                f"the label {label!r} is not one of GitHub's default labels"
                for label in labels
                if label not in DEFAULT_LABELS
            ]
    body = form.get("body")
    if not isinstance(body, list) or not body:
        return problems + ["body must be a non-empty list"]
    if len(body) > MAX_ELEMENTS:
        problems.append(f"the body has {len(body)} elements, at most {MAX_ELEMENTS} (keep the form short)")
    ids: Set[str] = set()
    seen_labels: Set[str] = set()
    for number, element in enumerate(body, 1):
        problems += [f"element {number}: {problem}" for problem in element_problems(element, ids, seen_labels)]
    return problems


def config_problems(config: Any) -> List[str]:
    """What is wrong with config.yml (the mapping it stands for)."""
    if not isinstance(config, dict):
        return ["the file is not a mapping"]
    problems = [f"unknown key {key!r}" for key in config if key not in CONFIG_KEYS]
    if not isinstance(config.get("blank_issues_enabled"), bool):
        problems.append("blank_issues_enabled must be true or false")
    links = config.get("contact_links")
    if not isinstance(links, list) or not links:
        return problems + ["contact_links must be a non-empty list"]
    for number, link in enumerate(links, 1):
        if not isinstance(link, dict):
            problems.append(f"contact link {number} is not a mapping")
            continue
        where = f"contact link {number}"
        problems += [f"{where}: unknown key {key!r}" for key in link if key not in CONTACT_LINK_KEYS]
        problems += [
            f"{where}: {key} must be a non-empty string"
            for key in sorted(CONTACT_LINK_KEYS)
            if not non_empty(link.get(key))
        ]
        url = link.get("url")
        if non_empty(url) and not url.startswith(REPO_URL):
            problems.append(f"{where}: the url must point into {REPO_URL}")
    return problems


# What the loader returns for the constructs the forms use: (label, YAML text, value). PyYAML has to agree.
LOADER_CASES: Tuple[Tuple[str, str, Any], ...] = (
    (
        "a mapping of scalars",
        "name: Bug report\ncount: 3\nnegative: -2\non_switch: true\noff_switch: false\n",
        {"name": "Bug report", "count": 3, "negative": -2, "on_switch": True, "off_switch": False},
    ),
    (
        "nested mappings",
        "a:\n  b:\n    c: d\n  e: f\ng: h\n",
        {"a": {"b": {"c": "d"}, "e": "f"}, "g": "h"},
    ),
    (
        "a list under a key",
        "labels:\n  - bug\n  - enhancement\n",
        {"labels": ["bug", "enhancement"]},
    ),
    (
        "a list of mappings whose keys line up after the dash",
        "- name: one\n  value: 1\n- name: two\n  value: 2\n",
        [{"name": "one", "value": 1}, {"name": "two", "value": 2}],
    ),
    (
        "mappings in lists in mappings, the shape of a form",
        "body:\n  - type: input\n    attributes:\n      label: L\n    validations:\n      required: true\n"
        '  - type: dropdown\n    attributes:\n      options:\n        - "No"\n        - Not sure\n',
        {
            "body": [
                {"type": "input", "attributes": {"label": "L"}, "validations": {"required": True}},
                {"type": "dropdown", "attributes": {"options": ["No", "Not sure"]}},
            ]
        },
    ),
    (
        "a list in a list",
        "- - a\n  - b\n- - c\n",
        [["a", "b"], ["c"]],
    ),
    (
        "quoted strings",
        "single: 'it''s'\n"
        'double: "say \\"hi\\"\\n\\ttab \\\\ done"\n'
        'word: "No"\n'
        "colon: 'a: b # c'\n"
        "padded: '  x  '\n"
        "empty: ''\n",
        {
            "single": "it's",
            "double": 'say "hi"\n\ttab \\ done',
            "word": "No",
            "colon": "a: b # c",
            "padded": "  x  ",
            "empty": "",
        },
    ),
    (
        "quoted keys",
        "'x y': 1\n\"yes\": 2\n",
        {"x y": 1, "yes": 2},
    ),
    (
        "comments",
        "# a full-line comment\n"
        "a: 1  # after a number\n"
        "b: text # after a word\n"
        "c: 'not # a comment'  # but this is\n"
        "d: C#neither is this\n"
        "\n"
        "  # an indented one\n"
        "\n"
        "e: 2\n",
        {"a": 1, "b": "text", "c": "not # a comment", "d": "C#neither is this", "e": 2},
    ),
    (
        "literal blocks: clip, strip and keep",
        "clip: |\n  one\n    indented\n\n  two\n\nstrip: |-\n  one\n\nkeep: |+\n  one\n\nother: 1\n",
        {"clip": "one\n  indented\n\ntwo\n", "strip": "one", "keep": "one\n\n", "other": 1},
    ),
    (
        "text that only looks like YAML inside a literal block",
        'value: |\n  # not a comment\n  key: not a mapping\n  - not a list\n  "not a string"\nnext: ok\n',
        {"value": '# not a comment\nkey: not a mapping\n- not a list\n"not a string"\n', "next": "ok"},
    ),
    (
        "literal blocks in a list and in the mapping of a list entry",
        "- |\n  first\n- text: |-\n    second\n  other: 2\n",
        ["first\n", {"text": "second", "other": 2}],
    ),
    (
        "values that only look special",
        "flag: --apply\n"
        "version: 3d\n"
        "word: nullable\n"
        "day: yesterday\n"
        "url: https://github.com/DuongNAD/smart-drive-os/security/policy\n"
        "list: a, b; c? d!\n"
        "tilde: a~b\n"
        "dash: x - y\n"
        "percent: 100%\n",
        {
            "flag": "--apply",
            "version": "3d",
            "word": "nullable",
            "day": "yesterday",
            "url": "https://github.com/DuongNAD/smart-drive-os/security/policy",
            "list": "a, b; c? d!",
            "tilde": "a~b",
            "dash": "x - y",
            "percent": "100%",
        },
    ),
    (
        "Windows line endings and a byte order mark",
        "\ufeffa:\r\n  - b\r\n  - c\r\n",
        {"a": ["b", "c"]},
    ),
)

# What the loader refuses: (label, YAML text, the number of the line the error names).
REFUSED_CASES: Tuple[Tuple[str, str, int], ...] = (
    ("a flow list", "a: 1\nb: [1, 2]\n", 2),
    ("a flow mapping", "a: {b: 1}\n", 1),
    ("a tab as indentation", "a:\n\tb: 1\n", 2),
    ("a tab after spaces", "a:\n  \tb: 1\n", 2),
    ("an unquoted No", "a: No\n", 1),
    ("an unquoted yes", "a: yes\n", 1),
    ("an unquoted Y", "a: Y\n", 1),
    ("an unquoted n", "a: n\n", 1),
    ("an unquoted On", "a: On\n", 1),
    ("an unquoted OFF", "a: OFF\n", 1),
    ("an unquoted null", "a: null\n", 1),
    ("an unquoted ~", "a: ~\n", 1),
    ("an unquoted word in a list", "a:\n  - ok\n  - No\n", 3),
    ("an unquoted word as a key", "no: 1\n", 1),
    ("a plain scalar starting with a backtick", "a: `smart-drive doctor`\n", 1),
    ("a plain scalar starting with @", "a: @user\n", 1),
    ("a plain scalar starting with %", "a: %x\n", 1),
    ("a plain scalar starting with ?", "a: ?x\n", 1),
    ("a plain scalar starting with a colon", "a: :x\n", 1),
    ("a plain scalar starting with a comma", "a: ,x\n", 1),
    ("a plain scalar starting with ]", "a: ]x\n", 1),
    ("a plain scalar starting with a dash and a space", "a: - x\n", 1),
    ("a duplicate key", "a: 1\nb: 2\na: 3\n", 3),
    ("a duplicate key in a nested mapping", "a:\n  b: 1\n  b: 2\n", 3),
    ("a duplicate key in a list entry", "- a: 1\n  a: 2\n", 2),
    ("an anchor", "a: &x 1\n", 1),
    ("an alias", "a: *x\n", 1),
    ("a tag", "a: !!str 1\n", 1),
    ("a document start", "---\na: 1\n", 1),
    ("a document end", "a: 1\n...\n", 2),
    ("a colon and a space in a plain scalar", "a: b: c\n", 1),
    ("a colon at the end of a plain scalar", "a: b:\n", 1),
    ("a folded block", "a: >\n  x\n", 1),
    ("a block with an indentation indicator", "a: |2\n  x\n", 1),
    ("a key without a value", "a:\nb: 1\n", 1),
    ("a key without a value at the end", "a: 1\nb:\n", 2),
    ("a list that is not indented under its key", "a:\n- b\n", 1),
    ("a plain scalar that goes on in the next line", "a: one\n  two\n", 2),
    ("a quoted string that is not closed", "a: 'one\n", 1),
    ("an escape that is not supported", 'a: "\\x41"\n', 1),
    ("text after a closing quote", "a: 'x' y\n", 1),
    ("a float", "a: 3.9\n", 1),
    ("a date", "a: 2026-10-21\n", 1),
    ("a hexadecimal number", "a: 0x1F\n", 1),
    ("a number with a leading zero", "a: 012\n", 1),
    ("true in an odd case", "a: tRUE\n", 1),
    ("an empty document", "", 1),
    ("a document of comments", "# nothing\n\n", 1),
    ("a document that is a scalar", "hello\n", 1),
    ("a list entry without a value", "-\n", 1),
    ("a list entry in a mapping", "a: 1\n- b\n", 2),
    ("a key in a list", "- a\nb: 1\n", 2),
    ("no space after the colon", "a:1\n", 1),
    ("a complex key", "? a\n: b\n", 1),
)


class TestLoader(unittest.TestCase):
    """tests/yaml_subset.py: what it returns for the constructs the forms use, and what it refuses."""

    def test_it_returns_what_a_yaml_parser_returns(self) -> None:
        for label, text, expected in LOADER_CASES:
            with self.subTest(case=label):
                self.assertEqual(load(text), expected)

    def test_booleans_and_integers_keep_their_types(self) -> None:
        loaded = load("a: true\nb: 1\nc: '1'\nd: 'true'\n")
        self.assertIs(loaded["a"], True)
        self.assertEqual(type(loaded["b"]), int)
        self.assertEqual((loaded["c"], loaded["d"]), ("1", "true"))

    def test_it_refuses_what_it_does_not_understand_and_names_the_line(self) -> None:
        for label, text, number in REFUSED_CASES:
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, rf"^line {number}: "):
                    load(text)

    def test_the_refusal_of_a_word_that_yaml_1_1_turns_into_a_boolean_says_to_quote_it(self) -> None:
        with self.assertRaisesRegex(ValueError, "put it in quotes"):
            load("options:\n  - No\n")

    def test_a_quoted_word_is_accepted(self) -> None:
        self.assertEqual(load('options:\n  - "No"\n  - \'Yes\'\n'), {"options": ["No", "Yes"]})


@unittest.skipIf(yaml is None, "PyYAML is not installed (CI installs nothing)")
class TestAgainstPyYaml(unittest.TestCase):
    """Where PyYAML happens to be installed, it has to agree with the loader."""

    def test_the_expected_values_are_what_pyyaml_returns(self) -> None:
        for label, text, expected in LOADER_CASES:
            with self.subTest(case=label):
                self.assertEqual(yaml.safe_load(text), expected)

    def test_pyyaml_reads_the_issue_template_files_as_the_loader_does(self) -> None:
        paths = sorted(TEMPLATES.glob("*.yml"))
        names = {path.name for path in paths}
        self.assertTrue({"bug_report.yml", "feature_request.yml", "config.yml"} <= names, f"found only {sorted(names)}")
        for path in paths:
            with self.subTest(file=path.name):
                self.assertEqual(yaml.safe_load(path.read_text(encoding="utf-8")), read_yaml(path))

    def test_the_words_the_loader_makes_you_quote_do_change_meaning(self) -> None:
        """The refusal is not pedantry: unquoted, `No` is False for a YAML 1.1 parser and `null` is None."""
        self.assertEqual(yaml.safe_load("a: No\nb: yes\nc: null\n"), {"a": False, "b": True, "c": None})
        self.assertEqual(yaml.safe_load('a: "No"\n'), {"a": "No"})


def good_form() -> dict:
    """A form with one element of each type that satisfies every rule: the base the broken forms are made from."""
    return {
        "name": "Example",
        "description": "An example form.",
        "labels": ["bug"],
        "body": [
            {"type": "markdown", "attributes": {"value": "Hello."}},
            {
                "type": "textarea",
                "id": "one",
                "attributes": {"label": "One", "description": "d", "placeholder": "p", "render": "shell"},
                "validations": {"required": True},
            },
            {"type": "input", "id": "two", "attributes": {"label": "Two"}},
            {
                "type": "dropdown",
                "id": "three-3",
                "attributes": {"label": "Three", "options": ["a", "b"], "default": 1, "multiple": False},
            },
            {
                "type": "checkboxes",
                "id": "four_4",
                "attributes": {"label": "Four", "options": [{"label": "ok", "required": True}, {"label": "fine"}]},
            },
        ],
    }


def nth_element(form: dict, number: int) -> dict:
    """The body element with this number (from 1)."""
    return form["body"][number - 1]


Change = Callable[[dict], None]


def drop(key: str) -> Change:
    """A change that removes a key from the top of the form."""
    return lambda form: form.pop(key)


def set_form(key: str, value: Any) -> Change:
    """A change that sets a key at the top of the form."""
    return lambda form: form.__setitem__(key, value)


def set_key(number: int, key: str, value: Any) -> Change:
    """A change that sets a key of a body element."""
    return lambda form: nth_element(form, number).__setitem__(key, value)


def put(number: int, section: str, key: str, value: Any) -> Change:
    """A change that sets a key in the attributes (or the validations) of a body element."""
    return lambda form: nth_element(form, number)[section].__setitem__(key, value)


def many_elements(form: dict) -> None:
    """A change that gives the form one element more than it may have."""
    form["body"] = [
        {"type": "input", "id": f"id{n}", "attributes": {"label": f"Label {n}"}} for n in range(MAX_ELEMENTS + 1)
    ]


# What breaks the good form, and a part of the problem the validator names: (label, change, message).
BROKEN_FORMS: Tuple[Tuple[str, Change, str], ...] = (
    ("no name", drop("name"), "name must be a non-empty string"),
    ("an empty description", set_form("description", " "), "description must be a non-empty string"),
    ("a label GitHub does not have", set_form("labels", ["urgent"]), "default labels"),
    ("labels that are not a list", set_form("labels", "bug"), "labels must be a list of strings"),
    ("a label that is a boolean", set_form("labels", [True]), "labels must be a list of strings"),
    ("an unknown top-level key", set_form("colour", "red"), "unknown key 'colour'"),
    ("no body", drop("body"), "body must be a non-empty list"),
    ("an empty body", set_form("body", []), "body must be a non-empty list"),
    ("too many elements", many_elements, f"at most {MAX_ELEMENTS}"),
    ("an element that is not a mapping", lambda form: form["body"].append("text"), "it is not a mapping"),
    ("an unknown element type", set_key(3, "type", "number"), "type 'number' is not one of"),
    ("an unknown element key", set_key(3, "required", True), "unknown key 'required'"),
    ("no attributes", set_key(3, "attributes", None), "attributes must be a mapping"),
    ("an unknown attribute", put(2, "attributes", "hint", "x"), "has no attribute 'hint'"),
    ("a markdown element with an id", set_key(1, "id", "intro"), "cannot have id"),
    ("a markdown element with validations", set_key(1, "validations", {"required": True}), "cannot have validations"),
    ("a markdown element without a value", put(1, "attributes", "value", ""), "needs a non-empty value"),
    ("no label", lambda form: nth_element(form, 3)["attributes"].pop("label"), "label must be a non-empty string"),
    ("a label used twice", put(3, "attributes", "label", "One"), "'One' is used twice"),
    ("no id", lambda form: nth_element(form, 2).pop("id"), "must be made of letters"),
    ("an id with a space", set_key(2, "id", "has space"), "must be made of letters"),
    ("an id used twice", set_key(3, "id", "one"), "'one' is used twice"),
    ("validations that are not a mapping", set_key(2, "validations", True), "validations must be a mapping"),
    ("an unknown validation", put(2, "validations", "min", 1), "unknown validation 'min'"),
    ("required written as a word", put(2, "validations", "required", "yes"), "required must be true or false"),
    ("a description that is not a string", put(2, "attributes", "description", 5), "description must be a string"),
    ("dropdown options that are empty", put(4, "attributes", "options", []), "non-empty list of strings"),
    ("a dropdown option YAML read as a boolean", put(4, "attributes", "options", [False, "b"]), "list of strings"),
    ("dropdown options that repeat", put(4, "attributes", "options", ["a", "a"]), "must be distinct"),
    ("a dropdown default out of range", put(4, "attributes", "default", 2), "index of one of the options"),
    ("a dropdown multiple that is not a bool", put(4, "attributes", "multiple", "no"), "multiple must be true"),
    ("checkbox options that are empty", put(5, "attributes", "options", []), "options must be a non-empty list"),
    ("a checkbox option that is a string", put(5, "attributes", "options", ["ok"]), "mapping with a label"),
    ("a checkbox option without a label", put(5, "attributes", "options", [{"required": True}]), "with a label"),
    (
        "a checkbox option with an unknown key",
        put(5, "attributes", "options", [{"label": "a", "checked": 1}]),
        "unknown key 'checked'",
    ),
    (
        "a checkbox option with required as a word",
        put(5, "attributes", "options", [{"label": "a", "required": "yes"}]),
        "must be true or false",
    ),
)


class TestFormSchema(unittest.TestCase):
    """The validator: it accepts a good form and says what is wrong with a form that breaks a rule."""

    def test_a_good_form_has_no_problems(self) -> None:
        self.assertEqual(form_problems(good_form()), [])

    def test_each_rule_is_enforced(self) -> None:
        for label, change, expected in BROKEN_FORMS:
            with self.subTest(case=label):
                form = good_form()
                change(form)
                problems = form_problems(form)
                self.assertTrue(any(expected in problem for problem in problems), f"{expected!r} not in {problems}")

    def test_a_form_that_is_not_a_mapping_is_refused(self) -> None:
        self.assertEqual(form_problems(["a"]), ["the form is not a mapping"])


class TestIssueForms(unittest.TestCase):
    """The forms of the repository, whatever they ask."""

    def test_there_is_a_bug_form_and_a_feature_form(self) -> None:
        names = [path.name for path in form_paths()]
        self.assertIn("bug_report.yml", names)
        self.assertIn("feature_request.yml", names)

    def test_every_form_follows_githubs_schema(self) -> None:
        for path in form_paths():
            with self.subTest(form=path.name):
                self.assertEqual(form_problems(read_yaml(path)), [])

    def test_the_names_of_the_forms_are_unique(self) -> None:
        names = [read_yaml(path)["name"] for path in form_paths()]
        self.assertEqual(len(names), len(set(names)), f"two forms are called alike: {names}")

    def test_the_tag_finder_sees_a_tag_in_text_and_not_in_a_code_span(self) -> None:
        self.assertEqual(hidden_tags("your user name as <user>, or as `<user>`"), ["<user>"])
        self.assertEqual(hidden_tags("your user name as `<user>`"), [])

    def test_no_markdown_text_has_an_angle_bracket_word_outside_code(self) -> None:
        """A description and a markdown element are rendered as Markdown, which hides `<user>` unless it is code."""
        for path in form_paths():
            for number, item in enumerate(read_yaml(path)["body"], 1):
                for key in ("description", "value"):
                    if key == "value" and item["type"] != "markdown":  # the value of a field is text, not Markdown
                        continue
                    with self.subTest(form=path.name, element=number, key=key):
                        self.assertEqual(hidden_tags(item["attributes"].get(key, "")), [])


class TestBugReportForm(unittest.TestCase):
    """What the bug form asks, and where it sends a security problem."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.form = read_yaml(TEMPLATES / "bug_report.yml")
        cls.fields = {item["id"]: item for item in cls.form["body"] if "id" in item}

    def test_it_asks_for_the_report_the_command_what_happened_and_what_was_expected(self) -> None:
        for identifier in ("report", "command", "what-happened", "expected"):
            with self.subTest(field=identifier):
                self.assertIn(identifier, self.fields)
                self.assertIs(self.fields[identifier]["validations"]["required"], True)

    def test_the_report_field_names_the_command_that_makes_the_report(self) -> None:
        self.assertIn("smart-drive doctor --report", self.fields["report"]["attributes"]["description"])

    def test_the_command_is_shown_as_a_shell_block(self) -> None:
        self.assertEqual(self.fields["command"]["attributes"]["render"], "shell")

    def test_it_is_labelled_bug(self) -> None:
        self.assertEqual(self.form["labels"], ["bug"])

    def test_it_sends_security_problems_to_the_policy_instead_of_a_public_issue(self) -> None:
        text = "\n".join(item["attributes"]["value"] for item in self.form["body"] if item["type"] == "markdown")
        self.assertIn(POLICY_URL, text)


class TestFeatureRequestForm(unittest.TestCase):
    """What the feature form asks."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.form = read_yaml(TEMPLATES / "feature_request.yml")

    def test_it_requires_at_least_two_fields(self) -> None:
        required = [item["id"] for item in self.form["body"] if item.get("validations", {}).get("required") is True]
        self.assertGreaterEqual(len(required), 2, f"required fields: {required}")

    def test_it_is_labelled_enhancement(self) -> None:
        self.assertEqual(self.form["labels"], ["enhancement"])


class TestConfig(unittest.TestCase):
    """config.yml: the chooser of the New issue page."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.config = read_yaml(CONFIG)

    def test_it_is_valid(self) -> None:
        self.assertEqual(config_problems(self.config), [])

    def test_blank_issues_stay_enabled(self) -> None:
        """On purpose: there are no Discussions, and beta testers need a place for questions and 'all steps passed'."""
        self.assertIs(self.config["blank_issues_enabled"], True)

    def test_a_contact_link_sends_security_problems_to_the_policy(self) -> None:
        urls = [link["url"] for link in self.config["contact_links"]]
        self.assertIn(POLICY_URL, urls)
        self.assertTrue((ROOT / "SECURITY.md").is_file(), "GitHub shows SECURITY.md at the policy url; it is missing")

    def test_the_validator_refuses_a_broken_config(self) -> None:
        link = {"name": "n", "url": REPO_URL + "x", "about": "a"}
        on = {"blank_issues_enabled": True}
        cases: Tuple[Tuple[str, Any, str], ...] = (
            ("blank issues written as a word", dict(on, blank_issues_enabled="yes", contact_links=[link]), "true or"),
            ("no links", on, "contact_links must be"),
            ("a link outside the repo", dict(on, contact_links=[dict(link, url="https://x.test/")]), "must point"),
            ("a link without about", dict(on, contact_links=[{"name": "n", "url": REPO_URL}]), "about must be a non"),
            ("an unknown key", dict(on, contact_links=[link], extra=1), "unknown key 'extra'"),
        )
        for label, config, expected in cases:
            with self.subTest(case=label):
                problems = config_problems(config)
                self.assertTrue(any(expected in problem for problem in problems), f"{expected!r} not in {problems}")


class TestContributing(unittest.TestCase):
    """CONTRIBUTING.md: where a reporter is sent."""

    def test_it_sends_bug_reports_to_the_form_chooser_with_the_report_and_security_problems_to_the_policy(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn(f"({REPO_URL}issues/new/choose)", text)
        self.assertIn("`smart-drive doctor --report`", text)
        self.assertIn("(SECURITY.md)", text)


if __name__ == "__main__":
    unittest.main()
