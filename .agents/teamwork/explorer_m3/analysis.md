# Technical Implementation Blueprint: Milestone 3 (Intelligent Classifier & Auto-Tagger)

**Document Version:** 1.0.0  
**Target Milestone:** M3 (`smart-drive classify`)  
**Features Covered:** F19, F20, F21, F22, F23, F24  
**Integrity Mode:** 100% Python Standard Library (Zero External Dependencies)  
**Author:** Explorer M3  

---

## 1. Executive Summary

Milestone 3 equips SmartDrive-OS with a deep file-inspection and taxonomy auto-routing subsystem (`smart-drive classify`). Unlike rudimentary extension-only sorting, Milestone 3 implements authentic binary header recognition (magic bytes, structural frames, and project indicators) to classify specialized machine learning artifacts, big data datasets, scientific research materials, and development repositories.

### Key Capabilities:
1. **Deep Format Recognition (F20, F21, F22, F23):**
   - **AI Models & Weights:** GGUF (`b"GGUF"` v1-3), Safetensors (uint64 length + parsed JSON metadata), ONNX (protobuf wire format & tags), PyTorch (`PK\x03\x04` zip archives with `archive/` members & pickle protocol streams `\x80\x02`..`\x80\x05`), HuggingFace Model Configs (`config.json` containing `architectures`/`model_type`).
   - **Datasets:** Parquet (`b"PAR1"`), Arrow/Feather (`b"ARROW1"` / `b"FEA1"`), HDF5 (`b"\x89HDF\r\n\x1a\n"`), JSONL (multi-line valid JSON stream), CSV/TSV (consistent column structure).
   - **Research & Documents:** PDF (`b"%PDF-"`), ePub (`PK\x03\x04` container with `mimetype: application/epub+zip`), Markdown notes (`.md`/`.markdown` syntax).
   - **Source Code Repositories:** Git (`.git/`), Node (`package.json`), Python (`pyproject.toml`, `setup.py`), Rust (`Cargo.toml`).
2. **Taxonomy Routing & Auto-Tagging (F24):**
   - Maps detected formats to canonical sub-taxonomies: `01_AI_Models/Weights/`, `01_AI_Models/Datasets/`, `01_AI_Models/Configs/`, `02_Learning_Knowledge/Papers/`, `02_Learning_Knowledge/Books/`, `02_Learning_Knowledge/Notes/`, `03_Development_Projects/<repo_name>/`, `05_Dev_Toolbox/Scripts/`.
3. **Collision Avoidance & Inviolable Safeguards:**
   - Non-destructive execution: never overwrites existing destination files (appends `_1`, `_2` before the extension).
   - Inviolable protection: never moves or mutates protected root files (`gemini.md`, `agents.md`, `readme.md`, `.mcp.json`, etc.) or root taxonomies.
   - Batch collision tracking: handles multiple incoming files resolving to the same name within a single batch.
4. **CLI Subcommand (F19):**
   - `smart-drive classify [path] [--root ROOT] [--suggest] [--dry-run] [--apply] [--json] [--no-recursive]`.
   - `--suggest`: Inspection and destination table without filesystem changes.
   - `--dry-run`: Simulation of relocation with collision resolution previews.
   - `--apply`: Atomic, safe physical file relocation.
   - `--json`: Complete machine-readable output.

---

## 2. Package Structure & File Inventory

The implementation spans four specific files:

```
d:\teamwork_projects\smart_drive_os\
├── smart_drive\
│   ├── core\
│   │   └── classifier.py       # (NEW) ClassifierEngine, deep format detectors, routing rules, collision resolver
│   └── cli\
│       ├── cmd_classify.py     # (NEW) CLI handler for `smart-drive classify`
│       └── main.py             # (MODIFIED) Register classify subparser and dispatch table entry
└── tests\
    └── test_classifier.py     # (NEW) Full unittest test suite with synthetic binary fixtures
```

---

## 3. Deep Format Recognition Specifications (F20 - F23)

