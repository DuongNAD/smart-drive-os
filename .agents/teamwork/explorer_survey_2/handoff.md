# Handoff Report: Subsystem, Data Flow, Taxonomy & Safe Cleanup Mapping

**Agent**: explorer_survey_2 (Explorer 2: Subsystem & Data Flow Explorer)  
**Parent**: 823718c3-b759-4b3d-905f-b7ec934d7995 (orchestrator)  
**Target Milestone**: SmartDrive-OS v1.1.0 Survey  
**Working Directory**: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_2`  
**Analysis File**: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_2\analysis.md`  

---

## 1. Observation

1. **Taxonomy Structure and Configuration**:
   - In `smart_drive/core/config.py` (lines 111–134):
     `TAXONOMY_ROOT_DIRS` defines the 6 business taxonomies as:
     `"01_AI_Models"`, `"02_Learning_Knowledge"`, `"03_Personal_Documents"`, `"04_Creative_Assets"`, `"05_Dev_Toolbox"`, `"06_Archives_Storage"`.
     `PROTECTED_CORE_TAXONOMIES` harmonizes these with `"03_Development_Projects"` and `"04_System_Workspaces"`.
   - In `smart_drive/core/initializer.py` (lines 18–74):
     Preset profiles (`general-workspace`, `ai-developer`, `data-science`) standardize taxonomies on disk:
     `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects`, `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage`.
   - In `smart_drive/core/config.py` (lines 137–156):
     `PROTECTED_ROOT_DIRS` casefolds all taxonomy names and system folders (`system volume information`, `.fseventsd`, `.agents`, etc.) into a frozen set to guarantee case-insensitive protection on exFAT.

2. **Filesystem Traversal Engine**:
   - In `smart_drive/core/scanner.py` (lines 38–86, 194–318):
     `FastDirectoryScanner.scan_iter()` executes an explicit stack DFS (`while stack: current_dir, depth = stack.pop()`) using `os.scandir(follow_symlinks=False)` in a `with` block.
     `ScanEntry` uses slotted attributes for minimal memory (~112 bytes).
     Symlinks are intercepted and skipped (`if is_sym and not self.follow_symlinks: self.skipped_symlinks.append(entry.path); continue`).
     Exclusions check `self._exclude_dirs_lower` containing `$recycle.bin`, `system volume information`, `.spotlight-v100`, `.trashes`, `.git`, `.agents`.

3. **Safe Cleanup Implementation**:
   - In `smart_drive/core/config.py` (lines 416–481):
     `JunkTier` enum defines:
     `TIER_1_SAFE = 1` (AppleDouble `._*`, `.DS_Store`, `Thumbs.db`, `$RECYCLE.BIN`, `desktop.ini`, `__pycache__`, `*.pyc`).
     `TIER_2_DEV_CACHE = 2` (`.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.coverage`, `build`, `dist`).
     `TIER_3_SENSITIVE = 3` (`*.dmp`, `core.*`, `hs_err_pid*.log`, `*.tmp`, `*.temp`, `*.swp`).
     `match_junk_rule()` enforces that anti-indexing markers (`.metadata_never_index`, `no_log`) and protected root files/directories are never flagged.
   - In `smart_drive/core/purge_engine.py` (lines 114–238, 243–626):
     `SecurityGuard.is_protected()` enforces 6 invariant rules (root cannot be deleted, no root boundary escape, `.git` is inviolable, anti-indexing markers are inviolable, protected root directories cannot be purged, protected root files cannot be purged).
     `PurgeEngine` defaults to `dry_run=True`, returning `DeletionRecord(status="SIMULATED")`.
     When `dry_run=False`, it unlinks files via `_make_writable_and_remove_file` which intercepts `PermissionError` and clears Windows read-only bits via `os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)`.
     Post-purge executes `self.ensure_anti_indexing()` to restore shields if affected.
   - In `smart_drive/cli/cmd_clean.py` (lines 16–80):
     `cmd_clean` defaults to `dry_run=True` (simulating Tier 1). Passing `--apply` activates `PurgeEngine(root, dry_run=False)`.

4. **Existing Hash & Checksum Utilities**:
   - In `smart_drive/core/duplicates.py` (lines 33–75):
     `compute_full_sha256(filepath, chunk_size=65536)` streams 64KB blocks with `hashlib.sha256()`.
     `EMPTY_FILE_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"` is returned for 0-byte files with 0 disk I/O.
     `compute_partial_hash(filepath, chunk_size=8192)` reads head/tail 8KB chunks.

5. **Existing CLI Entrypoint & Tests**:
   - In `smart_drive/cli/main.py` (lines 154–295):
     `build_parser()` registers subparsers: `init`, `status`, `audit`, `clean`, `search`, `organize`, `sentinel`, `mcp`, `mcp-config`, `dup`, `index`, `update`.
   - Running `python -m unittest discover tests` executed 132 tests in 3.575s with 100% pass (exit code 0).

---

## 2. Logic Chain

1. **Taxonomy Naming Reconciliation**:
   - *Observation*: The user prompt mentions taxonomies (`01_System_Boot`, `02_Learning_Knowledge`, `03_Development_Projects`, `04_Archive_ColdStorage`, `05_Dev_Toolbox`, `06_Temporary_Trash`).
   - *Observation*: The codebase (`config.py`, `initializer.py`, `auto_zoner.py`) uses `01_AI_Models`, `02_Learning_Knowledge`, `03_Development_Projects`, `04_System_Workspaces`, `05_Dev_Toolbox`, `06_Archives_Storage` (with aliases `03_Personal_Documents`, `04_Creative_Assets` in `PROTECTED_CORE_TAXONOMIES`).
   - *Deduction*: The existing system's canonical structure uses `01_AI_Models` .. `06_Archives_Storage`. For v1.1.0, the classifier and snapshot modules should operate seamlessly with these canonical taxonomies, treating any alias variations as recognized taxonomy categories in `PROTECTED_CORE_TAXONOMIES`.

