# SmartDrive-OS

> [!TIP]
> ### 🚀 Internal Secondary Drive Architect & C-Drive Cache Offloader
> Looking to optimize internal secondary NVMe/SATA SSDs (`D:`, `E:`), offload massive C-drive caches (HuggingFace, Ollama, Docker, uv, pip, npm) via non-elevated NTFS Directory Junctions (`mklink /J`), or monitor SSD TRIM health?
> 
> 🔗 **Active Branch**: [`internal-secondary-drive`](https://github.com/DuongNAD/smart-drive-os/tree/internal-secondary-drive) &nbsp;|&nbsp; 📖 **Documentation Guide**: [`README_INTERNAL.md`](https://github.com/DuongNAD/smart-drive-os/blob/internal-secondary-drive/README_INTERNAL.md)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Pip Dependencies](https://img.shields.io/badge/dependencies-0%20external%20pip-success.svg)](#)
[![exFAT 512KB Optimized](https://img.shields.io/badge/filesystem-exFAT%20512KB%20Guard-orange.svg)](#)
[![Web Dashboard](https://img.shields.io/badge/UI-Embedded%20Dark%20SPA-blueviolet.svg)](#)
[![MCP Protocol](https://img.shields.io/badge/MCP-JSON--RPC%202.0%20stdio-purple.svg)](https://modelcontextprotocol.io/)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os)](https://m8ven.ai/mcp/duongnad/smart-drive-os)
[![Release: v1.1.0](https://img.shields.io/badge/release-v1.1.0-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.1.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 100% Pass](https://img.shields.io/badge/tests-314%2F314%20passed%20(100%25)-brightgreen.svg)](#)

> **High-Performance Autonomous Drive Operating Suite, Visual Web Dashboard, Snapshot Integrity Engine & SQLite FTS5 Instant Search for External SSDs (exFAT) & AI Coding Agents.**

---

## The Problem: The exFAT 512KB Cluster Slack Trap

High-capacity external SSDs (such as the **Kingston XS2000 2TB**) formatted with exFAT default to an allocation unit size (cluster size) of **512 KB (524,288 bytes)**. While ideal for massive media files, this geometry is catastrophic for modern development workloads:

$$\text{Cluster Allocation} = \left\lceil \frac{\text{File Size}}{524,288} \right\rceil \times 524,288 \text{ bytes}$$

| File Nominal Size | Physical Space on Disk | Cluster Slack Wasted | Slack Ratio |
|---|---|---|---|
| **0 bytes** | 0 bytes (header entry only) | 0 bytes | 0.0% |
| **1 byte** | **524,288 bytes** (512 KB) | **524,287 bytes** | **99.9998%** |
| **10 KB** (code / config) | **524,288 bytes** (512 KB) | **514,048 bytes** | **98.05%** |
| **100 KB** (JSON / docs) | **524,288 bytes** (512 KB) | **421,888 bytes** | **80.47%** |
| **524,289 bytes** (512 KB + 1B) | **1,048,576 bytes** (1,024 KB) | **524,287 bytes** | **50.00%** |

### The Real-World Impact
- A project with **10,000 small files** totalling **15 MB** actually consumes over **5.12 GB** on your external SSD — wasting **>99% of disk capacity**.
- OS indexing daemons (macOS Spotlight `mds`, Windows Search) continuously traverse external drives, generating thousands of tiny metadata writes (`.DS_Store`, `Thumbs.db`, `.fseventsd`), causing massive I/O heat and battery drain.
- When AI coding agents (Antigravity, Claude, Cursor) explore deep workspaces, recursive file searches flood context windows and exhaust tokens.

**SmartDrive-OS** completely eliminates these issues with **zero external pip dependencies**, pure Python 3.9+ standard library (`http.server`, `hashlib`, `json`, `sqlite3`, `urllib`, `shutil`, `pathlib`), native hardware geometry calculations, anti-indexing shields, point-in-time SHA-256 snapshot protection, deep file classification, and an embedded modern dark-mode visual Web UI.

---

## Architecture

```
+-------------------------------------------------------------------------------------------------+
|                                   User & AI Agent Interfaces                                    |
|   +-----------------------+    +-----------------------+    +---------------+    +----------+   |
|   |  1-Touch Launchers    |    |   Python CLI & TUI    |    |  AI Agents    |    |  Web UI  |   |
|   |  (.bat / .command)    |    |   (smart-drive CLI)   |    |  (MCP Clients)|    | (SPA App)|   |
|   +-----------+-----------+    +-----------+-----------+    +-------+-------+    +----+-----+   |
+---------------|----------------------------|------------------------|-----------------|---------+
                |                            |                        |                 |
                +----------------------------+                        |                 |
                                             |                        | JSON-RPC 2.0    | HTTP 8765
                                             v                        v                 v
+-------------------------------------------------------------------------------------------------+
|                                       SmartDrive-OS Core                                        |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
|  | Drive Initializer  |  |  Storage Auditor   |  |   MCP Server       |  | Zero-Dep Web UI   |  |
|  | - Preset Profiles  |  |  - 512KB Geometry  |  |   - stdio RPC 2.0  |  | - ThreadingHTTP   |  |
|  | - Shield Seeding   |  |  - Slack Analytics |  |   - 8 Agent Tools  |  | - Dark Mode SPA   |  |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
|  | Purge / Safe Clean |  | SQLite FTS5 Search |  | Snapshot & Backup  |  | Deep Classifier   |  |
|  | - 3-Tier Rule Match|  | - unicode61 BM25   |  | - SHA-256 Streaming|  | - Magic Bytes &   |  |
|  | - Whitelist Shield |  | - Sub-10ms Queries |  | - Bit-Rot Verify   |  |   Project Markers |  |
|  +--------------------+  +--------------------+  +--------------------+  +-------------------+  |
+-------------------------------------------------------------------------------------------------+
                                             |
                                             v
+-------------------------------------------------------------------------------------------------+
|                         Target Storage (Kingston XS2000 2TB exFAT SSD)                          |
|   01_AI_Models/           02_Learning_Knowledge/        03_Development_Projects/                |
|   04_System_Workspaces/   05_Dev_Toolbox/               06_Archives_Storage/                    |
|   .metadata_never_index   .fseventsd/no_log             .smart_drive/index.db                   |
|   .smart_drive/snapshots/<name>.json                    <target>/backup_manifest.json           |
+-------------------------------------------------------------------------------------------------+
```

---

## What's New in v1.1.0

### 1. Zero-Dependency Web Dashboard & Visual UI (`smart-drive ui`)
- **Embedded Dark Mode SPA**: Zero external web frameworks (no Flask, FastAPI, Node, or npm). Built 100% on Python's `http.server.ThreadingHTTPServer` with embedded HTML5, CSS3, and JavaScript.
- **Visual Analytics**: Interactive 6-taxonomy distribution charts and real-time visualization of wasted 512KB cluster slack bytes.
- **Sub-10ms Instant FTS5 Search**: Real-time search bar with instant filtering by file extension, size threshold, and taxonomy category.
- **3-Tier Safe Cleanup Panel**: Visual preview of purgeable OS junk, developer caches, and crash logs with safe dry-run preview and 1-click confirmation dialog.
- **Flags**: `smart-drive ui --port 8765 --no-browser --root <path> --db <path>`.

### 2. Snapshot & Incremental Backup Engine (`smart-drive snapshot` / `backup`)
- **Point-in-Time Manifests**: Snapshot critical development and knowledge partitions (`02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox`).
- **Streaming SHA-256 Verification**: Constant-memory 64KB chunk SHA-256 hashing to verify file integrity against bit rot and silent corruption.
- **Safe Incremental Backup**: Transfer only modified or newly created files to external destinations, skipping unchanged data and system junk.
- **Commands**:
  - `smart-drive snapshot create [name]`: Record file states, sizes, and SHA-256 checksums into `.smart_drive/snapshots/<name>.json`.
  - `smart-drive snapshot list`: View all stored snapshots with file counts, logical sizes, and allocated cluster bytes.
  - `smart-drive snapshot verify <name>`: Audit data integrity against the manifest (detects modified, missing, and untracked files).
  - `smart-drive backup --target <path>`: Incremental backup with optional SHA-256 matching (`--hash`) and dry-run preview (`--dry-run`).

### 3. Deep Classifier & Auto-Tagger (`smart-drive classify`)
- **Deep File Format Recognition**: Magic byte and structural inspection:
  - **AI Models & Weights**: GGUF (`GGUF` magic), Safetensors (JSON metadata header), ONNX (protobuf header), PyTorch (`.pt`/`.pth`), HuggingFace configurations (`config.json`, `tokenizer.json`).
  - **Datasets**: Apache Parquet (`PAR1` magic), Apache Arrow (`ARROW1`), JSONL, CSV, HDF5 (`\x89HDF\r\n\x1a\n`).
  - **Research & Documents**: PDF (`%PDF-`), ePub (PK container with `application/epub+zip`), Markdown notes.
  - **Source Code Repositories**: Git projects (`.git`), Node.js (`package.json`), Python (`pyproject.toml`, `setup.py`), Rust (`Cargo.toml`).
- **Autonomous Auto-Routing**: Suggests or moves loose files into canonical sub-taxonomies safely without overwrite or collision.
- **Commands**: `smart-drive classify [dir] --suggest --dry-run --apply --json`.

---

## 3-Step Quickstart

### Step 1: Run or Install (Strictly Zero Pip Dependencies)
SmartDrive-OS requires Python 3.9+ and relies **exclusively on the Python Standard Library**:
```bash
python -m smart_drive --help
```

Or install in editable / developer mode:
```bash
pip install -e .
```

### Step 2: 1-Touch Initialization
Initialize standard taxonomy directories, install anti-indexing shields, generate AI manifests, and build the initial FTS5 search index with a single command or double-click:

- **Windows**: Double-click `Setup_SSD.bat`
- **macOS / Linux**: Double-click `Setup_SSD.command`
- **CLI**:
  ```bash
  python -m smart_drive init --profile ai-developer
  ```

#### Preset Profiles:
1. `ai-developer`: Checkpoints, GGUF/safetensors trees, `.noindex` developer environments, and agent workspaces.
2. `data-science`: EDA notebooks, pipelines, parquet vs small-CSV storage guidelines, and raw data archives.
3. `general-workspace` (Default): Universal active/archive code, docs, learning notes, and utilities.

### Step 3: Launch Visual Dashboard or Connect AI Agents
Start the local Web Dashboard in your browser:
```bash
smart-drive ui
```

Or auto-configure SmartDrive-OS as an MCP Server for your AI coding assistant:
```bash
python -m smart_drive mcp-config
```
Supports **Google Antigravity 2.0**, **Claude Desktop / Claude Code**, **Cursor IDE**, and **Windsurf**.

---

## 1-Touch Cross-Platform Launchers

All launchers reside both at the workspace root and inside `launchers/` for instant double-click execution:

| Windows (`.bat`) | macOS / Linux (`.command`) | Function |
|---|---|---|
| `Setup_SSD.bat` | `Setup_SSD.command` | Interactive 1-touch drive initialization and profile selector |
| `Quick_Search.bat` | `Quick_Search.command` | Interactive instant SQLite FTS5 search |
| `Quick_Clean.bat` | `Quick_Clean.command` | Safe dry-run junk preview followed by confirmed Tier 1 purge |
| `Quick_Audit.bat` | `Quick_Audit.command` | Instant storage breakdown and 512KB cluster slack audit |

---

## CLI Command Reference

Execute via `smart-drive <command>` or `python -m smart_drive <command>`:

| Command | Arguments | Description |
|---|---|---|
| `ui` | `--port <n>`, `--no-browser`, `--root <path>`, `--db <path>` | Launches the zero-dependency Web Dashboard & interactive visual UI. |
| `snapshot create` | `[name]`, `--partitions <list>`, `--root <path>`, `--json` | Generates a point-in-time manifest with 64KB-chunk streaming SHA-256 hashes. |
| `snapshot list` | `--root <path>`, `--json` | Lists all recorded point-in-time snapshots with sizes and file counts. |
| `snapshot verify` | `<name>`, `--no-untracked`, `--root <path>`, `--json` | Validates data integrity of files against snapshot manifest to detect tampering or corruption. |
| `backup` | `--target <path>`, `--dry-run`, `--no-skip-junk`, `--hash`, `--partitions <list>`, `--json` | Performs safe incremental backup copying only modified/new files to target directory. |
| `classify` | `[dir]`, `--suggest`, `--dry-run`, `--apply`, `--no-recursive`, `--json` | Deep content inspection (magic bytes & markers) for AI models, datasets, docs, and code repos. |
| `init` | `--profile {ai-developer, data-science, general-workspace}`, `--root <path>`, `--force`, `--json` | 1-touch drive setup, taxonomy creation, anti-indexing shield installation, and FTS5 DB seeding. |
| `status` | `--root <path>`, `--json` | Inspect SSD mount point, geometry, shield health, and taxonomy status. |
| `audit` | `--root <path>`, `--json`, `--markdown`, `--export <file>` | Detailed storage breakdown and 512KB cluster slack metrics. |
| `clean` | `--dry-run` *(default)*, `--apply`, `--tier {1,2,3}`, `--log`, `--json` | Safe junk cleaner with mandatory dry-run safeguard and inviolable whitelist protection. |
| `search` | `<query>`, `--ext <ext>`, `--size <spec>`, `--category <cat>`, `--dir <dir>`, `--limit <n>`, `--json`, `--csv` | Sub-10ms SQLite FTS5 multi-criteria query parser with BM25 ranking. |
| `organize` | `--dry-run`, `--apply`, `--clean`, `--json` | Autonomous drive auto-zoning, loose-file relocation, and anti-slack rebalancing. |
| `sentinel` | `--root <path>`, `--auto-heal`, `--no-heal`, `--json` | 1-touch health audit, git repo status, and shield self-healing (alias: `agent-check`). |
| `mcp` | `--root <path>` | Starts the zero-dependency JSON-RPC 2.0 stdio Model Context Protocol (MCP) server. |
| `mcp-config`| `--target-dir <dir>`, `--antigravity`, `--claude`, `--cursor`, `--windsurf`, `--json` | Auto-registers SmartDrive MCP Server in standard IDE config files. |
| `dup` | `--root <path>`, `--json` | 3-phase SHA-256 duplicate candidate detector with cluster slack reclamation preview. |
| `index` | `--root <path>`, `--db <path>`, `--batch <n>` | Full SQLite FTS5 index creation (>15,000 files/sec). |
| `update` | `--root <path>`, `--db <path>`, `--json` | Fast O(1) incremental search index synchronization (<2s). |

---

## Search Query Syntax

The FTS5 search engine supports intuitive structured syntax:

- Free text: `smart-drive search "transformer attention"`
- File extension: `smart-drive search "weights ext:gguf"`
- Size filter: `smart-drive search "dataset size:>100MB"` (or `size:<1MB`)
- Taxonomy category: `smart-drive search "llama cat:ai_models"`
- Directory constraint: `smart-drive search "README dir:03_Development_Projects"`
- Compound query: `smart-drive search "resnet ext:safetensors size:>50MB"`

---

## Safe Junk Cleaner Tiers

1. **Tier 1 (Safe OS Metadata Junk)**:
   - macOS `.DS_Store`, `._*` AppleDouble resource forks.
   - Windows `Thumbs.db`, `desktop.ini`, `ehthumbs.db`.
   - `.Spotlight-V100`, `.Trashes`.
2. **Tier 2 (Developer Build & Caches)**:
   - Python `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`.
   - Node `node_modules/.cache`.
   - Rust `target/debug/build`.
3. **Tier 3 (Transient & Logs)**:
   - `*.log`, `*.tmp`, `*.bak`, crash dumps.

> **Inviolable Whitelist**: The root manifests (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`), setup scripts (`Setup_*.bat`, `Setup_*.command`), and the 6 primary taxonomy directories are **permanently whitelisted** and can never be modified or deleted by the cleaner.

---

## Model Context Protocol (MCP) Server

SmartDrive-OS includes an MCP stdio server conforming to JSON-RPC 2.0. It exposes 8 specialized tools to AI Agents:

1. `ssd_search`: Instant FTS5 search returning compact tokens (<2,000 tokens per query).
2. `ssd_audit`: Storage breakdown and cluster slack waste analytics.
3. `ssd_clean`: Whitelist-protected safe junk cleaner with dry-run support.
4. `ssd_find_duplicates`: 3-phase SHA-256 duplicate file detection.
5. `ssd_update_index`: Fast incremental index synchronization (<2s).
6. `ssd_check_safety`: exFAT compatibility validator (audits 9 Win32 forbidden chars, 22 DOS stems, and symlinks).
7. `ssd_status`: SSD mount root status, shield health, and taxonomy status.
8. `ssd_auto_organize`: Autonomous drive auto-zoning and anti-slack relocation.

### IDE Integration

Pre-packaged configuration templates are available in `configs/`:
- `configs/.mcp.json` — Workspace root
- `configs/mcp_config.json` — Google Antigravity
- `configs/claude_desktop_config.json` — Claude Desktop
- `configs/cursor_mcp.json` — Cursor IDE
- `configs/windsurf_mcp.json` — Windsurf

Or configure automatically with:
```bash
smart-drive mcp-config
```

---

## Verification & Testing

SmartDrive-OS is tested across 314 automated unit, integration, and adversarial test cases using **100% pure standard library `unittest`**:

```bash
python -m unittest discover tests
```

Output:
```text
Ran 314 tests in ~37.9s
OK
```

---

## Contributing

SmartDrive-OS is an open-source project and welcomes all community contributions!
- 🌟 **Star & Fork** the repository on GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)
- 🐛 Report bugs or suggest new features via [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues)
- 🔀 Submit improvements via [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls)
- 📖 Read our full guidelines in [CONTRIBUTING.md](CONTRIBUTING.md)

---

## License

This project is open-source software licensed under the permissive **MIT License** — see the [LICENSE](LICENSE) file for details.  
You are free to use, modify, distribute, and integrate it into personal and commercial projects without restrictions.
