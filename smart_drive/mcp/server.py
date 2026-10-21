"""smart_drive.mcp.server - Zero-Dependency JSON-RPC 2.0 stdio MCP Server.

Provides autonomous AI coding agents with 8 high-performance tools:
1. ssd_search: Instant sub-10ms multi-criteria search over 500,000+ files.
2. ssd_audit: Storage allocation breakdown and cluster slack metrics at the drive's real cluster size.
3. ssd_clean: Safe junk cleaner (Tier 1 safe by default, dry-run or apply mode), sized at the drive's real cluster size.
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
    SCAN_SKIPPED_PROTECTED_BY_SYSTEM,
    JunkTier,
    ensure_anti_indexing_markers,
    is_protected_root_file,
    is_protected_root_dir,
)
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.exfat_compat import ExFatEngine
from smart_drive.core.fsinfo import detect_filesystem, slack_cluster
from smart_drive.core.junk_detector import JunkDetector
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard
from smart_drive.core.relink import after_moves, relink
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.core.root import DriveRootNotFound, find_drive_root
from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.search.engine import SearchEngine
from smart_drive.search.parser import apply_size_spec, parse_search_query

# The MCP revisions that open with an `initialize` request, newest first: the client names the one it wants and the
# server answers with the one it will speak for the whole process.
LEGACY_PROTOCOL_VERSIONS: Tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
# The revisions without `initialize`: every request names its revision and the client's capabilities in
# `params._meta`, so the server keeps no per-connection state for them. Such a request is served beside the handshake
# of the older revisions, whatever `initialize` negotiated.
MODERN_PROTOCOL_VERSIONS: Tuple[str, ...] = ("2026-07-28",)
SUPPORTED_PROTOCOL_VERSIONS: Tuple[str, ...] = MODERN_PROTOCOL_VERSIONS + LEGACY_PROTOCOL_VERSIONS
LATEST_PROTOCOL_VERSION: str = SUPPORTED_PROTOCOL_VERSIONS[0]
# The revision of a client that names none (an initialize without a string protocolVersion, or any request before
# initialize): the oldest, all this server spoke before it negotiated, so such a client sees no change.
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "smart-drive"
SERVER_VERSION = "1.4.3"
# How MCP shows the server to people: the title from revision 2025-06-18, the description and website from 2025-11-25.
# The same words as server.json, which the registry lists.
SERVER_TITLE = "SmartDrive-OS"
SERVER_DESCRIPTION = "Organize a drive without breaking coding agents: relink moved folders, clean junk, search files."
SERVER_WEBSITE_URL = "https://github.com/DuongNAD/smart-drive-os#readme"
# The `_meta` keys of MCP 2026-07-28: what a request names, and the identity a result carries.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
# The JSON-RPC error code of a request that names a revision this server does not serve that way.
UNSUPPORTED_PROTOCOL_VERSION_CODE = -32022
# How long (in milliseconds) a client may keep a `tools/list` or `server/discover` answer. The tools and this server's
# identity only change when the package is upgraded, which restarts the process; five minutes bounds how long a client
# that caches across restarts keeps an old list.
CACHE_TTL_MS = 300_000

# Sent as InitializeResult.instructions: the MCP spec puts it at the top level of the result, which is where
# clients look for it (it was nested inside serverInfo, so no client ever saw it).
SERVER_INSTRUCTIONS = (
    "SmartDrive-OS MCP Server governs an external high-speed SSD formatted as exFAT with a "
    "512KB cluster allocation unit. Hard rules for autonomous coding agents:\n"
    "1. Never run recursive find, grep, dir /s, or Get-ChildItem on this SSD. Always use the ssd_search tool (<10ms).\n"
    "2. Call ssd_update_index after writing or modifying files to update the FTS5 index.\n"
    "3. Never delete or relocate the 6 standard taxonomies (01_AI_Models .. 06_Archives_Storage).\n"
    "4. Never place symlinks or Windows-illegal characters (\\ / : * ? \" < > |) on the drive.\n"
    "5. Keep small micro-files bundled to prevent 512KB cluster slack waste.\n"
    "6. Search lessons and research with smart-drive search --kind slide|fact|capture \"<query>\" (MCP ssd_search kind/content).\n"
    "7. Commit in small, atomic steps: one logical fix or feature per commit, paired with a regression test and a green test run, after checking ssd_gitignore (or smart-drive gitignore audit).\n"
    "8. Never move or rename folders by hand: ssd_auto_organize relinks coding agents itself; after a manual move run `smart-drive relink <old> <new>`."
)

# Mistakes a client can make (bad path, unknown tool, no drive configured): one log line, not a traceback.
_CLIENT_ERRORS = (
    ValueError,
    PermissionError,
    FileNotFoundError,
    NotADirectoryError,
    IsADirectoryError,
    DriveRootNotFound,
)

# A JSON-RPC batch is answered in one piece, so its size is capped (clients do not batch dozens of calls).
MAX_BATCH_SIZE = 100
# The revisions that have JSON-RPC batches. MCP 2025-03-26 added them and 2025-06-18 took them out again; 2024-11-05
# is plain JSON-RPC 2.0, which has them, and this server has always answered them.
BATCH_PROTOCOL_VERSIONS: Tuple[str, ...] = ("2025-03-26", "2024-11-05")


def _is_at_least(version: str, since: str) -> bool:
    """True when revision `version` is `since` or later; revisions are ISO dates, so strings sort in date order."""
    return version >= since


def negotiate_protocol_version(requested: Any) -> str:
    """The revision to speak after an `initialize` that asked for `requested` (its `protocolVersion`).

    MCP says to answer with the requested revision when it is supported and otherwise with the latest one supported.
    The latest here is the newest that opens with `initialize`, so a client asking for "2026-07-28", which has none,
    is answered with that. A client that names no revision (not a string) gets the oldest, as it always did.
    """
    if not isinstance(requested, str):
        return PROTOCOL_VERSION
    if requested in LEGACY_PROTOCOL_VERSIONS:
        return requested
    return LEGACY_PROTOCOL_VERSIONS[0]


def server_info(version: str) -> Dict[str, Any]:
    """The `serverInfo` of an initialize result under revision `version`; each revision only adds to the one before.

    2024-11-05 has `name` and `version`, 2025-06-18 adds the display `title`, 2025-11-25 adds `description` and
    `websiteUrl`.
    """
    info: Dict[str, Any] = {"name": SERVER_NAME, "version": SERVER_VERSION}
    if _is_at_least(version, "2025-06-18"):
        info["title"] = SERVER_TITLE
    if _is_at_least(version, "2025-11-25"):
        info["description"] = SERVER_DESCRIPTION
        info["websiteUrl"] = SERVER_WEBSITE_URL
    return info


def _is_modern_request(message: Any) -> bool:
    """True for a request in the 2026-07-28 style: not `initialize`, and `params._meta` names the revision.

    An `initialize` is always the handshake of an older revision, whatever its `_meta` says. The key alone decides (its
    value may be wrong, which is an error to report); a request without it is answered under what `initialize` chose.
    """
    if not isinstance(message, dict) or message.get("method") == "initialize":
        return False
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    return isinstance(meta, dict) and META_PROTOCOL_VERSION in meta


def _modern_envelope_error(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The JSON-RPC error object for a 2026-07-28 request whose `_meta` cannot be served, or None when it can.

    The revision is checked first and on its own: the envelope of a revision this server does not know may follow rules
    it does not know. A legacy revision is not served this way either, it is reached through `initialize`.
    """
    version = meta[META_PROTOCOL_VERSION]
    if not isinstance(version, str):
        return {
            "code": -32602,
            "message": f"Invalid params: '{META_PROTOCOL_VERSION}' in params._meta must be a string",
        }
    if version not in MODERN_PROTOCOL_VERSIONS:
        return {
            "code": UNSUPPORTED_PROTOCOL_VERSION_CODE,
            "message": "Unsupported protocol version",
            "data": {"supported": list(MODERN_PROTOCOL_VERSIONS), "requested": version},
        }
    if not isinstance(meta.get(META_CLIENT_CAPABILITIES), dict):
        return {
            "code": -32602,
            "message": (
                f"Invalid params: '{META_CLIENT_CAPABILITIES}' in params._meta is required and must be an object"
            ),
        }
    return None


