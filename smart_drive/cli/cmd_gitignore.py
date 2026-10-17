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


def _format_repo_details(r: GitignoreAuditResult, show_untrack_info: bool = False) -> List[str]:
    lines: List[str] = [f"\nRepository: {r.repo} [owner: {r.owner_class}, visibility: {r.visibility}]"]
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

    if r.omitted_patterns:
        omitted_desc = ", ".join(f"{p} ({reason})" for p, reason in sorted(r.omitted_patterns.items()))
        lines.append(f"  Omitted Recommended Patterns: {omitted_desc}")

    if r.intentional_files:
        preview_int = ", ".join(r.intentional_files[:5])
        more_i = f" (+{len(r.intentional_files) - 5} more)" if len(r.intentional_files) > 5 else ""
        lines.append(f"  Intentional Build Outputs: {len(r.intentional_files)} file(s) ({preview_int}{more_i})")

    # High-Severity Tracked Secrets
    if r.tracked_secrets:
        if r.visibility == "public":
            lines.append("  [!] HIGH SEVERITY: Tracked Secrets Detected in public repository!")
        elif r.visibility == "missing":
            lines.append("  [!] HIGH SEVERITY: Tracked Secrets Detected!")
        elif r.is_possibly_public:
            lines.append("  [!] HIGH SEVERITY: Tracked Secrets Detected in possibly public repository!")
        else:
            lines.append("  [!] HIGH SEVERITY: Tracked Secrets Detected!")
        lines.append("    Tracked secret files:")
        for s in r.tracked_secrets:
            vnames = s.get("var_names", [])
            if vnames:
                preview_vars = ", ".join(vnames[:5])
                more_v = f" (+{len(vnames) - 5} more)" if len(vnames) > 5 else ""
                lines.append(f"      - {s['path']} (variables: {preview_vars}{more_v})")
            else:
                lines.append(f"      - {s['path']}")
        if r.visibility == "public":
            lines.append("    [!] Advice: Rotate exposed secrets immediately (repository is public)!")
        elif r.visibility == "missing":
            lines.append("    [!] Advice: Rotate secrets if this repository is or ever was public.")
        elif r.is_possibly_public:
            lines.append("    [!] Advice: Rotate secrets if this repository is or ever was public!")
        else:
            lines.append("    [!] Advice: Rotate secrets if this repository is or ever was public.")

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
        if r.personal_data_at_risk:
            if r.visibility == "public":
                lines.append("    [!] WARNING: Personal learning data tracked in repository with public remote!")
            else:
                lines.append("    [!] WARNING: Personal learning data tracked in repository with possibly public remote!")
    else:
        lines.append("  Tracked Personal Data: 0")

    remote_desc = r.remote if r.remote else "(none configured)"
    if r.visibility in ("public", "private", "internal"):
        pub_desc = f"{r.visibility} (verified via gh)"
    elif r.visibility == "missing":
        pub_desc = "remote not found on GitHub"
    elif r.is_possibly_public:
        pub_desc = "possibly public"
    else:
        pub_desc = "private/local"
    lines.append(f"  Remote: {remote_desc} [{pub_desc}]")

    files_to_untrack = r.tracked_junk_count + len(r.tracked_secrets)
    if show_untrack_info and files_to_untrack > 0:
        lines.append(f"  Files to untrack: {files_to_untrack} file(s) would be untracked with --untrack")

    if r.visibility == "missing":
        lines.append("  Status: Remote not found on GitHub (deleted, renamed or no access)")
    elif not r.missing_patterns and not r.tracked_junk_count and not r.personal_data_at_risk and not r.tracked_secrets:
        lines.append("  Status: [OK] Compliant")
    elif files_to_untrack > 0:
        lines.append(f"  Status: [ACTION REQUIRED] Run: smart-drive gitignore apply \"{r.repo}\" --yes --untrack (would untrack {files_to_untrack} file(s))")
    else:
        lines.append(f"  Status: [ACTION REQUIRED] Run: smart-drive gitignore apply \"{r.repo}\" --yes")

    return lines


