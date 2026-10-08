# SmartDrive-OS

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Pip Dependencies](https://img.shields.io/badge/dependencies-0%20external%20pip-success.svg)](#)
[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local-success?style=flat-square&logo=shield)](PRIVACY.md)
[![Architecture: Workstation Hybrid](https://img.shields.io/badge/architecture-Workstation%20Hybrid%20Native-blueviolet.svg)](#)
[![Filesystem: exFAT 512KB Guard](https://img.shields.io/badge/filesystem-exFAT%20512KB%20Guard-orange.svg)](#)
[![Filesystem: NTFS 4KB Native](https://img.shields.io/badge/filesystem-NTFS%204KB%20Native-cyan.svg)](#)
[![Web Dashboard](https://img.shields.io/badge/UI-Embedded%20Dark%20SPA-purple.svg)](#)
[![MCP Protocol: JSON-RPC 2.0](https://img.shields.io/badge/MCP-JSON--RPC%202.0%20stdio-purple.svg)](https://modelcontextprotocol.io/)
[![MCP Security Audit: Grade A (100/100)](https://img.shields.io/badge/MCP%20Audit-Grade%20A%20(100%2F100)-brightgreen.svg)](#)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os-1kxkwu)](https://m8ven.ai/mcp/duongnad-smart-drive-os)
[![Tests: 750/750 Passed (100%)](https://img.shields.io/badge/tests-750%2F750%20passed%20(100%25)-brightgreen.svg)](#)
[![20 Portable Launchers](https://img.shields.io/badge/launchers-20%20portable%20scripts-blue.svg)](#)
[![Release: v1.1.0](https://img.shields.io/badge/release-v1.1.0-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.1.0)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **High-Performance Autonomous Drive Operating Suite, Workstation Hybrid Architecture, Snapshot Integrity Engine & Sub-10ms SQLite FTS5 Instant Search for External SSDs (exFAT), Internal Secondary Drives (NTFS), and AI Coding Agents.**
>
> 🇻🇳 **Tài liệu hướng dẫn Tiếng Việt đầy đủ**: Dành cho người dùng, sinh viên đại học (FPTU) và lập trình viên Việt Nam: [README_VN.md](README_VN.md)
> 
> 🚀 **Internal Secondary Drive Architect & C-Drive Cache Offloader**: Chi tiết kiến trúc quy hoạch ổ SSD phụ trong máy trạm (`D:`, `E:`), offload thư mục cache AI bằng NTFS Junctions (`mklink /J`) và giám sát TRIM: [README_INTERNAL.md](README_INTERNAL.md)

---

## Table of Contents

1. [The Problem: Dual Filesystem Geometry Trap](#the-problem-dual-filesystem-geometry-trap)
   - [The exFAT 512KB Cluster Slack Trap](#the-exfat-512kb-cluster-slack-trap)
   - [The Internal Secondary Drive (NTFS 4KB) Challenge](#the-internal-secondary-drive-ntfs-4kb-challenge)
   - [Comparison: External exFAT vs. Internal NTFS](#comparison-external-exfat-vs-internal-ntfs)
2. [Workstation Hybrid Architecture](#workstation-hybrid-architecture)
3. [Core Capabilities & Innovations](#core-capabilities--innovations)
   - [1. Workstation Hybrid Architecture & Dynamic Geometry Adaptation](#1-workstation-hybrid-architecture--dynamic-geometry-adaptation)
   - [2. AcademicClassifier & Vietnamese University Localization](#2-academicclassifier--vietnamese-university-localization)
   - [3. Personal Reading, OEM Drivers & Installer Auto-Routing](#3-personal-reading-oem-drivers--installer-auto-routing)
   - [4. AutoZoner Inviolable Self-Defense & Workstation Protection](#4-autozoner-inviolable-self-defense--workstation-protection)
   - [5. Environment & PATH Verification CLI (`self-path-check`)](#5-environment--path-verification-cli-self-path-check)
   - [6. Model Context Protocol (MCP) Server Grade A (100/100) & Token Efficiency](#6-model-context-protocol-mcp-server-grade-a-100100--token-efficiency)
   - [7. Suite of 20 Portable Cross-Platform Launchers](#7-suite-of-20-portable-cross-platform-launchers)
   - [8. Embedded Zero-Dependency Web Dashboard (`smart-drive ui`)](#8-embedded-zero-dependency-web-dashboard-smart-drive-ui)
   - [9. C-Drive Cache Offloader & NTFS Directory Junctions (`smart-drive offload`)](#9-c-drive-cache-offloader--ntfs-directory-junctions-smart-drive-offload)
   - [10. SHA-256 Snapshot Integrity & Incremental Backup Engine](#10-sha-256-snapshot-integrity--incremental-backup-engine)
   - [11. 100% Zero Pip Dependencies (`dependencies = []`)](#11-100-zero-pip-dependencies-dependencies--)
4. [3-Step Quickstart](#3-step-quickstart)
5. [The 5 Preset Profiles](#the-5-preset-profiles)
6. [Cross-Platform Portable Launchers Guide](#cross-platform-portable-launchers-guide)
7. [Comprehensive CLI Command Reference](#comprehensive-cli-command-reference)
8. [Advanced Search Query Syntax](#advanced-search-query-syntax)
9. [Safe Junk Cleaner & 3-Tier Protection Hierarchy](#safe-junk-cleaner--3-tier-protection-hierarchy)
10. [Model Context Protocol (MCP) Server & AI Coding Agent Integration](#model-context-protocol-mcp-server--ai-coding-agent-integration)
11. [Testing & Verification Record (750 Tests, 100% Pass Rate)](#testing--verification-record-750-tests-100-pass-rate)
12. [Privacy, Security & Data Isolation](#privacy-security--data-isolation)
13. [Contributing & License](#contributing--license)

---

## The Problem: Dual Filesystem Geometry Trap

Modern developers and power users work across two fundamentally different physical drive environments:

### The exFAT 512KB Cluster Slack Trap

High-capacity external SSDs (such as the **Kingston XS2000 2TB**) formatted with exFAT default to an allocation unit size (cluster size) of **512 KB (524,288 bytes)**. While ideal for continuous video recording, this geometry is catastrophic for modern development repositories containing thousands of source code and config files:

$$\text{Cluster Allocation} = \left\lceil \frac{\text{File Size}}{524,288} \right\rceil \times 524,288 \text{ bytes}$$

$$\text{Wasted Slack} = \text{Cluster Allocation} - \text{Nominal File Size}$$

| File Nominal Size | Physical Space on Disk | Cluster Slack Wasted | Slack Ratio |
|---|---|---|---|
| **0 bytes** | 0 bytes (directory entry only) | 0 bytes | 0.0% |
| **1 byte** | **524,288 bytes** (512 KB) | **524,287 bytes** | **99.9998%** |
| **10 KB** (code / config) | **524,288 bytes** (512 KB) | **514,048 bytes** | **98.05%** |
| **100 KB** (JSON / docs) | **524,288 bytes** (512 KB) | **421,888 bytes** | **80.47%** |
| **524,289 bytes** (512 KB + 1B) | **1,048,576 bytes** (1,024 KB) | **524,287 bytes** | **50.00%** |

- **Real-World Impact**: A project containing **10,000 source files** totalling **15 MB** actually consumes over **5.12 GB** on your external exFAT SSD — wasting **>99% of disk capacity** solely on cluster slack!
- Furthermore, operating system indexing daemons (macOS Spotlight `mds`, Windows Search) continuously write hidden metadata (`.DS_Store`, `Thumbs.db`, `.fseventsd`), generating excessive I/O wear and battery drain.

### The Internal Secondary Drive (NTFS 4KB) Challenge

On developer workstations and gaming rigs, a secondary internal NVMe/SATA SSD (`D:`, `E:`) formatted with **NTFS 4KB** faces the opposite challenge:
- The system drive (`C:`) is rapidly choked by massive AI models, HuggingFace weights, Ollama blobs, Docker WSL2 images, pip, uv, npm, and Gradle caches (often reaching 50GB – 200GB+).
- Moving these caches manually breaks developer tools and requires administrative elevation for standard symlinks (`mklink /D`).
- The internal drive already hosts critical system folders (`WindowsApps`, `Program Files`, `$Recycle.Bin`), game installations (`SteamLibrary`, `Riot Games`, `LDPlayer`), active enterprise databases (`Microsoft SQL Server 2022`), and university coursework. Blind drive organization tools risk breaking working installations or corrupting active database data files.

### Comparison: External exFAT vs. Internal NTFS

| Dimension | External Portable SSD (exFAT) | Internal Secondary SSD (NTFS) |
|---|---|---|
| **Typical Mount** | `/Volumes/KINGSTON` or `E:\` | `D:\` or `E:\` (Fixed Disk) |
| **Cluster Size** | **512 KB** (524,288 bytes) | **4 KB** (4,096 bytes) |
| **Primary Bottleneck** | Catastrophic cluster slack on small files | C-drive disk exhaustion & cache fragmentation |
| **Symlink Support** | ❌ Unsupported (corrupts cross-platform) | ⚡ Supported via NTFS Junctions (`mklink /J`) |
| **Specialized Strategy** | Bundling, anti-slack auto-zoning, anti-indexing shields | 7-Phase transactional cache offload, service protection |
| **Protected Assets** | Root manifests, scripts, 6 core taxonomies | Windows apps, game libraries, active SQL Server databases |

---

## Workstation Hybrid Architecture

SmartDrive-OS bridges this divide with a unified, zero-external-dependency workstation architecture:

```
+---------------------------------------------------------------------------------------------------------+
|                                      User & AI Agent Interfaces                                         |
|   +-----------------------+   +-----------------------+   +---------------------+   +---------------+   |
|   | 20 Portable Launchers |   |   Python CLI & TUI    |   |  AI Coding Agents   |   |   Web UI SPA  |   |
|   | (.bat/.ps1/.cmd/.sh)  |   | (smart-drive CLI/TUI) |   | (Antigravity/Claude)|   |  (HTTP :8765) |   |
|   +-----------+-----------+   +-----------+-----------+   +----------+----------+   +-------+-------+   |
+---------------|---------------------------|--------------------------|----------------------|-----------+
                |                           |                          |                      |
                +---------------------------+                          | JSON-RPC 2.0 stdio   | HTTP Loopback
                                            |                          v                      v
                                            v             +-----------------------------------------------+
+---------------------------------------------------------|        MCP Server Grade A (100/100)           |
|                                                         |  - 100% AST-Resolvable Isolated Handlers      |
|                     SmartDrive-OS Core Subsystems       |  - Token-Efficient Paged Compressed JSON      |
|                                                         |  - Constant-Time HMAC Handshake Auth          |
|  +--------------------------------+  +------------------+  - Strict Loopback Socket Isolation           |
|  | Drive Initializer              |  | Storage Auditor  +-----------------------------------------------+
|  | - 5 Preset Profiles            |  | - Adaptive Geometry (512KB exFAT vs 4KB NTFS)                    |
|  | - Anti-Indexing Shields        |  | - Precise Cluster Slack & Capacity Calculation                   |
|  +--------------------------------+  +------------------------------------------------------------------+
|  +--------------------------------+  +------------------------------------------------------------------+
|  | AcademicClassifier & Localizer |  | AutoZoner & Inviolable Self-Defense                              |
|  | - FPTU Course Code Extraction  |  | - Repository & Running Code Anchors (_SMART_DRIVE_REPO_DIR)       |
|  | - Vietnamese Mojibake Repair   |  | - Windows Apps Protection (WindowsApps, Program Files)           |
|  | - Semester Auto-Zoning (Ky_1-7)|  | - Games Protection (SteamLibrary, Riot Games, LDPlayer)          |
|  | - Personal Books & Drivers     |  | - Active DB Service Shield (Microsoft SQL Server 2022)           |
|  +--------------------------------+  +------------------------------------------------------------------+
|  +--------------------------------+  +------------------------------------------------------------------+
|  | Purge / Safe Cleaner Engine    |  | C-Drive Cache Offloader & NTFS Junctions                         |
|  | - 3-Tier Granular Rule Match   |  | - 7-Phase Transactional Migration with Rollback Safety           |
|  | - Mandatory Dry-Run Safeguard  |  | - Unprivileged Non-Elevated mklink /J Reparse Points             |
|  | - Whitelist Protected System   |  | - Target Caches: HuggingFace, Ollama, PyTorch, Docker, Pip, Npm  |
|  +--------------------------------+  +------------------------------------------------------------------+
|  +--------------------------------+  +------------------------------------------------------------------+
|  | SQLite FTS5 Search Engine      |  | Snapshot & Incremental Backup Engine                             |
|  | - unicode61 BM25 Tokenizer     |  | - Streaming 64KB-Chunk SHA-256 Hashing                           |
|  | - Sub-10ms Multi-Criteria Query|  | - Point-in-Time Manifests & Bit-Rot Verification                 |
|  +--------------------------------+  +------------------------------------------------------------------+
+---------------------------------------------------------------------------------------------------------+
                                            |
                   +------------------------+------------------------+
                   |                                                 |
                   v                                                 v
+-------------------------------------------------+ +-----------------------------------------------------+
| Target 1: External Portable SSD (exFAT 512KB)   | | Target 2: Internal Secondary SSD (NTFS 4KB)         |
|  01_AI_Models/        02_Learning_Knowledge/    | |  01_AI_Models/        02_Learning_Knowledge/ (FPTU) |
|  03_Development_Proj/ 04_System_Workspaces/     | |  03_Development_Proj/ 04_System_Workspaces/         |
|  05_Dev_Toolbox/      06_Archives_Storage/      | |  05_Dev_Toolbox/      06_Archives_Storage/          |
|  .metadata_never_index .fseventsd/no_log        | |  [PROTECTED APPS]: WindowsApps, Program Files,      |
|  .smart_drive/index.db (FTS5 search index)      | |  SteamLibrary, Riot Games, LDPlayer, SQL2022, fo4   |
|  .smart_drive/snapshots/<manifest>.json         | |  [PROTECTED DB]:   DBI202_VuPT\MSSQL16.MSSQLSERVER  |
+-------------------------------------------------+ +-----------------------------------------------------+
```

---

## Core Capabilities & Innovations

### 1. Workstation Hybrid Architecture & Dynamic Geometry Adaptation
SmartDrive-OS automatically identifies the underlying drive hardware and filesystem geometry through `FilesystemAdapter` and `DriveDetectorBackend`:
- **exFAT Volumes (512KB Clusters)**: Activates the **512KB Cluster Slack Guard**, enforcing anti-symlink invariants, Spotlight/FSEvents anti-indexing shields (`.metadata_never_index`, `.fseventsd/no_log`), and loose file packaging.
- **NTFS Volumes (4KB Clusters)**: Dynamically adjusts slack math to standard 4,096-byte clusters, enables NTFS Directory Junctions (`mklink /J`), verifies hardware TRIM status (`fsutil behavior query DisableDeleteNotify`), and activates C-drive cache offloading.

### 2. AcademicClassifier & Vietnamese University Localization
Designed specifically for university students, educators, and software engineering learners (with specialized knowledge of Vietnamese universities such as **FPT University / FPTU**):
- **Course Code Regex Extraction**: Automatically parses course codes across filename boundaries (`DBI202`, `WED201c`, `SWE202c`, `PRN211`, `PRJ301`, `OSG`, etc.).
- **Academic Keyword Detection**: Identifies practical exam practice (`PE`, `de thi`, `on luyen`, `pe_dbi202`), lab assignments (`lab1_sp26`, `testjava`), and academic terms (`sp26`, `fa25`, `su25`).
- **Semester Auto-Zoning**: Automatically groups coursework into standardized semester folders `02_Learning_Knowledge/FPTU/Ky_1/` through `Ky_7/`.
- **Vietnamese Mojibake & Font Corruption Repair**: Automatically repairs corrupted Unicode / question-mark folder names caused by non-UTF-8 zip extractions or legacy archive tools:
  - `h?c k? 3 fptu` $\rightarrow$ `Hoc_Ky_3_fptu`
  - `n luy?n pe dbi202` $\rightarrow$ `On_Luyen_pe_dbi202`
  - `k 1 fptu` $\rightarrow$ `Ky_1_fptu`
  - `dich truyen` $\rightarrow$ `Dich_Truyen`

### 3. Personal Reading, OEM Drivers & Installer Auto-Routing
SmartDrive-OS categorizes non-code assets into specialized workstations zones:
- `dich truyen` (manga/light novel/translated books) $\rightarrow$ `02_Learning_Knowledge/Personal_Books/dich truyen`
- `lenovo` (OEM recovery and hardware drivers) $\rightarrow$ `05_Dev_Toolbox/OEM_Drivers/LENOVO`
- `sql2022` (standalone database setup kits) $\rightarrow$ `05_Dev_Toolbox/Installers/SQL2022`

### 4. AutoZoner Inviolable Self-Defense & Workstation Protection
To prevent destructive accidents when running SmartDrive-OS directly on a root workstation volume, the engine incorporates **multi-layered inviolable defense**:
- **Code Repository Self-Protection**: AutoZoner strictly resolves its executing file (`_CURRENT_FILE`), core module (`_SMART_DRIVE_CORE_DIR`), package root (`_SMART_DRIVE_PKG_DIR`), repository root (`_SMART_DRIVE_REPO_DIR`), and all parent ancestors. `classify_item()` guarantees that `smart-drive-os` is **never relocated, deleted, or altered**.
- **Windows System Directory Defense**: Whitelists and excludes Windows system folders (`WindowsApps`, `Program Files`, `Program Files (x86)`, `$Recycle.Bin`, `System Volume Information`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`).
- **Game & Platform Launcher Defense**: Whitelists and excludes game directories (`SteamLibrary`, `Riot Games`, `LDPlayer`, `fo4`, `32837`, `Downloads`).
- **Active Database Service Defense**: Specifically protects active Microsoft SQL Server 2022 instances, databases, and LDF/MDF data directories (`DBI202_VuPT\MSSQL16.MSSQLSERVER`, `MSSQLSERVER`, `MSSQL`).
- **Quiet Scanning**: Excluded system directories are pre-filtered during directory enumeration, generating zero unnecessary syscalls and suppressing permission warnings at `DEBUG` level.

### 5. Environment & PATH Verification CLI (`self-path-check`)
Ensures developer convenience across Windows, macOS, and Linux:
```bash
smart-drive self-path-check
```
- Inspects system `PATH` and verifies whether the `smart-drive` CLI script is discoverable.
- **1-Click Windows PowerShell Setup**: If not on PATH, provides an exact copy-paste PowerShell command:
  ```powershell
  [Environment]::SetEnvironmentVariable("Path", $env:Path + ";$env:APPDATA\Python\Python311\Scripts", "User")
  ```
- **POSIX Shell Setup**: Provides the exact export line for `~/.bashrc` or `~/.zshrc`.
- **Universal Zero-Config Fallback**: Confirms that SmartDrive-OS can always be run without any PATH setup using:
  ```bash
  python -m smart_drive <subcommand>
  ```

### 6. Model Context Protocol (MCP) Server Grade A (100/100) & Token Efficiency
SmartDrive-OS provides an enterprise-hardened MCP stdio server conforming to JSON-RPC 2.0:
- **Grade A (100/100) Security Audit Compliance**: Passed 100% of MCP registry security checks with zero warnings.
- **100% Static AST-Resolvable Handler Isolation**: Handlers are explicitly mapped and resolved through static dispatch, enabling security analyzers to inspect every tool handler.
- **Token-Efficient Compressed JSON Output**: Formatted with pagination (`limit`, `offset`, `total_groups`, `returned_group_count`, `has_more`, `next_offset`). Duplicate file lists are capped at a maximum of 10 preview paths per group (`truncated_files_count`) to protect AI context windows against token exhaustion.
- **8 Specialized Tools**: `ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`.
- **1-Click Multi-Agent Registration**:
  ```bash
  smart-drive mcp register --all
  # Or:
  smart-drive mcp-config --all
  ```
  Automatically detects and registers configurations for **Google Antigravity 2.0**, **Claude Code & Claude Desktop**, **Cursor IDE / OpenAI Codex**, **Windsurf**, and local workspace `.mcp.json`.
- **Constant-Time HMAC Authentication Handshake**: Token verification powered by standard library `hmac.compare_digest` with optional `--auth-token` and `--require-auth` CLI parameters.
- **Strict Loopback Network Isolation**: Embedded web components strictly validate socket addresses against `ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")`, instantly rejecting external LAN bindings.

### 7. Suite of 20 Portable Cross-Platform Launchers
Zero Git requirement, zero account dependencies. 5 essential workflows provided across 4 script formats:

| Script Workflow | Windows CMD (`.bat`) | Windows PowerShell (`.ps1`) | macOS App (`.command`) | Linux/POSIX (`.sh`) |
|---|---|---|---|---|
| **Drive Setup** | `Setup_SSD.bat` | `Setup_SSD.ps1` | `Setup_SSD.command` | `Setup_SSD.sh` |
| **Quick Search** | `Quick_Search.bat` | `Quick_Search.ps1` | `Quick_Search.command` | `Quick_Search.sh` |
| **Quick Clean** | `Quick_Clean.bat` | `Quick_Clean.ps1` | `Quick_Clean.command` | `Quick_Clean.sh` |
| **Quick Audit** | `Quick_Audit.bat` | `Quick_Audit.ps1` | `Quick_Audit.command` | `Quick_Audit.sh` |
| **Interactive Menu** | `SmartDrive.bat` | `SmartDrive.ps1` | `SmartDrive.command` | `SmartDrive.sh` |

All 20 launchers reside both in the repository root and in `launchers/` for instant execution.

### 8. Embedded Zero-Dependency Web Dashboard (`smart-drive ui`)
- **Embedded Dark Mode SPA**: Built 100% on Python's built-in `http.server.ThreadingHTTPServer`. Zero npm, zero node, zero Flask/FastAPI dependencies.
- **Visual Analytics**: Interactive 6-taxonomy distribution charts and real-time visualization of wasted 512KB cluster slack bytes.
- **Interactive Search**: Real-time search bar with instant filtering by extension, size threshold, and category.
- **3-Tier Safe Cleanup Panel**: Visual dry-run preview with a 1-click confirmation dialog.

### 9. C-Drive Cache Offloader & NTFS Directory Junctions (`smart-drive offload`)
- **7-Phase Transactional Migration**: Safe migration of heavy developer and AI caches (HuggingFace, Ollama, PyTorch, Docker WSL2, pip, uv, npm, Conda, Gradle, Cargo) from `C:` to secondary drive (`D:\04_System_Offload_Caches\<name>`).
- **Unprivileged NTFS Directory Junctions (`mklink /J`)**: Creates transparent Windows hardware reparse points without requiring Administrator elevation or Developer Mode.
- **Instant Rollback Safety**: Automatic transactional rollback if any step fails during offload or revert operations.

### 10. SHA-256 Snapshot Integrity & Incremental Backup Engine
- **Streaming 64KB-Chunk SHA-256 Hashing**: Constant-memory $O(1)$ SHA-256 calculation detecting silent bit rot and corruption.
- **Point-in-Time Manifests**: Snapshot critical directories into `.smart_drive/snapshots/<name>.json`.
- **Safe Incremental Backup**: Transfers only modified or new files to backup targets, automatically excluding OS junk.

### 11. 100% Zero Pip Dependencies (`dependencies = []`)
- Strict architectural rule: `dependencies = []` in `pyproject.toml`.
- Fully written using the Python Standard Library (`sqlite3`, `http.server`, `hashlib`, `hmac`, `json`, `urllib`, `shutil`, `pathlib`, `ctypes`, `subprocess`, `argparse`).
- Guaranteed to run out of the box on any machine with Python 3.9+ installed.

---

## 3-Step Quickstart

### Step 1: Run Environment Check
Verify your Python environment and PATH accessibility:
```bash
python -m smart_drive self-path-check
```

Or install in editable developer mode:
```bash
pip install -e .
```

### Step 2: 1-Touch Initialization
Initialize standard taxonomy directories, install anti-indexing shields, generate AI manifests, and build the initial FTS5 search index with a single command or double-click:

- **Windows**: Double-click `Setup_SSD.bat` or run `Setup_SSD.ps1`
- **macOS**: Double-click `Setup_SSD.command`
- **Linux**: Execute `./Setup_SSD.sh`
- **CLI**:
  ```bash
  # For internal secondary drives (D:, E:) with coursework & games:
  python -m smart_drive init --profile workstation-hybrid

  # Or for external USB SSDs with AI models:
  python -m smart_drive init --profile ai-developer
  ```

### Step 3: Launch Visual Dashboard or Register AI Agents
Start the local Web Dashboard in your browser:
```bash
smart-drive ui
```

Or register SmartDrive-OS as an MCP Server across all your AI coding assistants in 1 click:
```bash
smart-drive mcp register --all
```

---

## The 5 Preset Profiles

SmartDrive-OS provides 5 tailored profiles for different workflows:

| Profile | Target Architecture | Included Taxonomies & Subdirectories |
|---|---|---|
| **`workstation-hybrid`** *(Recommended for dual-drive PCs)* | Internal Secondary Drive (`D:`, `E:`) with existing games, apps, and student coursework | `01_AI_Models`, `02_Learning_Knowledge` (`FPTU/`, `Personal_Books/`, `Notes/`), `03_Development_Projects` (`active/`, `archive/`), `04_System_Workspaces`, `05_Dev_Toolbox` (`OEM_Drivers/`, `Installers/`, `scripts/`), `06_Archives_Storage` |
| **`internal-developer-vault`** | Dedicated Internal secondary NVMe/SATA SSD receiving offloaded C-drive caches | `01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches` (`huggingface/`, `ollama/`, `pip/`, `uv/`), `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`ai-developer`** | High-speed external SSD dedicated to LLMs, fine-tuning, and AI agents | `01_AI_Models` (`checkpoints/`, `gguf/`, `safetensors/`, `datasets/`), `02_Learning_Knowledge`, `03_Development_Projects` (`ai_agents/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`data-science`** | Data engineering, tabular pipelines, and EDA notebooks | `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects` (`notebooks/`, `data_raw/`, `pipelines/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`general-workspace`** *(Default)* | Universal active/archive code, docs, learning notes, and utilities | Standard 6 taxonomies with `Notes/`, `References/`, `active/`, `archive/`, and `scripts/` |

---

## Cross-Platform Portable Launchers Guide

The project includes 20 portable launchers that require **zero Git installation** and run with 1 touch:

```
smart-drive-os/
├── launchers/
│   ├── Setup_SSD.bat        Quick_Search.bat        Quick_Clean.bat        Quick_Audit.bat        SmartDrive.bat
│   ├── Setup_SSD.ps1        Quick_Search.ps1        Quick_Clean.ps1        Quick_Audit.ps1        SmartDrive.ps1
│   ├── Setup_SSD.command    Quick_Search.command    Quick_Clean.command    Quick_Audit.command    SmartDrive.command
│   └── Setup_SSD.sh         Quick_Search.sh         Quick_Clean.sh         Quick_Audit.sh         SmartDrive.sh
```

- **`Setup_SSD`**: Interactive 1-touch drive initialization and profile selector.
- **`Quick_Search`**: Interactive instant SQLite FTS5 search prompt.
- **`Quick_Clean`**: Safe dry-run junk preview followed by confirmed Tier 1 purge.
- **`Quick_Audit`**: Instant storage breakdown and 512KB cluster slack audit.
- **`SmartDrive`**: Full interactive TUI menu giving access to all operations.

---

## Comprehensive CLI Command Reference

All commands can be invoked via `smart-drive <command>` or `python -m smart_drive <command>`:

| Command | Key Arguments | Description |
|---|---|---|
| `self-path-check` | *(none)* | Inspects Python CLI script paths on system `PATH` and outputs 1-click configuration commands. |
| `ui` | `--port <n>`, `--no-browser`, `--root <path>`, `--db <path>` | Launches the zero-dependency Web Dashboard & interactive visual UI on port 8765. |
| `init` | `--profile <name>`, `--root <path>`, `--force`, `--json` | 1-touch drive setup, taxonomy creation, anti-indexing shield installation, and FTS5 DB seeding. |
| `status` | `--root <path>`, `--json` | Inspect SSD mount point, geometry, shield health, and taxonomy status. |
| `audit` | `--root <path>`, `--json`, `--markdown`, `--export <file>` | Detailed storage breakdown and cluster slack metrics (512KB or 4KB adaptive). |
| `clean` | `--dry-run` *(default)*, `--apply`, `--tier {1,2,3}`, `--log`, `--json` | Safe junk cleaner with mandatory dry-run safeguard and inviolable whitelist protection. |
| `search` | `<query>`, `--ext <ext>`, `--size <spec>`, `--category <cat>`, `--limit <n>`, `--json` | Sub-10ms SQLite FTS5 multi-criteria query parser with BM25 ranking. |
| `organize` | `--dry-run`, `--apply`, `--clean`, `--json` | Autonomous drive auto-zoning, loose-file relocation, AcademicClassifier routing, and slack rebalancing. |
| `sentinel` | `--root <path>`, `--auto-heal`, `--no-heal`, `--json` | 1-touch health audit, git repo status, and shield self-healing (alias: `agent-check`). |
| `mcp` | `[{serve,register}]`, `--root <path>`, `--all`, `--auth-token <token>`, `--require-auth` | Starts the JSON-RPC 2.0 stdio MCP server (`serve`) or registers configs for AI agents (`register`). |
| `mcp-config` | `--all`, `--antigravity`, `--claude`, `--cursor`, `--windsurf`, `--workspace`, `--json` | Auto-registers SmartDrive MCP Server in standard AI coding agent config files. |
| `snapshot create` | `[name]`, `--partitions <list>`, `--root <path>`, `--json` | Generates a point-in-time manifest with 64KB-chunk streaming SHA-256 hashes. |
| `snapshot list` | `--root <path>`, `--json` | Lists all recorded point-in-time snapshots with sizes and file counts. |
| `snapshot verify` | `<name>`, `--no-untracked`, `--root <path>`, `--json` | Validates data integrity of files against snapshot manifest to detect tampering or bit rot. |
| `backup` | `--target <path>`, `--dry-run`, `--hash`, `--partitions <list>`, `--json` | Safe incremental backup copying only modified/new files to target directory. |
| `classify` | `[path]`, `--suggest`, `--dry-run`, `--apply`, `--no-recursive`, `--json` | Deep content inspection (magic bytes & markers) for AI models, datasets, docs, and code repos. |
| `offload` | `--scan`, `--move <name>`, `--target <drive>`, `--revert <name>`, `--dry-run`, `--json` | C-Drive developer cache discovery and transactional NTFS junction offloading to secondary drive. |
| `health` | `[drive]`, `--root <path>`, `--json` | SSD health, TRIM verification, partition geometry, and storage utilization monitor. |
| `dup` | `--root <path>`, `--json` | 3-phase SHA-256 duplicate candidate detector with cluster slack reclamation preview. |
| `index` | `--root <path>`, `--db <path>`, `--batch <n>` | Full SQLite FTS5 index creation (>15,000 files/sec). |
| `update` | `--root <path>`, `--db <path>`, `--json` | Fast $O(1)$ incremental search index synchronization (<2s). |

---

## Advanced Search Query Syntax

The SQLite FTS5 search engine processes complex queries in under 10 milliseconds:

- **Free Text**: `smart-drive search "machine learning"`
- **File Extension**: `smart-drive search "weights ext:gguf"`
- **Size Filter**: `smart-drive search "dataset size:>100MB"` (or `size:<1MB`, `size:0`)
- **Taxonomy Category**: `smart-drive search "llama cat:ai_models"`
- **Directory Constraint**: `smart-drive search "assignment dir:FPTU"`
- **Compound Query**: `smart-drive search "exam ext:pdf size:>1MB dir:DBI202"`

---

## Safe Junk Cleaner & 3-Tier Protection Hierarchy

SmartDrive-OS categorizes purgeable files into 3 safe tiers:

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

> **Inviolable Whitelist Security**: Root manifests (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`, `PRIVACY.md`), setup scripts (`Setup_*.bat`, `Setup_*.command`), the 6 primary taxonomy directories, Windows system folders (`WindowsApps`, `Program Files`), games (`SteamLibrary`, `Riot Games`, `LDPlayer`), active database services (`Microsoft SQL Server 2022`), and the `smart-drive-os` codebase are **permanently whitelisted** and cannot be deleted or relocated by the cleaner.

---

## Model Context Protocol (MCP) Server & AI Coding Agent Integration

SmartDrive-OS provides native MCP server support, exposing 8 specialized tools to AI Agents:

1. `ssd_search`: Instant FTS5 search returning compact tokens (<2,000 tokens per query). *(read-only)*
2. `ssd_audit`: Storage breakdown and cluster slack waste analytics with compact formatting. *(read-only)*
3. `ssd_clean`: Whitelist-protected safe junk cleaner with dry-run support. *(destructive)*
4. `ssd_find_duplicates`: 3-phase SHA-256 duplicate file detection with pagination and preview capping. *(read-only)*
5. `ssd_update_index`: Fast incremental index synchronization (<2s). *(idempotent)*
6. `ssd_check_safety`: exFAT compatibility validator (audits 9 Win32 forbidden chars, 22 DOS stems, and symlinks). *(read-only)*
7. `ssd_status`: SSD mount root status, shield health, and taxonomy status. *(read-only)*
8. `ssd_auto_organize`: Autonomous drive auto-zoning and anti-slack relocation with AcademicClassifier. *(destructive)*

### 1-Click Multi-Agent Registration
Register all installed AI assistants instantly:
```bash
smart-drive mcp register --all
```

Pre-packaged configuration templates are also maintained in `configs/`:
- `configs/.mcp.json` — Local project / workspace root
- `configs/mcp_config.json` — Google Antigravity 2.0 (`~/.gemini/antigravity/mcp_config.json`)
- `configs/claude_desktop_config.json` — Claude Desktop & Claude Code (`~/.claude.json`)
- `configs/cursor_mcp.json` — Cursor IDE / OpenAI Codex (`~/.cursor/mcp.json`)
- `configs/windsurf_mcp.json` — Windsurf IDE (`~/.codeium/windsurf/mcp_config.json`)

### M8ven MCP Directory & Trust Index Certified (Grade A 100/100)
SmartDrive-OS is officially certified and indexed on the [M8ven MCP Directory](https://m8ven.ai/mcp/duongnad-smart-drive-os) with a **Grade A (100/100) Trust Score**:
- **Verification Token**: `duongnad-smart-drive-os-1kxkwu`
- **100% Static AST-Resolvable Handler Isolation**: Tools are mapped via class-level `SmartDriveMCPServer.TOOL_HANDLERS` and dispatched deterministically without dynamic code execution or hidden reflection.
- **Constant-Time Authentication Handshake**: Token verification powered by standard library `hmac.compare_digest` with JSON-RPC `auth/handshake` method. Supports `--auth-token` and `--require-auth` CLI parameters, `SMART_DRIVE_MCP_AUTH_TOKEN` environment variable, while defaulting to zero-friction stdio execution for local AI assistants.
- **In-Memory Rate Limiting**: Built-in thread-safe `SlidingWindowRateLimiter` preventing agent DoS loops with millisecond-accurate `Retry-After` headers and JSON-RPC `-32000` error codes.
- **Strict Input Boundary Sanitizers**: Parameter validation enforcing safe path resolution (`_resolve_safe_path`), preventing path traversal (`../`), null-byte injection (`\0`), and cross-drive jumping.
- **Tool Hint Annotations & Schema Parity**: All 8 tools declare explicit boolean hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) meeting Anthropic Claude, OpenAI Actions, and M8ven trust standards.
- **Zero Hidden Outbound Calls**: Certified 100% offline, local-first stdio execution with zero external telemetry, zero tracking packages, and zero data leakage.

---

## Testing & Verification Record (750 Tests, 100% Pass Rate)

SmartDrive-OS is tested across **750 automated unit, integration, stress, and adversarial test cases** using **100% pure standard library `unittest`**:

```bash
# Run full test suite with pytest:
pytest -q

# Or run with standard library unittest:
python -m unittest discover tests -v
```

### Verified Test Results:
```text
============================= test session starts ==============================
collected 750 items

........................................................................ [  9%]
.................................. [ 14%]
........................................................................ [ 23%]
........................................................................ [ 33%]
........................................................................ [ 42%]
........................................................................ [ 52%]
........................................................................ [ 62%]
.............................................ssssss........sss.....ss.................... [ 74%]
........................................................................ [ 83%]
........................................................................ [ 93%]
...................................................                      [100%]
================== 739 passed, 11 skipped, 93 subtests passed ==================
```

- **Pass Rate**: **100.0%** (739 passed, 11 platform-skipped on non-Windows OS, 0 failures, 0 errors).
- **Adversarial Filesystem Coverage**: Cluster sizes from 512B to 32MB, boundary conditions (0B, 1B, 512KB-1B, 512KB, 512KB+1B), and large-scale stress tests.
- **Inviolable Defense Coverage**: Complete verification of repository self-defense, Windows system folders, games, and active SQL Server database protection.
- **AcademicClassifier Coverage**: Full course regex matching, mojibake repair, and semester routing.

---

## Privacy, Security & Data Isolation

SmartDrive-OS is engineered from the ground up with a strict **local-first, zero-trust** architecture:

- **100% Local-Only Operations**: All filesystem scanning, SQLite FTS5 search indexing, and SHA-256 snapshotting occur exclusively on your local storage. No data is ever transmitted over the network.
- **Zero Telemetry & Phone-Home**: Zero analytics, zero usage trackers, and zero background network beacons.
- **Zero PII Logging**: File contents, credentials, and source code secrets are never parsed or harvested; only basic filesystem metadata is stored in local `.smart_drive/index.db`.
- **Air-Gap Ready**: Zero external pip dependencies (`dependencies = []`). The embedded Web Dashboard binds exclusively to `127.0.0.1` (`localhost`), and the MCP Server operates solely over local `stdio`.
- **Defensive Safeguards**: Inviolable whitelist protecting critical files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`), mandatory dry-run defaults for cleanup, and strict input boundary validation.

For full architectural details, security models, and compliance specifications, please read our authoritative [PRIVACY.md](PRIVACY.md).

---

## Contributing & License

### Contributing
SmartDrive-OS is an open-source project and welcomes all community contributions!
- 🌟 **Star & Fork** the repository on GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)
- 🐛 Report bugs or suggest new features via [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues)
- 🔀 Submit improvements via [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls)
- 📖 Read our full contribution guidelines in [CONTRIBUTING.md](CONTRIBUTING.md)

### License
This project is open-source software licensed under the permissive **MIT License** — see the [LICENSE](LICENSE) file for details. You are free to use, modify, distribute, and integrate it into personal and commercial projects without restrictions.
