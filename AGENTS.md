# AGENTS.md - Autonomous AI Coding Agent Manifest

Target: Autonomous AI Coding Agents (Antigravity 2.0, Claude Code, Codex, GLM, Cursor, Windsurf).
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

## 4. Atomic Commit & Verification Discipline
Every AI agent working in this workspace must commit in small, verified steps:
- **One logical change per commit**: Keep each commit small and self-contained (`fix(scope): ...`, `feat(scope): ...`, `test(scope): ...`, `docs: ...`). Never bundle unrelated fixes or features into one commit.
- **Explain why, not just what**: State the root cause or invariant protected in the commit body.
- **Lock every fix with a regression test**: Add or update a deterministic test in the same commit as the code change, and run the test suite before committing so every commit passes on its own.
- **Audit before staging**: Run `smart-drive gitignore audit` and check `git status` / `git diff --staged` before committing so secrets, personal learning state (`.vega/`, `.polaris/`, `.sirius/`), and build/OS junk are never tracked.

## 5. Moving Folders Without Breaking Coding Agents
Coding agents remember projects and MCP servers by absolute path, and exFAT cannot leave a link at the old place:
- Move folders with `smart-drive organize --apply` (or `classify --apply`): it relinks the coding agents to the new places. Preview with `--dry-run`.
- After moving or renaming a folder by hand, run `smart-drive relink "<old path>" "<new path>"` (`--dry-run` previews). With no arguments it re-applies the latest organize/classify batch.
- Lost track of moved folders? `smart-drive relink --find` lists stale agent paths and where each folder went; `--find --apply --wait` relinks the sure matches once the agents are closed. A match marked `?` is only a guess (the folder it found may be another project, and the old one deleted): confirm it with the user, then run `smart-drive relink "<old>" "<new>"`. In a terminal, `--find --apply --ask` (menu option `[8]`) asks the user y/N for each `?` match: let the user answer, never answer for them.
- Close Claude desktop, Claude Code and Codex before relinking (or relink again after closing them), then restart the agents so they load the new paths.
