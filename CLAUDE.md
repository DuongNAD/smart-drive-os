# CLAUDE.md - AI Coding Instructions for SSD Workspace

- Filesystem: exFAT (512KB clusters).
- Avoid loose micro-files to minimize cluster slack waste.
- Search files using `smart-drive search "<query>"`.
- Search lessons and research with `smart-drive search --kind slide|fact|capture "<query>"` (MCP ssd_search kind/content).
- Verify SSD health using `smart-drive sentinel`.
- Run `smart-drive gitignore audit` before committing.
- First time on a machine: run `python3 -m smart_drive setup` (Windows: `python -m smart_drive setup`) to fetch and register the MCP ecosystem; add `--dry-run` to preview.
