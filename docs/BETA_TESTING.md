# Beta testing SmartDrive-OS

Thank you for helping. This checklist takes about 15 minutes. It tries what most needs a real machine: installing SmartDrive-OS, reading a real drive, moving folders and putting them back, and connecting a coding agent. At the end you paste one report into a GitHub issue.

Two rules keep it safe:

- **Only step 5 moves anything, and only inside a scratch folder of copies you make for this test.** Every command that reads or changes a drive names it with `--root`, which wins over a `SMART_DRIVE_ROOT` you may have set. Never point `--apply` at real data during the beta. Besides that, steps 2, 4 and 5 leave small files in a `.smart_drive` folder, and step 6 adds one entry to your coding agents' config files.
- **Nothing leaves your computer.** SmartDrive-OS has no telemetry, and none of its commands below opens a network connection ([PRIVACY.md](../PRIVACY.md)). The only thing you send is the report you choose to paste, and you read it first.

You need Python 3.9 or newer, a terminal, the drive you want to test (an external drive formatted exFAT helps us most) and, for step 6, one coding agent: Claude Code, Claude Desktop, Cursor, Windsurf, Antigravity or Codex.

Set two variables in the terminal you use for every step. `DRIVE` is the drive to test (on Linux often `/media/$USER/<label>`; keep the quotes, a drive name often has a space) and `SCRATCH` is a new folder on it for the copies of steps 4 and 5. The commands below use them as they are in bash, zsh and PowerShell; set them again in a new terminal.

macOS and Linux (bash or zsh):

```bash
DRIVE="/Volumes/T7"
SCRATCH="$DRIVE/sd-beta-scratch"
```

Windows (PowerShell):

```powershell
$DRIVE = "E:\"
$SCRATCH = "E:\sd-beta-scratch"
```

## 1. Install (2 min)

```bash
pipx install smart-drive-os
smart-drive doctor --root "$DRIVE"
```

To get pipx itself, see the notes for your system at the end. `doctor` only reads: it shows the SmartDrive-OS version and how it is installed, Python, your system, the drive's filesystem and cluster size, and which coding agents and IDEs it found. Check that the `Filesystem` line names your drive's filesystem, for example exFAT.

If it answers `invalid choice: 'doctor'`, your copy is older than this checklist: run `pipx upgrade smart-drive-os`, and if that does not help, install the development version with `pipx install --force git+https://github.com/DuongNAD/smart-drive-os.git` (needs git).

## 2. Check the drive (2 min, read-only)

```bash
smart-drive checkup --root "$DRIVE"
```

You get a 0-100 score and three suggested next steps. It reads every folder of the drive (a minute or more on a large one) and writes only its result, to `.smart_drive/checkup.json` on the drive. Look for anything that seems wrong: the filesystem or the free space, junk that is not junk, a "broken path" that works. Do not run the suggested commands with `--apply` on this drive during the beta.

## 3. Preview an organize (2 min, read-only)

```bash
smart-drive organize --root "$DRIVE"
```

It lists the loose folders at the top of the drive and where it would move each one (into the six folders `01_AI_Models` to `06_Archives_Storage`), which folders it keeps in place (`Skipped ... used by ...`), and which coding agents and IDEs would follow the moves. On a drive that is already tidy it ends with `No loose or unorganized items detected`. Nothing moves: this is the preview. The most useful thing you can report is a folder it wants to move that must stay where it is: an installed app or game, a folder a sync client (OneDrive, Dropbox, iCloud Drive, Google Drive) or a virtual machine uses, or a project another tool expects at its path.

## 4. Make a scratch folder of copies (2 min)

Copy two small folders you do not mind about into a new folder on the drive: a small project (with a `package.json`, a `pyproject.toml` or a `.git` folder, for example) and a folder of documents. Copies only, never the originals. Keep the names `demo-project` and `my-notes`.

macOS and Linux:

```bash
mkdir "$SCRATCH"
cp -R ~/path/to/a-small-project "$SCRATCH/demo-project"
cp -R ~/path/to/some-documents "$SCRATCH/my-notes"
```

Windows (PowerShell):

```powershell
New-Item -ItemType Directory $SCRATCH
Copy-Item -Recurse C:\path\to\a-small-project "$SCRATCH\demo-project"
Copy-Item -Recurse C:\path\to\some-documents "$SCRATCH\my-notes"
```

Then record what the copies hold, so you can check later that nothing was lost:

```bash
smart-drive snapshot create before --root "$SCRATCH" --partitions "demo-project,my-notes"
```

`--partitions` names the two folders: without it the snapshot would hold none of your copies (`Files: 0`, `Not covered: demo-project, my-notes`). Its `Files:` line should count the files you copied, not 0.

## 5. Move and move back (3 min, changes only the scratch folder)

Run these one at a time and read each answer:

```bash
smart-drive organize --root "$SCRATCH"
smart-drive organize --root "$SCRATCH" --apply
smart-drive undo --root "$SCRATCH"
smart-drive undo --root "$SCRATCH" --apply
smart-drive snapshot verify before --root "$SCRATCH"
```

They are the preview, the move, the preview of the way back, the way back and the check. What you should see:

- The preview plans 2 relocations (`Total planned relocations: 2 items.`), for example `demo-project` into `03_Development_Projects` and `my-notes` into `02_Learning_Knowledge`. If coding agents are running it also says `Close these before --apply:` and names them. For these fresh copies you can go ahead: it says `Nothing left to change` because no agent remembers them.
- `--apply` prints `Moved items: 2`, and the scratch folder now holds the six `0X_` folders with your copies inside two of them.
- The preview of the way back says `Total items to restore: 2`.
- `undo --apply` prints `Restored items: 2`, and the copies are back where they were.
- `snapshot verify` prints a `Status:` line that says `✓ INTACT`, and `Modified:` and `Missing:` lines that say `0 files`.

