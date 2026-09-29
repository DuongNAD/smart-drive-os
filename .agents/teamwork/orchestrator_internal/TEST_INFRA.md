# SmartDrive-OS Internal Secondary Drive Suite: Test Infrastructure & Specification

## Document Metadata
- **Scope**: Internal Secondary Drive Architect & C-Drive Cache Offloader (Milestones M1–M4)
- **Author**: Test Writer Agent (`test_writer_internal`)
- **Target Repository**: `DuongNAD/smart-drive-os` (branch: `internal-secondary-drive`)
- **Integrity Level**: Production / High Assurance (Zero External Dependencies)
- **Status**: PUBLISHED & ACTIVE

---

## 1. Test Architecture & Principles

### 1.1 Zero-Dependency Pure Python Test Architecture
All tests are implemented strictly with the Python Standard Library (`unittest`, `subprocess`, `tempfile`, `pathlib`, `json`, `os`, `shutil`, `unittest.mock`). No third-party testing frameworks (e.g., pytest, tox) or external packages are permitted, preserving SmartDrive-OS's core architectural principle of 100% Zero-Dependency operation.

### 1.2 Opaque-Box CLI Subprocess Testing
CLI commands are tested end-to-end as opaque executables via `subprocess.run([sys.executable, "-m", "smart_drive", ...])`. The test runner validates observable contracts:
- Process exit codes (0 for success, 1 for runtime/business error, 2 for argparse syntax error).
- Standard output (`stdout`) formats: structured JSON payloads (when `--json` is supplied) and formatted human-readable terminal output.
- Standard error (`stderr`) error reporting and warning messages.
- Filesystem side-effects on disk: directory structures, manifests, SQLite databases, and NTFS junctions.

