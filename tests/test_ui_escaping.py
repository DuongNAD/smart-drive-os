"""tests/test_ui_escaping.py - Names that come from the drive must never reach innerHTML unescaped.

Regression: the dashboard built its table rows with template literals that dropped file, folder and
category names straight into `innerHTML`. On a volume that allows `<` in names (APFS, ext4, ...) a file
called `<img src=x onerror=...>` ran script inside the dashboard the next time it was searched or listed,
and the dashboard has same-origin POST endpoints (clean, snapshot, ...). Every drive-supplied string now
goes through `esc()`.

Three layers keep it that way, because a check by field name alone is easy to walk around (a new column
called `${m.directory}`, `${esc(a) + b}`, or an intermediate variable all slip past one):
1. A deny-by-default scan of every template literal in every <script>: an interpolation must be exactly
   `esc(...)`, or a number/constant that is on an explicit allow-list. Anything new has to be one of the two.
2. Rules about the surrounding code: no string-concatenated innerHTML, no esc() inside an HTML tag (it
   escapes for text, not for attribute values or JavaScript strings), no other HTML sinks.
3. The real thing: the shipped script is run under node against canned API answers in which EVERY string
   field is an attack payload, and the markup it hands to innerHTML is parsed and must contain nothing but
   the dashboard's own tags.
"""

from __future__ import annotations

import html.parser
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from smart_drive.ui.dashboard import get_dashboard_html

# Interpolations in a template literal that is assigned to innerHTML must be exactly esc(...), or something
# that cannot carry markup: a number, a byte count, a constant class name. Add to this list only for those.
# (Template literals that go to innerText, a toast or an alert are text, not HTML, and are not held to it.)
SAFE_EXPRESSIONS = [
    re.compile(r"^[\w.]+\.toFixed\(\d\)$"),
    re.compile(r"^\([\w.]+ \|\| 0\)\.(toFixed\(\d\)|toLocaleString\(\))$"),
    re.compile(r"^data\.total \|\| data\.total_count \|\| 0$"),
    re.compile(r"^item\.tier === 1 \? 'badge-safe' : item\.tier === 2 \? 'badge-dev' : 'badge-optin'$"),
]
SAFE_CALLS = ("formatBytes", "Math.min", "Math.max")  # as the whole expression: one call that closes at the very end

TEMPLATE_TAGS = {"div", "span", "tr", "td", "th"}  # what the dashboard's own row templates are made of
SINKS_NEVER_USED = ("document.write", "outerHTML", "insertAdjacentHTML", "eval(", "new Function")
PAYLOAD = "<img src=x onerror=alert(1)>\"'&<script>alert(2)</script>"


def _scripts() -> List[str]:
    html_text = get_dashboard_html()
    return [m.group(1) for m in re.finditer(r"<script[^>]*>(.*?)</script>", html_text, re.DOTALL)]


def _template_literals(script: str) -> List[Tuple[str, List[str], bool]]:
    """Every backtick string in the script as (text with \\0 where an interpolation was, [expressions], to_html).

    ``to_html`` says the literal is assigned straight to innerHTML (the only place its markup is parsed).

    Comments, ordinary quoted strings (one of them contains backticks) and regex literals (esc() has one with
    both kinds of quote in it) are skipped, so only real template literals are reported.
    """
    found: List[Tuple[str, List[str], bool]] = []
    i, n, previous = 0, len(script), ""  # previous: last significant character, to tell a regex from a division
    while i < n:
        ch = script[i]
        if script.startswith("//", i):
            i = script.find("\n", i)
            i = n if i < 0 else i
        elif script.startswith("/*", i):
            i = script.find("*/", i) + 2
        elif ch in "'\"":
            quote = ch
            i += 1
            while i < n and script[i] != quote:
                i += 2 if script[i] == "\\" else 1
            i += 1
            previous = quote
        elif ch == "/" and (previous == "" or previous in "(,=:[!&|?{};"):
            i += 1
            in_class = False
            while i < n and (script[i] != "/" or in_class):
                if script[i] == "\\":
                    i += 1
                elif script[i] == "[":
                    in_class = True
                elif script[i] == "]":
                    in_class = False
                i += 1
            i += 1
            previous = "/"
        elif ch == "`":
            to_html = re.search(r"innerHTML\s*=\s*$", script[max(0, i - 40):i]) is not None
            j, text, exprs = i + 1, [], []
            while j < n and script[j] != "`":
                if script[j] == "\\":
                    text.append(script[j:j + 2])
                    j += 2
                elif script.startswith("${", j):
                    depth, k = 1, j + 2
                    while k < n and depth:
                        depth += {"{": 1, "}": -1}.get(script[k], 0)
                        k += 1
                    exprs.append(script[j + 2:k - 1].strip())
                    text.append("\0")
                    j = k
                else:
                    text.append(script[j])
                    j += 1
            found.append(("".join(text), exprs, to_html))
            i = j + 1
            previous = "`"
        else:
            if not ch.isspace():
                previous = ch
            i += 1
    return found


