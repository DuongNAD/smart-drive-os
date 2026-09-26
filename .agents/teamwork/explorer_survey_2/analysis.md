# Comprehensive Analysis: Subsystems, Data Flow, Taxonomy & Safe Cleanup Architecture

**Document ID**: SMARTDRIVE-V110-SURVEY-SUB-02  
**Date**: 2026-09-26  
**Author**: Explorer 2 (Subsystem & Data Flow Explorer)  
**Target Milestone**: SmartDrive-OS v1.1.0 Upgrade Survey  

---

## 1. Executive Summary

SmartDrive-OS is an autonomous governance and instant search suite designed for high-performance external SSDs (Kingston XS2000 2TB exFAT, 512KB allocation block geometry). The entire system strictly adheres to a **Zero External Dependency** invariant (100% Python standard library).

This investigation provides a comprehensive mapping of existing data management, taxonomy folders, 3-tier safe cleanup mechanics, hashing/traversal routines, and concrete architectural integration blueprints for the v1.1.0 new capabilities:
- **`smart-drive ui`**: Zero-dependency local web dashboard powered by Python `http.server`.
- **`smart-drive snapshot`**: Point-in-time recovery and SHA-256 integrity verification engine.
- **`smart-drive classify`**: Deep file format recognition and intelligent taxonomy auto-tagging.

---

## 2. Taxonomy Management & Scanning Architecture

### 2.1 Taxonomy Folder Definitions & Inviolable Root Boundaries

The prompt references standard taxonomy names:
`01_System_Boot`, `02_Learning_Knowledge`, `03_Development_Projects`, `04_Archive_ColdStorage`, `05_Dev_Toolbox`, `06_Temporary_Trash`.

In the existing codebase (`smart_drive/core/config.py`, `smart_drive/core/initializer.py`, `smart_drive/core/auto_zoner.py`, `smart_drive/core/auditor.py`), the taxonomy system is defined as follows:

| Codebase Canonical Name | Config Display / Aliases | Primary Content & Purpose |
|-------------------------|--------------------------|---------------------------|
| `01_AI_Models` | `01_AI_Models` (alias `01_System_Boot` concept) | AI model weights, checkpoints, GGUF, Safetensors, ONNX, PyTorch `.pt`/`.pth`, HuggingFace configs |
| `02_Learning_Knowledge` | `02_Learning_Knowledge` | Educational PDFs, ePubs, books, study notes, research papers, Jupyter notebooks |
| `03_Development_Projects` | `03_Personal_Documents`, `03_Development_Projects` | Active/archive source code repositories (Git, Node, Python, Rust, C++) |
| `04_System_Workspaces` | `04_Creative_Assets`, `04_Archive_ColdStorage` | Agent workspaces, creative assets, multi-project workspace trees, cold storage |
| `05_Dev_Toolbox` | `05_Dev_Toolbox` | Developer scripts, shell tools, quantization utilities, batch commands |
| `06_Archives_Storage` | `06_Archives_Storage` (alias `06_Temporary_Trash` / cold archive) | Compressed tarballs, zip archives, raw data dumps, ISOs |

#### Inviolable Root Directories (`PROTECTED_ROOT_DIRS` & `PROTECTED_CORE_TAXONOMIES`)
Defined in `smart_drive/core/config.py` (lines 111–165):
```python
PROTECTED_CORE_TAXONOMIES: Tuple[str, ...] = (
    "01_AI_Models",
    "02_Learning_Knowledge",
    "03_Personal_Documents",
    "03_Development_Projects",
    "04_Creative_Assets",
    "04_System_Workspaces",
    "05_Dev_Toolbox",
    "06_Archives_Storage",
)
```
- Casefolded in `PROTECTED_ROOT_DIRS` to ensure exFAT case-insensitivity on Windows and macOS.
- Root directories can **never** be deleted, purged, or renamed by the purge or auto-zoning engines.