2. **Clean Engine Reuse for Web UI (R1)**:
   - *Observation*: `PurgeEngine` supports dry-run simulation and batch execution (`purge_items`), and `JunkDetector` categorizes junk by tier with hardware-accurate slack calculations.
   - *Observation*: `StorageAuditor` calculates full taxonomy breakdowns and cluster slack, while `SearchEngine` executes instant FTS5 queries in <10ms.
   - *Deduction*: The Web UI (`smart-drive ui`) can cleanly import and invoke `StorageAuditor`, `SearchEngine`, `JunkDetector`, and `PurgeEngine` without rewriting any core logic, exposing them via standard library `http.server` JSON endpoints (`/api/stats`, `/api/search`, `/api/junk`, `/api/purge`).

3. **Snapshot Engine (R2)**:
   - *Observation*: `compute_full_sha256` in `smart_drive/core/duplicates.py` streams 64KB blocks to generate SHA-256 digests.
   - *Observation*: `FastDirectoryScanner` traverses directories without symlink risks, generating normalized relative paths with forward slashes.
   - *Deduction*: A new module `smart_drive/core/snapshot.py` can compose `FastDirectoryScanner` and `compute_full_sha256` to create point-in-time JSON manifests for `02_Learning_Knowledge`, `03_Development_Projects`, and `05_Dev_Toolbox`. Verification is simply re-computing hashes and comparing against the stored manifest entries.

4. **Classifier Engine (R3)**:
   - *Observation*: `AutoZoner` in `smart_drive/core/auto_zoner.py` contains basic classification rules for extensions and project indicator files (`.git`, `package.json`, `pyproject.toml`).
   - *Observation*: Requirement R3 requires deep classification (GGUF, Safetensors, ONNX, PyTorch, HF configs; Parquet, Arrow, JSONL, CSV; Git/Node/Python/Rust repos).
   - *Deduction*: A dedicated classifier `smart_drive/core/classifier.py` should expand upon `AutoZoner`'s classification heuristics, providing `--suggest` and `--apply` modes, directing files into granular sub-taxonomies (e.g. `01_AI_Models/safetensors/`, `01_AI_Models/gguf/`, `01_AI_Models/datasets/`, `02_Learning_Knowledge/Notes/`).

---

## 3. Caveats

1. **Network Independence**: The Web Dashboard (`smart-drive ui`) must embed all HTML, CSS, and JavaScript directly into the Python handler or local asset file. It cannot reference external CDNs (such as Tailwind or Bootstrap CDNs) since SmartDrive-OS enforces a strict offline / zero-external-dependency requirement.
2. **Snapshot Performance on Large Models**: Computing full SHA-256 on multi-gigabyte models (e.g., 20GB+ GGUF files in `01_AI_Models`) can take tens of seconds of disk I/O. Partition targeting should focus primarily on `02_Learning_Knowledge`, `03_Development_Projects`, and `05_Dev_Toolbox` as specified in Requirement R2, while supporting optional model verification.
3. **No Code Modification During Survey**: In accordance with the Explorer archetype constraints, no source files were modified during this investigation.

---

## 4. Conclusion

The SmartDrive-OS codebase possesses an exceptionally solid architectural foundation:
- Pure Python standard library with zero external dependencies.
- Hardware-accurate 512KB exFAT allocation math and robust path normalization.
- Inviolable security boundaries in `SecurityGuard` preventing accidental deletion of root scripts, manifests, Git repositories, anti-indexing shields, or business taxonomies.
- 3-tier junk detection with default dry-run simulation.
- High-speed iterative stack traversal and streaming SHA-256 calculation.

All three required v1.1.0 subsystems (`ui`, `snapshot`, `classify`) have clean, direct integration points with existing modules:
1. `smart-drive ui` -> wraps `http.server`, calls `StorageAuditor`, `SearchEngine`, `JunkDetector`, `PurgeEngine`.
2. `smart-drive snapshot` -> uses `FastDirectoryScanner`, `compute_full_sha256`, `normalize_rel_path`.
3. `smart-drive classify` -> builds upon `AutoZoner` and `config.py` rules for deep AI/dataset/project classification.

---

## 5. Verification Method

To independently verify all findings in this report:

1. **Verify Existing Tests**:
   ```powershell
   python -m unittest discover tests
   ```
   *Expected outcome*: 132 tests pass with 0 errors.

2. **Inspect Subsystem Core Modules**:
   - Taxonomy definitions & rules: `d:\teamwork_projects\smart_drive_os\smart_drive\core\config.py`
   - Filesystem scanner: `d:\teamwork_projects\smart_drive_os\smart_drive\core\scanner.py`
   - 3-Tier junk detection: `d:\teamwork_projects\smart_drive_os\smart_drive\core\junk_detector.py`
   - Safe purge engine & boundary guard: `d:\teamwork_projects\smart_drive_os\smart_drive\core\purge_engine.py`
   - Duplicate detection & SHA-256 hashing: `d:\teamwork_projects\smart_drive_os\smart_drive\core\duplicates.py`
   - Auto-zoner & taxonomy routing: `d:\teamwork_projects\smart_drive_os\smart_drive\core\auto_zoner.py`
   - Storage auditor & 512KB cluster slack: `d:\teamwork_projects\smart_drive_os\smart_drive\core\auditor.py`
   - CLI dispatch: `d:\teamwork_projects\smart_drive_os\smart_drive\cli\main.py`

3. **Verify Detailed Analysis**:
   Inspect `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_survey_2\analysis.md`.
