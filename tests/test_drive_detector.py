"""tests/test_drive_detector.py - Unit Test Suite for Secondary Drive Detector & Filesystem Adapter.

100% Python Standard Library unittest.
Tests:
1. Interface contract conformance (DriveType, FilesystemType, DriveInfo, function signatures).
2. Drive letter normalization and validation.
3. System drive C: detection and strict auto-exclusion.
4. Real host drive inspection (C:, D:, E: on Windows).
5. Mock backend simulation for CI/CD (NVMe internal, SATA internal, USB external SSD, exFAT, NTFS).
6. Filesystem adaptation (NTFS 4KB, Directory Junctions, compression, exFAT 512KB slack guard, anti-symlink).
7. Storage bus type mapping and hardware detection fallbacks (IOCTL, PowerShell, GetDriveTypeW).
8. TRIM query verification and diagnostic parsing.
"""

from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestInterfaceContracts(unittest.TestCase):
    """Verifies that all classes and enums strictly conform to PROJECT.md § Interface Contracts."""

    def test_drive_type_enum_values(self):
        """PROJECT.md: DriveType must define fixed_internal, removable_external, and unknown."""
        self.assertEqual(DriveType.FIXED_INTERNAL.value, "fixed_internal")
        self.assertEqual(DriveType.REMOVABLE_EXTERNAL.value, "removable_external")
        self.assertEqual(DriveType.UNKNOWN.value, "unknown")

    def test_filesystem_type_enum_values(self):
        """PROJECT.md: FilesystemType must define NTFS, exFAT, FAT32, and other."""
        self.assertEqual(FilesystemType.NTFS.value, "NTFS")
        self.assertEqual(FilesystemType.EXFAT.value, "exFAT")
        self.assertEqual(FilesystemType.FAT32.value, "FAT32")
        self.assertEqual(FilesystemType.OTHER.value, "other")

    def test_drive_info_dataclass_contract_fields(self):
        """PROJECT.md: DriveInfo must have 9 core fields with exact positional/keyword compatibility."""
        info = DriveInfo(
            drive_letter="D:",
            mount_point="D:\\",
            is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL,
            filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096,
            total_bytes=1000_000_000,
            free_bytes=500_000_000,
            volume_label="Secondary",
        )
        self.assertEqual(info.drive_letter, "D:")
        self.assertEqual(info.mount_point, "D:\\")
        self.assertFalse(info.is_system_drive)
        self.assertEqual(info.hardware_type, DriveType.FIXED_INTERNAL)
        self.assertEqual(info.filesystem, FilesystemType.NTFS)
        self.assertEqual(info.cluster_size_bytes, 4096)
        self.assertEqual(info.total_bytes, 1000_000_000)
        self.assertEqual(info.free_bytes, 500_000_000)
        self.assertEqual(info.volume_label, "Secondary")

        # Computed properties
        self.assertTrue(info.supports_junctions)
        self.assertTrue(info.supports_compression)
        self.assertTrue(info.supports_trim)
        self.assertFalse(info.anti_symlink_required)
        self.assertFalse(info.is_slack_sensitive)
        self.assertEqual(info.used_bytes, 500_000_000)
        self.assertAlmostEqual(info.free_percent, 50.0, places=2)
        self.assertAlmostEqual(info.free_ratio, 0.5, places=2)

    def test_drive_info_exfat_properties(self):
        """Verifies exFAT-specific DriveInfo behavioral flags."""
        info = DriveInfo(
            drive_letter="D:",
            mount_point="D:\\",
            is_system_drive=False,
            hardware_type=DriveType.REMOVABLE_EXTERNAL,
            filesystem=FilesystemType.EXFAT,
            cluster_size_bytes=524288,
            total_bytes=2_000_000_000,
            free_bytes=1_000_000_000,
            volume_label="KINGSTON",
        )
        self.assertFalse(info.supports_junctions)
        self.assertFalse(info.supports_compression)
        self.assertTrue(info.anti_symlink_required)
        self.assertTrue(info.is_slack_sensitive)

    def test_drive_info_to_dict_serialization(self):
        """DriveInfo.to_dict() should produce a complete serializable dictionary."""
        info = DriveInfo(
            drive_letter="E:",
            mount_point="E:\\",
            is_system_drive=False,
            hardware_type=DriveType.FIXED_INTERNAL,
            filesystem=FilesystemType.NTFS,
            cluster_size_bytes=4096,
            total_bytes=1000_000_000,
            free_bytes=800_000_000,
            volume_label="Fast_SSD",
            bus_type="NVMe",
            vendor_model="Crucial P3",
        )
        d = info.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["drive_letter"], "E:")
        self.assertEqual(d["hardware_type"], "fixed_internal")
        self.assertEqual(d["filesystem"], "NTFS")
        self.assertEqual(d["bus_type"], "NVMe")
        self.assertEqual(d["vendor_model"], "Crucial P3")
        self.assertEqual(d["free_percent"], 80.0)


