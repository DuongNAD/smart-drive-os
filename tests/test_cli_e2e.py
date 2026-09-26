"""tests.test_cli_e2e - End-to-End CLI Subprocess Test Suite.

Executes actual subprocess invocations of `python -m smart_drive <subcommand>`
across all CLI tools:
- help / no args
- invalid subcommand
- init (preset profiles, manifests, shields)
- status (mount, cluster geometry, database, taxonomies)
- audit (storage breakdown, 512KB slack, markdown export)
- clean (safe 3-tier junk detection, dry-run simulation, apply purge)
- index & update (SQLite FTS5 full indexing and incremental update)
- search (keyword queries, filters, JSON, CSV output)
- dup (3-phase duplicate detection and reclamation plan)
- organize (auto-zoning dry-run and apply rebalancing)
- sentinel / agent-check (1-touch self-healing and Git repository audit)
- mcp-config (Multi-IDE MCP registration)
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from tests.helpers import (
    CLUSTER_SIZE,
    SmartDriveTestCase,
    TempWorkspace,
    create_mock_ssd_tree,
    run_smart_drive_cli,
)


class TestCliEndToEnd(SmartDriveTestCase):
    """E2E subprocess test suite for SmartDrive-OS CLI commands."""

    def test_cli_help_flag(self) -> None:
        """Executing `smart-drive --help` displays usage banner and exits 0."""
        res = run_smart_drive_cli(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("smart-drive", res.stdout.lower())
        self.assertIn("subcommands", res.stdout.lower())
        for subcmd in ["init", "status", "audit", "clean", "search", "organize", "sentinel"]:
            self.assertIn(subcmd, res.stdout)

    def test_cli_no_args_displays_help(self) -> None:
        """Executing `smart-drive` without arguments displays help and exits 0."""
        res = run_smart_drive_cli([])
        self.assertEqual(res.returncode, 0)
        self.assertIn("usage: smart-drive", res.stdout)

    def test_cli_invalid_subcommand_exits_code_2(self) -> None:
        """Executing an unknown subcommand causes argparse to exit with code 2."""
        res = run_smart_drive_cli(["nonexistent-subcommand-xyz"])
        self.assertEqual(res.returncode, 2)
        self.assertIn("invalid choice", res.stderr.lower())

    def test_cli_init_subcommand(self) -> None:
        """`init` creates taxonomies, manifests, shields, and search database."""
        with TempWorkspace() as ws:
            res = run_smart_drive_cli([
                "init",
                "--root", str(ws),
                "--profile", "ai-developer",
                "--json",
            ])
            self.assertEqual(res.returncode, 0, f"init failed: {res.stderr}")

            data = json.loads(res.stdout)
            self.assertEqual(data["status"], "initialized")
            self.assertEqual(data["profile"], "ai-developer")
            self.assertTrue(data["database_initialized"])

            # Verify on-disk artifacts
            self.assertTrue((ws / "01_AI_Models").is_dir())
            self.assertTrue((ws / "01_AI_Models" / "gguf").is_dir())
            self.assertTrue((ws / ".metadata_never_index").is_file())
            self.assertTrue((ws / ".fseventsd" / "no_log").is_file())
            self.assertTrue((ws / "AGENTS.md").is_file())
            self.assertTrue((ws / "GEMINI.md").is_file())
            self.assertTrue((ws / "CLAUDE.md").is_file())
            self.assertTrue((ws / ".smart_drive" / "index.db").is_file())

    def test_cli_status_subcommand(self) -> None:
        """`status` reports mount geometry, database readiness, and shield markers."""
        with TempWorkspace() as ws:
            # Initialize first
            run_smart_drive_cli(["init", "--root", str(ws)])

            # Status JSON output
            res_json = run_smart_drive_cli(["status", "--root", str(ws), "--json"])
            self.assertEqual(res_json.returncode, 0)
            data = json.loads(res_json.stdout)
            self.assertEqual(data["status"], "ready")
            self.assertEqual(data["cluster_size_bytes"], CLUSTER_SIZE)
            self.assertTrue(data["database"]["exists"])
            self.assertTrue(data["anti_indexing_markers"][".metadata_never_index"])

            # Status human-readable text output
            res_text = run_smart_drive_cli(["status", "--root", str(ws)])
            self.assertEqual(res_text.returncode, 0)
            self.assertIn("SmartDrive-OS Status: Ready", res_text.stdout)
            self.assertIn("524,288 bytes", res_text.stdout)

    def test_cli_audit_subcommand(self) -> None:
        """`audit` calculates storage metrics and exports reports."""
        with TempWorkspace() as ws:
            create_mock_ssd_tree(ws)

            # JSON audit report
            res_json = run_smart_drive_cli(["audit", "--root", str(ws), "--json"])
            self.assertEqual(res_json.returncode, 0)
            data = json.loads(res_json.stdout)
            self.assertIn("total_logical_bytes", data)
            self.assertIn("total_allocated_bytes", data)
            self.assertIn("categories", data)
            self.assertGreater(data["total_logical_bytes"], 0)

            # Markdown export
            export_file = ws / "audit_report.md"
            res_export = run_smart_drive_cli([
                "audit",
                "--root", str(ws),
                "--markdown",
                "--export", str(export_file),
            ])
            self.assertEqual(res_export.returncode, 0)
            self.assertTrue(export_file.is_file())
            content = export_file.read_text(encoding="utf-8")
            self.assertIn("Storage Breakdown", content)

    def test_cli_clean_dry_run_and_apply(self) -> None:
        """`clean` safely identifies junk in dry-run and removes it on apply."""
        with TempWorkspace() as ws:
            create_mock_ssd_tree(ws)
            junk_ds_store = ws / ".DS_Store"
            self.assertTrue(junk_ds_store.is_file())

            # 1. Dry run: finds junk, files remain untouched
            res_dry = run_smart_drive_cli(["clean", "--root", str(ws), "--dry-run", "--json"])
            self.assertEqual(res_dry.returncode, 0)
            dry_data = json.loads(res_dry.stdout)
            self.assertTrue(dry_data["dry_run"])
            self.assertGreater(dry_data["junk_count"], 0)
            self.assertTrue(junk_ds_store.is_file(), "Dry run must NOT delete files")

            # 2. Apply: purges junk files
            res_apply = run_smart_drive_cli(["clean", "--root", str(ws), "--apply"])
            self.assertEqual(res_apply.returncode, 0)
            self.assertIn("Purged", res_apply.stdout)
            self.assertFalse(junk_ds_store.exists(), ".DS_Store should be purged")

            # Inviolable controls must remain intact
            self.assertTrue((ws / "GEMINI.md").is_file())
            self.assertTrue((ws / ".metadata_never_index").is_file())

    def test_cli_index_and_search_subcommand(self) -> None:
        """`index` builds SQLite FTS5 database; `search` queries with various formats."""
        with TempWorkspace() as ws:
            create_mock_ssd_tree(ws)

            # Build index
            res_idx = run_smart_drive_cli(["index", "--root", str(ws)])
            self.assertEqual(res_idx.returncode, 0)
            self.assertIn("Indexed", res_idx.stdout)

            db_file = ws / ".smart_drive" / "index.db"
            self.assertTrue(db_file.is_file())

            # Search with keyword
            res_search = run_smart_drive_cli(["search", "llama", "--root", str(ws), "--json"])
            self.assertEqual(res_search.returncode, 0)
            matches = json.loads(res_search.stdout)
            self.assertIsInstance(matches, list)
            match_names = [m["name"] for m in matches]
            self.assertTrue(any("llama" in n.lower() for n in match_names))

            # Search with filter flags
            res_filter = run_smart_drive_cli(["search", "--root", str(ws), "--ext", "gguf", "--json"])
            self.assertEqual(res_filter.returncode, 0)
            gguf_matches = json.loads(res_filter.stdout)
            self.assertTrue(all(m["name"].endswith(".gguf") for m in gguf_matches))

            # Search CSV export
            res_csv = run_smart_drive_cli(["search", "--root", str(ws), "--csv"])
            self.assertEqual(res_csv.returncode, 0)
            self.assertIn("path", res_csv.stdout.lower())
            self.assertIn("size_bytes", res_csv.stdout.lower())

    def test_cli_update_subcommand(self) -> None:
        """`update` incrementally syncs modifications and additions into FTS5 index."""
        with TempWorkspace() as ws:
            create_mock_ssd_tree(ws)
            run_smart_drive_cli(["index", "--root", str(ws)])

            # Add a new file
            new_file = ws / "02_Learning_Knowledge" / "new_research.md"
            new_file.write_text("# Research\nDocument.\n", encoding="utf-8")

            res_upd = run_smart_drive_cli(["update", "--root", str(ws), "--json"])
            self.assertEqual(res_upd.returncode, 0)
            data = json.loads(res_upd.stdout)
            self.assertIn("added", data)
            self.assertGreaterEqual(data["added"], 1)

            # Verify newly added file path is searchable
            res_search = run_smart_drive_cli(["search", "research", "--root", str(ws), "--json"])
            self.assertEqual(res_search.returncode, 0)
            results = json.loads(res_search.stdout)
            self.assertTrue(any("new_research" in r["name"] for r in results))

    def test_cli_dup_subcommand(self) -> None:
        """`dup` identifies identical file duplicates and calculates reclaimable space."""
        with TempWorkspace() as ws:
            create_mock_ssd_tree(ws)
            res_dup = run_smart_drive_cli(["dup", "--root", str(ws), "--json"])
            self.assertEqual(res_dup.returncode, 0)
            data = json.loads(res_dup.stdout)
            self.assertIn("duplicate_group_count", data)
            self.assertGreater(data["duplicate_group_count"], 0)
            self.assertGreater(data["total_reclaimable_bytes"], 0)

    def test_cli_organize_subcommand(self) -> None:
        """`organize` auto-zones loose root files into standard taxonomies."""
        with TempWorkspace() as ws:
            # Create a loose script (.sh) and loose model file (.safetensors) at root
            loose_script = ws / "loose_script.sh"
            loose_script.write_text("#!/bin/bash\necho test\n", encoding="utf-8")
            loose_model = ws / "loose_weights.safetensors"
            loose_model.write_bytes(b"MODEL_TENSOR_WEIGHTS" * 50)

            # 1. Dry run
            res_dry = run_smart_drive_cli(["organize", "--root", str(ws), "--dry-run", "--json"])
            self.assertEqual(res_dry.returncode, 0)
            dry_data = json.loads(res_dry.stdout)
            self.assertEqual(dry_data["status"], "dry_run")
            self.assertGreater(len(dry_data["planned_actions"]), 0)
            self.assertTrue(loose_script.is_file(), "Dry run must not move files")

            # 2. Apply
            res_apply = run_smart_drive_cli(["organize", "--root", str(ws), "--apply", "--json"])
            self.assertEqual(res_apply.returncode, 0)
            apply_data = json.loads(res_apply.stdout)
            self.assertEqual(apply_data["status"], "applied")
            self.assertGreater(apply_data["zoning"]["moved_count"], 0)

            # Loose files should now be relocated into standard taxonomies
            self.assertFalse(loose_script.exists())
            self.assertFalse(loose_model.exists())

    def test_cli_sentinel_subcommand(self) -> None:
        """`sentinel` and `agent-check` report SSD health and self-heal missing shields."""
        with TempWorkspace() as ws:
            run_smart_drive_cli(["init", "--root", str(ws)])

            # Delete a shield to test self-healing
            shield = ws / ".metadata_never_index"
            if shield.exists():
                shield.unlink()

            # Run sentinel
            res = run_smart_drive_cli(["sentinel", "--root", str(ws), "--json"])
            self.assertEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertTrue(data["is_healthy"])
            self.assertTrue(shield.is_file(), "Sentinel must auto-heal missing shield")

            # Run agent-check alias
            res_alias = run_smart_drive_cli(["agent-check", "--root", str(ws), "--json"])
            self.assertEqual(res_alias.returncode, 0)

    def test_cli_mcp_config_subcommand(self) -> None:
        """`mcp-config` registers local .mcp.json in specified target directory."""
        with TempWorkspace() as ws:
            res = run_smart_drive_cli(["mcp-config", "--target-dir", str(ws), "--json"])
            self.assertEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertTrue(data.get("workspace"))

            mcp_file = ws / ".mcp.json"
            self.assertTrue(mcp_file.is_file())
            content = json.loads(mcp_file.read_text(encoding="utf-8"))
            self.assertIn("smart-drive", content["mcpServers"])


if __name__ == "__main__":
    unittest.main()
