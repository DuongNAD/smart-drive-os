"""smart_drive.core.initializer - 1-Touch SSD Initialization Engine & Preset Profiles.

Creates standard taxonomies, anti-indexing shields, AI agent manifests,
and SQLite FTS5 database for Kingston XS2000 external SSDs.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
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

Target: Autonomous AI Coding Agents (Antigravity 2.0, Claude Code, Codex, GLM, Cursor, Windsurf).
Workspace: External High-Speed SSD. exFAT with a 512 KB allocation unit is the design target; this folder may sit on another filesystem, so run `smart-drive status` to see the real one.

## 1. Fast-Path Protocol (<10ms)
- Never traverse disk with recursive find or grep.
- Use built-in SQLite FTS5 search engine:
  ```bash
  smart-drive search "<query>"
  ```
- Search lessons and research with: `smart-drive search --kind slide|fact|capture "<query>"` (MCP `ssd_search` kind/content).

## 2. Hard Invariants & Zero Data Loss
- Standard taxonomies (01_AI_Models .. 06_Archives_Storage) must never be deleted.
- Never place symlinks or illegal Windows characters (`\\ / : * ? " < > |`) on exFAT.
- Cluster slack prevention: bundle micro-files and avoid unignored build caches.
- Gitignore governance: run `smart-drive gitignore audit` before committing.

## 3. First-Time Setup (MCP ecosystem)
- Run once per machine: `smart-drive setup --json`. It installs nothing until the user agrees: it exits 4 and lists the MCP servers (name + what each does + the coding agents it would be added to). Show that list to the user with your own question/confirmation prompt, ask which to install or add, then run `smart-drive setup --only <approved,names> --yes`. Never pass `--yes` without the user's answer. `--dry-run` previews, `--check --json` reports state (exit code 3 = setup needed).
- Needs Node >= 22.4 and git. Once approved it fetches the MCP servers (Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius, Mira) and registers them with the installed coding agents.

## 4. Atomic Commit & Verification Discipline
Every AI agent working in this workspace must commit in small, verified steps:
- **One logical change per commit**: Keep each commit small and self-contained (`fix(scope): ...`, `feat(scope): ...`, `test(scope): ...`, `docs: ...`). Never bundle unrelated fixes or features into one commit.
- **Explain why, not just what**: State the root cause or invariant protected in the commit body.
- **Lock every fix with a regression test**: Add or update a deterministic test in the same commit as the code change, and run the test suite before committing so every commit passes on its own.
- **Audit before staging**: Run `smart-drive gitignore audit` and check `git status` / `git diff --staged` before committing so secrets, personal learning state (`.vega/`, `.polaris/`, `.sirius/`), and build/OS junk are never tracked.

## 5. Moving Folders Without Breaking Coding Agents
Coding agents, IDEs, and shortcuts remember projects and MCP servers by absolute path, and exFAT cannot leave a link at the old place:
- Move folders with `smart-drive organize --apply` (or `Smart_Organize.*`): it skips system-referenced folders (`protected_by_system`: `PATH`, Services, Installed Apps, Startup, `.lnk`, game libraries) and relinks coding agents, VS Code/JetBrains/Obsidian, and `.lnk` shortcuts to the new places. Preview with `--dry-run`. `classify --apply` (sorting by file content) relinks the same way but moves only the files directly in the scanned folder; `--recursive` also looks inside subfolders, yet never takes apart an app, library, or project folder (programs, launcher scripts, manifests, `.git`): those are listed as `skipped_bundles`.
- Revert the latest move batch and relink back to original paths with `smart-drive undo --apply` (`--dry-run` default).
- After moving or renaming a folder by hand, run `smart-drive relink "<old path>" "<new path>"` (`--dry-run` previews). With no arguments it re-applies the latest organize/classify batch.
- Lost track of moved folders or broken shortcuts/IDE entries? `smart-drive relink --find` and `smart-drive paths --broken` list stale paths and where each folder went; `relink --find --apply --wait` or `paths --broken --fix` repairs the sure matches once open apps are closed. A match marked `?` is only a guess (the folder it found may be another project, and the old one deleted): confirm it with the user, then run `smart-drive relink "<old>" "<new>"`. In a terminal, `--find --apply --ask` (menu option `[8]`) asks the user y/N for each `?` match: let the user answer, never answer for them.
- Run `smart-drive checkup` for a 1-screen health score (`0-100`), `smart-drive audit --html` for an offline HTML treemap, and `smart-drive clean --list-quarantine` / `--restore latest` to inspect or restore quarantined Tier 3 junk (`clean --apply` deletes Tier 1-2 junk at once).
- Close Claude desktop, Claude Code, Codex, and open IDEs before relinking (or relink again after closing them), then restart the agents so they load the new paths.
"""

