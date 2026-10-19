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

import heapq
import html
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
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple, Union

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
    LEGACY_TAXONOMY_ALIASES,
    PROTECTED_ROOT_DIRS,
    DEFAULT_EXCLUDE_DIRS,
)
from smart_drive.core.scanner import FastDirectoryScanner, ScanEntry, ScanOptions, ScanStats
from smart_drive.core.exfat_compat import ExFatEngine, normalize_rel_path
from smart_drive.core.fsinfo import detect_filesystem, slack_model_note
from smart_drive.core.learning_units import (
    get_hotspot_advice,
    get_learning_summary,
    is_in_learning_unit,
)

logger = logging.getLogger("smart_drive.core.auditor")


# ==============================================================================
# 1. TAXONOMY SPECIFICATIONS & CANONICAL GROUPINGS
# ==============================================================================

# Canonical taxonomy names: the folders `smart-drive init` creates (single source: config.py)
CANONICAL_TAXONOMIES: Tuple[str, ...] = TAXONOMY_ROOT_DIRS

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
    direct_micro_files: int = 0
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
        if size < 32 * 1024:
            self.direct_micro_files += 1

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
            "direct_micro_files": self.direct_micro_files,
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

    def to_html(self) -> str:
        """Generates a self-contained offline HTML report with squarified treemap."""
        if self.auditor:
            return self.auditor.generate_html_report(self)
        return _fallback_html_report(self)

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

    def export_html(self, filepath: Union[str, Path]) -> None:
        """Exports self-contained HTML treemap report to disk."""
        target = Path(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_html(), encoding="utf-8")


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
        # Drives made by earlier versions: their folders count in today's rows
        self._taxonomy_lookup.update(LEGACY_TAXONOMY_ALIASES)

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
        large_files_heap: List[Tuple[int, int, Dict[str, Any]]] = []
        seen_links: Set[Tuple[int, int]] = set()  # (device, inode) of hard-linked data already counted
        hardlinks_not_counted = 0

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
                # Another path to data that was already counted shares its storage and adds no space: counting it
                # again overstated a folder full of hard links by a third (265.7 GB reported, 171.8 GB on disk).
                # The file is still listed once per path; only its bytes are counted once.
                extra_link = False
                if entry.nlink > 1 and entry.inode:
                    identity = (entry.device, entry.inode)
                    if identity in seen_links:
                        extra_link = True
                        hardlinks_not_counted += 1
                    else:
                        seen_links.add(identity)
                if extra_link:
                    size = 0
                    allocated = 0
                else:
                    allocated = self.calculate_allocation(size)
                total_nominal_bytes += size
                total_allocated_bytes += allocated

                if size == 0 and not extra_link:
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

                # Track top largest files (keep top 50 via min-heap)
                if len(large_files_heap) < 50 or size > large_files_heap[0][0]:
                    file_info = {
                        "name": entry.name,
                        "rel_path": entry.rel_path,
                        "size": size,
                        "allocated": allocated,
                        "slack": allocated - size,
                        "category": cat_name,
                        "taxonomy": tax_name,
                    }
                    if len(large_files_heap) < 50:
                        heapq.heappush(large_files_heap, (size, -total_files, file_info))
                    else:
                        heapq.heapreplace(large_files_heap, (size, -total_files, file_info))

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

        top_largest = [
            item[2]
            for item in sorted(large_files_heap, key=lambda x: (-x[0], -x[1]))
        ]

        top_largest_dirs = [
            {
                "name": n.name,
                "rel_path": n.rel_path or ".",
                "recursive_files": n.recursive_files,
                "recursive_bytes": n.recursive_bytes,
                "recursive_allocated": n.recursive_allocated,
                "recursive_slack": n.recursive_slack,
                "recursive_slack_percentage": round(n.recursive_slack_percentage, 2),
            }
            for n in root_node.get_top_size_dirs(limit=51)
            if n.rel_path
        ][:50]

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

        # Micro-file hotspots: folders with at least 20 files under 32 KB whose cluster slack exceeds 90%
        hotspot_nodes = [
            n for n in root_node.flatten()
            if n.direct_micro_files >= 20 and n.direct_slack_percentage > 90.0
        ]
        hotspot_nodes.sort(key=lambda n: n.direct_slack, reverse=True)

        micro_file_hotspots: List[Dict[str, Any]] = []
        for n in hotspot_nodes:
            is_unit = is_in_learning_unit(n.path, self.root_path)
            advice_str = get_hotspot_advice(n.rel_path, is_learning_unit=is_unit)
            micro_file_hotspots.append({
                "path": n.path,
                "rel_path": n.rel_path or ".",
                "name": n.name,
                "file_count": n.direct_files,
                "micro_file_count": n.direct_micro_files,
                "nominal_bytes": n.direct_bytes,
                "allocated_bytes": n.direct_allocated,
                "slack_bytes": n.direct_slack,
                "slack_percentage": round(n.direct_slack_percentage, 2),
                "advice": advice_str,
            })

        total_recoverable_slack = sum(h["slack_bytes"] for h in micro_file_hotspots)
        learning_summary = get_learning_summary(self.root_path)

        report_data: Dict[str, Any] = {
            "root_path": self.root_path,
            "cluster_size": self.cluster_size,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "scan_stats": {
                "elapsed_seconds": round(elapsed_time, 3),
                "total_files": total_files,
                "total_directories": total_directories,
                "throughput_fps": round(total_files / elapsed_time, 1) if elapsed_time > 0 else 0.0,
                "symlinks_skipped": int(getattr(getattr(scanner, "stats", None), "symlinks_skipped", 0) or 0),
                "hardlinks_not_counted": hardlinks_not_counted,
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
            "top_largest_directories": top_largest_dirs,
            "top_largest_files": top_largest,
            "micro_file_hotspots": micro_file_hotspots,
            "total_recoverable_slack": total_recoverable_slack,
            "learning_summary": learning_summary,
            "learning": learning_summary,
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
        skipped_links = (data.get("scan_stats") or {}).get("symlinks_skipped", 0)
        if skipped_links:
            buf.write(f"  Symlinks Skipped:        {format_count(skipped_links)} (links are never followed or counted)\n")
        extra_links = (data.get("scan_stats") or {}).get("hardlinks_not_counted", 0)
        if extra_links:
            buf.write(f"  Hard Links Not Counted:  {format_count(extra_links)} (extra paths to data already counted)\n")
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

        # 5. Micro-File Hotspots
        micro_hotspots = data.get("micro_file_hotspots", [])
        if micro_hotspots:
            buf.write("MICRO-FILE CLUSTER SLACK HOTSPOTS (>=20 FILES <32KB WITH >90% SLACK)\n")
            buf.write("+--------------------------------------+----------+--------------+--------------+--------+-------------------------------------------------------+\n")
            buf.write("| Directory                            | Micro-F  | Direct Size  | Slack Wasted | Waste% | Advice                                                |\n")
            buf.write("+--------------------------------------+----------+--------------+--------------+--------+-------------------------------------------------------+\n")
            for h in micro_hotspots:
                p_display = (h.get("rel_path") or ".")[:36]
                mf_str = format_count(h.get("micro_file_count", 0)).rjust(8)
                sz_str = format_bytes(h.get("nominal_bytes", 0)).rjust(12)
                slk_str = format_bytes(h.get("slack_bytes", 0)).rjust(12)
                pct_str = format_percentage(h.get("slack_percentage", 0.0)).rjust(6)
                adv_str = (h.get("advice", ""))[:53]
                buf.write(f"| {p_display:<36} | {mf_str} | {sz_str} | {slk_str} | {pct_str} | {adv_str:<53} |\n")
            buf.write("+--------------------------------------+----------+--------------+--------------+--------+-------------------------------------------------------+\n")
            buf.write(f"  Total Recoverable Hotspot Slack: {format_bytes(data.get('total_recoverable_slack', 0))}\n\n")

        # 6. Learning Integration Summary
        learning = data.get("learning_summary") or data.get("learning") or {}
        if any(learning.values()):
            buf.write("LEARNING UNITS & KNOWLEDGE INDEX\n")
            buf.write(f"  Aurora Courses:          {format_count(learning.get('aurora_courses', 0))}\n")
            buf.write(f"  Standalone Aurora Decks: {format_count(learning.get('aurora_decks', 0))}\n")
            buf.write(f"  Polaris Subjects:        {format_count(learning.get('polaris_subjects', 0))}\n")
            buf.write(f"  Indexed Document Chunks: {format_count(learning.get('chunk_count', 0))}\n")
            buf.write(f"  Last Index Update:       {learning.get('last_index_update') or 'Never'}\n")
            buf.write("-" * 80 + "\n\n")

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
        buf.write(f"| **Zero-Byte Files** | {format_count(summary.get('empty_files_count', 0))} (0 physical bytes) |\n")
        skipped_links = (data.get("scan_stats") or {}).get("symlinks_skipped", 0)
        if skipped_links:
            buf.write(f"| **Symlinks Skipped** | {format_count(skipped_links)} (never followed or counted) |\n")
        extra_links = (data.get("scan_stats") or {}).get("hardlinks_not_counted", 0)
        if extra_links:
            buf.write(f"| **Hard Links Not Counted** | {format_count(extra_links)} (extra paths to data already counted) |\n")
        buf.write("\n")

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

        # 5. Micro-File Hotspots
        micro_hotspots = data.get("micro_file_hotspots", [])
        if micro_hotspots:
            buf.write("## 5. Micro-File Hotspots (High Cluster Slack)\n\n")
            buf.write("Folders containing at least 20 micro-files (< 32 KB) with > 90% cluster slack waste on 512 KB clusters:\n\n")
            buf.write("| Directory | Micro-Files | Direct Files | Logical Size | Allocated | Slack Wasted | Waste % | Advice |\n")
            buf.write("|:---|---:|---:|---:|---:|---:|---:|:---||\n")
            for h in micro_hotspots:
                p = h.get("rel_path") or "."
                mf_str = format_count(h.get("micro_file_count", 0))
                df_str = format_count(h.get("file_count", 0))
                sz_str = format_bytes(h.get("nominal_bytes", 0))
                al_str = format_bytes(h.get("allocated_bytes", 0))
                slk_str = format_bytes(h.get("slack_bytes", 0))
                pct_str = format_percentage(h.get("slack_percentage", 0.0))
                adv_str = h.get("advice", "")
                buf.write(f"| `{p}` | {mf_str} | {df_str} | {sz_str} | {al_str} | {slk_str} | {pct_str} | {adv_str} |\n")
            buf.write(f"\n- **Total Recoverable Hotspot Slack**: {format_bytes(data.get('total_recoverable_slack', 0))}\n\n")

        # 6. Learning Integration Summary
        learning = data.get("learning_summary") or data.get("learning") or {}
        if any(learning.values()):
            buf.write("## 6. Learning Units & Knowledge Index\n\n")
            buf.write(f"- **Aurora Courses**: {format_count(learning.get('aurora_courses', 0))}\n")
            buf.write(f"- **Standalone Aurora Decks**: {format_count(learning.get('aurora_decks', 0))}\n")
            buf.write(f"- **Polaris Subjects**: {format_count(learning.get('polaris_subjects', 0))}\n")
            buf.write(f"- **Indexed Document Chunks**: {format_count(learning.get('chunk_count', 0))}\n")
            buf.write(f"- **Last Index Update**: `{learning.get('last_index_update') or 'Never'}`\n\n")

        buf.write("## 7. Storage Optimization Insights\n\n")
        slack_pct = summary.get("total_slack_percentage", 0.0)
        slack_bytes = summary.get("total_slack_bytes", 0)
        buf.write(f"- **Volume Cluster Health**: Total slack loss is **{format_bytes(slack_bytes)}** ({slack_pct:.1f}%).\n")
        buf.write("- **Recommendation 1 (Purge AppleDouble)**: Run `smart-drive clean --dry-run` to detect and safely eliminate `._*` resource forks, which each waste 512 KB.\n")
        buf.write("- **Recommendation 2 (Archive Small Code Repos)**: Folders with >50% slack overhead (e.g. unbundled node_modules or unpacked repositories) should be zipped or tarred into `.zip` / `.tar.gz` archives to eliminate cluster quantization loss.\n")

        return buf.getvalue()

    def generate_html_report(self, data: Dict[str, Any]) -> str:
        """Renders a self-contained offline HTML report with a squarified treemap (depth <= 3)."""
        return _render_self_contained_html(data)


# ------------------------------------------------------------------------------
# Squarified Treemap & Self-Contained HTML Generator (Zero External URLs)
# ------------------------------------------------------------------------------

_TREEMAP_PALETTE: Tuple[str, ...] = (
    "#2563eb", "#0891b2", "#059669", "#7c3aed", "#d97706",
    "#dc2626", "#4f46e5", "#0d9488", "#65a30d", "#db2777",
    "#9333ea", "#ea580c", "#0284c7", "#475569",
)


def _worst_aspect_ratio(row_areas: List[float], side: float) -> float:
    if not row_areas or side <= 0:
        return float("inf")
    s = sum(row_areas)
    if s <= 0:
        return float("inf")
    r_max = max(row_areas)
    r_min = min(row_areas)
    if r_min <= 0:
        return float("inf")
    s2 = s * s
    side2 = side * side
    return max((side2 * r_max) / s2, s2 / (side2 * r_min))


def _squarify_rects(
    items: List[Dict[str, Any]],
    x: float = 0.0,
    y: float = 0.0,
    w: float = 100.0,
    h: float = 100.0,
) -> List[Dict[str, Any]]:
    """Computes Bruls-Huizing-van Wijk squarified treemap coordinates in [x..x+w, y..y+h]."""
    positive = [dict(it) for it in items if it.get("bytes", 0) > 0]
    if not positive or w <= 0 or h <= 0:
        return []
    positive.sort(key=lambda it: it["bytes"], reverse=True)
    total_bytes = sum(it["bytes"] for it in positive)
    if total_bytes <= 0:
        return []

    total_area = w * h
    for it in positive:
        it["_area"] = (it["bytes"] / total_bytes) * total_area

    result: List[Dict[str, Any]] = []
    rx, ry, rw, rh = x, y, w, h
    idx = 0
    n = len(positive)

    while idx < n and rw > 1e-6 and rh > 1e-6:
        row: List[Dict[str, Any]] = [positive[idx]]
        idx += 1
        side = min(rw, rh)
        while idx < n:
            candidate = positive[idx]
            curr_areas = [r["_area"] for r in row]
            next_areas = curr_areas + [candidate["_area"]]
            if _worst_aspect_ratio(next_areas, side) <= _worst_aspect_ratio(curr_areas, side):
                row.append(candidate)
                idx += 1
            else:
                break

        row_area = sum(r["_area"] for r in row)
        if rw >= rh:
            col_w = row_area / rh if rh > 0 else 0.0
            cy = ry
            for r in row:
                ih = (r["_area"] / col_w) if col_w > 0 else 0.0
                r["rect"] = (round(rx, 3), round(cy, 3), round(col_w, 3), round(ih, 3))
                cy += ih
                result.append(r)
            rx += col_w
            rw = max(0.0, rw - col_w)
        else:
            bar_h = row_area / rw if rw > 0 else 0.0
            cx = rx
            for r in row:
                iw = (r["_area"] / bar_h) if bar_h > 0 else 0.0
                r["rect"] = (round(cx, 3), round(ry, 3), round(iw, 3), round(bar_h, 3))
                cx += iw
                result.append(r)
            ry += bar_h
            rh = max(0.0, rh - bar_h)

    for r in result:
        r.pop("_area", None)
    return result


def _prepare_treemap_node(
    tree_node: Dict[str, Any],
    depth: int = 0,
    max_depth: int = 3,
    min_share: float = 0.015,
    max_children: int = 14,
) -> Dict[str, Any]:
    """Recursively builds a squarified treemap hierarchy up to max_depth, grouping tiny items into 'Other (Khác)'."""
    name = str(tree_node.get("name") or ".")
    rel_path = str(tree_node.get("rel_path") or ".")
    rec_bytes = int(tree_node.get("recursive_bytes", 0) or 0)
    rec_alloc = int(tree_node.get("recursive_allocated", 0) or 0)
    rec_slack = int(tree_node.get("recursive_slack", 0) or 0)
    rec_files = int(tree_node.get("recursive_files", 0) or 0)

    node_out: Dict[str, Any] = {
        "name": name,
        "rel_path": rel_path,
        "bytes": rec_bytes,
        "bytes_fmt": format_bytes(rec_bytes),
        "allocated": rec_alloc,
        "allocated_fmt": format_bytes(rec_alloc),
        "slack": rec_slack,
        "slack_fmt": format_bytes(rec_slack),
        "files": rec_files,
        "depth": depth,
        "children": [],
    }

    if depth >= max_depth:
        return node_out

    subdirs = tree_node.get("subdirs") or {}
    raw_children: List[Dict[str, Any]] = []
    for sub_name, sub_dict in subdirs.items():
        s_bytes = int(sub_dict.get("recursive_bytes", 0) or 0)
        s_alloc = int(sub_dict.get("recursive_allocated", 0) or 0)
        s_files = int(sub_dict.get("recursive_files", 0) or 0)
        if s_bytes <= 0 and s_alloc > 0:
            s_bytes = 1
        if s_bytes > 0:
            raw_children.append({
                "raw": sub_dict,
                "name": str(sub_dict.get("name") or sub_name),
                "rel_path": str(sub_dict.get("rel_path") or sub_name),
                "bytes": s_bytes,
                "allocated": s_alloc,
                "slack": int(sub_dict.get("recursive_slack", 0) or 0),
                "files": s_files,
                "is_dir": True,
            })

    direct_bytes = int(tree_node.get("direct_bytes", 0) or 0)
    direct_alloc = int(tree_node.get("direct_allocated", 0) or 0)
    direct_files = int(tree_node.get("direct_files", 0) or 0)
    if direct_files > 0 and (direct_bytes > 0 or direct_alloc > 0) and raw_children:
        eff_bytes = direct_bytes if direct_bytes > 0 else 1
        raw_children.append({
            "raw": None,
            "name": "[Direct Files]",
            "rel_path": f"{rel_path}/[Direct Files]" if rel_path != "." else "[Direct Files]",
            "bytes": eff_bytes,
            "allocated": direct_alloc,
            "slack": max(0, direct_alloc - direct_bytes),
            "files": direct_files,
            "is_dir": False,
        })

    if not raw_children:
        return node_out

    raw_children.sort(key=lambda c: c["bytes"], reverse=True)
    parent_total = sum(c["bytes"] for c in raw_children)

    kept: List[Dict[str, Any]] = []
    grouped: List[Dict[str, Any]] = []
    for idx, child in enumerate(raw_children):
        share = (child["bytes"] / parent_total) if parent_total > 0 else 0.0
        if idx < max_children and (share >= min_share or idx == 0):
            kept.append(child)
        else:
            grouped.append(child)

    # If only 1 item ended up in 'grouped' and we still have room, keep it directly
    if len(grouped) == 1 and len(kept) < max_children:
        kept.append(grouped.pop())

    if grouped:
        other_bytes = sum(c["bytes"] for c in grouped)
        other_alloc = sum(c["allocated"] for c in grouped)
        other_slack = sum(c["slack"] for c in grouped)
        other_files = sum(c["files"] for c in grouped)
        kept.append({
            "raw": None,
            "name": f"Other / Khác ({len(grouped)} items)",
            "rel_path": f"{rel_path}/[Other]" if rel_path != "." else "[Other]",
            "bytes": other_bytes,
            "allocated": other_alloc,
            "slack": other_slack,
            "files": other_files,
            "is_dir": False,
            "is_other": True,
            "grouped_names": [c["name"] for c in grouped[:20]],
        })

    laid_out = _squarify_rects(kept, 0.0, 0.0, 100.0, 100.0)
    children_out: List[Dict[str, Any]] = []
    for i, item in enumerate(laid_out):
        color = "#64748b" if item.get("is_other") else _TREEMAP_PALETTE[i % len(_TREEMAP_PALETTE)]
        if item.get("raw") is not None:
            child_node = _prepare_treemap_node(
                item["raw"],
                depth=depth + 1,
                max_depth=max_depth,
                min_share=min_share,
                max_children=max_children,
            )
        else:
            child_node = {
                "name": item["name"],
                "rel_path": item["rel_path"],
                "bytes": item["bytes"],
                "bytes_fmt": format_bytes(item["bytes"]),
                "allocated": item["allocated"],
                "allocated_fmt": format_bytes(item["allocated"]),
                "slack": item["slack"],
                "slack_fmt": format_bytes(item["slack"]),
                "files": item["files"],
                "depth": depth + 1,
                "children": [],
            }
        child_node["rect"] = item.get("rect", (0.0, 0.0, 0.0, 0.0))
        child_node["color"] = color
        child_node["is_other"] = bool(item.get("is_other", False))
        if item.get("grouped_names"):
            child_node["grouped_names"] = item["grouped_names"]
        children_out.append(child_node)

    node_out["children"] = children_out
    return node_out


def _esc(val: Any) -> str:
    """Escapes any value safely for HTML text and attribute contexts."""
    return html.escape(str(val if val is not None else ""), quote=True)


def _render_static_treemap_boxes(node: Dict[str, Any]) -> str:
    """Renders static HTML treemap divs for depth 1..3 so the treemap works even without JS."""
    parts: List[str] = []
    for child in node.get("children", []):
        rx, ry, rw, rh = child.get("rect", (0.0, 0.0, 0.0, 0.0))
        if rw <= 0.1 or rh <= 0.1:
            continue
        c_name = _esc(child.get("name", ""))
        c_rel = _esc(child.get("rel_path", ""))
        c_bytes = _esc(child.get("bytes_fmt", ""))
        c_alloc = _esc(child.get("allocated_fmt", ""))
        c_slack = _esc(child.get("slack_fmt", ""))
        c_files = format_count(int(child.get("files", 0)))
        c_color = _esc(child.get("color", "#2563eb"))
        grouped_attr = ""
        if child.get("grouped_names"):
            grouped_attr = f' data-grouped="{_esc(", ".join(child["grouped_names"]))}"'

        nested_html = ""
        sub_children = child.get("children", [])
        if sub_children and rw >= 8.0 and rh >= 8.0:
            sub_parts: List[str] = []
            for sub in sub_children:
                sx, sy, sw, sh = sub.get("rect", (0.0, 0.0, 0.0, 0.0))
                if sw <= 0.5 or sh <= 0.5:
                    continue
                s_name = _esc(sub.get("name", ""))
                s_bytes = _esc(sub.get("bytes_fmt", ""))
                sub_parts.append(
                    f'<div class="tm-sub" style="left:{sx}%;top:{sy}%;width:{sw}%;height:{sh}%;" '
                    f'title="{s_name} ({s_bytes})">'
                    f'<span class="tm-sub-label">{s_name}</span></div>'
                )
            if sub_parts:
                nested_html = f'<div class="tm-sub-container">{"".join(sub_parts)}</div>'

        parts.append(
            f'<div class="tm-node" style="left:{rx}%;top:{ry}%;width:{rw}%;height:{rh}%;background:{c_color};" '
            f'data-rel-path="{c_rel}" data-name="{c_name}" data-bytes="{c_bytes}" '
            f'data-allocated="{c_alloc}" data-slack="{c_slack}" data-files="{c_files}"{grouped_attr} '
            f'title="{c_rel} | Logical: {c_bytes} | Allocated: {c_alloc} | Slack: {c_slack} | Files: {c_files}">'
            f'<div class="tm-header"><span class="tm-title">{c_name}</span>'
            f'<span class="tm-meta">{c_bytes} ({c_files} files)</span></div>'
            f'{nested_html}'
            f'</div>'
        )
    if not parts:
        return '<div class="tm-empty">Empty directory tree (0 bytes)</div>'
    return "".join(parts)


def _render_self_contained_html(data: Dict[str, Any]) -> str:
    """Produces a single self-contained HTML file with inline CSS/JS and zero external URLs."""
    summary = data.get("summary") or {}
    root_path = _esc(data.get("root_path", "."))
    generated_at = _esc(data.get("generated_at", ""))
    cluster_size = int(data.get("cluster_size", CLUSTER_SIZE_BYTES) or CLUSTER_SIZE_BYTES)
    cluster_kb = f"{cluster_size / 1024:.1f}".rstrip("0").rstrip(".")
    fs_dict = data.get("filesystem") or {}
    fs_summary = _esc(fs_dict.get("summary") or f"Cluster size: {cluster_kb} KB ({cluster_size:,} B)")
    slack_note = _esc(data.get("slack_model_note") or "")

    total_files = int(summary.get("total_files", data.get("total_files", 0)) or 0)
    total_dirs = int(summary.get("total_directories", data.get("total_directories", 0)) or 0)
    logical_bytes = int(summary.get("total_logical_bytes", data.get("total_logical_bytes", 0)) or 0)
    allocated_bytes = int(summary.get("total_allocated_bytes", data.get("total_allocated_bytes", 0)) or 0)
    slack_bytes = int(summary.get("total_slack_bytes", data.get("total_slack_bytes", 0)) or 0)
    slack_pct = float(summary.get("total_slack_percentage", data.get("total_slack_percentage", 0.0)) or 0.0)

    tree_dict = data.get("tree") or {}
    treemap_root = _prepare_treemap_node(tree_dict, depth=0, max_depth=3)
    static_treemap_html = _render_static_treemap_boxes(treemap_root)

    # Safe JSON serialization for inline script: escape <, >, & as unicode escapes
    # AND html-escape all string fields in treemap_root so no unescaped sequence exists
    treemap_json = (
        json.dumps(treemap_root, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )

    # Top 50 largest directories & Top 50 largest files
    top_dirs = (data.get("top_largest_directories") or [])[:50]
    top_files = (data.get("top_largest_files") or [])[:50]
    top_slack = (data.get("top_slack_directories") or [])[:25]

    dir_rows: List[str] = []
    for idx, d in enumerate(top_dirs, 1):
        d_rel = _esc(d.get("rel_path") or d.get("name") or ".")
        d_files = format_count(int(d.get("recursive_files", 0)))
        d_log = _esc(format_bytes(int(d.get("recursive_bytes", 0))))
        d_alloc = _esc(format_bytes(int(d.get("recursive_allocated", 0))))
        d_slack = _esc(format_bytes(int(d.get("recursive_slack", 0))))
        d_pct = _esc(format_percentage(float(d.get("recursive_slack_percentage", 0.0))))
        dir_rows.append(
            f"<tr><td>{idx}</td><td class=\"mono\">{d_rel}</td>"
            f"<td class=\"num\">{d_files}</td><td class=\"num\">{d_log}</td>"
            f"<td class=\"num\">{d_alloc}</td><td class=\"num\">{d_slack}</td>"
            f"<td class=\"num\">{d_pct}</td></tr>"
        )
    if not dir_rows:
        dir_rows.append('<tr><td colspan="7" class="empty-cell">No directories recorded.</td></tr>')

    file_rows: List[str] = []
    for idx, f in enumerate(top_files, 1):
        f_rel = _esc(f.get("rel_path") or f.get("name") or "")
        f_cat = _esc(f.get("category") or "Other")
        f_size = _esc(format_bytes(int(f.get("size", 0))))
        f_alloc = _esc(format_bytes(int(f.get("allocated", 0))))
        f_slack = _esc(format_bytes(int(f.get("slack", 0))))
        file_rows.append(
            f"<tr><td>{idx}</td><td class=\"mono\">{f_rel}</td>"
            f"<td><span class=\"badge\">{f_cat}</span></td>"
            f"<td class=\"num\">{f_size}</td><td class=\"num\">{f_alloc}</td>"
            f"<td class=\"num\">{f_slack}</td></tr>"
        )
    if not file_rows:
        file_rows.append('<tr><td colspan="6" class="empty-cell">No files recorded.</td></tr>')

    slack_rows: List[str] = []
    for idx, s in enumerate(top_slack, 1):
        s_rel = _esc(s.get("rel_path") or s.get("name") or ".")
        s_files = format_count(int(s.get("recursive_files", 0)))
        s_log = _esc(format_bytes(int(s.get("recursive_bytes", 0))))
        s_slk = _esc(format_bytes(int(s.get("recursive_slack", 0))))
        s_pct = _esc(format_percentage(float(s.get("recursive_slack_percentage", 0.0))))
        slack_rows.append(
            f"<tr><td>{idx}</td><td class=\"mono\">{s_rel}</td>"
            f"<td class=\"num\">{s_files}</td><td class=\"num\">{s_log}</td>"
            f"<td class=\"num\">{s_slk}</td><td class=\"num\">{s_pct}</td></tr>"
        )
    if not slack_rows:
        slack_rows.append('<tr><td colspan="6" class="empty-cell">No slack hotspots recorded.</td></tr>')

    note_banner = f'<div class="note-banner">{slack_note}</div>' if slack_note else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SmartDrive Storage Audit — {root_path}</title>
<style>
:root {{
  --bg: #0f172a;
  --panel: #1e293b;
  --border: #334155;
  --text: #f8fafc;
  --muted: #94a3b8;
  --accent: #38bdf8;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  padding: 24px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}}
header {{
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}}
h1 {{ margin: 0; font-size: 1.5rem; }}
.subtitle {{ color: var(--muted); font-size: 0.9rem; margin-top: 4px; }}
.note-banner {{
  background: #1e3a8a;
  border: 1px solid #3b82f6;
  color: #dbeafe;
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 18px;
  font-size: 0.9rem;
}}
.cards {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}}
.card {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}}
.card-label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }}
.card-value {{ font-size: 1.35rem; font-weight: 700; margin-top: 4px; }}
.card-sub {{ color: var(--muted); font-size: 0.82rem; margin-top: 2px; }}
.section {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 24px;
}}
.section-header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}}
.section-title {{ margin: 0; font-size: 1.1rem; }}
.breadcrumb {{
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  font-size: 0.88rem;
}}
.crumb-btn {{
  background: #334155;
  color: var(--text);
  border: none;
  border-radius: 6px;
  padding: 4px 10px;
  cursor: pointer;
  font-size: 0.85rem;
}}
.crumb-btn:hover {{ background: #475569; }}
#treemap-canvas {{
  position: relative;
  width: 100%;
  height: 440px;
  background: #0b1120;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--border);
}}
.tm-node {{
  position: absolute;
  border: 1.5px solid #0f172a;
  overflow: hidden;
  padding: 4px 6px;
  cursor: pointer;
  transition: filter 0.12s ease;
  display: flex;
  flex-direction: column;
}}
.tm-node:hover {{ filter: brightness(1.14); z-index: 2; }}
.tm-header {{
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-size: 0.78rem;
  font-weight: 600;
  text-shadow: 0 1px 2px rgba(0,0,0,0.65);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.tm-meta {{ font-weight: 400; opacity: 0.9; font-size: 0.72rem; }}
.tm-sub-container {{
  position: relative;
  flex: 1;
  margin-top: 4px;
  border-radius: 4px;
  overflow: hidden;
}}
.tm-sub {{
  position: absolute;
  border: 1px solid rgba(15,23,42,0.55);
  background: rgba(15,23,42,0.22);
  padding: 2px 4px;
  overflow: hidden;
  font-size: 0.68rem;
}}
.tm-sub-label {{
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
  opacity: 0.9;
}}
.tm-empty {{
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--muted);
}}
.tabs {{
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}}
.tab-btn {{
  background: #0f172a;
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 14px;
  cursor: pointer;
  font-weight: 600;
}}
.tab-btn.active {{
  background: var(--accent);
  color: #0f172a;
  border-color: var(--accent);
}}
.table-wrap {{ overflow-x: auto; }}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}}
th, td {{
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  text-align: left;
}}
th {{ color: var(--muted); font-weight: 600; background: rgba(15,23,42,0.5); }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
td.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; word-break: break-all; }}
.badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  background: #334155;
  font-size: 0.76rem;
}}
.empty-cell {{ text-align: center; color: var(--muted); padding: 18px; }}
</style>
</head>
<body>
<header>
  <div>
    <h1>SmartDrive Storage Audit &amp; Squarified Treemap</h1>
    <div class="subtitle">Root: <strong>{root_path}</strong> &bull; Filesystem: <strong>{fs_summary}</strong></div>
  </div>
  <div class="subtitle">Generated: {generated_at}</div>
