# Comprehensive Survey Report: Hardware & Filesystem Abstraction (R1)

**Agent:** Explorer 1 (Survey: Hardware & Filesystem Abstraction)  
**Date:** 2026-10-01  
**Status:** Completed  
**Target Requirement:** R1 from `ORIGINAL_REQUEST.md` (Workstation Hybrid Upgrade)

---

## 1. Executive Summary

This investigation analyzed the SmartDrive-OS codebase and test suites to resolve cross-platform assumptions that cause test regressions on Windows systems with fixed internal NTFS secondary drives (`D:\`), environment variable clearance under Python 3.13, and unprivileged execution on Windows without symlink elevation permissions.

### Key Findings Matrix
| Target Module | Current Issue | Root Cause | Proposed Solution |
| :--- | :--- | :--- | :--- |
| `tests/test_drive_detector.py` (lines 252-261) | Hardcoded assertion that `D:` is always `FilesystemType.EXFAT`, 524,288 bytes cluster size, `DriveType.REMOVABLE_EXTERNAL`, and `bus_type == "USB"`. | Assumed test host is exclusively equipped with Kingston XS2000 external SSD. Fails on Windows workstations where `D:` is a fixed internal NTFS SSD partition. | Use dynamic inspection via `inspect_drive("D:")` or iterate `secondary_drives` to assert properties conditionally based on actual filesystem format (`NTFS` vs `exFAT`). |
| `tests/test_adversarial_filesystem.py` (lines 382-390) | Blindly runs `cmd /c mklink /J "D:\__test_junc_probe__" ...` if `os.path.exists("D:\\")` and asserts `returncode != 0`. | Assumed `D:` is always an exFAT drive where `mklink /J` fails. If `D:` is an NTFS drive, `mklink /J` succeeds (`returncode == 0`), causing a test failure and leaving a junction behind. | Dynamically check via `inspect_drive("D:")` whether the volume is actually `FilesystemType.EXFAT` before probing junction rejection. |
| `smart_drive/mcp/registrar.py` (lines 24 & 66) | Direct unhandled call to `Path.home()` in `get_agent_config_paths()` and `detect_installed_agents()`. | In Python 3.13 on Windows (or stripped environments where `USERPROFILE` / `HOME` are cleared), `Path.home()` raises `RuntimeError: Could not determine home directory`. | Wrap `Path.home()` in a `try...except` helper `_safe_home_dir()` falling back to `os.environ.get("USERPROFILE")` -> `os.environ.get("HOME")` -> `Path("C:/Users/Default")` on Windows or `Path("/tmp")` on POSIX. |
| `tests/test_mcp_adversarial_challenger1.py` (lines 364-380) | `test_broken_junction_detection_on_posix` calls `os.symlink()` directly without elevation guards. | On Windows without Developer Mode or administrator privileges, calling `os.symlink` raises `OSError: [WinError 1314] A required privilege is not held by the client`. | Decorate the test method with `@unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")`. |
| `smart_drive/core/offloader.py` (line 210) | Secondary discovery: `user_str = mock_user or os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())` lacks `try...except`. | If all environment variables are wiped on Python 3.13, `str(Path.home())` could raise `RuntimeError`. | Apply safe home resolution fallback pattern to prevent unexpected regressions. |

---

## 2. Baseline Test Suite Status

The complete test suite was executed to establish an authoritative baseline:
```bash
python3 -m unittest discover tests
```
- **Total Tests:** 697
- **Passed:** 686
- **Skipped:** 11 (Windows-specific host tests skipped on macOS APFS environment)
- **Failures:** 0
- **Errors:** 0
- **Duration:** 34.47s

The existing suite is currently green on macOS. The survey targets the latent breakage that triggers when running the suite on a real Windows workstation having `D:\` as an internal NTFS drive, without symlink privilege elevation, or under Python 3.13 environment wiping.

---

## 3. Deep Dive 1: Hardcoded Drive D: Assumptions

### 3.1 `tests/test_drive_detector.py`
- **Location:** Lines 244–271
- **Class / Method:** `TestRealHostInspection.test_inspect_existing_secondary_drives`

#### Current Implementation (Lines 244–271):
```python
    def test_inspect_existing_secondary_drives(self):
        available = list_secondary_drive_letters()
        if not available:
            self.skipTest("No secondary drive available on this host.")

        secondary_drives = list_secondary_drives()
        self.assertGreaterEqual(len(secondary_drives), 1)

        # If D: is Kingston XS2000 external SSD, verify USB detection
        d_matches = [d for d in secondary_drives if d.drive_letter.upper() == "D:"]
        if d_matches:
            d_info = d_matches[0]
            self.assertFalse(d_info.is_system_drive)
            self.assertEqual(d_info.filesystem, FilesystemType.EXFAT)
            self.assertEqual(d_info.cluster_size_bytes, 524288)
            self.assertEqual(d_info.hardware_type, DriveType.REMOVABLE_EXTERNAL)
            self.assertEqual(d_info.bus_type.upper(), "USB")

        # If E: is Crucial internal NVMe SSD, verify NVMe detection
        e_matches = [d for d in secondary_drives if d.drive_letter.upper() == "E:"]
        if e_matches:
            e_info = e_matches[0]
            self.assertFalse(e_info.is_system_drive)
            self.assertEqual(e_info.filesystem, FilesystemType.NTFS)
            self.assertEqual(e_info.cluster_size_bytes, 4096)
            self.assertEqual(e_info.hardware_type, DriveType.FIXED_INTERNAL)
            self.assertEqual(e_info.bus_type.upper(), "NVME")
```

#### Vulnerability Analysis:
1. When a user runs tests on a Windows workstation where `D:` is an internal fixed drive (e.g. secondary NVMe or SATA SSD partitioned as `D:` with NTFS format and 4096-byte clusters):
   - `d_matches` finds drive `D:`.
   - `self.assertEqual(d_info.filesystem, FilesystemType.EXFAT)` raises `AssertionError: <FilesystemType.NTFS: 'NTFS'> != <FilesystemType.EXFAT: 'exFAT'>`.
   - `self.assertEqual(d_info.cluster_size_bytes, 524288)` raises `AssertionError: 4096 != 524288`.
   - `self.assertEqual(d_info.hardware_type, DriveType.REMOVABLE_EXTERNAL)` raises `AssertionError: <DriveType.FIXED_INTERNAL: 'fixed_internal'> != <DriveType.REMOVABLE_EXTERNAL: 'removable_external'>`.
   - `self.assertEqual(d_info.bus_type.upper(), "USB")` raises `AssertionError: 'NVME' != 'USB'`.
2. Similarly for `E:`, assuming `E:` is always Crucial NVMe NTFS will fail if `E:` is an external USB backup drive or optical drive.

#### Proposed Solution:
Inspect the actual properties dynamically via `inspect_drive(drive_letter)` or `d_info`:
```python
    def test_inspect_existing_secondary_drives(self):
        available = list_secondary_drive_letters()
        if not available:
            self.skipTest("No secondary drive available on this host.")

        secondary_drives = list_secondary_drives()
        self.assertGreaterEqual(len(secondary_drives), 1)

        # Validate general invariants across all detected secondary drives
        for sec in secondary_drives:
            self.assertFalse(sec.is_system_drive)
            self.assertGreater(sec.total_bytes, 0)
            self.assertGreater(sec.cluster_size_bytes, 0)
            self.assertIn(sec.filesystem, [FilesystemType.NTFS, FilesystemType.EXFAT, FilesystemType.FAT32, FilesystemType.OTHER])
            self.assertIn(sec.hardware_type, [DriveType.FIXED_INTERNAL, DriveType.REMOVABLE_EXTERNAL, DriveType.UNKNOWN])

            # Check filesystem-specific invariants dynamically
            if sec.filesystem == FilesystemType.EXFAT:
                self.assertTrue(sec.anti_symlink_required)
                self.assertFalse(sec.supports_junctions)
                self.assertFalse(sec.supports_compression)
            elif sec.filesystem == FilesystemType.NTFS:
                self.assertFalse(sec.anti_symlink_required)
                self.assertTrue(sec.supports_junctions)
                self.assertTrue(sec.supports_compression)

        # Dynamic inspection for D: respecting hardware and format differences
        d_matches = [d for d in secondary_drives if d.drive_letter.upper() == "D:"]
        if d_matches:
            d_info = d_matches[0]
            self.assertFalse(d_info.is_system_drive)
            if d_info.filesystem == FilesystemType.EXFAT:
                self.assertTrue(d_info.anti_symlink_required)
                self.assertFalse(d_info.supports_junctions)
                if "kingston" in (d_info.vendor_model or "").lower() or (d_info.bus_type or "").upper() == "USB":
                    self.assertEqual(d_info.hardware_type, DriveType.REMOVABLE_EXTERNAL)
                    self.assertEqual(d_info.cluster_size_bytes, 524288)
            elif d_info.filesystem == FilesystemType.NTFS:
                self.assertFalse(d_info.anti_symlink_required)
                self.assertTrue(d_info.supports_junctions)

        # Dynamic inspection for E:
        e_matches = [d for d in secondary_drives if d.drive_letter.upper() == "E:"]
        if e_matches:
            e_info = e_matches[0]
            self.assertFalse(e_info.is_system_drive)
            if e_info.filesystem == FilesystemType.NTFS:
                self.assertTrue(e_info.supports_junctions)
                if "crucial" in (e_info.vendor_model or "").lower() or (e_info.bus_type or "").upper() == "NVME":
                    self.assertEqual(e_info.hardware_type, DriveType.FIXED_INTERNAL)
```

---

### 3.2 `tests/test_adversarial_filesystem.py`
- **Location:** Lines 382–390
- **Class / Method:** `TestAntiSymlinkAndJunctionSupport.test_real_ntfs_junction_lifecycle_and_policy`

#### Current Implementation (Lines 382–390):
```python
            # 4. If host has an exFAT volume mounted (e.g. D:), verify mklink /J is rejected by OS
            if os.path.exists("D:\\"):
                exfat_junc = "D:\\__test_junc_probe__"
                res_exfat = subprocess.run(f'cmd /c mklink /J "{exfat_junc}" "{target_dir}"', capture_output=True, text=True, shell=True)
                self.assertNotEqual(res_exfat.returncode, 0, "mklink /J must fail when target location is on exFAT")
                self.assertIn("Local NTFS volumes are required", res_exfat.stderr + res_exfat.stdout)
                if os.path.lexists(exfat_junc):
                    os.rmdir(exfat_junc)
```

#### Vulnerability Analysis:
- `os.path.exists("D:\\")` only checks if drive `D:` exists.
- On a Windows workstation where `D:` is an NTFS partition, `mklink /J "D:\__test_junc_probe__" "{target_dir}"` succeeds with `returncode == 0`!
- As a result:
  1. `self.assertNotEqual(res_exfat.returncode, 0)` raises `AssertionError: 0 == 0`.
  2. The test fails unexpectedly on legitimate Windows hardware.
  3. A lingering directory junction `D:\__test_junc_probe__` may be created.

#### Proposed Solution:
Import `inspect_drive` in `tests/test_adversarial_filesystem.py` and verify whether `D:` (or any mounted secondary drive) is actually formatted as exFAT before executing the probe:
```python
            # 4. If host has an actual exFAT volume mounted, verify mklink /J is rejected by OS
            exfat_probe_dir = None
            if os.path.exists("D:\\"):
                try:
                    d_info = inspect_drive("D:")
                    if d_info.filesystem == FilesystemType.EXFAT:
                        exfat_probe_dir = "D:\\__test_junc_probe__"
                except Exception:
                    pass

            if exfat_probe_dir:
                res_exfat = subprocess.run(f'cmd /c mklink /J "{exfat_probe_dir}" "{target_dir}"', capture_output=True, text=True, shell=True)
                self.assertNotEqual(res_exfat.returncode, 0, "mklink /J must fail when target location is on exFAT")
                self.assertIn("Local NTFS volumes are required", res_exfat.stderr + res_exfat.stdout)
                if os.path.lexists(exfat_probe_dir):
                    os.rmdir(exfat_probe_dir)
```

---

## 4. Deep Dive 2: `Path.home()` Hardening on Python 3.13

### 4.1 `smart_drive/mcp/registrar.py`
- **Location:** Line 24 in `get_agent_config_paths()`, Line 66 in `detect_installed_agents()`

#### Current Implementation:
```python
22: def get_agent_config_paths() -> Dict[str, Path]:
23:     """Returns standard config paths for supported AI agents on this host."""
24:     home = Path.home()
...
64: def detect_installed_agents() -> Dict[str, bool]:
65:     """Detects which supported AI coding agents are installed on the local system."""
66:     home = Path.home()
```

#### Vulnerability Analysis:
1. In Python 3.13, `Path.home()` strictly raises `RuntimeError("Could not determine home directory")` if the environment variables (`USERPROFILE`, `HOME`, `HOMEDRIVE`+`HOMEPATH`) are unset or stripped.
2. In `tests/test_adversarial_tier5.py:411-426` (`test_registrar_with_completely_unset_environment`), `with patch.dict(os.environ, {}, clear=True):` clears all environment variables.
3. On Windows without `USERPROFILE` or `HOME`, `Path.home()` cannot resolve a home directory and raises an unhandled `RuntimeError`.

#### Proposed Solution:
Create a helper `_safe_home_dir() -> Path` in `smart_drive/mcp/registrar.py` following the explicit R1 fallback chain:
1. Attempt `Path.home()`.
2. Fallback to `os.environ.get("USERPROFILE")`.
3. Fallback to `os.environ.get("HOME")`.
4. Fallback to `Path("C:/Users/Default")` on Windows (`sys.platform == "win32"` or `platform.system().lower() == "windows"`).
5. Fallback to `Path("/tmp")` on POSIX systems.

```python
def _safe_home_dir() -> Path:
    """Safely determines the user home directory with fallbacks for stripped environments (Python 3.13+)."""
    try:
        return Path.home()
    except Exception:
        user_profile = os.environ.get("USERPROFILE")
        if user_profile:
            return Path(user_profile)
        home_env = os.environ.get("HOME")
        if home_env:
            return Path(home_env)
        if sys.platform == "win32" or platform.system().lower() == "windows":
            return Path("C:/Users/Default")
        return Path("/tmp")
```
Both `get_agent_config_paths()` and `detect_installed_agents()` will then call:
```python
    home = _safe_home_dir()
```

### 4.2 Secondary Audit: `smart_drive/core/offloader.py`
- **Location:** Line 210 in `_get_base_directories()`:
```python
209:     mock_user = os.environ.get("SMART_DRIVE_MOCK_USERPROFILE")
210:     user_str = mock_user or os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
```
If `mock_user`, `USERPROFILE`, and `HOME` are all wiped, calling `str(Path.home())` without `try...except` could raise `RuntimeError` on Python 3.13.
**Recommendation:** Update line 210 to safely wrap `Path.home()`:
```python
    if not (mock_user or os.environ.get("USERPROFILE") or os.environ.get("HOME")):
        try:
            user_str = str(Path.home())
        except Exception:
            user_str = "C:\\Users\\Default" if (sys.platform == "win32" or platform.system().lower() == "windows") else "/tmp"
    else:
        user_str = mock_user or os.environ.get("USERPROFILE") or os.environ.get("HOME")
```

---

## 5. Deep Dive 3: Unhandled `os.symlink` Elevation on Windows

### 5.1 `tests/test_mcp_adversarial_challenger1.py`
- **Location:** Lines 364–380
- **Class / Method:** `TestAdversarialWorkerRemediationsIntegrity.test_broken_junction_detection_on_posix`

#### Current Implementation (Lines 364–380):
```python
    def test_broken_junction_detection_on_posix(self) -> None:
        """Broken directory symlinks on POSIX are correctly identified as junctions."""
        temp_dir = tempfile.mkdtemp()
        try:
            target_dir = os.path.join(temp_dir, "real_dir")
            os.makedirs(target_dir, exist_ok=True)
            link_dir = os.path.join(temp_dir, "junction_link")
            os.symlink(target_dir, link_dir)

            # Valid junction
            self.assertTrue(is_directory_junction(link_dir))

            # Delete target to make it a broken junction
            os.rmdir(target_dir)
            self.assertTrue(is_directory_junction(link_dir))
        finally:
            shutil.rmtree(temp_dir)
```

#### Vulnerability Analysis:
1. Notice the test name: `test_broken_junction_detection_on_posix`. The intent of this test is to verify the POSIX directory symlink fallback of `is_directory_junction`.
2. Line 371 calls `os.symlink(target_dir, link_dir)` unconditionally.
3. On Windows, creating symbolic links requires `SeCreateSymbolicLinkPrivilege`. By default, standard non-elevated user shells do not have this privilege unless Developer Mode is activated.
4. Calling `os.symlink()` raises:
   `OSError: [WinError 1314] A required privilege is not held by the client`
5. Other tests in the repository (`test_mcp_grade_a.py:287`, `test_cross_platform_adversarial_m1_2.py:407`) catch `(OSError, NotImplementedError)`. But because this test specifically exercises POSIX symlink handling, running it on Windows is inappropriate.

#### Proposed Solution:
Decorate the test method:
```python
    @unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
    def test_broken_junction_detection_on_posix(self) -> None:
        """Broken directory symlinks on POSIX are correctly identified as junctions."""
```

---

## 6. Comprehensive Audit of Related Occurrences

### 6.1 Complete Survey of `os.symlink`
| Location | Context | Status | Action Needed |
| :--- | :--- | :--- | :--- |
| `tests/test_mcp_adversarial_challenger1.py:144` | In `test_symlink_directory_escape_blocked` | Safe (`try...except (OSError, NotImplementedError)`) | None |
| `tests/test_mcp_adversarial_challenger1.py:371` | In `test_broken_junction_detection_on_posix` | **Vulnerable** (Unprotected call) | Add `@unittest.skipIf(sys.platform == "win32", ...)` |
| `tests/test_mcp_grade_a.py:288` | In `test_check_safety_symlink_detection` | Safe (`try...except (OSError, NotImplementedError)` with mock fallback) | None |
| `tests/test_cross_platform_adversarial_m1_2.py:408` | In `test_regular_file_and_symlink_to_file_rejected` | Safe (`try...except OSError: pass`) | None |
| `smart_drive/core/junction.py:130` | In `create_directory_junction` | Safe (Inside `else:` block for POSIX only; Windows uses `mklink /J`) | None |

### 6.2 Complete Survey of `Path.home`
| Location | Context | Status | Action Needed |
| :--- | :--- | :--- | :--- |
| `smart_drive/mcp/registrar.py:24` | In `get_agent_config_paths()` | **Vulnerable** (Crashes on Python 3.13 when env cleared) | Use `_safe_home_dir()` |
| `smart_drive/mcp/registrar.py:66` | In `detect_installed_agents()` | **Vulnerable** (Crashes on Python 3.13 when env cleared) | Use `_safe_home_dir()` |
| `smart_drive/core/offloader.py:210` | In `_get_base_directories()` | Minor risk (Falls back to `Path.home()` if env cleared) | Add `try...except` guard |

### 6.3 Complete Survey of Real Host `D:` Assumptions
| Location | Context | Status | Action Needed |
| :--- | :--- | :--- | :--- |
| `tests/test_drive_detector.py:252-261` | Live host secondary drive inspection | **Vulnerable** (Hardcoded to USB exFAT 512KB) | Dynamic inspection via `inspect_drive` / conditional assertions |
| `tests/test_adversarial_filesystem.py:382-390` | Probing `mklink /J` on `D:\` expecting failure | **Vulnerable** (Fails if `D:` is NTFS) | Dynamically verify `d_info.filesystem == FilesystemType.EXFAT` |
| `tests/test_health.py:275` | Mock backend setup | Safe (Mocked in memory) | None |
| `tests/test_cross_platform_adversarial_m1_2.py:130` | Mock backend setup | Safe (Mocked in memory) | None |

---

## 7. Implementation Blueprint (Patch Guide)

### Patch 1: `smart_drive/mcp/registrar.py`
```diff
--- a/smart_drive/mcp/registrar.py
+++ b/smart_drive/mcp/registrar.py
@@ -21,8 +21,21 @@ from typing import Any, Dict, Optional
+def _safe_home_dir() -> Path:
+    """Safely determines user home directory with fallbacks for stripped environments (Python 3.13+)."""
+    try:
+        return Path.home()
+    except Exception:
+        user_profile = os.environ.get("USERPROFILE")
+        if user_profile:
+            return Path(user_profile)
+        home_env = os.environ.get("HOME")
+        if home_env:
+            return Path(home_env)
+        if sys.platform == "win32" or platform.system().lower() == "windows":
+            return Path("C:/Users/Default")
+        return Path("/tmp")
+
 def get_agent_config_paths() -> Dict[str, Path]:
     """Returns standard config paths for supported AI agents on this host."""
-    home = Path.home()
+    home = _safe_home_dir()
     system = platform.system().lower()
@@ -65,3 +78,3 @@ def detect_installed_agents() -> Dict[str, bool]:
     """Detects which supported AI coding agents are installed on the local system."""
-    home = Path.home()
+    home = _safe_home_dir()
     system = platform.system().lower()
```

### Patch 2: `tests/test_mcp_adversarial_challenger1.py`
```diff
--- a/tests/test_mcp_adversarial_challenger1.py
+++ b/tests/test_mcp_adversarial_challenger1.py
@@ -363,3 +363,4 @@ class TestAdversarialWorkerRemediationsIntegrity(SmartDriveTestCase):
 
+    @unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
     def test_broken_junction_detection_on_posix(self) -> None:
         """Broken directory symlinks on POSIX are correctly identified as junctions."""
```

### Patch 3: `tests/test_adversarial_filesystem.py`
```diff
--- a/tests/test_adversarial_filesystem.py
+++ b/tests/test_adversarial_filesystem.py
@@ -43,2 +43,3 @@ from smart_drive.core.drive_detector import (
     FilesystemAdapter,
+    inspect_drive,
     get_filesystem_adapter,
@@ -382,9 +383,16 @@ class TestAntiSymlinkAndJunctionSupport(unittest.TestCase):
-            # 4. If host has an exFAT volume mounted (e.g. D:), verify mklink /J is rejected by OS
+            # 4. If host has an actual exFAT volume mounted, verify mklink /J is rejected by OS
+            exfat_probe_dir = None
             if os.path.exists("D:\\"):
-                exfat_junc = "D:\\__test_junc_probe__"
-                res_exfat = subprocess.run(f'cmd /c mklink /J "{exfat_junc}" "{target_dir}"', capture_output=True, text=True, shell=True)
-                self.assertNotEqual(res_exfat.returncode, 0, "mklink /J must fail when target location is on exFAT")
-                self.assertIn("Local NTFS volumes are required", res_exfat.stderr + res_exfat.stdout)
-                if os.path.lexists(exfat_junc):
-                    os.rmdir(exfat_junc)
+                try:
+                    d_info = inspect_drive("D:")
+                    if d_info.filesystem == FilesystemType.EXFAT:
+                        exfat_probe_dir = "D:\\__test_junc_probe__"
+                except Exception:
+                    pass
+
+            if exfat_probe_dir:
+                res_exfat = subprocess.run(f'cmd /c mklink /J "{exfat_probe_dir}" "{target_dir}"', capture_output=True, text=True, shell=True)
+                self.assertNotEqual(res_exfat.returncode, 0, "mklink /J must fail when target location is on exFAT")
+                self.assertIn("Local NTFS volumes are required", res_exfat.stderr + res_exfat.stdout)
+                if os.path.lexists(exfat_probe_dir):
+                    os.rmdir(exfat_probe_dir)
```

### Patch 4: `tests/test_drive_detector.py`
```diff
--- a/tests/test_drive_detector.py
+++ b/tests/test_drive_detector.py
@@ -251,21 +251,33 @@ class TestRealHostInspection(unittest.TestCase):
 
-        # If D: is Kingston XS2000 external SSD, verify USB detection
+        # Validate general invariants across all detected secondary drives
+        for sec in secondary_drives:
+            self.assertFalse(sec.is_system_drive)
+            self.assertGreater(sec.total_bytes, 0)
+            self.assertGreater(sec.cluster_size_bytes, 0)
+            self.assertIn(sec.filesystem, [FilesystemType.NTFS, FilesystemType.EXFAT, FilesystemType.FAT32, FilesystemType.OTHER])
+            self.assertIn(sec.hardware_type, [DriveType.FIXED_INTERNAL, DriveType.REMOVABLE_EXTERNAL, DriveType.UNKNOWN])
+            if sec.filesystem == FilesystemType.EXFAT:
+                self.assertTrue(sec.anti_symlink_required)
+                self.assertFalse(sec.supports_junctions)
+            elif sec.filesystem == FilesystemType.NTFS:
+                self.assertFalse(sec.anti_symlink_required)
+                self.assertTrue(sec.supports_junctions)
+
+        # Dynamic inspection for D: respecting hardware and format differences
         d_matches = [d for d in secondary_drives if d.drive_letter.upper() == "D:"]
         if d_matches:
             d_info = d_matches[0]
             self.assertFalse(d_info.is_system_drive)
-            self.assertEqual(d_info.filesystem, FilesystemType.EXFAT)
-            self.assertEqual(d_info.cluster_size_bytes, 524288)
-            self.assertEqual(d_info.hardware_type, DriveType.REMOVABLE_EXTERNAL)
-            self.assertEqual(d_info.bus_type.upper(), "USB")
+            if d_info.filesystem == FilesystemType.EXFAT:
+                self.assertTrue(d_info.anti_symlink_required)
+                self.assertFalse(d_info.supports_junctions)
+                if "kingston" in (d_info.vendor_model or "").lower() or (d_info.bus_type or "").upper() == "USB":
+                    self.assertEqual(d_info.hardware_type, DriveType.REMOVABLE_EXTERNAL)
+                    self.assertEqual(d_info.cluster_size_bytes, 524288)
+            elif d_info.filesystem == FilesystemType.NTFS:
+                self.assertFalse(d_info.anti_symlink_required)
+                self.assertTrue(d_info.supports_junctions)
 
-        # If E: is Crucial internal NVMe SSD, verify NVMe detection
+        # Dynamic inspection for E:
         e_matches = [d for d in secondary_drives if d.drive_letter.upper() == "E:"]
         if e_matches:
             e_info = e_matches[0]
             self.assertFalse(e_info.is_system_drive)
             if e_info.filesystem == FilesystemType.NTFS:
-                self.assertEqual(e_info.cluster_size_bytes, 4096)
                 self.assertTrue(e_info.supports_junctions)
-                self.assertEqual(e_info.hardware_type, DriveType.FIXED_INTERNAL)
-                self.assertEqual(e_info.bus_type.upper(), "NVME")
+                if "crucial" in (e_info.vendor_model or "").lower() or (e_info.bus_type or "").upper() == "NVME":
+                    self.assertEqual(e_info.hardware_type, DriveType.FIXED_INTERNAL)
```

---

## 8. Verification Strategy

1. **Unit Test Suite:**
   Run full unittest discovery:
   ```bash
   python3 -m unittest discover tests
   ```
   Must pass all 697 tests with 0 errors and 0 failures.

2. **Simulated Host Topologies:**
   Test dynamic inspection with mock backend:
   ```bash
   python3 -m unittest tests.test_drive_detector
   python3 -m unittest tests.test_adversarial_filesystem
   python3 -m unittest tests.test_mcp_adversarial_challenger1
   python3 -m unittest tests.test_adversarial_tier5
   ```

3. **Python 3.13 Empty Environment Simulation:**
   Run direct snippet asserting fallback behavior under `patch.dict(os.environ, {}, clear=True)` and broken `Path.home()` to confirm `Path("C:/Users/Default")` and `Path("/tmp")` are returned without unhandled exceptions.

4. **Zero-Dependency Check:**
   Verify `pyproject.toml` runtime dependencies remain strictly empty (`dependencies = []`).
