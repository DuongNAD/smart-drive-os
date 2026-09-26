# SmartDrive-OS v1.1.0: Comprehensive Requirements & Specification Analysis

**Author:** Explorer 3 / Spec Miner  
**Date:** 2026-09-26  
**Target Version:** v1.1.0  
**Project Root:** `d:\teamwork_projects\smart_drive_os`  
**Reference Document:** `ORIGINAL_REQUEST.md`  
**Integrity Mode:** Development (Zero-Dependency Python Standard Library)

---

## 1. Executive Summary & Specification Scope

SmartDrive-OS is an autonomous exFAT SSD governance suite and sub-10ms SQLite FTS5 search system engineered for high-performance external SSDs (Kingston XS2000 2TB, 512KB allocation blocks) and AI Coding Agents (Google Antigravity, Claude, Cursor, Windsurf).

The v1.1.0 upgrade represents a major generational expansion, delivering four key capability pillars while strictly maintaining the **Zero-Dependency Invariant** (100% Python Standard Library: `http.server`, `hashlib`, `json`, `sqlite3`, `urllib`, `dataclasses`, `argparse`, `shutil`):

1. **R1: Zero-Dependency Web Dashboard & Visual UI (`smart-drive ui`)**:
   An embedded HTTP dashboard providing interactive dark-mode data visualizations: 6-taxonomy distribution, 512KB cluster slack metrics, sub-10ms FTS5 instant search, and a safe 3-tier junk cleanup control center with dry-run preview and 1-click confirmation.
2. **R2: Snapshot & Backup Engine (`smart-drive snapshot` / `restore` / `backup`)**:
   Point-in-time snapshot creation with SHA-256 cryptographic manifests for high-value data partitions (`02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox`), verification of file integrity, and fast incremental backups to secondary media.
3. **R3: Deep Classifier & Auto-Tagger (`smart-drive classify`)**:
   Content-aware file classification engine recognizing AI models (.safetensors headers, .gguf magic bytes, .onnx, .pt/.pth, HuggingFace configs), datasets (.parquet, .arrow, .jsonl, .csv, .hdf5), research docs (.pdf, .epub, .md), and developer project structures (Git, Node.js, Python, Rust) with `--suggest` and safe auto-zoning.
4. **R4: Quality Assurance, Packaging & v1.1.0 Release**:
   Expansion of standard library `unittest` test suite covering 100% of new modules and routes, synchronized bilingual documentation (`README.md`, `README_VN.md`), `pyproject.toml` version bump to `1.1.0`, and Git release commit/tag pushed to GitHub `DuongNAD/smart-drive-os`.

---

## 2. Traceability Matrix: Requirements to Existing Codebase

| Req ID | Requirement Description | Existing Codebase Anchor | New / Expanded Components |
|---|---|---|---|
| **R1.1** | Pure Python `http.server` Web UI | `smart_drive.cli.main` (CLI dispatcher) | `smart_drive.ui.server`, `smart_drive.ui.template`, `smart_drive.cli.cmd_ui` |
| **R1.2** | 6 Taxonomy & Cluster Slack Charts | `smart_drive.core.auditor.StorageAuditor` | Canvas/SVG embedded dark-mode charts, API `/api/audit` |
| **R1.3** | Sub-10ms Instant Search with Filters | `smart_drive.search.engine.SearchEngine`, `smart_drive.indexer.db` | API `/api/search` with parameter parsing & JSON response |
| **R1.4** | 3-Tier Safe Cleanup with Dry-run & 1-Click | `smart_drive.core.junk_detector`, `smart_drive.core.purge_engine` | API `/api/junk` (preview) & `/api/clean` (purge execution) |
| **R1.5** | CLI Flags `--port`, `--no-browser`, `--root` | `smart_drive.cli.main.build_parser` | Subparser `ui` with port (default 8765), browser launcher |
| **R2.1** | Point-in-time Snapshot for Taxonomies 02, 03, 05 | `smart_drive.core.config.STANDARD_TAXONOMIES` | `smart_drive.core.snapshot.SnapshotManager` |
| **R2.2** | SHA-256 Manifest Generation & Hashing | Standard `hashlib.sha256` | Snapshot manifest JSON schema with Merkle root hash |
| **R2.3** | CLI `snapshot create`, `list`, `verify` | `smart_drive.cli.main` | `smart_drive.cli.cmd_snapshot`, `SnapshotVerifier` |
| **R2.4** | Incremental `backup --target <path>` | `smart_drive.core.purge_engine` (safe copy/excl) | `smart_drive.core.snapshot.BackupEngine`, `cmd_backup` |
| **R3.1** | Deep File Signature & Header Classifier | `smart_drive.core.config.classify_by_extension` | `smart_drive.core.classifier.DeepSignatureDetector` |
| **R3.2** | AI Models (GGUF, Safetensors, ONNX, PyTorch, HF) | `smart_drive.core.config.CATEGORIES["AI Models"]` | Magic bytes & JSON header parsing engine |
| **R3.3** | Datasets (Parquet, Arrow, JSONL, CSV, HDF5) | `smart_drive.core.config.CATEGORIES` | Binary header recognition (`PAR1`, `ARROW1`, `\x89HDF`) |
| **R3.4** | Project Structure Recognition (Git, Node, Py, Rust) | `smart_drive.core.auto_zoner.PROJECT_INDICATORS` | `smart_drive.core.classifier.ProjectDetector` |
| **R3.5** | CLI `classify --suggest` & `--apply` | `smart_drive.core.auto_zoner.AutoZoner` | `smart_drive.cli.cmd_classify` |
| **R4.1** | Test Suite Expansion (`unittest`) | `tests/helpers.py`, `tests/test_cli_e2e.py` | `tests/test_ui.py`, `tests/test_snapshot.py`, `tests/test_classifier.py` |
| **R4.2** | Documentation (`README.md`, `README_VN.md`) | `README.md`, `README_VN.md` | Add sections, CLI tables, workflows, ASCII architecture diagrams |
| **R4.3** | Version Bump (1.1.0) & Packaging | `pyproject.toml`, `smart_drive/__init__.py` | Update `version = "1.1.0"` and `__version__ = "1.1.0"` |
| **R4.4** | Git Commit, Tag `v1.1.0`, Push to GitHub | `.git/config` (`origin` -> DuongNAD/smart-drive-os) | Conventional commit, tag `v1.1.0`, push origin main |

