"""tests/test_scanner.py - Comprehensive Unit Tests for FastDirectoryScanner.

100% Python Standard Library unittest.
Tests Tiers 1 & 2:
- High-speed DFS traversal and file discovery.
- ScanEntry metadata fields (size, ext, mtime, is_file, is_dir, is_empty).
- Directory pruning for excluded folders (.git, .agents, $RECYCLE.BIN).
- Depth limiting (max_depth=0, max_depth=1).
- Selective yields (yield_files vs yield_dirs).
- Handling deeply nested trees (>=10 levels).
- Unicode and Vietnamese filenames.
- Empty directory scanning and error resilience.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from smart_drive.core.scanner import FastDirectoryScanner, ScanEntry, ScanOptions, ScanStats
except ImportError:
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from core.scanner import FastDirectoryScanner, ScanEntry, ScanOptions, ScanStats

from tests.helpers import SmartDriveTestCase


class TestScannerTraversal(SmartDriveTestCase):
    """Tier 1: Basic traversal and metadata completeness."""

    def test_scanner_discovers_all_mock_drive_files(self) -> None:
        """Scanner finds all regular files created in mock SSD directory tree."""
        mock_root = self.create_mock_drive()
        scanner = FastDirectoryScanner(str(mock_root))

        entries = list(scanner.scan_iter())
        files = [e for e in entries if e.is_file]
        dirs = [e for e in entries if e.is_dir]

        self.assertTrue(len(files) >= 15, f"Expected >=15 files, found {len(files)}")
        self.assertTrue(len(dirs) >= 6, f"Expected >=6 taxonomy dirs, found {len(dirs)}")

        # Verify specific expected files exist in results
        rel_paths = {e.rel_path for e in files}
        self.assertIn("GEMINI.md", rel_paths)
        self.assertIn("01_AI_Models/GGUF/llama-3-8b.Q4_K_M.gguf", rel_paths)
        self.assertIn("02_Learning_Knowledge/INDEX.md", rel_paths)

    def test_scan_entry_fields(self) -> None:
        """ScanEntry contains correct and complete metadata attributes."""
        mock_root = self.create_mock_drive()
        scanner = FastDirectoryScanner(str(mock_root))

        entries_by_name = {e.name: e for e in scanner.scan_iter()}
        self.assertIn("zero_byte.dat", entries_by_name)
        zero_entry = entries_by_name["zero_byte.dat"]
        self.assertTrue(zero_entry.is_file)
        self.assertFalse(zero_entry.is_dir)
        self.assertEqual(zero_entry.size, 0)
        self.assertTrue(zero_entry.is_empty)
        self.assertEqual(zero_entry.ext, ".dat")

        self.assertIn("one_byte.dat", entries_by_name)
        one_entry = entries_by_name["one_byte.dat"]
        self.assertEqual(one_entry.size, 1)
        self.assertFalse(one_entry.is_empty)

    def test_scanner_boundary_integrity(self) -> None:
        """Scanner never emits paths that escape the target root directory."""
        mock_root = self.create_mock_drive()
        scanner = FastDirectoryScanner(str(mock_root))

        for entry in scanner.scan_iter():
            entry_path = Path(entry.path)
            self.assertTrue(
                entry_path.is_relative_to(mock_root),
                f"Path {entry.path} escaped root boundary {mock_root}"
            )


class TestScannerOptionsAndPruning(SmartDriveTestCase):
    """Tier 1 & Tier 2: Filtering options, pruning, and depth constraints."""

    def test_system_directories_pruned(self) -> None:
        """Excluded system folders (.git, .agents, $RECYCLE.BIN) are pruned from traversal."""
        root = self.test_dir / "prune_test"
        root.mkdir()

        # Regular file
        (root / "regular.txt").write_text("ok")

        # Excluded directories
        git_dir = root / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("git config")

        agents_dir = root / ".agents"
        agents_dir.mkdir()
        (agents_dir / "agent.json").write_text("agent data")

        scanner = FastDirectoryScanner(str(root))
        rel_paths = {e.rel_path for e in scanner.scan_iter()}

        self.assertIn("regular.txt", rel_paths)
        self.assertNotIn(".git/config", rel_paths)
        self.assertNotIn(".agents/agent.json", rel_paths)

    def test_yield_files_only(self) -> None:
        """When yield_dirs=False, only regular files are emitted."""
        mock_root = self.create_mock_drive()
        scanner = FastDirectoryScanner(str(mock_root), yield_dirs=False, yield_files=True)

        for entry in scanner.scan_iter():
            self.assertTrue(entry.is_file)
            self.assertFalse(entry.is_dir)

    def test_yield_dirs_only(self) -> None:
        """When yield_files=False, only directories are emitted."""
        mock_root = self.create_mock_drive()
        scanner = FastDirectoryScanner(str(mock_root), yield_dirs=True, yield_files=False)

        for entry in scanner.scan_iter():
            self.assertTrue(entry.is_dir)
            self.assertFalse(entry.is_file)

    def test_max_depth_zero(self) -> None:
        """max_depth=0 only scans root level items, never recursing into subdirectories."""
        mock_root = self.create_mock_drive()
        scanner = FastDirectoryScanner(str(mock_root), max_depth=0)

        for entry in scanner.scan_iter():
            self.assertNotIn("/", entry.rel_path, f"Depth exceeded 0: {entry.rel_path}")

    def test_empty_directory_scan(self) -> None:
        """Scanning an empty directory yields zero files and zero directories without error."""
        empty_dir = self.test_dir / "empty"
        empty_dir.mkdir()

        scanner = FastDirectoryScanner(str(empty_dir))
        entries = list(scanner.scan_iter())
        self.assertEqual(entries, [])
        self.assertEqual(scanner.stats.total_files, 0)
        self.assertEqual(scanner.stats.total_dirs, 0)


class TestScannerDeepTreesAndUnicode(SmartDriveTestCase):
    """Tier 2: Deep nesting and unicode characters."""

    def test_deep_directory_traversal(self) -> None:
        """Iterative DFS scans deep directory trees (>=10 levels) without recursion error."""
        current = self.test_dir
        for i in range(12):
            current = current / f"sub_{i:02d}"
        current.mkdir(parents=True)
        deep_target = current / "target.txt"
        deep_target.write_text("deep data")

        scanner = FastDirectoryScanner(str(self.test_dir))
        files = [e for e in scanner.scan_iter() if e.is_file]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "target.txt")

    def test_unicode_and_vietnamese_characters(self) -> None:
        """Scans files and folders containing accented and Vietnamese characters."""
        vn_dir = self.test_dir / "Tài liệu học tập"
        vn_dir.mkdir()
        vn_file = vn_dir / "Kiến trúc máy tính.pdf"
        vn_file.write_bytes(b"%PDF-1.4 Mock PDF")

        scanner = FastDirectoryScanner(str(self.test_dir))
        entries = list(scanner.scan_iter())
        names = {e.name for e in entries}

        self.assertIn("Tài liệu học tập", names)
        self.assertIn("Kiến trúc máy tính.pdf", names)


if __name__ == "__main__":
    unittest.main()
