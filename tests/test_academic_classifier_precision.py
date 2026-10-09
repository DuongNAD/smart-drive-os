"""tests/test_academic_classifier_precision.py - Ordinary folders must not be taken for coursework.

Regression for an over-eager AcademicClassifier: `k\\s*\\d+` matched any "k" followed by digits,
the course-code regex matched "Win10", "mp3" and "CS6", the "lab" keyword matched "Colab", and the
mojibake rewrite of "k 1" turned "Book 1" into "BooKy_1". `organize` would then move and rename
such folders into the FPTU tree.
"""

from __future__ import annotations

import os
import unittest

from smart_drive.core.academic_classifier import AcademicClassifier, sanitize_folder_name

NOT_ACADEMIC = [
    # the false positives found by running `organize --dry-run` on ordinary folder names
    "Book 1", "Network 2", "Task 3 notes", "Colab Notebooks", "Elaboration",
    "Win10 ISO", "mp3", "Photoshop CS6", "backup2024",
    # more look-alikes
    "Family Photos", "Music Collection", "Stack3", "Hack2", "Pack 4", "Labels", "Collaboration",
    "iOS17", "x86", "h264", "utf8", "ps5", "Ubuntu2204", "IMG123", "DSC001", "GTX970", "SSD512",
    "Project Alpha", "Taxes 2024", "Receipts", "Downloads",
]

# (folder name, expected sub-path fragments)
ACADEMIC = [
    ("DBI202", ("FPTU", "DBI202")),
    ("DBI202_VuPT", ("FPTU", "DBI202")),
    ("wed201c", ("FPTU", "WED201C")),
    ("SWP391_Project", ("FPTU", "SWP391")),
    ("PRF192 - Lab 3", ("FPTU", "PRF192")),
    ("LAB211", ("FPTU", "LAB211")),
    ("PE_WED201c_SP26", ("FPTU", "WED201C")),
    ("FA25_PRN211", ("FPTU", "PRN211")),
    ("hoc ky 3", ("Ky_3", "Hoc_Ky_3")),
    ("h?c k? 3 fptu", ("Ky_3",)),
    ("HK2", ("Ky_2",)),
    ("k 4", ("Ky_4",)),
    ("FPTU_VuPTHE204339_HK4", ("Ky_4",)),
    ("FPTU_SP26", ("FPTU", "SP26")),
    ("SU25 notes", ("FPTU", "SU25")),
    ("FA27", ("FPTU", "FA27")),  # not limited to the years that used to be hard-coded
    ("lab1", ("Java_Labs",)),
    ("testjava", ("Java_Labs",)),
    ("Lab Reports", ("FPTU",)),
]


class TestNotMistakenForCoursework(unittest.TestCase):
    def test_ordinary_folder_names_are_left_alone(self) -> None:
        for name in NOT_ACADEMIC:
            with self.subTest(name=name):
                self.assertIsNone(AcademicClassifier.classify_folder(name))


class TestRealCourseworkIsStillRecognised(unittest.TestCase):
    def test_course_codes_semesters_and_labs(self) -> None:
        for name, fragments in ACADEMIC:
            with self.subTest(name=name):
                result = AcademicClassifier.classify_folder(name)
                self.assertIsNotNone(result)
                taxonomy, sub_path, _reason = result
                self.assertEqual(taxonomy, "02_Learning_Knowledge")
                parts = sub_path.split(os.sep)
                for fragment in fragments:
                    self.assertTrue(
                        any(fragment in part for part in parts), f"{fragment!r} not in {sub_path!r}"
                    )

    def test_vietnamese_with_diacritics_is_recognised(self) -> None:
        """'Học kỳ 3' used to be missed: only the ASCII or font-damaged spellings matched."""
        for name, semester in (("Học kỳ 3", "Ky_3"), ("HỌC KỲ 4", "Ky_4"), ("Ôn luyện PRF192", "PRF192")):
            with self.subTest(name=name):
                result = AcademicClassifier.classify_folder(name)
                self.assertIsNotNone(result)
                self.assertIn(semester, result[1])

    def test_a_folder_name_is_never_rewritten_into_a_different_word(self) -> None:
        result = AcademicClassifier.classify_folder("Task 3 CSD201")
        self.assertIsNotNone(result)
        self.assertEqual(os.path.basename(result[1]), "Task_3_CSD201")  # not "TasKy_3_CSD201"
        self.assertIn("CSD201", result[1])


class TestSanitizeFolderName(unittest.TestCase):
    def test_mojibake_is_repaired_only_as_whole_words(self) -> None:
        self.assertEqual(sanitize_folder_name("Book 1"), "Book_1")
        self.assertEqual(sanitize_folder_name("Network 2"), "Network_2")
        self.assertEqual(sanitize_folder_name("Task 3"), "Task_3")
        self.assertEqual(sanitize_folder_name("Backup de thi"), "Backup_De_Thi")

    def test_known_repairs_still_work(self) -> None:
        self.assertEqual(sanitize_folder_name("h?c k? 3 fptu"), "Hoc_Ky_3_fptu")
        self.assertEqual(sanitize_folder_name("k 1 fptu"), "Ky_1_fptu")
        self.assertEqual(sanitize_folder_name("n luy?n pe dbi202"), "On_Luyen_pe_dbi202")
        self.assertEqual(sanitize_folder_name("bai tap 5"), "Bai_Tap_5")


if __name__ == "__main__":
    unittest.main()