class TestDriveNormalization(unittest.TestCase):
    """Tests normalization of various drive string formats."""

    def test_normalize_drive_letter_standard_variants(self):
        self.assertEqual(normalize_drive_letter("D"), "D:")
        self.assertEqual(normalize_drive_letter("d"), "D:")
        self.assertEqual(normalize_drive_letter("D:"), "D:")
        self.assertEqual(normalize_drive_letter("d:"), "D:")
        self.assertEqual(normalize_drive_letter("D:\\"), "D:")
        self.assertEqual(normalize_drive_letter("d:\\"), "D:")
        self.assertEqual(normalize_drive_letter("D:/"), "D:")
        self.assertEqual(normalize_drive_letter("D:/some/deep/path"), "D:")
        self.assertEqual(normalize_drive_letter(r"D:\some\deep\path"), "D:")
        self.assertEqual(normalize_drive_letter(Path("D:/some/path")), "D:")

    def test_normalize_drive_letter_device_prefixes(self):
        self.assertEqual(normalize_drive_letter(r"\\.\D:"), "D:")
        self.assertEqual(normalize_drive_letter(r"\\?\D:"), "D:")
        self.assertEqual(normalize_drive_letter("//./D:"), "D:")

    def test_normalize_mount_point(self):
        self.assertEqual(normalize_mount_point("D"), "D:\\")
        self.assertEqual(normalize_mount_point("d:"), "D:\\")
        self.assertEqual(normalize_mount_point("D:\\"), "D:\\")
        self.assertEqual(normalize_mount_point("D:/folder/file.txt"), "D:\\")

    def test_normalize_drive_letter_invalid_inputs(self):
        with self.assertRaises(ValueError):
            normalize_drive_letter("")
        with self.assertRaises(ValueError):
            normalize_drive_letter("   ")
        with self.assertRaises(ValueError):
            normalize_drive_letter("1:")
        with self.assertRaises(ValueError):
            normalize_drive_letter("relative/path/no/drive")


class TestSystemDriveExclusion(unittest.TestCase):
    """Verifies that the Windows OS system drive is authoritatively detected and excluded."""

    def test_get_system_drive_letter_format(self):
        sys_letter = get_system_drive_letter()
        self.assertTrue(len(sys_letter) == 2 and sys_letter[1] == ":")
        self.assertTrue(sys_letter[0].isupper())

    def test_is_system_drive_checks(self):
        sys_letter = get_system_drive_letter()
        self.assertTrue(is_system_drive(sys_letter))
        self.assertTrue(is_system_drive(sys_letter.lower()))
        self.assertTrue(is_system_drive(f"{sys_letter}\\Windows"))
        self.assertTrue(is_system_drive(f"{sys_letter.lower()}/Windows/System32"))

        # Different drive should be False
        other_letter = "Z:" if sys_letter != "Z:" else "Y:"
        self.assertFalse(is_system_drive(other_letter))

    def test_list_secondary_drive_letters_excludes_system_drive(self):
        sys_letter = get_system_drive_letter().upper()
        secondary = list_secondary_drive_letters()
        self.assertNotIn(sys_letter, [d.upper() for d in secondary])

    def test_list_secondary_drives_excludes_system_drive(self):
        sys_letter = get_system_drive_letter().upper()
        secondary_drives = list_secondary_drives()
        for drive in secondary_drives:
            self.assertNotEqual(drive.drive_letter.upper(), sys_letter)
            self.assertFalse(drive.is_system_drive)

    def test_list_secondary_drive_letters_unconditionally_excludes_c(self):
        """Invariant: C: must NEVER appear in list_secondary_drive_letters()."""
        secondary = list_secondary_drive_letters()
        self.assertNotIn("C:", [d.upper() for d in secondary])

    def test_list_secondary_drives_unconditionally_excludes_c(self):
        """Invariant: C: must NEVER appear in list_secondary_drives()."""
        secondary_drives = list_secondary_drives()
        for drive in secondary_drives:
            self.assertNotEqual(drive.drive_letter.upper(), "C:")
            self.assertFalse(drive.is_system_drive)


