"""tests.test_health - Unit Tests for SSD Health & TRIM Monitoring Engine.

Validates:
- TRIM output parsing for Windows fsutil queries (0 = enabled, 1 = disabled, edge cases)
- SSDHealthReport dataclass interface contracts and property calculations
- Capacity and TRIM diagnostic warning thresholds (<15% reserve, <5% critical, TRIM disabled, exFAT slack)
- check_drive_health resolution: default secondary drive vs system drive fallback
- Pluggable MockDriveBackend integration for deterministic multi-drive simulation
- CLI command execution (smart-drive health [drive] [--json])

100% Zero-Dependency Python Standard Library.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import unittest

from smart_drive.cli.cmd_health import cmd_health
from smart_drive.core.drive_detector import (
    DriveInfo,
    DriveType,
    FilesystemType,
    MockDriveBackend,
    use_mock_backend,
)
from smart_drive.core.health import (
    SSDHealthReport,
    check_drive_health,
    evaluate_health_warnings,
    parse_trim_output,
)
from tests.helpers import SmartDriveTestCase, TempWorkspace


class TestTrimParsing(SmartDriveTestCase):
    """Tests parsing logic for 'fsutil behavior query DisableDeleteNotify'."""

    def test_trim_enabled_standard_windows_output(self) -> None:
        """Parses standard Windows 10/11 output with NTFS and ReFS DisableDeleteNotify = 0."""
        raw = (
            "NTFS DisableDeleteNotify = 0  (Allows TRIM operations to be sent to the storage device)\n"
            "ReFS DisableDeleteNotify = 0  (Allows TRIM operations to be sent to the storage device)"
        )
        enabled, msg = parse_trim_output(raw)
        self.assertTrue(enabled)
        self.assertIn("enabled", msg.lower())
        self.assertIn("0", msg)

    def test_trim_enabled_short_format(self) -> None:
        """Parses short output 'DisableDeleteNotify = 0'."""
        enabled, msg = parse_trim_output("DisableDeleteNotify = 0")
        self.assertTrue(enabled)
        self.assertIn("enabled", msg.lower())

    def test_trim_disabled_standard_output(self) -> None:
        """Parses output when DisableDeleteNotify = 1 (TRIM disabled)."""
        raw = "NTFS DisableDeleteNotify = 1"
        enabled, msg = parse_trim_output(raw)
        self.assertFalse(enabled)
        self.assertIn("disabled", msg.lower())
        self.assertIn("1", msg)

    def test_trim_disabled_short_format(self) -> None:
        """Parses short output 'DisableDeleteNotify = 1'."""
        enabled, msg = parse_trim_output("DisableDeleteNotify = 1")
        self.assertFalse(enabled)
        self.assertIn("disabled", msg.lower())

    def test_trim_empty_or_whitespace_output(self) -> None:
        """Empty or whitespace output returns None and appropriate status message."""
        enabled, msg = parse_trim_output("")
        self.assertIsNone(enabled)
        self.assertIn("unavailable", msg.lower())

        enabled_ws, msg_ws = parse_trim_output("   \n\t  ")
        self.assertIsNone(enabled_ws)
        self.assertIn("unavailable", msg_ws.lower())

    def test_trim_unsupported_or_error_output(self) -> None:
        """Error or unsupported query responses return False."""
        enabled, msg = parse_trim_output("Error: The requested feature is not supported on this device.")
        self.assertFalse(enabled)
        self.assertIn("unsupported", msg.lower())


class TestHealthWarnings(SmartDriveTestCase):
    """Tests diagnostic warning evaluation under various capacity, filesystem, and TRIM conditions."""

    def test_healthy_drive_produces_zero_warnings(self) -> None:
        """NTFS drive with >20% free space and TRIM enabled generates 0 warnings."""
        total = 1000 * (1024 ** 3)
        free = 600 * (1024 ** 3)  # 60% free
        warnings = evaluate_health_warnings(
            free_percent=60.0,
            free_bytes=free,
            total_bytes=total,
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(warnings, [])

    def test_low_space_warning_threshold_below_15_percent(self) -> None:
        """Free space between 5% and 15% triggers low space reserve warning."""
        total = 500 * (1024 ** 3)
        free = 60 * (1024 ** 3)  # 12% free
        warnings = evaluate_health_warnings(
            free_percent=12.0,
            free_bytes=free,
            total_bytes=total,
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("15% reserve", warnings[0])
        self.assertIn("amplification", warnings[0].lower())

    def test_critical_space_warning_threshold_below_5_percent(self) -> None:
        """Free space below 5% triggers critical alert."""
        total = 500 * (1024 ** 3)
        free = 15 * (1024 ** 3)  # 3% free
        warnings = evaluate_health_warnings(
            free_percent=3.0,
            free_bytes=free,
            total_bytes=total,
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("CRITICAL", warnings[0])
        self.assertIn("critically low", warnings[0].lower())

    def test_critical_space_by_absolute_bytes_under_10gb(self) -> None:
        """Free space under 10 GB on a large drive triggers critical alert."""
        total = 2000 * (1024 ** 3)
        free = 8 * (1024 ** 3)  # 8 GB free (0.4%)
        warnings = evaluate_health_warnings(
            free_percent=0.4,
            free_bytes=free,
            total_bytes=total,
            trim_enabled=True,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertTrue(any("CRITICAL" in w for w in warnings))

    def test_trim_disabled_warning(self) -> None:
        """trim_enabled is False triggers explicit TRIM remediation warning."""
        total = 1000 * (1024 ** 3)
        free = 800 * (1024 ** 3)
        warnings = evaluate_health_warnings(
            free_percent=80.0,
            free_bytes=free,
            total_bytes=total,
            trim_enabled=False,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("TRIM is disabled", warnings[0])
        self.assertIn("fsutil behavior set DisableDeleteNotify 0", warnings[0])

    def test_exfat_large_cluster_slack_notice(self) -> None:
        """exFAT volume with 512KB clusters triggers slack advisory."""
        total = 2000 * (1024 ** 3)
        free = 1400 * (1024 ** 3)
        warnings = evaluate_health_warnings(
            free_percent=70.0,
            free_bytes=free,
            total_bytes=total,
            trim_enabled=True,
            filesystem="exFAT",
            cluster_size_bytes=524288,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("NOTICE", warnings[0])
        self.assertIn("512KB", warnings[0])
        self.assertIn("slack", warnings[0].lower())

    def test_multiple_simultaneous_warnings(self) -> None:
        """Drive with critical space and disabled TRIM triggers multiple warnings."""
        total = 500 * (1024 ** 3)
        free = 10 * (1024 ** 3)  # 2% free
        warnings = evaluate_health_warnings(
            free_percent=2.0,
            free_bytes=free,
            total_bytes=total,
            trim_enabled=False,
            filesystem="NTFS",
            cluster_size_bytes=4096,
        )
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("CRITICAL" in w for w in warnings))
        self.assertTrue(any("TRIM is disabled" in w for w in warnings))


class TestSSDHealthReportDataclass(SmartDriveTestCase):
    """Tests SSDHealthReport methods, properties, and dictionary serialization."""

    def test_report_properties_and_dict_serialization(self) -> None:
        """Validates all required fields in to_dict() and computed properties."""
        report = SSDHealthReport(
            drive_letter="E:",
            filesystem="NTFS",
            trim_enabled=True,
            trim_status_message="TRIM is enabled (DisableDeleteNotify = 0)",
            total_bytes=1000 * (1024 ** 3),
            free_bytes=600 * (1024 ** 3),
            free_percent=60.0,
            cluster_size_bytes=4096,
            warnings=[],
        )

        self.assertEqual(report.used_bytes, 400 * (1024 ** 3))
        self.assertAlmostEqual(report.total_gb, 1000.0, places=1)
        self.assertAlmostEqual(report.free_gb, 600.0, places=1)
        self.assertAlmostEqual(report.used_gb, 400.0, places=1)
        self.assertTrue(report.is_healthy)

        data = report.to_dict()
        required_keys = [
            "drive_letter",
            "filesystem",
            "trim_enabled",
            "trim_status_message",
            "total_bytes",
            "free_bytes",
            "free_percent",
            "cluster_size_bytes",
            "warnings",
        ]
        for key in required_keys:
            self.assertIn(key, data)

        self.assertEqual(data["drive_letter"], "E:")
        self.assertEqual(data["filesystem"], "NTFS")
        self.assertTrue(data["trim_enabled"])
        self.assertEqual(data["cluster_size_bytes"], 4096)


class TestCheckDriveHealthWithMockBackend(SmartDriveTestCase):
    """Tests check_drive_health using pluggable MockDriveBackend simulation."""

    def setUp(self) -> None:
        super().setUp()
        # Build mock drive backend: C: (OS NTFS), D: (USB exFAT), E: (NVMe NTFS), F: (SATA NTFS degraded)
        self.mock_backend = MockDriveBackend(system_drive="C:")

        # C: System Drive (NTFS, 4KB, 14% free)
        self.mock_backend.register_drive(
            DriveInfo(
                drive_letter="C:",
                mount_point="C:\\",
                is_system_drive=True,
                hardware_type=DriveType.FIXED_INTERNAL,
                filesystem=FilesystemType.NTFS,
                cluster_size_bytes=4096,
                total_bytes=500 * (1024 ** 3),
                free_bytes=70 * (1024 ** 3),
                volume_label="Windows",
                bus_type="NVMe",
            )
        )

        # D: Secondary Drive (exFAT, 512KB, 70% free)
        self.mock_backend.register_drive(
            DriveInfo(
                drive_letter="D:",
                mount_point="D:\\",
                is_system_drive=False,
                hardware_type=DriveType.REMOVABLE_EXTERNAL,
                filesystem=FilesystemType.EXFAT,
                cluster_size_bytes=524288,
                total_bytes=2000 * (1024 ** 3),
                free_bytes=1400 * (1024 ** 3),
                volume_label="KINGSTON",
                bus_type="USB",
            )
        )

        # E: Secondary Drive (NTFS, 4KB, 60% free)
        self.mock_backend.register_drive(
            DriveInfo(
                drive_letter="E:",
                mount_point="E:\\",
                is_system_drive=False,
                hardware_type=DriveType.FIXED_INTERNAL,
                filesystem=FilesystemType.NTFS,
                cluster_size_bytes=4096,
                total_bytes=1000 * (1024 ** 3),
                free_bytes=600 * (1024 ** 3),
                volume_label="Dev_Vault",
                bus_type="NVMe",
            )
        )

        # F: Secondary Drive (NTFS, 4KB, 2% free space - Critical)
        self.mock_backend.register_drive(
            DriveInfo(
                drive_letter="F:",
                mount_point="F:\\",
                is_system_drive=False,
                hardware_type=DriveType.FIXED_INTERNAL,
                filesystem=FilesystemType.NTFS,
                cluster_size_bytes=4096,
                total_bytes=500 * (1024 ** 3),
                free_bytes=10 * (1024 ** 3),
                volume_label="Cold_Archive",
                bus_type="SATA",
            )
        )

    def test_default_resolution_selects_first_secondary_drive(self) -> None:
        """When drive_letter is None, check_drive_health defaults to first secondary drive ('D:')."""
        with use_mock_backend(self.mock_backend):
            report = check_drive_health(None)
            self.assertEqual(report.drive_letter, "D:")
            self.assertEqual(report.filesystem, "exFAT")
            self.assertEqual(report.cluster_size_bytes, 524288)

    def test_explicit_ntfs_secondary_drive_health(self) -> None:
        """Querying E: on mock backend returns healthy NTFS report."""
        with use_mock_backend(self.mock_backend):
            report = check_drive_health("E:")
            self.assertEqual(report.drive_letter, "E:")
            self.assertEqual(report.filesystem, "NTFS")
            self.assertEqual(report.cluster_size_bytes, 4096)
            self.assertTrue(report.trim_enabled)
            self.assertEqual(report.warnings, [])
            self.assertTrue(report.is_healthy)

    def test_degraded_secondary_drive_health_critical_warning(self) -> None:
        """Querying F: on mock backend reports critical capacity alert."""
        with use_mock_backend(self.mock_backend):
            report = check_drive_health("F:")
            self.assertEqual(report.drive_letter, "F:")
            self.assertTrue(any("CRITICAL" in w for w in report.warnings))
            self.assertFalse(report.is_healthy)

    def test_system_drive_fallback_when_no_secondary_drives(self) -> None:
        """When only C: exists, defaulting drive_letter selects C:."""
        single_c_backend = MockDriveBackend(system_drive="C:")
        single_c_backend.register_drive(
            DriveInfo(
                drive_letter="C:",
                mount_point="C:\\",
                is_system_drive=True,
                hardware_type=DriveType.FIXED_INTERNAL,
                filesystem=FilesystemType.NTFS,
                cluster_size_bytes=4096,
                total_bytes=500 * (1024 ** 3),
                free_bytes=100 * (1024 ** 3),
                volume_label="Windows",
                bus_type="NVMe",
            )
        )
        with use_mock_backend(single_c_backend):
            report = check_drive_health(None)
            self.assertEqual(report.drive_letter, "C:")
            self.assertEqual(report.filesystem, "NTFS")

    def test_nonexistent_drive_returns_graceful_warning(self) -> None:
        """Querying an unmounted drive letter returns unknown filesystem and warning."""
        with use_mock_backend(self.mock_backend):
            report = check_drive_health("Z:")
            self.assertEqual(report.drive_letter, "Z:")
            self.assertEqual(report.filesystem, "unknown")
            self.assertGreater(len(report.warnings), 0)
            self.assertIn("not mounted", report.warnings[0].lower())

    def test_malformed_drive_specifier(self) -> None:
        """Invalid drive specifier returns warning without crashing."""
        with use_mock_backend(self.mock_backend):
            report = check_drive_health("???")
            self.assertEqual(report.filesystem, "unknown")
            self.assertGreater(len(report.warnings), 0)


class TestCmdHealthCliExecution(SmartDriveTestCase):
    """Tests CLI entry point cmd_health with Namespace arguments."""

    def test_cmd_health_json_flag(self) -> None:
        """cmd_health with json=True outputs valid JSON matching interface contract."""
        args = argparse.Namespace(drive=None, root=None, json=True)
        old_stdout = sys.stdout
        try:
            sys.stdout = io.StringIO()
            ret = cmd_health(args)
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertEqual(ret, 0)
        data = json.loads(out)
        self.assertIn("drive_letter", data)
        self.assertIn("filesystem", data)
        self.assertIn("trim_enabled", data)
        self.assertIn("free_percent", data)
        self.assertIn("warnings", data)

    def test_cmd_health_human_readable(self) -> None:
        """cmd_health without json flag outputs formatted terminal banners."""
        args = argparse.Namespace(drive=None, root=None, json=False)
        old_stdout = sys.stdout
        try:
            sys.stdout = io.StringIO()
            ret = cmd_health(args)
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertEqual(ret, 0)
        self.assertIn("SmartDrive-OS SSD Health", out)
        self.assertIn("Drive Volume:", out)
        self.assertIn("TRIM Status:", out)

    def test_cmd_health_invalid_drive_json(self) -> None:
        """cmd_health on invalid drive with json=True returns non-empty warnings."""
        args = argparse.Namespace(drive="Z:", root=None, json=True)
        old_stdout = sys.stdout
        try:
            sys.stdout = io.StringIO()
            ret = cmd_health(args)
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        data = json.loads(out)
        self.assertTrue(len(data.get("warnings", [])) > 0 or data.get("filesystem") == "unknown")


if __name__ == "__main__":
    unittest.main()
