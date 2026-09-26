# Hard Handoff Report: Milestone 3 (Intelligent Classifier & Auto-Tagger)

**Date:** 2026-09-26T07:19:00Z  
**Agent:** Explorer M3  
**Target Milestone:** M3 (`smart-drive classify`)  
**Status:** COMPLETE (Ready for Implementation)  
**Deliverable Document:** `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_m3\analysis.md`  

---

## 1. Observation

1. **Current Codebase Test Baseline:**
   - Ran `python -m unittest discover tests` from `d:\teamwork_projects\smart_drive_os`.
   - Result:
     ```
     Ran 257 tests in 37.503s
     OK
     ```
   - Observed that all 257 tests across M1 (`smart_drive/ui`, `tests/test_ui.py`, `tests/test_ui_adversarial.py`) and M2 (`smart_drive/core/snapshot.py`, `tests/test_snapshot.py`, `tests/test_adversarial_snapshot.py`) pass 100%.

2. **Milestone 3 Requirements (`ORIGINAL_REQUEST.md:29-36`):**
   - Quote:
     ```markdown
     ### R3. Bộ phân loại thông minh & Auto-Tagger (`smart-drive classify`)
     - Engine nhận diện sâu các loại định dạng tệp chuyên dụng:
       - AI Models & Weights: GGUF, Safetensors, ONNX, PyTorch .pt/.pth, HuggingFace model configs.
       - Datasets: Parquet, Arrow, JSONL, CSV, HDF5.
       - Nghiên cứu & Tài liệu: PDF, ePub, Markdown notes.
       - Source Code: Tự nhận diện cấu trúc repo git, dự án Node/Python/Rust.
     - Hỗ trợ chế độ đề xuất di chuyển thông minh (`smart-drive classify --suggest`) và tự động gom file phân loại vào đúng taxonomy con an toàn.
     ```

3. **Scope and File Boundaries (`orchestrator/PROJECT.md:75-80, 87, 92, 98`):**
   - Quote:
     ```markdown
     ### M3 (Classifier) ↔ CLI & Core
     - ClassifierEngine(root_path: Path)
       - classify_file(filepath: Path) -> ClassificationResult(category: str, subcategory: str, confidence: float, recommended_path: str, reason: str)
       - scan_and_classify(target_dir: Path, suggest_only: bool = True) -> List[ClassificationResult]
       - apply_organization(results: List[ClassificationResult], dry_run: bool = True) -> OrganizationReport
     - smart_drive/core/classifier.py: Deep file recognition and auto-tagging engine (M3)
     - smart_drive/cli/cmd_classify.py: Subcommand handler for smart-drive classify (M3)
     - tests/test_classifier.py: Unit tests for deep file classification and auto-tagging (M3)
     ```

4. **CLI Integration Contract (`smart_drive/cli/main.py:44-60, 317-353`):**
   - `build_parser()` currently registers subcommands 1 through 15 (`init`, `status`, `audit`, `clean`, `search`, `organize`, `sentinel`, `mcp`, `mcp-config`, `dup`, `index`, `update`, `ui`, `snapshot`, `backup`). Subcommand `classify` is missing from `build_parser()` and `dispatch` table.

5. **ExFAT & Protection Architecture (`smart_drive/core/config.py:108-240`):**
   - `PROTECTED_ROOT_DIRS` contains inviolable root taxonomies (`01_ai_models`, `02_learning_knowledge`, etc.) and agent workspaces (`.agents`, `smart_ssd_workspace`).
   - `PROTECTED_ROOT_FILES` protects root orchestration files (`gemini.md`, `agents.md`, `readme.md`, `.mcp.json`, scripts).
   - Functions `is_protected_root_dir` and `is_protected_root_file` exist and must be checked to prevent unlawful movement of system files.

---

## 2. Logic Chain

1. **Format Detection Mechanism (linking Observation 2 to `smart_drive/core/classifier.py`):**
   - Because standard library only is allowed (zero pip dependencies), each file format must be parsed via built-in modules:
     - `struct` for binary offsets and unpacks: GGUF (`b"GGUF"` + `<I` version + `<Q` counts), Safetensors (`<Q` header length), Parquet (`b"PAR1"`), Arrow (`b"ARROW1"`), HDF5 (`b"\x89HDF\r\n\x1a\n"`).
     - `json` for UTF-8 metadata strings in Safetensors headers, HuggingFace model `config.json`, and JSONL streaming lines.
     - `zipfile` for ePub containers (inspecting `mimetype` member == `application/epub+zip`) and modern PyTorch `.pt`/`.pth` archives (inspecting `archive/data.pkl`).
     - Pickle stream inspection for legacy PyTorch files (`0x80` protocol opcodes + torch symbols).
     - `csv.Sniffer` for tabular CSV and TSV structure verification.
     - `pathlib` for project directory marker detection (`.git`, `Cargo.toml`, `package.json`, `pyproject.toml`).
   - Therefore, a dedicated `ClassifierEngine` class with granular format detectors can identify all requested file types with 90-100% confidence.

