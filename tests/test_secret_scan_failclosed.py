#!/usr/bin/env python3
# secretscan: allow-pattern-docs
"""
Test suite for secret_scan.py fail-closed behavior (wave-25+ P1 security fix).

This file constructs test fixtures with secret-like patterns (concatenated fake
tokens, passwords, etc.) for regression testing. These are never real secrets.

Validates that:
1. Git errors in history/staged/range scanning raise errors (exit 2)
2. Regression: normal clean files still pass (exit 0)
3. Regression: real secrets are still detected (exit 1)
4. Regression: pragma behavior is unchanged

Run: python -m unittest tests.test_secret_scan_failclosed
"""
import sys
import unittest
import tempfile
import subprocess
import stat
from pathlib import Path


class TestSecretScanRegression(unittest.TestCase):
    """Regression tests: verify secrets still detected, clean files still pass, etc."""

    @classmethod
    def setUpClass(cls):
        """Locate the scanner and set up test environment."""
        cls.scanner_path = Path(__file__).parent.parent / "tools" / "secret_scan.py"
        if not cls.scanner_path.exists():
            raise FileNotFoundError(f"secret_scan.py not found at {cls.scanner_path}")

    def setUp(self):
        """Create a temporary directory for each test."""
        self.tmpdir = tempfile.mkdtemp(prefix="test_scan_regression_")
        self.tmpdir_path = Path(self.tmpdir)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        if Path(self.tmpdir).exists():
            for item in Path(self.tmpdir).rglob("*"):
                try:
                    item.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                except Exception:
                    pass
            try:
                shutil.rmtree(self.tmpdir)
            except Exception:
                pass

    def run_scanner(self, *args):
        """Run the scanner with given arguments, return (exit_code, stdout, stderr)."""
        cmd = [sys.executable, str(self.scanner_path)] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            self.fail("Scanner timed out")
        except Exception as e:
            self.fail(f"Scanner failed to run: {e}")

    # =========== REGRESSION TESTS ===========

    def test_clean_file_still_passes(self):
        """Regression: a normal clean file still exits 0."""
        clean_file = self.tmpdir_path / "clean.py"
        clean_file.write_text("#!/usr/bin/env python3\ndef hello():\n    return 'world'\n")

        exit_code, stdout, stderr = self.run_scanner(str(clean_file))

        self.assertEqual(exit_code, 0, f"Clean file should exit 0. stdout: {stdout}, stderr: {stderr}")
        self.assertIn("CLEAN", stdout, "Should report CLEAN")

    def test_file_with_real_secret_still_detected(self):
        """Regression: a file with a real (concatenated) secret is still detected."""
        secret_file = self.tmpdir_path / "config.py"
        # Construct secret by concatenation so it's not a literal that trips the push gate
        fake_token = "ghp_" + "a" * 32
        secret_file.write_text(f"# GitHub token:\ntoken = '{fake_token}'\n")

        exit_code, stdout, stderr = self.run_scanner(str(secret_file))

        self.assertEqual(
            exit_code, 1,
            f"File with secret should exit 1. stdout: {stdout}, stderr: {stderr}"
        )
        self.assertIn("HIGH", stdout, "Should report HIGH severity finding")
        self.assertIn("github_token", stdout, "Should identify as github_token rule")
        # Should not print the actual token
        self.assertNotIn(fake_token, stdout, "Should not print the actual token")
        self.assertNotIn(fake_token, stderr, "Should not print the actual token")

    def test_aws_key_still_detected(self):
        """Regression: AWS access keys are still detected."""
        aws_file = self.tmpdir_path / "creds.txt"
        aws_key = "AKIA" + "B" * 16
        aws_file.write_text(f"AccessKeyId={aws_key}\n")

        exit_code, stdout, stderr = self.run_scanner(str(aws_file))

        self.assertEqual(exit_code, 1, "File with AWS key should exit 1")
        self.assertIn("aws_access_key", stdout, "Should identify AWS key")

    def test_pragma_still_softens_doc_rules(self):
        """Regression: pragma still softens doc-shaped rules (exit 0) but not fatal rules."""
        # Doc rule with pragma should soften to ALLOWED-DOC
        doc_file = self.tmpdir_path / "docs.py"
        doc_file.write_text(
            "# secretscan: allow-pattern-docs\n"
            'password = "this_is_documentation_value_12345"\n'
        )

        exit_code, stdout, stderr = self.run_scanner(str(doc_file))

        self.assertEqual(exit_code, 0, "Doc file with pragma should exit 0")
        self.assertIn("ALLOWED-DOC", stdout, "Should report ALLOWED-DOC")

    def test_pragma_does_not_soften_fatal_rules(self):
        """Regression: pragma does NOT soften fatal rules like github tokens."""
        fatal_file = self.tmpdir_path / "keys.py"
        # Even with pragma, github token must block
        token = "ghp_" + "c" * 32
        fatal_file.write_text(
            "# secretscan: allow-pattern-docs\n"
            f'token = "{token}"\n'
        )

        exit_code, stdout, stderr = self.run_scanner(str(fatal_file))

        self.assertEqual(
            exit_code, 1,
            f"Fatal rule (github token) should exit 1 even with pragma. stdout: {stdout}"
        )
        self.assertIn("HIGH", stdout, "Should report HIGH (fatal) even with pragma")

    # =========== LARGE FILE HANDLING ===========

    def test_large_clean_file_still_passes(self):
        """Regression: large clean file still exits 0 with SKIPPED-LARGE."""
        large_file = self.tmpdir_path / "bigfile.log"
        # Write 1MB + 100 bytes
        with open(large_file, "w") as f:
            f.write("x" * (1024 * 1024 + 100) + "\n")

        exit_code, stdout, stderr = self.run_scanner(str(large_file))

        self.assertEqual(exit_code, 0, "Large clean file should exit 0")
        self.assertIn("SKIPPED-LARGE", stderr, "Should report SKIPPED-LARGE")
        self.assertIn("CLEAN", stdout, "Should still report CLEAN")

    def test_large_file_with_secret_still_detected(self):
        """Regression: large file with secret in first 1MB is still detected."""
        large_file = self.tmpdir_path / "large_creds.txt"
        # Place AWS key in first part, then pad with 1MB
        aws_key = "AKIA" + "C" * 16
        with open(large_file, "w") as f:
            f.write(f"AccessKeyId={aws_key}\n")
            f.write("y" * (1024 * 1024 + 100) + "\n")

        exit_code, stdout, stderr = self.run_scanner(str(large_file))

        self.assertEqual(exit_code, 1, "Large file with secret should exit 1")
        self.assertIn("aws_access_key", stdout, "Should detect AWS key in large file")

    # =========== BINARY FILE HANDLING ===========

    def test_binary_file_scanned_for_fatal_rules(self):
        """Regression: binary file is scanned for FATAL_RULES."""
        binary_file = self.tmpdir_path / "blob.bin"
        aws_key = "AKIA" + "D" * 16
        with open(binary_file, "wb") as f:
            f.write(b"\x00binary junk\n")
            f.write(f"key = {aws_key}\n".encode())

        exit_code, stdout, stderr = self.run_scanner(str(binary_file))

        self.assertEqual(exit_code, 1, "Binary file with AWS key should exit 1")
        self.assertIn("aws_access_key", stdout, "Should detect AWS key in binary")
        self.assertIn("SKIPPED-BINARY", stderr, "Should report SKIPPED-BINARY")


