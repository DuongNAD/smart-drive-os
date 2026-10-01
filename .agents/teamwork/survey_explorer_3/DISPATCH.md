# DISPATCH: Survey Explorer 3 — MCP Registrar & Portable Safe Launchers

## Mission
Investigate `smart_drive/mcp/registrar.py` multi-agent registration and design requirements for portable cross-platform distribution launchers (.bat, .ps1, .command, .sh) per R1 and R4.

## Authoritative Inputs
- Read `ORIGINAL_REQUEST.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md` (specifically under `## 2026-10-01T07:40:41Z`)
- Rule files: `/Users/duongnad/Documents/tool/smart-drive-os/AGENTS.md`, `/Users/duongnad/Documents/tool/smart-drive-os/GEMINI.md`
- Codebase paths:
  - `smart_drive/mcp/registrar.py`
  - `smart_drive/cli.py`
  - Existing scripts, launchers, or root files
  - Config paths:
    - Google Antigravity 2.0 (`~/.gemini/antigravity/mcp_config.json` or IDE settings)
    - Claude Desktop & Claude Code (`claude_desktop_config.json`)
    - OpenAI Codex / Cursor (`~/.cursor/mcp.json`)
    - Windsurf (`~/.codeium/windsurf/mcp_config.json`)
    - Local Workspace (`.mcp.json`)

## Scope of Investigation
1. Examine `smart_drive/mcp/registrar.py`:
   - What agents are currently supported?
   - How does auto-detection work?
   - What configuration formats and paths are needed for Antigravity 2.0, Claude, Cursor, Windsurf, and `.mcp.json`?
   - How should `python -m smart_drive mcp register` (or `smart-drive mcp register`) work seamlessly across OS platforms?
2. Portable Launchers (R4):
   - What launchers currently exist or need to be created?
   - Requirements for Windows (`.bat`, `.ps1`) and macOS/Linux (`.command`, `.sh`).
   - Self-checking environment logic (verifying Python 3.9+, verifying drive mount, exFAT safety checks, running tests or health-checks).
   - Ensuring complete isolation from personal user profiles or git credentials (preventing leakage or misconfiguration when copied to another machine or USB/SSD).
3. Propose exact architecture, code structure, and test cases for registrar and launchers.

## Deliverable
Write your comprehensive report to `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_3/handoff.md`.
Report format: Observation, Logic Chain, Caveats, Conclusion, Verification Method.
Notify parent via `send_message` when done.

## 2026-10-01T07:42:41Z
You are Survey Explorer 3.
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_3
Your parent is: 49720693-a82c-49f8-8742-35eba7ba1b1f (Project Orchestrator)

MANDATORY FIRST STEP: Read the user request at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-10-01T07:40:41Z) and your dispatch file at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_3/DISPATCH.md.

Task:
1. Investigate `smart_drive/mcp/registrar.py`: check existing agent registration support and design upgrades for 1-click auto-detection and registration for Google Antigravity 2.0 (~/.gemini/antigravity/mcp_config.json or IDE settings), Claude Desktop & Claude Code (claude_desktop_config.json), OpenAI Codex / Cursor (~/.cursor/mcp.json), Windsurf (~/.codeium/windsurf/mcp_config.json), and local workspace (.mcp.json).
2. Investigate R4 distribution requirements: design portable launchers (.bat, .ps1 for Windows; .command, .sh for macOS/Linux) that run standalone, perform self-environment checks (Python version, drive mount, exFAT safety), and operate completely isolated from personal git credentials/profile.
3. Write your full report to /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_3/handoff.md.
4. Notify parent via send_message when finished.

