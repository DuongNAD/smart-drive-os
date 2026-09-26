"""tests/test_classifier.py - Comprehensive Unit Tests for ClassifierEngine & CLI.

Milestone 3 (SmartDrive-OS v1.1.0).
Zero external dependencies: 100% Python Standard Library.
Tests deep format detectors, project repo recognition, collision avoidance,
inviolable root file protection, and CLI modes (--suggest, --dry-run, --apply, --json).
"""

from __future__ import annotations

import argparse
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


class TestFormatDetectors(unittest.TestCase):
    """Tests synthetic binary and structural inspection for specialized file formats."""

    def test_detect_gguf(self) -> None:
        with TempWorkspace(prefix="test_gguf_") as tmp:
            p = tmp / "model.gguf"
            # GGUF v3: magic "GGUF" + version 3 (uint32) + tensor_count 128 (uint64) + kv_count 64 (uint64)
            payload = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 128) + struct.pack("<Q", 64) + b"\x00" * 32
            p.write_bytes(payload)

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertTrue(insp.format_type.startswith("GGUF"))
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Weights")
            self.assertEqual(insp.confidence, 1.0)
            self.assertEqual(insp.metadata.get("version"), 3)
            self.assertEqual(insp.metadata.get("tensor_count"), 128)

    def test_detect_safetensors(self) -> None:
        with TempWorkspace(prefix="test_safetensors_") as tmp:
            p = tmp / "model.safetensors"
            meta = {
                "weight1": {"dtype": "F16", "shape": [10, 10], "data_offsets": [0, 200]},
                "weight2": {"dtype": "F16", "shape": [20, 20], "data_offsets": [200, 1000]},
                "__metadata__": {"format": "pt"},
            }
            meta_json = json.dumps(meta).encode("utf-8")
            header_len = len(meta_json)
            payload = struct.pack("<Q", header_len) + meta_json + b"\x00" * 1000
            p.write_bytes(payload)

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Safetensors")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Weights")
            self.assertGreaterEqual(insp.confidence, 0.95)
            self.assertEqual(insp.metadata.get("tensor_count"), 2)

    def test_detect_parquet(self) -> None:
        with TempWorkspace(prefix="test_parquet_") as tmp:
            p = tmp / "dataset.parquet"
            # Parquet header & footer magic "PAR1"
            payload = b"PAR1" + b"\x00" * 100 + b"PAR1"
            p.write_bytes(payload)

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Parquet")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Datasets")
            self.assertEqual(insp.confidence, 1.0)

    def test_detect_arrow_and_feather(self) -> None:
        with TempWorkspace(prefix="test_arrow_") as tmp:
            # Arrow IPC
            p_arrow = tmp / "data.arrow"
            p_arrow.write_bytes(b"ARROW1\x00\x00" + b"\x00" * 64)

            # Feather v1
            p_fea = tmp / "data.feather"
            p_fea.write_bytes(b"FEA1\x00\x00" + b"\x00" * 64)

            engine = ClassifierEngine(root_path=tmp)

            insp_arrow = engine.detect_format(p_arrow)
            self.assertIsNotNone(insp_arrow)
            assert insp_arrow is not None
            self.assertEqual(insp_arrow.format_type, "Arrow")
            self.assertEqual(insp_arrow.category, "01_AI_Models")
            self.assertEqual(insp_arrow.subcategory, "Datasets")

            insp_fea = engine.detect_format(p_fea)
            self.assertIsNotNone(insp_fea)
            assert insp_fea is not None
            self.assertEqual(insp_fea.format_type, "Arrow")
            self.assertEqual(insp_fea.category, "01_AI_Models")
            self.assertEqual(insp_fea.subcategory, "Datasets")

    def test_detect_hdf5(self) -> None:
        with TempWorkspace(prefix="test_hdf5_") as tmp:
            p_data = tmp / "measurements.h5"
            p_data.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 32)

            p_weights = tmp / "model_weights.h5"
            p_weights.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 32)

            engine = ClassifierEngine(root_path=tmp)

            insp_data = engine.detect_format(p_data)
            self.assertIsNotNone(insp_data)
            assert insp_data is not None
            self.assertEqual(insp_data.format_type, "HDF5")
            self.assertEqual(insp_data.category, "01_AI_Models")

            insp_weights = engine.detect_format(p_weights)
            self.assertIsNotNone(insp_weights)
            assert insp_weights is not None
            self.assertEqual(insp_weights.format_type, "HDF5")
            self.assertEqual(insp_weights.category, "01_AI_Models")
            self.assertEqual(insp_weights.subcategory, "Weights")

    def test_detect_pdf(self) -> None:
        with TempWorkspace(prefix="test_pdf_") as tmp:
            p = tmp / "deep_learning_paper.pdf"
            p.write_bytes(b"%PDF-1.7\n%test content stream\n%%EOF\n")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertTrue(insp.format_type.startswith("PDF"))
            self.assertEqual(insp.category, "02_Learning_Knowledge")
            self.assertEqual(insp.subcategory, "Papers")
            self.assertGreaterEqual(insp.confidence, 0.95)
            self.assertEqual(insp.metadata.get("pdf_version"), "1.7")

    def test_detect_epub(self) -> None:
        with TempWorkspace(prefix="test_epub_") as tmp:
            p = tmp / "handbook.epub"
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("mimetype", "application/epub+zip")
                zf.writestr("META-INF/container.xml", "<container/>")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "ePub")
            self.assertEqual(insp.category, "02_Learning_Knowledge")
            self.assertEqual(insp.subcategory, "Books")
            self.assertEqual(insp.confidence, 1.0)

    def test_detect_pytorch_zip(self) -> None:
        with TempWorkspace(prefix="test_torch_zip_") as tmp:
            p = tmp / "model.pt"
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("archive/data.pkl", b"PICKLE_DATA")
                zf.writestr("archive/byteorder", b"little")
                zf.writestr("archive/data/0", b"\x00" * 64)

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "PyTorch")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Weights")
            self.assertGreaterEqual(insp.confidence, 0.95)

    def test_detect_pytorch_pickle(self) -> None:
        with TempWorkspace(prefix="test_torch_pkl_") as tmp:
            p = tmp / "checkpoint.pth"
            payload = b"\x80\x02ctorch._utils\n_rebuild_tensor\nq\x01."
            p.write_bytes(payload)

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "PyTorch")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Weights")

    def test_detect_onnx(self) -> None:
        with TempWorkspace(prefix="test_onnx_") as tmp:
            p = tmp / "detector.onnx"
            # Protobuf wire format field 1 varint 0x08 (ir_version 7)
            payload = b"\x08\x07\x12\x0apytorch_ai\x1a\x051.13\x22\x04onnx"
            p.write_bytes(payload)

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "ONNX")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Weights")

    def test_detect_hf_config(self) -> None:
        with TempWorkspace(prefix="test_hf_") as tmp:
            p = tmp / "config.json"
            cfg = {
                "architectures": ["LlamaForCausalLM"],
                "model_type": "llama",
                "torch_dtype": "bfloat16",
                "vocab_size": 32000,
            }
            p.write_text(json.dumps(cfg), encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "HuggingFace Config")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Configs")
            self.assertGreaterEqual(insp.confidence, 0.90)

    def test_detect_jsonl(self) -> None:
        with TempWorkspace(prefix="test_jsonl_") as tmp:
            p = tmp / "dataset.jsonl"
            lines = [
                json.dumps({"prompt": "Hello", "completion": "Hi"}),
                json.dumps({"prompt": "What is Python?", "completion": "A language"}),
            ]
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "JSONL")
            self.assertEqual(insp.category, "01_AI_Models")
            self.assertEqual(insp.subcategory, "Datasets")

    def test_detect_csv_and_tsv(self) -> None:
        with TempWorkspace(prefix="test_csv_") as tmp:
            p_csv = tmp / "train.csv"
            p_csv.write_text("feature1,feature2,label\n1.0,2.0,0\n3.0,4.0,1\n", encoding="utf-8")

            p_tsv = tmp / "eval.tsv"
            p_tsv.write_text("id\ttext\tscore\n1\tsample A\t0.9\n2\tsample B\t0.8\n", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)

            insp_csv = engine.detect_format(p_csv)
            self.assertIsNotNone(insp_csv)
            assert insp_csv is not None
            self.assertEqual(insp_csv.format_type, "CSV")
            self.assertEqual(insp_csv.category, "01_AI_Models")
            self.assertEqual(insp_csv.subcategory, "Datasets")

            insp_tsv = engine.detect_format(p_tsv)
            self.assertIsNotNone(insp_tsv)
            assert insp_tsv is not None
            self.assertEqual(insp_tsv.format_type, "TSV")
            self.assertEqual(insp_tsv.category, "01_AI_Models")
            self.assertEqual(insp_tsv.subcategory, "Datasets")

    def test_detect_markdown(self) -> None:
        with TempWorkspace(prefix="test_md_") as tmp:
            p = tmp / "research_notes.md"
            p.write_text("# Research Notes\n\n- Finding 1\n- Finding 2\n", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Markdown")
            self.assertEqual(insp.category, "02_Learning_Knowledge")
            self.assertEqual(insp.subcategory, "Notes")

    def test_detect_scripts_and_archives(self) -> None:
        with TempWorkspace(prefix="test_scripts_") as tmp:
            p_script = tmp / "train.py"
            p_script.write_text("import os\nprint('hello')\n", encoding="utf-8")

            p_archive = tmp / "bundle.zip"
            with zipfile.ZipFile(p_archive, "w") as zf:
                zf.writestr("test.txt", "regular file")

            engine = ClassifierEngine(root_path=tmp)

            insp_script = engine.detect_format(p_script)
            self.assertIsNotNone(insp_script)
            assert insp_script is not None
            self.assertEqual(insp_script.format_type, "Script")
            self.assertEqual(insp_script.category, "05_Dev_Toolbox")
            self.assertEqual(insp_script.subcategory, "Scripts")

            insp_arch = engine.detect_format(p_archive)
            self.assertIsNotNone(insp_arch)
            assert insp_arch is not None
            self.assertEqual(insp_arch.format_type, "Archive")
            self.assertEqual(insp_arch.category, "06_Archives_Storage")
            self.assertEqual(insp_arch.subcategory, "Archives")

    def test_detect_empty_file(self) -> None:
        with TempWorkspace(prefix="test_empty_") as tmp:
            p = tmp / "empty.dat"
            p.touch()

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_format(p)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Empty File")


class TestProjectRepoDetection(unittest.TestCase):
    """Tests recognition of project repositories (Git, Rust, Node, Python)."""

    def test_detect_git_repo(self) -> None:
        with TempWorkspace(prefix="test_git_") as tmp:
            repo = tmp / "my_git_project"
            repo.mkdir()
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Project\n", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_project_repo(repo)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Git Repository")
            self.assertEqual(insp.category, "03_Development_Projects")
            self.assertEqual(insp.subcategory, "my_git_project")
            self.assertEqual(insp.confidence, 1.0)

    def test_detect_rust_project(self) -> None:
        with TempWorkspace(prefix="test_rust_") as tmp:
            repo = tmp / "rust_engine"
            repo.mkdir()
            (repo / "Cargo.toml").write_text('[package]\nname = "rust_engine"\n', encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_project_repo(repo)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Rust Project")
            self.assertEqual(insp.category, "03_Development_Projects")
            self.assertEqual(insp.subcategory, "rust_engine")

    def test_detect_node_project(self) -> None:
        with TempWorkspace(prefix="test_node_") as tmp:
            repo = tmp / "web_client"
            repo.mkdir()
            (repo / "package.json").write_text('{"name": "web_client", "version": "1.0.0"}', encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_project_repo(repo)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Node Project")
            self.assertEqual(insp.category, "03_Development_Projects")
            self.assertEqual(insp.subcategory, "web_client")

    def test_detect_python_project(self) -> None:
        with TempWorkspace(prefix="test_python_") as tmp:
            repo = tmp / "ml_pipeline"
            repo.mkdir()
            (repo / "pyproject.toml").write_text('[project]\nname = "ml_pipeline"\n', encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_project_repo(repo)
            self.assertIsNotNone(insp)
            assert insp is not None
            self.assertEqual(insp.format_type, "Python Project")
            self.assertEqual(insp.category, "03_Development_Projects")
            self.assertEqual(insp.subcategory, "ml_pipeline")

    def test_protected_root_dirs_not_treated_as_repos(self) -> None:
        with TempWorkspace(prefix="test_protected_dir_") as tmp:
            # Even if 01_AI_Models has a pyproject.toml inside, the directory itself is protected
            prot_dir = tmp / "01_AI_Models"
            prot_dir.mkdir()
            (prot_dir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            insp = engine.detect_project_repo(prot_dir)
            self.assertIsNone(insp)


class TestCollisionAvoidanceAndRelocation(unittest.TestCase):
    """Tests destination resolution, numeric collision suffixes, and safe relocation."""

    def test_no_collision(self) -> None:
        with TempWorkspace(prefix="test_reloc_") as tmp:
            src = tmp / "test_model.gguf"
            src.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            engine = ClassifierEngine(root_path=tmp)
            res = engine.inspect_path(src)
            self.assertIsNotNone(res)
            assert res is not None

            dest, collision = engine.resolve_destination(res)
            expected = tmp / "01_AI_Models" / "Weights" / "test_model.gguf"
            self.assertEqual(dest, expected)
            self.assertFalse(collision)

    def test_single_collision(self) -> None:
        with TempWorkspace(prefix="test_reloc_") as tmp:
            # Pre-create the canonical target
            target = tmp / "01_AI_Models" / "Weights" / "test_model.gguf"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"EXISTING_CONTENT")

            src = tmp / "downloads" / "test_model.gguf"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            engine = ClassifierEngine(root_path=tmp)
            res = engine.inspect_path(src)
            self.assertIsNotNone(res)
            assert res is not None

            dest, collision = engine.resolve_destination(res)
            expected = tmp / "01_AI_Models" / "Weights" / "test_model_1.gguf"
            self.assertEqual(dest, expected)
            self.assertTrue(collision)

    def test_multiple_collisions(self) -> None:
        with TempWorkspace(prefix="test_reloc_") as tmp:
            # Pre-create canonical target and _1
            weights_dir = tmp / "01_AI_Models" / "Weights"
            weights_dir.mkdir(parents=True, exist_ok=True)
            (weights_dir / "test_model.gguf").write_bytes(b"EXISTING_1")
            (weights_dir / "test_model_1.gguf").write_bytes(b"EXISTING_2")

            src = tmp / "test_model.gguf"
            src.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            engine = ClassifierEngine(root_path=tmp)
            res = engine.inspect_path(src)
            self.assertIsNotNone(res)
            assert res is not None

            dest, collision = engine.resolve_destination(res)
            expected = weights_dir / "test_model_2.gguf"
            self.assertEqual(dest, expected)
            self.assertTrue(collision)

    def test_batch_internal_collision(self) -> None:
        with TempWorkspace(prefix="test_batch_") as tmp:
            dir_a = tmp / "source_a"
            dir_b = tmp / "source_b"
            dir_a.mkdir()
            dir_b.mkdir()

            file_a = dir_a / "notes.md"
            file_b = dir_b / "notes.md"
            file_a.write_text("# Notes A", encoding="utf-8")
            file_b.write_text("# Notes B", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            res_a = engine.inspect_path(file_a)
            res_b = engine.inspect_path(file_b)
            self.assertIsNotNone(res_a)
            self.assertIsNotNone(res_b)
            assert res_a is not None and res_b is not None

            allocated: set[Path] = set()
            dest_a, col_a = engine.resolve_destination(res_a, allocated)
            dest_b, col_b = engine.resolve_destination(res_b, allocated)

            self.assertFalse(col_a)
            self.assertEqual(dest_a, tmp / "02_Learning_Knowledge" / "Notes" / "notes.md")
            self.assertTrue(col_b)
            self.assertEqual(dest_b, tmp / "02_Learning_Knowledge" / "Notes" / "notes_1.md")

    def test_already_in_place_skipped(self) -> None:
        with TempWorkspace(prefix="test_in_place_") as tmp:
            weights_dir = tmp / "01_AI_Models" / "Weights"
            weights_dir.mkdir(parents=True, exist_ok=True)
            p = weights_dir / "model.gguf"
            p.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            engine = ClassifierEngine(root_path=tmp)
            res = engine.inspect_path(p)
            self.assertIsNotNone(res)
            assert res is not None

            actions = engine.execute_relocation([res], dry_run=False)
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].status, "SKIPPED_ALREADY_IN_PLACE")
            self.assertTrue(p.exists())


class TestSafeguardsAndRootProtection(unittest.TestCase):
    """Tests inviolable safeguards ensuring root orchestration files and dirs are never moved."""

    def test_protected_root_files_never_classified(self) -> None:
        with TempWorkspace(prefix="test_safe_") as tmp:
            gemini = tmp / "GEMINI.md"
            readme = tmp / "README.md"
            agents = tmp / "AGENTS.md"
            mcp = tmp / ".mcp.json"
            setup_bat = tmp / "setup_win.bat"

            gemini.write_text("# Guidelines", encoding="utf-8")
            readme.write_text("# Readme", encoding="utf-8")
            agents.write_text("# Agents", encoding="utf-8")
            mcp.write_text("{}", encoding="utf-8")
            setup_bat.write_text("@echo off", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            for f in [gemini, readme, agents, mcp, setup_bat]:
                res = engine.inspect_path(f)
                self.assertIsNone(res, f"Protected file {f.name} should not be classified")

    def test_protected_root_dirs_never_scanned_as_movable(self) -> None:
        with TempWorkspace(prefix="test_safe_dirs_") as tmp:
            # 01_AI_Models is a protected root dir
            ai_dir = tmp / "01_AI_Models"
            ai_dir.mkdir()
            (ai_dir / "model.gguf").write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            engine = ClassifierEngine(root_path=tmp)
            # Inspecting 01_AI_Models as a target directory should not treat the folder itself as a repo
            repo_res = engine.detect_project_repo(ai_dir)
            self.assertIsNone(repo_res)


class TestClassifierCLI(unittest.TestCase):
    """Tests CLI invocation for suggest, dry-run, apply, and json modes."""

    def test_cli_suggest_default(self) -> None:
        with TempWorkspace(prefix="test_cli_suggest_") as tmp:
            p = tmp / "test_model.gguf"
            p.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            args = argparse.Namespace(
                root=str(tmp),
                path=str(tmp),
                suggest=True,
                dry_run=False,
                apply=False,
                json=False,
                no_recursive=False,
            )

            # Capture stdout
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = cmd_classify(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            self.assertIn("Intelligent Classifier", out)
            self.assertIn("test_model.gguf", out)
            self.assertIn("GGUF", out)
            self.assertIn("01_AI_Models/Weights", out)
            # File should not have moved
            self.assertTrue(p.exists())

    def test_cli_suggest_json(self) -> None:
        with TempWorkspace(prefix="test_cli_json_") as tmp:
            p = tmp / "paper.pdf"
            p.write_bytes(b"%PDF-1.7\nSample content\n%%EOF")

            args = argparse.Namespace(
                root=str(tmp),
                path=str(tmp),
                suggest=True,
                dry_run=False,
                apply=False,
                json=True,
                no_recursive=False,
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = cmd_classify(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            data = json.loads(out)
            self.assertEqual(data["total_scanned"], 1)
            self.assertEqual(data["results"][0]["name"], "paper.pdf")
            self.assertEqual(data["results"][0]["subcategory"], "Papers")

    def test_cli_dry_run(self) -> None:
        with TempWorkspace(prefix="test_cli_dry_") as tmp:
            p = tmp / "data.parquet"
            p.write_bytes(b"PAR1" + b"\x00" * 64 + b"PAR1")

            args = argparse.Namespace(
                root=str(tmp),
                path=str(tmp),
                suggest=False,
                dry_run=True,
                apply=False,
                json=False,
                no_recursive=False,
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = cmd_classify(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            self.assertIn("DRY-RUN SIMULATION", out)
            self.assertIn("data.parquet", out)
            self.assertIn("PLANNED", out)
            # Ensure physical file did NOT move
            self.assertTrue(p.exists())
            self.assertFalse((tmp / "01_AI_Models" / "Datasets" / "data.parquet").exists())

    def test_cli_apply(self) -> None:
        with TempWorkspace(prefix="test_cli_apply_") as tmp:
            downloads = tmp / "downloads"
            downloads.mkdir()
            p = downloads / "qwen.gguf"
            p.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            args = argparse.Namespace(
                root=str(tmp),
                path=str(downloads),
                suggest=False,
                dry_run=False,
                apply=True,
                json=False,
                no_recursive=False,
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = cmd_classify(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            self.assertIn("APPLIED", out)
            self.assertIn("Moved: qwen.gguf", out)

            # Check file moved to destination
            dest = tmp / "01_AI_Models" / "Weights" / "qwen.gguf"
            self.assertTrue(dest.exists())
            self.assertFalse(p.exists())

    def test_cli_apply_with_collision(self) -> None:
        with TempWorkspace(prefix="test_cli_collision_") as tmp:
            # Existing model in weights
            weights_dir = tmp / "01_AI_Models" / "Weights"
            weights_dir.mkdir(parents=True, exist_ok=True)
            existing = weights_dir / "qwen.gguf"
            existing.write_bytes(b"EXISTING_MODEL")

            # New incoming model with same name
            incoming = tmp / "incoming" / "qwen.gguf"
            incoming.parent.mkdir()
            incoming.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)

            args = argparse.Namespace(
                root=str(tmp),
                path=str(incoming.parent),
                suggest=False,
                dry_run=False,
                apply=True,
                json=False,
                no_recursive=False,
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = cmd_classify(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            self.assertIn("Renamed to prevent overwrite", out)

            # Both files must exist safely
            self.assertTrue(existing.exists())
            self.assertEqual(existing.read_bytes(), b"EXISTING_MODEL")
            new_dest = weights_dir / "qwen_1.gguf"
            self.assertTrue(new_dest.exists())
            self.assertTrue(new_dest.read_bytes().startswith(b"GGUF"))

    def test_cli_single_file_target(self) -> None:
        with TempWorkspace(prefix="test_cli_single_") as tmp:
            p = tmp / "sample.pdf"
            p.write_bytes(b"%PDF-1.4 Sample")

            args = argparse.Namespace(
                root=str(tmp),
                path=str(p),
                suggest=False,
                dry_run=False,
                apply=True,
                json=False,
                no_recursive=False,
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = cmd_classify(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            self.assertIn("Moved: sample.pdf", out)
            self.assertTrue((tmp / "02_Learning_Knowledge" / "Papers" / "sample.pdf").exists())

    def test_cli_no_recursive_flag(self) -> None:
        with TempWorkspace(prefix="test_cli_norec_") as tmp:
            top_file = tmp / "top.md"
            top_file.write_text("# Top", encoding="utf-8")

            sub_dir = tmp / "subdir"
            sub_dir.mkdir()
            nested_file = sub_dir / "nested.md"
            nested_file.write_text("# Nested", encoding="utf-8")

            args = argparse.Namespace(
                root=str(tmp),
                path=str(tmp),
                suggest=True,
                dry_run=False,
                apply=False,
                json=True,
                no_recursive=True,
            )

            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                ret = cmd_classify(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            data = json.loads(out)
            names = [r["name"] for r in data["results"]]
            self.assertIn("top.md", names)
            self.assertNotIn("nested.md", names)


class TestClassifierEdgeCases(unittest.TestCase):
    """Tests edge cases, contract methods, corrupt files, and directory collisions."""

    def test_apply_organization_contract(self) -> None:
        with TempWorkspace(prefix="test_contract_") as tmp:
            p = tmp / "test.parquet"
            p.write_bytes(b"PAR1" + b"\x00" * 32 + b"PAR1")

            engine = ClassifierEngine(root_path=tmp)
            classified = engine.scan_and_classify(tmp)
            self.assertEqual(len(classified), 1)

            report = engine.apply_organization(classified, dry_run=False)
            self.assertIsInstance(report, ClassificationReport)
            self.assertEqual(report.total_scanned, 1)
            self.assertEqual(report.total_classified, 1)
            self.assertTrue(report.applied)
            self.assertFalse(report.dry_run)
            self.assertEqual(len(report.actions), 1)
            self.assertEqual(report.actions[0].status, "MOVED")

    def test_corrupt_or_truncated_headers(self) -> None:
        with TempWorkspace(prefix="test_corrupt_") as tmp:
            # File with partial GGUF header (< 4 bytes)
            p1 = tmp / "truncated_gguf.dat"
            p1.write_bytes(b"GG")

            # File with safetensors header length that claims 500 bytes but file only has 10 bytes
            p2 = tmp / "truncated_st.safetensors"
            p2.write_bytes(struct.pack("<Q", 500) + b"{'w':")

            # Truncated zip
            p3 = tmp / "bad.epub"
            p3.write_bytes(b"PK\x03\x04BADZIPDATA")

            engine = ClassifierEngine(root_path=tmp)

            # None of these should raise an exception
            res1 = engine.detect_format(p1)
            self.assertIsNotNone(res1)

            res2 = engine.detect_format(p2)
            self.assertIsNotNone(res2)

            res3 = engine.detect_format(p3)
            self.assertIsNotNone(res3)

    def test_repo_directory_relocation_and_collision(self) -> None:
        with TempWorkspace(prefix="test_repo_reloc_") as tmp:
            # Create a target destination repo
            target_repo = tmp / "03_Development_Projects" / "web_app"
            target_repo.mkdir(parents=True)
            (target_repo / "package.json").write_text("{}", encoding="utf-8")

            # Incoming repo with same name
            incoming_repo = tmp / "staging" / "web_app"
            incoming_repo.mkdir(parents=True)
            (incoming_repo / "package.json").write_text("{}", encoding="utf-8")
            (incoming_repo / "index.js").write_text("console.log('hi')", encoding="utf-8")

            engine = ClassifierEngine(root_path=tmp)
            classified = engine.scan_and_classify(incoming_repo.parent)
            self.assertEqual(len(classified), 1)
            self.assertEqual(classified[0].name, "web_app")
            self.assertTrue(classified[0].is_dir)

            # Execute relocation
            actions = engine.execute_relocation(classified, dry_run=False)
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0].status, "MOVED")
            self.assertTrue(actions[0].collision_resolved)
            self.assertEqual(actions[0].relative_target, "03_Development_Projects/web_app_1")

            # Verify directory was moved properly
            moved_dir = tmp / "03_Development_Projects" / "web_app_1"
            self.assertTrue(moved_dir.exists())
            self.assertTrue((moved_dir / "index.js").exists())
            self.assertFalse(incoming_repo.exists())


if __name__ == "__main__":
    unittest.main()

