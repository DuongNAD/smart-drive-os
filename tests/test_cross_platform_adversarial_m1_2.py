"""tests/test_cross_platform_adversarial_m1_2.py - Adversarial Cross-Platform Verification.

EMPIRICAL CHALLENGER TEST SUITE (Milestone 1, Challenger 2):
Adversarially tests cross-platform components:
1. smart_drive/core/drive_detector.py under malformed/strange paths, mock system drives, and cluster math.
2. smart_drive/mcp/proxy.py under mock mount roots, working directory hierarchies, and POSIX/Windows cross-simulation.
3. smart_drive/core/junction.py with valid symlinks, broken symlinks, real directories, and non-existent paths.
4. smart_drive/ui/server.py under burst concurrent socket requests, malformed payloads, and host binding restrictions.

100% Python Standard Library. Zero external dependencies.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

from smart_drive.core.config import CLUSTER_SIZE_BYTES
from smart_drive.core.drive_detector import (
    DriveInfo,
    DriveType,
    FilesystemType,
    MockDriveBackend,
    get_backend,
    get_filesystem_adapter,
    get_system_drive_letter,
    inspect_drive,
    is_system_drive,
    list_drive_letters,
    list_secondary_drive_letters,
    list_secondary_drives,
    normalize_drive_letter,
    normalize_mount_point,
    use_mock_backend,
)
from smart_drive.core.junction import (
    create_directory_junction,
    get_junction_target,
    is_directory_junction,
    remove_directory_junction,
)
from smart_drive.mcp.proxy import SmartDriveProxy
from smart_drive.ui.server import ThreadingHTTPServer, create_server


class TestDriveDetectorCrossPlatformAdversarial(unittest.TestCase):
    """Adversarial stress-testing of drive_detector.py under strange/malformed inputs."""

    def test_normalize_drive_letter_malformed_and_strange_inputs(self) -> None:
        """normalize_drive_letter must accept valid drive letters and reject UNC, empty, and malformed specs."""
        # 1. Valid drive letters in various strange forms
        valid_cases = [
            ("c:", "C:"),
            ("C:", "C:"),
            ("z:\\", "Z:"),
            ("Z:/", "Z:"),
            ("d", "D:"),
            ("E", "E:"),
            ("D:\\some\\nested\\file.txt", "D:"),
            ("e:/forward/slashes/path", "E:"),
            ("   k:   ", "K:"),
            ("x:\\", "X:"),
            (Path("f:\\data"), "F:"),
            (Path("g:/folder"), "G:"),
            ("//?/C:/foo/bar", "C:"),
            ("\\\\.\\D:", "D:"),
            ("//./E:/test", "E:"),
            ("\\\\?\\Z:\\system", "Z:"),
        ]
        for inp, expected in valid_cases:
            with self.subTest(inp=inp):
                self.assertEqual(normalize_drive_letter(inp), expected)

        # 2. Malformed / invalid inputs that must be rejected with ValueError
        invalid_cases = [
            "//server/share",
            "\\\\server\\share",
            "//127.0.0.1/c$",
            "\\\\attacker.com\\payload",
            "",
            "   ",
            "\t\r\n",
            "/usr/local/bin",
            "relative/path/without/drive",
            "1:",
            ".:",
            "!:",
            "\\\\.\\PhysicalDrive0",
            "\\\\?\\Volume{12345678-1234-1234-1234-123456789abc}\\",
        ]
        for inp in invalid_cases:
            with self.subTest(invalid_inp=inp):
                with self.assertRaises(ValueError, msg=f"Should have rejected invalid drive spec: '{inp}'"):
                    normalize_drive_letter(inp)

        # 3. None input handling
        with self.assertRaises((ValueError, AttributeError, TypeError)):
            normalize_drive_letter(None)  # type: ignore

    def test_normalize_mount_point_formatting(self) -> None:
        """normalize_mount_point always outputs standard format with trailing backslash."""
        self.assertEqual(normalize_mount_point("d"), "D:\\")
        self.assertEqual(normalize_mount_point("e:"), "E:\\")
        self.assertEqual(normalize_mount_point("f:\\"), "F:\\")
        self.assertEqual(normalize_mount_point("g:/data"), "G:\\")
        with self.assertRaises(ValueError):
            normalize_mount_point("//server/share")
        with self.assertRaises(ValueError):
            normalize_mount_point("")

    def test_mock_system_drive_reassignment_and_exclusion(self) -> None:
        """System drive reassignment (e.g. E: or G:) must exclude both system drive AND C: from secondary drives."""
        backend = MockDriveBackend(system_drive="C:")
        # Add drives C:, D:, E:, F:, G:
        for letter in ["C:", "D:", "E:", "F:", "G:"]:
            backend.register_drive(
                DriveInfo(
                    drive_letter=letter,
                    mount_point=f"{letter}\\",
                    is_system_drive=(letter == "C:"),
                    hardware_type=DriveType.FIXED_INTERNAL if letter != "D:" else DriveType.REMOVABLE_EXTERNAL,
                    filesystem=FilesystemType.NTFS if letter != "D:" else FilesystemType.EXFAT,
                    cluster_size_bytes=4096 if letter != "D:" else 524288,
                    total_bytes=100 * (1024**3),
                    free_bytes=50 * (1024**3),
                    volume_label=f"Vol_{letter[0]}",
                )
            )

        with use_mock_backend(backend):
            # Baseline: C: is system drive
            self.assertEqual(get_system_drive_letter(), "C:")
            self.assertTrue(is_system_drive("C:"))
            self.assertTrue(is_system_drive("c:\\"))
            self.assertFalse(is_system_drive("D:"))
            sec_drives = list_secondary_drive_letters()
            self.assertNotIn("C:", sec_drives)
            self.assertEqual(sec_drives, ["D:", "E:", "F:", "G:"])

            # Adversarial test: Reassign system drive to E:
            backend.set_system_drive("E:")
            self.assertEqual(get_system_drive_letter(), "E:")
            self.assertTrue(is_system_drive("e:"))
            self.assertTrue(is_system_drive("E:\\Windows"))
            self.assertFalse(is_system_drive("C:"))

            # HARD INVARIANT: Both C: and E: (system drive) MUST BE EXCLUDED!
            sec_drives_after = list_secondary_drive_letters()
            self.assertNotIn("E:", sec_drives_after, "System drive E: must be excluded")
            self.assertNotIn("C:", sec_drives_after, "C: drive must always be excluded")
            self.assertEqual(sec_drives_after, ["D:", "F:", "G:"])

            # Inspect list_secondary_drives() objects
            sec_objs = list_secondary_drives()
            sec_obj_letters = [d.drive_letter for d in sec_objs]
            self.assertNotIn("C:", sec_obj_letters)
            self.assertNotIn("E:", sec_obj_letters)
            self.assertEqual(sec_obj_letters, ["D:", "F:", "G:"])

    def test_inspect_drive_unmounted_or_nonexistent(self) -> None:
        """inspect_drive raises FileNotFoundError on unmounted drives and ValueError on malformed inputs."""
        backend = MockDriveBackend.create_standard_mock()
        with use_mock_backend(backend):
            # Z: is not mounted in standard mock (C:, D:, E:, F: exist)
            with self.assertRaises(FileNotFoundError):
                inspect_drive("Z:")

            with self.assertRaises(FileNotFoundError):
                inspect_drive("z:\\")

            # Malformed specs
            with self.assertRaises(ValueError):
                inspect_drive("//server/share")
            with self.assertRaises(ValueError):
                inspect_drive("")

    def test_filesystem_adapter_cluster_math_adversarial(self) -> None:
        """FilesystemAdapter cluster allocation and slack calculations under boundary conditions."""
        exfat_adapter = get_filesystem_adapter("exFAT", cluster_size_bytes=524288)

        # 0 bytes consumes 0 clusters
        self.assertEqual(exfat_adapter.calculate_allocated_bytes(0), 0)
        self.assertEqual(exfat_adapter.calculate_slack_bytes(0), 0)
        self.assertEqual(exfat_adapter.calculate_slack_percentage(0), 0.0)

        # 1 byte consumes 1 cluster (512 KB)
        self.assertEqual(exfat_adapter.calculate_allocated_bytes(1), 524288)
        self.assertEqual(exfat_adapter.calculate_slack_bytes(1), 524287)
        self.assertAlmostEqual(exfat_adapter.calculate_slack_percentage(1), (524287 / 524288) * 100.0, places=3)

        # Exact cluster boundary
        self.assertEqual(exfat_adapter.calculate_allocated_bytes(524288), 524288)
        self.assertEqual(exfat_adapter.calculate_slack_bytes(524288), 0)
        self.assertEqual(exfat_adapter.calculate_slack_percentage(524288), 0.0)

        # Cluster boundary + 1 byte
        self.assertEqual(exfat_adapter.calculate_allocated_bytes(524289), 1048576)
        self.assertEqual(exfat_adapter.calculate_slack_bytes(524289), 524287)

        # Negative size must raise ValueError
        with self.assertRaises(ValueError):
            exfat_adapter.calculate_allocated_bytes(-1)
        with self.assertRaises(ValueError):
            exfat_adapter.calculate_slack_bytes(-500)


class TestProxyCrossPlatformAdversarial(unittest.TestCase):
    """Adversarial stress-testing of proxy.py dynamic mount point discovery."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_proxy_adv_")
        self.test_root = Path(self.temp_dir).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_env_root_overrides_valid_and_invalid(self) -> None:
        """SMART_DRIVE_ROOT and KINGSTON_SSD_ROOT precedence, handling valid and invalid paths."""
        mock_valid_dir = self.test_root / "valid_mount"
        mock_valid_dir.mkdir()

        # 1. SMART_DRIVE_ROOT takes precedence
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": str(mock_valid_dir), "KINGSTON_SSD_ROOT": "/other/path"}):
            detected = SmartDriveProxy.detect_mount_point()
            self.assertIsNotNone(detected)
            self.assertEqual(detected.resolve(), mock_valid_dir)

        # 2. KINGSTON_SSD_ROOT takes precedence if SMART_DRIVE_ROOT is empty
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": str(mock_valid_dir)}):
            detected = SmartDriveProxy.detect_mount_point()
            self.assertIsNotNone(detected)
            self.assertEqual(detected.resolve(), mock_valid_dir)

        # 3. Invalid/non-existent directory in env falls through without crash
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "/completely/non_existent/path/987654321", "KINGSTON_SSD_ROOT": ""}):
            detected = SmartDriveProxy.detect_mount_point()
            if detected:
                self.assertNotEqual(str(detected), "/completely/non_existent/path/987654321")

        # 4. File path instead of directory in env must NOT be treated as mount root
        dummy_file = self.test_root / "not_a_dir.txt"
        dummy_file.write_text("dummy", encoding="utf-8")
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": str(dummy_file), "KINGSTON_SSD_ROOT": ""}):
            detected = SmartDriveProxy.detect_mount_point()
            if detected:
                self.assertNotEqual(detected.resolve(), dummy_file.resolve())

    def test_nested_working_directory_structure_detection(self) -> None:
        """Deeply nested working directory resolves correctly to root containing marker and GEMINI.md or AGENTS.md."""
        deep_dir = self.test_root / "03_Development_Projects" / "frontend" / "src" / "components"
        deep_dir.mkdir(parents=True)
        marker_dir = self.test_root / ".smart_drive"
        marker_dir.mkdir(parents=True, exist_ok=True)

        # Case A: GEMINI.md + marker at test_root
        gemini_file = self.test_root / "GEMINI.md"
        gemini_file.write_text("# GEMINI", encoding="utf-8")

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
            with patch("pathlib.Path.cwd", return_value=deep_dir):
                detected = SmartDriveProxy.detect_mount_point()
                self.assertIsNotNone(detected)
                self.assertEqual(detected.resolve(), self.test_root.resolve())

        gemini_file.unlink()

        # Case B: AGENTS.md + marker at test_root
        agents_file = self.test_root / "AGENTS.md"
        agents_file.write_text("# AGENTS", encoding="utf-8")

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
            with patch("pathlib.Path.cwd", return_value=deep_dir):
                detected = SmartDriveProxy.detect_mount_point()
                self.assertIsNotNone(detected)
                self.assertEqual(detected.resolve(), self.test_root.resolve())

        agents_file.unlink()

        # Case C: Directory named KINGSTON alone (without marker/anchor) is not detected
        kingston_root = self.test_root / "Kingston"
        kingston_nested = kingston_root / "subdir" / "deep"
        kingston_nested.mkdir(parents=True)

        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": "", "SMART_DRIVE_NO_PROBE": "1"}):
            with patch("pathlib.Path.cwd", return_value=kingston_nested):
                detected = SmartDriveProxy.detect_mount_point()
                # Directory named Kingston without anchor/marker must not be detected
                self.assertIsNone(detected)

    def test_mock_windows_paths_on_posix_no_host_directory_leak(self) -> None:
        """Simulated Windows drive letter paths on POSIX do not resolve to local host repo."""
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": "", "KINGSTON_SSD_ROOT": ""}):
            # Simulate a non-existent Windows path when running on POSIX
            mock_cwd = Path("C:/MockNonDrive/subdir")
            with patch("pathlib.Path.cwd", return_value=mock_cwd):
                # Ensure it doesn't crash or falsely return host working directory
                detected = SmartDriveProxy.detect_mount_point()
                # On macOS host, if /Volumes/KINGSTON doesn't exist, should return None or Volumes
                if detected is not None:
                    # Must NOT be the repository directory
                    self.assertNotEqual(detected.resolve(), Path.cwd().resolve())

    def test_discover_with_latency_performance_under_100ms(self) -> None:
        """discover_with_latency completes well within the 100ms SLA."""
        with patch.dict(os.environ, {"SMART_DRIVE_ROOT": str(self.test_root)}):
            _, elapsed_ms = SmartDriveProxy.discover_with_latency()
            self.assertLess(elapsed_ms, 100.0, f"Discovery latency {elapsed_ms:.2f}ms exceeded 100ms SLA")


