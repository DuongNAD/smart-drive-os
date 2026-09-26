"""tests.test_junction - Unit Test Suite for NTFS Directory Junction Management Engine.

Verifies:
- Detection of directory junctions vs regular directories, files, non-existent paths.
- Extraction and normalization of junction targets (stripping Win32 \\?\\ prefixes).
- Creation of directory junctions without Administrator privileges.
- Transparent read/write operations through the junction.
- Safe removal of directory junctions leaving 100% of target files intact.
- Strict safety rejection when attempting to remove non-junction directories.
- Handling of paths with spaces.
- Detection and safe cleanup of broken junctions (missing target).

100% Zero-Dependency Python Standard Library unittest.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from smart_drive.core.junction import (
    create_directory_junction,
    get_junction_target,
    is_directory_junction,
    remove_directory_junction,
)


class TestJunctionEngine(unittest.TestCase):
    """Test suite for NTFS directory junction operations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_junction_test_")
        self.workspace = Path(self.temp_dir).resolve()

    def tearDown(self) -> None:
        # If there are any active junctions, remove them safely before deleting temp workspace
        if self.workspace.exists():
            for root, dirs, _ in os.walk(str(self.workspace)):
                for d in list(dirs):
                    full_p = Path(root) / d
                    if is_directory_junction(full_p):
                        try:
                            remove_directory_junction(full_p)
                        except Exception:
                            pass
            try:
                shutil.rmtree(str(self.workspace), ignore_errors=True)
            except Exception:
                pass

    def test_is_directory_junction_on_regular_dir_and_file(self) -> None:
        """A normal directory, normal file, and non-existent path return False."""
        normal_dir = self.workspace / "regular_dir"
        normal_dir.mkdir()
        self.assertFalse(is_directory_junction(normal_dir))

        normal_file = self.workspace / "regular_file.txt"
        normal_file.write_text("hello", encoding="utf-8")
        self.assertFalse(is_directory_junction(normal_file))

        non_existent = self.workspace / "does_not_exist"
        self.assertFalse(is_directory_junction(non_existent))

    def test_create_and_inspect_directory_junction(self) -> None:
        """Create a directory junction and verify detection, target resolution, and file access."""
        target_dir = self.workspace / "target_folder"
        target_dir.mkdir()
        test_file = target_dir / "sample.txt"
        test_file.write_text("data_inside_target", encoding="utf-8")

        junction_link = self.workspace / "junction_link"

        # Create junction
        ok = create_directory_junction(junction_link, target_dir)
        self.assertTrue(ok, "Expected create_directory_junction to succeed")

        # Verify detection
        self.assertTrue(is_directory_junction(junction_link))
        self.assertFalse(is_directory_junction(target_dir))

        # Verify target resolution (normalized path)
        resolved_target = get_junction_target(junction_link)
        self.assertIsNotNone(resolved_target)
        self.assertEqual(
            os.path.normcase(os.path.abspath(resolved_target)),
            os.path.normcase(os.path.abspath(str(target_dir))),
        )

        # Transparent read through junction
        read_file = junction_link / "sample.txt"
        self.assertTrue(read_file.exists())
        self.assertEqual(read_file.read_text(encoding="utf-8"), "data_inside_target")

        # Transparent write through junction
        write_file = junction_link / "written_via_link.txt"
        write_file.write_text("written_from_link", encoding="utf-8")

        # Verify file exists in physical target directory
        physical_file = target_dir / "written_via_link.txt"
        self.assertTrue(physical_file.exists())
        self.assertEqual(physical_file.read_text(encoding="utf-8"), "written_from_link")

    def test_safe_removal_preserves_target_data(self) -> None:
        """Removing a junction link MUST NOT delete the target folder or files inside."""
        target_dir = self.workspace / "valuable_data"
        target_dir.mkdir()
        for i in range(5):
            (target_dir / f"model_part_{i}.bin").write_bytes(f"VALUABLE_DATA_{i}".encode("utf-8"))

        junction_link = self.workspace / "cache_link"
        ok = create_directory_junction(junction_link, target_dir)
        self.assertTrue(ok)
        self.assertTrue(is_directory_junction(junction_link))

        # Safely remove junction
        removed = remove_directory_junction(junction_link)
        self.assertTrue(removed)

        # Link must be gone
        self.assertFalse(junction_link.exists())
        self.assertFalse(is_directory_junction(junction_link))

        # CRITICAL SAFETY: Target folder and ALL 5 files MUST be 100% intact!
        self.assertTrue(target_dir.exists())
        self.assertTrue(target_dir.is_dir())
        for i in range(5):
            f = target_dir / f"model_part_{i}.bin"
            self.assertTrue(f.exists(), f"Target file '{f.name}' was erroneously deleted!")
            self.assertEqual(f.read_bytes(), f"VALUABLE_DATA_{i}".encode("utf-8"))

    def test_remove_junction_safety_guard_rejects_normal_directory(self) -> None:
        """Attempting to remove a regular directory via remove_directory_junction raises ValueError."""
        normal_dir = self.workspace / "normal_unlinked_dir"
        normal_dir.mkdir()
        marker = normal_dir / "important.txt"
        marker.write_text("do not delete", encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            remove_directory_junction(normal_dir)

        self.assertIn("Safety Violation", str(ctx.exception))
        # Verify directory was NOT deleted
        self.assertTrue(normal_dir.exists())
        self.assertTrue(marker.exists())

    def test_junction_with_spaces_in_path(self) -> None:
        """Create, inspect, and remove directory junction with spaces in both link and target paths."""
        target_dir = self.workspace / "Target With Spaces"
        target_dir.mkdir()
        (target_dir / "spaced file.txt").write_text("content with space", encoding="utf-8")

        junction_link = self.workspace / "Link With Spaces"

        ok = create_directory_junction(junction_link, target_dir)
        self.assertTrue(ok)
        self.assertTrue(is_directory_junction(junction_link))

        resolved = get_junction_target(junction_link)
        self.assertIsNotNone(resolved)
        self.assertEqual(
            os.path.normcase(os.path.abspath(resolved)),
            os.path.normcase(os.path.abspath(str(target_dir))),
        )

        # Verify access
        self.assertTrue((junction_link / "spaced file.txt").exists())

        # Safe removal
        self.assertTrue(remove_directory_junction(junction_link))
        self.assertFalse(junction_link.exists())
        self.assertTrue(target_dir.exists())
        self.assertTrue((target_dir / "spaced file.txt").exists())

    def test_broken_junction_detection(self) -> None:
        """A junction whose target directory was deleted is still detected as a directory junction."""
        target_dir = self.workspace / "temporary_target"
        target_dir.mkdir()
        junction_link = self.workspace / "will_be_broken_link"

        ok = create_directory_junction(junction_link, target_dir)
        self.assertTrue(ok)
        self.assertTrue(is_directory_junction(junction_link))

        # Delete physical target to simulate broken link
        shutil.rmtree(str(target_dir))
        self.assertFalse(target_dir.exists())

        # Broken junction must still be recognized as a junction reparse point
        self.assertTrue(is_directory_junction(junction_link))

        # And can be safely unlinked
        self.assertTrue(remove_directory_junction(junction_link))
        self.assertFalse(is_directory_junction(junction_link))


if __name__ == "__main__":
    unittest.main()
