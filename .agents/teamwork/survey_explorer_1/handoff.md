# Diagnostic & Survey Report: Test Suite & Path Traversal Security

**Agent**: Survey Explorer 1  
**Working Directory**: `/Users/duongnad/Documents/tool/smart-drive-os/.agents/teamwork/survey_explorer_1`  
**Date**: 2026-10-01T07:52:00Z  
**Target Milestone**: Milestone 1 - Test Suite & Path Traversal Security Diagnostic  

---

## 1. Observation

### 1.1 Test Suite Baseline Execution
Executed command:
```bash
python3 -m unittest discover -s tests -v
```
**Results Overview**:
- Total tests: **565**
- Passed: **538**
- Failed: **14**
- Errors: **2**
- Skipped: **11** (Platform-dependent: Windows IOCTL and raw host drive tests skipped on macOS/POSIX)
- Total execution time: **32.46s**

### 1.2 Breakdown of All 14 Test Failures & 2 Errors

#### Group A: Path Traversal, Cross-Drive & Safety Checks in `smart_drive/mcp/server.py` (7 Failures)

1. **`test_cross_drive_path_in_check_safety`** (`tests/test_mcp_adversarial_challenger2.py:180`)
   - Verbatim error:
     ```
     AssertionError: 'error' not found in {'path': 'C:\\Windows\\System32\\notepad.exe', 'is_safe': False, 'is_symlink': False, 'forbidden_character_violations': ['FORBIDDEN_CHAR::'], 'is_protected_root_file': False, 'is_protected_root_dir': False}
     ```
   - Observation: When `cross_path = "C:\\Windows\\System32\\notepad.exe"` is passed to `handle_ssd_check_safety` on a POSIX host, the response lacks the `"error"` key containing `"different drive"`, and instead reports `:` as a forbidden character.

2. **`test_unc_paths_in_check_safety`** (`tests/test_mcp_adversarial_challenger2.py:186`)
   - Verbatim error:
     ```
     AssertionError: True is not false
     ```
   - Observation: `handle_ssd_check_safety({"path": "\\\\remote-server\\share\\exploit.exe"})` returns `is_safe=True`.

3. **`test_directory_escape_attacks_resolve_safe_path`** (`tests/test_mcp_adversarial_challenger2.py:62`)
   - Verbatim error:
     ```
     AssertionError: (<class 'ValueError'>, <class 'FileNotFoundError'>) not raised : Payload 'C:\\' was not blocked!
     ```
   - Observation: In `_resolve_safe_path`, Windows drive escapes (`"C:\\"`, `"C:\\Windows"`, `"D:\\"`, etc.) and UNC paths (`"\\\\attacker\\share\\payload"`) do not raise `ValueError` on POSIX systems.

4. **`test_path_traversal_blocked_in_tools_call_jsonrpc`** (`tests/test_mcp_adversarial_challenger2.py:92`)
   - Verbatim error:
     ```
     Scanner error [NOT_FOUND] on /private/var/folders/.../mock_drive/C:\Windows: [Errno 2] No such file or directory: '/private/var/folders/.../mock_drive/C:\\Windows'
     AssertionError: False is not true : Tool ssd_clean did not set isError=True on traversal
     ```
   - Observation: Invoking `ssd_clean` with `{"sub_dir": "C:\\Windows"}` does not fail at `_resolve_safe_path`. It runs `JunkDetector` on `mock_drive/C:\Windows` and returns `isError=False`.

5. **`test_check_safety_intermediate_path_segments_and_forbidden_chars`** (`tests/test_mcp_grade_a.py:333`)
   - Verbatim error:
     ```
     AssertionError: True is not false
     ```
   - Observation: For `drive_path = "D:\\01_AI_Models\\clean_file.txt"`, `res4["forbidden_character_violations"]` contains `'FORBIDDEN_CHAR::'` because `os.path.splitdrive` on POSIX did not strip `D:`.

