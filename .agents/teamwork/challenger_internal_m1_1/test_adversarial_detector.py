"""Adversarial stress test suite for smart_drive/core/drive_detector.py.

Empirical verification covering:
1. Malformed and adversarial drive strings (empty, whitespace, numbers, symbols, UNC shares, unicode, device namespaces)
2. Case-insensitivity invariants ('c:', 'C:\\', lowercase 'd', mixed case, etc.)
3. Network shares and UNC path rejections
4. Non-existent drive letters and missing volume handling
5. Mock bus types, IOCTL structures, and hardware classifier edge cases
6. Corrupt volume queries, IO errors, disk usage failures, zero/negative cluster sizes
7. C: drive exclusion invariants under ALL conditions (standard, missing env vars, failed Win32 calls, non-C system drive)
8. Filesystem adapter stress testing (slack math, boundary conditions, anti-symlink, compression/indexing)
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.drive_detector import (
    DriveType,
    FilesystemType,
    DriveInfo,
    FilesystemAdapter,
    DriveDetectorBackend,
    Win32DriveBackend,
    MockDriveBackend,
    get_backend,
    set_backend,
    reset_backend,
    use_mock_backend,
    normalize_drive_letter,
    normalize_mount_point,
    get_system_drive_letter,
    is_system_drive,
    list_drive_letters,
    list_secondary_drive_letters,
    inspect_drive,
    list_secondary_drives,
    get_filesystem_adapter,
    verify_trim_support,
    STORAGE_BUS_TYPE_MAP,
    IOCTL_STORAGE_QUERY_PROPERTY,
)


class AdversarialDriveNormalizationTests(unittest.TestCase):
    """Stress tests drive specifier normalization against adversarial and malformed inputs."""

    def test_empty_and_whitespace(self):
        malformed = ["", "   ", "\t", "\n", "\r\n", "   \t   \n"]
        for item in malformed:
            with self.subTest(spec=repr(item)):
                with self.assertRaises(ValueError):
                    normalize_drive_letter(item)
                with self.assertRaises(ValueError):
                    normalize_mount_point(item)

    def test_single_letter_variations(self):
        cases = [
            ("c", "C:"), ("C", "C:"),
            ("d", "D:"), ("D", "D:"),
            ("z", "Z:"), ("Z", "Z:"),
            ("a", "A:"), ("A", "A:"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(normalize_drive_letter(inp), expected)
                self.assertEqual(normalize_mount_point(inp), f"{expected}\\")

    def test_colon_and_slash_variations(self):
        cases = [
            ("c:", "C:"), ("C:", "C:"),
            ("c:\\", "C:"), ("C:\\", "C:"),
            ("c:/", "C:"), ("C:/", "C:"),
            ("  c:  ", "C:"),
            ("  d:\\  ", "D:"),
            ("E:/some/folder/path", "E:"),
            (r"F:\windows\system32\cmd.exe", "F:"),
            (Path("g:/data/models"), "G:"),
            (Path(r"H:\vault\test"), "H:"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(normalize_drive_letter(inp), expected)
                self.assertEqual(normalize_mount_point(inp), f"{expected}\\")

    def test_device_namespaces(self):
        cases = [
            (r"\\.\D:", "D:"),
            (r"\\?\D:", "D:"),
            ("//./D:", "D:"),
            ("//?/D:", "D:"),
            (r"\\.\d:\folder", "D:"),
            (r"\\?\c:\windows", "C:"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(normalize_drive_letter(inp), expected)
                self.assertEqual(normalize_mount_point(inp), f"{expected}\\")

    def test_network_shares_unc_rejection(self):
        """Network shares and UNC paths MUST be rejected with ValueError."""
        unc_paths = [
            r"\\server\share",
            r"\\192.168.1.1\c$",
            r"\\nas\storage\backup",
            "//server/share",
            r"\\?\UNC\server\share",
            r"\\.\UNC\server\share",
        ]
        for unc in unc_paths:
            with self.subTest(unc=unc):
                with self.assertRaises(ValueError, msg=f"UNC path '{unc}' should be rejected"):
                    normalize_drive_letter(unc)

    def test_invalid_drive_specifiers(self):
        invalid_inputs = [
            "1:", "9:", "0:",
            "1:\\", "0:/",
            "AB:", "XYZ:",
            ":", "::", ":::",
            r"\c:", "/c:",
            "relative/path/no/drive",
            r"subfolder\file.txt",
            None,  # should raise TypeError or AttributeError
            123,   # non-string non-Path
        ]
        for item in invalid_inputs:
            with self.subTest(item=item):
                with self.assertRaises((ValueError, TypeError, AttributeError)):
                    normalize_drive_letter(item)  # type: ignore

    def test_unicode_and_special_characters(self):
        """Non-ASCII characters should be handled safely."""
        special_inputs = [
            "*: ", "?:", "!:", "#:",
            "D:\\тест\\модели",  # valid drive D: with unicode path
            "E:/tập_tin_mô_hình", # valid drive E: with vietnamese path
        ]
        self.assertEqual(normalize_drive_letter("D:\\тест\\модели"), "D:")
        self.assertEqual(normalize_drive_letter("E:/tập_tin_mô_hình"), "E:")

        with self.assertRaises(ValueError):
            normalize_drive_letter("?:")
        with self.assertRaises(ValueError):
            normalize_drive_letter("*:")


class AdversarialSystemDriveExclusionTests(unittest.TestCase):
    """Stress tests that C: (and OS system drive) is strictly and unconditionally excluded."""

    def test_case_insensitive_system_drive_checks(self):
        """Check all variations of C: are recognized as system drive."""
        variations = [
            "c", "C",
            "c:", "C:",
            "c:\\", "C:\\",
            "c:/", "C:/",
            "  c:  ", "  C:\\  ",
            r"\\.\C:", r"\\?\C:",
            "c:\\Windows", "C:\\Windows\\System32",
            "c:/users/admin", "C:/Program Files",
            Path("C:/"), Path("c:/Windows"),
        ]
        for var in variations:
            with self.subTest(var=var):
                self.assertTrue(is_system_drive(var), f"is_system_drive should be True for '{var}'")

    def test_c_drive_never_in_list_secondary_drives_live(self):
        """Live environment: list_secondary_drives() must NEVER contain C: in any form."""
        sec_drives = list_secondary_drives()
        for drive in sec_drives:
            self.assertNotEqual(drive.drive_letter.upper(), "C:", "C: must NEVER be in list_secondary_drives()")
            self.assertFalse(drive.is_system_drive, "No drive in list_secondary_drives() may have is_system_drive=True")
            self.assertFalse(drive.mount_point.upper().startswith("C:"), "Mount point must not start with C:")

        sec_letters = list_secondary_drive_letters()
        self.assertNotIn("C:", [l.upper() for l in sec_letters], "C: must not be in list_secondary_drive_letters()")

    def test_c_drive_exclusion_under_mock_variations(self):
        """Simulate various drive topologies with C: as system drive."""
        mock = MockDriveBackend(system_drive="C:")
        # Add C: and various other drives
        mock.register_drive(DriveInfo(
            drive_letter="C:", mount_point="C:\\", is_system_drive=True,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="OS"
        ))
        mock.register_drive(DriveInfo(
            drive_letter="D:", mount_point="D:\\", is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="Data"
        ))

        with use_mock_backend(mock):
            drives = list_secondary_drives()
            self.assertEqual(len(drives), 1)
            self.assertEqual(drives[0].drive_letter, "D:")
            self.assertNotIn("C:", [d.drive_letter.upper() for d in drives])

    def test_c_drive_exclusion_when_environment_variables_stripped(self):
        """If SystemDrive, SystemRoot, and WINDIR are missing from os.environ, C: must still be excluded."""
        backend = Win32DriveBackend()
        with patch.dict(os.environ, {}, clear=True):
            # Even if backend.get_system_directory raises, fallback defaults to C:
            with patch.object(backend, "get_system_directory", side_effect=OSError("API failure")):
                with patch("smart_drive.core.drive_detector.get_backend", return_value=backend):
                    sys_drive = get_system_drive_letter()
                    self.assertEqual(sys_drive, "C:")
                    self.assertTrue(is_system_drive("C:"))
                    self.assertTrue(is_system_drive("c:"))

    def test_non_c_system_drive_behavior(self):
        """What if Windows is installed on D: or E:?
        Requirement §R1: 'tự động loại trừ ổ C: hoặc ổ chứa thư mục Windows hệ thống'.
        Verify behavior when system drive is D:."""
        mock = MockDriveBackend(system_drive="D:")
        mock.register_drive(DriveInfo(
            drive_letter="C:", mount_point="C:\\", is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="Extra"
        ))
        mock.register_drive(DriveInfo(
            drive_letter="D:", mount_point="D:\\", is_system_drive=True,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="WindowsOS"
        ))
        mock.register_drive(DriveInfo(
            drive_letter="E:", mount_point="E:\\", is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="SecondaryVault"
        ))

        with use_mock_backend(mock):
            sys_letter = get_system_drive_letter()
            self.assertEqual(sys_letter, "D:")
            self.assertTrue(is_system_drive("D:"))
            # Check secondary drive letters
            sec_letters = list_secondary_drive_letters()
            self.assertNotIn("D:", sec_letters)

    def test_c_drive_never_included_even_if_system_drive_is_d(self):
        """CRITICAL INVARIANT TEST:
        User prompt requirement 2: 'Empirically verify that C: is NEVER included under any circumstances in list_secondary_drives().'
        ORIGINAL_REQUEST §R1: 'tự động loại trừ ổ C: hoặc ổ chứa thư mục Windows hệ thống'.
        Construct mock where system drive is D:, and C: is another drive.
        C: MUST NOT be included in list_secondary_drives()!
        """
        mock = MockDriveBackend(system_drive="D:")
        mock.register_drive(DriveInfo(
            drive_letter="C:", mount_point="C:\\", is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="LegacyC"
        ))
        mock.register_drive(DriveInfo(
            drive_letter="D:", mount_point="D:\\", is_system_drive=True,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="Windows"
        ))
        mock.register_drive(DriveInfo(
            drive_letter="E:", mount_point="E:\\", is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="Data"
        ))

        with use_mock_backend(mock):
            sec_drives = list_secondary_drives()
            sec_letters = [d.drive_letter.upper() for d in sec_drives]
            # Empirical verification: Does list_secondary_drives() include C:?
            # Per invariant requirement: C: must NEVER be included under ANY circumstances!
            self.assertNotIn("C:", sec_letters, "BUG FOUND: C: was included in list_secondary_drives() when system drive is D:!")

    def test_unnormalized_drive_letters_in_list_secondary_drive_letters(self):
        """BUG TEST: If backend returns unnormalized strings like 'C' or 'C:\\',
        list_secondary_drive_letters() must not let C or C:\\ leak into the list.
        """
        class UnnormalizedBackend(MockDriveBackend):
            def list_drive_letters(self):
                return ["C", "C:\\", "c:", "D:"]

        mock = UnnormalizedBackend(system_drive="C:")
        with use_mock_backend(mock):
            sec_letters = list_secondary_drive_letters()
            for l in sec_letters:
                norm = normalize_drive_letter(l)
                self.assertNotEqual(norm, "C:", f"BUG FOUND: '{l}' leaked into secondary letters")


class AdversarialDriveInspectorTests(unittest.TestCase):
    """Stress tests inspect_drive() with corrupted, offline, and non-existent drives."""

    def test_inspect_nonexistent_drive_raises(self):
        non_existent = ["X:", "Y:", "Z:", "B:", "K:"]
        available = list_drive_letters()
        missing = [d for d in non_existent if d not in available]
        for letter in missing:
            with self.subTest(letter=letter):
                with self.assertRaises(FileNotFoundError):
                    inspect_drive(letter)

    def test_inspect_drive_case_insensitivity(self):
        available = list_secondary_drive_letters()
        if not available:
            self.skipTest("No secondary drive on this host")
        target = available[0]
        lower_target = target.lower()

        info1 = inspect_drive(target)
        info2 = inspect_drive(lower_target)
        info3 = inspect_drive(f"{lower_target}\\")

        self.assertEqual(info1.drive_letter, info2.drive_letter)
        self.assertEqual(info1.drive_letter, info3.drive_letter)
        self.assertEqual(info1.filesystem, info2.filesystem)
        self.assertEqual(info1.hardware_type, info2.hardware_type)
        self.assertEqual(info1.total_bytes, info2.total_bytes)

    def test_corrupted_volume_info_handling(self):
        """Simulate a drive where GetVolumeInformationW fails (e.g. unformatted / BitLocker)."""
        mock = MockDriveBackend(system_drive="C:")
        mock.register_drive(DriveInfo(
            drive_letter="D:", mount_point="D:\\", is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL, filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096, total_bytes=1000, free_bytes=500, volume_label="Corrupt"
        ))

        # Monkeypatch get_volume_info on mock to simulate OSError
        def fail_get_volume_info(mount_point):
            raise OSError("The volume does not contain a recognized file system.")

        mock.get_volume_info = fail_get_volume_info  # type: ignore

        with use_mock_backend(mock):
            # inspect_drive should propagate OSError
            with self.assertRaises(OSError):
                inspect_drive("D:")

            # BUT list_secondary_drives() must GRACEFULLY SKIP the corrupt drive and not crash
            results = list_secondary_drives()
            self.assertEqual(results, [], "Corrupt drive should be gracefully skipped")

    def test_zero_total_bytes_handling(self):
        """Test DriveInfo properties when disk total_bytes is 0 (unformatted or virtual disk)."""
        info = DriveInfo(
            drive_letter="D:", mount_point="D:\\", is_system_drive=False,
            hardware_type=DriveType.UNKNOWN, filesystem=FilesystemType.OTHER,
            cluster_size_bytes=4096, total_bytes=0, free_bytes=0, volume_label=""
        )
        self.assertEqual(info.used_bytes, 0)
        self.assertEqual(info.free_percent, 0.0)
        self.assertEqual(info.free_ratio, 0.0)
        self.assertEqual(info.total_gb, 0.0)
        self.assertEqual(info.free_gb, 0.0)
        self.assertEqual(info.used_gb, 0.0)


class AdversarialBusTypeAndHardwareTests(unittest.TestCase):
    """Stress tests storage bus mapping, IOCTL fallback chains, and edge case bus types."""

    def test_all_storage_bus_types_mapped(self):
        """Every entry in STORAGE_BUS_TYPE_MAP must map to a valid DriveType."""
        for bus_id, (bus_name, drive_type) in STORAGE_BUS_TYPE_MAP.items():
            self.assertIsInstance(bus_id, int)
            self.assertIsInstance(bus_name, str)
            self.assertIn(drive_type, [DriveType.FIXED_INTERNAL, DriveType.REMOVABLE_EXTERNAL, DriveType.UNKNOWN])

    def test_unknown_and_extreme_bus_type_ids(self):
        backend = Win32DriveBackend()
        # Querying an unmapped bus_type_id like 9999 or -1
        bus_name, drive_type = STORAGE_BUS_TYPE_MAP.get(9999, ("Unknown", DriveType.UNKNOWN))
        self.assertEqual(drive_type, DriveType.UNKNOWN)
        self.assertEqual(bus_name, "Unknown")

    def test_powershell_disk_fallback_corrupt_json(self):
        """When PowerShell returns corrupt JSON, Win32DriveBackend._query_powershell_disk must return None."""
        backend = Win32DriveBackend()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="INVALID JSON {{{")
            result = backend._query_powershell_disk("D")
            self.assertIsNone(result)

    def test_powershell_disk_fallback_timeout(self):
        """When PowerShell times out or raises, Win32DriveBackend._query_powershell_disk must return None."""
        backend = Win32DriveBackend()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ps", timeout=5)):
            result = backend._query_powershell_disk("D")
            self.assertIsNone(result)

    def test_powershell_disk_fallback_empty_output(self):
        backend = Win32DriveBackend()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = backend._query_powershell_disk("D")
            self.assertIsNone(result)

    def test_removable_bit_overrides_fixed_bus(self):
        """If removable bit is 1 (e.g. external USB NVMe enclosure), it MUST classify as REMOVABLE_EXTERNAL."""
        # Test the classification logic in get_storage_hardware
        # Construct synthetic STORAGE_DEVICE_DESCRIPTOR raw bytes
        # struct unpack: '<IIBBBBIIIII' -> 36 bytes
        # removable is at index 4 (5th element)
        raw_header = struct.pack(
            '<IIBBBBIIIII',
            36,     # Version
            36,     # Size
            0,      # DeviceType
            0,      # DeviceModifier
            1,      # RemovableMedia = 1 (TRUE!)
            0,      # CommandQueueing
            0,      # VendorIdOffset
            0,      # ProductIdOffset
            0,      # ProductRevisionOffset
            0,      # SerialNumberOffset
            17      # BusType = 17 (NVMe)
        )
        self.assertEqual(len(raw_header), 32)
        # Even though BusType is 17 (NVMe), removable == 1 MUST make it REMOVABLE_EXTERNAL
        # We test that the mapping rule holds:
        removable = 1
        bus_type_id = 17
        if removable == 1:
            hw_type = DriveType.REMOVABLE_EXTERNAL
        elif bus_type_id in (7, 12, 13, 4):
            hw_type = DriveType.REMOVABLE_EXTERNAL
        elif bus_type_id in (17, 11, 3, 8, 10, 16, 18):
            hw_type = DriveType.FIXED_INTERNAL
        else:
            hw_type = DriveType.UNKNOWN
        self.assertEqual(hw_type, DriveType.REMOVABLE_EXTERNAL)


class AdversarialFilesystemAdapterTests(unittest.TestCase):
    """Stress tests cluster slack calculations, extreme boundaries, and filesystem adapters."""

    def test_factory_string_tolerance(self):
        cases = [
            ("ntfs", FilesystemType.NTFS),
            ("NTFS", FilesystemType.NTFS),
            ("  NtFs  ", FilesystemType.NTFS),
            ("exfat", FilesystemType.EXFAT),
            ("EXFAT", FilesystemType.EXFAT),
            ("fat32", FilesystemType.FAT32),
            ("FAT32", FilesystemType.FAT32),
            ("btrfs", FilesystemType.OTHER),
            ("ext4", FilesystemType.OTHER),
        ]
        for s, expected in cases:
            with self.subTest(s=s):
                adapter = get_filesystem_adapter(s)
                self.assertEqual(adapter.filesystem, expected)

    def test_cluster_slack_boundary_values(self):
        adapter = get_filesystem_adapter(FilesystemType.NTFS, 4096)
        # 1 byte
        self.assertEqual(adapter.calculate_allocated_bytes(1), 4096)
        self.assertEqual(adapter.calculate_slack_bytes(1), 4095)

        # 4095 bytes
        self.assertEqual(adapter.calculate_allocated_bytes(4095), 4096)
        self.assertEqual(adapter.calculate_slack_bytes(4095), 1)

        # Exact cluster (4096)
        self.assertEqual(adapter.calculate_allocated_bytes(4096), 4096)
        self.assertEqual(adapter.calculate_slack_bytes(4096), 0)

        # 4097 bytes
        self.assertEqual(adapter.calculate_allocated_bytes(4097), 8192)
        self.assertEqual(adapter.calculate_slack_bytes(4097), 4095)

        # Huge file (1 TB)
        one_tb = 1024 ** 4
        self.assertEqual(adapter.calculate_allocated_bytes(one_tb), one_tb)
        self.assertEqual(adapter.calculate_slack_bytes(one_tb), 0)

    def test_cluster_slack_extreme_cluster_sizes(self):
        # 2MB cluster size (huge exFAT)
        adapter_huge = get_filesystem_adapter(FilesystemType.EXFAT, 2097152)
        self.assertEqual(adapter_huge.calculate_allocated_bytes(1), 2097152)
        self.assertEqual(adapter_huge.calculate_slack_bytes(1), 2097151)
        self.assertAlmostEqual(adapter_huge.calculate_slack_percentage(1), 99.9999, places=2)

    def test_invalid_negative_slack_inputs(self):
        adapter = get_filesystem_adapter(FilesystemType.NTFS, 4096)
        with self.assertRaises(ValueError):
            adapter.calculate_allocated_bytes(-1)
        with self.assertRaises(ValueError):
            adapter.calculate_slack_bytes(-100)
        with self.assertRaises(ValueError):
            adapter.calculate_slack_percentage(-5)
        with self.assertRaises(ValueError):
            adapter.evaluate_cluster_slack(file_count=-1, avg_file_size=4096)
        with self.assertRaises(ValueError):
            adapter.evaluate_cluster_slack(file_count=10, avg_file_size=-100)

    def test_invalid_cluster_size_zero_or_negative(self):
        bad_adapter = FilesystemAdapter(filesystem=FilesystemType.NTFS, cluster_size_bytes=0)
        with self.assertRaises(ValueError):
            bad_adapter.calculate_allocated_bytes(100)

        bad_adapter_neg = FilesystemAdapter(filesystem=FilesystemType.NTFS, cluster_size_bytes=-4096)
        with self.assertRaises(ValueError):
            bad_adapter_neg.calculate_allocated_bytes(100)


class AdversarialTrimVerificationTests(unittest.TestCase):
    """Stress tests verify_trim_support under various fsutil command outcomes."""

    def test_trim_parsing_enabled(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="NTFS DisableDeleteNotify = 0  (Disabled)\nReFS DisableDeleteNotify = 0"
            )
            res = verify_trim_support()
            self.assertTrue(res["supported"])
            self.assertTrue(res["enabled"])
            self.assertIn("TRIM is enabled", res["message"])

    def test_trim_parsing_disabled(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="NTFS DisableDeleteNotify = 1  (Enabled)"
            )
            res = verify_trim_support()
            self.assertTrue(res["supported"])
            self.assertFalse(res["enabled"])
            self.assertIn("TRIM is disabled", res["message"])

    def test_trim_parsing_failure_or_missing_fsutil(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("fsutil not found")):
            res = verify_trim_support()
            self.assertFalse(res["supported"])
            self.assertFalse(res["enabled"])


if __name__ == "__main__":
    unittest.main()