def _is_single_call(expression: str, name: str) -> bool:
    """`name(...)` as the WHOLE expression: the call opened at the start closes at the very end."""
    if not (expression.startswith(name + "(") and expression.endswith(")")):
        return False
    depth = 0
    for index, ch in enumerate(expression[len(name):], start=len(name)):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0:
            return index == len(expression) - 1
    return False


def _is_exactly_esc(expression: str) -> bool:
    return _is_single_call(expression, "esc")


def _is_vetted(expression: str) -> bool:
    return (
        _is_exactly_esc(expression)
        or any(_is_single_call(expression, call) for call in SAFE_CALLS)
        or any(pattern.match(expression) for pattern in SAFE_EXPRESSIONS)
    )


def _inside_a_tag(text: str, position: int) -> bool:
    """Whether ``position`` in a template literal's text falls between a '<tag' and its '>'."""
    last_open, last_close = text.rfind("<", 0, position), text.rfind(">", 0, position)
    return last_open > last_close and last_open + 1 < len(text) and (text[last_open + 1].isalpha() or text[last_open + 1] == "/")


class TestEveryInterpolationIsEscapedOrHarmless(unittest.TestCase):
    def setUp(self) -> None:
        self.scripts = _scripts()
        self.literals = [lit for script in self.scripts for lit in _template_literals(script)]
        self.html_literals = [(text, exprs) for text, exprs, to_html in self.literals if to_html]

    def test_there_is_something_to_check(self) -> None:
        self.assertGreaterEqual(len(self.scripts), 1)
        self.assertGreaterEqual(len(self.html_literals), 5)  # taxonomy card, hotspot row, search row, empty row, junk row
        expressions = [e for _, exprs in self.html_literals for e in exprs]
        self.assertGreater(len(expressions), 20)
        self.assertTrue(any(_is_exactly_esc(e) for e in expressions))

    def test_no_interpolation_in_html_is_unvetted(self) -> None:
        offenders = [e for _, exprs in self.html_literals for e in exprs if not _is_vetted(e)]
        self.assertEqual(
            offenders, [],
            "wrap strings that come from the drive or the API in esc(...); add to SAFE_EXPRESSIONS/SAFE_CALLS "
            "only an expression that is a number or a constant",
        )

    def test_the_scanner_would_catch_the_ways_round_a_name_based_check(self) -> None:
        """Every one of these passed an earlier, weaker version of this test."""
        bypasses = [
            "m.directory", "esc(m.extension) + m.path", "n", "esc(a)  + esc(b)", "esc(m.name), m.path",
            "msg", "item.tier", "data.message || ''", "formatBytes(a) + m.path + formatBytes(1)",
            "Math.max(1, 2) + m.path", "formatBytes(m.size)  +  m.name",
        ]
        for expression in bypasses:
            with self.subTest(expression=expression):
                self.assertFalse(_is_vetted(expression))
        for expression in ("esc(m.category || 'Other')", "esc(item.description || item.rule || '')", "formatBytes(m.size)",
                           "Math.min(100, Math.max(4, pct))", "(stat.file_count || 0).toLocaleString()"):
            with self.subTest(expression=expression):
                self.assertTrue(_is_vetted(expression))

    def test_esc_is_never_used_inside_an_html_tag(self) -> None:
        """esc() makes text safe for a text node; inside an attribute it is not enough (&#39; becomes ' again)."""
        for text, exprs, _ in self.literals:
            position = 0
            for expression in exprs:
                position = text.index("\0", position)
                if _is_exactly_esc(expression):
                    self.assertFalse(_inside_a_tag(text, position), f"esc() inside a tag: {text[max(0, position - 40):position + 20]!r}")
                position += 1

    def test_the_helper_exists_and_nothing_else_writes_html(self) -> None:
        joined = "\n".join(self.scripts)
        self.assertIn("function esc(", joined)
        for sink in SINKS_NEVER_USED:
            self.assertNotIn(sink, joined)

    def test_inner_html_is_only_given_a_template_literal_or_a_plain_string(self) -> None:
        plain_string = re.compile(r"""'(?:[^'\\]|\\.)*'\s*;|"(?:[^"\\]|\\.)*"\s*;""", re.DOTALL)
        checked = 0
        for script in self.scripts:
            for match in re.finditer(r"innerHTML\s*(\+?=)\s*", script):
                rest = script[match.end():]
                self.assertEqual(match.group(1), "=", f"innerHTML must be assigned, not appended to: {rest[:60]!r}")
                self.assertTrue(
                    rest.startswith("`") or plain_string.match(rest),
                    f"innerHTML built by concatenation or from a variable: {rest[:60]!r}",
                )
                checked += 1
        self.assertGreaterEqual(checked, 8)

    def test_no_event_handler_receives_data(self) -> None:
        """The inline handlers in the page markup call functions with constants only."""
        page = get_dashboard_html()
        for match in re.finditer(r'\son\w+="([^"]*)"', page):
            self.assertNotIn("${", match.group(1))


