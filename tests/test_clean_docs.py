"""tests/test_clean_docs.py - what the documentation says about `clean` is what it does.

The README listed tier members that were never rules (`node_modules/.cache`, Rust `target/debug/build`, `*.log`,
`*.bak`) and put `__pycache__` in the wrong tier, and the agent instructions called Tier 1-2 cleanup something to
run, without saying that it is permanent or what it leaves alone.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from smart_drive.core import initializer
from smart_drive.core.config import JUNK_RULES

ROOT = Path(__file__).resolve().parent.parent
README_TIER_SECTIONS = {"README.md": "## Safe Junk Cleaner", "README_VN.md": "## Dọn Rác An Toàn"}


def tier_section(name: str) -> str:
    text = (ROOT / name).read_text(encoding="utf-8")
    start = text.index(README_TIER_SECTIONS[name])
    return text[start:text.index("\n---\n", start)]


class TestAgentInstructions(unittest.TestCase):
    def texts(self) -> dict:
        return {
            "CLAUDE.md": (ROOT / "CLAUDE.md").read_text(encoding="utf-8"),
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
            "GEMINI.md": (ROOT / "GEMINI.md").read_text(encoding="utf-8"),
            "CLAUDE template": initializer.CLAUDE_MD_TEMPLATE,
            "AGENTS template": initializer.AGENTS_MD_TEMPLATE,
            "GEMINI template": initializer.GEMINI_MD_TEMPLATE,
        }

    def test_they_say_that_apply_deletes_tier_1_2_junk_for_good(self) -> None:
        for name, text in self.texts().items():
            with self.subTest(name=name):
                self.assertIn("Tier 1-2 junk permanently and at once", text)

    def test_they_say_what_clean_leaves_alone(self) -> None:
        for name, text in self.texts().items():
            with self.subTest(name=name):
                self.assertIn("installed packages", text)
                self.assertIn("anything git tracks", text)
                self.assertIn("Windows or installed apps depend on", text)


class TestReadmes(unittest.TestCase):
    def test_every_junk_rule_is_listed_in_the_cleaner_section(self) -> None:
        for name in README_TIER_SECTIONS:
            section = tier_section(name)
            for rule in JUNK_RULES:
                with self.subTest(readme=name, rule=rule.pattern):
                    self.assertIn(rule.pattern, section)

    def test_rules_that_do_not_exist_are_not_promised(self) -> None:
        for name in README_TIER_SECTIONS:
            section = tier_section(name)
            for invented in ("node_modules/.cache", "target/debug/build", "`*.log`", "`*.bak`"):
                with self.subTest(readme=name, invented=invented):
                    self.assertNotIn(invented, section)

    def test_they_tell_to_preview_tier_1_2_before_applying_it(self) -> None:
        for name in README_TIER_SECTIONS:
            with self.subTest(readme=name):
                self.assertIn("smart-drive clean --tier 2", (ROOT / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
