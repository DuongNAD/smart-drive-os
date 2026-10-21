# Changelog

All notable changes to SmartDrive-OS are written down here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases before 1.4.0 are described on the
[GitHub Releases](https://github.com/DuongNAD/smart-drive-os/releases) page.

## [Unreleased]

### Added

- `dup --json`, `checkup --json` (in its `filesystem` block) and the MCP tool `ssd_find_duplicates` now say whether the
  cluster size their slack figures are worked out at was read from the drive or assumed: `cluster_size_source` is
  `"detected"`, or `"assumed"` when it could not be read and the 512 KB exFAT cluster is used, as in `audit --json` and
  `ssd_audit`. `audit`, `dup`, `checkup`, `organize`'s plan and the MCP tools all take that cluster size from one place
  now, `fsinfo.slack_cluster`.
- The MCP server negotiates the protocol version in `initialize` instead of answering every client with `2024-11-05`.
  A client that asks for `2025-11-25`, `2025-06-18`, `2025-03-26` or `2024-11-05` is answered with that revision, and
  any other revision (a newer one this server does not know, or `2026-07-28`, which has no `initialize`) with
  `2025-11-25`, the latest one that opens with `initialize`, as the MCP specification says. A client that names no
  revision still gets `2024-11-05` and a reply that is the same byte for byte. The chosen revision lasts for the
  connection and decides what `serverInfo` carries: `title` from `2025-06-18`, and `description` and `websiteUrl` (the
  words of `server.json`) from `2025-11-25`.
- The MCP tools get display titles and structured results in the protocol revisions that define them. `tools/list`
  carries `annotations.title` from `2025-03-26` (`"Search Drive Files"` for `ssd_search`, and so on for the nine tools)
  and also a top-level `title` from `2025-06-18`. From `2025-06-18` a successful `tools/call` returns the result object
  as `structuredContent` and, as the specification recommends, the same JSON as text in `content`, so a result is about
  twice as large for those clients. The hints stay at the top level of each tool, error results are unchanged, and a
  client on `2024-11-05` (or one that never sends `initialize`) gets the same tool list and results as before, byte for
  byte.

### Fixed

- The MCP tool `ssd_audit` worked out cluster slack at an assumed 512 KB exFAT cluster on every drive, so on a 4 KB NTFS
  drive a folder holding 1.35 MB showed 34.7 MB of slack where `smart-drive audit` showed 157 KB, and nothing in the
  result said which cluster size the figures were worked out at. It now uses the drive's real cluster size, like
  `smart-drive audit` (512 KB only when the size cannot be read), through the new `fsinfo.slack_cluster`. Its result
  gained `cluster_size`, `cluster_size_source` (`"detected"`, or `"assumed"` when the 512 KB cluster was used because
  the size could not be read), `filesystem` and `slack_model_note`; `audit --json` gained `cluster_size_source`; and the
  tool description no longer claims 512 KB.
- The MCP tool `ssd_find_duplicates` still described its savings as "512KB physical cluster space savings" although
  1.4.0 made it work them out at the drive's real cluster size (`cluster_size`, 4096 on an NTFS drive), with 512 KB only
  as the fallback when that size cannot be read. Agents read a tool's description to decide what its numbers mean, so on
  a 4 KB drive they were told the savings were 512 KB ones. The description now says what the tool computes: the
  nominal bytes the redundant copies hold and the cluster slack they waste at the drive's real cluster size
  (`cluster_size_source` is `"detected"`, or `"assumed"` when the 512 KB exFAT cluster was used), how it compares files
  (by size, then by their first 8 KB and, above 16 KB, their last 8 KB, then in full), and which folders it does not
  enter.
- The MCP tool `ssd_check_safety` called a drive-relative Windows path such as `E:x` safe, reading it as `x` below the
  storage root, although Windows reads it against the current folder of drive `E:` and every other path tool refuses it.
  It now refuses it with an error that says so and what to send instead (an absolute path, or one relative to the
  storage root), and the other tools' refusal says the same instead of "path escapes storage root". It also called the
  storage root itself safe (`E:\`, `.`, `x/..`, or the folder root in any spelling), the one folder an agent must never
  delete or move, while the taxonomy folders below it were protected. It now reports the root protected: `is_safe`
  false, `is_protected_root_dir` true and the new field `is_storage_root` true (every answer carries the field, false
  for any other path).
- `organize` wrote `.metadata_never_index` and `.fseventsd/no_log` into the drive root during a dry run (`--dry-run`,
  the default, and `--clean` without `--apply`), although a dry run promises to change nothing and `status`, `clean`
  and the MCP tool `ssd_auto_organize` already wrote nothing until applied. A dry run now writes nothing: its `--json`
  reports which markers are on the drive (`anti_indexing_shields`) and which `--apply` would create
  (`anti_indexing_shields_to_create`), and the text preview says so. `--apply` writes them as before.
- `clean`, the MCP tool `ssd_clean` and `snapshot create` worked their sizes out at an assumed 512 KB exFAT cluster on
  every drive, because they built their junk scanner, purge engine and snapshot manager without a cluster size. On a
  4 KB NTFS drive `clean --json` listed 3,811 junk items holding 570,405,009 bytes and gave them 15,993,405,440 bytes of
  `allocated_size` (every file rounded up to 512 KB), `ssd_clean` reported the same 15,993,405,440 as
  `slack_bytes_reclaimed`, the space a cleanup would free, and a snapshot's allocated and slack bytes were worked out
  the same way; nothing said which cluster size the figures were worked out at. They now use the drive's real cluster
  size, like `smart-drive audit` (512 KB only when it cannot be read), through `fsinfo.slack_cluster`: on that drive
  both now give the same junk 639,787,008 bytes at 4 KB clusters. `clean --json`, `ssd_clean` and the snapshot
  manifests (so `snapshot create --json` and `snapshot list --json`) gained `cluster_size` and `cluster_size_source`
  (`"detected"`, or `"assumed"` when the 512 KB cluster was used because the size could not be read); a manifest written
  before reads as 512 KB, `"assumed"`, which is what it was worked out at. The text of `snapshot create` and
  `snapshot list` names the cluster too, and the `ssd_clean` description says what its sizes are. `organize --clean`
  and the clean step of `ssd_auto_organize` now work at the cluster their own plan reports (`cluster_size`), and
  `backup`, which reports no allocated size and so gained no field, and the junk scan of `checkup`, whose allocated
  sizes the report never shows, are given the real cluster as well.
- The text and markdown reports of `audit` still printed 512 KB labels on every drive. The text report called its
  cluster count "Allocated 512KB Clusters"; the markdown report did the same, titled its hotspot section
  "512KB Cluster Slack Hotspots", described its micro-file folders as wasting slack "on 512 KB clusters" and said
  each `._*` fork wastes 512 KB. Since 1.4.0 every figure in them is worked out at the drive's real cluster size, so
  on a 4 KB NTFS drive a count of 4 KB clusters stood under a 512 KB label (the JSON and HTML reports were right).
  Each of those labels now names the cluster size the figures use (the count is followed by `(4 KB each)`, the
  section is titled `Cluster Slack Hotspots (4 KB clusters)`), and the fork tip says each fork takes at least one
  cluster of that size. The headers of both reports divided the size by 1024, so a cluster below 1 KB read "0 KB" and
  1536 B read "1 KB"; the headers and the labels now give such a size in bytes (`512 B`, `1536 B`). The text header
  also says where the size came from, `Cluster Size: 4 KB (4096 B, detected)`, or `assumed` when the 512 KB exFAT
  cluster is used because the drive's own could not be read.
- The guided organizer (`Smart_Organize.*`) appended to `<root>/.smart_drive/smart-organize.log` from its first line,
  creating `.smart_drive/` when the drive had none, so its dry-run preview and its `SD_SELFTEST=1` / `--selftest` mode,
  which the launchers and the docstring called read-only, left a log (START, DRY_RUN, BUSY_APPS, CANCELLED_*, END
  nothing_to_move, SELFTEST_OK) on a drive that a preview promises not to touch. The preview now keeps its log lines in
  memory and writes them to the log, in order and with the times they were recorded at, only once you type `YES` and
  the moves start; from then on every event is appended as before. A run that ends before that (nothing to move, `Q`
  while apps are open, anything but `YES`, the self-test) leaves the drive exactly as it was and, on purpose, no longer
  leaves a trail. The banner, the launchers and both READMEs say when the log is written.
- The `smart-drive checkup` help called the command "Read-only" and the README said it leaves your files alone, although
  every run rewrites `<root>/.smart_drive/checkup.json` (its whole last report) and creates `.smart_drive/` when the
  drive had none. The write stays: the comparison with the last run ("Previous 72 → now 80", `previous_score`,
  `score_delta`) needs that report, and the report describes this drive, so it is kept on the drive, where it follows
  the drive to another computer or drive letter (a per-user folder would lose it there or show another drive's score in
  its place). It is one JSON file overwritten in place, so it never grows, and nothing else on the drive is changed. The
  `--help` text, the module docstrings and both READMEs (the sentence under "Check the drive", the "What it does" table
  and the command reference) now say exactly that.
- `sentinel` (also with `--no-heal`, which leaves the drive alone) and the MCP tool `ssd_status`, which is annotated
  read-only, ran `git status` in every repository on the drive without turning optional locks off, so in a repository
  whose index was stale (a clone copied to another computer or drive letter, files touched by a tool) git refreshed the
  index and wrote `.git/index` back. `repos`, `gitignore audit` and the junk detector already ran git with
  `GIT_OPTIONAL_LOCKS=0`; the repository check of `sentinel` now does too, so a health check leaves every repository
  byte for byte as it was. Found by running the previews on a real repository: the repositories the tests used until
  then held a `.git/HEAD` and nothing else, which git cannot read.

## [1.4.2] - 2026-10-21

Safety and packaging fixes from the product-readiness review of 1.4.1: a list of running programs that cannot be read
no longer counts as "nothing is running", purging the quarantine keeps the recent batches, Tier 3 junk can be
quarantined on its own, `paths --broken` no longer takes a folder inside installed packages or a folder that only has
the right name for a moved one, and the package is ready for its first PyPI release: run it with `uvx` or `pipx`,
register the MCP server from either, and find it in the official MCP Registry once it is published. The shipped folder
lists no longer carry one user's folder name.

### Added

- `clean --only-tier N` lists and applies a single junk tier. `--tier N` still means tiers 1 to N, so
  `clean --tier 3 --apply` also deletes every Tier 1-2 item permanently; `clean --only-tier 3 --apply` moves only the
  Tier 3 junk (temp files, swap files, crash dumps) to the restorable quarantine and deletes nothing else.
  `--only-tier` cannot be combined with `--tier`, its dry run says what `--apply` will do, and `--json` has
  `only_tier`. The READMEs and `AGENTS.md`, `CLAUDE.md` and `GEMINI.md` (and the copies `init` writes) mention it.
- A release workflow, `.github/workflows/release.yml`. Pushing a tag `v<version>` builds the source distribution and the
  wheel, checks them with `twine check --strict` (and that the tag is the version in `pyproject.toml`), installs the
  wheel in a fresh virtual environment on Linux, macOS and Windows with Python 3.9 and 3.13 and smoke-tests it
  (`packaging/smoke_test.py`: `status --json`, `organize --dry-run`, and starting the MCP server that `mcp register`
  wrote; also run through `uvx` and `pipx`), and then uploads both files to PyPI through Trusted Publishing (OpenID
  Connect with attestations; no token is stored anywhere), but only after the owner approves the deployment in the
  protected `pypi` environment. Branch pushes and pull requests that touch the packaging files, and manual runs, only
  build and test. The one-time PyPI and GitHub setup, the steps of a release and what to do when one goes wrong are in
  `docs/RELEASING.md`.
- `uvx smart-drive-os <subcommand>` and `pipx run smart-drive-os <subcommand>` work: the package now installs a console
  script named like itself, `smart-drive-os` (`smart-drive` and the other names stay).
- README install options (`uvx`, `pipx` and a source checkout; the first two once the first release is on PyPI) and
  ready-to-paste MCP entries for Claude Desktop, Cursor, Antigravity, Windsurf and a project's `.mcp.json`, for Claude
  Code and for Codex.
- `server.json` for the official MCP Registry (`io.github.DuongNAD/smart-drive-os`, the PyPI package `smart-drive-os`,
  started with `uvx smart-drive-os mcp`) and the `mcp-name` comment in `README.md` that the registry looks for in the
  package description. Nothing is published to the registry yet; `docs/RELEASING.md` has the steps.

### Changed

- `clean --purge-quarantine` keeps the quarantine batches younger than 30 days unless `--older-than` says otherwise.
  It used to default to `--older-than 0`, so `clean --purge-quarantine --apply` permanently deleted every batch at once,
  the one made a minute ago included, although the hint printed after a quarantine says `--older-than 30`. Purging
  every batch now takes an explicit `--older-than 0`, a negative age is refused with exit code 2 before anything is
  touched, and the text report says how many newer batches were kept. `purge_quarantine_batches()` defaults to the same
  30 days (`QUARANTINE_RETENTION_DAYS`).
- The package is classified as `Development Status :: 4 - Beta` instead of `5 - Production/Stable`, and
  `README_INTERNAL.md` shows the live CI badge instead of a static "968/968 tests passed" one.
- Package metadata: the description says what the tool mainly does (organize a drive and relink the paths coding agents
  and IDEs remember, clean junk safely, search files instantly), the license is the SPDX expression `MIT` (PEP 639;
  building from source now needs setuptools 77 or newer, which pip fetches by itself), and the empty `setup.py` shim is
  gone (pip 21.3 and newer build from `pyproject.toml` alone).

### Removed

- `DBI202_VuPT\MSSQL16.MSSQLSERVER` (in both spellings), the name of one user's own course folder, from the protected
  and the scan-excluded folder lists. The generic `MSSQL16.MSSQLSERVER` entries already protect and skip a SQL Server
  instance folder under any parent, so nothing is less protected. `32837` and `fo4` stay, now with a comment: they are
  the install folders of FC Online (Garena's app ID and the game's short name), generic for players in Vietnam. There
  is still no list of your own folders to protect; the READMEs now say what keeps one in place: `organize` only moves
  what lies directly in the drive root, and on Windows a folder that a shortcut on the Desktop, in the Start Menu or on
  the taskbar points to is `protected_by_system`.

### Fixed

- `paths --broken` and `relink --find` no longer take a folder inside installed packages for a moved one. Looking for
  a missing folder by its name one and two levels below its parent, they went into `node_modules`, `site-packages`,
  `vendor` and the like, so the stale `PATH` folder `C:\Users\<you>\bin` was reported as moved to
  `C:\Users\<you>\node_modules\playwright-core\bin`. The search now neither enters nor returns a folder named
  `node_modules`, `bower_components`, `jspm_packages`, `.pnpm-store`, `site-packages`, `dist-packages`,
  `__pypackages__`, `.venv`, `venv` or `vendor`, unless the missing folder itself lay inside one.
- `paths --broken` no longer counts one folder of the same name as a sure match when nothing below the missing folder
  can confirm it, as for a `PATH` folder or a shortcut to a folder. Such a shortcut was marked ✓, and
  `paths --broken --fix` relinked the old folder to the match, and with it every agent and IDE path below the old
  folder, although the match may be another program's folder and the old one deleted; such a `PATH` folder was
  reported as "moved to" the match. These matches now have the kind `name-only` (also in `--json`), are shown with `?`
  and "maybe moved to" next to the `smart-drive relink "OLD" "NEW"` command to run if you did move the folder there,
  and `--fix` leaves them alone, as `relink --find --apply` does. A folder that holds what the shortcut or `PATH` entry
  pointed to inside the old one is still `same-name`. Agent and IDE entries matched by their name alone, which the
  report listed as "not found", are shown the same way.
- `relink --dry-run` no longer asks to close coding agents the run does not touch, and `relink` asks to restart them
  only after it changed one of their config files. The dry run listed every running agent under "Close these before
  --apply", and the real run ended with "Restart the coding agents that are open...", also when all it changed were a
  virtual environment's console or activation scripts, editable installs, Windows shortcuts or git worktrees, which no
  running agent holds. The hint now names only the running agents whose own config files would change (the ones the
  real run warns about) and reads "Close these before running it without --dry-run" (`relink` applies by default;
  `--apply` only goes with `--find`), and the restart lines appear only when an agent's config file was rewritten.
  `organize`, `undo`, `classify` and `paths` print what they printed before, and `busy_apps` in the `--json` of
  `organize` and `undo` still lists every running agent.
- `offload --move` and `offload --revert` no longer move a cache while the list of running programs cannot be read.
  When `tasklist` or `ps` failed or passed its 10 s timeout, the list came back empty, so Docker Desktop's WSL data,
  Ollama's models or Cargo's registry could be copied and relinked while the program using them was open. Such a cache
  is now refused with a message saying that the list could not be read: `--wait` keeps checking until the list can be
  read and the program is closed (still unreadable at the deadline: exit code 3), and `--force` moves it anyway with a
  warning. npm and Gradle caches, which only warn while node or java runs, warn that this could not be checked. A dry
  run says it would be blocked and has `process_list_readable` in `--json`.
- `organize`, `undo` and the guided `Smart_Organize` flow say when they could not check which coding agents and IDEs
  are open, instead of reporting `busy_apps: []` as if none were. Next to every `busy_apps` their `--json` output now
  has `busy_apps_unknown`, the text dry run asks to quit open coding agents and IDEs before `--apply`, and
  `Smart_Organize` stops at step 2 and asks for them to be closed (Enter checks again, `C` continues, `Q` cancels)
  instead of moving at once. `--apply` itself is unchanged, and `relink` already counted an unreadable list as "still
  running".
- `mcp register` and `setup` run through `uvx` or `pipx run` wrote the interpreter of the temporary environment the
  package runs from (uv's cache, or pipx's `<PIPX_HOME>/.cache/<hash>`, which pipx deletes after 14 days without use)
  into every agent config, which broke the server whenever that environment was cleaned up. They now register
  `uvx smart-drive-os mcp` and `pipx run smart-drive-os mcp` instead (with the absolute path of `uvx` or `pipx` when it
  is known). A `pipx install`, `uv tool install` or pip install keeps registering its own Python.

## [1.4.1] - 2026-10-20

A patch release for what the first moves on a real drive turned up: a refused move now says what holds the folder,
`relink` reports only what it changed, and a moved virtual environment works again without a reinstall.

### Added

- `relink` now repoints the console scripts of a moved virtual environment instead of only warning about them: the
  interpreter path in the shebang line of each pip/distlib launcher (`pip.exe`, `fastapi.exe`, ... on Windows; the zip
  archive in them stays byte for byte the same and the result is checked to open and read back) and in the shebang of
  text scripts in `Scripts/` and `bin/` (including distlib's `#!/bin/sh` form) follows the move, with a backup
  `<file>.bak.smart-drive` of every file it rewrites. This also covers virtual environments that hold no `.pth` file.
  `--json` has `console_scripts` per venv. A launcher it cannot parse or rewrite safely, such as one made by uv, is left
  untouched and is the only thing that still gets the console-scripts warning.
- `relink` also repoints the activation scripts of a moved virtual environment. `activate`, `activate.bat`,
  `activate.fish`, `activate.csh`, `activate.nu` and the like hold the venv's path as text, so after a move activating
  the venv put its old folder on `PATH`. relink writes the new path into them, replacing only whole occurrences of the
  old venv path (also its forward-slash, doubled-backslash, Git Bash `/e/...` and Cygwin `/cygdrive/e/...` spellings)
  and keeping line endings, encoding and every other byte, with a `.bak.smart-drive` backup of each rewritten script.
  The venv line reads `Python venv updated: <venv> (15 console scripts and 3 activation scripts repointed)`, and
  `--json` has `activation_scripts` per venv.

### Fixed

- A move that Windows refuses (`[WinError 5] Access is denied`) now names the folder that is in use. `organize`, `undo`
  and `classify` listed the usual suspects and asked the Restart Manager about the files directly in the folder, so a
  handle on a nested folder (`E:\project` could not be moved because `...\datathon-2026-round-1\Nop_bai\Images` was
  open) was named by nothing. NTFS refuses to rename a folder while anything below it is open, so after a refused move
  on Windows a read-only probe (8 levels, 20,000 entries, 5 seconds; it renames nothing and keeps nothing open) tries to
  open every folder and file exclusively and reports the deepest entries that are in use. The hint says
  `in use: E:\project\...\Images`, asks the Restart Manager about the files that were found open, and when no program
  can be named it names `explorer.exe` (an open Explorer window or Quick Access) as a common holder and says how to find
  a system service or an elevated program: Resource Monitor as administrator (`resmon`) → CPU → Associated Handles, or
  a restart. The paths also come back as `in_use` in the `--json` output of `organize`, `undo` and `classify` and in the
  errors of the MCP tool `ssd_auto_organize`.
- A folder pinned in Explorer's Quick Access keeps every folder above it from being moved: Explorer holds a
  change-notification handle on each Quick Access folder and opens it again at every logon, so unpinning alone does
  not release it and neither does restarting Windows while the folder is still pinned. When something is in use below
  the folder being moved, the hint now asks Windows for the Quick Access list (one short PowerShell call, 5 seconds,
  skipped silently when it fails) and, if a folder inside is listed, says so: unpin it (right-click → Unpin from Quick
  access), restart Windows Explorer (Task Manager → Windows Explorer → Restart) and run the command again.
- `relink` no longer claims work it did not do. Running `smart-drive relink` again for a move that was already relinked
  (menu `[11]`) listed every repository under "git worktrees repaired" and every virtual environment under "Python venv
  updated" with a console-scripts warning each, although the dry run said "Nothing left to change" and nothing had
  changed: `git worktree repair` exits 0 whether or not it repaired anything, and the venv check was always true in a
  real run. A repository is now listed as repaired only when git printed a `repair:` line or the
  `.git/worktrees/*/gitdir` and `.git` files it rewrites changed, and a virtual environment only when one of its files
  (`.pth`, `.egg-link`, editable finder, console script, activation script) was rewritten, or in a dry run would be. A
  second `relink` of the same move now ends with "Nothing left to change", `--json` reports empty `worktrees` and
  `venvs`, and `relink --dry-run` no longer adds "Close these before --apply" when it has nothing to apply.

## [1.4.0] - 2026-10-20

The safety release for everyday users. Commands that delete or move things keep away from what only looks like clutter
(installed apps, dependency trees, files git tracks, folders Windows depends on), the folders that do move are followed
by the coding agents, IDEs and shortcuts that remember them, and the slowest commands no longer need many minutes on a
large drive (the measured times are under Changed). `#10`, `#11` and `#12` are pull requests; the other entries landed
in the same release.

### Added

- Launcher menu option `[11]` "Relink Coding Agents to the Latest Move" in `SmartDrive.bat`, `.ps1`, `.sh` and
  `.command`. It shows `smart-drive relink --dry-run`, asks `[y/N]`, and only then runs `smart-drive relink --wait`,
  which re-applies the latest `organize`/`classify` batch once the open coding agents have closed. Use it after
  `organize --apply` was run from inside a coding agent and that agent has quit: a running agent can write its old
  paths back. Option `[8]` (`relink --find`) stays for folders moved by hand. `AGENTS.md`, `CLAUDE.md` and `GEMINI.md`,
  and the copies `init` writes to a drive, tell coding agents the same.
- `.gitattributes` pins the line endings of the launchers: `.sh` and `.command` are LF, `.bat`, `.cmd` and `.ps1` are
  CRLF, on every system. A Windows checkout no longer gives the macOS and Linux launchers CRLF ("bad interpreter"), and a
  ZIP of the repository keeps CRLF in the batch files.
- `dup` gains `--min-size BYTES`, progress lines on stderr about every 2 seconds (stdout stays valid JSON), and a plan
  that counts what was scanned and what was skipped (`folders_scanned`, `files_scanned`, `skipped_dependency_dirs`,
  `skipped_protected_dirs`, `elapsed_seconds`).
- `clean` says what it left alone although it looks like junk, and why (inside a dependency tree, used by the system,
  not at a project root, tracked by git): a "Kept N look-alike item(s) that are not junk" block in the text report, and
  `kept` and `protected_by_system` in `--json`.
- `setup` asks which MCP servers to install (none by default; without a terminal it installs nothing and exits with
  code 4 and a `consent` list that the coding agent puts to the user), and asks before adding a server that is already
  downloaded to a coding agent. (#10, #12)
- Since 1.3.1: `relink` follows moved folders in Claude Code, Claude desktop, Codex, Gemini CLI, Antigravity, Cursor,
  Windsurf, VS Code and JetBrains IDEs, Obsidian vaults, Windows `.lnk` shortcuts, git worktrees and virtual
  environments, with `--find` (where did a forgotten folder go), `--ask`, `--wait` and `--dry-run`; `undo` reverses the
  latest `organize`/`classify` batch; `clean` quarantines Tier 3 junk so it can be restored; `checkup` scores the drive
  0-100 and lists next steps; `paths --broken` lists stale shortcuts, IDE entries, PATH folders and agent paths;
  `audit --html` writes an offline treemap; the `Smart_Organize` launchers guide a move step by step; and
  `protected_by_system` keeps `organize` and `classify` away from folders that Windows services, PATH, installed apps,
  startup entries, shortcuts and game libraries depend on.

### Changed

- `classify` looks only at the files lying directly in the scanned folder; `--recursive` opts in to subfolders
  (`--no-recursive` is still accepted). It never sorts the files of a folder that holds a program, library, launcher
  script, project manifest, `.git` or Obsidian vault, of a folder that holds such a program folder, or of a folder the
  system depends on: that folder is reported as kept whole. This changes the default: a plain `classify` used to plan
  hundreds of moves that pulled files out of the apps and projects they belong to.
- `clean --apply` deletes Tier 1-2 junk permanently and at once; only Tier 3 goes to the restorable quarantine. The
  dry-run footer, `checkup`, the READMEs and the agent instructions now say so, and `checkup` recommends the dry run
  (`smart-drive clean --tier 2`) before the cleanup instead of jumping to `--apply`.
- `dup` hashes up to 8 files at a time, hands the results back in discovery order, and stops promptly on Ctrl-C.
- `dup`, `checkup --dup` and the MCP tool `ssd_find_duplicates` work out the slack at the volume's own allocation unit
  (4 KB on NTFS) instead of always 512 KB; 512 KB stays the fallback when the size cannot be read.
- `audit` models the volume's own cluster size in every output mode instead of always 512 KB, and the docs call exFAT
  with 512 KB clusters the design target, not a fact about every folder. (#10)
- `checkup` scores and recommends only the stale references that point into the drive being checked; the rest stay
  listed in `paths --broken` as information. (#11)
- Speed, measured on a 167 GB NTFS drive with 342,000 files: a `clean` dry run took 491 s and takes 8-9 s now, and
  `checkup` took 571 s and takes 40-65 s now (#11). `dup` ran for more than 15 minutes without any output; it takes 28 s
  on the first run (Windows Defender scans a file the first time it is opened) and 2-3 s when run again.

### Fixed

- Junk rules matched part of a name, so `Photos/buildings_2024` counted as `build`, `Docs/my_dist_notes` as `dist` and
  `lodash._basecopy/index.js` as `._*`, and Tier 1-2 junk is deleted without a quarantine. Name rules now match only the
  name, and rules with a `/` match the trailing path components. (#10)
- `undo` reverses only `organize` and `classify` batches, not the folders that `relink` or `paths --fix` journaled, and
  items that failed to move back stay undoable instead of being marked as undone. (#10)
- `Smart_Organize` applies only the plan the user confirmed, a retry only re-runs the relink instead of journaling the
  batch again, and `U` undoes only the batch of that run. (#10)
- `init` no longer falls back to an ordinary parent folder as the drive root; it asks for `--root`. (#10)
- `relink` escapes the quote when it rewrites the finder of an editable install, so a project moved into `Bob's stuff`
  can still be imported. (#10)
- Folders that something on the computer depends on are found in more cases: shortcuts without a LinkInfo block (60 of
  269 on one machine), scripts and DLLs passed as command-line arguments, environment variables such as `JAVA_HOME`,
  an app folder inside a protected top-level folder (`classify`), and `.lnk` files at the drive root. HoYoPlay and
  Genshin Impact are protected as the README says. Only runnable files directly in a PATH folder pin that folder's
  files, so a drive root on PATH no longer freezes every loose PDF. (#10)
- The READMEs no longer describe `undo --batch`, `paths --wait` or a checkup letter grade, which never existed. (#10)
- `clean` never flags Tier 2-3 junk inside dependency trees (`node_modules`, `site-packages`, virtualenvs, `vendor`),
  inside a folder that Windows or an installed app depends on, for `build`, `dist` and `.cache` folders that are not at
  a project root, or when git tracks something at or under it (at most 300 questions to git per scan).
- Junk directories are sized by the files inside them instead of 0 bytes, so `checkup`, `clean --apply` and `ssd_clean`
  report the real space. (#11)
- `dup` does not enter dependency, environment and cache trees (including the caches tagged with `CACHEDIR.TAG`) or the
  folders Windows and installed apps depend on, and no longer scans for more than 15 minutes in silence.
- The Tier 3 rule for Unix core dumps (`core.*`) also matched source and data files such as `core.py`, `core.js` and
  `core.css` (334 files on the test drive), so `clean --tier 3 --apply` would have moved them to the quarantine. It now
  matches only `core` and `core.<pid>`.
- `organize`, `undo` and `classify` failed with "Access is denied" when a git `fsmonitor` daemon (a repository with
  `core.fsmonitor=true`, started by any `git status`, `ls-files` or `diff`) held a folder inside the one being moved.
  They now stop the daemons of the repositories inside, and of their linked worktrees, before renaming; every git command
  smart-drive runs passes `-c core.fsmonitor=false`, so it never starts one; dry runs list the daemons that would be
  stopped; and a refused move says what may hold the folder (on Windows, with the process names the Restart Manager
  reports).
- `ssd_clean` and `ssd_find_duplicates` called with a `sub_dir` inside an installed app or a dependency tree now judge
  the folders above it the way a scan of the whole drive does instead of scanning it; `ssd_clean` reports what it kept,
  and `ssd_find_duplicates` drops files below `min_size` before hashing them.
- The MCP tools accept absolute paths when the storage root is a drive root (`E:\` or `/`). Containment is decided on
  the resolved path, so `/data/drive/../x` is no longer called safe for the root `/data/drive`.
- `SmartDrive.bat` printed "'Presets' is not recognized as an internal or external command" and cut five menu lines at
  an unescaped `&`; `SmartDrive.ps1` drew the menu again after option `[0]` instead of exiting.
- `Quick_Audit.bat` printed "'Cluster' is not recognized as an internal or external command" and cut its window title
  and banner at an unescaped `&`, and `Quick_Search.bat` wrote a file named `10MB` into the program folder instead of
  showing the example `size:>10MB`. No `.bat` launcher holds such a line any more.
- The `.bat` launchers turned delayed expansion on, which makes cmd.exe delete every `!` in a path or a typed text it
  has just expanded. A launcher in a folder with a `!` in its name lost the `!` from its own path (`cd` failed with "The
  system cannot find the path specified" and the code was not found), `Quick_Search.bat` searched for `wowbc` when asked
  for `wow! a!b!c`, and "Goodbye!" and the Python-missing error printed without their `!`. Delayed expansion is now off;
  only the line of `SmartDrive.bat` that shows the drive root switches it on.

[Unreleased]: https://github.com/DuongNAD/smart-drive-os/compare/v1.4.2...main
[1.4.2]: https://github.com/DuongNAD/smart-drive-os/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/DuongNAD/smart-drive-os/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.4.0
