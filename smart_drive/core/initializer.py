"""smart_drive.core.initializer - 1-Touch SSD Initialization Engine & Preset Profiles.

Creates standard taxonomies, anti-indexing shields, AI agent manifests,
and SQLite FTS5 database for Kingston XS2000 external SSDs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from smart_drive.core.config import CLUSTER_SIZE_BYTES, STANDARD_TAXONOMIES
from smart_drive.core.sentinel import check_and_heal_shields
from smart_drive.indexer.db import DatabaseManager

PROFILES = {
    "general-workspace": {
        "description": "Universal workspace for active/archive code, docs, learning notes",
        "taxonomies": [
            "01_AI_Models",
            "02_Learning_Knowledge",
            "03_Development_Projects",
            "04_System_Workspaces",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ],
        "subdirs": [
            "02_Learning_Knowledge/Notes",
            "02_Learning_Knowledge/References",
            "03_Development_Projects/active",
            "03_Development_Projects/archive",
            "05_Dev_Toolbox/scripts",
        ],
    },
    "ai-developer": {
        "description": "AI model engineering, fine-tuning checkpoints, GGUF trees, and agent workspaces",
        "taxonomies": [
            "01_AI_Models",
            "02_Learning_Knowledge",
            "03_Development_Projects",
            "04_System_Workspaces",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ],
        "subdirs": [
            "01_AI_Models/checkpoints",
            "01_AI_Models/gguf",
            "01_AI_Models/safetensors",
            "01_AI_Models/datasets",
            "03_Development_Projects/ai_agents",
            "05_Dev_Toolbox/quantization",
        ],
    },
    "data-science": {
        "description": "Data analysis, Parquet vs CSV workflows, EDA notebooks, and pipelines",
        "taxonomies": [
            "01_AI_Models",
            "02_Learning_Knowledge",
            "03_Development_Projects",
            "04_System_Workspaces",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ],
        "subdirs": [
            "03_Development_Projects/notebooks",
            "03_Development_Projects/data_raw",
            "03_Development_Projects/data_processed",
            "03_Development_Projects/pipelines",
            "05_Dev_Toolbox/visualizations",
        ],
    },
    "internal-developer-vault": {
        "description": "Internal secondary SSD workstation vault for AI models, workspaces, datasets & offloaded C-drive caches",
        "taxonomies": [
            "01_AI_Models",
            "02_Development_Workspaces",
            "03_Data_Vault",
            "04_System_Offload_Caches",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ],
        "subdirs": [
            "01_AI_Models/checkpoints",
            "01_AI_Models/gguf",
            "01_AI_Models/safetensors",
            "01_AI_Models/onnx",
            "02_Development_Workspaces/active",
            "02_Development_Workspaces/archive",
            "03_Data_Vault/datasets",
            "03_Data_Vault/databases",
            "04_System_Offload_Caches/huggingface",
            "04_System_Offload_Caches/ollama",
            "04_System_Offload_Caches/pip",
            "04_System_Offload_Caches/uv",
            "04_System_Offload_Caches/npm",
            "04_System_Offload_Caches/gradle",
            "05_Dev_Toolbox/scripts",
            "05_Dev_Toolbox/sdks",
            "06_Archives_Storage/backups",
        ],
    },
    "workstation-hybrid": {
        "description": "Internal secondary drive (D:, E:) hybrid profile accommodating existing Games, Windows Apps, and University Coursework",
        "taxonomies": [
            "01_AI_Models",
            "02_Learning_Knowledge",
            "03_Development_Projects",
            "04_System_Workspaces",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ],
        "subdirs": [
            "02_Learning_Knowledge/FPTU",
            "02_Learning_Knowledge/Personal_Books",
            "02_Learning_Knowledge/Notes",
            "03_Development_Projects/active",
            "03_Development_Projects/archive",
            "05_Dev_Toolbox/OEM_Drivers",
            "05_Dev_Toolbox/Installers",
            "05_Dev_Toolbox/scripts",
        ],
    },
}

AGENTS_MD_TEMPLATE = """# AGENTS.md - Autonomous AI Coding Agent Manifest