All detectors operate directly on byte streams or file metadata using standard library modules (`struct`, `json`, `zipfile`, `csv`, `os`, `pathlib`).

### 3.1 AI Models & Weights (F20)

| Format | Magic Bytes / Header Signature | Detection Algorithm | Extracted Metadata |
|---|---|---|---|
| **GGUF** | `b"GGUF"` at offset 0 | Read 24 bytes: `b"GGUF"` + `<I` version (1..10) + `<Q` tensor_count + `<Q` kv_count | Version, tensor count, kv count |
| **Safetensors** | uint64 LE header length at offset 0 | Read 8 bytes: `header_len = struct.unpack("<Q", data[:8])[0]`. Validate `0 < header_len < 100MB`. Read header_len JSON bytes, parse JSON dict, verify tensor keys or `__metadata__`. | Header size, tensor count, metadata |
| **PyTorch (Zip)** | `b"PK\x03\x04"` at offset 0 | `zipfile.is_zipfile(p)` -> Check if any entry starts with `archive/` or contains `data.pkl` or `byteorder`. | Archive format, entry count |
| **PyTorch (Pickle)** | `b"\x80\x02"` .. `b"\x80\x05"` | Check first 2 bytes pickle opcode (`0x80` + protocol 2-5). Search first 1KB for `torch`, `OrderedDict`, or `_rebuild_tensor`. | Pickle protocol |
| **ONNX** | Protobuf wire `0x08` + `.onnx` | Field tag 1 varint `0x08` (ir_version 1..20). Check for keywords `onnx`, `pytorch`, `ai.onnx` or extension `.onnx`. | Protobuf valid wire, keywords |
| **HuggingFace Config** | JSON with model attributes | Extension `.json` (and name e.g. `config.json` or `generation_config.json`). Parse JSON: contains keys `architectures` or `model_type` or `torch_dtype`. | Model type, architectures |

### 3.2 Datasets (F21)

| Format | Magic Bytes / Header Signature | Detection Algorithm | Extracted Metadata |
|---|---|---|---|
| **Parquet** | `b"PAR1"` | Offset 0: `b"PAR1"` AND/OR last 4 bytes: `b"PAR1"`. | Header & footer validation |
| **Arrow / Feather** | `b"ARROW1"` or `b"FEA1"` | Offset 0: `b"ARROW1"` (Arrow IPC/Feather v2) or `b"FEA1"` (Feather v1). | Arrow IPC variant |
| **HDF5** | `b"\x89HDF\r\n\x1a\n"` | Offset 0: exactly 8 bytes `0x89 0x48 0x44 0x46 0x0D 0x0A 0x1A 0x0A`. | HDF5 signature match |
| **JSONL** | Newline-delimited JSON | Extension `.jsonl`/`.ndjson` or `.json`. Read up to 5 non-empty lines, verify each line parses with `json.loads()` into a dict/list. | Line count sample verified |
| **CSV / TSV** | Delimited tabular text | Extension `.csv`/`.tsv`. Use `csv.Sniffer` or `csv.reader`. Verify >= 2 rows with matching column counts > 1. | Delimiter, column count |

### 3.3 Research & Documents (F22)

| Format | Magic Bytes / Header Signature | Detection Algorithm | Extracted Metadata |
|---|---|---|---|
| **PDF** | `b"%PDF-"` at offset 0 | Read 8 bytes: `b"%PDF-"` + version string (e.g. `1.4`, `1.7`, `2.0`). | PDF version |
| **ePub** | `b"PK\x03\x04"` container | Zip check: inspect member `mimetype`. Content must contain `application/epub+zip`. | Mimetype string |
| **Markdown** | Extension `.md`/`.markdown` | UTF-8 text containing markdown indicators (`#`, `- `, `* `, `[ ]`, ````). | Markdown syntax score |

### 3.4 Project Repositories (F23)

Inspection applies to directories:

