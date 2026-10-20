# Changelog

All notable changes to SmartDrive-OS are written down here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases before 1.4.0 are described on the
[GitHub Releases](https://github.com/DuongNAD/smart-drive-os/releases) page.

## [Unreleased]

## [1.4.0] - 2026-10-20

The safety release for everyday users. Commands that delete or move things keep away from what only looks like clutter
(installed apps, dependency trees, files git tracks, folders Windows depends on), the folders that do move are followed
by the coding agents, IDEs and shortcuts that remember them, and the slowest commands finish in seconds on a large
drive. `#10`, `#11` and `#12` are pull requests; the other entries landed in the same release.

### Added

- Launcher menu option `[11]` "Relink Coding Agents to the Latest Move" in `SmartDrive.bat`, `.ps1`, `.sh` and
  `.command`. It shows `smart-drive relink --dry-run`, asks `[y/N]`, and only then runs `smart-drive relink --wait`,
  which re-applies the latest `organize`/`classify` batch once the open coding agents have closed. Use it after
  `organize --apply` was run from inside a coding agent and that agent has quit: a running agent can write its old
  paths back. Option `[8]` (`relink --find`) stays for folders moved by hand.
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
- `audit` models the volume's own cluster size in every output mode instead of always 512 KB, and the docs call exFAT
  with 512 KB clusters the design target, not a fact about every folder. (#10)
- `checkup` scores and recommends only the stale references that point into the drive being checked; the rest stay
  listed in `paths --broken` as information. (#11)
- Speed on a 167 GB NTFS drive with 342,000 files: `checkup` 571 s to about 40 s and `clean` 491 s to about 8 s (#11);
  `dup` from more than 15 minutes without any output to about 3 s once the files are cached.

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
  folders Windows and installed apps depend on, and no longer scans for 15 minutes in silence.
- The MCP tools accept absolute paths when the storage root is a drive root (`E:\` or `/`). Containment is decided on
  the resolved path, so `/data/drive/../x` is no longer called safe for the root `/data/drive`.
- `SmartDrive.bat` printed "'Presets' is not recognized as an internal or external command" and cut five menu lines at
  an unescaped `&`; `SmartDrive.ps1` drew the menu again after option `[0]` instead of exiting.
- `Quick_Audit.bat` printed "'Cluster' is not recognized as an internal or external command" and cut its window title
  and banner at an unescaped `&`, and `Quick_Search.bat` wrote a file named `10MB` into the program folder instead of
  showing the example `size:>10MB`. No `.bat` launcher holds such a line any more.

[Unreleased]: https://github.com/DuongNAD/smart-drive-os/compare/v1.4.0...main
[1.4.0]: https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.4.0