class TestJunctionCrossPlatformAdversarial(unittest.TestCase):
    """Adversarial stress-testing of junction.py on valid, broken, real dirs, and files."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sd_junc_adv_")
        self.workspace = Path(self.temp_dir).resolve()

    def tearDown(self) -> None:
        # Cleanly remove any symlinks/junctions first
        if self.workspace.exists():
            for root, dirs, _ in os.walk(str(self.workspace)):
                for d in list(dirs):
                    p = Path(root) / d
                    if is_directory_junction(p):
                        try:
                            remove_directory_junction(p)
                        except Exception:
                            pass
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_symlink_directory_detected_as_junction(self) -> None:
        """On POSIX systems, a symlink pointing to an existing directory is recognized as a junction."""
        target_dir = self.workspace / "real_target"
        target_dir.mkdir()
        (target_dir / "file.txt").write_text("data", encoding="utf-8")

        junc_path = self.workspace / "junc_link"
        success = create_directory_junction(junc_path, target_dir)
        self.assertTrue(success, "Directory junction/symlink creation should succeed")
        self.assertTrue(is_directory_junction(junc_path), "is_directory_junction should return True for valid link")

        target = get_junction_target(junc_path)
        self.assertIsNotNone(target)
        self.assertEqual(Path(target).resolve(), target_dir.resolve())

    def test_broken_junction_detection_and_safe_cleanup(self) -> None:
        """Broken directory junction (target deleted) must be detected as junction and removed safely."""
        target_dir = self.workspace / "transient_target"
        target_dir.mkdir()
        junc_path = self.workspace / "broken_junc_link"
        create_directory_junction(junc_path, target_dir)

        # Delete the target directory, making it a broken junction
        target_dir.rmdir()
        self.assertFalse(target_dir.exists())

        # CRITICAL TEST (verifying Worker M1 fix):
        # Even with target gone, the broken link must still be recognized as a junction
        self.assertTrue(
            is_directory_junction(junc_path),
            "Broken junction/symlink must be detected as a directory junction",
        )

        # Removing the broken junction must succeed cleanly
        res = remove_directory_junction(junc_path)
        self.assertTrue(res, "Removing broken junction must succeed")
        self.assertFalse(os.path.lexists(str(junc_path)), "Broken junction link must no longer exist")

    def test_real_directory_rejection_prevents_data_loss(self) -> None:
        """remove_directory_junction strictly refuses to remove a regular directory, preserving data."""
        real_dir = self.workspace / "important_data_dir"
        real_dir.mkdir()
        canary_file = real_dir / "valuable.txt"
        canary_file.write_text("Do not delete me!", encoding="utf-8")

        self.assertFalse(is_directory_junction(real_dir))

        with self.assertRaises(ValueError) as ctx:
            remove_directory_junction(real_dir)
        self.assertIn("Safety Violation", str(ctx.exception))

        # Invariant: data must remain 100% intact
        self.assertTrue(real_dir.is_dir())
        self.assertTrue(canary_file.is_file())
        self.assertEqual(canary_file.read_text(encoding="utf-8"), "Do not delete me!")

    def test_regular_file_and_symlink_to_file_rejected(self) -> None:
        """Regular files and symlinks to regular files must NOT be recognized as directory junctions."""
        regular_file = self.workspace / "regular_file.txt"
        regular_file.write_text("regular file", encoding="utf-8")

        self.assertFalse(is_directory_junction(regular_file))
        with self.assertRaises(ValueError):
            remove_directory_junction(regular_file)

        # Symlink to file (not a directory)
        file_link = self.workspace / "symlink_to_file.txt"
        try:
            os.symlink(str(regular_file), str(file_link))
            self.assertFalse(is_directory_junction(file_link), "Symlink to regular file is NOT a directory junction")
        except OSError:
            pass

    def test_nonexistent_and_malformed_paths(self) -> None:
        """Non-existent and malformed paths safely return False / None without unhandled crashes."""
        nonexistent = self.workspace / "does_not_exist_12345"
        self.assertFalse(is_directory_junction(nonexistent))
        self.assertFalse(remove_directory_junction(nonexistent))
        self.assertIsNone(get_junction_target(nonexistent))

        # Path with null byte
        null_path = str(self.workspace) + "\x00invalid"
        self.assertFalse(is_directory_junction(null_path))

    def test_junction_unlinking_preserves_target_files(self) -> None:
        """Removing a directory junction unlinks the junction and preserves 100% of target files."""
        target_dir = self.workspace / "persisted_target"
        target_dir.mkdir()
        files = {f"file_{i}.dat": f"content_{i}" * 100 for i in range(5)}
        for fname, fcontent in files.items():
            (target_dir / fname).write_text(fcontent, encoding="utf-8")

        junc_path = self.workspace / "active_junc"
        create_directory_junction(junc_path, target_dir)

        # Remove the junction
        removed = remove_directory_junction(junc_path)
        self.assertTrue(removed)
        self.assertFalse(os.path.lexists(str(junc_path)))

        # Target files must be completely intact
        self.assertTrue(target_dir.is_dir())
        for fname, expected_content in files.items():
            fpath = target_dir / fname
            self.assertTrue(fpath.is_file())
            self.assertEqual(fpath.read_text(encoding="utf-8"), expected_content)


class TestUIServerConcurrencyAndSocketBacklog(unittest.TestCase):
    """Adversarial stress-testing of ui/server.py under burst concurrency and edge inputs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.mkdtemp(prefix="sd_ui_adv_")
        cls.test_root = Path(cls.temp_dir).resolve()
        # Create minimal structure
        (cls.test_root / "01_AI_Models").mkdir(parents=True)
        (cls.test_root / "02_Learning_Knowledge").mkdir(parents=True)
        (cls.test_root / "GEMINI.md").write_text("# GEMINI", encoding="utf-8")
        (cls.test_root / "AGENTS.md").write_text("# AGENTS", encoding="utf-8")

        # Bind to port 0 for dynamic ephemeral port allocation
        cls.server = create_server(root_path=str(cls.test_root), port=0, host="127.0.0.1")
        cls.actual_port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.actual_port}"

        import threading

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _raw_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> tuple[int, dict, bytes]:
        req_headers = headers or {}
        url = urllib.parse.urljoin(self.base_url, endpoint)
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                body = resp.read()
                return status, resp_headers, body
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_burst_concurrent_socket_requests_backlog_resilience(self) -> None:
        """Burst of 80 concurrent worker threads making 160 requests must not drop connections."""
        endpoints = [
            "/",
            "/api/status",
            "/api/audit",
            "/api/search?q=test",
            "/api/junk",
        ]
        # Repeat to create 160 requests
        request_list = (endpoints * 32)[:160]

        def worker_task(ep: str) -> tuple[int, float]:
            t0 = time.perf_counter()
            status, _, _ = self._raw_request(ep, timeout=15.0)
            elapsed = time.perf_counter() - t0
            return status, elapsed

        results: list[tuple[int, float]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=80) as executor:
            future_to_ep = [executor.submit(worker_task, ep) for ep in request_list]
            for future in concurrent.futures.as_completed(future_to_ep):
                results.append(future.result())

        self.assertEqual(len(results), 160)
        # All requests must succeed with HTTP 200 without connection reset or drop
        for status, elapsed in results:
            self.assertEqual(status, 200, f"Request failed with status {status}")
            self.assertLess(elapsed, 15.0, f"Request latency too high: {elapsed}s")

        # Verify server is still healthy
        status, _, body = self._raw_request("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(data["status"], "ready")

    def test_malformed_http_payloads_and_boundaries(self) -> None:
        """Malformed payloads in POST and edge query parameters are safely handled."""
        # 1. Non-existent route -> 404
        status, _, body = self._raw_request("/api/nonexistent_route_12345")
        self.assertEqual(status, 404)
        err = json.loads(body.decode("utf-8"))
        self.assertIn("error", err)

        # 2. Unsupported method on endpoints -> 405
        status, _, body = self._raw_request("/api/status", method="POST", data=b"{}")
        self.assertEqual(status, 405)

        # 3. POST /api/junk/clean with empty body -> 400
        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            data=b"",
            headers={"Content-Length": "0"},
        )
        self.assertEqual(status, 400)

        # 4. POST /api/junk/clean with invalid JSON -> 400
        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            data=b"{not_json",
            headers={"Content-Length": str(len(b"{not_json"))},
        )
        self.assertEqual(status, 400)

        # 5. POST /api/junk/clean with empty tiers list -> 400
        bad_payload = json.dumps({"tiers": []}).encode("utf-8")
        status, _, body = self._raw_request(
            "/api/junk/clean",
            method="POST",
            data=bad_payload,
            headers={"Content-Length": str(len(bad_payload))},
        )
        self.assertEqual(status, 400)

        # 6. POST /api/junk/clean with non-dict JSON (e.g. list, string, number)
        for bad_data in [b"[\"tiers\"]", b"\"just_a_string\"", b"12345", b"true"]:
            with self.subTest(bad_data=bad_data):
                status, _, body = self._raw_request(
                    "/api/junk/clean",
                    method="POST",
                    data=bad_data,
                    headers={"Content-Length": str(len(bad_data))},
                )
                # Must be handled without crashing the server process
                self.assertIn(status, [400, 500])

    def test_security_restriction_host_binding(self) -> None:
        """create_server strictly enforces 127.0.0.1 loopback binding and prohibits external interfaces."""
        prohibited_hosts = ["0.0.0.0", "192.168.1.100", "10.0.0.1", "public.host.com"]
        for host in prohibited_hosts:
            with self.subTest(host=host):
                with self.assertRaises(ValueError) as ctx:
                    create_server(root_path=str(self.test_root), port=8765, host=host)
                self.assertIn("Security restriction", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