### 2.2 Filesystem Traversal Engine (`FastDirectoryScanner`)
Defined in `smart_drive/core/scanner.py` (354 lines):
- **Iterative Stack DFS**: `while stack: current_dir, depth = stack.pop()` prevents Python `RecursionError` regardless of folder depth.
- **Low Memory Overhead**: `ScanEntry` uses `slots=True` (or `__slots__` on Python <3.10), consuming ~112 bytes per record compared to ~352 bytes for default dictionary-backed instances.
- **Zero-Syscall Efficiency**: Uses `os.scandir(follow_symlinks=False)` enclosed in `with scandir_it:` context managers to release file descriptors immediately.
- **Absolute exFAT Symlink Guard**: Skips all symlinks by default (`is_sym and not self.follow_symlinks`), incrementing `symlinks_skipped` to prevent circular references and exFAT corruption.
- **Default Exclusions**: `DEFAULT_EXCLUDE_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".Spotlight-V100", ".Trashes", ".git", ".agents"}` evaluated case-insensitively via `_exclude_dirs_lower`.
- **Path Normalization**: Uses `ExFatEngine.normalize_rel_path()` ensuring forward slash `'/'` separators across both Windows and macOS.
- **Streaming Pipeline**: `scan_iter()`, `scan_files()`, `scan_dirs()` allow pure generator streaming directly into the auditor, cleaner, and indexer without holding millions of entries in RAM.

### 2.3 Storage Audit & 512KB Cluster Slack Calculation (`StorageAuditor`)
Defined in `smart_drive/core/auditor.py` (847 lines):
- **512KB Cluster Math**:
  ```python
  # 0-byte files consume 0 clusters (0 bytes) in exFAT directory table
  # Files > 0 bytes: ceil(nominal_size / 524288) * 524288
  allocated = calculate_allocated_bytes(size, 524288)
  slack = allocated - size
  slack_percentage = (slack / allocated) * 100.0
  ```
- **Taxonomy Classification**: `classify_taxonomy(rel_path)` extracts the top-level path segment and maps it against canonical taxonomies, `Workspaces`, `System`, or `Other`.
- **Category Classification**: `classify_category()` maps extensions to 7 functional groups: `AI Models`, `Code`, `Books/Learning`, `Docs`, `Media`, `Archives`, `System Junk`, plus `Other`.
- **Subtree Rollup (`DirectoryNode`)**: Recursively computes direct and recursive files, direct and recursive bytes, and direct/recursive slack. Identifies top slack and top size directories via `get_top_slack_dirs()` and `get_top_size_dirs()`.
- **Multi-Format Reports**: `AuditResult` supports `.to_json()`, `.to_markdown()`, `.to_ascii_table()`, and file exports.

### 2.4 Auto-Zoning & Anti-Slack Engine (`AutoZoner`)
Defined in `smart_drive/core/auto_zoner.py` (322 lines):
- Inspects unclassified/loose root items.
- Identifies projects via `PROJECT_INDICATORS` (`.git`, `package.json`, `pyproject.toml`, `cargo.toml`, `go.mod`, etc.) -> routes to `03_Development_Projects`.
- Identifies model weights (`.gguf`, `.safetensors`, `.pt`, `.pth`, etc.) -> routes to `01_AI_Models/weights`.
- Identifies books/notes (`.pdf`, `.epub`, etc.) -> routes to `02_Learning_Knowledge/inbox`.
- Identifies archives (`.zip`, `.tar.gz`, etc.) -> routes to `06_Archives_Storage`.
- Two-phase execution: `generate_plan()` produces a dry-run `List[ZoningAction]`; `apply_plan()` executes relocations with conflict resolution (`resolve_destination_conflict` appends `_1`, `_2`).

---

## 3. Safe Cleanup Implementation & 3-Tier Safety Mechanics

### 3.1 3-Tier Classification Model (`JunkTier`)
Defined in `smart_drive/core/config.py` (lines 416–482):

```python
class JunkTier(IntEnum):
    TIER_1_SAFE = 1        # Completely safe: OS metadata, AppleDouble, bytecode caches
    TIER_2_DEV_CACHE = 2   # Build & test runner caches: pytest, ruff, mypy, dist, build
    TIER_3_SENSITIVE = 3   # Crash dumps, logs, scratch files (opt-in purge only)
```

