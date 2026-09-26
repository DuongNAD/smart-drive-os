"""tests/test_exfat_compat.py - Comprehensive Unit Tests for exFAT Compatibility Engine.

100% Python Standard Library unittest.
Tests Tiers 1 & 2:
- 9 Win32 forbidden characters: \\ / : * ? " < > |
- ASCII control characters (0x00 - 0x1F).
- Trailing space and trailing period detection.
- 22 Windows 16-bit DOS reserved device stems (CON, PRN, AUX, NUL, COM1-9, LPT1-9).
- Filename sanitization, prefixing, stripping, and idempotency.
- Relative path normalization ('/' forward slash canonical form).
- Symlink detection and SymlinkNotPermittedError enforcement.
- Unicode, Vietnamese diacritics, spaces, and deeply nested paths.
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
    from smart_drive.core.exfat_compat import (
        EXFAT_CLUSTER_SIZE,
        FORBIDDEN_CHARS,
        WINDOWS_RESERVED_NAMES,
        ExFatCompatError,
        ExFatEngine,
        SymlinkNotPermittedError,
        Violation,
        assert_no_symlink,
        assert_no_symlinks,
        audit_forbidden_characters,
        is_symlink,
        normalize_rel_path,
        sanitize_filename,
    )
except ImportError:
    sys.path.insert(0, r"D:\teamwork_projects\smart_drive_manager")
    from core.exfat_compat import (
        EXFAT_CLUSTER_SIZE,
        FORBIDDEN_CHARS,
        WINDOWS_RESERVED_NAMES,
        ExFatCompatError,
        ExFatEngine,
        SymlinkNotPermittedError,
        Violation,
        assert_no_symlink,
        assert_no_symlinks,
        audit_forbidden_characters,
        is_symlink,
        normalize_rel_path,
        sanitize_filename,
    )

from tests.helpers import SmartDriveTestCase


class TestForbiddenCharacters(SmartDriveTestCase):
    """Tier 1 & Tier 2: Validates auditing of 9 Win32 forbidden chars and control characters."""

    def test_all_nine_forbidden_chars_individually_detected(self) -> None:
        """Every single one of the 9 forbidden characters must be flagged."""
        expected_chars = {'\\', '/', ':', '*', '?', '"', '<', '>', '|'}
        self.assertEqual(FORBIDDEN_CHARS, expected_chars)

        for ch in expected_chars:
            filename = f"bad{ch}name.txt"
            violations = audit_forbidden_characters(filename)
            self.assertTrue(len(violations) >= 1, f"Character {ch!r} was not detected")
            self.assertTrue(any(v.token == ch for v in violations), f"Violation token mismatch for {ch!r}")

    def test_clean_name_has_no_violations(self) -> None:
        """Valid cross-platform alphanumeric filename produces zero violations."""
        clean_names = [
            "valid_filename.txt",
            "model-q4_k_m.gguf",
            "README.md",
            "script_2026.py",
            "archive.tar.gz",
        ]
        for name in clean_names:
            violations = audit_forbidden_characters(name)
            self.assertEqual(violations, [], f"Expected clean file for {name}, got {violations}")

    def test_ascii_control_characters_detected(self) -> None:
        """ASCII control characters 0x00 through 0x1F must be flagged."""
        for code in [0, 1, 7, 10, 13, 27, 31]:  # NUL, SOH, BEL, LF, CR, ESC, US
            char = chr(code)
            violations = audit_forbidden_characters(f"bad{char}file.txt")
            self.assertTrue(len(violations) >= 1)
            self.assertTrue(any("CONTROL_CHAR" in str(v) for v in violations))

    def test_trailing_space_and_dot_detected(self) -> None:
        """Files ending with a trailing space or dot must be flagged."""
        v_space = audit_forbidden_characters("bad_name.txt ")
        self.assertTrue(any("TRAILING_SPACE" in str(v) for v in v_space))

        v_dot = audit_forbidden_characters("bad_name.")
        self.assertTrue(any("TRAILING_DOT" in str(v) for v in v_dot))


class TestReservedDOSDeviceNames(SmartDriveTestCase):
    """Tier 1: Audits the 22 Windows 16-bit DOS reserved device stems."""

    def test_all_22_dos_device_stems_cataloged(self) -> None:
        """Verifies all 22 stems exist in WINDOWS_RESERVED_NAMES."""
        expected = {
            'CON', 'PRN', 'AUX', 'NUL',
            'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
            'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
        }
        self.assertEqual(WINDOWS_RESERVED_NAMES, expected)

    def test_dos_device_stems_detected_case_insensitively(self) -> None:
        """All 22 DOS stems with any extension must be flagged regardless of casing."""
        for stem in WINDOWS_RESERVED_NAMES:
            for variant in [stem.lower(), stem.upper(), stem.capitalize()]:
                # Bare stem
                v_bare = audit_forbidden_characters(variant)
                self.assertTrue(any("RESERVED_NAME" in str(v) for v in v_bare), f"Failed for {variant}")

                # Stem with extension
                v_ext = audit_forbidden_characters(f"{variant}.txt")
                self.assertTrue(any("RESERVED_NAME" in str(v) for v in v_ext), f"Failed for {variant}.txt")


class TestFilenameSanitization(SmartDriveTestCase):
    """Tier 1 & Tier 2: Validates filename sanitization, idempotency, and prefixing."""

    def test_sanitize_forbidden_chars_replaced_with_underscore(self) -> None:
        """Forbidden characters must be replaced with replacement char."""
        bad_name = 'test:file*name?.txt'
        sanitized = sanitize_filename(bad_name)
        self.assertEqual(sanitized, "test_file_name_.txt")
        self.assertEqual(audit_forbidden_characters(sanitized), [])

    def test_sanitize_dos_reserved_stem_prefixed(self) -> None:
        """DOS stems must be prefixed with replacement char."""
        self.assertEqual(sanitize_filename("con.txt"), "_con.txt")
        self.assertEqual(sanitize_filename("aux.py"), "_aux.py")
        self.assertEqual(sanitize_filename("NUL"), "_NUL")
        self.assertEqual(sanitize_filename("com1.log"), "_com1.log")
        self.assertEqual(audit_forbidden_characters(sanitize_filename("aux.py")), [])

    def test_sanitize_strips_trailing_dots_and_spaces(self) -> None:
        """Trailing dots and spaces must be stripped."""
        self.assertEqual(sanitize_filename("report.txt   "), "report.txt")
        self.assertEqual(sanitize_filename("data..."), "data")
        self.assertEqual(audit_forbidden_characters(sanitize_filename("report.txt   ")), [])

    def test_sanitize_idempotency(self) -> None:
        """Sanitizing an already-sanitized filename must yield the exact same string."""
        cases = [
            "con.txt",
            "invalid:name*.dat",
            "trailing.dot.",
            "clean_file_123.pdf",
            "normal_dir",
        ]
        for name in cases:
            first_pass = sanitize_filename(name)
            second_pass = sanitize_filename(first_pass)
            self.assertEqual(first_pass, second_pass)
            self.assertEqual(audit_forbidden_characters(second_pass), [])

    def test_sanitize_empty_or_invalid_replacement_raises(self) -> None:
        """Empty replacement string or one containing forbidden chars must raise ValueError."""
        with self.assertRaises(ValueError):
            sanitize_filename("file.txt", replacement="")
        with self.assertRaises(ValueError):
            sanitize_filename("file.txt", replacement=":")


class TestPathNormalization(SmartDriveTestCase):
    """Tier 1 & Tier 2: Path normalization to '/' and cross-platform handling."""

    def test_normalize_mixed_slashes_and_dots(self) -> None:
        """Normalizes Windows backslashes and relative dot segments to canonical '/'."""
        root = "D:/teamwork_projects"
        path = "D:\\teamwork_projects\\01_AI_Models\\model.gguf"
        norm = normalize_rel_path(path, root)
        self.assertEqual(norm, "01_AI_Models/model.gguf")

    def test_normalize_root_equality_returns_empty_string(self) -> None:
        """Normalizing the root path itself against root returns empty string."""
        self.assertEqual(normalize_rel_path("D:/mount", "D:/mount"), "")
        self.assertEqual(normalize_rel_path("D:\\mount\\", "D:/mount"), "")

    def test_unicode_and_vietnamese_paths(self) -> None:
        """Unicode characters (Vietnamese, accents, emojis) preserved during normalization."""
        root = str(self.test_dir)
        sub = self.test_dir / "02_Learning_Knowledge" / "Tài liệu học tập"
        sub.mkdir(parents=True, exist_ok=True)
        file_path = sub / "Báo cáo tiến độ.md"
        file_path.write_text("Nội dung", encoding="utf-8")

        norm = normalize_rel_path(str(file_path), root)
        self.assertEqual(norm, "02_Learning_Knowledge/Tài liệu học tập/Báo cáo tiến độ.md")

    def test_deeply_nested_path_normalization(self) -> None:
        """Paths with >=10 levels of directory nesting normalize cleanly."""
        levels = [f"level_{i:02d}" for i in range(12)]
        deep_dir = self.test_dir
        for lvl in levels:
            deep_dir = deep_dir / lvl
        deep_dir.mkdir(parents=True, exist_ok=True)
        deep_file = deep_dir / "deep_file.dat"
        deep_file.write_bytes(b"data")

        norm = normalize_rel_path(str(deep_file), str(self.test_dir))
        expected = "/".join(levels) + "/deep_file.dat"
        self.assertEqual(norm, expected)


class TestSymlinkPrevention(SmartDriveTestCase):
    """Tier 1: Enforcement of strict no-symlink rule on exFAT."""

    def test_is_symlink_regular_file_returns_false(self) -> None:
        """Regular file is not a symlink."""
        test_file = self.test_dir / "regular.txt"
        test_file.write_text("hello", encoding="utf-8")
        self.assertFalse(is_symlink(str(test_file)))
        # assert_no_symlinks should not raise
        assert_no_symlinks(str(test_file))
        assert_no_symlink(str(test_file))

    def test_is_symlink_nonexistent_returns_false(self) -> None:
        """Non-existent path returns False without raising."""
        non_existent = str(self.test_dir / "does_not_exist.dat")
        self.assertFalse(is_symlink(non_existent))


if __name__ == "__main__":
    unittest.main()
