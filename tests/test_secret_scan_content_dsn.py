#!/usr/bin/env python3
"""
Behavioural tests for scan_content (the history / prepublish path) and
connection-string placeholder routing.

Defect (tracker item scan-content-dsn, found by lane scan-placeholder refuting
b0937071216e): scan_content applied is_placeholder() to the FULL connection-string
match, so a real DSN whose path ends in `/test` (the DB name) was treated as a
placeholder and passed. scan_file / scan_blob route connection_string matches
through _connection_password_is_placeholder, which checks only the password.

Each test drives the real scanner. Credential-shaped values are assembled at
runtime so no scanner pattern appears contiguously in this source file.

Run: python -m unittest tests.test_secret_scan_content_dsn
"""
import secrets
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

from secret_scan import scan_content, scan_blob  # noqa: E402


def _j(*parts):
    """Join fragments at runtime (self-scan invariant, see module docstring)."""
    return "".join(parts)


def _random_password():
    """A fresh random credential per run: letters and digits, never a placeholder."""
    return "k" + secrets.token_hex(12) + "Q"


def _connection_rules(findings):
    return {rule for (_ln, rule, _m, _f) in findings if rule == "connection_string"}


class TestScanContentConnectionStringDsn(unittest.TestCase):
    """scan_content must judge a DSN by its password, not its DB name or path."""

    def test_real_password_dsn_ending_in_test_is_flagged(self):
        pw = _random_password()
        dsn = _j("postgresql://app:", pw, "@db.prod-acme.io:5432/test")
        findings = scan_content(dsn + "\n")
        self.assertGreaterEqual(
            len(_connection_rules(findings)), 1,
            "real DSN with a runtime password and /test path must be a finding",
        )

    def test_placeholder_password_dsn_is_not_flagged(self):
        # Only placeholders is_placeholder() defines (PLACEHOLDERS + template syntax).
        # 'password' is deliberately absent: the scanner does not treat it as one.
        for placeholder in ("changeme", "xxx", "${PW}"):
            with self.subTest(password=placeholder):
                dsn = _j("postgresql://app:", placeholder, "@db.prod-acme.io:5432/test")
                findings = scan_content(dsn + "\n")
                self.assertEqual(
                    _connection_rules(findings), set(),
                    f"placeholder password {placeholder!r} must not be a finding",
                )

    def test_scan_content_agrees_with_scan_blob_on_real_dsn(self):
        pw = _random_password()
        dsn = _j("postgresql://app:", pw, "@db.prod-acme.io:5432/test\n")
        via_content = _connection_rules(scan_content(dsn))
        via_blob = _connection_rules(scan_blob("fixture.txt", dsn.encode("utf-8")))
        self.assertTrue(via_blob, "precondition: scan_blob flags this DSN")
        self.assertEqual(bool(via_content), bool(via_blob))


if __name__ == "__main__":
    unittest.main()
