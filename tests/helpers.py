"""tests/helpers.py - Test Fixtures, Mock Generators, and Reference Oracles.

100% Python Standard Library. Zero external dependencies.
Provides isolated sandbox directories, mock SSD drive structures,
and authoritative mathematical oracles for all test modules.
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# exFAT Hardware Constants
CLUSTER_SIZE: int = 524_288  # 512 KB
CLUSTER_SIZE_BYTES: int = CLUSTER_SIZE
CLUSTER_SIZE_KB: int = 512

# Inviolable protected root files according to specification
INVIOLABLE_ROOT_FILES: Set[str] = {
    "gemini.md",
    "claude.md",
    "agents.md",
    "readme.md",
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
    ".metadata_never_index",
}

# The 6 standard taxonomies
STANDARD_TAXONOMIES: Tuple[str, ...] = (
    "01_AI_Models",
    "02_Learning_Knowledge",
    "03_Development_Projects",
    "04_System_Workspaces",
    "05_Dev_Toolbox",
    "06_Archives_Storage",
)

# Reference Junk Signatures
JUNK_BASENAMES: Set[str] = {
    ".DS_Store",
    "Thumbs.db",
    "thumbs.db",
    "ehthumbs.db",
    "Desktop.ini",
    "desktop.ini",
}

JUNK_EXTENSIONS: Set[str] = {
    ".dmp",
    ".tmp",
    ".temp",
}


# ==============================================================================
# Authoritative Reference Oracles
# ==============================================================================

def oracle_cluster_allocation(size: int, cluster_size: int = CLUSTER_SIZE) -> Dict[str, Any]:
    """Derives exact physical cluster allocation and slack space from exFAT geometry.

    Rules:
    - size < 0: raises ValueError
    - size == 0: 0 allocated bytes, 0 slack bytes, 0 clusters, 0.0 slack ratio
    - size > 0: ceil(size / cluster_size) * cluster_size
    """
    if size < 0:
        raise ValueError(f"File size cannot be negative: {size}")
    if size == 0:
        allocated = 0
        slack = 0
        slack_ratio = 0.0
        clusters = 0
    else:
        clusters = math.ceil(size / cluster_size)
        allocated = clusters * cluster_size
        slack = allocated - size
        slack_ratio = slack / allocated if allocated > 0 else 0.0

    return {
        "logical_bytes": size,
        "allocated_bytes": allocated,
        "slack_bytes": slack,
        "slack_ratio": slack_ratio,
        "slack_percentage": slack_ratio * 100.0,
        "clusters": clusters,
    }


def oracle_full_sha256(filepath: Path) -> str:
    """Computes authoritative full SHA-256 checksum."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def oracle_is_protected(target_path: Path, drive_root: Path) -> Tuple[bool, str]:
    """Authoritative whitelist protection oracle."""
    try:
        rel = target_path.relative_to(drive_root).as_posix()
    except ValueError:
        return False, "Not in drive root"

    parts = rel.split("/")
    base_lower = parts[0].lower()

    if len(parts) == 1 and base_lower in INVIOLABLE_ROOT_FILES:
        return True, f"Inviolable root file: {parts[0]}"

    if len(parts) == 1:
        if (
            base_lower.startswith("setup_")
            or base_lower.startswith("quick_")
            or base_lower.startswith("clean_")
            or base_lower.endswith(".command")
            or base_lower.endswith(".bat")
            or base_lower.endswith(".ps1")
        ):
            return True, f"Inviolable root script: {parts[0]}"

    if rel in {".metadata_never_index", ".fseventsd/no_log"}:
        return True, "Anti-indexing system shield marker"

    if len(parts) == 1 and parts[0] in STANDARD_TAXONOMIES:
        return True, f"Standard taxonomy directory: {parts[0]}"

    return False, "Not protected"


def oracle_is_junk(target_path: Path) -> Tuple[bool, str]:
    """Authoritative junk pattern matcher oracle."""
    name = target_path.name
    if name.startswith("._"):
        return True, "AppleDouble resource fork"
    if name in JUNK_BASENAMES:
        return True, "System view/thumbnail junk"
    ext = target_path.suffix.lower()
    if ext in JUNK_EXTENSIONS:
        return True, "Temporary/dump junk"
    if target_path.is_dir() and name in {".Spotlight-V100", ".TemporaryItems", ".Trashes", "$RECYCLE.BIN"}:
        return True, "System junk directory"
    return False, "Not junk"


# ==============================================================================
# Isolated Temp Workspace Fixture
# ==============================================================================

