"""smart_drive.mcp.server - Zero-Dependency JSON-RPC 2.0 stdio MCP Server.

Provides autonomous AI coding agents with 8 high-performance tools:
1. ssd_search: Instant sub-10ms multi-criteria search over 500,000+ files.
2. ssd_audit: Storage allocation breakdown and 512KB cluster slack metrics.
3. ssd_clean: Safe junk cleaner (Tier 1 safe by default, dry-run or apply mode).
4. ssd_find_duplicates: 3-phase duplicate detection and space reclamation plan.
5. ssd_update_index: Fast incremental mtime/size search index synchronization.
6. ssd_check_safety: exFAT compatibility guard (illegal chars, symlinks, whitelist).
7. ssd_status: Root discovery, anti-indexing shield status, and taxonomy health.
8. ssd_auto_organize: Autonomous drive auto-zoning, cleaning & rebalancing.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import CLUSTER_SIZE_BYTES, JunkTier, is_protected_root_file, is_protected_root_dir
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.exfat_compat import ExFatEngine
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.search.engine import SearchEngine
from smart_drive.search.parser import SearchParams, parse_search_query, parse_size_spec

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "smart-drive"
SERVER_VERSION = "1.0.0"

logger = logging.getLogger("smart_drive.mcp.server")
logger.setLevel(logging.INFO)
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setFormatter(logging.Formatter("[SmartDrive MCP %(levelname)s] %(message)s"))
logger.addHandler(stderr_handler)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "ssd_search",
        "description": "Instant high-speed search across 500,000+ files on the SSD (<10ms latency). Use this instead of running slow shell find or grep commands.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword, phrase, or pattern to search for.",
                },
                "ext": {
                    "type": "string",
                    "description": "Filter by file extension(s) without dot (e.g. 'pdf', 'py'). Comma-separated.",
                },
                "category": {
                    "type": "string",
                    "description": "Functional category filter: 'Code', 'AI Models', 'Books/Learning', 'Docs', 'Media', 'Archives'.",
                },
                "size": {
                    "type": "string",
                    "description": "File size constraint (e.g. '>10MB', '<500KB', '0').",
                },
                "directory": {
                    "type": "string",
                    "description": "Subdirectory path filter.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 25, max: 100).",
                    "default": 25,
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default: 0).",
                    "default": 0,
                },
                "compact": {
                    "type": "boolean",
                    "description": "Return compact schema ('path', 'size', 'cat') to preserve token budget (default: true).",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "ssd_audit",
        "description": "Returns full storage allocation breakdown across standard taxonomies and calculates wasted 512KB exFAT cluster slack.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sub_dir": {
                    "type": "string",
                    "description": "Optional subdirectory to restrict audit scope.",
                },
            },
        },
    },
    {
        "name": "ssd_clean",
        "description": "Identifies and purges system junk files (.DS_Store, Thumbs.db, temp caches). Enforces mandatory whitelist protection.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, only previews junk items without unlinking (default: true).",
                    "default": True,
                },
                "apply": {
                    "type": "boolean",
                    "description": "If true, executes actual deletion. Overrides dry_run to False.",
                    "default": False,
                },
                "sub_dir": {
                    "type": "string",
                    "description": "Optional subdirectory to restrict cleaning scope.",
                },
                "tier": {
                    "type": "integer",
                    "description": "Junk severity tier: 1 = Safe OS metadata (default), 2 = Dev build/test caches, 3 = Crash dumps.",
                    "default": 1,
                },
            },
        },
    },
    {
        "name": "ssd_find_duplicates",
        "description": "Detects identical duplicate files using 3-phase cascade (Size -> 8KB Hash -> Full SHA-256) and returns exact space savings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sub_dir": {
                    "type": "string",
                    "description": "Optional subdirectory to inspect for duplicates.",
                },
            },
        },
    },
    {
        "name": "ssd_update_index",
        "description": "Performs fast incremental synchronization of the SQLite FTS5 search index after creating, editing, or deleting files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Optional subdirectory to restrict incremental update scope.",
                },
            },
        },
    },
    {
        "name": "ssd_check_safety",
        "description": "Verifies whether a file path or operation complies with exFAT rules: checks for illegal Windows characters, symlink attempts, and whitelist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path or filename to inspect.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "ssd_status",
        "description": "Inspects SSD mount status, anti-indexing shield integrity (.metadata_never_index, .fseventsd/no_log), and taxonomy health.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "ssd_auto_organize",
        "description": "Autonomous drive auto-zoning, anti-slack rebalancing, and organization. Classifies loose items into standard taxonomies, ensures shields, and syncs index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "apply": {
                    "type": "boolean",
                    "description": "If true, executes relocation and index sync. If false (default), simulates plan.",
                },
                "clean": {
                    "type": "boolean",
                    "description": "If true, also scans and purges Tier 1 safe system junk.",
                },
            },
        },
    },
]


class SmartDriveMCPServer:
    """JSON-RPC 2.0 stdio MCP Server implementation."""

    def __init__(self, root: Optional[str] = None) -> None:
        if root:
            self.root = os.path.abspath(root)
        else:
            detected = SmartDriveProxy.detect_mount_point()
            self.root = str(detected) if detected else os.getcwd()
        self.use_content_length_mode = False

    def get_db_path(self) -> str:
        new_path = os.path.join(self.root, ".smart_drive", "index.db")
        if os.path.exists(new_path):
            return new_path
        legacy_path = os.path.join(self.root, ".smart_drive_manager", "index.db")
        if os.path.exists(legacy_path):
            return legacy_path
        return new_path

    def handle_ssd_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query_str = args.get("query", "")
        params = parse_search_query(query_str)

        if args.get("ext"):
            for e in args["ext"].split(","):
                clean_e = e.strip().lstrip(".")
                if clean_e:
                    params.extensions.add(clean_e)

        if args.get("category"):
            params.category = args["category"]

        if args.get("directory"):
            params.directory = args["directory"].strip().strip('"').strip("'")

        if args.get("size"):
            op, b_val = parse_size_spec(args["size"])
            if b_val is not None:
                if op in (">", ">="):
                    params.min_size = b_val
                elif op in ("<", "<="):
                    params.max_size = b_val
                elif op == "=":
                    params.min_size = b_val
                    params.max_size = b_val

        params.limit = min(int(args.get("limit", 25)), 100)
        params.offset = max(int(args.get("offset", 0)), 0)
        compact_mode = bool(args.get("compact", True))

        db_path = self.get_db_path()
        if not os.path.exists(db_path):
            return {
                "error": f"Search database not found at '{db_path}'. Run ssd_update_index first.",
                "matches": [],
                "total_count": 0,
                "offset": params.offset,
                "limit": params.limit,
                "returned": 0,
                "has_more": False,
                "next_offset": None,
            }

        db = DatabaseManager(db_path)
        engine = SearchEngine(db)
        result = engine.search(params)

        MAX_CHAR_BUDGET = 4800
        serialized_matches = []
        truncated = False

        for m in result.matches:
            entry = {"path": m.path, "size": m.size, "cat": m.category} if compact_mode else m.to_dict()
            serialized_matches.append(entry)
            candidate_res = {
                "query": query_str,
                "total_count": result.total_count,
                "matches": serialized_matches,
            }
            if len(json.dumps(candidate_res, ensure_ascii=False)) > MAX_CHAR_BUDGET:
                serialized_matches.pop()
                truncated = True
                break

        returned_count = len(serialized_matches)
        has_more = ((params.offset + returned_count) < result.total_count) or truncated

        return {
            "query": query_str,
            "total_count": result.total_count,
            "offset": params.offset,
            "limit": params.limit,
            "returned": returned_count,
            "has_more": has_more,
            "next_offset": (params.offset + returned_count) if has_more else None,
            "elapsed_ms": round(result.elapsed_ms, 2),
            "matches": serialized_matches,
            "truncated_to_token_limit": truncated,
        }

    def handle_ssd_audit(self, args: Dict[str, Any]) -> Dict[str, Any]:
        sub_dir = args.get("sub_dir")
        target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root
        auditor = StorageAuditor(target_root)
        report = auditor.run_audit()
        return {
            "root_path": report.get("root_path", target_root),
            "total_files": report.get("total_files", 0),
            "total_directories": report.get("total_directories", 0),
            "total_logical_bytes": report.get("total_logical_bytes", 0),
            "total_allocated_bytes": report.get("total_allocated_bytes", 0),
            "total_slack_bytes": report.get("total_slack_bytes", 0),
            "total_slack_percentage": report.get("total_slack_percentage", 0.0),
            "taxonomies": report.get("taxonomies", {}),
            "categories": report.get("categories", {}),
            "top_slack_directories": report.get("top_slack_directories", [])[:10],
        }

    def handle_ssd_clean(self, args: Dict[str, Any]) -> Dict[str, Any]:
        dry_run = args.get("dry_run", True)
        if "apply" in args:
            dry_run = not bool(args["apply"])
        sub_dir = args.get("sub_dir") or args.get("directory")
        target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root

        tier_val = args.get("tier", 1)
        tier_map = {1: JunkTier.TIER_1_SAFE, 2: JunkTier.TIER_2_DEV_CACHE, 3: JunkTier.TIER_3_SENSITIVE}
        max_tier = tier_map.get(tier_val, JunkTier.TIER_1_SAFE)

        detector = JunkDetector(target_root, max_tier=max_tier)
        junk_items = detector.find_junk()

        guard = SecurityGuard(drive_root=self.root)
        purge_engine = PurgeEngine(guard, dry_run=dry_run)
        summary = purge_engine.purge_batch(junk_items, dry_run=dry_run)

        return {
            "target_root": target_root,
            "dry_run": dry_run,
            "tier": max_tier.name,
            "detected_count": len(junk_items),
            "purged_count": summary.total_succeeded,
            "nominal_bytes_reclaimed": summary.nominal_bytes_reclaimed,
            "slack_bytes_reclaimed": summary.allocated_bytes_reclaimed,
        }

    def handle_ssd_find_duplicates(self, args: Dict[str, Any]) -> Dict[str, Any]:
        sub_dir = args.get("sub_dir")
        target_root = os.path.join(self.root, sub_dir) if sub_dir else self.root
        detector = DuplicateDetector(target_root)
        return detector.generate_reclamation_plan()

    def handle_ssd_update_index(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target_dir = args.get("directory")
        target_root = os.path.join(self.root, target_dir) if target_dir else self.root
        db_path = self.get_db_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db = DatabaseManager(db_path)
        mgr = IndexManager(db, target_root)
        inc = mgr.incremental_update()
        return dataclasses.asdict(inc)

    def handle_ssd_check_safety(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path_str = args.get("path", "")
        if not path_str:
            return {"error": "Missing required argument 'path'"}
        rel_path = os.path.relpath(path_str, self.root) if os.path.isabs(path_str) else path_str
        forbidden_chars = ExFatEngine.audit_forbidden_characters(os.path.basename(path_str))
        is_prot_file = is_protected_root_file(rel_path)
        is_prot_dir = is_protected_root_dir(rel_path)
        return {
            "path": path_str,
            "is_safe": len(forbidden_chars) == 0 and not is_prot_file and not is_prot_dir,
            "forbidden_character_violations": [str(c) for c in forbidden_chars],
            "is_protected_root_file": is_prot_file,
            "is_protected_root_dir": is_prot_dir,
        }

    def handle_ssd_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from smart_drive.core.sentinel import SentinelEngine
        engine = SentinelEngine(self.root)
        report = engine.run_health_check(self.root, auto_heal=False)
        return dict(report)

    def handle_ssd_auto_organize(self, args: Dict[str, Any]) -> Dict[str, Any]:
        zoner = AutoZoner(self.root)
        plan = zoner.generate_plan()
        apply_mode = bool(args.get("apply", False))
        if apply_mode:
            res = zoner.apply_plan(plan)
            return {"status": "applied", "result": res}
        return {"status": "dry_run", "plan_count": len(plan), "actions": [a.to_dict() for a in plan]}

    def dispatch_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        dispatch_table: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "ssd_search": self.handle_ssd_search,
            "ssd_audit": self.handle_ssd_audit,
            "ssd_clean": self.handle_ssd_clean,
            "ssd_find_duplicates": self.handle_ssd_find_duplicates,
            "ssd_update_index": self.handle_ssd_update_index,
            "ssd_check_safety": self.handle_ssd_check_safety,
            "ssd_status": self.handle_ssd_status,
            "ssd_auto_organize": self.handle_ssd_auto_organize,
        }
        handler = dispatch_table.get(name)
        if not handler:
            raise ValueError(f"Unknown tool '{name}'")
        return handler(args)

    def send_response(self, response: Dict[str, Any]) -> None:
        body = json.dumps(response, ensure_ascii=False)
        if self.use_content_length_mode:
            sys.stdout.write(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}")
        else:
            sys.stdout.write(body + "\n")
        sys.stdout.flush()

    def handle_request(self, req: Dict[str, Any]) -> None:
        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            self.send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                    "capabilities": {
                        "tools": {},
                    },
                },
            })
            return

        if method == "ping":
            self.send_response({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            return

        if method == "notifications/initialized":
            return

        if method == "tools/list":
            self.send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })
            return

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                res_data = self.dispatch_tool(tool_name, arguments)
                self.send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(res_data, indent=2, ensure_ascii=False),
                            }
                        ],
                    },
                })
            except Exception as e:
                logger.exception("Error executing tool %s: %s", tool_name, e)
                self.send_response({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error executing tool '{tool_name}': {str(e)}",
                            }
                        ],
                    },
                })
            return

        if msg_id is not None:
            self.send_response({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Unhandled method '{method}'",
                },
            })

    def run_stdio(self) -> None:
        """Runs the JSON-RPC 2.0 stdio event loop."""
        if sys.platform == "win32":
            if hasattr(sys.stdin, "reconfigure"):
                sys.stdin.reconfigure(encoding="utf-8")
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8")

        logger.info("Smart Drive MCP Server started. Root: %s", self.root)

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                line_clean = line.strip()
                if not line_clean:
                    continue

                if line_clean.lower().startswith("content-length:"):
                    self.use_content_length_mode = True
                    content_length = int(line_clean.split(":", 1)[1].strip())
                    while True:
                        sep_line = sys.stdin.readline()
                        if sep_line.strip() == "":
                            break
                    body = sys.stdin.read(content_length)
                    try:
                        req = json.loads(body)
                    except Exception as json_err:
                        self.send_response({
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": f"Parse error: {json_err}"},
                        })
                        continue
                    self.handle_request(req)
                elif line_clean.startswith("{"):
                    try:
                        req = json.loads(line_clean)
                    except Exception as json_err:
                        self.send_response({
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": f"Parse error: {json_err}"},
                        })
                        continue
                    self.handle_request(req)
            except Exception as e:
                logger.error("Error in run_stdio: %s", e)


__all__ = ["PROTOCOL_VERSION", "SERVER_NAME", "SERVER_VERSION", "SmartDriveMCPServer", "TOOLS"]
