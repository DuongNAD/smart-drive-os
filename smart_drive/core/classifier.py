"""smart_drive.core.classifier - Deep File Inspection, Format Detection & Auto-Tagging Engine.

Milestone 3 (SmartDrive-OS v1.1.0).
Zero external dependencies: 100% Python Standard Library.
Provides deep binary header inspection (magic bytes, structural frames, project indicators)
for AI models, datasets, documents, and code repositories, paired with safe collision-free
relocation into canonical sub-taxonomies.
"""

from __future__ import annotations

import csv
import dataclasses
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import shutil
import struct
from typing import Any, Dict, List, Optional, Set, Tuple, Union
import zipfile

from smart_drive.core.config import (
    DEFAULT_EXCLUDE_DIRS,
    PROTECTED_ROOT_DIRS,
    PROTECTED_ROOT_FILES,
    is_protected_root_dir,
    is_protected_root_file,
)
from smart_drive.core.exfat_compat import detect_drive_root

logger = logging.getLogger(__name__)


@dataclass
class FormatInspection:
    """Detailed result of deep format inspection."""

    format_type: str              # e.g., "GGUF", "Safetensors", "Parquet", "PDF", "Git Repository"
    category: str                 # e.g., "01_AI_Models", "02_Learning_Knowledge", "03_Development_Projects"
    subcategory: str              # e.g., "Weights", "Datasets", "Papers", "Notes"
    confidence: float             # Range 0.0 to 1.0 (1.0 for magic bytes, 0.95 for parsed headers, etc.)
    reason: str                   # Human-readable explanation of detection method
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassificationResult:
    """Classification and taxonomy recommendation for a specific path."""

    source_path: Path
    name: str
    is_dir: bool
    format_type: str
    category: str
    subcategory: str
    recommended_path: str         # Relative to root, e.g. "01_AI_Models/Weights/model.gguf"
    confidence: float
    reason: str
    size_bytes: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "name": self.name,
            "is_dir": self.is_dir,
            "format_type": self.format_type,
            "category": self.category,
            "subcategory": self.subcategory,
            "recommended_path": self.recommended_path.replace("\\", "/"),
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "size_bytes": self.size_bytes,
            "metadata": self.metadata,
        }


@dataclass
class RelocationAction:
    """Action record representing a planned or executed file relocation."""

    source_path: Path
    destination_path: Path
    relative_target: str
    status: str                   # "PLANNED", "MOVED", "SKIPPED_ALREADY_IN_PLACE", "ERROR"
    collision_resolved: bool
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "destination_path": str(self.destination_path),
            "relative_target": self.relative_target.replace("\\", "/"),
            "status": self.status,
            "collision_resolved": self.collision_resolved,
            "error_message": self.error_message,
        }


@dataclass
class ClassificationReport:
    """Aggregate summary of classification and organization."""

    root: str
    total_scanned: int
    total_classified: int
    total_unknown: int
    applied: bool
    dry_run: bool
    results: List[ClassificationResult] = field(default_factory=list)
    actions: List[RelocationAction] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "total_scanned": self.total_scanned,
            "total_classified": self.total_classified,
            "total_unknown": self.total_unknown,
            "applied": self.applied,
            "dry_run": self.dry_run,
            "results": [r.to_dict() for r in self.results],
            "actions": [a.to_dict() for a in self.actions],
        }


# Alias OrganizationReport for API contract compatibility with PROJECT.md
OrganizationReport = ClassificationReport