def _is_client_error(error: BaseException) -> bool:
    """True for a deliberate refusal; encoding and JSON decoding failures are ValueErrors too, but are bugs."""
    return isinstance(error, _CLIENT_ERRORS) and not isinstance(error, (UnicodeError, json.JSONDecodeError))


def _is_inside(root: str, path: str) -> bool:
    """True when `path` is `root` itself or lies below it; both absolute and resolved, as os.path.realpath gives them.

    Whole path components are compared (os.path.commonpath), so a root that already ends in a separator (a drive root
    such as `E:\\` or `/`) needs no special case, `/data2` is not inside `/data`, the letter case of a Windows path
    does not matter, and a path on another drive is simply outside instead of an error.
    """
    try:
        return os.path.normcase(os.path.commonpath([root, path])) == os.path.normcase(root)
    except ValueError:
        return False


def _drive_relative_explanation(drive: str, path: str) -> str:
    """Why a path such as `E:x` is refused and what to send instead: the one wording of every tool that takes a path.

    `path` is the text that was sent (stripped), quoted back to the caller. Windows reads a drive letter that no
    separator follows against the current folder of that drive, which is process state and says nothing about the
    storage root, so such a path points nowhere the tool can vouch for.
    """
    return (
        f"Drive-relative path: Windows resolves '{path}' against the current folder of drive {drive}, "
        f"not against the storage root; send an absolute path ({drive}\\...) or a path relative to the storage root"
    )


def _refused_path_report(path: str, violations: List[str], error: str) -> Dict[str, Any]:
    """The ssd_check_safety answer for a path refused outright: the keys of every other answer, and the reason."""
    return {
        "path": path,
        "is_safe": False,
        "is_symlink": False,
        "forbidden_character_violations": violations,
        "is_protected_root_file": False,
        "is_protected_root_dir": False,
        "is_storage_root": False,
        "error": error,
    }


