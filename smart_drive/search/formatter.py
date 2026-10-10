"""smart_drive.search.formatter - Multi-format Output Formatter for Search Results.

Supports:
- Terminal ASCII / UTF-8 tabular formatting
- JSON serialization
- CSV tabular export
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime
from typing import List

from smart_drive.search.engine import SearchResult


def format_bytes(size: int) -> str:
    """Formats raw byte counts into human-readable strings."""
    if size < 1024:
        return f"{size} B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    val = float(size)
    for u in units:
        val /= 1024.0
        if val < 1024.0 or u == "PB":
            return f"{val:.1f} {u}"
    return f"{size} B"


def format_table(result: SearchResult, max_rows: int = 50) -> str:
    """Formats search results as a visual terminal table."""
    buf = io.StringIO()
    if not result.matches:
        buf.write(f"\nFound {result.total_count:,} matching items ({result.elapsed_ms:.1f} ms):\n\n")
        buf.write("  No files matching the specified criteria.\n")
        return buf.getvalue()

    is_chunk = any(m.kind is not None for m in result.matches)
    if is_chunk:
        buf.write(f"\nFound {result.total_count:,} matching chunks ({result.elapsed_ms:.1f} ms):\n\n")
        rows = result.matches[:max_rows]
        for m in rows:
            path_display = m.path
            if len(path_display) > 50:
                path_display = "…" + path_display[-47:]
            parts = [path_display]
            if m.locator:
                parts.append(m.locator)
            elif m.kind:
                parts.append(m.kind)
            if m.snippet:
                parts.append(m.snippet)
            line = " · ".join(parts)
            buf.write(f"  {line}\n")

        if len(result.matches) > max_rows:
            buf.write(f"\n... and {len(result.matches) - max_rows} more chunks.\n")
        return buf.getvalue()

    buf.write(f"\nFound {result.total_count:,} matching files ({result.elapsed_ms:.1f} ms):\n\n")

    rows = result.matches[:max_rows]
    headers = ["Category", "Size", "Modified", "Path"]

    data = []
    for m in rows:
        dt = datetime.fromtimestamp(m.mtime).strftime("%Y-%m-%d %H:%M")
        data.append([
            m.category,
            format_bytes(m.size),
            dt,
            m.path,
        ])

    col_widths = [len(h) for h in headers]
    for row in data:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    col_widths[3] = min(col_widths[3], 70)

    header_line = "  ".join(f"{h:<{w}}" for h, w in zip(headers, col_widths))
    sep_line = "  ".join("-" * w for w in col_widths)
    buf.write(f"{header_line}\n")
    buf.write(f"{sep_line}\n")

    for row in data:
        cat, sz, dt, p = row
        if len(p) > col_widths[3]:
            p = "..." + p[-(col_widths[3] - 3):]
        buf.write(f"{cat:<{col_widths[0]}}  {sz:>{col_widths[1]}}  {dt:<{col_widths[2]}}  {p:<{col_widths[3]}}\n")

    if len(result.matches) > max_rows:
        buf.write(f"\n... and {len(result.matches) - max_rows} more files.\n")

    return buf.getvalue()


def export_json(result: SearchResult) -> str:
    """Exports results to formatted JSON string."""
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)


def export_csv(result: SearchResult) -> str:
    """Exports results to CSV formatted string."""
    out = io.StringIO()
    writer = csv.writer(out)
    is_chunk = any(m.kind is not None for m in result.matches)
    if is_chunk:
        writer.writerow(["id", "kind", "unit_id", "locator", "snippet", "path", "category", "size_bytes", "modified_timestamp"])
        for m in result.matches:
            writer.writerow([m.id, m.kind or "", m.unit_id or "", m.locator or "", m.snippet or "", m.path, m.category, m.size, m.mtime])
    else:
        writer.writerow(["id", "category", "size_bytes", "modified_timestamp", "filename", "path"])
        for m in result.matches:
            writer.writerow([m.id, m.category, m.size, m.mtime, m.name, m.path])
    return out.getvalue()


__all__ = [
    "format_bytes",
    "format_table",
    "export_json",
    "export_csv",
]
