# Project: SmartDrive-OS MCP & Cross-Platform Hardening

## Architecture
SmartDrive-OS is an autonomous agent operating layer and performance engine for external SSDs (specifically exFAT with 512KB allocation units). It provides sub-10ms SQLite FTS5 file indexing, storage slack auditing, safe junk cleaning, duplicate detection, and a Model Context Protocol (MCP) server over JSON-RPC 2.0 stdio.

### Key Subsystems & Boundaries
1. **MCP Server & Tool Engine** (`smart_drive/mcp/server.py`):
   JSON-RPC 2.0 stdio transport exposing 8 tools (`ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`).
2. **Security & Path Normalization Engine** (`smart_drive/mcp/server.py`, `smart_drive/purge_engine.py`, `smart_drive/core/`):
   Enforces drive boundary containment, blocks directory escape (POSIX and Windows drive letters, backslashes, UNC paths), detects Windows forbidden characters, and protects immutable root configuration files (`AGENTS.md`, `GEMINI.md`).
3. **Core OS & Hardware Abstraction** (`smart_drive/core/`):
   Cross-platform drive detector (`drive_detector.py`), directory junctions (`junction.py`), cache offloader (`offloader.py`), and storage engine (`exfat_engine.py`).
4. **Agent Registrar** (`smart_drive/mcp/registrar.py`, `smart_drive/cli/`):
   Auto-detection and 1-click registration of SmartDrive MCP server into AI coding agent configurations (Google Antigravity 2.0, Claude Desktop/Code, Cursor/Codex, Windsurf, local workspace).
