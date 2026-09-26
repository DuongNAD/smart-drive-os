"""tests/test_geometry.py - exFAT 512KB Cluster Geometry & Slack Math Tests.

100% Python Standard Library unittest.
Tests Tiers 1 & 2:
- 512KB allocation block size hardware constants.
- 0-byte file allocation (0 bytes, 0 clusters, 0 slack).
- 1-byte file allocation (524,288 bytes, 1 cluster, 524,287 bytes slack).
- Exact cluster boundary (524,288 bytes -> 1 cluster, 0 slack).
- Boundary + 1 (524,289 bytes -> 2 clusters, 524,287 bytes slack).
- Multi-cluster allocations & gigabyte-scale files.
- Negative size and invalid cluster size exception handling.
- Authoritative reference oracle derivation comparison.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.core.config import (
        CLUSTER_SIZE_BYTES,
        CLUSTER_SIZE_KB,
        calculate_allocated_bytes,
        calculate_cluster_count,
        calculate_slack_bytes,
        calculate_slack_percentage,
    )
except ImportError:
    # Fallback to smart_drive_manager during migration transition
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from config import (
        CLUSTER_SIZE_BYTES,
        CLUSTER_SIZE_KB,
        calculate_allocated_bytes,
        calculate_cluster_count,
        calculate_slack_bytes,
        calculate_slack_percentage,
    )

from tests.helpers import CLUSTER_SIZE, oracle_cluster_allocation


class TestClusterConstants(unittest.TestCase):
    """Tier 1: Verifies exFAT hardware geometry constants."""

    def test_cluster_size_bytes_equals_512kb(self) -> None:
        """Kingston XS2000 exFAT allocation block size is exactly 524,288 bytes (512 KB)."""
        self.assertEqual(CLUSTER_SIZE_BYTES, 524_288)

    def test_cluster_size_kb_equals_512(self) -> None:
        """CLUSTER_SIZE_KB must be 512."""
        self.assertEqual(CLUSTER_SIZE_KB, 512)

    def test_cluster_byte_kb_consistency(self) -> None:
        """CLUSTER_SIZE_BYTES must equal CLUSTER_SIZE_KB * 1024."""
        self.assertEqual(CLUSTER_SIZE_BYTES, CLUSTER_SIZE_KB * 1024)


class TestClusterAllocationMath(unittest.TestCase):
    """Tier 1 & Tier 2: Boundary and standard cluster allocation math."""

    def test_zero_byte_file(self) -> None:
        """0-byte file occupies 0 clusters and 0 bytes on exFAT directory entry."""
        oracle = oracle_cluster_allocation(0)
        self.assertEqual(calculate_allocated_bytes(0), oracle["allocated_bytes"])
        self.assertEqual(calculate_allocated_bytes(0), 0)
        self.assertEqual(calculate_slack_bytes(0), 0)
        self.assertEqual(calculate_cluster_count(0), 0)
        self.assertEqual(calculate_slack_percentage(0), 0.0)

    def test_one_byte_file(self) -> None:
        """1-byte file allocates 1 full 512KB cluster (524,287 bytes slack)."""
        oracle = oracle_cluster_allocation(1)
        self.assertEqual(calculate_allocated_bytes(1), 524_288)
        self.assertEqual(calculate_allocated_bytes(1), oracle["allocated_bytes"])
        self.assertEqual(calculate_slack_bytes(1), 524_287)
        self.assertEqual(calculate_slack_bytes(1), oracle["slack_bytes"])
        self.assertEqual(calculate_cluster_count(1), 1)
        expected_pct = (524_287 / 524_288) * 100.0
        self.assertAlmostEqual(calculate_slack_percentage(1), expected_pct, places=4)

    def test_exact_cluster_boundary(self) -> None:
        """524,288 bytes occupies exactly 1 cluster with 0 bytes slack (0.0%)."""
        size = CLUSTER_SIZE_BYTES
        oracle = oracle_cluster_allocation(size)
        self.assertEqual(calculate_allocated_bytes(size), 524_288)
        self.assertEqual(calculate_allocated_bytes(size), oracle["allocated_bytes"])
        self.assertEqual(calculate_slack_bytes(size), 0)
        self.assertEqual(calculate_cluster_count(size), 1)
        self.assertEqual(calculate_slack_percentage(size), 0.0)

    def test_boundary_plus_one_byte(self) -> None:
        """524,289 bytes crosses boundary, allocating 2 clusters (1,048,576 bytes)."""
        size = CLUSTER_SIZE_BYTES + 1
        oracle = oracle_cluster_allocation(size)
        self.assertEqual(calculate_allocated_bytes(size), 1_048_576)
        self.assertEqual(calculate_allocated_bytes(size), oracle["allocated_bytes"])
        self.assertEqual(calculate_slack_bytes(size), 524_287)
        self.assertEqual(calculate_slack_bytes(size), oracle["slack_bytes"])
        self.assertEqual(calculate_cluster_count(size), 2)
        expected_pct = (524_287 / 1_048_576) * 100.0
        self.assertAlmostEqual(calculate_slack_percentage(size), expected_pct, places=4)

    def test_exact_two_cluster_boundary(self) -> None:
        """1,048,576 bytes occupies exactly 2 clusters with 0 slack."""
        size = 2 * CLUSTER_SIZE_BYTES
        oracle = oracle_cluster_allocation(size)
        self.assertEqual(calculate_allocated_bytes(size), 1_048_576)
        self.assertEqual(calculate_slack_bytes(size), 0)
        self.assertEqual(calculate_cluster_count(size), 2)
        self.assertEqual(calculate_slack_percentage(size), 0.0)

    def test_boundary_minus_one_byte(self) -> None:
        """524,287 bytes occupies 1 cluster with 1 byte slack."""
        size = CLUSTER_SIZE_BYTES - 1
        oracle = oracle_cluster_allocation(size)
        self.assertEqual(calculate_allocated_bytes(size), 524_288)
        self.assertEqual(calculate_slack_bytes(size), 1)
        self.assertEqual(calculate_cluster_count(size), 1)
        expected_pct = (1 / 524_288) * 100.0
        self.assertAlmostEqual(calculate_slack_percentage(size), expected_pct, places=4)

    def test_large_model_file_allocation(self) -> None:
        """Test large 4.37GB GGUF model file allocation math."""
        # 4,692,840,448 bytes (~4.37 GB)
        nominal_size = 4_692_840_448
        expected_clusters = math.ceil(nominal_size / CLUSTER_SIZE_BYTES)
        expected_allocated = expected_clusters * CLUSTER_SIZE_BYTES
        oracle = oracle_cluster_allocation(nominal_size)

        self.assertEqual(calculate_cluster_count(nominal_size), expected_clusters)
        self.assertEqual(calculate_allocated_bytes(nominal_size), expected_allocated)
        self.assertEqual(calculate_allocated_bytes(nominal_size), oracle["allocated_bytes"])
        self.assertEqual(calculate_slack_bytes(nominal_size), expected_allocated - nominal_size)

    def test_custom_cluster_size(self) -> None:
        """Tests cluster allocation formulas with standard 4KB NTFS/ext4 cluster size."""
        custom_cluster = 4096
        self.assertEqual(calculate_allocated_bytes(0, cluster_size=custom_cluster), 0)
        self.assertEqual(calculate_allocated_bytes(1, cluster_size=custom_cluster), 4096)
        self.assertEqual(calculate_allocated_bytes(4096, cluster_size=custom_cluster), 4096)
        self.assertEqual(calculate_allocated_bytes(4097, cluster_size=custom_cluster), 8192)
        self.assertEqual(calculate_slack_bytes(4097, cluster_size=custom_cluster), 4095)


class TestGeometryErrorHandling(unittest.TestCase):
    """Tier 2: Error handling for negative and invalid geometry parameters."""

    def test_negative_nominal_size_raises_value_error(self) -> None:
        """Negative file size must raise ValueError."""
        with self.assertRaises(ValueError):
            calculate_allocated_bytes(-1)
        with self.assertRaises(ValueError):
            calculate_allocated_bytes(-524288)
        with self.assertRaises(ValueError):
            calculate_slack_bytes(-10)
        with self.assertRaises(ValueError):
            calculate_cluster_count(-1)

    def test_zero_or_negative_cluster_size_raises_value_error(self) -> None:
        """Zero or negative cluster_size must raise ValueError."""
        with self.assertRaises(ValueError):
            calculate_allocated_bytes(100, cluster_size=0)
        with self.assertRaises(ValueError):
            calculate_allocated_bytes(100, cluster_size=-4096)
        with self.assertRaises(ValueError):
            calculate_cluster_count(100, cluster_size=0)


if __name__ == "__main__":
    unittest.main()
