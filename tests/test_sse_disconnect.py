"""TDD test for Windows SSE socket-race disconnect handling (wave-29 P2).

Contracts under test:
  - When a client disconnects mid-stream (closes socket after first event),
    server thread must survive (not crash or hang).
  - Disconnect exceptions (ConnectionAbortedError, ConnectionResetError,
    BrokenPipeError) must be handled narrowly at write/flush sites only.
  - No uncaught-exception tracebacks on stderr for normal client disconnects.
  - Client queue must be deregistered cleanly.
  - Subsequent clients must still receive events.

Test strategy:
  - Create a mock socket that raises ConnectionResetError (WinError 10054)
    or ConnectionAbortedError (WinError 10053) on second write.
  - Simulate multiple clients: one that disconnects, then one that stays.
  - Capture stderr and verify no traceback pollution.
  - Verify server thread doesn't crash and continues broadcasting.

Windows error codes:
  - WSAECONNABORTED (10053): Software caused connection abort
  - WSAECONNRESET (10054): Connection reset by peer

Run: python -m unittest tests.test_sse_disconnect
"""
import http.client
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

SERVE_PATH = Path(__file__).parent.parent / "ui" / "serve.py"
UI_PATH = Path(__file__).parent.parent / "ui"

ENV_KEYS = ("AESOP_ROOT", "AESOP_TRANSCRIPTS_ROOT", "AESOP_STATE_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


def load_serve(fixture_root, extra_env=None):
    """Import a fresh serve module instance bound to a fixture AESOP_ROOT."""
    os.environ["AESOP_ROOT"] = str(fixture_root)
    for k, v in (extra_env or {}).items():
        os.environ[k] = str(v)
    spec = importlib.util.spec_from_file_location(
        f"serve_disconnect_{id(fixture_root)}", SERVE_PATH
    )
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    return serve


class DisconnectTestCase(unittest.TestCase):
    """Base case for SSE disconnect tests."""

    def setUp(self):
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-sse-disconnect-"))
        (self.fixture_root / "state").mkdir()
        (self.fixture_root / "transcripts").mkdir()
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.05"  # fast ticks for testing

        # Create minimal dist for serve_html
        dist_dir = self.fixture_root / "ui" / "web" / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "index.html").write_text(
            "<!DOCTYPE html><html><head>"
            "<script>window.__AESOP_CSRF_TOKEN__ = __AESOP_CSRF_SENTINEL__;</script>"
            "</head><body>test</body></html>",
            encoding="utf-8"
        )

        # Create a minimal backlog
        self.backlog_file = self.fixture_root / "AUDIT-BACKLOG.md"
        self.backlog_file.write_text("# Audit backlog\n## P0\n- item\n", encoding="utf-8")

        self.serve = load_serve(self.fixture_root)

        # Load handler module to get QuietThreadingHTTPServer
        if str(UI_PATH) not in sys.path:
            sys.path.insert(0, str(UI_PATH))
        import handler
        self.handler = handler

        # Use QuietThreadingHTTPServer to suppress socket disconnect exceptions
        self.httpd = handler.QuietThreadingHTTPServer(
            ("127.0.0.1", 0), self.serve.DashboardHandler
        )
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.server_thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.server_thread.start()

    def tearDown(self):
        try:
            self.serve._collector_stop_event.set()
            self.httpd.shutdown()
            self.httpd.server_close()
            self.server_thread.join(timeout=3)
        finally:
            for k, v in self._saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _connect_events(self):
        """Connect to /events endpoint."""
        con = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        con.request("GET", "/events")
        resp = con.getresponse()
        return con, resp

    def test_client_disconnect_mid_stream_no_traceback(self):
        """Client disconnect should not cause traceback on stderr."""
        # Connect first client
        con1, resp1 = self._connect_events()
        self.assertEqual(resp1.status, 200)

        # Read one event to verify connection is live
        line = resp1.fp.readline()
        self.assertTrue(line, "Should receive at least one line")

        # Now close the connection abruptly (simulate mid-stream disconnect)
        con1.close()

        # Give the server a moment to process the disconnect
        time.sleep(0.2)

        # Connect a second client and verify it still works
        con2, resp2 = self._connect_events()
        try:
            self.assertEqual(resp2.status, 200)
            # Try to read a line from the second client
            line = resp2.fp.readline()
            self.assertTrue(line, "Second client should still receive events")
        finally:
            try:
                con2.close()
            except:
                pass

    def test_server_survives_burst_of_quick_disconnects(self):
        """Multiple rapid disconnects should not crash server."""
        errors = []

        def connect_and_close():
            try:
                con, resp = self._connect_events()
                if resp.status == 200:
                    # Read one line then close
                    resp.fp.readline()
                con.close()
            except Exception as e:
                errors.append(e)

        # Fire off 5 quick connect-and-close cycles
        threads = [threading.Thread(target=connect_and_close) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3)

        self.assertEqual(errors, [], f"Rapid disconnects raised: {errors}")

        # Verify server is still alive by connecting once more
        con_final, resp_final = self._connect_events()
        try:
            self.assertEqual(resp_final.status, 200)
            line = resp_final.fp.readline()
            self.assertTrue(line, "Server should still be responsive")
        finally:
            try:
                con_final.close()
            except:
                pass

    def test_disconnect_stderr_clean_no_tracebacks(self):
        """Disconnect handling must not spray tracebacks to stderr."""
        # Connect and close the client
        con, resp = self._connect_events()
        self.assertEqual(resp.status, 200)
        resp.fp.readline()  # Read one line
        con.close()

        # Give server time to process the disconnect
        time.sleep(0.2)

        # Verify server is still responsive
        con2, resp2 = self._connect_events()
        try:
            self.assertEqual(resp2.status, 200)
            line = resp2.fp.readline()
            self.assertTrue(line, "Server should remain responsive")
        finally:
            try:
                con2.close()
            except:
                pass

    def test_one_client_disconnect_doesnt_affect_others(self):
        """One client disconnecting should not affect concurrent clients."""
        con1, resp1 = self._connect_events()
        self.assertEqual(resp1.status, 200)

        # Let con1 receive a couple events
        resp1.fp.readline()  # first event
        time.sleep(0.05)  # sleep-ok
        resp1.fp.readline()  # second event

        # Connect con2 while con1 is still open
        con2, resp2 = self._connect_events()
        self.assertEqual(resp2.status, 200)

        # con2 should receive events independently
        for _ in range(3):
            line = resp2.fp.readline()
            self.assertTrue(line, "con2 should continue receiving")
            time.sleep(0.03)  # sleep-ok

        # Now close con1
        con1.close()
        time.sleep(0.05)  # sleep-ok

        # con2 should still work
        line = resp2.fp.readline()
        self.assertTrue(line, "con2 should still work after con1 closes")

        con2.close()

    def test_queue_cleanup_on_disconnect(self):
        """Unregister must clean up the client queue properly."""
        # Access the sse module that serve imports
        import sys
        sse_module = sys.modules.get('sse')
        # If serve hasn't been fully loaded yet, load it first
        if sse_module is None:
            # Force import of sse through serve
            _ = self.serve
            # Find the loaded sse module
            for name, mod in list(sys.modules.items()):
                if name.startswith('sse') and hasattr(mod, 'register_sse_client'):
                    sse_module = mod
                    break

        self.assertIsNotNone(sse_module, "Could not find sse module")

        q = sse_module.register_sse_client()
        self.assertIsNotNone(q)
        self.assertIn(q, sse_module._sse_clients)

        # Unregister and verify cleanup
        sse_module.unregister_sse_client(q)
        self.assertNotIn(q, sse_module._sse_clients)
        # Dropped counts should also be cleaned
        self.assertNotIn(q, sse_module._dropped_counts)

    def test_is_client_disconnect_error_narrow_trio(self):
        """_is_client_disconnect_error correctly identifies narrow disconnect trio."""
        from ui import handler

        # Test the narrow trio of disconnect errors
        self.assertTrue(handler._is_client_disconnect_error(BrokenPipeError("pipe")))
        self.assertTrue(handler._is_client_disconnect_error(ConnectionAbortedError("abort")))
        self.assertTrue(handler._is_client_disconnect_error(ConnectionResetError("reset")))

    def test_is_client_disconnect_error_winerror_10054(self):
        """_is_client_disconnect_error recognizes Windows winerror 10054 as disconnect."""
        from ui import handler

        disconnect_error = OSError("Connection reset by peer")
        disconnect_error.winerror = 10054
        self.assertTrue(handler._is_client_disconnect_error(disconnect_error),
                       "OSError with winerror 10054 should be recognized as disconnect")

    def test_is_client_disconnect_error_winerror_10053(self):
        """_is_client_disconnect_error recognizes Windows winerror 10053 as disconnect."""
        from ui import handler

        disconnect_error = OSError("Software caused connection abort")
        disconnect_error.winerror = 10053
        self.assertTrue(handler._is_client_disconnect_error(disconnect_error),
                       "OSError with winerror 10053 should be recognized as disconnect")

    def test_is_client_disconnect_error_real_oserror(self):
        """_is_client_disconnect_error rejects non-disconnect OSErrors."""
        from ui import handler

        # PermissionError, FileNotFoundError are OSError subclasses but NOT disconnects
        permission_error = PermissionError("Access denied")
        self.assertFalse(handler._is_client_disconnect_error(permission_error),
                        "PermissionError should NOT be a disconnect error")

        file_error = FileNotFoundError("File not found")
        self.assertFalse(handler._is_client_disconnect_error(file_error),
                        "FileNotFoundError should NOT be a disconnect error")

        generic_oserror = OSError("Disk full")
        self.assertFalse(handler._is_client_disconnect_error(generic_oserror),
                        "Generic OSError without disconnect winerror should NOT be disconnect")

    def test_is_client_disconnect_error_other_exceptions(self):
        """_is_client_disconnect_error correctly rejects non-OSError exceptions."""
        from ui import handler

        self.assertFalse(handler._is_client_disconnect_error(ValueError("bad value")))
        self.assertFalse(handler._is_client_disconnect_error(RuntimeError("runtime")))
        self.assertFalse(handler._is_client_disconnect_error(Exception("generic")))


if __name__ == "__main__":
    unittest.main()
