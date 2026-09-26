"""smart_drive.cli.main - Unified CLI Dispatcher for SmartDrive-OS.

Autonomous drive management and high-speed search suite for Kingston XS2000 SSD.
Provides subcommands:
- init: Initialize workspace with preset profile, taxonomies, shields, and manifests.
- status: Inspect SSD mount, geometry, shields, and taxonomies.
- audit: Storage breakdown and 512KB cluster slack metrics.
- clean: Detect and safely purge system junk (mandatory dry-run safeguard).
- search: Sub-10ms multi-criteria search and query parsing.
- organize: Autonomous drive auto-zoning, cleaning & rebalancing.
- sentinel: 1-touch SSD self-healing and Git repository status (alias: agent-check).
- mcp: Zero-dependency JSON-RPC 2.0 stdio MCP server.
- mcp-config: Multi-IDE MCP configuration registrar.
- dup: 3-phase SHA-256 duplicate file detection.
- index: Full SQLite FTS5 search index creation.
- update: Fast incremental search index synchronization.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from typing import List, Optional

# Configure UTF-8 encoding on Windows consoles
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from smart_drive.cli.cmd_audit import cmd_audit
from smart_drive.cli.cmd_clean import cmd_clean
from smart_drive.cli.cmd_init import cmd_init
from smart_drive.cli.cmd_mcp import cmd_mcp
from smart_drive.cli.cmd_mcp_config import cmd_mcp_config
from smart_drive.cli.cmd_organize import cmd_organize
from smart_drive.cli.cmd_search import cmd_search
from smart_drive.cli.cmd_sentinel import cmd_sentinel
from smart_drive.cli.cmd_status import cmd_status
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.mcp.proxy import SmartDriveProxy


def detect_default_root() -> str:
    """Discovers SSD root dynamically or falls back to project/current dir."""
    mount = SmartDriveProxy.detect_mount_point()
    if mount:
        return str(mount)
    if os.path.exists("/Volumes/KINGSTON"):
        return "/Volumes/KINGSTON"
    if os.path.exists("D:\\"):
        return "D:\\"
    return os.getcwd()


def get_default_db_path(root: str) -> str:
    """Default location for the SQLite search database."""
    new_path = os.path.join(root, ".smart_drive", "index.db")
    if os.path.exists(new_path):
        return new_path
    legacy_path = os.path.join(root, ".smart_drive_manager", "index.db")
    if os.path.exists(legacy_path):
        return legacy_path
    return new_path


def cmd_dup(args: argparse.Namespace) -> int:
    """Handles the `dup` subcommand."""
    root = os.path.abspath(getattr(args, "root", None) or detect_default_root())
    detector = DuplicateDetector(root)
    plan = detector.generate_reclamation_plan()

    if getattr(args, "json", False):
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    groups = plan.get("duplicate_groups", [])
    print(f"\nDuplicate File Analysis for: {root}")
    print(f"Found {plan.get('duplicate_group_count', 0)} duplicate groups ({plan.get('duplicate_file_count', 0)} files)")
    print(f"Reclaimable Logical Space: {plan.get('total_reclaimable_bytes', 0) / (1024*1024):.2f} MB")
    print(f"Reclaimable Cluster Slack: {plan.get('total_reclaimable_slack', 0) / (1024*1024):.2f} MB")
    print("-" * 70)

    for i, g in enumerate(groups[:15], 1):
        sz = g["size"]
        print(f"Group #{i} ({sz:,} B each, {len(g['files'])} copies, SHA: {g['hash'][:12]}...):")
        for f in g["files"]:
            print(f"  - {os.path.relpath(f, root)}")
        print()

    if len(groups) > 15:
        print(f"... and {len(groups) - 15} more duplicate groups.")

    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """Handles the `index` subcommand."""
    root = os.path.abspath(getattr(args, "root", None) or detect_default_root())
    db_path = getattr(args, "db", None) or get_default_db_path(root)
    db_path = os.path.abspath(db_path)

    print(f"Building FTS5 search index for: {root}")
    print(f"Database target: {db_path}")

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = DatabaseManager(db_path)
    db.initialize_schema()
    mgr = IndexManager(db, root)

    stats = mgr.full_index(batch_size=getattr(args, "batch", 500) or 500)
    print(f"✓ Indexed {stats.indexed_files:,} files in {stats.elapsed_seconds:.2f}s ({stats.throughput_fps:,.1f} files/sec).")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Handles the `update` subcommand."""
    root = os.path.abspath(getattr(args, "root", None) or detect_default_root())
    db_path = getattr(args, "db", None) or get_default_db_path(root)
    db_path = os.path.abspath(db_path)

    if not os.path.exists(db_path):
        sys.stderr.write(f"Error: Database file not found at '{db_path}'. Please run 'index' first.\n")
        return 1

    db = DatabaseManager(db_path)
    mgr = IndexManager(db, root)

    inc = mgr.incremental_update()
    if getattr(args, "json", False):
        print(json.dumps(dataclasses.asdict(inc), indent=2))
        return 0

    print(f"✓ Incremental sync completed in {inc.elapsed_seconds:.2f}s:")
    print(f"  + Added:     {inc.added:,}")
    print(f"  ~ Modified:  {inc.modified:,}")
    print(f"  - Deleted:   {inc.deleted:,}")
    print(f"  = Unchanged: {inc.unchanged:,}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Constructs the top-level argument parser and subparsers."""
    parser = argparse.ArgumentParser(
        prog="smart-drive",
        description="SmartDrive-OS: Unified Autonomous Drive Management Suite (exFAT 512KB).",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # 1. init
    p_init = subparsers.add_parser("init", help="Initialize SSD workspace with taxonomies, shields, and manifests")
    p_init.add_argument("path", nargs="?", default=None, help="Target directory to initialize")
    p_init.add_argument("--root", help="Alias for target directory")
    p_init.add_argument(
        "--profile",
        default="general-workspace",
        choices=["general-workspace", "ai-developer", "data-science"],
        help="Preset profile configuration",
    )
    p_init.add_argument("--force", action="store_true", help="Force overwrite existing manifests")
    p_init.add_argument("--json", action="store_true", help="Output initialization report in JSON")

    # 2. status
    p_status = subparsers.add_parser("status", help="Inspect SSD mount status, geometry, and taxonomies")
    p_status.add_argument("--root", help="Root directory of the SSD")
    p_status.add_argument("--json", action="store_true", help="Output status in JSON format")

    # 3. audit
    p_audit = subparsers.add_parser("audit", help="Storage breakdown & 512KB cluster slack audit")
    p_audit.add_argument("--root", help="Root directory of the SSD")
    p_audit.add_argument("--json", action="store_true", help="Output audit report in JSON format")
    p_audit.add_argument("--markdown", action="store_true", help="Output audit report in Markdown format")
    p_audit.add_argument("--export", help="Save output to specified file path")

    # 4. clean
    p_clean = subparsers.add_parser("clean", help="Detect and safely purge system junk")
    p_clean.add_argument("--root", help="Root directory of the SSD")
    p_clean.add_argument("--dry-run", action="store_true", default=True, help="Simulate cleaning without deleting (default)")
    p_clean.add_argument("--apply", action="store_true", help="Execute deletion of detected junk")
    p_clean.add_argument("--tier", type=int, default=1, choices=[1, 2, 3], help="Junk tier severity (1=Safe, 2=Caches, 3=Dumps)")
    p_clean.add_argument("--log", help="Path to export JSON audit log")
    p_clean.add_argument("--json", action="store_true", help="Output junk report in JSON format")

    # 5. search
    p_search = subparsers.add_parser("search", help="Instant sub-10ms multi-criteria search")
    p_search.add_argument("query", nargs="?", default="", help="Query keyword or syntax (e.g. ext:pdf size:>10MB)")
    p_search.add_argument("--db", help="Path to SQLite index database file")
    p_search.add_argument("--root", help="Drive root directory")
    p_search.add_argument("--ext", help="Filter by file extension(s), comma-separated")
    p_search.add_argument("--size", help="Filter by size expression (e.g. >10MB, <1KB, 0)")
    p_search.add_argument("--category", help="Filter by category (Code, AI Models, Books/Learning, Docs, Media, Archives)")
    p_search.add_argument("--dir", help="Filter by directory substring")
    p_search.add_argument("--limit", type=int, default=100, help="Maximum number of results to display")
    p_search.add_argument("--json", action="store_true", help="Output matches in JSON format")
    p_search.add_argument("--csv", action="store_true", help="Output matches in CSV format")

    # 6. organize
    p_org = subparsers.add_parser("organize", help="Autonomous drive auto-zoning, cleaning & rebalancing")
    p_org.add_argument("--root", help="Root directory of the SSD")
    p_org.add_argument("--dry-run", action="store_true", help="Simulate organization without moving files (default)")
    p_org.add_argument("--apply", action="store_true", help="Execute file relocation and index update")
    p_org.add_argument("--clean", action="store_true", help="Also scan and purge Tier 1 safe system junk")
    p_org.add_argument("--json", action="store_true", help="Output zoning plan or result in JSON format")

    # 7. sentinel & agent-check
    for cmd_name in ["sentinel", "agent-check"]:
        p_check = subparsers.add_parser(
            cmd_name,
            aliases=["agent_check"] if cmd_name == "agent-check" else [],
            help="1-touch SSD self-healing & health verification for AI agents",
        )
        p_check.add_argument("--drive-root", "--root", dest="root", default=None, help="Root directory of the SSD")
        p_check.add_argument("--json", action="store_true", help="Output health report in structured JSON format")
        p_check.add_argument("--auto-heal", action="store_true", default=True, help="Auto-heal missing anti-indexing shields (default)")
        p_check.add_argument("--no-heal", action="store_false", dest="auto_heal", help="Disable automatic shield healing")

    # 8. mcp
    p_mcp = subparsers.add_parser("mcp", help="Run JSON-RPC 2.0 stdio MCP server for AI Coding Agents")
    p_mcp.add_argument("--root", help="Root directory of the SSD")

    # 9. mcp-config
    p_mcp_cfg = subparsers.add_parser("mcp-config", help="Register SmartDrive MCP server in AI IDE configurations")
    p_mcp_cfg.add_argument("--target-dir", help="Target project directory to write local .mcp.json")
    p_mcp_cfg.add_argument("--antigravity", action="store_true", help="Register only for Antigravity")
    p_mcp_cfg.add_argument("--claude", action="store_true", help="Register only for Claude Desktop")
    p_mcp_cfg.add_argument("--cursor", action="store_true", help="Register only for Cursor")
    p_mcp_cfg.add_argument("--windsurf", action="store_true", help="Register only for Windsurf")
    p_mcp_cfg.add_argument("--json", action="store_true", help="Output registration results in JSON")

    # 10. dup
    p_dup = subparsers.add_parser("dup", help="3-Phase SHA-256 duplicate detection")
    p_dup.add_argument("--root", help="Root directory of the SSD")
    p_dup.add_argument("--json", action="store_true", help="Output duplicate groups in JSON format")

    # 11. index
    p_idx = subparsers.add_parser("index", help="Build SQLite FTS5 search index")
    p_idx.add_argument("--root", help="Root directory of the SSD")
    p_idx.add_argument("--db", help="Path to SQLite index database file")
    p_idx.add_argument("--batch", type=int, default=500, help="Batch commit size (default: 500)")

    # 12. update
    p_upd = subparsers.add_parser("update", help="Incremental mtime/size search index synchronization")
    p_upd.add_argument("--root", help="Root directory of the SSD")
    p_upd.add_argument("--db", help="Path to SQLite index database file")
    p_upd.add_argument("--json", action="store_true", help="Output update stats in JSON format")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    dispatch = {
        "init": cmd_init,
        "status": cmd_status,
        "audit": cmd_audit,
        "clean": cmd_clean,
        "search": cmd_search,
        "organize": cmd_organize,
        "sentinel": cmd_sentinel,
        "agent-check": cmd_sentinel,
        "agent_check": cmd_sentinel,
        "mcp": cmd_mcp,
        "mcp-config": cmd_mcp_config,
        "dup": cmd_dup,
        "index": cmd_index,
        "update": cmd_update,
    }

    handler = dispatch.get(args.subcommand)
    if handler:
        return handler(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
