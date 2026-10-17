# AGENTS.md - Autonomous AI Coding Agent Manifest

Target: Autonomous AI Coding Agents (Antigravity 2.0, Claude Code, Codex, Cursor).
Workspace: External High-Speed SSD (exFAT, Allocation Unit: 512 KB).

## 1. Fast-Path Protocol (<10ms)
- Never traverse disk with recursive find or grep.
- Use built-in SQLite FTS5 search engine:
  ```bash
  smart-drive search "<query>"
  ```
- Search lessons and research with: `smart-drive search --kind slide|fact|capture "<query>"` (MCP `ssd_search` kind/content).

## 2. Hard Invariants & Zero Data Loss
- Standard taxonomies (01_AI_Models .. 06_Archives_Storage) must never be deleted.
- Never place symlinks or illegal Windows characters (`\ / : * ? " < > |`) on exFAT.
- Cluster slack prevention: bundle micro-files and avoid unignored build caches.
- Gitignore governance: run `smart-drive gitignore audit` before committing.

## 3. First-Time Setup (MCP ecosystem)
On a fresh clone or a new machine, run once from the repo root. No prompts; safe to repeat:
```bash
python3 -m smart_drive setup    # macOS / Linux
python -m smart_drive setup     # Windows
```
- Needs Node >= 22.4 and git. Fetches Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius and Mira into `~/.smart-drive/mcp`, registers them and SmartDrive with the installed coding agents (Claude Code, Antigravity, Cursor, Codex), then health-checks them.
- Check state without changing anything: `python3 -m smart_drive setup --check --json` (exit code 3 = setup needed). Preview: `--dry-run`. Offline: `--no-clone`.
- Afterwards restart the agent (new conversation) so it loads the new MCP servers.
