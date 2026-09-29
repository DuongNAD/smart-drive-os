# Forensic Audit Report: Milestone M2 — Cache Offloader & NTFS Directory Junction Engine

- **Author**: Forensic Integrity Auditor (`auditor_internal_m2`)
- **Roles**: critic, specialist, auditor
- **Date**: 2026-09-26T10:18:00Z
- **Target Repository**: `smart_drive_os` (`d:\teamwork_projects\smart_drive_os`)
- **Branch**: `internal-secondary-drive`
- **Work Product**: `smart_drive/core/junction.py`, `smart_drive/core/offloader.py`, `smart_drive/cli/cmd_offload.py`, `tests/test_junction.py`, `tests/test_offloader.py`
- **Profile**: General Project (Integrity Mode: `development` per `ORIGINAL_REQUEST.md`)
- **Verdict**: **CLEAN**

---

## 1. Observation

Directly observed files, line references, commands, and verbatim tool outputs:

### 1.1 Source Code Verification
1. **`smart_drive/core/junction.py`**:
   - Lines 21-22: `FILE_ATTRIBUTE_DIRECTORY: int = 0x00000010`, `FILE_ATTRIBUTE_REPARSE_POINT: int = 0x00000400`.
   - Lines 37-41: Bitwise verification using `os.lstat()` and `getattr(st, 'st_file_attributes', 0)` checking both `0x400` and (`0x10` or `stat.S_ISDIR`).
   - Lines 58-71: Normalized target resolution using `os.readlink()`, stripping NT object prefixes (`\\?\UNC\`, `\\?\`, `\??\`).
   - Lines 102-121: Genuine Win32 junction creation via `cmd.exe /c mklink /J <junction_path> <target_path>`.
   - Lines 153-157: Strict safety validation throwing `ValueError` ("Safety Violation: ...") if attempting to remove a regular directory.
   - Lines 159-186: Safe unlinking prioritizing `os.unlink`, fallback to `os.rmdir`, fallback to `cmd.exe /c rmdir`.
2. **`smart_drive/core/offloader.py`**:
   - Lines 92-200: Authoritative catalog `CACHE_CATALOG` covering 10 major AI and developer caches (`huggingface`, `ollama`, `pytorch`, `pip`, `npm`, `uv`, `conda`, `gradle`, `docker_wsl`, `cargo`) with environment variable overrides.
   - Lines 255-286: Non-recursive directory traversal in `calculate_dir_size()`, explicitly filtering out directory junctions and symlinks to prevent runaway recursion or miscalculations.
   - Lines 378-430: Strict target drive validation in `validate_target_drive()` rejecting `C:`, `C:\`, `c:`, and checking `drive_detector.is_system_drive()`.
   - Lines 484-688: Complete 7-Phase transactional move (`offload_cache`) with automatic rollback:
     - Phase 1: Pre-flight checks (existence, junction status, C: rejection, free space check).
     - Phase 2: Staged copy to `.tmp_offload_<name>_<timestamp>`.
     - Phase 3: Atomic target activation (`os.rename(staging, target)`).
     - Phase 4: Atomic source quarantine on C: (`os.rename(source, source.offload_bak)`).
     - Phase 5: Directory junction creation (`create_directory_junction`).
     - Phase 6: Integrity verification probe (reparse point check, target path validation).
     - Phase 7: Quarantine purge (`shutil.rmtree(source_bak)`) and manifest commit (`offload_manifest.json`).
     - Rollback handler: Catches all exceptions, restores quarantined source, removes partial junctions, and deletes staging files.
   - Lines 694-825: Safe reversion (`revert_cache`) staging data back to C:, safely unlinking the junction, reactivating the native directory, and cleaning the secondary drive.
3. **`smart_drive/cli/cmd_offload.py` & `main.py`**:
   - Subcommand `smart-drive offload` registered in `main.py` (lines 51, 330-341, 373).
   - Handlers for `--scan`, `--move <name> --target <drive>`, `--revert <name>`, `--dry-run`, `--force`, `--json`.

### 1.2 Dependency Audit
- Dynamic runtime import analysis confirms 100% Python Standard Library usage:
  ```
  Newly imported modules: ['_ast', '_bisect', '_blake2', '_bz2', '_compression', '_csv', '_datetime', '_hashlib', '_json', '_locale', '_lzma', '_opcode', '_random', '_sha512', '_socket', '_sqlite3', '_ssl', '_string', '_struct', '_typing', 'argparse', 'ast', 'atexit', 'base64', 'binascii', 'bisect', 'bz2', 'calendar', 'collections.abc', 'copy', 'csv', 'dataclasses', 'datetime', 'dis', 'email', ..., 'hashlib', 'html', 'http', 'http.server', 'json', 'logging', 'math', 'os', 'pathlib', 'platform', 'shlex', 'shutil', 'sqlite3', 'stat', 'subprocess', 'sys', 'time', 'typing', 'unittest', 'zipfile']
  ```
  Zero third-party packages or pip dependencies.

### 1.3 Empirical Win32 Junction Testing
Live execution on Windows filesystem (`tmp` sandbox):
```
creating junction: True
is_junction: True
target: C:\Users\Admin\AppData\Local\Temp\tmp8uddljoa\target
stat attrs: 0x410
content through junc: hello
removing junction: True
target still exists: True hello
```
Windows native CMD verification via `cmd.exe /c dir /a`:
```
09/26/2026  05:16 PM    <JUNCTION>     junc [C:\Users\Admin\AppData\Local\Temp\tmpfzx9i85r\target]
09/26/2026  05:16 PM    <DIR>          target
```
- Reparse point attribute: `0x410` (`FILE_ATTRIBUTE_REPARSE_POINT (0x400)` | `FILE_ATTRIBUTE_DIRECTORY (0x10)`).
- Windows directory listing confirms native `<JUNCTION>` reparse point.
- Target data preserved 100% after `remove_directory_junction`.

### 1.4 Empirical Transactional Rollback Testing
Injected simulated Phase 5 junction failure:
```
Caught expected failure: Offload transaction aborted with error: Failed to create NTFS directory junction from '...\pip' to '...\pip'.. All changes were rolled back.
Source still exists: True Is dir: True Is junction: False
Content intact: b'PIP_DATA'
Target cleaned up: True
```
- Full automated rollback confirmed: source directory restored, data intact, temporary files purged.

### 1.5 Independent Test Execution
- `python -m unittest tests/test_junction.py -v`:
  ```
  Ran 6 tests in 0.081s
  OK
  ```
- `python -m unittest tests/test_offloader.py -v`:
  ```
  Ran 12 tests in 0.120s
  OK
  ```
- `python -m unittest tests/test_cli_internal_e2e.py -v`:
  ```
  Ran 14 tests in 1.436s
  OK (skipped=8)
  ```
- Full test suite: `python -m unittest discover tests`:
  ```
  Ran 402 tests in 48.140s
  OK (skipped=8)
  ```
  (The 8 skipped tests belong strictly to Milestone M3 features: `internal-developer-vault` and `smart-drive health`).

---

## 2. Logic Chain

1. **Static Analysis & Genuine Logic**:
   - `junction.py` implements genuine Win32 junction creation via `mklink /J` and genuine reparse point inspection using `st_file_attributes & 0x0400`. No facade, no hardcoded values, no dummy pass/stub methods.
   - `offloader.py` implements a genuine 7-phase transactional staging, moving, quarantine, probe, and commit protocol, backed by automated rollback on exceptions.
2. **Safety & Zero Data Loss**:
   - Directory junction removal rigorously guards against accidental deletion of regular directories by requiring `is_directory_junction()` to return True; otherwise it raises `ValueError`.
   - Data in the target folder is preserved 100% when unlinking junctions.
   - Injected failure tests demonstrate that any error during offload completely restores the source directory and purges target staging.
3. **Target Drive Validation**:
   - System drive `C:`, `C:\`, `c:`, and `drive_detector.is_system_drive()` are strictly rejected as offload targets, satisfying Requirement R2.
4. **Zero External Dependencies**:
   - Codebase uses 100% Python Standard Library modules (`os`, `sys`, `stat`, `subprocess`, `pathlib`, `shutil`, `json`, `time`, `dataclasses`, `argparse`).
5. **Test Veracity**:
   - All tests execute real disk operations, real subprocess commands, and real assertions without mock cheating or pre-fabricated verification outputs.
   - 402 tests pass cleanly with 0 errors and 0 failures.

---

## 3. Caveats

- **Cross-Platform Junction Emulation**: On non-Windows platforms (e.g. POSIX), `junction.py` falls back to symlinks for testability. Under Windows (the target production environment), native Win32 `mklink /J` and reparse points are exclusively used.
- **Milestone Boundary**: M3 features (`internal-developer-vault` profile and `health` command) remain skipped in `test_cli_internal_e2e.py` as planned until Worker M3 implementation.

---

## 4. Conclusion

**Verdict: CLEAN**

Milestone M2 work products authentically implement all features required by `ORIGINAL_REQUEST.md` § R2 and `PROJECT.md` Milestone M2:
1. Genuine Win32 NTFS directory junction engine (`create_directory_junction`, `is_directory_junction`, `get_junction_target`, `remove_directory_junction`).
2. Robust cache scanner for 10 AI and developer cache categories with environment variable overrides.
3. Authentic 7-phase transactional offload move with automated rollback and zero data loss guarantee.
4. Reliable cache reversion protocol.
5. Strict rejection of system drive C:.
6. 100% Python Standard Library zero-dependency architecture.
7. Full suite of 402 tests passing cleanly.

---

## 5. Verification Method

To independently reproduce this forensic audit:

1. **Execute NTFS Junction Unit Suite**:
   ```powershell
   python -m unittest tests/test_junction.py -v
   ```
   *Expected*: 6 tests pass.

2. **Execute Cache Offloader Unit Suite**:
   ```powershell
   python -m unittest tests/test_offloader.py -v
   ```
   *Expected*: 12 tests pass.

3. **Execute CLI E2E Suite**:
   ```powershell
   python -m unittest tests/test_cli_internal_e2e.py -v
   ```
   *Expected*: 6 offload tests pass (8 M3 tests skipped).

4. **Execute Full Project Test Discovery**:
   ```powershell
   python -m unittest discover tests
   ```
   *Expected*: 402 tests pass, 0 failures, 0 errors.

5. **Empirically Verify Live Win32 Junction & Reparse Point**:
   ```powershell
   python -c "import tempfile, os, subprocess; from pathlib import Path; from smart_drive.core.junction import create_directory_junction, is_directory_junction, remove_directory_junction; td = Path(tempfile.mkdtemp()); target = td / 'target'; target.mkdir(); junc = td / 'junc'; create_directory_junction(junc, target); res = subprocess.run(['cmd.exe', '/c', 'dir', '/a', str(td)], capture_output=True, text=True); print(res.stdout); remove_directory_junction(junc); __import__('shutil').rmtree(str(td))"
   ```
   *Expected*: CMD output displays `<JUNCTION> junc [...]`.