---

## 3. Deep Feature Specifications

### 3.1 R1: Web UI & Dashboard Engine (`smart-drive ui`)

#### 3.1.1 Architectural Design
- **Server Framework:** Pure Python Standard Library `http.server.ThreadingHTTPServer` with custom request handler `SmartDriveUIHandler(http.server.BaseHTTPRequestHandler)`.
- **Concurrency Model:** Threaded request handling allowing non-blocking FTS5 searches while background audits or previews run.
- **Security Boundaries:**
  - Binds strictly to `127.0.0.1` by default (localhost only).
  - Explicit CORS restriction (same-origin only).
  - Rate limiting & path traversal protections (paths resolved against drive root).
- **Embedded Frontend:**
  - Single-page application (SPA) containing embedded HTML5, CSS3, and ES6 JavaScript.
  - Zero external CDN dependencies (no Google Fonts, Bootstrap, FontAwesome, or Chart.js). All charts rendered dynamically via HTML5 Canvas and SVG.
  - Dark mode aesthetic: Slate/Zinc color palette (`#0f172a` body background, `#1e293b` container cards, `#334155` borders, `#38bdf8` cyan accents, `#4ade80` emerald indicators, `#f43f5e` danger alerts).
  - Fully responsive layout for mobile, tablet, and widescreen desktop monitors.

#### 3.1.2 Command Line Interface
```bash
smart-drive ui [--port 8765] [--host 127.0.0.1] [--no-browser] [--root <path>]
```
- `--port`: Port number to listen on (integer, default: `8765`). If occupied, cleanly reports error or optionally tries fallback.
- `--host`: Host interface to bind (string, default: `"127.0.0.1"`).
- `--no-browser`: Flag to suppress launching system default browser via `webbrowser.open()`.
- `--root`: SSD drive root directory (default: autodetected or current directory).

#### 3.1.3 REST API Endpoints Specification

| Method | Endpoint | Description | Query / Body Parameters | Response Format |
|---|---|---|---|---|
| `GET` | `/` | Serves dashboard SPA HTML | None | `text/html; charset=utf-8` |
| `GET` | `/api/status` | SSD health, mount, cluster geometry, index status | None | `application/json` (Status object) |
| `GET` | `/api/audit` | Comprehensive storage audit, 6 taxonomy breakdown, 512KB slack | `force_refresh=0\|1` | `application/json` (Audit report) |
| `GET` | `/api/search` | Instant FTS5 search (<10ms target) | `q`: query keyword<br>`category`: category filter<br>`ext`: extension filter<br>`size`: size range<br>`limit`: max hits (default: 50) | `application/json` (`{ matches, total_count, elapsed_ms }`) |
| `GET` | `/api/junk` | Preview detectable junk items (Dry-run mode) | `tier`: 1, 2, or 3 (default: 1) | `application/json` (`{ dry_run: true, junk_count, items, reclaimable_bytes, reclaimable_slack }`) |
| `POST` | `/api/clean` | Executes safe purge for selected tier | JSON Body:<br>`{ "tier": 1, "confirm": true }` | `application/json` (`{ purged_count, reclaimed_bytes, reclaimed_slack, audit_log }`) |
| `GET` | `/api/snapshots` | List point-in-time snapshots | None | `application/json` (List of snapshot summaries) |

#### 3.1.4 Dashboard UI Components
1. **Header & SSD Metric Bar**:
   - Drive mount status badge, filesystem type (`exFAT`), cluster allocation size (`524,288 Bytes / 512 KB`).
   - Summary gauges: Total Logical Size vs Physical Allocated Size, Total Cluster Slack Wasted (bytes + percentage), Total Indexed Files.
2. **Taxonomy & Slack Visualization Panel**:
   - 6-Taxonomy Donut Chart: Proportional storage breakdown of `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects`, `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage`.
   - Cluster Slack Efficiency Bar: Side-by-side comparison of actual data size vs physical allocated size per taxonomy, highlighting micro-file overhead.