def _node() -> Optional[str]:
    return shutil.which("node")


class _Tags(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: List[str] = []
        self.attributes: List[Tuple[str, str]] = []
        self.text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.tags.append(tag)
        self.attributes.extend((name, value or "") for name, value in attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const script = fs.readFileSync(process.argv[2], 'utf8');
const P = JSON.parse(process.argv[3]);
const captured = [];
const elements = {};
function makeEl(id) {
  return {
    id, style: {}, children: [], value: '', checked: true, className: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; },
    remove() {}, addEventListener() {}, querySelectorAll() { return []; },
    set innerHTML(v) { captured.push(String(v)); this._h = v; }, get innerHTML() { return this._h || ''; },
    set innerText(v) { this._t = v; }, get innerText() { return this._t || ''; },
    set textContent(v) { this._c = v; }, get textContent() { return this._c || ''; },
  };
}
const document = {
  getElementById(id) { return elements[id] || (elements[id] = makeEl(id)); },
  createElement() { return makeEl('created'); },
  querySelectorAll() { return []; },
};
const tier = { count: 1, nominal_bytes: 1, slack_bytes: 1, items: [
  { tier: P, rel_path: P, description: P, rule: P, size: 1, allocated_size: 2, slack_bytes: 1, name: P, path: P, category: P } ] };
const answers = {
  '/api/status': { root: P, cluster_size_kb: 512 },
  '/api/audit': {
    summary: { total_files: 1 },
    taxonomies: { [P]: { slack_percentage: 5, file_count: 3, nominal_bytes: 1, allocated_bytes: 2 } },
    top_slack_directories: [ { rel_path: P, name: P, recursive_files: 1, recursive_bytes: 1, recursive_allocated: 2, recursive_slack: 1, recursive_slack_percentage: 50 } ],
  },
  '/api/junk': { tiers: { '1': tier, '2': tier, '3': tier } },
  '/api/search': { results: [ { id: 1, name: P, filename: P, path: P, extension: P, category: P, size: 10, allocated_size: 20, slack_bytes: 10, rank: P, mtime: P } ], total: 1, latency_ms: 1, index_exists: true, warnings: [P] },
};
const ctx = {
  document, window: { addEventListener() {} }, console, performance, alert() {}, setTimeout, clearTimeout, encodeURIComponent,
  fetch: async (url) => ({ json: async () => answers[String(url).split('?')[0]] }),
};
vm.createContext(ctx);
vm.runInContext(script, ctx);
(async () => {
  await ctx.loadStatus(); await ctx.loadAudit(); await ctx.loadJunkPreview();
  elements['search-input'] = makeEl('search-input'); elements['search-input'].value = 'q';
  await ctx.executeSearch();
  // the "nothing found" and "no index yet" rows, with a poisoned server message in the answer
  answers['/api/search'] = { results: [], total: 0, index_exists: true, message: P, warnings: [P] };
  await ctx.executeSearch();
  answers['/api/search'] = { results: [], total: 0, index_exists: false, message: P, warnings: [P] };
  await ctx.executeSearch();
  const others = Object.values(elements).map(e => e._c || '').filter(Boolean);
  process.stdout.write(JSON.stringify({ innerHTML: captured, textContent: others }));
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(_node(), "node is needed to run the dashboard's JavaScript")
class TestTheRenderedMarkupContainsNoInjection(unittest.TestCase):
    """The shipped script, fed attack payloads in every string field, as the browser would run it."""

    def run_dashboard(self) -> Dict[str, List[str]]:
        with tempfile.TemporaryDirectory(prefix="sd_dash_") as tmp:
            script_file, harness_file = Path(tmp) / "dashboard.js", Path(tmp) / "harness.js"
            script_file.write_text("\n".join(_scripts()), encoding="utf-8")
            harness_file.write_text(HARNESS, encoding="utf-8")
            proc = subprocess.run(
                [_node(), str(harness_file), str(script_file), json.dumps(PAYLOAD)],
                capture_output=True, timeout=120, encoding="utf-8",
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_only_the_dashboards_own_tags_and_no_event_handlers_come_out(self) -> None:
        output = self.run_dashboard()
        self.assertGreaterEqual(len([m for m in output["innerHTML"] if m]), 8)  # taxonomy card, hotspot row, search row, 3 junk rows, 2 empty rows
        for markup in output["innerHTML"]:
            parser = _Tags()
            parser.feed(markup)
            parser.close()
            self.assertLessEqual(set(parser.tags), TEMPLATE_TAGS, f"foreign tag in: {markup[:200]!r}")
            for name, value in parser.attributes:
                self.assertFalse(name.startswith("on"), f"event handler {name}= in: {markup[:200]!r}")
                self.assertNotIn("javascript:", value.lower())

    def test_the_payload_is_shown_as_text_not_dropped(self) -> None:
        shown = "".join(t for markup in self.run_dashboard()["innerHTML"] for t in _text_of(markup))
        self.assertIn(PAYLOAD, shown)  # it is visible to the user, exactly as the file is named

    def test_warnings_from_the_server_are_written_as_text(self) -> None:
        output = self.run_dashboard()
        self.assertTrue(any(PAYLOAD in text for text in output["textContent"]))  # textContent, never parsed as HTML


def _text_of(markup: str) -> List[str]:
    parser = _Tags()
    parser.feed(markup)
    parser.close()
    return parser.text


@unittest.skipUnless(_node(), "node is needed to run the dashboard's JavaScript")
class TestEscapeHelperBehaviour(unittest.TestCase):
    """Runs the helper the dashboard really ships, not a copy of it."""

    def run_esc(self, cases: List[str]) -> List[str]:
        joined = "\n".join(_scripts())
        match = re.search(r"function esc\(value\) \{.*?\n    \}", joined, re.DOTALL)
        self.assertIsNotNone(match, "esc() helper not found in the dashboard script")
        program = "%s\nconst cases = [%s];\nprocess.stdout.write(JSON.stringify(cases.map(esc)));\n" % (
            match.group(0),
            ", ".join(cases),
        )
        with tempfile.TemporaryDirectory(prefix="sd_esc_") as tmp:
            path = Path(tmp) / "check.js"
            path.write_text(program, encoding="utf-8")
            proc = subprocess.run([_node(), str(path)], capture_output=True, timeout=60, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_html_metacharacters_are_neutralised(self) -> None:
        payload = '<img src=x onerror="alert(1)">'
        result = self.run_esc([json.dumps(payload), json.dumps("a'b & c")])
        self.assertEqual(result[0], "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;")
        self.assertEqual(result[1], "a&#39;b &amp; c")

    def test_escaped_text_cannot_be_smuggled_through_twice(self) -> None:
        self.assertEqual(self.run_esc([json.dumps("&lt;b&gt;")]), ["&amp;lt;b&amp;gt;"])

    def test_missing_values_and_numbers_are_rendered_sensibly(self) -> None:
        self.assertEqual(self.run_esc(["null", "undefined", "0", "12.5"]), ["", "", "0", "12.5"])

    def test_ordinary_names_are_left_alone(self) -> None:
        name = "tài liệu - báo cáo (final) 📄.pdf"
        self.assertEqual(self.run_esc([json.dumps(name)]), [name])


if __name__ == "__main__":
    unittest.main()
