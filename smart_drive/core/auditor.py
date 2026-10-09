"""smart_drive.core.auditor - Storage Audit, Taxonomy Breakdown & 512KB Cluster Slack Calculator.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Implements:
1. Storage breakdown by taxonomy (01_AI_Models .. 06_Archives_Storage, workspaces, and system).
2. Extension & MIME classification (7 categories + Other) utilizing config.py constants and core/scanner.py streams.
3. 512KB cluster slack metrics (nominal size vs allocated physical size).
4. Directory tree aggregation (DirectoryNode) tracking direct and recursive metrics.
5. Multi-format reporting (ASCII table, JSON, Markdown).
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

from smart_drive.core.config import (
    CLUSTER_SIZE_BYTES,
    CLUSTER_SIZE,
    calculate_allocated_bytes,
    calculate_slack_bytes,
    calculate_slack_percentage,
    CATEGORIES,
    EXT_TO_CATEGORY,
    classify_by_extension,
    classify_file_entry,
    TAXONOMY_ROOT_DIRS,
    PROTECTED_ROOT_DIRS,
    DEFAULT_EXCLUDE_DIRS,
)
from smart_drive.core.scanner import FastDirectoryScanner, ScanEntry, ScanOptions, ScanStats
from smart_drive.core.exfat_compat import ExFatEngine, normalize_rel_path
from smart_drive.core.fsinfo import detect_filesystem, slack_model_note

logger = logging.getLogger("smart_drive.core.auditor")


# ==============================================================================
# 1. TAXONOMY SPECIFICATIONS & CANONICAL GROUPINGS
# ==============================================================================

# Standard canonical taxonomy names on Kingston XS2000
CANONICAL_TAXONOMIES: Tuple[str, ...] = (
    "01_AI_Models",
    "02_Learning_Knowledge",
    "03_Personal_Documents",
    "04_Creative_Assets",
    "05_Dev_Toolbox",
    "06_Archives_Storage",
)

# Known workspace root folders
WORKSPACE_ROOT_DIRS: FrozenSet[str] = frozenset({
    "smart_ssd_workspace",
    "teamwork_projects",
    "python_master_final",
})

# Known system root folders
SYSTEM_ROOT_DIRS: FrozenSet[str] = frozenset({
    "system volume information",
    "$recycle.bin",
    ".fseventsd",
    ".spotlight-v100",
    ".trashes",
    ".temporaryitems",
    ".agents",
})


# ==============================================================================
# 2. FORMATTING HELPERS
# ==============================================================================

def format_bytes(byte_count: int, precision: int = 2) -> str:
    """Formats byte counts into human-readable strings (e.g. 512.00 KB, 1.40 GB)."""
    if byte_count < 0:
        return f"-{format_bytes(-byte_count, precision)}"
    if byte_count < 1024:
        return f"{byte_count} B"
    units = ("KB", "MB", "GB", "TB", "PB")
    val = float(byte_count)
    for unit in units:
        val /= 1024.0
        if val < 1024.0 or unit == units[-1]:
            return f"{val:.{precision}f} {unit}"
    return f"{byte_count} B"


def format_count(count: int) -> str:
    """Formats integer counts with thousands separators."""
    return f"{count:,}"


def format_percentage(pct: float, precision: int = 1) -> str:
    """Formats percentage values with a percent sign."""
    return f"{pct:.{precision}f}%"


# ==============================================================================
# 3. DATA MODELS
# ==============================================================================

@dataclass
class CategoryStats:
    """Statistical summary for an extension/MIME functional category."""
    name: str
    file_count: int = 0
    nominal_bytes: int = 0
    allocated_bytes: int = 0
    extensions: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    extension_bytes: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def logical_bytes(self) -> int:
        """Alias for nominal_bytes."""
        return self.nominal_bytes

    @property
    def slack_bytes(self) -> int:
        """Wasted slack bytes in this category."""
        return self.allocated_bytes - self.nominal_bytes

    @property
    def slack_ratio(self) -> float:
        """Ratio of slack bytes to allocated bytes (0.0 - 1.0)."""
        return (self.slack_bytes / self.allocated_bytes) if self.allocated_bytes > 0 else 0.0

    @property
    def slack_percentage(self) -> float:
        """Percentage of allocated bytes lost to slack (0.0 - 100.0)."""
        return self.slack_ratio * 100.0

    def record_file(self, ext: str, nominal_size: int, allocated_size: int) -> None:
        """Accumulates a file entry into category metrics."""
        self.file_count += 1
        self.nominal_bytes += nominal_size
        self.allocated_bytes += allocated_size
        clean_ext = ext.lower() if ext else "(no ext)"
        self.extensions[clean_ext] += 1
        self.extension_bytes[clean_ext] += nominal_size

    def to_dict(self) -> Dict[str, Any]:
        """Serializes category stats to a dictionary."""
        return {
            "name": self.name,
            "file_count": self.file_count,
            "nominal_bytes": self.nominal_bytes,
            "logical_bytes": self.nominal_bytes,
            "allocated_bytes": self.allocated_bytes,
            "slack_bytes": self.slack_bytes,
            "slack_ratio": self.slack_ratio,
            "slack_percentage": self.slack_percentage,
            "extensions": dict(self.extensions),
            "extension_bytes": dict(self.extension_bytes),
        }


@dataclass
class TaxonomyStats:
    """Statistical summary for a root storage taxonomy (e.g. 01_AI_Models)."""
    name: str
    file_count: int = 0
    dir_count: int = 0
    nominal_bytes: int = 0
    allocated_bytes: int = 0

    @property
    def logical_bytes(self) -> int:
        """Alias for nominal_bytes."""
        return self.nominal_bytes

    @property
    def slack_bytes(self) -> int:
        """Wasted slack bytes in this taxonomy."""
        return self.allocated_bytes - self.nominal_bytes

    @property
    def slack_ratio(self) -> float:
        """Ratio of slack bytes to allocated bytes (0.0 - 1.0)."""
        return (self.slack_bytes / self.allocated_bytes) if self.allocated_bytes > 0 else 0.0

    @property
    def slack_percentage(self) -> float:
        """Percentage of allocated bytes lost to slack (0.0 - 100.0)."""
        return self.slack_ratio * 100.0

    def record_file(self, nominal_size: int, allocated_size: int) -> None:
        """Accumulates a file entry into taxonomy metrics."""
        self.file_count += 1
        self.nominal_bytes += nominal_size
        self.allocated_bytes += allocated_size

    def record_dir(self) -> None:
        """Increments directory counter."""
        self.dir_count += 1

    def to_dict(self) -> Dict[str, Any]:
        """Serializes taxonomy stats to a dictionary."""
        return {
            "name": self.name,
            "file_count": self.file_count,
            "dir_count": self.dir_count,
            "nominal_bytes": self.nominal_bytes,
            "logical_bytes": self.nominal_bytes,
            "allocated_bytes": self.allocated_bytes,
            "slack_bytes": self.slack_bytes,
            "slack_ratio": self.slack_ratio,
            "slack_percentage": self.slack_percentage,
        }


@dataclass
class DirectoryNode:
    """Hierarchical directory node tracking direct and recursive metrics."""
    path: str
    rel_path: str
    name: str
    # Direct metrics: files residing directly in this folder
    direct_files: int = 0
    direct_bytes: int = 0
    direct_allocated: int = 0
    # Recursive metrics: rollup of this directory and all descendant subtrees
    recursive_files: int = 0
    recursive_bytes: int = 0
    recursive_allocated: int = 0
    recursive_dirs: int = 0
    subdirs: Dict[str, DirectoryNode] = field(default_factory=dict)

    @property
    def direct_slack(self) -> int:
        """Direct wasted cluster slack bytes."""
        return self.direct_allocated - self.direct_bytes

    @property
    def direct_slack_percentage(self) -> float:
        """Direct slack percentage."""
        return (self.direct_slack / self.direct_allocated * 100.0) if self.direct_allocated > 0 else 0.0

    @property
    def recursive_slack(self) -> int:
        """Recursive wasted cluster slack bytes across entire subtree."""
        return self.recursive_allocated - self.recursive_bytes

    @property
    def recursive_slack_percentage(self) -> float:
        """Recursive slack percentage across entire subtree."""
        return (self.recursive_slack / self.recursive_allocated * 100.0) if self.recursive_allocated > 0 else 0.0

    def add_file(self, size: int, allocated: int) -> None:
        """Adds a direct file's metrics to this directory node."""
        self.direct_files += 1
        self.direct_bytes += size
        self.direct_allocated += allocated

    def get_or_create_child(self, child_name: str, child_path: str, child_rel: str) -> DirectoryNode:
        """Finds or instantiates an immediate child directory node."""
        if child_name not in self.subdirs:
            self.subdirs[child_name] = DirectoryNode(
                path=child_path,
                rel_path=child_rel,
                name=child_name,
            )
        return self.subdirs[child_name]

    def rollup(self) -> Tuple[int, int, int, int]:
        """Recursively rolls up file counts and byte sizes from leaf to root.

        Returns:
            Tuple: (recursive_files, recursive_bytes, recursive_allocated, recursive_dirs)
        """
        r_files = self.direct_files
        r_bytes = self.direct_bytes
        r_alloc = self.direct_allocated
        r_dirs = len(self.subdirs)

        for child in self.subdirs.values():
            c_files, c_bytes, c_alloc, c_dirs = child.rollup()
            r_files += c_files
            r_bytes += c_bytes
            r_alloc += c_alloc
            r_dirs += c_dirs

        self.recursive_files = r_files
        self.recursive_bytes = r_bytes
        self.recursive_allocated = r_alloc
        self.recursive_dirs = r_dirs

        return r_files, r_bytes, r_alloc, r_dirs

    def find_node(self, target_rel_path: str) -> Optional[DirectoryNode]:
        """Finds a descendant node matching the given relative path."""
        norm_target = target_rel_path.strip().replace("\\", "/").strip("/")
        if not norm_target or norm_target == self.rel_path:
            return self
        parts = norm_target.split("/")
        curr: DirectoryNode = self
        for part in parts:
            if part in curr.subdirs:
                curr = curr.subdirs[part]
            else:
                return None
        return curr

    def flatten(self) -> List[DirectoryNode]:
        """Returns a flat list of all nodes in this subtree (pre-order)."""
        nodes: List[DirectoryNode] = [self]
        for child in self.subdirs.values():
            nodes.extend(child.flatten())
        return nodes

    def get_top_slack_dirs(self, limit: int = 10, min_files: int = 1) -> List[DirectoryNode]:
        """Finds subdirectories with the highest recursive cluster slack."""
        all_nodes = [n for n in self.flatten() if n.recursive_files >= min_files]
        all_nodes.sort(key=lambda n: n.recursive_slack, reverse=True)
        return all_nodes[:limit]

    def get_top_size_dirs(self, limit: int = 10) -> List[DirectoryNode]:
        """Finds subdirectories with the highest recursive nominal size."""
        all_nodes = self.flatten()
        all_nodes.sort(key=lambda n: n.recursive_bytes, reverse=True)
        return all_nodes[:limit]

    def to_dict(self, max_depth: Optional[int] = None, current_depth: int = 0) -> Dict[str, Any]:
        """Serializes node to dictionary, optionally limiting subtree depth."""
        data: Dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "rel_path": self.rel_path,
            "direct_files": self.direct_files,
            "direct_bytes": self.direct_bytes,
            "direct_allocated": self.direct_allocated,
            "direct_slack": self.direct_slack,
            "direct_slack_percentage": self.direct_slack_percentage,
            "recursive_files": self.recursive_files,
            "recursive_bytes": self.recursive_bytes,
            "recursive_allocated": self.recursive_allocated,
            "recursive_slack": self.recursive_slack,
            "recursive_slack_percentage": self.recursive_slack_percentage,
            "recursive_dirs": self.recursive_dirs,
        }
        if max_depth is None or current_depth < max_depth:
            data["subdirs"] = {
                k: v.to_dict(max_depth=max_depth, current_depth=current_depth + 1)
                for k, v in sorted(self.subdirs.items())
            }
        else:
            data["subdirs"] = {}
        return data


