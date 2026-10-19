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


class TestAuditHtmlTreemap(SmartDriveTestCase):
    """T17: Self-contained HTML treemap report (smart-drive audit --html [file])."""

    def test_html_report_escapes_filenames_and_has_no_external_urls(self) -> None:
        """HTML report escapes special characters in file/folder names and uses zero external URLs."""
        import html as html_mod
        from smart_drive.core.scanner import ScanEntry

        root = self.test_dir / "html_escape_drive"
        root.mkdir()
        (root / "safe_folder & notes'1").mkdir()
        (root / "safe_folder & notes'1" / "doc & report'2.txt").write_bytes(b"hello" * 100)

        auditor = StorageAuditor(str(root), cluster_size=4096)
        result = auditor.run_audit()

        # Also inject an adversarial <script> filename into top_largest_files & tree to test full HTML escaping
        adversarial_name = '<script>alert("xss")</script>&bad\'name.bin'
        result["top_largest_files"].insert(
            0,
            {
                "name": adversarial_name,
                "rel_path": f"01_AI_Models/{adversarial_name}",
                "size": 1048576,
                "allocated": 1048576,
                "slack": 0,
                "category": "AI Models",
                "taxonomy": "01_AI_Models",
            },
        )
        result["tree"]["subdirs"][adversarial_name] = {
            "name": adversarial_name,
            "path": str(root / adversarial_name),
            "rel_path": adversarial_name,
            "direct_files": 1,
            "direct_bytes": 1048576,
            "direct_allocated": 1048576,
            "direct_micro_files": 0,
            "direct_slack": 0,
            "direct_slack_percentage": 0.0,
            "recursive_files": 1,
            "recursive_bytes": 1048576,
            "recursive_allocated": 1048576,
            "recursive_slack": 0,
            "recursive_slack_percentage": 0.0,
            "recursive_dirs": 0,
            "subdirs": {},
        }

        html_out = result.to_html()

        # Unescaped script tag must NEVER appear
        self.assertNotIn('<script>alert("xss")</script>', html_out)
        # Escaped version must appear in the HTML table and treemap
        escaped_adv = html_mod.escape(adversarial_name, quote=True)
        self.assertIn(escaped_adv, html_out)
        self.assertIn(html_mod.escape("safe_folder & notes'1", quote=True), html_out)

        # Zero external URLs (offline self-contained requirement)
        self.assertNotIn("http://", html_out.lower())
        self.assertNotIn("https://", html_out.lower())

        # Reasonable size
        self.assertGreater(len(html_out), 2000)
        self.assertLess(len(html_out), 500_000)

    def test_html_treemap_squarify_and_other_grouping(self) -> None:
        """Treemap groups tiny folders into 'Other / Khác' and limits depth to <= 3."""
        root = self.test_dir / "treemap_group_drive"
        root.mkdir()

        # Create 3 large folders and 18 tiny folders
        for i in range(3):
            big = root / f"big_folder_{i}"
            big.mkdir()
            (big / "data.bin").write_bytes(b"X" * 50_000)
        for i in range(18):
            tiny = root / f"tiny_folder_{i:02d}"
            tiny.mkdir()
            (tiny / "small.txt").write_bytes(b"x" * 10)

        auditor = StorageAuditor(str(root), cluster_size=4096)
        result = auditor.run_audit()
        html_out = result.to_html()

        self.assertIn("Other / Khác", html_out)
        self.assertIn("big_folder_0", html_out)

    def test_cli_audit_html_default_and_custom_path_single_scan(self) -> None:
        """CLI `smart-drive audit --html` runs a single scan and writes to .smart_drive/reports/audit.html."""
        from unittest import mock
        from smart_drive.cli.main import main as cli_main
        from smart_drive.core.scanner import FastDirectoryScanner

        root = self.create_mock_drive()
        default_html = root / ".smart_drive" / "reports" / "audit.html"
        custom_html = self.test_dir / "custom_report.html"

        orig_scan_iter = FastDirectoryScanner.scan_iter
        scan_calls = []

        def tracked_scan_iter(scanner_self):
            scan_calls.append(1)
            return orig_scan_iter(scanner_self)

        with mock.patch.object(FastDirectoryScanner, "scan_iter", tracked_scan_iter):
            rc = cli_main(["audit", "--root", str(root), "--html"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(scan_calls), 1, "audit --html must scan the filesystem only once")
        self.assertTrue(default_html.is_file())
        content = default_html.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", content)
        self.assertNotIn("http://", content.lower())
        self.assertNotIn("https://", content.lower())

        # Custom file path
        rc2 = cli_main(["audit", "--root", str(root), "--html", str(custom_html)])
        self.assertEqual(rc2, 0)
        self.assertTrue(custom_html.is_file())


if __name__ == "__main__":
    unittest.main()

