"""smart_drive.core.academic_classifier - University Coursework & Academic Folder Classifier.

Specialized classifier for academic courseworks, university curriculum folders (specifically
Vietnamese universities such as FPT University / FPTU), course code regex extraction,
and UTF-8 / Mojibake font corruption sanitization.

Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional, Tuple


# Loose token pattern for course-like codes (e.g. DBI202, WED201c, SWE202c, PRN211, PRJ301, LAB1).
# Kept for compatibility; the classification itself uses the stricter pattern below, because this
# one also matches "Win10", "mp3", "CS6", "x86" or "iOS17".
COURSE_CODE_PATTERN = re.compile(
    r"(?:^|[^A-Za-z0-9])([A-Za-z]{2,4}\d{1,4}[A-Za-z]?)(?:$|[^A-Za-z0-9])",
    re.IGNORECASE,
)

# FPTU subject codes: three letters, three digits and an optional trailing letter (DBI202, WED201c).
COURSE_CODE_STRICT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{3}\d{3}[A-Za-z]?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# File-type, camera and hardware prefixes that fit the course-code shape but are not subjects
# (IMG123, DSC001, GTX970, SSD512, MP4001 ...).
NON_ACADEMIC_CODE_PREFIXES = frozenset({
    "IMG", "DSC", "VID", "MOV", "PIC", "CAM", "SCR", "TMP", "BAK", "LOG",
    "SSD", "HDD", "USB", "GTX", "RTX", "GPU", "CPU", "RAM", "SDK", "API", "AMD", "ISO", "DVD",
    "PDF", "DOC", "XLS", "PPT", "TXT", "ZIP", "RAR", "EXE", "DLL", "JPG", "PNG", "GIF",
    "MP3", "MP4", "AVI", "MKV", "WAV",
})

# Vietnamese semesters, matched on lower-case text without diacritics: "hoc ky 3", the font-damaged
# "h?c k? 3", "hk4", "ky 1" and "k 2". Every alternative must start a token, so "Task 3", "Book 1"
# and "backup2024" are not semesters.
SEMESTER_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:h[o?]c\s*k[y?]|hk|ky|k)\s*[_.\-]?\s*(\d{1,2})(?![0-9])"
)

# FPTU season codes: SP26 (spring), SU25 (summer), FA25 (fall); any year.
SEASON_PATTERN = re.compile(r"(?<![a-z0-9])(?:sp|su|fa)\s*[_\-]?\s*\d{2}(?![0-9])")

# "lab", "labs", "lab1", "lab_3" as a word of its own, but not Colab, Elaboration or Label.
LAB_PATTERN = re.compile(r"(?<![a-z0-9])labs?\d*(?![a-z])")
LAB1_PATTERN = re.compile(r"(?<![a-z0-9])lab\s*[_\-]?1(?![0-9])")

# Practical-exam prefix ("PE_PRN211_SP26"), as the start of a token only.
PE_PATTERN = re.compile(r"(?<![a-z0-9])pe_")

# Vietnamese / mojibake replacement dictionary for broken filenames
MOJIBAKE_MAP = {
    "h?c k?": "Hoc_Ky",
    "hoc ky": "Hoc_Ky",
    "k 1": "Ky_1",
    "k 2": "Ky_2",
    "k 3": "Ky_3",
    "k 4": "Ky_4",
    "n luy?n": "On_Luyen",
    "on luyen": "On_Luyen",
    "dich truyen": "Dich_Truyen",
    "bai tap": "Bai_Tap",
    "de thi": "De_Thi",
}

# Same keys, compiled once with word boundaries ("k 1" must not rewrite "Book 1" into "BooKy_1").
_MOJIBAKE_PATTERNS = [
    (re.compile(r"(?<![A-Za-z0-9])" + re.escape(bad) + r"(?![A-Za-z0-9])", re.IGNORECASE), good)
    for bad, good in MOJIBAKE_MAP.items()
]


def _fold(text: str) -> str:
    """Lower-cases and strips Vietnamese diacritics, so 'Học kỳ 3' and 'hoc ky 3' compare equal."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "d").lower()


