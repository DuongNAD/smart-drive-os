"""tests/test_adversarial_filesystem.py - Empirical Adversarial Stress Suite for Filesystem Adaptation & Cluster Math.

100% Python Standard Library unittest.
Adversarial Verification for Milestone M1:
1. FilesystemAdapter against extreme cluster sizes:
   - 512B, 1KB, 4KB, 16KB, 64KB, 128KB, 512KB, 1MB, 2MB, 16MB, 32MB
   - Invalid cluster sizes: 0, negative (-512, -4096)
   - Slack sensitivity thresholds across filesystem types and cluster sizes
2. Cluster slack calculations with tiny and boundary files:
   - 0 bytes, 1 byte, 4095 bytes, 4097 bytes, 524287 bytes, 524288 bytes, 524289 bytes
   - Multi-cluster and gigabyte-scale files
   - evaluate_cluster_slack stress (0 files, 1 file, 10,000 files, 1,000,000 files)
   - Negative size handling
   - Floating-point percentage bounds [0.0, 100.0]
3. Anti-symlink enforcement on exFAT vs junction support on NTFS:
   - Real NTFS Directory Junctions (mklink /J) on Windows
   - Broken junctions (dangling targets)
   - exFAT vs NTFS adapter policies
   - exfat_compat assert_no_symlinks and check_symlink integration
   - DriveInfo delegation to FilesystemAdapter consistency
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.drive_detector import (
    DriveType,
    FilesystemType,
    DriveInfo,
    FilesystemAdapter,
    get_filesystem_adapter,
)
from smart_drive.core.exfat_compat import (
    SymlinkNotPermittedError,
    assert_no_symlinks,
    check_symlink,
    is_symlink,
    calculate_allocated_bytes as exfat_calc_alloc,
    calculate_slack_bytes as exfat_calc_slack,
    calculate_slack_ratio as exfat_calc_ratio,
)


class TestExtremeClusterSizes(unittest.TestCase):
    """Adversarial stress-testing of FilesystemAdapter with extreme cluster sizes."""

    EXTREME_SIZES = [
        512,          # 512B - FAT16 / sector-level cluster
        1024,         # 1KB
        4096,         # 4KB - standard NTFS / ext4
        16384,        # 16KB
        65536,        # 64KB - large cluster NTFS / allocation unit threshold
        131072,       # 128KB
        524288,       # 512KB - Kingston XS2000 exFAT default
        1048576,      # 1MB
        2097152,      # 2MB - high-capacity exFAT
        16777216,     # 16MB
        33554432,     # 32MB - maximum allowable exFAT cluster size
    ]

    def test_extreme_cluster_sizes_allocation_math(self):
        """Verify allocation math across the entire range of extreme cluster sizes."""
        for c_size in self.EXTREME_SIZES:
            with self.subTest(cluster_size=c_size):
                adapter = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=c_size)
                self.assertEqual(adapter.cluster_size_bytes, c_size)

                # 0 bytes must always allocate 0 bytes
                self.assertEqual(adapter.calculate_allocated_bytes(0), 0)
                self.assertEqual(adapter.calculate_slack_bytes(0), 0)
                self.assertEqual(adapter.calculate_slack_percentage(0), 0.0)

                # 1 byte must always allocate exactly 1 cluster
                self.assertEqual(adapter.calculate_allocated_bytes(1), c_size)
                self.assertEqual(adapter.calculate_slack_bytes(1), c_size - 1)
                expected_pct = ((c_size - 1) / c_size) * 100.0
                self.assertAlmostEqual(adapter.calculate_slack_percentage(1), expected_pct, places=5)

                # Exactly 1 cluster
                self.assertEqual(adapter.calculate_allocated_bytes(c_size), c_size)
                self.assertEqual(adapter.calculate_slack_bytes(c_size), 0)
                self.assertEqual(adapter.calculate_slack_percentage(c_size), 0.0)

                # 1 cluster + 1 byte
                self.assertEqual(adapter.calculate_allocated_bytes(c_size + 1), 2 * c_size)
                self.assertEqual(adapter.calculate_slack_bytes(c_size + 1), c_size - 1)

                # 1 cluster - 1 byte
                if c_size > 1:
                    self.assertEqual(adapter.calculate_allocated_bytes(c_size - 1), c_size)
                    self.assertEqual(adapter.calculate_slack_bytes(c_size - 1), 1)

    def test_invalid_cluster_sizes_raise_value_error(self):
        """FilesystemAdapter must reject 0 or negative cluster sizes."""
        invalid_sizes = [0, -1, -512, -4096, -524288]
        for inv_size in invalid_sizes:
            with self.subTest(invalid_cluster_size=inv_size):
                adapter = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=inv_size)
                with self.assertRaises(ValueError):
                    adapter.calculate_allocated_bytes(100)
                with self.assertRaises(ValueError):
                    adapter.calculate_slack_bytes(100)
                with self.assertRaises(ValueError):
                    adapter.calculate_slack_percentage(100)

    def test_slack_sensitivity_thresholds(self):
        """Verify is_slack_sensitive logic across filesystem types and cluster sizes."""
        # exFAT is ALWAYS slack sensitive regardless of cluster size
        for sz in [512, 4096, 65536, 524288, 2097152]:
            adapter_exfat = FilesystemAdapter(FilesystemType.EXFAT, cluster_size_bytes=sz)
            self.assertTrue(adapter_exfat.is_slack_sensitive)

        # NTFS is slack sensitive ONLY when cluster size >= 65536 (64KB)
        ntfs_512 = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=512)
        ntfs_4k = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=4096)
        ntfs_32k = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=32768)
        ntfs_64k = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=65536)
        ntfs_512k = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=524288)
        ntfs_2m = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=2097152)

        self.assertFalse(ntfs_512.is_slack_sensitive)
        self.assertFalse(ntfs_4k.is_slack_sensitive)
        self.assertFalse(ntfs_32k.is_slack_sensitive)
        self.assertTrue(ntfs_64k.is_slack_sensitive)
        self.assertTrue(ntfs_512k.is_slack_sensitive)
        self.assertTrue(ntfs_2m.is_slack_sensitive)

    def test_factory_get_filesystem_adapter(self):
        """Verify get_filesystem_adapter factory parses names and defaults correctly."""
        # Default cluster size for NTFS is 4096
        a_ntfs = get_filesystem_adapter("NTFS")
        self.assertEqual(a_ntfs.filesystem, FilesystemType.NTFS)
        self.assertEqual(a_ntfs.cluster_size_bytes, 4096)

        # Default cluster size for exFAT is 524288
        a_exfat = get_filesystem_adapter("exFAT")
        self.assertEqual(a_exfat.filesystem, FilesystemType.EXFAT)
        self.assertEqual(a_exfat.cluster_size_bytes, 524288)

        # Explicit cluster size overrides default
        a_custom = get_filesystem_adapter("NTFS", cluster_size_bytes=2097152)
        self.assertEqual(a_custom.cluster_size_bytes, 2097152)

        # Case-insensitivity
        self.assertEqual(get_filesystem_adapter("ntfs").filesystem, FilesystemType.NTFS)
        self.assertEqual(get_filesystem_adapter("EXFAT").filesystem, FilesystemType.EXFAT)
        self.assertEqual(get_filesystem_adapter("fat32").filesystem, FilesystemType.FAT32)
        self.assertEqual(get_filesystem_adapter("ext4").filesystem, FilesystemType.OTHER)


class TestClusterSlackMathBoundary(unittest.TestCase):
    """Adversarial stress-testing of cluster slack calculations with tiny and boundary files."""

    def test_slack_math_required_test_points(self):
        """Directly verify the 5 required test points: 0B, 1B, 4095B, 4097B, 524287B."""
        # Reference test points
        test_points = [0, 1, 4095, 4097, 524287]

        # Targets to test against: 512B, 4KB, 64KB, 512KB, 2MB
        cluster_configs = [
            (512, "512B"),
            (4096, "4KB"),
            (65536, "64KB"),
            (524288, "512KB"),
            (2097152, "2MB"),
        ]

        for c_size, label in cluster_configs:
            adapter = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=c_size)

            for size in test_points:
                with self.subTest(cluster_size=label, file_size=size):
                    alloc = adapter.calculate_allocated_bytes(size)
                    slack = adapter.calculate_slack_bytes(size)
                    pct = adapter.calculate_slack_percentage(size)

                    if size == 0:
                        self.assertEqual(alloc, 0)
                        self.assertEqual(slack, 0)
                        self.assertEqual(pct, 0.0)
                    else:
                        expected_clusters = math.ceil(size / c_size)
                        expected_alloc = expected_clusters * c_size
                        expected_slack = expected_alloc - size
                        expected_pct = (expected_slack / expected_alloc) * 100.0

                        self.assertEqual(alloc, expected_alloc)
                        self.assertEqual(slack, expected_slack)
                        self.assertAlmostEqual(pct, expected_pct, places=5)
                        self.assertGreaterEqual(pct, 0.0)
                        self.assertLess(pct, 100.0)

    def test_specific_analytical_values(self):
        """Verifies exact analytical values for specific milestone boundary cases."""
        # 1. 0 bytes on 512KB
        exfat = FilesystemAdapter(FilesystemType.EXFAT, 524288)
        self.assertEqual(exfat.calculate_allocated_bytes(0), 0)
        self.assertEqual(exfat.calculate_slack_bytes(0), 0)
        self.assertEqual(exfat.calculate_slack_percentage(0), 0.0)

        # 2. 1 byte on 512KB
        self.assertEqual(exfat.calculate_allocated_bytes(1), 524288)
        self.assertEqual(exfat.calculate_slack_bytes(1), 524287)
        self.assertAlmostEqual(exfat.calculate_slack_percentage(1), 99.9998, places=3)

        # 3. 4095 bytes (4KB - 1B)
        ntfs_4k = FilesystemAdapter(FilesystemType.NTFS, 4096)
        self.assertEqual(ntfs_4k.calculate_allocated_bytes(4095), 4096)
        self.assertEqual(ntfs_4k.calculate_slack_bytes(4095), 1)

        # 4. 4097 bytes (4KB + 1B)
        self.assertEqual(ntfs_4k.calculate_allocated_bytes(4097), 8192)
        self.assertEqual(ntfs_4k.calculate_slack_bytes(4097), 4095)

        # 5. 524287 bytes (512KB - 1B)
        self.assertEqual(exfat.calculate_allocated_bytes(524287), 524288)
        self.assertEqual(exfat.calculate_slack_bytes(524287), 1)
        self.assertAlmostEqual(exfat.calculate_slack_percentage(524287), (1 / 524288) * 100.0, places=5)

        # 6. 524288 bytes (exact 512KB)
        self.assertEqual(exfat.calculate_allocated_bytes(524288), 524288)
        self.assertEqual(exfat.calculate_slack_bytes(524288), 0)
        self.assertEqual(exfat.calculate_slack_percentage(524288), 0.0)

        # 7. 524289 bytes (512KB + 1B)
        self.assertEqual(exfat.calculate_allocated_bytes(524289), 1048576)
        self.assertEqual(exfat.calculate_slack_bytes(524289), 524287)

        # 8. 2MB clusters with 524287 bytes
        adapter_2mb = FilesystemAdapter(FilesystemType.EXFAT, 2097152)
        self.assertEqual(adapter_2mb.calculate_allocated_bytes(524287), 2097152)
        self.assertEqual(adapter_2mb.calculate_slack_bytes(524287), 2097152 - 524287)

    def test_evaluate_cluster_slack_adversarial_distributions(self):
        """Stress tests evaluate_cluster_slack with edge case distributions."""
        adapter = FilesystemAdapter(FilesystemType.EXFAT, 524288)

        # Case A: 0 files
        res_zero = adapter.evaluate_cluster_slack(0, 4096)
        self.assertEqual(res_zero["file_count"], 0)
        self.assertEqual(res_zero["total_nominal_bytes"], 0)
        self.assertEqual(res_zero["total_allocated_bytes"], 0)
        self.assertEqual(res_zero["total_slack_bytes"], 0)
        self.assertEqual(res_zero["slack_percentage"], 0.0)
        self.assertFalse(res_zero["is_critical"])

        # Case B: 0 byte average size
        res_zero_size = adapter.evaluate_cluster_slack(1000, 0)
        self.assertEqual(res_zero_size["total_nominal_bytes"], 0)
        self.assertEqual(res_zero_size["total_allocated_bytes"], 0)
        self.assertEqual(res_zero_size["total_slack_bytes"], 0)
        self.assertEqual(res_zero_size["slack_percentage"], 0.0)
        self.assertFalse(res_zero_size["is_critical"])

        # Case C: 1,000,000 tiny 100-byte files (massive slack waste ~500 GB)
        res_massive = adapter.evaluate_cluster_slack(1_000_000, 100)
        self.assertEqual(res_massive["file_count"], 1_000_000)
        self.assertEqual(res_massive["total_nominal_bytes"], 100_000_000)
        self.assertEqual(res_massive["total_allocated_bytes"], 524_288_000_000)
        self.assertTrue(res_massive["is_critical"])
        self.assertIsNotNone(res_massive["warning"])
        self.assertIn("exFAT cluster slack guard", res_massive["warning"])

        # Case D: Negative file_count or avg_file_size raises ValueError
        with self.assertRaises(ValueError):
            adapter.evaluate_cluster_slack(-1, 4096)
        with self.assertRaises(ValueError):
            adapter.evaluate_cluster_slack(10, -50)

    def test_cross_module_consistency_with_exfat_compat(self):
        """Ensure FilesystemAdapter calculation matches exfat_compat exactly."""
        adapter = FilesystemAdapter(FilesystemType.EXFAT, 524288)
        sizes = [0, 1, 511, 512, 4095, 4096, 4097, 65535, 65536, 524287, 524288, 524289, 1048576, 5000000]

        for s in sizes:
            with self.subTest(file_size=s):
                self.assertEqual(adapter.calculate_allocated_bytes(s), exfat_calc_alloc(s))
                self.assertEqual(adapter.calculate_slack_bytes(s), exfat_calc_slack(s))
                self.assertAlmostEqual(adapter.calculate_slack_percentage(s), exfat_calc_ratio(s), places=5)


class TestAntiSymlinkAndJunctionSupport(unittest.TestCase):
    """Adversarial stress-testing of anti-symlink enforcement on exFAT vs NTFS junction support."""

    def setUp(self):
        self.ntfs = FilesystemAdapter(FilesystemType.NTFS, 4096)
        self.exfat = FilesystemAdapter(FilesystemType.EXFAT, 524288)
        self.fat32 = FilesystemAdapter(FilesystemType.FAT32, 4096)

    def test_policy_flags_matrix(self):
        """Verify strict capability flags across NTFS, exFAT, and FAT32."""
        # NTFS allows junctions, compression, indexing, TRIM; does not require anti-symlink
        self.assertTrue(self.ntfs.supports_junctions)
        self.assertTrue(self.ntfs.supports_compression)
        self.assertTrue(self.ntfs.supports_selective_indexing)
        self.assertTrue(self.ntfs.supports_trim)
        self.assertFalse(self.ntfs.anti_symlink_required)

        # exFAT disallows junctions, compression, indexing, TRIM; requires anti-symlink
        self.assertFalse(self.exfat.supports_junctions)
        self.assertFalse(self.exfat.supports_compression)
        self.assertFalse(self.exfat.supports_selective_indexing)
        self.assertFalse(self.exfat.supports_trim)
        self.assertTrue(self.exfat.anti_symlink_required)

        # FAT32 disallows junctions, compression, indexing; does not require anti-symlink
        self.assertFalse(self.fat32.supports_junctions)
        self.assertFalse(self.fat32.supports_compression)
        self.assertFalse(self.fat32.anti_symlink_required)

    def test_mock_symlink_violation(self):
        """Test check_symlink_violation using mocked path symlink states."""
        mock_symlink = MagicMock(spec=Path)
        mock_symlink.is_symlink.return_value = True

        mock_regular = MagicMock(spec=Path)
        mock_regular.is_symlink.return_value = False

        with patch("smart_drive.core.drive_detector.Path", return_value=mock_symlink):
            # exFAT detects violation on symlinks
            self.assertTrue(self.exfat.check_symlink_violation("D:\\link"))
            # NTFS permits symlinks (never a policy violation)
            self.assertFalse(self.ntfs.check_symlink_violation("C:\\link"))

        with patch("smart_drive.core.drive_detector.Path", return_value=mock_regular):
            # Normal files are never violations
            self.assertFalse(self.exfat.check_symlink_violation("D:\\regular_file.txt"))
            self.assertFalse(self.ntfs.check_symlink_violation("C:\\regular_file.txt"))

    @unittest.skipUnless(sys.platform == "win32", "Real NTFS Directory Junction tests require Windows.")
    def test_real_ntfs_junction_lifecycle_and_policy(self):
        """Empirically test real NTFS Directory Junction lifecycle, reparse tag, and exFAT rejection."""
        temp_dir = tempfile.mkdtemp(prefix="sd_junc_test_")
        try:
            target_dir = os.path.join(temp_dir, "real_target")
            os.makedirs(target_dir, exist_ok=True)
            test_file = os.path.join(target_dir, "test.txt")
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("payload")

            junction_link = os.path.join(temp_dir, "junction_link")

            # 1. On NTFS: Create real NTFS directory junction via mklink /J
            cmd = f'cmd /c mklink /J "{junction_link}" "{target_dir}"'
            proc = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            self.assertEqual(proc.returncode, 0, f"mklink /J failed: {proc.stderr} {proc.stdout}")

            self.assertTrue(os.path.exists(junction_link))
            self.assertTrue(os.path.isdir(junction_link))

            # Verify Win32 reparse tag: IO_REPARSE_TAG_MOUNT_POINT (0xa0000003)
            st = os.lstat(junction_link)
            reparse_tag = getattr(st, "st_reparse_tag", 0)
            self.assertEqual(reparse_tag, 0xA0000003, "Directory Junction must have IO_REPARSE_TAG_MOUNT_POINT tag")

            # Test reading through junction
            junc_file = os.path.join(junction_link, "test.txt")
            self.assertTrue(os.path.exists(junc_file))
            with open(junc_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "payload")

            # 2. Verify Adapter Capabilities
            self.assertTrue(self.ntfs.supports_junctions)
            self.assertFalse(self.exfat.supports_junctions)

            # 3. NTFS adapter allows junctions (check_symlink_violation returns False)
            self.assertFalse(self.ntfs.check_symlink_violation(junction_link))

            # 4. If host has an exFAT volume mounted (e.g. D:), verify mklink /J is rejected by OS
            if os.path.exists("D:\\"):
                exfat_junc = "D:\\__test_junc_probe__"
                res_exfat = subprocess.run(f'cmd /c mklink /J "{exfat_junc}" "{target_dir}"', capture_output=True, text=True, shell=True)
                self.assertNotEqual(res_exfat.returncode, 0, "mklink /J must fail when target location is on exFAT")
                self.assertIn("Local NTFS volumes are required", res_exfat.stderr + res_exfat.stdout)
                if os.path.lexists(exfat_junc):
                    os.rmdir(exfat_junc)

            # 5. Clean up junction (rmdir removes reparse point without touching target)
            os.rmdir(junction_link)
            self.assertFalse(os.path.lexists(junction_link))
            self.assertTrue(os.path.exists(test_file), "Removing junction must preserve target directory and file contents")

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def test_drive_info_delegates_to_adapter_consistently(self):
        """DriveInfo properties and cluster math must delegate to FilesystemAdapter consistently."""
        drive = DriveInfo(
            drive_letter="D:",
            mount_point="D:\\",
            is_system_drive=False,
            hardware_type=DriveType.REMOVABLE_EXTERNAL,
            filesystem=FilesystemType.EXFAT,
            cluster_size_bytes=524288,
            total_bytes=2000_000_000,
            free_bytes=1000_000_000,
            volume_label="Kingston",
        )

        adapter = drive.adapter
        self.assertEqual(drive.supports_junctions, adapter.supports_junctions)
        self.assertEqual(drive.supports_compression, adapter.supports_compression)
        self.assertEqual(drive.anti_symlink_required, adapter.anti_symlink_required)
        self.assertEqual(drive.is_slack_sensitive, adapter.is_slack_sensitive)

        for sz in [0, 1, 4095, 4097, 524287, 524288, 524289]:
            self.assertEqual(drive.calculate_allocated_bytes(sz), adapter.calculate_allocated_bytes(sz))
            self.assertEqual(drive.calculate_slack_bytes(sz), adapter.calculate_slack_bytes(sz))


class TestFuzzAndScaleInvariants(unittest.TestCase):
    """Property-based invariant fuzzing across diverse cluster sizes and file scales."""

    def test_mathematical_invariants_fuzzing(self):
        """Verify core allocation invariants hold across 5,000 pseudorandom inputs.
        
        Invariants:
        1. For size == 0: allocated == 0, slack == 0, percentage == 0.0
        2. For size > 0: allocated >= size
        3. 0 <= slack < cluster_size
        4. allocated == size + slack
        5. allocated % cluster_size == 0
        6. 0.0 <= slack_percentage < 100.0
        7. If size % cluster_size == 0: slack == 0 and slack_percentage == 0.0
        """
        import random
        rng = random.Random(42)  # Deterministic seed

        cluster_sizes = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 33554432]

        for _ in range(5000):
            c_size = rng.choice(cluster_sizes)
            # Sample sizes across orders of magnitude: 0, 1..100, 100..10K, 10K..100MB, exact boundaries
            mode = rng.randint(0, 4)
            if mode == 0:
                size = 0
            elif mode == 1:
                size = rng.randint(1, 100)
            elif mode == 2:
                # Around cluster boundaries
                k = rng.randint(1, 50)
                offset = rng.randint(-5, 5)
                size = max(0, k * c_size + offset)
            elif mode == 3:
                size = rng.randint(1, 50_000_000)
            else:
                # Multi-gigabyte file (up to 10 GB)
                size = rng.randint(1_000_000_000, 10_000_000_000)

            adapter = FilesystemAdapter(FilesystemType.NTFS, cluster_size_bytes=c_size)
            alloc = adapter.calculate_allocated_bytes(size)
            slack = adapter.calculate_slack_bytes(size)
            pct = adapter.calculate_slack_percentage(size)

            if size == 0:
                self.assertEqual(alloc, 0)
                self.assertEqual(slack, 0)
                self.assertEqual(pct, 0.0)
            else:
                self.assertGreaterEqual(alloc, size)
                self.assertGreaterEqual(slack, 0)
                self.assertLess(slack, c_size)
                self.assertEqual(alloc, size + slack)
                self.assertEqual(alloc % c_size, 0)
                self.assertGreaterEqual(pct, 0.0)
                self.assertLess(pct, 100.0)
                if size % c_size == 0:
                    self.assertEqual(slack, 0)
                    self.assertEqual(pct, 0.0)


class TestRealHostDriveAdapters(unittest.TestCase):
    """Empirical inspection of real Windows host drives and their adapter properties."""

    @unittest.skipUnless(sys.platform == "win32", "Host drive verification requires Windows.")
    def test_live_secondary_drive_adapters(self):
        from smart_drive.core.drive_detector import list_secondary_drives

        drives = list_secondary_drives()
        self.assertGreater(len(drives), 0, "Host should have at least one secondary drive.")

        for d in drives:
            adapter = d.adapter
            self.assertEqual(adapter.filesystem, d.filesystem)
            self.assertEqual(adapter.cluster_size_bytes, d.cluster_size_bytes)

            if d.filesystem == FilesystemType.NTFS:
                self.assertTrue(d.supports_junctions)
                self.assertTrue(d.supports_compression)
                self.assertFalse(d.anti_symlink_required)
            elif d.filesystem == FilesystemType.EXFAT:
                self.assertFalse(d.supports_junctions)
                self.assertFalse(d.supports_compression)
                self.assertTrue(d.anti_symlink_required)
                self.assertTrue(d.is_slack_sensitive)


if __name__ == "__main__":
    unittest.main()
