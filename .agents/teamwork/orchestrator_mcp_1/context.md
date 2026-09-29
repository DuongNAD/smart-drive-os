# Context for Orchestrator (Milestone: MCP Grade A)

## Workspace
`d:\teamwork_projects\smart_drive_os`

## Working Directory
`d:\teamwork_projects\smart_drive_os\.agents\teamwork\orchestrator_mcp_1`

## Authoritative Request
`d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md` (see section `## 2026-09-29T16:34:33Z`)

## Hard Invariants
- 100% Python Standard Library zero-dependency invariant (`dependencies = []` in pyproject.toml).
- exFAT safety: 512KB cluster slack protection, whitelist immutability, no symlinks, no Windows illegal characters (`\ / : * ? " < > |`), no recursive disk grep/find.
- 523 existing tests must continue passing 100% with no regressions.
- All 8 MCP tools refactored to independent handlers with 100% static AST resolvable dispatch, tool description accuracy, local loopback 127.0.0.1 isolation, and authentication mechanism.
