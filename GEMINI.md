# GEMINI.md - SSD Workspace Guidelines & Directives

Environment: High-Speed External SSD (exFAT).
Allocation Unit: 524,288 bytes (512 KB).

## Automated SSD Health Check
- Run `smart-drive sentinel` or `smart-drive agent-check` to verify mount, shields, and index.
- Use `smart-drive clean` (Tier 1 safe by default, `--apply` required for purge).
- Use `smart-drive organize` for auto-zoning and anti-slack rebalancing.
