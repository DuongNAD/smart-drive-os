# Privacy Policy & Security Architecture — SmartDrive-OS

**Effective Date:** September 2026  
**Authoritative Repository:** [https://github.com/DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)  
**License:** MIT License  
**Contact:** [smartdrive.os@proton.me](mailto:smartdrive.os@proton.me) | [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues)

---

## 1. Executive Summary & Core Pledge

**SmartDrive-OS** is an open-source, local-first storage management and governance suite engineered specifically for external SSDs (exFAT 512KB cluster slack optimization), secondary NVMe/SATA internal drives, developers, and autonomous AI coding agents (Anthropic Claude Code / Desktop, Google Antigravity, Cursor IDE, Windsurf).

Our core architectural pledge is absolute:
> **100% Local-Only Operation • Zero Telemetry • Zero PII Logging • Zero External Network Transmission • Air-Gap Ready**

We believe that developer workspaces, proprietary source code, AI model checkpoints, and personal files must remain strictly under your sovereign control. SmartDrive-OS contains **zero third-party trackers, zero telemetry beacons, zero analytics collectors, and zero background cloud-sync daemons**.

---

## 2. Core Privacy Guarantees

### 2.1 100% Local-Only Storage & Execution
All filesystem operations performed by SmartDrive-OS—including directory auditing, 512KB cluster slack calculation, file type classification, SHA-256 snapshot hashing, junk file detection, and SQLite FTS5 search indexing—occur **exclusively on the local machine and attached drive volumes**. No file contents, directory structures, hashes, or metadata are ever uploaded, mirrored, or staged on external cloud services or remote servers.

### 2.2 Zero Telemetry & Analytics
SmartDrive-OS adheres to an uncompromising zero-telemetry policy:
- **Zero Analytics SDKs**: The repository does not include or depend on PostHog, Mixpanel, Google Analytics, Sentry, Segment, or any other telemetry framework.
- **Zero Phone-Home / Pingbacks**: The software never checks in with remote servers, never transmits usage statistics, never measures command frequency, and never reports crash traces externally.
- **Zero Advertising or Profiling**: No device fingerprinting, user profiling, or advertising trackers exist anywhere in the code.

### 2.3 Zero Personally Identifiable Information (PII) Logging
SmartDrive-OS does not inspect, parse, harvest, or log sensitive personal data:
- **No Content Scraping**: The indexing engine indexes strictly filesystem metadata (`rel_path`, `name`, `extension`, `category`, `size_bytes`, `allocated_bytes`, `mtime`). It never reads or parses the textual content of source files, documents, private keys, SSH tokens, credentials, or `.env` environment files.
- **Local SQLite FTS5 Database**: All indexed metadata is stored in a standard local SQLite database located at `.smart_drive/index.db` directly on the target drive volume. It is never mirrored off-disk.
- **Terminal Diagnostics**: All diagnostic logs and error traces are printed strictly to local `stderr` or local console streams.

### 2.4 Zero External Network Transmission & Air-Gap Readiness
SmartDrive-OS is designed for mission-critical, air-gapped workstations and restricted offline corporate environments:
- **Zero Outbound Sockets**: SmartDrive-OS makes **zero outbound HTTP/HTTPS, TCP, or UDP socket connections**.
- **Localhost Loopback Only**: The embedded visual Web Dashboard (`smart-drive ui`) binds strictly and exclusively to the local loopback interface `127.0.0.1:8765` (`localhost`). It is mathematically and architecturally unreachable from external network interfaces or remote networks. It also refuses any request that carries a foreign `Host` or `Origin` header, or that the browser labels as coming from another site (`Sec-Fetch-Site`), so a web page open in your own browser cannot read from it, drive it, or make it scan your drive.
- **Zero External Runtime Dependencies**: SmartDrive-OS is built with **100% Python Standard Library** modules (`http.server`, `sqlite3`, `hashlib`, `json`, `pathlib`, `collections`, `threading`, `time`). The project invariant `dependencies = []` guarantees zero supply-chain attack surfaces and zero transitive dependency network calls.

---

## 3. Security Architecture & Data Handling Model

### 3.1 exFAT 512KB Cluster Slack Protection
High-capacity external SSDs (such as the Kingston XS2000 2TB) formatted with exFAT allocate physical storage in 512KB (524,288 bytes) blocks. SmartDrive-OS accurately models this geometry to expose wasted cluster slack without altering file contents. File categorization and anti-slack rebalancing operate locally with dry-run verification defaults.

### 3.2 Inviolable Whitelist Engine
To prevent catastrophic accidental data loss during automated or manual cleanups, SmartDrive-OS enforces a hardcoded, immutable whitelist across all purge paths:
- **Protected Root Manifests**: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`, `.mcp.json`, and system scripts (`clean_mac_junk.*`, `check_ssd_status.*`, `setup_*`, `quick_*`) can never be deleted or purged, even if `--apply` or `--force` is supplied.
- **Protected Root Taxonomies**: Standard corporate taxonomy partitions (`01_AI_Models` through `06_Archives_Storage`, `.agents`, `teamwork_projects`, `smart_ssd_workspace`) are permanently shielded from deletion.
- **Anti-Indexing Markers**: `.metadata_never_index` and `.fseventsd/no_log` are immutably protected to keep OS indexing daemons from degrading SSD health.

### 3.3 Data Sovereignty & User Control
- **Data Location**: All index databases, configuration states, and point-in-time snapshot manifests are contained entirely within the root-level hidden directory `.smart_drive/` on the managed volume.
- **Instant Purge**: You retain 100% data sovereignty. Deleting the `.smart_drive/` folder permanently removes all local index records and snapshot manifests without leaving residual traces or orphaned registry entries on your host OS.

---

## 4. Model Context Protocol (MCP) Trust & Safety

SmartDrive-OS exposes an integrated **Model Context Protocol (MCP)** server (`smart-drive mcp-server`) enabling AI coding assistants (Anthropic Claude Desktop, Claude Code, Google Antigravity, Cursor IDE, Windsurf) to govern workspace storage safely.

### 4.1 Local Standard I/O (stdio) Transport
The MCP server operates strictly over standard input/output (`stdio`) using JSON-RPC 2.0. No network ports or sockets are opened for MCP communication.

### 4.2 Explicit Tool Safety Hints
In compliance with the Anthropic Model Context Protocol specification, all 8 MCP tools declare explicit safety metadata:
- **`readOnlyHint`**: Explicitly set to `true` on non-modifying diagnostic tools (`ssd_status`, `ssd_audit`, `ssd_search`, `ssd_find_duplicates`, `ssd_check_safety`), and `false` on modifying tools (`ssd_clean`, `ssd_auto_organize`, `ssd_snapshot_create`).
- **`destructiveHint`**: Explicitly set to `true` on irreversible deletion tools (`ssd_clean`), and `false` on safe tools.
- **`idempotentHint`**: Explicitly declared across all tools to prevent unexpected side effects on repeated invocations.
- **`openWorldHint`**: Explicitly set to `false` for all tools, certifying that tools operate exclusively within the bounded local storage domain and do not access open-world external network resources.

### 4.3 Defensive Sanitization & Rate Limiting
- **Boundary Checks & Path Traversal Prevention**: Every tool strictly resolves and validates paths within the drive boundary (`_resolve_safe_path`), rejecting directory escapes (`..`), Windows cross-drive jumps, and null bytes (`\0`).
- **In-Memory Rate Limiting**: A pure Python standard library sliding-window rate limiter prevents runaway AI agent loops and DoS resource exhaustion.

---

## 5. Ecosystem & Marketplace Directory Compliance

### 5.1 OpenAI GPT Store & Actions Compliance
- **Public Privacy Policy**: This document serves as the official privacy policy required for OpenAI Custom GPTs and Actions integrations.
- **Data Use Disclosure**: SmartDrive-OS does **not** use, collect, retain, or share user data to train machine learning models.

### 5.2 Anthropic Claude Desktop & Tools Compliance
- SmartDrive-OS conforms to Anthropic's least-privilege tool guidelines.
- Destructive operations require explicit user approval (or `--apply` confirmation flags).
- All tool input schemas enforce strict type validation and bounded parameter values.

### 5.3 M8ven MCP Directory & Trust Index Compliance
- **Trust Index**: Verified 100% Python Standard Library execution with zero runtime pip dependencies (`dependencies = []`).
- **No Hidden Network Calls**: Audited and certified for zero external outbound communication.
- **Transparency**: Fully open-source under the MIT license, with full test coverage (523+ tests passing at 100%).

---

## 6. Security Vulnerability Reporting

We treat the security and privacy of developer workspaces with paramount seriousness. If you discover a potential vulnerability, bug, or security concern in SmartDrive-OS:

1. **GitHub Issues**: Open a ticket at [https://github.com/DuongNAD/smart-drive-os/issues](https://github.com/DuongNAD/smart-drive-os/issues) (for non-sensitive bug reports).
2. **Direct Security Contact**: For sensitive security disclosures, email [smartdrive.os@proton.me](mailto:smartdrive.os@proton.me).
3. **Response SLA**: We commit to acknowledging reported security issues within 48 hours and delivering patches in subsequent maintenance releases.

---

*SmartDrive-OS is committed to open, transparent, and strictly local-first storage governance.*
