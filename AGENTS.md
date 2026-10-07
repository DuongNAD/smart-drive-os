# AGENTS.md - Autonomous AI Coding Agent Manifest

Target: Autonomous AI Coding Agents (Antigravity 2.0, Claude Code, Codex, Cursor).
Workspace: External High-Speed SSD (exFAT, Allocation Unit: 512 KB).

## 1. Fast-Path Protocol (<10ms)
- Never traverse disk with recursive find or grep.
- Use built-in SQLite FTS5 search engine:
  ```bash
  smart-drive search "<query>"
  ```

## 2. Hard Invariants & Zero Data Loss
- Standard taxonomies (01_AI_Models .. 06_Archives_Storage) must never be deleted.
- Never place symlinks or illegal Windows characters (`\ / : * ? " < > |`) on exFAT.
- Cluster slack prevention: bundle micro-files and avoid unignored build caches.

## 3. Organizing a Drive
- Deterministic organization workflow:
  1. `ssd_survey`: Survey direct children, detect units, and calculate mess score.
  2. `ssd_plan_organize`: Generate proposed relocation plan (`zones` or `in_place`).
  3. Review plan with user and adjust if needed.
  4. `ssd_validate_moves`: Verify safety invariants and collision rules.
  5. `ssd_apply_moves(apply=false)`: Dry-run simulation preview.
  6. `ssd_apply_moves(apply=true)`: Journaled move execution ONLY after user confirmation.
  7. `ssd_undo_organize`: Safe transactional rollback via journal ID.
- Inviolable rules:
  - Files are NEVER renamed and NEVER deleted.
  - Apply ONLY after explicit user confirmation.
  - Atomic journaling and rollback via `.smart_drive/journal/`.
