# Security policy

SmartDrive-OS moves, cleans and relinks folders on your drives, and it edits the configuration files of your coding agents and IDEs. A mistake in that code can cost data, so the problems listed under [What counts as a security issue](#what-counts-as-a-security-issue) are handled privately until a fix is released.

## Report a problem privately

1. Open a private report: [Report a vulnerability](https://github.com/DuongNAD/smart-drive-os/security/advisories/new) (the repository's **Security** tab, then **Report a vulnerability**). Only you and the maintainer can see it.
2. If you cannot use GitHub, email [smartdrive.os@proton.me](mailto:smartdrive.os@proton.me).

Please do not open a public issue, pull request or discussion about it.

Include what you can:

- the output of `smart-drive doctor --report` (it shows your home folder as `~` and your user name as `<user>`, and holds no file names, tokens or config contents);
- the exact command, or the MCP tool and the arguments your agent sent;
- what happened, and what you expected;
- the smallest way to reproduce it, ideally on a scratch folder of copies.

We acknowledge a report within 48 hours, keep you posted while we work on a fix, and publish the fix in a release together with a GitHub security advisory. Tell us whether you want to be credited in the advisory and the changelog.

## Supported versions

Fixes are released for the latest minor version only.

| Version | Supported |
|---|---|
| 1.4.x | ✅ |
| older than 1.4 | ❌ (upgrade: `pipx upgrade smart-drive-os` or `pip install -U smart-drive-os`) |

## What counts as a security issue

For this tool, a security issue is any of the following, whether it happens by accident or someone can cause it on purpose (with a crafted folder name, a config file or an MCP tool call):

- **Data loss.** SmartDrive-OS deletes or loses something it should keep: `clean` removes a file outside its junk rules, or one that is whitelisted or used by the system; something that should go to the restorable quarantine is deleted for good; `organize`, `classify` or an MCP tool moves something that `undo` cannot put back; or a dry run (the default) changes files.
- **Path escape in MCP tools.** An MCP tool reads, lists, moves or deletes anything outside the drive root its server works on, for example through `..`, an absolute path, a drive-relative path such as `E:x`, a symlink or junction, or a network path.
- **Writing outside the drive.** A command or MCP tool writes outside the drive root anywhere it does not document or you did not name. The documented places are the coding agent and IDE settings that `relink`, `organize --apply`, `undo --apply` and `paths --fix` rewrite (the README lists them, and each config file is copied to `<file>.bak.smart-drive` first), the agent configs that `mcp register` and `setup` add the MCP server to, `~/.smart-drive/mcp` for `setup`, the cache folders `offload` moves, and what you name yourself, for example with `backup --target`, `audit --export`, `audit --html FILE` or `clean --log`.
- **Config corruption.** A coding agent's or IDE's config (`~/.claude.json`, `claude_desktop_config.json`, `~/.cursor/mcp.json`, `~/.codex/config.toml`, VS Code's `storage.json` or `state.vscdb`, JetBrains' `recentProjects.xml`, ...) is left unreadable or truncated, or loses entries SmartDrive-OS had no reason to touch.

A broken [privacy promise](PRIVACY.md) counts too: a network connection other than the `git clone` you approve in `smart-drive setup`, or a `smart-drive doctor --report` that shows a token, a file name from the drive or an unredacted home folder.

These are ordinary bugs, for the [Bug report form](https://github.com/DuongNAD/smart-drive-os/issues/new/choose): wrong sizes or scores, a preview that suggests a bad move (as long as nothing was moved), a crash that changed nothing, a slow scan, a mistake in the documentation.
