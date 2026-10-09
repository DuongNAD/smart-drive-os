"""smart_drive.core.config - Global Configuration, Constants, and Classification Engine.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT, 512KB allocation blocks).
Cross-platform compatible (macOS & Windows).
Zero external dependencies (Python 3.9+ standard library only).
"""

from __future__ import annotations

import fnmatch
import os
from enum import IntEnum
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple, Set


# ==============================================================================
# 1. HARDWARE & CLUSTER GEOMETRY CONSTANTS
# ==============================================================================

# Kingston XS2000 2TB exFAT Allocation Block Size is verified as 524,288 bytes (512 KB).
CLUSTER_SIZE_BYTES: int = 524_288
CLUSTER_SIZE_KB: int = 512
CLUSTER_SIZE: int = CLUSTER_SIZE_BYTES

SECTOR_SIZE: int = 512
SECTORS_PER_CLUSTER: int = 1024


def calculate_allocated_bytes(nominal_size: int, cluster_size: int = CLUSTER_SIZE) -> int:
    """Calculates physical cluster-allocated disk space in bytes for exFAT.

    Rules:
    - 0-byte files consume 0 clusters (0 bytes) because exFAT records 0-length files
      entirely within directory entries without allocating an FAT cluster chain.
    - Files > 0 bytes consume ceil(nominal_size / cluster_size) * cluster_size.
    - Negative sizes raise ValueError.

    Args:
        nominal_size: File size in bytes (logical length).
        cluster_size: Volume cluster size in bytes (default: 524,288).

    Returns:
        Physical bytes allocated on disk.
    """
    if nominal_size < 0:
        raise ValueError(f"File size cannot be negative: {nominal_size}")
    if cluster_size <= 0:
        raise ValueError(f"Cluster size must be positive: {cluster_size}")
    if nominal_size == 0:
        return 0
    return ((nominal_size + cluster_size - 1) // cluster_size) * cluster_size


def calculate_slack_bytes(nominal_size: int, cluster_size: int = CLUSTER_SIZE) -> int:
    """Calculates wasted cluster slack space in bytes.

    Slack = Physical Allocated Bytes - Logical Nominal Bytes.

    Args:
        nominal_size: File size in bytes.
        cluster_size: Volume cluster size in bytes.

    Returns:
        Wasted slack bytes.
    """
    return calculate_allocated_bytes(nominal_size, cluster_size) - nominal_size


def calculate_cluster_count(nominal_size: int, cluster_size: int = CLUSTER_SIZE) -> int:
    """Calculates the number of physical clusters allocated for a given file size.

    Args:
        nominal_size: File size in bytes.
        cluster_size: Volume cluster size in bytes.

    Returns:
        Integer count of allocated clusters (0 for 0-byte file).
    """
    if nominal_size < 0:
        raise ValueError(f"File size cannot be negative: {nominal_size}")
    if cluster_size <= 0:
        raise ValueError(f"Cluster size must be positive: {cluster_size}")
    if nominal_size == 0:
        return 0
    return (nominal_size + cluster_size - 1) // cluster_size


def calculate_slack_percentage(nominal_size: int, cluster_size: int = CLUSTER_SIZE) -> float:
    """Calculates the percentage of allocated space lost to cluster slack.

    Args:
        nominal_size: File size in bytes.
        cluster_size: Volume cluster size in bytes.

    Returns:
        Float percentage in range [0.0, 100.0).
    """
    allocated = calculate_allocated_bytes(nominal_size, cluster_size)
    if allocated == 0:
        return 0.0
    slack = allocated - nominal_size
    return (slack / allocated) * 100.0


# ==============================================================================
# 2. INVIOLABLE PROTECTED ROOT DIRECTORIES
# ==============================================================================

# Standard Kingston Taxonomies (display casing)
TAXONOMY_ROOT_DIRS: Tuple[str, ...] = (
    "01_AI_Models",
    "02_Learning_Knowledge",
    "03_Personal_Documents",
    "04_Creative_Assets",
    "05_Dev_Toolbox",
    "06_Archives_Storage",
)
STANDARD_TAXONOMIES = TAXONOMY_ROOT_DIRS


# Harmonized Core Taxonomies (both on-disk folder names and standard manifest taxonomy names)
PROTECTED_CORE_TAXONOMIES: Tuple[str, ...] = (
    "01_AI_Models",
    "02_Learning_Knowledge",
    "02_Development_Workspaces",
    "03_Personal_Documents",
    "03_Development_Projects",
    "03_Data_Vault",
    "04_Creative_Assets",
    "04_System_Workspaces",
    "04_System_Offload_Caches",
    "05_Dev_Toolbox",
    "06_Archives_Storage",
)

# Set of root directories that must NEVER be deleted, purged, or renamed.
# Stored in lowercase/casefolded form to support exFAT case-insensitive matching.
PROTECTED_ROOT_DIRS: FrozenSet[str] = frozenset({
    # Business Taxonomies (both on-disk and standard manifest names)
    "01_ai_models",
    "02_learning_knowledge",
    "02_development_workspaces",
    "03_personal_documents",
    "03_development_projects",
    "03_data_vault",
    "04_creative_assets",
    "04_system_workspaces",
    "04_system_offload_caches",
    "05_dev_toolbox",
    "06_archives_storage",
    # Workspaces & Agent Infrastructure
    "smart_ssd_workspace",
    "teamwork_projects",
    ".agents",
    "python_master_final",
    # OS System Directories (Windows & Unix)
    "system volume information",
    ".fseventsd",
    ".spotlight-v100",
    ".trashes",
    "$recycle.bin",
    "windowsapps",
    "wpsystem",
    "deliveryoptimization",
    "wudownloadcache",
    "program files",
    "program files (x86)",
    # Game & Application Directories
    "steamlibrary",
    "riot games",
    "ldplayer",
    "sql2022",
    "downloads",
    "32837",
    "fo4",
    # Active Services & Databases
    "dbi202_vupt/mssql16.mssqlserver",
    "dbi202_vupt\\mssql16.mssqlserver",
    "mssql16.mssqlserver",
    "mssqlserver",
    "mssql",
    # SmartDrive-OS Self-Defense
    "smart-drive-os",
    "smart_drive_os",
    "smart_drive",
    "smart_drive_manager",
})

_TAXONOMY_DIRS_LOWER: FrozenSet[str] = frozenset(t.lower() for t in PROTECTED_CORE_TAXONOMIES)


def is_protected_root_dir(dir_name: str) -> bool:
    """Validates whether a directory name corresponds to an inviolable protected root folder.

    Handles leading/trailing slashes, Windows drive letters, compound paths,
    path segments, and case-insensitivity.
    """
    if not dir_name:
        return False

    cleaned = dir_name.strip().replace("\\", "/")
    # Remove drive prefix if present (e.g. "D:", "C:")
    if len(cleaned) >= 2 and cleaned[1] == ":":
        cleaned = cleaned[2:]
    normalized = cleaned.strip("/")
    if not normalized:
        return False

    norm_lower = normalized.lower()

    # 1. Direct match on normalized relative path
    if norm_lower in PROTECTED_ROOT_DIRS:
        return True

    # 2. Check base directory name (e.g. "D:/WindowsApps" -> "WindowsApps")
    base_name = os.path.basename(normalized) or normalized
    base_lower = base_name.lower()
    if base_lower in PROTECTED_ROOT_DIRS:
        return True

    # 3. Check compound path segments and subpaths for non-taxonomy protected entities
    # (e.g., "DBI202_VuPT/MSSQL16.MSSQLSERVER", or inside system/app/service dirs)
    # Inner items inside taxonomies (e.g. 01_AI_Models/llama3.gguf) are NOT root dirs.
    parts = norm_lower.split("/")
    for part in parts:
        if part in PROTECTED_ROOT_DIRS and part not in _TAXONOMY_DIRS_LOWER:
            return True

    for i in range(len(parts)):
        for j in range(i + 1, len(parts) + 1):
            subpath = "/".join(parts[i:j])
            if subpath in PROTECTED_ROOT_DIRS and subpath not in _TAXONOMY_DIRS_LOWER:
                return True

    return False


# ==============================================================================
# 3. INVIOLABLE PROTECTED ROOT FILES
# ==============================================================================

# Exact root filenames that must NEVER be deleted. Casefolded for comparison.
PROTECTED_ROOT_FILES: FrozenSet[str] = frozenset({
    "gemini.md",
    "claude.md",
    "agents.md",
    "readme.md",
    "privacy.md",
    ".mcp.json",
    "clean_mac_junk.bat",
    "clean_mac_junk.command",
    "clean_mac_junk.sh",
    "check_ssd_status.command",
    "check_ssd_status.ps1",
    "sync_repos.ps1",
    "inspect_repos.ps1",
    "test_pushes.ps1",
    "setup_mac.command",
    "setup_win.bat",
    "setup_ssd.bat",
    "setup_ssd.command",
    "quick_search.bat",
    "quick_search.command",
    "quick_clean.bat",
    "quick_clean.command",
    "quick_audit.bat",
    "quick_audit.command",
    "start_deeptutor.bat",
    "start_deeptutor.command",
    "update_deeptutor.bat",
    "update_deeptutor.command",
    "setup_deeptutor_mcp.bat",
    "setup_deeptutor_mcp.command",
    ".metadata_never_index",
})

# Wildcard glob patterns for protected root files.
PROTECTED_ROOT_FILE_PATTERNS: Tuple[str, ...] = (
    "Smart_*.bat",
    "Smart_*.command",
    "smart_*.bat",
    "smart_*.command",
    "smart_*",
    "check_ssd_status.*",
    "sync_repos.ps1",
    "sync_repos.*",
    "inspect_repos.ps1",
    "inspect_repos.*",
    "test_pushes.ps1",
    "test_pushes.*",
    "clean_mac_junk.*",
    "setup_*",
    "quick_*",
    "start_*",
    "update_*",
)


def is_protected_root_file(filename: str) -> bool:
    """Validates whether a filename corresponds to an inviolable protected root file.

    Checks both exact match against PROTECTED_ROOT_FILES and glob patterns.
    """
    base_name = os.path.basename(filename.strip().replace("\\", "/")).lower()
    if base_name in PROTECTED_ROOT_FILES:
        return True
    for pattern in PROTECTED_ROOT_FILE_PATTERNS:
        if fnmatch.fnmatch(base_name, pattern.lower()):
            return True
    return False


# ==============================================================================
# 4. ANTI-INDEXING MARKERS
# ==============================================================================

# Essential markers preventing macOS Spotlight and FSEvents from degrading SSD performance
ANTI_INDEXING_ROOT_FILE: str = ".metadata_never_index"
ANTI_INDEXING_FSEVENT_DIR: str = ".fseventsd"
ANTI_INDEXING_FSEVENT_FILE: str = ".fseventsd/no_log"

ANTI_INDEXING_MARKERS: Tuple[str, ...] = (
    ANTI_INDEXING_ROOT_FILE,
    ANTI_INDEXING_FSEVENT_FILE,
)


def verify_anti_indexing_markers(drive_root: str) -> Dict[str, bool]:
    """Inspects the drive root to determine presence of anti-indexing markers.

    Args:
        drive_root: Absolute path to the SSD root.

    Returns:
        Dictionary mapping marker path to existence boolean.
    """
    root_path = Path(drive_root)
    return {
        ANTI_INDEXING_ROOT_FILE: (root_path / ANTI_INDEXING_ROOT_FILE).exists(),
        ANTI_INDEXING_FSEVENT_FILE: (root_path / ANTI_INDEXING_FSEVENT_FILE).exists(),
    }


def ensure_anti_indexing_markers(drive_root: str) -> List[str]:
    """Ensures that anti-indexing markers exist, creating them if missing.

    Args:
        drive_root: Absolute path to the SSD root.

    Returns:
        List of marker file paths that were created.
    """
    root_path = Path(drive_root)
    created: List[str] = []

    # 1. Root marker: .metadata_never_index
    never_index_path = root_path / ANTI_INDEXING_ROOT_FILE
    if not never_index_path.exists():
        try:
            never_index_path.touch()
            created.append(ANTI_INDEXING_ROOT_FILE)
        except OSError:
            pass

    # 2. .fseventsd/no_log
    fsevents_dir = root_path / ANTI_INDEXING_FSEVENT_DIR
    try:
        fsevents_dir.mkdir(exist_ok=True)
        no_log_path = root_path / ANTI_INDEXING_FSEVENT_FILE
        if not no_log_path.exists():
            no_log_path.touch()
            created.append(ANTI_INDEXING_FSEVENT_FILE)
    except OSError:
        pass

    return created


# ==============================================================================
# 5. STANDARD TAXONOMY CATEGORIES & EXTENSIONS
# ==============================================================================

CATEGORIES: Dict[str, FrozenSet[str]] = {
    "AI Models": frozenset({
        ".gguf", ".bin", ".safetensors", ".onnx", ".pt", ".pth",
        ".ckpt", ".tflite", ".h5", ".weights", ".llama", ".model",
        ".mlmodel", ".pb", ".caffemodel", ".engine", ".params"
    }),
    "Code": frozenset({
        ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        ".rs", ".go", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
        ".hxx", ".java", ".kt", ".kts", ".swift", ".cs", ".php",
        ".rb", ".html", ".htm", ".css", ".scss", ".sass", ".less",
        ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".psm1",
        ".json", ".json5", ".yaml", ".yml", ".toml", ".xml", ".sql",
        ".lua", ".zig", ".scala", ".r", ".dart", ".vue", ".svelte",
        ".graphql", ".proto", ".asm", ".s", ".v", ".sv"
    }),
    "Books/Learning": frozenset({
        ".epub", ".mobi", ".azw", ".azw3", ".djvu", ".cbr", ".cbz",
        ".fb2", ".lit", ".prc", ".ibooks"
    }),
    "Docs": frozenset({
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
        ".txt", ".md", ".markdown", ".rst", ".rtf", ".csv", ".tsv",
        ".odt", ".ods", ".odp", ".tex", ".pages", ".numbers", ".key",
        ".log"
    }),
    "Media": frozenset({
        # Video
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv",
        ".m4v", ".mpg", ".mpeg", ".3gp",
        # Audio
        ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus",
        ".wma", ".alac", ".aiff", ".mid", ".midi",
        # Image
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp",
        ".ico", ".tiff", ".tif", ".heic", ".psd", ".ai", ".raw",
        ".cr2", ".nef"
    }),
    "Archives": frozenset({
        ".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz",
        ".txz", ".7z", ".rar", ".iso", ".dmg", ".pkg", ".zst",
        ".lzma", ".cab", ".wim", ".apk"
    }),
    "System Junk": frozenset({
        ".ds_store", "thumbs.db", "desktop.ini", ".pyc", ".pyo",
        ".tmp", ".bak", ".swp", ".swo", ".dmp"
    }),
}

# Pre-computed inverted mapping for O(1) extension category lookup
EXT_TO_CATEGORY: Dict[str, str] = {}
for _cat, _ext_set in CATEGORIES.items():
    for _ext in _ext_set:
        EXT_TO_CATEGORY[_ext.lower()] = _cat


def classify_by_extension(ext: str) -> str:
    """Maps a file extension to its primary functional category.

    Args:
        ext: File extension, with or without leading dot (e.g. '.py' or 'py').

    Returns:
        Category name string (e.g. 'Code', 'AI Models', 'Docs') or 'Other'.
    """
    clean_ext = ext.strip().lower()
    if clean_ext and not clean_ext.startswith("."):
        clean_ext = "." + clean_ext
    return EXT_TO_CATEGORY.get(clean_ext, "Other")


def classify_file_entry(name: str, ext: str, rel_path: str = "") -> str:
    """Classifies a file based on name, extension, and optional relative path.

    Handles AppleDouble '._*' and special system files before falling back
    to extension-based classification.

    Args:
        name: Base filename (e.g. '._test.py', 'document.pdf').
        ext: Extension (e.g. '.pdf').
        rel_path: Relative path from drive root (optional).

    Returns:
        Category string.
    """
    name_lower = name.lower()

    # Special AppleDouble resource forks and OS caches
    if name_lower.startswith("._") or name_lower in {".ds_store", "thumbs.db", "desktop.ini"}:
        return "System Junk"

    # Contextual check: learning path priority for PDFs
    if ext.lower() == ".pdf" and rel_path:
        norm_rel = rel_path.replace("\\", "/").lower()
        if norm_rel.startswith("02_learning_knowledge"):
            return "Books/Learning"

    return classify_by_extension(ext)


# ==============================================================================
# 6. JUNK CLASSIFICATION PATTERNS & 3-TIER RULES
# ==============================================================================

class JunkTier(IntEnum):
    """Junk classification severity and safety tiers."""
    TIER_1_SAFE = 1        # Completely safe: OS metadata, AppleDouble, bytecode caches
    TIER_2_DEV_CACHE = 2   # Build & test runner caches: pytest, ruff, mypy, dist, build
    TIER_3_SENSITIVE = 3   # Crash dumps, logs, scratch files (opt-in purge only)


@dataclass(frozen=True)
class JunkRule:
    """Definition of a declarative junk detection pattern rule."""
    pattern: str
    is_directory: bool
    tier: JunkTier
    description: str
    os_source: str


JUNK_RULES: Tuple[JunkRule, ...] = (
    # --------------------------------------------------------------------------
    # TIER 1: SAFE TO PURGE (OS Metadata & Compiler Bytecode)
    # --------------------------------------------------------------------------
    # macOS
    JunkRule("._*", False, JunkTier.TIER_1_SAFE, "AppleDouble resource fork", "macOS"),
    JunkRule(".DS_Store", False, JunkTier.TIER_1_SAFE, "macOS Finder metadata", "macOS"),
    JunkRule(".Spotlight-V100", True, JunkTier.TIER_1_SAFE, "Spotlight index directory", "macOS"),
    JunkRule(".Trashes", True, JunkTier.TIER_1_SAFE, "macOS Trash folder", "macOS"),
    JunkRule(".TemporaryItems", True, JunkTier.TIER_1_SAFE, "macOS temporary items folder", "macOS"),

    # Windows
    JunkRule("Thumbs.db", False, JunkTier.TIER_1_SAFE, "Windows thumbnail cache", "Windows"),
    JunkRule("ehthumbs.db", False, JunkTier.TIER_1_SAFE, "Windows media thumbnail cache", "Windows"),
    JunkRule("ehthumbs_vista.db", False, JunkTier.TIER_1_SAFE, "Windows Vista thumbnail cache", "Windows"),
    JunkRule("$RECYCLE.BIN", True, JunkTier.TIER_1_SAFE, "Windows Recycle Bin directory", "Windows"),
    JunkRule("desktop.ini", False, JunkTier.TIER_1_SAFE, "Windows folder custom settings", "Windows"),

    # Python
    JunkRule("__pycache__", True, JunkTier.TIER_1_SAFE, "Python bytecode cache directory", "Python"),
    JunkRule("*.pyc", False, JunkTier.TIER_1_SAFE, "Python compiled bytecode", "Python"),
    JunkRule("*.pyo", False, JunkTier.TIER_1_SAFE, "Python optimized bytecode", "Python"),

    # --------------------------------------------------------------------------
    # TIER 2: DEV ARTIFACTS & TEST CACHES (Regenerable)
    # --------------------------------------------------------------------------
    JunkRule(".pytest_cache", True, JunkTier.TIER_2_DEV_CACHE, "Pytest test cache directory", "Dev"),
    JunkRule(".ruff_cache", True, JunkTier.TIER_2_DEV_CACHE, "Ruff linter cache directory", "Dev"),
    JunkRule(".mypy_cache", True, JunkTier.TIER_2_DEV_CACHE, "Mypy type checker cache directory", "Dev"),
    JunkRule(".tox", True, JunkTier.TIER_2_DEV_CACHE, "Tox test environment directory", "Dev"),
    JunkRule(".nox", True, JunkTier.TIER_2_DEV_CACHE, "Nox test environment directory", "Dev"),
    JunkRule(".coverage", False, JunkTier.TIER_2_DEV_CACHE, "Coverage measurement data", "Dev"),
    JunkRule("htmlcov", True, JunkTier.TIER_2_DEV_CACHE, "Coverage HTML report directory", "Dev"),
    JunkRule("build", True, JunkTier.TIER_2_DEV_CACHE, "Build output directory", "Dev"),
    JunkRule("dist", True, JunkTier.TIER_2_DEV_CACHE, "Distribution packaging directory", "Dev"),
    JunkRule("*.egg-info", True, JunkTier.TIER_2_DEV_CACHE, "Python package egg info", "Dev"),

    # --------------------------------------------------------------------------
    # TIER 3: SENSITIVE / OPT-IN (Crash Dumps, Diagnostics, Temp)
    # --------------------------------------------------------------------------
    JunkRule("*.dmp", False, JunkTier.TIER_3_SENSITIVE, "Windows crash dump", "Windows"),
    JunkRule("core.*", False, JunkTier.TIER_3_SENSITIVE, "Unix core dump", "Dev"),
    JunkRule("hs_err_pid*.log", False, JunkTier.TIER_3_SENSITIVE, "Java crash log", "Dev"),
    JunkRule("*.tmp", False, JunkTier.TIER_3_SENSITIVE, "Temporary scratch file", "All"),
    JunkRule("*.temp", False, JunkTier.TIER_3_SENSITIVE, "Temporary scratch file", "All"),
    JunkRule("*.swp", False, JunkTier.TIER_3_SENSITIVE, "Vim swap file", "Dev"),
    JunkRule("*.swo", False, JunkTier.TIER_3_SENSITIVE, "Vim swap file", "Dev"),
    JunkRule("*~", False, JunkTier.TIER_3_SENSITIVE, "Editor backup file", "Dev"),
)


def match_junk_rule(
    name: str,
    is_dir: bool,
    max_tier: JunkTier = JunkTier.TIER_1_SAFE,
    is_root_level: bool = False
) -> Optional[JunkRule]:
    """Matches a file or directory name against known junk rules up to max_tier.

    Enforces inviolable protections:
    - Never flags anti-indexing markers (.metadata_never_index, no_log).
    - Never flags protected root files or root directories.

    Args:
        name: Filename or directory name.
        is_dir: True if inspecting a directory, False for file.
        max_tier: Maximum allowed tier (e.g. TIER_1_SAFE, TIER_2_DEV_CACHE, TIER_3_SENSITIVE).
        is_root_level: True if this item resides directly at the drive root.

    Returns:
        Matching JunkRule if matched and allowed, else None.
    """
    clean_name = name.strip()
    name_lower = clean_name.lower()

    # Hard Inviolable Check: Anti-indexing markers
    if clean_name in {ANTI_INDEXING_ROOT_FILE, "no_log"}:
        return None

    # Hard Inviolable Check: Root protected files & dirs
    if is_root_level:
        if is_dir and is_protected_root_dir(clean_name):
            return None
        if not is_dir and is_protected_root_file(clean_name):
            return None

    # Evaluate rules
    for rule in JUNK_RULES:
        if rule.is_directory != is_dir:
            continue
        if rule.tier > max_tier:
            continue
        if fnmatch.fnmatch(name_lower, rule.pattern.lower()):
            return rule

    return None


# ==============================================================================
# 7. DEFAULT SYSTEM EXCLUSIONS FOR SCANNER
# ==============================================================================

DEFAULT_EXCLUDE_DIRS: FrozenSet[str] = frozenset({
    # Windows & Unix System Directories
    "$RECYCLE.BIN",
    "$Recycle.Bin",
    "System Volume Information",
    ".Spotlight-V100",
    ".Trashes",
    ".fseventsd",
    ".git",
    ".agents",
    "WindowsApps",
    "WpSystem",
    "DeliveryOptimization",
    "WUDownloadCache",
    "Program Files",
    "Program Files (x86)",
    # Game & Application Directories
    "SteamLibrary",
    "Riot Games",
    "LDPlayer",
    "SQL2022",
    "Downloads",
    "32837",
    "fo4",
    # Active Services & Databases
    "DBI202_VuPT/MSSQL16.MSSQLSERVER",
    "DBI202_VuPT\\MSSQL16.MSSQLSERVER",
    "MSSQL16.MSSQLSERVER",
    "MSSQLSERVER",
    "MSSQL",
    # SmartDrive-OS Self-Defense
    "smart-drive-os",
    "smart_drive_os",
    "smart_drive",
    "smart_drive_manager",
})


# ==============================================================================
# EXPORTS
# ==============================================================================

__all__ = [
    # Cluster constants & math
    "CLUSTER_SIZE",
    "CLUSTER_SIZE_BYTES",
    "CLUSTER_SIZE_KB",
    "SECTOR_SIZE",
    "SECTORS_PER_CLUSTER",
    "calculate_allocated_bytes",
    "calculate_slack_bytes",
    "calculate_cluster_count",
    "calculate_slack_percentage",
    # Protected entities
    "PROTECTED_ROOT_DIRS",
    "TAXONOMY_ROOT_DIRS",
    "STANDARD_TAXONOMIES",
    "PROTECTED_CORE_TAXONOMIES",
    "PROTECTED_ROOT_FILES",
    "PROTECTED_ROOT_FILE_PATTERNS",
    "is_protected_root_dir",
    "is_protected_root_file",
    # Anti-indexing
    "ANTI_INDEXING_MARKERS",
    "ANTI_INDEXING_ROOT_FILE",
    "ANTI_INDEXING_FSEVENT_DIR",
    "ANTI_INDEXING_FSEVENT_FILE",
    "verify_anti_indexing_markers",
    "ensure_anti_indexing_markers",
    # Taxonomy & categories
    "CATEGORIES",
    "EXT_TO_CATEGORY",
    "classify_by_extension",
    "classify_file_entry",
    # Junk classification
    "JunkTier",
    "JunkRule",
    "JUNK_RULES",
    "match_junk_rule",
    # Scanner exclusions
    "DEFAULT_EXCLUDE_DIRS",
]
