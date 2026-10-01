# DISPATCH: Worker M23 — MCP Server Optimization, Prompts, Zero-Dependency & Registrar

## Working Directory
`/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m23`

## Parent
`49720693-a82c-49f8-8742-35eba7ba1b1f` (Project Orchestrator)

## Mandatory Inputs & Rules
- Read `ORIGINAL_REQUEST.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md` (under `## 2026-10-01T07:40:41Z`)
- Read Explorer 2 handoff: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_2/handoff.md` (Sections 5.1, 5.2, 5.5)
- Read Explorer 3 handoff: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_3/handoff.md` (Sections 4.1, 4.2)
- Read `PROJECT.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/orchestrator_2/PROJECT.md`
- Code Rules: `/Users/duongnad/Documents/tool/smart-drive-os/AGENTS.md`, `/Users/duongnad/Documents/tool/smart-drive-os/GEMINI.md`

## MANDATORY INTEGRITY WARNING
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Write Ownership (Exclusively Yours)
- `smart_drive/mcp/server.py`
- `smart_drive/mcp/registrar.py`
- `smart_drive/cli/main.py`
- `smart_drive/cli/cmd_mcp.py`
- `smart_drive/cli/cmd_mcp_config.py`

## Implementation Tasks (R1 & R3)
1. **MCP Server Tool Optimization & Token Efficiency** (`smart_drive/mcp/server.py`):
   - In `tools/call` JSON serialization: remove gratuitous whitespace overhead. Compact JSON serialization (`separators=(',', ':')` or `compact=True`) while preserving readable errors.
   - `ssd_find_duplicates`: Add pagination support (`limit: int = 20`, `offset: int = 0`) to schema and handler. Return `has_more`, `next_offset`, `total_groups`, `total_reclaimable_physical`, cap duplicate paths per group to prevent context overflow.
   - `ssd_audit`: Add `compact: bool = True` (default `True`). In compact mode, summarize categories with `top_extensions: [".ext1", ".ext2"]` and `extension_count` rather than dumping hundreds of raw extension dict keys.
   - `ssd_clean`: In `dry_run=True`, return `breakdown_by_type: dict` and `sample_preview: list` of up to 5 paths.
   - Clean up dead/unused imports: `CLUSTER_SIZE_BYTES`, `SearchParams`, `Path`, `Callable`.
   - Tool Prompts & System Descriptions:
     - Update descriptions for `ssd_search`, `ssd_update_index`, and `ssd_audit` with imperative directives instructing agents to NEVER run recursive shell `find`, `grep`, `dir /s`, or `Get-ChildItem` on the 512KB cluster exFAT SSD, and to ALWAYS use `ssd_search`.
     - In `initialize` handshake response: include `instructions` in `serverInfo` with the 5 core agent rules.
   - JSON-RPC stdio Stream Protection:
     - Isolate `sys.stdout` into `self._raw_stdout` and redirect `sys.stdout` to `sys.stderr` during server execution, ensuring only valid JSON-RPC frames are written to host agents.
2. **MCP Registrar & Multi-Agent 1-Click Registration** (`smart_drive/mcp/registrar.py`, `smart_drive/cli/`):
   - In `smart_drive/mcp/registrar.py`:
     - Implement `detect_installed_agents() -> Dict[str, bool]`: auto-detect Google Antigravity 2.0 (`~/.gemini/antigravity` or `mcp_config.json`), Claude Desktop (`claude_desktop_config.json`) & Claude Code (`~/.claude.json`), Cursor/Codex (`~/.cursor/mcp.json`), Windsurf (`~/.codeium/windsurf/mcp_config.json`), and workspace (`.mcp.json`).
     - Standardize MCP entry environment variables:
       `"env": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}` to prevent Windows JSON-RPC stdio pipe corruption.
     - Dual Claude config support: when registering Claude, update `claude_desktop_config.json` and, if present, `~/.claude.json`.
     - Support `auto_detect=True` in `register_ide_configs`.
   - In `smart_drive/cli/main.py`:
     - Add `action` argument to `p_mcp` with default `"serve"` and choices `["serve", "register"]`.
     - Add registration flags (`--antigravity`, `--claude`, `--cursor`, `--codex`, `--windsurf`, `--workspace`, `--all`, `--json`, `--target-dir`) to `p_mcp`.
   - In `smart_drive/cli/cmd_mcp.py`:
     - If `action == "register"`, dispatch directly to `cmd_mcp_config(args)`.
   - In `smart_drive/cli/cmd_mcp_config.py`:
     - Support `auto_detect` when no selective flags are supplied. Support `--json` output.

