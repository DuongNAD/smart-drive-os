# GEMINI.md - SSD Workspace Guidelines & Directives

Environment: High-Speed External SSD (exFAT).
Allocation Unit: 524,288 bytes (512 KB).

## Automated SSD Health Check
- Run `smart-drive sentinel` or `smart-drive agent-check` to verify mount, shields, and index; run `smart-drive checkup` for a 1-screen `0–100` health score and next steps, or `smart-drive audit --html` for an offline HTML treemap.
- Search lessons and research with `smart-drive search --kind slide|fact|capture "<query>"` (MCP ssd_search kind/content).
- Use `smart-drive clean` (Tier 1 safe by default, `--apply` moves junk to reversible `.smart_drive/quarantine/`; restore with `--restore latest`, list with `--list-quarantine`).
- Use `smart-drive organize` (or `Smart_Organize.*`) for auto-zoning and anti-slack rebalancing (`protected_by_system` pins folders referenced by Windows `PATH`, Services, Installed Apps, Startup, `.lnk`, and game libraries); revert the latest move batch with `smart-drive undo --apply`.
- After moving a folder by hand, run `smart-drive relink "<old>" "<new>"` so coding agents, IDEs, Obsidian, and `.lnk` shortcuts follow; `organize --apply` does this itself (`--dry-run` previews). Use `smart-drive paths --broken` (`--fix`) to inspect or repair broken shortcuts and IDE entries.
- Run `smart-drive gitignore audit` before committing.

## MCP Ecosystem Setup
- First time on a machine: run `python3 -m smart_drive setup` (Windows: `python -m smart_drive setup` or `py -m smart_drive setup` when `python` is not on `PATH`) to fetch and register the MCP ecosystem with the installed coding agents. `--dry-run` previews; `--check --json` reports state (exit code 3 = setup needed).

## Atomic Commit & Verification Discipline
- Make small, self-contained commits (one logical fix or feature per commit: `fix(scope): ...`, `feat(scope): ...`).
- Include a regression test in the same commit and run the test suite before committing.
- Explain the root cause or invariant in the commit body, and verify `git diff --staged` contains no secrets, personal learning state, or exFAT junk.
