# CLAUDE.md - AI Coding Instructions for SSD Workspace

- Filesystem: exFAT (512KB clusters).
- Avoid loose micro-files to minimize cluster slack waste.
- Search files using `smart-drive search "<query>"`.
- Verify SSD health using `smart-drive sentinel`.

## Organizing a Drive
- Safe organization workflow:
  `ssd_survey` -> `ssd_plan_organize` -> review with user -> `ssd_validate_moves` -> `ssd_apply_moves(apply=false)` preview -> `ssd_apply_moves(apply=true)` upon confirmation -> `ssd_undo_organize` for rollback.
- Rules:
  - Files are NEVER renamed and NEVER deleted.
  - Apply ONLY after explicit user confirmation.
  - Full rollback supported via atomic journal log.
