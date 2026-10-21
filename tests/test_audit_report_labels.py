"""tests/test_audit_report_labels.py - the text and markdown reports of `audit` name the cluster size they used.

Regression: since 1.4.0 `audit` works every figure out at the volume's own cluster size (`cluster_size` in its JSON;
`cluster_size_source` says whether it was detected or assumed), but its text report still labelled the cluster count
"Allocated 512KB Clusters", and the markdown report did the same, titled its hotspot section "512KB Cluster Slack
Hotspots", said micro-files waste "> 90% ... on 512 KB clusters" and that each `._*` fork wastes 512 KB. On a 4 KB NTFS
drive that is a count of 4 KB clusters under a 512 KB label. Both headers divided the size by 1024 as well, so a cluster
below 1 KB read "0 KB" and 1536 B read "1 KB". Every label now comes from `fsinfo.cluster_size_text` and follows the
figures, and the text header says where the size came from: `Cluster Size: 4 KB (4096 B, detected)`.

The tests run `audit` on a volume with a 4 KB cluster, on odd ones (1536 B, 512 B) and on one whose cluster cannot be
read. For that one the fallback is patched to 64 KB, as in test_slack_cluster_routing.py, so that a label which keeps
saying 512 KB is caught. Each label has to say the cluster the figures are at, and the count printed beside it has to be
the number of clusters of that size that the files take.

The markdown report's tables are checked as well: the delimiter row of the micro-file table ended in `||`, nine cells
under an eight-column header, and GitHub Flavored Markdown does not draw a table whose delimiter row is not as wide as
its header.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

from smart_drive.cli.main import main
from smart_drive.core.auditor import StorageAuditor
from smart_drive.core.fsinfo import FilesystemInfo

# `smart_drive.cli` re-exports the `main` function, so the attribute `smart_drive.cli.main` is that function and not the
# submodule; the modules whose names the code under test looks up are patched through the module objects instead.
AUDITOR = importlib.import_module("smart_drive.core.auditor")
CLI_AUDIT = importlib.import_module("smart_drive.cli.cmd_audit")
FSINFO = importlib.import_module("smart_drive.core.fsinfo")

# 25 micro-files in one folder, so that the hotspot sections are rendered whatever the cluster (a 20-byte file is 99.5%
# slack on a 4 KB cluster and still 96% on 512 B ones), and two larger files
FILES: List[Tuple[str, int]] = [(f"notes/n{i:02d}.txt", 20) for i in range(25)] + [
    ("data/big.bin", 70_000),
    ("data/medium.bin", 5_000),
]
FALLBACK = 65_536

# Where each label stands in a report (one match per pattern: the label is there once, and says the cluster)
TEXT_LABELS: Dict[str, str] = {
    "header": r"Cluster Size: (.+?) \(",
    "count": r"^  Allocated Clusters: +[\d,]+ \((.+?) each\)$",
}
MARKDOWN_LABELS: Dict[str, str] = {
    "geometry": r"^- \*\*Volume Cluster Geometry\*\*: `(.+?)` \(",
    "count": r"^\| \*\*Allocated Clusters\*\* \| [\d,]+ \((.+?) each\) \|$",
    "section": r"^## 4\. Cluster Slack Hotspots \((.+?) clusters\)$",
    "micro-files": r"cluster slack waste on (.+?) clusters:$",
    "AppleDouble": r"`\._\*` resource forks, which each take at least one (.+?) cluster\.$",
}
TEXT_COUNT = re.compile(r"^  Allocated Clusters: +([\d,]+) \(", re.MULTILINE)
MARKDOWN_COUNT = re.compile(r"^\| \*\*Allocated Clusters\*\* \| ([\d,]+) \(", re.MULTILINE)

# A cell of the delimiter row of a table in GitHub Flavored Markdown: hyphens, with a colon at either end or both
DELIMITER_CELL = re.compile(r"^\s*:?-+:?\s*$")


def volume(path: Any, block_size: int, fs_type: str = "ntfs") -> FilesystemInfo:
    return FilesystemInfo(str(path), fs_type, block_size, str(path), "win32" if fs_type != "unknown" else "unknown")


def clusters(cluster: int) -> int:
    """How many clusters of this size the files take: each file takes as many as hold it (none when it is empty)."""
    return sum(-(-size // cluster) for _, size in FILES)


def labels(report: str, patterns: Dict[str, str]) -> Dict[str, List[str]]:
    """What each label of the report says (a list per label: every place it stands)."""
    return {name: re.findall(pattern, report, re.MULTILINE) for name, pattern in patterns.items()}


def table_cells(row: str) -> List[str]:
    """The cells of a markdown table row; the outer pipes are optional in GFM, so one at each end is dropped."""
    cells = row.strip()
    if cells.startswith("|"):
        cells = cells[1:]
    if cells.endswith("|"):
        cells = cells[:-1]
    return cells.split("|")  # (nothing in this report escapes a pipe)


def markdown_tables(report: str) -> List[Tuple[str, str, List[str]]]:
    """Each table of a markdown report as (header row, delimiter row, body rows).

    A delimiter row is found by its characters (it starts with `|`, has a `-` and nothing but `|`, `:`, `-` and spaces),
    not by GFM's rule for one, so that a malformed one is still found, and checked. The header is the line just above
    it and the body is the run of lines right below it that start with `|`.
    """
    lines = report.splitlines()
    tables = []
    for number, line in enumerate(lines):
        if number > 0 and line.startswith("|") and "-" in line and set(line) <= set("|:- "):
            body = []
            for row in lines[number + 1:]:
                if not row.startswith("|"):
                    break
                body.append(row)
            tables.append((lines[number - 1], line, body))
    return tables


class _Drive(unittest.TestCase):
    """A drive holding the files above, and `audit` run on it as a volume that `detected` describes."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="sd_audit_labels_")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "drive"
        for rel, size in FILES:
            path = self.root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x" * size)

    def audit(self, detected: FilesystemInfo, *flags: str) -> str:
        out = io.StringIO()
        with patch.object(CLI_AUDIT, "detect_filesystem", return_value=detected), \
                patch.object(AUDITOR, "detect_filesystem", return_value=detected):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["audit", "--root", str(self.root), *flags]), 0)
        return out.getvalue()

    def check_reports(self, detected: FilesystemInfo, cluster: int, label: str, source: str) -> Tuple[str, str]:
        """Runs the text and the markdown report and checks every label says ``label`` for a ``cluster`` volume.

        Returns the two reports for the checks that look at more than the labels.
        """
        data = json.loads(self.audit(detected, "--json"))
        self.assertEqual((data["cluster_size"], data["cluster_size_source"]), (cluster, source))
        total = data["summary"]["total_clusters"]
        self.assertEqual(total, clusters(cluster))  # (so the figures the labels sit beside are at that cluster)
        self.assertEqual(data["summary"]["total_allocated_bytes"], total * cluster)

        text, markdown = self.audit(detected), self.audit(detected, "--markdown")
        self.assertEqual(labels(text, TEXT_LABELS), {name: [label] for name in TEXT_LABELS})
        self.assertEqual(labels(markdown, MARKDOWN_LABELS), {name: [label] for name in MARKDOWN_LABELS})
        self.assertIn(f"Cluster Size: {label} ({cluster} B, {source})\n", text)
        self.assertIn(f"- **Volume Cluster Geometry**: `{label}` ({cluster:,} bytes per allocation unit)\n", markdown)
        # The count printed beside the label is the count of clusters of that size, in both reports
        for report, pattern in ((text, TEXT_COUNT), (markdown, MARKDOWN_COUNT)):
            (printed,) = pattern.findall(report)
            self.assertEqual(int(printed.replace(",", "")), total)
        return text, markdown


