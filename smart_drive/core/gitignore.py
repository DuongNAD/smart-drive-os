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
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from smart_drive.core.git_locks import NO_FSMONITOR

GIT_TIMEOUT = 30
GH_TIMEOUT = 5

MANAGED_HEADER = "# >>> smart-drive managed (do not edit inside) >>>"
MANAGED_FOOTER = "# <<< smart-drive managed <<<"

THIRD_PARTY_DIR_NAMES: FrozenSet[str] = frozenset({
    "open_source",
    "open-source",
    "vendor",
    "third_party",
    "third-party",
})

# Installed-dependency folders: files inside them are never intentional build outputs.
DEPENDENCY_DIR_NAMES: FrozenSet[str] = frozenset({
    "node_modules",
    "bower_components",
    "jspm_packages",
    ".venv",
    "venv",
    "site-packages",
    "__pypackages__",
})

_GH_AUTH_CACHE: Optional[bool] = None
_GH_VISIBILITY_CACHE: Dict[str, str] = {}

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
    # agent scratch (e.g. Antigravity teamwork runs); agent skills, workflows and rules stay tracked
    "**/.agents/*",
    "!**/.agents/skills/",
    "!**/.agents/workflows/",
    "!**/.agents/rules/",
    ".claude/settings.local.json",
    # secrets
    ".env",
    ".env.*",
    "!.env.example",
    "!.env.sample",
    "!.env.template",
    "*.pem",
    "*.key",
    "credentials*.json",
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
        ".polaris/",
        ".sirius/",
    ],
}

# Constellation personal-data contract patterns (STACK_PATTERNS["learning"] must stay equal; a test checks it)
PERSONAL_DATA_PATTERNS: List[str] = [
    ".vega/",
    "**/progress.jsonl",
    "aurora-progress-*.jsonl",
    "orion-attempts.jsonl",
    "**/polaris/.cache/",
    "**/polaris/captures/",
    ".polaris/",
    ".sirius/",
]

# Hub folders where the learning servers keep personal state (Vega cards, Sirius execution journal, Polaris hub);
# finding any of them marks a repository as the "learning" stack.
LEARNING_STATE_DIR_NAMES: FrozenSet[str] = frozenset({".vega", ".sirius", ".polaris"})


def _write_text_in_place(path: str, content: str) -> None:
    """Writes content byte for byte (its own line endings), into the existing file when there is one.

    On Windows, open(path, "w") asks for CREATE_ALWAYS, which is refused for a file with the hidden or system
    attribute (a hidden .gitignore made apply fail with "Permission denied"); rewriting the existing file in
    place works and keeps its attributes.
    """
    data = content.encode("utf-8", errors="replace")
    if os.path.isfile(path):
        with open(path, "r+b") as fh:
            fh.write(data)
            fh.truncate()
    else:
        with open(path, "wb") as fh:
            fh.write(data)


# -----------------------------------------------------------------------------
# Git subprocess helper
# -----------------------------------------------------------------------------

