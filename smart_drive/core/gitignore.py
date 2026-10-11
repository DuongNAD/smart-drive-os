"""smart_drive.core.gitignore - Gitignore governance for repositories on the SSD.

Audits and applies SmartDrive-managed .gitignore blocks per repo stack,
keeps personal learning data out of public repositories, and optionally
untracks committed junk without deleting working files.

100% Python Standard Library (Python 3.9+ compatible).
"""

from __future__ import annotations

import collections
from dataclasses import asdict, dataclass, field
import difflib
import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

GIT_TIMEOUT = 30

MANAGED_HEADER = "# >>> smart-drive managed (do not edit inside) >>>"
MANAGED_FOOTER = "# <<< smart-drive managed <<<"

# Base patterns: exFAT / macOS / Windows / Linux junk, logs, temp, agent scratch, secrets, runtime, node_modules
BASE_PATTERNS: List[str] = [
    # exFAT / OS metadata junk
    "._*",
    ".DS_Store",
    ".AppleDouble/",
    ".Spotlight-V100/",
    ".Trashes",
    ".fseventsd/",
    ".TemporaryItems/",
    ".DocumentRevisions-V100/",
    ".VolumeIcon.icns",
    ".apdisk",
    "Thumbs.db",
    "ehthumbs.db",
    "desktop.ini",
    "$RECYCLE.BIN/",
    "System Volume Information/",
    ".Trash-*/",
    "*~",
    # logs and temporary files
    "*.log",
    "*.tmp",
    "*.bak",
    "*.swp",
    "*.swo",
    # agent scratch
    ".agents/",
    ".claude/settings.local.json",
    # secrets
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    # SmartDrive runtime
    ".smart_drive/",
    # node_modules on exFAT
    "node_modules/",
]

# Per-stack specific pattern catalog
STACK_PATTERNS: Dict[str, List[str]] = {
    "python": [
        "__pycache__/",
        "*.py[cod]",
        "*$py.class",
        ".venv/",
        "venv/",
        "env/",
        "ENV/",
        "build/",
        "dist/",
        "*.egg-info/",
        ".pytest_cache/",
        ".mypy_cache/",
        ".ruff_cache/",
        ".coverage",
        "htmlcov/",
        ".tox/",
        ".nox/",
        ".hypothesis/",
    ],
    "node": [
        "dist/",
        "out/",
        ".next/",
        ".nuxt/",
        ".cache/",
        ".turbo/",
        ".npm/",
    ],
    "jupyter": [
        ".ipynb_checkpoints/",
    ],
    "java": [
        "target/",
        "*.class",
        ".gradle/",
        "build/",
    ],
    "rust": [
        "target/",
    ],
    "dotnet": [
        "bin/",
        "obj/",
    ],
    "latex": [
        "*.aux",
        "*.bbl",
        "*.blg",
        "*.log",
        "*.out",
        "*.toc",
        "*.synctex.gz",
        "*.fdb_latexmk",
        "*.fls",
    ],
    "ai-models": [
        "*.gguf",
        "*.safetensors",
        "*.pt",
        "*.bin",
        "*.onnx",
        "*.ckpt",
    ],
    "learning": [
        ".vega/",
        "**/progress.jsonl",
        "aurora-progress-*.jsonl",
        "orion-attempts.jsonl",
        "**/polaris/.cache/",
        "**/polaris/captures/",
    ],
}

# Constellation personal-data contract patterns
PERSONAL_DATA_PATTERNS: List[str] = [
    ".vega/",
    "**/progress.jsonl",
    "aurora-progress-*.jsonl",
    "orion-attempts.jsonl",
    "**/polaris/.cache/",
    "**/polaris/captures/",
]


# -----------------------------------------------------------------------------
# Git subprocess helper
# -----------------------------------------------------------------------------