| Project Type | Indicator Markers | Detection Algorithm |
|---|---|---|
| **Git Repository** | `.git/` folder or `.git` file | Check `(dir / ".git").exists()` |
| **Rust Project** | `Cargo.toml` | Check `(dir / "Cargo.toml").is_file()` |
| **Node.js Project** | `package.json` | Check `(dir / "package.json").is_file()` |
| **Python Project** | `pyproject.toml`, `setup.py` | Check `(dir / "pyproject.toml").is_file()` or `(dir / "setup.py").is_file()` |

---

## 4. Taxonomy Routing & Collision Avoidance (F24)

### 4.1 Sub-Taxonomy Destination Mapping

All recommended paths are relative to the drive/workspace `root`:

```
Drive Root
├── 01_AI_Models/
│   ├── Weights/         <- GGUF, Safetensors, ONNX, PyTorch (.pt, .pth, .bin)
│   ├── Datasets/        <- Parquet, Arrow, HDF5 (data), JSONL, CSV/TSV
│   └── Configs/         <- HuggingFace model configs, tokenizers
├── 02_Learning_Knowledge/
│   ├── Papers/          <- Research PDFs
│   ├── Books/           <- ePub eBooks
│   └── Notes/           <- Markdown research and study notes
├── 03_Development_Projects/
│   └── <project_name>/  <- Git repos, Rust, Node, Python projects
├── 05_Dev_Toolbox/
│   └── Scripts/         <- Standalone loose scripts (.py, .sh, .ps1, .bat)
└── 06_Archives_Storage/
    └── Archives/        <- Compressed archives (.zip, .tar.gz, .7z)
```

### 4.2 Collision Avoidance Algorithm

When an item is relocated to `target_dir / target_name`:
1. Check if `source_path.resolve() == (target_dir / target_name).resolve()`. If true, item is **already in place** -> Status: `SKIPPED_ALREADY_IN_PLACE`.
2. Check if `target_dir / target_name` exists on disk OR has been allocated in the current execution batch:
   - For a **File** with `stem` and `suffix` (e.g., `stem = "model"`, `suffix = ".gguf"`):
     - Iterate $i = 1, 2, 3, \dots$ until `target_dir / f"{stem}_{i}{suffix}"` is free.
     - Final name: `model_1.gguf`, `model_2.gguf`, etc.
   - For a **Directory** with `name` (e.g., `"my_project"`):
     - Iterate $i = 1, 2, 3, \dots$ until `target_dir / f"{name}_{i}"` is free.
     - Final name: `my_project_1`, `my_project_2`, etc.
3. Track newly allocated destinations in a `Set[Path]` to prevent collisions within multi-file batch moves.

### 4.3 Inviolable Safeguards
- Items listed in `PROTECTED_ROOT_FILES` (`gemini.md`, `agents.md`, `readme.md`, `.mcp.json`, scripts) are NEVER moved.
- Root directories in `PROTECTED_ROOT_DIRS` (`01_AI_Models`, `02_Learning_Knowledge`, `.agents`, `smart_ssd_workspace`, `system volume information`) are NEVER moved or treated as movable projects.
- Scanner skips internal contents of `.git/`, `.agents/`, and `.smart_drive/`.

---

## 5. Architectural Data Models (`smart_drive/core/classifier.py`)

```python
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
    PROTECTED_ROOT_DIRS,
    PROTECTED_ROOT_FILES,
    is_protected_root_dir,
    is_protected_root_file,
)
from smart_drive.core.exfat_compat import detect_drive_root


@dataclass
class FormatInspection:
    """Detailed result of deep format inspection."""
    format_type: str              # e.g., "GGUF", "Safetensors", "Parquet", "PDF", "Git Repository"
    category: str                 # e.g., "01_AI_Models", "02_Learning_Knowledge", "03_Development_Projects"
    subcategory: str              # e.g., "Weights", "Datasets", "Papers", "Notes"
    confidence: float             # Range 0.0 to 1.0 (e.g. 1.0 for magic bytes, 0.95 for header json, 0.85 for extension)
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
```

---

## 6. Implementation Template: `smart_drive/core/classifier.py`

