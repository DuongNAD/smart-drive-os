## 2026-09-26T07:14:12Z

You are Explorer M3 for SmartDrive-OS v1.1.0 Milestone 3: Intelligent Classifier & Auto-Tagger (`smart-drive classify`).
Your working directory is: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_m3`
The project root is: `d:\teamwork_projects\smart_drive_os`
The original user request is at: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md` (You MUST read this file first).
The project scope document is at: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\orchestrator\PROJECT.md`.

Objective:
Formulate an exact technical implementation blueprint for Milestone 3 (Features F19 through F24):
1. Package Structure & Files:
   - `smart_drive/core/classifier.py`: Deep inspection engine (`ClassifierEngine`), file format detectors, taxonomy routing rules, safe moving/copying logic.
   - `smart_drive/cli/cmd_classify.py`: CLI command handler for `smart-drive classify`.
   - Update `smart_drive/cli/main.py`: Register `classify` subparser and dispatch handler.
   - `tests/test_classifier.py`: Comprehensive test suite using pure standard library `unittest`.
2. Detailed Technical Requirements:
   - Zero external dependencies: Strict 100% Python Standard Library (`json`, `shutil`, `os`, `pathlib`, `struct`, `zipfile`, `typing`, `dataclasses`).
   - Deep File Format Recognition (inspecting magic bytes / headers + extensions):
     - AI Models & Weights:
       - GGUF: magic bytes `b"GGUF"`
       - Safetensors: uint64 header length + JSON parser verifying tensor metadata
       - ONNX: Protobuf wire format checks or `.onnx` model headers
       - PyTorch: `.pt`/`.pth` zip container (`PK\x03\x04` containing `archive/`) or pickle stream (`b"\x80\x02"`, `b"\x80\x03"`, `b"\x80\x04"`)
       - HuggingFace configs: `config.json` containing `"architectures"` or `"model_type"`
     - Datasets:
       - Parquet: magic bytes `b"PAR1"`
       - Arrow: magic bytes `b"ARROW1"`
       - HDF5: magic bytes `b"\x89HDF\r\n\x1a\n"`
       - JSONL: newline-delimited JSON objects
       - CSV: comma/tab-separated tabular data with headers
     - Research & Documents:
       - PDF: starts with `b"%PDF-"`
       - ePub: zip container with `mimetype` containing `application/epub+zip`
       - Markdown: `.md`/`.markdown` notes
     - Source Code & Project Repositories:
       - Git repos: directory containing `.git`
       - Node: directory containing `package.json`
       - Python: directory containing `pyproject.toml` or `setup.py`
       - Rust: directory containing `Cargo.toml`
   - Taxonomy Routing & Safe Auto-Tagging:
     - Target canonical sub-taxonomies (e.g. `01_AI_Models/Weights/`, `01_AI_Models/Datasets/`, `02_Learning_Knowledge/Papers/`, `02_Learning_Knowledge/Notes/`, `03_Development_Projects/<repo_name>/`).
     - `--suggest`: Outputs clear table or JSON of classifications and recommended destinations without modifying filesystem.
     - `--dry-run`: Simulates moving/tagging files, printing exact source -> target mappings and collision handling.
     - `--apply`: Moves files safely to target directories. Never overwrites existing files (appends numerical suffix `_1`, `_2` if target filename exists).
     - `--json`: Machine-readable output.
   - Unit test strategy:
     - Synthetic test file generation with authentic magic bytes for all formats.
     - Verification of classification confidence and suggested path.
     - Safe relocation and collision avoidance testing.
3. Document complete design, schemas, algorithms, and code templates in `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_m3\analysis.md` and write a hard handoff report at `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_m3\handoff.md`.