</header>
{note_banner}
<div class="cards">
  <div class="card">
    <div class="card-label">Logical Size</div>
    <div class="card-value">{_esc(format_bytes(logical_bytes))}</div>
    <div class="card-sub">{logical_bytes:,} bytes</div>
  </div>
  <div class="card">
    <div class="card-label">Allocated on Disk</div>
    <div class="card-value">{_esc(format_bytes(allocated_bytes))}</div>
    <div class="card-sub">Cluster: {cluster_kb} KB ({cluster_size:,} B)</div>
  </div>
  <div class="card">
    <div class="card-label">Cluster Slack Waste</div>
    <div class="card-value">{_esc(format_bytes(slack_bytes))}</div>
    <div class="card-sub">{slack_pct:.2f}% slack overhead</div>
  </div>
  <div class="card">
    <div class="card-label">Files &amp; Folders</div>
    <div class="card-value">{format_count(total_files)}</div>
    <div class="card-sub">{format_count(total_dirs)} directories</div>
  </div>
</div>

<section class="section">
  <div class="section-header">
    <h2 class="section-title">Squarified Folder Treemap (Depth &le; 3)</h2>
    <div class="breadcrumb" id="treemap-breadcrumb">
      <button type="button" class="crumb-btn" onclick="resetTreemap()">Root ({root_path})</button>
    </div>
  </div>
  <div id="treemap-canvas">{static_treemap_html}</div>