3. **Instant Search Studio (<10ms)**:
   - Real-time debounced input box querying `/api/search` on keystroke.
   - Filter pill buttons: Category (`AI Models`, `Code`, `Docs`, `Media`, etc.), Extension presets, File Size thresholds (`>100MB`, `>1GB`).
   - Interactive results table with file name, relative path, nominal size, allocated size, slack, and category badge.
4. **3-Tier Junk Cleanup Dashboard**:
   - Tier Selector Tabs: Tier 1 (Safe: OS junk, thumbnails, bytecode), Tier 2 (Dev Caches: build, dist, pytest), Tier 3 (Sensitive: logs, crash dumps).
   - Real-time Dry-Run table displaying items scheduled for deletion.
   - Prominent **"1-Click Clean"** action button triggering a modal confirmation dialog before issuing `POST /api/clean`.

---

### 3.2 R2: Snapshot & Backup Engine (`smart-drive snapshot` / `restore` / `backup`)

#### 3.2.1 Core Architectural Principles
- **Target Partitions:** Default snapshot target covers the critical intellectual property and active working zones:
  - `02_Learning_Knowledge` (Research papers, reference books, technical notes)
  - `03_Development_Projects` (Active source code, Git repos, workspaces)
  - `05_Dev_Toolbox` (Dev utilities, scripts, quantization tools)
- **Cryptographic Manifest:** Every snapshot computes individual SHA-256 digests for all constituent files, plus a top-level deterministic manifest signature (Merkle root or concatenated digest).
- **Storage Location:** Saved in `.smart_drive/snapshots/<snapshot_id>.json`.
- **Zero Destruction Guarantee:** The snapshot engine is strictly read-only on source files; restore operations require explicit confirmation.

#### 3.2.2 Manifest JSON Schema
```json
{
  "schema_version": "1.1.0",
  "snapshot_id": "snap_20260926_130000",
  "name": "baseline_dev_knowledge",
  "created_at": "2026-09-26T06:00:00.000Z",
  "root_path": "D:/",
  "target_taxonomies": [
    "02_Learning_Knowledge",
    "03_Development_Projects",
    "05_Dev_Toolbox"
  ],
  "total_files": 1420,
  "total_bytes": 5242880000,
  "manifest_hash": "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
  "files": [
    {
      "rel_path": "02_Learning_Knowledge/Notes/exfat_architecture.md",
      "size": 15420,
      "mtime": 1727330000.0,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "category": "Docs",
      "taxonomy": "02_Learning_Knowledge"
    }
  ]
}
```

#### 3.2.3 CLI Commands Specification
1. **`smart-drive snapshot create [name]`**:
   ```bash
   smart-drive snapshot create [name] [--root <path>] [--taxonomies <list>] [--json]
   ```
   - Scans target taxonomies.
   - Computes SHA-256 hash in 64KB streaming blocks per file (zero memory blowup).
   - Generates manifest, verifies self-integrity, and writes to `.smart_drive/snapshots/`.
2. **`smart-drive snapshot list`**:
   ```bash
   smart-drive snapshot list [--root <path>] [--json]
   ```
   - Reads snapshot catalog and outputs structured summary table (Snapshot ID, Name, Created Timestamp, File Count, Total Size, Taxonomies).
3. **`smart-drive snapshot verify [name]`**:
   ```bash
   smart-drive snapshot verify [name] [--root <path>] [--json]
   ```
   - Validates live files against the recorded manifest.
   - Categorizes each entry into:
     - `VERIFIED`: Size, mtime, and SHA-256 match perfectly.
     - `MODIFIED`: File exists but SHA-256 checksum differs.
     - `MISSING`: File recorded in manifest has been deleted or moved.
     - `UNTRACKED`: New files present in target directories not in manifest.
   - Returns exit code 0 if 100% verified; exit code 1 if corruptions or missing files detected.
4. **`smart-drive backup --target <path>`**:
   ```bash
   smart-drive backup --target <destination_path> [--root <path>] [--dry-run] [--taxonomies <list>] [--json]
   ```
   - High-speed incremental backup engine.
   - Compares source files against destination directory using mtime, size, and SHA-256 verification.
   - Automatically excludes Tier-1 and Tier-2 junk (e.g. `.DS_Store`, `Thumbs.db`, `__pycache__`, `.pytest_cache`).
   - Copies only created or modified files while preserving relative hierarchy and file timestamps.
   - Writes backup transaction log `.smart_drive_backup.json` at destination.
5. **`smart-drive restore [name]` (or `smart-drive snapshot restore [name]`):**
   ```bash
   smart-drive restore [name] --from <backup_or_snapshot_path> [--target <restore_path>] [--dry-run]
   ```
   - Restores missing or corrupted files from a verified backup target or snapshot staging directory.

---

### 3.3 R3: Deep Classifier & Auto-Tagger (`smart-drive classify`)

#### 3.3.1 Multilayer Content Identification Engine
Unlike naive extension-only classifiers, the SmartDrive-OS Deep Classifier performs content-level introspection using binary magic bytes and structured header decoders:

| Category | File Type | Magic Bytes / Header Signature | Parser & Validation Rule | Destination Taxonomy |
|---|---|---|---|---|
| **AI Models** | **GGUF** | `b"GGUF"` (`0x47, 0x47, 0x55, 0x46`) | Reads little-endian uint32 version, uint64 tensor_count, kv_count | `01_AI_Models/gguf` |
| **AI Models** | **Safetensors** | First 8 bytes: `<Q` uint64 header size `N` | Parses next `N` bytes as UTF-8 JSON; verifies tensor dict and `__metadata__` | `01_AI_Models/safetensors` |
| **AI Models** | **ONNX** | Extension `.onnx` or protobuf model proto | Validates protobuf header `0x08` (IR version) | `01_AI_Models/onnx` |
| **AI Models** | **PyTorch** | `b"PK\x03\x04"` or `b"\x80\x02"`..`\x80\x05"` | Zip contains `byteorder`, `data.pkl`, or `pytorch_model.bin` | `01_AI_Models/checkpoints` |
| **AI Models** | **HF Configs** | `config.json`, `tokenizer.json`, etc. | JSON contains `architectures`, `model_type`, `transformers_version` | `01_AI_Models/configs` |
| **Datasets** | **Parquet** | First & last 4 bytes: `b"PAR1"` | Apache Parquet columnar table verification | `02_Learning_Knowledge/Datasets` |
| **Datasets** | **Arrow / Feather** | First 6 bytes: `b"ARROW1"` | Apache Arrow IPC stream / file format | `02_Learning_Knowledge/Datasets` |
| **Datasets** | **JSONL** | Lines of valid JSON objects | Validates first N lines parse cleanly as JSON dicts | `02_Learning_Knowledge/Datasets` |
| **Datasets** | **CSV / TSV** | Delimited text with headers | Inspects delimiter consistency (`csv.Sniffer`) | `02_Learning_Knowledge/Datasets` |
| **Datasets** | **HDF5 / H5** | `b"\x89HDF\r\n\x1a\n"` | HDF5 hierarchical data format magic | `02_Learning_Knowledge/Datasets` |
| **Docs** | **PDF** | Starts with `b"%PDF-"` | PDF document header | `02_Learning_Knowledge/Papers` |
| **Docs** | **ePub** | Zip archive with `application/epub+zip` | Reads `mimetype` file in zip | `02_Learning_Knowledge/Books` |
| **Docs** | **Markdown** | Text starting with frontmatter `---` or `# ` | Markdown document or notebook notes | `02_Learning_Knowledge/Notes` |
| **Projects** | **Git Repo** | Root contains `.git/` directory or file | Git version controlled repository | `03_Development_Projects` |
| **Projects** | **Node.js** | Root contains `package.json` | Node / TypeScript project workspace | `03_Development_Projects/NodeJS` |
| **Projects** | **Python** | `pyproject.toml`, `setup.py`, `requirements.txt` | Python project workspace | `03_Development_Projects/Python` |
| **Projects** | **Rust** | `Cargo.toml`, `Cargo.lock` | Rust cargo crate workspace | `03_Development_Projects/Rust` |

#### 3.3.2 CLI Commands Specification
```bash
smart-drive classify [path] [--root <path>] [--suggest] [--apply] [--dry-run] [--json]
```
- `smart-drive classify [path]`: Scans file or directory, runs signature detection, and displays detailed breakdown.
- `smart-drive classify --suggest`: Analyzes unclassified or misplaced files and prints a recommendation table with current path, detected type, suggested destination, and reasoning.
- `smart-drive classify --apply [--dry-run]`: Executes file relocation into target taxonomy subdirectories. Defaults to simulation (`--dry-run`) unless `--apply` is explicitly given.
- **Safety Invariant:** Never relocates protected root files (`AGENTS.md`, `GEMINI.md`, `README.md`, setup scripts) or system directories.

---

### 3.4 R4: Test Suite, Documentation & GitHub Release

#### 3.4.1 Test Suite Expansion Strategy
All tests must execute seamlessly via `python -m unittest discover tests` with **Zero External Dependencies** (pure `unittest`, zero `pytest` required).
New test modules:
1. `tests/test_ui.py`:
   - Instantiation of `SmartDriveUIHandler` and `ThreadingHTTPServer` on ephemeral port (`port=0`).
   - Route tests: `GET /` (HTTP 200, contains HTML/CSS), `GET /api/status` (JSON schema match), `GET /api/audit` (taxonomies and slack metrics), `GET /api/search` (query latency and filter results), `GET /api/junk` (dry-run preview), `POST /api/clean` (dry-run and confirmed purge), `GET /api/snapshots` (list snapshots).
   - Header validation: `Content-Type: application/json; charset=utf-8`, security headers.
2. `tests/test_snapshot.py`:
   - Snapshot manifest creation, SHA-256 calculation against reference oracles.
   - Verification of intact files (100% verified status).
   - Detection of modified files (tampered content), missing files (deleted), and untracked additions.
   - Incremental backup engine: test copying new/modified files and skipping unchanged files.