class TestRealHostInspection(unittest.TestCase):
    """Inspects live drives present on the current host machine (Windows 11)."""

    def setUp(self):
        if sys.platform != "win32":
            self.skipTest("Host inspection tests require Windows OS.")

    def test_inspect_system_drive_c(self):
        sys_drive = get_system_drive_letter()
        info = inspect_drive(sys_drive)
        self.assertEqual(info.drive_letter, sys_drive)
        self.assertTrue(info.is_system_drive)
        self.assertEqual(info.filesystem, FilesystemType.NTFS)
        self.assertEqual(info.cluster_size_bytes, 4096)
        self.assertGreater(info.total_bytes, 0)
        self.assertGreater(info.free_bytes, 0)
        self.assertEqual(info.hardware_type, DriveType.FIXED_INTERNAL)

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
            if sec.filesystem == FilesystemType.EXFAT:
                self.assertTrue(sec.anti_symlink_required)
                self.assertFalse(sec.supports_junctions)
            elif sec.filesystem == FilesystemType.NTFS:
                self.assertFalse(sec.anti_symlink_required)
                self.assertTrue(sec.supports_junctions)

        # Dynamic inspection for D: respecting hardware and format differences
        d_matches = [d for d in secondary_drives if d.drive_letter.upper() == "D:"]
        if d_matches:
            d_info = inspect_drive("D:")
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
            e_info = inspect_drive("E:")
            self.assertFalse(e_info.is_system_drive)
            if e_info.filesystem == FilesystemType.NTFS:
                self.assertTrue(e_info.supports_junctions)
                if "crucial" in (e_info.vendor_model or "").lower() or (e_info.bus_type or "").upper() == "NVME":
                    self.assertEqual(e_info.hardware_type, DriveType.FIXED_INTERNAL)

    def test_inspect_nonexistent_drive_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            inspect_drive("Z:")


