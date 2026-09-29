# Plan: SmartDrive-OS MCP Grade A Upgrade

## Objectives
Elevate MCP Audit Score from Grade B (89/100) to Grade A (95-100/100) by addressing 5 core areas:
1. **R1**: Handler Isolation & 100% Static AST Resolvable Dispatch for all 8 MCP tools (`ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`) + Tool Description Accuracy.
2. **R2**: Authentication Handshake (token/key) for network/public transports + local loopback 127.0.0.1 endpoint isolation + zero-friction stdio compatibility.
3. **R3**: Domain Consistency & Packaging Metadata standardization (`pyproject.toml`, URLs, author, descriptors).
4. **R4**: Zero-dependency invariant (`dependencies = []`), exFAT safety, 523 existing tests pass with 0 regressions + new unit/integration tests for authentication, handler isolation, and network endpoint security.

## Phased Workflow
- **Phase 0: Comprehensive Survey (Parallel Explorers)**
  - Explorer 1: Inspect `smart_drive/mcp/server.py`, AST dispatch structure, handler isolation requirements, tool descriptions vs actual behavior.
  - Explorer 2: Inspect network endpoints, transport mechanisms (stdio, SSE/HTTP if any or public transports), auth handshake design, loopback binding.
  - Explorer 3: Inspect `pyproject.toml`, packaging metadata, domain consistency, and existing test suites (523 tests across 32 modules).
- **Phase 1: Architecture & Scope Finalization (SCOPE.md)**
  - Synthesize survey findings into unified architecture and interface contracts.
- **Phase 2: Milestone Iteration Loop (Explorer -> Worker -> Reviewers -> Challengers -> Auditor -> Gate)**
  - Milestone 1 (M1): AST Handler Isolation & Tool Description Accuracy
  - Milestone 2 (M2): Authentication Handshake & Network Loopback Isolation
  - Milestone 3 (M3): Packaging Metadata & Domain Consistency
  - Milestone 4 (M4): Test Suite Expansion & Adversarial Coverage Verification
- **Phase 3: Final Verification & Audit Gate**
  - Run full test suite (523 existing + new tests).
  - Reviewer + Challenger + Forensic Auditor verification.
- **Phase 4: Synthesis & Human Reporting**
  - Write handoff.md, report results to parent/user.
