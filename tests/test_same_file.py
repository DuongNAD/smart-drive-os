"""tests/test_same_file.py - "are these the same folder" must not be fooled by spelling, or by inode 0.

`same_file` backs the classifier's check that a path typed inside the drive does not really lead outside it,
and snapshot's recognition of the live .smart_drive. os.path.samefile trusts st_ino, but some network and FUSE
volumes report 0 for every file; there "same device and same inode" would make every path the same folder and
switch the link check off. Those volumes are compared by resolved path instead.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_drive.core.root import same_file


@unittest.skipIf(sys.platform == "win32", "os.symlink requires elevation on Windows")
class TestSameFile(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_same_"))  # deliberately not resolved: /var is an alias on macOS
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.drive = self.base / "drive"
        self.other = self.base / "other"
        self.drive.mkdir()
        self.other.mkdir()
        (self.drive / "f.txt").write_text("x", encoding="utf-8")

    def test_the_same_folder_spelled_differently(self) -> None:
        os.symlink(self.drive, self.base / "alias")
        self.assertTrue(same_file(self.drive, self.drive))
        self.assertTrue(same_file(self.base / "alias", self.drive))
        self.assertTrue(same_file(self.drive / "." / ".." / "drive", self.drive))
        self.assertTrue(same_file(self.base.resolve() / "drive", self.drive))  # /private/var vs /var on macOS

    def test_different_folders_and_missing_paths(self) -> None:
        self.assertFalse(same_file(self.drive, self.other))
        self.assertFalse(same_file(self.drive, self.drive / "f.txt"))
        self.assertFalse(same_file(self.drive, self.base / "nope"))
        self.assertFalse(same_file(self.base / "nope", self.base / "nope"))  # nothing to compare
        self.assertFalse(same_file(self.drive, "bad\0name"))  # an embedded NUL is an answer, not a crash

    def test_volumes_that_report_inode_zero_are_compared_by_resolved_path(self) -> None:
        real_stat = os.stat

        def zero_inodes(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            result = real_stat(path, *args, **kwargs)
            return os.stat_result((*result[:1], 0, *result[2:]))  # st_ino -> 0

        with patch("smart_drive.core.root.os.stat", zero_inodes):
            self.assertFalse(same_file(self.drive, self.other), "every folder must not become 'the same' on inode 0")
            os.symlink(self.drive, self.base / "alias")
            self.assertTrue(same_file(self.base / "alias", self.drive))
            self.assertTrue(same_file(self.drive, self.drive))


if __name__ == "__main__":
    unittest.main()
