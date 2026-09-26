"""tests/test_auditor.py - Comprehensive Unit Tests for StorageAuditor.

100% Python Standard Library unittest.
Tests Tiers 1 & 2:
- Storage breakdown across standard taxonomies.
- Nominal vs allocated physical bytes and cluster slack calculation.
- Category statistics (AI Models, Books/Learning, Code, Docs, etc.).
- Formatting utilities (format_bytes, format_count, format_percentage).
- Multi-format report generation (JSON, Markdown, ASCII table).
- Empty directory audit and boundary cases.
- Authoritative reference oracle derivation comparison.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.core.auditor import (
        AuditResult,
        CategoryStats,
        DirectoryNode,
        StorageAuditor,
        TaxonomyStats,
        format_bytes,
        format_count,
        format_percentage,
    )
except ImportError:
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from core.auditor import (
        AuditResult,
        CategoryStats,
        DirectoryNode,
        StorageAuditor,
        TaxonomyStats,
        format_bytes,
        format_count,
        format_percentage,
    )

from tests.helpers import CLUSTER_SIZE_BYTES, SmartDriveTestCase, oracle_cluster_allocation


class TestFormattingHelpers(SmartDriveTestCase):
    """Tier 1: Human-readable display formatting functions."""

    def test_format_bytes(self) -> None:
        """Byte formatting converts to appropriate units with precision."""
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1024), "1.00 KB")
        self.assertEqual(format_bytes(524_288), "512.00 KB")
        self.assertEqual(format_bytes(1_048_576), "1.00 MB")
        self.assertEqual(format_bytes(1_073_741_824), "1.00 GB")

    def test_format_count(self) -> None:
        """Count formatting adds thousands separators."""
        self.assertEqual(format_count(0), "0")
        self.assertEqual(format_count(999), "999")
        self.assertEqual(format_count(1000), "1,000")
        self.assertEqual(format_count(512000), "512,000")

    def test_format_percentage(self) -> None:
        """Percentage formatting includes percent sign with specified precision."""
        self.assertEqual(format_percentage(0.0), "0.0%")
        self.assertEqual(format_percentage(99.99, precision=2), "99.99%")
        self.assertEqual(format_percentage(50.0, precision=1), "50.0%")


class TestStorageAuditorExecution(SmartDriveTestCase):
    """Tier 1 & Tier 2: Storage breakdown and slack calculation on mock drive."""

    def test_audit_populated_mock_drive(self) -> None:
        """Audit accurately counts files, logical bytes, and calculates 512KB slack."""
        mock_root = self.create_mock_drive()
        auditor = StorageAuditor(str(mock_root))
        result = auditor.run_audit()

        self.assertIsInstance(result, (dict, AuditResult))
        self.assertGreater(result["total_files"], 15)
        self.assertGreater(result["total_logical_bytes"], 0)
        self.assertGreater(result["total_allocated_bytes"], result["total_logical_bytes"])
        self.assertGreater(result["total_slack_bytes"], 0)
        self.assertGreater(result["total_slack_percentage"], 0.0)

        # Taxonomies must be populated
        taxonomies = result["taxonomies"]
        self.assertIn("01_AI_Models", taxonomies)
        ai_tax = taxonomies["01_AI_Models"]
        self.assertGreater(ai_tax["file_count"], 0)
        self.assertGreater(ai_tax["nominal_bytes"], 0)

        # Categories must be populated
        categories = result["categories"]
        self.assertIn("AI Models", categories)
        ai_cat = categories["AI Models"]
        self.assertGreater(ai_cat["file_count"], 0)

    def test_audit_boundary_file_slack_accuracy(self) -> None:
        """Tests that 1-byte file creates 524,287 bytes slack in auditor."""
        root = self.test_dir / "boundary_audit"
        root.mkdir()
        (root / "one_byte.txt").write_bytes(b"A")

        auditor = StorageAuditor(str(root))
        result = auditor.run_audit()

        self.assertEqual(result["total_files"], 1)
        self.assertEqual(result["total_logical_bytes"], 1)
        self.assertEqual(result["total_allocated_bytes"], CLUSTER_SIZE_BYTES)
        self.assertEqual(result["total_slack_bytes"], CLUSTER_SIZE_BYTES - 1)

        oracle = oracle_cluster_allocation(1)
        self.assertEqual(result["total_allocated_bytes"], oracle["allocated_bytes"])
        self.assertEqual(result["total_slack_bytes"], oracle["slack_bytes"])

    def test_audit_empty_directory(self) -> None:
        """Audit on an empty directory returns zero files, zero bytes, zero slack."""
        empty_dir = self.test_dir / "empty_audit"
        empty_dir.mkdir()

        auditor = StorageAuditor(str(empty_dir))
        result = auditor.run_audit()

        self.assertEqual(result["total_files"], 0)
        self.assertEqual(result["total_logical_bytes"], 0)
        self.assertEqual(result["total_allocated_bytes"], 0)
        self.assertEqual(result["total_slack_bytes"], 0)
        self.assertEqual(result["total_slack_percentage"], 0.0)


class TestAuditReportFormatting(SmartDriveTestCase):
    """Tier 1: Multi-format report generation (JSON, Markdown, ASCII Table)."""

    def test_report_json_export(self) -> None:
        """AuditResult serializes to valid JSON with required keys."""
        mock_root = self.create_mock_drive()
        auditor = StorageAuditor(str(mock_root))
        result = auditor.run_audit()

        json_str = result.to_json() if hasattr(result, "to_json") else json.dumps(result)
        data = json.loads(json_str)

        self.assertIn("total_files", data)
        self.assertIn("total_logical_bytes", data)
        self.assertIn("total_allocated_bytes", data)
        self.assertIn("total_slack_bytes", data)
        self.assertIn("taxonomies", data)
        self.assertIn("categories", data)

    def test_report_markdown_and_ascii_table(self) -> None:
        """AuditResult generates non-empty Markdown and ASCII table reports."""
        mock_root = self.create_mock_drive()
        auditor = StorageAuditor(str(mock_root))
        result = auditor.run_audit()

        if hasattr(result, "to_markdown"):
            md = result.to_markdown()
            self.assertIsInstance(md, str)
            self.assertIn("# ", md)
            self.assertIn("Taxonomy", md)

        if hasattr(result, "to_ascii_table"):
            table = result.to_ascii_table()
            self.assertIsInstance(table, str)
            self.assertGreater(len(table), 50)


if __name__ == "__main__":
    unittest.main()