def format_audit_report(
    results: List[GitignoreAuditResult],
    target_desc: str,
    show_all: bool = False,
) -> str:
    """Formats a human-readable report for gitignore audit."""
    lines: List[str] = [
        "SmartDrive Gitignore Governance Audit",
        "=" * 70,
        f"Scope / Target: {target_desc}",
        f"Repositories Audited: {len(results)}",
    ]

    own_count = sum(1 for r in results if r.owner_class == "own")
    local_count = sum(1 for r in results if r.owner_class == "local")
    tp_count = sum(1 for r in results if r.owner_class == "third-party")
    remote_missing_count = sum(1 for r in results if r.visibility == "missing")

    compliant = sum(
        1 for r in results
        if not r.missing_patterns and not r.tracked_junk_count and not r.personal_data_at_risk and not r.tracked_secrets and r.visibility != "missing"
    )
    with_junk = sum(1 for r in results if r.tracked_junk_count > 0)
    with_personal = sum(1 for r in results if r.personal_data_at_risk)
    with_secrets = sum(1 for r in results if r.tracked_secrets)

    lines.append(
        f"Summary: {compliant} compliant, {with_junk} with junk, {with_secrets} with secrets, "
        f"{with_personal} with personal data at risk, {remote_missing_count} remote missing"
    )
    lines.append(f"Categories: {own_count} own, {local_count} local, {tp_count} third-party")

    offenders = sorted(
        [r for r in results if r.tracked_junk_count > 0 or r.tracked_secrets or r.personal_data_at_risk],
        key=lambda r: (len(r.tracked_secrets), r.tracked_junk_count, len(r.tracked_personal_data)),
        reverse=True,
    )
    if offenders:
        offender_parts = []
        for o in offenders[:5]:
            name = os.path.basename(o.repo)
            parts = []
            if o.tracked_secrets:
                parts.append(f"{len(o.tracked_secrets)} secrets")
            if o.tracked_junk_count:
                parts.append(f"{o.tracked_junk_count} junk")
            if o.personal_data_at_risk:
                parts.append(f"{len(o.tracked_personal_data)} personal")
            offender_parts.append(f"{name} ({', '.join(parts)})")
        lines.append(f"Top Offenders: {'; '.join(offender_parts)}")
    else:
        lines.append("Top Offenders: none")

    lines.append("-" * 70)

    if not results:
        lines.append("No git repositories found.")
        return "\n".join(lines)

    # When auditing a single repo directly (and not third-party unless show_all)
    if len(results) == 1 and (results[0].owner_class != "third-party" or show_all):
        r = results[0]
        is_action_needed = (r.tracked_junk_count > 0 or bool(r.tracked_secrets))
        lines.extend(_format_repo_details(r, show_untrack_info=is_action_needed))
        lines.append("\n" + "=" * 70)
        lines.append("Note: untrack removes files from the git index only. Working files are never deleted.")
        return "\n".join(lines)

    own_repos = [r for r in results if r.owner_class == "own"]
    local_repos = [r for r in results if r.owner_class == "local"]
    third_party_repos = [r for r in results if r.owner_class == "third-party"]

    # Partition own repos: action needed, remote missing, only missing patterns, compliant
    own_action_needed = [r for r in own_repos if r.tracked_junk_count > 0 or r.tracked_secrets]
    own_remote_missing = [r for r in own_repos if r.visibility == "missing" and r not in own_action_needed]
    own_only_missing = [
        r for r in own_repos
        if r.missing_patterns and r not in own_action_needed and r not in own_remote_missing
    ]
    own_compliant = [
        r for r in own_repos
        if r not in own_action_needed and r not in own_remote_missing and r not in own_only_missing
    ]

    if own_action_needed:
        lines.append("\n" + "=" * 70)
        lines.append(f"Own Repositories - Action Needed ({len(own_action_needed)} repos with tracked junk or secrets):")
        for r in own_action_needed:
            lines.extend(_format_repo_details(r, show_untrack_info=True))

    if own_only_missing:
        lines.append("\n" + "=" * 70)
        lines.append(f"Own Repositories - Only Missing Patterns ({len(own_only_missing)} repos):")
        for r in own_only_missing:
            lines.extend(_format_repo_details(r, show_untrack_info=False))

    if own_remote_missing:
        lines.append("\n" + "=" * 70)
        lines.append(f"Own Repositories - Remote Missing ({len(own_remote_missing)} repos):")
        for r in own_remote_missing:
            lines.extend(_format_repo_details(r, show_untrack_info=False))

    if own_compliant:
        lines.append("\n" + "=" * 70)
        lines.append(f"Own Repositories - Compliant ({len(own_compliant)} repos):")
        for r in own_compliant:
            lines.append(f"  - {r.repo} [compliant]")

    # Compact listing for local repos
    if local_repos:
        lines.append("\n" + "=" * 70)
        lines.append(f"Local Repositories ({len(local_repos)} repos):")
        if show_all:
            for r in local_repos:
                lines.extend(_format_repo_details(r, show_untrack_info=True))
        else:
            for r in local_repos:
                remote_desc = r.remote if r.remote else "(local filesystem repository)"
                lines.append(f"  - {r.repo} [remote: {remote_desc}]")
                if r.tracked_secrets:
                    sec_list = ", ".join(s["path"] for s in r.tracked_secrets)
                    lines.append(f"    [!] HIGH SEVERITY: Tracked secrets: {sec_list}")
                    lines.append(f"    Run: smart-drive gitignore apply \"{r.repo}\" --yes --untrack")
                elif r.tracked_junk_count > 0:
                    lines.append(f"    Tracked junk: {r.tracked_junk_count} file(s) would be untracked with --untrack")

    # Compact listing for third-party repos
    if third_party_repos:
        lines.append("\n" + "=" * 70)
        lines.append(f"Third-Party Repositories ({len(third_party_repos)} repos, upstream-managed - junk checks skipped):")
        if show_all:
            for r in third_party_repos:
                lines.extend(
                    _format_repo_details(
                        r,
                        show_untrack_info=bool(r.tracked_junk_count or r.tracked_secrets),
                    )
                )
        else:
            for r in third_party_repos:
                remote_desc = r.remote if r.remote else "(none configured)"
                lines.append(f"  - {r.repo} [remote: {remote_desc}]")
                if r.tracked_secrets:
                    sec_list = ", ".join(s["path"] for s in r.tracked_secrets)
                    lines.append(f"    [!] HIGH SEVERITY: Tracked secrets: {sec_list}")
                    lines.append(f"    Run: smart-drive gitignore apply \"{r.repo}\" --yes --untrack")

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

    if res.omitted_patterns:
        omitted_desc = ", ".join(f"{p} ({reason})" for p, reason in sorted(res.omitted_patterns.items()))
        lines.append(f"Omitted Patterns: {omitted_desc}")

    if res.intentional_files:
        lines.append(f"Intentional Build Outputs Preserved: {len(res.intentional_files)} file(s)")

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
    keep_patterns = getattr(args, "keep", None) or []

    if action == "audit":
        explicit_repo = getattr(args, "repo", None)
        explicit_root = getattr(args, "root", None)
        show_all = getattr(args, "all", False)
        explicit_owner = getattr(args, "owner", None)

        if explicit_repo and os.path.exists(os.path.join(explicit_repo, ".git")):
            # Audit single repository
            results = [
                audit_repo(
                    explicit_repo,
                    configured_owner=explicit_owner,
                    include_third_party_junk=show_all,
                    keep_patterns=keep_patterns,
                )
            ]
            desc = explicit_repo
        elif explicit_repo and os.path.isdir(explicit_repo):
            # Treat as root
            results = audit_all(
                explicit_repo,
                configured_owner=explicit_owner,
                include_third_party_junk=show_all,
                keep_patterns=keep_patterns,
            )
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
            results = audit_all(
                drive_root,
                configured_owner=explicit_owner,
                include_third_party_junk=show_all,
                keep_patterns=keep_patterns,
            )
            desc = drive_root

        if is_json:
            print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
        else:
            print(format_audit_report(results, desc, show_all=show_all))
        return 0

    elif action == "apply":
        target_repo = getattr(args, "repo", None)
        if not target_repo:
            sys.stderr.write("Error: repository path is required for `gitignore apply`.\n")
            return 2

        yes = getattr(args, "yes", False)
        untrack = getattr(args, "untrack", False)

        try:
            res = apply_repo(target_repo, dry_run=not yes, untrack=untrack, keep_patterns=keep_patterns)
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
