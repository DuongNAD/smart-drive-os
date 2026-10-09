"""tests/test_search_quirks.py - `search` must do what the query says, or say that it did not.

Regressions:
- `size:>10MB` also returned a file of exactly 10MB (`>` and `<` were treated like `>=` and `<=`).
- A size filter that could not be read (`size:>abc`, `size:>10 MB`) was dropped without a word, so the
  search looked fine while returning everything. It is now a warning on every surface (CLI stderr, MCP
  `warnings`) and the parser stays lenient.
- `1PB` was read as one byte, a 400-digit size crashed the parser, and a size beyond 64 bits crashed SQLite.
- `dir:my_project` also matched `myXproject` and `dir:100%` matched `1000`: `_` and `%` were LIKE wildcards.
- An empty answer for a category that does not exist now lists the categories the index really has.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from smart_drive.cli.main import main
from smart_drive.mcp.server import SmartDriveMCPServer
from smart_drive.search.engine import SearchEngine, _like_contains
from smart_drive.search.parser import SearchParams, apply_size_spec, parse_search_query, parse_size_spec
from smart_drive.indexer.db import DatabaseManager

SIZES = {
    "sizes/s099.bin": 99,
    "sizes/s100.bin": 100,
    "sizes/s101.bin": 101,
    "sizes/k1.bin": 1024,
    "sizes/k1plus.bin": 1025,
}
FRACTIONAL = {"frac/f101.frc": 101, "frac/f102.frc": 102, "frac/f103.frc": 103, "frac/f104.frc": 104}
OTHER_FILES = {
    "my_project/a.txt": 5,
    "myXproject/b.txt": 5,
    "100%/c.txt": 5,
    "1000/d.txt": 5,
    "code/tool.py": 7,
    "empty/zero.dat": 0,
    "models/llama.gguf": 9,
}


class _Drive(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_quirk_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for rel, size in {**SIZES, **FRACTIONAL, **OTHER_FILES}.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)
        self.cli("index", "--root", str(self.root))

    def cli(self, *argv: str) -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def found(self, query: str, *extra: str) -> List[str]:
        """Names of the files `search --json` returns."""
        code, out, _ = self.cli("search", "--root", str(self.root), "--json", "--limit", "100", *extra, query)
        self.assertEqual(code, 0)
        return sorted(Path(m["path"]).name for m in json.loads(out))

    def bins(self, spec: str) -> List[str]:
        return self.found(f"size:{spec}", "--ext", "bin")

    def engine(self) -> SearchEngine:
        db = DatabaseManager(str(self.root / ".smart_drive" / "index.db"))
        self.addCleanup(db.close)
        return SearchEngine(db)

    def categories_in_index(self) -> List[str]:
        db = sqlite3.connect(str(self.root / ".smart_drive" / "index.db"))
        try:
            return sorted(row[0] for row in db.execute("SELECT DISTINCT category FROM files"))
        finally:
            db.close()


class TestSizeBoundsAreStrictOrInclusiveAsWritten(_Drive):
    def test_greater_than_excludes_the_bound(self) -> None:
        self.assertEqual(self.bins(">100"), ["k1.bin", "k1plus.bin", "s101.bin"])

    def test_greater_or_equal_includes_the_bound(self) -> None:
        self.assertEqual(self.bins(">=100"), ["k1.bin", "k1plus.bin", "s100.bin", "s101.bin"])

    def test_less_than_excludes_the_bound(self) -> None:
        self.assertEqual(self.bins("<100"), ["s099.bin"])

    def test_less_or_equal_includes_the_bound(self) -> None:
        self.assertEqual(self.bins("<=100"), ["s099.bin", "s100.bin"])

    def test_a_bare_size_is_an_exact_match(self) -> None:
        self.assertEqual(self.bins("100"), ["s100.bin"])

    def test_units_follow_the_same_rule(self) -> None:
        self.assertEqual(self.bins(">1KB"), ["k1plus.bin"])
        self.assertEqual(self.bins(">=1KB"), ["k1.bin", "k1plus.bin"])
        self.assertEqual(self.bins("<1KB"), ["s099.bin", "s100.bin", "s101.bin"])
        self.assertEqual(self.bins("<=1KB"), ["k1.bin", "s099.bin", "s100.bin", "s101.bin"])

    def test_the_size_flag_behaves_like_the_query_syntax(self) -> None:
        self.assertEqual(self.found("", "--ext", "bin", "--size", ">100"), ["k1.bin", "k1plus.bin", "s101.bin"])
        self.assertEqual(self.found("", "--ext", "bin", "--size", "<100"), ["s099.bin"])

    def test_two_bounds_make_a_range(self) -> None:
        self.assertEqual(self.found("size:>99 size:<101", "--ext", "bin"), ["s100.bin"])

    def test_a_bound_between_two_whole_sizes_is_rounded_the_way_the_comparison_needs(self) -> None:
        """0.1KB is 102.4 bytes: no file is exactly that, 102 is below it and 103 above."""
        cases = {
            ">=0.1KB": ["f103.frc", "f104.frc"],
            ">0.1KB": ["f103.frc", "f104.frc"],
            "<0.1KB": ["f101.frc", "f102.frc"],  # used to drop the 102-byte file
            "<=0.1KB": ["f101.frc", "f102.frc"],
            "0.1KB": [],  # used to match the 102-byte file
        }
        for spec, expected in cases.items():
            with self.subTest(spec=spec):
                self.assertEqual(self.found(f"size:{spec}", "--ext", "frc"), expected)

    def test_fractions_of_a_byte(self) -> None:
        self.assertEqual(self.found("size:<0.5", "--ext", "dat"), ["zero.dat"])  # used to find nothing
        self.assertEqual(self.found("size:<=0.5", "--ext", "dat"), ["zero.dat"])
        self.assertEqual(self.found("size:>=0.5", "--ext", "dat"), [])  # used to include the empty file
        self.assertEqual(self.found("size:>0.5", "--ext", "dat"), [])
        self.assertEqual(self.found("size:0.5", "--ext", "dat"), [])

    def test_a_second_bound_can_tighten_but_never_loosen(self) -> None:
        self.assertEqual(self.found("size:>100 size:>1KB", "--ext", "bin"), ["k1plus.bin"])
        self.assertEqual(self.found("size:>1KB size:>100", "--ext", "bin"), ["k1plus.bin"])
        self.assertEqual(self.found("size:<100 size:<1KB", "--ext", "bin"), ["s099.bin"])
        self.assertEqual(self.found("size:<1KB size:<100", "--ext", "bin"), ["s099.bin"])

    def test_nothing_is_smaller_than_zero(self) -> None:
        self.assertEqual(self.found("size:<0", "--ext", "dat"), [])  # used to return the empty file
        self.assertEqual(self.found("size:<=0", "--ext", "dat"), ["zero.dat"])
        self.assertEqual(self.found("size:>0", "--ext", "dat"), [])
        self.assertEqual(self.found("size:>=0", "--ext", "dat"), ["zero.dat"])
        self.assertEqual(len(self.bins(">=0")), len(SIZES))

    def test_sizes_beyond_64_bits_mean_nothing_or_everything_not_a_crash(self) -> None:
        huge = "9" * 30
        self.assertEqual(self.bins(f">{huge}"), [])
        self.assertEqual(len(self.bins(f"<{huge}")), len(SIZES))
        self.assertEqual(self.bins(f">{'9' * 400}"), [])


class TestSizeSpecParsing(unittest.TestCase):
    def test_petabytes_are_petabytes_not_one_byte(self) -> None:
        self.assertEqual(parse_size_spec("1PB"), ("=", 1024 ** 5))
        self.assertEqual(parse_size_spec(">2p"), (">", 2 * 1024 ** 5))

    def test_fractions_stay_exact(self) -> None:
        self.assertEqual(parse_size_spec("1.5KB"), ("=", 1536))

    def test_an_absurdly_long_number_is_invalid_not_a_crash(self) -> None:
        self.assertEqual(parse_size_spec("9" * 400)[0], "=")  # a huge but valid number
        for digits in (400, 5000):
            params = parse_search_query("size:>" + "9" * digits)
            self.assertGreater(params.min_size, 10 ** 30)
            self.assertEqual(params.warnings, [])

    def test_apply_size_spec_reports_whether_it_applied(self) -> None:
        params = SearchParams()
        self.assertTrue(apply_size_spec(params, ">10MB"))
        self.assertEqual(params.min_size, 10 * 1024 ** 2 + 1)
        self.assertFalse(apply_size_spec(params, "lots"))
        self.assertEqual(params.min_size, 10 * 1024 ** 2 + 1)  # unchanged by the bad one
        self.assertEqual(len(params.warnings), 1)


class TestUnreadableFiltersAreReported(unittest.TestCase):
    BAD = ("size:>abc", "size:", "size:>", "size:10XB", "size:1e9", "size:>-5", "size:<>5", "size:=>5", "size:><5")

    def test_each_unreadable_size_filter_adds_one_warning_and_sets_no_bound(self) -> None:
        for query in self.BAD:
            with self.subTest(query=query):
                params = parse_search_query(query)
                self.assertEqual(len(params.warnings), 1, params.warnings)
                self.assertIn(query.split(":", 1)[1] or "''", params.warnings[0])
                self.assertIsNone(params.min_size)
                self.assertIsNone(params.max_size)

    def test_the_rest_of_the_query_still_applies(self) -> None:
        params = parse_search_query("report ext:pdf size:>abc cat:Docs")
        self.assertEqual((params.keyword, params.extensions, params.category), ("report", {"pdf"}, "Docs"))
        self.assertEqual(len(params.warnings), 1)

    def test_a_unit_after_a_space_is_pointed_out(self) -> None:
        params = parse_search_query("size:>10 MB")
        self.assertEqual(params.min_size, 11)  # still read as bytes, as before, but no longer silently
        self.assertEqual(len(params.warnings), 1)
        self.assertIn("size:>10MB", params.warnings[0])

    def test_warnings_are_deduplicated_and_capped(self) -> None:
        from smart_drive.search.engine import MAX_WARNINGS

        tmp = tempfile.mkdtemp(prefix="sd_warn_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db = DatabaseManager(str(Path(tmp) / "index.db"))  # (":memory:" would become a file in the working directory)
        db.initialize_schema()
        self.addCleanup(db.close)
        engine = SearchEngine(db)
        repeated = engine.search(parse_search_query(" ".join(["size:x"] * 140)))
        self.assertEqual(len(repeated.warnings), 1)
        distinct = engine.search(parse_search_query(" ".join(f"size:x{i}" for i in range(30))))
        self.assertEqual(len(distinct.warnings), MAX_WARNINGS + 1)
        self.assertEqual(distinct.warnings[-1], "... and 20 more warning(s)")

    def test_ordinary_queries_have_no_warnings(self) -> None:
        for query in ("", "llama", "ext:pdf size:>1MB cat:AI dir:foo", "size:>10MB report", "report mb", "size:>10 report"):
            with self.subTest(query=query):
                self.assertEqual(parse_search_query(query).warnings, [])

    def test_the_parser_never_raises(self) -> None:
        for query in ("size:\x00", "size:" + "9" * 5000, "size:١٢", 'size:"', "size:>>>>>", "SIZE:>1Mb", "size:'1'"):
            with self.subTest(query=query[:20]):
                self.assertIsInstance(parse_search_query(query).warnings, list)


class TestWarningsReachTheCaller(_Drive):
    def test_the_engine_carries_parser_warnings_and_omits_the_key_when_clean(self) -> None:
        engine = self.engine()
        result = engine.search(parse_search_query("size:>abc"))
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("warnings", result.to_dict())
        clean = engine.search(parse_search_query("ext:py"))
        self.assertEqual(clean.warnings, [])
        self.assertEqual(set(clean.to_dict()), {"matches", "total_count", "elapsed_ms"})

    def test_cli_warns_on_stderr_and_keeps_stdout_machine_readable(self) -> None:
        code, out, err = self.cli("search", "--root", str(self.root), "--json", "size:>abc")
        self.assertEqual(code, 0)
        self.assertIn("Warning: Ignored size filter", err)
        self.assertIsInstance(json.loads(out), list)
        _, _, err_flag = self.cli("search", "--root", str(self.root), "--json", "--size", "abc")
        self.assertIn("Warning: Ignored size filter 'abc'", err_flag)
        _, _, quiet = self.cli("search", "--root", str(self.root), "--json", "ext:py")
        self.assertEqual(quiet, "")

    def test_mcp_search_reports_warnings_only_when_there_are_some(self) -> None:
        server = SmartDriveMCPServer(root=str(self.root))
        bad = server.handle_ssd_search({"query": "size:>abc"})
        self.assertEqual(len(bad["warnings"]), 1)
        flag = server.handle_ssd_search({"query": "ext:py", "size": "abc"})
        self.assertIn("abc", flag["warnings"][0])
        self.assertNotIn("warnings", server.handle_ssd_search({"query": "ext:py"}))

    def test_mcp_size_given_as_a_json_number_is_not_ignored(self) -> None:
        """`size: 0` is falsy in Python, so the filter used to vanish without a word."""
        server = SmartDriveMCPServer(root=str(self.root))
        self.assertEqual(server.handle_ssd_search({"ext": "bin", "size": 0})["total_count"], 0)
        exact = server.handle_ssd_search({"ext": "bin", "size": 100})
        self.assertEqual([Path(m["path"]).name for m in exact["matches"]], ["s100.bin"])
        self.assertEqual(server.handle_ssd_search({"ext": "dat", "size": 0})["total_count"], 1)  # zero.dat
        self.assertIn("warnings", server.handle_ssd_search({"ext": "bin", "size": True}))  # nonsense is reported

    def test_mcp_search_applies_strict_bounds(self) -> None:
        server = SmartDriveMCPServer(root=str(self.root))
        names = sorted(Path(m["path"]).name for m in server.handle_ssd_search({"ext": "bin", "size": ">100"})["matches"])
        self.assertEqual(names, ["k1.bin", "k1plus.bin", "s101.bin"])


class TestCategoryTypos(_Drive):
    def test_an_unknown_category_lists_the_real_ones(self) -> None:
        result = self.engine().search(parse_search_query("category:Documents"))
        self.assertEqual(result.total_count, 0)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("'Documents'", result.warnings[0])
        for present in self.categories_in_index():
            self.assertIn(present, result.warnings[0])

    def test_a_known_but_absent_category_is_explained_too(self) -> None:
        result = self.engine().search(parse_search_query("category:Media"))
        self.assertEqual(result.total_count, 0)
        self.assertEqual(len(result.warnings), 1)

    def test_a_real_category_with_matches_has_no_warning(self) -> None:
        result = self.engine().search(parse_search_query("category:Code"))
        self.assertGreater(result.total_count, 0)
        self.assertEqual(result.warnings, [])

    def test_a_real_category_that_other_filters_emptied_is_not_a_typo(self) -> None:
        result = self.engine().search(parse_search_query("category:Code ext:zzz"))
        self.assertEqual(result.total_count, 0)
        self.assertEqual(result.warnings, [])

    def test_the_cli_shows_it(self) -> None:
        _, _, err = self.cli("search", "--root", str(self.root), "--json", "--category", "Documents")
        self.assertIn("No file has category 'Documents'", err)

    def test_only_a_real_typo_pays_for_listing_the_categories(self) -> None:
        """Telling a typo from an emptied-out real category is an index seek; the full listing is for typos."""
        engine = self.engine()
        statements: List[str] = []
        engine.db.get_connection().set_trace_callback(statements.append)

        engine.search(parse_search_query("category:Code ext:zzz"))
        self.assertEqual([s for s in statements if "DISTINCT" in s.upper()], [])

        statements.clear()
        engine.search(parse_search_query("category:Documents"))
        self.assertEqual(len([s for s in statements if "DISTINCT" in s.upper()]), 1)


class TestCategorySpellings(_Drive):
    def test_underscores_hyphens_and_spaces_name_the_same_category(self) -> None:
        for spelling in ("AI Models", "ai_models", "AI-Models", "ai models", "AI", "models"):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.found(f'category:"{spelling}" ext:gguf'), ["llama.gguf"])

    def test_the_readme_example_finds_what_it_promises(self) -> None:
        self.assertEqual(self.found("llama cat:ai_models"), ["llama.gguf"])  # as written in the README

    def test_the_category_flag_accepts_the_same_spellings_without_a_warning(self) -> None:
        code, out, err = self.cli(
            "search", "--root", str(self.root), "--json", "--category", "ai_models", "--ext", "gguf"
        )
        self.assertEqual(code, 0)
        self.assertEqual([Path(m["path"]).name for m in json.loads(out)], ["llama.gguf"])
        self.assertEqual(err, "")

    def test_a_blank_category_is_not_the_first_category(self) -> None:
        result = self.engine().search(SearchParams(category="  "))
        self.assertEqual(result.total_count, 0)


class TestDirectoryWildcards(_Drive):
    def test_an_underscore_matches_only_an_underscore(self) -> None:
        self.assertEqual(self.found("dir:my_project"), ["a.txt"])

    def test_a_percent_sign_matches_only_a_percent_sign(self) -> None:
        self.assertEqual(self.found("dir:100%"), ["c.txt"])

    def test_the_directory_filter_is_still_a_substring_match(self) -> None:
        self.assertEqual(self.found("dir:project"), ["a.txt", "b.txt"])

    def test_like_pattern_escaping(self) -> None:
        self.assertEqual(_like_contains("a_b%c\\d"), "%a\\_b\\%c\\\\d%")
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE t (p TEXT)")
            db.executemany("INSERT INTO t VALUES (?)", [("a_b",), ("aXb",), ("100%",), ("1000",), ("c\\d",), ("cXd",)])
            for text, expected in (("a_b", ["a_b"]), ("100%", ["100%"]), ("c\\d", ["c\\d"])):
                rows = db.execute("SELECT p FROM t WHERE p LIKE ? ESCAPE '\\'", (_like_contains(text),)).fetchall()
                self.assertEqual([r[0] for r in rows], expected, text)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
