"""smart_drive.mcp.server - Zero-Dependency JSON-RPC 2.0 stdio MCP Server.

Provides autonomous AI coding agents with 8 high-performance tools:
1. ssd_search: Instant sub-10ms multi-criteria search over 500,000+ files.
2. ssd_audit: Storage allocation breakdown and 512KB cluster slack metrics.
3. ssd_clean: Safe junk cleaner (Tier 1 safe by default, dry-run or apply mode).
4. ssd_find_duplicates: 3-phase duplicate detection and space reclamation plan.
5. ssd_update_index: Fast incremental mtime/size search index synchronization.
6. ssd_check_safety: exFAT compatibility guard (illegal chars, symlinks, whitelist).
7. ssd_status: Root discovery, anti-indexing shield status, SQLite search database integrity, exFAT safety, and Git multi-repository status.
8. ssd_auto_organize: Autonomous drive auto-zoning, cleaning & rebalancing.
"""

from __future__ import annotations

import collections
import dataclasses
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from smart_drive.core.auditor import StorageAuditor, format_bytes
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.config import (
    JunkTier,
    ensure_anti_indexing_markers,
    is_protected_root_file,
    is_protected_root_dir,
)
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.exfat_compat import ExFatEngine
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.core.root import DriveRootNotFound, find_drive_root
from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.search.engine import SearchEngine
from smart_drive.search.parser import apply_size_spec, parse_search_query

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "smart-drive"
SERVER_VERSION = "1.1.0"

