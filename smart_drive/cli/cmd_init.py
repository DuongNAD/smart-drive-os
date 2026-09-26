"""smart_drive.cli.cmd_init - 1-Touch SSD Initialization Command."""

from __future__ import annotations

import argparse
import json
import os
import sys

from smart_drive.core.initializer import DriveInitializer, PROFILES


def detect_default_root() -> str:
    """Discovers SSD root dynamically or falls back to project/current dir."""
    if os.path.exists("/Volumes/KINGSTON"):
        return "/Volumes/KINGSTON"
    if os.path.exists("D:\\"):
        return "D:\\"
    return os.getcwd()


def cmd_init(args: argparse.Namespace) -> int:
    """Handles the `init` subcommand."""
    target_path = getattr(args, "path", None) or getattr(args, "root", None) or detect_default_root()
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
