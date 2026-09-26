# Handoff Report: SmartDrive-OS Architecture Survey & v1.1.0 Blueprint

**Agent**: Explorer 1 (Codebase Architect)  
**Working Directory**: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_1`  
**Target Codebase**: `d:\teamwork_projects\smart_drive_os`  
**Handoff Type**: Hard (Task Complete)  
**Date**: 2026-09-26  

---

## 1. Observation

### 1.1 Packaging, Versioning, and Dependencies
- **File**: `d:\teamwork_projects\smart_drive_os\pyproject.toml`
  - Lines 6-7:
    ```toml
    [project]
    name = "smart-drive-os"
    version = "1.0.0"
    ```
  - Line 45: `dependencies = []` (Zero external dependencies enforced).
  - Lines 52-56:
    ```toml
    [project.scripts]
    smart-drive = "smart_drive.cli.main:main"
    smart_drive = "smart_drive.cli.main:main"
    smart-drive-manager = "smart_drive.cli.main:main"
    ```
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\__init__.py`
  - Line 8: `__version__ = "1.0.0"`

### 1.2 CLI Subsystem & Argument Parsing
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\cli\main.py`
  - Lines 154-259: `build_parser() -> argparse.ArgumentParser` defines top-level parser `prog="smart-drive"` with subparsers registered using `subparsers.add_parser(...)`.
  - Lines 271-286: `main(argv)` uses a static dispatch dictionary:
    ```python
    dispatch = {
        "init": cmd_init,
        "status": cmd_status,
        "audit": cmd_audit,
        "clean": cmd_clean,
        "search": cmd_search,
        "organize": cmd_organize,
        "sentinel": cmd_sentinel,
        "agent-check": cmd_sentinel,
        "agent_check": cmd_sentinel,
        "mcp": cmd_mcp,
        "mcp-config": cmd_mcp_config,
        "dup": cmd_dup,
        "index": cmd_index,
        "update": cmd_update,
    }
    ```
  - Subcommands `ui`, `snapshot`, `backup`, and `classify` are not yet defined in `main.py`.

### 1.3 Database & Index Schema
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\indexer\db.py`
  - Lines 20-69: `SCHEMA_SQL` defines:
    - Metadata table `files` (`id`, `path` UNIQUE, `filename`, `extension`, `size`, `mtime`, `category`, `indexed_at`).
    - B-Tree indexes: `idx_files_ext`, `idx_files_cat`, `idx_files_mtime`, `idx_files_size`, `idx_files_path`, `idx_files_cat_size`.
    - FTS5 virtual table: `files_fts USING fts5(filename, path, content='files', content_rowid='id', tokenize='unicode61 remove_diacritics 2')`.
    - Automated sync triggers: `trg_files_ai` (INSERT), `trg_files_ad` (DELETE), `trg_files_au` (UPDATE).
    - Meta table: `index_meta(key PRIMARY KEY, value)`.
  - Lines 92-106: Performance PRAGMAs:
    - WAL journal mode (`PRAGMA journal_mode = WAL;`)
    - Normal sync (`PRAGMA synchronous = NORMAL;`)
    - 64MB cache (`PRAGMA cache_size = -64000;`)
    - Memory temp store (`PRAGMA temp_store = MEMORY;`)
    - 256MB MMAP (`PRAGMA mmap_size = 268435456;`)
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\search\engine.py`
  - Lines 188-200: FTS5 MATCH with BM25 ranking (`bm25(files_fts, 5.0, 1.0) AS rank`).
  - Lines 227-245: Fast B-Tree metadata fallback when no keyword is provided.

### 1.4 Taxonomy Definitions & Cluster Slack Geometry
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\core\config.py`
  - Lines 23-28:
    ```python
    CLUSTER_SIZE_BYTES: int = 524_288  # 512 KB
    CLUSTER_SIZE_KB: int = 512
    CLUSTER_SIZE: int = CLUSTER_SIZE_BYTES
    SECTOR_SIZE: int = 512
    SECTORS_PER_CLUSTER: int = 1024
    ```
  - Lines 31-54: `calculate_allocated_bytes(nominal_size, cluster_size=524_288)`: 0 bytes allocates 0 clusters (0 bytes); >0 bytes allocates `((nominal_size + cluster_size - 1) // cluster_size) * cluster_size`.
  - Lines 56-68: `calculate_slack_bytes(nominal_size, cluster_size=524_288) = calculate_allocated_bytes(...) - nominal_size`.
  - Lines 112-120:
    ```python
    TAXONOMY_ROOT_DIRS: Tuple[str, ...] = (
        "01_AI_Models",
        "02_Learning_Knowledge",
        "03_Personal_Documents",
        "04_Creative_Assets",
        "05_Dev_Toolbox",
        "06_Archives_Storage",
    )
    ```
  - Lines 124-133: `PROTECTED_CORE_TAXONOMIES` additionally includes `03_Development_Projects` and `04_System_Workspaces`.
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\core\auditor.py`
  - Lines 498-504: Tracks canonical 6 taxonomies + `Workspaces`, `System`, and `Other`.
  - Lines 506-512: Tracks 7 functional categories + `Other`: `AI Models`, `Code`, `Books/Learning`, `Docs`, `Media`, `Archives`, `System Junk`.
  - Lines 223-360: `DirectoryNode` rolls up recursive file counts, nominal bytes, physical allocated bytes, and wasted slack bytes to generate top slack hotspot lists.

### 1.5 Duplicate Hashing & Safe Purge Infrastructure
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\core\duplicates.py`
  - Lines 60-75: `compute_full_sha256(filepath, chunk_size=65536)` streaming SHA-256 in 64KB blocks.
