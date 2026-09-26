"""smart_drive.cli.cmd_audit - Storage breakdown & 512KB cluster slack audit."""

from __future__ import annotations

import argparse
import os

from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.exfat_compat import detect_drive_root


def cmd_audit(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive audit`."""
    root = os.path.abspath(getattr(args, "root", None) or detect_drive_root())
    auditor = StorageAuditor(root)
    report = auditor.run_audit()

    if getattr(args, "json", False):
        out_str = report.to_json()
    elif getattr(args, "markdown", False):
        out_str = report.to_markdown()
    else:
        out_str = report.to_ascii_table()

    export_path = getattr(args, "export", None)
    if export_path:
        out_path = os.path.abspath(export_path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out_str)
        print(f"Audit report saved to: {out_path}")
    else:
        print(out_str)

    return 0


__all__ = ["cmd_audit"]
