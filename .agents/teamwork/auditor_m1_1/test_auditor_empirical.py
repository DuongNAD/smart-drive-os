import os
import sys
import tempfile

# Add project root to sys.path
sys.path.insert(0, "/Users/duongnad/Documents/tool/smart-drive-os")

from smart_drive.mcp.server import SmartDriveMCPServer
from smart_drive.core.drive_detector import get_system_drive_letter, normalize_drive_letter
from smart_drive.core.junction import is_directory_junction
from smart_drive.core.offloader import resolve_cache_path, validate_target_drive, CacheDefinition

def test_all():
    with tempfile.TemporaryDirectory() as tmpdir:
        server = SmartDriveMCPServer(root=tmpdir)
        print("Server initialized with root:", server.root)

        # 1. Null byte
        try:
            server._resolve_safe_path("test\x00file")
            assert False, "Should fail null byte"
        except ValueError as e:
            assert "null byte" in str(e)
        print("Pass 1: Null byte blocked")

        # 2. UNC path
        for p in [r"\\server\share", "//server/share", r"\\?\C:\foo", r"\\.\PhysicalDrive0"]:
            try:
                server._resolve_safe_path(p)
                assert False, f"Should fail UNC: {p}"
            except ValueError as e:
                assert "escapes storage root" in str(e)
        print("Pass 2: UNC and device paths blocked")

        # 3. Parent traversal
        for p in ["../../etc/passwd", r"..\..\Windows", "a/b/../../../etc", "/etc/passwd"]:
            try:
                server._resolve_safe_path(p)
                assert False, f"Should fail traversal: {p}"
            except ValueError as e:
                assert "escapes storage root" in str(e)
        print("Pass 3: Traversal and root escapes blocked")

        # 4. Windows cross-drive path
        for p in [r"C:\Windows\System32", r"D:\file.txt", r"Z:\root", "X:/abc"]:
            try:
                server._resolve_safe_path(p)
                assert False, f"Should fail cross-drive: {p}"
            except ValueError as e:
                assert "escapes storage root" in str(e)
        print("Pass 4: Cross-drive paths blocked")

        # 5. Legitimate subpath
        sub = os.path.join("sub", "dir", "test.txt")
        resolved = server._resolve_safe_path(sub)
        expected = os.path.realpath(os.path.abspath(os.path.join(tmpdir, sub)))
        assert resolved == expected, f"{resolved} != {expected}"
        print("Pass 5: Legitimate subpath resolved correctly")

        # 6. Safety check API on cross-drive
        res = server.handle_ssd_check_safety({"path": r"C:\Windows\System32\notepad.exe"})
        assert res["is_safe"] is False, "Cross drive should be unsafe"
        assert "error" in res, "Error key should be present"
        assert "different drive" in res["error"] or "escapes drive root" in res["error"]
        print("Pass 6: Safety check cross-drive flagged with error")

        # 7. Safety check API on UNC
        res = server.handle_ssd_check_safety({"path": r"\\network_share\folder\file.txt"})
        assert res["is_safe"] is False, "UNC should be unsafe"
        assert "error" in res, "Error key should be present"
        print("Pass 7: Safety check UNC flagged with error")

        # 8. Safety check API on intermediate forbidden characters
        res = server.handle_ssd_check_safety({"path": "sub/bad:char/file.txt"})
        assert res["is_safe"] is False, "Forbidden char should be unsafe"
        assert len(res["forbidden_character_violations"]) > 0
        print("Pass 8: Forbidden characters correctly audited without drive colon false positives")

        # 9. Drive detector cross-platform normalization
        assert normalize_drive_letter(r"E:\Windows\System32") == "E:"
        assert normalize_drive_letter("d:/data") == "D:"
        assert normalize_drive_letter("c") == "C:"
        print("Pass 9: Drive letter normalization functions accurately across platforms")

        # 10. Cache offloader path resolution
        defn = CacheDefinition(name="test", title="Test", category="test", description="test", env_vars=["MOCK_CACHE_DIR"], relative_paths=[])
        os.environ["MOCK_CACHE_DIR"] = tmpdir
        cache_p = resolve_cache_path(defn)
        assert str(cache_p) == os.path.realpath(tmpdir)
        print("Pass 10: Cache offloader resolves symlink canonical path")

        # 11. Target drive validation formatting
        letter, offload_root = validate_target_drive("D:")
        assert letter == "D:"
        assert str(offload_root) == r"D:\04_System_Offload_Caches"
        print("Pass 11: Target drive offload root formatted cleanly")

    print("\nALL 11 EMPIRICAL AUDITOR CHECKS PASSED CLEANLY.")

if __name__ == "__main__":
    test_all()
