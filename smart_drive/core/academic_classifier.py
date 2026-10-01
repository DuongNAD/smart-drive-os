"""smart_drive.core.academic_classifier - University Coursework & Academic Folder Classifier.

Specialized classifier for academic courseworks, university curriculum folders (specifically
Vietnamese universities such as FPT University / FPTU), course code regex extraction,
and UTF-8 / Mojibake font corruption sanitization.

Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple


# Regex pattern to match university course codes (e.g. DBI202, WED201c, SWE202c, PRN211, PRJ301)
COURSE_CODE_PATTERN = re.compile(
    r"(?:^|[^A-Za-z0-9])([A-Za-z]{2,4}\d{1,4}[A-Za-z]?)(?:$|[^A-Za-z0-9])",
    re.IGNORECASE,
)

# Pattern for Vietnamese semesters (e.g. "h?c k? 3", "hoc ky 4", "k 1", "hk4", "sp26")
SEMESTER_PATTERN = re.compile(
    r"(?:h[o\?a-z]*\s*k[y\?a-z]*|k|hk)\s*(\d+)", re.IGNORECASE
)

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


def sanitize_folder_name(name: str) -> str:
    """Sanitizes broken font, mojibake question marks, and messy whitespace in folder names."""
    cleaned = name.strip()

    # Replace known mojibake patterns
    for bad, good in MOJIBAKE_MAP.items():
        pattern = re.compile(re.escape(bad), re.IGNORECASE)
        cleaned = pattern.sub(good, cleaned)

    # Replace lone question marks that were font encoding failures
    cleaned = re.sub(r"\?+", "_", cleaned)
    # Collapse multiple underscores/spaces
    cleaned = re.sub(r"[\s_]+", "_", cleaned).strip("_")

    return cleaned or name


class AcademicClassifier:
    """Intelligent classifier for academic courseworks and localization."""

    @staticmethod
    def classify_folder(folder_name: str) -> Optional[Tuple[str, str, str]]:
        """Classifies a directory into (target_taxonomy, target_subpath, reason).

        Returns None if the folder is not an academic coursework or recognized student folder.
        """
        raw_name = folder_name.strip()
        lower_name = raw_name.lower()

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
        is_fptu = "fptu" in lower_name
        course_match = COURSE_CODE_PATTERN.search(raw_name)
        semester_match = SEMESTER_PATTERN.search(lower_name)

        has_academic_keyword = any(
            kw in lower_name
            for kw in (
                "pe_",
                "lab",
                "sp26",
                "fa25",
                "su25",
                "project_osg",
                "testjava",
                "on luyen",
                "n luy?n",
                "h?c k?",
                "hoc ky",
            )
        ) or lower_name in ("java", "wed201c")

        if not (is_fptu or course_match or semester_match or has_academic_keyword):
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
        if lower_name in ("java", "testjava") or "lab1" in lower_name:
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", "Java_Labs", clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                "Detected programming practical lab coursework",
            )

        # 5. Course Code Specific Mapping (e.g. DBI202, WED201c, OSG)
        if course_match:
            code = course_match.group(1).upper()
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", code, clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                f"Detected academic course code {code}",
            )

        if "osg" in lower_name:
            target_sub = os.path.join(
                "02_Learning_Knowledge", "FPTU", "OSG", clean_name
            )
            return (
                "02_Learning_Knowledge",
                target_sub,
                "Detected Operating Systems (OSG) coursework",
            )

        # 6. General FPTU Coursework
        target_sub = os.path.join("02_Learning_Knowledge", "FPTU", clean_name)
        return (
            "02_Learning_Knowledge",
            target_sub,
            "Detected general university coursework folder",
        )


__all__ = ["AcademicClassifier", "sanitize_folder_name", "COURSE_CODE_PATTERN"]
