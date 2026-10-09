"""smart_drive.search.parser - Query Syntax Parser and FTS Sanitizer.

Parses compound query syntax:
- Keywords & phrases: llama, "machine learning"
- Extension filters: ext:pdf,md or .pdf
- Size filters: size:>100MB, size:<=10GB, size:>1KB, size:0 (> and < are strict, >= and <= include the bound)
- Category filters: cat:AI, category:Code, tag:Docs
- Path / Directory filters: dir:02_Learning_Knowledge
- Sub-100ms FTS5 token sanitizer resilient to unbalanced syntax.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import List, Optional, Set, Tuple


SIZE_REGEX = re.compile(r"^([><=]{1,2})?(\d+(?:\.\d+)?)\s*([kmgtp]?b?)?$", re.IGNORECASE)
SIZE_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024 ** 2,
    "mb": 1024 ** 2,
    "g": 1024 ** 3,
    "gb": 1024 ** 3,
    "t": 1024 ** 4,
    "tb": 1024 ** 4,
    "p": 1024 ** 5,
    "pb": 1024 ** 5,
}


@dataclass
class SearchParams:
    """Structured parameters for search engine execution."""
    keyword: Optional[str] = None
    extensions: Set[str] = field(default_factory=set)
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    min_mtime: Optional[float] = None
    max_mtime: Optional[float] = None
    category: Optional[str] = None
    directory: Optional[str] = None
    limit: int = 100
    offset: int = 0
    # Filters that could not be applied (e.g. "size:>abc"). The parser stays lenient, but says so.
    warnings: List[str] = field(default_factory=list)


def sanitize_fts_query(query: str) -> str:
    """Sanitizes raw user input into safe SQLite FTS5 query string.

    Prevents syntax errors from unbalanced quotes, dangling boolean operators,
    or wildcard abuse. Quotes punctuation in tokens (hyphens, dots, colons, +, @, #) to
    prevent FTS5 syntax errors (e.g. llama-3-8b, c++, react@18).
    """
    if not query:
        return ""

    clean = query.strip()
    # Normalize excessive quotes
    quote_count = clean.count('"')
    if quote_count % 2 != 0:
        clean = clean.replace('"', "")

    try:
        tokens = shlex.split(clean, posix=False)
    except ValueError:
        tokens = clean.split()

    reserved_words = {"AND", "OR", "NOT", "NEAR"}

    # If query contains only reserved words or symbols, return empty
    non_reserved = [
        t for t in tokens
        if t.upper() not in reserved_words and t not in {'*', '(', ')', '{', '}', ':'}
    ]
    if not non_reserved:
        return ""

    safe_tokens = []
    for i, token in enumerate(tokens):
        t_upper = token.upper()
        if t_upper in reserved_words:
            if not safe_tokens or safe_tokens[-1].upper() in reserved_words or i == len(tokens) - 1:
                continue
            safe_tokens.append(t_upper)
        else:
            sub_tok = re.sub(r"\*+", "*", token)
            if sub_tok == "*" or sub_tok in {'(', ')', '{', '}', ':'}:
                continue

            has_star = sub_tok.endswith("*")
            bare_tok = sub_tok[:-1] if has_star else sub_tok

            if sub_tok.startswith('"') and sub_tok.endswith('"') and len(sub_tok) >= 2:
                safe_tokens.append(sub_tok)
            elif any(not c.isalnum() and c != '"' for c in bare_tok):
                clean_inner = bare_tok.replace('"', "")
                if clean_inner:
                    safe_tokens.append(f'"{clean_inner}"' + ("*" if has_star else ""))
            else:
                safe_tokens.append(sub_tok)

    while safe_tokens and safe_tokens[-1].upper() in reserved_words:
        safe_tokens.pop()

    return " ".join(safe_tokens)


def _parse_size_exact(val: str) -> Tuple[Optional[str], Optional[Decimal]]:
    """(operator, exact number of bytes) - possibly fractional, as in '0.1KB' = 102.4 bytes."""
    m = SIZE_REGEX.match(val.strip())
    if not m:
        return None, None
    op = m.group(1) or "="
    unit = (m.group(3) or "b").lower()
    mult = SIZE_MULTIPLIERS.get(unit, 1)
    try:
        return op, Decimal(m.group(2)) * mult  # exact, and a 400-digit number is not an OverflowError
    except (ArithmeticError, ValueError):
        return None, None


def parse_size_spec(val: str) -> Tuple[Optional[str], Optional[int]]:
    """Parses size spec like '>1KB', '<=100MB', '0' into (operator, whole bytes, truncated)."""
    op, exact = _parse_size_exact(val)
    if exact is None:
        return None, None
    try:
        return op, int(exact)
    except (ArithmeticError, ValueError):  # ValueError: Python's own limit on very long integers
        return None, None


def apply_size_spec(params: SearchParams, spec: str) -> bool:
    """Narrows `params` to a size spec such as '>10MB'; False (and a warning) if it is not one.

    Sizes are whole bytes, so a strict bound is the inclusive one moved by a byte: '>10MB' means
    at least 10MB + 1 and '<10MB' at most 10MB - 1, while '>=' and '<=' keep the bound itself.
    A bound that falls between two whole sizes ('0.1KB' = 102.4 bytes) is rounded the way the comparison
    needs: '>= 0.1KB' starts at 103, '<= 0.1KB' ends at 102, and '= 0.1KB' matches nothing.
    Several size filters all have to hold, so a later one never loosens an earlier one.
    """
    op, exact = _parse_size_exact(spec)
    try:
        floor_bound = int(exact.to_integral_value(rounding=ROUND_FLOOR)) if exact is not None else None
        ceil_bound = int(exact.to_integral_value(rounding=ROUND_CEILING)) if exact is not None else None
    except (ArithmeticError, ValueError):
        floor_bound = ceil_bound = None
    if floor_bound is None or ceil_bound is None:
        params.warnings.append(
            f"Ignored size filter {spec!r}: expected a number with an optional unit (B, KB, MB, GB, TB, PB), "
            "optionally after >, >=, <, <= or ="
        )
        return False
    lower: Optional[int] = None
    upper: Optional[int] = None
    if op == ">":
        lower = floor_bound + 1  # more than 102.4 means at least 103; more than 102 means at least 103
    elif op == ">=":
        lower = ceil_bound
    elif op == "<":
        upper = ceil_bound - 1
    elif op == "<=":
        upper = floor_bound
    elif op == "=":
        lower, upper = ceil_bound, floor_bound  # for a fractional size lower > upper: no file has it
    else:
        params.warnings.append(f"Ignored size filter {spec!r}: operator {op!r} is not one of >, >=, <, <=, =")
        return False
    if lower is not None:
        params.min_size = lower if params.min_size is None else max(params.min_size, lower)
    if upper is not None:
        params.max_size = upper if params.max_size is None else min(params.max_size, upper)
    return True


def parse_search_query(query_str: str) -> SearchParams:
    """Parses free-text query string into structured SearchParams."""
    params = SearchParams()
    if not query_str:
        return params

    try:
        parts = shlex.split(query_str)
    except ValueError:
        parts = query_str.split()

    free_words = []
    bare_size: Optional[str] = None  # the previous token if it was a size filter without a unit, e.g. "size:>10"

    for part in parts:
        p_lower = part.lower()

        if bare_size is not None and p_lower in SIZE_MULTIPLIERS and p_lower:
            params.warnings.append(
                f"{bare_size!r} is followed by {part!r} as a separate word; "
                f"write the unit without a space ({bare_size}{part}) or the size is read in bytes"
            )
        bare_size = part if p_lower.startswith("size:") and p_lower[-1:].isdigit() else None

        # ext:pdf,md or .pdf
        if p_lower.startswith("ext:"):
            exts = p_lower[4:].split(",")
            for e in exts:
                clean_e = e.strip().lstrip(".")
                if clean_e:
                    params.extensions.add(clean_e)
        elif p_lower.startswith(".") and len(p_lower) > 1 and not p_lower.startswith(".."):
            clean_e = p_lower.lstrip(".")
            if clean_e:
                params.extensions.add(clean_e)

        # size:>1KB, size:<10MB, size:0
        elif p_lower.startswith("size:"):
            apply_size_spec(params, part[5:])

        # cat:AI, category:Code, tag:Docs
        elif p_lower.startswith("cat:") or p_lower.startswith("category:") or p_lower.startswith("tag:"):
            colon_idx = part.find(":")
            cat_val = part[colon_idx + 1:].strip()
            if cat_val:
                params.category = cat_val

        # dir:02_Learning_Knowledge or path:AI_Models
        elif p_lower.startswith("dir:") or p_lower.startswith("path:"):
            colon_idx = part.find(":")
            dir_val = part[colon_idx + 1:].strip().strip('"').strip("'")
            if dir_val:
                # Strip common drive prefixes
                for drive_prefix in ("/Volumes/KINGSTON", "/Volumes/Kingston", "/volumes/kingston"):
                    if dir_val.startswith(drive_prefix):
                        dir_val = dir_val[len(drive_prefix):]
                        break
                # Windows drive letter e.g. D:, d:, C:, c:
                if len(dir_val) >= 2 and dir_val[1] == ":" and dir_val[0].isalpha():
                    dir_val = dir_val[2:]
                dir_val = dir_val.replace("\\", "/").strip("/")
                if dir_val:
                    params.directory = dir_val

        else:
            free_words.append(part)

    if free_words:
        raw_kw = " ".join(free_words)
        params.keyword = raw_kw

    return params


__all__ = [
    "SearchParams",
    "apply_size_spec",
    "parse_size_spec",
    "parse_search_query",
    "sanitize_fts_query",
]