def sanitize_folder_name(name: str) -> str:
    """Sanitizes broken font, mojibake question marks, and messy whitespace in folder names."""
    cleaned = name.strip()

    # Replace known mojibake patterns (whole words only)
    for pattern, good in _MOJIBAKE_PATTERNS:
        cleaned = pattern.sub(good, cleaned)

    # Replace lone question marks that were font encoding failures
    cleaned = re.sub(r"\?+", "_", cleaned)
    # Collapse multiple underscores/spaces
    cleaned = re.sub(r"[\s_]+", "_", cleaned).strip("_")

    return cleaned or name


def _find_course_code(raw_name: str) -> Optional[str]:
    """First FPTU-style subject code in the name (upper-cased), ignoring file-type look-alikes."""
    for match in COURSE_CODE_STRICT_PATTERN.finditer(raw_name):
        code = match.group(1).upper()
        if code[:3] not in NON_ACADEMIC_CODE_PREFIXES:
            return code
    return None


class AcademicClassifier:
    """Intelligent classifier for academic courseworks and localization."""

    @staticmethod
    def classify_folder(folder_name: str) -> Optional[Tuple[str, str, str]]:
        """Classifies a directory into (target_taxonomy, target_subpath, reason).

        Returns None if the folder is not an academic coursework or recognized student folder.
        """
        raw_name = folder_name.strip()
        lower_name = raw_name.lower()
        folded = _fold(raw_name)

        # 1. Special Known Workstation Folders
        if lower_name == "dich truyen":
            return (
                "02_Learning_Knowledge",
                os.path.join("02_Learning_Knowledge", "Personal_Books", "dich truyen"),
                "Detected personal reading/book collection",
            )
        if lower_name == "lenovo":
            return (
                "05_Dev_Toolbox",
                os.path.join("05_Dev_Toolbox", "OEM_Drivers", "LENOVO"),
                "Detected OEM system driver folder",
            )
        if lower_name == "sql2022":
            return (
                "05_Dev_Toolbox",
                os.path.join("05_Dev_Toolbox", "Installers", "SQL2022"),
                "Detected developer tool installer package",
            )

        # 2. Check for FPTU or University Academic Keywords
        is_fptu = "fptu" in folded
        course_code = _find_course_code(raw_name)
        semester_match = SEMESTER_PATTERN.search(folded)
        season_match = SEASON_PATTERN.search(folded)

        has_academic_keyword = (
            season_match is not None
            or LAB_PATTERN.search(folded) is not None
            or PE_PATTERN.search(folded) is not None
            or any(
                kw in folded
                for kw in (
                    "project_osg",
                    "testjava",
                    "on luyen",
                    "n luy?n",
                    "h?c k?",
                    "hoc ky",
                )
            )
            or folded in ("java", "wed201c")
        )

        if not (is_fptu or course_code or semester_match or has_academic_keyword):
            return None

        clean_name = sanitize_folder_name(raw_name)

        # 3. Semester-Specific Mapping (Ky_1, Ky_3, Ky_4, etc.)
        if semester_match:
            sem_num = semester_match.group(1)
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", f"Ky_{sem_num}", clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                f"Detected FPTU Semester {sem_num} coursework",
            )

        # 4. Java / Programming Lab Mapping
        if folded in ("java", "testjava") or LAB1_PATTERN.search(folded):
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", "Java_Labs", clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                "Detected programming practical lab coursework",
            )

        # 5. Course Code Specific Mapping (e.g. DBI202, WED201c, OSG)
        if course_code:
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", course_code, clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                f"Detected academic course code {course_code}",
            )

        # 6. Season code (SP26, FA25, SU25) without a subject code
        if season_match:
            season = re.sub(r"[\s_\-]", "", season_match.group(0)).upper()
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", season, clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                f"Detected academic season {season}",
            )

        if "osg" in folded:
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", "OSG", clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                "Detected Operating Systems (OSG) coursework",
            )

        # 7. General FPTU Coursework
        target_sub = os.path.join("02_Learning_Knowledge", "FPTU", clean_name)
        return (
            "02_Learning_Knowledge",
            target_sub,
            "Detected general university coursework folder",
        )


__all__ = ["AcademicClassifier", "sanitize_folder_name", "COURSE_CODE_PATTERN", "COURSE_CODE_STRICT_PATTERN"]
