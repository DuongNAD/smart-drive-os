"""smart_drive.cli.cmd_shield - Audit and configure NTFS cache redirection to prevent exFAT drive bloat."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from smart_drive.core.exfat_shield import (
    apply_exfat_shield,
    audit_exfat_shield,
    recommend_cache_base,
    revert_exfat_shield,
)
from smart_drive.core.fsinfo import detect_filesystem
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def cmd_shield(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive shield`."""
    as_json = bool(getattr(args, "json", False))
    cache_dir: Optional[str] = getattr(args, "cache_dir", None)
    apply_mode = bool(getattr(args, "apply", False))
    revert_mode = bool(getattr(args, "revert", False))
    force = bool(getattr(args, "force", False))

    if revert_mode:
        target_dir = cache_dir
        if not target_dir:
            try:
                resolved = resolve_drive_root(getattr(args, "root", None))
                root_fs = detect_filesystem(resolved.path)
            except DriveRootNotFound:
                root_fs = detect_filesystem(".")
            target_dir = recommend_cache_base(root_fs)["path"]

        try:
            res = revert_exfat_shield(target_dir, dry_run=not apply_mode)
        except FileNotFoundError as exc:
            if as_json:
                print(json.dumps({"status": "error", "error": str(exc)}, indent=2, ensure_ascii=False))
            else:
                sys.stderr.write(f"Error: {exc}\n")
            return 1

        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        if res["status"] == "dry_run":
            print(f"[DRY RUN] Would revert {len(res['planned_restore'])} env var(s) from '{res['cache_base']}'.")
            print("Re-run with '--revert --apply' to restore registry/environment state.")
        else:
            print(f"✓ Reverted {len(res['restored_vars'])} shield env var(s) recorded at '{res['cache_base']}'.")
        return 0

    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    if apply_mode:
        try:
            res = apply_exfat_shield(root=root, cache_dir=cache_dir, dry_run=False, force=force)
        except ValueError as exc:
            if as_json:
                print(json.dumps({"status": "error", "error": str(exc)}, indent=2, ensure_ascii=False))
            else:
                sys.stderr.write(f"Error: {exc}\n")
            return 1

        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0

        print(f"\n✓ exFAT Cache Shield Applied -> {res['cache_base']} ({res['target_filesystem']['summary']})")
        for var, val in res["applied_vars"].items():
            print(f"  + {var} = {val}")
        if res["preserved_vars"]:
            print(f"  = Preserved existing safe vars: {', '.join(res['preserved_vars'])}")
        if res["skipped_risk_vars"]:
            print(f"  ! Skipped exFAT vars (pass --force to override): {', '.join(res['skipped_risk_vars'])}")
        print(f"  Manifest: {res['manifest_path']}")
        if res.get("posix_script"):
            print(f"  Source in your shell profile: source \"{res['posix_script']}\"")
        else:
            print("  Open a new terminal/IDE window for updated user environment variables to take effect.")
        return 0

    audit = audit_exfat_shield(root=root, cache_dir=cache_dir)
    if as_json:
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return 0

    rec = audit["recommended_cache_base"]
    print(f"\nSmartDrive-OS exFAT Bloat Prevention Shield: {root}")
    print(f"Volume: {audit['filesystem']['summary']} | Large-cluster sensitive: {audit['is_large_cluster']}")
    print(f"Recommended NTFS/local cache host: {audit['target_cache_base']} ({rec['reason']})")
    print("-" * 92)
    print(f"{'Variable':<18} {'Status':<16} {'Current / Target'}")
    print("-" * 92)
    for item in audit["variables"]:
        cur = item["current_value"] or f"(unset -> would set {item['recommended_value']})"
        print(f"{item['var']:<18} {item['status']:<16} {cur}")

    if audit["advisory_risks"]:
        print("\nAdvisory Warnings:")
        for adv in audit["advisory_risks"]:
            print(f"  ! {adv['message']}")

    print("\nNotes:")
    for note in audit["notes"]:
        print(f"  - {note}")

    if audit["needs_attention"]:
        print(f"\n[DRY RUN] Run 'smart-drive shield --root \"{root}\" --apply' to configure persistent NTFS cache redirection.")
    else:
        print("\n✓ Shield status: no unprotected cache variables on large-cluster exFAT.")
    return 0


__all__ = ["cmd_shield"]
