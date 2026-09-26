"""smart_drive.core.auto_zoner - Autonomous Drive Auto-Zoning, Organization & Anti-Slack Packaging Engine.

Provides automated intelligence for:
1. Detecting loose/unorganized files or directories in root or unclassified locations.
2. Classifying items into the 6 standard taxonomies (01_AI_Models .. 06_Archives_Storage).
3. Two-phase execution: generating safe dry-run ZoningPlan and applying with conflict resolution.
4. Ensuring anti-indexing shields (.metadata_never_index, .fseventsd/no_log, .noindex) are active.
5. Inviolable whitelist security (never moves root setup scripts, manifests, or existing taxonomies).

Zero external dependencies (Python 3.8+ standard library only).
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from smart_drive.core.config import (
    CLUSTER_SIZE_BYTES,
    calculate_allocated_bytes,
    calculate_slack_bytes,
    is_protected_root_dir,
    is_protected_root_file,
)
from smart_drive.core.exfat_compat import ExFatEngine


@dataclasses.dataclass(frozen=True)
class ZoningAction:
    """Represents a planned or executed file/directory relocation."""
    src_path: str
    dest_path: str
    target_taxonomy: str
    reason: str
    is_dir: bool
    nominal_size: int
    slack_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src_path": self.src_path,
            "dest_path": self.dest_path,
            "target_taxonomy": self.target_taxonomy,
            "reason": self.reason,
            "is_dir": self.is_dir,
            "nominal_size": self.nominal_size,
            "slack_bytes": self.slack_bytes,
        }


class AutoZoner:
    """Intelligent autonomous drive organization and anti-slack rebalancing engine."""

    # 6 Standard Taxonomies on SSD
    TAXONOMIES = {
        "01_AI_Models": "01_AI_Models",
        "02_Learning_Knowledge": "02_Learning_Knowledge",
        "03_Development_Projects": "03_Development_Projects",
        "04_System_Workspaces": "04_System_Workspaces",
        "05_Dev_Toolbox": "05_Dev_Toolbox",
        "06_Archives_Storage": "06_Archives_Storage",
    }

    AI_MODEL_EXTENSIONS = frozenset({
        ".gguf", ".safetensors", ".pt", ".pth", ".bin", ".onnx", ".tflite", ".ckpt"
    })

    LEARNING_EXTENSIONS = frozenset({
        ".pdf", ".epub", ".mobi", ".djvu", ".azw3", ".ipynb"
    })

    ARCHIVE_EXTENSIONS = frozenset({
        ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".iso", ".dmg"
    })

    TOOLBOX_EXTENSIONS = frozenset({
        ".sh", ".bash", ".zsh", ".ps1"
    })

    PROJECT_INDICATORS = frozenset({
        ".git", "package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml", "cmakelists.txt"
    })

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.compat = ExFatEngine()

    def ensure_taxonomies_exist(self) -> List[str]:
        """Ensures the 6 core taxonomy directories exist on disk."""
        created = []
        for tax in self.TAXONOMIES.values():
            tax_path = os.path.join(self.root, tax)
            if not os.path.exists(tax_path):
                os.makedirs(tax_path, exist_ok=True)
                created.append(tax)
        return created

    def ensure_anti_indexing_shields(self) -> Dict[str, bool]:
        """Ensures essential Spotlight and FSEvents anti-indexing markers exist in root."""
        results = {}
        # 1. Root .metadata_never_index
        marker_file = os.path.join(self.root, ".metadata_never_index")
        if not os.path.exists(marker_file):
            try:
                with open(marker_file, "w", encoding="utf-8") as f:
                    f.write("# Spotlight indexing disabled for Kingston SSD performance\n")
                results[".metadata_never_index"] = True
            except OSError:
                results[".metadata_never_index"] = False
        else:
            results[".metadata_never_index"] = True

        # 2. .fseventsd/no_log
        fsevents_dir = os.path.join(self.root, ".fseventsd")
        fsevents_file = os.path.join(fsevents_dir, "no_log")
        try:
            os.makedirs(fsevents_dir, exist_ok=True)
            if not os.path.exists(fsevents_file):
                with open(fsevents_file, "w", encoding="utf-8") as f:
                    f.write("")
            results[".fseventsd/no_log"] = True
        except OSError:
            results[".fseventsd/no_log"] = False

        return results

    def classify_item(self, item_path: str) -> Optional[Tuple[str, str, str]]:
        """Classifies a loose item into (target_taxonomy, target_subpath, reason).

        Returns None if item should not be relocated.
        """
        base_name = os.path.basename(item_path)
        is_dir = os.path.isdir(item_path)

        # 1. Whitelist guard: Never move protected items
        if is_dir:
            if is_protected_root_dir(base_name):
                return None
        else:
            if is_protected_root_file(base_name):
                return None

        # Hidden items at root (e.g. .DS_Store, .git)
        if base_name.startswith("."):
            return None

        # 2. Directory Classification
        if is_dir:
            try:
                entries = {e.name.lower() for e in os.scandir(item_path)}
            except (OSError, PermissionError):
                entries = set()

            if any(ind in entries for ind in self.PROJECT_INDICATORS):
                return ("03_Development_Projects", os.path.join("03_Development_Projects", base_name), "Detected development project indicator")

            base_lower = base_name.lower()
            if any(k in base_lower for k in ("model", "weights", "checkpoint", "gguf")):
                return ("01_AI_Models", os.path.join("01_AI_Models", base_name), "Detected AI model folder naming")

            if any(k in base_lower for k in ("learn", "course", "book", "note", "tutorial")):
                return ("02_Learning_Knowledge", os.path.join("02_Learning_Knowledge", base_name), "Detected educational/course folder naming")

            if any(k in base_lower for k in ("tool", "utility", "script")):
                return ("05_Dev_Toolbox", os.path.join("05_Dev_Toolbox", base_name), "Detected tool/utility folder naming")

            if any(k in base_lower for k in ("archive", "backup", "dump", "old_")):
                return ("06_Archives_Storage", os.path.join("06_Archives_Storage", base_name), "Detected archive/backup folder naming")

            return None

        # 3. File Classification
        ext = os.path.splitext(base_name)[1].lower()

        if ext in self.AI_MODEL_EXTENSIONS:
            return ("01_AI_Models", os.path.join("01_AI_Models", "weights", base_name), f"AI model weight file ({ext})")

        if ext in self.LEARNING_EXTENSIONS:
            return ("02_Learning_Knowledge", os.path.join("02_Learning_Knowledge", "inbox", base_name), f"Document/Learning material ({ext})")

        if ext in self.ARCHIVE_EXTENSIONS:
            return ("06_Archives_Storage", os.path.join("06_Archives_Storage", base_name), f"Archive package ({ext})")

        if ext in self.TOOLBOX_EXTENSIONS:
            return ("05_Dev_Toolbox", os.path.join("05_Dev_Toolbox", "scripts", base_name), f"Developer script ({ext})")

        return None

    def _get_item_size(self, path: str) -> Tuple[int, int]:
        """Calculates total nominal bytes and cluster slack bytes for file or directory."""
        if os.path.isfile(path) or os.path.islink(path):
            try:
                nom = os.path.getsize(path)
                slack = calculate_slack_bytes(nom)
                return nom, slack
            except OSError:
                return 0, 0

        total_nom = 0
        total_slack = 0
        try:
            for root, _, files in os.walk(path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(fp)
                        total_nom += sz
                        total_slack += calculate_slack_bytes(sz)
                    except OSError:
                        pass
        except OSError:
            pass
        return total_nom, total_slack

    def generate_plan(self, scan_dir: Optional[str] = None) -> List[ZoningAction]:
        """Scans the specified directory (defaults to drive root) and generates a ZoningPlan."""
        target_dir = os.path.abspath(scan_dir or self.root)
        plan: List[ZoningAction] = []

        try:
            with os.scandir(target_dir) as it:
                for entry in it:
                    classified = self.classify_item(entry.path)
                    if not classified:
                        continue

                    tax, rel_dest, reason = classified
                    dest_path = os.path.join(self.root, rel_dest)

                    if os.path.abspath(entry.path) == os.path.abspath(dest_path):
                        continue

                    nom_size, slack_size = self._get_item_size(entry.path)
                    action = ZoningAction(
                        src_path=entry.path,
                        dest_path=dest_path,
                        target_taxonomy=tax,
                        reason=reason,
                        is_dir=entry.is_dir(),
                        nominal_size=nom_size,
                        slack_bytes=slack_size,
                    )
                    plan.append(action)
        except (OSError, PermissionError):
            pass

        return plan

    def resolve_destination_conflict(self, dest_path: str) -> str:
        """Appends suffix to filename if destination already exists to prevent data overwriting."""
        if not os.path.exists(dest_path):
            return dest_path

        base_dir = os.path.dirname(dest_path)
        base_name = os.path.basename(dest_path)
        stem, ext = os.path.splitext(base_name)

        counter = 1
        while True:
            candidate = os.path.join(base_dir, f"{stem}_{counter}{ext}")
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def apply_plan(self, plan: List[ZoningAction]) -> Dict[str, Any]:
        """Applies a ZoningPlan safely: creates destination folders, avoids overwrites, moves items."""
        self.ensure_taxonomies_exist()
        moved: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        total_nominal_moved = 0
        total_slack_rebalanced = 0

        for action in plan:
            base_name = os.path.basename(action.src_path)
            if action.is_dir and is_protected_root_dir(base_name):
                continue
            if not action.is_dir and is_protected_root_file(base_name):
                continue

            if not os.path.exists(action.src_path):
                continue

            dest = self.resolve_destination_conflict(action.dest_path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)

            try:
                shutil.move(action.src_path, dest)
                moved.append({
                    "src": action.src_path,
                    "dest": dest,
                    "taxonomy": action.target_taxonomy,
                    "reason": action.reason,
                    "size_bytes": action.nominal_size,
                })
                total_nominal_moved += action.nominal_size
                total_slack_rebalanced += action.slack_bytes
            except Exception as e:
                errors.append({
                    "src": action.src_path,
                    "error": str(e),
                })

        return {
            "success": len(errors) == 0,
            "moved_count": len(moved),
            "error_count": len(errors),
            "total_bytes_moved": total_nominal_moved,
            "total_slack_rebalanced": total_slack_rebalanced,
            "moved": moved,
            "errors": errors,
        }


__all__ = [
    "ZoningAction",
    "AutoZoner",
]
