"""smart_drive.cli.cmd_audit - Storage breakdown & cluster slack audit at the volume's real cluster size."""

from __future__ import annotations

import argparse
import os
import sys

from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.fsinfo import detect_filesystem, slack_cluster
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def cmd_audit(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive audit`."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2

    cluster_size, cluster_size_source = slack_cluster(detect_filesystem(root))
    auditor = StorageAuditor(root, cluster_size=cluster_size)

    html_arg = getattr(args, "html", None)
    report = auditor.run_audit()
    report["cluster_size_source"] = cluster_size_source

    if html_arg:
        if isinstance(html_arg, str) and html_arg.strip():
            html_out_path = os.path.abspath(html_arg)
        elif getattr(args, "export", None):
            html_out_path = os.path.abspath(args.export)
        else:
            html_out_path = os.path.join(root, ".smart_drive", "reports", "audit.html")
        report.export_html(html_out_path)
        if getattr(args, "json", False):
            report["html_report_path"] = html_out_path
            print(report.to_json())
        else:
            print(f"Audit report saved to: {html_out_path}")
        return 0

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