The six empty `0X_` folders, two small marker files (`.metadata_never_index` and `.fseventsd/no_log`) and a `.smart_drive` folder (the snapshot and the move journal) stay in the scratch folder; that is expected. Any other result (a refused move, an error, another count, a verify that is not intact) is exactly what we want to hear about.

## 6. Connect a coding agent (3 min)

Quit your coding agent completely, so it cannot write its config over the change, then run:

```bash
smart-drive mcp register --all
```

It adds a `smart-drive` entry for Antigravity, Claude Desktop (and Claude Code's `~/.claude.json` when that file exists), Cursor and Windsurf, keeps every other entry, and creates the config file of an agent that is not installed. To register one agent only, use `--claude`, `--cursor`, `--windsurf` or `--antigravity` instead of `--all`. For Codex, paste the TOML entry from the README's [Use with coding agents (MCP)](../README.md#use-with-coding-agents-mcp) instead.

Start your agent again and ask it:

> Call the smart-drive MCP tool ssd_status and show me its drive_root and mount fields.

It should answer with your drive and its filesystem. In Claude Code, `claude mcp list` also shows whether `smart-drive` is connected. If the answer says `Could not determine the SmartDrive root`, lists several candidate roots or names another drive, tell the server which drive to use: set `SMART_DRIVE_ROOT` to your drive, or add `"--root"` and your drive to the entry's `args` (the README shows how). To undo this step later, delete the `smart-drive` entry from each config file; the command keeps no backup copy.

## 7. File the report (1 min)

```bash
smart-drive doctor --report --root "$SCRATCH"
```

If a step went wrong, use that step's `--root` instead. Read the output first: your home folder shows as `~` and your user name as `<user>`, and it holds the drive path you passed to `--root` but no file names, tokens or config contents. Then:

- Something went wrong: open a [Bug report](https://github.com/DuongNAD/smart-drive-os/issues/new?template=bug_report.yml), one per problem, and paste the report.
- An idea: open a [Feature request](https://github.com/DuongNAD/smart-drive-os/issues/new?template=feature_request.yml).
- Everything worked: open a [blank issue](https://github.com/DuongNAD/smart-drive-os/issues/new) titled `Beta: all steps passed (<your OS>, <filesystem>)` and paste the report. That helps too.
- Lost data, an MCP tool that reached outside the drive, a write outside the drive or a damaged agent config: do not post it publicly; follow [SECURITY.md](../SECURITY.md).

Then delete the scratch folder: it only holds your copies.

## Notes for your system

### Windows

- Use PowerShell, not cmd: the `$DRIVE` and `$SCRATCH` lines above are PowerShell syntax there.
- Install pipx with `py -m pip install --user pipx`, then `py -m pipx ensurepath`, and open a new terminal. No `py`? Install Python from python.org.
- Without `--root`, SmartDrive-OS looks for its drive among D: to Z:, never C:, and only finds one that already has a `.smart_drive` folder or a `.metadata_never_index` file. This checklist passes `--root` every time.
- Before step 5, close File Explorer windows and terminals that have the scratch folder, or a folder inside it, open: Windows refuses to move a folder another program holds. Then `--apply` prints a smaller `Moved items:` count and `Could not move demo-project` with the reason. Close what holds it, run `smart-drive undo --root "$SCRATCH" --apply`, and do step 5 again.
- An antivirus can make the first `checkup` of a large drive slow.

### macOS

- Install pipx with Homebrew (`brew install pipx`, then `pipx ensurepath`) and open a new terminal. Without Homebrew: `python3 -m pip install --user pipx`, then `python3 -m pipx ensurepath`.
- External drives are in `/Volumes/<name>`.
- The first time a command reads the external drive, macOS may ask whether Terminal may access files on a removable volume: allow it, or the drive looks empty or unreadable to SmartDrive-OS.
- On exFAT and other non-Mac drives macOS writes `._name` and `.DS_Store` files next to yours; SmartDrive-OS counts them as junk.
- Apps started from the Dock, such as Claude Desktop, do not see `~/.local/bin`. `smart-drive mcp register` writes full paths, so prefer it to an entry typed by hand.

### Linux

- Install pipx with `sudo apt install pipx` (Debian 12, Ubuntu 23.04 and newer) or `sudo dnf install pipx` (Fedora), then `pipx ensurepath`; elsewhere `python3 -m pip install --user pipx`.
- Removable drives are usually mounted at `/media/$USER/<label>` or `/run/media/$USER/<label>`; `findmnt -T "$DRIVE"` shows the filesystem and the mount options.
- Run every command as yourself, never with `sudo`.
- Under WSL, Windows drives appear in `/mnt/<letter>` through a translation layer: test on Windows itself, or say WSL in your report.

### exFAT drives

- Start with `smart-drive doctor --root "$DRIVE"`: the Filesystem line should say exFAT and give the allocation unit (cluster) size. SmartDrive-OS was designed for 512 KB clusters, but any size works for this test, and the report records yours.
- exFAT has no symbolic links, junctions or hard links, so a moved folder cannot leave a link behind. That is why SmartDrive-OS rewrites the paths agents and IDEs remember, and why it never creates links on the drive.
- exFAT keeps no journal: let every `--apply` finish, and eject the drive before you unplug it (Windows: Safely Remove Hardware; macOS: Eject; Linux: the file manager's eject button). A move cut short by unplugging is worth a report.
- exFAT has no owners or permissions. On Linux the mount options (`uid`, `gid`, `umask`) decide who may write: if a command fails with "Permission denied", mount the drive as your user (the desktop file manager does).
