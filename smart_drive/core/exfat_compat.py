"""smart_drive.core.exfat_compat - exFAT Geometry, Path Normalization & Windows Compatibility Engine.

Provides hardware-accurate 512KB cluster allocation math, relative path normalization
using '/', Windows forbidden character auditing and sanitization, symlink guards,
and dynamic drive root auto-detection for the exFAT external SSDs.
"""

from __future__ import annotations

import os
import sys
import stat
from typing import Optional, List, Set, Tuple

# exFAT Allocation Block Size: 524,288 Bytes (512 KB)
EXFAT_CLUSTER_SIZE: int = 524288

# Characters strictly prohibited in file/directory names on Windows / Win32
FORBIDDEN_CHARS: Set[str] = {'\\', '/', ':', '*', '?', '"', '<', '>', '|'}

# Reserved 16-bit DOS device names on Windows (case-insensitive stem)
WINDOWS_RESERVED_NAMES: Set[str] = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
}


class ExFatCompatError(Exception):
    """Base exception for all exFAT compatibility and filesystem constraint errors."""
    pass


class SymlinkNotPermittedError(ExFatCompatError):
    """Raised when a symlink is encountered or created on the exFAT filesystem."""
    pass


class Violation(str):
    """Specialized string representing a compatibility violation.

    Behaves as the full descriptive violation code string (e.g. 'FORBIDDEN_CHAR::')
    while also matching the raw offending character (e.g. ':') or token for direct
    membership tests in sequences (e.g. `':' in violations_list`).
    """
    def __new__(cls, code: str, token: str = ""):
        obj = super().__new__(cls, code)
        obj._token = token
        return obj

    @property
    def token(self) -> str:
        """The raw offending character or substring token (e.g. ':', 'CON')."""
        return self._token

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Violation):
            return super().__eq__(other) and self._token == other._token
        if isinstance(other, str):
            return super().__eq__(other) or (bool(self._token) and other == self._token)
        return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()