def _git(path: str, *args: str) -> Tuple[int, str, str]:
    """Runs a git command in path without locks, interactive prompts or the fsmonitor daemon."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        res = subprocess.run(
            ["git", *NO_FSMONITOR, "-C", path, *args],
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
                        if name_lower in LEARNING_STATE_DIR_NAMES:
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

        # Learning: course.json, deck.json, polaris/brief.json, .vega, .sirius, .polaris, and personal-data files
        if filename in ("course.json", "deck.json"):
            stacks.add("learning")
        elif filename == "brief.json" and "polaris" in parts:
            stacks.add("learning")
        elif not LEARNING_STATE_DIR_NAMES.isdisjoint(parts):
            stacks.add("learning")
        elif is_personal_data_path(rel_path):
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


def generate_managed_block(
    stacks: Sequence[str],
    newline: str = "\n",
    intentional_files: Optional[Sequence[str]] = None,
) -> str:
    """Builds the SmartDrive-managed .gitignore block for given stacks."""
    lines: List[str] = [
        MANAGED_HEADER,
        "# Base patterns (exFAT, OS metadata, agent scratch, secrets, runtime)",
    ]
    seen_patterns: Set[str] = set()

    def _resolve(patterns: Sequence[str]) -> List[str]:
        if not intentional_files:
            return list(patterns)
        resolved, _ = resolve_managed_patterns(patterns, intentional_files)
        return resolved

    base_resolved = _resolve(BASE_PATTERNS)
    for pat in base_resolved:
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
            stack_resolved = _resolve(pats)
            for pat in stack_resolved:
                if pat not in seen_patterns or pat.startswith("!"):
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
# Intentional build outputs & keep list
# -----------------------------------------------------------------------------

def find_nearest_package_json(repo_dir: str, file_rel_path: str) -> Optional[str]:
    """Finds the nearest package.json relative to repo_dir walking up from file directory."""
    norm_file = file_rel_path.replace("\\", "/").strip("/")
    curr = posixpath.dirname(norm_file)
    while True:
        candidate = os.path.join(repo_dir, curr, "package.json") if curr else os.path.join(repo_dir, "package.json")
        if os.path.isfile(candidate):
            return posixpath.join(curr, "package.json") if curr else "package.json"
        if not curr or curr == ".":
            break
        parent = posixpath.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return None


def extract_package_json_references(repo_dir: str, pkg_rel_path: str) -> Set[str]:
    """Extracts intentional build outputs referenced in nearest package.json.

    Supports bin (string or object values), main, module, types, and exports
    (all string values, nested objects included; normalise "./").
    """
    full_path = os.path.join(repo_dir, pkg_rel_path)
    pkg_dir = posixpath.dirname(pkg_rel_path.replace("\\", "/"))
    referenced: Set[str] = set()

    try:
        content = Path(full_path).read_text(encoding="utf-8", errors="replace")
        data = json.loads(content)
        if not isinstance(data, dict):
            return referenced
    except Exception:
        return referenced

    raw_candidates: List[str] = []

    # bin: string or object values
    bin_val = data.get("bin")
    if isinstance(bin_val, str):
        raw_candidates.append(bin_val)
    elif isinstance(bin_val, dict):
        for v in bin_val.values():
            if isinstance(v, str):
                raw_candidates.append(v)

    # main, module, types: strings
    for field_name in ("main", "module", "types"):
        val = data.get(field_name)
        if isinstance(val, str):
            raw_candidates.append(val)

    # exports: all string values, nested objects included
    exports_val = data.get("exports")
    if exports_val is not None:
        def _walk_exports(obj: Any) -> None:
            if isinstance(obj, str):
                raw_candidates.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _walk_exports(v)
            elif isinstance(obj, list):
                for item in obj:
                    _walk_exports(item)

        _walk_exports(exports_val)

    for raw in raw_candidates:
        s = raw.replace("\\", "/").strip()
        # Normalise leading "./"
        while s.startswith("./"):
            s = s[2:]
        s = s.lstrip("/")
        if not s:
            continue
        # Resolve relative to pkg_dir
        if pkg_dir and pkg_dir != ".":
            full_rel = posixpath.normpath(posixpath.join(pkg_dir, s))
        else:
            full_rel = posixpath.normpath(s)
        full_rel = full_rel.replace("\\", "/").strip("/")
        referenced.add(full_rel)

    return referenced


def find_intentional_files(
    repo_path: str,
    tracked_files: Sequence[str],
    keep_patterns: Optional[Sequence[str]] = None,
) -> Set[str]:
    """Identifies intentional build outputs and files that must never be untracked or counted as junk.

    A tracked file is intentional when:
    (a) referenced by the nearest package.json (bin, main, module, types, exports);
    (b) re-included by a negation in the repo's own .gitignore (outside managed block);
    (c) in a keep list: git config smartdrive.keep or --keep <pattern>.
    """
    repo_path = os.path.abspath(repo_path)
    tracked_norm = [f.replace("\\", "/").strip("/") for f in tracked_files if f.strip()]
    if not tracked_norm:
        return set()

    intentional: Set[str] = set()

    # (a) Nearest package.json references. Installed dependencies are never the repo's own
    # build output: inside node_modules every package.json points at its own dist/ files.
    pkg_cache: Dict[str, Optional[str]] = {}
    pkg_refs_cache: Dict[str, Set[str]] = {}

    for f in tracked_norm:
        if any(seg in DEPENDENCY_DIR_NAMES for seg in f.split("/")[:-1]):
            continue
        f_dir = posixpath.dirname(f)
        if f_dir not in pkg_cache:
            pkg_cache[f_dir] = find_nearest_package_json(repo_path, f)
        nearest_pkg = pkg_cache[f_dir]
        if nearest_pkg:
            if nearest_pkg not in pkg_refs_cache:
                pkg_refs_cache[nearest_pkg] = extract_package_json_references(repo_path, nearest_pkg)
            if f in pkg_refs_cache[nearest_pkg]:
                intentional.add(f)

    # (b) Re-included by a negation in repo's own .gitignore
    gi_path = os.path.join(repo_path, ".gitignore")
    if os.path.isfile(gi_path):
        try:
            gi_content = Path(gi_path).read_text(encoding="utf-8", errors="replace")
            if MANAGED_HEADER in gi_content and MANAGED_FOOTER in gi_content:
                s_idx = gi_content.find(MANAGED_HEADER)
                e_idx = gi_content.find(MANAGED_FOOTER) + len(MANAGED_FOOTER)
                user_content = gi_content[:s_idx] + "\n" + gi_content[e_idx:]
            else:
                user_content = gi_content

            neg_patterns: List[str] = []
            for line in user_content.splitlines():
                line_s = line.strip()
                if line_s.startswith("!"):
                    neg_pat = line_s[1:].strip()
                    if neg_pat:
                        neg_patterns.append(neg_pat)

            for neg_pat in neg_patterns:
                for f in tracked_norm:
                    if pattern_matches_path(f, neg_pat):
                        intentional.add(f)
        except OSError:
            pass

    # (c) Keep list: git config smartdrive.keep (multi-value) + keep_patterns
    git_keep: List[str] = []
    code, out, _ = _git(repo_path, "config", "--get-all", "smartdrive.keep")
    if code == 0 and out.strip():
        git_keep = [line.strip() for line in out.splitlines() if line.strip()]

    combined_keep = list(git_keep) + list(keep_patterns or [])
    for pat in combined_keep:
        pat_clean = pat.strip()
        if pat_clean.startswith("!"):
            pat_clean = pat_clean[1:].strip()
        if not pat_clean:
            continue
        for f in tracked_norm:
            if pattern_matches_path(f, pat_clean) or fnmatch.fnmatchcase(f, pat_clean):
                intentional.add(f)

    return intentional


def resolve_managed_patterns(
    recommended_patterns: Sequence[str],
    intentional_files: Sequence[str],
) -> Tuple[List[str], Dict[str, str]]:
    """Resolves recommended patterns against intentional build outputs.

    If a recommended pattern would ignore intentional file(s):
    - Uses `<dir>/*` followed by `!<file>` lines if every intentional file
      sits directly in that directory.
    - Otherwise omits that pattern for this repo and records the reason.
    """
    intentional_list = sorted(list(set(intentional_files)))
    if not intentional_list:
        return list(recommended_patterns), {}

    resolved: List[str] = []
    omitted: Dict[str, str] = {}

    for pat in recommended_patterns:
        pat_s = pat.strip()
        if not pat_s or pat_s.startswith("#") or pat_s.startswith("!"):
            resolved.append(pat)
            continue

        matching_intentional = [f for f in intentional_list if pattern_matches_path(f, pat_s)]
        if not matching_intentional:
            resolved.append(pat_s)
            continue

        if pat_s.endswith("/"):
            dir_name = pat_s.rstrip("/")
            all_directly_in_dir = all(
                posixpath.dirname(f) == dir_name
                for f in matching_intentional
            )
            if all_directly_in_dir:
                resolved.append(f"{dir_name}/*")
                for f in sorted(matching_intentional):
                    resolved.append(f"!{f}")
            else:
                nested_files = [
                    f for f in matching_intentional
                    if posixpath.dirname(f) != dir_name
                ]
                sample_nested = nested_files[0] if nested_files else matching_intentional[0]
                reason = f"contains nested intentional file: {sample_nested}"
                omitted[pat_s] = reason
        else:
            reason = f"matches intentional file: {matching_intentional[0]}"
            omitted[pat_s] = reason

    return resolved, omitted


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
    owner_class: str = "own"  # "own" | "third-party" | "local"
    visibility: str = "unknown"  # "public" | "private" | "internal" | "missing" | "unknown"
    tracked_secrets: List[Dict[str, Any]] = field(default_factory=list)
    intentional_files: List[str] = field(default_factory=list)
    omitted_patterns: Dict[str, str] = field(default_factory=dict)

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
    intentional_files: List[str] = field(default_factory=list)
    omitted_patterns: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# GitHub CLI (gh) & Visibility
# -----------------------------------------------------------------------------

def _run_gh(*args: str, timeout: int = GH_TIMEOUT) -> Tuple[int, str, str]:
    """Runs a gh CLI command without interactive prompts."""
    env = dict(os.environ, GH_PROMPT_DISABLED="1")
    try:
        res = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
        return res.returncode, res.stdout, res.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def check_gh_authenticated(reset_cache: bool = False) -> bool:
    """Checks whether the GitHub CLI (gh) is installed and authenticated."""
    global _GH_AUTH_CACHE
    if reset_cache or _GH_AUTH_CACHE is None:
        if shutil.which("gh") is None:
            _GH_AUTH_CACHE = False
        else:
            code, _, _ = _run_gh("auth", "status", timeout=GH_TIMEOUT)
            _GH_AUTH_CACHE = (code == 0)
    return bool(_GH_AUTH_CACHE)


def query_gh_visibility(owner_repo: str, timeout: int = GH_TIMEOUT) -> str:
    """Queries repository visibility via gh CLI ('public', 'private', 'internal', 'missing', or 'unknown')."""
    global _GH_VISIBILITY_CACHE
    if owner_repo in _GH_VISIBILITY_CACHE:
        return _GH_VISIBILITY_CACHE[owner_repo]

    if not check_gh_authenticated():
        _GH_VISIBILITY_CACHE[owner_repo] = "unknown"
        return "unknown"

    code, out, err = _run_gh("repo", "view", owner_repo, "--json", "visibility", timeout=timeout)
    if code == 0 and out.strip():
        try:
            data = json.loads(out)
            vis = str(data.get("visibility", "")).strip().lower()
            if vis in ("public", "private", "internal"):
                _GH_VISIBILITY_CACHE[owner_repo] = vis
                return vis
        except Exception:
            pass
    else:
        combined = (err + " " + out).lower()
        if "could not resolve to a repository" in combined or "not found" in combined:
            _GH_VISIBILITY_CACHE[owner_repo] = "missing"
            return "missing"

    _GH_VISIBILITY_CACHE[owner_repo] = "unknown"
    return "unknown"


def clear_gh_cache() -> None:
    """Clears gh authentication and visibility caches (useful for testing)."""
    global _GH_AUTH_CACHE, _GH_VISIBILITY_CACHE
    _GH_AUTH_CACHE = None
    _GH_VISIBILITY_CACHE.clear()


# -----------------------------------------------------------------------------
# Ownership & Secret Detection Helpers
# -----------------------------------------------------------------------------

def extract_remote_owner(remote_url: str) -> Optional[str]:
    """Extracts remote repository owner/organization from a remote URL."""
    url = (remote_url or "").strip()
    if not url:
        return None
    if url.startswith("file://") or os.path.isabs(url) or re.match(r"^[A-Za-z]:[\\/]", url):
        return None

    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url)
    u = re.sub(r"^[^@/]+@", "", u)
    u = re.sub(r"^([^/:]+):(\d+)/", r"\1/", u)
    u = re.sub(r"^([^/:]+):", r"\1/", u)
    parts = [p for p in u.replace("\\", "/").split("/") if p]
    if len(parts) >= 2:
        owner = parts[1]
        if owner.endswith(".git"):
            owner = owner[:-4]
        return owner if owner else None
    return None


def extract_github_owner_repo(remote_url: str) -> Optional[str]:
    """Extracts owner/repo for GitHub hosted remotes (e.g. DuongNAD/smart-drive-os)."""
    url = (remote_url or "").strip()
    if not url:
        return None
    if url.startswith("file://") or os.path.isabs(url) or re.match(r"^[A-Za-z]:[\\/]", url):
        return None

    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url)
    u = re.sub(r"^[^@/]+@", "", u)
    u = re.sub(r"^([^/:]+):(\d+)/", r"\1/", u)
    u = re.sub(r"^([^/:]+):", r"\1/", u)
    parts = [p for p in u.replace("\\", "/").split("/") if p]
    if len(parts) >= 3 and "github.com" in parts[0].lower():
        owner = parts[1]
        repo_name = parts[2]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        return f"{owner}/{repo_name}"
    return None


def is_third_party_path(repo_path: str) -> bool:
    """True if repo_path contains a folder named open_source, vendor, or third_party."""
    norm = os.path.normpath(repo_path).replace("\\", "/")
    segments = [s.lower() for s in norm.split("/") if s]
    return any(seg in THIRD_PARTY_DIR_NAMES for seg in segments)


def get_configured_git_owner(
    repo_path: Optional[str] = None,
    drive_root: Optional[str] = None,
) -> Optional[str]:
    """Resolves configured git owner from environment variable, git config, or drive config."""
    # 1. Environment variable
    env_owner = os.environ.get("SMART_DRIVE_GIT_OWNER")
    if env_owner and env_owner.strip():
        return env_owner.strip()

    # 2. Git config smartdrive.owner
    if repo_path and os.path.isdir(repo_path):
        code, out, _ = _git(repo_path, "config", "--get", "smartdrive.owner")
        if code == 0 and out.strip():
            return out.strip()
    code, out, _ = _git(".", "config", "--global", "--get", "smartdrive.owner")
    if code == 0 and out.strip():
        return out.strip()

    # 3. Drive config file (.smart_drive/config.json)
    if drive_root and os.path.isdir(drive_root):
        cfg_file = os.path.join(drive_root, ".smart_drive", "config.json")
        if os.path.isfile(cfg_file):
            try:
                data = json.loads(Path(cfg_file).read_text(encoding="utf-8", errors="replace"))
                cfg_owner = data.get("git_owner") or data.get("owner")
                if cfg_owner and str(cfg_owner).strip():
                    return str(cfg_owner).strip()
            except Exception:
                pass

    return None


def is_secret_path(rel_path: str) -> bool:
    """True if rel_path matches .env-like or secret credential files."""
    norm = rel_path.replace("\\", "/")
    filename = norm.split("/")[-1].lower()

    # Exclude templates and examples
    if (
        filename in (".env.example", ".env.sample", ".env.template")
        or filename.endswith(".example")
        or filename.endswith(".template")
    ):
        return False

    if filename == ".env" or filename.startswith(".env."):
        return True
    if filename.endswith(".pem") or filename.endswith(".key"):
        return True
    if fnmatch.fnmatchcase(filename, "credentials*.json"):
        return True
    return False


def extract_secret_var_names(full_path: str, filename: Optional[str] = None) -> List[str]:
    """Extracts variable or key names only (never values) from secret files."""
    if not os.path.isfile(full_path):
        return []

    if filename is None:
        filename = os.path.basename(full_path)

    fn_low = filename.lower()
    var_names: List[str] = []

    try:
        if fn_low == ".env" or fn_low.startswith(".env."):
            content = Path(full_path).read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" in line:
                    k = line.split("=", 1)[0].strip()
                    if k and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                        var_names.append(k)
        elif fnmatch.fnmatchcase(fn_low, "credentials*.json"):
            content = Path(full_path).read_text(encoding="utf-8", errors="replace")
            data = json.loads(content)
            if isinstance(data, dict):
                for k in data.keys():
                    if isinstance(k, str) and k.strip():
                        var_names.append(k.strip())
    except Exception:
        pass

    return list(dict.fromkeys(var_names))


# -----------------------------------------------------------------------------
# Core operations: audit and apply
# -----------------------------------------------------------------------------

def audit_repo(
    repo_path: str,
    configured_owner: Optional[str] = None,
    include_third_party_junk: bool = False,
    keep_patterns: Optional[Sequence[str]] = None,
) -> GitignoreAuditResult:
    """Audits one repository for .gitignore compliance, tracked junk, personal data, and secrets."""
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

    # Ownership classification: own, third-party, local
    if is_third_party_path(repo_path):
        owner_class = "third-party"
    elif not remote_url or remote_url.startswith("file://") or os.path.isabs(remote_url) or re.match(r"^[A-Za-z]:[\\/]", remote_url):
        owner_class = "local"
    else:
        remote_owner = extract_remote_owner(remote_url)
        cfg_owner = configured_owner or get_configured_git_owner(repo_path)
        if cfg_owner and remote_owner and remote_owner.lower() == cfg_owner.lower():
            owner_class = "own"
        elif cfg_owner and remote_owner:
            owner_class = "third-party"
        else:
            owner_class = "own"

    # Visibility detection (gh queried only for own repos)
    visibility = "unknown"
    if owner_class == "own":
        gh_owner_repo = extract_github_owner_repo(remote_url)
        if gh_owner_repo:
            visibility = query_gh_visibility(gh_owner_repo)

    if visibility == "public":
        is_public = True
    elif visibility in ("private", "internal", "missing"):
        is_public = False
    else:
        is_public, _ = is_remote_possibly_public(remote_url)

    # Tracked files & intentional files detection
    code, out, _ = _git(repo_path, "ls-files")
    tracked_files: List[str] = []
    if code == 0 and out.strip():
        tracked_files = [f.strip() for f in out.splitlines() if f.strip()]

    intentional_set = find_intentional_files(repo_path, tracked_files, keep_patterns=keep_patterns)
    intentional_files = sorted(list(intentional_set))

    effective_patterns, omitted_patterns = resolve_managed_patterns(recommended, intentional_files)

    # Missing patterns check (third-party repos manage their own .gitignore)
    if owner_class == "third-party" and not include_third_party_junk:
        missing_patterns = []
    else:
        missing_patterns = [p for p in effective_patterns if not p.startswith("#") and p not in existing_patterns]

    tracked_junk_count = 0
    tracked_junk_by_pattern: Dict[str, int] = collections.defaultdict(int)
    tracked_junk_files: List[str] = []
    tracked_personal_data: List[str] = []
    tracked_secrets: List[Dict[str, Any]] = []

    for f in tracked_files:
        if f in intentional_set:
            continue

        # Tracked secrets: .env, .env.*, *.pem, *.key, credentials*.json
        if is_secret_path(f):
            full_f = os.path.join(repo_path, f)
            vnames = extract_secret_var_names(full_f, os.path.basename(f))
            tracked_secrets.append({"path": f, "var_names": vnames, "vars": vnames})

        # Personal learning data
        if is_personal_data_path(f):
            tracked_personal_data.append(f)

        # Tracked junk: intentional files are never counted as junk
        if owner_class != "third-party" or include_third_party_junk:
            if not is_secret_path(f):
                matched_pat = check_path_against_patterns(f, effective_patterns)
                if matched_pat:
                    tracked_junk_count += 1
                    tracked_junk_by_pattern[matched_pat] += 1
                    if len(tracked_junk_files) < 20:
                        tracked_junk_files.append(f)

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
        owner_class=owner_class,
        visibility=visibility,
        tracked_secrets=tracked_secrets,
        intentional_files=intentional_files,
        omitted_patterns=omitted_patterns,
    )


def audit_all(
    root: Optional[str] = None,
    configured_owner: Optional[str] = None,
    include_third_party_junk: bool = False,
    keep_patterns: Optional[Sequence[str]] = None,
) -> List[GitignoreAuditResult]:
    """Audits gitignore governance across all repositories discovered under root."""
    if not root:
        from smart_drive.core.root import resolve_drive_root
        resolved = resolve_drive_root()
        root = resolved.path

    from smart_drive.core.repos import find_repositories
    repo_paths = find_repositories([root])

    # Infer owner if not explicitly passed
    cfg_owner = configured_owner or get_configured_git_owner(drive_root=root)
    if not cfg_owner and repo_paths:
        remote_owners: List[str] = []
        for rp in repo_paths:
            code, out, _ = _git(rp, "config", "--get", "remote.origin.url")
            if code == 0 and out.strip():
                o = extract_remote_owner(out.strip())
                if o:
                    remote_owners.append(o)
        if remote_owners:
            counter = collections.Counter(remote_owners)
            cfg_owner = counter.most_common(1)[0][0]

    return [
        audit_repo(
            rp,
            configured_owner=cfg_owner,
            include_third_party_junk=include_third_party_junk,
            keep_patterns=keep_patterns,
        )
        for rp in repo_paths
    ]


def apply_repo(
    repo_path: str,
    dry_run: bool = True,
    untrack: bool = False,
    keep_patterns: Optional[Sequence[str]] = None,
) -> GitignoreApplyResult:
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
            # newline="" keeps "\r\n" so detect_line_ending sees the file's real line endings.
            with open(gi_path, encoding="utf-8", errors="replace", newline="") as fh:
                old_content = fh.read()
            newline = detect_line_ending(old_content)
        except OSError as e:
            raise ValueError(f"Cannot read .gitignore: {e}")

    code, out, _ = _git(repo_path, "ls-files")
    tracked_files: List[str] = []
    if code == 0 and out.strip():
        tracked_files = [f.strip() for f in out.splitlines() if f.strip()]

    intentional_set = find_intentional_files(repo_path, tracked_files, keep_patterns=keep_patterns)
    intentional_files = sorted(list(intentional_set))

    recommended = get_recommended_patterns(stacks)
    effective_patterns, omitted_patterns = resolve_managed_patterns(recommended, intentional_files)

    managed_block = generate_managed_block(stacks, newline=newline, intentional_files=intentional_files)
    new_content = merge_managed_block(old_content, managed_block, newline=newline)

    # Diff calculation
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    from_file = ".gitignore (current)" if not created else "/dev/null"
    diff = "".join(difflib.unified_diff(old_lines, new_lines, fromfile=from_file, tofile=".gitignore (managed)"))

    # Check matching tracked files (intentional files are never untracked)
    tracked_matching: List[str] = []
    for f in tracked_files:
        if f in intentional_set:
            continue
        if check_path_against_patterns(f, effective_patterns) or is_secret_path(f):
            tracked_matching.append(f)

    applied = False
    if not dry_run:
        _write_text_in_place(gi_path, new_content)
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
        managed_patterns_count=len(effective_patterns),
        untracked_files=tracked_matching if untrack else [],
        intentional_files=intentional_files,
        omitted_patterns=omitted_patterns,
    )


__all__ = [
    "BASE_PATTERNS",
    "STACK_PATTERNS",
    "PERSONAL_DATA_PATTERNS",
    "MANAGED_HEADER",
    "MANAGED_FOOTER",
    "THIRD_PARTY_DIR_NAMES",
    "GitignoreAuditResult",
    "GitignoreApplyResult",
    "detect_stacks",
    "get_recommended_patterns",
    "generate_managed_block",
    "detect_line_ending",
    "merge_managed_block",
    "pattern_matches_path",
    "check_path_against_patterns",
    "is_personal_data_path",
    "is_remote_possibly_public",
    "has_staged_changes",
    "check_gh_authenticated",
    "query_gh_visibility",
    "clear_gh_cache",
    "extract_remote_owner",
    "extract_github_owner_repo",
    "is_third_party_path",
    "get_configured_git_owner",
    "is_secret_path",
    "extract_secret_var_names",
    "audit_repo",
    "audit_all",
    "apply_repo",
    "find_intentional_files",
    "find_nearest_package_json",
    "extract_package_json_references",
    "resolve_managed_patterns",
    "_run_gh",
]