class TestEveryLabelNamesTheCluster(_Drive):
    def test_a_detected_4_kb_cluster_is_named_in_every_label_and_512_kb_nowhere(self) -> None:
        text, markdown = self.check_reports(volume(self.root, 4096), 4096, "4 KB", "detected")
        for name, report in (("text", text), ("markdown", markdown)):
            for fixed in ("512KB", "512 KB"):
                with self.subTest(report=name, fixed=fixed):
                    self.assertNotIn(fixed, report)
        self.assertIn("  Allocated Clusters:      45 (4 KB each)\n", text)
        self.assertIn("| **Allocated Clusters** | 45 (4 KB each) |\n", markdown)
        self.assertIn("## 4. Cluster Slack Hotspots (4 KB clusters)\n", markdown)
        self.assertIn("waste on 4 KB clusters:\n", markdown)
        self.assertIn("resource forks, which each take at least one 4 KB cluster.\n", markdown)

    def test_a_cluster_that_is_not_a_whole_number_of_kb_is_named_in_bytes(self) -> None:
        """Dividing by 1024 made a 1536 B cluster "1 KB" and one of 512 B "0 KB"."""
        for cluster in (1536, 512):
            with self.subTest(cluster=cluster):
                text, markdown = self.check_reports(volume(self.root, cluster), cluster, f"{cluster} B", "detected")
                self.assertIn(f"Cluster Size: {cluster} B ({cluster} B, detected)\n", text)
                self.assertIn(f"({cluster} B each)\n", text)
                self.assertNotIn("512 KB", text + markdown)

    def test_a_cluster_that_cannot_be_read_is_named_as_the_fallback_that_was_used(self) -> None:
        with patch.object(FSINFO, "CLUSTER_SIZE_BYTES", FALLBACK):
            for fs_type in ("unknown", "ntfs"):
                with self.subTest(fs_type=fs_type):
                    text, markdown = self.check_reports(volume(self.root, 0, fs_type), FALLBACK, "64 KB", "assumed")
                    for name, report in (("text", text), ("markdown", markdown)):
                        for fixed in ("512KB", "512 KB"):
                            with self.subTest(report=name, fixed=fixed):
                                self.assertNotIn(fixed, report)

    def test_the_real_fallback_is_still_named_512_kb(self) -> None:
        text, markdown = self.check_reports(volume(self.root, 0, "unknown"), 524_288, "512 KB", "assumed")
        self.assertIn("Cluster Size: 512 KB (524288 B, assumed)\n", text)
        self.assertIn("  Allocated Clusters:      27 (512 KB each)\n", text)
        self.assertIn("## 4. Cluster Slack Hotspots (512 KB clusters)\n", markdown)

    def test_the_count_beside_the_label_changes_with_the_cluster(self) -> None:
        counts = {}
        for cluster in (512, 4096, 65_536):
            text = self.audit(volume(self.root, cluster))
            counts[cluster] = int(TEXT_COUNT.findall(text)[0].replace(",", ""))
        self.assertEqual(counts, {512: clusters(512), 4096: 45, 65_536: clusters(65_536)})
        self.assertEqual(len(set(counts.values())), 3)