def _scan_skipped_note(target: str, skipped: Optional[Dict[str, str]], action: str) -> Optional[Dict[str, str]]:
    """A detector's `scan_skipped` plus a sentence to show: what kept the scan from looking at `target`, and why.

    None when the scan was done. `action` is what the tool would have offered ("cleaning", "deduplication").
    """
    if skipped is None:
        return None
    folder = skipped["folder"]
    if skipped["reason"] == SCAN_SKIPPED_PROTECTED_BY_SYSTEM:
        clause = f"used by Windows or an installed app ({skipped['detail']})"
    else:
        clause = skipped["detail"]
    same = os.path.normcase(os.path.normpath(target)) == os.path.normcase(os.path.normpath(folder))
    subject = folder if same else f"{target} lies inside {folder}, which"
    message = f"Nothing was scanned: {subject} is {clause}, so nothing in it is offered for {action}."
    return {**skipped, "message": message}


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
                "kind": {
                    "type": "string",
                    "description": "Filter by learning chunk kind: 'slide', 'lesson', 'fact', 'source', 'module', 'capture', 'outline', 'research', 'error', 'learning'.",
                    "enum": ["slide", "lesson", "fact", "source", "module", "capture", "outline", "research", "error", "learning"],
                },
                "content": {
                    "type": "boolean",
                    "description": "Search learning content chunks (default: false).",
                    "default": False,
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
            "06_Archives_Storage) and computes wasted cluster slack and top slack directories at the drive's real "
            "cluster size, as `smart-drive audit` does. The result's cluster_size_source is 'detected', or 'assumed' "
            "when the cluster size could not be read and the 512KB exFAT cluster was used instead. "
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
        "description": (
            "Identifies and purges system junk files (.DS_Store, Thumbs.db, temp caches). Enforces mandatory whitelist "
            "protection. nominal_bytes_reclaimed is what the junk holds and slack_bytes_reclaimed the space it takes on "
            "the drive, worked out at the drive's real cluster size (cluster_size; cluster_size_source is 'detected', "
            "or 'assumed' when it could not be read and the 512KB exFAT cluster was used)."
        ),
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
        "description": (
            "Detects identical duplicate files using a 3-phase cascade (size -> SHA-256 of the first 8KB and, above "
            "16KB, the last 8KB -> full SHA-256), without entering dependency or cache trees or folders Windows or "
            "installed apps depend on. Returns the nominal bytes the redundant copies hold (total_reclaimable_bytes) "
            "and the cluster slack they waste (total_reclaimable_slack) at the drive's real cluster size "
            "(cluster_size; cluster_size_source is 'detected', or 'assumed' when it could not be read and the 512KB "
            "exFAT cluster was used), with pagination controls."
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
        "description": (
            "Verifies whether a file path or operation complies with exFAT rules: checks for illegal Windows "
            "characters, symlink attempts, and whitelist. The storage root itself is reported protected "
            "(is_storage_root), and drive-relative Windows paths such as E:x, which Windows resolves against the "
            "drive's current folder and not the storage root, are refused."
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
        "description": (
            "Autonomous drive auto-zoning, anti-slack rebalancing, and organization. Classifies loose items into "
            "standard taxonomies, ensures shields, and syncs index. Moved folders stay usable by coding agents: the "
            "project and MCP server paths they remember are rewritten to the new places. Slack figures are worked out "
            "at the drive's real cluster size (cluster_size; cluster_size_source is 'detected', or 'assumed' when it "
            "could not be read and the 512KB exFAT cluster was used)."
        ),
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
    {
        "name": "ssd_gitignore",
        "description": (
            "Gitignore governance: audit and apply SmartDrive-managed .gitignore blocks per repo stack, "
            "keep personal learning data out of public repositories, and optionally untrack committed junk."
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
                "action": {
                    "type": "string",
                    "enum": ["audit", "apply"],
                    "description": "Action to perform: 'audit' (check compliance) or 'apply' (update .gitignore block).",
                },
                "repo": {
                    "type": "string",
                    "description": "Target repository path. For 'audit', defaults to all repositories on the drive if omitted.",
                },
                "apply": {
                    "type": "boolean",
                    "description": "If true, writes changes to disk. If false (default), runs in dry-run simulation mode.",
                },
                "untrack": {
                    "type": "boolean",
                    "description": "If true, untracks files matching managed patterns from git index (leaves working files untouched).",
                },
                "all": {
                    "type": "boolean",
                    "description": "If true, audit includes all repositories including third-party clones in detailed report (default: false).",
                },
                "owner": {
                    "type": "string",
                    "description": "Override git owner for repository classification (optional).",
                },
                "keep": {
                    "type": ["string", "array"],
                    "description": "Pattern or list of patterns of intentional files to keep (never untracked or counted as junk).",
                },
            },
            "required": ["action"],
        },
    },
]

# What MCP shows people instead of a tool's name (`title`). It is not in TOOLS, which is the 2024-11-05 catalog:
# `tools_for_version` adds it in the revisions that define it.
TOOL_TITLES: Dict[str, str] = {
    "ssd_search": "Search Drive Files",
    "ssd_audit": "Audit Storage and Cluster Slack",
    "ssd_clean": "Clean Junk Files",
    "ssd_find_duplicates": "Find Duplicate Files",
    "ssd_update_index": "Update Search Index",
    "ssd_check_safety": "Check Path Safety",
    "ssd_status": "Drive Status",
    "ssd_auto_organize": "Auto-Organize Drive",
    "ssd_gitignore": "Gitignore Governance",
}


def tools_for_version(version: str) -> List[Dict[str, Any]]:
    """The tool catalog `tools/list` answers under revision `version`; each revision only adds to the one before.

    2024-11-05 is TOOLS itself. 2025-03-26 adds the display `title` to each tool's `annotations`, 2025-06-18 also puts
    it at the top of the tool, right after the name. The hints stay at the top level too. Later revisions get copies,
    so TOOLS, which every 2024-11-05 reply is built from, is never changed.
    """
    if not _is_at_least(version, "2025-03-26"):
        return TOOLS
    with_top_level_title = _is_at_least(version, "2025-06-18")
    catalog: List[Dict[str, Any]] = []
    for tool in TOOLS:
        title = TOOL_TITLES[tool["name"]]
        entry: Dict[str, Any] = {}
        for key, value in tool.items():
            entry[key] = {"title": title, **value} if key == "annotations" else value
            if key == "name" and with_top_level_title:
                entry["title"] = title
        catalog.append(entry)
    return catalog


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
        "ssd_gitignore": "handle_ssd_gitignore",
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
        self._batch: Optional[List[Dict[str, Any]]] = None  # collects the replies of a JSON-RPC batch
        self.protocol_version: str = PROTOCOL_VERSION  # what `initialize` negotiated; the oldest revision until then
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
        - Accepts absolute paths inside the root: any drive-letter case, / or \\ separators, a drive-root or a folder root.
        - Rejects Windows drive letters escaping root (^[a-zA-Z]:), and drive-relative or drive-less paths (E:x, \\x).
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

        # Check for Windows drive letter (e.g. C:, D:)
        if re.match(r"^[a-zA-Z]:", sub_path_str):
            root_drive = canonical_root[:2].upper() if len(canonical_root) >= 2 and canonical_root[0].isalpha() and canonical_root[1] == ":" else ""
            path_drive = sub_path_str[:2].upper()
            if not root_drive or path_drive != root_drive:
                raise ValueError("Access denied: path escapes storage root")
            # "E:x" is relative to the drive's working directory, which is process state, not a place on the drive
            if clean_norm[2:3] != "/":
                raise ValueError("Access denied: " + _drive_relative_explanation(root_drive, sub_path_str))
            target = os.path.realpath(os.path.abspath(clean_norm))
        elif clean_norm.startswith("/"):
            # Windows reads a drive-less "\x" against the process' current drive, which says nothing about the root
            if sys.platform == "win32":
                raise ValueError("Access denied: path escapes storage root")
            target = os.path.realpath(os.path.abspath(clean_norm))
        else:
            target = os.path.realpath(os.path.abspath(os.path.join(canonical_root, clean_norm)))

        # Verify boundary containment on the resolved path, which is where `..` and links show what they point at
        if not _is_inside(canonical_root, target):
            raise ValueError("Access denied: path escapes storage root")

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

        if args.get("kind"):
            k_clean = str(args["kind"]).strip().lower()
            valid_kinds = {"slide", "lesson", "fact", "source", "module", "capture", "outline", "research", "error", "learning"}
            if k_clean in valid_kinds:
                params.kind = k_clean
            elif k_clean:
                params.warnings.append(f"Unknown kind filter {k_clean!r}")

        if "content" in args:
            params.content = self._parse_bool(args.get("content"), default=False)

        if args.get("directory"):
            raw_dir = str(args["directory"]).strip().strip('"').strip("'")
            safe_dir = self._resolve_safe_path(raw_dir)
            try:
                rel_dir = os.path.relpath(safe_dir, self.root)
                params.directory = "" if rel_dir == "." else rel_dir
            except ValueError:
                params.directory = safe_dir

        size_arg = args.get("size")
        if size_arg is not None and size_arg != "":  # not just truthy: a JSON number 0 means "exactly 0 bytes"
            apply_size_spec(params, str(size_arg))

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
        try:
            result = engine.search(params)
        finally:
            db.close()

        MAX_CHAR_BUDGET = 4800
        serialized_matches = []
        truncated = False

        for m in result.matches:
            if compact_mode:
                if m.kind is not None:
                    entry = {
                        "path": m.path,
                        "kind": m.kind,
                        "unit": m.unit_id,
                        "loc": m.locator,
                        "snip": m.snippet,
                    }
                else:
                    entry = {"path": m.path, "size": m.size, "cat": m.category}
            else:
                entry = m.to_dict()
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

        # Slack is worked out at the allocation unit of the folder audited, as `audit` does at its root: the volume's
        # own, 512 KB exFAT only when it cannot be read. The result says which, so that an agent can tell.
        cluster_size, cluster_size_source = slack_cluster(detect_filesystem(target_root))
        auditor = StorageAuditor(target_root, cluster_size=cluster_size)
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
        micro_hotspots = report.get("micro_file_hotspots", [])
        total_rec_slack = report.get("total_recoverable_slack", 0)
        learning_summary = report.get("learning_summary") or report.get("learning") or {}

        result = {
            "root_path": report.get("root_path", target_root),
            "cluster_size": cluster_size,
            "cluster_size_source": cluster_size_source,
            "filesystem": report.get("filesystem"),
            "slack_model_note": report.get("slack_model_note"),
            "total_files": report.get("total_files", 0),
            "total_directories": report.get("total_directories", 0),
            "total_logical_bytes": logical_bytes,
            "total_allocated_bytes": report.get("total_allocated_bytes", 0),
            "total_slack_bytes": slack_bytes,
            "total_slack_percentage": report.get("total_slack_percentage", 0.0),
            "taxonomies": report.get("taxonomies", {}),
            "categories": categories_out,
            "top_slack_directories": report.get("top_slack_directories", [])[:10],
            "micro_file_hotspots": micro_hotspots[:10] if compact_mode else micro_hotspots,
            "total_recoverable_slack": total_rec_slack,
            "learning": learning_summary,
            "learning_summary": learning_summary,
        }
        if compact_mode:
            result["compact"] = True
            result["total_logical_formatted"] = format_bytes(logical_bytes)
            result["total_slack_formatted"] = format_bytes(slack_bytes)
            result["total_recoverable_slack_formatted"] = format_bytes(total_rec_slack)

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

        # Sizes are worked out at the allocation unit of the folder cleaned, as ssd_audit does: the volume's own, 512 KB
        # exFAT only when it cannot be read. The result says which, so that an agent can tell.
        cluster_size, cluster_size_source = slack_cluster(detect_filesystem(target_root))

        # The detector works from the storage root and is told where to start, so that a sub-folder is judged by the
        # folders above it (an installed app, a dependency tree) as the scan of the whole drive judges it
        detector = JunkDetector(self._resolve_safe_path(None), max_tier=max_tier, cluster_size=cluster_size)
        junk_items = detector.find_junk(sub_path=target_root)

        guard = SecurityGuard(drive_root=self._ensure_root())
        purge_engine = PurgeEngine(guard, dry_run=dry_run, cluster_size=cluster_size)
        summary = purge_engine.purge_batch(junk_items, dry_run=dry_run)

        result = {
            "target_root": target_root,
            "dry_run": dry_run,
            "tier": max_tier.name,
            "detected_count": len(junk_items),
            "purged_count": summary.total_succeeded,
            "deleted_count": summary.deleted_count,
            "freed_bytes": summary.freed_bytes,
            "quarantined_count": summary.quarantined_count,
            "quarantined_bytes": summary.quarantined_bytes,
            "batch_id": summary.batch_id,
            "cluster_size": cluster_size,
            "cluster_size_source": cluster_size_source,
            "nominal_bytes_reclaimed": summary.nominal_bytes_reclaimed,
            "slack_bytes_reclaimed": summary.allocated_bytes_reclaimed,
            # What looked like junk and was kept (by reason), the folders the system depends on, and the folder that
            # kept the whole scan from starting (as in `clean --json`)
            "kept": detector.get_kept_summary(),
            "protected_by_system": detector.protected_by_system,
            "scan_skipped": _scan_skipped_note(target_root, detector.scan_skipped, "cleaning"),
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

        # drive_root: a sub-folder is judged by the folders above it, as the scan of the whole drive judges them.
        # min_size is the detector's, so that small files are dropped before anything is hashed. Slack is modelled at
        # the volume's real allocation unit (as `audit` does), 512 KB exFAT when it cannot be read.
        storage_root = self._resolve_safe_path(None)
        cluster_size, cluster_size_source = slack_cluster(detect_filesystem(storage_root))
        detector = DuplicateDetector(target_root, cluster_size=cluster_size, drive_root=storage_root, min_size=min_size)
        plan = detector.generate_reclamation_plan()
        all_groups = plan.get("duplicate_groups", [])

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
            "cluster_size": cluster_size,
            "cluster_size_source": cluster_size_source,
            "total_reclaimable_bytes": total_reclaimable_bytes,
            "total_reclaimable_slack": total_reclaimable_slack,
            "total_reclaimable_physical": total_reclaimable_physical,
            "has_more": has_more,
            "next_offset": next_offset,
            "duplicate_groups": capped_groups,
            "skipped_dependency_dirs": plan.get("skipped_dependency_dirs", 0),
            "skipped_protected_dirs": plan.get("skipped_protected_dirs", 0),
            "scan_skipped": _scan_skipped_note(target_root, plan.get("scan_skipped"), "deduplication"),
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
            return _refused_path_report(path_str, ["\\x00"], "Path contains null byte")

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
            return _refused_path_report(
                path_str, all_forbidden, "Path is on a different drive mount or escapes drive root (UNC path)"
            )

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
            return _refused_path_report(
                path_str, all_forbidden, "Path is on a different drive mount or escapes drive root"
            )

        # "E:x" is read against the current folder of drive E:, which is process state and not the storage root
        if path_drive and clean_path[2:3] not in ("\\", "/"):
            return _refused_path_report(path_str, all_forbidden, _drive_relative_explanation(root_drive, clean_path))

        if norm_sub.startswith("/"):
            # Absolute: the drive letter dropped from norm_sub for the segment audit is part of where it points
            raw_target = clean_path.replace("\\", "/")
            if sys.platform == "win32" and not path_drive:
                # Windows reads a drive-less "\x" against the process' current drive, which says nothing about the root
                escapes_root = True
                normalized_target = canonical_root
            else:
                normalized_target = os.path.realpath(os.path.abspath(raw_target))
                escapes_root = not _is_inside(canonical_root, normalized_target)
        else:
            raw_target = os.path.join(canonical_root, norm_sub)
            normalized_target = os.path.realpath(os.path.abspath(raw_target))
            escapes_root = not _is_inside(canonical_root, normalized_target)

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

        # The storage root itself is the one folder nothing may delete or move: reported protected, like the taxonomies
        is_storage_root = not escapes_root and os.path.normcase(normalized_target) == os.path.normcase(canonical_root)
        is_prot_file = is_protected_root_file(rel_path)
        is_prot_dir = is_storage_root or is_protected_root_dir(rel_path)
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
            "is_storage_root": is_storage_root,
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
                detector = JunkDetector(root, max_tier=JunkTier.TIER_1_SAFE, cluster_size=zoner.cluster_size)
                junk_items = detector.find_junk()
                guard = SecurityGuard(drive_root=root)
                purge_engine = PurgeEngine(guard, dry_run=not apply_mode, cluster_size=zoner.cluster_size)
                purge_engine.purge_batch(junk_items, dry_run=not apply_mode)
            except Exception as e:
                logger.warning("Error running clean in auto_organize: %s", e)
        if apply_mode:
            created_shields = ensure_anti_indexing_markers(root)
            res = zoner.apply_plan(plan)
            relinked = after_moves(root, [(m["src"], m["dest"]) for m in res["moved"]], "organize")
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
                "cluster_size": zoner.cluster_size,
                "cluster_size_source": zoner.cluster_size_source,
                "result": res,
                "protected_by_system": zoner.protected_by_system,
                "relink": relinked.to_dict() if relinked else None,
                "shields_created": created_shields,
                "index_sync": dataclasses.asdict(inc_stats),
            }
        preview = relink([(a.src_path, a.dest_path) for a in plan], root=root, dry_run=True) if plan else None
        return {
            "status": "dry_run",
            "cluster_size": zoner.cluster_size,
            "cluster_size_source": zoner.cluster_size_source,
            "plan_count": len(plan),
            "actions": [a.to_dict() for a in plan],
            "protected_by_system": zoner.protected_by_system,
            "relink_preview": preview.to_dict() if preview else None,
        }

    def handle_ssd_gitignore(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from smart_drive.core.gitignore import (
            apply_repo,
            audit_all,
            audit_repo,
        )

        action = str(args.get("action", "audit")).strip().lower()
        repo = args.get("repo")
        show_all = self._parse_bool(args.get("all"), default=False)
        owner_override = args.get("owner")
        raw_keep = args.get("keep")
        if isinstance(raw_keep, str):
            keep_patterns = [raw_keep]
        elif isinstance(raw_keep, list):
            keep_patterns = [str(k) for k in raw_keep]
        else:
            keep_patterns = []

        if repo:
            repo_path = self._resolve_safe_path(repo, must_exist=True)
        else:
            repo_path = None

        if action == "audit":
            if repo_path and os.path.exists(os.path.join(repo_path, ".git")):
                results = [
                    audit_repo(
                        repo_path,
                        configured_owner=owner_override,
                        include_third_party_junk=show_all,
                        keep_patterns=keep_patterns,
                    )
                ]
            else:
                root = repo_path or self._ensure_root()
                results = audit_all(
                    root,
                    configured_owner=owner_override,
                    include_third_party_junk=show_all,
                    keep_patterns=keep_patterns,
                )

            offenders = sorted(
                [r for r in results if r.tracked_junk_count > 0 or r.tracked_secrets or r.personal_data_at_risk],
                key=lambda r: (len(r.tracked_secrets), r.tracked_junk_count, len(r.tracked_personal_data)),
                reverse=True,
            )

            return {
                "summary": {
                    "repos_audited": len(results),
                    "own_repos": sum(1 for r in results if r.owner_class == "own"),
                    "local_repos": sum(1 for r in results if r.owner_class == "local"),
                    "third_party_repos": sum(1 for r in results if r.owner_class == "third-party"),
                    "compliant_repos": sum(
                        1 for r in results
                        if not r.missing_patterns and not r.tracked_junk_count and not r.personal_data_at_risk and not r.tracked_secrets and r.visibility != "missing"
                    ),
                    "repos_with_junk": sum(1 for r in results if r.tracked_junk_count > 0),
                    "repos_with_secrets": sum(1 for r in results if r.tracked_secrets),
                    "repos_with_personal_data_at_risk": sum(1 for r in results if r.personal_data_at_risk),
                    "remote_missing": sum(1 for r in results if r.visibility == "missing"),
                    "top_offenders": [
                        {
                            "repo": r.repo,
                            "tracked_junk_count": r.tracked_junk_count,
                            "tracked_secrets_count": len(r.tracked_secrets),
                            "owner_class": r.owner_class,
                        }
                        for r in offenders[:5]
                    ],
                },
                "repos": [
                    {
                        "repo": r.repo,
                        "owner_class": r.owner_class,
                        "visibility": r.visibility,
                        "has_gitignore": r.has_gitignore,
                        "has_managed_block": r.has_managed_block,
                        "stacks": r.stacks,
                        "missing_patterns_count": len(r.missing_patterns),
                        "tracked_junk_count": r.tracked_junk_count,
                        "tracked_junk_top": r.tracked_junk_files[:5],
                        "tracked_personal_data": r.tracked_personal_data,
                        "personal_data_at_risk": r.personal_data_at_risk,
                        "tracked_secrets": r.tracked_secrets,
                        "intentional_files": r.intentional_files,
                        "omitted_patterns": r.omitted_patterns,
                        "remote": r.remote,
                        "is_possibly_public": r.is_possibly_public,
                    }
                    for r in results
                ],
            }
        elif action == "apply":
            if not repo_path:
                raise ValueError("Parameter 'repo' is required for action 'apply'")
            apply_mode = self._parse_bool(args.get("apply"), default=False)
            untrack_mode = self._parse_bool(args.get("untrack"), default=False)
            res = apply_repo(repo_path, dry_run=not apply_mode, untrack=untrack_mode, keep_patterns=keep_patterns)
            diff_lines = res.diff.splitlines() if res.diff else []
            return {
                "repo": res.repo,
                "dry_run": res.dry_run,
                "applied": res.applied,
                "created": res.created,
                "stacks": res.stacks,
                "managed_patterns_count": res.managed_patterns_count,
                "untracked_count": len(res.untracked_files),
                "untracked_files": res.untracked_files[:20],
                "intentional_files": res.intentional_files,
                "omitted_patterns": res.omitted_patterns,
                "diff_lines_count": len(diff_lines),
                "diff_preview": diff_lines[:15],
            }
        else:
            raise ValueError(f"Unknown gitignore action: '{action}'. Expected 'audit' or 'apply'.")

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
        elif name == "ssd_gitignore":
            return self.handle_ssd_gitignore(args)
        else:
            raise ValueError(f"Unknown tool '{name}'")

    def send_response(self, response: Any) -> None:
        if self._batch is not None:  # inside a JSON-RPC batch the replies leave together, as one array
            self._batch.append(response)
            return
        self._write(response)

    def _send_result(self, msg_id: Any, result: Dict[str, Any], modern_version: Optional[str]) -> Dict[str, Any]:
        """Sends a JSON-RPC result and returns the response that was sent.

        A 2026-07-28 request (`modern_version` is its revision) gets a result that also says it is complete and which
        server answered. Error responses are never decorated, so they are not sent through here.
        """
        if modern_version is not None:
            result = {"resultType": "complete", **result, "_meta": {META_SERVER_INFO: server_info(modern_version)}}
        resp = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        self.send_response(resp)
        return resp

    def _write(self, payload: Any) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        try:
            body.encode("utf-8")
        except UnicodeEncodeError:
            # A lone surrogate the client itself sent (a path like "\udcff.txt") cannot be written as UTF-8;
            # the escaped form is plain ASCII, still valid JSON, and the reply gets through.
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        out = self._raw_stdout if self._raw_stdout is not None else sys.stdout
        if self.use_content_length_mode:
            out.write(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}")
        else:
            out.write(body + "\n")
        out.flush()

    def process_message(self, message: Any) -> None:
        """Handles one decoded JSON-RPC message: a request or notification, or a batch (array) of them."""
        if not isinstance(message, list):
            self._process_one(message)
            return
        # Before the other checks, and whatever the batch holds, so that none of its requests runs: 2025-06-18 and
        # later have no batches, and a 2026-07-28 request is never part of one.
        if self.protocol_version not in BATCH_PROTOCOL_VERSIONS or any(_is_modern_request(item) for item in message):
            self.send_response({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": (
                        "Invalid Request: MCP 2025-06-18 and later have no JSON-RPC batches; "
                        "send each request as a message of its own"
                    ),
                },
            })
            return
        if not message:
            self.send_response({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: empty batch"},
            })
            return
        if len(message) > MAX_BATCH_SIZE:  # the replies are held until the whole batch is done
            self.send_response({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": f"Invalid Request: batch of {len(message)} exceeds the limit of {MAX_BATCH_SIZE}",
                },
            })
            return
        self._batch = []
        try:
            for item in message:
                self._process_one(item)
            replies, self._batch = self._batch, None
        finally:
            self._batch = None
        if replies:  # a batch made only of notifications gets no reply at all
            self._write(replies)

    def _process_one(self, message: Any) -> None:
        if not isinstance(message, dict):
            self.send_response({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected a JSON object"},
            })
            return
        try:
            self.handle_request(message)
        except Exception as e:
            # A bug on our side must not leave the client waiting for an answer that never comes.
            logger.exception("Unhandled error while handling %r: %s", message.get("method"), e)
            if message.get("id") is not None:
                self.send_response({
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32603, "message": "Internal error"},
                })

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

        # MCP 2026-07-28 has no handshake: each request names its revision in params._meta and is answered under it,
        # never under (or changing) what `initialize` negotiated for the older revisions.
        modern_version: Optional[str] = None
        if _is_modern_request(req):
            if msg_id is None:  # a notification gets no answer, not even an error
                return None
            meta = params["_meta"]
            envelope_error = _modern_envelope_error(meta)
            if envelope_error is not None:
                err_resp = {"jsonrpc": "2.0", "id": msg_id, "error": envelope_error}
                self.send_response(err_resp)
                return err_resp
            modern_version = meta[META_PROTOCOL_VERSION]
        version = modern_version if modern_version is not None else self.protocol_version

        if method == "initialize":
            self.protocol_version = negotiate_protocol_version(params.get("protocolVersion"))
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": self.protocol_version,
                    "serverInfo": server_info(self.protocol_version),
                    "capabilities": {
                        "tools": {},
                    },
                    "instructions": SERVER_INSTRUCTIONS,
                },
            }
            self.send_response(resp)
            return resp

        if method == "auth/handshake":
            if not self.require_auth and not self.auth_token:
                return self._send_result(msg_id, {"status": "authenticated", "authenticated": True}, modern_version)

            if self.verify_token(inline_token):
                self._authenticated = True
                return self._send_result(msg_id, {"status": "authenticated", "authenticated": True}, modern_version)
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

        if method == "ping" and modern_version is None:  # 2026-07-28 dropped it: there it is an unknown method
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
            self.send_response(resp)
            return resp

        if method == "notifications/initialized":
            return None

        if method == "server/discover":  # like initialize and ping, it must work before any token is shown
            if modern_version is None:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32602,
                        "message": (
                            f"Invalid params: server/discover needs params._meta with '{META_PROTOCOL_VERSION}' "
                            f"and '{META_CLIENT_CAPABILITIES}'"
                        ),
                    },
                }
                if msg_id is not None:
                    self.send_response(err_resp)
                return err_resp
            return self._send_result(
                msg_id,
                {
                    "supportedVersions": list(MODERN_PROTOCOL_VERSIONS),
                    "capabilities": {"tools": {}},
                    "instructions": SERVER_INSTRUCTIONS,
                    "ttlMs": CACHE_TTL_MS,
                    "cacheScope": "public",
                },
                modern_version,
            )

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
            listing: Dict[str, Any] = {"tools": tools_for_version(version)}
            if modern_version is not None:
                listing.update({"ttlMs": CACHE_TTL_MS, "cacheScope": "public"})
            return self._send_result(msg_id, listing, modern_version)

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
                result_text = json.dumps(res_data, separators=(",", ":"), ensure_ascii=False)
            except Exception as e:
                if _is_client_error(e):
                    logger.warning("Tool %s refused the request: %s", tool_name, e)
                else:
                    logger.exception("Error executing tool %s: %s", tool_name, e)
                return self._send_result(
                    msg_id,
                    {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error executing tool '{tool_name}': {str(e)}",
                            }
                        ],
                    },
                    modern_version,
                )

            # Outside the try on purpose: a failure to write the reply is a server problem, not a tool refusal.
            result: Dict[str, Any] = {"content": [{"type": "text", "text": result_text}]}
            if _is_at_least(version, "2025-06-18"):
                result["structuredContent"] = res_data  # the same JSON as the text, as the object it came from
            return self._send_result(msg_id, result, modern_version)

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
                        self.process_message(req)
                    elif line_clean.startswith(("{", "[")):
                        try:
                            req = json.loads(line_clean)
                        except Exception as json_err:
                            self.send_response({
                                "jsonrpc": "2.0",
                                "id": None,
                                "error": {"code": -32700, "message": f"Parse error: {json_err}"},
                            })
                            continue
                        self.process_message(req)
                except Exception as e:
                    logger.error("Error in run_stdio: %s", e)
        finally:
            sys.stdout = orig_stdout
            self._raw_stdout = None


__all__ = [
    "BATCH_PROTOCOL_VERSIONS",
    "CACHE_TTL_MS",
    "LATEST_PROTOCOL_VERSION",
    "LEGACY_PROTOCOL_VERSIONS",
    "MAX_BATCH_SIZE",
    "META_CLIENT_CAPABILITIES",
    "META_PROTOCOL_VERSION",
    "META_SERVER_INFO",
    "MODERN_PROTOCOL_VERSIONS",
    "PROTOCOL_VERSION",
    "SERVER_DESCRIPTION",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "SERVER_TITLE",
    "SERVER_VERSION",
    "SERVER_WEBSITE_URL",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "SlidingWindowRateLimiter",
    "SmartDriveMCPServer",
    "TOOLS",
    "TOOL_TITLES",
    "UNSUPPORTED_PROTOCOL_VERSION_CODE",
    "negotiate_protocol_version",
    "server_info",
    "tools_for_version",
]
