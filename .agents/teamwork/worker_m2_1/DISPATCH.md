## 2026-09-29T16:54:07Z
You are the Worker agent for Milestone 2 (M2) of the SmartDrive-OS MCP Grade A upgrade.
Identity: worker_m2_1
Working directory: d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_m2_1
Parent: orchestrator_mcp_1 (conversation ID: 09e9f6f6-cea0-43b3-ba73-105ef2f87c01)

MANDATORY FIRST STEP:
Read the authoritative request file at:
d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md
Specifically study the latest request under section ## 2026-09-29T16:34:33Z.

Also read:
- d:\teamwork_projects\smart_drive_os\.agents\teamwork\orchestrator_mcp_1\SCOPE.md
- d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_mcp_2\handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

EXCLUSIVE WRITE OWNERSHIP:
You own:
- `smart_drive/mcp/server.py`
- `smart_drive/cli/cmd_mcp.py`
- `smart_drive/cli/main.py`
- `smart_drive/ui/server.py`
Do NOT touch files outside this scope.

YOUR MISSION (Milestone 2: Authentication Handshake & Network Loopback Isolation):
Implement the architecture specified in Explorer 2's handoff report:
1. Authentication Engine in `SmartDriveMCPServer`:
   - Accept `auth_token: Optional[str] = None` and `require_auth: Optional[bool] = None` in `__init__`.
   - Resolve `auth_token` from parameter > CLI > `SMART_DRIVE_MCP_AUTH_TOKEN` env var > None.
   - Resolve `require_auth` from parameter > CLI > `SMART_DRIVE_MCP_REQUIRE_AUTH` env var > `bool(self.auth_token)`.
   - In stdio mode, when neither token nor require_auth is provided, `require_auth = False` to guarantee zero-friction access for local AI agents (Antigravity, Claude, Cursor).
   - Implement `verify_token(token: Optional[str]) -> bool` using standard library `hmac.compare_digest`.
   - Support `auth/handshake` method with `params: {"token": "..."}`, returning `{"status": "authenticated", "authenticated": True}` and marking session authenticated.
   - Support handshake during `initialize` via `params.get("_meta", {}).get("authToken")` or `params.get("authToken")` or `params.get("token")`.
   - If `require_auth` is True and session is not authenticated, reject `tools/list` and `tools/call` with JSON-RPC error:
     `{"code": -32001, "message": "Authentication required: Missing or invalid authentication token", "data": {"authenticated": False}}`.
     Allow `initialize` and `auth/handshake` methods to be called before authentication.
2. CLI Flag Integration:
   - In `smart_drive/cli/main.py`, add `--auth-token` and `--require-auth` (flag or bool) to the `mcp` parser (`p_mcp`).
   - In `smart_drive/cli/cmd_mcp.py`, pass `auth_token` and `require_auth` to `SmartDriveMCPServer`.
3. Network Loopback Guard & Endpoint Isolation in `smart_drive/ui/server.py`:
   - In `create_server` and `run_server`, validate `host`. If `host not in ("127.0.0.1", "localhost")`, raise `ValueError("Security restriction: Network listening sockets must bind strictly to '127.0.0.1' loopback. Binding to external interfaces is prohibited.")`.
   - Normalize server URL generation to `url = f"http://127.0.0.1:{actual_port}"`.
   - In `_set_cors_headers`, restrict `Access-Control-Allow-Origin` to loopback origins (`127.0.0.1`, `localhost`) instead of `*`.
4. Verification:
   - Run `python -m unittest discover tests` — all 523 existing tests must pass with 0 regressions.
   - Ensure zero external pip dependencies (`dependencies = []`).
5. Write your completion report to `d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_m2_1\handoff.md`.
6. Send a concise completion message to parent using send_message.
