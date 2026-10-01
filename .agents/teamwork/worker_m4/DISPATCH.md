# DISPATCH: Worker M4 — Portable Launchers & Distribution Suite

## Working Directory
`/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m4`

## Parent
`49720693-a82c-49f8-8742-35eba7ba1b1f` (Project Orchestrator)

## Mandatory Inputs & Rules
- Read `ORIGINAL_REQUEST.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md` (specifically under `## 2026-10-01T07:40:41Z`)
- Read Explorer 3 handoff: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_3/handoff.md` (Section 4.3)
- Read `PROJECT.md`: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/orchestrator_2/PROJECT.md`
- Code Rules: `/Users/duongnad/Documents/tool/smart-drive-os/AGENTS.md`, `/Users/duongnad/Documents/tool/smart-drive-os/GEMINI.md`

## MANDATORY INTEGRITY WARNING
DO NOT CHEAT. All launcher scripts must be functional, genuine, and safe. DO NOT hardcode machine-specific paths or personal usernames. A teamwork_preview_auditor will independently verify your work.

## Write Ownership (Exclusively Yours)
- `launchers/` directory:
  - `Setup_SSD.bat`, `Setup_SSD.ps1`, `Setup_SSD.command`, `Setup_SSD.sh`
  - `Quick_Audit.bat`, `Quick_Audit.ps1`, `Quick_Audit.command`, `Quick_Audit.sh`
  - `Quick_Clean.bat`, `Quick_Clean.ps1`, `Quick_Clean.command`, `Quick_Clean.sh`
  - `Quick_Search.bat`, `Quick_Search.ps1`, `Quick_Search.command`, `Quick_Search.sh`
  - `SmartDrive.bat`, `SmartDrive.ps1`, `SmartDrive.command`, `SmartDrive.sh` (Master 1-Touch Menu)
- Root repository launcher mirrors:
  - `Setup_SSD.bat`, `Setup_SSD.ps1`, `Setup_SSD.command`, `Setup_SSD.sh`
  - `Quick_Audit.bat`, `Quick_Audit.ps1`, `Quick_Audit.command`, `Quick_Audit.sh`
  - `Quick_Clean.bat`, `Quick_Clean.ps1`, `Quick_Clean.command`, `Quick_Clean.sh`
  - `Quick_Search.bat`, `Quick_Search.ps1`, `Quick_Search.command`, `Quick_Search.sh`
  - `SmartDrive.bat`, `SmartDrive.ps1`, `SmartDrive.command`, `SmartDrive.sh`

## Implementation Tasks (R4)
Implement the 5-Tier Self-Environment Check in all scripts (.bat, .ps1, .command, .sh):
1. **Tier 1: Directory & Root Resolution**:
   - Detect script directory (`%~dp0`, `$PSScriptRoot`, `dirname "${BASH_SOURCE[0]}"`).
   - If running from `launchers/`, resolve parent directory as `DRIVE_ROOT`.
2. **Tier 2: Python 3.9+ Discovery & Version Check**:
   - Check `python3`, `py -3`, `python`. Verify `sys.version_info >= (3, 9)`.
   - If missing, display a clear, user-friendly error banner instructing how to install Python 3.9+ without external pip packages, and pause before exit.
3. **Tier 3: exFAT Optimization & Host Isolation**:
   - `PYTHONIOENCODING=utf-8` & `PYTHONUTF8=1` (UTF-8 console/pipe).
   - `PYTHONDONTWRITEBYTECODE=1` (CRITICAL: prevents `.pyc` caching micro-file cluster slack explosion on 512KB exFAT SSD).
   - `GIT_TERMINAL_PROMPT=0` & `GIT_CONFIG_NOSYSTEM=1` (complete isolation from host git credentials/prompts).
   - `SMART_DRIVE_ROOT=<DRIVE_ROOT>` & `PYTHONPATH=<DRIVE_ROOT>:<PYTHONPATH>`.
4. **Tier 4: Execution**:
   - Respective command invocation: `setup` / `init`, `audit`, `clean`, `search`, or Master Menu in `SmartDrive.*`.
   - Master 1-Touch Menu options:
     - `[1] Initialize Drive & Presets (smart-drive init)`
     - `[2] Sentinel & Integrity Health Check (smart-drive sentinel)`
     - `[3] SQLite FTS5 File Search (smart-drive search)`
     - `[4] Storage & Cluster Slack Audit (smart-drive audit)`
     - `[5] Safe Junk Cleaner (smart-drive clean)`
     - `[6] Auto-Zoning & Anti-Slack Rebalancer (smart-drive organize)`
     - `[7] Register AI Coding Agents MCP (smart-drive mcp register)`
     - `[8] Launch Web Dashboard UI (smart-drive ui)`
     - `[0] Exit`
5. **Tier 5: Safe Pause on Exit**:
   - Ensure the terminal window stays open on double-click so the user can read results.

## Verification Requirements
- Syntax check for shell scripts: `bash -n launchers/*.sh launchers/*.command *.command *.sh`.
- Execute a sample script check.
- Run test suite: `python3 -m unittest tests/test_e2e_mcp_distribution.py`.
- Document commands and outcomes in `handoff.md`.

## Deliverable
Write your completion report to `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m4/handoff.md`.
Notify parent via `send_message` when done.

## 2026-10-01T08:18:27Z
You are Worker M4.
Your working directory is: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m4
Your parent is: 49720693-a82c-49f8-8742-35eba7ba1b1f (Project Orchestrator)

MANDATORY FIRST STEP: Read the user request at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/ORIGINAL_REQUEST.md (under ## 2026-10-01T07:40:41Z) and your dispatch file at /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m4/DISPATCH.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All launcher scripts must be functional, genuine, and safe. DO NOT hardcode machine-specific paths or personal usernames. A teamwork_preview_auditor will independently verify your work.

Your task (R4):
1. In `launchers/` and root repository:
   - Provide the complete portable launcher matrix (.bat, .ps1, .command, .sh) for:
     `Setup_SSD`, `Quick_Audit`, `Quick_Clean`, `Quick_Search`, and `SmartDrive` (Master 1-Touch Menu).
2. Implement the standard 5-Tier Self-Environment Check:
   - Tier 1: Directory & Root Resolution (relative to launcher location or parent drive root).
   - Tier 2: Python 3.9+ Discovery & Version Check (`sys.version_info >= (3, 9)`), displaying clean error banner if missing.
   - Tier 3: exFAT Optimization & Host Isolation (`PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1` cluster slack defense, `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, `SMART_DRIVE_ROOT`, `PYTHONPATH`).
   - Tier 4: Respective execution (or 1-touch interactive menu for `SmartDrive.*`).
   - Tier 5: Safe Pause on exit to keep terminal open on double-click.

Verify shell script syntax (`bash -n launchers/*.sh launchers/*.command *.command *.sh`) and test suite compatibility.
Write your completion report to /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_m4/handoff.md and notify parent via send_message when done.