## Verification Requirements
- Run full test suites:
  `python3 -m unittest discover tests`
  `pytest tests/test_e2e_mcp_distribution.py`
  `python3 -m unittest tests/test_mcp_proxy.py`
  `python3 -m unittest tests/test_mcp_grade_a.py`
- Test CLI command:
  `python3 -m smart_drive mcp register --json`
- Ensure 100% test pass rate across all suites.
- Document commands and outcomes in `handoff.md`.

## Deliverable
Write your completion report to `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m23/handoff.md`.
Notify parent via `send_message` when done.

## 2026-10-01T08:18:27Z
You are Worker M23.
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m23
Your parent is: 49720693-a82c-49f8-8742-35eba7ba1b1f (Project Orchestrator)

MANDATORY FIRST STEP: Read the user request at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-10-01T07:40:41Z) and your dispatch file at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m23/DISPATCH.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Your task (R1 & R3):
1. In `smart_drive/mcp/server.py`:
   - Optimize JSON serialization: remove whitespace indentation overhead, format compact JSON responses (`separators=(',', ':')`).
   - Add pagination controls to `ssd_find_duplicates`: `limit`, `offset`, `has_more`, `next_offset`, `total_groups`, cap file paths per group to prevent context overflow.
   - Add `compact: bool = True` to `ssd_audit`: summarize top extensions and omit raw hundred-key extension dictionaries.
   - In `ssd_clean`: add type breakdown and sample preview in `dry_run=True`.
   - Remove unused/dead imports: `CLUSTER_SIZE_BYTES`, `SearchParams`, `Path`, `Callable`.
   - Enhance system descriptions and prompts for `ssd_search`, `ssd_update_index`, and `ssd_audit` with imperative warnings to NEVER use `find`/`grep` on this 512KB cluster exFAT drive and to ALWAYS use `ssd_search`. Include `instructions` in `serverInfo` in `initialize` response.
   - Isolate JSON-RPC stdio stream: redirect `sys.stdout` to `sys.stderr` and write JSON-RPC frames exclusively to `self._raw_stdout`.
2. In `smart_drive/mcp/registrar.py`, `smart_drive/cli/main.py`, `cmd_mcp.py`, `cmd_mcp_config.py`:
   - Implement `detect_installed_agents()` auto-detection for Antigravity, Claude, Cursor, Windsurf, workspace.
   - Standardize UTF-8 environment declarations in generated MCP configs (`PYTHONIOENCODING: utf-8`, `PYTHONUTF8: 1`).
   - Add `action` argument to `p_mcp` with default `"serve"` and choices `["serve", "register"]`, routing `action == "register"` directly to `cmd_mcp_config`. Support `--json` and auto-detection.

Verify that all unit, integration, and E2E tests pass 100%:
`python3 -m unittest discover tests`
`python3 -m smart_drive mcp register --json`
Write your completion report to /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m23/handoff.md and notify parent via send_message when done.


## 2026-10-01T08:40:27Z
**Context**: Milestone M23 Orchestration Status Check
**Content**: Checking on the status of your tasks in `smart_drive/mcp/server.py` and `smart_drive/mcp/registrar.py`.
**Action**: Please report your current progress, any blockers or test results, and estimated completion.
