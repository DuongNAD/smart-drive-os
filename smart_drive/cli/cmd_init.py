"""smart_drive.cli.cmd_init - 1-Touch SSD Initialization Command."""

from __future__ import annotations

import argparse
import json
import os
import sys

from smart_drive.core.initializer import DriveInitializer, PROFILES
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root


def normalize_drive_path(path_spec: str) -> str:
    """Normalizes drive letters and root paths to canonical form (e.g. 'D:' -> 'D:\\')."""
    s = path_spec.strip()
    if len(s) == 2 and s[1] == ":" and s[0].isalpha():
        return f"{s[0].upper()}:\\"
    if len(s) == 1 and s.isalpha():
        return f"{s.upper()}:\\"
    if len(s) == 3 and s[1] == ":" and s[0].isalpha() and s[2] in ("\\", "/"):
        return f"{s[0].upper()}:\\"
    return s


def cmd_init(args: argparse.Namespace) -> int:
    """Handles the `init` subcommand."""
    raw_path = getattr(args, "path", None)
    if raw_path:
        target_path = normalize_drive_path(raw_path)
    else:
        try:
            resolved = resolve_drive_root(getattr(args, "root", None))
            target_path = resolved.path
        except DriveRootNotFound as e:
            sys.stderr.write(f"{e}\n")
            return 2
    profile = getattr(args, "profile", "general-workspace")
    force = getattr(args, "force", False)

    initializer = DriveInitializer(target_path)
    result = initializer.initialize(target_path=target_path, profile=profile, force=force)

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(f"✓ Initialized SmartDrive workspace at: {result['root']}")
    print(f"  Profile: {result['profile']}")
    print(f"  Created Taxonomies: {len(result['created_dirs'])} dirs")
    print(f"  Anti-Indexing Shields: {'Active' if result['shields'].get('all_healthy') else 'Partial'}")
    print(f"  Manifests Created: {', '.join(result['manifests']) if result['manifests'] else 'Existing preserved'}")
    print(f"  FTS5 Search DB: {'Ready' if result['database_initialized'] else 'Error'}")

    return 0