#### Declarative Rule Matrix (`JUNK_RULES`):
1. **Tier 1 (Safe to Purge)**:
   - macOS: `._*` (AppleDouble resource forks), `.DS_Store`, `.Spotlight-V100`, `.Trashes`, `.TemporaryItems`
   - Windows: `Thumbs.db`, `ehthumbs.db`, `ehthumbs_vista.db`, `$RECYCLE.BIN`, `desktop.ini`
   - Python: `__pycache__`, `*.pyc`, `*.pyo`
2. **Tier 2 (Developer Artifacts & Test Caches - Regenerable)**:
   - Dev caches: `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.tox`, `.nox`
   - Test & build output: `.coverage`, `htmlcov`, `build`, `dist`, `*.egg-info`
3. **Tier 3 (Sensitive / Opt-In Only)**:
   - Dumps & crash logs: `*.dmp`, `core.*`, `hs_err_pid*.log`
   - Temp & swap files: `*.tmp`, `*.temp`, `*.swp`, `*.swo`, `*~`

### 3.2 Inviolable Protection Matrix (`SecurityGuard`)
Defined in `smart_drive/core/purge_engine.py` (lines 114–238):
Every unlinking or directory deletion is intercepted by `SecurityGuard.is_protected(target_path)`:
1. **Drive Root**: Cannot be unlinked or removed under any circumstances.
2. **Path Escape / Directory Traversal**: Targets resolving outside the canonical drive root (via `..` or symlink evasion) are strictly rejected.
3. **Git Repositories**: Any path containing `.git` as a component is strictly inviolable.
4. **Anti-Indexing Shields**: `.metadata_never_index` and `.fseventsd/no_log` can **never** be purged.
5. **Inviolable Root Directories**: Items in `PROTECTED_ROOT_DIRS` cannot be deleted as a folder; sub-files inside protected root directories cannot be deleted unless they explicitly match a known `JunkRule`.
6. **Inviolable Root Scripts & Manifests**: `PROTECTED_ROOT_FILES` and glob patterns (`smart_*`, `setup_*`, `quick_*`, `sync_repos.*`, `GEMINI.md`, `AGENTS.md`, `README.md`) are protected.

Any attempt to delete a guarded file raises `SecurityViolationError`.

### 3.3 Safe Purge Engine (`PurgeEngine`)
Defined in `smart_drive/core/purge_engine.py` (lines 243–635):
- **Dry-Run by Default**: Unless `dry_run=False` is explicitly passed (e.g. `--apply`), operations return `DeletionRecord` with `status="SIMULATED"` and no filesystem changes occur.
- **Windows Read-Only Clearing**: On Windows/exFAT, unlinking files with read-only flags raises `PermissionError`. `PurgeEngine._make_writable_and_remove_file` traps this, applies `os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)`, and retries unlinking.
- **Directory Tree Deletion**: `_make_writable_and_remove_dir` utilizes `shutil.rmtree(..., onerror=...)` to clear read-only flags on nested children during deletion.
- **Shield Auto-Restoration**: Immediately following any executed purge (`not self.dry_run`), `ensure_anti_indexing()` is called to recreate `.metadata_never_index` and `.fseventsd/no_log` if missing.
- **JSON Audit Logging**:
  - `export_audit_log(filepath)` serializes session metadata, dry-run state, nominal/allocated bytes reclaimed, and list of `DeletionRecord` entries.

### 3.4 CLI Confirmation Flow (`smart_drive/cli/cmd_clean.py`)
- Command defaults to `dry_run=True` (simulating Tier 1 junk).
- Running `smart-drive clean` prints a preview table and summary:
  ```
  Found X junk items. Reclaimable space: Y.YY MB
  [DRY RUN] No files were deleted. Run with --apply to execute purge.
  ```
- Running `smart-drive clean --apply` executes the deletion and outputs confirmation.
- Flags: `--tier [1|2|3]`, `--log <path>`, `--json`.

---

## 4. Hash Utilities, Traversal Routines, Formatting & Logging

