# Handoff Report: Requirement R2 (C-Drive Cache Offloader) & R5 (Git Branching & Navigation)

**From**: Explorer 3 (`explorer_internal_survey_3`)  
**To**: Orchestrator (`orchestrator_internal`) / Parent Agent  
**Date**: 2026-09-26  
**Status**: Survey & Investigation Complete  

---

## 1. Observation

1. **Permission Behavior on Windows Reparse Points**:
   - Running `os.symlink(target, link_path, target_is_directory=True)` as a standard user in Windows 11 raises:
     ```
     OSError: [WinError 1314] A required privilege is not held by the client: '<target>' -> '<link>'
     ```
   - Running `cmd.exe /c mklink /J <link> <target>` succeeds without elevation (exit code 0):
     ```
     Junction created for C:\...\link <<===>> D:\...
     ```
   - Observed in empirical tests executed via `.agents/teamwork/explorer_internal_survey_3/test_cross_drive_junction.py`.

2. **Reparse Point Attributes & Inspection**:
   - `os.lstat(junction_path).st_file_attributes` contains `0x0400` (`FILE_ATTRIBUTE_REPARSE_POINT`).
   - `os.readlink(junction_path)` returns the target path prefixed with `\\?\` or `\??\` (e.g., `\\?\D:\test_junction_probe_sd`).
   - `shutil.rmtree(junction_path)` on Python 3.11+ raises:
     ```
     OSError: Cannot call rmtree on a symbolic link
     ```
   - `os.unlink(junction_path)` and `os.rmdir(junction_path)` remove the junction link without touching target files.

3. **Live Host Environment Cache Audit**:
   - Executing `mock_scanner.py` on the live Windows host observed:
     * `huggingface`: At `C:\Users\Admin\.cache\huggingface`, already an NTFS Junction pointing to `\\?\E:\AI_Models\.cache\huggingface` (`st_file_attributes = 0x410`).
     * `uv`: Found at `C:\Users\Admin\AppData\Local\uv\cache`, size 361.0 MB.
     * `cargo`: Found at `C:\Users\Admin\.cargo\registry\cache`, size 318.4 MB.
     * `gradle`: Found at `C:\Users\Admin\.gradle\caches`, size 50.9 MB.
     * `ollama`: Directory exists at `C:\Users\Admin\.ollama\models`.
     * `pip`: Directory exists at `C:\Users\Admin\AppData\Local\pip\cache`.

4. **Cross-Volume Filesystem Behavior**:
   - `C:` is formatted as NTFS (`GetVolumeInformationW` returns `fs=NTFS, flags=0x3e72eff`).
   - `D:` is formatted as exFAT (`GetVolumeInformationW` returns `fs=exFAT, vol=KINGSTON, flags=0x20206`).
   - An NTFS Directory Junction created on `C:` successfully resolves and links to directory targets on `D:`, verifying cross-volume compatibility regardless of target filesystem format.

5. **Current Git Status & Test Suite**:
   - `git status` output:
     ```
     On branch main
     Your branch is up to date with 'origin/main'.
     ```
   - Running `python -m unittest discover tests` resulted in:
     ```
     Ran 314 tests in 36.185s
     OK
     ```
   - No tracked project source files are modified; repository is in a pristine state on `origin/main`.

---

## 2. Logic Chain

1. **Why Junctions Must Be Used Instead of Symlinks**:
   - Direct observation #1 proves `os.symlink` fails with `[WinError 1314]` unless the user runs an elevated administrator shell or enables Windows Developer Mode.
   - In contrast, `cmd.exe /c mklink /J` succeeds without administrator privileges.
   - Therefore, the offloader must use `cmd.exe /c mklink /J` to ensure seamless execution for all standard users.

2. **Why Cross-Volume Moves Require a 7-Phase Transaction**:
   - Cross-volume movement cannot use atomic `os.rename` across physical disks (`C:` to `D:`).
   - Direct observation #2 shows that standard `shutil.rmtree` cannot be called on junctions and unlinking must be done deliberately.
   - If a cross-volume copy fails midway, files on `C:` must remain intact.
   - By copying to `<target_drive>:\04_System_Offload_Caches\.tmp_offload_<name>`, atomically activating it via `os.rename` on the target volume, quarantining the source on `C:` (`os.rename(<source>, <source>.offload_bak)`), creating the junction, and only then purging the quarantine, zero data loss is mathematically guaranteed.

3. **Why Existing Junctions Must Be Guarded**:
   - Direct observation #3 proved that users already create junctions manually (e.g. `C:\Users\Admin\.cache\huggingface -> E:\AI_Models\.cache\huggingface`).
   - If `smart-drive offload` blindly moves an existing junction, it would attempt to copy through the link or damage the reparse point.
   - Therefore, detection logic must inspect `st_file_attributes & 0x0400` and report `OFFLOADED` status, refusing to re-move unless explicitly forced.

4. **Why Git Branching & Navigation Strategy is Sound**:
   - Direct observation #5 shows the repository on `main` is clean with 314 passing tests.
   - Branching `git checkout -b internal-secondary-drive` allows dedicated development of the internal drive architect features without destabilizing the released v1.1.0 codebase.
   - Adding a navigation banner on `main` ensures discoverability for internal SSD users while preserving `main` as the default portable external SSD version.

---

## 3. Caveats

1. **File Locking by Background Daemons**:
   - Certain applications (specifically Docker Desktop WSL2 `ext4.vhdx`, Ollama service `ollama.exe`, or active IDE gradle daemons) maintain exclusive file handles on Windows (`ERROR_SHARING_VIOLATION` / `WinError 32`).
   - If a file is locked during staging copy, the copy operation will fail. The pre-flight check should warn users if known background daemons are running or graceful error handling must cleanly abort and clean up staging directories.
2. **exFAT Cluster Slack on Secondary Drive**:
   - Although junctions point transparently from `C:` (NTFS) to `D:`, if `D:` is formatted as exFAT with 512KB clusters, offloading millions of tiny cache files (like npm or gradle caches) would trigger exFAT cluster slack waste.
   - The offloader should inform users whether the target drive is NTFS (recommended for caches, 4KB clusters) or exFAT (warn about slack).
3. **No Code Written Outside Agent Directory**:
   - In accordance with read-only explorer constraints, no modifications to `smart_drive/` or repo files were made; all proof-of-concept tests were contained within `.agents/teamwork/explorer_internal_survey_3/`.

---

## 4. Conclusion

Requirements R2 and R5 are thoroughly vetted, fully feasible, and ready for implementation:
1. **R2 Cache Offloader**: Implement `smart_drive/core/offloader.py` and `smart_drive/cli/cmd_offload.py` supporting `--scan`, `--move <name> --target <drive>`, and `--revert <name>` using NTFS directory junctions (`mklink /J`) and a 7-phase safe transactional protocol.
2. **R5 Git & Navigation**: Create branch `internal-secondary-drive`, implement features, provide `README_INTERNAL.md` and dual-branch navigation banners, and verify 100% pass across all tests.

---

## 5. Verification Method

1. **Verify Test Suite**:
   ```bash
   python -m unittest discover tests
   ```
   *Expected result*: 314 tests pass, OK.

2. **Verify Junction Creation Without Elevation**:
   ```bash
   python .agents/teamwork/explorer_internal_survey_3/test_cross_drive_junction.py
   ```
   *Expected result*: Return code 0, probe file read through junction, file on D: intact, junction unlinked cleanly.

3. **Verify Safe Move & Revert Transaction**:
   ```bash
   python .agents/teamwork/explorer_internal_survey_3/test_offload_transaction.py
   ```
   *Expected result*: `Move transaction SUCCESS!` and `Revert transaction SUCCESS!`.

4. **Verify Host Cache Scanning**:
   ```bash
   python .agents/teamwork/explorer_internal_survey_3/mock_scanner.py
   ```
   *Expected result*: Outputs formatted table with `huggingface` detected as `OFFLOADED`, and `uv`, `cargo`, `gradle`, `pip`, `ollama` detected accurately.
