"""tests/test_search.py - Comprehensive Unit Tests for Search Parser, Engine, and Formatters.

100% Python Standard Library unittest.
Tests Tiers 1 & 2:
- Query parsing with ext:, size:, cat:, dir: filters.
- FTS5 token sanitization and punctuation quoting (llama-3-8b, c++, react@18).
- Unbalanced quote and syntax error resilience.
- Sub-100ms SQLite FTS5 BM25 search execution.
- Dual-path search (Path A: FTS5 MATCH vs Path B: B-tree metadata filter).
- Result formatters (Table, JSON, CSV).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.indexer.db import DatabaseManager
    from smart_drive.indexer.manager import IndexManager
    from smart_drive.search.parser import (
        SearchParams,
        parse_search_query,
        parse_size_spec,
        sanitize_fts_query,
    )
    from smart_drive.search.engine import SearchEngine, SearchMatch, SearchResult
    from smart_drive.search.formatter import format_table, export_json as format_json, export_csv as format_csv
except (ImportError, ModuleNotFoundError):
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from indexer.db import DatabaseManager
    from indexer.manager import IndexManager
    from search.parser import (
        SearchParams,
        parse_search_query,
        parse_size_spec,
        sanitize_fts_query,
    )
    from search.engine import SearchEngine, SearchMatch, SearchResult
    from search.formatter import format_table, export_json as format_json, export_csv as format_csv

from tests.helpers import SmartDriveTestCase


class TestSearchParser(SmartDriveTestCase):
    """Tier 1 & Tier 2: Search syntax parsing and query sanitization."""

    def test_parse_free_text_keyword(self) -> None:
        """Parses plain keyword into SearchParams.keyword."""
        params = parse_search_query("llama")
        self.assertEqual(params.keyword, "llama")

    def test_parse_extension_filter(self) -> None:
        """Parses ext: filter with single or multiple extensions."""
        params1 = parse_search_query("ext:pdf")
        self.assertIn("pdf", params1.extensions)

        params2 = parse_search_query("ext:pdf,md,txt")
        self.assertIn("pdf", params2.extensions)
        self.assertIn("md", params2.extensions)
        self.assertIn("txt", params2.extensions)

    def test_parse_size_filter(self) -> None:
        """Parses size:>100MB, size:<1GB, size:1KB."""
        op1, b_val1 = parse_size_spec(">100MB")
        self.assertEqual(op1, ">")
        self.assertEqual(b_val1, 100 * 1024 * 1024)

        op2, b_val2 = parse_size_spec("<=10KB")
        self.assertEqual(op2, "<=")
        self.assertEqual(b_val2, 10 * 1024)

    def test_parse_compound_query(self) -> None:
        """Parses combined keywords, extension, size, and category."""
        params = parse_search_query("model ext:gguf size:>1MB cat:AI")
        self.assertEqual(params.keyword, "model")
        self.assertIn("gguf", params.extensions)
        self.assertGreater(params.min_size, 0)
        self.assertEqual(params.category, "AI")

    def test_sanitize_fts_query_quotes_special_tokens(self) -> None:
        """Punctuation in tokens (hyphens, dots, +, @) must be quoted for FTS5."""
        sanitized = sanitize_fts_query("llama-3-8b")
        self.assertEqual(sanitized, '"llama-3-8b"')

        sanitized_plus = sanitize_fts_query("c++")
        self.assertEqual(sanitized_plus, '"c++"')

        sanitized_at = sanitize_fts_query("react@18")
        self.assertEqual(sanitized_at, '"react@18"')

    def test_sanitize_fts_query_unbalanced_quotes(self) -> None:
        """Unbalanced quotes must be safely closed or stripped without throwing."""
        sanitized = sanitize_fts_query('"machine learning')
        self.assertIsInstance(sanitized, str)
        self.assertNotIn('""', sanitized)

    def test_sanitize_empty_query_returns_empty_string(self) -> None:
        """Empty or whitespace queries return empty string."""
        self.assertEqual(sanitize_fts_query(""), "")
        self.assertEqual(sanitize_fts_query("   "), "")


class TestSearchEngineExecution(SmartDriveTestCase):
    """Tier 1 & Tier 2: Query execution, BM25 ranking, and sub-100ms latency."""

    def setUp(self) -> None:
        super().setUp()
        # Seed test drive and index
        self.mock_root = self.create_mock_drive()
        self.db_file = self.test_dir / "search_test.db"
        self.db = DatabaseManager(str(self.db_file))
        self.db.initialize_schema()
        manager = IndexManager(self.db, str(self.mock_root))
        manager.full_index()
        self.engine = SearchEngine(self.db)

    def tearDown(self) -> None:
        self.db.close()
        super().tearDown()

    def test_search_by_keyword_fts_match(self) -> None:
        """Keyword search uses FTS5 MATCH and returns relevant files with BM25 rank."""
        params = parse_search_query("llama")
        result = self.engine.search(params)

        self.assertIsInstance(result, SearchResult)
        self.assertGreater(result.total_count, 0)
        names = [m.name for m in result.matches]
        self.assertTrue(any("llama" in name for name in names))
        self.assertLess(result.elapsed_ms, 100.0, "Search query must complete in < 100ms")

    def test_search_by_extension_btree_filter(self) -> None:
        """Search with only ext: filter uses fast B-tree index."""
        params = parse_search_query("ext:pdf")
        result = self.engine.search(params)

        self.assertGreater(result.total_count, 0)
        for m in result.matches:
            self.assertEqual(m.extension, ".pdf")
        self.assertLess(result.elapsed_ms, 100.0)

    def test_search_compound_filters(self) -> None:
        """Compound query filtering by extension and directory."""
        params = parse_search_query("ext:md dir:02_Learning_Knowledge")
        result = self.engine.search(params)

        self.assertGreater(result.total_count, 0)
        for m in result.matches:
            self.assertEqual(m.extension, ".md")
            self.assertIn("02_Learning_Knowledge", m.path)

    def test_search_no_results_cleanly(self) -> None:
        """Query with no matches returns empty list without error."""
        params = parse_search_query("nonexistent_keyword_xyz_123")
        result = self.engine.search(params)

        self.assertEqual(result.total_count, 0)
        self.assertEqual(result.matches, [])


class TestSearchFormatters(SmartDriveTestCase):
    """Tier 1: Formatting search results as table, JSON, and CSV."""

    def test_format_json(self) -> None:
        """format_json produces valid JSON string."""
        matches = [
            SearchMatch(
                id=1,
                path="01_AI_Models/model.gguf",
                name="model.gguf",
                extension=".gguf",
                size=1048576,
                mtime=1700000000.0,
                category="AI Models",
                rank=-0.5,
            )
        ]
        result = SearchResult(matches=matches, total_count=1, elapsed_ms=5.0)

        json_output = format_json(result)
        data = json.loads(json_output)
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(len(data["matches"]), 1)
        self.assertEqual(data["matches"][0]["name"], "model.gguf")

    def test_format_csv_and_table(self) -> None:
        """format_csv and format_table produce non-empty strings with headers."""
        matches = [
            SearchMatch(
                id=1,
                path="02_Learning_Knowledge/INDEX.md",
                name="INDEX.md",
                extension=".md",
                size=2048,
                mtime=1700000000.0,
                category="Books/Learning",
                rank=-0.8,
            )
        ]
        result = SearchResult(matches=matches, total_count=1, elapsed_ms=4.0)

        csv_out = format_csv(result)
        self.assertIn("path", csv_out.lower())
        self.assertIn("INDEX.md", csv_out)

        table_out = format_table(result)
        self.assertIn("INDEX.md", table_out)


if __name__ == "__main__":
    unittest.main()