### 4.1 SHA-256 Hashing Utilities
Defined in `smart_drive/core/duplicates.py` (lines 33–75):
- **`compute_full_sha256(filepath: str, chunk_size: int = 65536) -> str`**:
  - Uses `hashlib.sha256()`.
  - Streams file contents in 64KB blocks.
  - Returns `EMPTY_FILE_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"` for 0-byte files without disk read.
  - Returns `""` on `OSError` / `PermissionError`.
- **`compute_partial_hash(filepath: str, chunk_size: int = 8192) -> str`**:
  - Reads the first 8KB. If the file is >16KB, seeks to end and reads the last 8KB.
  - Used for rapid duplicate candidate pruning.

### 4.2 File Traversal Subsystems Comparison

| Subsystem | Traversal Routine | Method | Key Safeguards / Behaviors |
|-----------|-------------------|--------|----------------------------|
| `core.scanner` | `FastDirectoryScanner.scan_iter()` | Iterative Stack DFS + `os.scandir` | Symlinks skipped, slotted `ScanEntry`, case-insensitive exFAT exclusions |
| `core.duplicates` | `DuplicateDetector._walk_files()` | Iterative Stack DFS + `os.scandir` | Yields `(abs_path, size)`, skips exclusions, 0-byte file handling |
| `indexer.manager`| `IndexManager._scan_disk_files()` | Iterative Stack DFS + `os.scandir` | Skips AppleDouble `._*` & system junk, classifies category, extracts mtime |
| `core.auto_zoner` | `AutoZoner.generate_plan()` | Single-level `os.scandir` + `_get_item_size` recursive walk | Inspects root items, calculates nominal & slack bytes per item |

### 4.3 Formatting and Logging Subsystems
- **Human-Readable Formatting** (`smart_drive/core/auditor.py`):
  - `format_bytes(byte_count: int, precision: int = 2) -> str` (e.g. `512.00 KB`, `1.42 GB`)
  - `format_count(count: int) -> str` (e.g. `1,234,567`)
  - `format_percentage(pct: float, precision: int = 1) -> str` (e.g. `24.5%`)
- **Reporting Outputs**:
  - ASCII tables with aligned columns.
  - Markdown tables.
  - JSON objects with `json.dumps(..., indent=2, ensure_ascii=False)`.
- **Logging**:
  - Standard `logging.getLogger("smart_drive.<module>")`.
  - Windows console UTF-8 stream reconfiguring in `smart_drive/cli/main.py`:
    ```python
    if sys.platform == "win32":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ```

---

## 5. Architectural Integration Blueprint for v1.1.0

### 5.1 R1: Zero-Dependency Web Dashboard (`smart-drive ui`)

#### Requirements:
- Pure Python standard library `http.server` (`HTTPServer`, `BaseHTTPRequestHandler`), `urllib.parse`, `json`, `webbrowser`.
- Dark-mode responsive single-page application (embedded HTML/CSS/JS).
- Real-time endpoints:
  - Storage breakdown of 6 taxonomies + 512KB cluster slack visualization.
  - FTS5 instant search bar (<10ms) with extension, size, and category filters.
  - 3-tier safe cleanup control panel with Dry-Run preview and 1-touch purge execution.
- CLI flags: `--port 8765`, `--no-browser`, `--root <path>`.

#### Component Architecture:
```
smart_drive/
  ui/
    __init__.py
    server.py        # WebServer, RequestHandler, REST API routing
    dashboard.html   # Embedded modern HTML/CSS/JS frontend (Dark mode)
  cli/
    cmd_ui.py        # CLI subcommand handler for `smart-drive ui`
```

#### API Route Specifications:
1. `GET /`: Serves embedded single-page dashboard HTML.
2. `GET /api/stats` or `/api/audit`:
   - Calls `StorageAuditor(root).run_audit()`.
   - Returns JSON: total files, total nominal bytes, total allocated bytes, total slack bytes, slack percentage, breakdown by taxonomy, breakdown by category.
3. `GET /api/search?q=<query>&cat=<category>&ext=<ext>&limit=50`:
   - Resolves database path via `get_default_db_path(root)`.
   - Invokes `parse_search_query(q)` and `SearchEngine(db_manager).search(params)`.
   - Returns JSON matches with elapsed search time in milliseconds (<10ms).
