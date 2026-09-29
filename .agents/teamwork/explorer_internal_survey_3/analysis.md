# Technical Investigation Report: Requirement R2 (C-Drive Cache Offloader & Junction Engine) & R5 (Git Branching & Navigation)

**Explorer**: Explorer 3 (Cache Offload & Git Explorer)  
**Date**: 2026-09-26  
**Target Repository**: `smart_drive_os` (`d:\teamwork_projects\smart_drive_os`)  
**Context**: Architecture survey for internal secondary drive (`internal-secondary-drive` branch)

---

## 1. Executive Summary

Requirement R2 and R5 expand SmartDrive-OS from external portable SSD management (exFAT, 512KB clusters) into high-performance workstation internal storage architecture (NVMe/SATA SSDs formatted with NTFS, drive letters D:, E:, etc.).

Key discoveries from empirical testing on Windows:
1. **NTFS Directory Junctions (`mklink /J`) require ZERO Administrator privileges and ZERO Developer Mode privileges**, unlike standard symbolic links (`mklink /D` or `os.symlink`) which fail with `[WinError 1314] A required privilege is not held by the client`.
2. Cross-volume NTFS junctions pointing from `C:` (NTFS) to `D:` (whether NTFS or exFAT) function seamlessly for both read and write operations.
3. In standard Python on Windows, `os.lstat(path).st_file_attributes & 0x0400` identifies reparse points (junctions), `os.readlink(path)` returns the target path, and `os.unlink(path)` / `os.rmdir(path)` cleanly removes the junction without deleting any files in the target directory.
4. On the current host machine, `C:\Users\Admin\.cache\huggingface` was found to already be an NTFS junction pointing to `E:\AI_Models\.cache\huggingface`, proving direct real-world need and validating our detection logic.
5. Current git repository has 314/314 passing unit tests, is cleanly tracked on `main` up to date with `origin/main`, ready for branching to `internal-secondary-drive`.

---

## 2. Requirement R2: C-Drive Cache Offloader & Junction Engine

### 2.1 Known Developer & AI Caches on Windows

Modern AI engineering and software development tools store gigabytes to hundreds of gigabytes of models, packages, and virtual disks inside `%USERPROFILE%` or `%LOCALAPPDATA%` on the system `C:` drive.

| Cache ID | Title | Category | Environment Variable | Default Windows Path | Typical Size Range | Locking Process / Risk |
|---|---|---|---|---|---|---|
| `huggingface` | HuggingFace Hub Cache | AI Models & Weights | `HF_HOME` / `HUGGINGFACE_HUB_CACHE` | `%USERPROFILE%\.cache\huggingface` | 10 GB – 200 GB+ | Python ML processes (PyTorch, transformers) |
| `ollama` | Ollama Model Weights | AI Models & Weights | `OLLAMA_MODELS` | `%USERPROFILE%\.ollama\models` | 5 GB – 100 GB+ | `ollama.exe`, `ollama app.exe` (tray background service) |
| `pytorch` | PyTorch Hub Cache | AI Models & Weights | `TORCH_HOME` | `%USERPROFILE%\.cache\torch` | 2 GB – 50 GB | Active Python training scripts |
| `pip` | pip Package Cache | Package Managers | `PIP_CACHE_DIR` | `%LOCALAPPDATA%\pip\cache` (or `%LOCALAPPDATA%\pip`) | 1 GB – 25 GB | Active `pip install` processes |
| `npm` | npm Global Cache | Package Managers | `npm_config_cache` | `%APPDATA%\npm-cache` | 1 GB – 30 GB | Active `npm install` processes |
| `uv` | uv Package Cache | Package Managers | `UV_CACHE_DIR` | `%LOCALAPPDATA%\uv\cache` (or `%LOCALAPPDATA%\uv`) | 500 MB – 20 GB | Active `uv` commands |
| `conda_user` | Conda User Cache | Package Managers | `CONDA_PKGS_DIRS` | `%USERPROFILE%\.conda\pkgs` | 2 GB – 50 GB | Active conda environments |
| `miniconda` | Miniconda Packages | Package Managers | N/A | `%USERPROFILE%\miniconda3\pkgs` | 2 GB – 50 GB | Active conda / mamba commands |
| `anaconda` | Anaconda Packages | Package Managers | N/A | `%USERPROFILE%\anaconda3\pkgs` | 5 GB – 80 GB | Active conda / mamba commands |
| `gradle` | Gradle Build Cache | Build Systems | `GRADLE_USER_HOME` | `%USERPROFILE%\.gradle\caches` | 2 GB – 40 GB | Gradle daemon (`gradle.exe`, Java daemon) |
| `docker_wsl` | Docker Desktop WSL2 Data | Containerization | N/A | `%LOCALAPPDATA%\Docker\wsl` | 20 GB – 150 GB+ | Docker Desktop service & WSL VM (`ext4.vhdx` locked) |
| `cargo` | Cargo Registry Cache | Package Managers | `CARGO_HOME` | `%USERPROFILE%\.cargo\registry\cache` | 1 GB – 20 GB | Active `cargo build` |

