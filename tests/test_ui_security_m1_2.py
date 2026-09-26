"""tests/test_ui_security_m1_2.py - Empirical Challenger M1-2 Safety & Security Tests.

Empirically challenges the safety and security boundaries of the Web UI cleanup mechanism:
1. Protected root files preservation (GEMINI.md, CLAUDE.md, README.md, Quick_*.bat, etc.)
2. Anti-indexing markers protection (.metadata_never_index, .fseventsd/no_log)
3. Path traversal attacks (../, ..\\, absolute outside paths)
4. Inviolable directories (.git repository and internal files)
5. Strict dry-run filesystem immutability guarantees
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.indexer.db import DatabaseManager
from smart_drive.indexer.manager import IndexManager
from smart_drive.ui.server import ThreadingHTTPServer, create_server
from tests.helpers import SmartDriveTestCase, create_mock_ssd_tree


def sha256_file(filepath: Path) -> str:
    """Calculates SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestUiSecurityM12(SmartDriveTestCase):
    """Adversarial security test suite targeting POST /api/junk/clean."""

    def setUp(self) -> None:
        super().setUp()
        # Create an isolated mock drive root inside self.test_dir
        self.mock_root = create_mock_ssd_tree(self.test_dir / "secure_mock_drive")
        self.db_path = self.mock_root / ".smart_drive" / "index.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with DatabaseManager(str(self.db_path)) as db:
            db.initialize_schema()
            mgr = IndexManager(db, str(self.mock_root))
            mgr.full_index()

        self.server = create_server(
            root_path=self.mock_root,
            port=0,
            db_path=self.db_path,
        )
        self.port = self.server.server_address[1]
        self.srv_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.srv_thread.start()

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
            self.srv_thread.join(timeout=3)
        finally:
            super().tearDown()

    def _post_clean(self, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        """Sends POST request to /api/junk/clean and returns (status_code, json_dict)."""
        url = f"http://127.0.0.1:{self.port}/api/junk/clean"
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json", "Content-Length": str(len(data_bytes))},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                body = json.loads(resp.read().decode("utf-8"))
                return status, body
        except urllib.error.HTTPError as exc:
            body = {}
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                pass
            return exc.code, body

    # =========================================================================
    # 1. CHALLENGE: Protected Root Files Preservation
    # =========================================================================

    def test_cannot_trick_clean_into_deleting_protected_root_files(self) -> None:
        """Explicit path injection of protected root files must NEVER delete them."""
        # Ensure protected root files exist
        protected_files = {
            "GEMINI.md": "Keep this secure GEMINI instructions",
            "CLAUDE.md": "Keep this secure CLAUDE instructions",
            "README.md": "SmartDrive Documentation",
            "AGENTS.md": "Agent Rules",
            ".mcp.json": '{"mcpServers": {}}',
            "Quick_clean.bat": "@echo off\necho Cleaning",
            "Quick_search.bat": "@echo off\necho Searching",
            "smart_drive.bat": "@echo off\necho Run",
            "setup_ssd.bat": "@echo off\necho Setup",
        }
        for name, content in protected_files.items():
            fpath = self.mock_root / name
            fpath.write_text(content, encoding="utf-8")

        # Record initial hashes
        initial_hashes = {name: sha256_file(self.mock_root / name) for name in protected_files}

        # Vector 1: Explicitly pass protected paths in POST /api/junk/clean with dry_run=False
        targets = list(protected_files.keys())
        # Add lowercase / mixed case variations
        targets.extend(["gemini.md", "GeMiNi.Md", "readme.md", "./GEMINI.md", ".\\README.md"])
        # Add absolute path versions
        targets.append(str((self.mock_root / "GEMINI.md").resolve()))

        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": targets,
        })
        self.assertEqual(status, 200)

        # Verify all protected files remain intact with identical content
        for name, expected_hash in initial_hashes.items():
            fpath = self.mock_root / name
            self.assertTrue(fpath.exists(), f"Protected root file was deleted: {name}")
            self.assertEqual(sha256_file(fpath), expected_hash, f"Protected file content was modified: {name}")

        # Verify none of them were reported as purged
        purged_paths = [p["path"] for p in resp.get("purged", [])]
        for name in protected_files:
            for p in purged_paths:
                self.assertNotIn(name.lower(), p.lower(), f"Protected file appeared in purged list: {p}")

    def test_full_clean_preserves_all_protected_root_files(self) -> None:
        """Full cleanup across all tiers (1, 2, 3) must leave all protected root files untouched."""
        protected_names = [
            "GEMINI.md",
            "CLAUDE.md",
            "README.md",
            "AGENTS.md",
            ".mcp.json",
            "Quick_clean.bat",
            "Quick_search.bat",
            "smart_drive.bat",
        ]
        for name in protected_names:
            (self.mock_root / name).write_text(f"Protected content for {name}", encoding="utf-8")

        # Execute full clean across all tiers without paths filter
        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
        })
        self.assertEqual(status, 200)
        self.assertFalse(resp["dry_run"])

        # Check all protected files still exist
        for name in protected_names:
            fpath = self.mock_root / name
            self.assertTrue(fpath.exists(), f"Full clean unlinked protected file: {name}")

    # =========================================================================
    # 2. CHALLENGE: Anti-Indexing Markers Protection & Restoration
    # =========================================================================

    def test_cannot_trick_clean_into_deleting_anti_indexing_markers(self) -> None:
        """Anti-indexing markers (.metadata_never_index, .fseventsd/no_log) must NEVER be unlinked."""
        root_marker = self.mock_root / ".metadata_never_index"
        fsevent_dir = self.mock_root / ".fseventsd"
        fsevent_dir.mkdir(parents=True, exist_ok=True)
        fsevent_marker = fsevent_dir / "no_log"

        root_marker.write_text("anti-indexing marker", encoding="utf-8")
        fsevent_marker.write_text("anti-indexing fsevent marker", encoding="utf-8")

        # Attack: Explicitly request unlinking anti-indexing markers
        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": [
                ".metadata_never_index",
                str(root_marker.resolve()),
                ".fseventsd/no_log",
                ".fseventsd\\no_log",
                str(fsevent_marker.resolve()),
            ],
        })
        self.assertEqual(status, 200)

        # Both markers must exist
        self.assertTrue(root_marker.exists(), "Root anti-indexing marker .metadata_never_index was deleted!")
        self.assertTrue(fsevent_marker.exists(), "Fsevents anti-indexing marker .fseventsd/no_log was deleted!")

    def test_clean_restores_anti_indexing_markers_if_missing(self) -> None:
        """POST /api/junk/clean with dry_run=False automatically restores missing anti-indexing markers."""
        root_marker = self.mock_root / ".metadata_never_index"
        fsevent_marker = self.mock_root / ".fseventsd" / "no_log"

        # Remove markers if they exist
        if root_marker.exists():
            root_marker.unlink()
        if fsevent_marker.exists():
            fsevent_marker.unlink()

        self.assertFalse(root_marker.exists())

        # Run clean with dry_run=False
        status, resp = self._post_clean({
            "tiers": [1],
            "dry_run": False,
        })
        self.assertEqual(status, 200)

        # Markers must now be restored
        self.assertTrue(root_marker.exists(), "PurgeEngine did not restore .metadata_never_index!")
        self.assertTrue(fsevent_marker.exists(), "PurgeEngine did not restore .fseventsd/no_log!")

    # =========================================================================
    # 3. CHALLENGE: Path Traversal (../ attacks) Outside Drive Root
    # =========================================================================

    def test_cannot_delete_files_outside_drive_root_via_path_traversal(self) -> None:
        """Path traversal attacks (../, ..\\, absolute outside) must NEVER unlink outside files."""
        # Create canary files in parent directory (strictly outside self.mock_root)
        parent_dir = self.mock_root.parent
        canary1 = parent_dir / "victim_outside_1.txt"
        canary2 = parent_dir / "victim_outside_2.txt"
        canary3 = self.test_dir / "system_important_file.conf"

        canary1.write_text("Do not touch canary 1", encoding="utf-8")
        canary2.write_text("Do not touch canary 2", encoding="utf-8")
        canary3.write_text("Critical host system file", encoding="utf-8")

        traversal_attempts = [
            "../victim_outside_1.txt",
            "..\\victim_outside_1.txt",
            "../../victim_outside_1.txt",
            "./../victim_outside_2.txt",
            "/../victim_outside_2.txt",
            "....//....//victim_outside_2.txt",
            str(canary1.resolve()),
            str(canary2.resolve()),
            str(canary3.resolve()),
            # System paths
            "C:\\Windows\\System32\\drivers\\etc\\hosts",
            "/etc/passwd",
        ]

        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": traversal_attempts,
        })
        self.assertEqual(status, 200)

        # Verify ALL outside canary files remain intact
        self.assertTrue(canary1.exists(), "Canary 1 was deleted via path traversal!")
        self.assertTrue(canary2.exists(), "Canary 2 was deleted via path traversal!")
        self.assertTrue(canary3.exists(), "Canary 3 was deleted via outside path injection!")

        self.assertEqual(canary1.read_text(encoding="utf-8"), "Do not touch canary 1")
        self.assertEqual(canary2.read_text(encoding="utf-8"), "Do not touch canary 2")
        self.assertEqual(canary3.read_text(encoding="utf-8"), "Critical host system file")

        # Ensure no outside file was reported as purged
        for p in resp.get("purged", []):
            purged_path = p.get("path", "")
            self.assertFalse(purged_path.startswith(str(parent_dir)) and not purged_path.startswith(str(self.mock_root)))

    # =========================================================================
    # 4. CHALLENGE: Inviolable Directories (.git repository)
    # =========================================================================

    def test_git_directory_and_contents_are_strictly_inviolable(self) -> None:
        """The .git directory and all files inside it must NEVER be deleted."""
        git_dir = self.mock_root / ".git"
        git_dir.mkdir(parents=True, exist_ok=True)
        git_config = git_dir / "config"
        git_head = git_dir / "HEAD"
        git_objects = git_dir / "objects"
        git_objects.mkdir(parents=True, exist_ok=True)
        git_blob = git_objects / "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

        git_config.write_text("[core]\nrepositoryformatversion = 0\n", encoding="utf-8")
        git_head.write_text("ref: refs/heads/main\n", encoding="utf-8")
        git_blob.write_bytes(b"git object binary data")

        # Also plant junk-pattern files INSIDE .git
        git_thumbs = git_dir / "Thumbs.db"
        git_ds_store = git_dir / ".DS_Store"
        git_tmp = git_dir / "temp_cache.tmp"
        git_thumbs.write_bytes(b"junk thumbs inside git")
        git_ds_store.write_bytes(b"junk ds_store inside git")
        git_tmp.write_bytes(b"junk tmp inside git")

        # Attack 1: Request deleting .git root and individual git files
        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": [
                ".git",
                ".git/",
                ".git\\",
                str(git_dir.resolve()),
                ".git/config",
                ".git/HEAD",
                ".git/Thumbs.db",
                ".git/.DS_Store",
                ".git/temp_cache.tmp",
            ],
        })
        self.assertEqual(status, 200)

        # Verify .git directory and core files still exist
        self.assertTrue(git_dir.exists(), "Git directory .git was deleted!")
        self.assertTrue(git_config.exists(), ".git/config was deleted!")
        self.assertTrue(git_head.exists(), ".git/HEAD was deleted!")
        self.assertTrue(git_blob.exists(), ".git/objects blob was deleted!")

        # Attack 2: Run full clean across all tiers without paths filter
        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
        })
        self.assertEqual(status, 200)

        # Verify .git directory and its internal files are STILL intact
        self.assertTrue(git_dir.exists(), ".git was deleted in full clean!")
        self.assertTrue(git_config.exists(), ".git/config was deleted in full clean!")
        self.assertTrue(git_head.exists(), ".git/HEAD was deleted in full clean!")
        self.assertTrue(git_thumbs.exists(), "Junk file inside .git was improperly targeted!")
        self.assertTrue(git_ds_store.exists(), "Junk file inside .git was improperly targeted!")

    # =========================================================================
    # 5. CHALLENGE: Strict Dry-Run Filesystem Immutability
    # =========================================================================

    def test_dry_run_strictly_prevents_any_filesystem_modification(self) -> None:
        """When dry_run=True, zero files or directories can be unlinked or modified."""
        # Plant realistic junk candidates across all three tiers
        tier1_ds = self.mock_root / "02_Learning_Knowledge" / ".DS_Store"
        tier1_thumbs = self.mock_root / "04_Creative_Assets" / "Thumbs.db"
        tier1_apple = self.mock_root / "03_Personal_Documents" / "._notes.txt"

        tier2_cache_dir = self.mock_root / "05_Dev_Toolbox" / "__pycache__"
        tier2_cache_dir.mkdir(parents=True, exist_ok=True)
        tier2_pyc = tier2_cache_dir / "helper.cpython-311.pyc"

        tier3_dmp = self.mock_root / "05_Dev_Toolbox" / "crash_test.dmp"
        tier3_tmp = self.mock_root / "06_Archives_Storage" / "temp_backup.tmp"

        for p in (tier1_ds, tier1_thumbs, tier1_apple, tier2_pyc, tier3_dmp, tier3_tmp):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"JUNK DATA 1234567890" * 100)

        junk_files = [tier1_ds, tier1_thumbs, tier1_apple, tier2_pyc, tier3_dmp, tier3_tmp]

        # Record initial states: existence, byte sizes, sha256, mtimes
        initial_states = {}
        for jf in junk_files:
            self.assertTrue(jf.exists(), f"Precondition failed: {jf} does not exist")
            stat = jf.stat()
            initial_states[jf] = {
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "sha256": sha256_file(jf),
            }

        # Send POST /api/junk/clean with dry_run=True across all tiers
        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(resp.get("dry_run"))
        self.assertGreater(resp.get("total_attempted", 0), 0)
        self.assertGreater(resp.get("nominal_bytes_reclaimed", 0), 0)

        # Verify simulated status on all reported records
        records = resp.get("records", [])
        for rec in records:
            if rec.get("status") not in ("BLOCKED", "SIMULATED"):
                self.fail(f"Record has non-simulated status in dry-run mode: {rec}")

        # Rigorous check: Every single candidate file must exist with exact same mtime & content
        for jf, state in initial_states.items():
            self.assertTrue(jf.exists(), f"DRY-RUN VIOLATION: File was unlinked from disk: {jf}")
            cur_stat = jf.stat()
            self.assertEqual(cur_stat.st_size, state["size"], f"File size changed during dry-run: {jf}")
            self.assertEqual(cur_stat.st_mtime, state["mtime"], f"mtime changed during dry-run: {jf}")
            self.assertEqual(sha256_file(jf), state["sha256"], f"Content mutated during dry-run: {jf}")

        # Now execute with dry_run=False to confirm real unlinking ONLY happens when dry_run=False
        status_real, resp_real = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
        })
        self.assertEqual(status_real, 200)
        self.assertFalse(resp_real.get("dry_run"))
        self.assertGreater(resp_real.get("total_succeeded", 0), 0)

        # Confirm junk files ARE now deleted
        for jf in (tier1_ds, tier1_thumbs, tier1_apple, tier3_dmp, tier3_tmp):
            self.assertFalse(jf.exists(), f"File should have been deleted in live purge: {jf}")

    # =========================================================================
    # 6. CHALLENGE: User Files in Protected Taxonomies & Boundary Extremes
    # =========================================================================

    def test_user_files_in_protected_taxonomies_preserved(self) -> None:
        """User assets in protected taxonomies must NEVER be unlinked even if explicitly targeted."""
        ai_model = self.mock_root / "01_AI_Models" / "custom_model.safetensors"
        learning_doc = self.mock_root / "02_Learning_Knowledge" / "deep_learning_paper.pdf"
        personal_doc = self.mock_root / "03_Personal_Documents" / "tax_returns_2025.xlsx"
        dev_code = self.mock_root / "05_Dev_Toolbox" / "deploy_cluster.py"

        for p in (ai_model, learning_doc, personal_doc, dev_code):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"Valuable user asset data: DO NOT DELETE")

        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": [
                "01_AI_Models/custom_model.safetensors",
                "02_Learning_Knowledge/deep_learning_paper.pdf",
                "03_Personal_Documents/tax_returns_2025.xlsx",
                "05_Dev_Toolbox/deploy_cluster.py",
                str(ai_model.resolve()),
                str(personal_doc.resolve()),
            ],
        })
        self.assertEqual(status, 200)

        # All user files must be intact
        for p in (ai_model, learning_doc, personal_doc, dev_code):
            self.assertTrue(p.exists(), f"Valuable user asset in protected taxonomy was deleted: {p}")
            self.assertEqual(p.read_bytes(), b"Valuable user asset data: DO NOT DELETE")

    def test_drive_root_unlinking_blocked(self) -> None:
        """Targeting drive root ('', '.', '/', root path) must NEVER damage or delete drive root."""
        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": ["", ".", "/", "\\", "./", ".\\", str(self.mock_root.resolve())],
        })
        self.assertEqual(status, 200)
        self.assertTrue(self.mock_root.exists(), "Drive root was unlinked!")
        self.assertTrue(self.mock_root.is_dir(), "Drive root is no longer a directory!")

    def test_api_clean_malformed_requests(self) -> None:
        """Malformed requests to /api/junk/clean are handled securely without crashing."""
        # 1. Empty body
        url = f"http://127.0.0.1:{self.port}/api/junk/clean"
        req = urllib.request.Request(url, data=b"", headers={"Content-Length": "0"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)

        # 2. Invalid JSON string
        req2 = urllib.request.Request(
            url,
            data=b"{not_valid_json",
            headers={"Content-Length": "15", "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx2:
            urllib.request.urlopen(req2)
        self.assertEqual(ctx2.exception.code, 400)

        # 3. Empty tiers list
        status, body = self._post_clean({"tiers": [], "dry_run": False})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

        # 4. Non-list tiers
        status, body = self._post_clean({"tiers": "1,2", "dry_run": False})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_nonexistent_paths_filter_is_noop(self) -> None:
        """Filtering by non-existent paths is safe and deletes zero files."""
        status, resp = self._post_clean({
            "tiers": [1, 2, 3],
            "dry_run": False,
            "paths": ["non_existent_folder/phantom.tmp", "nowhere/to/be/found.junk"],
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(resp.get("purged", [])), 0)
        self.assertEqual(resp.get("nominal_bytes_reclaimed", 0), 0)


if __name__ == "__main__":
    unittest.main()

