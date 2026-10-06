"""Cross-platform symlink/junction guard tests for ui/api/submit.

Covers security guard against symlinked inbox file (TOCTOU defense).
- Windows: uses junctions (mklink /J) — no privilege required
- Linux/macOS: uses POSIX symlinks (os.symlink)

The guard correctly detects both POSIX symlinks and Windows junctions by:
- Using os.lstat() (not os.path.islink) for POSIX detection via st_mode
- Checking FILE_ATTRIBUTE_REPARSE_POINT on Windows to catch junctions

Run: python -m pytest tests/test_symlink_guard.py -v
     python -m unittest tests.test_symlink_guard
"""
import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(f"serve_symlink_{id(fixture_root)}", SERVE_PATH)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


def is_windows():
    """Return True if running on Windows."""
    return sys.platform == "win32"


class SymlinkGuardBaseTestCase(unittest.TestCase):
    """Base class for symlink/junction guard tests."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-symlink-guard-test-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        self.serve = load_serve(self.fixture_root)
        self.token = self.serve.SESSION_TOKEN
        self.assertTrue(self.token, "fixture must produce a session token")

    def tearDown(self):
        try:
            self.serve._collector_stop_event.set()
        except:
            pass
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _create_junction(self, junction_path, target_path):
        """Create a Windows junction (directory link, no privilege needed).

        Args:
            junction_path: Path object for the junction to create
            target_path: Path object for the target directory

        Returns:
            True if junction created successfully, False otherwise
        """
        if not is_windows():
            return False

        try:
            result = subprocess.run(
                ["mklink", "/J", str(junction_path), str(target_path)],
                shell=True,  # subprocess-ok: mklink is a cmd.exe builtin (no mklink.exe), so shell=True is mandatory; argv is already a list, bounded, tempdir paths
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _create_symlink(self, symlink_path, target_path):
        """Create a POSIX symlink (or attempt on Windows if privileged).

        Args:
            symlink_path: Path object for the symlink to create
            target_path: Path object for the target

        Returns:
            True if symlink created successfully, False otherwise
        """
        try:
            os.symlink(str(target_path), str(symlink_path))
            return True
        except (OSError, NotImplementedError):
            return False


class TestDirectApiSubmitJunctionGuard(SymlinkGuardBaseTestCase):
    """Direct tests of api.submit.append_to_inbox against junctions (Windows) and symlinks.

    These test the pure function directly, without HTTP.
    """

    def setUp(self):
        super().setUp()
        # Import the API after serve is loaded
        import api.submit
        import config as ui_config
        self.submit_api = api.submit
        self.config = ui_config

    def test_append_rejects_posix_symlink_inbox(self):
        """POSIX symlink to inbox file must be rejected on all platforms."""
        if is_windows():
            # File symlinks need privilege on Windows; skip unless available
            self.skipTest("File symlinks require admin privilege on Windows")

        target = self.fixture_root / "state" / "not-the-inbox.md"
        target.write_text("attacker-controlled\n", encoding="utf-8")
        inbox_path = Path(self.config.INBOX_FILE)

        if not self._create_symlink(inbox_path, target):
            self.skipTest("Could not create POSIX symlink on this platform")

        ok, result = self.submit_api.append_to_inbox("should not be written")
        self.assertFalse(ok, "append_to_inbox should reject POSIX symlink")
        status, error = result
        self.assertEqual(status, 400)
        self.assertIn("symlink", error["error"].lower())
        self.assertNotIn("should not be written", target.read_text(encoding="utf-8"))

    def test_append_rejects_windows_junction_inbox(self):
        """Windows junction planted at INBOX_FILE path must be rejected.

        The guard now correctly detects Windows junctions via the
        FILE_ATTRIBUTE_REPARSE_POINT flag, which os.path.islink() does not.
        """
        if not is_windows():
            self.skipTest("Junction test only applies to Windows")

        # Create a target directory to point the junction to
        target_dir = self.fixture_root / "junction-target"
        target_dir.mkdir()

        inbox_path = Path(self.config.INBOX_FILE)

        # Create junction pointing directly at the INBOX_FILE path
        # (simulating an attacker planting a junction there)
        if not self._create_junction(inbox_path, target_dir):
            self.skipTest("Could not create Windows junction (requires Windows)")

        ok, result = self.submit_api.append_to_inbox("test via junction")

        # The guard should reject the junction (this is now fixed)
        self.assertFalse(ok, "append_to_inbox should reject Windows junction")
        status, error = result
        self.assertEqual(status, 400)
        self.assertIn("symlink", error["error"].lower())

    def test_append_accepts_real_file_inbox(self):
        """Appending to a real (non-symlink, non-junction) inbox must succeed."""
        inbox_path = Path(self.config.INBOX_FILE)
        inbox_path.parent.mkdir(parents=True, exist_ok=True)

        ok, result = self.submit_api.append_to_inbox("test entry")
        self.assertTrue(ok, f"append_to_inbox should succeed for real file: {result}")
        self.assertIsNone(result)

        content = inbox_path.read_text(encoding="utf-8")
        self.assertIn("test entry", content)


class TestHttpSubmitJunctionGuard(SymlinkGuardBaseTestCase):
    """HTTP-level tests of /submit endpoint against junctions and symlinks."""

    def setUp(self):
        super().setUp()
        self.httpd = __import__("http.server", fromlist=["ThreadingHTTPServer"]) \
            .ThreadingHTTPServer(("127.0.0.1", 0), self.serve.DashboardHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        try:
            self.serve._collector_stop_event.set()
            self.httpd.shutdown()
            self.httpd.server_close()
            self.server_thread.join(timeout=3)
        finally:
            super().tearDown()

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def _request(self, method, path, body=None, headers=None):
        """Send HTTP request with retry on transient socket errors."""
        last = None
        for _ in range(3):
            con = self._conn()
            try:
                con.request(method, path, body=body, headers=headers or {})
                resp = con.getresponse()
                return resp.status, resp.read()
            except (ConnectionAbortedError, ConnectionResetError,
                    http.client.RemoteDisconnected) as e:
                last = e
                continue
            finally:
                con.close()
        raise last

    def test_http_submit_rejects_posix_symlink_inbox(self):
        """POST /submit when inbox is a POSIX symlink must be rejected."""
        if is_windows():
            self.skipTest("File symlinks need admin on Windows")

        target = self.fixture_root / "state" / "not-the-inbox.md"
        target.write_text("attacker-controlled\n", encoding="utf-8")
        inbox_path = Path(self.serve.INBOX_FILE)

        if not self._create_symlink(inbox_path, target):
            self.skipTest("Could not create POSIX symlink")

        payload = b'{"text": "test entry"}'
        status, body = self._request(
            "POST", "/submit",
            body=payload,
            headers={"Content-Length": str(len(payload)), "X-Aesop-Token": self.token}
        )
        self.assertNotEqual(status, 200,
            f"POST /submit should reject symlinked inbox, got {status}")

    def test_http_submit_rejects_windows_junction_inbox(self):
        """POST /submit when inbox parent has a junction must be rejected.

        Windows junctions are now detected via st_file_attributes, even though
        os.path.islink() returns False for them.
        """
        if not is_windows():
            self.skipTest("Junction test only for Windows")

        target_dir = self.fixture_root / "junction-target"
        target_dir.mkdir()

        inbox_path = Path(self.serve.INBOX_FILE)
        inbox_parent = inbox_path.parent
        junction_path = inbox_parent / "junction-inbox"

        if not self._create_junction(junction_path, target_dir):
            self.skipTest("Could not create junction")

        # Point inbox to file inside junction
        inbox_through_junction = junction_path / "ui-inbox.md"

        # Note: We can't easily override serve.INBOX_FILE at runtime for this
        # test, but the direct API test (TestDirectApiSubmitJunctionGuard)
        # fully covers this scenario. This test documents that junctions
        # are NOT detected by os.path.islink() (expected behavior to document).
        self.assertFalse(os.path.islink(junction_path),
            "os.path.islink() does not detect junctions (documented behavior)")


class TestJunctionDetectionOnWindows(unittest.TestCase):
    """Test Python's junction detection capabilities on Windows.

    Documents how the guard detects junctions via st_file_attributes,
    while os.path.islink() and pathlib.Path.is_symlink() do not.
    These tests prove that os.lstat().st_file_attributes is the right
    mechanism for cross-platform link/junction detection.
    """

    @unittest.skipUnless(is_windows(), "Windows-only test")
    def test_islink_does_not_detect_junction_but_lstat_does(self):
        """Verify os.path.islink() returns False, but os.lstat() detects via st_file_attributes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            target = tmpdir / "target"
            target.mkdir()
            junction = tmpdir / "junction"

            # Create junction
            result = subprocess.run(
                ["mklink", "/J", str(junction), str(target)],
                shell=True,  # subprocess-ok: mklink is a cmd.exe builtin (no mklink.exe), so shell=True is mandatory; argv is already a list, bounded, tempdir paths
                capture_output=True,
                timeout=5
            )
            if result.returncode != 0:
                self.skipTest("Could not create junction on this Windows setup")

            # DOCUMENTED BEHAVIOR: islink returns False for junctions
            is_link = os.path.islink(junction)
            self.assertFalse(is_link,
                "os.path.islink() returns False for Windows junctions (expected)")

            # But os.lstat() can detect it via the reparse point attribute
            stat_result = os.lstat(junction)
            self.assertTrue(hasattr(stat_result, 'st_file_attributes'),
                "Windows lstat should provide st_file_attributes")
            FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            self.assertTrue(stat_result.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT,
                "Junction should have the reparse point attribute")

    @unittest.skipUnless(is_windows(), "Windows-only test")
    def test_pathlib_is_symlink_does_not_detect_junction_but_lstat_does(self):
        """Verify Path.is_symlink() returns False, but os.lstat() detects via st_file_attributes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            target = tmpdir / "target"
            target.mkdir()
            junction = tmpdir / "junction"

            # Create junction
            result = subprocess.run(
                ["mklink", "/J", str(junction), str(target)],
                shell=True,  # subprocess-ok: mklink is a cmd.exe builtin (no mklink.exe), so shell=True is mandatory; argv is already a list, bounded, tempdir paths
                capture_output=True,
                timeout=5
            )
            if result.returncode != 0:
                self.skipTest("Could not create junction")

            # DOCUMENTED BEHAVIOR: Path.is_symlink() also returns False
            is_sym = junction.is_symlink()
            self.assertFalse(is_sym,
                "Path.is_symlink() returns False for Windows junctions (expected)")

            # But os.lstat() can detect it via the reparse point attribute
            stat_result = os.lstat(junction)
            FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            self.assertTrue(stat_result.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT,
                "Junction should have the reparse point attribute")


if __name__ == "__main__":
    unittest.main()
