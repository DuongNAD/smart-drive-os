# E2E Test Infra: SmartDrive-OS

## Test Philosophy
- Opaque-box, requirement-driven. Validates user-facing functionality and security guarantees without internal module coupling.
- Methodology: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial Testing + Real-World Workload Testing.

## Test Architecture
- Test Runner: `python3 -m unittest discover tests` (or `pytest`).
- Baseline Suite: 565 tests across `tests/`.
- Target Pass Rate: 100% active tests passing (554 passed, 0 failures, 0 errors, 11 platform skips).
- Additional E2E & Adversarial verification: Dedicated test suite verifying R1 (token efficiency & registrar), R2 (path traversal & UNC escape), R3 (zero-dependency & stdio stream integrity), and R4 (portable launcher execution & bytecode slack prevention).

## Feature Inventory Coverage Across Tiers
| # | Feature | Requirement | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Pairwise) | Tier 4 (Workload) |
|---|---------|-------------|:----------------:|:-----------------:|:-----------------:|:-----------------:|
| 1 | Universal Path Traversal Defense | R2 | ≥5 | ≥5 | ✓ | ✓ |
| 2 | Forbidden Characters & Cross-Drive | R2 | ≥5 | ≥5 | ✓ | ✓ |
| 3 | Core Cross-Platform Consistency | R2 | ≥5 | ≥5 | ✓ | ✓ |
| 4 | MCP Token-Efficient Schemas & Pagination | R1 | ≥5 | ≥5 | ✓ | ✓ |
| 5 | Agent System Descriptions & Prompts | R1 | ≥5 | ≥5 | ✓ | ✓ |
| 6 | Zero-Dependency & stdio Stream Integrity | R3 | ≥5 | ≥5 | ✓ | ✓ |
| 7 | Multi-Agent Registrar 1-Click | R1 | ≥5 | ≥5 | ✓ | ✓ |
| 8 | Portable Launchers & 5-Tier Self-Check | R4 | ≥5 | ≥5 | ✓ | ✓ |

## Coverage Goals
- Tier 1: ≥5 test cases per feature (Isolated happy-path checks).
- Tier 2: ≥5 boundary cases per feature (Escapes, extreme inputs, null bytes, long paths, non-ASCII Unicode).
- Tier 3: Pairwise combinations (e.g. `ssd_search` with pagination and unicode query under rate-limiting).
- Tier 4: Real-world workflows (Full autonomous agent session: register -> init -> update_index -> search -> clean -> audit).