def calculate_allocated_bytes(size: int, cluster_size: int = EXFAT_CLUSTER_SIZE) -> int:
    """Calculate actual physical bytes allocated on an exFAT filesystem for a given logical size.

    Rules:
    - size < 0: raises ValueError
    - size == 0: 0 bytes (empty files occupy 0 cluster blocks in the exFAT data region)
    - size > 0: ceil(size / cluster_size) * cluster_size
    """
    if size < 0:
        raise ValueError(f"File size cannot be negative: {size}")
    if cluster_size <= 0:
        raise ValueError(f"Cluster size must be positive: {cluster_size}")
    if size == 0:
        return 0
    return ((size + cluster_size - 1) // cluster_size) * cluster_size


def calculate_slack_bytes(size: int, cluster_size: int = EXFAT_CLUSTER_SIZE) -> int:
    """Calculate wasted cluster slack space in bytes (allocated_bytes - logical_size)."""
    return calculate_allocated_bytes(size, cluster_size) - size


def calculate_slack_ratio(size: int, cluster_size: int = EXFAT_CLUSTER_SIZE) -> float:
    """Calculate percentage of wasted space relative to allocated space (0.0% to 100.0%)."""
    allocated = calculate_allocated_bytes(size, cluster_size)
    if allocated == 0:
        return 0.0
    return ((allocated - size) / allocated) * 100.0


# Alias for cross-module consistency
calculate_slack_percentage = calculate_slack_ratio


def normalize_rel_path(path: str, root_path: str, strict: bool = False) -> str:
    """Normalize an arbitrary filesystem path to a canonical relative path using '/' forward slashes.

    Ensures cross-platform SQLite database portability between macOS (/Volumes/KINGSTON/...)
    and Windows (D:\\...).

    Fixes Windows drive root normalization bug where stripping trailing slash from 'D:/'
    caused 'D:' which evaluated relative to process CWD instead of drive root.

    Args:
        path: Target file or directory path (absolute or relative, POSIX or Windows syntax).
        root_path: Base drive root path.
        strict: If True, raises ValueError if path escapes root_path boundary.

    Returns:
        Canonical relative path string using '/' (e.g. '01_AI_Models/model.gguf').
        Returns '' if path == root_path or if path is empty/root dot.
    """
    norm_path = path.replace('\\', '/').strip()
    norm_root = root_path.replace('\\', '/').strip()

    # Normalize Windows drive letter casing for cross-platform consistency
    if len(norm_path) >= 2 and norm_path[1] == ':' and norm_path[0].isalpha():
        norm_path = norm_path[0].upper() + norm_path[1:]
    if len(norm_root) >= 2 and norm_root[1] == ':' and norm_root[0].isalpha():
        norm_root = norm_root[0].upper() + norm_root[1:]

    # Handle empty path
    if not norm_path:
        return ''

    # Preserve trailing slash on Windows drive roots (e.g. 'D:/')
    if len(norm_root) == 2 and norm_root[1] == ':':
        norm_root = norm_root + '/'
    elif len(norm_root) > 1 and norm_root.endswith('/'):
        if not (len(norm_root) == 3 and norm_root[1] == ':'):
            norm_root = norm_root.rstrip('/')

    if len(norm_path) == 2 and norm_path[1] == ':':
        norm_path = norm_path + '/'
    elif len(norm_path) > 1 and norm_path.endswith('/'):
        if not (len(norm_path) == 3 and norm_path[1] == ':'):
            norm_path = norm_path.rstrip('/')

    if norm_path == norm_root or norm_path == '.':
        return ''

    try:
        p = os.path.normpath(norm_path)
        r = os.path.normpath(norm_root)
        rel = os.path.relpath(p, r).replace('\\', '/')
    except ValueError:
        if strict:
            raise ValueError(f"Path '{path}' escapes root boundary '{root_path}'")
        return norm_path

    if rel == '.':
        return ''
    if strict and (rel.startswith('../') or rel == '..'):
        raise ValueError(f"Path '{path}' escapes root boundary '{root_path}'")

    parts = [part for part in rel.split('/') if part and part != '.']
    return '/'.join(parts)


def audit_forbidden_characters(name: str) -> List[Violation]:
    """Audit a filename or directory name for Windows/exFAT naming incompatibilities.

    Checks:
    - Forbidden characters: \\ / : * ? " < > |
    - ASCII control characters: 0x00 through 0x1F
    - Trailing spaces (' ') or trailing periods ('.')
    - Windows reserved 16-bit DOS device names (CON, PRN, AUX, NUL, COM1..9, LPT1..9)

    Returns:
        List of Violation objects (which compare equal to both full codes and raw chars).
        Empty list means 100% Windows/exFAT compatible.
    """
    violations: List[Violation] = []
    for ch in name:
        if ch in FORBIDDEN_CHARS:
            violations.append(Violation(f"FORBIDDEN_CHAR:{ch}", ch))
        elif ord(ch) < 32:
            violations.append(Violation(f"CONTROL_CHAR:0x{ord(ch):02X}", ch))

    if name.endswith(' '):
        violations.append(Violation("TRAILING_SPACE", " "))
    if name.endswith('.'):
        violations.append(Violation("TRAILING_DOT", "."))

    # Extract stem before first period for reserved device check
    stem = name.split('.')[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        violations.append(Violation(f"RESERVED_NAME:{stem}", stem))

    return violations


def sanitize_filename(name: str, replacement: str = '_') -> str:
    """Sanitize an incompatible filename into a 100% Windows/exFAT compliant name.

    Transformations:
    - Replaces forbidden characters (\\ / : * ? " < > |) and control chars with `replacement`.
    - Strips invalid trailing spaces and periods.
    - Prefixes reserved DOS device names with `replacement` (e.g. 'aux.py' -> '_aux.py').
    - Ensures non-empty output string.
    - Idempotent: audit_forbidden_characters(sanitize_filename(name)) == []

    Raises:
        ValueError if `replacement` itself contains forbidden or control characters or is empty.
    """
    if not replacement:
        raise ValueError("Replacement string cannot be empty")
    if any(c in FORBIDDEN_CHARS for c in replacement) or any(ord(c) < 32 for c in replacement):
        raise ValueError(f"Replacement string contains forbidden characters: {replacement}")

    chars: List[str] = []
    for ch in name:
        if ch in FORBIDDEN_CHARS or ord(ch) < 32:
            chars.append(replacement)
        else:
            chars.append(ch)

    sanitized = ''.join(chars).rstrip('. ')
    if not sanitized:
        sanitized = replacement

    stem = sanitized.split('.')[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        sanitized = f"{replacement}{sanitized}"

    return sanitized


def is_symlink(path: str) -> bool:
    """Determine whether a path is a symbolic link without following it.
    Safely traps FileNotFoundError and OSError.
    """
    try:
        return os.path.islink(path)
    except (OSError, ValueError):
        return False


def assert_no_symlinks(path: str) -> None:
    """Assert that the target path is not a symbolic link.

    Raises:
        SymlinkNotPermittedError if the path is a symbolic link.
    """
    if is_symlink(path):
        raise SymlinkNotPermittedError(
            f"Symlinks are strictly prohibited on exFAT filesystem: '{path}'. "
        )


assert_no_symlink = assert_no_symlinks


def check_symlink(path: str) -> bool:
    """Strictly checks and rejects symbolic links on the exFAT filesystem.

    Args:
        path: Target filesystem path to inspect.

    Returns:
        False if the path is not a symbolic link.

    Raises:
        SymlinkNotPermittedError if the path is a symbolic link.
    """
    assert_no_symlinks(path)
    return False


def detect_drive_root(start_path: Optional[str] = None) -> str:
    """Dynamically discover the SSD root directory across macOS, Windows, and Linux.

    Search Strategy:
    1. Environment variable check: SMART_DRIVE_ROOT or KINGSTON_SSD_ROOT.
    2. Upward directory traversal starting from `start_path` (or cwd), checking for
       a quorum (>= 2) of signature SSD root markers.
    3. Platform-specific fallback checks (if start_path is None):
       - macOS: Probe `/Volumes/KINGSTON`
       - Windows: Probe drive letters (D:, E:, F:, G:, H:, C:) for markers.
    4. Final fallback: Return canonical start_path (or cwd).
    """
    env_root = os.environ.get("SMART_DRIVE_ROOT") or os.environ.get("KINGSTON_SSD_ROOT")
    if env_root and os.path.isdir(env_root):
        return os.path.abspath(env_root)

    current = os.path.abspath(start_path) if start_path else os.getcwd()
    markers = {'GEMINI.md', 'AGENTS.md', '01_AI_Models', '02_Learning_Knowledge', 'Clean_Mac_Junk.bat', '.metadata_never_index'}

    # Stage 1: Traverse upwards
    check_dir = current
    while True:
        hits = sum(1 for m in markers if os.path.exists(os.path.join(check_dir, m)))
        if hits >= 2:
            return check_dir
        parent = os.path.dirname(check_dir)
        if parent == check_dir:
            break
        check_dir = parent

    # Stage 2: OS Default Probing (only when start_path was not explicitly provided)
    if start_path is None:
        if os.path.exists('/Volumes/KINGSTON'):
            return '/Volumes/KINGSTON'

        if sys.platform == 'win32':
            for letter in ['D', 'E', 'F', 'G', 'H', 'C']:
                candidate = f"{letter}:\\"
                if os.path.exists(os.path.join(candidate, 'GEMINI.md')) or os.path.exists(os.path.join(candidate, '.metadata_never_index')):
                    return candidate
            if os.path.exists("D:\\"):
                return "D:\\"

    return current


class ExFatEngine:
    """Unified namespace class providing class-level access to all exFAT geometry,
    path normalization, forbidden character, and symlink protection utilities.
    """
    CLUSTER_SIZE: int = EXFAT_CLUSTER_SIZE
    FORBIDDEN_CHARS: Set[str] = FORBIDDEN_CHARS
    WINDOWS_RESERVED_NAMES: Set[str] = WINDOWS_RESERVED_NAMES

    calculate_allocated_bytes = staticmethod(calculate_allocated_bytes)
    calculate_slack_bytes = staticmethod(calculate_slack_bytes)
    calculate_slack_ratio = staticmethod(calculate_slack_ratio)
    calculate_slack_percentage = staticmethod(calculate_slack_percentage)
    normalize_rel_path = staticmethod(normalize_rel_path)
    audit_forbidden_characters = staticmethod(audit_forbidden_characters)
    sanitize_filename = staticmethod(sanitize_filename)
    is_symlink = staticmethod(is_symlink)
    assert_no_symlinks = staticmethod(assert_no_symlinks)
    assert_no_symlink = staticmethod(assert_no_symlink)
    check_symlink = staticmethod(check_symlink)
    detect_drive_root = staticmethod(detect_drive_root)


__all__ = [
    "EXFAT_CLUSTER_SIZE",
    "FORBIDDEN_CHARS",
    "WINDOWS_RESERVED_NAMES",
    "ExFatCompatError",
    "SymlinkNotPermittedError",
    "Violation",
    "calculate_allocated_bytes",
    "calculate_slack_bytes",
    "calculate_slack_ratio",
    "calculate_slack_percentage",
    "normalize_rel_path",
    "audit_forbidden_characters",
    "sanitize_filename",
    "is_symlink",
    "assert_no_symlinks",
    "assert_no_symlink",
    "check_symlink",
    "detect_drive_root",
    "ExFatEngine",
]