3. `tests/test_classifier.py`:
   - Binary signature detection on mock files: GGUF magic, Safetensors JSON header, Parquet `PAR1`, Arrow `ARROW1`, HDF5 `\x89HDF`, PDF `%PDF-`, ePub zip, HF configs.
   - Project directory detection: Git repo, Node.js project, Python package, Rust crate.
   - `--suggest` plan generation and conflict-free safe relocation.
4. `tests/test_cli_e2e.py` Expansion:
   - Add end-to-end subprocess tests for `ui --help`, `snapshot create/list/verify`, `backup --target`, `classify --suggest`.

#### 3.4.2 Documentation Requirements
- `README.md`: English documentation updated with v1.1.0 badges, new feature highlights, complete CLI manual for `ui`, `snapshot`, `backup`, `classify`, API reference table, and ASCII architecture flowcharts.
- `README_VN.md`: Comprehensive Vietnamese documentation covering all new commands, configuration flags, usage scenarios, and safety principles.

#### 3.4.3 Package & Release Workflow
- Update `pyproject.toml` to `version = "1.1.0"`.
- Update `smart_drive/__init__.py` to `__version__ = "1.1.0"`.
- Clean working tree verification (`git status`).
- Git commit message following Conventional Commits:
  `feat: release SmartDrive-OS v1.1.0 with Web UI, Snapshot Engine & AI Classifier`
- Git tag: `v1.1.0`.
- Push to GitHub: `git push origin main --tags`.

---