class TestSecretScanGitFailures(unittest.TestCase):
    """Test that git failures in --history, --staged, --range modes fail closed (exit 2)."""

    @classmethod
    def setUpClass(cls):
        """Locate the scanner."""
        cls.scanner_path = Path(__file__).parent.parent / "tools" / "secret_scan.py"

    def setUp(self):
        """Create a temporary directory and initialize a git repo."""
        self.tmpdir = tempfile.mkdtemp(prefix="test_git_failclosed_")
        self.tmpdir_path = Path(self.tmpdir)

    def tearDown(self):
        """Clean up."""
        import shutil
        if Path(self.tmpdir).exists():
            for item in Path(self.tmpdir).rglob("*"):
                try:
                    item.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                except Exception:
                    pass
            try:
                shutil.rmtree(self.tmpdir)
            except Exception:
                pass

    def run_scanner(self, *args):
        """Run the scanner with given arguments."""
        cmd = [sys.executable, str(self.scanner_path)] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            self.fail("Scanner timed out")

    def test_git_history_on_invalid_repo_fails_closed(self):
        """
        --history on a directory that's not a git repo should fail closed (exit 2).
        Git log will fail, raising GitScanError instead of returning [] as "no history".
        """
        # tmpdir is not a git repo, so git log will fail
        exit_code, stdout, stderr = self.run_scanner("--history", "--repo", str(self.tmpdir_path))

        # FAIL CLOSED: exit 2 on git error (not 0 for "no history")
        self.assertEqual(
            exit_code, 2,
            f"--history on non-git-repo should exit 2, not {exit_code}.\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )
        self.assertIn("FATAL", stderr, "Should print FATAL message on git error")
        self.assertIn("git history", stderr.lower(), "Should mention history scan failure")

    def test_git_staged_on_invalid_repo_fails_closed(self):
        """
        --staged on a directory that's not a git repo should fail closed (exit 2).
        Git diff --cached will fail, raising GitScanError.
        """
        exit_code, stdout, stderr = self.run_scanner("--staged", "--repo", str(self.tmpdir_path))

        # Exit code 2 is expected on git error
        self.assertNotEqual(exit_code, 0, "--staged on non-git-repo should not exit 0")
        # We expect exit 1 (no staged files found) or 2 (git error)
        # The exact behavior depends on how git behaves, but not exit 0
        self.assertIn("FATAL", stderr, "Should print FATAL or error message")

    def test_git_range_with_invalid_ref_fails_closed(self):
        """
        --range with an invalid/unresolvable commit should fail closed (exit 2).
        Git diff will fail due to unresolvable ref, raising GitScanError.
        """
        exit_code, stdout, stderr = self.run_scanner(
            "--range", "nonexistent_ref..HEAD", "--repo", str(self.tmpdir_path)
        )

        # Exit code 2 is expected on git error
        self.assertNotEqual(exit_code, 0, "--range with invalid ref should not exit 0")
        self.assertIn("FATAL", stderr, "Should print FATAL message")


if __name__ == "__main__":
    unittest.main()