# ==============================================================================
# 4. AUDIT RESULT WRAPPER (DICT SUBCLASS)
# ==============================================================================

class AuditResult(dict):
    """Dictionary subclass containing complete storage audit results with multi-format reporters.

    Provides direct subscripting/key access (e.g. result['taxonomies']) for 100%
    compatibility with standard dictionaries and json.dumps(), while exposing
    rich reporting methods: to_json(), to_markdown(), to_ascii_table(), and export_*().
    """

    def __init__(self, data: Dict[str, Any], auditor: Optional[StorageAuditor] = None) -> None:
        super().__init__(data)
        self.auditor = auditor

    def to_json(self, indent: int = 2) -> str:
        """Serializes audit report to formatted JSON string."""
        return json.dumps(self, indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Generates a comprehensive Markdown report."""
        if self.auditor:
            return self.auditor.generate_markdown_report(self)
        return _fallback_markdown_report(self)

    def to_ascii_table(self) -> str:
        """Generates a terminal-formatted ASCII table report."""
        if self.auditor:
            return self.auditor.generate_ascii_table_report(self)
        return _fallback_ascii_table_report(self)

    def export_json(self, filepath: Union[str, Path], indent: int = 2) -> None:
        """Exports JSON report to disk."""
        target = Path(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(indent=indent), encoding="utf-8")

    def export_markdown(self, filepath: Union[str, Path]) -> None:
        """Exports Markdown report to disk."""
        target = Path(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_markdown(), encoding="utf-8")

    def export_text(self, filepath: Union[str, Path]) -> None:
        """Exports ASCII table text report to disk."""
        target = Path(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_ascii_table(), encoding="utf-8")


AuditReport = AuditResult


# ==============================================================================
# 5. STORAGE AUDITOR ENGINE
# ==============================================================================

class StorageAuditor:
    """Comprehensive storage auditor and 512KB cluster slack calculator.

    Traverses drives via `FastDirectoryScanner.scan_iter()`, classifying files into
    the 6 business taxonomies + workspaces + system, categorizing extensions into
    7 functional groups + Other, computing exact 512KB cluster allocations,
    and building an aggregated directory tree (`DirectoryNode`).
    """

    def __init__(
        self,
        root_path: str,
        cluster_size: int = CLUSTER_SIZE_BYTES,
        exclude_dirs: Optional[Set[str]] = None,
        max_depth: Optional[int] = None,
        scanner: Optional[FastDirectoryScanner] = None,
    ) -> None:
        self.root_path = os.path.abspath(root_path)
        self.cluster_size = cluster_size
        self.exclude_dirs = exclude_dirs
        self.max_depth = max_depth
        self.scanner = scanner

        # Pre-populate canonical taxonomy mappings
        self._taxonomy_lookup: Dict[str, str] = {
            t.lower(): t for t in CANONICAL_TAXONOMIES
        }

    def audit(self, root_dir: Optional[str] = None, scan_entries: Optional[List[ScanEntry]] = None) -> AuditResult:
        """Interface method compatible with PROJECT.md contract."""
        if root_dir:
            self.root_path = os.path.abspath(root_dir)
        return self.run_audit()

    def classify_taxonomy(self, rel_path: str) -> str:
        """Determines the storage taxonomy for a given relative path."""
        norm_rel = rel_path.strip().replace("\\", "/").lstrip("/")
        if not norm_rel:
            return "System"

        parts = norm_rel.split("/")
        top_segment = parts[0]
        top_lower = top_segment.lower()

        # Check canonical 6 taxonomies
        if top_lower in self._taxonomy_lookup:
            return self._taxonomy_lookup[top_lower]

        # Check workspace folders
        if top_lower in WORKSPACE_ROOT_DIRS or top_lower.endswith("_workspace") or top_lower.endswith("_projects"):
            return "Workspaces"

        # Check system folders
        if top_lower in SYSTEM_ROOT_DIRS:
            return "System"

        # If it's a file located directly at root
        if len(parts) == 1:
            return "System"

        return "Other"

    def classify_category(self, name: str, ext: str, rel_path: str = "") -> str:
        """Classifies a file entry into one of 7 functional categories or 'Other'."""
        return classify_file_entry(name, ext, rel_path)

    def calculate_allocation(self, size: int) -> int:
        """Calculates physical cluster allocation on exFAT for a given size."""
        return calculate_allocated_bytes(size, self.cluster_size)

    def calculate_slack(self, size: int) -> int:
        """Calculates wasted cluster slack in bytes."""
        return calculate_slack_bytes(size, self.cluster_size)

    def run_audit(self) -> AuditResult:
        """Executes full drive audit, streaming files through classification pipelines."""
        t0 = time.perf_counter()

        # Initialize taxonomies dictionary with all canonical groups
        taxonomies: Dict[str, TaxonomyStats] = {
            t: TaxonomyStats(name=t) for t in CANONICAL_TAXONOMIES
        }
        taxonomies["Workspaces"] = TaxonomyStats(name="Workspaces")
        taxonomies["System"] = TaxonomyStats(name="System")
        taxonomies["Other"] = TaxonomyStats(name="Other")

        # Initialize categories dictionary with 7 categories + Other
        category_names = list(CATEGORIES.keys()) if CATEGORIES else [
            "AI Models", "Code", "Books/Learning", "Docs", "Media", "Archives", "System Junk"
        ]
        categories: Dict[str, CategoryStats] = {
            c: CategoryStats(name=c) for c in category_names
        }
        categories["Other"] = CategoryStats(name="Other")

        # Root directory node
        root_name = os.path.basename(self.root_path) or self.root_path
        root_node = DirectoryNode(path=self.root_path, rel_path="", name=root_name)

        # Directory node index for quick parent lookups
        dir_nodes: Dict[str, DirectoryNode] = {"": root_node}

        # Track drive-wide summary accumulators
        total_files = 0
        total_directories = 0
        total_nominal_bytes = 0
        total_allocated_bytes = 0
        empty_files_count = 0
        large_files: List[Dict[str, Any]] = []

        # Create or reuse scanner
        if self.scanner is not None:
            scanner = self.scanner
        else:
            scanner = FastDirectoryScanner(
                root_path=self.root_path,
                exclude_dirs=self.exclude_dirs,
                max_depth=self.max_depth,
            )

        # Stream entries from scanner iterator
        for entry in scanner.scan_iter():
            if entry.is_dir:
                total_directories += 1
                norm_rel = entry.rel_path.strip().replace("\\", "/").strip("/")
                if norm_rel and norm_rel not in dir_nodes:
                    parts = norm_rel.split("/")
                    curr = root_node
                    curr_rel_acc = []
                    for part in parts:
                        curr_rel_acc.append(part)
                        part_rel = "/".join(curr_rel_acc)
                        part_path = os.path.join(self.root_path, *curr_rel_acc)
                        curr = curr.get_or_create_child(part, part_path, part_rel)
                        dir_nodes[part_rel] = curr

                tax_name = self.classify_taxonomy(entry.rel_path)
                taxonomies[tax_name].record_dir()

            elif entry.is_file:
                total_files += 1
                size = entry.size
                total_nominal_bytes += size
                allocated = self.calculate_allocation(size)
                total_allocated_bytes += allocated

                if size == 0:
                    empty_files_count += 1

                # 1. Taxonomy Breakdown
                tax_name = self.classify_taxonomy(entry.rel_path)
                taxonomies[tax_name].record_file(size, allocated)

                # 2. Category / MIME Breakdown
                cat_name = self.classify_category(entry.name, entry.ext, entry.rel_path)
                if cat_name not in categories:
                    cat_name = "Other"
                categories[cat_name].record_file(entry.ext, size, allocated)

                # 3. Directory Tree Aggregation
                parent_rel = normalize_rel_path(entry.parent_dir, self.root_path).strip().replace("\\", "/").strip("/")
                if parent_rel not in dir_nodes:
                    parts = parent_rel.split("/") if parent_rel else []
                    curr = root_node
                    curr_rel_acc = []
                    for part in parts:
                        curr_rel_acc.append(part)
                        part_rel = "/".join(curr_rel_acc)
                        part_path = os.path.join(self.root_path, *curr_rel_acc)
                        curr = curr.get_or_create_child(part, part_path, part_rel)
                        dir_nodes[part_rel] = curr
                    parent_node = curr
                else:
                    parent_node = dir_nodes[parent_rel]

                parent_node.add_file(size, allocated)

                # Track top largest files (keep top 20)
                if size > 10 * 1024 * 1024 or len(large_files) < 20:
                    large_files.append({
                        "name": entry.name,
                        "rel_path": entry.rel_path,
                        "size": size,
                        "allocated": allocated,
                        "slack": allocated - size,
                        "category": cat_name,
                        "taxonomy": tax_name,
                    })

        # Post-order tree rollup
        root_node.rollup()

        elapsed_time = time.perf_counter() - t0
        total_slack_bytes = total_allocated_bytes - total_nominal_bytes
        total_slack_percentage = (
            (total_slack_bytes / total_allocated_bytes * 100.0)
            if total_allocated_bytes > 0 else 0.0
        )
        total_slack_ratio = total_slack_percentage / 100.0
        total_clusters = (total_allocated_bytes // self.cluster_size) if self.cluster_size > 0 else 0

        large_files.sort(key=lambda f: f["size"], reverse=True)
        top_largest = large_files[:20]

        top_slack_dirs = [
            {
                "name": n.name,
                "rel_path": n.rel_path or ".",
                "recursive_files": n.recursive_files,
                "recursive_bytes": n.recursive_bytes,
                "recursive_allocated": n.recursive_allocated,
                "recursive_slack": n.recursive_slack,
                "recursive_slack_percentage": n.recursive_slack_percentage,
            }
            for n in root_node.get_top_slack_dirs(limit=15)
        ]

        report_data: Dict[str, Any] = {
            "root_path": self.root_path,
            "cluster_size": self.cluster_size,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scan_stats": {
                "elapsed_seconds": round(elapsed_time, 3),
                "total_files": total_files,
                "total_directories": total_directories,
                "throughput_fps": round(total_files / elapsed_time, 1) if elapsed_time > 0 else 0.0,
            },
            "summary": {
                "total_files": total_files,
                "total_directories": total_directories,
                "total_logical_bytes": total_nominal_bytes,
                "total_allocated_bytes": total_allocated_bytes,
                "total_slack_bytes": total_slack_bytes,
                "total_slack_percentage": round(total_slack_percentage, 2),
                "total_slack_ratio": round(total_slack_ratio, 4),
                "total_clusters": total_clusters,
                "empty_files_count": empty_files_count,
            },
            "total_files": total_files,
            "total_directories": total_directories,
            "total_logical_bytes": total_nominal_bytes,
            "total_bytes": total_nominal_bytes,
            "total_allocated_bytes": total_allocated_bytes,
            "total_slack_bytes": total_slack_bytes,
            "total_slack_percentage": round(total_slack_percentage, 2),
            "taxonomies": {name: stat.to_dict() for name, stat in taxonomies.items()},
            "categories": {name: stat.to_dict() for name, stat in categories.items()},
            "top_slack_directories": top_slack_dirs,
            "top_largest_files": top_largest,
            "tree": root_node.to_dict(max_depth=self.max_depth or 3),
        }

        # The slack figures model self.cluster_size; say when this volume is something else.
        fs_info = detect_filesystem(self.root_path)
        report_data["filesystem"] = fs_info.to_dict()
        report_data["slack_model_note"] = slack_model_note(fs_info, self.cluster_size)

        return AuditResult(report_data, auditor=self)

    # --------------------------------------------------------------------------
    # Multi-Format Report Generators
    # --------------------------------------------------------------------------

    def generate_ascii_table_report(self, data: Dict[str, Any]) -> str:
        """Renders an ASCII text report formatted with structured tables."""
        buf = io.StringIO()
        summary = data.get("summary", {})
        cluster_kb = data.get("cluster_size", CLUSTER_SIZE_BYTES) // 1024

        buf.write("=" * 80 + "\n")
        buf.write("SMART DRIVE OS - STORAGE AUDIT REPORT\n")
        buf.write(f"Root: {data.get('root_path')} | Cluster Size: {cluster_kb} KB ({data.get('cluster_size')} B)\n")
        if data.get("filesystem"):
            buf.write(f"Filesystem: {data['filesystem'].get('summary')}\n")
        if data.get("slack_model_note"):
            buf.write(f"Note: {data['slack_model_note']}\n")
        buf.write(f"Generated: {data.get('generated_at')}\n")
        buf.write("=" * 80 + "\n\n")

        # 1. Summary
        buf.write("EXECUTIVE SUMMARY\n")
        buf.write("-" * 80 + "\n")
        buf.write(f"  Total Files:             {format_count(summary.get('total_files', 0))}\n")
        buf.write(f"  Total Directories:       {format_count(summary.get('total_directories', 0))}\n")
        buf.write(f"  Logical Nominal Size:    {format_bytes(summary.get('total_logical_bytes', 0))} ({summary.get('total_logical_bytes', 0):,} B)\n")
        buf.write(f"  Physical Allocated Size: {format_bytes(summary.get('total_allocated_bytes', 0))} ({summary.get('total_allocated_bytes', 0):,} B)\n")
        buf.write(f"  Wasted Cluster Slack:    {format_bytes(summary.get('total_slack_bytes', 0))} ({format_percentage(summary.get('total_slack_percentage', 0.0))} waste)\n")
        buf.write(f"  Zero-Byte Files:         {format_count(summary.get('empty_files_count', 0))} (0 clusters allocated)\n")
        buf.write(f"  Allocated 512KB Clusters:{format_count(summary.get('total_clusters', 0))}\n")
        buf.write("-" * 80 + "\n\n")

        # 2. Taxonomy Breakdown Table
        buf.write("STORAGE BREAKDOWN BY TAXONOMY\n")
        buf.write("+-------------------------+----------+--------------+--------------+--------------+--------+\n")
        buf.write("| Taxonomy                | Files    | Logical Size | Allocated    | Slack Wasted | Waste% |\n")
        buf.write("+-------------------------+----------+--------------+--------------+--------------+--------+\n")
        taxonomies = data.get("taxonomies", {})
        for name, stat in sorted(taxonomies.items()):
            files_str = format_count(stat.get("file_count", 0)).rjust(8)
            log_str = format_bytes(stat.get("nominal_bytes", 0)).rjust(12)
            alloc_str = format_bytes(stat.get("allocated_bytes", 0)).rjust(12)
            slack_str = format_bytes(stat.get("slack_bytes", 0)).rjust(12)
            pct_str = format_percentage(stat.get("slack_percentage", 0.0)).rjust(6)
            buf.write(f"| {name:<23} | {files_str} | {log_str} | {alloc_str} | {slack_str} | {pct_str} |\n")
        buf.write("+-------------------------+----------+--------------+--------------+--------------+--------+\n\n")

        # 3. Category Breakdown Table
        buf.write("STORAGE BREAKDOWN BY FUNCTIONAL CATEGORY\n")
        buf.write("+-------------------------+----------+--------------+--------------+--------------+--------+\n")
        buf.write("| Category                | Files    | Logical Size | Allocated    | Slack Wasted | Waste% |\n")
        buf.write("+-------------------------+----------+--------------+--------------+--------------+--------+\n")
        categories = data.get("categories", {})
        for name, stat in sorted(categories.items(), key=lambda kv: kv[1].get("nominal_bytes", 0), reverse=True):
            files_str = format_count(stat.get("file_count", 0)).rjust(8)
            log_str = format_bytes(stat.get("nominal_bytes", 0)).rjust(12)
            alloc_str = format_bytes(stat.get("allocated_bytes", 0)).rjust(12)
            slack_str = format_bytes(stat.get("slack_bytes", 0)).rjust(12)
            pct_str = format_percentage(stat.get("slack_percentage", 0.0)).rjust(6)
            buf.write(f"| {name:<23} | {files_str} | {log_str} | {alloc_str} | {slack_str} | {pct_str} |\n")
        buf.write("+-------------------------+----------+--------------+--------------+--------------+--------+\n\n")

        # 4. Top Slack Hotspots
        buf.write("CLUSTER SLACK HOTSPOTS (TOP DIRECTORIES BY WASTED SPACE)\n")
        buf.write("+----------------------------------------------+----------+--------------+--------------+--------+\n")
        buf.write("| Directory (Subtree)                          | Files    | Subtree Size | Slack Wasted | Waste% |\n")
        buf.write("+----------------------------------------------+----------+--------------+--------------+--------+\n")
        top_slack = data.get("top_slack_directories", [])
        for d in top_slack[:10]:
            p_display = (d.get("rel_path") or ".")[:44]
            files_str = format_count(d.get("recursive_files", 0)).rjust(8)
            size_str = format_bytes(d.get("recursive_bytes", 0)).rjust(12)
            slack_str = format_bytes(d.get("recursive_slack", 0)).rjust(12)
            pct_str = format_percentage(d.get("recursive_slack_percentage", 0.0)).rjust(6)
            buf.write(f"| {p_display:<44} | {files_str} | {size_str} | {slack_str} | {pct_str} |\n")
        buf.write("+----------------------------------------------+----------+--------------+--------------+--------+\n\n")

        return buf.getvalue()

    def generate_markdown_report(self, data: Dict[str, Any]) -> str:
        """Renders a complete Markdown storage audit report."""
        buf = io.StringIO()
        summary = data.get("summary", {})
        cluster_kb = data.get("cluster_size", CLUSTER_SIZE_BYTES) // 1024

        buf.write(f"# Storage Audit Report\n\n")
        buf.write(f"- **Root Path**: `{data.get('root_path')}`\n")
        buf.write(f"- **Volume Cluster Geometry**: `{cluster_kb} KB` ({data.get('cluster_size'):,} bytes per allocation unit)\n")
        if data.get("filesystem"):
            buf.write(f"- **Filesystem**: `{data['filesystem'].get('summary')}`\n")
        if data.get("slack_model_note"):
            buf.write(f"- **Note**: {data['slack_model_note']}\n")
        buf.write(f"- **Timestamp**: `{data.get('generated_at')}`\n\n")

        buf.write("## 1. Executive Summary\n\n")
        buf.write("| Metric | Value |\n")
        buf.write("|:---|---:|\n")
        buf.write(f"| **Total Files** | {format_count(summary.get('total_files', 0))} |\n")
        buf.write(f"| **Total Directories** | {format_count(summary.get('total_directories', 0))} |\n")
        buf.write(f"| **Logical Nominal Size** | {format_bytes(summary.get('total_logical_bytes', 0))} ({summary.get('total_logical_bytes', 0):,} bytes) |\n")
        buf.write(f"| **Physical Allocated Size** | {format_bytes(summary.get('total_allocated_bytes', 0))} ({summary.get('total_allocated_bytes', 0):,} bytes) |\n")
        buf.write(f"| **Wasted Cluster Slack** | {format_bytes(summary.get('total_slack_bytes', 0))} ({format_percentage(summary.get('total_slack_percentage', 0.0))} overhead) |\n")
        buf.write(f"| **Allocated 512KB Clusters** | {format_count(summary.get('total_clusters', 0))} |\n")
        buf.write(f"| **Zero-Byte Files** | {format_count(summary.get('empty_files_count', 0))} (0 physical bytes) |\n\n")

        buf.write("## 2. Taxonomy Storage Breakdown\n\n")
        buf.write("| Taxonomy | Files | Logical Size | Physical Allocated | Cluster Slack | Waste % |\n")
        buf.write("|:---|---:|---:|---:|---:|---:|\n")
        taxonomies = data.get("taxonomies", {})
        for name, stat in sorted(taxonomies.items()):
            files_str = format_count(stat.get("file_count", 0))
            log_str = format_bytes(stat.get("nominal_bytes", 0))
            alloc_str = format_bytes(stat.get("allocated_bytes", 0))
            slack_str = format_bytes(stat.get("slack_bytes", 0))
            pct_str = format_percentage(stat.get("slack_percentage", 0.0))
            buf.write(f"| **{name}** | {files_str} | {log_str} | {alloc_str} | {slack_str} | {pct_str} |\n")
        buf.write("\n")

        buf.write("## 3. Extension & MIME Functional Classification\n\n")
        buf.write("| Category | Files | Logical Size | Physical Allocated | Cluster Slack | Waste % |\n")
        buf.write("|:---|---:|---:|---:|---:|---:|\n")
        categories = data.get("categories", {})
        for name, stat in sorted(categories.items(), key=lambda kv: kv[1].get("nominal_bytes", 0), reverse=True):
            files_str = format_count(stat.get("file_count", 0))
            log_str = format_bytes(stat.get("nominal_bytes", 0))
            alloc_str = format_bytes(stat.get("allocated_bytes", 0))
            slack_str = format_bytes(stat.get("slack_bytes", 0))
            pct_str = format_percentage(stat.get("slack_percentage", 0.0))
            buf.write(f"| **{name}** | {files_str} | {log_str} | {alloc_str} | {slack_str} | {pct_str} |\n")
        buf.write("\n")

        buf.write("## 4. 512KB Cluster Slack Hotspots\n\n")
        buf.write("Folders with significant cluster slack overhead caused by thousands of small files:\n\n")
        buf.write("| Directory (Subtree) | Files | Subtree Size | Slack Wasted | Slack Overhead % |\n")
        buf.write("|:---|---:|---:|---:|---:|\n")
        for d in data.get("top_slack_directories", [])[:10]:
            p = d.get("rel_path") or "."
            files_str = format_count(d.get("recursive_files", 0))
            size_str = format_bytes(d.get("recursive_bytes", 0))
            slack_str = format_bytes(d.get("recursive_slack", 0))
            pct_str = format_percentage(d.get("recursive_slack_percentage", 0.0))
            buf.write(f"| `{p}` | {files_str} | {size_str} | {slack_str} | {pct_str} |\n")
        buf.write("\n")

        buf.write("## 5. Storage Optimization Insights\n\n")
        slack_pct = summary.get("total_slack_percentage", 0.0)
        slack_bytes = summary.get("total_slack_bytes", 0)
        buf.write(f"- **Volume Cluster Health**: Total slack loss is **{format_bytes(slack_bytes)}** ({slack_pct:.1f}%).\n")
        buf.write("- **Recommendation 1 (Purge AppleDouble)**: Run `smart-drive clean --dry-run` to detect and safely eliminate `._*` resource forks, which each waste 512 KB.\n")
        buf.write("- **Recommendation 2 (Archive Small Code Repos)**: Folders with >50% slack overhead (e.g. unbundled node_modules or unpacked repositories) should be zipped or tarred into `.zip` / `.tar.gz` archives to eliminate cluster quantization loss.\n")

        return buf.getvalue()


# ------------------------------------------------------------------------------
# Module-level Fallbacks for AuditResult when Auditor is Detached
# ------------------------------------------------------------------------------

def _fallback_markdown_report(data: Dict[str, Any]) -> str:
    auditor = StorageAuditor(data.get("root_path", "."))
    return auditor.generate_markdown_report(data)


def _fallback_ascii_table_report(data: Dict[str, Any]) -> str:
    auditor = StorageAuditor(data.get("root_path", "."))
    return auditor.generate_ascii_table_report(data)


__all__ = [
    "StorageAuditor",
    "AuditResult",
    "AuditReport",
    "CategoryStats",
    "TaxonomyStats",
    "DirectoryNode",
    "CANONICAL_TAXONOMIES",
    "WORKSPACE_ROOT_DIRS",
    "SYSTEM_ROOT_DIRS",
    "format_bytes",
    "format_count",
    "format_percentage",
]