class TestTheOtherWaysToAskForTheReport(_Drive):
    def test_a_report_that_was_not_told_where_the_size_came_from_names_none(self) -> None:
        """Only `audit` knows the source: a report made by the auditor alone keeps the header it had."""
        with patch.object(AUDITOR, "detect_filesystem", return_value=volume(self.root, 4096)):
            report = StorageAuditor(str(self.root), cluster_size=4096).run_audit()
        self.assertNotIn("cluster_size_source", report)
        self.assertIn("Cluster Size: 4 KB (4096 B)\n", report.to_ascii_table())
        self.assertIn("`4 KB` (4,096 bytes per allocation unit)", report.to_markdown())

    def test_a_report_made_without_a_cluster_size_falls_back_to_the_exfat_cluster(self) -> None:
        text = StorageAuditor(str(self.root)).generate_ascii_table_report({"summary": {"total_clusters": 7}})
        self.assertIn("Cluster Size: 512 KB (524288 B)\n", text)
        self.assertIn("  Allocated Clusters:      7 (512 KB each)\n", text)
        markdown = StorageAuditor(str(self.root)).generate_markdown_report({"summary": {"total_clusters": 7}})
        self.assertIn("`512 KB` (524,288 bytes per allocation unit)", markdown)
        self.assertIn("| **Allocated Clusters** | 7 (512 KB each) |", markdown)

    def test_the_exported_files_carry_the_same_labels(self) -> None:
        detected = volume(self.root, 4096)
        text_file, markdown_file = self.base / "out" / "audit.txt", self.base / "out" / "audit.md"
        self.audit(detected, "--export", str(text_file))
        self.audit(detected, "--markdown", "--export", str(markdown_file))
        in_text = labels(text_file.read_text(encoding="utf-8"), TEXT_LABELS)
        in_markdown = labels(markdown_file.read_text(encoding="utf-8"), MARKDOWN_LABELS)
        self.assertEqual(in_text, {name: ["4 KB"] for name in TEXT_LABELS})
        self.assertEqual(in_markdown, {name: ["4 KB"] for name in MARKDOWN_LABELS})

    def test_the_html_report_has_no_fixed_512_label(self) -> None:
        """It already took the cluster from the figures; this keeps a fixed label from being added to it."""
        page = self.base / "out" / "audit.html"
        self.audit(volume(self.root, 4096), "--html", str(page))
        html = page.read_text(encoding="utf-8")
        self.assertIn("Cluster: 4 KB (4,096 B)", html)
        for fixed in ("512KB", "512 KB"):
            with self.subTest(fixed=fixed):
                self.assertNotIn(fixed, html)


