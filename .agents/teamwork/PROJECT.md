# Project: SmartDrive-OS MCP Grade A Upgrade & Trust Compliance

## Architecture
- **Zero-Dependency Core**: 100% Python Standard Library. Zero runtime pip dependencies (`dependencies = []`).
- **Static AST-Resolvable MCP Dispatch**: `SmartDriveMCPServer.TOOL_HANDLERS` dictionary and explicit static `if-elif` dispatch chain in `dispatch_tool` enabling 100% static AST call-graph coverage for all 8 MCP tools.
- **MCP Tool Declaration & Schema Fidelity**: Synchronized schemas and execution logic across all 8 tools (`ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`).
- **Authentication Handshake Engine**: Pure stdlib token handshake (`auth/handshake`, `initialize` tokens) using `hmac.compare_digest`, JSON-RPC `-32001` protection, CLI flags (`--auth-token`, `--require-auth`), and transparent zero-friction stdio defaults for local IDE agents (Antigravity, Claude, Cursor, Windsurf).
- **Network Loopback Isolation & CORS Hardening**: `ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")` in `smart_drive/ui/server.py` with strict host validation, URL normalization, and CORS origin restriction.
- **Domain Consistency & Packaging Metadata**: Author/maintainer aligned to `DuongNAD`, version `1.1.0` synchronized across `pyproject.toml`, `smart_drive/__init__.py`, and `smart_drive/mcp/server.py`, `Privacy` URL in project URLs, and updated documentation.
- **Comprehensive Verification Layer**: 565 tests passing cleanly across 32 test modules (523 baseline + 42 new Grade A tests in `tests/test_mcp_grade_a.py`).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | AST Handler Isolation | Class-level `TOOL_HANDLERS` mapping and explicit static `if-elif` chain in `dispatch_tool` | M1 | explorer_1 |
| 2 | Tool Schema Alignment (`ssd_find_duplicates`) | Add `min_size` to inputSchema and document exact 512KB physical savings | M1 | explorer_1 |
| 3 | Tool Behavior Alignment (`ssd_check_safety`) | Add symlink check and intermediate path segment audit to `handle_ssd_check_safety` | M1 | explorer_1 |
| 4 | Tool Description Accuracy (`ssd_status`) | Clarify description to SQLite DB, exFAT safety, and Git status | M1 | explorer_1 |
| 5 | Tool Behavior Alignment (`ssd_auto_organize`) | Enforce shield creation and `IndexManager.incremental_update()` on apply | M1 | explorer_1 |
| 6 | Tool Schema Alignment (`ssd_audit`) | Support `directory` and `sub_dir`, document 6 taxonomies and slack directories | M1 | explorer_1 |
| 7 | Pure Stdlib Auth Engine | Token handshake supporting `auth/handshake` and `initialize` tokens with `hmac.compare_digest` | M2 | explorer_2 |
| 8 | Zero-Friction Stdio Access | Default `require_auth=False` when token unset for local AI agents | M2 | explorer_2 |
| 9 | CLI Auth Options | Support `--auth-token` and `--require-auth` flags and `SMART_DRIVE_MCP_AUTH_TOKEN` env var | M2 | explorer_2 |
| 10 | Network Loopback Binding Guard | Strict loopback validation (`127.0.0.1`, `localhost`) in `smart_drive/ui/server.py` | M2 | explorer_2 |
| 11 | Safe URL & CORS Hardening | Normalize loopback URLs and restrict CORS origins away from wildcard `*` | M2 | explorer_2 |
| 12 | Packaging Domain Consistency | Align author/maintainer with `DuongNAD` and add `Privacy` URL in `pyproject.toml` | M3 | explorer_3 |
| 13 | Version & Descriptor Sync | Sync `SERVER_VERSION = "1.1.0"` in `server.py`, `__author__` in `__init__.py`, and `LICENSE` | M3 | explorer_3 |
| 14 | Documentation Metrics Update | Update test counts and compliance citations in `PRIVACY.md` | M3 | explorer_3 |
| 15 | Dedicated Grade A Test Suite | New automated tests in `tests/test_mcp_grade_a.py` for AST resolution, auth, loopback, metadata | M4 | explorer_3 |
| 16 | Zero Regression & Invariants | 100% pass across all 565 tests, zero external dependencies (`dependencies = []`) | M4 | survey |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | AST Handler Isolation & Tool Description Accuracy | `smart_drive/mcp/server.py` | none | DONE |
| M2 | Authentication Handshake & Network Loopback Isolation | `smart_drive/mcp/server.py`, `smart_drive/cli/`, `smart_drive/ui/server.py` | M1 | DONE |
| M3 | Packaging Metadata & Domain Consistency | `pyproject.toml`, `smart_drive/__init__.py`, `server.py`, `LICENSE`, `PRIVACY.md` | M1, M2 | DONE |
| M4 | Comprehensive Test Verification & Final Gate | `tests/test_mcp_grade_a.py`, regression runs on all 565 tests, reviewer & auditor gates | M1, M2, M3 | DONE |

## Gate Result
- Gate Status: **PASS** (Reviewer 1 APPROVE, Reviewer 2 APPROVE, Challenger 1 APPROVE, Challenger 2 APPROVE, Forensic Auditor CLEAN).
- Total Tests: **565 passed** across all 32 test modules.
- Runtime Dependencies: strictly `dependencies = []` (100% Python Standard Library).
