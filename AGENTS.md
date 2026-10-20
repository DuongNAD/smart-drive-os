# AGENTS.md - Autonomous AI Coding Agent Manifest

Target: Autonomous AI Coding Agents (Antigravity 2.0, Claude Code, Codex, GLM, Cursor, Windsurf).
Workspace: External High-Speed SSD. exFAT with a 512 KB allocation unit is the design target; this folder may sit on another filesystem, so run `smart-drive status` to see the real one.

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
On a fresh clone or a new machine, run once from the repo root. It installs nothing until the user agrees; safe to repeat:
```bash
python3 -m smart_drive setup --json    # macOS / Linux
python -m smart_drive setup --json     # Windows (or `py -m smart_drive setup --json` when `python` is not on PATH)
```
- It exits 4 and lists the MCP servers (`consent` in the JSON: name, repo, what each does, `agents`). Show the user each server (name + what it does + the coding agents it would be added to) with your own question/confirmation prompt, ask which to install or add, then run `python -m smart_drive setup --only <approved,names> --yes`. Never pass `--yes` without the user's answer. If the user declines, stop. A server that is already downloaded is listed too while it would be added to an agent's config. Constellation (the control plane) is installed together with any server.
- Needs Node >= 22.4 and git (exit code 2 when one is missing). Once approved it fetches Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius and Mira (as chosen) into `~/.smart-drive/mcp`, registers them and SmartDrive with the installed coding agents (Claude Code, Antigravity, Cursor, Codex), then health-checks them.
- Check state without changing anything: `python3 -m smart_drive setup --check --json` (exit code 3 = setup needed). Preview: `--dry-run`. Offline: `--no-clone`.
- Afterwards restart the agent (new conversation) so it loads the new MCP servers.

## 4. Atomic Commit & Verification Discipline
Every AI agent working in this workspace must commit in small, verified steps:
- **One logical change per commit**: Keep each commit small and self-contained (`fix(scope): ...`, `feat(scope): ...`, `test(scope): ...`, `docs: ...`). Never bundle unrelated fixes or features into one commit.
- **Explain why, not just what**: State the root cause or invariant protected in the commit body.
- **Lock every fix with a regression test**: Add or update a deterministic test in the same commit as the code change, and run the test suite before committing so every commit passes on its own.
- **Audit before staging**: Run `smart-drive gitignore audit` and check `git status` / `git diff --staged` before committing so secrets, personal learning state (`.vega/`, `.polaris/`, `.sirius/`), and build/OS junk are never tracked.

## 5. Moving Folders Without Breaking Coding Agents
Coding agents, IDEs, and shortcuts remember projects and MCP servers by absolute path, and exFAT cannot leave a link at the old place:
- Move folders with `smart-drive organize --apply` (or `Smart_Organize.*`): it skips system-referenced folders (`protected_by_system`: `PATH`, Services, Installed Apps, Startup, `.lnk`, game libraries) and relinks coding agents, VS Code/JetBrains/Obsidian, and `.lnk` shortcuts to the new places. Preview with `--dry-run`. `classify --apply` (sorting by file content) relinks the same way but moves only the files directly in the scanned folder; `--recursive` also looks inside subfolders, yet never takes apart an app, library, or project folder (programs, launcher scripts, manifests, `.git`): those are listed as `skipped_bundles`.
- Revert the latest move batch and relink back to original paths with `smart-drive undo --apply` (`--dry-run` default).
- After moving or renaming a folder by hand, run `smart-drive relink "<old path>" "<new path>"` (`--dry-run` previews). With no arguments it re-applies the latest organize/classify batch.
- Lost track of moved folders or broken shortcuts/IDE entries? `smart-drive relink --find` and `smart-drive paths --broken` list stale paths and where each folder went; `relink --find --apply --wait` or `paths --broken --fix` repairs the sure matches once open apps are closed. A match marked `?` is only a guess (the folder it found may be another project, and the old one deleted): confirm it with the user, then run `smart-drive relink "<old>" "<new>"`. In a terminal, `--find --apply --ask` (menu option `[8]`) asks the user y/N for each `?` match: let the user answer, never answer for them.
- Run `smart-drive checkup` for a 1-screen health score (`0-100`), `smart-drive audit --html` for an offline HTML treemap, and `smart-drive clean --list-quarantine` / `--restore latest` to inspect or restore quarantined Tier 3 junk (`clean --apply` deletes Tier 1-2 junk at once).
- Close Claude desktop, Claude Code, Codex, and open IDEs before relinking (or relink again after closing them), then restart the agents so they load the new paths.
