# Handoff Report: Challenger M1-2 — Safety & Security Boundary Verification

**Agent**: Challenger M1-2 (Adversarial Security Critic)  
**Milestone**: M1 (Zero-Dependency Web Dashboard & Visual UI)  
**Date**: 2026-09-26  
**Type**: Hard Handoff (Task Complete)  
**Verdict**: **CONFIRMED**  

---

## 1. Observation

Direct empirical observations from codebase inspection, architectural tracing, and adversarial execution:

1. **Endpoint Implementation (`smart_drive/ui/server.py:313-370`)**:
   - `handle_api_junk_clean()` parses JSON request body containing `tiers`, `dry_run`, and optional `paths`.
   - Lines 342-343:
     ```python
     detector = JunkDetector(self.server.root_path, max_tier=detector_max, cluster_size=self.server.cluster_size)
     items = detector.find_junk()
     ```
   - Lines 346-351:
     ```python
     candidates = [item for item in items if int(item.tier) in selected_tiers]
     if specific_paths is not None:
         path_set = set(specific_paths)
         candidates = [c for c in candidates if c.path in path_set or c.rel_path in path_set]
     ```
   - *Observation*: An adversary cannot inject arbitrary target files via the `paths` parameter unless `JunkDetector` independently identified that target as a valid junk candidate matching declarative rules.

2. **Security Guard Boundaries (`smart_drive/core/purge_engine.py:164-237`)**:
   - `SecurityGuard.is_protected()` enforces multiple inviolable rules:
     - Lines 177-179 & 186-188:
       ```python
       if any(p.lower() == ".git" for p in target_path_obj.parts):
           return True, "Git repository contents are inviolable"
       ```
     - Lines 191-192:
       ```python
       if real_target == self.drive_root or real_target.lower() == self.drive_root.lower():
           return True, "Cannot delete drive root"
       ```
     - Lines 195-197:
       ```python
       is_inside, rel_path = self._normalize_rel_path(real_target)
       if not is_inside:
           return True, "Path escapes drive root boundary"
       ```
     - Lines 210-213:
       ```python
       if base_name.lower() in {ANTI_INDEXING_ROOT_FILE.lower(), "no_log"}:
           return True, f"Anti-indexing marker protected: {base_name}"
       ```
     - Lines 216-224: Protects non-junk user files inside business taxonomy directories (`01_AI_Models` .. `06_Archives_Storage`).
     - Lines 227-228: Protects root files matching exact names (`GEMINI.md`, `CLAUDE.md`, `README.md`, etc.) and glob patterns (`Quick_*.bat`, `smart_*`, etc.).

3. **Purge Engine Dry-Run & Anti-Indexing Restoration (`smart_drive/core/purge_engine.py:387-401, 559-561`)**:
   - Lines 387-401: When `self.dry_run` is `True`, `delete_file` returns a `DeletionRecord(status="SIMULATED", reason="Dry-run simulation (file intact)")` without calling `os.unlink()`.
   - Lines 559-561:
     ```python
     if not self.dry_run:
         self.ensure_anti_indexing()
     ```
   - When a live purge executes, `ensure_anti_indexing()` is unconditionally invoked to recreate `.metadata_never_index` and `.fseventsd/no_log` if an external entity removed them.

4. **Empirical Adversarial Test Suite Execution (`tests/test_ui_security_m1_2.py`)**:
   - Built a comprehensive adversarial test harness (`tests/test_ui_security_m1_2.py`) consisting of 11 targeted test methods across 5 challenge domains:
     ```bash
     python -m unittest tests/test_ui_security_m1_2.py
     ```
   - Verbatim console output:
     ```
     ...........
     ----------------------------------------------------------------------
     Ran 11 tests in 6.058s

     OK
     ```
   - Zero test failures, zero regressions.

---

## 2. Logic Chain

1. **Protected Root Files Inviolability**:
   - *Premise*: An attacker might attempt to supply `"GEMINI.md"`, `"CLAUDE.md"`, `"README.md"`, `"Quick_clean.bat"`, or lowercase/relative variants in the `paths` parameter of `POST /api/junk/clean` with `dry_run: false`.
   - *Observation*: `test_cannot_trick_clean_into_deleting_protected_root_files` injected exact, lowercase, mixed-case, relative (`./`), and absolute paths of all protected root files into `POST /api/junk/clean` with `dry_run: false`.
   - *Deduction*: `JunkDetector` does not recognize these filenames as junk; furthermore, `SecurityGuard.is_protected()` explicitly catches them under Rule 5 (`is_protected_root_file`). SHA-256 hashes of all protected files before and after the API call were verified identical (`OK`). Full cleans across all tiers also left 100% of protected root files intact.

2. **Anti-Indexing Markers Defense & Restoration**:
   - *Premise*: Deletion of `.metadata_never_index` or `.fseventsd/no_log` would allow macOS Spotlight or Windows Search to write junk onto the exFAT drive.
   - *Observation*: `test_cannot_trick_clean_into_deleting_anti_indexing_markers` targeted these files directly via `POST /api/junk/clean`. `test_clean_restores_anti_indexing_markers_if_missing` unlinked the markers prior to calling the endpoint.
   - *Deduction*: Both markers were shielded from deletion (`SecurityGuard` Rule 3). In addition, when markers were intentionally wiped prior to a live purge, `POST /api/junk/clean` triggered `PurgeEngine.ensure_anti_indexing()`, successfully recreating them on disk.