Target: Autonomous AI Coding Agents (Antigravity 2.0, Claude Code, Codex, Cursor).
Workspace: External High-Speed SSD (exFAT, Allocation Unit: 512 KB).

## 1. Fast-Path Protocol (<10ms)
- Never traverse disk with recursive find or grep.
- Use built-in SQLite FTS5 search engine:
  ```bash
  smart-drive search "<query>"
  ```

## 2. Hard Invariants & Zero Data Loss
- Standard taxonomies (01_AI_Models .. 06_Archives_Storage) must never be deleted.
- Never place symlinks or illegal Windows characters (`\\ / : * ? " < > |`) on exFAT.
- Cluster slack prevention: bundle micro-files and avoid unignored build caches.
"""

GEMINI_MD_TEMPLATE = """# GEMINI.md - SSD Workspace Guidelines & Directives

Environment: High-Speed External SSD (exFAT).
Allocation Unit: 524,288 bytes (512 KB).

## Automated SSD Health Check
- Run `smart-drive sentinel` or `smart-drive agent-check` to verify mount, shields, and index.
- Use `smart-drive clean` (Tier 1 safe by default, `--apply` required for purge).
- Use `smart-drive organize` for auto-zoning and anti-slack rebalancing.
"""

CLAUDE_MD_TEMPLATE = """# CLAUDE.md - AI Coding Instructions for SSD Workspace

- Filesystem: exFAT (512KB clusters).
- Avoid loose micro-files to minimize cluster slack waste.
- Search files using `smart-drive search "<query>"`.
- Verify SSD health using `smart-drive sentinel`.
"""


class DriveInitializer:
    """Initializes drive root with taxonomies, shields, manifests, and search index."""

    def __init__(self, target_path: str) -> None:
        self.target_path = os.path.abspath(target_path)

    @classmethod
    def list_profiles(cls) -> Dict[str, str]:
        """Returns available profile names and descriptions."""
        return {k: v["description"] for k, v in PROFILES.items()}

    def initialize(
        self,
        target_path: Optional[str] = None,
        profile: str = "general-workspace",
        force: bool = False,
    ) -> Dict[str, Any]:
        """Initializes target path with selected profile."""
        root = os.path.abspath(target_path or self.target_path)
        os.makedirs(root, exist_ok=True)

        if profile not in PROFILES:
            profile = "general-workspace"

        profile_data = PROFILES[profile]

        # 1. Create taxonomies and subdirectories
        created_dirs: List[str] = []
        for tax in profile_data["taxonomies"]:
            tax_path = os.path.join(root, tax)
            if not os.path.exists(tax_path):
                os.makedirs(tax_path, exist_ok=True)
                created_dirs.append(tax)

        for subdir in profile_data.get("subdirs", []):
            sub_path = os.path.join(root, subdir)
            if not os.path.exists(sub_path):
                os.makedirs(sub_path, exist_ok=True)
                created_dirs.append(subdir)

        # 2. Anti-indexing shields
        shields_res = check_and_heal_shields(root, auto_heal=True)

        # 3. AI Agent Manifests
        manifests = {
            "AGENTS.md": AGENTS_MD_TEMPLATE,
            "GEMINI.md": GEMINI_MD_TEMPLATE,
            "CLAUDE.md": CLAUDE_MD_TEMPLATE,
        }
        created_manifests: List[str] = []
        for filename, content in manifests.items():
            f_path = os.path.join(root, filename)
            if not os.path.exists(f_path) or force:
                with open(f_path, "w", encoding="utf-8") as f:
                    f.write(content)
                created_manifests.append(filename)

        # 4. Search Database Initialization
        db_dir = os.path.join(root, ".smart_drive")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "index.db")
        db = DatabaseManager(db_path)
        db.initialize_schema()

        return {
            "status": "initialized",
            "root": root,
            "profile": profile,
            "created_dirs": created_dirs,
            "shields": shields_res,
            "manifests": created_manifests,
            "database_initialized": os.path.exists(db_path),
            "db_path": db_path,
        }


__all__ = [
    "AGENTS_MD_TEMPLATE",
    "CLAUDE_MD_TEMPLATE",
    "DriveInitializer",
    "GEMINI_MD_TEMPLATE",
    "PROFILES",
]
