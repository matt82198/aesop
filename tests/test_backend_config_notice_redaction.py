"""validate_api_key_env's NOTICE must never echo the configured env var name (CodeQL clear-text logging)."""
import os
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "driver"))

from backend_config import validate_api_key_env  # noqa: E402


class NoticeRedactionTest(unittest.TestCase):
    def test_notice_does_not_echo_name(self):
        name = "ZZUNIQ_CANARY_API_KEY"
        buf = StringIO()
        with redirect_stderr(buf):
            validate_api_key_env(name, where="seats.worker")
        out = buf.getvalue()
        self.assertIn("NOTICE", out)
        self.assertIn("seats.worker", out)
        self.assertNotIn("ZZUNIQ", out)
        self.assertNotIn(name, out)


if __name__ == "__main__":
    unittest.main()
