"""Dashboard CSRF origin must be pinned to the bound port; token-write failure must warn.

Contract under test (S1 spec, red-first):
  (a) The Origin allowlist is bound to the server's actual port. Both
      ui/handler.py::_is_local_origin and ui/csrf.py::validate_csrf_request take a
      keyword argument ``expected_port`` (the port the server is bound to, e.g. 8770).
      Accepted: http://127.0.0.1:<port>, http://localhost:<port>, http://[::1]:<port>.
      Rejected: any other port (http://localhost:3000), a host suffix trick
      (http://localhost:8770.evil.com), and a bare host with no port.
  (b) When the session token file cannot be written, _generate_session_token()
      emits a warning to stderr AND still returns the in-memory token (no fail-open
      silence, no fail-closed crash).

Run: python -m unittest tests.test_dash_origin_port
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

UI_PATH = Path(__file__).resolve().parent.parent / "ui"
if str(UI_PATH) not in sys.path:
    sys.path.insert(0, str(UI_PATH))

BOUND_PORT = 8770
FIXED_TOKEN = "t" * 43
REJECT_REASON = "Foreign Origin/Referer rejected"

ACCEPTED_ORIGINS = [
    f"http://127.0.0.1:{BOUND_PORT}",
    f"http://localhost:{BOUND_PORT}",
    f"http://[::1]:{BOUND_PORT}",
]
REJECTED_ORIGINS = [
    "http://localhost:3000",                      # other local port
    f"http://localhost:{BOUND_PORT}.evil.com",    # host-suffix trick
    "http://localhost",                           # bare host, no port
]


def _handler_is_local(origin):
    import handler  # ui/handler.py (test_tracker_csrf imports it the same way)
    return handler._is_local_origin(origin, expected_port=BOUND_PORT)


def _csrf_validate(origin):
    import csrf
    headers = {"Origin": origin, "X-Aesop-Token": FIXED_TOKEN}
    with mock.patch.object(csrf, "SESSION_TOKEN", FIXED_TOKEN):
        return csrf.validate_csrf_request(headers, expected_port=BOUND_PORT)


class BoundPortOriginTests(unittest.TestCase):
    """(a) Origin allowlist pinned to the bound port."""

    def test_handler_accepts_loopback_origins_on_bound_port(self):
        for origin in ACCEPTED_ORIGINS:
            with self.subTest(origin=origin):
                self.assertTrue(_handler_is_local(origin),
                                f"{origin} must be accepted when bound to {BOUND_PORT}")

    def test_handler_rejects_origins_not_on_bound_port(self):
        for origin in REJECTED_ORIGINS:
            with self.subTest(origin=origin):
                self.assertFalse(_handler_is_local(origin),
                                 f"{origin} must be rejected when bound to {BOUND_PORT}")

    def test_csrf_accepts_loopback_origins_on_bound_port(self):
        for origin in ACCEPTED_ORIGINS:
            with self.subTest(origin=origin):
                ok, reason = _csrf_validate(origin)
                self.assertTrue(ok, f"{origin} must pass CSRF on {BOUND_PORT}, got reason={reason!r}")

    def test_csrf_rejects_origins_not_on_bound_port(self):
        for origin in REJECTED_ORIGINS:
            with self.subTest(origin=origin):
                ok, reason = _csrf_validate(origin)
                self.assertFalse(ok, f"{origin} must fail CSRF on {BOUND_PORT}")
                self.assertEqual(reason, REJECT_REASON)


class TokenWriteFailureWarningTests(unittest.TestCase):
    """(b) Unwritable token directory warns on stderr and still returns the token."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="dash-origin-"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_unwritable_token_dir_warns_and_returns_token(self):
        import csrf
        state_dir = self._tmp / "state"
        token_file = state_dir / ".ui-session-token"
        err = io.StringIO()
        with mock.patch.object(csrf.config, "STATE_DIR", state_dir), \
             mock.patch.object(csrf.config, "UI_SESSION_TOKEN_FILE", token_file), \
             mock.patch.object(csrf.os, "open", side_effect=OSError(13, "Permission denied")), \
             contextlib.redirect_stderr(err):
            token = csrf._generate_session_token()

        self.assertIsInstance(token, str)
        self.assertGreaterEqual(len(token), 32, "in-memory token must still be returned")
        stderr_text = err.getvalue().lower()
        self.assertTrue(stderr_text.strip(),
                        "token-file write failure must emit a warning to stderr (currently silent)")
        self.assertIn("csrf", stderr_text)
        self.assertIn("token", stderr_text)


DEV_VAR = "AESOP_DASH_DEV_ORIGINS"
VITE_ORIGIN = "http://localhost:5173"


def _env_without_dev_var():
    return {k: v for k, v in os.environ.items() if k != DEV_VAR}


class DevOriginOptInTests(unittest.TestCase):
    """(c) Vite dev origins are refused by default and admitted only by explicit opt-in."""

    def test_vite_origin_refused_without_opt_in(self):
        import csrf
        with mock.patch.dict(os.environ, _env_without_dev_var(), clear=True):
            self.assertFalse(csrf.is_local_origin(VITE_ORIGIN, BOUND_PORT))

    def test_vite_origin_accepted_with_opt_in(self):
        import csrf
        with mock.patch.dict(os.environ, {DEV_VAR: VITE_ORIGIN}):
            self.assertTrue(csrf.is_local_origin(VITE_ORIGIN, BOUND_PORT))

    def test_non_loopback_dev_origin_is_refused_with_warning(self):
        import csrf
        err = io.StringIO()
        with mock.patch.dict(os.environ, {DEV_VAR: "http://evil.example:5173"}), \
             contextlib.redirect_stderr(err):
            self.assertFalse(csrf.is_local_origin("http://evil.example:5173", BOUND_PORT))
        self.assertIn(DEV_VAR, err.getvalue())

    def test_loopback_dev_origin_without_explicit_port_is_ignored_with_warning(self):
        import csrf
        err = io.StringIO()
        with mock.patch.dict(os.environ, {DEV_VAR: "http://localhost"}), \
             contextlib.redirect_stderr(err):
            self.assertFalse(csrf.is_local_origin(VITE_ORIGIN, BOUND_PORT))
        self.assertIn(DEV_VAR, err.getvalue())


if __name__ == "__main__":
    unittest.main()