2. **Safe Relocation and Collision Avoidance (linking Observation 2 & 5 to `resolve_destination`):**
   - When moving files via `--apply`, overwriting an existing file could lead to unrecoverable data loss.
   - Therefore, the destination resolver must check if the target path exists. If it exists on disk OR was allocated earlier in the same batch, it must dynamically append numerical suffixes before the file extension (`file_1.ext`, `file_2.ext`) or directory name (`project_1`).
   - If an item is already located at its canonical sub-taxonomy path, the engine must skip relocation (`SKIPPED_ALREADY_IN_PLACE`).

3. **Sub-Taxonomy Hierarchy (linking Observation 2 to Routing):**
   - Canonical target sub-taxonomies are derived directly from the system specification:
     - `01_AI_Models/Weights/` for GGUF, Safetensors, ONNX, PyTorch.
     - `01_AI_Models/Datasets/` for Parquet, Arrow, HDF5, JSONL, CSV/TSV.
     - `01_AI_Models/Configs/` for HuggingFace model configs.
     - `02_Learning_Knowledge/Papers/` for research PDFs.
     - `02_Learning_Knowledge/Books/` for ePub eBooks.
     - `02_Learning_Knowledge/Notes/` for Markdown notes.
     - `03_Development_Projects/<repo_name>/` for full Git, Rust, Node, Python project directories.
     - `05_Dev_Toolbox/Scripts/` for standalone dev scripts.
     - `06_Archives_Storage/Archives/` for general archives.

4. **CLI User Experience (linking Observation 2 & 4 to `smart_drive/cli/cmd_classify.py`):**
   - The CLI must provide three primary modes:
     - `--suggest` (default): outputs clear tables showing file, format, confidence, and recommended destination.
     - `--dry-run`: simulates relocations, printing source -> destination mappings and collision resolutions.
     - `--apply`: executes physical `shutil.move` operations with parent directory creation and collision resolution.
     - `--json`: outputs machine-readable JSON for scripting.
   - Registering `classify` in `smart_drive/cli/main.py` exposes this subcommand to the user and automation tools.

5. **Test Strategy (linking Observation 1 to `tests/test_classifier.py`):**
   - To guarantee 100% pass without external mock frameworks, tests will synthesize byte sequences for each format (authentic GGUF header, Safetensors byte buffer, Parquet magic bytes, zip files for ePub and PyTorch, etc.).
   - Sandboxes are isolated via `TempWorkspace` from `tests/helpers.py`.

---

## 3. Caveats

1. **Large Model Files:** Safetensors header inspection could theoretically encounter massive JSON headers. The engine guards against this by sanity-checking that the header length is `< 100,000,000` bytes and reading only what is needed.
2. **Generic CSV vs AI Datasets:** CSV files can represent general spreadsheets or AI datasets. In the context of SmartDrive-OS auto-tagging, tabular CSVs detected during classification are routed to `01_AI_Models/Datasets/` (or `02_Learning_Knowledge/Notes/` depending on context).
3. **No External Libraries:** PyTorch or HuggingFace libraries are NOT imported or required; inspection is strictly binary and structural, ensuring 100% standard library compliance.

---

## 4. Conclusion

The architectural design for Milestone 3 is complete, validated against existing code, and fully documented in `d:\teamwork_projects\smart_drive_os\.agents\teamwork\explorer_m3\analysis.md`. Downstream agents (Worker M3) can immediately implement:
- `smart_drive/core/classifier.py`
- `smart_drive/cli/cmd_classify.py`
- Subparser integration in `smart_drive/cli/main.py`
- Pure stdlib test suite in `tests/test_classifier.py`

---

## 5. Verification Method

To independently verify the implementation:
1. **Run Unit Tests:**
   ```powershell
   python -m unittest discover tests
   ```
   *Expected:* All existing 257 tests plus all new tests in `tests/test_classifier.py` pass cleanly (`OK`).

2. **Verify CLI Subcommand:**
   ```powershell
   python -m smart_drive.cli.main classify --help
   python -m smart_drive.cli.main classify --suggest
   python -m smart_drive.cli.main classify --dry-run
   python -m smart_drive.cli.main classify --json
   ```
   *Expected:* Help displays options (`--suggest`, `--dry-run`, `--apply`, `--json`, `--no-recursive`); each mode executes without exception and returns exit code 0.

3. **Invalidation Conditions:**
   - Any dependency on `torch`, `transformers`, `pyarrow`, `pandas`, `h5py`, or non-stdlib packages.
   - Any overwrite of existing files on disk during `--apply`.
   - Any relocation of protected root files (`gemini.md`, `agents.md`, `readme.md`, `.mcp.json`).
   - Failure of any existing test in the test suite.