Below is the complete architectural implementation blueprint for the detection engine and classifier logic:

```python
class ClassifierEngine:
    """Deep file inspection, taxonomy auto-tagging, and safe relocation engine."""

    HEADER_BUFFER_SIZE: int = 65_536  # 64 KB header read buffer

    def __init__(self, root_path: Union[str, Path]) -> None:
        self.root = Path(root_path).resolve()

    # --------------------------------------------------------------------------
    # Format Detectors
    # --------------------------------------------------------------------------

    def detect_format(self, filepath: Path) -> Optional[FormatInspection]:
        """Performs deep inspection of a file using magic bytes, headers, and metadata."""
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

        # 1. GGUF
        if len(header_bytes) >= 24 and header_bytes[:4] == b"GGUF":
            version = struct.unpack("<I", header_bytes[4:8])[0]
            tensor_count = struct.unpack("<Q", header_bytes[8:16])[0]
            kv_count = struct.unpack("<Q", header_bytes[16:24])[0]
            return FormatInspection(
                format_type=f"GGUF (v{version})",
                category="01_AI_Models",
                subcategory="Weights",
                confidence=1.0,
                reason=f"GGUF magic bytes b'GGUF', version {version}, {tensor_count} tensors",
                metadata={"version": version, "tensor_count": tensor_count, "kv_count": kv_count},
            )

        # 2. Safetensors
        if len(header_bytes) >= 8:
            header_len = struct.unpack("<Q", header_bytes[:8])[0]
            if 0 < header_len < 100_000_000 and header_len + 8 <= size:
                try:
                    with open(filepath, "rb") as f:
                        f.seek(8)
                        read_len = min(header_len, self.HEADER_BUFFER_SIZE)
                        json_bytes = f.read(read_len)
                        if header_len <= self.HEADER_BUFFER_SIZE:
                            meta = json.loads(json_bytes.decode("utf-8"))
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

        # 3. Parquet
        if len(header_bytes) >= 4 and header_bytes[:4] == b"PAR1":
            return FormatInspection(
                format_type="Parquet",
                category="01_AI_Models",
                subcategory="Datasets",
                confidence=1.0,
                reason="Parquet magic bytes b'PAR1'",
            )
        # Check Parquet footer if file > 8 bytes
        if size >= 8 and filepath.suffix.lower() in {".parquet", ".pq"}:
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

        # 4. Arrow / Feather
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

        # 5. HDF5
        if len(header_bytes) >= 8 and header_bytes[:8] == b"\x89HDF\r\n\x1a\n":
            subcat = "Weights" if "model" in filepath.name.lower() or "weight" in filepath.name.lower() else "Datasets"
            return FormatInspection(
                format_type="HDF5",
                category="01_AI_Models",
                subcategory=subcat,
                confidence=1.0,
                reason="HDF5 magic bytes \\x89HDF\\r\\n\\x1a\\n",
            )

        # 6. PDF
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

        # 7. Zip containers (ePub, PyTorch zip)
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

        # 8. PyTorch Pickle Stream
        if len(header_bytes) >= 2 and header_bytes[0] == 0x80 and header_bytes[1] in (2, 3, 4, 5):
            snippet = header_bytes[:1024]
            if b"torch" in snippet or b"OrderedDict" in snippet or b"_rebuild_tensor" in snippet:
                return FormatInspection(
                    format_type="PyTorch",
                    category="01_AI_Models",
                    subcategory="Weights",
                    confidence=0.95,
                    reason=f"PyTorch pickle stream (protocol {header_bytes[1]})",
                )

        # 9. ONNX
        ext = filepath.suffix.lower()
        if ext == ".onnx" or (len(header_bytes) >= 2 and header_bytes[0] == 0x08 and 1 <= header_bytes[1] <= 20 and b"onnx" in header_bytes[:2048].lower()):
            return FormatInspection(
                format_type="ONNX",
                category="01_AI_Models",
                subcategory="Weights",
                confidence=0.95,
                reason="ONNX protobuf wire model",
            )

        # 10. HuggingFace Config
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
                    if valid_lines >= (2 if ext == ".json" else 1):
                        return FormatInspection(
                            format_type="JSONL",
                            category="01_AI_Models",
                            subcategory="Datasets",
                            confidence=0.95 if ext != ".json" else 0.85,
                            reason=f"Newline-delimited JSON dataset ({valid_lines} sample rows parsed)",
                        )
            except Exception:
                pass

        # 12. CSV / TSV Dataset
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

        # 13. Markdown Notes
        if ext in {".md", ".markdown"}:
            return FormatInspection(
                format_type="Markdown",
                category="02_Learning_Knowledge",
                subcategory="Notes",
                confidence=0.90,
                reason="Markdown research/notes document",
            )

        # 14. Standalone Scripts
        if ext in {".py", ".sh", ".bash", ".ps1", ".bat", ".cmd"}:
            return FormatInspection(
                format_type="Script",
                category="05_Dev_Toolbox",
                subcategory="Scripts",
                confidence=0.80,
                reason=f"Standalone developer script ({ext})",
            )

        # 15. Archives
        if ext in {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}:
            return FormatInspection(
                format_type="Archive",
                category="06_Archives_Storage",
                subcategory="Archives",
                confidence=0.85,
                reason=f"Compressed archive ({ext})",
            )

        # Unknown / Unclassified
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
        if (dirpath / "pyproject.toml").is_file() or (dirpath / "setup.py").is_file():
            return FormatInspection(
                format_type="Python Project",
                category="03_Development_Projects",
                subcategory=dirpath.name,
                confidence=0.95,
                reason="Directory contains pyproject.toml or setup.py manifest",
            )

        return None

    # --------------------------------------------------------------------------
    # Scanning and Classification
    # --------------------------------------------------------------------------

    def inspect_path(self, target_path: Union[str, Path]) -> Optional[ClassificationResult]:
        """Inspects and classifies a single file or project directory."""
        p = Path(target_path).resolve()
        if not p.exists():
            return None

        # Check protected files/dirs
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

    def scan_and_classify(
        self,
        target_dir: Optional[Union[str, Path]] = None,
        recursive: bool = True
    ) -> List[ClassificationResult]:
        """Scans a directory, detecting project repos and inspecting files."""
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

            # Skip hidden/system directories
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".")
                and d != "$RECYCLE.BIN"
                and d != "System Volume Information"
                and not is_protected_root_dir(d)
            ]

            # Check if curr_dir itself is a project repo
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
                    dirs.clear()  # Do not recurse into classified project repo
                    continue

            # Inspect loose files
            for file_name in files:
                if file_name.startswith(".") or is_protected_root_file(file_name):
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
        allocated: Optional[Set[Path]] = None
    ) -> Tuple[Path, bool]:
        """Resolves target destination, preventing collisions with _1, _2 suffixes."""
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
        dry_run: bool = True
    ) -> List[RelocationAction]:
        """Simulates or executes safe relocation of classified items."""
        actions: List[RelocationAction] = []
        allocated: Set[Path] = set()

        for item in results:
            dest_path, collision = self.resolve_destination(item, allocated)
            rel_target = str(dest_path.relative_to(self.root)).replace("\\", "/")

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
```

