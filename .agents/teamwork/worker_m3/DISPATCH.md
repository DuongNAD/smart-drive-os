## 2026-09-26T07:18:57Z
You are Worker M3 (Classifier & Auto-Tagger Developer) for SmartDrive-OS v1.1.0 Milestone 3: Intelligent Classifier & Auto-Tagger (`smart-drive classify`).
Your working directory is: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_m3`
The project root is: `d:\teamwork_projects\smart_drive_os`
The original user request is at: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\ORIGINAL_REQUEST.md` (You MUST read this file first).
The project scope document is at: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\orchestrator\PROJECT.md`.
Explorer M3's technical blueprint and code templates are at:
- Analysis: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_m3\analysis.md`
- Handoff: `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_m3\handoff.md`

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

File Ownership:
You own and may create/edit exclusively:
- `smart_drive/core/classifier.py`
- `smart_drive/cli/cmd_classify.py`
- `smart_drive/cli/main.py` (registering `classify` subparser and dispatch entry)
- `tests/test_classifier.py`

Objective & Requirements:
1. Implement pure standard library classification engine (`smart_drive/core/classifier.py`):
   - Strict zero external dependencies (pure Python standard library: `json`, `shutil`, `os`, `pathlib`, `struct`, `zipfile`, `csv`, `typing`, `dataclasses`).
   - Deep inspection and format detectors:
     - AI Models & Weights: GGUF (`b"GGUF"`), Safetensors (uint64 header length + JSON parser), ONNX, PyTorch (zip archive with `archive/` or pickle opcodes `\x80\x02`..`\x04`), HuggingFace configs (`config.json` containing `"architectures"` or `"model_type"`).
     - Datasets: Parquet (`b"PAR1"`), Arrow (`b"ARROW1"`), HDF5 (`b"\x89HDF\r\n\x1a\n"`), JSONL (newline-delimited JSON objects), CSV/TSV (`csv.Sniffer`).
     - Research & Docs: PDF (`b"%PDF-"`), ePub (zip container with `mimetype` == `application/epub+zip`), Markdown (`.md`/`.markdown`).
     - Project Repositories: Git (`.git`), Rust (`Cargo.toml`), Node (`package.json`), Python (`pyproject.toml`, `setup.py`, `requirements.txt`).
   - Canonical sub-taxonomy routing:
     - `01_AI_Models/Weights/`
     - `01_AI_Models/Datasets/`
     - `01_AI_Models/Configs/`
     - `02_Learning_Knowledge/Papers/`
     - `02_Learning_Knowledge/Books/`
     - `02_Learning_Knowledge/Notes/`
     - `03_Development_Projects/<repo_name>/`
   - Safe relocation & Collision avoidance (`resolve_destination`):
     - Append `_1`, `_2` numerical suffixes if destination already exists or is claimed in the batch.
     - Skip if file is already in canonical place (`SKIPPED_ALREADY_IN_PLACE`).
     - Enforce `SecurityGuard` / protection checks: NEVER move protected root files (`GEMINI.md`, `README.md`, etc.) or anti-indexing shields.
2. Implement CLI command (`smart_drive/cli/cmd_classify.py` & `smart_drive/cli/main.py`):
   - `smart-drive classify [target_dir]` supporting:
     - `--suggest` (default): outputs clear table of detected types, confidence, and recommended target path.
     - `--dry-run`: simulates relocations with source -> destination mappings and collision resolutions.
     - `--apply`: executes physical moves safely.
     - `--json`: machine-readable output.
     - `--no-recursive`: scan only top-level directory.
   - Register `classify` in `build_parser()` in `smart_drive/cli/main.py` and bind `"classify": cmd_classify` in the dispatch map.
3. Implement comprehensive unit tests (`tests/test_classifier.py`):
   - Synthesize valid byte sequences for all formats (GGUF, Safetensors, ONNX, PyTorch zip/pickle, HF configs, Parquet, Arrow, HDF5, JSONL, CSV, PDF, ePub, Markdown).
   - Test project repo detection (Git, Node, Python, Rust).
   - Test `--suggest`, `--dry-run`, and `--apply` relocation.
   - Test collision avoidance with multiple identical target filenames.
   - Test protected root file immunity.
4. Run tests:
   - Run `python -m unittest tests/test_classifier.py`
   - Run `python -m unittest discover tests` (ensure all 257 existing tests + new classifier tests pass 100%).
5. Document results and commands in `d:\teamwork_projects\smart_drive_os\.agents\teamwork\worker_m3\handoff.md`.
6. Send a message back to parent orchestrator with the outcome.