</section>

<section class="section">
  <div class="tabs">
    <button type="button" class="tab-btn active" onclick="showTab('dirs', this)">Top 50 Largest Folders ({len(top_dirs)})</button>
    <button type="button" class="tab-btn" onclick="showTab('files', this)">Top 50 Largest Files ({len(top_files)})</button>
    <button type="button" class="tab-btn" onclick="showTab('slack', this)">Cluster Slack Hotspots ({len(top_slack)})</button>
  </div>
  <div id="tab-dirs" class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Directory (Relative Path)</th>
          <th class="num">Files</th>
          <th class="num">Logical Size</th>
          <th class="num">Allocated Size</th>
          <th class="num">Cluster Slack</th>
          <th class="num">Slack %</th>
        </tr>
      </thead>
      <tbody>
        {"".join(dir_rows)}
      </tbody>
    </table>
  </div>
  <div id="tab-files" class="table-wrap" style="display:none;">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>File (Relative Path)</th>
          <th>Category</th>
          <th class="num">Logical Size</th>
          <th class="num">Allocated Size</th>
          <th class="num">Cluster Slack</th>
        </tr>
      </thead>
      <tbody>
        {"".join(file_rows)}
      </tbody>
    </table>
  </div>
  <div id="tab-slack" class="table-wrap" style="display:none;">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Directory</th>
          <th class="num">Files</th>
          <th class="num">Logical Size</th>
          <th class="num">Slack Wasted</th>
          <th class="num">Waste %</th>
        </tr>
      </thead>
      <tbody>
        {"".join(slack_rows)}
      </tbody>
    </table>
  </div>
