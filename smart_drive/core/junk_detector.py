"""smart_drive.core.junk_detector - 3-Tier Filesystem Junk Pattern Matcher & Classifier.

Designed specifically for external SSDs (Kingston XS2000 2TB exFAT).
Matches macOS artifacts, Windows thumbnails & recycle bins, Python bytecode,
developer test/build caches, and temporary/crash files.
Guarantees zero-destruction of anti-indexing shields (.metadata_never_index,
.fseventsd/no_log) and inviolable root scripts/taxonomies.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

from smart_drive.core.config import (
    ANTI_INDEXING_FSEVENT_DIR,
    ANTI_INDEXING_FSEVENT_FILE,
    ANTI_INDEXING_ROOT_FILE,
    CLUSTER_SIZE_BYTES,
    JUNK_RULES,
    PROJECT_MANIFEST_DIRS,
    JunkRule,
    JunkTier,
    calculate_allocated_bytes,
    is_inside_dependency_tree,
    is_project_manifest,
    is_protected_root_dir,
    is_protected_root_file,
    match_junk_rule,
)
from smart_drive.core.learning_units import find_unit_root
from smart_drive.core.scanner import FastDirectoryScanner, ScanEntry, ScanOptions, measure_tree
from smart_drive.core.system_refs import (
    SystemRef,
    SystemRefIndex,
    format_protected_by_system_entry,
    summarize_system_refs,
)

# Why an entry that looks like junk was kept (JunkDetector.kept maps each reason to how many entries it kept)
KEPT_DEPENDENCY_TREE = "dependency_tree"  # inside node_modules, site-packages...: installed packages, not leftovers
KEPT_SYSTEM_REFERENCED = "system_referenced"  # in a folder Windows or an installed application depends on
KEPT_NOT_PROJECT_ROOT = "not_project_root"  # a build/dist/.cache folder with no project manifest beside it
KEPT_GIT_TRACKED = "git_tracked"  # git tracks files in it: it is part of a repository, not an untracked leftover
KEPT_GIT_UNVERIFIED = "git_unverified"  # inside a git work tree that git could not be asked about

GIT_TIMEOUT = 20  # seconds for one `git ls-files`
# Set when this runs from a git hook; they would send `git -C <folder>` to the wrong repository
_GIT_ENV_UNSET = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")


def _holds_git_repository(folder: str) -> bool:
    """Whether ``folder`` has a `.git` that git itself would take for a repository.

    A `.git` file points at a repository kept elsewhere (linked worktrees, submodules). A `.git` folder counts when
    it has what git's own check asks for: a HEAD file and `objects` and `refs` folders. A stray `.git/HEAD` alone,
    as a test or a half-deleted clone leaves behind, is not one.
    """
    dot_git = os.path.join(folder, ".git")
    if not os.path.lexists(dot_git):
        return False
    if os.path.isfile(dot_git):
        return True
    return (
        os.path.isfile(os.path.join(dot_git, "HEAD"))
        and os.path.isdir(os.path.join(dot_git, "objects"))
        and os.path.isdir(os.path.join(dot_git, "refs"))
    )


def _git_tracks(folder: str, name: Optional[str] = None) -> Optional[bool]:
    """Whether git tracks any file in ``folder`` (or, given ``name``, at or under ``folder/name``).

    None when git could not say: it is not installed, refused (a repository owned by another user, a damaged
    index) or timed out. Untracked and ignored files are not "tracked", so a regenerable folder that
    .gitignore hides is not one.
    """
    git = shutil.which("git")
    if git is None:
        return None
    env = {key: value for key, value in os.environ.items() if key not in _GIT_ENV_UNSET}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    command = [git, "--literal-pathspecs", "-C", folder, "ls-files", "-z"]
    if name is not None:
        command += ["--", name]
    try:
        done = subprocess.run(
            command, capture_output=True, stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT, env=env, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return bool(done.stdout)


def _drop_items_inside_junk_dirs(items: List["JunkItem"]) -> List["JunkItem"]:
    """A junk directory is purged as a whole: drop the items that sit inside another reported directory.

    The scanner does not prune detected junk directories, and on POSIX the entries inside one match its
    rule too, so `.pytest_cache` and `.pytest_cache/pkg` were both reported, sized and "deleted".
    """
    junk_dirs = {item.path for item in items if item.is_dir}
    if not junk_dirs:
        return items

    def inside_junk_dir(path: str) -> bool:
        child, parent = path, os.path.dirname(path)
        while parent and parent != child:
            if parent in junk_dirs:
                return True
            child, parent = parent, os.path.dirname(parent)
        return False

    return [item for item in items if not inside_junk_dir(item.path)]


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

# dataclass() only accepts `slots` on Python 3.10+; even slots=False raises TypeError on 3.9.
_SLOTS_KWARGS = {"slots": True} if sys.version_info >= (3, 10) else {}


@dataclass(**_SLOTS_KWARGS)
class JunkItem:
    """Represents a filesystem entry identified as system or developer junk.

    Supports both object attribute access (.rel_path, .name) and dictionary
    subscripting (item["rel_path"], item["name"]) for backward compatibility.
    """
    path: str
    rel_path: str
    name: str
    size: int
    allocated_size: int
    is_dir: bool
    tier: JunkTier
    description: str
    os_source: str

    def __getitem__(self, key: str) -> Any:
        """Enables dict-like subscripting (e.g. item['rel_path'], item['name'])."""
        if key == "rule":
            return self.description
        if hasattr(self, key):
            val = getattr(self, key)
            if key == "tier" and isinstance(val, JunkTier):
                return int(val)
            return val
        raise KeyError(f"JunkItem has no attribute or key '{key}'")

    def __contains__(self, key: str) -> bool:
        return key == "rule" or hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_dict(self) -> Dict[str, Any]:
        """Convert JunkItem to JSON-serializable dictionary."""
        d = asdict(self)
        d["tier"] = int(self.tier)
        d["rule"] = self.description
        return d


# ---------------------------------------------------------------------------
# Junk Detector Engine
# ---------------------------------------------------------------------------

class JunkDetector:
    """High-speed declarative pattern matcher and classifier for storage junk.

    Capabilities:
    1. 3-Tier risk categorization (Tier 1: Safe, Tier 2: Dev caches, Tier 3: Logs/Dumps).
    2. macOS AppleDouble (._*) and .DS_Store detection.
    3. Windows Thumbs.db, ehthumbs, and $RECYCLE.BIN detection.
    4. Anti-indexing shield protection (.metadata_never_index, no_log).
    5. Inviolable root file and root directory protection.
    6. Hardware-accurate 512KB cluster allocation math.
    7. Entries that only look like junk are kept, and counted by reason in ``kept``: Tier 2-3 matches inside
       dependency trees (node_modules, site-packages...) are installed packages, not leftovers, and nothing
       of any tier is reported inside a folder that Windows or an installed application depends on (the same
       SystemRefIndex that keeps ``organize`` and ``classify`` from moving it), and a ``build``, ``dist`` or
       ``.cache`` folder counts only at a project root (a manifest such as package.json lies beside it), and
       a Tier 2-3 entry that git tracks is never reported (git is asked only for those that survive the other checks).

    ``system_refs`` is that index; without one it is collected from this computer the first time an entry needs
    it (a scan that finds nothing that looks like junk never reads the registry).
    """

    def __init__(
        self,
        drive_root: str,
        max_tier: JunkTier = JunkTier.TIER_3_SENSITIVE,
        cluster_size: int = CLUSTER_SIZE_BYTES,
        system_refs: Optional[SystemRefIndex] = None,
    ) -> None:
        self.drive_root = os.path.realpath(os.path.abspath(drive_root))
        self.max_tier = max_tier
        self.cluster_size = cluster_size
        self._name_matchers, self._always_check = self._compile_name_matchers(max_tier)
        self._system_refs: Optional[SystemRefIndex] = system_refs
        # The drive root and everything above it is never judged: a drive root on PATH would protect the whole drive
        self._root_key = os.path.normcase(self.drive_root)
        self._below_root_prefix = self._root_key if self._root_key.endswith(os.sep) else self._root_key + os.sep
        # Folder (normcase) -> (outermost folder from the drive root down to it that the system depends on, its refs)
        self._folder_protection: Dict[str, Optional[Tuple[str, List[SystemRef]]]] = {}
        # Folder (normcase) -> whether it directly holds a project manifest
        self._manifest_cache: Dict[str, bool] = {}
        # Folder (normcase) -> whether it is, or lies in, a git work tree
        self._work_tree_cache: Dict[str, bool] = {}
        # Entries that matched a junk rule by name but were kept anyway: reason -> count (reset by find_junk)
        self.kept: Dict[str, int] = {}
        self._entry_kept_reason: Optional[str] = None  # why the entry inspected last was kept, if it was
        # The folders the system depends on that kept entries (reset by find_junk), as organize/classify list them
        self.protected_by_system: List[Dict[str, Any]] = []
        self._protected_entries: Dict[str, Dict[str, Any]] = {}

    @property
    def system_refs(self) -> SystemRefIndex:
        """Lazily collects OS/application references once per JunkDetector instance."""
        if self._system_refs is None:
            self._system_refs = SystemRefIndex.collect(shortcut_roots=[self.drive_root])
        return self._system_refs

    @staticmethod
    def _compile_name_matchers(max_tier: JunkTier) -> Tuple[Dict[bool, Optional["re.Pattern[str]"]], Dict[bool, bool]]:
        """Per entry kind, one regex that every name a junk rule could match must satisfy.

        This is only a filter in front of `match_junk_rule`, which still decides: it lets the rest of
        `inspect_file` (several path walks and a learning-unit lookup, i.e. filesystem calls) be skipped for the
        ~99% of entries whose name no rule can match. A path rule (`polaris/.cache`) can only match an entry named
        like its last component; a path rule whose last component is a wildcard cannot be filtered that way, so
        for that kind of entry every name is let through.
        """
        patterns: Dict[bool, List[str]] = {False: [], True: []}
        always_check = {False: False, True: False}
        for rule in JUNK_RULES:
            if rule.tier > max_tier:
                continue
            pattern = rule.pattern.lower()
            if "/" in pattern:
                tail = pattern.rsplit("/", 1)[1]
                if any(ch in tail for ch in "*?["):
                    always_check[rule.is_directory] = True
                else:
                    patterns[rule.is_directory].append(re.escape(tail))
            else:
                patterns[rule.is_directory].append(fnmatch.translate(pattern))
        compiled = {kind: (re.compile("|".join(found)) if found else None) for kind, found in patterns.items()}
        return compiled, always_check

    def _may_be_junk(self, name: str, is_dir: bool, norm_rel: str) -> bool:
        """False only when no junk rule and no Polaris-cache exception can apply to this name and path."""
        if self._always_check[is_dir]:
            return True
        matcher = self._name_matchers[is_dir]
        if matcher is not None and matcher.match(name.lower()):
            return True
        # Inside a learning unit, everything under polaris/.cache is junk whatever its name.
        return "polaris/.cache" in norm_rel.lower()

    def _normalize_rel_path(self, target_path: str) -> str:
        """Computes clean relative path from drive root using forward slashes."""
        try:
            rel = os.path.relpath(target_path, self.drive_root)
            if rel == ".":
                return ""
            return rel.replace("\\", "/")
        except ValueError:
            return target_path.replace("\\", "/")

    def _protecting_folder(self, folder: str) -> Optional[Tuple[str, List[SystemRef]]]:
        """The outermost folder, below the drive root and down to ``folder`` itself, that the system depends on.

        "Depends on" is what keeps ``organize`` and ``classify`` from moving a folder: something Windows or an
        application starts or reads lies in it (a PATH entry, a service image, an installed app's files, a
        shortcut's target...), or it holds an uninstaller. Everything inside such a folder belongs to that
        dependency, so the first one met on the way down decides. Answers are kept for the whole scan: it asks
        about the same folders again and again, and each fresh one costs a listing of the folder.
        """
        cache = self._folder_protection
        pending: List[Tuple[str, str]] = []  # folders without an answer yet, innermost first
        outer: Optional[Tuple[str, List[SystemRef]]] = None
        current = folder
        while True:
            key = os.path.normcase(current)
            if key in cache:
                outer = cache[key]
                break
            if key == self._root_key or not key.startswith(self._below_root_prefix):
                break
            pending.append((current, key))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        for current, key in reversed(pending):  # outermost first
            if outer is None:
                refs = self.system_refs.check_folder(current, is_file=False)
                if refs:
                    outer = (current, refs)
            cache[key] = outer
        return outer

    def _system_dependency(self, path: str, is_dir: bool) -> Optional[Tuple[str, List[SystemRef]]]:
        """The folder the system depends on that ``path`` is, or lies in, with the references that say so."""
        if is_dir:
            return self._protecting_folder(path)
        found = self._protecting_folder(os.path.dirname(path))
        if found is None:
            refs = self.system_refs.check_folder(path, is_file=True)  # a file that is itself referenced
            if refs:
                found = (path, refs)
        return found

    def _has_project_manifest(self, folder: str) -> bool:
        """Whether ``folder`` directly holds a project manifest (package.json, pyproject.toml...); listed once per folder.

        A folder that cannot be listed is not taken for a project root: nothing is reported on that evidence.
        """
        key = os.path.normcase(folder)
        found = self._manifest_cache.get(key)
        if found is None:
            found = False
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if is_project_manifest(entry.name) or (
                            entry.name.lower() in PROJECT_MANIFEST_DIRS and entry.is_dir()
                        ):
                            found = True
                            break
            except OSError:
                pass
            self._manifest_cache[key] = found
        return found

    def _in_git_work_tree(self, folder: str) -> bool:
        """Whether ``folder`` is, or lies in, a git work tree (a repository's `.git` here or in a folder above)."""
        cache = self._work_tree_cache
        pending: List[str] = []
        found = False
        current = folder
        while True:
            key = os.path.normcase(current)
            if key in cache:
                found = cache[key]
                break
            pending.append(key)
            if _holds_git_repository(current):
                found = True
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        for key in pending:
            cache[key] = found
        return found

    def _git_keep_reason(self, path: str, is_dir: bool) -> Optional[str]:
        """Whether git has a stake in ``path``: the files it tracks are the project's, not leftovers.

        Asked only for the few entries that survive every other check, and only inside a git work tree. When git
        cannot be asked, an entry in a work tree is kept: a wrong guess here would delete tracked work.
        """
        if is_dir and _holds_git_repository(path):
            return KEPT_GIT_TRACKED  # a repository of its own
        parent = os.path.dirname(path)
        if not self._in_git_work_tree(parent):
            return None
        tracked = _git_tracks(path) if is_dir else _git_tracks(parent, os.path.basename(path))
        if tracked is None:
            return KEPT_GIT_UNVERIFIED
        return KEPT_GIT_TRACKED if tracked else None

    def _keep_reason(self, path: str, tier: JunkTier, is_dir: bool, rule: Optional[JunkRule] = None) -> Optional[str]:
        """Why an entry whose name matches ``rule`` (of ``tier``) is still not junk, or None when it is."""
        if tier >= JunkTier.TIER_2_DEV_CACHE and is_inside_dependency_tree(path):
            return KEPT_DEPENDENCY_TREE
        dependency = self._system_dependency(path, is_dir)
        if dependency is not None:
            folder, refs = dependency
            entry = self._protected_entries.get(os.path.normcase(folder))
            if entry is None:
                entry = format_protected_by_system_entry(folder, refs)
                entry["used_by"] = summarize_system_refs(refs)
                entry["kept_items"] = 0
                self._protected_entries[os.path.normcase(folder)] = entry
                self.protected_by_system.append(entry)
            entry["kept_items"] += 1
            return KEPT_SYSTEM_REFERENCED
        if rule is not None and rule.project_root_only and not self._has_project_manifest(os.path.dirname(path)):
            return KEPT_NOT_PROJECT_ROOT
        if tier >= JunkTier.TIER_2_DEV_CACHE:
            return self._git_keep_reason(path, is_dir)
        return None

    def _note_if_kept(self, path: str, tier: JunkTier, is_dir: bool, rule: Optional[JunkRule] = None) -> bool:
        """Applies the location checks to a name match; True (and counted in ``kept``) when the entry is to be kept."""
        reason = self._keep_reason(path, tier, is_dir, rule)
        if reason is None:
            return False
        self.kept[reason] = self.kept.get(reason, 0) + 1
        self._entry_kept_reason = reason
        return True

    def inspect_file(
        self,
        path: str,
        name: str,
        size: int,
        is_dir: bool,
        rel_path: Optional[str] = None,
    ) -> Optional[JunkItem]:
        """Inspects an individual file or directory against junk rules and safeguards.

        Args:
            path: Absolute filesystem path.
            name: Filename or directory basename.
            size: Nominal size in bytes.
            is_dir: True if directory, False if regular file.
            rel_path: Optional relative path from drive root.

        Returns:
            JunkItem if identified as junk within max_tier and not protected; else None.
        """
        self._entry_kept_reason = None
        clean_name = name.strip()
        norm_rel = rel_path if rel_path is not None else self._normalize_rel_path(path)
        if not self._may_be_junk(clean_name, is_dir, norm_rel):
            return None
        parts = norm_rel.split("/") if norm_rel else []

        # ----------------------------------------------------------------------
        # HARD SAFEGUARD 1: Anti-Indexing Shields (NEVER JUNK)
        # ----------------------------------------------------------------------
        if clean_name in {ANTI_INDEXING_ROOT_FILE, "no_log"}:
            return None
        if norm_rel in {ANTI_INDEXING_ROOT_FILE, ANTI_INDEXING_FSEVENT_FILE}:
            return None
        if parts and parts[0].lower() == ANTI_INDEXING_FSEVENT_DIR.lower():
            if len(parts) == 1 or parts[1].lower() == "no_log":
                return None

        # ----------------------------------------------------------------------
        # HARD SAFEGUARD 2: Inviolable Root Files and Taxonomies
        # ----------------------------------------------------------------------
        is_root_level = len(parts) <= 1
        if is_root_level:
            if is_dir and is_protected_root_dir(clean_name):
                return None
            if not is_dir and is_protected_root_file(clean_name):
                return None
        elif norm_rel and is_protected_root_dir(norm_rel):
            return None

        # ----------------------------------------------------------------------
        # HARD SAFEGUARD 3: Learning Unit Protection (Aurora & Polaris)
        # ----------------------------------------------------------------------
        unit_root = find_unit_root(path, self.drive_root)
        if unit_root is not None:
            # Everything inside a unit is protected, EXCEPT polaris/.cache/**
            norm_fwd = norm_rel.lower().replace("\\", "/")
            is_polaris_cache = (
                "/polaris/.cache" in norm_fwd
                or norm_fwd.startswith("polaris/.cache")
                or norm_fwd.endswith("/polaris/.cache")
                or norm_fwd == "polaris/.cache"
            )
            if is_polaris_cache:
                if self.max_tier >= JunkTier.TIER_2_DEV_CACHE:
                    if self._note_if_kept(path, JunkTier.TIER_2_DEV_CACHE, is_dir):
                        return None
                    allocated_bytes = 0 if is_dir else calculate_allocated_bytes(size, self.cluster_size)
                    return JunkItem(
                        path=os.path.abspath(path),
                        rel_path=norm_rel,
                        name=clean_name,
                        size=size,
                        allocated_size=allocated_bytes,
                        is_dir=is_dir,
                        tier=JunkTier.TIER_2_DEV_CACHE,
                        description="Polaris regenerable API cache (Polaris 0.3.2+ keeps it on C:)",
                        os_source="Polaris",
                    )
                return None
            # All other unit data (images, progress.jsonl, captures, JSON/MD) is protected
            return None

        # ----------------------------------------------------------------------
        # RULE MATCHING: 3-Tier Pattern Matcher
        # ----------------------------------------------------------------------
        rule = match_junk_rule(
            name=clean_name,
            is_dir=is_dir,
            max_tier=self.max_tier,
            is_root_level=is_root_level,
            rel_path=norm_rel,
            check_location=False,  # _note_if_kept applies the location checks, and counts what they keep
        )
        if not rule:
            return None

        # A name that matches is not yet junk: where the entry lives can say otherwise
        if self._note_if_kept(path, rule.tier, is_dir, rule):
            return None

        # Calculate allocated cluster bytes
        allocated_bytes = 0 if is_dir else calculate_allocated_bytes(size, self.cluster_size)

        return JunkItem(
            path=os.path.abspath(path),
            rel_path=norm_rel,
            name=clean_name,
            size=size,
            allocated_size=allocated_bytes,
            is_dir=is_dir,
            tier=rule.tier,
            description=rule.description,
            os_source=rule.os_source,
        )

    def inspect_entry(self, entry: ScanEntry) -> Optional[JunkItem]:
        """Inspects a ScanEntry produced by FastDirectoryScanner."""
        return self.inspect_file(
            path=entry.path,
            name=entry.name,
            size=entry.size,
            is_dir=entry.is_dir,
            rel_path=entry.rel_path,
        )

    def find_junk(self, sub_path: Optional[str] = None) -> List[JunkItem]:
        """Scans the drive root or subtree and returns all detected junk items.

        Uses FastDirectoryScanner for high throughput, bypassing symlinks and
        handling permission boundaries gracefully.

        Args:
            sub_path: Optional subfolder path to restrict the scan.

        Returns:
            List of JunkItem objects matching rules up to self.max_tier.
        """
        scan_root = os.path.realpath(os.path.abspath(sub_path or self.drive_root))
        junk_items: List[JunkItem] = []
        self.kept = {}
        self.protected_by_system = []
        self._protected_entries = {}
        self._folder_protection = {}
        self._manifest_cache = {}
        self._work_tree_cache = {}

        # Only exclude version control, agent working directories, and SmartDrive metadata/quarantine during scan;
        # allow scanner to inspect system junk directories like $RECYCLE.BIN, .Trashes
        scanner_excludes = {".git", ".agents", ".smart_drive", ".smart_drive_manager", "System Volume Information"}

        if FastDirectoryScanner is not None:
            scanner = FastDirectoryScanner(
                root_path=scan_root,
                follow_symlinks=False,
                exclude_dirs=scanner_excludes,
            )
            for entry in scanner.scan_iter():
                item = self.inspect_entry(entry)
                if item:
                    junk_items.append(item)
                    if item.is_dir:
                        # Purged as a whole and sized below: its files need not be listed one by one first.
                        scanner.skip_descent()
                elif entry.is_dir and self._entry_kept_reason == KEPT_SYSTEM_REFERENCED:
                    # Everything inside belongs to the same dependency, so none of it can be junk either.
                    scanner.skip_descent()
        else:
            stack = [scan_root]
            while stack:
                curr = stack.pop()
                try:
                    with os.scandir(curr) as it:
                        for entry in it:
                            try:
                                is_symlink = entry.is_symlink()
                                if is_symlink:
                                    continue
                                is_dir = entry.is_dir(follow_symlinks=False)
                                if is_dir:
                                    if entry.name in scanner_excludes:
                                        continue
                                    item = self.inspect_file(
                                        path=entry.path,
                                        name=entry.name,
                                        size=0,
                                        is_dir=True,
                                    )
                                    if item:
                                        junk_items.append(item)
                                    elif self._entry_kept_reason != KEPT_SYSTEM_REFERENCED:
                                        stack.append(entry.path)
                                else:
                                    st = entry.stat(follow_symlinks=False)
                                    item = self.inspect_file(
                                        path=entry.path,
                                        name=entry.name,
                                        size=st.st_size,
                                        is_dir=False,
                                    )
                                    if item:
                                        junk_items.append(item)
                            except (PermissionError, FileNotFoundError, OSError):
                                continue
                except (PermissionError, FileNotFoundError, OSError):
                    continue

        junk_items = _drop_items_inside_junk_dirs(junk_items)
        # The scanner reports a directory with size 0; what a purge would free is everything inside it. Measured
        # here, after the nested duplicates are gone, so a tree of junk directories is walked once, not once per level.
        for item in junk_items:
            if item.is_dir:
                item.size, item.allocated_size = measure_tree(item.path, self.cluster_size)
        return junk_items

    def get_summary(self, items: Sequence[JunkItem]) -> Dict[str, Any]:
        """Calculates aggregated metrics across a collection of junk items."""
        total_nominal = sum(item.size for item in items)
        total_allocated = sum(item.allocated_size for item in items)
        by_tier: Dict[int, int] = {int(tier): 0 for tier in JunkTier}
        by_os: Dict[str, int] = {}

        for item in items:
            by_tier[int(item.tier)] = by_tier.get(int(item.tier), 0) + 1
            by_os[item.os_source] = by_os.get(item.os_source, 0) + 1

        return {
            "total_items": len(items),
            "total_nominal_bytes": total_nominal,
            "total_allocated_bytes": total_allocated,
            "slack_bytes": total_allocated - total_nominal,
            "by_tier": by_tier,
            "by_os": by_os,
        }


__all__ = [
    "JunkItem",
    "JunkDetector",
]
