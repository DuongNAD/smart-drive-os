"""smart_drive.cli.cmd_status - Inspect drive mount, geometry, shields, and taxonomies."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from smart_drive.core.config import CLUSTER_SIZE_BYTES, TAXONOMY_ROOT_DIRS, PROTECTED_CORE_TAXONOMIES
from smart_drive.core.root import DriveRootNotFound, resolve_drive_root
from smart_drive.core.sentinel import get_default_db_path


def cmd_status(args: argparse.Namespace) -> int:
    """Subcommand handler for `smart-drive status`."""
    try:
        resolved = resolve_drive_root(getattr(args, "root", None))
        root = resolved.path
    except DriveRootNotFound as e:
        sys.stderr.write(f"{e}\n")
        return 2
    markers = {
        ".metadata_never_index": os.path.exists(os.path.join(root, ".metadata_never_index")),
        ".fseventsd/no_log": os.path.exists(os.path.join(root, ".fseventsd", "no_log")),
    }

    taxonomies = [
        "01_AI_Models",
        "02_Learning_Knowledge",
        "03_Development_Projects",
        "04_System_Workspaces",
        "05_Dev_Toolbox",
        "06_Archives_Storage",
    ]
    tax_status = {}
    for t in taxonomies:
        # Check standard taxonomy or legacy alias
        exists = os.path.isdir(os.path.join(root, t))
        if not exists and t == "03_Development_Projects":
            exists = os.path.isdir(os.path.join(root, "03_Personal_Documents"))
        if not exists and t == "04_System_Workspaces":
            exists = os.path.isdir(os.path.join(root, "04_Creative_Assets"))
        tax_status[t] = exists

    db_path = get_default_db_path(root)
    db_exists = os.path.exists(db_path)
    db_size = os.path.getsize(db_path) if db_exists else 0

    if getattr(args, "json", False):
        status_data = {
            "status": "ready",
            "root": root,
            "cluster_size_bytes": CLUSTER_SIZE_BYTES,
            "cluster_size_kb": CLUSTER_SIZE_BYTES // 1024,
            "database": {
                "path": db_path,
                "exists": db_exists,
                "size_bytes": db_size,
            },
            "anti_indexing_markers": markers,
            "taxonomies": tax_status,
        }
        print(json.dumps(status_data, indent=2, ensure_ascii=False))
        return 0

    print(f"SmartDrive-OS Status: Ready (Root: {root})")
    print(f"Cluster Size: {CLUSTER_SIZE_BYTES:,} bytes ({CLUSTER_SIZE_BYTES // 1024} KB)")
    print(f"Search Database: {'Ready (' + str(db_size // 1024) + ' KB)' if db_exists else 'Not found'}")
    print("Anti-indexing markers:")
    for m, ok in markers.items():
        print(f"  {'✓' if ok else '✗'} {m}")
    print("Taxonomies:")
    for t, ok in tax_status.items():
        print(f"  {'✓' if ok else '✗'} {t}")
    return 0


__all__ = ["cmd_status"]
