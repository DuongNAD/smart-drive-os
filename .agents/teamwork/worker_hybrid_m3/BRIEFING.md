# BRIEFING — 2026-10-01T11:54:00Z

## Mission
Implement R3 profile 'workstation-hybrid', AcademicClassifier, AutoZoner integration, launcher updates, and comprehensive test suite.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/worker_hybrid_m3
- Original parent: 7b5524b5-f368-4c9e-9c61-3310d53f6752
- Milestone: M3 (Workstation Hybrid & Academic Classifier)

## 🔒 Key Constraints
- Exclusive file write ownership strictly:
  * smart_drive/core/academic_classifier.py
  * smart_drive/core/initializer.py
  * smart_drive/cli/cmd_init.py
  * smart_drive/cli/main.py (for --profile choices only)
  * smart_drive/core/auto_zoner.py
  * SmartDrive.bat, Setup_SSD.bat, SmartDrive.ps1, Setup_SSD.ps1 (and mirrors in launchers/)
  * tests/test_academic_classifier.py
  * tests/test_workstation_hybrid.py
- Do not implement self-path-check in main.py (belongs to M4).
- Maintain real state and logic (Zero Cheating / Integrity Mandate).
- All tests in unittest discover tests must pass cleanly.

## Current Parent
- Conversation ID: 7b5524b5-f368-4c9e-9c61-3310d53f6752
- Updated: not yet

## Task Summary
- **What to build**: AcademicClassifier with FPTU curriculum mapping, regex, mojibake decoder, personal books/tools routing; 'workstation-hybrid' profile in initializer and profile marker `.smart_drive/profile.json`; AutoZoner integration; launchers update; test suite.
- **Success criteria**: All existing and new tests pass, zero regressions, all constraints met.
- **Interface contracts**: PROJECT.md & explorer_survey_hybrid_3/report.md
- **Code layout**: smart_drive/core, smart_drive/cli, launchers, tests

## Key Decisions Made
- [TBD]

## Artifact Index
- DISPATCH.md — Assignment instructions
- progress.md — Liveness heartbeat & progress log
- handoff.md — Final handoff report

## Change Tracker
- **Files modified**: None yet
- **Build status**: Untested
- **Pending issues**: None

## Quality Status
- **Build/test result**: Not run yet
- **Lint status**: Clean
- **Tests added/modified**: Pending

## Loaded Skills
- None
