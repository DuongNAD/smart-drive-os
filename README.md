<p align="center"><img src="docs/banner.jpg" alt="SmartDrive-OS — Saturn and its orderly rings in deep space" width="100%"></p>

# SmartDrive-OS

*Saturn's rings as orderly storage lanes: every file in its lane, across the whole drive.*

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![Zero Pip Dependencies](https://img.shields.io/badge/dependencies-0%20external%20pip-success.svg)](pyproject.toml)
[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local-success?style=flat-square&logo=shield)](PRIVACY.md)
[![CI](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os-1kxkwu)](https://m8ven.ai/mcp/duongnad-smart-drive-os)
[![Release: v1.3.1](https://img.shields.io/badge/release-v1.3.1-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.3.1)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

SmartDrive-OS is a zero-dependency drive operating suite and sub-10ms SQLite FTS5 search engine for external portable SSDs (exFAT 512 KB clusters) and internal secondary workstation drives (NTFS 4 KB clusters). It is built for developers, university students, and AI coding agents (Claude Code, Claude Desktop, Antigravity, Cursor, Codex, Windsurf) to organize drives without breaking agent paths, govern `.gitignore` rules, offload `C:` caches, and verify file integrity with SHA-256 snapshots. Everything runs 100% locally on the Python 3.9+ standard library (`dependencies = []`) with zero telemetry.

> 🇻🇳 **Tiếng Việt**: [README_VN.md](README_VN.md) · 🚀 **Internal NTFS Drive & C-Drive Cache Offload Guide**: [README_INTERNAL.md](README_INTERNAL.md)

---

## Table of Contents

- [Quickstart](#quickstart)
- [Everyday Use](#everyday-use)
- [Why exFAT Needs This](#why-exfat-needs-this)
- [Features at a Glance](#features-at-a-glance)
  - [CLI Command Reference](#cli-command-reference)
  - [Folder Moves and Coding Agents](#folder-moves-and-coding-agents)
  - [MCP Server & AI Coding Agents](#mcp-server--ai-coding-agents)
  - [Search Syntax](#search-syntax)
  - [Learning Materials (Aurora Slides and Polaris)](#learning-materials-aurora-slides-and-polaris)
  - [Gitignore Governance](#gitignore-governance)
  - [Safe Junk Cleaner](#safe-junk-cleaner)
  - [Snapshots and Backup](#snapshots-and-backup)
  - [Workstation Hybrid and C-Drive Offload](#workstation-hybrid-and-c-drive-offload)
  - [Launchers](#launchers)
  - [Profiles](#profiles)
- [Testing](#testing)
- [Privacy, Security & Data Isolation](#privacy-security--data-isolation)
- [Contributing & License](#contributing--license)

---

## Quickstart

### 1. Install (or run without installing)
```bash
pip install -e .                    # editable install (`smart-drive` CLI)
python -m smart_drive <subcommand>  # or run directly from the checkout
py -m smart_drive <subcommand>      # Windows Python Launcher (when `python` is not on PATH)
smart-drive self-path-check         # check PATH and print 1-click shell setup
```

### 2. Initialise the drive
Run `Setup_SSD.bat` / `Setup_SSD.ps1` (Windows), `Setup_SSD.command` (macOS), `./Setup_SSD.sh` (Linux), or `smart-drive init --profile <name>` (see [Profiles](#profiles)):
- `workstation-hybrid`: your secondary drive (`D:`, `E:`, …) with games, Windows apps, and coursework.
- `internal-developer-vault`: internal NVMe/SATA SSD receiving offloaded `C:` dev and AI caches.
- `ai-developer`: portable external SSD for LLM weights, checkpoints, datasets, and AI agents.
- `data-science`: data engineering pipelines, raw data, and EDA notebooks.
- `general-workspace` *(default)*: universal active/archive code, learning notes, and utilities.
```bash
python -m smart_drive init --profile workstation-hybrid
python -m smart_drive init --profile ai-developer
```

### 3. Set up the MCP ecosystem (one command)
Fetch Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius, and Mira into `~/.smart-drive/mcp` (off the exFAT drive), register them with SmartDrive in every installed coding agent (Claude Code, Antigravity, Cursor, Codex), and health-check them. It lists the servers and asks which ones you want before it downloads or registers anything; safe to repeat:
```bash
python3 -m smart_drive setup  # macOS / Linux (Windows: python -m smart_drive setup or py -m smart_drive setup)
```
Answer `a` (all), `n` (none, the default), or numbers/names such as `1,3` or `polaris,vega`; Constellation, the control plane, always comes with the others. `--yes` skips the question (`--only polaris,vega --yes` installs just those). A coding agent has no terminal to answer in: its run installs nothing, exits with code `4` and lists the servers, and the agent asks you in its own chat before it runs `setup --only <your picks> --yes`.

Needs Node.js >= 22.4 and git. `--dry-run` previews; `--check --json` reports state (exit code `3` = setup needed); `--no-clone` stays offline; `--agents claude,cursor` limits targets. Registered servers are reused, never cloned twice (`mcp-agy` is separate: own venv, Python >= 3.10, API key).

### 4. Register SmartDrive MCP standalone
```bash
smart-drive mcp register --all  # Antigravity, Claude, Cursor, Codex, Windsurf, .mcp.json
```

---

## Everyday Use

**Checkup (1-screen health score & next steps)** — Get a `0–100` (`A–F`) health score with plain-language status and copy-pasteable commands:
```bash
smart-drive checkup
smart-drive checkup --lang vi
```

**Search (<10ms)** — Query the SQLite FTS5 index with BM25 ranking instead of recursive `find`/`grep`:
```bash
smart-drive search "llama cat:ai_models"
smart-drive search "exam ext:pdf size:>1MB dir:DBI202"
smart-drive search --kind slide "vòng lặp"
```

**Organize & Undo** — Auto-zone root folders into the 6 taxonomies, route coursework, and rebalance slack (or run `Smart_Organize.bat` / `./Smart_Organize.sh` for a guided flow):
```bash
smart-drive organize --dry-run
smart-drive organize --apply
smart-drive undo --apply        # revert the latest organize/classify batch & relink back
```

**Moving folders without breaking coding agents, IDEs, or shortcuts** — `organize --apply` and `classify --apply` relink automatically. After moving folders by hand (or via menu option `[8]` in `SmartDrive`), run:
```bash
smart-drive relink --find --apply --ask --wait
smart-drive relink "E:\Old\proj" "E:\New\proj" --wait
smart-drive paths --broken      # scan for broken IDE entries, agent configs, and .lnk shortcuts
```

**Clean (with quarantine & restore)** — Preview and move OS metadata junk, build caches, or logs into `.smart_drive/quarantine/` (mandatory dry-run default):
```bash
smart-drive clean
smart-drive clean --apply --tier 1
smart-drive clean --list-quarantine
smart-drive clean --restore latest
```

**Audit & HTML Treemap** — Inspect storage and cluster slack using actual volume cluster geometry, or export a self-contained offline HTML treemap:
```bash
smart-drive audit
smart-drive audit --html
```

**Health (Sentinel)** — Verify mount, filesystem geometry, shields (auto-healed), index, and Git status:
```bash
smart-drive sentinel
smart-drive health
```

**Snapshot and backup** — Create streaming SHA-256 manifests to detect bit rot and run incremental backups:
```bash
smart-drive snapshot create pre_release
smart-drive snapshot verify pre_release
smart-drive backup --target E:\Backups\SSD_Mirror --dry-run
```

---

## Why exFAT Needs This

High-capacity external SSDs (such as the Kingston XS2000 2 TB) format exFAT with **512 KB (524,288-byte) clusters**:
- Physical allocation is $\lceil \text{File Size} / 524,288 \rceil \times 524,288\text{ B}$ (0 B $\rightarrow$ 0 B; 1 B $\rightarrow$ 512 KB / 99.9998% slack; 10 KB $\rightarrow$ 512 KB / 98.05% slack; 100 KB $\rightarrow$ 512 KB / 80.47% slack; 512 KB + 1 B $\rightarrow$ 1,024 KB / 50% slack).
- A repo with **10,000 source files** (15 MB nominal) consumes **>5.12 GB** on exFAT (>99% cluster slack waste).
- OS indexers (macOS Spotlight `mds`, Windows Search) write hidden metadata (`.DS_Store`, `Thumbs.db`, `.fseventsd`), causing I/O wear and battery drain.
- Internal secondary SSDs (`D:`, `E:`, … on NTFS 4 KB) face `C:` exhaustion from 50–200 GB AI/dev caches while your secondary drive (`D:`, `E:`, …) hosts Windows apps, games, and active SQL Server databases that must never be moved or deleted.

| Dimension | External Portable SSD (exFAT) | Internal Secondary SSD (NTFS) |
|---|---|---|
| **Typical Mount** | `/Volumes/KINGSTON` or `E:\` | `D:\` or `E:\` (Fixed Disk) |
| **Cluster Size** | **512 KB** (524,288 bytes) | **4 KB** (4,096 bytes) |
| **Primary Bottleneck** | Catastrophic cluster slack on small files | C-drive disk exhaustion & cache fragmentation |
| **Symlink Support** | ❌ Unsupported (corrupts cross-platform) | ⚡ Supported via NTFS Junctions (`mklink /J`) |
| **Specialized Strategy** | Bundling, anti-slack auto-zoning, anti-indexing shields | 7-Phase transactional cache offload, service protection |
| **Protected Assets** | Root manifests, scripts, 6 core taxonomies | Windows apps, game libraries, active SQL Server databases |

---

## Features at a Glance

| Capability | One-Line Summary | Main Command | Section |
|---|---|---|---|
| **Unified CLI & Storage Audit** | 25 commands for checkup, setup, FTS5 indexing, real-cluster slack math, HTML treemap, and drive governance | `smart-drive checkup` / `smart-drive audit --html` | [CLI Command Reference](#cli-command-reference) |
| **Agent, IDE & Shortcut Relinking** | Keeps coding agents, VS Code/JetBrains/Obsidian, `.lnk` shortcuts, worktrees, and `.venv` working after moves (plus `undo` & `paths --broken`) | `smart-drive relink --find --apply --ask --wait` | [Folder Moves and Coding Agents](#folder-moves-and-coding-agents) |
| **MCP Server & Setup** | 9 JSON-RPC 2.0 stdio tools (M8ven Grade A 100/100) and 1-command ecosystem setup | `python -m smart_drive setup` | [MCP Server & AI Coding Agents](#mcp-server--ai-coding-agents) |
| **Sub-10ms FTS5 Search** | SQLite FTS5 `unicode61` BM25 search with extension, binary size, category, dir filters | `smart-drive search "<query>"` | [Search Syntax](#search-syntax) |
| **Learning Materials** | Indexes Aurora Slides & Polaris (`slide`, `fact`, `capture`) and pins learning units | `smart-drive search --kind slide "<q>"` | [Learning Materials](#learning-materials-aurora-slides-and-polaris) |
| **Gitignore Governance** | Stack-aware `.gitignore` blocks, Constellation `personal-data` guard, secret/leak audits | `smart-drive gitignore audit` | [Gitignore Governance](#gitignore-governance) |
| **3-Tier Safe Cleaner & Quarantine** | Dry-run-first junk cleaner with reversible quarantine, restore, and inviolable system/app whitelist | `smart-drive clean` | [Safe Junk Cleaner](#safe-junk-cleaner) |
| **Snapshots, Backup & Repos** | Streaming SHA-256 bit-rot verification, incremental backup, duplicate & clone audits | `smart-drive snapshot create` | [Snapshots and Backup](#snapshots-and-backup) |
| **Workstation Hybrid & Offload** | Adaptive exFAT/NTFS geometry, `AcademicClassifier` (FPTU), `protected_by_system` guard, `C:` offload | `smart-drive offload --scan` | [Workstation Hybrid and C-Drive Offload](#workstation-hybrid-and-c-drive-offload) |
| **24 Portable Launchers** | Zero-Git 1-click `.bat`, `.ps1`, `.command`, `.sh` scripts (including `Smart_Organize`) plus `self-path-check` | `Smart_Organize.bat` / `SmartDrive.bat` | [Launchers](#launchers) |
| **5 Preset Profiles** | Layouts for hybrid workstations, C-drive vaults, AI, data science, and general use | `smart-drive init --profile <name>` | [Profiles](#profiles) |

---

## CLI Command Reference

All commands run via `smart-drive <command>`, `python -m smart_drive <command>`, or `py -m smart_drive <command>` (Windows when `python` is not on `PATH`):

| Command | Key Arguments | Description |
|---|---|---|
| `self-path-check` | *(none)* | Inspects Python CLI script paths on system `PATH` and outputs 1-click setup commands. |
| `checkup` | `--root <path>`, `--dup`, `--lang {en,vi}`, `--json` | 1-screen drive health score (`0–100`, grade `A–F`), comparison against previous run, and prioritized next-step commands. |
| `init` | `--profile <name>`, `--root <path>`, `--force`, `--json` | 1-touch drive setup, taxonomy creation, anti-indexing shield installation, and FTS5 DB seeding. |
| `status` | `--root <path>`, `--json` | Inspect SSD mount point, detected filesystem, geometry, shield health, and taxonomy status. |
| `audit` | `--root <path>`, `--html [file]`, `--json`, `--markdown`, `--export <file>` | Storage & cluster slack audit using the drive's real cluster size (4 KB NTFS, 512 KB exFAT, etc.). `--html` exports a self-contained offline treemap report (default `<root>/.smart_drive/reports/audit.html`). Hard links counted once; symlinks/junctions skipped and counted. |
| `clean` | `--dry-run` *(default)*, `--apply`, `--tier {1,2,3}`, `--no-quarantine`, `--list-quarantine`, `--restore <batch\|latest>`, `--purge-quarantine`, `--older-than <days>`, `--log`, `--json` | Safe junk cleaner with mandatory dry-run, inviolable whitelist, and default quarantine to `<root>/.smart_drive/quarantine/<batch_id>/` (restorable via `--restore`). |
| `search` | `<query>`, `--ext <ext>`, `--size <spec>`, `--category <cat>`, `--kind <k>`, `--limit <n>`, `--json` | Sub-10ms SQLite FTS5 multi-criteria query parser with BM25 ranking. |
| `organize` | `--dry-run`, `--apply`, `--clean`, `--json` | Auto-zoning, loose-file relocation, `AcademicClassifier` routing, `protected_by_system` guard, and agent/IDE/shortcut relinking; logs move batches to `.smart_drive/moves.jsonl`. |
| `undo` | `[--batch <id>]`, `--apply`, `--root <path>`, `--json` | Reverses the latest (or specified) `organize`/`classify` move batch in reverse order, refuses to overwrite conflicts, and relinks agents/IDEs/shortcuts back to the original paths (dry-run default). |
| `relink` | `[OLD NEW]`, `--find`, `--apply`, `--ask`, `--wait [MIN]`, `--root <path>`, `--dry-run`, `--json` | Rewrites project/MCP paths in coding agents, VS Code/Cursor/Windsurf/Antigravity (`storage.json` & `state.vscdb`), JetBrains (`recentProjects.xml`), Obsidian (`obsidian.json`), Windows `.lnk` shortcuts, Claude Code history, git worktrees, and `.venv`. |
| `paths` | `--broken`, `--fix`, `--dry-run`, `--wait [MIN]`, `--root <path>`, `--json` | Scans IDE histories, coding agent configs, Obsidian vaults, Windows `.lnk` shortcuts, and `PATH` for broken paths; suggests where moved folders went and repairs sure matches with `--fix`. |
| `sentinel` | `--root <path>`, `--auto-heal`, `--no-heal`, `--json` | 1-touch health audit (detected filesystem, git repo status) and shield self-healing (alias: `agent-check`). |
| `mcp` | `[{serve,register}]`, `--root <path>`, `--all`, `--auth-token <token>`, `--require-auth` | Starts the JSON-RPC 2.0 stdio MCP server (`serve`) or registers configs for AI agents (`register`). |
| `mcp-config` | `--all`, `--antigravity`, `--claude`, `--cursor`, `--windsurf`, `--workspace`, `--json` | Auto-registers SmartDrive MCP Server in AI coding agent config files. |
| `setup` | `--check`, `--dry-run`, `--json`, `--agents <list>`, `--only <list>`, `--dest <dir>`, `--no-clone`, `--no-health`, `--repair`, `--yes` | Asks which MCP servers to install (Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius, Mira), then fetches them, registers them with installed coding agents (Claude Code, Antigravity, Cursor, Codex), and health-checks them. `--yes` skips the question. Exit code `3` from `--check` = setup needed; `4` = without a terminal and without `--yes`, user approval is needed (nothing installed). |
| `snapshot create` | `[name]`, `--partitions <list>`, `--include-hidden`, `--root <path>`, `--json` | Generates a point-in-time manifest with 64KB-chunk streaming SHA-256 hashes. |
| `snapshot list` | `--root <path>`, `--json` | Lists recorded point-in-time snapshots with sizes and file counts. |
| `snapshot verify` | `<name>`, `--no-untracked`, `--root <path>`, `--json` | Validates file integrity against snapshot manifest to detect tampering or bit rot. |
| `backup` | `--target <path>`, `--dry-run`, `--hash`, `--partitions <list>`, `--include-hidden`, `--json` | Safe incremental backup copying only modified/new files to target directory. |
| `classify` | `[path]`, `--suggest`, `--dry-run`, `--apply`, `--no-recursive`, `--json` | Deep content inspection (magic bytes & markers) for AI models, datasets, docs, and code repos. Never follows/moves symlinks or junctions, or moves SmartDrive scripts/system files. |
| `offload` | `--scan`, `--move <name>`, `--target <drive>`, `--revert <name>`, `--wait [MIN]`, `--force`, `--dry-run`, `--json` | Developer/AI cache discovery and transactional offloading to your secondary drive (`D:`, `E:`, …) via NTFS junction (Windows) or symlink (macOS/Linux); refuses when cache-using processes are running unless `--wait` or `--force` is passed. |
| `health` | `[drive]`, `--root <path>`, `--json` | SSD health, TRIM check (Windows), partition geometry, and utilization monitor (drive letter on Windows or `/Volumes/MySSD` on macOS/Linux). |
| `dup` | `--root <path>`, `--json` | 3-phase SHA-256 duplicate detector with real-cluster slack reclamation preview. Hard links to the same data are one file, never offered for deletion. |
| `repos` | `[paths...]`, `--root <ssd>`, `--portable <path>`, `--max-depth <n>`, `--json` | Read-only report of git clones: duplicates in the same place (keep / remove / worktree suggestions) and sync state of SSD-vs-machine mirrors. |
| `gitignore audit` | `[repo]`, `--root <path>`, `--all`, `--owner <name>`, `--keep <pat>`, `--json` | Audit repos for missing `.gitignore` patterns, tracked junk (`.agents`, pycache), and tracked personal learning data in public repos. |
| `gitignore apply` | `<repo>`, `--yes`, `--untrack`, `--keep <pat>`, `--json` | Apply SmartDrive-managed `.gitignore` block for repo stack (dry-run default; `--yes` to apply; `--untrack` to remove matching files from git index). |
| `index` | `--root <path>`, `--db <path>`, `--batch <n>` | Full SQLite FTS5 index creation (>15,000 files/sec). |
| `update` | `[path]`, `--root <path>`, `--db <path>`, `--quiet`, `--json` | Fast $O(1)$ incremental search index synchronization (<2s). |

---

## Folder Moves and Coding Agents

Coding agents, IDEs, and desktop shortcuts remember projects and MCP servers by absolute path, and exFAT cannot leave a link at the old place. When `organize` or `classify` moves a folder, SmartDrive protects system-referenced folders first and rewrites paths everywhere else:
- **System & App Protection (`protected_by_system`)**: Before planning any move, SmartDrive scans Windows User/System `PATH`, Windows Services (`ImagePath`), Installed Apps (`Uninstall` registry `InstallLocation` / `DisplayIcon` / `UninstallString`), Startup (`Run`), `.lnk` shortcuts (Desktop, Start Menu, Quick Launch, drive root), folders containing `unins000.exe` / `uninstall.exe`, and game libraries (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`). Any matching folder is pinned in place with its exact reason in `protected_by_system`.
- **Agent, IDE & Shortcut Settings**: Claude Code (`~/.claude.json`), Claude desktop (config, sessions, worktrees, including Microsoft Store MSIX), Codex (`config.toml`), Gemini CLI, Antigravity, Cursor, Windsurf, VS Code, VSCodium, Positron (`storage.json` and SQLite `state.vscdb`), JetBrains IDEs (`recentProjects.xml` including `$USER_HOME$` macros), Obsidian (`obsidian.json`), Windows `.lnk` shortcuts, Constellation manifest, and the drive's `.mcp.json`. Each file is backed up to `<file>.bak.smart-drive` before writing; SQLite `state.vscdb` is skipped with a warning (`busy_apps`) if the IDE currently holds it open.
- **History and repositories**: Claude Code history and memory move to the new project folder, git worktrees are repaired, and editable installs in a moved `.venv` point at the new place.
- **Reversible Journal (`undo`)**: Every batch of moves is recorded in `.smart_drive/moves.jsonl` with a `batch_id` so `smart-drive undo --apply` can move folders back in reverse order and relink agents/IDEs/shortcuts back to the original paths.

How to run it:
```bash
smart-drive organize --dry-run                           # preview moves, protected folders, and which agents/IDEs follow
smart-drive organize --apply                             # move, then relink agents, IDEs, Obsidian, and shortcuts
smart-drive undo                                         # preview reversing the latest move batch
smart-drive undo --apply                                 # undo the latest batch and relink back to original paths
smart-drive relink                                       # re-apply latest batch (after closing an open IDE/agent)
smart-drive relink "E:\Old\proj" "E:\New\proj" --dry-run # a folder moved by hand: preview
smart-drive relink "E:\Old\proj" "E:\New\proj"           # ...then apply
smart-drive relink --find                                # list stale agent/IDE paths and where folders went
smart-drive relink --find --apply --wait                 # relink sure (?) matches after open apps close
smart-drive relink --find --apply --ask --wait           # ...and ask y/N for each ? match first
smart-drive paths --broken                               # inspect broken shortcuts, IDE entries, PATH, and agent configs
smart-drive paths --broken --fix                         # repair sure matches via relink
```
Menu option `[8]` in `SmartDrive` (or `[10]` / `Smart_Organize.*` for the guided organizer with 1-key `U` undo) handles this interactively. Close open coding agents and IDEs before relinking, or pass `--wait` so SmartDrive waits for them to close, then reopen them. If a command in a moved `.venv` still points at the old place, run `uv sync --reinstall` in that project.

`--find` marks a folder ✓ only when the move journal, or what the agents remember inside the old folder, shows where it went. A folder found by its name alone is marked `?` and never relinked by `--apply` on its own, since it may be another project and the old one deleted: check it, then run the `smart-drive relink "OLD" "NEW"` command printed next to it. Or add `--ask` (menu option `[8]` does): it asks `Relink OLD -> NEW? [y/N]` for each `?` folder with one candidate, and relinks the ones you answer `y` together with the ✓ ones. Enter means no. It asks only in a terminal: with `--json` or piped input it asks nothing.

---

## MCP Server & AI Coding Agents

SmartDrive-OS exposes a JSON-RPC 2.0 `stdio` MCP server with **9 tools**:

1. `ssd_search`: Instant FTS5 search returning compact tokens (<2,000 tokens/query). *(read-only)*
2. `ssd_audit`: Storage breakdown and cluster slack waste analytics with compact formatting. *(read-only)*
3. `ssd_clean`: Whitelist-protected safe junk cleaner with dry-run support. *(destructive)*
4. `ssd_find_duplicates`: 3-phase SHA-256 duplicate detection with pagination and preview capping. *(read-only)*
5. `ssd_update_index`: Fast incremental index synchronization (<2s). *(idempotent)*
6. `ssd_check_safety`: exFAT compatibility validator (9 Win32 forbidden chars, 22 DOS stems, symlinks). *(read-only)*
7. `ssd_status`: SSD mount root status, shield health, and taxonomy status. *(read-only)*
8. `ssd_auto_organize`: Autonomous drive auto-zoning and anti-slack relocation with `AcademicClassifier`. *(destructive)*
9. `ssd_gitignore`: Stack-aware `.gitignore` governance, personal data defense, secrets audit, and safe untracking. *(idempotent)*

Register across all agents via `smart-drive mcp register --all` (or `smart-drive mcp-config --all`). From a source checkout (without `pip install`), each entry sets `PYTHONPATH` to the checkout so clients can launch from any folder. Templates in `configs/`: `.mcp.json` (workspace), `mcp_config.json` (Antigravity), `claude_desktop_config.json` (Claude Desktop & Code), `cursor_mcp.json` (Cursor / Codex), `windsurf_mcp.json` (Windsurf).

<details>
<summary>M8ven MCP Directory Certification (Grade A 100/100) & Token Efficiency</summary>

Certified on the [M8ven MCP Directory](https://m8ven.ai/mcp/duongnad-smart-drive-os) with a **Grade A (100/100) Trust Score** (token: `duongnad-smart-drive-os-1kxkwu`):
- **Static AST Handler Isolation**: Mapped via `SmartDriveMCPServer.TOOL_HANDLERS` without dynamic reflection.
- **Paged Compact JSON**: Paginated (`limit`, `offset`, `total_groups`, `returned_group_count`, `has_more`, `next_offset`) with duplicate previews capped at 10 paths/group (`truncated_files_count`).
- **HMAC Auth & Rate Limiting**: `hmac.compare_digest` via `auth/handshake` (`--auth-token`, `--require-auth`, `SMART_DRIVE_MCP_AUTH_TOKEN`) and thread-safe `SlidingWindowRateLimiter` (`-32000` with `Retry-After`).
- **Path Sanitizers, Loopback & Tool Hints**: `_resolve_safe_path` blocks `../`, `\0`, and cross-drive jumps; web helpers enforce `ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")`; all 9 tools declare `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
- **Zero Hidden Outbound Calls**: 100% offline `stdio`; only explicit opt-in `git clone` in `smart-drive setup` accesses the network (skipped with `--no-clone`).

</details>

---

## Search Syntax

The SQLite FTS5 engine (`unicode61` + BM25) executes multi-criteria queries in <10ms:

- **Free Text**: `smart-drive search "machine learning"`
- **File Extension**: `smart-drive search "weights ext:gguf"`
- **Size Filter**: `smart-drive search "dataset size:>100MB"` (`size:<1MB`, `size:0`). `>`/`<` exclude the bound, `>=`/`<=` include it; binary units (`1KB = 1024` bytes). Unreadable filters (`size:>abc`, `size:>10 MB`) emit a warning (CLI stderr, MCP `warnings`) instead of being ignored silently.
- **Taxonomy Category**: `smart-drive search "llama cat:ai_models"`
- **Directory Constraint**: `smart-drive search "assignment dir:FPTU"`
- **Learning Kind**: `smart-drive search --kind slide "vòng lặp"` (or `kind:fact`, `--kind capture`, `--kind lesson`)
- **Compound Query**: `smart-drive search "exam ext:pdf size:>1MB dir:DBI202"`

---

## Learning Materials (Aurora Slides and Polaris)

SmartDrive-OS integrates with [Aurora Slides MCP](https://github.com/DuongNAD/aurora-slides-mcp) and [Polaris MCP](https://github.com/DuongNAD/polaris-mcp) across the DuongNAD learning ecosystem:

| Project | Role | Repository |
| :--- | :--- | :--- |
| **Polaris** | Course architect: sources, facts with locators, roadmap, schedule, error log | [polaris-mcp](https://github.com/DuongNAD/polaris-mcp) |
| **Aurora Slides** | Keynote-style lessons: quizzes, predict and trace slides, progress events | [aurora-slides-mcp](https://github.com/DuongNAD/aurora-slides-mcp) |
| **Vega** | FSRS memory engine and the single review queue (card inbox) | [vega-mcp](https://github.com/DuongNAD/vega-mcp) |
| **Orion** | Practice labs with real tests, tutoring ladder and PRIMM warm-up | [orion-mcp](https://github.com/DuongNAD/orion-mcp) |
| **Mira** | Code-tracing lab: real Python traces, line-by-line players with prediction gates | [mira-mcp](https://github.com/DuongNAD/mira-mcp) |
| **Rigel** | Grounded tutor: answers from the learner's lesson materials with [ref] citations | [rigel-mcp](https://github.com/DuongNAD/rigel-mcp) |
| **Sirius** | Execution loop: deadlines, triage, today router, session timer, weekly review | [sirius-mcp](https://github.com/DuongNAD/sirius-mcp) |
| **Constellation** | MCP registration, health checks and shared contracts | [constellation-mcp](https://github.com/DuongNAD/constellation-mcp) |
| **SmartDrive-OS** | SSD governance, index and learning search | [smart-drive-os](https://github.com/DuongNAD/smart-drive-os) |

SmartDrive-OS serves Polaris (learning search), Sirius, and Rigel (file discovery), receives post-write updates (`smart-drive update <dir> --quiet`) from Aurora ($\ge$ 0.6.2), Polaris ($\ge$ 0.3.2), and Mira, and enforces the `personal-data` ignore rules of [Constellation's contracts](https://github.com/DuongNAD/constellation-mcp/tree/main/contracts).

<details>
<summary>Indexed learning artifact kinds & Atomic Learning-Unit Protection</summary>

| Kind | Target Artifact | Extracted Content |
|---|---|---|
| `slide` | `deck.json` (Aurora Slides) | Slide titles, bullets, speaker notes, key takeaways |
| `lesson` | `course.json` (Aurora Courses) | Lesson syllabus, objectives, prerequisites, modules |
| `module` | `polaris/roadmap.json` (Polaris) | Roadmap modules, milestones and prerequisites |
| `fact` | `polaris/facts.json` (Polaris) | Atomic facts, statements, concepts, confidence scores |
| `source` | `polaris/sources.json` (Polaris) | Bibliographic citations, URLs, author notes, digests |
| `capture` | `polaris/captures/` (Polaris) | Extracted excerpts, highlights, study pages, quotes |
| `outline` | `polaris/lessons/*.outline.md` (Polaris) | Lesson outlines (headings) |
| `research` | `polaris/research/*.md` (Polaris) | Deep research syntheses, literature summaries, analyses |
| `error` | `polaris/errorlog.md` (Polaris) | Bug retrospectives, misconceptions, fixes |
| `learning` | All learning artifacts | Consolidated search across all educational kinds |

Aurora deck/course folders and Polaris subject dirs (`polaris/brief.json`) are pinned as atomic **Learning Units** (`pinned: Aurora course/deck or Polaris subject`): AutoZoner never splits/moves them, Cleaner and Duplicate Detector never delete their files, and legacy `polaris/.cache/**` files are cleaned as Tier-2 dev-cache junk (*"Polaris regenerable API cache (Polaris 0.3.2+ keeps it on C:)"*).

</details>

---

## Gitignore Governance

`smart-drive gitignore` audits and applies stack-aware `.gitignore` rules across Git repos on the drive (<5ms shallow detection for Python, Node.js, Jupyter, Java, Rust, .NET, LaTeX, AI Models, Learning). It maintains an idempotent block between `# >>> smart-drive managed (do not edit inside) >>>` and `# <<< smart-drive managed <<<`, preserving user rules and CRLF/LF line endings:
- **exFAT Cluster Slack Defense**: Ignores `**/.agents/*` (500+ scratch files waste >260 MB on 512 KB clusters; `.agents/skills/`, `.agents/workflows/`, `.agents/rules/` stay tracked), `.claude/settings.local.json`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `target/`/`dist/`/`bin/`/`obj/`.
- **Constellation `personal-data` Protection**: Locks down `.vega/`, `**/progress.jsonl`, `aurora-progress-*.jsonl`, `orion-attempts.jsonl`, `**/polaris/.cache/`, `**/polaris/captures/`, `.polaris/`, and `.sirius/`, alerting if tracked in public or unconfigured remotes.

```bash
smart-drive gitignore audit                                 # audit own & local repos (--all includes third-party)
smart-drive gitignore apply D:\teamwork_projects\my_project # dry-run preview
smart-drive gitignore apply D:\teamwork_projects\my_project --yes --untrack
```

<details>
<summary>Ownership classes, GitHub CLI visibility, intentional build outputs, secrets & safe untracking</summary>

- **Ownership Classes**: `own` (matches `--owner`, `SMART_DRIVE_GIT_OWNER`, `smartdrive.owner`, or drive consensus), `third-party` (`open_source/`, `vendor/`, `third_party/`, or external owner—upstream `dist/`/`build/` respected), and `local` (no remote).
- **Live GitHub Visibility (`gh`)**: Queries `public`, `private`, `internal`, or `missing` ("remote not found on GitHub") with per-run caching so private/deleted remotes never trigger false public-leak alarms.
- **Intentional Build Outputs & Keep Lists**: Files in `package.json` (`bin`, `main`, `module`, `types`, `exports`), `.gitignore` negations (`!dist/bundle.mjs`), or `smartdrive.keep` / `--keep <pattern>` are never flagged or untracked. Direct intentional files in `dist/` emit `<dir>/*` plus `!<file>` lines (or omit the pattern if nested).
- **High-Severity Secrets & Safe Untracking**: Tracked `.env`, `.env.*`, `*.pem`, `*.key`, and `credentials*.json` alert with **variable/key names only** (no secret values). `--untrack` runs `git rm -r --cached` (keeping disk files) and refuses when uncommitted staged changes exist.

</details>

---

## Safe Junk Cleaner

Purgeable files are grouped into 3 tiers with a mandatory dry-run default (`--apply` required to move or delete):
1. **Tier 1 (Safe OS Metadata)**: macOS `.DS_Store`, `._*` AppleDouble, `.Spotlight-V100`, `.Trashes`; Windows `Thumbs.db`, `desktop.ini`, `ehthumbs.db`.
2. **Tier 2 (Developer Caches)**: Python `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`; Node `node_modules/.cache`; Rust `target/debug/build`; legacy `polaris/.cache/**`.
3. **Tier 3 (Transient & Logs)**: `*.log`, `*.tmp`, `*.bak`, crash dumps.

By default, `smart-drive clean --apply` moves files into a reversible quarantine batch under `<root>/.smart_drive/quarantine/<batch_id>/` (with `manifest.json`) instead of permanently unlinking them:
- Inspect quarantined batches: `smart-drive clean --list-quarantine`
- Restore a batch to its original paths: `smart-drive clean --restore latest` (or `--restore <batch_id>`)
- Permanently purge old quarantine batches: `smart-drive clean --purge-quarantine --older-than 14`
- Bypass quarantine (or when free space is critically low): `smart-drive clean --apply --no-quarantine`

> **Inviolable Whitelist**: Root manifests (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`, `PRIVACY.md`), launcher scripts (`Setup_*`, `Quick_*`, `Smart_Organize.*`, `SmartDrive.*`), the 6 taxonomies, Windows folders (`WindowsApps`, `Program Files`, `DumpStack.log.tmp`), system-referenced folders (`protected_by_system`: Windows `PATH`, Services, Installed Apps, Startup, `.lnk` targets, uninstallers), game libraries (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`), active SQL Server databases (`Microsoft SQL Server 2022`), and `smart-drive-os` are permanently protected.

---

## Snapshots and Backup

- **SHA-256 Snapshots (`smart-drive snapshot`)**: Streaming 64 KB-chunk ($O(1)$ memory) point-in-time manifests in `.smart_drive/snapshots/<name>.json` to detect bit rot or tampering.
- **Incremental Backup (`smart-drive backup`)**: Copies modified/new files while excluding OS junk. Default coverage: `02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox` (override with `--partitions`); hidden dirs (`.git`, `.github`, `.vscode`) are skipped unless `--include-hidden` is passed, and every run lists what was not covered.
- **Duplicates (`smart-drive dup`) & Git Clones (`smart-drive repos`)**: 3-phase SHA-256 duplicate finder (hard links counted once) and read-only Git clone auditor checking SSD-vs-machine **mirrors** (`commit`, `push`, `pull`, `fetch to compare`) and same-drive **duplicates** (newest commit to keep, redundant same-branch copies, or `git worktree` with `--relative-paths` on git 2.48+; dirty/unpushed/stashed clones are blocked from removal).

---

## Workstation Hybrid and C-Drive Offload

`FilesystemAdapter` and `DriveDetectorBackend` adapt between **exFAT 512 KB** (slack guard, anti-symlink rules, Spotlight/FSEvents shields) and **NTFS 4 KB** (4,096-byte math, `fsutil` TRIM check, `C:` cache offload). `smart-drive offload` migrates heavy caches (HuggingFace, Ollama, PyTorch, Docker WSL2, pip, uv, npm, Conda, Gradle, Cargo) from `C:` to `<target>\04_System_Offload_Caches\<name>` on your secondary drive (`D:`, `E:`, …) in a **7-phase transactional workflow** with running-process detection (`--wait`, `--force`), rollback safety, and unprivileged NTFS Directory Junctions (`mklink /J`). See [README_INTERNAL.md](README_INTERNAL.md).

<details>
<summary>AcademicClassifier (FPTU), Non-Code Routing & AutoZoner Self-Defense</summary>

- **AcademicClassifier (FPTU)**: Extracts course codes (`DBI202`, `WED201c`, `SWE202c`, `PRN211`, `PRJ301`, `OSG`), detects exams/labs (`PE`, `de thi`, `on luyen`, `lab1_sp26`, `sp26`, `fa25`, `su25`), zones into `02_Learning_Knowledge/FPTU/Ky_1/` .. `Ky_7/`, and repairs ZIP mojibake (`h?c k? 3 fptu` $\rightarrow$ `Hoc_Ky_3_fptu`, `n luy?n pe dbi202` $\rightarrow$ `On_Luyen_pe_dbi202`, `k 1 fptu` $\rightarrow$ `Ky_1_fptu`, `dich truyen` $\rightarrow$ `Dich_Truyen`).
- **Non-Code Routing**: `dich truyen` $\rightarrow$ `02_Learning_Knowledge/Personal_Books/dich truyen`, `lenovo` $\rightarrow$ `05_Dev_Toolbox/OEM_Drivers/LENOVO`, `sql2022` $\rightarrow$ `05_Dev_Toolbox/Installers/SQL2022`.
- **AutoZoner Self-Defense & System Reference Guard (`protected_by_system`)**: Anchors `_CURRENT_FILE` through `_SMART_DRIVE_REPO_DIR` and ancestors so `smart-drive-os` never moves itself; pre-filters Windows folders (`WindowsApps`, `Program Files`, `Program Files (x86)`, `$Recycle.Bin`, `System Volume Information`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `DumpStack.log.tmp`), folders referenced by Windows `PATH`, Services, Installed Apps (`Uninstall` registry), Startup (`Run`), `.lnk` shortcuts, folders with `unins000.exe`/`uninstall.exe`, game libraries (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`, `fo4`, `32837`, `Downloads`), and active SQL Server (`DBI202_VuPT\MSSQL16.MSSQLSERVER`, `MSSQLSERVER`, `MSSQL`).

</details>

---

## Launchers

All 24 portable launchers reside in both the repo root and `launchers/` (zero Git required):

| Workflow | Windows CMD | PowerShell | macOS App | Linux/POSIX | Purpose |
|---|---|---|---|---|---|
| **Drive Setup** | `Setup_SSD.bat` | `Setup_SSD.ps1` | `Setup_SSD.command` | `Setup_SSD.sh` | Interactive 1-touch initialization & profile selector |
| **Quick Search** | `Quick_Search.bat` | `Quick_Search.ps1` | `Quick_Search.command` | `Quick_Search.sh` | Instant interactive SQLite FTS5 search prompt |
| **Quick Clean** | `Quick_Clean.bat` | `Quick_Clean.ps1` | `Quick_Clean.command` | `Quick_Clean.sh` | Dry-run junk preview followed by confirmed Tier 1 quarantine |
| **Quick Audit** | `Quick_Audit.bat` | `Quick_Audit.ps1` | `Quick_Audit.command` | `Quick_Audit.sh` | Instant storage breakdown and cluster slack audit |
| **Safe Guided Organizer** | `Smart_Organize.bat` | `Smart_Organize.ps1` | `Smart_Organize.command` | `Smart_Organize.sh` | Step-by-step dry-run preview, busy-app wait, `YES` confirmation, post-move verification, and 1-key `U` undo (EN/VI) |
| **Interactive Menu** | `SmartDrive.bat` | `SmartDrive.ps1` | `SmartDrive.command` | `SmartDrive.sh` | Full interactive TUI menu (including `[8]` Relink Agents, `[9]` Checkup, `[10]` Safe Guided Organizer) |

`smart-drive self-path-check` checks the running Python environment (venv, pyenv, user scripts) and prints the PowerShell `SetEnvironmentVariable`, `~/.bashrc`/`~/.zshrc` export, or `pyenv rehash` command if `smart-drive` is not on `PATH`.

---

## Profiles

| Profile | Target Architecture | Included Taxonomies & Subdirectories |
|---|---|---|
| **`workstation-hybrid`** *(Recommended)* | Your secondary drive (`D:`, `E:`, …) with games, apps, coursework | `01_AI_Models`, `02_Learning_Knowledge` (`FPTU/`, `Personal_Books/`, `Notes/`), `03_Development_Projects` (`active/`, `archive/`), `04_System_Workspaces`, `05_Dev_Toolbox` (`OEM_Drivers/`, `Installers/`, `scripts/`), `06_Archives_Storage` |
| **`internal-developer-vault`** | Internal NVMe/SATA SSD (`D:`, `E:`, …) for offloaded `C:` caches | `01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches` (`huggingface/`, `ollama/`, `pip/`, `uv/`), `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`ai-developer`** | External SSD for LLMs, fine-tuning, AI agents | `01_AI_Models` (`checkpoints/`, `gguf/`, `safetensors/`, `datasets/`), `02_Learning_Knowledge`, `03_Development_Projects` (`ai_agents/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`data-science`** | Data engineering, pipelines, EDA notebooks | `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects` (`notebooks/`, `data_raw/`, `pipelines/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`general-workspace`** *(Default)* | Universal code, docs, notes, utilities | Standard 6 taxonomies with `Notes/`, `References/`, `active/`, `archive/`, `scripts/` |

---

## Testing

1,150+ automated tests run in CI on Windows, macOS and Linux with Python 3.9 and 3.13 using `unittest`:

```bash
python -m unittest discover tests -v
```

Coverage spans gitignore governance (ownership, intentional build outputs, negations, keep lists, `gh` visibility, secret variable names, CRLF preservation, staged-change guard), atomic learning units, system reference protection (`protected_by_system`), agent/IDE/Obsidian/shortcut relinking (`storage.json`, `state.vscdb`, `recentProjects.xml`, `.lnk`, MSIX Claude desktop, `--find`, `--wait`), `undo`, `checkup`, `paths --broken`, `clean` quarantine/restore, HTML treemap audit, `Smart_Organize` (`SD_SELFTEST=1`), cluster geometry (512 B–32 MB), self-defense, and `AcademicClassifier`.

---

## Privacy, Security & Data Isolation

SmartDrive-OS is engineered with a **local-first, zero-trust** architecture:
- **100% Local-Only Operations**: All scanning, SQLite FTS5 indexing, and SHA-256 snapshotting run exclusively on local storage.
- **Zero Telemetry & Zero PII Logging**: No analytics, network beacons, or harvesting of file contents/secrets; only basic metadata is stored in `.smart_drive/index.db`.
- **Air-Gap Ready & Defensive Safeguards**: Zero external pip dependencies (`dependencies = []`), local `stdio` MCP transport, offline `setup --no-clone`, mandatory dry-run cleanup defaults, and an inviolable whitelist (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`).

For full details and compliance specifications, read [PRIVACY.md](PRIVACY.md).

---

## Contributing & License

- 🌟 **Star & Fork** on GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os) · 🐛 [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues) · 🔀 [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls) · 📖 [CONTRIBUTING.md](CONTRIBUTING.md)
- 📄 Licensed under the **MIT License** — see [LICENSE](LICENSE).