logger = logging.getLogger("smart_drive.mcp.server")
logger.setLevel(logging.INFO)
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setFormatter(logging.Formatter("[SmartDrive MCP %(levelname)s] %(message)s"))
logger.addHandler(stderr_handler)

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "ssd_search",
        "description": (
            "MANDATORY FILE SEARCH: Instant sub-10ms multi-criteria search over 500,000+ files "
            "using the local SQLite FTS5 index. ALWAYS use ssd_search to locate files, code, "
            "models, or documents. DO NOT use recursive shell commands ('find', 'grep', 'dir /s', "
            "'Get-ChildItem') on this external SSD, as walking the 512KB cluster exFAT filesystem "
            "causes severe I/O thrashing and high latency. Supports keywords ('transformer'), "
            "exact phrases ('\"llama 3\"'), prefix wildcards ('audit*'), extensions ('py,json'), "
            "categories ('Code', 'AI Models'), directory scopes, and size filters ('>10MB')."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
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
                    "description": "File size constraint (e.g. '>10MB', '<500KB', '0'). '>' and '<' exclude the bound, '>=' and '<=' include it; units are binary (1KB = 1024 bytes).",
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
        "description": (
            "Analyzes SSD storage allocation breakdown across 6 standard taxonomies (01_AI_Models .. "
            "06_Archives_Storage) and computes wasted 512KB exFAT cluster slack and top slack directories. "
            "Use this to audit drive capacity. NEVER run recursive shell find or grep commands."
        ),
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "sub_dir": {
                    "type": "string",
                    "description": "Optional subdirectory to restrict audit scope.",
                },
                "directory": {
                    "type": "string",
                    "description": "Optional subdirectory to restrict audit scope (alias for sub_dir).",
                },
                "compact": {
                    "type": "boolean",
                    "description": "Return summarized category extensions instead of raw extension dictionary (default: true).",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "ssd_clean",
        "description": "Identifies and purges system junk files (.DS_Store, Thumbs.db, temp caches). Enforces mandatory whitelist protection.",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
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
        "description": "Detects identical duplicate files using 3-phase cascade (Size -> 8KB Hash -> Full SHA-256) and returns exact nominal and 512KB physical cluster space savings with pagination controls.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
        "inputSchema": {
            "type": "object",
            "properties": {
                "sub_dir": {
                    "type": "string",
                    "description": "Optional subdirectory to inspect for duplicates.",
                },
                "min_size": {
                    "type": "integer",
                    "description": "Optional minimum file size in bytes to filter duplicates (default: 0).",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum duplicate groups to return (default: 20, max: 100).",
                    "default": 20,
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset for duplicate groups (default: 0).",
                    "default": 0,
                },
            },
        },
    },
    {
        "name": "ssd_update_index",
        "description": (
            "Performs fast incremental synchronization of the SQLite FTS5 search index (<50ms) "
            "after creating, editing, or deleting files. Call this tool to keep the search index fresh. "
            "NEVER use shell find or grep."
        ),
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
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
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
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
        "description": "Inspects SSD mount status, anti-indexing shield integrity (.metadata_never_index, .fseventsd/no_log), SQLite search database integrity, exFAT safety, and Git multi-repository status.",
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "ssd_auto_organize",
        "description": "Autonomous drive auto-zoning, anti-slack rebalancing, and organization. Classifies loose items into standard taxonomies, ensures shields, and syncs index.",
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
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


class _LoadInt(int):
    """Integer that can also be called as a method for dual property/method access."""

    def __call__(self) -> int:
        return int(self)


class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding-window rate limiter using Python standard library.

    Enforces maximum requests within a rolling time window.
    Pure Python Standard Library: time.monotonic, collections.deque, threading.Lock.
    Zero external dependencies.
    """

    def __init__(
        self,
        max_requests: Optional[int] = None,
        window_seconds: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        if max_requests is None:
            env_req = os.environ.get("SMART_DRIVE_MCP_RATE_LIMIT_REQUESTS", "120")
            try:
                max_requests = int(env_req)
            except (ValueError, TypeError):
                max_requests = 120
        self.max_requests = max(1, int(max_requests))

        if window_seconds is None:
            env_win = os.environ.get("SMART_DRIVE_MCP_RATE_LIMIT_WINDOW", "60.0")
            try:
                window_seconds = float(env_win)
            except (ValueError, TypeError):
                window_seconds = 60.0
        self.window_seconds = max(0.001, float(window_seconds))

        if enabled is None:
            env_en = os.environ.get("SMART_DRIVE_MCP_RATE_LIMIT_ENABLED", "true")
            enabled = env_en.strip().lower() in ("1", "true", "yes", "on")
        self.enabled = bool(enabled)

        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()
        self._last_simulated_time: Optional[float] = None

    def acquire(self, now: Optional[float] = None) -> Tuple[bool, float]:
        """Attempts to acquire a slot in the current sliding window.

        Args:
            now: Optional current timestamp in seconds (allows deterministic testing).

        Returns:
            Tuple[bool, float]: (allowed, retry_after).
            If allowed is True: (True, 0.0).
            If throttled: (False, retry_after_in_seconds).
        """
        if not self.enabled:
            return True, 0.0

        if now is not None:
            current_time = float(now)
            self._last_simulated_time = current_time
        else:
            current_time = time.monotonic()
            self._last_simulated_time = None

        with self._lock:
            cutoff = current_time - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(current_time)
                return True, 0.0

            oldest = self._timestamps[0]
            retry_after = max(0.001, (oldest + self.window_seconds) - current_time)
            return False, retry_after

    def reset(self) -> None:
        """Clears all recorded timestamps, resetting the window immediately."""
        with self._lock:
            self._timestamps.clear()
            self._last_simulated_time = None

    @property
    def current_load(self) -> _LoadInt:
        """Returns the number of active requests in the current window.

        Usable both as property (rl.current_load) and method (rl.current_load()).
        """
        with self._lock:
            now = self._last_simulated_time if self._last_simulated_time is not None else time.monotonic()
            cutoff = now - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            return _LoadInt(len(self._timestamps))


class SmartDriveMCPServer:
    """JSON-RPC 2.0 stdio MCP Server implementation."""

    TOOL_HANDLERS: Dict[str, str] = {
        "ssd_search": "handle_ssd_search",
        "ssd_audit": "handle_ssd_audit",
        "ssd_clean": "handle_ssd_clean",
        "ssd_find_duplicates": "handle_ssd_find_duplicates",
        "ssd_update_index": "handle_ssd_update_index",
        "ssd_check_safety": "handle_ssd_check_safety",
        "ssd_status": "handle_ssd_status",
        "ssd_auto_organize": "handle_ssd_auto_organize",
    }

    def __init__(
        self,
        root: Optional[str] = None,
        rate_limit_requests: Optional[int] = None,
        rate_limit_window: Optional[float] = None,
        rate_limit_enabled: Optional[bool] = None,
        auth_token: Optional[str] = None,
        require_auth: Optional[bool] = None,
    ) -> None:
        if root:
            res = find_drive_root(explicit=root)
            if res:
                self.root: Optional[str] = res.path
                self.root_source: Optional[str] = res.source
            else:
                self.root = None
                self.root_source = None
        else:
            res = find_drive_root()
            if res:
                self.root = res.path
                self.root_source = res.source
            else:
                self.root = None
                self.root_source = None
        self.use_content_length_mode = False
        self._raw_stdout: Optional[Any] = None
        self.rate_limiter = SlidingWindowRateLimiter(
            max_requests=rate_limit_requests,
            window_seconds=rate_limit_window,
            enabled=rate_limit_enabled,
        )

        # Authentication Engine
        # Priority: parameter > SMART_DRIVE_MCP_AUTH_TOKEN env var > None
        if auth_token is not None:
            clean_token = str(auth_token).strip()
            self.auth_token: Optional[str] = clean_token if clean_token else None
        else:
            env_token = os.environ.get("SMART_DRIVE_MCP_AUTH_TOKEN", "").strip()
            self.auth_token = env_token if env_token else None

        # Priority: parameter > SMART_DRIVE_MCP_REQUIRE_AUTH env var > bool(self.auth_token)
        if require_auth is not None:
            self.require_auth: bool = bool(require_auth)
        else:
            env_req = os.environ.get("SMART_DRIVE_MCP_REQUIRE_AUTH")
            if env_req is not None:
                self.require_auth = env_req.strip().lower() in ("1", "true", "yes", "on")
            else:
                self.require_auth = bool(self.auth_token)

        # In stdio mode with no token and require_auth unconfigured, require_auth is False (zero-friction).
        self._authenticated: bool = not self.require_auth

    def _ensure_root(self) -> str:
        """Ensures that a valid drive root is configured, or raises DriveRootNotFound."""
        if self.root is None:
            raise DriveRootNotFound("Could not determine the SmartDrive root. Pass --root <path> or set SMART_DRIVE_ROOT.")
        return self.root

    def verify_token(self, token: Optional[str]) -> bool:
        """Verifies the provided token against self.auth_token using constant-time comparison."""
        if not self.auth_token or token is None:
            return False
        try:
            return hmac.compare_digest(
                str(token).encode("utf-8"),
                str(self.auth_token).encode("utf-8"),
            )
        except Exception:
            return False

    def _resolve_safe_path(self, sub_path: Optional[str], must_exist: bool = False) -> str:
        """Validates and resolves sub_path strictly within self.root.

        - If sub_path is empty or None, returns self.root.
        - Rejects null bytes (\\x00).
        - Rejects UNC / device namespace paths (//, \\, etc.).
        - Rejects Windows drive letters escaping root (^[a-zA-Z]:).
        - Rejects POSIX root escapes (/...).
        - Normalizes backslashes to / before joining and checking boundary containment.
        - If must_exist and not os.path.exists(target), raises FileNotFoundError.

        Raises:
            DriveRootNotFound: If self.root is None.
            ValueError: If path contains null bytes, is UNC, or escapes self.root.
            FileNotFoundError: If must_exist is True and path does not exist.
        """
        root = self._ensure_root()
        canonical_root = os.path.realpath(os.path.abspath(root))
        if sub_path is None:
            return canonical_root

        sub_path_str = str(sub_path).strip()
        if not sub_path_str:
            return canonical_root

        if "\x00" in sub_path_str:
            raise ValueError("Access denied: path contains null byte")

        # Reject UNC or device namespace paths
        if sub_path_str.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\", "\\??\\")):
            raise ValueError("Access denied: path escapes storage root")

        # Normalize backslashes to forward slashes for universal boundary check
        clean_norm = sub_path_str.replace("\\", "/")
        c_root_fwd = canonical_root.replace("\\", "/")

        # Check for Windows drive letter (e.g. C:, D:)
        if re.match(r"^[a-zA-Z]:", sub_path_str):
            root_drive = canonical_root[:2].upper() if len(canonical_root) >= 2 and canonical_root[0].isalpha() and canonical_root[1] == ":" else ""
            path_drive = sub_path_str[:2].upper()
            if not root_drive or path_drive != root_drive:
                raise ValueError("Access denied: path escapes storage root")
            if not (clean_norm == c_root_fwd or clean_norm.startswith(c_root_fwd + "/")):
                raise ValueError("Access denied: path escapes storage root")
            target = os.path.realpath(os.path.abspath(clean_norm))
        elif clean_norm.startswith("/"):
            if clean_norm == c_root_fwd or clean_norm.startswith(c_root_fwd + "/"):
                target = os.path.realpath(os.path.abspath(clean_norm))
            else:
                raise ValueError("Access denied: path escapes storage root")
        else:
            target = os.path.realpath(os.path.abspath(os.path.join(canonical_root, clean_norm)))

        # Verify boundary containment
        try:
            common = os.path.commonpath([canonical_root, target])
            if os.path.normcase(common) != os.path.normcase(canonical_root):
                raise ValueError("Access denied: path escapes storage root")
        except ValueError as err:
            raise ValueError("Access denied: path escapes storage root") from err

        if must_exist and not os.path.exists(target):
            raise FileNotFoundError(f"Path does not exist: {target}")

        return target

    @staticmethod
    def _parse_bool(val: Any, default: bool = False) -> bool:
        """Safely parses boolean values from JSON-RPC parameters.

        Prevents string 'false' or '0' from evaluating as truthy.
        """
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            clean = val.strip().lower()
            if clean in ("true", "1", "yes", "on", "apply"):
                return True
            if clean in ("false", "0", "no", "off", "dry_run"):
                return False
            return default
        return bool(val)

    @staticmethod
    def _parse_int(
        val: Any,
        default: int,
        min_val: Optional[int] = None,
        max_val: Optional[int] = None,
    ) -> int:
        """Safely parses integer values with bounds clamping and fallback."""
        if val is None:
            res = default
        else:
            try:
                res = int(val)
            except (ValueError, TypeError, OverflowError):
                res = default
        if min_val is not None and res < min_val:
            res = min_val
        if max_val is not None and res > max_val:
            res = max_val
        return res

    def get_db_path(self) -> str:
        root = self._ensure_root()
        new_path = os.path.join(root, ".smart_drive", "index.db")
        if os.path.exists(new_path):
            return new_path
        legacy_path = os.path.join(root, ".smart_drive_manager", "index.db")
        if os.path.exists(legacy_path):
            return legacy_path
        return new_path

    def handle_ssd_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query_str = str(args.get("query") or "")[:1000]
        params = parse_search_query(query_str)

        if args.get("ext"):
            for e in str(args["ext"]).split(","):
                clean_e = e.strip().lstrip(".")
                if clean_e:
                    params.extensions.add(clean_e)

        if args.get("category"):
            params.category = str(args["category"])

        if args.get("directory"):
            raw_dir = str(args["directory"]).strip().strip('"').strip("'")
            safe_dir = self._resolve_safe_path(raw_dir)
            try:
                rel_dir = os.path.relpath(safe_dir, self.root)
                params.directory = "" if rel_dir == "." else rel_dir
            except ValueError:
                params.directory = safe_dir

        if args.get("size"):
            apply_size_spec(params, str(args["size"]))

        params.limit = self._parse_int(args.get("limit"), default=25, min_val=1, max_val=100)
        params.offset = self._parse_int(args.get("offset"), default=0, min_val=0)
        compact_mode = self._parse_bool(args.get("compact"), default=True)

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

        response: Dict[str, Any] = {
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
        if result.warnings:
            response["warnings"] = result.warnings
        return response

    def handle_ssd_audit(self, args: Dict[str, Any]) -> Dict[str, Any]:
        sub_dir = args.get("sub_dir") or args.get("directory")
        target_root = self._resolve_safe_path(sub_dir)
        compact_mode = self._parse_bool(args.get("compact"), default=True)
        auditor = StorageAuditor(target_root)
        report = auditor.run_audit()

        raw_categories = report.get("categories", {})
        if compact_mode:
            categories_out = {}
            for name, cdata in raw_categories.items():
                exts = cdata.get("extensions", {})
                sorted_exts = sorted(exts.items(), key=lambda x: x[1], reverse=True)
                top_exts = [k if k.startswith(".") else f".{k}" for k, _ in sorted_exts[:3]]
                categories_out[name] = {
                    "name": cdata.get("name", name),
                    "file_count": cdata.get("file_count", 0),
                    "nominal_bytes": cdata.get("nominal_bytes", 0),
                    "allocated_bytes": cdata.get("allocated_bytes", 0),
                    "slack_bytes": cdata.get("slack_bytes", 0),
                    "slack_percentage": cdata.get("slack_percentage", 0.0),
                    "top_extensions": top_exts,
                    "extension_count": len(exts),
                }
        else:
            categories_out = raw_categories

        logical_bytes = report.get("total_logical_bytes", 0)
        slack_bytes = report.get("total_slack_bytes", 0)

        result = {
            "root_path": report.get("root_path", target_root),
            "total_files": report.get("total_files", 0),
            "total_directories": report.get("total_directories", 0),
            "total_logical_bytes": logical_bytes,
            "total_allocated_bytes": report.get("total_allocated_bytes", 0),
            "total_slack_bytes": slack_bytes,
            "total_slack_percentage": report.get("total_slack_percentage", 0.0),
            "taxonomies": report.get("taxonomies", {}),
            "categories": categories_out,
            "top_slack_directories": report.get("top_slack_directories", [])[:10],
        }
        if compact_mode:
            result["compact"] = True
            result["total_logical_formatted"] = format_bytes(logical_bytes)
            result["total_slack_formatted"] = format_bytes(slack_bytes)

        return result

    def handle_ssd_clean(self, args: Dict[str, Any]) -> Dict[str, Any]:
        dry_run = self._parse_bool(args.get("dry_run"), default=True)
        if "apply" in args:
            dry_run = not self._parse_bool(args["apply"], default=False)
        sub_dir = args.get("sub_dir") or args.get("directory")
        target_root = self._resolve_safe_path(sub_dir)

        tier_val = self._parse_int(args.get("tier"), default=1, min_val=1, max_val=3)
        tier_map = {1: JunkTier.TIER_1_SAFE, 2: JunkTier.TIER_2_DEV_CACHE, 3: JunkTier.TIER_3_SENSITIVE}
        max_tier = tier_map.get(tier_val, JunkTier.TIER_1_SAFE)

        detector = JunkDetector(target_root, max_tier=max_tier)
        junk_items = detector.find_junk()

        guard = SecurityGuard(drive_root=self._ensure_root())
        purge_engine = PurgeEngine(guard, dry_run=dry_run)
        summary = purge_engine.purge_batch(junk_items, dry_run=dry_run)

        result = {
            "target_root": target_root,
            "dry_run": dry_run,
            "tier": max_tier.name,
            "detected_count": len(junk_items),
            "purged_count": summary.total_succeeded,
            "nominal_bytes_reclaimed": summary.nominal_bytes_reclaimed,
            "slack_bytes_reclaimed": summary.allocated_bytes_reclaimed,
        }

        if dry_run:
            breakdown: Dict[str, int] = collections.defaultdict(int)
            sample_preview: List[str] = []
            for item in junk_items:
                type_name = getattr(item, "name", "") or getattr(item, "description", "other")
                breakdown[type_name] += 1
                if len(sample_preview) < 5:
                    sample_preview.append(getattr(item, "rel_path", getattr(item, "path", "")))
            result["breakdown_by_type"] = dict(breakdown)
            result["sample_preview"] = sample_preview

        return result

    def handle_ssd_find_duplicates(self, args: Dict[str, Any]) -> Dict[str, Any]:
        sub_dir = args.get("sub_dir")
        target_root = self._resolve_safe_path(sub_dir)
        min_size = self._parse_int(args.get("min_size"), default=0, min_val=0)
        limit = self._parse_int(args.get("limit"), default=20, min_val=1, max_val=100)
        offset = self._parse_int(args.get("offset"), default=0, min_val=0)

        detector = DuplicateDetector(target_root)
        plan = detector.generate_reclamation_plan()
        all_groups = plan.get("duplicate_groups", [])

        if min_size > 0:
            all_groups = [g for g in all_groups if g.get("size", 0) >= min_size]

        total_groups = len(all_groups)
        total_dup_files = sum(len(g.get("files", [])) for g in all_groups)
        total_reclaimable_bytes = sum(g.get("reclaimable_bytes", 0) for g in all_groups)
        total_reclaimable_slack = sum(g.get("reclaimable_slack", 0) for g in all_groups)
        total_reclaimable_physical = total_reclaimable_bytes + total_reclaimable_slack

        paged_groups = all_groups[offset: offset + limit]
        has_more = (offset + len(paged_groups)) < total_groups
        next_offset = (offset + len(paged_groups)) if has_more else None

        # Cap file paths per group to prevent context overflow (max 10 paths preview per group)
        max_files_per_group = 10
        capped_groups = []
        for g in paged_groups:
            files = g.get("files", [])
            g_copy = dict(g)
            if len(files) > max_files_per_group:
                g_copy["files"] = files[:max_files_per_group]
                g_copy["truncated_files_count"] = len(files) - max_files_per_group
            capped_groups.append(g_copy)

        return {
            "root": target_root,
            "limit": limit,
            "offset": offset,
            "total_groups": total_groups,
            "duplicate_group_count": total_groups,
            "returned_group_count": len(capped_groups),
            "duplicate_file_count": total_dup_files,
            "total_reclaimable_bytes": total_reclaimable_bytes,
            "total_reclaimable_slack": total_reclaimable_slack,
            "total_reclaimable_physical": total_reclaimable_physical,
            "has_more": has_more,
            "next_offset": next_offset,
            "duplicate_groups": capped_groups,
        }

    def handle_ssd_update_index(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target_dir = args.get("directory") or args.get("target_dir")
        target_root = self._resolve_safe_path(target_dir)
        db_path = self.get_db_path()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db = DatabaseManager(db_path)
        try:
            db.initialize_schema()
            # Index paths are stored relative to the drive root, so the manager must always be
            # rooted there; a sub-directory is passed as the sync scope. Rooting the manager at
            # the sub-directory would treat every row outside it as deleted.
            mgr = IndexManager(db, self._ensure_root())
            inc = mgr.incremental_update(directory=target_root if target_dir else None)
            return dataclasses.asdict(inc)
        finally:
            db.close()

    def handle_ssd_check_safety(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path_str = args.get("path", "")
        if not path_str or not isinstance(path_str, str):
            return {"error": "Missing required argument 'path'"}
        if "\x00" in path_str:
            return {
                "path": path_str,
                "is_safe": False,
                "is_symlink": False,
                "forbidden_character_violations": ["\\x00"],
                "is_protected_root_file": False,
                "is_protected_root_dir": False,
                "error": "Path contains null byte",
            }

        root = self._ensure_root()
        canonical_root = os.path.realpath(os.path.abspath(root))
        clean_path = path_str.strip()

        # 1. UNC network paths
        if clean_path.startswith(("\\\\", "//")):
            path_no_unc = re.sub(r'^[\\/]+', '', clean_path)
            path_segments = [p for p in path_no_unc.replace("\\", "/").split("/") if p and p not in (".", "..")]
            all_forbidden = []
            for seg in path_segments:
                violations = ExFatEngine.audit_forbidden_characters(seg)
                for v in violations:
                    v_str = str(v)
                    if v_str not in all_forbidden:
                        all_forbidden.append(v_str)
            return {
                "path": path_str,
                "is_safe": False,
                "is_symlink": False,
                "forbidden_character_violations": all_forbidden,
                "is_protected_root_file": False,
                "is_protected_root_dir": False,
                "error": "Path is on a different drive mount or escapes drive root (UNC path)",
            }

        # 2. Cross-drive checks & Windows drive stripping
        root_drive = canonical_root[:2].upper() if len(canonical_root) >= 2 and canonical_root[0].isalpha() and canonical_root[1] == ":" else ""
        path_drive = clean_path[:2].upper() if len(clean_path) >= 2 and clean_path[0].isalpha() and clean_path[1] == ":" else ""
        path_no_drive = re.sub(r'^[a-zA-Z]:', '', clean_path)
        norm_sub = path_no_drive.replace("\\", "/")

        # Inspect intermediate segments without Windows drive prefix to avoid false positive colons
        path_segments = [p for p in norm_sub.split("/") if p and p not in (".", "..")]
        all_forbidden: List[str] = []
        for seg in path_segments:
            violations = ExFatEngine.audit_forbidden_characters(seg)
            for v in violations:
                v_str = str(v)
                if v_str not in all_forbidden:
                    all_forbidden.append(v_str)

        if path_drive and path_drive != root_drive:
            return {
                "path": path_str,
                "is_safe": False,
                "is_symlink": False,
                "forbidden_character_violations": all_forbidden,
                "is_protected_root_file": False,
                "is_protected_root_dir": False,
                "error": "Path is on a different drive mount or escapes drive root",
            }

        c_root_fwd = canonical_root.replace("\\", "/")
        if norm_sub.startswith("/"):
            raw_target = norm_sub
            if norm_sub == c_root_fwd or norm_sub.startswith(c_root_fwd + "/"):
                escapes_root = False
                normalized_target = os.path.realpath(os.path.abspath(norm_sub))
            else:
                escapes_root = True
                normalized_target = canonical_root
        else:
            raw_target = os.path.join(canonical_root, norm_sub)
            normalized_target = os.path.realpath(os.path.abspath(raw_target))
            try:
                escapes_root = (os.path.commonpath([canonical_root, normalized_target]) != canonical_root)
            except ValueError:
                escapes_root = True

        if escapes_root:
            rel_path = clean_path
        else:
            try:
                rel_path = os.path.relpath(normalized_target, canonical_root)
            except ValueError:
                escapes_root = True
                rel_path = clean_path

        is_symlink = False
        try:
            if (
                ExFatEngine.is_symlink(clean_path)
                or ExFatEngine.is_symlink(raw_target)
                or ExFatEngine.is_symlink(normalized_target)
                or os.path.islink(clean_path)
                or os.path.islink(raw_target)
                or os.path.islink(normalized_target)
            ):
                is_symlink = True
        except (OSError, ValueError):
            is_symlink = False

        is_prot_file = is_protected_root_file(rel_path)
        is_prot_dir = is_protected_root_dir(rel_path)
        is_safe = (
            len(all_forbidden) == 0
            and not is_symlink
            and not is_prot_file
            and not is_prot_dir
            and not escapes_root
        )
        res = {
            "path": path_str,
            "is_safe": is_safe,
            "is_symlink": is_symlink,
            "forbidden_character_violations": all_forbidden,
            "is_protected_root_file": is_prot_file,
            "is_protected_root_dir": is_prot_dir,
        }
        if escapes_root:
            res["error"] = "Path is on a different drive mount or escapes drive root"
        return res

    def handle_ssd_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = self._ensure_root()
        try:
            from smart_drive.core.sentinel import SentinelEngine
            engine = SentinelEngine(root)
            report = engine.run_health_check(root, auto_heal=False)
            res = dict(report)
            if "mount" in res and isinstance(res["mount"], dict):
                res["mount"]["source"] = self.root_source
            res["source"] = self.root_source
            return res
        except Exception as e:
            logger.exception("Error during ssd_status health check: %s", e)
            return {
                "mount": {"root": root, "source": self.root_source, "accessible": os.path.exists(root)},
                "anti_indexing_shields": {"status": "unknown"},
                "taxonomies": {},
                "integrity_status": "error",
                "error": str(e),
                "source": self.root_source,
            }

    def handle_ssd_auto_organize(self, args: Dict[str, Any]) -> Dict[str, Any]:
        root = self._ensure_root()
        zoner = AutoZoner(root)
        plan = zoner.generate_plan()
        apply_mode = self._parse_bool(args.get("apply"), default=False)
        clean_mode = self._parse_bool(args.get("clean"), default=False)
        if clean_mode:
            try:
                detector = JunkDetector(root, max_tier=JunkTier.TIER_1_SAFE)
                junk_items = detector.find_junk()
                guard = SecurityGuard(drive_root=root)
                purge_engine = PurgeEngine(guard, dry_run=not apply_mode)
                purge_engine.purge_batch(junk_items, dry_run=not apply_mode)
            except Exception as e:
                logger.warning("Error running clean in auto_organize: %s", e)
        if apply_mode:
            created_shields = ensure_anti_indexing_markers(root)
            res = zoner.apply_plan(plan)
            db_path = self.get_db_path()
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            db = DatabaseManager(db_path)
            try:
                db.initialize_schema()
                mgr = IndexManager(db, root)
                inc_stats = mgr.incremental_update()
            finally:
                db.close()

            return {
                "status": "applied",
                "result": res,
                "shields_created": created_shields,
                "index_sync": dataclasses.asdict(inc_stats),
            }
        return {"status": "dry_run", "plan_count": len(plan), "actions": [a.to_dict() for a in plan]}

    def dispatch_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches a tool call with 100% static AST-resolvable branching."""
        if name == "ssd_search":
            return self.handle_ssd_search(args)
        elif name == "ssd_audit":
            return self.handle_ssd_audit(args)
        elif name == "ssd_clean":
            return self.handle_ssd_clean(args)
        elif name == "ssd_find_duplicates":
            return self.handle_ssd_find_duplicates(args)
        elif name == "ssd_update_index":
            return self.handle_ssd_update_index(args)
        elif name == "ssd_check_safety":
            return self.handle_ssd_check_safety(args)
        elif name == "ssd_status":
            return self.handle_ssd_status(args)
        elif name == "ssd_auto_organize":
            return self.handle_ssd_auto_organize(args)
        else:
            raise ValueError(f"Unknown tool '{name}'")

    def send_response(self, response: Dict[str, Any]) -> None:
        body = json.dumps(response, separators=(",", ":"), ensure_ascii=False)
        out = self._raw_stdout if self._raw_stdout is not None else sys.stdout
        if self.use_content_length_mode:
            out.write(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}")
        else:
            out.write(body + "\n")
        out.flush()

    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params")
        if not isinstance(params, dict):
            params = {}

        # Rate Limiting Check
        if method != "notifications/initialized":
            allowed, retry_after = self.rate_limiter.acquire()
            if not allowed:
                logger.warning("MCP rate limit exceeded. Retry after %.2fs", retry_after)
                retry_after_display = max(0.01, round(retry_after, 2))
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": f"Rate limit exceeded. Try again in {retry_after_display:.2f} seconds.",
                        "data": {
                            "retry_after": retry_after_display,
                            "max_requests": self.rate_limiter.max_requests,
                            "window_seconds": self.rate_limiter.window_seconds,
                        },
                    },
                }
                if msg_id is not None:
                    self.send_response(err_resp)
                return err_resp

        # Check for inline authentication token in params or _meta
        meta_token = None
        if isinstance(params.get("_meta"), dict):
            meta_token = params["_meta"].get("authToken") or params["_meta"].get("token")
        inline_token = meta_token or params.get("authToken") or params.get("token")

        if inline_token and self.verify_token(inline_token):
            self._authenticated = True

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                        "instructions": (
                            "SmartDrive-OS MCP Server governs an external high-speed SSD formatted as exFAT with a "
                            "512KB cluster allocation unit. Hard rules for autonomous coding agents:\n"
                            "1. Never run recursive find, grep, dir /s, or Get-ChildItem on this SSD. Always use the ssd_search tool (<10ms).\n"
                            "2. Call ssd_update_index after writing or modifying files to update the FTS5 index.\n"
                            "3. Never delete or relocate the 6 standard taxonomies (01_AI_Models .. 06_Archives_Storage).\n"
                            "4. Never place symlinks or Windows-illegal characters (\\ / : * ? \" < > |) on the drive.\n"
                            "5. Keep small micro-files bundled to prevent 512KB cluster slack waste."
                        ),
                    },
                    "capabilities": {
                        "tools": {},
                    },
                },
            }
            self.send_response(resp)
            return resp

        if method == "auth/handshake":
            if not self.require_auth and not self.auth_token:
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "status": "authenticated",
                        "authenticated": True,
                    },
                }
                self.send_response(resp)
                return resp

            if self.verify_token(inline_token):
                self._authenticated = True
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "status": "authenticated",
                        "authenticated": True,
                    },
                }
                self.send_response(resp)
                return resp
            else:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32001,
                        "message": "Authentication required: Missing or invalid authentication token",
                        "data": {"authenticated": False},
                    },
                }
                if msg_id is not None:
                    self.send_response(err_resp)
                return err_resp

        if method == "ping":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
            self.send_response(resp)
            return resp

        if method == "notifications/initialized":
            return None

        # Authentication Enforcement for Protected Endpoints (tools/list, tools/call, etc.)
        if self.require_auth and not self._authenticated:
            err_resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32001,
                    "message": "Authentication required: Missing or invalid authentication token",
                    "data": {"authenticated": False},
                },
            }
            if msg_id is not None:
                self.send_response(err_resp)
            return err_resp

        if method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            }
            self.send_response(resp)
            return resp

        if method == "tools/call":
            tool_name = params.get("name")
            raw_arguments = params.get("arguments")

            if raw_arguments is None:
                arguments: Dict[str, Any] = {}
            elif isinstance(raw_arguments, dict):
                arguments = raw_arguments
            else:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32602,
                        "message": "Invalid params: 'arguments' must be a JSON object dictionary",
                    },
                }
                if msg_id is not None:
                    self.send_response(err_resp)
                return err_resp

            try:
                res_data = self.dispatch_tool(tool_name, arguments)
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(res_data, separators=(",", ":"), ensure_ascii=False),
                            }
                        ],
                    },
                }
                self.send_response(resp)
                return resp
            except Exception as e:
                logger.exception("Error executing tool %s: %s", tool_name, e)
                err_tool_resp = {
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
                }
                self.send_response(err_tool_resp)
                return err_tool_resp

        if msg_id is not None:
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Unhandled method '{method}'",
                },
            }
            self.send_response(resp)
            return resp
        return None

    def run_stdio(self) -> None:
        """Runs the JSON-RPC 2.0 stdio event loop."""
        self._raw_stdout = sys.stdout
        orig_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            if sys.platform == "win32":
                if hasattr(sys.stdin, "reconfigure"):
                    sys.stdin.reconfigure(encoding="utf-8")
                if hasattr(self._raw_stdout, "reconfigure"):
                    self._raw_stdout.reconfigure(encoding="utf-8")
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
        finally:
            sys.stdout = orig_stdout
            self._raw_stdout = None


__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "SlidingWindowRateLimiter",
    "SmartDriveMCPServer",
    "TOOLS",
]