## 4. Complete Feature Inventory Table

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|---|---|---|---|---|---|---|
| **F01** | Web UI | Local HTTP Dashboard Server | Spawns zero-dependency multi-threaded web server on localhost | `--port` (default: 8765), `--host` (127.0.0.1), `--root` | Active HTTP listening socket, console banner | Port conflict error -> informative message or fallback | ORIGINAL_REQUEST.md § R1, cmd_ui.py |
| **F02** | Web UI | Auto-Launch Browser | Opens system default web browser to the dashboard URL | `--no-browser` flag (suppresses launch) | Browser tab launched with `http://127.0.0.1:8765` | `webbrowser.Error` caught gracefully with URL printed | ORIGINAL_REQUEST.md § R1 |
| **F03** | Web UI | Embedded Dark Mode SPA | Single-page UI with dark theme, responsive flex/grid, zero external CSS/JS | `GET /` | Embedded HTML5/CSS3/JS document | 500 error if asset rendering fails | ORIGINAL_REQUEST.md § R1 |
| **F04** | Web UI | 6-Taxonomy Storage Breakdown Chart | Canvas/SVG interactive chart displaying distribution across 6 taxonomies | `GET /api/audit` | JSON object containing taxonomy metrics & SVG/Canvas render | 500 error if drive unreadable | ORIGINAL_REQUEST.md § R1 |
| **F05** | Web UI | 512KB Cluster Slack Visualizer | Comparative visualization of nominal vs physical allocated space | `GET /api/audit` | Visual slack bar chart & percentage indicators | 500 error if audit fails | ORIGINAL_REQUEST.md § R1 |
| **F06** | Web UI | Sub-10ms Instant FTS5 Search API | Live search endpoint powered by SQLite FTS5 index | `GET /api/search?q=...&category=...&size=...` | JSON matches list, count, and query latency ms | 400 on malformed query; 404 if DB missing | ORIGINAL_REQUEST.md § R1, search/engine.py |
| **F07** | Web UI | 3-Tier Junk Cleanup Dashboard | Live preview of junk files categorized by safety tier | `GET /api/junk?tier=1\|2\|3` | JSON array of junk items, reclaimable bytes & slack | 400 on invalid tier | ORIGINAL_REQUEST.md § R1, core/junk_detector.py |
| **F08** | Web UI | 1-Click Confirmed Purge API | Executes safe deletion of detected junk with confirmation guard | `POST /api/clean` with `{ tier, confirm: true }` | JSON summary of deleted files, reclaimed bytes & slack | 400 if `confirm` is false; 403 on protected files | ORIGINAL_REQUEST.md § R1, core/purge_engine.py |
| **F09** | Snapshot | Point-in-Time Snapshot Creation | Captures state of critical taxonomies (02, 03, 05) with SHA-256 hashes | `smart-drive snapshot create [name]` | Manifest JSON saved in `.smart_drive/snapshots/` | Exits 1 if target paths do not exist | ORIGINAL_REQUEST.md § R2 |
| **F10** | Snapshot | Streaming SHA-256 Checksum Engine | Computes cryptographic digest in 64KB chunks for arbitrarily large files | File path string | 64-character hexadecimal SHA-256 string | Handles `PermissionError` / `FileNotFoundError` | ORIGINAL_REQUEST.md § R2 |
| **F11** | Snapshot | Snapshot Catalog Listing | Displays table or JSON of all stored snapshots | `smart-drive snapshot list [--json]` | Formatted table or JSON catalog array | Returns empty list if no snapshots exist | ORIGINAL_REQUEST.md § R2 |
| **F12** | Snapshot | File Integrity Verification | Compares current live files against recorded snapshot manifest | `smart-drive snapshot verify [name] [--json]` | Verification report (VERIFIED, MODIFIED, MISSING, UNTRACKED) | Returns exit code 1 if any discrepancy detected | ORIGINAL_REQUEST.md § R2 |
| **F13** | Snapshot | Incremental Backup Engine | Synchronizes new/modified files to destination media without copying junk | `smart-drive backup --target <path> [--dry-run]` | Summary of files copied, bytes transferred, elapsed time | Validates destination path writable; exits 1 on error | ORIGINAL_REQUEST.md § R2 |
| **F14** | Snapshot | Snapshot Restoration | Restores corrupted or missing files from backup or snapshot staging | `smart-drive restore [name] --from <path>` | Summary of restored files and byte count | Prompts before overwrite; dry-run preview | ORIGINAL_REQUEST.md § R2 |
| **F15** | Classifier | Deep AI Model Recognition | Recognizes GGUF magic, Safetensors headers, ONNX, PyTorch checkpoints | File path / stream | ClassificationResult (type, metadata, confidence) | Falls back to extension if header unreadable | ORIGINAL_REQUEST.md § R3 |
| **F16** | Classifier | Dataset Format Recognition | Binary header recognition for Parquet (`PAR1`), Arrow, HDF5, JSONL, CSV | File path / stream | ClassificationResult with dataset taxonomy routing | Falls back to Docs/Other if invalid | ORIGINAL_REQUEST.md § R3 |
| **F17** | Classifier | Research Document Recognition | Validates PDF headers (`%PDF-`), ePub zip mimetypes, Markdown notes | File path / stream | ClassificationResult with learning taxonomy routing | Fallback to generic Docs | ORIGINAL_REQUEST.md § R3 |
| **F18** | Classifier | Project Structure Recognition | Identifies Git repos, Node.js (`package.json`), Python, and Rust crates | Directory path | ProjectType (Git, Node, Python, Rust) | Ignored if no project markers detected | ORIGINAL_REQUEST.md § R3, auto_zoner.py |
| **F19** | Classifier | Migration Suggestion Mode | Generates relocation plan for misplaced files without modifying filesystem | `smart-drive classify --suggest [--root <path>]` | Formatted suggestion table or JSON plan | Exits 0 with message if all files correctly zoned | ORIGINAL_REQUEST.md § R3 |
| **F20** | Classifier | Safe Auto-Zoning Relocation | Moves classified files into target taxonomy subdirectories | `smart-drive classify --apply [--dry-run]` | Relocation report with bytes moved and slack gained | Inviolable guards prevent touching system/root files | ORIGINAL_REQUEST.md § R3, auto_zoner.py |
| **F21** | Test Suite | 100% Passing Standard `unittest` | Comprehensive unit & integration tests covering all new features | `python -m unittest discover tests` | Test execution results: Ran N tests, OK | Zero external dependencies; 100% pass | ORIGINAL_REQUEST.md § R4 |
| **F22** | Docs | English Documentation Upgrade | Updated `README.md` with v1.1.0 instructions, tables, and architecture | File inspection | Formatted Markdown | N/A | ORIGINAL_REQUEST.md § R4 |
| **F23** | Docs | Vietnamese Documentation Upgrade | Synchronized `README_VN.md` with complete v1.1.0 guidelines | File inspection | Formatted Markdown | N/A | ORIGINAL_REQUEST.md § R4 |
| **F24** | Packaging | Version 1.1.0 Bump | Increments package version in `pyproject.toml` and `smart_drive` | `pyproject.toml`, `__init__.py` | Consistent `1.1.0` string across all sources | Packaging validation fails if versions mismatch | ORIGINAL_REQUEST.md § R4 |
| **F25** | Release | Git Conventional Commit & Tag Push | Creates release commit, tags `v1.1.0`, and pushes to GitHub | Git commands | Pushed commit and tag `v1.1.0` on `origin main` | Fails if working tree dirty or network error | ORIGINAL_REQUEST.md § R4 |

---

## 5. Edge Cases & Boundary Conditions