4. `GET /api/junk?tier=[1|2|3]`:
   - Instantiates `JunkDetector(root, max_tier=tier)`.
   - Calls `find_junk()` and returns JSON item list + summary (reclaimable bytes, reclaimable slack).
5. `POST /api/purge`:
   - Body: `{"tier": 1, "dry_run": false}`.
   - Instantiates `JunkDetector` and `PurgeEngine(root, dry_run=dry_run)`.
   - Executes `purge_items()`, returns `PurgeSummary` JSON.

#### CLI Integration in `smart_drive/cli/main.py`:
```python
p_ui = subparsers.add_parser("ui", help="Launch zero-dependency web dashboard")
p_ui.add_argument("--port", type=int, default=8765, help="HTTP server port (default: 8765)")
p_ui.add_argument("--no-browser", action="store_true", help="Do not automatically launch web browser")
p_ui.add_argument("--root", help="Root directory of the SSD")
```

---

### 5.2 R2: Snapshot & Backup Engine (`smart-drive snapshot` / `backup`)

#### Requirements:
- Point-in-time recovery snapshots for key data partitions:
  - `02_Learning_Knowledge`
  - `03_Development_Projects`
  - `05_Dev_Toolbox`
  - (and optionally `01_AI_Models`)
- Data integrity verification with SHA-256 manifests.
- Subcommands:
  - `create [name]`: Traverses target partitions, computes streaming SHA-256 for all files, saves manifest JSON in `.smart_drive/snapshots/<timestamp>_<name>.json`.
  - `list`: Lists all saved snapshots with metadata (name, timestamp, files count, total bytes, manifest checksum).
  - `verify [name]`: Re-computes SHA-256 across target files, compares against manifest, reports:
    - Match count
    - Modified files (hash mismatch)
    - Missing files (deleted since snapshot)
    - Untracked files (added since snapshot)
  - `backup --target <path>`: Incremental backup copying modified/new files to target directory or external drive.

#### Component Architecture:
```
smart_drive/
  core/
    snapshot.py      # SnapshotEngine, ManifestManager, IncrementalBackupEngine
  cli/
    cmd_snapshot.py  # CLI handler for `smart-drive snapshot` & `smart-drive backup`
```

#### Manifest JSON Structure:
```json
{
  "version": "1.0",
  "name": "backup_20260926",
  "created_at": "2026-09-26T13:00:00Z",
  "drive_root": "D:/",
  "partitions": [
    "02_Learning_Knowledge",
    "03_Development_Projects",
    "05_Dev_Toolbox"
  ],
  "total_files": 1250,
  "total_bytes": 4839210048,
  "manifest_sha256": "4a7b3c...",
  "entries": [
    {
      "rel_path": "03_Development_Projects/smart_drive_os/pyproject.toml",
      "size": 1917,
      "mtime": 1758866400.0,
      "sha256": "3e9b1d84f..."
    }
  ]
}
```

#### Subsystem Reuse:
- `FastDirectoryScanner`: Traverses partitions without recursion or symlink risks.
- `compute_full_sha256` (`smart_drive/core/duplicates.py`): Streams 64KB blocks for SHA-256 calculation.
- `normalize_rel_path` (`smart_drive/core/exfat_compat.py`): Ensures cross-platform forward-slash paths.

---

### 5.3 R3: Deep Classifier & Auto-Tagger (`smart-drive classify`)

