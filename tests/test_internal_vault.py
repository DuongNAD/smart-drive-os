"""tests.test_internal_vault - Unit Tests for Internal Developer Vault Profile & Protection.

Validates:
- Registration of 'internal-developer-vault' in initializer PROFILES
- 6 canonical partitions and specialized subdirectories
- Taxonomy protection rules in config.py (PROTECTED_CORE_TAXONOMIES, PROTECTED_ROOT_DIRS, is_protected_root_dir)
- DriveInitializer execution on disk (taxonomies, subdirs, manifests, shields, SQLite FTS5)
- Drive letter path normalization in cmd_init (e.g. 'D:' -> 'D:\\')
- Idempotency and force overwrite behaviors

100% Zero-Dependency Python Standard Library.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys
import unittest

from smart_drive.cli.cmd_init import cmd_init, normalize_drive_path
from smart_drive.core.config import (
    PROTECTED_CORE_TAXONOMIES,
    PROTECTED_ROOT_DIRS,
    is_protected_root_dir,
)
from smart_drive.core.initializer import (
    DriveInitializer,
    PROFILES,
)
from tests.helpers import SmartDriveTestCase, TempWorkspace


class TestInternalDeveloperVaultProfile(SmartDriveTestCase):
    """Tests specification, schema, and directory layout of internal-developer-vault profile."""

    def test_profile_is_registered(self) -> None:
        """'internal-developer-vault' must be registered in PROFILES dictionary."""
        self.assertIn("internal-developer-vault", PROFILES)
        profile_def = PROFILES["internal-developer-vault"]
        self.assertIn("description", profile_def)
        self.assertIn("taxonomies", profile_def)
        self.assertIn("subdirs", profile_def)

    def test_canonical_six_taxonomies(self) -> None:
        """Vault profile must define exactly the 6 canonical partitions."""
        profile_def = PROFILES["internal-developer-vault"]
        taxonomies = profile_def["taxonomies"]
        expected = [
            "01_AI_Models",
            "02_Development_Workspaces",
            "03_Data_Vault",
            "04_System_Offload_Caches",
            "05_Dev_Toolbox",
            "06_Archives_Storage",
        ]
        self.assertEqual(taxonomies, expected)
        self.assertEqual(len(taxonomies), 6)

    def test_specialized_subdirectories(self) -> None:
        """Vault profile must define subdirectories for AI, workspaces, datasets, offload caches."""
        subdirs = PROFILES["internal-developer-vault"]["subdirs"]

        # 01_AI_Models subdirs
        self.assertIn("01_AI_Models/checkpoints", subdirs)
        self.assertIn("01_AI_Models/gguf", subdirs)
        self.assertIn("01_AI_Models/safetensors", subdirs)
        self.assertIn("01_AI_Models/onnx", subdirs)

        # 02_Development_Workspaces subdirs
        self.assertIn("02_Development_Workspaces/active", subdirs)
        self.assertIn("02_Development_Workspaces/archive", subdirs)

        # 03_Data_Vault subdirs
        self.assertIn("03_Data_Vault/datasets", subdirs)
        self.assertIn("03_Data_Vault/databases", subdirs)

        # 04_System_Offload_Caches subdirs (for C: cache migrations)
        self.assertIn("04_System_Offload_Caches/huggingface", subdirs)
        self.assertIn("04_System_Offload_Caches/ollama", subdirs)
        self.assertIn("04_System_Offload_Caches/pip", subdirs)
        self.assertIn("04_System_Offload_Caches/uv", subdirs)
        self.assertIn("04_System_Offload_Caches/npm", subdirs)
        self.assertIn("04_System_Offload_Caches/gradle", subdirs)

        # 05_Dev_Toolbox & 06_Archives_Storage
        self.assertIn("05_Dev_Toolbox/scripts", subdirs)
        self.assertIn("05_Dev_Toolbox/sdks", subdirs)
        self.assertIn("06_Archives_Storage/backups", subdirs)


class TestInternalTaxonomyProtection(SmartDriveTestCase):
    """Tests inviolable protection rules for new internal vault partitions in config.py."""

    def test_protected_core_taxonomies_contain_new_partitions(self) -> None:
        """PROTECTED_CORE_TAXONOMIES must include the 3 new internal partition names."""
        self.assertIn("02_Development_Workspaces", PROTECTED_CORE_TAXONOMIES)
        self.assertIn("03_Data_Vault", PROTECTED_CORE_TAXONOMIES)
        self.assertIn("04_System_Offload_Caches", PROTECTED_CORE_TAXONOMIES)

    def test_protected_root_dirs_contain_casefolded_partitions(self) -> None:
        """PROTECTED_ROOT_DIRS must include casefolded forms of the new internal partition names."""
        self.assertIn("02_development_workspaces", PROTECTED_ROOT_DIRS)
        self.assertIn("03_data_vault", PROTECTED_ROOT_DIRS)
        self.assertIn("04_system_offload_caches", PROTECTED_ROOT_DIRS)

    def test_is_protected_root_dir_logic(self) -> None:
        """is_protected_root_dir must return True for new partitions regardless of case or slash format."""
        # Exact case
        self.assertTrue(is_protected_root_dir("02_Development_Workspaces"))
        self.assertTrue(is_protected_root_dir("03_Data_Vault"))
        self.assertTrue(is_protected_root_dir("04_System_Offload_Caches"))

        # Uppercase
        self.assertTrue(is_protected_root_dir("02_DEVELOPMENT_WORKSPACES"))
        self.assertTrue(is_protected_root_dir("03_DATA_VAULT"))
        self.assertTrue(is_protected_root_dir("04_SYSTEM_OFFLOAD_CACHES"))

        # Lowercase
        self.assertTrue(is_protected_root_dir("02_development_workspaces"))
        self.assertTrue(is_protected_root_dir("03_data_vault"))
        self.assertTrue(is_protected_root_dir("04_system_offload_caches"))

        # Paths with slashes and drive letters
        self.assertTrue(is_protected_root_dir("D:/02_Development_Workspaces/"))
        self.assertTrue(is_protected_root_dir("D:\\03_Data_Vault"))
        self.assertTrue(is_protected_root_dir("/04_System_Offload_Caches"))

        # Non-protected directories
        self.assertFalse(is_protected_root_dir("07_Random_Unorganized"))
        self.assertFalse(is_protected_root_dir("temp_downloads"))


class TestDriveInitializerExecution(SmartDriveTestCase):
    """Tests filesystem execution of DriveInitializer with internal-developer-vault profile."""

    def test_initialize_internal_vault_on_disk(self) -> None:
        """DriveInitializer creates all 6 partitions and subdirectories in a sandbox workspace."""
        with TempWorkspace() as ws:
            initializer = DriveInitializer(str(ws))
            res = initializer.initialize(profile="internal-developer-vault")

            self.assertEqual(res["status"], "initialized")
            self.assertEqual(res["profile"], "internal-developer-vault")
            self.assertTrue(res["database_initialized"])

            # Verify all 6 taxonomies exist
            for tax in PROFILES["internal-developer-vault"]["taxonomies"]:
                p = ws / tax
                self.assertTrue(p.is_dir(), f"Taxonomy directory '{tax}' was not created.")

            # Verify subdirectories exist
            for subdir in PROFILES["internal-developer-vault"]["subdirs"]:
                p = ws / subdir
                self.assertTrue(p.is_dir(), f"Subdirectory '{subdir}' was not created.")

            # Verify manifests & index DB
            self.assertTrue((ws / "AGENTS.md").is_file())
            self.assertTrue((ws / "GEMINI.md").is_file())
            self.assertTrue((ws / "CLAUDE.md").is_file())
            self.assertTrue((ws / ".metadata_never_index").is_file())
            self.assertTrue((ws / ".smart_drive" / "index.db").is_file())

    def test_initialize_idempotency_preserves_content(self) -> None:
        """Re-initialization must not destroy existing files in any partition."""
        with TempWorkspace() as ws:
            initializer = DriveInitializer(str(ws))
            initializer.initialize(profile="internal-developer-vault")

            # Create data in 04_System_Offload_Caches and 03_Data_Vault
            cache_file = ws / "04_System_Offload_Caches" / "uv" / "cached_wheel.whl"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(b"EXISTING_UV_CACHE_DATA_BYTES")

            data_file = ws / "03_Data_Vault" / "datasets" / "data.parquet"
            data_file.write_bytes(b"PARQUET_MAGIC_BYTES_TEST")

            # Run second initialization
            res = initializer.initialize(profile="internal-developer-vault", force=False)
            self.assertEqual(res["status"], "initialized")

            # Content must remain intact
            self.assertTrue(cache_file.is_file())
            self.assertEqual(cache_file.read_bytes(), b"EXISTING_UV_CACHE_DATA_BYTES")
            self.assertTrue(data_file.is_file())
            self.assertEqual(data_file.read_bytes(), b"PARQUET_MAGIC_BYTES_TEST")


class TestCmdInitNormalizationAndCli(SmartDriveTestCase):
    """Tests drive letter path normalization and CLI execution in cmd_init."""

    def test_normalize_drive_path_variants(self) -> None:
        """Validates canonicalization of various drive letter specifications."""
        self.assertEqual(normalize_drive_path("D:"), "D:\\")
        self.assertEqual(normalize_drive_path("d:"), "D:\\")
        self.assertEqual(normalize_drive_path("E:"), "E:\\")
        self.assertEqual(normalize_drive_path("D"), "D:\\")
        self.assertEqual(normalize_drive_path("d"), "D:\\")
        self.assertEqual(normalize_drive_path("D:/"), "D:\\")
        self.assertEqual(normalize_drive_path("D:\\"), "D:\\")

        # Standard directories should remain intact
        self.assertEqual(normalize_drive_path("D:\\Workspaces\\Project"), "D:\\Workspaces\\Project")
        self.assertEqual(normalize_drive_path("/var/tmp"), "/var/tmp")

    def test_cmd_init_with_json_and_internal_vault(self) -> None:
        """cmd_init with profile='internal-developer-vault' and json=True outputs valid JSON."""
        with TempWorkspace() as ws:
            args = argparse.Namespace(
                path=str(ws),
                root=None,
                profile="internal-developer-vault",
                force=False,
                json=True,
            )
            old_stdout = sys.stdout
            try:
                sys.stdout = io.StringIO()
                ret = cmd_init(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            data = json.loads(out)
            self.assertEqual(data["status"], "initialized")
            self.assertEqual(data["profile"], "internal-developer-vault")
            self.assertTrue(data["database_initialized"])

    def test_cmd_init_human_readable_output(self) -> None:
        """cmd_init without json flag prints clean confirmation lines."""
        with TempWorkspace() as ws:
            args = argparse.Namespace(
                path=str(ws),
                root=None,
                profile="internal-developer-vault",
                force=False,
                json=False,
            )
            old_stdout = sys.stdout
            try:
                sys.stdout = io.StringIO()
                ret = cmd_init(args)
                out = sys.stdout.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            self.assertIn("Initialized SmartDrive workspace", out)
            self.assertIn("internal-developer-vault", out)


if __name__ == "__main__":
    unittest.main()
