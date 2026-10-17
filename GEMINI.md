# GEMINI.md - SSD Workspace Guidelines & Directives

Environment: High-Speed External SSD (exFAT).
Allocation Unit: 524,288 bytes (512 KB).

## Automated SSD Health Check
- Run `smart-drive sentinel` or `smart-drive agent-check` to verify mount, shields, and index.
- Search lessons and research with `smart-drive search --kind slide|fact|capture "<query>"` (MCP ssd_search kind/content).
- Use `smart-drive clean` (Tier 1 safe by default, `--apply` required for purge).
- Use `smart-drive organize` for auto-zoning and anti-slack rebalancing.
- Run `smart-drive gitignore audit` before committing.

## MCP Ecosystem Setup
- First time on a machine: run `python3 -m smart_drive setup` (Windows: `python -m smart_drive setup`) to fetch and register the MCP ecosystem with the installed coding agents. `--dry-run` previews; `--check --json` reports state (exit code 3 = setup needed).