6. **`test_resolve_safe_path_traversal_relative_parent_rejected`** (`tests/test_mcp_hardening.py:302`)
   - Verbatim error:
     ```
     AssertionError: ValueError not raised : Should reject escape path: ..\..\Windows\System32
     ```
   - Observation: `..\..\Windows\System32` contains backslashes. On POSIX, `\` is treated as a filename character, so it is joined as `mock_drive/..\..\Windows\System32` without directory traversal.

7. **`test_ssd_check_safety_cross_drive_path_handled_safely`** (`tests/test_mcp_hardening.py:435`)
   - Verbatim error:
     ```
     AssertionError: 'different drive' not found in ''
     ```
   - Observation: For `Z:\foreign_dir\model.gguf`, `handle_ssd_check_safety` returns no error field because `os.path.splitdrive` on POSIX returns `("", path)`.

---

#### Group B: Cross-Platform Drive Letter Detection in `smart_drive/core/drive_detector.py` (2 Failures)

8. **`test_mock_system_drive_reassignment`** (`tests/test_drive_detector.py:327`)
   - Verbatim error:
     ```
     AssertionError: 'C:' != 'E:'
     - C:
     + E:
     ```
   - Observation: `mock.set_system_drive("E:")` followed by `get_system_drive_letter()` returned `'C:'` instead of `'E:'`.

9. **`test_unnormalized_drive_strings_with_non_c_system_drive`** (`tests/test_drive_detector.py:378`)
   - Verbatim error:
     ```
     AssertionError: 'E:' unexpectedly found in ['D:', 'E:', 'F:']
     ```
   - Observation: In `list_secondary_drive_letters()`, with `system_drive="E:"`, `E:` was not filtered out because `get_system_drive_letter()` returned `'C:'`.

---

#### Group C: Dynamic Mount Point Discovery in `smart_drive/mcp/proxy.py` (2 Failures)

10. **`test_detect_mount_point_macos_mock`** (`tests/test_mcp_proxy.py:110`)
    - Verbatim error:
      ```
      AssertionError: '/Users/duongnad/Documents/tool/smart-drive-os' != '/Volumes/KINGSTON'
      - /Users/duongnad/Documents/tool/smart-drive-os
      + /Volumes/KINGSTON
      ```
    - Observation: When `Path.cwd()` was mocked as `Path("C:/MockNonDrive")`, calling `Path.cwd().resolve()` on POSIX resolved it against the real host working directory (`/Users/duongnad/Documents/tool/smart-drive-os`), finding `GEMINI.md` and returning the repository directory instead of proceeding to the macOS `/Volumes/KINGSTON` check.

11. **`test_detect_mount_point_windows_letters_mock`** (`tests/test_mcp_proxy.py:127`)
    - Verbatim error:
      ```
      AssertionError: unexpectedly None
      ```
    - Observation: In `SmartDriveProxy.detect_mount_point()`, line 58 constructs `root_cand = Path(f"{letter}:\\")`. On POSIX, `root_cand / "GEMINI.md"` becomes `Path("E:\\/GEMINI.md")`. When replacing `\\` with `/`, it results in `"e://gemini.md"`, which fails the test mock assertion checking for `"e:/gemini.md"`.

---

#### Group D: Broken Junction Detection in `smart_drive/core/junction.py` (1 Failure)

12. **`test_broken_junction_detection`** (`tests/test_junction.py:194`)
    - Verbatim error:
      ```
      AssertionError: False is not true
      ```
    - Observation: Line 44 of `smart_drive/core/junction.py` checks `os.path.islink(p_str) and (os.path.isdir(p_str) or stat.S_ISDIR(st.st_mode))`. When the junction's target directory is deleted, `os.path.isdir(p_str)` returns `False` because it follows the link, and `stat.S_ISDIR(st.st_mode)` is `False` because `st_mode` is `S_IFLNK`.

---

#### Group E: Cache Path & Target Drive Resolution in `smart_drive/core/offloader.py` (2 Failures)

13. **`test_environment_variable_override_resolution`** (`tests/test_offloader.py:78`)
    - Verbatim error:
      ```
      AssertionError: PosixPath('/var/folders/cd/zk3m8vps03vg88p8k8c3j_gw00[44 chars]ome') != PosixPath('/private/var/folders/cd/zk3m8vps03vg88p8k8[52 chars]ome')
      ```
    - Observation: `custom_hf.resolve()` resolves the macOS symlink `/var` to `/private/var`. `resolve_cache_path` did `Path(os.path.abspath(clean))` without `.resolve()`.

14. **`test_valid_secondary_drive_letter_resolution`** (`tests/test_offloader.py:189`)
    - Verbatim error:
      ```
      AssertionError: 'D:\\/04_System_Offload_Caches' != 'D:\\04_System_Offload_Caches'
      - D:\/04_System_Offload_Caches
      ?    -
      + D:\04_System_Offload_Caches
      ```
    - Observation: `validate_target_drive("D:")` executed `root = Path(f"{letter}\\")` followed by `offload_root = root / "04_System_Offload_Caches"`. On POSIX, `root` is a `PosixPath("D:\\")`, and `/` appends `/`, yielding `"D:\\/04_System_Offload_Caches"`.

---

#### Group F: HTTP Server High-Concurrency Listen Backlog in `smart_drive/ui/server.py` (2 Errors)

15. **`test_concurrent_search_sqlite_lock_resistance`** (`tests/test_ui_adversarial.py:624`)
    - Verbatim error:
      ```
      urllib.error.URLError: <urlopen error [Errno 54] Connection reset by peer>
      ```
16. **`test_high_concurrency_mixed_endpoints`** (`tests/test_ui_adversarial.py:602`)
    - Verbatim error:
      ```
      urllib.error.URLError: <urlopen error [Errno 54] Connection reset by peer>
      ```
    - Observation: In `smart_drive/ui/server.py:37`, `ThreadingHTTPServer` inherits from `socketserver.TCPServer` where default `request_queue_size = 5`. Firing 30 to 50 simultaneous threads overflows the OS listen queue on macOS, triggering TCP RST (`Connection reset by peer`).

---

## 2. Logic Chain

### 2.1 Why `_resolve_safe_path` Failed on Traversal & Drive Attacks
1. `_resolve_safe_path` in `smart_drive/mcp/server.py:492` computed:
   `target = os.path.realpath(os.path.abspath(os.path.join(canonical_root, sub_path_str)))`
2. On POSIX (macOS/Linux), backslashes `\` are not directory separators; they are valid filename characters.
3. Therefore:
   - `os.path.join(canonical_root, "..\\..\\Windows\\System32")` produced `mock_drive/..\..\Windows\System32`.
   - `os.path.join(canonical_root, "C:\\Windows")` produced `mock_drive/C:\Windows`.
   - `os.path.join(canonical_root, "\\\\attacker\\share")` produced `mock_drive/\\attacker\share`.
4. Because all these paths were joined directly onto `canonical_root` as single string components, `os.path.commonpath([canonical_root, target])` returned `canonical_root`.
5. Boundary escape validation failed to detect that these paths represent directory escapes on Windows or foreign mounts.
6. The failure propagated directly into `ssd_clean`, `ssd_audit`, `ssd_search`, and `ssd_update_index`, causing tools to attempt operations on non-existent pseudo-files rather than returning an error with `isError=True`.

### 2.2 Why `handle_ssd_check_safety` Misclassified Cross-Drive & UNC Paths
1. `handle_ssd_check_safety` in `smart_drive/mcp/server.py:734-738` relied on `os.path.join(canonical_root, clean_path)` followed by `os.path.commonpath`.
2. On POSIX, `os.path.splitdrive("C:\\Windows\\System32")` returned `("", "C:\\Windows\\System32")`.
3. In line 772, `path_segments = [p for p in path_no_drive.replace("\\", "/").split("/") ...]`.
   Because `path_no_drive` still contained `"C:"`, `"C:"` became the first element of `path_segments`.
4. `ExFatEngine.audit_forbidden_characters("C:")` flagged the colon `:` as `'FORBIDDEN_CHAR::'`.
5. Because `os.path.commonpath` did not raise `ValueError` on POSIX, `escapes_root` was `False`, so the `"error"` key was not populated.
6. For UNC paths (`\\\\remote-server\\share`), segments `["remote-server", "share"]` contained no forbidden characters, leading to `is_safe=True`.

### 2.3 Why `get_system_drive_letter()` Returned `C:` Instead of `E:`
1. In `smart_drive/core/drive_detector.py:972-975`:
   ```python
   sys_dir = backend.get_system_directory()
   drive_part, _ = os.path.splitdrive(sys_dir)
   if drive_part:
       return normalize_drive_letter(drive_part)
   ```
2. `backend.get_system_directory()` returned `"E:\\Windows\\System32"`.
3. On POSIX, `os.path.splitdrive("E:\\Windows\\System32")` returned `("", "E:\\Windows\\System32")`.
4. `drive_part` was empty `""`, so the branch failed, falling back to line 985 returning `"C:"`.
5. In `smart_drive/core/drive_detector.py:946-953`, `normalize_drive_letter` already contained the cross-platform rule:
   `if len(s) >= 2 and s[0].isalpha() and s[1] == ":": return f"{s[0].upper()}:"`
   Passing `sys_dir` directly to `normalize_drive_letter(sys_dir)` immediately returns `"E:"` on all platforms.

### 2.4 Why `detect_mount_point()` Failed in Mock Environments
1. `SmartDriveProxy.detect_mount_point()` in `smart_drive/mcp/proxy.py:33`:
   `cwd = Path.cwd().resolve()`
2. In the unit test, `Path.cwd` was mocked to return `Path("C:/MockNonDrive")`.
3. On POSIX, `Path("C:/MockNonDrive")` is not absolute. `.resolve()` resolved it relative to the real test execution directory (`/Users/duongnad/Documents/tool/smart-drive-os`).
4. Because the test execution directory contains `GEMINI.md`, the loop matched `p / "GEMINI.md"` immediately, bypassing the Darwin `/Volumes/KINGSTON` probe.
5. In the Windows letter probe, `root_cand = Path(f"{letter}:\\")` on POSIX joined with `"GEMINI.md"` produced `E:\\/GEMINI.md` which transformed into `e://gemini.md`, failing string equality with `e:/gemini.md`.