class TestMockDriveBackend(unittest.TestCase):
    """Tests comprehensive drive topologies via isolated in-memory MockDriveBackend."""

    def test_standard_mock_topology(self):
        mock = MockDriveBackend.create_standard_mock()
        with use_mock_backend(mock):
            self.assertEqual(get_system_drive_letter(), "C:")
            all_letters = list_drive_letters()
            self.assertEqual(all_letters, ["C:", "D:", "E:", "F:"])

            secondary_letters = list_secondary_drive_letters()
            self.assertEqual(secondary_letters, ["D:", "E:", "F:"])

            sec_drives = list_secondary_drives()
            self.assertEqual(len(sec_drives), 3)

            # Validate D: (USB exFAT SSD)
            d = inspect_drive("D:")
            self.assertEqual(d.hardware_type, DriveType.REMOVABLE_EXTERNAL)
            self.assertEqual(d.filesystem, FilesystemType.EXFAT)
            self.assertEqual(d.cluster_size_bytes, 524288)
            self.assertEqual(d.bus_type, "USB")
            self.assertEqual(d.vendor_model, "Kingston XS2000")
            self.assertFalse(d.supports_junctions)
            self.assertTrue(d.anti_symlink_required)

            # Validate E: (NVMe NTFS SSD)
            e = inspect_drive("E:")
            self.assertEqual(e.hardware_type, DriveType.FIXED_INTERNAL)
            self.assertEqual(e.filesystem, FilesystemType.NTFS)
            self.assertEqual(e.cluster_size_bytes, 4096)
            self.assertEqual(e.bus_type, "NVMe")
            self.assertEqual(e.vendor_model, "CT1000P3PSSD8")
            self.assertTrue(e.supports_junctions)
            self.assertTrue(e.supports_compression)
            self.assertTrue(e.supports_trim)

            # Validate F: (SATA NTFS SSD with low free space)
            f = inspect_drive("F:")
            self.assertEqual(f.hardware_type, DriveType.FIXED_INTERNAL)
            self.assertEqual(f.filesystem, FilesystemType.NTFS)
            self.assertEqual(f.bus_type, "SATA")
            self.assertLess(f.free_percent, 5.0)

    def test_mock_system_drive_reassignment(self):
        """Verify dynamic exclusion when system drive letter changes, strictly excluding C:."""
        mock = MockDriveBackend.create_standard_mock()
        with use_mock_backend(mock):
            # Change system drive to E:
            mock.set_system_drive("E:")
            self.assertEqual(get_system_drive_letter(), "E:")

            secondary_letters = list_secondary_drive_letters()
            self.assertNotIn("E:", secondary_letters)
            self.assertNotIn("C:", secondary_letters)
            self.assertIn("D:", secondary_letters)
            self.assertIn("F:", secondary_letters)

            # Inspecting E: now reflects is_system_drive == True
            e_info = inspect_drive("E:")
            self.assertTrue(e_info.is_system_drive)

            # Inspecting C: now reflects is_system_drive == False
            c_info = inspect_drive("C:")
            self.assertFalse(c_info.is_system_drive)

            # Invariant: list_secondary_drives() strictly excludes both C: and the system drive (E:)
            sec_drives = list_secondary_drives()
            sec_drive_letters = [d.drive_letter.upper() for d in sec_drives]
            self.assertNotIn("C:", sec_drive_letters)
            self.assertNotIn("E:", sec_drive_letters)
            self.assertIn("D:", sec_drive_letters)
            self.assertIn("F:", sec_drive_letters)

    def test_unnormalized_drive_strings_cannot_bypass_filter(self):
        """Verify unnormalized drive strings ('C', 'C:\\', lowercase 'c:') are properly excluded."""
        class MockUnnormalizedBackend(MockDriveBackend):
            def list_drive_letters(self):
                return ["C", "C:\\", "c:", "c", "C:/", "D", "d:", "D:\\", "E:"]

        mock = MockUnnormalizedBackend(system_drive="C:")
        with use_mock_backend(mock):
            secondary = list_secondary_drive_letters()
            self.assertEqual(secondary, ["D:", "E:"])
            self.assertNotIn("C:", secondary)
            self.assertNotIn("C", secondary)
            self.assertNotIn("C:\\", secondary)
            self.assertNotIn("c:", secondary)
            self.assertNotIn("c", secondary)
            self.assertNotIn("C:/", secondary)

    def test_unnormalized_drive_strings_with_non_c_system_drive(self):
        """Verify unnormalized C variants are strictly excluded even when system drive is E:."""
        class MockUnnormalizedBackend(MockDriveBackend):
            def list_drive_letters(self):
                return ["C", "C:\\", "c:", "d", "e", "E:\\", "F:"]

        mock = MockUnnormalizedBackend(system_drive="E:")
        with use_mock_backend(mock):
            secondary = list_secondary_drive_letters()
            self.assertNotIn("C:", secondary)
            self.assertNotIn("E:", secondary)
            self.assertEqual(secondary, ["D:", "F:"])

    def test_mock_unregister_drive(self):
        mock = MockDriveBackend.create_standard_mock()
        with use_mock_backend(mock):
            mock.unregister_drive("D:")
            self.assertNotIn("D:", list_drive_letters())
            with self.assertRaises(FileNotFoundError):
                inspect_drive("D:")


