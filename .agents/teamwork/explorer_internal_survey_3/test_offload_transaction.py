import os
import shutil
import subprocess
import tempfile

def test_offload_transaction():
    # Setup mock C: and mock D:
    c_base = tempfile.mkdtemp(prefix="mock_c_")
    d_base = tempfile.mkdtemp(prefix="mock_d_")
    try:
        # Mock source cache on C:
        src_cache = os.path.join(c_base, ".cache", "huggingface")
        os.makedirs(os.path.join(src_cache, "hub", "models--test"), exist_ok=True)
        with open(os.path.join(src_cache, "hub", "models--test", "model.safetensors"), "wb") as f:
            f.write(b"AI_MODEL_WEIGHTS" * 1024)

        target_base = os.path.join(d_base, "04_System_Offload_Caches")
        os.makedirs(target_base, exist_ok=True)
        final_target = os.path.join(target_base, "huggingface")
        staging_target = os.path.join(target_base, ".tmp_transfer_huggingface")

        # Step B: Copy to staging
        shutil.copytree(src_cache, staging_target)
        # Step C: Atomic rename on D:
        os.rename(staging_target, final_target)

        # Step D: Quarantine on C:
        bak_src = src_cache + ".bak"
        os.rename(src_cache, bak_src)

        # Step E: Create junction
        cmd = ["cmd.exe", "/c", "mklink", "/J", src_cache, final_target]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"mklink failed: {res.stderr}"

        # Verify junction
        read_file = os.path.join(src_cache, "hub", "models--test", "model.safetensors")
        with open(read_file, "rb") as f:
            data = f.read()
            assert data == b"AI_MODEL_WEIGHTS" * 1024

        # Step F: Purge quarantine
        shutil.rmtree(bak_src)
        print("Move transaction SUCCESS!")

        # Now test REVERT transaction:
        # Revert Step 1: Verify src is junction pointing to final_target
        assert os.path.exists(src_cache)
        # Revert Step 2: Staging copy on C:
        c_restore_staging = src_cache + ".revert_staging"
        shutil.copytree(final_target, c_restore_staging)
        # Revert Step 3: Remove junction
        os.unlink(src_cache)
        # Revert Step 4: Atomic rename to restore src
        os.rename(c_restore_staging, src_cache)
        # Revert Step 5: Clean up target on D:
        shutil.rmtree(final_target)

        # Verify reverted state
        assert os.path.isdir(src_cache)
        assert not os.path.islink(src_cache)
        with open(read_file, "rb") as f:
            assert f.read() == b"AI_MODEL_WEIGHTS" * 1024
        print("Revert transaction SUCCESS!")

    finally:
        # Cleanup mock dirs
        try:
            if os.path.islink(src_cache) or hasattr(os, "readlink"):
                try:
                    os.unlink(src_cache)
                except:
                    pass
        except:
            pass
        shutil.rmtree(c_base, ignore_errors=True)
        shutil.rmtree(d_base, ignore_errors=True)

test_offload_transaction()
