"""tests/test_taxonomy_consistency.py - `init`, `status` and `audit` must agree on the taxonomy names.

Regression: `init` creates 03_Development_Projects and 04_System_Workspaces, but the audit report
(and the public TAXONOMY_ROOT_DIRS constant it copied) still named 03_Personal_Documents and
04_Creative_Assets, so those rows were always empty and the folders `init` made were counted as
"Workspaces". The constant is now the single source of truth and a test keeps it in step with `init`.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from smart_drive.cli.main import main
from smart_drive.core.auditor import CANONICAL_TAXONOMIES, StorageAuditor
from smart_drive.core.config import LEGACY_TAXONOMY_ALIASES, TAXONOMY_ROOT_DIRS


class _Drive(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sd_tax_")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def cli(self, *argv: str) -> Tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = main(list(argv))
        return code, out.getvalue()

    def taxonomy_rows(self) -> dict:
        return json.loads(StorageAuditor(str(self.root)).run_audit().to_json())["taxonomies"]


class TestInitAndConstantsAgree(_Drive):
    def test_init_creates_exactly_the_standard_taxonomy_folders(self) -> None:
        self.cli("init", "--root", str(self.root))
        created = {p.name for p in self.root.iterdir() if p.is_dir() and p.name[:2].isdigit()}
        self.assertEqual(created, set(TAXONOMY_ROOT_DIRS))

    def test_the_audit_uses_the_same_names(self) -> None:
        self.assertEqual(tuple(CANONICAL_TAXONOMIES), tuple(TAXONOMY_ROOT_DIRS))
        for legacy in LEGACY_TAXONOMY_ALIASES:
            self.assertNotIn(legacy, {name.lower() for name in TAXONOMY_ROOT_DIRS})
        for current in LEGACY_TAXONOMY_ALIASES.values():
            self.assertIn(current, TAXONOMY_ROOT_DIRS)


class TestAuditCountsFilesInTheFoldersInitCreates(_Drive):
    def test_every_standard_folder_gets_its_own_row(self) -> None:
        self.cli("init", "--root", str(self.root))
        for index, name in enumerate(TAXONOMY_ROOT_DIRS):
            (self.root / name / "inside.txt").write_text("x" * (index + 1), encoding="utf-8")
        rows = self.taxonomy_rows()
        for name in TAXONOMY_ROOT_DIRS:
            self.assertEqual(rows[name]["file_count"], 1, f"{name}: {rows[name]}")
        self.assertNotIn("03_Personal_Documents", rows)
        self.assertNotIn("04_Creative_Assets", rows)
        self.assertEqual(rows["Workspaces"]["file_count"], 0)

    def test_folders_from_older_versions_still_count_in_todays_rows(self) -> None:
        for legacy_dir, row in (("03_Personal_Documents", "03_Development_Projects"), ("04_Creative_Assets", "04_System_Workspaces")):
            (self.root / legacy_dir).mkdir()
            (self.root / legacy_dir / "old.txt").write_text("old", encoding="utf-8")
        rows = self.taxonomy_rows()
        self.assertEqual(rows["03_Development_Projects"]["file_count"], 1)
        self.assertEqual(rows["04_System_Workspaces"]["file_count"], 1)

    def test_old_and_new_folder_names_on_one_drive_share_the_row(self) -> None:
        (self.root / "03_Personal_Documents").mkdir()
        (self.root / "03_Personal_Documents" / "a.txt").write_text("a", encoding="utf-8")
        (self.root / "03_Development_Projects").mkdir()
        (self.root / "03_Development_Projects" / "b.txt").write_text("b", encoding="utf-8")
        self.assertEqual(self.taxonomy_rows()["03_Development_Projects"]["file_count"], 2)


class TestStatusSeesBothSpellings(_Drive):
    def test_status_marks_standard_and_legacy_folders_present(self) -> None:
        (self.root / "01_AI_Models").mkdir()
        (self.root / "03_Personal_Documents").mkdir()  # legacy name of 03_Development_Projects
        _, out = self.cli("status", "--root", str(self.root), "--json")
        taxonomies = json.loads(out)["taxonomies"]
        self.assertEqual(list(taxonomies), list(TAXONOMY_ROOT_DIRS))
        self.assertTrue(taxonomies["01_AI_Models"])
        self.assertTrue(taxonomies["03_Development_Projects"])
        self.assertFalse(taxonomies["04_System_Workspaces"])


if __name__ == "__main__":
    unittest.main()
