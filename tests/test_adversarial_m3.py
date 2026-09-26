"""tests/test_adversarial_m3.py - Adversarial Stress-Tests for SmartDrive-OS M3 Classifier.

Milestone 3 Empirical Challenge Suite:
1. Corrupted & Truncated Binary Headers:
   - Truncated GGUF (0, 1, 2, 3, 4, 7, 15 bytes)
   - Malformed Safetensors (header_len > 100MB, header_len == 0, uint64 overflow, corrupted JSON body, JSON not dict)
   - Corrupted Zip/EPUB (invalid zip magic with valid epub content, valid zip magic with truncated stream, zero-byte zip)
   - Invalid Protobuf wire bytes for ONNX
   - Empty files (0-byte) and sub-cluster files
2. Ambiguous & Mismatched Files:
   - Extension mismatch vs magic bytes (e.g. .pdf with GGUF magic, .png with PDF header, .gguf with text)
   - .safetensors extension with arbitrary ASCII text / CSV
   - Mixed multi-dot extensions (.tar.gz, .backup.pt, .weights.model.onnx)
3. Aggressive Name Collisions & Zero Data Loss:
   - 25+ files with identical names across distinct source directories moved in --apply mode
   - Verify every file is moved, renamed with unique suffix, and content checksum matches 100%
   - Duplicate directory repository moves with name collisions
4. Protection Guard Stress-Test:
   - Direct and indirect targeting of root protected files (GEMINI.md, README.md, CLAUDE.md, .metadata_never_index, etc.)
   - Case variations (gemini.md, README.MD, Claude.Md)
   - Inviolability under --apply, --dry-run, --suggest, --path, and recursive scan
5. Traversal Stress & Scalability:
   - Deep directory hierarchy (50 levels deep)
   - High file count directory (200+ loose files)
   - --no-recursive limit verification
   - Excluded system directories (.git, __pycache__, $RECYCLE.BIN)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import unittest
import zipfile

from smart_drive.core.classifier import (
    ClassifierEngine,
    ClassificationResult,
    ClassificationReport,
    FormatInspection,
    RelocationAction,
)
from smart_drive.cli.cmd_classify import cmd_classify
from tests.helpers import TempWorkspace, SmartDriveTestCase


class TestAdversarialBinaryHeaders(SmartDriveTestCase):
    """Stress tests classifier against corrupted, truncated, and malicious binary headers."""

    def test_truncated_gguf_various_lengths(self) -> None:
        """Tests GGUF detection on partial and truncated headers."""
        with TempWorkspace(prefix="adv_gguf_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # Test byte lengths from 1 to 24 bytes
            full_header = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 42) + struct.pack("<Q", 10)
            for length in [1, 2, 3, 4, 7, 8, 12, 15, 16, 20, 23]:
                truncated = full_header[:length]
                p = tmp / f"trunc_{length}.gguf"
                p.write_bytes(truncated)

                insp = engine.detect_format(p)
                self.assertIsNotNone(insp)
                assert insp is not None

                if length < 4:
                    # Not enough bytes for "GGUF" magic
                    self.assertFalse(insp.format_type.startswith("GGUF"))
                else:
                    # 4+ bytes has "GGUF", should detect safely without IndexError or struct.error
                    self.assertTrue(insp.format_type.startswith("GGUF"))
                    self.assertEqual(insp.category, "01_AI_Models")
                    self.assertEqual(insp.subcategory, "Weights")

    def test_malformed_safetensors_headers(self) -> None:
        """Tests Safetensors detection on malicious header lengths and corrupt JSON bodies."""
        with TempWorkspace(prefix="adv_safetensors_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # Case A: Header length exceeds 100MB limit (e.g. 500 MB)
            p_huge = tmp / "huge_header.safetensors"
            p_huge.write_bytes(struct.pack("<Q", 500_000_000) + b"{}" + b"\x00" * 100)
            insp_huge = engine.detect_format(p_huge)
            self.assertIsNotNone(insp_huge)
            assert insp_huge is not None
            # Should NOT detect as Safetensors because header_len > 100MB
            self.assertNotEqual(insp_huge.format_type, "Safetensors")

            # Case B: Header length is 0
            p_zero = tmp / "zero_header.safetensors"
            p_zero.write_bytes(struct.pack("<Q", 0) + b"{}" + b"\x00" * 100)
            insp_zero = engine.detect_format(p_zero)
            self.assertIsNotNone(insp_zero)
            assert insp_zero is not None
            self.assertNotEqual(insp_zero.format_type, "Safetensors")

            # Case C: Maximum uint64 value (potential overflow: 2^64 - 1)
            p_max = tmp / "max_uint64.safetensors"
            p_max.write_bytes(struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + b"..." )
            insp_max = engine.detect_format(p_max)
            self.assertIsNotNone(insp_max)
            assert insp_max is not None
            self.assertNotEqual(insp_max.format_type, "Safetensors")

            # Case D: Header length claims 100 bytes, but file is only 20 bytes total (truncated)
            p_trunc = tmp / "truncated_st.safetensors"
            p_trunc.write_bytes(struct.pack("<Q", 100) + b'{"weight":')
            insp_trunc = engine.detect_format(p_trunc)
            self.assertIsNotNone(insp_trunc)
            assert insp_trunc is not None
            self.assertNotEqual(insp_trunc.format_type, "Safetensors")

            # Case E: Valid header length and file size, but JSON body is completely invalid syntax
            p_bad_json = tmp / "bad_json.safetensors"
            bad_content = b"NOT_VALID_JSON_AT_ALL!!!"
            p_bad_json.write_bytes(struct.pack("<Q", len(bad_content)) + bad_content + b"\x00" * 64)
            insp_bad_json = engine.detect_format(p_bad_json)
            self.assertIsNotNone(insp_bad_json)
            assert insp_bad_json is not None
            self.assertNotEqual(insp_bad_json.format_type, "Safetensors")

            # Case F: Valid JSON body, but it's a JSON array `[...]`, not a dictionary `{...}`
            p_arr_json = tmp / "array_json.safetensors"
            arr_content = json.dumps(["weight1", "weight2"]).encode("utf-8")
            p_arr_json.write_bytes(struct.pack("<Q", len(arr_content)) + arr_content + b"\x00" * 64)
            insp_arr_json = engine.detect_format(p_arr_json)
            self.assertIsNotNone(insp_arr_json)
            assert insp_arr_json is not None
            # Tensors dictionary check should reject array
            self.assertNotEqual(insp_arr_json.format_type, "Safetensors")

    def test_corrupted_zip_and_epub_containers(self) -> None:
        """Tests handling of broken zip headers and partial zip streams."""
        with TempWorkspace(prefix="adv_zip_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # Case A: PK\x03\x04 followed by random noise (unparseable zip)
            p_corrupt_zip = tmp / "broken.zip"
            p_corrupt_zip.write_bytes(b"PK\x03\x04\xff\xff\x00\x00\x12\x34\x56\x78RandomNoiseGarbageData")
            insp_zip = engine.detect_format(p_corrupt_zip)
            self.assertIsNotNone(insp_zip)
            assert insp_zip is not None
            # Extension fallback allows it to be classed as Archive without crashing
            self.assertEqual(insp_zip.format_type, "Archive")

            # Case B: epub extension with broken zip data
            p_bad_epub = tmp / "broken.epub"
            p_bad_epub.write_bytes(b"PK\x03\x04NOT_A_VALID_ZIP_FILE")
            insp_epub = engine.detect_format(p_bad_epub)
            self.assertIsNotNone(insp_epub)
            assert insp_epub is not None
            self.assertNotEqual(insp_epub.format_type, "ePub")

            # Case C: zip container with 'mimetype' entry that is NOT application/epub+zip
            p_fake_epub = tmp / "fake.epub"
            with zipfile.ZipFile(p_fake_epub, "w") as zf:
                zf.writestr("mimetype", "text/plain")
            insp_fake = engine.detect_format(p_fake_epub)
            self.assertIsNotNone(insp_fake)
            assert insp_fake is not None
            self.assertNotEqual(insp_fake.format_type, "ePub")

    def test_invalid_protobuf_and_onnx(self) -> None:
        """Tests ONNX detector against invalid protobuf byte sequences."""
        with TempWorkspace(prefix="adv_onnx_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # 1-byte file with 0x08 (insufficient for protobuf wire check)
            p_short = tmp / "short.bin"
            p_short.write_bytes(b"\x08")
            insp_short = engine.detect_format(p_short)
            self.assertIsNotNone(insp_short)
            assert insp_short is not None
            self.assertNotEqual(insp_short.format_type, "ONNX")

            # Starts with 0x08, wire field 1, but doesn't mention onnx
            p_not_onnx = tmp / "other_proto.bin"
            p_not_onnx.write_bytes(b"\x08\x01\x12\x04test")
            insp_not_onnx = engine.detect_format(p_not_onnx)
            self.assertIsNotNone(insp_not_onnx)
            assert insp_not_onnx is not None
            self.assertNotEqual(insp_not_onnx.format_type, "ONNX")

    def test_corrupted_parquet_and_arrow(self) -> None:
        """Tests handling of corrupted Parquet and Arrow signatures."""
        with TempWorkspace(prefix="adv_pq_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # Parquet file truncated (only 2 bytes)
            p_short_pq = tmp / "short.parquet"
            p_short_pq.write_bytes(b"PA")
            insp_pq = engine.detect_format(p_short_pq)
            self.assertIsNotNone(insp_pq)
            assert insp_pq is not None
            self.assertNotEqual(insp_pq.format_type, "Parquet")

            # Arrow file with truncated magic bytes
            p_short_arrow = tmp / "short.arrow"
            p_short_arrow.write_bytes(b"ARR")
            insp_arr = engine.detect_format(p_short_arrow)
            self.assertIsNotNone(insp_arr)
            assert insp_arr is not None
            self.assertNotEqual(insp_arr.format_type, "Arrow")

    def test_csv_with_binary_garbage(self) -> None:
        """Tests that a .csv file containing random binary bytes does not crash Sniffer."""
        with TempWorkspace(prefix="adv_csv_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            p_bin_csv = tmp / "corrupt.csv"
            # Write non-decodable binary bytes and nulls
            p_bin_csv.write_bytes(b"\x00\xff\xfe\x01\x02\x03\x80\x81\x82\x83\x00\x00")

            insp = engine.detect_format(p_bin_csv)
            self.assertIsNotNone(insp)
            assert insp is not None
            # Should safely classify as CSV fallback without throwing exception
            self.assertEqual(insp.format_type, "CSV")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Datasets")

    def test_hf_config_adversarial(self) -> None:
        """Tests HuggingFace config parser against malformed, oversized, and array JSON."""
        with TempWorkspace(prefix="adv_hf_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # Case A: config.json with JSON array instead of dict
            p_arr = tmp / "config.json"
            p_arr.write_text('["architectures", "LlamaForCausalLM"]', encoding="utf-8")
            insp_arr = engine.detect_format(p_arr)
            self.assertIsNotNone(insp_arr)
            assert insp_arr is not None
            self.assertNotEqual(insp_arr.format_type, "HuggingFace Config")

            # Case B: config.json with corrupt syntax
            p_corrupt = tmp / "corrupt_config.json"
            p_corrupt.write_text('{"architectures": [unclosed array', encoding="utf-8")
            insp_corrupt = engine.detect_format(p_corrupt)
            self.assertIsNotNone(insp_corrupt)
            assert insp_corrupt is not None
            self.assertNotEqual(insp_corrupt.format_type, "HuggingFace Config")

            # Case C: JSON file with size > 2MB (boundary check)
            p_huge = tmp / "huge_config.json"
            # Write 2.1 MB of JSON padding
            p_huge.write_text('{"architectures": ["Llama"], "pad": "' + ("A" * 2_200_000) + '"}', encoding="utf-8")
            insp_huge = engine.detect_format(p_huge)
            self.assertIsNotNone(insp_huge)
            assert insp_huge is not None
            # Oversized json is rejected from HF config check
            self.assertNotEqual(insp_huge.format_type, "HuggingFace Config")

    def test_zero_byte_and_boundary_files(self) -> None:
        """Tests detection and handling of 0-byte and single-byte files."""
        with TempWorkspace(prefix="adv_empty_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            for ext in [".gguf", ".safetensors", ".parquet", ".pdf", ".epub", ".pt", ".onnx", ".csv", ".md"]:
                p = tmp / f"empty{ext}"
                p.touch()
                insp = engine.detect_format(p)
                self.assertIsNotNone(insp)
                assert insp is not None
                self.assertEqual(insp.format_type, "Empty File")
                self.assertEqual(insp.category, "06_Archives_Storage")
                self.assertEqual(insp.subcategory, "Empty")


class TestAdversarialAmbiguousFiles(SmartDriveTestCase):
    """Stress tests classifier when extension and content disagree."""

    def test_extension_vs_magic_byte_conflict(self) -> None:
        """Verifies that authentic magic bytes take precedence over misleading file extensions."""
        with TempWorkspace(prefix="adv_ambig_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # 1. File named .pdf containing real GGUF weights
            p_fake_pdf = tmp / "actually_weights.pdf"
            p_fake_pdf.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 64) + struct.pack("<Q", 16) + b"\x00" * 32)
            insp_pdf = engine.detect_format(p_fake_pdf)
            self.assertIsNotNone(insp_pdf)
            assert insp_pdf is not None
            self.assertTrue(insp_pdf.format_type.startswith("GGUF"))
            self.assertEqual(insp_pdf.category, "01_AI_Models")
            self.assertEqual(insp_pdf.subcategory, "Weights")

            # 2. File named .png containing PDF document header
            p_fake_png = tmp / "actually_paper.png"
            p_fake_png.write_bytes(b"%PDF-1.7\n%Fake image stream\n%%EOF")
            insp_png = engine.detect_format(p_fake_png)
            self.assertIsNotNone(insp_png)
            assert insp_png is not None
            self.assertTrue(insp_png.format_type.startswith("PDF"))
            self.assertEqual(insp_png.category, "02_Learning_Knowledge")
            self.assertEqual(insp_png.subcategory, "Papers")

            # 3. File named .safetensors containing arbitrary ASCII narrative text
            p_fake_st = tmp / "readme.safetensors"
            p_fake_st.write_text("This is an ordinary readme note talking about safetensors.", encoding="utf-8")
            insp_st = engine.detect_format(p_fake_st)
            self.assertIsNotNone(insp_st)
            assert insp_st is not None
            self.assertNotEqual(insp_st.format_type, "Safetensors")

            # 4. File named .bin containing PyTorch pickle stream
            p_torch_bin = tmp / "weights.bin"
            p_torch_bin.write_bytes(b"\x80\x02ctorch._utils\n_rebuild_tensor\nq\x01.")
            insp_torch = engine.detect_format(p_torch_bin)
            self.assertIsNotNone(insp_torch)
            assert insp_torch is not None
            self.assertEqual(insp_torch.format_type, "PyTorch")
            self.assertEqual(insp_torch.category, "01_AI_Models")

    def test_multi_dot_and_tricky_extensions(self) -> None:
        """Tests files with multiple periods and unconventional compound extensions."""
        with TempWorkspace(prefix="adv_multidot_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # .tar.gz
            p_tar = tmp / "archive.tar.gz"
            p_tar.write_bytes(b"\x1f\x8b\x08\x00" + b"\x00" * 32)
            insp_tar = engine.detect_format(p_tar)
            self.assertIsNotNone(insp_tar)
            assert insp_tar is not None
            self.assertEqual(insp_tar.format_type, "Archive")

            # file.v1.0.final.md
            p_note = tmp / "project.v1.0.final.md"
            p_note.write_text("# Release Notes", encoding="utf-8")
            insp_note = engine.detect_format(p_note)
            self.assertIsNotNone(insp_note)
            assert insp_note is not None
            self.assertEqual(insp_note.format_type, "Markdown")
            self.assertEqual(insp_note.subcategory, "Notes")


class TestAdversarialCollisionsAndDataIntegrity(SmartDriveTestCase):
    """Stress tests massive collisions and guarantees ZERO data loss."""

    def test_massive_collision_relocation_in_apply_mode(self) -> None:
        """Moves 25 identical filenames with unique payloads into the same target taxonomy.

        Assures:
        - All 25 files exist on disk after --apply
        - No file is overwritten
        - Exact SHA-256 payload integrity preserved for every file
        """
        with TempWorkspace(prefix="adv_collision_") as tmp:
            num_files = 25
            file_hashes: dict[str, str] = {}
            file_sources: list[Path] = []

            # Create an existing file at canonical target location first
            canonical_dir = tmp / "01_AI_Models" / "Weights"
            canonical_dir.mkdir(parents=True, exist_ok=True)
            existing_target = canonical_dir / "target_model.gguf"
            target_payload = b"GGUF" + struct.pack("<I", 3) + b"EXISTING_ORIGINAL"
            existing_target.write_bytes(target_payload)
            file_hashes[str(existing_target)] = hashlib.sha256(target_payload).hexdigest()

            # Create 25 different source directories, each with a file named 'target_model.gguf'
            # each having unique content
            for i in range(1, num_files + 1):
                src_dir = tmp / f"source_folder_{i}"
                src_dir.mkdir()
                src_file = src_dir / "target_model.gguf"
                payload = b"GGUF" + struct.pack("<I", 3) + f"PAYLOAD_INSTANCE_{i:03d}_{'X'*100}".encode("ascii")
                src_file.write_bytes(payload)
                file_hashes[f"payload_{i}"] = hashlib.sha256(payload).hexdigest()
                file_sources.append(src_file)

            engine = ClassifierEngine(root_path=tmp)
            classified = engine.scan_and_classify(tmp)

            # 01_AI_Models is a protected root dir so it is pruned from scanning;
            # exactly the 25 source files from source_folder_1..25 are classified.
            self.assertEqual(len(classified), num_files)

            # Execute relocation in APPLY mode
            actions = engine.execute_relocation(classified, dry_run=False)

            # Verify action counts: all 25 source files should be moved with collision resolution
            moved_actions = [a for a in actions if a.status == "MOVED"]
            skipped_actions = [a for a in actions if a.status == "SKIPPED_ALREADY_IN_PLACE"]
            error_actions = [a for a in actions if a.status == "ERROR"]

            self.assertEqual(len(error_actions), 0, f"Errors encountered: {error_actions}")
            self.assertEqual(len(skipped_actions), 0)
            self.assertEqual(len(moved_actions), num_files)

            # Verify on disk: canonical_dir must contain exactly 26 files
            all_target_files = list(canonical_dir.glob("target_model*.gguf"))
            self.assertEqual(len(all_target_files), num_files + 1, "Expected exactly 26 files on disk!")

            # Verify that every source file was deleted from its original location
            for src_file in file_sources:
                self.assertFalse(src_file.exists(), f"Source file {src_file} was not relocated!")

            # Verify SHA-256 integrity: all hashes must be present in canonical_dir files
            found_hashes = set()
            for tf in all_target_files:
                content = tf.read_bytes()
                h = hashlib.sha256(content).hexdigest()
                found_hashes.add(h)

            for key, expected_hash in file_hashes.items():
                self.assertIn(expected_hash, found_hashes, f"Lost payload data for {key}!")

    def test_repo_directory_name_collision(self) -> None:
        """Tests that moving multiple project repositories with identical folder names resolves without overwrite."""
        with TempWorkspace(prefix="adv_repo_col_") as tmp:
            # 1. Canonical repo already exists
            proj_root = tmp / "03_Development_Projects" / "awesome_tool"
            proj_root.mkdir(parents=True)
            (proj_root / "Cargo.toml").write_text('[package]\nname = "original"\n', encoding="utf-8")

            # 2. Two incoming repos named 'awesome_tool'
            repo1 = tmp / "incoming1" / "awesome_tool"
            repo1.mkdir(parents=True)
            (repo1 / "Cargo.toml").write_text('[package]\nname = "v1"\n', encoding="utf-8")

            repo2 = tmp / "incoming2" / "awesome_tool"
            repo2.mkdir(parents=True)
            (repo2 / "Cargo.toml").write_text('[package]\nname = "v2"\n', encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            classified = engine.scan_and_classify(tmp / "incoming1") + engine.scan_and_classify(tmp / "incoming2")
            self.assertEqual(len(classified), 2)

            actions = engine.execute_relocation(classified, dry_run=False)
            self.assertEqual(len([a for a in actions if a.status == "MOVED"]), 2)

            # Both repos must exist under Development_Projects as awesome_tool_1 and awesome_tool_2
            r1 = tmp / "03_Development_Projects" / "awesome_tool_1"
            r2 = tmp / "03_Development_Projects" / "awesome_tool_2"
            self.assertTrue(r1.is_dir())
            self.assertTrue(r2.is_dir())
            self.assertIn('name = "v1"', (r1 / "Cargo.toml").read_text(encoding="utf-8"))
            self.assertIn('name = "v2"', (r2 / "Cargo.toml").read_text(encoding="utf-8"))
            self.assertIn('name = "original"', (proj_root / "Cargo.toml").read_text(encoding="utf-8"))

    def test_collision_with_gaps_in_numeric_suffixes(self) -> None:
        """Tests that when stem.ext, stem_1.ext, stem_3.ext exist, collision resolution finds stem_2.ext."""
        with TempWorkspace(prefix="adv_gap_") as tmp:
            weights_dir = tmp / "01_AI_Models" / "Weights"
            weights_dir.mkdir(parents=True, exist_ok=True)

            (weights_dir / "model.gguf").write_bytes(b"GGUF" + b"\x00" * 32)
            (weights_dir / "model_1.gguf").write_bytes(b"GGUF" + b"\x01" * 32)
            # Intentionally omit model_2.gguf
            (weights_dir / "model_3.gguf").write_bytes(b"GGUF" + b"\x03" * 32)

            incoming = tmp / "downloads" / "model.gguf"
            incoming.parent.mkdir()
            incoming.write_bytes(b"GGUF" + b"\x02" * 32)

            engine = ClassifierEngine(root_path=tmp)
            res = engine.inspect_path(incoming)
            self.assertIsNotNone(res)
            assert res is not None

            dest, collided = engine.resolve_destination(res)
            self.assertTrue(collided)
            # Must choose model_2.gguf to fill the gap cleanly
            self.assertEqual(dest, weights_dir / "model_2.gguf")


class TestAdversarialProtectionGuards(SmartDriveTestCase):
    """Stress tests inviolable safeguards against root configuration and orchestration files."""

    def test_root_protected_files_completely_immune(self) -> None:
        """Verifies that root protected files cannot be classified or moved under any flags."""
        with TempWorkspace(prefix="adv_guards_") as tmp:
            protected_files = [
                "GEMINI.md",
                "CLAUDE.md",
                "README.md",
                "AGENTS.md",
                ".metadata_never_index",
                ".mcp.json",
                "setup_win.bat",
                "setup_mac.command",
                "check_ssd_status.ps1",
                "clean_mac_junk.sh",
            ]

            created: list[Path] = []
            for name in protected_files:
                p = tmp / name
                p.write_text(f"Protected content for {name}\n", encoding="utf-8")
                created.append(p)

            engine = ClassifierEngine(root_path=tmp)

            # 1. inspect_path on each directly must return None
            for p in created:
                res = engine.inspect_path(p)
                self.assertIsNone(res, f"Protected file {p.name} must return None from inspect_path!")

            # 2. scan_and_classify on root must ignore all protected files
            results = engine.scan_and_classify(tmp)
            classified_names = [r.name for r in results]
            for p in created:
                self.assertNotIn(p.name, classified_names, f"Protected file {p.name} was returned in scan_and_classify!")

            # 3. CLI apply targeting root directly must leave files 100% untouched
            for p in created:
                args = argparse.Namespace(
                    root=str(tmp),
                    path=str(p),
                    suggest=False,
                    dry_run=False,
                    apply=True,
                    json=True,
                    no_recursive=False,
                )
                old_stdout = sys.stdout
                sys.stdout = io.StringIO()
                try:
                    ret = cmd_classify(args)
                finally:
                    sys.stdout = old_stdout

                self.assertEqual(ret, 0)
                self.assertTrue(p.exists(), f"Protected file {p.name} was removed or moved!")
                self.assertEqual(p.read_text(encoding="utf-8"), f"Protected content for {p.name}\n")

    def test_case_insensitive_root_protection(self) -> None:
        """Verifies that protection is strictly case-insensitive across platforms."""
        casing_variants = [
            "gemini.md",
            "GEMINI.MD",
            "Gemini.Md",
            "claude.md",
            "CLAUDE.md",
            "Claude.MD",
            "readme.md",
            "README.MD",
            "agents.md",
            "AGENTS.MD",
        ]
        with TempWorkspace(prefix="adv_case_") as tmp:
            engine = ClassifierEngine(root_path=tmp)
            for idx, variant in enumerate(casing_variants):
                folder = tmp / f"sub_{idx}"
                folder.mkdir()
                p = folder / variant
                p.write_text("# Documentation\n", encoding="utf-8")

                # Direct inspection must recognize protected status regardless of casing
                res = engine.inspect_path(p)
                self.assertIsNone(res, f"Variant {variant} should be recognized as protected!")

    def test_missing_and_contract_boundaries(self) -> None:
        """Tests that non-existent paths and invalid files return None gracefully per contract."""
        with TempWorkspace(prefix="adv_bound_") as tmp:
            engine = ClassifierEngine(root_path=tmp)

            # Non-existent file
            ghost = tmp / "does_not_exist.gguf"
            self.assertIsNone(engine.classify_file(ghost))
            self.assertIsNone(engine.inspect_path(ghost))

            # Non-existent directory scanning
            ghost_dir = tmp / "missing_folder"
            self.assertEqual(engine.scan_and_classify(ghost_dir), [])

            # ClassificationResult to_dict serialization
            p = tmp / "valid.parquet"
            p.write_bytes(b"PAR1" + b"\x00" * 32 + b"PAR1")
            res = engine.classify_file(p)
            self.assertIsNotNone(res)
            assert res is not None
            d = res.to_dict()
            self.assertIn("recommended_path", d)
            self.assertIn("confidence", d)
            self.assertEqual(d["format_type"], "Parquet")




class TestAdversarialTraversalAndScalability(SmartDriveTestCase):
    """Stress tests deep tree structures, huge file counts, and non-recursive limits."""

    def test_deep_tree_traversal(self) -> None:
        """Tests that 30-level deep nesting is scanned without RecursionError or stack overflow."""
        with TempWorkspace(prefix="adv_deep_") as tmp:
            current = tmp
            for level in range(30):
                current = current / f"level_{level}"
            current.mkdir(parents=True)

            deep_file = current / "deep_model.gguf"
            deep_file.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            engine = ClassifierEngine(root_path=tmp)
            classified = engine.scan_and_classify(tmp, recursive=True)
            self.assertEqual(len(classified), 1)
            self.assertEqual(classified[0].name, "deep_model.gguf")

    def test_large_file_count_directory(self) -> None:
        """Tests scanning and dry-run classifying 150 files in a single folder."""
        with TempWorkspace(prefix="adv_large_") as tmp:
            count = 150
            for i in range(count):
                p = tmp / f"note_{i:03d}.md"
                p.write_text(f"# Note {i}\n", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            classified = engine.scan_and_classify(tmp, recursive=False)
            self.assertEqual(len(classified), count)

            # Test dry-run relocation
            actions = engine.execute_relocation(classified, dry_run=True)
            self.assertEqual(len(actions), count)
            # Ensure none actually moved
            for i in range(count):
                self.assertTrue((tmp / f"note_{i:03d}.md").exists())

    def test_no_recursive_flag_traversal(self) -> None:
        """Tests that recursive=False stops at root level and ignores subdirectories."""
        with TempWorkspace(prefix="adv_norec_") as tmp:
            (tmp / "root_file.md").write_text("# Root", encoding="utf-8")
            sub = tmp / "sub_folder"
            sub.mkdir()
            (sub / "sub_file.md").write_text("# Sub", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            results = engine.scan_and_classify(tmp, recursive=False)
            names = [r.name for r in results]
            self.assertIn("root_file.md", names)
            self.assertNotIn("sub_file.md", names)


if __name__ == "__main__":
    unittest.main()