---

## 7. CLI Subcommand Implementation: `smart_drive/cli/cmd_classify.py`

Below is the design for `cmd_classify.py`:

```python
"""smart_drive.cli.cmd_classify - CLI Subcommand Handler for `smart-drive classify`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from smart_drive.core.classifier import ClassifierEngine, ClassificationReport
from smart_drive.core.exfat_compat import detect_drive_root


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def cmd_classify(args: argparse.Namespace) -> int:
    """Handles the `classify` subcommand."""
    root = os.path.abspath(getattr(args, "root", None) or detect_drive_root())
    target_path = getattr(args, "path", None)
    target = os.path.abspath(target_path) if target_path else root

    apply_mode = getattr(args, "apply", False)
    dry_run_mode = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)
    recursive = not getattr(args, "no_recursive", False)

    engine = ClassifierEngine(root_path=root)
    results = engine.scan_and_classify(target_dir=Path(target), recursive=recursive)

    # 1. APPLY MODE
    if apply_mode:
        actions = engine.execute_relocation(results, dry_run=False)
        report = ClassificationReport(
            root=root,
            total_scanned=len(results),
            total_classified=len([r for r in results if not r.format_type.startswith("Unknown")]),
            total_unknown=len([r for r in results if r.format_type.startswith("Unknown")]),
            applied=True,
            dry_run=False,
            results=results,
            actions=actions,
        )

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0

        moved = [a for a in actions if a.status == "MOVED"]
        skipped = [a for a in actions if a.status == "SKIPPED_ALREADY_IN_PLACE"]
        errors = [a for a in actions if a.status == "ERROR"]

        print(f"\nSmartDrive-OS Classification & Auto-Tagging [APPLIED]")
        print("=" * 90)
        for a in moved:
            suffix = " (Renamed to prevent overwrite)" if a.collision_resolved else ""
            print(f"  ✓ Moved: {os.path.basename(str(a.source_path))} -> {a.relative_target}{suffix}")
        for s in skipped:
            print(f"  - In place: {os.path.basename(str(s.source_path))} is already in {s.relative_target}")
        for e in errors:
            print(f"  ✗ Error moving {os.path.basename(str(e.source_path))}: {e.error_message}")
        print("-" * 90)
        print(f"Applied: {len(moved)} moved, {len(skipped)} already in place, {len(errors)} error(s).\n")
        return 0 if not errors else 1

    # 2. DRY-RUN SIMULATION MODE
    elif dry_run_mode:
        actions = engine.execute_relocation(results, dry_run=True)
        report = ClassificationReport(
            root=root,
            total_scanned=len(results),
            total_classified=len([r for r in results if not r.format_type.startswith("Unknown")]),
            total_unknown=len([r for r in results if r.format_type.startswith("Unknown")]),
            applied=False,
            dry_run=True,
            results=results,
            actions=actions,
        )

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0

        print(f"\nSmartDrive-OS Classification & Auto-Tagging [DRY-RUN SIMULATION]")
        print("=" * 90)
        print(f"{'Source File':<36} {'-> Target Destination':<40} {'Status'}")
        print("-" * 90)
        for a in actions:
            col_str = " (COLLISION RESOLVED)" if a.collision_resolved else ""
            src_name = os.path.basename(str(a.source_path))
            print(f"{src_name:<36} -> {a.relative_target:<38} {a.status}{col_str}")
        print("-" * 90)
        print(f"Simulation Complete: {len(actions)} item(s) analyzed. No changes made to disk.")
        print("Run with '--apply' to execute these relocations.\n")
        return 0

    # 3. SUGGEST MODE (Default)
    else:
        report = ClassificationReport(
            root=root,
            total_scanned=len(results),
            total_classified=len([r for r in results if not r.format_type.startswith("Unknown")]),
            total_unknown=len([r for r in results if r.format_type.startswith("Unknown")]),
            applied=False,
            dry_run=False,
            results=results,
            actions=[],
        )

        if as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0

        print(f"\nSmartDrive-OS Intelligent Classifier (v1.1.0)")
        print(f"Scan Root: {target} (Total Items: {len(results)})")
        print("=" * 95)
        print(f"{'File / Directory':<34} {'Format':<18} {'Confidence':<12} {'Target Taxonomy'}")
        print("-" * 95)
        for r in results:
            item_display = f"{r.name}/" if r.is_dir else r.name
            conf_str = f"{int(r.confidence * 100)}%"
            target_disp = f"{r.category}/{r.subcategory}"
            print(f"{item_display:<34} {r.format_type:<18} {conf_str:<12} {target_disp}")
        print("=" * 95)
        print(f"Classified {report.total_classified} of {report.total_scanned} item(s) ({report.total_unknown} unknown).")
        print("Options: Run with '--dry-run' to simulate moves, or '--apply' to safely organize.\n")
        return 0
```

