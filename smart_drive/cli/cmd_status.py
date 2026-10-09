"""smart_drive.cli.cmd_status - Inspect drive mount, geometry, shields, and taxonomies."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from smart_drive.core.config import (
    CLUSTER_SIZE_BYTES,
    LEGACY_TAXONOMY_ALIASES,
    PROTECTED_CORE_TAXONOMIES,
    TAXONOMY_ROOT_DIRS,
)
from smart_drive.core.fsinfo import detect_filesystem, slack_model_note
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

    legacy_of = {current: legacy for legacy, current in LEGACY_TAXONOMY_ALIASES.items()}  # current -> old name
    tax_status = {}
    for t in TAXONOMY_ROOT_DIRS:
        # Check standard taxonomy or its legacy name
        exists = os.path.isdir(os.path.join(root, t))
        if not exists and t in legacy_of:
            exists = any(
                entry.lower() == legacy_of[t] and os.path.isdir(os.path.join(root, entry))
                for entry in os.listdir(root)
            )
        tax_status[t] = exists

    db_path = get_default_db_path(root)
    db_exists = os.path.exists(db_path)
    db_size = os.path.getsize(db_path) if db_exists else 0

    fs_info = detect_filesystem(root)
    fs_note = slack_model_note(fs_info, CLUSTER_SIZE_BYTES)

    if getattr(args, "json", False):
        status_data = {
            "status": "ready",
            "root": root,
            "cluster_size_bytes": CLUSTER_SIZE_BYTES,
            "cluster_size_kb": CLUSTER_SIZE_BYTES // 1024,
            "filesystem": fs_info.to_dict(),
            "slack_model_note": fs_note,
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
    print(f"Filesystem: {fs_info.summary()}")
    if fs_note:
        print(f"Note: {fs_note}")
    print(f"Search Database: {'Ready (' + str(db_size // 1024) + ' KB)' if db_exists else 'Not found'}")
    print("Anti-indexing markers:")
    for m, ok in markers.items():
        print(f"  {'✓' if ok else '✗'} {m}")
    print("Taxonomies:")
    for t, ok in tax_status.items():
        print(f"  {'✓' if ok else '✗'} {t}")
    return 0


__all__ = ["cmd_status"]
