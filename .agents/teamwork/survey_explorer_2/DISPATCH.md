# DISPATCH: Survey Explorer 2 — MCP Server Optimization, Formatting & Zero-Dependency Codebase

## Mission
Investigate the MCP server tool outputs, token-efficient formatting, system descriptions, prompt hints for coding agents, AST handler isolation, JSON-RPC 2.0 stdio compliance, and zero-dependency compliance across codebase.

## Authoritative Inputs
- Read `ORIGINAL_REQUEST.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md` (specifically under `## 2026-10-01T07:40:41Z`)
- Rule files: `/Users/duongnad/Documents/tool/smart-drive-os/AGENTS.md`, `/Users/duongnad/Documents/tool/smart-drive-os/GEMINI.md`
- Codebase paths:
  - `smart_drive/mcp/server.py`
  - `smart_drive/mcp/`
  - `smart_drive/` (all modules)
  - `pyproject.toml`

## Scope of Investigation
1. Inspect MCP tool output schemas and payload formats for all tools (`ssd_search`, `ssd_audit`, `ssd_status`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_auto_organize`).
   - How are results currently returned?
   - How can we make them token-efficient, concise (<10ms latency), and prevent context window overflow when agents run search/audit? Check pagination, limits, summary stats, concise fields.
2. System Descriptions & Tool Prompts:
   - What are the current descriptions and prompt instructions in `server.py`?
   - How should we improve them so AI coding agents (Antigravity 2.0, Claude, Codex, Cursor) understand exactly when and why to use the FTS5 SQLite index instead of slow recursive disk scans (`find`, `grep`)?
3. Codebase audit for R3:
   - Verify 100% Python standard library (zero runtime pip dependencies, `pyproject.toml`).
   - Check AST handler structure, dead code, unused imports, JSON-RPC 2.0 stdio stream integrity (no spurious stdout print pollution breaking JSON-RPC parsing).
4. Propose precise, concrete implementation specifications and changes.

## Deliverable
Write your comprehensive report to `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_2/handoff.md`.
Report format: Observation, Logic Chain, Caveats, Conclusion, Verification Method.
Notify parent via `send_message` when done.

## 2026-10-01T07:42:40Z
[Message] timestamp=2026-10-01T07:42:40Z sender=49720693-a82c-49f8-8742-35eba7ba1b1f priority=MESSAGE_PRIORITY_HIGH content=You are Survey Explorer 2 (Spec Miner).
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_2
Your parent is: 49720693-a82c-49f8-8742-35eba7ba1b1f (Project Orchestrator)

MANDATORY FIRST STEP: Read the user request at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-10-01T07:40:41Z) and your dispatch file at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_2/DISPATCH.md.

Task:
1. Investigate `smart_drive/mcp/server.py` and all MCP tools (ssd_search, ssd_audit, ssd_status, ssd_clean, etc.).
2. Analyze output schemas to design token-efficient formatting, pagination/truncation metadata, and latency optimizations (<10ms) to prevent context window overflow for AI coding agents.
3. Review and upgrade System Descriptions & Tool Prompts in the MCP server so AI coding agents (Antigravity 2.0, Claude, Codex, Cursor) understand when and why to use FTS5 index queries instead of slow recursive disk scans (find, grep).
4. Inspect the codebase for R3: verify 100% Python standard library (zero runtime pip dependencies in pyproject.toml), AST handler isolation, dead code, and JSON-RPC 2.0 stdio stream protection against stdout corruption.
5. Write your full report to /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_2/handoff.md.
6. Notify parent via send_message when finished.