#### Detection Path Resolution Hierarchy
For each cache:
1. Check configured environment variable (e.g. `HF_HOME`, `OLLAMA_MODELS`). If defined and directory exists, use it.
2. Otherwise, check primary default relative path against base environment directory (`%USERPROFILE%`, `%LOCALAPPDATA%`, `%APPDATA%`).
3. If not found, check secondary/alternative path (e.g., `%LOCALAPPDATA%\pip` if `%LOCALAPPDATA%\pip\cache` does not exist).
4. Check if directory exists. If it exists, determine whether it is a physical folder or an existing junction reparse point.

#### Host Machine Empirical Scan Results
Executing our probe script against the actual host machine yielded:
- `huggingface`: Status = **OFFLOADED** (Junction pointing to `\\?\E:\AI_Models\.cache\huggingface`, attributes `0x410`)
- `ollama`: Status = **FOUND** (`C:\Users\Admin\.ollama\models`)
- `pip`: Status = **FOUND** (`C:\Users\Admin\AppData\Local\pip\cache`)
- `uv`: Status = **FOUND** (`C:\Users\Admin\AppData\Local\uv\cache`, 361.0 MB)
- `gradle`: Status = **FOUND** (`C:\Users\Admin\.gradle\caches`, 50.9 MB)
- `cargo`: Status = **FOUND** (`C:\Users\Admin\.cargo\registry\cache`, 318.4 MB)

---

### 2.2 Directory Junction Mechanics on Windows

#### Empirical Comparison: Junction (`mklink /J`) vs Symlink (`mklink /D` / `os.symlink`)
We tested both mechanisms on Windows 11 under a standard, non-elevated Python runtime:

```python
# Test 1: os.symlink
try:
    os.symlink(target, link_path, target_is_directory=True)
except OSError as e:
    # Fails immediately!
    # [WinError 1314] A required privilege is not held by the client
```

```python
# Test 2: cmd.exe /c mklink /J
cmd = ["cmd.exe", "/c", "mklink", "/J", link_path, target]
res = subprocess.run(cmd, capture_output=True, text=True)
# Return code: 0
# Output: Junction created for <link> <<===>> <target>
```

#### Why Directory Junctions are the Superior Choice
1. **Zero Elevation Required**: Windows security model permits any unprivileged user to create NTFS Directory Junctions within user-owned directories. Creating symbolic links requires `SeCreateSymbolicLinkPrivilege` or enabling Windows Developer Mode in system settings.
2. **Transparent Application Support**: Applications (Python, pip, Ollama, Docker, git, npm) use standard Win32 filesystem APIs (`FindFirstFileW`, `CreateFileW`). Junctions are resolved transparently at the Windows kernel I/O subsystem level (`IO_REPARSE_TAG_MOUNT_POINT`), with zero awareness or behavioral changes required by the calling application.
3. **Cross-Volume Support**: An NTFS junction on `C:` can point to any local volume (`D:`, `E:`, etc.), regardless of whether the target volume is formatted as NTFS or exFAT.

#### Python Junction Introspection & Safety APIs
Testing confirmed standard Python 3.9+ behavior on Windows:
- **Detection**:
  ```python
  def is_junction(path: str) -> bool:
      if not os.path.exists(path):
          return False
      try:
          st = os.lstat(path)
          return bool(getattr(st, "st_file_attributes", 0) & 0x0400) # FILE_ATTRIBUTE_REPARSE_POINT
      except OSError:
          return False
  ```
- **Target Extraction**:
  ```python
  def get_junction_target(path: str) -> Optional[str]:
      try:
          raw = os.readlink(path)
          # Clean Win32 NT prefix
          return raw.replace("\\\\?\\", "").replace("\\??\\", "")
      except (OSError, ValueError):
          return None
  ```