class ClassifierEngine:
    """Deep file inspection, taxonomy auto-tagging, and safe relocation engine."""

    HEADER_BUFFER_SIZE: int = 65_536  # 64 KB header read buffer

    def __init__(self, root_path: Union[str, Path]) -> None:
        self.root = Path(root_path).resolve()

    # --------------------------------------------------------------------------
    # Format Detectors
    # --------------------------------------------------------------------------

    def detect_format(self, filepath: Path) -> Optional[FormatInspection]:
        """Performs deep inspection of a file using magic bytes, headers, and metadata.

        Strictly zero external dependencies. Inspects:
        - AI Models & Weights: GGUF, Safetensors, ONNX, PyTorch (zip & pickle)
        - Datasets: Parquet, Arrow/Feather, HDF5, JSONL, CSV/TSV
        - Research & Docs: PDF, ePub, Markdown
        - HuggingFace configs
        - Scripts & Archives
        """
        if not filepath.is_file():
            return None

        # Check 0-byte boundary
        try:
            size = filepath.stat().st_size
        except OSError:
            return None

        if size == 0:
            return FormatInspection(
                format_type="Empty File",
                category="06_Archives_Storage",
                subcategory="Empty",
                confidence=1.0,
                reason="0-byte empty file",
            )

        # Read header buffer
        header_bytes = b""
        try:
            with open(filepath, "rb") as f:
                header_bytes = f.read(self.HEADER_BUFFER_SIZE)
        except OSError as exc:
            return FormatInspection(
                format_type="Unreadable",
                category="06_Archives_Storage",
                subcategory="Unreadable",
                confidence=0.0,
                reason=f"Read error: {exc}",
            )

        ext = filepath.suffix.lower()

        # 1. GGUF Models (b"GGUF" at offset 0)
        if len(header_bytes) >= 4 and header_bytes[:4] == b"GGUF":
            version = 1
            tensor_count = 0
            kv_count = 0
            if len(header_bytes) >= 8:
                version = struct.unpack("<I", header_bytes[4:8])[0]
            if len(header_bytes) >= 16:
                tensor_count = struct.unpack("<Q", header_bytes[8:16])[0]
            if len(header_bytes) >= 24:
                kv_count = struct.unpack("<Q", header_bytes[16:24])[0]

            return FormatInspection(
                format_type=f"GGUF (v{version})",
                category="01_AI_Models",
                subcategory="Weights",
                confidence=1.0,
                reason=f"GGUF magic bytes b'GGUF', version {version}, {tensor_count} tensors",
                metadata={"version": version, "tensor_count": tensor_count, "kv_count": kv_count},
            )

        # 2. Safetensors (uint64 LE header length at offset 0 + JSON)
        if len(header_bytes) >= 8:
            header_len = struct.unpack("<Q", header_bytes[:8])[0]
            # Sanity check: header length must be reasonable (< 100MB) and fit in file
            if 0 < header_len < 100_000_000 and header_len + 8 <= size:
                try:
                    with open(filepath, "rb") as f:
                        f.seek(8)
                        read_len = min(header_len, self.HEADER_BUFFER_SIZE)
                        json_bytes = f.read(read_len)
                        if header_len <= self.HEADER_BUFFER_SIZE:
                            meta = json.loads(json_bytes.decode("utf-8", errors="ignore"))
                            if isinstance(meta, dict):
                                tensors = [k for k in meta.keys() if k != "__metadata__"]
                                return FormatInspection(
                                    format_type="Safetensors",
                                    category="01_AI_Models",
                                    subcategory="Weights",
                                    confidence=1.0,
                                    reason=f"Safetensors uint64 header ({header_len} B), {len(tensors)} tensors",
                                    metadata={"header_length": header_len, "tensor_count": len(tensors)},
                                )
                        elif json_bytes.strip().startswith(b"{"):
                            return FormatInspection(
                                format_type="Safetensors",
                                category="01_AI_Models",
                                subcategory="Weights",
                                confidence=0.95,
                                reason=f"Safetensors uint64 header ({header_len} B)",
                                metadata={"header_length": header_len},
                            )
                except Exception:
                    pass

        # 3. Parquet (b"PAR1" at offset 0 or in last 4 bytes)
        if len(header_bytes) >= 4 and header_bytes[:4] == b"PAR1":
            return FormatInspection(
                format_type="Parquet",
                category="01_AI_Models",
                subcategory="Datasets",
                confidence=1.0,
                reason="Parquet magic bytes b'PAR1'",
            )
        if size >= 8 and ext in {".parquet", ".pq"}:
            try:
                with open(filepath, "rb") as f:
                    f.seek(size - 4)
                    if f.read(4) == b"PAR1":
                        return FormatInspection(
                            format_type="Parquet",
                            category="01_AI_Models",
                            subcategory="Datasets",
                            confidence=1.0,
                            reason="Parquet footer magic bytes b'PAR1'",
                        )
            except OSError:
                pass

        # 4. Arrow / Feather (b"ARROW1" or b"FEA1")
        if len(header_bytes) >= 6 and header_bytes[:6] == b"ARROW1":
            return FormatInspection(
                format_type="Arrow",
                category="01_AI_Models",
                subcategory="Datasets",
                confidence=1.0,
                reason="Apache Arrow IPC magic bytes b'ARROW1'",
            )
        if len(header_bytes) >= 4 and header_bytes[:4] == b"FEA1":
            return FormatInspection(
                format_type="Arrow",
                category="01_AI_Models",
                subcategory="Datasets",
                confidence=1.0,
                reason="Feather v1 magic bytes b'FEA1'",
            )

        # 5. HDF5 (b"\x89HDF\r\n\x1a\n")
        if len(header_bytes) >= 8 and header_bytes[:8] == b"\x89HDF\r\n\x1a\n":
            name_lower = filepath.name.lower()
            subcat = "Weights" if ("model" in name_lower or "weight" in name_lower or ext in {".h5", ".hdf5"}) else "Datasets"
            return FormatInspection(
                format_type="HDF5",
                category="01_AI_Models",
                subcategory=subcat,
                confidence=1.0,
                reason="HDF5 magic bytes \\x89HDF\\r\\n\\x1a\\n",
            )

        # 6. PDF (b"%PDF-")
        if len(header_bytes) >= 5 and header_bytes[:5] == b"%PDF-":
            ver = header_bytes[5:8].decode("ascii", errors="ignore").strip()
            return FormatInspection(
                format_type=f"PDF (v{ver})",
                category="02_Learning_Knowledge",
                subcategory="Papers",
                confidence=0.98,
                reason=f"PDF document header %PDF-{ver}",
                metadata={"pdf_version": ver},
            )

        # 7. Zip containers (ePub, PyTorch zip archives, general archives)
        if len(header_bytes) >= 4 and header_bytes[:4] == b"PK\x03\x04":
            try:
                if zipfile.is_zipfile(filepath):
                    with zipfile.ZipFile(filepath, "r") as zf:
                        names = zf.namelist()
                        # ePub check
                        if "mimetype" in names:
                            mt = zf.read("mimetype").decode("utf-8", errors="ignore").strip()
                            if "application/epub+zip" in mt:
                                return FormatInspection(
                                    format_type="ePub",
                                    category="02_Learning_Knowledge",
                                    subcategory="Books",
                                    confidence=1.0,
                                    reason="ePub zip container with mimetype application/epub+zip",
                                )
                        # PyTorch zip weights check
                        if any(n.startswith("archive/") or "data.pkl" in n or "byteorder" in n for n in names):
                            return FormatInspection(
                                format_type="PyTorch",
                                category="01_AI_Models",
                                subcategory="Weights",
                                confidence=0.98,
                                reason="PyTorch zip container with archive/data.pkl",
                            )
            except Exception:
                pass

        # 8. PyTorch Pickle Stream (protocol \x02..\x05)
        if len(header_bytes) >= 2 and header_bytes[0] == 0x80 and header_bytes[1] in (2, 3, 4, 5):
            snippet = header_bytes[:1024]
            if b"torch" in snippet or b"OrderedDict" in snippet or b"_rebuild_tensor" in snippet or ext in {".pt", ".pth", ".bin"}:
                return FormatInspection(
                    format_type="PyTorch",
                    category="01_AI_Models",
                    subcategory="Weights",
                    confidence=0.95,
                    reason=f"PyTorch pickle stream (protocol {header_bytes[1]})",
                )

        # 9. ONNX (Protobuf wire format or .onnx with protobuf tag 0x08)
        if ext == ".onnx" or (len(header_bytes) >= 2 and header_bytes[0] == 0x08 and 1 <= header_bytes[1] <= 20 and b"onnx" in header_bytes[:2048].lower()):
            return FormatInspection(
                format_type="ONNX",
                category="01_AI_Models",
                subcategory="Weights",
                confidence=0.95,
                reason="ONNX protobuf wire model",
            )

        # 10. HuggingFace Model Config
        if ext == ".json" and size < 2_000_000:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    hf_keys = {"architectures", "model_type", "torch_dtype", "vocab_size", "transformers_version"}
                    matched = [k for k in hf_keys if k in data]
                    if matched:
                        return FormatInspection(
                            format_type="HuggingFace Config",
                            category="01_AI_Models",
                            subcategory="Configs",
                            confidence=0.95,
                            reason=f"HuggingFace config containing {matched}",
                            metadata={"matched_keys": matched, "model_type": data.get("model_type")},
                        )
            except Exception:
                pass

        # 11. JSONL Dataset
        if ext in {".jsonl", ".ndjson"} or (ext == ".json" and size > 0):
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    valid_lines = 0
                    for _ in range(5):
                        line = f.readline()
                        if not line:
                            break
                        line_s = line.strip()
                        if line_s:
                            obj = json.loads(line_s)
                            if isinstance(obj, (dict, list)):
                                valid_lines += 1
                    min_valid = 2 if ext == ".json" else 1
                    if valid_lines >= min_valid:
                        return FormatInspection(
                            format_type="JSONL",
                            category="01_AI_Models",
                            subcategory="Datasets",
                            confidence=0.95 if ext != ".json" else 0.85,
                            reason=f"Newline-delimited JSON dataset ({valid_lines} sample rows parsed)",
                        )
            except Exception:
                pass

        # 12. CSV / TSV Tabular Dataset
        if ext in {".csv", ".tsv"}:
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    sample = f.read(4096)
                if sample.strip():
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
                    reader = csv.reader(sample.splitlines(), dialect)
                    rows = [r for r in reader if r]
                    if len(rows) >= 2 and len(rows[0]) > 1:
                        col_count = len(rows[0])
                        if all(len(r) == col_count for r in rows[:5]):
                            return FormatInspection(
                                format_type="CSV" if dialect.delimiter != "\t" else "TSV",
                                category="01_AI_Models",
                                subcategory="Datasets",
                                confidence=0.90,
                                reason=f"Tabular dataset with {col_count} columns (delimiter: {repr(dialect.delimiter)})",
                                metadata={"columns": col_count, "delimiter": dialect.delimiter},
                            )
            except Exception:
                return FormatInspection(
                    format_type="CSV" if ext == ".csv" else "TSV",
                    category="01_AI_Models",
                    subcategory="Datasets",
                    confidence=0.80,
                    reason=f"Tabular dataset file ({ext})",
                )

        # 13. Markdown Notes & Documentation
        if ext in {".md", ".markdown"}:
            return FormatInspection(
                format_type="Markdown",
                category="02_Learning_Knowledge",
                subcategory="Notes",
                confidence=0.90,
                reason="Markdown research/notes document",
            )

        # 14. Standalone Developer Scripts
        if ext in {".py", ".sh", ".bash", ".ps1", ".bat", ".cmd"}:
            return FormatInspection(
                format_type="Script",
                category="05_Dev_Toolbox",
                subcategory="Scripts",
                confidence=0.80,
                reason=f"Standalone developer script ({ext})",
            )

        # 15. General Compressed Archives
        if ext in {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}:
            return FormatInspection(
                format_type="Archive",
                category="06_Archives_Storage",
                subcategory="Archives",
                confidence=0.85,
                reason=f"Compressed archive ({ext})",
            )

        # Unrecognized / Generic Fallback
        return FormatInspection(
            format_type=f"Unknown ({ext.lstrip('.') or 'binary'})",
            category="06_Archives_Storage",
            subcategory="Unclassified",
            confidence=0.30,
            reason=f"Unrecognized file format with extension '{ext}'",
        )

    def detect_project_repo(self, dirpath: Path) -> Optional[FormatInspection]:
        """Detects whether a directory represents a software project or repository."""
        if not dirpath.is_dir():
            return None

        # Exclude protected root folders
        if is_protected_root_dir(dirpath.name):
            return None

        # 1. Git Repository
        if (dirpath / ".git").exists():
            return FormatInspection(
                format_type="Git Repository",
                category="03_Development_Projects",
                subcategory=dirpath.name,
                confidence=1.0,
                reason="Directory contains .git version control metadata",
            )

        # 2. Rust Project
        if (dirpath / "Cargo.toml").is_file():
            return FormatInspection(
                format_type="Rust Project",
                category="03_Development_Projects",
                subcategory=dirpath.name,
                confidence=0.95,
                reason="Directory contains Cargo.toml manifest",
            )

        # 3. Node.js Project
        if (dirpath / "package.json").is_file():
            return FormatInspection(
                format_type="Node Project",
                category="03_Development_Projects",
                subcategory=dirpath.name,
                confidence=0.95,
                reason="Directory contains package.json manifest",
            )

        # 4. Python Project
        if (
            (dirpath / "pyproject.toml").is_file()
            or (dirpath / "setup.py").is_file()
            or (dirpath / "requirements.txt").is_file()
        ):
            return FormatInspection(
                format_type="Python Project",
                category="03_Development_Projects",
                subcategory=dirpath.name,
                confidence=0.95,
                reason="Directory contains pyproject.toml, setup.py, or requirements.txt manifest",
            )

        return None

    # --------------------------------------------------------------------------
    # Single Path Inspection & Classification
    # --------------------------------------------------------------------------

    def classify_file(self, filepath: Path) -> Optional[ClassificationResult]:
        """Classifies a single file or directory, conforming to PROJECT.md interface contract."""
        return self.inspect_path(filepath)

    def inspect_path(self, target_path: Union[str, Path]) -> Optional[ClassificationResult]:
        """Inspects and classifies a single file or project directory."""
        p = Path(target_path).resolve()
        if not p.exists():
            return None

        # Check protected files/dirs against inviolable safeguards
        if is_protected_root_file(p.name) or is_protected_root_dir(p.name):
            return None

        if p.is_dir():
            repo_insp = self.detect_project_repo(p)
            if repo_insp:
                rec_path = f"{repo_insp.category}/{repo_insp.subcategory}"
                return ClassificationResult(
                    source_path=p,
                    name=p.name,
                    is_dir=True,
                    format_type=repo_insp.format_type,
                    category=repo_insp.category,
                    subcategory=repo_insp.subcategory,
                    recommended_path=rec_path,
                    confidence=repo_insp.confidence,
                    reason=repo_insp.reason,
                    size_bytes=0,
                    metadata=repo_insp.metadata,
                )
            return None

        file_insp = self.detect_format(p)
        if not file_insp:
            return None

        try:
            size = p.stat().st_size
        except OSError:
            size = 0

        rec_path = f"{file_insp.category}/{file_insp.subcategory}/{p.name}"
        return ClassificationResult(
            source_path=p,
            name=p.name,
            is_dir=False,
            format_type=file_insp.format_type,
            category=file_insp.category,
            subcategory=file_insp.subcategory,
            recommended_path=rec_path,
            confidence=file_insp.confidence,
            reason=file_insp.reason,
            size_bytes=size,
            metadata=file_insp.metadata,
        )

    # --------------------------------------------------------------------------
    # Directory Scanning
    # --------------------------------------------------------------------------

    def scan_and_classify(
        self,
        target_dir: Optional[Union[str, Path]] = None,
        recursive: bool = True,
        suggest_only: bool = True,
    ) -> List[ClassificationResult]:
        """Scans a directory or file, detecting project repos and inspecting files."""
        scan_root = Path(target_dir).resolve() if target_dir else self.root
        results: List[ClassificationResult] = []

        if not scan_root.exists():
            return results

        if scan_root.is_file():
            res = self.inspect_path(scan_root)
            return [res] if res else []

        # Directory traversal
        for root, dirs, files in os.walk(scan_root):
            curr_dir = Path(root)

            # Prune hidden, system, cache, and protected directories
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d != "$RECYCLE.BIN"
                and d != "System Volume Information"
                and d != "__pycache__"
                and d not in DEFAULT_EXCLUDE_DIRS
                and not is_protected_root_dir(d)
            ]

            # Check if curr_dir itself is a project repo (when scanning from parent)
            if curr_dir != scan_root:
                repo_res = self.detect_project_repo(curr_dir)
                if repo_res:
                    rec_path = f"{repo_res.category}/{repo_res.subcategory}"
                    results.append(ClassificationResult(
                        source_path=curr_dir,
                        name=curr_dir.name,
                        is_dir=True,
                        format_type=repo_res.format_type,
                        category=repo_res.category,
                        subcategory=repo_res.subcategory,
                        recommended_path=rec_path,
                        confidence=repo_res.confidence,
                        reason=repo_res.reason,
                        size_bytes=0,
                        metadata=repo_res.metadata,
                    ))
                    dirs.clear()  # Do not recurse into already classified project repo
                    continue

            # Inspect loose files
            for file_name in files:
                if (
                    file_name.startswith(".")
                    or is_protected_root_file(file_name)
                    or file_name.endswith((".pyc", ".pyo"))
                ):
                    continue
                file_path = curr_dir / file_name
                res = self.inspect_path(file_path)
                if res:
                    results.append(res)

            if not recursive:
                break

        return results

    # --------------------------------------------------------------------------
    # Destination Resolution & Collision Avoidance
    # --------------------------------------------------------------------------

    def resolve_destination(
        self,
        result: ClassificationResult,
        allocated: Optional[Set[Path]] = None,
    ) -> Tuple[Path, bool]:
        """Resolves target destination, preventing collisions with _1, _2 suffixes.

        Returns:
            Tuple of (destination_path, collision_resolved_bool).
        """
        if allocated is None:
            allocated = set()

        canonical_dest = (self.root / result.recommended_path).resolve()

        # If already at canonical location, return without collision
        if result.source_path.resolve() == canonical_dest:
            return canonical_dest, False

        # If canonical dest is free on disk and not yet allocated in this batch
        if not canonical_dest.exists() and canonical_dest not in allocated:
            allocated.add(canonical_dest)
            return canonical_dest, False

        # Collision avoidance loop
        parent = canonical_dest.parent
        if result.is_dir:
            base_name = canonical_dest.name
            i = 1
            while True:
                candidate = parent / f"{base_name}_{i}"
                if not candidate.exists() and candidate not in allocated:
                    allocated.add(candidate)
                    return candidate, True
                i += 1
        else:
            stem = canonical_dest.stem
            suffix = canonical_dest.suffix
            i = 1
            while True:
                candidate = parent / f"{stem}_{i}{suffix}"
                if not candidate.exists() and candidate not in allocated:
                    allocated.add(candidate)
                    return candidate, True
                i += 1

    # --------------------------------------------------------------------------
    # Relocation Execution
    # --------------------------------------------------------------------------

    def execute_relocation(
        self,
        results: List[ClassificationResult],
        dry_run: bool = True,
    ) -> List[RelocationAction]:
        """Simulates or executes safe relocation of classified items."""
        actions: List[RelocationAction] = []
        allocated: Set[Path] = set()

        for item in results:
            dest_path, collision = self.resolve_destination(item, allocated)
            try:
                rel_target = str(dest_path.relative_to(self.root)).replace("\\", "/")
            except ValueError:
                rel_target = str(dest_path).replace("\\", "/")

            if item.source_path.resolve() == dest_path.resolve():
                actions.append(RelocationAction(
                    source_path=item.source_path,
                    destination_path=dest_path,
                    relative_target=rel_target,
                    status="SKIPPED_ALREADY_IN_PLACE",
                    collision_resolved=False,
                ))
                continue

            if dry_run:
                actions.append(RelocationAction(
                    source_path=item.source_path,
                    destination_path=dest_path,
                    relative_target=rel_target,
                    status="PLANNED",
                    collision_resolved=collision,
                ))
            else:
                try:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(item.source_path), str(dest_path))
                    actions.append(RelocationAction(
                        source_path=item.source_path,
                        destination_path=dest_path,
                        relative_target=rel_target,
                        status="MOVED",
                        collision_resolved=collision,
                    ))
                except Exception as exc:
                    actions.append(RelocationAction(
                        source_path=item.source_path,
                        destination_path=dest_path,
                        relative_target=rel_target,
                        status="ERROR",
                        collision_resolved=collision,
                        error_message=str(exc),
                    ))

        return actions

    def apply_organization(
        self,
        results: List[ClassificationResult],
        dry_run: bool = True,
    ) -> OrganizationReport:
        """Applies organization and returns a comprehensive OrganizationReport (ClassificationReport)."""
        actions = self.execute_relocation(results, dry_run=dry_run)
        return ClassificationReport(
            root=str(self.root),
            total_scanned=len(results),
            total_classified=len([r for r in results if not r.format_type.startswith("Unknown")]),
            total_unknown=len([r for r in results if r.format_type.startswith("Unknown")]),
            applied=not dry_run,
            dry_run=dry_run,
            results=results,
            actions=actions,
        )
