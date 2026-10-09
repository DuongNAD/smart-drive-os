"""smart_drive.cli.cmd_search - Instant FTS5 multi-criteria search command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from smart_drive.core.exfat_compat import ExFatEngine
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.indexer.db import DatabaseManager
from smart_drive.search.engine import SearchEngine
from smart_drive.search.formatter import export_csv, export_json, format_table
from smart_drive.search.parser import SearchParams, apply_size_spec, parse_search_query


def get_default_db_path(root: str) -> str:
    """Default location for the SQLite search database."""
    # Check new location first, then legacy location
    new_path = os.path.join(root, ".smart_drive", "index.db")
    if os.path.exists(new_path):
        return new_path
    legacy_path = os.path.join(root, ".smart_drive_manager", "index.db")
    if os.path.exists(legacy_path):
        return legacy_path
    return new_path


def cmd_search(args: argparse.Namespace) -> int:
    """Handles the `search` subcommand."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2
    db_path = getattr(args, "db", None)
    if not db_path:
        db_path = get_default_db_path(root)
    else:
        db_path = os.path.abspath(db_path)

    if not os.path.exists(db_path):
        sys.stderr.write(f"Error: Database file not found at '{db_path}'. Please run 'index' first.\n")
        return 1

    db = DatabaseManager(db_path)
    engine = SearchEngine(db)

    raw_query = getattr(args, "query", "") or ""
    params = parse_search_query(raw_query)

    # CLI flag overrides
    ext_flag = getattr(args, "ext", None)
    if ext_flag:
        for e in ext_flag.split(","):
            clean_e = e.strip().lstrip(".")
            if clean_e:
                params.extensions.add(clean_e)

    category_flag = getattr(args, "category", None)
    if category_flag:
        params.category = category_flag

    dir_flag = getattr(args, "dir", None)
    if dir_flag:
        params.directory = dir_flag

    size_flag = getattr(args, "size", None)
    if size_flag:
        apply_size_spec(params, size_flag)

    limit_flag = getattr(args, "limit", None)
    if limit_flag is not None:
        params.limit = int(limit_flag)

    result = engine.search(params)

    # On stderr so that --json / --csv output stays machine-readable.
    for warning in result.warnings:
        sys.stderr.write(f"Warning: {warning}\n")

    if getattr(args, "json", False):
        print(json.dumps([m.to_dict() for m in result.matches], indent=2, ensure_ascii=False))
    elif getattr(args, "csv", False):
        print(export_csv(result))
    else:
        print(format_table(result))

    return 0