5. **Portable Launchers** (`launchers/`, root `.bat`, `.ps1`, `.command`, `.sh`):
   Double-clickable portable launchers with 5-tier self-environment verification (Python 3.9+, exFAT bytecode slack defense `PYTHONDONTWRITEBYTECODE=1`, personal git/host isolation).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Universal Path Normalization | Block traversal (`..\..`, `/etc/passwd`), Windows drives (`C:\`, `Z:`), UNC (`\\server\share`), null bytes on all OS | M1 (DONE) | R2 / Survey Explorer 1 & 2 |
| 2 | Forbidden Character & Segment Validation | Strip Windows drive prefix before character check, flag UNC paths, accurate error reporting | M1 (DONE) | R2 / Survey Explorer 1 |
| 3 | Cross-Platform System Drive Detection | Normalize Windows drive parsing in `drive_detector.py` when executing on POSIX/macOS | M1 (DONE) | R2 / Survey Explorer 1 |
| 4 | Proxy Mount Point Resolution | Ensure mock paths don't resolve to host working directory and normalize slashes | M1 (DONE) | R2 / Survey Explorer 1 |
| 5 | Broken Directory Junction Detection | Recognize broken directory reparse points / symlinks as junctions on POSIX | M1 (DONE) | R2 / Survey Explorer 1 |
| 6 | Cache Offload Path Resolution | Use `.resolve()` for macOS `/var` symlink and fix Windows double-slash formatting | M1 (DONE) | R2 / Survey Explorer 1 |
| 7 | UI High Concurrency Listen Backlog | Increase `request_queue_size = 128` in `ThreadingHTTPServer` to eliminate TCP RST | M1 (DONE) | R2 / Survey Explorer 1 |
| 8 | Search Index Schema Initialization | Ensure `db.initialize_schema()` is called in `handle_ssd_update_index` to prevent `OperationalError` | M1 (DONE) | R1 / Survey Explorer 2 |
| 9 | Token-Efficient JSON Output | Compact JSON serialization, removal of `indent=2`, saving 40-50% context window tokens | M2 (DONE) | R1 / Survey Explorer 2 |
| 10 | Duplicate Group Pagination | Add `limit`, `offset`, `has_more`, `next_offset` to `ssd_find_duplicates` | M2 (DONE) | R1 / Survey Explorer 2 |
| 11 | Summarized Storage Audit | Add `compact=True` mode to `ssd_audit` summarizing top extensions instead of dumping hundreds of raw keys | M2 (DONE) | R1 / Survey Explorer 2 |
| 12 | Informative Dry-Run Cleaner Preview | Add file-type breakdown and sample preview to `ssd_clean` dry-run | M2 (DONE) | R1 / Survey Explorer 2 |
| 13 | Agent System Descriptions & Prompts | Imperative prompt warnings instructing coding agents to use `ssd_search` instead of `find`/`grep` | M2 (DONE) | R1 / Survey Explorer 2 |
| 14 | JSON-RPC stdio Stream Isolation | Isolate `sys.stdout` via `_raw_stdout` and redirect `sys.stdout` to `sys.stderr` | M2 (DONE) | R3 / Survey Explorer 2 |
| 15 | Zero-Dependency & Clean Codebase | Verify 100% Python standard library, remove dead imports (`CLUSTER_SIZE_BYTES`, `SearchParams`, `Path`, `Callable`) | M2 (DONE) | R3 / Survey Explorer 2 |
| 16 | Multi-Agent Registrar Auto-Detection | Detect installed agents (Antigravity 2.0, Claude, Cursor, Windsurf, workspace) | M3 (DONE) | R1 / Survey Explorer 3 |
| 17 | CLI `smart-drive mcp register` Bridge | Route `mcp register` command directly to `cmd_mcp_config` with agent filter flags | M3 (DONE) | R1 / Survey Explorer 3 |
| 18 | UTF-8 Stdio Environment Declarations | Set `PYTHONIOENCODING: utf-8` and `PYTHONUTF8: 1` in generated MCP config files | M3 (DONE) | R1 / Survey Explorer 3 |
| 19 | Dual Claude Config Registration | Update both `claude_desktop_config.json` and `~/.claude.json` when present | M3 (DONE) | R1 / Survey Explorer 3 |
| 20 | Portable Scripts Matrix (.bat, .ps1, .command, .sh) | Full set of 20 launchers for Setup, Audit, Clean, Search, and Master 1-Touch SmartDrive menu | M4 (DONE) | R4 / Survey Explorer 3 |
| 21 | 5-Tier Self-Environment Check | Python 3.9+ verification, `PYTHONDONTWRITEBYTECODE=1` cluster slack defense, host/git isolation | M4 (DONE) | R4 / Survey Explorer 3 |
| 22 | 100% Test Suite Verification (565 tests) | Execute full unit and adversarial test suite; achieved 686 passed (0 fail, 0 err, 11 skip) | M5 (DONE) | R2 / Acceptance Criteria |
| 23 | Adversarial Coverage Hardening (Tier 5) | White-box adversarial testing and forensic audit for anti-cheat and integrity verification | M5 (DONE) | Project Pattern |

## Milestones Summary
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Path Traversal Security & Core Resolution | Features 1-8 (`server.py`, `drive_detector.py`, `proxy.py`, `junction.py`, `offloader.py`, `ui/server.py`) | none | DONE |
| M2 | MCP Tool Optimization & Zero-Dependency | Features 9-15 (`server.py` schemas, pagination, agent prompts, stdio stream isolation, dead imports) | M1 | DONE |
| M3 | MCP Registrar & Multi-Agent 1-Click | Features 16-19 (`registrar.py`, `cli/main.py`, `cmd_mcp.py`, `cmd_mcp_config.py`) | M1 | DONE |
| M4 | Portable Safe Launchers & Distribution | Features 20-21 (`launchers/`, root `.bat`, `.ps1`, `.command`, `.sh`, 5-tier self-check) | none | DONE |
| M5 | E2E Test Suite Pass & Adversarial Hardening | Features 22-23 (Phase 1: 100% pass on 686 tests; Phase 2: Tier 5 adversarial hardening + Forensic Audit) | M1, M2, M3, M4 | DONE |

## Code Layout
- `smart_drive/mcp/server.py`: MCP Server implementation, JSON-RPC 2.0 stdio handler, 8 tool handlers.
- `smart_drive/mcp/registrar.py`: IDE discovery and config registrar.
- `smart_drive/cli/`: CLI commands (`main.py`, `cmd_mcp.py`, `cmd_mcp_config.py`).
- `smart_drive/core/`: Hardware & storage modules (`drive_detector.py`, `junction.py`, `offloader.py`, `exfat_engine.py`).
- `smart_drive/ui/`: Web dashboard server (`server.py`).
- `launchers/` & root launchers: 20 portable double-clickable launch scripts with 5-tier self-environment check.
- `tests/`: 697 test suite files covering unit, integration, E2E, and adversarial security tests.