class TestFilesystemAdaptation(unittest.TestCase):
    """Tests dual-filesystem adaptation rules between NTFS and exFAT."""

    def setUp(self):
        self.ntfs = get_filesystem_adapter(FilesystemType.NTFS, 4096)
        self.exfat = get_filesystem_adapter(FilesystemType.EXFAT, 524288)

    def test_capabilities_matrix(self):
        # NTFS
        self.assertTrue(self.ntfs.supports_junctions)
        self.assertTrue(self.ntfs.supports_compression)
        self.assertTrue(self.ntfs.supports_selective_indexing)
        self.assertTrue(self.ntfs.supports_trim)
        self.assertFalse(self.ntfs.anti_symlink_required)
        self.assertFalse(self.ntfs.is_slack_sensitive)

        # exFAT
        self.assertFalse(self.exfat.supports_junctions)
        self.assertFalse(self.exfat.supports_compression)
        self.assertFalse(self.exfat.supports_selective_indexing)
        self.assertTrue(self.exfat.anti_symlink_required)
        self.assertTrue(self.exfat.is_slack_sensitive)

    def test_cluster_allocation_math_zero_bytes(self):
        self.assertEqual(self.ntfs.calculate_allocated_bytes(0), 0)
        self.assertEqual(self.exfat.calculate_allocated_bytes(0), 0)
        self.assertEqual(self.ntfs.calculate_slack_bytes(0), 0)
        self.assertEqual(self.exfat.calculate_slack_bytes(0), 0)
        self.assertEqual(self.ntfs.calculate_slack_percentage(0), 0.0)
        self.assertEqual(self.exfat.calculate_slack_percentage(0), 0.0)

    def test_cluster_allocation_math_small_file(self):
        # 100-byte file
        self.assertEqual(self.ntfs.calculate_allocated_bytes(100), 4096)
        self.assertEqual(self.ntfs.calculate_slack_bytes(100), 3996)

        self.assertEqual(self.exfat.calculate_allocated_bytes(100), 524288)
        self.assertEqual(self.exfat.calculate_slack_bytes(100), 524188)
        self.assertAlmostEqual(self.exfat.calculate_slack_percentage(100), (524188 / 524288) * 100.0, places=2)

    def test_cluster_allocation_math_boundary(self):
        # Exact cluster size
        self.assertEqual(self.ntfs.calculate_allocated_bytes(4096), 4096)
        self.assertEqual(self.ntfs.calculate_slack_bytes(4096), 0)

        # One byte over cluster size
        self.assertEqual(self.ntfs.calculate_allocated_bytes(4097), 8192)
        self.assertEqual(self.ntfs.calculate_slack_bytes(4097), 4095)

        # exFAT boundary
        self.assertEqual(self.exfat.calculate_allocated_bytes(524288), 524288)
        self.assertEqual(self.exfat.calculate_slack_bytes(524288), 0)
        self.assertEqual(self.exfat.calculate_allocated_bytes(524289), 1048576)
        self.assertEqual(self.exfat.calculate_slack_bytes(524289), 524287)

    def test_negative_file_size_raises_valueerror(self):
        with self.assertRaises(ValueError):
            self.ntfs.calculate_allocated_bytes(-1)
        with self.assertRaises(ValueError):
            self.exfat.calculate_allocated_bytes(-100)

    def test_evaluate_cluster_slack_exfat_warning(self):
        # 10,000 files of 4KB each
        eval_result = self.exfat.evaluate_cluster_slack(file_count=10000, avg_file_size=4096)
        self.assertEqual(eval_result["file_count"], 10000)
        self.assertEqual(eval_result["total_nominal_bytes"], 40960000)
        self.assertEqual(eval_result["total_allocated_bytes"], 5242880000)
        self.assertGreater(eval_result["total_slack_bytes"], 5000_000_000)
        self.assertTrue(eval_result["is_critical"])
        self.assertIsNotNone(eval_result["warning"])
        self.assertIn("exFAT cluster slack guard", eval_result["warning"])

    def test_evaluate_cluster_slack_ntfs_optimal(self):
        eval_result = self.ntfs.evaluate_cluster_slack(file_count=10000, avg_file_size=4096)
        self.assertEqual(eval_result["total_nominal_bytes"], 40960000)
        self.assertEqual(eval_result["total_allocated_bytes"], 40960000)
        self.assertEqual(eval_result["total_slack_bytes"], 0)
        self.assertFalse(eval_result["is_critical"])
        self.assertIsNone(eval_result["warning"])

    def test_exfat_rejects_compression(self):
        success, msg = self.exfat.compress_path("D:\\Some\\Path")
        self.assertFalse(success)
        self.assertIn("not supported", msg.lower())

    def test_exfat_rejects_indexing(self):
        success, msg = self.exfat.set_content_indexing("D:\\Some\\Path")
        self.assertFalse(success)
        self.assertIn("not supported", msg.lower())