3. **Path Traversal & Host System Isolation**:
   - *Premise*: A malicious actor might submit `../`, `..\`, or absolute host file paths (`/etc/passwd`, `C:\Windows\...`) to escape the SSD volume root and delete arbitrary host files.
   - *Observation*: `test_cannot_delete_files_outside_drive_root_via_path_traversal` placed external canary files (`victim_outside_1.txt`, `victim_outside_2.txt`, `system_important_file.conf`) outside `mock_root` and submitted diverse traversal strings (`../`, `..\`, `....//....//`, absolute paths).
   - *Deduction*: `SecurityGuard._normalize_rel_path()` canonicalizes paths via `os.path.realpath(os.path.abspath(...))` and checks `rel.startswith("..") or os.path.isabs(rel)`. All traversal attempts were blocked. 100% of canary files outside the drive remained intact and unedited.

4. **Inviolable Git Repositories**:
   - *Premise*: Developers frequently have `.git` directories on external drives; deletion of git objects or index would cause catastrophic data loss.
   - *Observation*: `test_git_directory_and_contents_are_strictly_inviolable` tested:
     a) Direct unlinking of `.git`, `.git/config`, `.git/HEAD`, `.git/objects/...`.
     b) Planted legitimate junk-type files inside `.git` (`.git/Thumbs.db`, `.git/.DS_Store`, `.git/temp.tmp`).
     c) Executed full cleans across all tiers (1, 2, 3) with `dry_run: false`.
   - *Deduction*: `FastDirectoryScanner` excludes `.git` from scanning entirely (`scanner_excludes = {".git", ...}`). Additionally, `SecurityGuard.is_protected()` unconditionally blocks any path containing `.git` as a path segment (`"Git repository contents are inviolable"`). 100% of git directories, configs, and internal files were preserved.

5. **Dry-Run Mode Absolute Immutability**:
   - *Premise*: `dry_run: true` must allow safe visual previews in the web dashboard without modifying a single byte on disk.
   - *Observation*: `test_dry_run_strictly_prevents_any_filesystem_modification` created authentic junk items across Tiers 1, 2, and 3 (`.DS_Store`, `Thumbs.db`, `._notes.txt`, `__pycache__/*.pyc`, `*.dmp`, `*.tmp`), recorded their exact byte size, modification timestamp (`st_mtime`), and SHA-256 hash, and invoked `POST /api/junk/clean` with `dry_run: true`.
   - *Deduction*: The response returned `dry_run: true`, reported accurate `nominal_bytes_reclaimed` and `allocated_bytes_reclaimed` with `SIMULATED` status. A post-check confirmed that every file remained on disk with identical size, unchanged `st_mtime`, and matching SHA-256. Subsequent execution with `dry_run: false` cleanly unlinked the junk files, proving the dry-run barrier is strictly enforced.

---

## 3. Caveats

1. **Non-Dict JSON Edge Case**:
   - If a client sends valid JSON that is not a dictionary (e.g. `[1, 2, 3]` or `"string"`), `server.py` line 327 (`req_data.get(...)`) triggers an `AttributeError`, which is caught by the route's `try...except Exception` handler and returns HTTP 500 (`Internal server error`) rather than HTTP 400. This is safely trapped, does not crash the server daemon, and causes zero filesystem destruction.
2. **File Locking under Windows**:
   - If a target junk file is open with exclusive read lock by another Windows process, `os.unlink()` records `FAILED` with the Windows error message, preserving system stability.

---

## 4. Conclusion

**Verdict: CONFIRMED**

The safety and security boundaries of `POST /api/junk/clean` and `PurgeEngine` are robust, empirical, and mathematically sound:
1. **Protected root files** (`GEMINI.md`, `CLAUDE.md`, `README.md`, `Quick_*.bat`, etc.) cannot be deleted via explicit path injection or full purge.
2. **Anti-indexing markers** (`.metadata_never_index`, `.fseventsd/no_log`) are inviolable and automatically restored if missing.
3. **Path traversal attacks** (`../`, `..\`, absolute system paths) are blocked by strict canonicalization.
4. **Git repositories** (`.git` and nested files) are completely excluded from scans and protected by hard security guards.
5. **Dry-run mode** strictly guarantees byte-for-byte and timestamp immutability.

---

## 5. Verification Method

To independently verify Challenger M1-2's findings:

1. **Run the M1-2 Empirical Security Test Suite**:
   ```bash
   python -m unittest tests/test_ui_security_m1_2.py
   ```
   *Expected Output*:
   ```
   ...........
   ----------------------------------------------------------------------
   Ran 11 tests in ~6s

   OK
   ```

2. **Run the Standard UI and Purge Unit Tests**:
   ```bash
   python -m unittest tests/test_ui.py
   python -m unittest tests/test_cleaner.py tests/test_sentinel.py
   ```
   *Expected Output*: All 18 UI tests, 9 cleaner tests, and 7 sentinel tests pass (`OK`).

3. **Inspect Test Code**:
   Review `tests/test_ui_security_m1_2.py` for exact attack vectors and assertion methods.