| # | Feature | Input / Condition | Expected & Observed Behavior |
|---|---|---|---|
| **E01** | `smart-drive ui` | Default port 8765 already bound by another process | Catch `OSError` (e.g. `EADDRINUSE` / WinError 10048); display clear error message suggesting alternative `--port` or auto-incrementing port. |
| **E02** | `smart-drive ui` | Headless environment (no GUI / no default browser) | `webbrowser.open()` returns False or throws exception; catch gracefully, output URL `http://127.0.0.1:<port>` to terminal, continue running server. |
| **E03** | `smart-drive ui` | Malformed search query (unclosed quotes `ext:"pdf`) | `parse_search_query` handles unclosed quotes via fallback or escaping; does not crash HTTP server; returns valid JSON result with 0 matches or sanitized query. |
| **E04** | `smart-drive ui` | Attempt to delete protected root file via `/api/clean` | `PurgeEngine.delete_item` and `is_protected_root_file` raise `SecurityViolationError`; HTTP handler returns 403 Forbidden with security violation reason. |
| **E05** | `smart-drive snapshot` | Empty taxonomy folder or 0-byte file in target taxonomies | 0-byte files hash to `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`; empty directory recorded gracefully without crashing manifest generator. |
| **E06** | `smart-drive snapshot` | Multi-gigabyte AI model file (e.g. 15GB `.safetensors` / `.gguf`) | Hashing reads in 64KB streaming buffers; does not load entire file into memory; memory footprint remains <50MB regardless of file size. |
| **E07** | `smart-drive snapshot verify` | File permissions changed to read-only on Windows | Standard library `open(..., 'rb')` opens read-only files without error; verifies hash accurately. |
| **E08** | `smart-drive snapshot verify` | File truncated, appended, or bit-flipped | SHA-256 mismatch detected; marked as `MODIFIED` / `CORRUPTED`; summary flags verification failure with non-zero exit code. |
| **E09** | `smart-drive backup` | Target destination runs out of disk space | Handle `OSError` (`ENOSPC` / WinError 112); rollback or record partial failure in backup manifest; report exact remaining space. |
| **E10** | `smart-drive backup` | Destination directory does not exist | Automatically create destination parent directories (`os.makedirs(..., exist_ok=True)`) or prompt user. |
| **E11** | `smart-drive classify` | Truncated / corrupt Safetensors file (header size > actual file length) | Header length validation checks `N <= file_size - 8`; if invalid, rejects as Safetensors and falls back to generic binary without throwing unhandled exceptions. |
| **E12** | `smart-drive classify` | File with `.parquet` extension but lacking `PAR1` magic bytes | Signature check detects missing magic header/footer; reports extension mismatch and avoids incorrect classification. |
| **E13** | `smart-drive classify` | Source code repo containing nested repos (e.g. git submodules or vendor packages) | Recognizes top-level project root; respects `.git` boundaries and excludes nested build junk (`node_modules`, `target`, `__pycache__`). |
| **E14** | `smart-drive classify --apply` | Target destination file already exists with same name | Check conflict resolution: if SHA-256 identical, skip duplicate move; if different, append numeric suffix (e.g. `_1.safetensors`) or report conflict in dry-run. |
| **E15** | `smart-drive classify --apply` | File is an inviolable protected root script (`setup_win.bat`) | Inviolable check blocks relocation; item flagged as protected; operation safely skipped. |

---

## 6. Architecture & Subsystem Integration Plan

### 6.1 Package File Layout
```
smart_drive/
├── __init__.py                # Version bump to 1.1.0, exports new engines
├── __main__.py                # Entry point
├── cli/
│   ├── __init__.py
│   ├── main.py                # Updated CLI dispatcher registering ui, snapshot, classify
│   ├── cmd_audit.py           # Existing audit handler
│   ├── cmd_clean.py           # Existing clean handler
│   ├── cmd_init.py            # Existing init handler
│   ├── cmd_mcp.py             # Existing MCP server handler
│   ├── cmd_mcp_config.py      # Existing MCP registrar
│   ├── cmd_organize.py        # Existing organize handler
│   ├── cmd_search.py          # Existing search handler
│   ├── cmd_sentinel.py        # Existing sentinel handler
│   ├── cmd_status.py          # Existing status handler
│   ├── cmd_ui.py              # [NEW] Web UI server launcher command
│   ├── cmd_snapshot.py        # [NEW] Snapshot & restore management commands
│   ├── cmd_backup.py          # [NEW] Incremental backup command
│   └── cmd_classify.py        # [NEW] Deep classifier & auto-tagger command
├── core/
│   ├── __init__.py
│   ├── config.py              # Extended with Datasets categories & signature constants
│   ├── exfat_compat.py        # exFAT path & character normalization
│   ├── scanner.py             # Fast directory scanner stream
│   ├── auditor.py             # Storage audit & 512KB slack calculator
│   ├── junk_detector.py       # 3-tier junk rules
│   ├── purge_engine.py        # Safe purge engine with boundary protection
│   ├── duplicates.py          # 3-phase duplicate detection
│   ├── auto_zoner.py          # Drive auto-zoning
│   ├── sentinel.py            # Self-healing engine
│   ├── initializer.py         # Drive initializer
│   ├── snapshot.py            # [NEW] SnapshotManager, SnapshotVerifier, BackupEngine
│   └── classifier.py          # [NEW] DeepSignatureDetector, ProjectDetector, FileClassifier
├── indexer/
│   ├── __init__.py
│   ├── db.py                  # SQLite schema & WAL connection manager
│   └── manager.py             # FTS5 index builder & incremental updater
├── search/
│   ├── __init__.py
│   ├── engine.py              # FTS5 query engine
│   ├── parser.py              # Query syntax parser
│   └── formatter.py           # Output formatters (table, json, csv)
├── ui/                        # [NEW] Zero-dependency Web UI package
│   ├── __init__.py
│   ├── server.py              # ThreadingHTTPServer & SmartDriveUIHandler
│   └── template.py            # Embedded Dark Mode HTML5/CSS3/SVG/JS SPA
└── mcp/                       # Model Context Protocol support
    ├── __init__.py
    ├── server.py
    ├── proxy.py
    └── registrar.py
```

