"""independent_cli_verify.py - Test CLI subcommands end-to-end via subprocess."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

def test_cli():
    print("Testing CLI subcommands via subprocess...")
    temp_dir = tempfile.mkdtemp(prefix="cli_audit_")
    try:
        drive_root = Path(temp_dir) / "mock_drive"
        drive_root.mkdir()
        part = drive_root / "02_Learning_Knowledge"
        part.mkdir()
        (part / "doc.md").write_text("# Knowledge", encoding="utf-8")

        # 1. Snapshot create
        cmd = [sys.executable, "-m", "smart_drive", "snapshot", "create", "cli_test", "--root", str(drive_root)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "Point-in-time snapshot created successfully" in res.stdout
        print("[OK] CLI snapshot create succeeded")

        # 2. Snapshot list
        cmd = [sys.executable, "-m", "smart_drive", "snapshot", "list", "--root", str(drive_root)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "cli_test" in res.stdout
        print("[OK] CLI snapshot list succeeded")

        # 3. Snapshot verify
        cmd = [sys.executable, "-m", "smart_drive", "snapshot", "verify", "cli_test", "--root", str(drive_root)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "INTACT" in res.stdout
        print("[OK] CLI snapshot verify succeeded (INTACT)")

        # 4. Backup
        backup_target = Path(temp_dir) / "backup_out"
        cmd = [sys.executable, "-m", "smart_drive", "backup", "--target", str(backup_target), "--root", str(drive_root)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "Copied:      1 files" in res.stdout
        print("[OK] CLI backup succeeded")

        # 5. Classify suggest & dry-run
        loose = drive_root / "downloads"
        loose.mkdir()
        (loose / "data.parquet").write_bytes(b"PAR1" + b"\x00" * 20 + b"PAR1")
        cmd = [sys.executable, "-m", "smart_drive", "classify", "--suggest", str(loose), "--root", str(drive_root)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "Parquet" in res.stdout
        print("[OK] CLI classify --suggest succeeded")

        cmd = [sys.executable, "-m", "smart_drive", "classify", "--dry-run", str(loose), "--root", str(drive_root)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "DRY-RUN" in res.stdout or "PLANNED" in res.stdout or "Simulate" in res.stdout or "Parquet" in res.stdout
        print("[OK] CLI classify --dry-run succeeded")

        cmd = [sys.executable, "-m", "smart_drive", "classify", "--apply", str(loose), "--root", str(drive_root)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert (drive_root / "01_AI_Models" / "Datasets" / "data.parquet").exists()
        print("[OK] CLI classify --apply succeeded (file moved)")

        print("ALL CLI SUBCOMMAND TESTS PASSED!")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    test_cli()