class TestHardwareBusClassification(unittest.TestCase):
    """Tests storage bus type mapping and hardware classifier logic."""

    def test_storage_bus_type_map_exhaustive(self):
        self.assertEqual(STORAGE_BUS_TYPE_MAP[17], ("NVMe", DriveType.FIXED_INTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[11], ("SATA", DriveType.FIXED_INTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[3], ("ATA", DriveType.FIXED_INTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[8], ("RAID", DriveType.FIXED_INTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[10], ("SAS", DriveType.FIXED_INTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[7], ("USB", DriveType.REMOVABLE_EXTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[12], ("SD", DriveType.REMOVABLE_EXTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[13], ("MMC", DriveType.REMOVABLE_EXTERNAL))
        self.assertEqual(STORAGE_BUS_TYPE_MAP[0], ("Unknown", DriveType.UNKNOWN))

    def test_powershell_fallback_parsing(self):
        backend = Win32DriveBackend()
        # Mock subprocess.run returning valid PowerShell JSON for an NVMe drive
        mock_output = '{"BusType": "NVMe", "FriendlyName": "Samsung 980 PRO 1TB", "SerialNumber": "S5GXNF0R123456"}'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output)
            res = backend._query_powershell_disk("E")
            self.assertIsNotNone(res)
            self.assertEqual(res["hardware_type"], DriveType.FIXED_INTERNAL)
            self.assertEqual(res["bus_type"], "NVMe")
            self.assertEqual(res["vendor_model"], "Samsung 980 PRO 1TB")
            self.assertFalse(res["removable_media"])

        # Mock subprocess.run returning USB drive
        mock_output_usb = '{"BusType": "USB", "FriendlyName": "Kingston XS2000", "SerialNumber": "50026B"}'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output_usb)
            res = backend._query_powershell_disk("D")
            self.assertIsNotNone(res)
            self.assertEqual(res["hardware_type"], DriveType.REMOVABLE_EXTERNAL)
            self.assertEqual(res["bus_type"], "USB")
            self.assertTrue(res["removable_media"])

    def test_trim_query_parsing(self):
        backend = Win32DriveBackend()
        # Mock fsutil output with TRIM enabled
        mock_stdout = "NTFS DisableDeleteNotify = 0  (Allows TRIM operations to be sent)\nReFS DisableDeleteNotify = 0"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout)
            trim = backend.query_trim_status()
            self.assertTrue(trim["supported"])
            self.assertTrue(trim["enabled"])
            self.assertIn("enabled", trim["message"].lower())

        # Mock fsutil output with TRIM disabled
        mock_stdout_disabled = "NTFS DisableDeleteNotify = 1\nReFS DisableDeleteNotify = 1"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_stdout_disabled)
            trim = backend.query_trim_status()
            self.assertTrue(trim["supported"])
            self.assertFalse(trim["enabled"])
            self.assertIn("disabled", trim["message"].lower())

        # Test top-level verify_trim_support()
        with patch.object(backend, "query_trim_status", return_value={"supported": True, "enabled": True, "message": "OK", "raw": ""}):
            with patch("smart_drive.core.drive_detector.get_backend", return_value=backend):
                res = verify_trim_support()
                self.assertTrue(res["enabled"])


class TestWin32IOCTLRawParsing(unittest.TestCase):
    """Directly tests raw byte buffer unpacking and hardware classification for IOCTL queries."""

    def setUp(self):
        if sys.platform != "win32":
            self.skipTest("Raw IOCTL testing targets Windows ctypes kernel32 structures.")

    @staticmethod
    def _make_device_descriptor_bytes(
        bus_type: int = 17,
        removable: int = 0,
        vendor: str = "Samsung",
        product: str = "980 Pro",
        serial: str = "12345",
    ) -> bytes:
        v_bytes = vendor.encode("ascii") + b"\x00"
        p_bytes = product.encode("ascii") + b"\x00"
        s_bytes = serial.encode("ascii") + b"\x00"
        v_off = 36
        p_off = v_off + len(v_bytes)
        r_off = 0
        s_off = p_off + len(p_bytes)
        payload_len = len(v_bytes) + len(p_bytes) + len(s_bytes)
        header = struct.pack(
            "<IIBBBBIIIIII",
            36,
            2048,
            0,
            0,
            removable,
            1,
            v_off,
            p_off,
            r_off,
            s_off,
            bus_type,
            payload_len,
        )
        raw = header + v_bytes + p_bytes + s_bytes
        return raw.ljust(2048, b"\x00")

    def test_ioctl_nvme_descriptor_parsing(self):
        backend = Win32DriveBackend()
        raw_nvme = self._make_device_descriptor_bytes(bus_type=17, removable=0, vendor="WD", product="Blue SN580", serial="SN100")

        def fake_ioctl(handle, ioctl, in_buf, in_len, out_buf, out_len, ret_bytes, overlapped):
            import ctypes
            ctypes.memmove(out_buf, raw_nvme, len(raw_nvme))
            ret_bytes._obj.value = len(raw_nvme)
            return 1

        import ctypes
        with patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=123):
            with patch.object(ctypes.windll.kernel32, "DeviceIoControl", side_effect=fake_ioctl):
                with patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=1):
                    hw = backend.get_storage_hardware("C:")
                    self.assertEqual(hw["hardware_type"], DriveType.FIXED_INTERNAL)
                    self.assertEqual(hw["bus_type"], "NVMe")
                    self.assertEqual(hw["vendor_model"], "WD Blue SN580")
                    self.assertEqual(hw["serial_number"], "SN100")
                    self.assertFalse(hw["removable_media"])

    def test_ioctl_usb_descriptor_parsing(self):
        backend = Win32DriveBackend()
        raw_usb = self._make_device_descriptor_bytes(bus_type=7, removable=0, vendor="Kingston", product="XS2000", serial="SN200")

        def fake_ioctl(handle, ioctl, in_buf, in_len, out_buf, out_len, ret_bytes, overlapped):
            import ctypes
            ctypes.memmove(out_buf, raw_usb, len(raw_usb))
            ret_bytes._obj.value = len(raw_usb)
            return 1

        import ctypes
        with patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=123):
            with patch.object(ctypes.windll.kernel32, "DeviceIoControl", side_effect=fake_ioctl):
                with patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=1):
                    hw = backend.get_storage_hardware("D:")
                    self.assertEqual(hw["hardware_type"], DriveType.REMOVABLE_EXTERNAL)
                    self.assertEqual(hw["bus_type"], "USB")
                    self.assertEqual(hw["vendor_model"], "Kingston XS2000")
                    self.assertEqual(hw["serial_number"], "SN200")

    def test_ioctl_sata_descriptor_parsing(self):
        backend = Win32DriveBackend()
        raw_sata = self._make_device_descriptor_bytes(bus_type=11, removable=0, vendor="Crucial", product="MX500", serial="SN300")

        def fake_ioctl(handle, ioctl, in_buf, in_len, out_buf, out_len, ret_bytes, overlapped):
            import ctypes
            ctypes.memmove(out_buf, raw_sata, len(raw_sata))
            ret_bytes._obj.value = len(raw_sata)
            return 1

        import ctypes
        with patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=123):
            with patch.object(ctypes.windll.kernel32, "DeviceIoControl", side_effect=fake_ioctl):
                with patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=1):
                    hw = backend.get_storage_hardware("E:")
                    self.assertEqual(hw["hardware_type"], DriveType.FIXED_INTERNAL)
                    self.assertEqual(hw["bus_type"], "SATA")
                    self.assertEqual(hw["vendor_model"], "Crucial MX500")

    def test_ioctl_removable_flag_override(self):
        backend = Win32DriveBackend()
        # bus_type 11 (SATA), but removable == 1 (e.g. hot-swap eSATA)
        raw_removable = self._make_device_descriptor_bytes(bus_type=11, removable=1, vendor="Generic", product="eSATA Drive", serial="SN400")

        def fake_ioctl(handle, ioctl, in_buf, in_len, out_buf, out_len, ret_bytes, overlapped):
            import ctypes
            ctypes.memmove(out_buf, raw_removable, len(raw_removable))
            ret_bytes._obj.value = len(raw_removable)
            return 1

        import ctypes
        with patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=123):
            with patch.object(ctypes.windll.kernel32, "DeviceIoControl", side_effect=fake_ioctl):
                with patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=1):
                    hw = backend.get_storage_hardware("F:")
                    self.assertEqual(hw["hardware_type"], DriveType.REMOVABLE_EXTERNAL)
                    self.assertTrue(hw["removable_media"])

    def test_ioctl_failure_powershell_fallback(self):
        backend = Win32DriveBackend()
        import ctypes
        with patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=123):
            with patch.object(ctypes.windll.kernel32, "DeviceIoControl", return_value=0):
                with patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=1):
                    ps_mock = {
                        "hardware_type": DriveType.FIXED_INTERNAL,
                        "bus_type": "NVMe",
                        "vendor_model": "Fallback NVMe SSD",
                        "serial_number": "FALLBACK123",
                        "removable_media": False,
                    }
                    with patch.object(backend, "_query_powershell_disk", return_value=ps_mock):
                        hw = backend.get_storage_hardware("E:")
                        self.assertEqual(hw["hardware_type"], DriveType.FIXED_INTERNAL)
                        self.assertEqual(hw["bus_type"], "NVMe")
                        self.assertEqual(hw["vendor_model"], "Fallback NVMe SSD")

    def test_ioctl_and_powershell_failure_getdrivetype_fallback(self):
        backend = Win32DriveBackend()
        import ctypes
        with patch.object(ctypes.windll.kernel32, "CreateFileW", return_value=123):
            with patch.object(ctypes.windll.kernel32, "DeviceIoControl", return_value=0):
                with patch.object(ctypes.windll.kernel32, "CloseHandle", return_value=1):
                    with patch.object(backend, "_query_powershell_disk", return_value=None):
                        with patch.object(ctypes.windll.kernel32, "GetDriveTypeW", return_value=2):
                            hw_rem = backend.get_storage_hardware("D:")
                            self.assertEqual(hw_rem["hardware_type"], DriveType.REMOVABLE_EXTERNAL)
                        with patch.object(ctypes.windll.kernel32, "GetDriveTypeW", return_value=3):
                            hw_fix = backend.get_storage_hardware("E:")
                            self.assertEqual(hw_fix["hardware_type"], DriveType.FIXED_INTERNAL)


class TestSymlinkPolicyAndIndexing(unittest.TestCase):
    """Tests symlink checking and filesystem adaptation policies."""

    def test_symlink_violation_check(self):
        adapter_exfat = get_filesystem_adapter(FilesystemType.EXFAT)
        adapter_ntfs = get_filesystem_adapter(FilesystemType.NTFS)

        mock_path = MagicMock(spec=Path)
        mock_path.is_symlink.return_value = True

        with patch("smart_drive.core.drive_detector.Path", return_value=mock_path):
            self.assertTrue(adapter_exfat.check_symlink_violation("D:\\symlink"))
            self.assertFalse(adapter_ntfs.check_symlink_violation("E:\\symlink"))

        mock_path.is_symlink.return_value = False
        with patch("smart_drive.core.drive_detector.Path", return_value=mock_path):
            self.assertFalse(adapter_exfat.check_symlink_violation("D:\\normal_file"))
            self.assertFalse(adapter_ntfs.check_symlink_violation("E:\\normal_file"))


if __name__ == "__main__":
    unittest.main()
