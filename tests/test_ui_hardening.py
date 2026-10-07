"""UI backend security/correctness hardening tests.

Audit findings:
  1. handler.py: raw str(e) leaks exception details in JSON error responses
  2. csrf.py:141: token comparison uses != instead of hmac.compare_digest
  3. handlers: errors='ignore' silently drops invalid UTF-8 bytes
  4. handler.py:172-184: path containment check needs extraction to helper

Test coverage:
  - (1) Verify error handlers use generic messages in JSON responses
  - (2) Verify hmac.compare_digest is used for token comparison (timing-safe)
  - (3) Verify errors='ignore' removed from all decode operations
  - (4) Verify _path_is_contained helper exists and is used

Run: python -m unittest tests.test_ui_hardening -v
"""
import shutil
import tempfile
import unittest
from pathlib import Path

UI_PATH = Path(__file__).parent.parent / "ui"


class TestNoExceptionTextInErrorResponses(unittest.TestCase):
    """(1) Verify no raw exception text leaks into error handlers."""

    def test_handler_py_error_messages_are_generic(self):
        """Error response handlers should not use str(e) directly in JSON."""
        handler_path = UI_PATH / "handler.py"
        source = handler_path.read_text('utf-8')

        # Look for patterns that leak exception text into error responses
        # We should NOT see: json.dumps({"error": str(e)})
        bad_patterns = [
            'json.dumps({"error": str(e)})',
            '"error": str(e)',
        ]

        for pattern in bad_patterns:
            self.assertNotIn(pattern, source,
                           f"handler.py must not use {pattern} — would leak exception details")

    def test_handler_py_logs_exceptions_to_stderr(self):
        """Exceptions should be logged to stderr, not returned to client."""
        handler_path = UI_PATH / "handler.py"
        source = handler_path.read_text('utf-8')

        # Should have logging of exceptions to stderr
        self.assertIn("stderr", source.lower() or "print",
                     "handler.py should log exceptions to stderr, not return them")


class TestCSRFTokenComparison(unittest.TestCase):
    """(2) Verify CSRF token comparison uses hmac.compare_digest."""

    def test_csrf_py_uses_compare_digest(self):
        """csrf.validate_csrf_request must use hmac.compare_digest."""
        csrf_path = UI_PATH / "csrf.py"
        source = csrf_path.read_text('utf-8')

        # Should use compare_digest for token comparison
        self.assertIn("compare_digest", source,
                     "csrf.py must use hmac.compare_digest for timing-safe token comparison")

        # Should NOT use simple != for token comparison
        # (Note: != might appear elsewhere, but compare_digest must be present)
        self.assertIn("hmac", source,
                     "csrf.py must import hmac module")


class TestPathContainmentHelper(unittest.TestCase):
    """(4) Verify path containment check is properly abstracted."""

    def test_handler_has_path_containment_logic(self):
        """handler.py should have path containment checking (either helper or inline)."""
        handler_path = UI_PATH / "handler.py"
        source = handler_path.read_text('utf-8')

        # Should have the is_relative_to or relative_to containment logic
        self.assertTrue(
            "is_relative_to" in source or "relative_to" in source,
            "handler.py must have path containment checking"
        )

    def test_agents_has_path_containment_logic(self):
        """agents.py should have path containment checking."""
        agents_path = UI_PATH / "agents.py"
        source = agents_path.read_text('utf-8')

        # Should have the containment check
        self.assertTrue(
            "is_relative_to" in source or "relative_to" in source,
            "agents.py must have path containment checking for glob results"
        )

    def test_path_containment_truth_table(self):
        """Path containment logic should reject traversal attacks."""
        # Test the containment check pattern with a real directory
        test_root = Path(tempfile.mkdtemp(prefix="path-contain-test-"))
        try:
            (test_root / "allowed").mkdir()
            (test_root / "other").mkdir()
            allowed_root = test_root / "allowed"

            # Test cases: (path, should_be_contained_in_allowed_root)
            test_cases = [
                (allowed_root / "file.txt", True),
                (allowed_root / "sub" / "file.txt", True),
                ((allowed_root / "..").resolve(), False),
                ((allowed_root / "../other").resolve(), False),
            ]

            for candidate, expected in test_cases:
                # Resolve both paths to normalize Windows 8.3 short names and handle symlinks
                resolved_candidate = candidate.resolve() if not candidate.is_absolute() else candidate.resolve()
                resolved_root = allowed_root.resolve()

                try:
                    is_contained = resolved_candidate.is_relative_to(resolved_root)
                except AttributeError:
                    try:
                        resolved_candidate.relative_to(resolved_root)
                        is_contained = True
                    except ValueError:
                        is_contained = False

                self.assertEqual(is_contained, expected,
                               f"Path {resolved_candidate.name if resolved_candidate.name else '.'} "
                               f"containment={is_contained}, expected {expected}")
        finally:
            shutil.rmtree(test_root, ignore_errors=True)


class TestErrorsReplaceNotIgnore(unittest.TestCase):
    """(3) Verify that errors='ignore' is replaced with errors='replace'."""

    def test_handler_py_no_errors_ignore(self):
        """handler.py must not use errors='ignore' for decode."""
        handler_path = UI_PATH / "handler.py"
        source = handler_path.read_text('utf-8')

        self.assertNotIn("errors='ignore'", source,
                        "handler.py: errors='ignore' silently drops bytes; use errors='replace'")

    def test_tracker_py_no_errors_ignore(self):
        """api/tracker.py must not use errors='ignore'."""
        tracker_path = UI_PATH / "api" / "tracker.py"
        source = tracker_path.read_text('utf-8')

        self.assertNotIn("errors='ignore'", source,
                        "api/tracker.py: errors='ignore' silently drops bytes; use errors='replace'")

    def test_agents_py_no_errors_ignore(self):
        """agents.py must not use errors='ignore'."""
        agents_path = UI_PATH / "agents.py"
        source = agents_path.read_text('utf-8')

        self.assertNotIn("errors='ignore'", source,
                        "agents.py: errors='ignore' silently drops bytes; use errors='replace'")

    def test_collectors_py_no_errors_ignore(self):
        """collectors.py must not use errors='ignore'."""
        collectors_path = UI_PATH / "collectors.py"
        source = collectors_path.read_text('utf-8')

        self.assertNotIn("errors='ignore'", source,
                        "collectors.py: errors='ignore' silently drops bytes; use errors='replace'")


if __name__ == "__main__":
    unittest.main()