---

## 8. CLI Integration Blueprint: `smart_drive/cli/main.py`

### 8.1 Parser Registration
In `build_parser()` of `smart_drive/cli/main.py`:

```python
    # 16. classify
    p_cls = subparsers.add_parser(
        "classify",
        help="Deep file format recognition, taxonomy auto-tagging, and safe relocation",
    )
    p_cls.add_argument("path", nargs="?", default=None, help="Target file or directory to inspect/classify")
    p_cls.add_argument("--root", help="Root directory of the SSD / workspace")
    p_cls.add_argument("--suggest", action="store_true", help="Display classification recommendations (default)")
    p_cls.add_argument("--dry-run", action="store_true", help="Simulate relocation and collision handling without moving files")
    p_cls.add_argument("--apply", action="store_true", help="Safely move files into recommended taxonomy directories")
    p_cls.add_argument("--json", action="store_true", help="Output classification report in JSON format")
    p_cls.add_argument("--no-recursive", action="store_true", help="Do not scan subdirectories recursively")
```

### 8.2 Import & Dispatch Table
Add import:
```python
from smart_drive.cli.cmd_classify import cmd_classify
```
Add to `dispatch` mapping in `main()`:
```python
    dispatch = {
        ...
        "snapshot": cmd_snapshot,
        "backup": cmd_backup,
        "classify": cmd_classify,
    }
```

