"""smart_drive.cli.cmd_doctor - Read-only, offline environment report for bug reports."""

from __future__ import annotations

import argparse
import json
import sys

from smart_drive.core.doctor import Redactor, collect_report, render_markdown, render_text


def _emit(text: str) -> None:
    """Prints the report; a console that cannot show a character gets `?` for it instead of an error."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


def cmd_doctor(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive doctor`.

    Prints the report as text, Markdown (--report) or JSON (--json, which wins over --report). The exit code is 0 once a
    report was produced: a drive root that cannot be found is part of the report, not a failure.
    """
    try:
        report = collect_report(getattr(args, "root", None))
    except Exception as err:  # noqa: BLE001 - every section is guarded, so this is a bug in the report itself
        try:
            detail = Redactor.from_environment().text(str(err))
        except Exception:  # noqa: BLE001
            detail = "details withheld"
        sys.stderr.write(f"smart-drive doctor could not build its report ({type(err).__name__}: {detail}).\n")
        return 1

    if getattr(args, "json", False):
        _emit(json.dumps(report, indent=2, ensure_ascii=False))
    elif getattr(args, "report", False):
        _emit(render_markdown(report))
    else:
        _emit(render_text(report))
    return 0


__all__ = ["cmd_doctor"]