GEMINI_MD_TEMPLATE = """# GEMINI.md - SSD Workspace Guidelines & Directives

Environment: High-Speed External SSD.
Allocation Unit: exFAT with 524,288 bytes (512 KB) is the design target; this folder may sit on another filesystem, so run `smart-drive status` to see the real one.

## Automated SSD Health Check
- Run `smart-drive sentinel` or `smart-drive agent-check` to verify mount, shields, and index; run `smart-drive checkup` for a 1-screen `0–100` health score and next steps, or `smart-drive audit --html` for an offline HTML treemap.
- Search lessons and research with `smart-drive search --kind slide|fact|capture "<query>"` (MCP ssd_search kind/content).
- Use `smart-drive clean` (Tier 1 and dry-run by default; `--apply` deletes regenerable Tier 1-2 junk at once, while Tier 3 junk (`--tier 3`: logs, temp files, dumps) moves to reversible `.smart_drive/quarantine/`; restore with `--restore latest`, list with `--list-quarantine`).
- Use `smart-drive organize` (or `Smart_Organize.*`) for auto-zoning and anti-slack rebalancing (`protected_by_system` pins folders referenced by Windows `PATH`, Services, Installed Apps, Startup, `.lnk`, and game libraries); revert the latest move batch with `smart-drive undo --apply`.
- After moving a folder by hand, run `smart-drive relink "<old>" "<new>"` so coding agents, IDEs, Obsidian, and `.lnk` shortcuts follow; `organize --apply` does this itself (`--dry-run` previews). Use `smart-drive paths --broken` (`--fix`) to inspect or repair broken shortcuts and IDE entries.
- Run `smart-drive gitignore audit` before committing.

## MCP Ecosystem Setup
- Run `smart-drive setup --json` once per machine. It installs nothing until the user agrees: it exits 4 and lists the MCP servers (name + what each does + the coding agents it would be added to). Show that list to the user with your own question/confirmation prompt, ask which to install or add, then run `smart-drive setup --only <approved,names> --yes` to fetch and register them. Never pass `--yes` without the user's answer. `--dry-run` previews.

## Atomic Commit & Verification Discipline
- Make small, self-contained commits (one logical fix or feature per commit: `fix(scope): ...`, `feat(scope): ...`).
- Include a regression test in the same commit and run the test suite before committing.
- Explain the root cause or invariant in the commit body, and verify `git diff --staged` contains no secrets, personal learning state, or exFAT junk.
"""

CLAUDE_MD_TEMPLATE = """# CLAUDE.md - AI Coding Instructions for SSD Workspace

- Filesystem: exFAT with 512 KB clusters is the design target; this folder may sit on another filesystem, so run `smart-drive status` to see the real one.
- Avoid loose micro-files to minimize cluster slack waste.
- Search files using `smart-drive search "<query>"`.
- Search lessons and research with `smart-drive search --kind slide|fact|capture "<query>"` (MCP ssd_search kind/content).
- Verify SSD health using `smart-drive sentinel` or `smart-drive checkup` (`smart-drive audit --html` exports an offline HTML treemap).
- Move folders with `smart-drive organize --apply` (or `Smart_Organize.*`, which skips `protected_by_system` folders and relinks agents/IDEs/shortcuts); revert the latest move batch with `smart-drive undo --apply`. After moving a folder by hand, run `smart-drive relink "<old>" "<new>"` (`--dry-run` previews) or `smart-drive paths --broken` (`--fix`).
- Clean junk with `smart-drive clean` (dry-run by default; `--apply` deletes regenerable Tier 1-2 junk at once, and only `--tier 3` junk such as logs, temp files, and dumps goes to a restorable quarantine: `--list-quarantine` and `--restore latest` inspect/restore).
- Run `smart-drive gitignore audit` before committing.
- Commit in small, atomic steps: one logical fix or feature per commit (`fix(scope): ...`, `feat(scope): ...`), paired with a regression test in the same commit, and run the test suite before committing.
- First time on a machine: run `smart-drive setup --json`. It installs nothing until the user agrees: it exits 4 and lists the MCP servers (name + what each does + the coding agents it would be added to). Show that list to the user with your own question/confirmation prompt, ask which to install or add, then run `smart-drive setup --only <approved,names> --yes`. Never pass `--yes` without the user's answer. `--dry-run` previews.
"""


def find_enclosing_code_dir(path: str) -> Optional[str]:
    """Returns the enclosing directory that contains a `smart_drive/` package folder, if any."""
    curr = os.path.abspath(path)
    while True:
        if os.path.isdir(os.path.join(curr, "smart_drive")):
            return curr
        parent = os.path.dirname(curr)
        if not parent or parent == curr:
            break
        curr = parent
    return None


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
        code_dir = find_enclosing_code_dir(root)
        if code_dir is not None:
            raise ValueError(
                f"Refusing to initialize SmartDrive workspace inside code directory '{code_dir}' "
                "(contains 'smart_drive/'). Initialize the volume root instead."
            )
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
            if os.path.exists(f_path):
                if force:
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    bak_path = os.path.join(root, f"{filename}.bak-{timestamp}")
                    shutil.copy2(f_path, bak_path)
                    with open(f_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    created_manifests.append(filename)
            else:
                with open(f_path, "w", encoding="utf-8") as f:
                    f.write(content)
                created_manifests.append(filename)

        # 4. Search Database Initialization
        db_dir = os.path.join(root, ".smart_drive")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "index.db")
        db = DatabaseManager(db_path)
        try:
            db.initialize_schema()
        finally:
            db.close()

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
