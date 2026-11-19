"""tests/test_exfat_shield.py - Root-cause exFAT bloat prevention shield and offloader guard."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import patch

from smart_drive.core.exfat_shield import (
    SHIELD_ENV_SPECS,
    _Win32UserEnvStore,
    apply_exfat_shield,
    audit_exfat_shield,
    recommend_cache_base,
    revert_exfat_shield,
)
from smart_drive.core.fsinfo import FilesystemInfo
from smart_drive.core.offloader import offload_cache


class FakeEnvStore:
    def __init__(self, initial: Optional[Dict[str, str]] = None) -> None:
        self.vars: Dict[str, str] = dict(initial or {})
        self.commits = 0

    def get_all(self) -> Dict[str, str]:
        return dict(self.vars)

    def get(self, name: str) -> Optional[str]:
        return self.vars.get(name)

    def set(self, name: str, value: str) -> None:
        self.vars[name] = value

    def delete(self, name: str) -> None:
        self.vars.pop(name, None)

    def commit(self) -> None:
        self.commits += 1


class TestExfatShield(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sd_shield_test_")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d_root = self.tmp / "exfat_d"
        self.e_cache = self.tmp / "ntfs_e" / ".smart_drive_caches"
        self.home = self.tmp / "home"
        self.d_root.mkdir(parents=True)
        self.e_cache.parent.mkdir(parents=True)
        self.home.mkdir(parents=True)

        self.vols = [
            {"mount_point": "C:\\", "fs_type": "ntfs", "block_size": 4096, "free_bytes": 50 * 1024**3, "total_bytes": 450 * 1024**3, "is_system": True, "is_fixed": True},
            {"mount_point": "E:\\", "fs_type": "ntfs", "block_size": 4096, "free_bytes": 140 * 1024**3, "total_bytes": 930 * 1024**3, "is_system": False, "is_fixed": True},
            {"mount_point": "D:\\", "fs_type": "exfat", "block_size": 524_288, "free_bytes": 40 * 1024**3, "total_bytes": 1900 * 1024**3, "is_system": False, "is_fixed": True},
        ]

    def _fs_for(self, path_str: str) -> FilesystemInfo:
        norm = os.path.abspath(path_str).lower()
        exfat_prefix = str(self.d_root).lower()
        if norm.startswith(exfat_prefix) or norm.startswith("d:\\"):
            return FilesystemInfo(path_str, "exfat", 524_288, str(self.d_root), "mock")
        return FilesystemInfo(path_str, "ntfs", 4096, str(self.e_cache.parent), "mock")

    def test_recommend_cache_base_prefers_secondary_ntfs_over_c_and_falls_back(self) -> None:
        exfat_fs = FilesystemInfo("D:\\", "exfat", 524_288, "D:\\", "mock")
        rec = recommend_cache_base(exfat_fs, home=self.home, volumes=self.vols)
        self.assertEqual(rec["source"], "secondary_ntfs")
        self.assertTrue(rec["path"].startswith("E:\\"))

        low_e_vols = [
            self.vols[0],
            {**self.vols[1], "free_bytes": 5 * 1024**3},
            self.vols[2],
        ]
        fallback = recommend_cache_base(exfat_fs, home=self.home, volumes=low_e_vols)
        self.assertEqual(fallback["source"], "system_local")
        self.assertFalse(fallback["path"].startswith("D:\\"))

    def test_audit_detects_unset_exfat_risk_and_safe_ntfs_vars(self) -> None:
        env = {
            "CARGO_TARGET_DIR": str(self.e_cache / "cargo-target"),
            "RUFF_CACHE_DIR": str(self.d_root / "bad_ruff"),
            "RUSTUP_HOME": str(self.d_root / ".rustup"),
        }
        rep = audit_exfat_shield(
            root=str(self.d_root),
            env=env,
            cache_dir=str(self.e_cache),
            home=self.home,
            fs_detector=self._fs_for,
            volumes=self.vols,
        )
        self.assertTrue(rep["is_large_cluster"])
        self.assertTrue(rep["needs_attention"])
        by_var = {v["var"]: v for v in rep["variables"]}
        self.assertEqual(by_var["CARGO_TARGET_DIR"]["status"], "protected")
        self.assertEqual(by_var["RUFF_CACHE_DIR"]["status"], "risk_on_exfat")
        self.assertEqual(by_var["MYPY_CACHE_DIR"]["status"], "unprotected")
        self.assertEqual(len(rep["advisory_risks"]), 1)
        self.assertEqual(rep["advisory_risks"][0]["var"], "RUSTUP_HOME")

    def test_ntfs_volume_reports_no_attention_needed(self) -> None:
        rep = audit_exfat_shield(
            root=str(self.e_cache.parent),
            env={},
            home=self.home,
            fs_info=FilesystemInfo(str(self.e_cache.parent), "ntfs", 4096, "C:\\", "mock"),
            fs_detector=self._fs_for,
            volumes=self.vols,
        )
        self.assertFalse(rep["is_large_cluster"])
        self.assertFalse(rep["needs_attention"])

    def test_apply_rejects_exfat_cache_dir(self) -> None:
        store = FakeEnvStore()
        with self.assertRaises(ValueError) as ctx:
            apply_exfat_shield(
                root=str(self.d_root),
                cache_dir=str(self.d_root / "bad_cache"),
                dry_run=False,
                env={},
                env_store=store,
                fs_detector=self._fs_for,
                volumes=self.vols,
                home=self.home,
            )
        self.assertIn("exFAT", str(ctx.exception))
        self.assertEqual(store.vars, {})

    def test_apply_dry_run_apply_and_exact_revert_lifecycle(self) -> None:
        store = FakeEnvStore(initial={"CARGO_TARGET_DIR": str(self.e_cache / "custom-cargo")})
        env = {"CARGO_TARGET_DIR": str(self.e_cache / "custom-cargo")}

        dry = apply_exfat_shield(
            root=str(self.d_root),
            cache_dir=str(self.e_cache),
            dry_run=True,
            env=env,
            env_store=store,
            fs_detector=self._fs_for,
            volumes=self.vols,
            home=self.home,
        )
        self.assertEqual(dry["status"], "dry_run")
        self.assertEqual(dry["preserved_vars"], ["CARGO_TARGET_DIR"])
        self.assertNotIn("CARGO_TARGET_DIR", dry["planned_vars"])
        self.assertEqual(len(dry["planned_vars"]), len(SHIELD_ENV_SPECS) - 1)
        self.assertFalse((self.e_cache / "shield_manifest.json").exists())

        applied = apply_exfat_shield(
            root=str(self.d_root),
            cache_dir=str(self.e_cache),
            dry_run=False,
            env=env,
            env_store=store,
            fs_detector=self._fs_for,
            volumes=self.vols,
            home=self.home,
        )
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(store.commits, 1)
        self.assertEqual(store.vars["CARGO_TARGET_DIR"], str(self.e_cache / "custom-cargo"))
        self.assertEqual(store.vars["RUFF_CACHE_DIR"], str(self.e_cache / "ruff-cache"))
        self.assertTrue((self.e_cache / "ruff-cache").is_dir())
        manifest = json.loads((self.e_cache / "shield_manifest.json").read_text(encoding="utf-8"))
        self.assertIsNone(manifest["previous_values"]["RUFF_CACHE_DIR"])

        reverted = revert_exfat_shield(
            str(self.e_cache),
            dry_run=False,
            env_store=store,
            mutate_process_env=False,
        )
        self.assertEqual(reverted["status"], "reverted")
        self.assertNotIn("RUFF_CACHE_DIR", store.vars)
        self.assertEqual(store.vars["CARGO_TARGET_DIR"], str(self.e_cache / "custom-cargo"))

    def test_offloader_blocks_micro_file_caches_on_512k_exfat_unless_forced(self) -> None:
        mock_user = self.tmp / "c_user"
        uv_cache = mock_user / "AppData" / "Local" / "uv" / "cache"
        uv_cache.mkdir(parents=True)
        (uv_cache / "a.whl").write_bytes(b"W" * 100)

        hf_cache = mock_user / ".cache" / "huggingface"
        hf_cache.mkdir(parents=True)
        (hf_cache / "model.safetensors").write_bytes(b"M" * 100)

        target_exfat = self.tmp / "exfat_target"
        target_exfat.mkdir()

        env = {
            "USERPROFILE": str(mock_user),
            "HOME": str(mock_user),
            "LOCALAPPDATA": str(mock_user / "AppData" / "Local"),
            "APPDATA": str(mock_user / "AppData" / "Roaming"),
            "SMART_DRIVE_MOCK_USERPROFILE": str(mock_user),
            "SMART_DRIVE_ALLOW_MOCK_TARGET": "1",
        }
        fake_exfat = FilesystemInfo(str(target_exfat), "exfat", 524_288, str(target_exfat), "mock")

        with patch.dict(os.environ, env), \
                patch("smart_drive.core.offloader.detect_filesystem", return_value=fake_exfat), \
                patch("smart_drive.core.offloader._running_processes", return_value=set()):
            dry = offload_cache("uv", target_exfat, dry_run=True)
            self.assertIn("exFAT cluster slack risk", dry["message"])
            self.assertTrue(any("512 KB" in w for w in dry["warnings"]))

            with self.assertRaises(ValueError) as ctx:
                offload_cache("uv", target_exfat, dry_run=False, force=False)
            self.assertIn("512 KB", str(ctx.exception))

            forced = offload_cache("uv", target_exfat, dry_run=False, force=True)
            self.assertEqual(forced["status"], "success")
            self.assertTrue(any("512 KB" in w for w in forced["warnings"]))

            # Large-file AI model cache is unaffected and allowed cleanly on exFAT
            hf_res = offload_cache("huggingface", target_exfat, dry_run=False, force=False)
            self.assertEqual(hf_res["status"], "success")
            self.assertEqual(hf_res["warnings"], [])

    def test_win32_user_env_store_reads_value_count_not_subkey_count(self) -> None:
        """QueryInfoKey returns (subkey_count, value_count, mtime); subkey_count is typically 0."""
        import sys
        import types

        class FakeKey:
            def __enter__(self) -> "FakeKey":
                return self

            def __exit__(self, *args: object) -> None:
                return None

        fake_winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER="HKCU",
            KEY_READ=1,
            OpenKey=lambda hive, subkey, res, sam: FakeKey(),
            QueryInfoKey=lambda key: (0, 2, 12345),
            EnumValue=lambda key, idx: [
                ("CARGO_TARGET_DIR", r"E:\.smart_drive_caches\cargo-target", 1),
                ("RUFF_CACHE_DIR", r"E:\.smart_drive_caches\ruff-cache", 1),
            ][idx],
        )
        with patch.object(sys, "platform", "win32"), patch.dict(sys.modules, {"winreg": fake_winreg}):
            got = _Win32UserEnvStore().get_all()
        self.assertEqual(
            got,
            {
                "CARGO_TARGET_DIR": r"E:\.smart_drive_caches\cargo-target",
                "RUFF_CACHE_DIR": r"E:\.smart_drive_caches\ruff-cache",
            },
        )


if __name__ == "__main__":
    unittest.main()