- **File**: `d:\teamwork_projects\smart_drive_os\smart_drive\core\purge_engine.py`
  - Lines 114-237: `SecurityGuard` prevents deletion of drive root, `.git` trees, anti-indexing markers (`.metadata_never_index`, `.fseventsd/no_log`), root scripts (`Setup_*`, `Quick_*`), and root manifests (`GEMINI.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`).
  - Lines 243-575: `PurgeEngine` defaults to `dry_run=True`, handles Win32 read-only file stripping (`stat.S_IWRITE`), and exports JSON audit logs.

### 1.6 Current Test Pass Status
- Command executed: `python -m unittest discover tests`
- Result verbatim:
  ```
  Ran 132 tests in 3.745s
  OK
  ```
- All 132 tests pass cleanly with 0 failures, 0 errors, 0 skips, without any third-party dependencies.

---

## 2. Logic Chain

1. **Zero-Dependency Mandate**:
   - `pyproject.toml` declares `dependencies = []`.
   - The test suite runs via standard library `unittest`.
   - **Inference**: All upcoming features (R1 Web UI, R2 Snapshot/Backup, R3 Classifier) must strictly use standard library modules (`http.server`, `hashlib`, `json`, `sqlite3`, `urllib`, `shutil`, `re`, `argparse`). No `pip install` packages (Flask, FastAPI, requests, pydantic) are permitted.

2. **Web UI Architecture (R1)**:
   - Python standard library provides `http.server.HTTPServer` and `http.server.BaseHTTPRequestHandler`.
   - `StorageAuditor.run_audit()` produces structured data on 6 taxonomies, cluster slack, and category breakdowns.
   - `SearchEngine.search()` executes instant queries (<10ms) backed by SQLite FTS5.
   - `JunkDetector` and `PurgeEngine` provide 3-tier junk scanning and dry-run/apply deletion.
   - **Inference**: A standalone `smart_drive/ui/` module can run an HTTP server on `--port 8765`, serve a self-contained modern HTML/CSS/JS Single-Page Application, and provide REST endpoints (`/api/status`, `/api/audit`, `/api/search`, `/api/junk`, `/api/junk/clean`) without any external frontend build tools or web frameworks.

3. **Snapshot & Backup Architecture (R2)**:
   - `smart_drive/core/duplicates.py` already includes `compute_full_sha256()` with 64KB chunking.
   - R2 requires point-in-time recovery tracking for key partitions (`02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox`).
   - **Inference**: A new `SnapshotManager` can traverse these partitions, hash each file using SHA-256, record manifest JSON files in `.smart_drive/snapshots/<name>.json`, compare current files against snapshot manifests to detect modifications/corruptions (`verify`), and copy changed files to a target directory (`backup --target`).

4. **Classifier & Auto-Tagger Architecture (R3)**:
   - `smart_drive/core/config.py` and `smart_drive/core/auto_zoner.py` already have partial classification logic for AI models, code, learning, and archives.
   - R3 specifies deep recognition for AI weights (GGUF, Safetensors, ONNX, PyTorch `.pt`/`.pth`), datasets (Parquet, Arrow, JSONL, CSV, HDF5), research/documents (PDF, ePub, Markdown), and Git/language repos.
   - **Inference**: Expanding the classification rule set and wrapping it in a dedicated `ClassifierEngine` with `--suggest`, `--dry-run`, and `--apply` will fulfill R3 while seamlessly integrating with `AutoZoner`.

