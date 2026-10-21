<p align="center"><img src="https://raw.githubusercontent.com/DuongNAD/smart-drive-os/main/docs/banner.jpg" alt="SmartDrive-OS — Saturn and its orderly rings in deep space" width="100%"></p>
<!-- mcp-name: io.github.DuongNAD/smart-drive-os -->

# SmartDrive-OS

**Move and tidy the folders on your drive without breaking your coding agents and IDEs.**

[![PyPI](https://img.shields.io/pypi/v/smart-drive-os)](https://pypi.org/project/smart-drive-os/)
[![Python versions](https://img.shields.io/pypi/pyversions/smart-drive-os)](https://pypi.org/project/smart-drive-os/)
[![CI](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/DuongNAD/smart-drive-os/actions/workflows/tests.yml)
[![Release: v1.4.2](https://img.shields.io/badge/release-v1.4.2-blue.svg)](https://github.com/DuongNAD/smart-drive-os/releases/tag/v1.4.2)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Privacy: 100% Local](https://img.shields.io/badge/Privacy-100%25%20Local-success?style=flat-square&logo=shield)](PRIVACY.md)
[![M8ven Score](https://m8ven.ai/badge/mcp/duongnad-smart-drive-os-1kxkwu)](https://m8ven.ai/mcp/duongnad-smart-drive-os)

SmartDrive-OS is a CLI and an MCP server for people who keep their projects on a big drive and work with coding agents. When it moves a folder, it updates everything that remembered the old path: Claude Code, Claude Desktop, Codex, Cursor, Antigravity, Windsurf, VS Code-family and JetBrains recent projects, Windows shortcuts, git worktrees, and Python venvs (`.pth`, console-script launchers, `activate` scripts). It also cleans junk safely, finds duplicates and searches the drive instantly. It uses only the Python 3.9+ standard library (no dependencies) and keeps everything local.

> 🇻🇳 **Tiếng Việt**: [README_VN.md](README_VN.md) · 🚀 **Internal NTFS Drive & C-Drive Cache Offload Guide**: [README_INTERNAL.md](README_INTERNAL.md)

**Jump to:** [Why](#why) · [Quick start](#quick-start) · [What it does](#what-it-does) · [Moving folders](#moving-folders-without-breaking-agents-and-ides) · [MCP](#use-with-coding-agents-mcp) · [Safe Junk Cleaner](#safe-junk-cleaner) · [Everyday use](#everyday-use) · [More features](#more-features) · [CLI reference](#cli-reference) · [Privacy](#privacy-security--data-isolation)

## Why

- **Moving a project folder by hand breaks things.** Coding agents, IDEs, shortcuts, git worktrees and Python virtual environments remember folders by absolute path, and exFAT cannot leave a link at the old place. After a manual move the agent no longer finds its project, sessions and MCP servers, the IDE's recent-projects list points nowhere, and a moved venv still points at its old folder.
- **SmartDrive moves and relinks in one step.** `smart-drive organize` previews by default; `--apply` moves the folders and rewrites every remembered path. Every move is journaled in `.smart_drive/moves.jsonl`, so `smart-drive undo --apply` puts the batch back and points everything at the original paths again.
- **Coding agents can do it themselves.** The 9 MCP tools (`ssd_auto_organize`, `ssd_clean`, `ssd_search`, ...) work under the same safety rules: previews by default, system and app folders pinned in place, a whitelist that is never deleted.

## Quick start

### 1. Install

```bash
pipx install smart-drive-os   # needs Python 3.9+; gives you the `smart-drive` command
```
Prefer not to install anything? `uvx` needs only [uv](https://docs.astral.sh/uv/), which also downloads a Python when the machine has none, and plain pip works too:
```bash
uvx smart-drive-os <subcommand>   # runs any subcommand, installs nothing
pip install smart-drive-os        # or install with pip
```

<details>
<summary>From source</summary>

```bash
git clone https://github.com/DuongNAD/smart-drive-os.git
cd smart-drive-os
pip install -e .                    # editable install (`smart-drive` CLI)
python -m smart_drive <subcommand>  # or run directly from the checkout
py -m smart_drive <subcommand>      # Windows Python Launcher (when `python` is not on PATH)
smart-drive self-path-check         # check PATH and print 1-click shell setup
```
Editable installs (`pip install -e .`) need pip 21.3 or newer: with an older pip (the Python 3.9 that ships with Xcode on macOS has pip 21.2), run `python -m pip install -U pip` first.

</details>

### 2. Connect your coding agents

```bash
smart-drive mcp register --all   # Antigravity, Claude, Cursor, Codex, Windsurf, .mcp.json
```
That registers the install you just made; with uvx, `uvx smart-drive-os mcp register --all` registers `uvx smart-drive-os mcp`. To paste the entry yourself, see [Use with coding agents (MCP)](#use-with-coding-agents-mcp).

### 3. Check the drive

```bash
smart-drive checkup   # a 0–100 health score and the top 3 next steps, as commands to copy
```
It leaves your files alone (it only keeps its last score in `.smart_drive/`). Drive commands take `--root <path>` (or set `SMART_DRIVE_ROOT`); without either, SmartDrive uses the SmartDrive root above the current folder, then one on an attached drive. `smart-drive init` prepares a new drive (see [drive profiles](#more-features)).

### 4. Reorganize safely

```bash
smart-drive organize          # preview only: what moves where, which folders stay, which agents and IDEs follow
smart-drive organize --apply  # close your coding agents and IDEs first; moves, then relinks agents, IDEs and shortcuts
smart-drive undo --apply      # changed your mind? moves the latest batch back and relinks to the original paths
```
`organize` sorts the loose folders in the drive root into the 6 taxonomies, routes coursework and rebalances slack. `Smart_Organize.bat` / `Smart_Organize.command` / `./Smart_Organize.sh` is the guided version: dry-run preview, busy-app wait, `YES` confirmation and a 1-key `U` undo. If an agent was open while `organize --apply` ran, quit it, then pick `[11]` in the `SmartDrive.*` menu or run `smart-drive relink`.

## What it does

| Task | Command | Safe by default |
|---|---|---|
| Health score and next steps | `smart-drive checkup` | Leaves your files alone |
| Organize the drive, and undo it | `smart-drive organize` / `smart-drive undo` | Preview until `--apply`; every batch is journaled |
| Repair paths after a manual move | `smart-drive relink "OLD" "NEW"` / `smart-drive paths --broken` | `--dry-run` previews; config files get a `.bak.smart-drive` backup first; `paths --broken` only lists (`--fix` repairs sure matches) |
| Clean junk | `smart-drive clean` | Dry run until `--apply`; only Tier 3 junk goes to a restorable quarantine |
| Search the drive instantly | `smart-drive search "<query>"` | Read-only |
| Find duplicate files | `smart-drive dup` | Report only: deletes nothing |
| See where the space goes | `smart-drive audit --html` | Read-only; writes one report file |
| Keep `.gitignore` files right | `smart-drive gitignore audit` | The audit is read-only; `gitignore apply` previews until `--yes` |
| Offload `C:` caches to another drive | `smart-drive offload --scan` | `--scan` only lists; refuses while a cache's app is running unless you pass `--wait` (waits for it to close) or `--force` |
| Snapshots and backup | `smart-drive snapshot create <name>` / `smart-drive backup --target <dir>` | Your files are never changed; `backup --dry-run` previews |

---

## Moving folders without breaking agents and IDEs

Coding agents, IDEs, and desktop shortcuts remember projects and MCP servers by absolute path, and exFAT cannot leave a link at the old place. When `organize` or `classify` moves a folder, SmartDrive protects system-referenced folders first and rewrites paths everywhere else:

- **System & App Protection (`protected_by_system`)**: Before planning any move, SmartDrive scans Windows User/System `PATH`, Windows Services (`ImagePath`), Installed Apps (`Uninstall` registry `InstallLocation` / `DisplayIcon` / `UninstallString`), Startup (`Run`), `.lnk` shortcuts (Desktop, Start Menu, Quick Launch, drive root), folders containing `unins000.exe` / `uninstall.exe`, and game libraries (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`). Any matching folder is pinned in place with its exact reason in `protected_by_system`.
- **Keeping a folder of your own in place**: `organize` only moves what lies directly in the drive root, so a folder you keep inside one of the taxonomy folders (for example `03_Development_Projects\my-app`) stays where it is. On Windows, a folder is also `protected_by_system` when a shortcut (`.lnk`) on the Desktop, in the Start Menu or on the taskbar points at it or at something inside it: `organize`, `classify`, `clean` and `dup` leave it alone, and `organize --dry-run` lists it as skipped. A shortcut in the drive root counts too, but `classify --apply` files loose root files, `.lnk` included, and the protection leaves with the shortcut.
- **Agent, IDE & Shortcut Settings**: Claude Code (`~/.claude.json`), Claude desktop (config, sessions, worktrees, including Microsoft Store MSIX), Codex (`config.toml`), Gemini CLI, Antigravity, Cursor, Windsurf, VS Code, VSCodium, Positron (`storage.json` and SQLite `state.vscdb`), JetBrains IDEs (`recentProjects.xml` including `$USER_HOME$` macros), Obsidian (`obsidian.json`), Windows `.lnk` shortcuts, Constellation manifest, and the drive's `.mcp.json`. Each file is backed up to `<file>.bak.smart-drive` before writing; SQLite `state.vscdb` is skipped with a warning (`busy_apps`) if the IDE currently holds it open.
- **History, repositories and venvs**: Claude Code history and memory move to the new project folder, git worktrees are repaired, and a moved Python venv works again: its `.pth` and `.egg-link` entries, the interpreter path in its console scripts (pip launchers and scripts with a shebang) and the venv path in its `activate` scripts are rewritten.
- **Reversible Journal (`undo`)**: Every batch of moves is recorded in `.smart_drive/moves.jsonl` (identified by its `time`) so `smart-drive undo --apply` can move the latest `organize`/`classify` batch back and relink agents/IDEs/shortcuts to the original paths. Moves logged by `relink` and `paths` are not undoable.

How to run it:
```bash
smart-drive organize --dry-run                           # preview moves, protected folders, and which agents/IDEs follow
smart-drive organize --apply                             # move, then relink agents, IDEs, Obsidian, and shortcuts
smart-drive undo                                         # preview reversing the latest move batch
smart-drive undo --apply                                 # undo the latest batch and relink back to original paths
smart-drive relink                                       # re-apply latest batch (after closing an open IDE/agent)
smart-drive relink "E:\Old\proj" "E:\New\proj" --dry-run # a folder moved by hand: preview
smart-drive relink "E:\Old\proj" "E:\New\proj"           # ...then apply
smart-drive relink --find                                # list stale agent/IDE paths and where folders went
smart-drive relink --find --apply --wait                 # relink the sure (✓) matches after open apps close
smart-drive relink --find --apply --ask --wait           # ...and ask y/N for each ? match first
smart-drive paths --broken                               # inspect broken shortcuts, IDE entries, PATH, and agent configs
smart-drive paths --broken --fix                         # repair sure matches via relink
```
Menu option `[8]` in `SmartDrive` (or `[10]` / `Smart_Organize.*` for the guided organizer with 1-key `U` undo) handles this interactively, and `[11]` re-applies the latest `organize`/`classify` batch: it previews `smart-drive relink --dry-run`, asks `[y/N]`, then runs `smart-drive relink --wait`. Use `[11]` after running `organize --apply` from inside a coding agent: quit the agent first (a running one may write its old paths back), then open `SmartDrive` and pick `[11]`. Close open coding agents and IDEs before relinking, or pass `--wait` so SmartDrive waits for them to close, then reopen them. If a command in a moved `.venv` still points at the old place (the relink report warns about it), run `uv sync --reinstall` in that project.

<details>
<summary>Locked folders: when Windows refuses to rename a folder (git fsmonitor)</summary>

Windows refuses to rename a folder while a process holds a handle inside it, and a repository with `core.fsmonitor=true` gets a background `git fsmonitor--daemon` that does exactly that after its first `git status` (`[WinError 5] Access is denied`). SmartDrive's own git commands run with `-c core.fsmonitor=false`, so they never start one, and every move stops the daemons of the repositories inside the folder first (`--dry-run` lists them as `fsmonitor_to_stop`, `--apply` reports `fsmonitor_stopped`; git starts a daemon again by itself at the next `git status`). If a move is still refused, the error says what may be holding the folder: a program, a terminal, an Explorer window or a daemon that is still running, and on Windows the processes that have files of the folder open. Windows also refuses to rename a folder while anything *below* it is open, so a read-only probe (at most 8 levels, 20,000 entries and 5 seconds; it renames and keeps open nothing) tries to open every folder and file exclusively and names the deepest ones that are in use (`in use: E:\project\...\Images`, and `in_use` in the `--json` output of `organize`, `undo`, `classify` and the MCP tool `ssd_auto_organize`). When a folder inside is pinned in Explorer's Quick Access, the hint says so (Explorer keeps a handle on every pinned folder and opens it again at each logon, so restarting Windows alone does not help): unpin it, then restart Windows Explorer from Task Manager. When no process can be named for what is in use, the hint points at `explorer.exe` (an open Explorer window or Quick Access) and at Resource Monitor as administrator (`resmon` → CPU → Associated Handles).

</details>

<details>
<summary>relink --find: sure (✓) and uncertain (?) matches</summary>

`--find` marks a folder ✓ only when the move journal, or what the agents remember inside the old folder, shows where it went. A folder found by its name alone is marked `?` and never relinked by `--apply` on its own, since it may be another project and the old one deleted: check it, then run the `smart-drive relink "OLD" "NEW"` command printed next to it. Or add `--ask` (menu option `[8]` does): it asks `Relink OLD -> NEW? [y/N]` for each `?` folder with one candidate, and relinks the ones you answer `y` together with the ✓ ones. Enter means no. It asks only in a terminal: with `--json` or piped input it asks nothing.

</details>

## Use with coding agents (MCP)

SmartDrive-OS exposes a JSON-RPC 2.0 `stdio` MCP server with **9 tools**:

1. `ssd_search`: Instant FTS5 search returning compact tokens (<2,000 tokens/query). *(read-only)*
2. `ssd_audit`: Storage breakdown and cluster slack waste analytics at the drive's real cluster size, with compact formatting. *(read-only)*
3. `ssd_clean`: Whitelist-protected safe junk cleaner with dry-run support. *(destructive)*
4. `ssd_find_duplicates`: 3-phase SHA-256 duplicate detection with pagination and preview capping. *(read-only)*
5. `ssd_update_index`: Fast incremental index synchronization (<2s). *(idempotent)*
6. `ssd_check_safety`: exFAT compatibility validator (9 Win32 forbidden chars, 22 DOS stems, symlinks). *(read-only)*
7. `ssd_status`: SSD mount root status, shield health, and taxonomy status. *(read-only)*
8. `ssd_auto_organize`: Autonomous drive auto-zoning and anti-slack relocation with `AcademicClassifier`. *(destructive)*
9. `ssd_gitignore`: Stack-aware `.gitignore` governance, personal data defense, secrets audit, and safe untracking. *(idempotent)*

<details>
<summary>Tool details</summary>

- `ssd_audit`: cluster slack is worked out at the drive's real allocation unit, as `smart-drive audit` does (`cluster_size`; `cluster_size_source` is `detected`, or `assumed` when it cannot be read and the 512 KB exFAT cluster is used).
- `ssd_clean`: a `sub_dir` is judged like the whole-drive scan, folders above it included: inside an installed app nothing is scanned and `scan_skipped` names the folder and why; the result also lists what was `kept` and the `protected_by_system` folders. `nominal_bytes_reclaimed` is what the junk holds and `slack_bytes_reclaimed` the space it takes on the drive, worked out at the drive's real allocation unit (`cluster_size`; `cluster_size_source` is `detected`, or `assumed` when it cannot be read and the 512 KB exFAT cluster is used).
- `ssd_find_duplicates`: `min_size` drops small files before anything is hashed, slack is worked out at the drive's real allocation unit (`cluster_size`; `cluster_size_source` is `detected`, or `assumed` when it cannot be read), and a `sub_dir` inside an installed app or a dependency tree is not scanned (`scan_skipped`).
- `ssd_check_safety`: the storage root itself is reported protected (`is_storage_root`), and drive-relative paths such as `E:x` (Windows reads them against the drive's current folder) are refused.

</details>

Register across all agents via `smart-drive mcp register --all` (or `smart-drive mcp-config --all`). A pipx or pip install registers its own Python, a `uvx smart-drive-os` run registers `uvx smart-drive-os mcp`, and from a source checkout (without `pip install`) each entry sets `PYTHONPATH` to the checkout so clients can launch from any folder. Templates in `configs/`: `.mcp.json` (workspace), `mcp_config.json` (Antigravity), `claude_desktop_config.json` (Claude Desktop & Code), `cursor_mcp.json` (Cursor / Codex), `windsurf_mcp.json` (Windsurf).

Or add the entry yourself. For Claude Desktop (`claude_desktop_config.json`), Cursor (`~/.cursor/mcp.json`), Antigravity (`~/.gemini/antigravity/mcp_config.json`), Windsurf (`~/.codeium/windsurf/mcp_config.json`) and a project's `.mcp.json`:
```json
{
  "mcpServers": {
    "smart-drive": {
      "command": "uvx",
      "args": ["smart-drive-os", "mcp"],
      "env": { "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1" }
    }
  }
}
```
Claude Code:
```bash
claude mcp add --scope user smart-drive -- uvx smart-drive-os mcp
```
Codex (`~/.codex/config.toml`):
```toml
[mcp_servers.smart-drive]
command = "uvx"
args = ["smart-drive-os", "mcp"]
```
With pipx use `"command": "smart-drive", "args": ["mcp"]` instead. Apps started from the desktop (Claude Desktop on macOS, for example) may not see `~/.local/bin`: if `uvx` or `smart-drive` is not found, put its full path in `command` (`which uvx` or `where uvx`); `smart-drive mcp register` writes full paths by itself. To pin a drive add `"--root", "E:\\"` to `args` or set `SMART_DRIVE_ROOT`; otherwise it looks for a SmartDrive root above the folder the agent starts it in, then on attached drives.

<details>
<summary>Optional: install the 7-server MCP ecosystem (smart-drive setup)</summary>

Fetch Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius, and Mira into `~/.smart-drive/mcp` (off the exFAT drive), register them with SmartDrive in every installed coding agent (Claude Code, Antigravity, Cursor, Codex), and health-check them. It lists the servers and asks which ones you want before it downloads or registers anything; safe to repeat:
```bash
python3 -m smart_drive setup  # macOS / Linux (Windows: python -m smart_drive setup or py -m smart_drive setup)
```
Answer `a` (all), `n` (none, the default), or numbers/names such as `1,3` or `polaris,vega`; Constellation, the control plane, always comes with the others while it is not installed yet. A server that is already downloaded is listed too when it would be added to an agent's config, with the agents named. `--yes` skips the question (`--only polaris,vega --yes` installs just those). A coding agent has no terminal to answer in: its run installs nothing, exits with code `4` and lists the servers, and the agent asks you in its own chat before it runs `setup --only <your picks> --yes`.

Needs Node.js >= 22.4 and git. `--dry-run` previews; `--check --json` reports state (exit code `3` = setup needed); `--no-clone` stays offline; `--agents claude,cursor` limits targets. Registered servers are reused, never cloned twice (`mcp-agy` is separate: own venv, Python >= 3.10, API key).

</details>

<details>
<summary>M8ven MCP Directory Certification (Grade A 100/100) & Token Efficiency</summary>

Certified on the [M8ven MCP Directory](https://m8ven.ai/mcp/duongnad-smart-drive-os) with a **Grade A (100/100) Trust Score** (token: `duongnad-smart-drive-os-1kxkwu`):
- **Static AST Handler Isolation**: Mapped via `SmartDriveMCPServer.TOOL_HANDLERS` without dynamic reflection.
- **Paged Compact JSON**: Paginated (`limit`, `offset`, `total_groups`, `returned_group_count`, `has_more`, `next_offset`) with duplicate previews capped at 10 paths/group (`truncated_files_count`).
- **HMAC Auth & Rate Limiting**: `hmac.compare_digest` via `auth/handshake` (`--auth-token`, `--require-auth`, `SMART_DRIVE_MCP_AUTH_TOKEN`) and thread-safe `SlidingWindowRateLimiter` (`-32000` with `Retry-After`).
- **Path Sanitizers, Loopback & Tool Hints**: `_resolve_safe_path` blocks `../`, `\0`, and cross-drive jumps; web helpers enforce `ALLOWED_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")`; all 9 tools declare `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`.
- **Zero Hidden Outbound Calls**: 100% offline `stdio`; only explicit opt-in `git clone` in `smart-drive setup` accesses the network (skipped with `--no-clone`).

</details>

## Safe Junk Cleaner

Purgeable files are grouped into 3 tiers with a mandatory dry-run default (`--apply` required to move or delete):
1. **Tier 1 (Safe OS Metadata & Bytecode)**: macOS `.DS_Store`, `._*` AppleDouble, `.Spotlight-V100`, `.Trashes`, `.TemporaryItems`; Windows `Thumbs.db`, `desktop.ini`, `ehthumbs.db`, `ehthumbs_vista.db`, `$RECYCLE.BIN`; Python `__pycache__`, `*.pyc`, `*.pyo`.
2. **Tier 2 (Developer Caches)**: `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.tox`, `.nox`, `.coverage`, `htmlcov`, `*.egg-info`; `build`, `dist` and `.cache` folders next to a project file; legacy `polaris/.cache/**`.
3. **Tier 3 (Transient & Dumps)**: `*.tmp`, `*.temp`, `*.swp`, `*.swo`, `*~`, `*.dmp`, `core` and `core.<pid>` (a Unix core dump; `core.py` or `core.js` is a source file and never matches), `hs_err_pid*.log`.

```bash
smart-drive clean                      # dry-run preview (Tier 1 by default)
smart-drive clean --tier 2             # dry-run preview of Tier 1-2: what --apply would delete permanently
smart-drive clean --apply --tier 3     # delete Tier 1-2 junk, quarantine Tier 3 junk
```

`clean` lists only what is really regenerable. It leaves installed packages, folders that Windows or installed apps depend on, and anything git tracks alone:
- **Installed packages**: it never lists anything of Tier 2-3 inside a dependency folder (`node_modules`, `bower_components`, `site-packages`, `.venv`, `vendor`...). That is installed package code, and `npm install` does not notice a deleted `dist/`.
- **Folders Windows or installed apps depend on**: it never lists anything inside one (the same `protected_by_system` index `organize` uses: PATH, Services, Installed Apps, shortcuts, uninstallers).
- **Build folders and git-tracked entries**: a `build`, `dist` or `.cache` folder counts only when a project file (`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `*.csproj`...) lies beside it, and a Tier 2-3 entry that git tracks (or sits in a repository git cannot be asked about) is kept.

The report says how many look-alikes were kept and why (`kept` and `protected_by_system` in `--json`).

`smart-drive clean --apply` deletes Tier 1-2 junk permanently and at once: it is regenerable (OS metadata, build caches), so it is never quarantined, and nothing can bring it back. Preview with `smart-drive clean --tier 2` first (`smart-drive checkup` recommends the same order). Only Tier 3 items (`--tier 3`: temp files, swap files, crash dumps) move into a reversible quarantine batch under `<root>/.smart_drive/quarantine/<batch_id>/` (with `manifest.json`) instead of being unlinked:
- Quarantine Tier 3 junk alone and delete nothing else (`--tier 3 --apply` also deletes every Tier 1-2 item permanently): preview with `smart-drive clean --only-tier 3`, then run `smart-drive clean --only-tier 3 --apply`
- Inspect quarantined batches: `smart-drive clean --list-quarantine`
- Restore a batch to its original paths: `smart-drive clean --restore latest` (or `--restore <batch_id>`)
- Permanently purge old quarantine batches: preview with `smart-drive clean --purge-quarantine`, then `smart-drive clean --purge-quarantine --apply` permanently deletes the batches older than 30 days (`--older-than <days>` sets another age, and `--older-than 0` purges every batch)
- Delete Tier 3 items outright instead of quarantining them (for example when free space is critically low): `smart-drive clean --apply --tier 3 --no-quarantine`

> **Inviolable Whitelist**: Root manifests (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `README.md`, `PRIVACY.md`), launcher scripts (`Setup_*`, `Quick_*`, `Smart_Organize.*`, `SmartDrive.*`), the 6 taxonomies, Windows folders (`WindowsApps`, `Program Files`, `DumpStack.log.tmp`), system-referenced folders (`protected_by_system`: Windows `PATH`, Services, Installed Apps, Startup, `.lnk` targets, uninstallers), game libraries (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`), active SQL Server databases (`Microsoft SQL Server 2022`), and `smart-drive-os` are permanently protected.

---

## Everyday use

```bash
smart-drive checkup                       # 0–100 health score in plain language, plus copy-pasteable next steps (--lang vi for Vietnamese)
smart-drive sentinel                      # verify mount, filesystem geometry, shields (auto-healed), index, and Git status
smart-drive health                        # SSD health, TRIM check (Windows), partition geometry, and utilization
smart-drive search "llama cat:ai_models"  # SQLite FTS5 with BM25 ranking instead of recursive find/grep (<10ms)
smart-drive dup                           # duplicate files (3-phase SHA-256) and the cluster slack they waste
smart-drive audit                         # storage and cluster slack at the volume's real cluster size
smart-drive audit --html                  # the same as a self-contained offline HTML treemap
```
Junk cleaning has [its own section](#safe-junk-cleaner); snapshots, backup, `.gitignore` governance and the rest are under [More features](#more-features).

<details>
<summary>Search syntax</summary>

The SQLite FTS5 engine (`unicode61` + BM25) executes multi-criteria queries in <10ms:

- **Free Text**: `smart-drive search "machine learning"`
- **File Extension**: `smart-drive search "weights ext:gguf"`
- **Size Filter**: `smart-drive search "dataset size:>100MB"` (`size:<1MB`, `size:0`). `>`/`<` exclude the bound, `>=`/`<=` include it; binary units (`1KB = 1024` bytes). Unreadable filters (`size:>abc`, `size:>10 MB`) emit a warning (CLI stderr, MCP `warnings`) instead of being ignored silently.
- **Taxonomy Category**: `smart-drive search "llama cat:ai_models"`
- **Directory Constraint**: `smart-drive search "assignment dir:FPTU"`
- **Learning Kind**: `smart-drive search --kind slide "vòng lặp"` (or `kind:fact`, `--kind capture`, `--kind lesson`)
- **Compound Query**: `smart-drive search "exam ext:pdf size:>1MB dir:DBI202"`

</details>

All 24 portable launchers (`.bat` for Windows CMD, `.ps1` for PowerShell, `.command` for macOS, `.sh` for Linux/POSIX) sit in both the repo root and `launchers/`, and need no Git:
- `SmartDrive.*`: the full interactive menu, including `[8]` Relink Agents after hand moves, `[9]` Checkup, `[10]` Safe Guided Organizer and `[11]` Relink to the latest move.
- `Smart_Organize.*`: the guided organizer with a step-by-step dry-run preview, busy-app wait, `YES` confirmation, post-move verification and 1-key `U` undo (EN/VI).
- `Quick_Search.*`, `Quick_Clean.*`, `Quick_Audit.*`: an instant interactive FTS5 search prompt, a dry-run junk preview followed by a confirmed Tier 1 deletion, and an instant storage breakdown and cluster slack audit.
- `Setup_SSD.*`: interactive 1-touch initialization and profile selector (see [More features](#more-features)).

`smart-drive self-path-check` checks the running Python environment (venv, pyenv, user scripts) and prints the PowerShell `SetEnvironmentVariable`, `~/.bashrc`/`~/.zshrc` export, or `pyenv rehash` command if `smart-drive` is not on `PATH`.

## More features

<details>
<summary>Drive profiles: init and the Setup_SSD launchers</summary>

Run `Setup_SSD.bat` / `Setup_SSD.ps1` (Windows), `Setup_SSD.command` (macOS), `./Setup_SSD.sh` (Linux), or `smart-drive init --profile <name>`; in one touch it creates the taxonomy folders, installs the anti-indexing shield and seeds the FTS5 database. Pick one of the 5 profiles:
```bash
python -m smart_drive init --profile workstation-hybrid
python -m smart_drive init --profile ai-developer
```

| Profile | Target Architecture | Included Taxonomies & Subdirectories |
|---|---|---|
| **`workstation-hybrid`** *(Recommended)* | Your secondary drive (`D:`, `E:`, …) with games, apps, coursework | `01_AI_Models`, `02_Learning_Knowledge` (`FPTU/`, `Personal_Books/`, `Notes/`), `03_Development_Projects` (`active/`, `archive/`), `04_System_Workspaces`, `05_Dev_Toolbox` (`OEM_Drivers/`, `Installers/`, `scripts/`), `06_Archives_Storage` |
| **`internal-developer-vault`** | Internal NVMe/SATA SSD (`D:`, `E:`, …) for offloaded `C:` caches | `01_AI_Models`, `02_Development_Workspaces`, `03_Data_Vault`, `04_System_Offload_Caches` (`huggingface/`, `ollama/`, `pip/`, `uv/`), `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`ai-developer`** | External SSD for LLMs, fine-tuning, AI agents | `01_AI_Models` (`checkpoints/`, `gguf/`, `safetensors/`, `datasets/`), `02_Learning_Knowledge`, `03_Development_Projects` (`ai_agents/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`data-science`** | Data engineering, pipelines, EDA notebooks | `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects` (`notebooks/`, `data_raw/`, `pipelines/`), `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` |
| **`general-workspace`** *(Default)* | Universal code, docs, notes, utilities | Standard 6 taxonomies with `Notes/`, `References/`, `active/`, `archive/`, `scripts/` |

</details>

<details>
<summary>Gitignore governance</summary>

`smart-drive gitignore` audits and applies stack-aware `.gitignore` rules across Git repos on the drive (<5ms shallow detection for Python, Node.js, Jupyter, Java, Rust, .NET, LaTeX, AI Models, Learning). It maintains an idempotent block between `# >>> smart-drive managed (do not edit inside) >>>` and `# <<< smart-drive managed <<<`, preserving user rules and CRLF/LF line endings:
- **exFAT Cluster Slack Defense**: Ignores `**/.agents/*` (500+ scratch files waste >260 MB on 512 KB clusters; `.agents/skills/`, `.agents/workflows/`, `.agents/rules/` stay tracked), `.claude/settings.local.json`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, `target/`/`dist/`/`bin/`/`obj/`.
- **Constellation `personal-data` Protection**: Locks down `.vega/`, `**/progress.jsonl`, `aurora-progress-*.jsonl`, `orion-attempts.jsonl`, `**/polaris/.cache/`, `**/polaris/captures/`, `.polaris/`, and `.sirius/`, alerting if tracked in public or unconfigured remotes.

```bash
smart-drive gitignore audit                                 # audit own & local repos (--all includes third-party)
smart-drive gitignore apply D:\teamwork_projects\my_project # dry-run preview
smart-drive gitignore apply D:\teamwork_projects\my_project --yes --untrack
```

- **Ownership Classes**: `own` (matches `--owner`, `SMART_DRIVE_GIT_OWNER`, `smartdrive.owner`, or drive consensus), `third-party` (`open_source/`, `vendor/`, `third_party/`, or external owner—upstream `dist/`/`build/` respected), and `local` (no remote).
- **Live GitHub Visibility (`gh`)**: Queries `public`, `private`, `internal`, or `missing` ("remote not found on GitHub") with per-run caching so private/deleted remotes never trigger false public-leak alarms.
- **Intentional Build Outputs & Keep Lists**: Files in `package.json` (`bin`, `main`, `module`, `types`, `exports`), `.gitignore` negations (`!dist/bundle.mjs`), or `smartdrive.keep` / `--keep <pattern>` are never flagged or untracked. Direct intentional files in `dist/` emit `<dir>/*` plus `!<file>` lines (or omit the pattern if nested).
- **High-Severity Secrets & Safe Untracking**: Tracked `.env`, `.env.*`, `*.pem`, `*.key`, and `credentials*.json` alert with **variable/key names only** (no secret values). `--untrack` runs `git rm -r --cached` (keeping disk files) and refuses when uncommitted staged changes exist.

</details>

<details>
<summary>Snapshots and backup</summary>

- **SHA-256 Snapshots (`smart-drive snapshot`)**: Streaming 64 KB-chunk ($O(1)$ memory) point-in-time manifests in `.smart_drive/snapshots/<name>.json` to detect bit rot or tampering.
- **Incremental Backup (`smart-drive backup`)**: Copies modified/new files while excluding OS junk. Default coverage: `02_Learning_Knowledge`, `03_Development_Projects`, `05_Dev_Toolbox` (override with `--partitions`); hidden dirs (`.git`, `.github`, `.vscode`) are skipped unless `--include-hidden` is passed, and every run lists what was not covered.
- **Duplicates (`smart-drive dup`) & Git Clones (`smart-drive repos`)**: 3-phase SHA-256 duplicate finder (hard links counted once; `node_modules`, virtualenvs, caches and installed apps are never scanned) and read-only Git clone auditor checking SSD-vs-machine **mirrors** (`commit`, `push`, `pull`, `fetch to compare`) and same-drive **duplicates** (newest commit to keep, redundant same-branch copies, or `git worktree` with `--relative-paths` on git 2.48+; dirty/unpushed/stashed clones are blocked from removal).

```bash
smart-drive snapshot create pre_release
smart-drive snapshot verify pre_release
smart-drive backup --target E:\Backups\SSD_Mirror --dry-run
```

</details>

<details>
<summary>Workstation hybrid and C: cache offload</summary>

`FilesystemAdapter` and `DriveDetectorBackend` adapt between **exFAT 512 KB** (slack guard, anti-symlink rules, Spotlight/FSEvents shields) and **NTFS 4 KB** (4,096-byte math, `fsutil` TRIM check, `C:` cache offload). `smart-drive offload` migrates heavy caches (HuggingFace, Ollama, PyTorch, Docker WSL2, pip, uv, npm, Conda, Gradle, Cargo) from `C:` to `<target>\04_System_Offload_Caches\<name>` on your secondary drive (`D:`, `E:`, …) in a **7-phase transactional workflow** with running-process detection (`--wait`, `--force`), rollback safety, and unprivileged NTFS Directory Junctions (`mklink /J`). See [README_INTERNAL.md](README_INTERNAL.md).

- **AcademicClassifier (FPTU)**: Extracts course codes (`DBI202`, `WED201c`, `SWE202c`, `PRN211`, `PRJ301`, `OSG`), detects exams/labs (`PE`, `de thi`, `on luyen`, `lab1_sp26`, `sp26`, `fa25`, `su25`), zones into `02_Learning_Knowledge/FPTU/Ky_1/` .. `Ky_7/`, and repairs ZIP mojibake (`h?c k? 3 fptu` $\rightarrow$ `Hoc_Ky_3_fptu`, `n luy?n pe dbi202` $\rightarrow$ `On_Luyen_pe_dbi202`, `k 1 fptu` $\rightarrow$ `Ky_1_fptu`, `dich truyen` $\rightarrow$ `Dich_Truyen`).
- **Non-Code Routing**: `dich truyen` $\rightarrow$ `02_Learning_Knowledge/Personal_Books/dich truyen`, `lenovo` $\rightarrow$ `05_Dev_Toolbox/OEM_Drivers/LENOVO`, `sql2022` $\rightarrow$ `05_Dev_Toolbox/Installers/SQL2022`.
- **AutoZoner Self-Defense & System Reference Guard (`protected_by_system`)**: Anchors `_CURRENT_FILE` through `_SMART_DRIVE_REPO_DIR` and ancestors so `smart-drive-os` never moves itself; pre-filters Windows folders (`WindowsApps`, `Program Files`, `Program Files (x86)`, `$Recycle.Bin`, `System Volume Information`, `WpSystem`, `DeliveryOptimization`, `WUDownloadCache`, `DumpStack.log.tmp`), folders referenced by Windows `PATH`, Services, Installed Apps (`Uninstall` registry), Startup (`Run`), `.lnk` shortcuts, folders with `unins000.exe`/`uninstall.exe`, game libraries (`SteamLibrary`, `Riot Games`, `Epic Games`, `XboxGames`, `GOG Galaxy`, `Battle.net`, `Ubisoft Game Launcher`, `EA Games`, `HoYoPlay`, `Genshin Impact`, `LDPlayer`, `fo4`, `32837`, `Downloads`), and active SQL Server (`MSSQL16.MSSQLSERVER`, `MSSQLSERVER`, `MSSQL`).

</details>

<details>
<summary>Learning materials (Aurora Slides and Polaris)</summary>

SmartDrive-OS integrates with [Aurora Slides MCP](https://github.com/DuongNAD/aurora-slides-mcp) and [Polaris MCP](https://github.com/DuongNAD/polaris-mcp) across the DuongNAD learning ecosystem:

| Project | Role | Repository |
| :--- | :--- | :--- |
| **Polaris** | Course architect: sources, facts with locators, roadmap, schedule, error log | [polaris-mcp](https://github.com/DuongNAD/polaris-mcp) |
| **Aurora Slides** | Keynote-style lessons: quizzes, predict and trace slides, progress events | [aurora-slides-mcp](https://github.com/DuongNAD/aurora-slides-mcp) |
| **Vega** | FSRS memory engine and the single review queue (card inbox) | [vega-mcp](https://github.com/DuongNAD/vega-mcp) |
| **Orion** | Practice labs with real tests, tutoring ladder and PRIMM warm-up | [orion-mcp](https://github.com/DuongNAD/orion-mcp) |
| **Mira** | Code-tracing lab: real Python traces, line-by-line players with prediction gates | [mira-mcp](https://github.com/DuongNAD/mira-mcp) |
| **Rigel** | Grounded tutor: answers from the learner's lesson materials with [ref] citations | [rigel-mcp](https://github.com/DuongNAD/rigel-mcp) |
| **Sirius** | Execution loop: deadlines, triage, today router, session timer, weekly review | [sirius-mcp](https://github.com/DuongNAD/sirius-mcp) |
| **Constellation** | MCP registration, health checks and shared contracts | [constellation-mcp](https://github.com/DuongNAD/constellation-mcp) |
| **SmartDrive-OS** | SSD governance, index and learning search | [smart-drive-os](https://github.com/DuongNAD/smart-drive-os) |

SmartDrive-OS serves Polaris (learning search), Sirius, and Rigel (file discovery), receives post-write updates (`smart-drive update <dir> --quiet`) from Aurora ($\ge$ 0.6.2), Polaris ($\ge$ 0.3.2), and Mira, and enforces the `personal-data` ignore rules of [Constellation's contracts](https://github.com/DuongNAD/constellation-mcp/tree/main/contracts).

| Kind | Target Artifact | Extracted Content |
|---|---|---|
| `slide` | `deck.json` (Aurora Slides) | Slide titles, bullets, speaker notes, key takeaways |
| `lesson` | `course.json` (Aurora Courses) | Lesson syllabus, objectives, prerequisites, modules |
| `module` | `polaris/roadmap.json` (Polaris) | Roadmap modules, milestones and prerequisites |
| `fact` | `polaris/facts.json` (Polaris) | Atomic facts, statements, concepts, confidence scores |
| `source` | `polaris/sources.json` (Polaris) | Bibliographic citations, URLs, author notes, digests |
| `capture` | `polaris/captures/` (Polaris) | Extracted excerpts, highlights, study pages, quotes |
| `outline` | `polaris/lessons/*.outline.md` (Polaris) | Lesson outlines (headings) |
| `research` | `polaris/research/*.md` (Polaris) | Deep research syntheses, literature summaries, analyses |
| `error` | `polaris/errorlog.md` (Polaris) | Bug retrospectives, misconceptions, fixes |
| `learning` | All learning artifacts | Consolidated search across all educational kinds |

Aurora deck/course folders and Polaris subject dirs (`polaris/brief.json`) are pinned as atomic **Learning Units** (`pinned: Aurora course/deck or Polaris subject`): AutoZoner never splits/moves them, Cleaner and Duplicate Detector never delete their files, and legacy `polaris/.cache/**` files are cleaned as Tier-2 dev-cache junk (*"Polaris regenerable API cache (Polaris 0.3.2+ keeps it on C:)"*).

</details>

<details>
<summary>Why exFAT needs this: cluster slack</summary>

High-capacity external SSDs (such as the Kingston XS2000 2 TB) format exFAT with **512 KB (524,288-byte) clusters**:
- Physical allocation is $\lceil \text{File Size} / 524,288 \rceil \times 524,288\text{ B}$ (0 B $\rightarrow$ 0 B; 1 B $\rightarrow$ 512 KB / 99.9998% slack; 10 KB $\rightarrow$ 512 KB / 98.05% slack; 100 KB $\rightarrow$ 512 KB / 80.47% slack; 512 KB + 1 B $\rightarrow$ 1,024 KB / 50% slack).
- A repo with **10,000 source files** (15 MB nominal) consumes **>5.12 GB** on exFAT (>99% cluster slack waste).
- OS indexers (macOS Spotlight `mds`, Windows Search) write hidden metadata (`.DS_Store`, `Thumbs.db`, `.fseventsd`), causing I/O wear and battery drain.
- Internal secondary SSDs (`D:`, `E:`, … on NTFS 4 KB) face `C:` exhaustion from 50–200 GB AI/dev caches while your secondary drive (`D:`, `E:`, …) hosts Windows apps, games, and active SQL Server databases that must never be moved or deleted.

| Dimension | External Portable SSD (exFAT) | Internal Secondary SSD (NTFS) |
|---|---|---|
| **Typical Mount** | `/Volumes/KINGSTON` or `E:\` | `D:\` or `E:\` (Fixed Disk) |
| **Cluster Size** | **512 KB** (524,288 bytes) | **4 KB** (4,096 bytes) |
| **Primary Bottleneck** | Catastrophic cluster slack on small files | C-drive disk exhaustion & cache fragmentation |
| **Symlink Support** | ❌ Unsupported (corrupts cross-platform) | ⚡ Supported via NTFS Junctions (`mklink /J`) |
| **Specialized Strategy** | Bundling, anti-slack auto-zoning, anti-indexing shields | 7-Phase transactional cache offload, service protection |
| **Protected Assets** | Root manifests, scripts, 6 core taxonomies | Windows apps, game libraries, active SQL Server databases |

</details>

## CLI reference

<details>
<summary>All 25 commands and their key arguments</summary>

All commands run via `smart-drive <command>`, `python -m smart_drive <command>`, or `py -m smart_drive <command>` (Windows when `python` is not on `PATH`):

| Command | Key Arguments | Description |
|---|---|---|
| `self-path-check` | *(none)* | Inspects Python CLI script paths on system `PATH` and outputs 1-click setup commands. |
| `checkup` | `--root <path>`, `--dup`, `--lang {en,vi}`, `--json` | 1-screen drive health score (`0–100`), comparison against previous run, and prioritized next-step commands. |
| `init` | `--profile <name>`, `--root <path>`, `--force`, `--json` | 1-touch drive setup, taxonomy creation, anti-indexing shield installation, and FTS5 DB seeding. |
| `status` | `--root <path>`, `--json` | Inspect SSD mount point, detected filesystem, geometry, shield health, and taxonomy status. |
| `audit` | `--root <path>`, `--html [file]`, `--json`, `--markdown`, `--export <file>` | Storage & cluster slack audit using the drive's real cluster size (4 KB NTFS, 512 KB exFAT, etc.). `--html` exports a self-contained offline treemap report (default `<root>/.smart_drive/reports/audit.html`). Hard links counted once; symlinks/junctions skipped and counted. |
| `clean` | `--dry-run` *(default)*, `--apply`, `--tier {1,2,3}`, `--only-tier {1,2,3}`, `--no-quarantine`, `--list-quarantine`, `--restore <batch\|latest>`, `--purge-quarantine`, `--older-than <days>` *(default 30, `0` = every batch)*, `--log`, `--json` | Junk cleaner with mandatory dry-run and inviolable whitelist; it skips installed packages, folders the system depends on and git-tracked entries, and reports what it kept. `--apply` deletes regenerable Tier 1-2 junk permanently and at once (preview first) and quarantines only Tier 3 junk to `<root>/.smart_drive/quarantine/<batch_id>/` (restorable via `--restore`; `--no-quarantine` deletes Tier 3 too). Allocated sizes are worked out at the drive's real allocation unit (512 KB when it cannot be read): `--json` gives each item's `allocated_size` and says which unit as `cluster_size` and `cluster_size_source` (`detected`, or `assumed` when the 512 KB is used). |
| `search` | `<query>`, `--ext <ext>`, `--size <spec>`, `--category <cat>`, `--kind <k>`, `--limit <n>`, `--json` | Sub-10ms SQLite FTS5 multi-criteria query parser with BM25 ranking. |
| `organize` | `--dry-run`, `--apply`, `--clean`, `--json` | Auto-zoning, loose-file relocation, `AcademicClassifier` routing, `protected_by_system` guard, and agent/IDE/shortcut relinking; logs move batches to `.smart_drive/moves.jsonl`. |
| `undo` | `--apply`, `--dry-run`, `--root <path>`, `--json` | Reverses the latest `organize`/`classify` move batch that has not been undone yet, skips items instead of overwriting when the original place is taken, and relinks agents/IDEs/shortcuts back to the original paths (dry-run default). |
| `relink` | `[OLD NEW]`, `--find`, `--apply`, `--ask`, `--wait [MIN]`, `--root <path>`, `--dry-run`, `--json` | Rewrites project/MCP paths in coding agents, VS Code/Cursor/Windsurf/Antigravity (`storage.json` & `state.vscdb`), JetBrains (`recentProjects.xml`), Obsidian (`obsidian.json`), Windows `.lnk` shortcuts, Claude Code history, git worktrees, and `.venv`. |
| `paths` | `--broken`, `--fix`, `--dry-run`, `--root <path>`, `--json` | Scans IDE histories, coding agent configs, Obsidian vaults, Windows `.lnk` shortcuts, and `PATH` for broken paths; suggests where moved folders went and repairs sure matches with `--fix`. |
| `sentinel` | `--root <path>`, `--auto-heal`, `--no-heal`, `--json` | 1-touch health audit (detected filesystem, git repo status) and shield self-healing (alias: `agent-check`). |
| `mcp` | `[{serve,register}]`, `--root <path>`, `--all`, `--auth-token <token>`, `--require-auth` | Starts the JSON-RPC 2.0 stdio MCP server (`serve`) or registers configs for AI agents (`register`). |
| `mcp-config` | `--all`, `--antigravity`, `--claude`, `--cursor`, `--windsurf`, `--workspace`, `--json` | Auto-registers SmartDrive MCP Server in AI coding agent config files. |
| `setup` | `--check`, `--dry-run`, `--json`, `--agents <list>`, `--only <list>`, `--dest <dir>`, `--no-clone`, `--no-health`, `--repair`, `--yes` | Asks which MCP servers to install (Constellation, Polaris, Aurora Slides, Vega, Orion, Sirius, Mira), then fetches them, registers them with installed coding agents (Claude Code, Antigravity, Cursor, Codex), and health-checks them. `--yes` skips the question. Exit code `3` from `--check` = setup needed; `4` = without a terminal and without `--yes`, user approval is needed (nothing installed). |
| `snapshot create` | `[name]`, `--partitions <list>`, `--include-hidden`, `--root <path>`, `--json` | Generates a point-in-time manifest with 64KB-chunk streaming SHA-256 hashes. The allocated sizes in it are at the drive's real cluster (512 KB when it cannot be read), recorded in the manifest as `cluster_size` and `cluster_size_source` (`detected`, or `assumed` when the 512 KB is used; a manifest from before they were recorded reads as 512 KB, `assumed`). |
| `snapshot list` | `--root <path>`, `--json` | Lists recorded point-in-time snapshots with sizes, file counts and the cluster size each was worked out at. |
| `snapshot verify` | `<name>`, `--no-untracked`, `--root <path>`, `--json` | Validates file integrity against snapshot manifest to detect tampering or bit rot. |
| `backup` | `--target <path>`, `--dry-run`, `--hash`, `--partitions <list>`, `--include-hidden`, `--json` | Safe incremental backup copying only modified/new files to target directory. |
| `classify` | `[path]`, `--suggest`, `--dry-run`, `--apply`, `--recursive`, `--json` | Deep content inspection (magic bytes & markers) for AI models, datasets, docs, and code repos. Looks only at the files directly in the scanned folder; `--recursive` also visits subfolders. It never takes apart an app, library or project folder (programs, launcher scripts, manifests, `.git`) or a folder holding a program folder, so a document tree with an installer subfolder is skipped too; each is listed as `skipped_bundles`, and a folder you name gets the same check (only the drive root is exempt; `--no-recursive` is still accepted). Never follows/moves symlinks or junctions, or moves SmartDrive scripts/system files. |
| `offload` | `--scan`, `--move <name>`, `--target <drive>`, `--revert <name>`, `--wait [MIN]`, `--force`, `--dry-run`, `--json` | Developer/AI cache discovery and transactional offloading to your secondary drive (`D:`, `E:`, …) via NTFS junction (Windows) or symlink (macOS/Linux); refuses when cache-using processes are running, or when the list of running programs cannot be read, unless `--wait` or `--force` is passed. |
| `health` | `[drive]`, `--root <path>`, `--json` | SSD health, TRIM check (Windows), partition geometry, and utilization monitor (drive letter on Windows or `/Volumes/MySSD` on macOS/Linux). |
| `dup` | `--root <path>`, `--min-size <bytes>`, `--json` | 3-phase SHA-256 duplicate detector with real-cluster slack reclamation preview: the slack is worked out at the volume's allocation unit (512 KB when it cannot be read) and `--json` says which as `cluster_size` and `cluster_size_source` (`detected`, or `assumed` when the 512 KB is used). Hard links to the same data are one file, never offered for deletion. Dependency, environment and cache trees (`node_modules`, `site-packages`, virtualenvs, folders tagged `CACHEDIR.TAG` such as Cargo's `target`, `__pycache__`, `.git`, …) and folders Windows or installed apps depend on (`protected_by_system`) are not scanned; the plan counts what was skipped. Files are opened only when another file has their size, several at a time, and progress goes to stderr every ~2 s so `--json` on stdout stays parseable. |
| `repos` | `[paths...]`, `--root <ssd>`, `--portable <path>`, `--max-depth <n>`, `--json` | Read-only report of git clones: duplicates in the same place (keep / remove / worktree suggestions) and sync state of SSD-vs-machine mirrors. |
| `gitignore audit` | `[repo]`, `--root <path>`, `--all`, `--owner <name>`, `--keep <pat>`, `--json` | Audit repos for missing `.gitignore` patterns, tracked junk (`.agents`, pycache), and tracked personal learning data in public repos. |
| `gitignore apply` | `<repo>`, `--yes`, `--untrack`, `--keep <pat>`, `--json` | Apply SmartDrive-managed `.gitignore` block for repo stack (dry-run default; `--yes` to apply; `--untrack` to remove matching files from git index). |
| `index` | `--root <path>`, `--db <path>`, `--batch <n>` | Full SQLite FTS5 index creation (>15,000 files/sec). |
| `update` | `[path]`, `--root <path>`, `--db <path>`, `--quiet`, `--json` | Fast $O(1)$ incremental search index synchronization (<2s). |

</details>

## Privacy, Security & Data Isolation

SmartDrive-OS is engineered with a **local-first, zero-trust** architecture:
- **100% Local-Only Operations**: All scanning, SQLite FTS5 indexing, and SHA-256 snapshotting run exclusively on local storage.
- **Zero Telemetry & Zero PII Logging**: No analytics, network beacons, or harvesting of file contents/secrets; only basic metadata is stored in `.smart_drive/index.db`.
- **Air-Gap Ready & Defensive Safeguards**: Zero external pip dependencies (`dependencies = []`), local `stdio` MCP transport, offline `setup --no-clone`, mandatory dry-run cleanup defaults, and an inviolable whitelist (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `README.md`, `PRIVACY.md`).

For full details and compliance specifications, read [PRIVACY.md](PRIVACY.md).

## Testing

2,000+ automated tests run in CI on Windows, macOS and Linux with Python 3.9 and 3.13 using `unittest`:

```bash
python -m unittest discover tests -v
```

Coverage spans gitignore governance (ownership, intentional build outputs, negations, keep lists, `gh` visibility, secret variable names, CRLF preservation, staged-change guard), atomic learning units, system reference protection (`protected_by_system`), agent/IDE/Obsidian/shortcut relinking (`storage.json`, `state.vscdb`, `recentProjects.xml`, `.lnk`, MSIX Claude desktop, `--find`, `--wait`), `undo`, `checkup`, `paths --broken`, `clean` quarantine/restore, HTML treemap audit, `Smart_Organize` (`SD_SELFTEST=1`), cluster geometry (512 B–32 MB), self-defense, and `AcademicClassifier`.

## Contributing & License

- 🌟 **Star & Fork** on GitHub: [DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os) · 🐛 [GitHub Issues](https://github.com/DuongNAD/smart-drive-os/issues) · 🔀 [Pull Requests](https://github.com/DuongNAD/smart-drive-os/pulls) · 📖 [CONTRIBUTING.md](CONTRIBUTING.md)
- 📄 Licensed under the **MIT License** — see [LICENSE](LICENSE).

*Saturn's rings as orderly storage lanes: every file in its lane, across the whole drive.*