### 2.5 Why Broken Directory Junctions Were Not Recognized on POSIX
1. `is_directory_junction(path)` in `smart_drive/core/junction.py:44` used:
   `os.path.islink(p_str) and (os.path.isdir(p_str) or stat.S_ISDIR(st.st_mode))`
2. In unit testing on POSIX, a symlink simulates a junction.
3. When the target directory is deleted to simulate a broken junction, `os.path.isdir(p_str)` returns `False` (broken symlink target cannot be stat'd), and `stat.S_ISDIR(st.st_mode)` is `False` (`st_mode` is `S_IFLNK`).
4. Thus, broken junctions returned `False`, violating the invariant that broken reparse points must still be identified as directory junctions.

### 2.6 Why Cache Offloading Paths Contained Extra Slashes
1. In `smart_drive/core/offloader.py:418-419`:
   ```python
   root = Path(f"{letter}\\")
   offload_root = root / "04_System_Offload_Caches"
   ```
2. On POSIX, `root` is a `PosixPath("D:\\")`. The `/` operator appends `/`, resulting in `D:\/04_System_Offload_Caches`.
3. Direct formatting as `Path(f"{letter}\\04_System_Offload_Caches")` avoids the dual slash separator.

### 2.7 Why Concurrency Tests Encountered Connection Reset
1. `ThreadingHTTPServer` inherits from `socketserver.TCPServer`.
2. Python standard library sets `request_queue_size = 5` by default in `TCPServer`.
3. When 30 or 50 worker threads connect simultaneously in `test_ui_adversarial.py`, the backlog queue fills instantly and macOS kernel rejects excess incoming SYN packets with RST.
4. Setting `request_queue_size = 128` allows concurrent bursts to queue safely in kernel space.

---

## 3. Caveats

1. **Hardware-Specific IOCTL Tests (11 Skipped)**:
   The 11 skipped tests in `test_adversarial_filesystem.py` and `test_drive_detector.py` target Win32 kernel32 IOCTL structures (NVMe, SATA, USB descriptors). These tests correctly skip on non-Windows platforms using standard `unittest.skipUnless(sys.platform == "win32")`. They are not failures.
2. **Zero pip Dependencies Invariant**:
   All diagnostic findings and proposed remediations use 100% Python Standard Library modules (`os`, `sys`, `pathlib`, `stat`, `socketserver`). No third-party packages are required.
3. **exFAT Cluster Slack & Invariants**:
   None of the proposed changes modify cluster math, anti-indexing shield logic, or whitelist preservation rules.

---

## 4. Conclusion & Concrete Recommendations

To achieve a 100% pass rate (554 passed, 0 failed, 0 errors, 11 skipped) across macOS, Linux, and Windows, apply the following 7 localized remediations:

### Remediation 1: Harden `_resolve_safe_path` in `smart_drive/mcp/server.py`
**File**: `smart_drive/mcp/server.py` (lines 469–505)  
**Proposed Change**:
```python
    def _resolve_safe_path(self, sub_path: Optional[str], must_exist: bool = False) -> str:
        canonical_root = os.path.realpath(os.path.abspath(self.root))
        if sub_path is None:
            return canonical_root
        
        sub_path_str = str(sub_path).strip()
        if not sub_path_str:
            return canonical_root

        if "\x00" in sub_path_str:
            raise ValueError("Access denied: path contains null byte")

        # Reject UNC or Windows device namespace paths
        if sub_path_str.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\", "\\??\\")):
            raise ValueError("Access denied: UNC or device namespace paths not allowed")

        # Cross-platform drive letter check
        root_drive = canonical_root[:2].upper() if len(canonical_root) >= 2 and canonical_root[0].isalpha() and canonical_root[1] == ":" else ""
        path_drive = sub_path_str[:2].upper() if len(sub_path_str) >= 2 and sub_path_str[0].isalpha() and sub_path_str[1] == ":" else ""
        if path_drive:
            if not root_drive or path_drive != root_drive:
                raise ValueError("Access denied: path is on a different drive mount or escapes storage root")
            sub_path_str = sub_path_str[2:]

        # Normalize backslashes to forward slashes for cross-platform containment check
        norm_sub = sub_path_str.replace("\\", "/")

        if norm_sub.startswith("/"):
            c_root_fwd = canonical_root.replace("\\", "/")
            if norm_sub == c_root_fwd or norm_sub.startswith(c_root_fwd + "/"):
                target = os.path.realpath(os.path.abspath(norm_sub))
            else:
                raise ValueError("Access denied: absolute path escapes storage root")
        else:
            target = os.path.realpath(os.path.abspath(os.path.join(canonical_root, norm_sub)))

        # Verify boundary containment
        try:
            common = os.path.commonpath([canonical_root, target])
            if os.path.normcase(common) != os.path.normcase(canonical_root):
                raise ValueError("Access denied: path escapes storage root")
        except ValueError as err:
            raise ValueError("Access denied: path escapes storage root") from err

        if must_exist and not os.path.exists(target):
            raise FileNotFoundError(f"Path does not exist: {target}")

        return target
```

### Remediation 2: Cross-Platform Drive & UNC Traversal in `handle_ssd_check_safety`
**File**: `smart_drive/mcp/server.py` (lines 717–801)  
**Proposed Change**:
```python
    def handle_ssd_check_safety(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path_str = args.get("path", "")
        if not path_str or not isinstance(path_str, str):
            return {"error": "Missing required argument 'path'"}
        if "\x00" in path_str:
            return {
                "path": path_str,
                "is_safe": False,
                "is_symlink": False,
                "forbidden_character_violations": ["\\x00"],
                "is_protected_root_file": False,
                "is_protected_root_dir": False,
                "error": "Path contains null byte",
            }

        canonical_root = os.path.realpath(os.path.abspath(self.root))
        clean_path = path_str.strip()

        # 1. UNC network paths
        if clean_path.startswith(("\\\\", "//")):
            return {
                "path": path_str,
                "is_safe": False,
                "is_symlink": False,
                "forbidden_character_violations": [],
                "is_protected_root_file": False,
                "is_protected_root_dir": False,
                "error": "Path is a UNC network path escaping drive root",
            }

        # 2. Cross-drive checks
        root_drive = canonical_root[:2].upper() if len(canonical_root) >= 2 and canonical_root[0].isalpha() and canonical_root[1] == ":" else ""
        path_drive = clean_path[:2].upper() if len(clean_path) >= 2 and clean_path[0].isalpha() and clean_path[1] == ":" else ""
        if path_drive and path_drive != root_drive:
            return {
                "path": path_str,
                "is_safe": False,
                "is_symlink": False,
                "forbidden_character_violations": [],
                "is_protected_root_file": False,
                "is_protected_root_dir": False,
                "error": "Path is on a different drive mount or escapes drive root",
            }

        path_no_drive = clean_path[2:] if path_drive else clean_path
        norm_sub = path_no_drive.replace("\\", "/")

        if norm_sub.startswith("/"):
            c_root_fwd = canonical_root.replace("\\", "/")
            if norm_sub == c_root_fwd or norm_sub.startswith(c_root_fwd + "/"):
                escapes_root = False
                normalized_target = os.path.realpath(os.path.abspath(norm_sub))
            else:
                escapes_root = True
                normalized_target = canonical_root
        else:
            raw_target = os.path.join(canonical_root, norm_sub)
            normalized_target = os.path.realpath(os.path.abspath(raw_target))
            try:
                escapes_root = (os.path.commonpath([canonical_root, normalized_target]) != canonical_root)
            except ValueError:
                escapes_root = True

        if escapes_root:
            rel_path = clean_path
        else:
            try:
                rel_path = os.path.relpath(normalized_target, canonical_root)
            except ValueError:
                escapes_root = True
                rel_path = clean_path

        is_symlink = False
        try:
            if (
                ExFatEngine.is_symlink(clean_path)
                or ExFatEngine.is_symlink(normalized_target)
                or os.path.islink(clean_path)
                or os.path.islink(normalized_target)
            ):
                is_symlink = True
        except (OSError, ValueError):
            is_symlink = False

        path_segments = [p for p in norm_sub.split("/") if p and p not in (".", "..")]
        all_forbidden: List[str] = []
        for seg in path_segments:
            violations = ExFatEngine.audit_forbidden_characters(seg)
            for v in violations:
                v_str = str(v)
                if v_str not in all_forbidden:
                    all_forbidden.append(v_str)

        is_prot_file = is_protected_root_file(rel_path)
        is_prot_dir = is_protected_root_dir(rel_path)
        is_safe = (
            len(all_forbidden) == 0
            and not is_symlink
            and not is_prot_file
            and not is_prot_dir
            and not escapes_root
        )
        res = {
            "path": path_str,
            "is_safe": is_safe,
            "is_symlink": is_symlink,
            "forbidden_character_violations": all_forbidden,
            "is_protected_root_file": is_prot_file,
            "is_protected_root_dir": is_prot_dir,
        }
        if escapes_root:
            res["error"] = "Path escapes drive root"
        return res
```

### Remediation 3: Cross-Platform System Directory Parsing in `smart_drive/core/drive_detector.py`
**File**: `smart_drive/core/drive_detector.py` (lines 970–986)  
**Proposed Change**:
```python
def get_system_drive_letter() -> str:
    backend = get_backend()
    try:
        sys_dir = backend.get_system_directory()
        if sys_dir:
            return normalize_drive_letter(sys_dir)
    except Exception:
        pass

    env = os.environ.get("SystemDrive") or os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if env:
        try:
            return normalize_drive_letter(env)
        except ValueError:
            pass

    return "C:"
```

### Remediation 4: Mock CWD & Windows Letter Probe in `smart_drive/mcp/proxy.py`
**File**: `smart_drive/mcp/proxy.py` (lines 32–65)  
**Proposed Change**:
```python
        # 2. Check current working directory or ancestors
        cwd = Path.cwd()
        if sys.platform != "win32" and len(str(cwd)) >= 2 and str(cwd)[1] == ":":
            cwd_cand = cwd
        else:
            try:
                cwd_cand = cwd.resolve()
            except Exception:
                cwd_cand = cwd
        for p in [cwd_cand] + list(cwd_cand.parents):
            if (p / "GEMINI.md").is_file() or (p / "AGENTS.md").is_file():
                return p
            if p.name.lower() == "kingston":
                return p

        # 3. macOS probe
        system = platform.system().lower()
        if "darwin" in system or sys.platform == "darwin":
            if os.path.isdir("/Volumes/KINGSTON"):
                return Path("/Volumes/KINGSTON")
            volumes = Path("/Volumes")
            if volumes.is_dir():
                try:
                    for vol in volumes.iterdir():
                        if (vol / "GEMINI.md").is_file() or (vol / "AGENTS.md").is_file():
                            return vol
                except (PermissionError, OSError):
                    pass

        # 4. Windows drive letter probe
        elif "windows" in system or sys.platform == "win32":
            letters = [l for l in string.ascii_uppercase if l not in ("A", "B", "C")] + ["C"]
            for letter in letters:
                root_cand = Path(f"{letter}:/")
                if root_cand.is_dir():
                    if (root_cand / "GEMINI.md").is_file() or (root_cand / "AGENTS.md").is_file():
                        return root_cand
            if Path("D:/").is_dir() or Path("D:\\").is_dir():
                return Path("D:/")
```

### Remediation 5: Broken Junction Detection on POSIX in `smart_drive/core/junction.py`
**File**: `smart_drive/core/junction.py` (lines 37–45)  
**Proposed Change**:
```python
    if sys.platform == "win32":
        attrs = getattr(st, "st_file_attributes", 0)
        is_reparse = bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
        is_dir = bool(attrs & FILE_ATTRIBUTE_DIRECTORY) or stat.S_ISDIR(st.st_mode)
        return is_reparse and is_dir
    else:
        # Cross-platform fallback for testing on POSIX systems
        if not os.path.islink(p_str):
            return False
        if os.path.exists(p_str):
            return os.path.isdir(p_str)
        # Broken symlink simulating a broken junction
        return True
```

### Remediation 6: Offloader Symlink Resolution & Path Formatting in `smart_drive/core/offloader.py`
**File**: `smart_drive/core/offloader.py` (lines 236 and 417–420)  
**Proposed Change**:
1. Line 236:
   ```python
   p = Path(os.path.abspath(clean)).resolve()
   ```
2. Line 417–420:
   ```python
   if len(clean_ts) <= 3 and (clean_ts.endswith(":") or len(clean_ts) == 1):
       letter = clean_ts[0] + ":"
       offload_root = Path(f"{letter}\\04_System_Offload_Caches")
       return letter, offload_root
   ```

### Remediation 7: Socket Listen Queue Size in `smart_drive/ui/server.py`
**File**: `smart_drive/ui/server.py` (lines 37–41)  
**Proposed Change**:
```python
class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server using daemon threads for clean process shutdown."""
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128
```

---

## 5. Verification Method

### 5.1 Independent Verification Commands
To independently verify the test suite and confirm that all issues are resolved once the recommendations are implemented:

1. **Verify Entire Suite (565 tests)**:
   ```bash
   python3 -m unittest discover -s tests -v
   ```
   **Expected Outcome**: `Ran 565 tests ... OK (skipped=11)` with 0 failures and 0 errors.

2. **Verify Adversarial Path Traversal & Cross-Drive Tests**:
   ```bash
   python3 -m unittest tests/test_mcp_adversarial_challenger2.py -v
   python3 -m unittest tests/test_mcp_hardening.py -v
   python3 -m unittest tests/test_mcp_grade_a.py -v
   ```

3. **Verify Drive Detection & Junctions**:
   ```bash
   python3 -m unittest tests/test_drive_detector.py -v
   python3 -m unittest tests/test_junction.py -v
   python3 -m unittest tests/test_mcp_proxy.py -v
   python3 -m unittest tests/test_offloader.py -v
   ```

4. **Verify UI Concurrency & High Load**:
   ```bash
   python3 -m unittest tests/test_ui_adversarial.py -v
   ```

### 5.2 Invalidation Conditions
- Any change that alters the return schema of the 8 MCP tools (`ssd_search`, `ssd_audit`, `ssd_clean`, `ssd_find_duplicates`, `ssd_update_index`, `ssd_check_safety`, `ssd_status`, `ssd_auto_organize`).
- Any external pip dependencies added to `pyproject.toml` (strictly prohibited by the Zero-Dependencies invariant).
- Any deviation from exFAT 512KB cluster slack accounting and whitelist protection.
