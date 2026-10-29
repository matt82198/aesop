#!/usr/bin/env python3
"""Test suite for healthcheck.py UTF-8 encoding fix.

Proves that healthcheck.py can run on Windows cp1252 consoles without
crashing on emoji characters.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path


class TestHealthcheckUTF8Encoding(unittest.TestCase):
    """Test healthcheck.py encoding resilience."""

    def test_healthcheck_runs_on_cp1252_console(self):
        """healthcheck.py should not crash with 'charmap' error on cp1252 console.

        Reproduces finding f-6905850f: the health check command was printing
        a green-circle emoji to stdout, which crashed when stdout was
        cp1252-encoded (common on Windows consoles).

        The fix configures sys.stdout to use UTF-8 with errors='replace' at
        the entry point, allowing emoji to be either displayed or replaced
        gracefully rather than crashing.
        """
        healthcheck_script = Path(__file__).parent.parent / "tools" / "healthcheck.py"

        # Run with cp1252 console encoding (simulates Windows console)
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'cp1252'  # Strict cp1252, no fallback

        result = subprocess.run(
            [sys.executable, str(healthcheck_script)],
            capture_output=True,
            text=False,  # Get raw bytes
            env=env,
        )

        stderr_text = result.stderr.decode('utf-8', errors='replace') if isinstance(result.stderr, bytes) else result.stderr

        # Should exit 0 (or 1 with expected error, not encoding error)
        # The key is that "charmap" should NOT appear in stderr
        self.assertNotIn(
            "charmap",
            stderr_text,
            msg="healthcheck crashed with charmap encoding error:\n" + stderr_text
        )

        # Verify stdout contains some health information
        stdout_text = result.stdout.decode('utf-8', errors='replace') if isinstance(result.stdout, bytes) else result.stdout
        self.assertIn(
            "HEALTH:",
            stdout_text,
            msg="healthcheck should print HEALTH: line"
        )


if __name__ == "__main__":
    unittest.main()
