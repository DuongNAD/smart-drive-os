# CLAUDE.md - AI Coding Instructions for SSD Workspace

- Filesystem: exFAT (512KB clusters).
- Avoid loose micro-files to minimize cluster slack waste.
- Search files using `smart-drive search "<query>"`.
- Search lessons and research with `smart-drive search --kind slide|fact|capture "<query>"` (MCP ssd_search kind/content).
- Verify SSD health using `smart-drive sentinel` or `smart-drive checkup` (`smart-drive audit --html` exports an offline HTML treemap).
- Move folders with `smart-drive organize --apply` (or `Smart_Organize.*`, which skips `protected_by_system` folders and relinks agents/IDEs/shortcuts); revert the latest move batch with `smart-drive undo --apply`. After moving a folder by hand, run `smart-drive relink "<old>" "<new>"` (`--dry-run` previews) or `smart-drive paths --broken` (`--fix`).
- Clean junk safely with `smart-drive clean` (`--apply` quarantines by default; `--list-quarantine` and `--restore latest` inspect/restore).
- Run `smart-drive gitignore audit` before committing.
- Commit in small, atomic steps: one logical fix or feature per commit (`fix(scope): ...`, `feat(scope): ...`), paired with a regression test in the same commit, and run the test suite before committing.
- First time on a machine: run `python3 -m smart_drive setup` (Windows: `python -m smart_drive setup` or `py -m smart_drive setup` when `python` is not on `PATH`) to fetch and register the MCP ecosystem; add `--dry-run` to preview.
