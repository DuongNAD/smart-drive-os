"""smart_drive.search - Sub-100ms multi-criteria SQLite FTS5 instant search engine."""
from __future__ import annotations

from smart_drive.search.parser import SearchParams, apply_size_spec, parse_search_query, parse_size_spec, sanitize_fts_query
from smart_drive.search.engine import SearchEngine, SearchMatch, SearchResult
from smart_drive.search.formatter import format_table, export_json, export_csv, format_bytes

__all__ = [
    "SearchParams",
    "apply_size_spec",
    "parse_search_query",
    "parse_size_spec",
    "sanitize_fts_query",
    "SearchEngine",
    "SearchMatch",
    "SearchResult",
    "format_table",
    "export_json",
    "export_csv",
    "format_bytes",
]
