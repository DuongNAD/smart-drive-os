# Project: SmartDrive-OS v1.1.0

## Architecture
SmartDrive-OS is a high-performance, zero-dependency storage operating system and organization toolkit strictly written in Python Standard Library (3.8+).

### Subsystems & Module Boundaries:
- `smart_drive.core.config`: Canonical taxonomies (`01_AI_Models` .. `06_Archives_Storage`), 512KB cluster slack geometry (`CLUSTER_SIZE_BYTES = 524_288`), protected root directories/files, junk tiers.
- `smart_drive.core.auditor`: Storage auditing, taxonomy byte rollups, cluster slack calculations.
- `smart_drive.indexer.db`: SQLite database with WAL, memory cache, and FTS5 virtual table with BM25 ranking.
- `smart_drive.search.engine`: Sub-10ms full-text and metadata search engine.
- `smart_drive.core.junk_detector`: 3-tier junk detection (Tier 1 Safe, Tier 2 Dev Cache, Tier 3 Sensitive).
- `smart_drive.core.purge_engine`: Inviolable `SecurityGuard` boundary checks, dry-run simulation, and safe unlinking.
- `smart_drive.ui` (M1 - COMPLETED): Zero-dependency `ThreadingHTTPServer` serving embedded modern Dark Mode SPA with interactive FTS5 search, visual storage and slack charts, and 3-tier safe cleanup dashboard.
- `smart_drive.core.snapshot` (M2 - COMPLETED): Point-in-time snapshot manifests with streaming SHA-256 hashes, integrity verification, and incremental target backup.
- `smart_drive.core.classifier` (M3 - COMPLETED): Deep content inspection (magic bytes, headers, project markers) for AI models, datasets, documents, and code repos; taxonomy auto-routing and `--suggest` mode.
- `smart_drive.cli`: Modular subparser CLI commands (`cmd_ui`, `cmd_snapshot`, `cmd_backup`, `cmd_classify`, `main`).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | F01: CLI `smart-drive ui` | CLI flags `--port` (default 8765) and `--no-browser` | M1 | Survey R1 |
| 2 | F02: Stdlib HTTP Server | `ThreadingHTTPServer` without Flask, FastAPI or pip packages | M1 | Survey R1 |
| 3 | F03: Embedded Dark Mode SPA | Modern responsive UI embedded in Python, zero CDN dependency | M1 | Survey R1 |
| 4 | F04: Taxonomy Breakdown Chart | Visual breakdown of 6 taxonomies on dashboard | M1 | Survey R1 |
| 5 | F05: 512KB Cluster Slack Chart | Visualization of wasted cluster slack bytes | M1 | Survey R1 |
| 6 | F06: Instant FTS5 Search API | `/api/search` endpoint responding in <10ms | M1 | Survey R1 |
| 7 | F07: Interactive Search Filters | Filter by extension, size range, category in UI | M1 | Survey R1 |
| 8 | F08: 3-Tier Cleanup Dashboard | Visual representation of Tier 1 Safe, Tier 2 Dev, Tier 3 Sensitive | M1 | Survey R1 |
| 9 | F09: Cleanup Dry-Run Preview | `/api/junk` endpoint previewing purge candidates safely | M1 | Survey R1 |
| 10 | F10: 1-Click Confirmed Purge | `/api/junk/clean` endpoint with explicit confirmation dialog | M1 | Survey R1 |
| 11 | F11: Headless Fallback | Graceful fallback if `webbrowser.open()` fails in headless CI | M1 | Survey R1 |
| 12 | F12: CLI `smart-drive snapshot` | Subparser with `create`, `list`, `verify` and flags | M2 | Survey R2 |
| 13 | F13: Point-in-Time Snapshot | Snapshot generation for `02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox` | M2 | Survey R2 |
| 14 | F14: Streaming SHA-256 Hashing | Constant-memory 64KB chunk SHA-256 hashing for all file sizes | M2 | Survey R2 |
| 15 | F15: Manifest Storage | Store and load JSON manifests in `.smart_drive/snapshots/<name>.json` | M2 | Survey R2 |
| 16 | F16: Snapshot Listing | `smart-drive snapshot list` showing metadata, counts, bytes | M2 | Survey R2 |
| 17 | F17: Snapshot Verification | `smart-drive snapshot verify <name>` detecting modified/missing files | M2 | Survey R2 |
| 18 | F18: Incremental Backup | `smart-drive backup --target <path>` copying changed files | M2 | Survey R2 |
| 19 | F19: CLI `smart-drive classify` | Subparser supporting `--suggest`, `--dry-run`, `--apply` | M3 | Survey R3 |
| 20 | F20: AI Models & Weights Detection | Binary header & magic inspection for GGUF, Safetensors, ONNX, PyTorch, HF | M3 | Survey R3 |
| 21 | F21: Datasets Recognition | Inspection for Parquet, Arrow, JSONL, CSV, HDF5 | M3 | Survey R3 |
| 22 | F22: Research & Docs Recognition | Recognition for PDF, ePub, Markdown notes | M3 | Survey R3 |
| 23 | F23: Source Code & Repo Detection | Structural recognition for Git, Node, Python, Rust projects | M3 | Survey R3 |
| 24 | F24: Safe Auto-Tagging & Routing | Move or suggest placement into sub-taxonomies without overwrite | M3 | Survey R3 |
| 25 | F25: Tests, Docs & Release v1.1.0 | 100% passing `unittest`, README/README_VN docs, bump `1.1.0`, git tag & push | M4 | Survey R4 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Web Dashboard & Visual UI | Features F01-F11: `smart_drive/ui/`, `cmd_ui.py`, `tests/test_ui.py` | none | DONE |
| M2 | Snapshot & Backup Engine | Features F12-F18: `smart_drive/core/snapshot.py`, `cmd_snapshot.py`, `tests/test_snapshot.py` | none | DONE |
| M3 | Classifier & Auto-Tagger | Features F19-F24: `smart_drive/core/classifier.py`, `cmd_classify.py`, `tests/test_classifier.py` | none | DONE |
| M4 | QA, Docs & GitHub Release v1.1.0 | Feature F25: Full test suite, docs update, version bump to 1.1.0, git commit & tag push | M1, M2, M3 | DONE |

## Release Information
- Version: `1.1.0`
- Release Commit: `a27af551a49aae2b13698bacedb86f1ab0749fc6`
- Release Tag: `v1.1.0`
- GitHub Repository: `https://github.com/DuongNAD/smart-drive-os`
- Test Pass Rate: 314 / 314 (100%)
- Dependency Footprint: Zero external dependencies (100% Python Standard Library)