- **Safe Junction Removal (Unlinking without touching target data)**:
  `shutil.rmtree(junction)` raises `OSError: Cannot call rmtree on a symbolic link` on Python 3.11+.
  Instead, `os.unlink(junction)` or `os.rmdir(junction)` safely removes the junction link and leaves 100% of target files and directories intact.

---

### 2.3 Target Directory Architecture

Requirement R2 mandates offloaded caches be organized under:
```
<target_drive>:\04_System_Offload_Caches\<name>
```

Where `<name>` corresponds to the cache identifier:
```
D:\04_System_Offload_Caches\
├── huggingface\
├── ollama\
├── pytorch\
├── pip\
├── npm\
├── uv\
├── gradle\
├── conda\
├── docker_wsl\
└── offload_manifest.json
```

#### Relationship with Requirement R3 (`internal-developer-vault`)
In R3, the internal drive is partitioned into 6 top-level taxonomies:
1. `01_AI_Models`
2. `02_Development_Workspaces`
3. `03_Data_Vault`
4. `04_System_Offload_Caches`  <-- Target recipient for R2!
5. `05_Dev_Toolbox`
6. `06_Archives_Storage`

If `04_System_Offload_Caches` does not exist on the target drive, `smart-drive offload` will automatically create it.

#### Transaction Manifest Format (`offload_manifest.json`)
Located at `<target_drive>:\04_System_Offload_Caches\offload_manifest.json`:
```json
{
  "version": "1.0.0",
  "target_drive": "D:\\",
  "updated_at": "2026-09-26T16:30:00Z",
  "caches": {
    "uv": {
      "name": "uv",
      "title": "uv Cache",
      "source_path": "C:\\Users\\Admin\\AppData\\Local\\uv\\cache",
      "target_path": "D:\\04_System_Offload_Caches\\uv",
      "offloaded_at": "2026-09-26T16:30:00Z",
      "size_bytes": 378535936,
      "file_count": 1420,
      "status": "active"
    }
  }
}
```

---

### 2.4 Transactional Safe Movement Protocol (7 Phases, Zero Data Loss)

Because offloading involves cross-volume transfer (C: to D:), `os.rename` cannot be used directly across disk boundaries. To eliminate any possibility of data loss or corrupted links during unexpected termination, power loss, or disk full conditions, the following 7-phase transactional protocol is required:

```
+-----------------------------------------------------------------------------------+
|                           Offload Transaction Workflow                            |
+-----------------------------------------------------------------------------------+
  [Phase 1] Pre-Flight Checks
      │ (Verify source exists & not already junction; target drive != C:; free space)
      ▼
  [Phase 2] Staged Cross-Volume Copy
      │ (shutil.copytree to D:\04_System_Offload_Caches\.tmp_offload_<name>)
      ▼
  [Phase 3] Atomic Target Activation
      │ (os.rename .tmp_offload_<name> -> D:\04_System_Offload_Caches\<name>)
      ▼
  [Phase 4] Atomic Source Quarantine on C:
      │ (os.rename <source> -> <source>.offload_bak_<timestamp> on C:)
      ▼
  [Phase 5] Junction Creation
      │ (cmd.exe /c mklink /J <source> <target>)
      ▼
  [Phase 6] Integrity Verification Probe
      │ (os.readlink, os.path.exists probe through junction)
      ▼
  [Phase 7] Quarantine Purge & Manifest Commit
      │ (shutil.rmtree <source>.offload_bak; write offload_manifest.json)
      ▼
   [DONE] C: space reclaimed, junction fully active!
```

#### Exception Recovery & Rollback Handling
- **Failure in Phase 2 (Copy interrupted / Disk full)**:
  Clean up target staging folder `.tmp_offload_<name>`. Original source on C: remains completely untouched.
- **Failure in Phase 4 / Phase 5 (Junction creation failed)**:
  If junction failed to create, immediately reverse Phase 4 by renaming `<source>.offload_bak` back to `<source>`.
  Target data on D: is removed or preserved as backup.
  Source directory on C: is restored with 100% integrity.
- **Failure in Phase 6 (Probe failed)**:
  Remove incomplete junction via `os.unlink(<source>)`.
  Restore original folder: `os.rename(<source>.offload_bak, <source>)`.