---

## 9. Comprehensive Unit Test Plan: `tests/test_classifier.py`

The test suite will be 100% standard library `unittest`, using `TempWorkspace` from `tests/helpers.py`.

### 9.1 Synthetic Test Fixtures Matrix

| Format | Generator Method / Payload Structure |
|---|---|
| **GGUF** | `b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 128) + struct.pack("<Q", 64) + b"\x00" * 32` |
| **Safetensors** | `header = json.dumps({"w": {"dtype": "F16", "shape": [10, 10], "data_offsets": [0, 200]}}).encode("utf-8")` + `struct.pack("<Q", len(header)) + header + b"\x00" * 200` |
| **ONNX** | `b"\x08\x07\x12\x0apytorch_ai\x1a\x051.13\x22\x04onnx"` |
| **PyTorch (Zip)** | Construct in-memory or on-disk zip containing `archive/data.pkl` and `archive/byteorder` |
| **PyTorch (Pickle)** | `b"\x80\x02ctorch._utils\n_rebuild_tensor\nq\x01."` |
| **HuggingFace Config** | `config.json` with `{"architectures": ["LlamaForCausalLM"], "model_type": "llama", "vocab_size": 32000}` |
| **Parquet** | `b"PAR1" + b"\x00" * 64 + b"PAR1"` |
| **Arrow** | `b"ARROW1\x00\x00" + b"\x00" * 32` |
| **HDF5** | `b"\x89HDF\r\n\x1a\n" + b"\x00" * 32` |
| **JSONL** | `{"id": 1, "text": "sample"}\n{"id": 2, "text": "data"}\n` |
| **CSV** | `"col1,col2,col3\nval1,val2,val3\nval4,val5,val6\n"` |
| **TSV** | `"col1\tcol2\tcol3\nval1\tval2\tval3\nval4\tval5\tval6\n"` |
| **PDF** | `b"%PDF-1.7\n%stream test content\n%%EOF\n"` |
| **ePub** | Zip archive with member `mimetype` storing text `application/epub+zip` |
| **Markdown** | `"# Research Notes\n\n- Finding A\n- Finding B\n"` |
| **Git Repo** | Directory containing `.git/HEAD` and `.git/config` |
| **Rust Project** | Directory containing `Cargo.toml` with `[package]\nname = "rust_app"\n` |
| **Node Project** | Directory containing `package.json` with `{"name": "node-app", "version": "1.0.0"}` |
| **Python Project** | Directory containing `pyproject.toml` with `[project]\nname = "py-app"\n` |