</section>

<script>
const TREEMAP_DATA = {treemap_json};
let currentStack = [TREEMAP_DATA];

function showTab(name, btn) {{
  ['dirs', 'files', 'slack'].forEach(function(id) {{
    const el = document.getElementById('tab-' + id);
    if (el) el.style.display = (id === name) ? 'block' : 'none';
  }});
  document.querySelectorAll('.tab-btn').forEach(function(b) {{
    b.classList.remove('active');
  }});
  if (btn) btn.classList.add('active');
}}

function renderNode(node) {{
  const canvas = document.getElementById('treemap-canvas');
  const bc = document.getElementById('treemap-breadcrumb');
  if (!canvas || !bc) return;
  bc.innerHTML = '';
  currentStack.forEach(function(item, idx) {{
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'crumb-btn';
    btn.textContent = idx === 0 ? 'Root' : item.name;
    btn.onclick = function() {{
      currentStack = currentStack.slice(0, idx + 1);
      renderNode(currentStack[currentStack.length - 1]);
    }};
    bc.appendChild(btn);
  }});

  const children = node.children || [];
  if (!children.length) return;
  canvas.innerHTML = '';
  children.forEach(function(child) {{
    const rect = child.rect || [0, 0, 0, 0];
    if (rect[2] <= 0.1 || rect[3] <= 0.1) return;
    const div = document.createElement('div');
    div.className = 'tm-node';
    div.style.left = rect[0] + '%';
    div.style.top = rect[1] + '%';
    div.style.width = rect[2] + '%';
    div.style.height = rect[3] + '%';
    div.style.background = child.color || '#2563eb';
    div.title = child.rel_path + ' | Logical: ' + child.bytes_fmt + ' | Allocated: ' + child.allocated_fmt + ' | Slack: ' + child.slack_fmt;

    const header = document.createElement('div');
    header.className = 'tm-header';
    const titleSpan = document.createElement('span');
    titleSpan.className = 'tm-title';
    titleSpan.textContent = child.name;
    const metaSpan = document.createElement('span');
    metaSpan.className = 'tm-meta';
    metaSpan.textContent = child.bytes_fmt;
    header.appendChild(titleSpan);
    header.appendChild(metaSpan);
    div.appendChild(header);

    if (child.children && child.children.length > 0) {{
      div.onclick = function() {{
        currentStack.push(child);
        renderNode(child);
      }};
    }}
    canvas.appendChild(div);
  }});
}}

function resetTreemap() {{
  currentStack = [TREEMAP_DATA];
  renderNode(TREEMAP_DATA);
}}
</script>
</body>
</html>
"""


# ------------------------------------------------------------------------------
# Module-level Fallbacks for AuditResult when Auditor is Detached
# ------------------------------------------------------------------------------

def _fallback_markdown_report(data: Dict[str, Any]) -> str:
    auditor = StorageAuditor(data.get("root_path", "."))
    return auditor.generate_markdown_report(data)


def _fallback_ascii_table_report(data: Dict[str, Any]) -> str:
    auditor = StorageAuditor(data.get("root_path", "."))
    return auditor.generate_ascii_table_report(data)


def _fallback_html_report(data: Dict[str, Any]) -> str:
    return _render_self_contained_html(data)


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