### 1.3 Safe Sandbox & Mockable Drive Isolation
To ensure 100% host isolation and prevent accidental modification of real production files, real user caches, or physical drive partitions:
- **Sandbox Workspaces**: Each test fixture allocates an isolated temporary workspace directory (`TempWorkspace` / `tempfile.TemporaryDirectory`).
- **User Environment Redirection**: C-drive user caches (`~/.cache/huggingface`, `~/AppData/Local/pip`, etc.) are redirected to temporary sandboxes by injecting simulated environment variables (`USERPROFILE`, `HOME`, `LOCALAPPDATA`, `APPDATA`) into subprocess calls.
- **Mock Drive Roots**: Secondary drive mount points (e.g. `D:\`) are mapped to isolated test directories.
- **Deterministic Oracles**: Every test verifies expected outputs against mathematical properties, documented interface contracts in `PROJECT.md`, or reference models.

---

## 2. 4-Tier Test Methodology

The test suite applies a strict 4-tier testing hierarchy to achieve exhaustive coverage across all features:

```
┌────────────────────────────────────────────────────────┐
│  Tier 4: Real-World Scenarios & Workstation Lifecycle  │
├────────────────────────────────────────────────────────┤
│  Tier 3: Pairwise Combinations & Format Equivalence   │
├────────────────────────────────────────────────────────┤
│  Tier 2: Boundary, Fault Injection & Edge Cases        │
├────────────────────────────────────────────────────────┤
│  Tier 1: Comprehensive Feature & Interface Contracts   │
└────────────────────────────────────────────────────────┘
```

### Tier 1: Feature Coverage (Happy Path & Contracts)
- Tests all positive functional requirements across every module and CLI command.
- Verifies exact field names, data types, and status values in both text and JSON outputs.
- Exercises primary workflows: drive enumeration, hardware classification, filesystem adaptation, cache scanning, cache offloading, junction linking, cache reverting, profile initialization, and SSD health reporting.

### Tier 2: Boundary, Fault Injection & Corner Cases
- Evaluates behavior under boundary conditions: empty directories, 0-byte files, maximum path lengths, and non-existent drives.
- Tests invalid inputs: malformed drive letters, invalid profile names, unknown cache names, non-numeric parameters.
- Validates critical security & safety invariants:
  - Strict C: drive rejection as offload target (`--target C:` must fail).
  - Transactional rollback on mid-move failures.
  - Safe junction removal without target directory data loss.
  - Read-only filesystem handling and permission error resilience.

### Tier 3: Pairwise Combinations & Flag Interoperability
- Tests orthogonal flag combinations:
  - `--json` combined with every subcommand and action (`--scan`, `--move`, `--revert`, `health`, `init`).
  - `--profile internal-developer-vault` combined with `--force`, custom paths, and drive letters.
  - Explicit drive letter argument vs default auto-detected drive.
  - NTFS target filesystem vs exFAT target filesystem.
- Verifies format equivalence: ensuring structured JSON output contains equivalent or richer semantic information compared to terminal text output.

### Tier 4: Real-World Scenarios & End-to-End Lifecycle
- Multi-step realistic developer workstation workflows:
  1. Initialize freshly attached secondary drive `D:` with `--profile internal-developer-vault`.
  2. Perform `smart-drive health D:` to verify SSD TRIM and filesystem geometry.
  3. Perform `smart-drive offload --scan` to discover heavy AI and runtime caches on `C:`.
  4. Sequentially offload multiple caches (`huggingface`, `npm`, `uv`) to `D:\04_System_Offload_Caches`.
  5. Verify NTFS Directory Junctions transparently redirect read/write access.
  6. Revert selected cache back to `C:` and verify complete restoration and junction unlinking.
  7. Verify idempotency across consecutive runs.

---

## 3. Feature Inventory & Coverage Matrix

| Feature ID | Feature Name | Milestone | Tier 1 (Feature) | Tier 2 (Boundary & Corner) | Tier 3 (Pairwise) | Tier 4 (Real-World) |
|---|---|---|---|---|---|---|
| **F01** | Secondary Drive Enumeration | M1 (R1) | Enumerate D:, E:, etc.; exclude C: | Non-existent drive, system-only drive | Subprocess vs programmatic list | Multi-drive workstation enumeration |
| **F02** | Hardware Drive Type Detection | M1 (R1) | Fixed Internal vs Removable External | Unknown bus, virtual RAM disk | Bus type + filesystem combinations | NVMe internal SSD + USB external SSD |
| **F03** | Filesystem Adaptation | M1 (R1) | NTFS 4KB + junctions; exFAT slack guard | FAT32, unknown FS, corrupted labels | NTFS vs exFAT feature activation | Cross-filesystem migration validation |
| **F04** | Cache Discovery Engine | M2 (R2) | Scan known caches (HuggingFace, Ollama, pip, npm, uv) | Empty cache, missing cache, 0 bytes | `--scan` text vs `--scan --json` | Multi-gigabyte developer workstation cache |
| **F05** | NTFS Directory Junction Engine | M2 (R2) | Create junction, check is_junction, get target, remove | Target missing, target is file, unicode path | Cross-volume junction (C: to D:) | Nested junction link/unlink lifecycle |
| **F06** | Transactional Offload & Revert | M2 (R2) | Safe 7-phase move to `04_System_Offload_Caches`, junction creation, revert | Reject target C:, rollback on move error, double revert | `--move` + `--target`, `--revert` + `--json` | Offload multiple AI models, verify app access, revert |
| **F07** | Internal Developer Vault Profile | M3 (R3) | 6 taxonomies (`01_AI_Models`..`06_Archives_Storage`), shields, manifests, FTS5 | Invalid profile name, read-only path | `--profile` + `--force`, custom root path | Cold drive init -> cache offload landing |
| **F08** | SSD Health & TRIM Monitor | M3 (R4) | Query TRIM status (`fsutil`), geometry, free bytes | Low free space warning, unknown TRIM, invalid drive | Explicit drive vs default, text vs JSON | Workstation diagnostic routine |
| **F09** | Unified CLI Subcommands | M4 (R1-R5) | `smart-drive offload`, `smart-drive health`, `smart-drive init` | Subprocess exit codes, unknown flags | All CLI flags + pipe to JSON parser | Comprehensive CLI automated execution |

---

## 4. Test Suite Implementation Structure

```
tests/
├── helpers.py                       # Existing test fixtures, CLUSTER_SIZE, run_smart_drive_cli, TempWorkspace
├── test_drive_detector.py           # [M1 Unit] Drive enumeration, bus type, filesystem adapter
├── test_junction.py                 # [M2 Unit] NTFS directory junction creation, detection, removal
├── test_offloader.py                # [M2 Unit] Cache scanner catalog, transactional offload & revert
├── test_internal_vault.py           # [M3 Unit] Profile registration, taxonomy protection in config.py
├── test_health.py                   # [M3 Unit] SSD TRIM query, geometry analysis, warning alerts
└── test_cli_internal_e2e.py         # [M4 E2E] Unified Opaque-Box CLI Subprocess Test Suite
```

---

## 5. Execution & Verification Commands

### 5.1 Run Unified E2E Internal CLI Suite
```powershell
python -m unittest tests/test_cli_internal_e2e.py -v
```

### 5.2 Run Full SmartDrive-OS Test Suite (Baseline + New Modules)
```powershell
python -m unittest discover tests -v
```

### 5.3 Quality Gate Criteria
1. **100% Pass Rate**: Zero failures and zero unhandled errors across all 300+ existing tests and new internal drive tests.
2. **Zero Dependencies**: Pure Python standard library only; must run on standard Python 3.10+ Windows environments.
3. **Host Safety**: Zero modifications to real host files outside temporary test sandboxes.
4. **Idempotency**: Running tests repeatedly produces deterministic results.