class TempWorkspace:
    """Manages an isolated temporary directory with automatic teardown."""

    def __init__(self, prefix: str = "smart_drive_test_") -> None:
        self.temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None
        self.path: Optional[Path] = None
        self.prefix = prefix

    def __enter__(self) -> Path:
        self.temp_dir = tempfile.TemporaryDirectory(prefix=self.prefix)
        self.path = Path(self.temp_dir.name).resolve()
        return self.path

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.temp_dir is not None:
            try:
                self.temp_dir.cleanup()
            except Exception:
                pass


def create_mock_ssd_tree(root: Path) -> Path:
    """Constructs a comprehensive mock SSD directory tree.

    Includes:
    - 6 taxonomy directories
    - Inviolable root control files (GEMINI.md, AGENTS.md, README.md, scripts)
    - Anti-indexing shields (.metadata_never_index, .fseventsd/no_log)
    - Realistic models, papers, scripts, and archives
    - macOS and Windows junk files (._*, .DS_Store, Thumbs.db, *.tmp, *.dmp)
    - Duplicate files
    - Boundary test files (0-byte, 1-byte, exact 512KB cluster, boundary+1)
    """
    root.mkdir(parents=True, exist_ok=True)

    # 1. Root control & safeguard files
    (root / "GEMINI.md").write_text("# SSD Workspace Guidelines\nEnvironment: Kingston XS2000\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Claude Directives\nUse ssd_search protocol.\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# Universal Agent Manifest\nFast path search protocol.\n", encoding="utf-8")
    (root / "README.md").write_text("# Kingston SSD Master Index\n", encoding="utf-8")
    (root / "Setup_Win.bat").write_text("@echo off\necho Setup SSD\n", encoding="utf-8")
    (root / "Setup_Mac.command").write_text("#!/bin/bash\necho Setup SSD\n", encoding="utf-8")
    (root / "check_ssd_status.ps1").write_text("Write-Host 'SSD Status OK'\n", encoding="utf-8")
    (root / "sync_repos.ps1").write_text("Write-Host 'Sync repos'\n", encoding="utf-8")
    (root / "Clean_Mac_Junk.bat").write_text("@echo off\necho Clean\n", encoding="utf-8")

    # 2. Anti-indexing shields
    (root / ".metadata_never_index").touch()
    fsevents = root / ".fseventsd"
    fsevents.mkdir(exist_ok=True)
    (fsevents / "no_log").touch()

    # 3. Taxonomy 01: 01_AI_Models
    ai_dir = root / "01_AI_Models"
    gguf_dir = ai_dir / "GGUF"
    hf_dir = ai_dir / "HuggingFace"
    gguf_dir.mkdir(parents=True, exist_ok=True)
    hf_dir.mkdir(parents=True, exist_ok=True)

    # Model file (simulated 10KB binary)
    model_content = b"GGUF_MODEL_TENSOR_WEIGHTS_DATA_BLOCK_" * 300
    (gguf_dir / "llama-3-8b.Q4_K_M.gguf").write_bytes(model_content)
    (hf_dir / "config.json").write_text('{"architectures": ["LlamaForCausalLM"], "vocab_size": 32000}\n', encoding="utf-8")
    (hf_dir / "tokenizer.model").write_bytes(b"TOKENIZER_DATA_" * 50)

    # 4. Taxonomy 02: 02_Learning_Knowledge
    learn_dir = root / "02_Learning_Knowledge"
    code_dir = learn_dir / "Learning_Code"
    notes_dir = learn_dir / "Study_Notes"
    code_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)

    (learn_dir / "INDEX.md").write_text("# Master Knowledge Index\nRouting and topology.\n", encoding="utf-8")
    (code_dir / "algorithm_kata.py").write_text("def binary_search(arr, target): pass\n", encoding="utf-8")
    (notes_dir / "deep_learning_theory.md").write_text("# Deep Learning Notes\nBackpropagation and attention.\n", encoding="utf-8")
    (notes_dir / "vietnamese_guide.pdf").write_bytes(b"%PDF-1.4 Mock Vietnamese Study Guide PDF content")

    # 5. Taxonomy 03: 03_Development_Projects
    dev_proj = root / "03_Development_Projects" / "smart_drive_os"
    dev_proj.mkdir(parents=True, exist_ok=True)
    (dev_proj / "pyproject.toml").write_text('[project]\nname = "smart-drive-os"\n', encoding="utf-8")
    (dev_proj / "main.py").write_text("print('SmartDrive')\n", encoding="utf-8")

    # 6. Taxonomy 04: 04_System_Workspaces
    sys_work = root / "04_System_Workspaces" / "configs"
    sys_work.mkdir(parents=True, exist_ok=True)
    (sys_work / "settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")

    # 7. Taxonomy 05: 05_Dev_Toolbox
    dev_tools = root / "05_Dev_Toolbox" / "Scripts"
    dev_tools.mkdir(parents=True, exist_ok=True)
    (dev_tools / "clean_mac_junk.sh").write_text("#!/bin/bash\nfind . -name '._*' -delete\n", encoding="utf-8")

    # 8. Taxonomy 06: 06_Archives_Storage
    archive_dir = root / "06_Archives_Storage" / "Backups"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "cold_archive_2025.zip").write_bytes(b"PK\x03\x04Mock Archive Content Zip")

    # 9. Duplicates: Identical model checkpoint placed in two locations
    dup_model_bytes = b"EXACT_IDENTICAL_MODEL_CHECKPOINT_DATA_" * 400  # ~15.2 KB
    (gguf_dir / "qwen2.5-coder-7b.gguf").write_bytes(dup_model_bytes)
    (archive_dir / "qwen2.5-coder-7b_backup.gguf").write_bytes(dup_model_bytes)

    # Duplicate note file
    dup_note_text = "# Duplicated Study Guide\nIdentical notes across trees.\n"
    (notes_dir / "study_guide_v1.md").write_text(dup_note_text, encoding="utf-8")
    (learn_dir / "study_guide_backup.md").write_text(dup_note_text, encoding="utf-8")

    # 10. Junk Files (macOS AppleDouble, .DS_Store, Windows Thumbs.db, temp caches)
    (root / "._GEMINI.md").write_bytes(b"\x00\x05\x16\x07AppleDouble Resource Fork")
    (ai_dir / "._GGUF").write_bytes(b"\x00\x05\x16\x07AppleDouble Dir Resource Fork")
    (gguf_dir / "._llama-3-8b.Q4_K_M.gguf").write_bytes(b"\x00\x05\x16\x07AppleDouble Model Fork")
    (notes_dir / "._deep_learning_theory.md").write_bytes(b"\x00\x05\x16\x07AppleDouble Notes Fork")
    (root / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1Mock DS_Store Root")
    (learn_dir / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1Mock DS_Store Sub")
    (root / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0Mock Thumbs DB Content")
    (dev_tools / "cache.tmp").write_text("temp cache data\n", encoding="utf-8")
    (dev_tools / "crash_dump.dmp").write_bytes(b"DUMP\x00\x01\x02CrashData")

    # 11. Cluster Boundary Test Files
    (root / "zero_byte.dat").touch()  # 0 bytes -> 0 allocated
    (root / "one_byte.dat").write_bytes(b"X")  # 1 byte -> 524,288 allocated
    exact_cluster = b"A" * CLUSTER_SIZE
    (root / "exact_cluster.dat").write_bytes(exact_cluster)  # 524,288 bytes -> 0 slack
    (root / "boundary_plus_one.dat").write_bytes(exact_cluster + b"B")  # 524,289 bytes -> 1,048,576 allocated

    return root


# ==============================================================================
# CLI Subprocess Execution Helper
# ==============================================================================

def run_smart_drive_cli(
    args: List[str],
    cwd: Optional[Path] = None,
    timeout: float = 30.0,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Executes `python -m smart_drive <args>` via subprocess."""
    project_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "smart_drive"] + args
    sub_env = os.environ.copy()
    if env:
        sub_env.update(env)
    # Ensure project root is in PYTHONPATH so smart_drive is found
    existing_pp = sub_env.get("PYTHONPATH", "")
    sub_env["PYTHONPATH"] = str(project_root) + (os.pathsep + existing_pp if existing_pp else "")
    # Child output may hold Vietnamese text: decode it as UTF-8 on every OS (Windows defaults to cp1252).
    sub_env.setdefault("PYTHONIOENCODING", "utf-8")

    return subprocess.run(
        cmd,
        cwd=str(cwd or project_root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=sub_env,
    )


# ==============================================================================
# Base Test Case Class
# ==============================================================================

class SmartDriveTestCase(unittest.TestCase):
    """Base class for all SmartDrive test suites providing fixtures and assertions."""

    def setUp(self) -> None:
        """Create an isolated temporary directory for the test case."""
        self._temp_dir = tempfile.TemporaryDirectory(prefix="sd_test_")
        self.test_dir = Path(self._temp_dir.name).resolve()

    def tearDown(self) -> None:
        """Clean up the isolated temporary directory."""
        if hasattr(self, "_temp_dir") and self._temp_dir is not None:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass

    def create_mock_drive(self) -> Path:
        """Helper to create a populated mock drive in self.test_dir."""
        return create_mock_ssd_tree(self.test_dir / "mock_drive")

    def assertSlackAlmostEqual(self, actual: float, expected: float, delta: float = 0.01) -> None:
        """Assert floating point percentage equality within delta."""
        self.assertAlmostEqual(actual, expected, delta=delta)