#### Revert Protocol (`--revert <name>`)
Reversing an offload restores the cache as a native local folder on C::
1. Check that source path on C: is currently an active junction.
2. Verify target directory on D: exists and calculate required disk space.
3. Check that C: has sufficient free space (`shutil.disk_usage("C:\\").free > target_size * 1.05`).
4. Copy data from D: to a staging folder on C:: `<source>.revert_staging`.
5. Remove junction link: `os.unlink(<source>)`.
6. Atomic rename on C:: `os.rename(<source>.revert_staging, <source>)`.
7. Purge target folder on D: `shutil.rmtree(<target_path>)`.
8. Update manifest status to `"reverted"`.

---

### 2.5 CLI Interface Specification

Command: `smart-drive offload`

```bash
# 1. Scan C-drive for known heavy developer caches
smart-drive offload --scan
smart-drive offload --scan --json

# 2. Safely move cache to secondary drive and create junction
smart-drive offload --move uv --target D:
smart-drive offload --move ollama --target D: --dry-run
smart-drive offload --move huggingface --target D: --force

# 3. Revert offloaded junction back to native C-drive folder
smart-drive offload --revert uv
smart-drive offload --revert uv --dry-run
```

#### Output Formatting
Table output for `--scan`:
```text
C-Drive Developer Cache Offload Scanner
==========================================================================================
Name         Category            Status        Size (C:)    Target Location
------------------------------------------------------------------------------------------
huggingface  AI Models & Weights OFFLOADED     0 B (linked) E:\AI_Models\.cache\huggingface
ollama       AI Models & Weights FOUND        18.4 GB       C:\Users\Admin\.ollama\models
pytorch      AI Models & Weights NOT FOUND        -         C:\Users\Admin\.cache\torch
pip          Package Managers    FOUND         2.1 GB       C:\Users\Admin\AppData\Local\pip\cache
npm          Package Managers    NOT FOUND        -         C:\Users\Admin\AppData\Roaming\npm-cache
uv           Package Managers    FOUND       361.0 MB       C:\Users\Admin\AppData\Local\uv\cache
gradle       Build Systems       FOUND        50.9 MB       C:\Users\Admin\.gradle\caches
docker_wsl   Containerization    NOT FOUND        -         C:\Users\Admin\AppData\Local\Docker\wsl
cargo        Package Managers    FOUND       318.4 MB       C:\Users\Admin\.cargo\registry\cache
------------------------------------------------------------------------------------------
Total Reclaimable Space on C: 20.9 GB (across 5 detected caches)
To offload: smart-drive offload --move <name> --target <drive_letter>
```

---

## 3. Requirement R5: Git Branching & Navigation