5. **Release & Packaging (R4)**:
   - Version in `pyproject.toml` and `smart_drive/__init__.py` is currently `1.0.0`.
   - Tests under `tests/` currently total 132.
   - Git remote is set to `https://github.com/DuongNAD/smart-drive-os.git`.
   - **Inference**: Bumping version to `1.1.0`, adding unit tests for UI, Snapshot, and Classifier (aiming for 100% pass rate), updating `README.md` and `README_VN.md`, and pushing git commit/tag `v1.1.0` will fulfill all criteria.

---

## 3. Caveats

1. **ExFAT Case-Insensitivity vs Linux/macOS**:
   - On Windows exFAT, path matching is case-insensitive. On Linux or POSIX systems, paths are case-sensitive. All database lookups and file matching normalize paths via `normalize_rel_path()` using `/` and casefolded sets.
2. **Symlinks on exFAT**:
   - exFAT does not natively support POSIX symbolic links. The codebase strictly enforces `follow_symlinks=False` and skips or rejects symlinks (`SymlinkNotPermittedError`). Any snapshot or backup mechanism must skip symlinks or handle them gracefully.
3. **HTTP Server Concurrency**:
   - `http.server.HTTPServer` in Python standard library is single-threaded by default. For responsive search and UI operations, `socketserver.ThreadingMixIn` can be used to handle concurrent HTTP requests without external dependencies.
4. **Target Partitions Naming**:
   - R2 specifies `02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox`. Note that on some drives `03_Personal_Documents` or `03_Development_Projects` might be present; snapshot creation should check for both or allow customizable partition selection.

---

## 4. Conclusion

The SmartDrive-OS codebase is solid, robust, and well-organized. Its zero-dependency architecture is strictly respected across all existing modules.

### Recommended Implementation Roadmap for v1.1.0:
1. **Module 1: Web Dashboard (`smart_drive.ui`)**:
   - Implement `smart_drive/ui/server.py` and `smart_drive/ui/dashboard.py` (or embedded HTML templates).
   - Implement `smart_drive/cli/cmd_ui.py`.
   - Add unit tests in `tests/test_ui.py`.
2. **Module 2: Snapshot & Backup Engine (`smart_drive.snapshot`)**:
   - Implement `smart_drive/core/snapshot.py` (manifest generation, SHA-256 verification, incremental backup).
   - Implement `smart_drive/cli/cmd_snapshot.py`.
   - Add unit tests in `tests/test_snapshot.py`.
3. **Module 3: Intelligent Classifier Engine (`smart_drive.classifier`)**:
   - Implement `smart_drive/core/classifier.py` (deep file recognition for AI models, datasets, research, code repos, `--suggest`).
   - Implement `smart_drive/cli/cmd_classify.py`.
   - Add unit tests in `tests/test_classifier.py`.
4. **Module 4: CLI Integration & Version Bump**:
   - Update `smart_drive/cli/main.py` with `ui`, `snapshot`, `backup`, `classify` subcommands.
   - Update `smart_drive/__init__.py` and `pyproject.toml` version to `1.1.0`.
5. **Module 5: Documentation & Git Release**:
   - Update `README.md` and `README_VN.md`.
   - Execute full test suite `python -m unittest discover tests`.
   - Commit and push tag `v1.1.0` to `origin main`.

---

## 5. Verification Method

To independently verify the observations and codebase state:
1. **Run Full Test Suite**:
   ```powershell
   python -m unittest discover tests
   ```
   *Expected result*: Ran 132 tests, OK.
2. **Inspect CLI Help and Subcommands**:
   ```powershell
   python -m smart_drive --help
   ```
   *Expected result*: Displays 12 existing subcommands (`init`, `status`, `audit`, `clean`, `search`, `organize`, `sentinel`, `mcp`, `mcp-config`, `dup`, `index`, `update`).
3. **Inspect pyproject.toml Version & Zero-Dependency**:
   ```powershell
   Get-Content d:\teamwork_projects\smart_drive_os\pyproject.toml | Select-String "version =", "dependencies ="
   ```
   *Expected result*: `version = "1.0.0"`, `dependencies = []`.
4. **Invalidation Conditions**:
   - If any test in `tests/` fails under standard Python 3.8 - 3.13, the baseline is invalidated.
   - If non-standard external dependencies are introduced, the zero-dependency invariant is invalidated.

---
*End of handoff report.*