### 9.2 Test Cases Matrix

1. **`TestFormatDetectors`**:
   - `test_detect_gguf`: Validates GGUF v3 magic bytes, tensor count extraction.
   - `test_detect_safetensors`: Validates uint64 header parsing and tensor count.
   - `test_detect_onnx`: Validates protobuf wire format and onnx signature.
   - `test_detect_pytorch_zip`: Validates PyTorch zip archive detection.
   - `test_detect_pytorch_pickle`: Validates legacy pickle protocol stream.
   - `test_detect_hf_config`: Validates HuggingFace model config JSON.
   - `test_detect_parquet`: Validates Parquet header/footer magic bytes.
   - `test_detect_arrow`: Validates Arrow IPC magic bytes.
   - `test_detect_hdf5`: Validates HDF5 8-byte signature.
   - `test_detect_jsonl`: Validates multi-line JSON stream detection.
   - `test_detect_csv_tsv`: Validates tabular CSV/TSV sniffing.
   - `test_detect_pdf`: Validates `%PDF-` header and version.
   - `test_detect_epub`: Validates ePub zip container with mimetype.
   - `test_detect_markdown`: Validates Markdown notes.
   - `test_detect_project_repos`: Validates Git, Rust, Node, Python directories.
   - `test_empty_file_handling`: Ensures 0-byte files do not crash.
   - `test_corrupt_file_handling`: Ensures truncated/malformed headers fallback cleanly.
2. **`TestCollisionAvoidance`**:
   - `test_no_collision`: Resolves cleanly to canonical path.
   - `test_single_collision`: Existing destination causes `_1` suffix before extension.
   - `test_multiple_collisions`: Existing `file.gguf` and `file_1.gguf` resolves to `file_2.gguf`.
   - `test_directory_collision`: Existing `repo` causes `repo_1`.
   - `test_batch_internal_collision`: Two source files with same filename in different directories avoid colliding with each other in target.
   - `test_already_in_place`: File already at target path is skipped without renaming.
3. **`TestSafeguards`**:
   - `test_protected_root_files_untouched`: `gemini.md`, `agents.md`, `readme.md` are never classified or relocated.
   - `test_protected_root_dirs_untouched`: `01_AI_Models`, `02_Learning_Knowledge` are never treated as movable items.
4. **`TestClassifierCLI`**:
   - `test_cli_suggest_table`: Verifies `--suggest` outputs table and returns 0.
   - `test_cli_suggest_json`: Verifies `--suggest --json` outputs valid parseable JSON.
   - `test_cli_dry_run`: Verifies `--dry-run` simulates moves without touching disk.
   - `test_cli_apply`: Verifies `--apply` actually moves files to correct sub-taxonomies.
   - `test_cli_apply_with_collision`: Verifies `--apply` safely renames on collision.

---

## 10. Risk Assessment & Mitigation

| Risk | Impact | Mitigation Strategy |
|---|---|---|
| Accidental overwrite of existing model or dataset | Critical | Enforce strict collision check (`resolve_destination`) which iterates `_1`, `_2` before any move. |
| Moving protected root workspace files | High | Enforce `is_protected_root_file` and `is_protected_root_dir` filters before any classification. |
| Memory exhaustion from large model headers | Medium | Cap header inspection reads to `65,536` bytes. Safetensors sanity checks length `< 100MB`. |
| Unhandled binary exception during inspection | Low | Wrap all header unpacks and JSON loads in defensive try/except blocks, returning fallback inspection. |

---

## 11. Verification Checklist for Downstream Implementer

1. Run existing test suite before and after implementation: `python -m unittest discover tests`.
2. Ensure all newly added tests in `tests/test_classifier.py` pass 100%.
3. Verify CLI execution:
   - `python -m smart_drive.cli.main classify --suggest`
   - `python -m smart_drive.cli.main classify --dry-run`
   - `python -m smart_drive.cli.main classify --apply`
   - `python -m smart_drive.cli.main classify --json`
4. Confirm zero external dependencies (no pip packages imported).
