"""tests.test_readme_vn - README_VN.md holds no word that was cut off in the middle of a syllable.

A code comment of the relink examples ended on "trướ" ("...hỏi y/N cho từng mục ? trướ"). No Vietnamese syllable ends
there: â and ă always take a final consonant, and the diphthongs ươ, iê/yê and uô are written ưa, ia/ya and ua when
nothing follows them. A word that ends so lost its last letters.
"""

from __future__ import annotations

import re
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CUT_OFF_ENDING = re.compile(r"(?:[ăắằẳẵặâấầẩẫậ]|ư[ơớờởỡợ]|[iy][êếềểễệ]|u[ôốồổỗộ])$", re.IGNORECASE)
WORD = re.compile(r"[^\W\d_]+")


class TestReadmeVn(unittest.TestCase):
    def test_the_check_finds_cut_off_words_and_not_whole_ones(self) -> None:
        for cut in ("trướ", "Trướ", "ngườ", "tiế", "nhiề", "chuyể", "cuố", "tắ", "chầ"):
            with self.subTest(cut=cut):
                self.assertTrue(CUT_OFF_ENDING.search(cut))
        for whole in ("trước", "người", "rượu", "tiếp", "nhiều", "chuyển", "cuối", "mua", "kia", "đứt", "thư", "cơ"):
            with self.subTest(whole=whole):
                self.assertFalse(CUT_OFF_ENDING.search(whole))

    def test_no_word_of_the_vietnamese_readme_is_cut_off(self) -> None:
        text = unicodedata.normalize("NFC", (ROOT / "README_VN.md").read_text(encoding="utf-8"))
        found = [
            f"line {number}: {word}"
            for number, line in enumerate(text.splitlines(), 1)
            for word in WORD.findall(line)
            if CUT_OFF_ENDING.search(word)
        ]
        self.assertEqual(found, [], "a word ends where no Vietnamese syllable can end: its last letters are missing")


if __name__ == "__main__":
    unittest.main()
