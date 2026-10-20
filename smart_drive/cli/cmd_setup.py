"""smart_drive.cli.cmd_setup - `setup`: one command that sets up the MCP ecosystem.

Reading (`--check`, `--dry-run`) never asks anything. Applying plans first, then asks the user which
servers to install; a coding agent that cannot ask installs nothing and gets exit code 4 and the list.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from smart_drive.mcp import bootstrap
from smart_drive.mcp.ecosystem import CONTROL_PLANE, member_names

#: How often an unclear answer is asked again before it counts as "none".
MAX_ANSWER_TRIES = 3
PROMPT = "Install them? [a]ll / [n]one / numbers or names, e.g. 1,3 or polaris,vega [n]: "
NOTHING_INSTALLED = "Nothing was installed. Run `smart-drive setup` again any time."


def _split(value: Optional[str]) -> Optional[List[str]]:
    """``"a, b"`` -> ``["a", "b"]``; nothing (or only separators) -> None."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return parts or None


def _is_interactive() -> bool:
    """True when a person can answer: both stdin and stdout are a terminal. Tests patch this."""
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _ask(prompt: str) -> str:
    """Reads one answer; end of input or Ctrl+C counts as an empty answer (none). Tests patch this."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _parse_answer(answer: str, names: Sequence[str]) -> Tuple[List[str], str]:
    """(chosen names in list order, error). An empty answer or ``n`` is none, ``a`` is all."""
    text = answer.strip().lower()
    if text in ("", "n", "no", "none"):
        return [], ""
    if text in ("a", "all", "y", "yes"):
        return list(names), ""
    chosen: List[str] = []
    unknown: List[str] = []
    for token in text.replace(",", " ").split():
        if token.isdecimal() and 1 <= int(token) <= len(names):
            chosen.append(names[int(token) - 1])
        elif token in names:
            chosen.append(token)
        else:
            unknown.append(token)
    if unknown:
        return [], f"Not understood: {', '.join(unknown)}. Answer a, n, numbers 1-{len(names)} or names."
    return [name for name in names if name in chosen], ""


def _ask_consent(items: Sequence[Mapping[str, Any]]) -> List[str]:
    """Shows the numbered list and returns the approved names; empty means none."""
    names = [item["name"] for item in items]
    print("SmartDrive can set up these MCP servers. Nothing is downloaded or registered until you answer:")
    print()
    print("\n".join(bootstrap.format_consent(items)))
    print()
    chosen: List[str] = []
    for _attempt in range(MAX_ANSWER_TRIES):
        chosen, error = _parse_answer(_ask(PROMPT), names)
        if not error:
            break
        chosen = []
        print(error)
    if chosen and CONTROL_PLANE in names and CONTROL_PLANE not in chosen:
        chosen = [name for name in names if name in chosen or name == CONTROL_PLANE]
        print(f"{CONTROL_PLANE} is added too: every other server is registered through it.")
    return chosen


def _only_after_answer(
    only: Optional[List[str]], items: Sequence[Mapping[str, Any]], approved: Sequence[str]
) -> List[str]:
    """``--only`` for the real run: the approved servers plus the wanted ones that needed no approval."""
    asked = {item["name"] for item in items}
    keep = {name for name in (only or member_names()) if name not in asked} | set(approved)
    return [name for name in member_names() if name in keep]


def _print(report: Mapping[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(bootstrap.format_report(report))


def _run(options: bootstrap.Options, as_json: bool) -> int:
    """Runs setup as given (check, dry-run or apply) and prints its report."""

    def progress(message: str) -> None:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()

    try:
        report = bootstrap.run_setup(options, log=progress)
    except ValueError as err:
        sys.stderr.write(f"setup: {err}\n")
        return 2

    _print(report, as_json)
    return bootstrap.exit_code(report)


def cmd_setup(args: argparse.Namespace) -> int:
    """Handles the `setup` subcommand."""
    dest = getattr(args, "dest", None)
    options = bootstrap.Options(
        dest=Path(dest).expanduser() if dest else None,
        agents=_split(getattr(args, "agents", None)),
        only=_split(getattr(args, "only", None)),
        dry_run=bool(getattr(args, "dry_run", False)),
        check=bool(getattr(args, "check", False)),
        no_clone=bool(getattr(args, "no_clone", False)),
        no_health=bool(getattr(args, "no_health", False)),
        repair=bool(getattr(args, "repair", False)),
    )
    as_json = bool(getattr(args, "json", False))
    if options.check or options.dry_run:
        return _run(options, as_json)

    # Applying: plan first, so nothing is written before the user has said what they want.
    try:
        plan = bootstrap.run_setup(dataclasses.replace(options, dry_run=True), log=None)
    except ValueError as err:
        sys.stderr.write(f"setup: {err}\n")
        return 2
    plan["next_steps"] = [step for step in plan["next_steps"] if step != bootstrap.APPLY_HINT]
    if plan["blocked"]:
        _print(plan, as_json)
        return bootstrap.exit_code(plan)

    items = bootstrap.consent_items(plan, repair=options.repair, no_clone=options.no_clone)
    if items and not getattr(args, "yes", False):
        if as_json or not _is_interactive():
            report = bootstrap.consent_report(plan, items)
            _print(report, as_json)
            return bootstrap.exit_code(report)
        approved = _ask_consent(items)
        if not approved:
            print(NOTHING_INSTALLED)
            return 0
        options = dataclasses.replace(options, only=_only_after_answer(options.only, items, approved))
    return _run(options, as_json)