### 6.2 Subsystem Dependency Graph
```
                          ┌─────────────────────────────┐
                          │    smart_drive.cli.main     │
                          └──────────────┬──────────────┘
               ┌─────────────────────────┼─────────────────────────┐
               ▼                         ▼                         ▼
      ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
      │   cli.cmd_ui    │       │cli.cmd_snapshot │       │cli.cmd_classify │
      └────────┬────────┘       └────────┬────────┘       └────────┬────────┘
               ▼                         ▼                         ▼
      ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
      │    ui.server    │       │  core.snapshot  │       │ core.classifier │
      └────────┬────────┘       └────────┬────────┘       └────────┬────────┘
               │                         │                         │
     ┌─────────┴─────────┐               │                         │
     ▼                   ▼               │                         │
┌──────────┐       ┌───────────┐         │                         │
│ auditor  │       │  search   │         │                         │
│ & slack  │       │  engine   │         │                         │
└────┬─────┘       └─────┬─────┘         │                         │
     │                   │               │                         │
     ▼                   ▼               ▼                         ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    smart_drive.core.config & exfat_compat                 │
│              (512KB Cluster Math, Protected Invariants, Taxonomies)       │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Proposed Milestones & Work Breakdown Structure

Following the project iteration pattern, the implementation should be partitioned into four sequential, verifiable milestones:

```
[M0: Survey & Spec Mining] (CURRENT)
         │
         ▼
[M1: R1 Web Dashboard & Visual UI (`smart-drive ui`)]
         │
         ▼
[M2: R2 Snapshot & Backup System (`smart-drive snapshot` / `backup`)]
         │
         ▼
[M3: R3 Deep Classifier & Auto-Tagger (`smart-drive classify`)]
         │
         ▼
[M4: R4 Test Suite, Documentation & GitHub v1.1.0 Release]
```

### Milestone 1: Zero-Dependency Web Dashboard & Visual UI (R1)
- **Goal:** Deliver `smart-drive ui` HTTP server, embedded dark-mode UI, charts, and API routes.
- **Deliverables:**
  - `smart_drive/ui/__init__.py`, `smart_drive/ui/server.py`, `smart_drive/ui/template.py`
  - `smart_drive/cli/cmd_ui.py` registered in `smart_drive/cli/main.py`
  - Unit tests in `tests/test_ui.py`
- **Verification Gate:** `python -m unittest tests/test_ui.py` passes 100%; HTTP server serves `/`, `/api/status`, `/api/audit`, `/api/search`, `/api/junk`, `/api/clean` on port 8765 with zero external dependencies.

### Milestone 2: Snapshot & Backup System (R2)
- **Goal:** Deliver point-in-time snapshots, SHA-256 verification, and incremental backups.
- **Deliverables:**
  - `smart_drive/core/snapshot.py` (`SnapshotManager`, `SnapshotManifest`, `SnapshotVerifier`, `BackupEngine`)
  - `smart_drive/cli/cmd_snapshot.py` and `cmd_backup.py` registered in `main.py`
  - Unit tests in `tests/test_snapshot.py`
- **Verification Gate:** `python -m unittest tests/test_snapshot.py` passes 100%; snapshot creation, listing, file corruption detection, and incremental backup verified against test fixtures.

### Milestone 3: Deep Classifier & Auto-Tagger (R3)
- **Goal:** Deliver content-level file identification (magic bytes + headers), project detection, and safe auto-tagging.
- **Deliverables:**
  - `smart_drive/core/classifier.py` (`DeepSignatureDetector`, `ProjectDetector`, `FileClassifier`)
  - `smart_drive/cli/cmd_classify.py` registered in `main.py`
  - Unit tests in `tests/test_classifier.py`
- **Verification Gate:** `python -m unittest tests/test_classifier.py` passes 100%; accurate detection of GGUF, Safetensors, Parquet, Arrow, HDF5, PDF, ePub, Git, Node, Python, Rust projects; `--suggest` and `--apply` safe relocation verified.

### Milestone 4: Test Suite, Documentation & GitHub Release (R4)
- **Goal:** Full test suite integration, documentation completion, packaging version bump, and GitHub v1.1.0 release push.
- **Deliverables:**
  - `tests/test_cli_e2e.py` updated with all new commands
  - `pyproject.toml` and `smart_drive/__init__.py` bumped to `1.1.0`
  - `README.md` and `README_VN.md` updated with comprehensive v1.1.0 documentation
  - Git commit: `feat: release SmartDrive-OS v1.1.0 with Web UI, Snapshot Engine & AI Classifier`
  - Git tag `v1.1.0` created and pushed to `origin main`
- **Verification Gate:** `python -m unittest discover tests` passes 100% (all existing 132 tests + all new tests); clean working tree; git remote confirms commit and tag pushed to GitHub.

---

## 8. Conclusion & Sign-Off

The specification mining phase confirms that the SmartDrive-OS codebase possesses an exceptionally well-factored architecture that naturally accommodates all four v1.1.0 requirements. The core design invariants—512KB cluster math, exFAT compatibility, inviolable root protections, and zero-dependency Python standard library compliance—remain fully preserved across all proposed specifications.
