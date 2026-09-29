# Original User Request

## 2026-09-29T13:39:47Z

Comprehensive enhancement of SmartDrive-OS to achieve top-tier directory trust compliance (OpenAI, Claude, M8ven), elevate Trust Index score, and harden MCP server defenses while maintaining 100% Python Standard Library zero-dependency architecture.

Working directory: d:\teamwork_projects\smart_drive_os
Integrity mode: development

## Requirements

### R1. Directory & Marketplace Compliance (OpenAI, Claude, M8ven)
Implement all missing compliance criteria highlighted by the M8ven audit:
- Create a comprehensive `PRIVACY.md` detailing strict data isolation: local-only drive operations, zero telemetry, zero logging of personal data, and zero external network transmission. Reference this in `README.md` and `README_VN.md`.
- Enrich `pyproject.toml` with full marketplace metadata: official homepage, documentation URL, GitHub issue tracker, PyPI classifiers, and keyword tags for optimal domain consistency.

### R2. MCP Server Defensive Hardening & In-Memory Rate Limiting
Harden the Model Context Protocol engine in `smart_drive/mcp/server.py`:
- Implement a pure Python Standard Library in-memory sliding-window or token-bucket rate limiter to protect MCP endpoints against accidental flood or DoS attacks.
- Ensure all 8 MCP tools enforce strict boundary checks and input sanitization on paths and query parameters.
- Verify that all 8 tools maintain explicit boolean declarations for `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`.

### R3. Comprehensive Test Verification & Zero-Dependency Invariant
- Expand the test suite (`tests/test_mcp_server.py` or new dedicated test module) to verify:
  1. Privacy policy structure and assertions.
  2. Rate limiting behavior (burst allowance, throttle trigger, window reset).
  3. Input sanitization boundaries on all 8 MCP tools.
- Maintain the hard project invariant: zero external runtime pip dependencies (100% Python Standard Library).

## Acceptance Criteria

### Directory Compliance
- [ ] `PRIVACY.md` exists at repository root, clearly articulating local-only storage, zero telemetry, and security guarantees.
- [ ] `README.md` and `README_VN.md` contain clickable links to `PRIVACY.md`.
- [ ] `pyproject.toml` contains `homepage`, `repository`, `documentation`, `issues`, `keywords`, and standard classifiers.

### Defensive Hardening
- [ ] In-memory rate limiting is integrated into `SmartDriveMCPServer` using only standard library modules (`time`, `collections`, `threading`).
- [ ] Rate limiting is configurable via parameters or environment variables, allowing normal operation without throttling standard agent workflows.
- [ ] All 8 MCP tools contain valid annotations and boundary protections.

### Automated Verification & Integrity
- [ ] All existing and new tests pass cleanly with `pytest` (100% pass rate).
- [ ] `pyproject.toml` runtime dependencies remain strictly empty (`dependencies = []`).
- [ ] No regression on SSD safety rules (512KB cluster slack protection, whitelist immutability, illegal character prevention).
