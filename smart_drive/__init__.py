"""SmartDrive-OS (`smart_drive`) - Autonomous exFAT SSD Governance & SQLite FTS5 Instant Search Suite.

Zero-dependency Python package (100% standard library) tailored for external SSDs
(exFAT 512KB cluster allocation) and AI Coding Agents (Google Antigravity, Claude, Cursor, Windsurf).
"""
from __future__ import annotations

__version__ = "1.0.0"
__author__ = "SmartDrive Team"

from smart_drive.core.config import (
    CLUSTER_SIZE,
    CLUSTER_SIZE_BYTES,
    CLUSTER_SIZE_KB,
    calculate_allocated_bytes,
    calculate_cluster_count,
    calculate_slack_bytes,
    calculate_slack_percentage,
    PROTECTED_ROOT_DIRS,
    TAXONOMY_ROOT_DIRS,
    PROTECTED_CORE_TAXONOMIES,
    PROTECTED_ROOT_FILES,
    ANTI_INDEXING_MARKERS,
    CATEGORIES,
    JunkTier,
    JunkRule,
    JUNK_RULES,
    classify_by_extension,
    classify_file_entry,
)
from smart_drive.core.exfat_compat import (
    ExFatEngine,
    ExFatCompatError,
    SymlinkNotPermittedError,
    normalize_rel_path,
    audit_forbidden_characters,
    sanitize_filename,
    detect_drive_root,
)
from smart_drive.core.scanner import FastDirectoryScanner, ScanEntry
from smart_drive.core.auditor import StorageAuditor, AuditReport
from smart_drive.core.junk_detector import JunkDetector, JunkItem
from smart_drive.core.purge_engine import PurgeEngine, SecurityGuard
from smart_drive.core.duplicates import DuplicateDetector
from smart_drive.core.auto_zoner import AutoZoner
from smart_drive.core.sentinel import SentinelEngine
from smart_drive.core.initializer import DriveInitializer
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.search.engine import SearchEngine, SearchMatch, SearchResult
from smart_drive.search.parser import SearchParams, parse_search_query
from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.mcp.registrar import register_ide_configs
from smart_drive.mcp.server import SmartDriveMCPServer
from smart_drive.cli.main import main

__all__ = [
    "__version__",
    "__author__",
    # Cluster geometry math
    "CLUSTER_SIZE",
    "CLUSTER_SIZE_BYTES",
    "CLUSTER_SIZE_KB",
    "calculate_allocated_bytes",
    "calculate_cluster_count",
    "calculate_slack_bytes",
    "calculate_slack_percentage",
    # Constants & Taxonomies
    "PROTECTED_ROOT_DIRS",
    "TAXONOMY_ROOT_DIRS",
    "PROTECTED_CORE_TAXONOMIES",
    "PROTECTED_ROOT_FILES",
    "ANTI_INDEXING_MARKERS",
    "CATEGORIES",
    "classify_by_extension",
    "classify_file_entry",
    # exFAT Compatibility
    "ExFatEngine",
    "ExFatCompatError",
    "SymlinkNotPermittedError",
    "normalize_rel_path",
    "audit_forbidden_characters",
    "sanitize_filename",
    "detect_drive_root",
    # Core domain engines
    "FastDirectoryScanner",
    "ScanEntry",
    "StorageAuditor",
    "AuditReport",
    "JunkDetector",
    "JunkItem",
    "JunkTier",
    "JunkRule",
    "JUNK_RULES",
    "PurgeEngine",
    "SecurityGuard",
    "DuplicateDetector",
    "AutoZoner",
    "SentinelEngine",
    "DriveInitializer",
    # Indexer & Search
    "DatabaseManager",
    "IndexManager",
    "SearchEngine",
    "SearchMatch",
    "SearchResult",
    "SearchParams",
    "parse_search_query",
    # MCP
    "SmartDriveMCPServer",
    "SmartDriveProxy",
    "register_ide_configs",
    # CLI
    "main",
]
