# SmartDrive-OS Architecture Analysis & Codebase Map (v1.1.0 Survey)

**Target Codebase**: `d:\teamwork_projects\smart_drive_os`  
**Explorer**: Explorer 1 (Codebase Architect)  
**Date**: 2026-09-26  
**Package**: `smart_drive` (current version: 1.0.0, target: 1.1.0)  
**Dependency Policy**: 100% Python Standard Library (Zero external `pip` dependencies)

---

## 1. Executive Architectural Summary

SmartDrive-OS is a zero-dependency, high-performance governance, indexing, and management engine specifically engineered for high-capacity external SSDs (notably the Kingston XS2000 2TB formatted with exFAT and a 512KB allocation block size).

The system addresses the fundamental physical trade-off of large-cluster exFAT filesystems:
- **exFAT 512KB Cluster Slack**: Storing a 1-byte file consumes 524,288 bytes on disk, resulting in a 99.9998% space overhead ("slack waste").
- **Zero Syscall Search**: Iterating over hundreds of thousands of files across external USB/Thunderbolt drives incurs massive I/O overhead. SmartDrive-OS implements an in-SSD SQLite FTS5 search index with BM25 ranking, delivering sub-10ms queries.
- **Inviolable Safeguards**: Core scripts, manifest files (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`), anti-indexing markers (`.metadata_never_index`, `.fseventsd/no_log`), and the 6 foundational taxonomies are protected against accidental purging or relocation.

The codebase is exceptionally modular, clean, type-annotated, and possesses 100% test coverage using standard library `unittest` (132/132 tests passing in under 4 seconds).

---

## 2. Directory Tree & Package Structure

```
d:\teamwork_projects\smart_drive_os\
├── pyproject.toml               # Package build configuration (PEP 621, setuptools)
├── setup.py                     # Legacy setuptools fallback
├── README.md                    # English documentation
├── README_VN.md                 # Vietnamese documentation
├── LICENSE                      # MIT License
├── CONTRIBUTING.md              # Community guidelines
├── TEST_READY.md                # Verification notes
├── Quick_Audit.bat/.command     # Quick-launch storage audit scripts
├── Quick_Clean.bat/.command     # Quick-launch junk cleaner scripts
├── Quick_Search.bat/.command    # Quick-launch search scripts
├── Setup_SSD.bat/.command       # Drive setup scripts
├── configs/                     # IDE MCP config templates (Claude, Cursor, Windsurf)
├── launchers/                   # Cross-platform execution wrappers
├── smart_drive/                 # Main Python package
│   ├── __init__.py              # Public API exports & version identifier
│   ├── __main__.py              # Package execution wrapper (`python -m smart_drive`)
│   ├── cli/                     # Command-line interface subsystem
│   │   ├── __init__.py
│   │   ├── main.py              # Central argument parser & subcommand dispatcher
│   │   ├── cmd_audit.py         # `smart-drive audit`
│   │   ├── cmd_clean.py         # `smart-drive clean`
│   │   ├── cmd_init.py          # `smart-drive init`
│   │   ├── cmd_mcp.py           # `smart-drive mcp`
│   │   ├── cmd_mcp_config.py    # `smart-drive mcp-config`
│   │   ├── cmd_organize.py      # `smart-drive organize`
│   │   ├── cmd_search.py        # `smart-drive search`
│   │   ├── cmd_sentinel.py      # `smart-drive sentinel` & `agent-check`
│   │   └── cmd_status.py        # `smart-drive status`
│   ├── core/                    # Core domain logic & filesystem engines
│   │   ├── __init__.py
│   │   ├── config.py            # Global constants, cluster math, taxonomies, categories, junk rules
│   │   ├── exfat_compat.py      # exFAT geometry, path normalization, Win32 forbidden character audits
│   │   ├── scanner.py           # Slotted DFS directory scanner (`FastDirectoryScanner`)
│   │   ├── auditor.py           # Taxonomy breakdown & 512KB slack calculator (`StorageAuditor`)
│   │   ├── junk_detector.py     # 3-Tier junk classifier (`JunkDetector`)
│   │   ├── purge_engine.py      # Safe deletion engine with SecurityGuard (`PurgeEngine`)
│   │   ├── duplicates.py        # 3-Phase SHA-256 cascade duplicate detection (`DuplicateDetector`)
│   │   ├── auto_zoner.py        # Autonomous drive auto-zoning & rebalancing (`AutoZoner`)
│   │   ├── sentinel.py          # SSD health sentinel & shield self-healer (`SentinelEngine`)
│   │   └── initializer.py       # 1-Touch SSD workspace initializer (`DriveInitializer`)
│   ├── indexer/                 # SQLite FTS5 database & indexing engine
│   │   ├── __init__.py
│   │   ├── db.py                # Database connection lifecycle, schema, and WAL PRAGMAs
│   │   └── manager.py           # High-throughput batch & incremental indexer (`IndexManager`)
│   ├── search/                  # Query parsing, execution, and formatting
│   │   ├── __init__.py
│   │   ├── engine.py            # FTS5 search query executor with BM25 ranking (`SearchEngine`)
│   │   ├── parser.py            # Compound query parser & FTS5 query sanitizer (`parse_search_query`)
│   │   └── formatter.py         # ASCII table, JSON, and CSV search result formatters
│   └── mcp/                     # Model Context Protocol (MCP) stdio server
│       ├── __init__.py
│       ├── proxy.py             # Subprocess proxy with latency benchmarks (`SmartDriveProxy`)
│       ├── registrar.py         # Multi-IDE configuration installer (`register_ide_configs`)
│       └── server.py            # JSON-RPC 2.0 stdio MCP server (`SmartDriveMCPServer`)
└── tests/                       # Unit and integration test suite (100% unittest)
    ├── __init__.py
    ├── helpers.py               # Mock drive generator, base test case, reference oracles
    ├── test_auditor.py          # Storage auditor and slack calculation tests
    ├── test_auto_zoner.py       # Auto zoner classification and relocation tests
    ├── test_cleaner.py          # 3-tier junk detection and safe purge tests
    ├── test_cli_e2e.py          # E2E subprocess CLI tests
    ├── test_exfat_compat.py     # Windows forbidden character and path normalization tests
    ├── test_geometry.py         # 512KB cluster allocation math tests
    ├── test_indexer.py          # SQLite FTS5 indexing & incremental update tests
    ├── test_initializer.py      # Workspace initialization and profile tests
    ├── test_mcp_proxy.py        # MCP proxy and mount discovery tests
    ├── test_mcp_server.py       # MCP protocol JSON-RPC tests
    ├── test_scanner.py          # Fast directory scanner traversal tests
    ├── test_search.py           # FTS5 query parser, sanitizer, and engine tests
    └── test_sentinel.py         # Sentinel health check and shield healing tests
```

---

## 3. CLI Subsystem & Argument Parsing Architecture

### 3.1 Entry Point Configuration
In `pyproject.toml`:
```toml
[project.scripts]
smart-drive = "smart_drive.cli.main:main"
smart_drive = "smart_drive.cli.main:main"
smart-drive-manager = "smart_drive.cli.main:main"
```
The CLI can be invoked in 3 ways:
1. Console script: `smart-drive <subcommand> [options]`
2. Python module: `python -m smart_drive <subcommand> [options]`
3. Direct execution: `python smart_drive/cli/main.py <subcommand> [options]`

### 3.2 Parser Structure (`smart_drive/cli/main.py`)
- `build_parser() -> argparse.ArgumentParser`:
  - Instantiates top-level parser: `argparse.ArgumentParser(prog="smart-drive", description="...")`.
  - Configures subparsers: `subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")`.
  - Current 12 subcommands:
    1. `init` -> `cmd_init`
    2. `status` -> `cmd_status`
    3. `audit` -> `cmd_audit`
    4. `clean` -> `cmd_clean`
    5. `search` -> `cmd_search`
    6. `organize` -> `cmd_organize`
    7. `sentinel` / `agent-check` -> `cmd_sentinel`
    8. `mcp` -> `cmd_mcp`
    9. `mcp-config` -> `cmd_mcp_config`
    10. `dup` -> `cmd_dup` (in-line in `main.py`)
    11. `index` -> `cmd_index` (in-line in `main.py`)
    12. `update` -> `cmd_update` (in-line in `main.py`)
- `main(argv=None) -> int`:
  - Dispatches `args.subcommand` via a handler dictionary `dispatch: Dict[str, Callable[[argparse.Namespace], int]]`.
  - Returns exit code (0 for success, non-zero for error).

### 3.3 Extension Points for v1.1.0
To support R1, R2, and R3, new subcommands should be registered in `build_parser()` and mapped in `dispatch`:
1. **R1: Web UI**:
   - Subcommand: `ui`
   - Flags: `--port 8765`, `--no-browser`, `--root <path>`
   - Handler: `cmd_ui(args)` in `smart_drive.cli.cmd_ui`
2. **R2: Snapshot & Backup**:
   - Subcommand: `snapshot` (actions: `create [name]`, `list`, `verify [name]`, `--root <path>`, `--json`)
   - Subcommand: `backup` (flags: `--target <path>`, `--root <path>`, `--dry-run`, `--json`)
   - Handler: `cmd_snapshot(args)` and `cmd_backup(args)` in `smart_drive.cli.cmd_snapshot`
3. **R3: Classifier**:
   - Subcommand: `classify`
   - Flags: `path` (optional target dir), `--root <path>`, `--suggest`, `--dry-run`, `--apply`, `--json`
   - Handler: `cmd_classify(args)` in `smart_drive.cli.cmd_classify`

---

## 4. Database Schema & FTS5 Search Architecture

### 4.1 SQLite Schema (`smart_drive/indexer/db.py`)
The database file is located by default at: `<DRIVE_ROOT>/.smart_drive/index.db`.
The database engine applies exFAT-optimized PRAGMAs:
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -64000;      -- 64 MB RAM cache
PRAGMA temp_store = MEMORY;
PRAGMA foreign_keys = OFF;
PRAGMA mmap_size = 268435456;    -- 256 MB memory-mapped I/O
```

Tables and Indexes:
1. **`files` table**:
   - `id`: `INTEGER PRIMARY KEY AUTOINCREMENT`
   - `path`: `TEXT UNIQUE NOT NULL` (normalized relative path with `/`)
   - `filename`: `TEXT NOT NULL`
   - `extension`: `TEXT` (lowercase with leading dot, e.g. `.pdf`)
   - `size`: `INTEGER NOT NULL` (nominal size in bytes)
   - `mtime`: `REAL NOT NULL` (epoch timestamp)
   - `category`: `TEXT NOT NULL` (Code, AI Models, Books/Learning, Docs, Media, Archives, Other)
   - `indexed_at`: `REAL NOT NULL`
2. **B-Tree Indexes**:
   - `idx_files_ext` on `files(extension)`
   - `idx_files_cat` on `files(category)`
   - `idx_files_mtime` on `files(mtime)`
   - `idx_files_size` on `files(size)`
   - `idx_files_path` on `files(path)`
   - `idx_files_cat_size` on `files(category, size DESC)`
3. **`files_fts` virtual table**:
   - External content FTS5 table: `USING fts5(filename, path, content='files', content_rowid='id', tokenize='unicode61 remove_diacritics 2')`
4. **Triggers**:
   - `trg_files_ai AFTER INSERT ON files`: synchronizes into `files_fts`
   - `trg_files_ad AFTER DELETE ON files`: deletes from `files_fts`
   - `trg_files_au AFTER UPDATE ON files`: updates `files_fts`
5. **`index_meta` table**:
   - `key TEXT PRIMARY KEY`, `value TEXT` (records `last_full_index`, `indexed_root`, `last_incremental_index`).

### 4.2 Indexing Performance (`smart_drive/indexer/manager.py`)
- Batch indexing achieves >15,000 files/sec using `executemany` with 500-item transaction commits.
- Filesystem traversal is performed by `_scan_disk_files()` which filters out AppleDouble (`._*`) and OS junk files (`.DS_Store`, `Thumbs.db`).
- Incremental updates compare `mtime` (with 0.02s tolerance) and `size`, updating modified rows and removing deleted rows with O(1) memory footprint.

### 4.3 Search Engine & Query Parsing (`smart_drive/search/`)
- `parse_search_query(query_str: str) -> SearchParams`:
  - Parses free text keywords, `ext:pdf,md`, `size:>100MB`, `cat:AI`, `dir:02_Learning_Knowledge`.
- `sanitize_fts_query(query: str) -> str`:
  - Robust sanitizer preventing syntax errors from unclosed quotes, bare operators (`AND`, `OR`, `NOT`), or special punctuation (`llama-3-8b`, `c++`, `react@18`).
- `SearchEngine.search(params: SearchParams) -> SearchResult`:
  - Dual execution paths:
    - **Path A (FTS5 MATCH)**: When keyword is present, uses `files_fts MATCH :fts_expr` with `bm25(files_fts, 5.0, 1.0) AS rank`.
    - **Path B (Direct B-Tree Filter)**: When purely filtering by extension, size, or directory, queries `files` table directly using B-Tree indexes.
  - Typical query latency: **0.5ms to 5ms** (<10ms target met consistently).

---

## 5. Taxonomy Definitions & Cluster Slack Geometry

### 5.1 The 6 Standard Taxonomies (`smart_drive/core/config.py`)
Defined as:
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
In addition, `PROTECTED_CORE_TAXONOMIES` harmonizes existing variations:
- `01_AI_Models`: Weights, GGUF quants, HuggingFace caches, checkpoints.
- `02_Learning_Knowledge`: Technical documentation, ebooks, study notes, research papers.
- `03_Personal_Documents` / `03_Development_Projects`: Active and archived repositories, projects.
- `04_Creative_Assets` / `04_System_Workspaces`: Design assets, system configs, presets.
- `05_Dev_Toolbox`: Automation scripts, CLI tools, developer utilities.
- `06_Archives_Storage`: Compressed archives, cold backups, historical snapshots.

### 5.2 512KB Cluster Slack Geometry Math (`smart_drive/core/config.py`)
Hardware parameters:
- `CLUSTER_SIZE_BYTES = 524_288` (512 KB)
- `SECTOR_SIZE = 512`
- `SECTORS_PER_CLUSTER = 1024`

Mathematical rules:
1. **Physical Allocated Bytes**:
   ```python
   def calculate_allocated_bytes(nominal_size: int, cluster_size: int = 524_288) -> int:
       if nominal_size < 0:
           raise ValueError(f"File size cannot be negative: {nominal_size}")
       if nominal_size == 0:
           return 0  # exFAT allocates 0 clusters for 0-byte directory entries
       return ((nominal_size + cluster_size - 1) // cluster_size) * cluster_size
   ```
2. **Cluster Slack Bytes**:
   ```python
   def calculate_slack_bytes(nominal_size: int, cluster_size: int = 524_288) -> int:
       return calculate_allocated_bytes(nominal_size, cluster_size) - nominal_size
   ```
3. **Slack Ratio & Waste Percentage**:
   ```python
   def calculate_slack_percentage(nominal_size: int, cluster_size: int = 524_288) -> float:
       allocated = calculate_allocated_bytes(nominal_size, cluster_size)
       if allocated == 0:
           return 0.0
       return ((allocated - nominal_size) / allocated) * 100.0
   ```

### 5.3 Storage Auditor (`smart_drive/core/auditor.py`)
- `StorageAuditor.run_audit()` traverses the drive via `FastDirectoryScanner.scan_iter()`.
- Calculates:
  - Per-taxonomy statistics (`TaxonomyStats`): files, directories, nominal bytes, allocated bytes, slack bytes, slack percentage.
  - Per-category statistics (`CategoryStats`): 7 categories + Other.
  - Directory tree rollup (`DirectoryNode`): identifies "Top Slack Hotspots" where thousands of small files create severe space loss.
  - Executive summary: total files, total directories, total logical size, total physical size, total slack wasted, zero-byte file count.

---

## 6. Safe Purge Engine & 3-Tier Junk Detector

### 6.1 3-Tier Rules (`smart_drive/core/config.py`)
1. **Tier 1 (Safe to Purge)**:
   - macOS: `._*` (AppleDouble), `.DS_Store`, `.Spotlight-V100`, `.Trashes`, `.TemporaryItems`
   - Windows: `Thumbs.db`, `ehthumbs.db`, `desktop.ini`, `$RECYCLE.BIN`
   - Python: `__pycache__`, `*.pyc`, `*.pyo`
2. **Tier 2 (Dev Artifacts & Test Caches)**:
   - `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.tox`, `.nox`, `.coverage`, `htmlcov`, `build`, `dist`, `*.egg-info`
3. **Tier 3 (Sensitive / Opt-in)**:
   - Crash dumps (`*.dmp`, `core.*`, `hs_err_pid*.log`), temporary files (`*.tmp`, `*.temp`, `*.swp`, `*.swo`, `*~`)

### 6.2 Security Guard (`smart_drive/core/purge_engine.py`)
- Never permits deletion of the drive root.
- Never permits deletion of Git repository internals (`.git`).
- Never permits deletion of anti-indexing markers (`.metadata_never_index`, `.fseventsd/no_log`).
- Never permits deletion of root manifest files (`GEMINI.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `setup_*`, `quick_*`, etc.).
- Safe-by-default: dry-run is enabled unless explicitly overridden with `--apply`.

---

## 7. Test Suite Architecture (`tests/`)

### 7.1 Test Harness & Framework
- Framework: Pure Python Standard Library `unittest`.
- Discovery Command: `python -m unittest discover tests`.
- Current Baseline:
  - **132 test cases passed**
  - **0 failures, 0 errors, 0 skipped**
  - **Execution time: ~3.7 seconds**

### 7.2 Test File Breakdown
| Test File | Test Count | Focus Area |
|---|---|---|
| `test_geometry.py` | 14 | Exact 512KB cluster math, 0-byte, 1-byte, boundary cases |
| `test_exfat_compat.py` | 13 | Win32 forbidden chars, path normalization, symlink rejection |
| `test_scanner.py` | 10 | Iterative DFS traversal, depth limits, exclusion filtering |
| `test_auditor.py` | 11 | Taxonomy breakdown, category stats, ASCII/MD/JSON formats |
| `test_cleaner.py` | 12 | 3-tier junk detection, dry-run simulation, SecurityGuard blocks |
| `test_search.py` | 13 | FTS5 query parser, tokenizer sanitization, BM25 execution |
| `test_indexer.py` | 10 | SQLite schema, batch indexing, incremental synchronization |
| `test_auto_zoner.py` | 9 | Autonomous item classification, relocation planning |
| `test_sentinel.py` | 8 | Shield detection & auto-healing, DB quick_check, Git sentinel |
| `test_initializer.py` | 7 | Workspace profiles, template writing, DB initialization |
| `test_mcp_proxy.py` | 9 | Tool dispatching, mount discovery, latency benchmarks |
| `test_mcp_server.py` | 6 | JSON-RPC 2.0 stdio protocol compliance |
| `test_cli_e2e.py` | 10 | Subprocess execution of all CLI commands |
| **Total** | **132** | **100% Pass** |

### 7.3 Mocking & Fixture Utilities (`tests/helpers.py`)
- `SmartDriveTestCase`: Base class providing isolated temporary directories in `setUp()` and automatic cleanup in `tearDown()`.
- `create_mock_ssd_tree(root: Path)`: Populates a realistic SSD directory structure with all 6 taxonomies, models, docs, scripts, junk, duplicates, and cluster boundary files.
- `oracle_cluster_allocation()`, `oracle_full_sha256()`, `oracle_is_protected()`: Authoritative reference oracles for mathematical verification.

---

## 8. Gap Analysis & Blueprint for v1.1.0 Features

### 8.1 R1: Zero-Dependency Web Dashboard & Visual UI (`smart-drive ui`)
- **Existing Assets**:
  - `StorageAuditor.run_audit()` provides complete JSON data for taxonomy distribution, cluster slack waste, categories, and hotspots.
  - `SearchEngine.search()` provides sub-10ms compound search with JSON output.
  - `JunkDetector.find_junk()` and `PurgeEngine.execute_purge()` provide 3-tier junk detection and dry-run/apply purge capabilities.
- **Required New Modules**:
  - `smart_drive/ui/server.py`: Custom HTTP request handler built on `http.server.HTTPServer` and `SimpleHTTPRequestHandler` or `BaseHTTPRequestHandler`.
  - Embedded Single-Page Application (HTML/CSS/JS) embedded directly in Python code (zero external static file dependencies, dark mode, responsive, pure SVG/Canvas charts).
  - REST/JSON API endpoints:
    - `GET /api/status`: Drive status, mount point, geometry, shields health.
    - `GET /api/audit`: Full storage breakdown (6 taxonomies, 512KB slack, category breakdown).
    - `GET /api/search?q=...&ext=...&size=...&cat=...`: Instant FTS5 search.
    - `GET /api/junk?tier=1`: 3-tier junk preview (dry-run).
    - `POST /api/junk/clean`: 1-touch clean execution with tier parameter and JSON audit log.
  - `smart_drive/cli/cmd_ui.py`: Subcommand handler for `smart-drive ui --port 8765 --no-browser`.

### 8.2 R2: Snapshot & Backup Engine (`smart-drive snapshot` / `backup`)
- **Existing Assets**:
  - `smart_drive/core/duplicates.py` already implements streaming SHA-256 chunked hashing (`compute_full_sha256`).
  - `FastDirectoryScanner` provides rapid traversal without symlinks.
- **Required New Modules**:
  - `smart_drive/snapshot/manager.py` (or `smart_drive/core/snapshot.py`):
    - Targeted partitions: `02_Learning_Knowledge`, `03_Development_Projects` (or `03_Personal_Documents`), `05_Dev_Toolbox`.
    - Point-in-time manifest file: `.smart_drive/snapshots/<timestamp>_<name>.json`.
    - Manifest contents: creation timestamp, target directories, total files, total size, file records with relative path, size, mtime, and SHA-256 checksum.
    - Methods:
      - `create_snapshot(name, partitions=None) -> SnapshotManifest`
      - `list_snapshots() -> List[SnapshotMetadata]`
      - `verify_snapshot(name) -> VerificationReport` (detects added, modified, deleted, corrupted files).
      - `backup(target_path, incremental=True) -> BackupSummary` (copies only new/modified files based on mtime/hash).
  - `smart_drive/cli/cmd_snapshot.py`: Subcommand handlers for `snapshot` and `backup`.

### 8.3 R3: Intelligent Classifier & Auto-Tagger (`smart-drive classify`)
- **Existing Assets**:
  - `AutoZoner` in `smart_drive/core/auto_zoner.py` contains basic classification rules.
  - `config.py` contains category maps.
- **Required Enhancements**:
  - Specialized format engines:
    - **AI Models & Weights**: `.gguf`, `.safetensors`, `.onnx`, `.pt`, `.pth`, `.bin`, `.ckpt`, HuggingFace `config.json`, `tokenizer.json`. Target: `01_AI_Models`.
    - **Datasets**: `.parquet`, `.arrow`, `.jsonl`, `.csv`, `.tsv`, `.h5`, `.hdf5`. Target: `01_AI_Models/datasets` or `03_Development_Projects/datasets`.
    - **Research & Documents**: `.pdf`, `.epub`, `.mobi`, `.md` notes. Target: `02_Learning_Knowledge`.
    - **Source Code Repositories**: Git repositories (`.git`), Node (`package.json`), Python (`pyproject.toml`, `setup.py`), Rust (`Cargo.toml`), Go (`go.mod`). Target: `03_Development_Projects`.
  - Mode support:
    - `--suggest`: Generates a classification plan and outputs recommendations without touching disk.
    - `--dry-run`: Previews the relocation and cluster slack impact.
    - `--apply`: Executes safe file relocation into designated sub-taxonomies using `AutoZoner.resolve_destination_conflict()`.
  - CLI command: `cmd_classify` in `smart_drive/cli/cmd_classify.py`.

### 8.4 R4: Test Suite Expansion & Documentation
- New test suites under `tests/`:
  - `test_ui.py`: Tests HTTP server request handling, API routes (`/api/status`, `/api/audit`, `/api/search`, `/api/junk`), error handling.
  - `test_snapshot.py`: Tests snapshot manifest creation, SHA-256 verification (file modification, corruption, missing file detection), incremental backup.
  - `test_classifier.py`: Tests deep file format classification (AI models, datasets, research, code repos), `--suggest`, and dry-run execution.
- Documentation:
  - `README.md` and `README_VN.md`: update with usage guides for `ui`, `snapshot`, `backup`, `classify`.
  - `pyproject.toml`: bump `version = "1.1.0"`.

---
*Analysis completed by Explorer 1 on 2026-09-26.*