class TestMarkdownTables(_Drive):
    """Every table of the markdown report has to be one that GitHub Flavored Markdown draws.

    The delimiter row of the micro-file table ended in `||`: nine cells under an eight-column header, so renderers that
    follow GFM showed the rows of the table as plain text.
    """

    def test_every_table_has_as_many_delimiter_cells_as_header_cells(self) -> None:
        markdown = self.audit(volume(self.root, 4096), "--markdown")
        self.assertIn("## 5. Micro-File Hotspots", markdown)  # (so the table that had the bug is in the report)
        tables = markdown_tables(markdown)
        # A table that the finder misses is a table that is not checked: the report has these five, in this order
        names = [table_cells(header)[0].strip() for header, _, _ in tables]
        self.assertEqual(names, ["Metric", "Taxonomy", "Category", "Directory (Subtree)", "Directory"])
        for name, (header, delimiter, rows) in zip(names, tables):
            with self.subTest(table=name):
                columns = len(table_cells(header))
                self.assertEqual(len(table_cells(delimiter)), columns, delimiter)
                for cell in table_cells(delimiter):
                    self.assertRegex(cell, DELIMITER_CELL)
                for row in rows:
                    self.assertEqual(len(table_cells(row)), columns, row)
        # The micro-file table is the last one; with no row under it the loop above would have had nothing to check
        self.assertTrue(tables[-1][2], "the micro-file table has no row")

    def test_the_check_catches_the_row_the_micro_file_table_had(self) -> None:
        """The helpers see what was wrong with the old row: nine cells under eight, and the last one no delimiter."""
        header = (
            "| Directory | Micro-Files | Direct Files | Logical Size | Allocated | Slack Wasted | Waste % | Advice |"
        )
        old = "|:---|---:|---:|---:|---:|---:|---:|:---||"
        fixed = "|:---|---:|---:|---:|---:|---:|---:|:---|"
        self.assertEqual(markdown_tables(header + "\n" + old + "\n"), [(header, old, [])])
        self.assertEqual(len(table_cells(header)), 8)
        self.assertEqual(len(table_cells(old)), 9)
        self.assertNotRegex(table_cells(old)[-1], DELIMITER_CELL)
        self.assertEqual(len(table_cells(fixed)), 8)
        for cell in table_cells(fixed):
            self.assertRegex(cell, DELIMITER_CELL)


if __name__ == "__main__":
    unittest.main()