#### Requirements:
- Deep recognition of specialized file formats:
  - **AI Models & Weights**: `.gguf`, `.safetensors`, `.onnx`, `.pt`, `.pth`, `.bin`, `.ckpt`, `.tflite`, HuggingFace model configs (`config.json`, `tokenizer.json`, `generation_config.json`, `model.safetensors.index.json`).
  - **Datasets**: `.parquet`, `.arrow`, `.jsonl`, `.csv`, `.tsv`, `.h5`, `.hdf5`.
  - **Research & Learning**: `.pdf`, `.epub`, `.mobi`, `.djvu`, `.ipynb`, Markdown notes (`.md`, `.markdown`).
  - **Source Code Repositories**: Automatic detection of project markers (`.git`, `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `CMakeLists.txt`).
- Operating Modes:
  - `smart-drive classify --suggest`: Analyzes unorganized or root files/folders and displays a relocation proposal table with reasons and 512KB cluster slack reduction metrics.
  - `smart-drive classify --apply`: Safely moves classified files into their designated sub-taxonomy destinations without overwrite (`resolve_destination_conflict`).
  - `smart-drive classify --dry-run` (default): Simulates classification and movements safely.

#### Component Architecture:
```
smart_drive/
  core/
    classifier.py    # DeepFileClassifier, TaxonomyRuleEngine, ProjectDetector
  cli/
    cmd_classify.py  # CLI handler for `smart-drive classify`
```

#### Target Taxonomy & Sub-Taxonomy Mapping:

| Classification Category | Target Taxonomy Subfolder | Matching Indicators / Extensions |
|-------------------------|---------------------------|----------------------------------|
| AI Weights (GGUF) | `01_AI_Models/gguf/` | `.gguf` |
| AI Weights (Safetensors)| `01_AI_Models/safetensors/` | `.safetensors`, `*.safetensors.index.json` |
| AI Checkpoints & Models | `01_AI_Models/checkpoints/` | `.pt`, `.pth`, `.bin`, `.ckpt`, `.onnx` |
| AI HuggingFace Configs | `01_AI_Models/configs/` | `config.json`, `tokenizer.json`, `generation_config.json` |
| Datasets | `01_AI_Models/datasets/` or `03_Development_Projects/data_raw/` | `.parquet`, `.arrow`, `.jsonl`, `.h5`, `.hdf5`, `.csv` |
| Research & Notes | `02_Learning_Knowledge/Notes/` | `.md`, `.markdown`, `.ipynb`, `.txt` |
| Books & Literature | `02_Learning_Knowledge/References/` | `.pdf`, `.epub`, `.mobi`, `.djvu` |
| Git / Code Project | `03_Development_Projects/active/<dirname>/` | Folder containing `.git`, `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod` |
| Developer Scripts | `05_Dev_Toolbox/scripts/` | `.sh`, `.bash`, `.zsh`, `.ps1`, `.bat` |
| Compressed Archives | `06_Archives_Storage/` | `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.7z`, `.rar` |

---

## 6. Code Architecture Reference Matrix

| Feature / Subsystem | Existing Source File | Proposed New / Updated File |
|---------------------|----------------------|------------------------------|
| Web UI Dashboard | *(None)* | `smart_drive/ui/server.py`, `smart_drive/cli/cmd_ui.py` |
| Snapshot & Backup | `core/duplicates.py` (hash utils) | `smart_drive/core/snapshot.py`, `smart_drive/cli/cmd_snapshot.py` |
| Deep Classifier | `core/auto_zoner.py` (basic zoner) | `smart_drive/core/classifier.py`, `smart_drive/cli/cmd_classify.py` |
| CLI Dispatcher | `smart_drive/cli/main.py` | Add `ui`, `snapshot`, `backup`, `classify` parsers & handlers |
| Package Metadata | `smart_drive/__init__.py`, `pyproject.toml` | Update `__version__ = "1.1.0"` |
| Test Suite | `tests/` (132 passing tests) | `tests/test_ui.py`, `tests/test_snapshot.py`, `tests/test_classifier.py` |

---

## 7. Verification & Invalidation Criteria

1. **Test Execution**: `python -m unittest discover tests` must run with 0 failures across all existing 132 tests and new feature tests.
2. **Zero-Dependency Check**: No imports outside Python 3.8+ standard library (`http.server`, `hashlib`, `json`, `sqlite3`, `urllib`, `dataclasses`, `os`, `sys`, `shutil`, `stat`).
3. **Safety Guard Enforcement**: Verify that `SecurityViolationError` is raised whenever deletion of protected root files, anti-indexing shields, or business taxonomies is attempted.
4. **Cluster Math Accuracy**: Nominal size `0` must yield `0` allocated bytes. File size `1` must yield `524,288` allocated bytes.
5. **Dry-Run Invariant**: Every destructive command (`clean`, `organize`, `classify`) must default to dry-run simulation mode unless `--apply` is explicitly passed.
