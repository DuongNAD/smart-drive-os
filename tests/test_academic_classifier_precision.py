"""tests/test_academic_classifier_precision.py - Ordinary folders must not be taken for coursework.

Regression for an over-eager AcademicClassifier: `k\\s*\\d+` matched any "k" followed by digits,
the course-code regex matched "Win10", "mp3" and "CS6", the "lab" keyword matched "Colab", and the
mojibake rewrite of "k 1" turned "Book 1" into "BooKy_1". `organize` would then move and rename
such folders into the FPTU tree.

A second pass (found by an independent review of `organize`) still moved Kubernetes' "K8s" and "k3s",
"K9 Mail", "Home Lab", "SHA256" and "net472". A single weak hint (a bare "k8", the word "lab", "pe_" or a
code with an unknown prefix) no longer moves a folder: strong signals (the word FPTU, a semester spelled
out, a season code alone, a subject code with an FPTU prefix) are enough on their own; weak ones need two
AND a subject-shaped code. A second review still found "K8 Lab", "lab pe_tools", "SP24 Lookbook", "Su-27",
"HK45", "Grade K 2" and "Pro100" being moved, which is what the extra conditions are for.
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
    # the second pass: one weak hint is not enough
    "K8s", "k3s", "K9 Mail", "K12", "k1", "Home Lab", "homelab", "My Lab", "Lab Reports", "labs",
    "SHA256", "SHA512", "net472", "AES256", "XYZ123", "pe_notes",
    # the third pass: two weak hints without a subject code, a season code that is not alone or not one token,
    # semesters that are not 0-9, a subject prefix with a number no subject has
    "K8 Lab", "k8-lab", "lab-k8", "My Home Lab K3", "lab pe_tools", "k5 lab", "pe_k3",
    "SP24 Lookbook", "SP26 Lookbook", "SU25 notes", "Su-27", "FA-18C Hornet", "fa 12",
    "HK45", "Grade K 2", "K 20", "k 4", "Pro100", "ACC100",
    "KI 2", "Ki 5 inhibitor",  # "ki" is a word as well; only "hoc ki 3" is a semester
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
    ("k 1 fptu", ("Ky_1",)),  # the "k 1" of a font-damaged "kỳ 1" is only trusted next to the word FPTU
    ("Học kì 3", ("Ky_3",)),  # kì as well as kỳ
    ("FPTU_VuPTHE204339_HK4", ("Ky_4",)),
    ("FPTU_SP26", ("FPTU", "SP26")),
    ("FA27", ("FPTU", "FA27")),  # a folder that is only a season code; not limited to the years once hard-coded
    ("XYZ123_SP26", ("FPTU", "XYZ123")),
    ("EXE101", ("FPTU", "EXE101")),  # EXE is also a file type, but it is an FPTU subject prefix too
    ("ACC101", ("FPTU", "ACC101")),
    ("PRU211m", ("FPTU", "PRU211M")),  # real subjects that an earlier list did not know
    ("PFP191", ("FPTU", "PFP191")),
    ("SYB302c", ("FPTU", "SYB302C")),
    ("lab1", ("Java_Labs",)),
    ("testjava", ("Java_Labs",)),
    ("Lab 3", ("FPTU", "Lab_3")),  # a name that starts with "lab" and a number is a lab course
    ("K5 DBI202", ("Ky_5", "K5_DBI202")),
    # two weak hints together are enough
    ("XYZ123 lab", ("FPTU", "XYZ123")),
    ("PE_XYZ123", ("FPTU", "XYZ123")),
    ("k5 XYZ123", ("Ky_5",)),
]


class TestNotMistakenForCoursework(unittest.TestCase):
    def test_ordinary_folder_names_are_left_alone(self) -> None:
        for name in NOT_ACADEMIC:
            with self.subTest(name=name):
                self.assertIsNone(AcademicClassifier.classify_folder(name))


class TestWeakHintsNeedACompanion(unittest.TestCase):
    def test_each_weak_hint_alone_is_not_enough_but_the_pair_is(self) -> None:
        pairs = (
            ("k5", "k5 XYZ123"), ("lab", "XYZ123 lab"), ("pe_notes", "pe_notes XYZ123"),
            ("XYZ123", "XYZ123 lab"), ("sp24 notes", "XYZ123 sp24"),
        )
        for alone, pair in pairs:
            with self.subTest(alone=alone):
                self.assertIsNone(AcademicClassifier.classify_folder(alone))
                self.assertIsNotNone(AcademicClassifier.classify_folder(pair))

    def test_two_weak_hints_without_a_subject_code_are_still_not_enough(self) -> None:
        for name in ("k5 lab", "K8 Lab", "lab pe_tools", "pe_k3", "SP24 lab"):
            with self.subTest(name=name):
                self.assertIsNone(AcademicClassifier.classify_folder(name))

    def test_a_known_subject_prefix_is_strong_but_a_look_alike_prefix_never_counts(self) -> None:
        self.assertIsNotNone(AcademicClassifier.classify_folder("PRN211"))
        self.assertIsNone(AcademicClassifier.classify_folder("SHA256"))
        self.assertIsNone(AcademicClassifier.classify_folder("SHA256 lab"))  # SHA is a known look-alike: no hint at all
        self.assertIsNone(AcademicClassifier.classify_folder("net472_pe_old"))
        self.assertIsNotNone(AcademicClassifier.classify_folder("EXE101"))  # a prefix on the subject list beats the file-type list
        self.assertIsNone(AcademicClassifier.classify_folder("Pro100"))  # PRO is a subject prefix, but there is no PRO100


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
