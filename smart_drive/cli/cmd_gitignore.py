"""smart_drive.cli.cmd_gitignore - `smart-drive gitignore`: audit and apply managed .gitignore blocks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from smart_drive.core.gitignore import (
    GitignoreApplyResult,
    GitignoreAuditResult,
    apply_repo,
    audit_all,
    audit_repo,
)
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def format_audit_report(results: List[GitignoreAuditResult], target_desc: str) -> str:
    """Formats a human-readable report for gitignore audit."""
    lines: List[str] = [
        "SmartDrive Gitignore Governance Audit",
        "=" * 70,
        f"Scope / Target: {target_desc}",
        f"Repositories Audited: {len(results)}",
    ]

    compliant = sum(
        1 for r in results
        if not r.missing_patterns and not r.tracked_junk_count and not r.personal_data_at_risk
    )
    with_junk = sum(1 for r in results if r.tracked_junk_count > 0)
    with_personal = sum(1 for r in results if r.personal_data_at_risk)

    lines.append(f"Summary: {compliant} compliant, {with_junk} with junk, {with_personal} with personal data at risk")
    lines.append("-" * 70)

    if not results:
        lines.append("No git repositories found.")
        return "\n".join(lines)

    for r in results:
        lines.append(f"\nRepository: {r.repo}")
        gi_status = "Present" if r.has_gitignore else "Missing"
        block_status = "Yes" if r.has_managed_block else "No"
        lines.append(f"  .gitignore: {gi_status} (SmartDrive-managed block: {block_status})")
        lines.append(f"  Detected Stacks: {', '.join(r.stacks) if r.stacks else 'generic (base only)'}")

        if r.missing_patterns:
            preview = ", ".join(r.missing_patterns[:5])
            more = f" (+{len(r.missing_patterns) - 5} more)" if len(r.missing_patterns) > 5 else ""
            lines.append(f"  Missing Recommended Patterns: {len(r.missing_patterns)} missing ({preview}{more})")
        else:
            lines.append("  Missing Recommended Patterns: none")

        if r.tracked_junk_count > 0:
            by_pat = ", ".join(f"{k}: {v}" for k, v in sorted(r.tracked_junk_by_pattern.items())[:5])
            lines.append(f"  Tracked Junk Files: {r.tracked_junk_count} files ({by_pat})")
            lines.append("    Sample tracked junk:")
            for jf in r.tracked_junk_files[:5]:
                lines.append(f"      - {jf}")
            if len(r.tracked_junk_files) > 5:
                lines.append(f"      ... and {r.tracked_junk_count - 5} more")
        else:
            lines.append("  Tracked Junk Files: 0")

        if r.tracked_personal_data:
            lines.append(f"  Tracked Personal Data: {len(r.tracked_personal_data)} files")
            for pdf in r.tracked_personal_data[:5]:
                lines.append(f"      - {pdf}")
            if r.is_possibly_public:
                lines.append("    [!] WARNING: Personal learning data tracked in repository with public/unknown remote!")
        else:
            lines.append("  Tracked Personal Data: 0")

        remote_desc = r.remote if r.remote else "(none configured)"
        pub_desc = "possibly public" if r.is_possibly_public else "private/local"
        lines.append(f"  Remote: {remote_desc} [{pub_desc}]")

        if not r.missing_patterns and not r.tracked_junk_count and not r.personal_data_at_risk:
            lines.append("  Status: [OK] Compliant")
        else:
            lines.append(f"  Status: [ACTION REQUIRED] Run: smart-drive gitignore apply \"{r.repo}\" --yes --untrack")

    lines.append("\n" + "=" * 70)
    lines.append("Note: untrack removes files from the git index only. Working files are never deleted.")
    return "\n".join(lines)


def format_apply_report(res: GitignoreApplyResult) -> str:
    """Formats a human-readable report for gitignore apply."""
    mode = "DRY-RUN Simulation (No changes written)" if res.dry_run else "EXECUTION (Changes applied)"
    lines: List[str] = [
        f"SmartDrive Gitignore Apply ({mode})",
        "=" * 70,
        f"Repository:      {res.repo}",
        f"Detected Stacks: {', '.join(res.stacks) if res.stacks else 'generic (base only)'}",
        f"Target File:     {res.gitignore_path} ({'created' if res.created else 'updated'})",
        f"Managed Patterns: {res.managed_patterns_count}",
        "-" * 70,
    ]

    if res.diff:
        lines.append("Proposed .gitignore diff:")
        lines.append(res.diff.rstrip())
        lines.append("-" * 70)
    else:
        lines.append(".gitignore already contains up-to-date managed block (no diff).")

    if res.untracked_files:
        verb = "Would untrack" if res.dry_run else "Untracked"
        lines.append(f"{verb} {len(res.untracked_files)} file(s) from git index (working files remain on disk):")
        for f in res.untracked_files[:15]:
            lines.append(f"  - {f}")
        if len(res.untracked_files) > 15:
            lines.append(f"  ... and {len(res.untracked_files) - 15} more")
    else:
        lines.append("No matching tracked files to untrack.")

    if res.dry_run:
        lines.append("-" * 70)
        lines.append("Dry run complete. To execute changes, run again with --yes.")

    return "\n".join(lines)


def cmd_gitignore(args: argparse.Namespace) -> int:
    """Handles the `gitignore` subcommand."""
    action = getattr(args, "action", None)
    if not action:
        # Default action when none specified is audit
        action = "audit"

    is_json = getattr(args, "json", False)

    if action == "audit":
        explicit_repo = getattr(args, "repo", None)
        explicit_root = getattr(args, "root", None)

        if explicit_repo and os.path.exists(os.path.join(explicit_repo, ".git")):
            # Audit single repository
            results = [audit_repo(explicit_repo)]
            desc = explicit_repo
        elif explicit_repo and os.path.isdir(explicit_repo):
            # Treat as root
            results = audit_all(explicit_repo)
            desc = explicit_repo
        else:
            try:
                resolved = resolve_drive_root(explicit_root)
                drive_root = resolved.path
            except DriveRootNotFound as e:
                if explicit_root:
                    sys.stderr.write(f"{e}\n")
                    return 2
                drive_root = os.getcwd()
            results = audit_all(drive_root)
            desc = drive_root

        if is_json:
            print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        else:
            print(format_audit_report(results, desc))
        return 0

    elif action == "apply":
        target_repo = getattr(args, "repo", None)
        if not target_repo:
            sys.stderr.write("Error: repository path is required for `gitignore apply`.\n")
            return 2

        yes = getattr(args, "yes", False)
        untrack = getattr(args, "untrack", False)

        try:
            res = apply_repo(target_repo, dry_run=not yes, untrack=untrack)
        except Exception as e:
            if is_json:
                print(json.dumps({"success": False, "error": str(e)}, indent=2, ensure_ascii=False))
            else:
                sys.stderr.write(f"Error: {e}\n")
            return 1

        if is_json:
            print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(format_apply_report(res))
        return 0

    else:
        sys.stderr.write(f"Unknown gitignore action: '{action}'. Available: audit, apply.\n")
        return 2