def _git(path: str, *args: str) -> Tuple[int, str, str]:
    """Runs a git command in path without locks or interactive prompts."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        res = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            env=env,
            check=False,
        )
        return res.returncode, res.stdout, res.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def git_available() -> bool:
    """True if git executable is available on PATH."""
    return shutil.which("git") is not None


# -----------------------------------------------------------------------------
# Stack detection
# -----------------------------------------------------------------------------

def detect_stacks(repo_path: str) -> List[str]:
    """Detects technologies and domains present at repo root or shallow levels.
    
    Checks shallow directory hierarchy (depth 0, 1, 2) pruning heavy subtrees,
    supplemented by git ls-files if available, avoiding recursive whole-drive scans.
    """
    stacks: Set[str] = set()
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        return []

    prune_dirs = {
        ".git", "node_modules", "site-packages", "__pycache__", "target",
        "$recycle.bin", "system volume information", ".venv", "venv", "env",
        "env", "bin", "obj", ".smart_drive", ".idea", ".vscode", "dist", "build",
    }

    shallow_files: List[Tuple[str, str]] = []
    stack_dirs: List[Tuple[str, int]] = [(repo_path, 0)]
    while stack_dirs:
        curr, depth = stack_dirs.pop()
        try:
            with os.scandir(curr) as it:
                for entry in it:
                    name_lower = entry.name.lower()
                    if entry.is_dir(follow_symlinks=False):
                        if name_lower in prune_dirs:
                            continue
                        if entry.name == ".vega":
                            stacks.add("learning")
                            continue
                        if depth < 2:
                            stack_dirs.append((entry.path, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        rel = os.path.relpath(entry.path, repo_path).replace("\\", "/")
                        shallow_files.append((rel, entry.name))
        except OSError:
            continue

    # If git is available, also inspect tracked files (instant, covers nested model weights or tex docs)
    tracked_files: List[str] = []
    if os.path.exists(os.path.join(repo_path, ".git")):
        code, out, _ = _git(repo_path, "ls-files")
        if code == 0 and out.strip():
            tracked_files = [f.strip() for f in out.splitlines() if f.strip()]

    all_rel_paths = set(f[0] for f in shallow_files) | set(tracked_files)

    for rel_path in all_rel_paths:
        p_lower = rel_path.lower()
        parts = p_lower.split("/")
        filename = parts[-1]

        # Python: pyproject.toml, setup.py, setup.cfg, requirements*.txt, *.py at top levels
        if filename in ("pyproject.toml", "setup.py", "setup.cfg"):
            stacks.add("python")
        elif fnmatch.fnmatch(filename, "requirements*.txt"):
            stacks.add("python")
        elif filename.endswith(".py") and len(parts) <= 2:
            stacks.add("python")

        # Node: package.json
        if filename == "package.json":
            stacks.add("node")

        # Jupyter: *.ipynb
        if filename.endswith(".ipynb"):
            stacks.add("jupyter")

        # Java: pom.xml, build.gradle, build.gradle.kts
        if filename in ("pom.xml", "build.gradle", "build.gradle.kts"):
            stacks.add("java")

        # Rust: Cargo.toml
        if filename == "cargo.toml":
            stacks.add("rust")

        # Dotnet: *.csproj, *.sln
        if filename.endswith(".csproj") or filename.endswith(".sln"):
            stacks.add("dotnet")

        # LaTeX: *.tex
        if filename.endswith(".tex"):
            stacks.add("latex")

        # AI Models: *.gguf, *.safetensors, *.pt, *.onnx
        if any(filename.endswith(ext) for ext in (".gguf", ".safetensors", ".pt", ".onnx")):
            stacks.add("ai-models")

        # Learning: course.json, deck.json, polaris/brief.json, .vega
        if filename in ("course.json", "deck.json"):
            stacks.add("learning")
        if filename == "brief.json" and "polaris" in parts:
            stacks.add("learning")
        if ".vega" in parts:
            stacks.add("learning")

    return sorted(stacks)


# -----------------------------------------------------------------------------
# Pattern catalog & block construction
# -----------------------------------------------------------------------------

def get_recommended_patterns(stacks: Sequence[str]) -> List[str]:
    """Returns deduplicated recommended patterns (base + detected stack patterns)."""
    pats: List[str] = []
    seen: Set[str] = set()

    for p in BASE_PATTERNS:
        p_str = p.strip()
        if p_str and not p_str.startswith("#"):
            if p_str not in seen:
                pats.append(p_str)
                if not p_str.startswith("!"):
                    seen.add(p_str)

    for stack in sorted(stacks):
        for p in STACK_PATTERNS.get(stack, []):
            p_str = p.strip()
            if p_str and not p_str.startswith("#"):
                if p_str not in seen:
                    pats.append(p_str)
                    if not p_str.startswith("!"):
                        seen.add(p_str)

    return pats


def generate_managed_block(stacks: Sequence[str], newline: str = "\n") -> str:
    """Builds the SmartDrive-managed .gitignore block for given stacks."""
    lines: List[str] = [
        MANAGED_HEADER,
        "# Base patterns (exFAT, OS metadata, agent scratch, secrets, runtime)",
    ]
    seen_patterns: Set[str] = set()
    for pat in BASE_PATTERNS:
        lines.append(pat)
        if not pat.startswith("#") and not pat.startswith("!"):
            seen_patterns.add(pat)

    for stack in sorted(stacks):
        pats = STACK_PATTERNS.get(stack, [])
        if pats:
            header_title = f"# Stack: {stack}"
            if stack == "learning":
                header_title += " (Constellation personal-data contract)"
            lines.append("")
            lines.append(header_title)
            for pat in pats:
                if pat not in seen_patterns:
                    lines.append(pat)
                    if not pat.startswith("#") and not pat.startswith("!"):
                        seen_patterns.add(pat)

    lines.append(MANAGED_FOOTER)
    return newline.join(lines) + newline


def detect_line_ending(content: str) -> str:
    """Detects \r\n (CRLF) or \n (LF) in content. Defaults to \n."""
    if "\r\n" in content:
        return "\r\n"
    return "\n"


def merge_managed_block(existing_content: str, managed_block: str, newline: str = "\n") -> str:
    """Inserts or replaces the SmartDrive managed block in .gitignore content.

    Preserves user lines outside the block and maintains original line endings.
    """
    if not existing_content:
        return managed_block

    if MANAGED_HEADER in existing_content and MANAGED_FOOTER in existing_content:
        start_idx = existing_content.find(MANAGED_HEADER)
        end_idx = existing_content.find(MANAGED_FOOTER) + len(MANAGED_FOOTER)

        # Consume any newline immediately after footer in original text
        if existing_content[end_idx:end_idx + 2] == "\r\n":
            end_idx += 2
        elif existing_content[end_idx:end_idx + 1] == "\n":
            end_idx += 1

        before = existing_content[:start_idx]
        after = existing_content[end_idx:]

        result = before + managed_block
        if after:
            result += after
        return result
    else:
        result = existing_content
        if not (result.endswith("\n") or result.endswith("\r")):
            result += newline
        if not result.endswith(newline + newline):
            result += newline
        result += managed_block
        return result


# -----------------------------------------------------------------------------
# Pattern matching engine
# -----------------------------------------------------------------------------

def pattern_matches_path(rel_path: str, pattern: str) -> bool:
    """Matches a git repository relative path against a gitignore pattern."""
    p = rel_path.replace("\\", "/").strip("/")
    pat = pattern.strip()
    if not pat or pat.startswith("#") or pat.startswith("!"):
        return False

    # Directory pattern (ends with '/')
    if pat.endswith("/"):
        dir_pat = pat[:-1]
        segments = p.split("/")
        if "/" not in dir_pat:
            # Matches any directory component matching dir_pat
            return any(fnmatch.fnmatchcase(seg, dir_pat) for seg in segments)
        else:
            if dir_pat.startswith("**/"):
                sub_pat = dir_pat[3:]
                if p.startswith(sub_pat + "/") or ("/" + sub_pat + "/") in p or p == sub_pat:
                    return True
                return (
                    fnmatch.fnmatchcase(p, f"*/{sub_pat}/*")
                    or fnmatch.fnmatchcase(p, f"{sub_pat}/*")
                    or fnmatch.fnmatchcase(p, f"*/{sub_pat}")
                    or fnmatch.fnmatchcase(p, sub_pat)
                )
            else:
                clean_dir = dir_pat.lstrip("/")
                return (
                    p.startswith(clean_dir + "/")
                    or p == clean_dir
                    or fnmatch.fnmatchcase(p, f"{clean_dir}/*")
                    or fnmatch.fnmatchcase(p, f"*/{clean_dir}/*")
                )

    # File pattern
    filename = p.split("/")[-1]
    if "/" not in pat:
        return fnmatch.fnmatchcase(filename, pat)
    else:
        if pat.startswith("**/"):
            sub_pat = pat[3:]
            if "/" not in sub_pat:
                return fnmatch.fnmatchcase(filename, sub_pat)
            else:
                return fnmatch.fnmatchcase(p, f"*/{sub_pat}") or fnmatch.fnmatchcase(p, sub_pat)
        else:
            clean_pat = pat.lstrip("/")
            return fnmatch.fnmatchcase(p, clean_pat) or fnmatch.fnmatchcase(p, f"*/{clean_pat}")


def check_path_against_patterns(rel_path: str, patterns: Sequence[str]) -> Optional[str]:
    """Returns the matching pattern if rel_path is ignored, or None if not ignored."""
    matched: Optional[str] = None
    for pat in patterns:
        pat_s = pat.strip()
        if not pat_s or pat_s.startswith("#"):
            continue
        if pat_s.startswith("!"):
            neg_pat = pat_s[1:]
            if pattern_matches_path(rel_path, neg_pat):
                matched = None
        else:
            if pattern_matches_path(rel_path, pat_s):
                matched = pat_s
    return matched


def is_personal_data_path(rel_path: str) -> bool:
    """Checks if a relative path matches the Constellation personal-data contract."""
    return check_path_against_patterns(rel_path, PERSONAL_DATA_PATTERNS) is not None


# -----------------------------------------------------------------------------
# Remote analysis (zero network calls)
# -----------------------------------------------------------------------------

def is_remote_possibly_public(remote_url: str) -> Tuple[bool, str]:
    """Determines if a remote URL host is public or unknown without network calls."""
    url = (remote_url or "").strip()
    if not url:
        return True, "no remote configured (treated as possibly public)"

    if url.startswith("file://") or os.path.isabs(url) or re.match(r"^[A-Za-z]:[\\/]", url):
        return False, "local filesystem repository"

    host = ""
    if "://" in url:
        after_scheme = url.split("://", 1)[1]
        host = after_scheme.split("/", 1)[0].split("@")[-1].split(":")[0].lower()
    elif "@" in url and ":" in url:
        after_at = url.split("@", 1)[1]
        host = after_at.split(":", 1)[0].lower()
    else:
        if "/" not in url and "\\" not in url:
            host = url.lower()
        else:
            host = url.split("/", 1)[0].split("\\", 1)[0].lower()

    if not host:
        return True, "unknown remote host (treated as possibly public)"

    if host in ("localhost", "127.0.0.1", "::1"):
        return False, f"local host ({host})"
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan"):
        return False, f"internal domain ({host})"

    return True, f"remote host ({host}) is public or possibly public"


def has_staged_changes(repo_path: str) -> bool:
    """Checks if the repository has any staged changes in the git index."""
    code, out, _ = _git(repo_path, "status", "--porcelain")
    if code != 0:
        return False
    for line in out.splitlines():
        if line and line[0] not in (" ", "?", "!"):
            return True
    return False


# -----------------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------------

@dataclass
class GitignoreAuditResult:
    repo: str
    has_gitignore: bool
    has_managed_block: bool
    stacks: List[str]
    missing_patterns: List[str]
    tracked_junk_count: int
    tracked_junk_by_pattern: Dict[str, int]
    tracked_junk_files: List[str]  # Top 20 files
    tracked_personal_data: List[str]
    remote: str
    is_possibly_public: bool
    personal_data_at_risk: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GitignoreApplyResult:
    repo: str
    dry_run: bool
    applied: bool
    diff: str
    gitignore_path: str
    created: bool
    stacks: List[str]
    managed_patterns_count: int
    untracked_files: List[str]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Core operations: audit and apply
# -----------------------------------------------------------------------------

def audit_repo(repo_path: str) -> GitignoreAuditResult:
    """Audits one repository for .gitignore compliance, tracked junk, and personal data."""
    repo_path = os.path.abspath(repo_path)
    gi_path = os.path.join(repo_path, ".gitignore")
    has_gitignore = os.path.exists(gi_path)
    has_managed_block = False
    existing_patterns: Set[str] = set()

    if has_gitignore:
        try:
            content = Path(gi_path).read_text(encoding="utf-8", errors="replace")
            has_managed_block = (MANAGED_HEADER in content and MANAGED_FOOTER in content)
            for line in content.splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    existing_patterns.add(s)
        except OSError:
            pass

    stacks = detect_stacks(repo_path)
    recommended = get_recommended_patterns(stacks)
    missing_patterns = [p for p in recommended if not p.startswith("#") and p not in existing_patterns]

    tracked_junk_count = 0
    tracked_junk_by_pattern: Dict[str, int] = collections.defaultdict(int)
    tracked_junk_files: List[str] = []
    tracked_personal_data: List[str] = []

    code, out, _ = _git(repo_path, "ls-files")
    if code == 0 and out.strip():
        for line in out.splitlines():
            f = line.strip()
            if not f:
                continue
            matched_pat = check_path_against_patterns(f, recommended)
            if matched_pat:
                tracked_junk_count += 1
                tracked_junk_by_pattern[matched_pat] += 1
                if len(tracked_junk_files) < 20:
                    tracked_junk_files.append(f)

            if is_personal_data_path(f):
                tracked_personal_data.append(f)

    # Remote detection
    code, out, _ = _git(repo_path, "config", "--get", "remote.origin.url")
    if code != 0 or not out.strip():
        code, out, _ = _git(repo_path, "config", "--get-regexp", r"^remote\..*\.url$")
        if code == 0 and out.strip():
            remote_url = out.splitlines()[0].split(" ", 1)[-1].strip()
        else:
            remote_url = ""
    else:
        remote_url = out.strip()

    is_public, _ = is_remote_possibly_public(remote_url)
    personal_data_at_risk = bool(tracked_personal_data and is_public)

    return GitignoreAuditResult(
        repo=repo_path,
        has_gitignore=has_gitignore,
        has_managed_block=has_managed_block,
        stacks=stacks,
        missing_patterns=missing_patterns,
        tracked_junk_count=tracked_junk_count,
        tracked_junk_by_pattern=dict(tracked_junk_by_pattern),
        tracked_junk_files=tracked_junk_files,
        tracked_personal_data=tracked_personal_data,
        remote=remote_url,
        is_possibly_public=is_public,
        personal_data_at_risk=personal_data_at_risk,
    )


def audit_all(root: Optional[str] = None) -> List[GitignoreAuditResult]:
    """Audits gitignore governance across all repositories discovered under root."""
    if not root:
        from smart_drive.core.root import resolve_drive_root
        resolved = resolve_drive_root()
        root = resolved.path

    from smart_drive.core.repos import find_repositories
    repo_paths = find_repositories([root])
    return [audit_repo(rp) for rp in repo_paths]


def apply_repo(repo_path: str, dry_run: bool = True, untrack: bool = False) -> GitignoreApplyResult:
    """Applies SmartDrive-managed .gitignore block and optionally untracks matching files.

    Refuses if untrack=True and repository has staged changes.
    """
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        raise ValueError(f"Not a directory: {repo_path}")

    # Refuse if untrack requested but staged changes exist
    if untrack and has_staged_changes(repo_path):
        raise ValueError("Refusing to untrack: repository has staged changes. Commit or unstage them before untracking.")

    stacks = detect_stacks(repo_path)
    gi_path = os.path.join(repo_path, ".gitignore")
    created = not os.path.exists(gi_path)
    old_content = ""
    newline = "\n"

    if not created:
        try:
            old_content = Path(gi_path).read_text(encoding="utf-8", errors="replace")
            newline = detect_line_ending(old_content)
        except OSError as e:
            raise ValueError(f"Cannot read .gitignore: {e}")

    managed_block = generate_managed_block(stacks, newline=newline)
    new_content = merge_managed_block(old_content, managed_block, newline=newline)

    # Diff calculation
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    from_file = ".gitignore (current)" if not created else "/dev/null"
    diff = "".join(difflib.unified_diff(old_lines, new_lines, fromfile=from_file, tofile=".gitignore (managed)"))

    # Check matching tracked files
    managed_patterns = get_recommended_patterns(stacks)
    tracked_matching: List[str] = []
    code, out, _ = _git(repo_path, "ls-files")
    if code == 0 and out.strip():
        for line in out.splitlines():
            f = line.strip()
            if f and check_path_against_patterns(f, managed_patterns):
                tracked_matching.append(f)

    applied = False
    if not dry_run:
        Path(gi_path).write_text(new_content, encoding="utf-8", errors="replace")
        applied = True
        if untrack and tracked_matching:
            batch_size = 50
            for i in range(0, len(tracked_matching), batch_size):
                chunk = tracked_matching[i:i + batch_size]
                code, _, err = _git(repo_path, "rm", "-r", "--cached", "--", *chunk)
                if code != 0:
                    raise RuntimeError(f"git rm --cached failed: {err}")

    return GitignoreApplyResult(
        repo=repo_path,
        dry_run=dry_run,
        applied=applied,
        diff=diff,
        gitignore_path=gi_path,
        created=created,
        stacks=stacks,
        managed_patterns_count=len(managed_patterns),
        untracked_files=tracked_matching if untrack else [],
    )
