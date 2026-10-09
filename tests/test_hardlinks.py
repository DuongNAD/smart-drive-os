"""tests/test_hardlinks.py - Hard-linked data is counted once and is never offered as a duplicate.

Found by auditing a real developer folder on APFS: 556,714 of its 965,000 paths were hard links (a link farm of
140,143 files), and the audit added every one of them up, reporting 265.7 GB for a folder that holds 171.8 GB
(`du` said 175 GB). The duplicate finder would have hashed every link again and offered the extra paths as
copies to delete, although deleting a hard link frees nothing. exFAT, the tool's home, has no hard links, which
is why neither case was handled.
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.duplicates import DuplicateDetector

MIB = 1024 * 1024


def _blob(seed: int, size: int = MIB) -> bytes:
    return random.Random(seed).randbytes(size) if hasattr(random.Random, "randbytes") else bytes(
        random.Random(seed).getrandbits(8) for _ in range(size)
    )


@unittest.skipIf(sys.platform == "win32", "needs stat() data that Windows' directory listing leaves at 0")
class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_links_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "data").mkdir()

    def write(self, rel: str, payload: bytes) -> Path:
        path = self.root / rel
        path.write_bytes(payload)
        return path

    def link(self, original: Path, rel: str) -> Path:
        path = self.root / rel
        os.link(original, path)
        return path


class TestTheAuditCountsHardLinkedDataOnce(_Folder):
    def setUp(self) -> None:
        super().setUp()
        original = self.write("data/original.bin", _blob(1))
        self.link(original, "data/link1.bin")
        self.link(original, "data/link2.bin")
        self.write("data/other.bin", _blob(2))

    def test_bytes_are_counted_per_inode_files_per_path(self) -> None:
        report = StorageAuditor(str(self.root)).run_audit()
        self.assertEqual(report["summary"]["total_files"], 4)  # four paths ...
        self.assertEqual(report["summary"]["total_logical_bytes"], 2 * MIB)  # ... holding two files' worth of data
        self.assertEqual(report["summary"]["total_allocated_bytes"], 2 * MIB)
        self.assertEqual(report["scan_stats"]["hardlinks_not_counted"], 2)

    def test_the_breakdowns_agree_with_the_total(self) -> None:
        report = StorageAuditor(str(self.root)).run_audit()
        self.assertEqual(sum(c["file_count"] for c in report["categories"].values()), 4)
        self.assertEqual(sum(c["nominal_bytes"] for c in report["categories"].values()), 2 * MIB)
        self.assertEqual(sum(t["nominal_bytes"] for t in report["taxonomies"].values()), 2 * MIB)

    def test_the_reports_say_how_many_paths_were_not_counted(self) -> None:
        report = StorageAuditor(str(self.root)).run_audit()
        self.assertIn("Hard Links Not Counted:  2", report.to_ascii_table())
        self.assertIn("**Hard Links Not Counted** | 2", report.to_markdown())

    def test_an_empty_file_with_two_names_is_one_empty_file(self) -> None:
        empty = self.write("data/empty.txt", b"")
        self.link(empty, "data/empty_too.txt")
        report = StorageAuditor(str(self.root)).run_audit()
        self.assertEqual(report["summary"]["empty_files_count"], 1)

    def test_a_folder_without_hard_links_is_reported_as_before(self) -> None:
        shutil.rmtree(self.root / "data")
        (self.root / "data").mkdir()
        self.write("data/a.bin", _blob(3))
        self.write("data/b.bin", _blob(3))  # a copy, not a link: both count
        report = StorageAuditor(str(self.root)).run_audit()
        self.assertEqual(report["summary"]["total_logical_bytes"], 2 * MIB)
        self.assertEqual(report["scan_stats"]["hardlinks_not_counted"], 0)
        self.assertNotIn("Hard Links Not Counted", report.to_ascii_table())
        self.assertNotIn("Hard Links Not Counted", report.to_markdown())


class TestHardLinksAreNotDuplicates(_Folder):
    def test_a_copy_of_a_linked_file_is_one_duplicate_not_two(self) -> None:
        original = self.write("data/x1.bin", _blob(4))
        self.link(original, "data/x2.bin")
        self.write("data/y.bin", _blob(4))  # a real, separate copy of the same bytes
        self.write("data/z.bin", _blob(5))
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)
        group = plan["duplicate_groups"][0]
        self.assertEqual(len(group["files"]), 2)  # one path per distinct file: a link and the copy
        self.assertEqual(group["reclaimable_bytes"], MIB)  # deleting the copy frees one MiB, not two
        self.assertEqual(plan["total_reclaimable_bytes"], MIB)
        self.assertEqual(plan["hardlinked_paths_ignored"], 1)

    def test_names_for_the_same_data_are_not_redundant_at_all(self) -> None:
        original = self.write("data/h1.bin", _blob(6))
        self.link(original, "data/h2.bin")
        self.link(original, "data/h3.bin")
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 0)  # used to offer two of them for deletion
        self.assertEqual(plan["total_reclaimable_bytes"], 0)
        self.assertEqual(plan["hardlinked_paths_ignored"], 2)

    def test_linked_paths_are_not_hashed_again(self) -> None:
        original = self.write("data/h1.bin", _blob(7))
        self.link(original, "data/h2.bin")
        self.write("data/copy.bin", _blob(7))
        from unittest.mock import patch

        import smart_drive.core.duplicates as duplicates

        real = duplicates.compute_full_sha256
        hashed = []

        def counting(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            hashed.append(Path(path).name)
            return real(path, *args, **kwargs)

        with patch.object(duplicates, "compute_full_sha256", counting):
            DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(len(hashed), 2)  # the data of h1/h2 once, and the copy

    def test_plain_duplicates_behave_as_before(self) -> None:
        self.write("data/a.bin", _blob(8))
        self.write("data/b.bin", _blob(8))
        self.write("data/c.bin", _blob(8))
        plan = DuplicateDetector(str(self.root)).generate_reclamation_plan()
        self.assertEqual(plan["duplicate_group_count"], 1)
        self.assertEqual(plan["total_reclaimable_bytes"], 2 * MIB)
        self.assertEqual(plan["hardlinked_paths_ignored"], 0)


if __name__ == "__main__":
    unittest.main()
