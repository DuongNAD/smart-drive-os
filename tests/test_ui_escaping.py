"""tests/test_ui_escaping.py - Names that come from the drive must never reach innerHTML unescaped.

Regression: the dashboard built its table rows with template literals that dropped file, folder and
category names straight into `innerHTML`. On a volume that allows `<` in names (APFS, ext4, ...) a file
called `<img src=x onerror=...>` ran script inside the dashboard the next time it was searched or listed,
and the dashboard has same-origin POST endpoints (clean, snapshot, ...). Every drive-supplied string now
goes through `esc()`, and this test keeps it that way.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import List

from smart_drive.ui.dashboard import get_dashboard_html


def _script() -> str:
    html = get_dashboard_html()
    return html[html.index("<script>"):html.index("</script>")]


def _interpolations(script: str) -> List[str]:
    """Every `${...}` expression used in a template literal of the dashboard script."""
    return [m.group(1).strip() for m in re.finditer(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", script)]


# Words that name a string coming from the drive (or from the API about the drive). Numbers, byte counts and
# fixed class names do not contain these, so they may be interpolated as they are.
_DRIVE_STRING = re.compile(
    r"\b(name|path|rel_path|category|description|rule|filename|extension|error|message|status|reason|rankVal)\b"
)


class TestDashboardInterpolations(unittest.TestCase):
    def setUp(self) -> None:
        self.script = _script()
        self.expressions = _interpolations(self.script)

    def test_the_script_has_an_escape_helper(self) -> None:
        self.assertTrue("function esc(" in self.script, "the dashboard script has no esc() helper")

    def test_every_drive_supplied_string_goes_through_esc(self) -> None:
        offenders = [e for e in self.expressions if _DRIVE_STRING.search(e) and not e.startswith("esc(")]
        self.assertEqual(offenders, [], "wrap these in esc(...) before they reach innerHTML")

    def test_the_known_render_paths_are_all_escaped(self) -> None:
        escaped = {e for e in self.expressions if e.startswith("esc(")}
        for expected in (
            "esc(name)",  # taxonomy cards
            "esc(d.rel_path)",  # slack hotspots
            "esc(m.name)",  # search results
            "esc(m.path)",
            "esc(m.category || 'Other')",
            "esc(item.rel_path)",  # junk preview
        ):
            self.assertIn(expected, escaped)

    def test_nothing_is_written_with_document_write_or_outer_html(self) -> None:
        for sink in ("document.write", "outerHTML", "insertAdjacentHTML"):
            self.assertNotIn(sink, self.script)


@unittest.skipUnless(shutil.which("node"), "node is needed to run the dashboard's JavaScript")
class TestEscapeHelperBehaviour(unittest.TestCase):
    """Runs the helper the dashboard really ships, not a copy of it."""

    def run_esc(self, cases: List[str]) -> List[str]:
        match = re.search(r"function esc\(value\) \{.*?\n    \}", _script(), re.DOTALL)
        self.assertIsNotNone(match, "esc() helper not found in the dashboard script")
        program = "%s\nconst cases = [%s];\nprocess.stdout.write(JSON.stringify(cases.map(esc)));\n" % (
            match.group(0),
            ", ".join(cases),
        )
        with tempfile.TemporaryDirectory(prefix="sd_esc_") as tmp:
            path = Path(tmp) / "check.js"
            path.write_text(program, encoding="utf-8")
            proc = subprocess.run(
                [shutil.which("node"), str(path)], capture_output=True, timeout=60, encoding="utf-8"
            )
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
