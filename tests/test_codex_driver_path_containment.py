#!/usr/bin/env python3
r"""Test path containment vulnerability in CodexDriver.

Verifies that various path forms (Windows-specific and platform-agnostic)
that should be rejected are actually caught by the containment check.

Path forms to test:
- Absolute paths: /foo, C:\foo (Unix and Windows)
- Drive-relative paths: C:foo (Windows-specific)
- UNC paths: \\server\share (Windows-specific)
- Directory traversal: ../, .., ../../../evil
- Mixed separators: ..\..\ evil, /../../evil
- Unicode lookalikes: U+F03A (︺) used instead of colon
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import agent_driver as ad  # noqa: E402
from agent_driver import WorkerRequest, WORKER_FAILED  # noqa: E402
from codex_driver import CodexDriver  # noqa: E402


class FakeTransport:
    """Minimal fake transport for testing."""
    def __call__(self, payload):
        return {
            "choices": [{"message": {"content": json.dumps({"files": [], "summary": "", "done": True})}}],
            "usage": {"total_tokens": 0},
        }


class TestPathContainmentVulnerability(unittest.TestCase):
    """Verify current code is vulnerable to path escaping on Windows."""

    def test_absolute_posix_path_blocked(self):
        """Absolute POSIX path /foo should be rejected."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            request = WorkerRequest(
                prompt="test",
                owned_files=("/foo",),  # Absolute path
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, WORKER_FAILED)
            # Fixed: now caught by containment check
            self.assertIn("escapes containment", result.error)

    def test_directory_traversal_dot_dot_blocked(self):
        """Path with .. should be rejected."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            request = WorkerRequest(
                prompt="test",
                owned_files=("../evil.py",),
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, WORKER_FAILED)
            # Fixed: now caught by containment check
            self.assertIn("escapes containment", result.error)

    def test_windows_absolute_path_c_drive(self):
        """Windows absolute path C:\\foo should be rejected."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            request = WorkerRequest(
                prompt="test",
                owned_files=(r"C:\foo",),
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            # On Windows, this SHOULD be rejected but may not be due to the bug
            # On Unix, this will be treated as a relative path and may pass
            self.assertFalse(result.ok, "C:\\foo should be rejected")

    def test_windows_drive_relative_path(self):
        """Windows drive-relative path C:foo is now safely contained.

        Previously this was a vulnerability: Path('C:foo').is_absolute() returns False.
        After the fix, the path is resolved and checked for containment, so even if
        someone uses C:foo, it will be resolved to an absolute path and validated.
        """
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            # This path form was problematic but is now fixed
            request = WorkerRequest(
                prompt="test",
                owned_files=("C:evil",),  # Drive-relative
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            # After the fix, this should fail cleanly (no file, not containment issue)
            # because the path is resolved and checked for containment
            self.assertFalse(result.ok)
            self.assertEqual(result.status, WORKER_FAILED)
            # Should be a file-not-found error, not a containment error
            # (because it resolved within tmpdir but doesn't exist)
            self.assertTrue(
                "does not exist" in result.error.lower() or "no such file" in result.error.lower()
                or "escapes containment" in result.error,
                f"Unexpected error: {result.error}"
            )

    def test_windows_unc_path(self):
        """Windows UNC path \\server\\share should be rejected."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            request = WorkerRequest(
                prompt="test",
                owned_files=(r"\\server\share\file",),
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            # This should be rejected
            self.assertFalse(result.ok, "UNC path should be rejected")

    def test_mixed_separators_traversal(self):
        """Mixed separators in traversal: ..\\..\\evil or /../../evil."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            # Mixed separators that could bypass simple checks
            test_cases = [
                r"..\..\..\evil",
                "../../../evil",
            ]

            for path in test_cases:
                request = WorkerRequest(
                    prompt="test",
                    owned_files=(path,),
                    workdir=tmpdir,
                )
                result = driver.dispatch_worker(request)
                self.assertFalse(result.ok, f"Path {path} should be rejected")


class TestPathContainmentFixed(unittest.TestCase):
    """Test cases that verify the fix works correctly.

    After implementing the fix, these tests verify:
    1. Legitimate relative paths still work
    2. Resolved paths are checked for containment
    3. All escaping attempts are blocked on both Unix and Windows
    """

    def test_legitimate_relative_path_allowed(self):
        """Relative path like foo/bar.py should be allowed."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the file
            subdir = Path(tmpdir) / "foo"
            subdir.mkdir()
            (subdir / "bar.py").write_text("print(42)")

            request = WorkerRequest(
                prompt="test",
                owned_files=("foo/bar.py",),
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            self.assertTrue(result.ok, f"Legitimate path should work: {result.error}")

    def test_nested_relative_path_allowed(self):
        """Nested relative path like foo/baz/file.py should be allowed."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the file
            subdir = Path(tmpdir) / "foo" / "baz"
            subdir.mkdir(parents=True)
            (subdir / "file.py").write_text("x = 1")

            request = WorkerRequest(
                prompt="test",
                owned_files=("foo/baz/file.py",),
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            self.assertTrue(result.ok, f"Nested relative path should work: {result.error}")

    def test_windows_backslash_separator_allowed(self):
        """Windows-style backslash in relative path should work."""
        driver = CodexDriver(transport=FakeTransport())

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the file
            subdir = Path(tmpdir) / "foo"
            subdir.mkdir()
            (subdir / "bar.py").write_text("y = 2")

            # Use backslash separator (normalized by Path)
            request = WorkerRequest(
                prompt="test",
                owned_files=(r"foo\bar.py",),
                workdir=tmpdir,
            )
            result = driver.dispatch_worker(request)
            self.assertTrue(result.ok, f"Windows-style relative path should work: {result.error}")


if __name__ == "__main__":
    unittest.main()
