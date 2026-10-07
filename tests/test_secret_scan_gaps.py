import unittest
import tempfile
import subprocess
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.secret_scan import scan_file


def _j(*parts):
    """Join fragments at runtime so secret-shaped strings never appear
    contiguously in this source file (the scanned artifact)."""
    return "".join(parts)


class TestSecretScanGaps(unittest.TestCase):
    """Test fixes for audit findings in secret_scan.py

    (1) Large files >1MB should be fully scanned, not just first 1MB
    (2) Connection string allowlist should include 127.0.0.0/8, *.local, *.localdomain, example.*, test.*
    """

    def test_runtime_assembled_secret_past_1mb_detected(self):
        """Test that a runtime-assembled AWS key past 1MB mark IS detected in large files"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "large_file.txt"

            # Create a file > 1MB with a secret well past the 1MB mark
            # First part: 1MB + 500KB of filler to ensure secret is far past threshold
            filler = "x" * (1024 * 1024 + 500 * 1024)

            # Runtime-assemble the dummy AWS key to avoid literal contiguous match
            # (rule: dummy secrets must be assembled at runtime by string concatenation)
            aws_key = _j("AKIA", "") + 'X' * 16

            content = filler + "\n" + aws_key + "\n"
            test_file.write_text(content)

            # Scan the file
            findings = scan_file(test_file)

            # Should find the AWS key despite being past 1MB
            aws_findings = [f for f in findings if f[1] == "aws_access_key"]
            self.assertGreater(len(aws_findings), 0,
                             "Should detect AWS key past 1MB mark (currently fails with 1MB limit)")

    def test_allowlisted_127_range_not_flagged(self):
        """Test that 127.0.0.0/8 (loopback range) is allowlisted"""
        test_cases = [
            _j("postgre", "sql://user:", "pass@127.0.0.1/db"),
            _j("postgre", "sql://user:", "pass@127.0.0.1:5432/db"),
            _j("postgre", "sql://user:", "pass@127.1.1.1/db"),
            _j("postgre", "sql://user:", "pass@127.255.255.255/db"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, conn_str in enumerate(test_cases):
                test_file = Path(tmpdir) / f"config_{i}.txt"
                test_file.write_text(conn_str)

                findings = scan_file(test_file)
                connection_findings = [f for f in findings if f[1] == "connection_string"]

                self.assertEqual(len(connection_findings), 0,
                               f"Should NOT flag 127.0.0.0/8 host: {conn_str}")

    def test_allowlisted_local_domain_not_flagged(self):
        """Test that *.local and *.localdomain are allowlisted"""
        test_cases = [
            _j("postgre", "sql://user:", "pass@localhost.local/db"),
            _j("postgre", "sql://user:", "pass@my-service.local/db"),
            _j("postgre", "sql://user:", "pass@db.local:5432/db"),
            _j("postgre", "sql://user:", "pass@localhost.localdomain/db"),
            _j("postgre", "sql://user:", "pass@app.localdomain:5432/db"),
            _j("postgre", "sql://user:", "pass@service.localdomain/db"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, conn_str in enumerate(test_cases):
                test_file = Path(tmpdir) / f"config_{i}.txt"
                test_file.write_text(conn_str)

                findings = scan_file(test_file)
                connection_findings = [f for f in findings if f[1] == "connection_string"]

                self.assertEqual(len(connection_findings), 0,
                               f"Should NOT flag .local/.localdomain host: {conn_str}")

    def test_allowlisted_example_domain_not_flagged(self):
        """Test that example.* (all subdomains) is allowlisted"""
        test_cases = [
            _j("postgre", "sql://user:", "pass@example.com/db"),
            _j("postgre", "sql://user:", "pass@example.org/db"),
            _j("postgre", "sql://user:", "pass@sub.example.com/db"),
            _j("postgre", "sql://user:", "pass@api.example.io/db"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, conn_str in enumerate(test_cases):
                test_file = Path(tmpdir) / f"config_{i}.txt"
                test_file.write_text(conn_str)

                findings = scan_file(test_file)
                connection_findings = [f for f in findings if f[1] == "connection_string"]

                self.assertEqual(len(connection_findings), 0,
                               f"Should NOT flag example.* host: {conn_str}")

    def test_allowlisted_test_domain_not_flagged(self):
        """Test that test.* (all subdomains) is allowlisted"""
        test_cases = [
            _j("postgre", "sql://user:", "pass@test.com/db"),
            _j("postgre", "sql://user:", "pass@test.example.com/db"),
            _j("postgre", "sql://user:", "pass@staging.test.local/db"),
            _j("postgre", "sql://user:", "pass@dev.test.io/db"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, conn_str in enumerate(test_cases):
                test_file = Path(tmpdir) / f"config_{i}.txt"
                test_file.write_text(conn_str)

                findings = scan_file(test_file)
                connection_findings = [f for f in findings if f[1] == "connection_string"]

                self.assertEqual(len(connection_findings), 0,
                               f"Should NOT flag test.* host: {conn_str}")

    def test_non_allowlisted_hosts_flagged(self):
        """Test that non-allowlisted hosts ARE flagged in connection strings"""
        test_cases = [
            _j("postgre", "sql://user:", "pass@aws.amazon.com/db"),
            _j("postgre", "sql://user:", "pass@db.herokuapp.com/db"),
            _j("postgre", "sql://user:", "pass@prod.mycompany.com/db"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, conn_str in enumerate(test_cases):
                test_file = Path(tmpdir) / f"config_{i}.txt"
                test_file.write_text(conn_str)

                findings = scan_file(test_file)
                connection_findings = [f for f in findings if f[1] == "connection_string"]

                self.assertGreater(len(connection_findings), 0,
                                 f"Should flag non-allowlisted host: {conn_str}")


class TestGetGitBlobFailsClosed(unittest.TestCase):
    """Wave-25 P2 gap fix: get_git_blob() must raise GitScanError on git-show
    failure, not return None. When enumeration (via --diff-filter=d) says a
    path exists, a subsequent git-show failure is a real error (corruption,
    permissions, unreadable blob), not a legitimate 'file absent' case.
    Failing closed ensures such errors block the push.
    """

    def test_get_git_blob_raises_on_git_show_failure(self):
        """Unit-level test: get_git_blob() must raise GitScanError when
        git show fails, not return None.

        When an enumerated file cannot be read (via git show), this is a
        real error that should fail closed (non-zero exit), not silently
        skip (continue) and possibly report CLEAN.
        """
        from tools.secret_scan import get_git_blob, GitScanError

        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a git repo
            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)

            # Try to show a blob that doesn't exist
            # This will cause git show to fail with a non-zero return code
            with self.assertRaises(
                GitScanError,
                msg="get_git_blob must raise GitScanError on git-show failure, "
                    "not return None."
            ):
                get_git_blob(tmpdir, ":nonexistent.txt")

    def test_get_git_blob_includes_git_error_in_exception(self):
        """GitScanError from get_git_blob should include the git stderr
        so the caller can log a clear error message."""
        from tools.secret_scan import get_git_blob, GitScanError

        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a git repo
            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)

            # Try to show a blob that doesn't exist
            try:
                get_git_blob(tmpdir, ":nonexistent.txt")
                self.fail("Expected GitScanError to be raised")
            except GitScanError as e:
                # Error message should reference the ref that failed
                self.assertIn(":nonexistent.txt", str(e))


if __name__ == "__main__":
    unittest.main()