### 3.1 Git Repository Status Audit
- **Current Branch**: `main`
- **Upstream**: Tracking `origin/main` ([DuongNAD/smart-drive-os](https://github.com/DuongNAD/smart-drive-os)), up to date.
- **Recent Commit**: `1949c06 chore(audit): archive final v1.1.0 victory audit reports and verification logs`
- **Test Suite Status**: 314 tests passing (`python -m unittest discover tests` -> `Ran 314 tests in 36.185s, OK`).
- **Working Tree**: Only agent metadata in `.agents/teamwork/` is present. Source tree (`smart_drive/`, `tests/`) is clean.

### 3.2 Branching Workflow
1. Create and switch to new branch:
   ```bash
   git checkout -b internal-secondary-drive
   ```
2. Develop new modules for R1–R5:
   - R1: `smart_drive/core/drive_detector.py` (Fixed Internal vs Removable External, NTFS vs exFAT).
   - R2: `smart_drive/core/offloader.py`, `smart_drive/cli/cmd_offload.py`.
   - R3: `smart_drive/core/initializer.py` (Add `internal-developer-vault` profile).
   - R4: `smart_drive/core/health.py`, `smart_drive/cli/cmd_health.py` (TRIM & S.M.A.R.T monitoring).
   - CLI dispatch updates in `smart_drive/cli/main.py`.
   - Comprehensive test suite in `tests/test_offload.py`, `tests/test_drive_detector.py`, `tests/test_health.py`.
3. Documentation creation:
   - Create `README_INTERNAL.md`.
   - Update `README.md` and `README_VN.md` on `internal-secondary-drive`.
4. Commit and push:
   ```bash
   git add .
   git commit -m "feat(internal): add secondary drive architect, C-drive cache offloader & TRIM health monitor"
   git push -u origin internal-secondary-drive
   ```
5. Navigation link on `main`:
   - Switch back to `main`: `git checkout main`.
   - Add cross-branch navigation banner to `README.md` and `README_VN.md`.
   - Commit and push to `origin main`:
     ```bash
     git commit -am "docs(nav): add navigation banner for internal-secondary-drive branch"
     git push origin main
     ```

### 3.3 Documentation Architecture

#### 1. `README_INTERNAL.md` (Dedicated on `internal-secondary-drive`)
Contents:
- **Title**: SmartDrive-OS Internal Drive Architect & C-Drive Cache Offloader
- **Introduction**: Why dedicated internal secondary drives (NVMe/SATA SSDs) require different architectural strategies than external exFAT drives.
- **Feature Matrix**:
  * C-Drive Cache Offloading (`smart-drive offload`) with NTFS Directory Junctions.
  * 6-Partition Developer Vault (`smart-drive init D: --profile internal-developer-vault`).
  * NTFS Optimization: 4KB clusters, selective search indexing, junction support.
  * Hardware Health & TRIM verification (`smart-drive health D:`).
- **Step-by-Step Offloading Guide**:
  * Scanning caches: `smart-drive offload --scan`
  * Moving AI models (HuggingFace, Ollama): `smart-drive offload --move huggingface --target D:`
  * Moving package caches (pip, uv, npm, gradle): `smart-drive offload --move uv --target D:`
  * Reverting when necessary: `smart-drive offload --revert <name>`
- **Technical Deep Dive**:
  * NTFS Junction (`mklink /J`) internals vs symlinks.
  * Why no administrator elevation is needed.
  * Zero application disruption guarantee.
- **Verification & Testing Instructions**.

#### 2. Cross-Branch Navigation Banner on `main`
Place immediately after the badges in `README.md`:
```markdown
> 💡 **Using an internal secondary SSD (D:, E:, NVMe)?**  
> Check out the specialized branch [`internal-secondary-drive`](https://github.com/DuongNAD/smart-drive-os/tree/internal-secondary-drive) for C-Drive Cache Offloading (`smart-drive offload`), NTFS Directory Junctions (`mklink /J`), SSD TRIM health verification, and the 6-partition `internal-developer-vault` layout!
```

And in `README_VN.md`:
```markdown
> 💡 **Bạn đang sử dụng ổ cứng SSD phụ gắn trong máy (D:, E:, NVMe)?**  
> Hãy khám phá nhánh chuyên biệt [`internal-secondary-drive`](https://github.com/DuongNAD/smart-drive-os/tree/internal-secondary-drive) dành riêng cho việc giải phóng ổ C: (`smart-drive offload`), tạo NTFS Directory Junctions (`mklink /J`), kiểm tra sức khỏe SSD TRIM và bộ quy hoạch 6 phân vùng `internal-developer-vault`!
```

#### 3. Return Navigation Banner on `internal-secondary-drive`
Place in `README.md` and `README_VN.md` on branch `internal-secondary-drive`:
```markdown
> 📦 **Using an external portable SSD (Kingston XS2000 exFAT)?**  
> Switch to the [`main`](https://github.com/DuongNAD/smart-drive-os/tree/main) branch for 512KB cluster slack protection, exFAT anti-indexing shields, portable launchers, and web dashboard!
```

---

## 4. Automated Verification Strategy

To satisfy the zero-dependency Python standard library requirement and guarantee 100% test pass:
1. **Unit Tests (`tests/test_offload.py`)**:
   - Test cache discovery and path resolution (mocking environment variables `USERPROFILE`, `LOCALAPPDATA`, `HF_HOME`, `OLLAMA_MODELS`).
   - Test junction detection (`is_junction`, `get_junction_target`).
   - Test junction creation (`cmd /c mklink /J`) and removal (`os.unlink` / `os.rmdir`) in a temporary sandbox.
   - Test 7-phase offload transaction with rollback simulation (simulating disk errors, missing source, missing target).
   - Test revert transaction and space verification.
   - Test CLI argument parsing (`offload --scan`, `--move`, `--target`, `--revert`, `--dry-run`, `--json`).
2. **Safety Invariants**:
   - Rejection of `C:` drive as target.
   - Pre-flight disk space assertion.
   - Safe unlinking preventing any accidental deletion of target data.
3. **Execution**:
   ```bash
   python -m unittest discover tests
   ```

---

## 5. Artifacts Produced During Survey
- `mock_scanner.py`: Validated cache scanner against live Windows host.
- `test_cross_drive_junction.py`: Validated cross-drive junction between C: and D:.
- `test_junction_spaces.py`: Validated space-handling in Windows junction creation.
- `test_offload_transaction.py`: Validated 7-step move and revert transaction cycles.
