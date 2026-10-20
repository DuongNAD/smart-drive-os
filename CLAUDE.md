# CLAUDE.md - AI Coding Instructions for SSD Workspace

- Filesystem: exFAT (512KB clusters).
- Avoid loose micro-files to minimize cluster slack waste.
- Search files using `smart-drive search "<query>"`.
- Search lessons and research with `smart-drive search --kind slide|fact|capture "<query>"` (MCP ssd_search kind/content).
- Verify SSD health using `smart-drive sentinel` or `smart-drive checkup` (`smart-drive audit --html` exports an offline HTML treemap).
- Move folders with `smart-drive organize --apply` (or `Smart_Organize.*`, which skips `protected_by_system` folders and relinks agents/IDEs/shortcuts); revert the latest move batch with `smart-drive undo --apply`. After moving a folder by hand, run `smart-drive relink "<old>" "<new>"` (`--dry-run` previews) or `smart-drive paths --broken` (`--fix`).
- Clean junk with `smart-drive clean` (dry-run by default; `--apply` deletes regenerable Tier 1-2 junk at once, and only `--tier 3` junk such as logs, temp files, and dumps goes to a restorable quarantine: `--list-quarantine` and `--restore latest` inspect/restore).
- Run `smart-drive gitignore audit` before committing.
- Commit in small, atomic steps: one logical fix or feature per commit (`fix(scope): ...`, `feat(scope): ...`), paired with a regression test in the same commit, and run the test suite before committing.
- First time on a machine: run `python3 -m smart_drive setup --json` (Windows: `python -m smart_drive setup --json` or `py -m smart_drive setup --json` when `python` is not on `PATH`). It installs nothing until the user agrees: it exits 4 and lists the MCP servers (name + what each does). Show that list to the user with your own question/confirmation prompt, ask which to install, then run `python -m smart_drive setup --only <approved,names> --yes`. Never pass `--yes` without the user's answer. `--dry-run` previews.
