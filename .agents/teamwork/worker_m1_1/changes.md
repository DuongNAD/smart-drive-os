# Changes Report: Milestone 1 — Directory & Marketplace Compliance

**Worker**: Worker M1 (Directory & Marketplace Compliance)  
**Date**: 2026-09-29  
**Status**: Completed  
**Test Suite**: 436/436 tests passed (100% success rate, 0 regressions)

---

## 1. Summary of Modifications

Milestone 1 implemented full ecosystem directory and marketplace trust compliance (M8ven, OpenAI GPT Store/Actions, Anthropic Claude Tools) for SmartDrive-OS. All changes maintain the hard invariant: **100% Python Standard Library execution with zero runtime pip dependencies (`dependencies = []`)**.

| File | Change Type | Description |
|---|---|---|
| `PRIVACY.md` | Created | Comprehensive privacy policy and security architecture document at repository root. |
| `smart_drive/core/config.py` | Modified | Added `"privacy.md"` to `PROTECTED_ROOT_FILES` to prevent accidental deletion by cleanup commands. |
| `README.md` | Modified | Added `Privacy: 100% Local` shield badge and dedicated `Privacy, Security & Data Isolation` section linking to `[PRIVACY.md](PRIVACY.md)`. |
| `README_VN.md` | Modified | Added `Privacy: 100% Local` shield badge, native Vietnamese `Bảo Mật & Quyền Riêng Tư Dữ Liệu` section linking to `[PRIVACY.md](PRIVACY.md)`, and renumbered subsequent sections cleanly. |
| `pyproject.toml` | Modified | Added author contact email (`smartdrive.os@proton.me`), updated `[project.urls]` to authoritative repo `https://github.com/DuongNAD/smart-drive-os` (Homepage, Documentation, Repository, Issues, Changelog), expanded `keywords` to 22 relevant terms, and enriched `classifiers` to 25 standard Trove classifiers while keeping `dependencies = []`. |

---

## 2. Detailed File Diffs and Specifications

### 2.1 `PRIVACY.md` (New File at Repository Root)
- **Executive Summary & Core Pledge**: Declares 100% Local-Only Operation, Zero Telemetry, Zero PII Logging, Zero External Network Transmission, and Air-Gap Readiness.
- **Core Privacy Guarantees**:
  - Local-only storage and processing across all filesystem operations (slack analysis, FTS5 search, SHA-256 snapshotting).
  - Absolute zero telemetry, analytics SDKs, phone-home mechanisms, or diagnostic pingbacks.
  - Zero PII logging: index engine stores solely filesystem metadata (`rel_path`, `name`, `extension`, `category`, `size_bytes`, `allocated_bytes`, `mtime`) without parsing user source code or credential contents.
  - Air-gap readiness with zero outbound network sockets; Web UI loopback interface binding restricted to `127.0.0.1:8765`.
- **Security Architecture & Data Handling Model**:
  - exFAT 512KB cluster slack protection geometry modeling.
  - Inviolable whitelist protecting root manifests (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`) and standard taxonomy partitions.
  - 100% data sovereignty: deleting `.smart_drive/` permanently purges all indexes.
- **Model Context Protocol (MCP) Trust & Safety**:
  - Local stdio JSON-RPC 2.0 communication.
  - Explicit boolean hint declarations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
  - Defensive input sanitization and in-memory rate limiting against automated loop DoS.
- **Directory Compliance**:
  - Conforms to OpenAI GPT Store / Actions privacy policy requirements.
  - Conforms to Anthropic Claude Desktop & Code least-privilege tool guidelines.
  - Meets all M8ven MCP Directory Trust Index audit requirements.
- **Vulnerability Reporting**:
  - Clear reporting guidelines via GitHub Issues and direct security email `smartdrive.os@proton.me`.

### 2.2 `smart_drive/core/config.py`
- Added `"privacy.md"` to `PROTECTED_ROOT_FILES` frozenset.
- Verified that `is_protected_root_file("PRIVACY.md")` returns `True`.

### 2.3 `README.md`
- Added badge:
  `[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local-success?style=flat-square&logo=shield)](PRIVACY.md)`
- Added section `## Privacy, Security & Data Isolation` before `## Contributing` with explicit link to `[PRIVACY.md](PRIVACY.md)`.

### 2.4 `README_VN.md`
- Added badge:
  `[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local-success?style=flat-square&logo=shield)](PRIVACY.md)`
- Added section `## 11. Bảo Mật & Quyền Riêng Tư Dữ Liệu (Privacy & Security)` with explicit link to `[PRIVACY.md](PRIVACY.md)`.
- Renumbered subsequent sections cleanly (`11 -> 12. Đóng góp mã nguồn mở`, `12 -> 13. Giấy phép mã nguồn mở`).

### 2.5 `pyproject.toml`
- Set `authors = [{ name = "SmartDrive Team", email = "smartdrive.os@proton.me" }]`.
- Updated `[project.urls]`:
  ```toml
  Homepage = "https://github.com/DuongNAD/smart-drive-os"
  Documentation = "https://github.com/DuongNAD/smart-drive-os#readme"
  Repository = "https://github.com/DuongNAD/smart-drive-os"
  Issues = "https://github.com/DuongNAD/smart-drive-os/issues"
  Changelog = "https://github.com/DuongNAD/smart-drive-os/releases"
  ```
- Expanded `keywords` from 9 to 22 terms.
- Enriched `classifiers` from 18 to 25 Trove classifiers.
- Preserved hard invariant: `dependencies = []`.

---

## 3. Verification Commands & Results

1. **Root File Protection Test**:
   ```bash
   python -c "from smart_drive.core.config import is_protected_root_file, PROTECTED_ROOT_FILES; print('privacy.md in set:', 'privacy.md' in PROTECTED_ROOT_FILES); print('is_protected PRIVACY.md:', is_protected_root_file('PRIVACY.md'))"
   ```
   *Result*: Both printed `True`.

2. **`pyproject.toml` Parsing & Invariant Validation**:
   ```bash
   python -c "import tomllib; f = open('pyproject.toml', 'rb'); data = tomllib.load(f); print(data['project']['name'], len(data['project']['keywords']), len(data['project']['classifiers']), data['project']['dependencies'])"
   ```
   *Result*: `smart-drive-os 22 25 []`

3. **Full Test Suite Execution**:
   ```bash
   python -m unittest discover tests
   ```
   *Result*: `Ran 436 tests in 41.374s. OK.` (0 failures, 0 errors, 100% pass rate).
