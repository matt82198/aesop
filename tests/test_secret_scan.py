#!/usr/bin/env python3
"""
Tests for secret_scan.py — security audit for pragma abuse and secret categories.

TDD tests for:
- ITEM 1: Pragma should only soften generic_secret_assignment and env_access,
  NOT fatal classes like pem_private_key, aws_access_key, etc.
- ITEM 2: (Covered by pre-push hook self-test "Test 6" in hooks/pre-push-policy.sh)

FIXTURE SAFETY: All dummy secrets below are assembled at runtime via _j() string
concatenation so that NO scanner pattern ever appears contiguously in this source
file. This keeps the repo's own push gate clean WITHOUT using the pragma escape
hatch (which is exactly the bypass these tests lock down). Every value is a
well-known dummy/documentation form — nothing here is a live credential.
"""

import subprocess
import unittest
from pathlib import Path
import sys
import tempfile
import os

# Add parent directory to path so we can import secret_scan
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from secret_scan import has_pragma, scan_file

SCANNER_PATH = Path(__file__).parent.parent / "tools" / "secret_scan.py"


def _j(*parts):
    """Join fragments at runtime so secret-shaped strings never appear
    contiguously in this source file (the scanned artifact)."""
    return "".join(parts)


def _write_fixture(suffix, lines):
    """Write lines to a temp file, return its path (caller unlinks)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        for line in lines:
            f.write(line + "\n")
        return f.name


PRAGMA_LINE = "# secretscan: allow-pattern-docs"
JS_PRAGMA_LINE = "// secretscan: allow-pattern-docs"



class SkPatternWordBoundaryTest(unittest.TestCase):
    """The sk- key pattern must not match inside a longer word."""

    def test_sk_pattern_requires_word_boundary(self):
        """sk- must not match inside a longer word, but must still catch real keys.

        Regression: the comment word "disk-wins-on-any-disagreement" contains sk-
        followed by 20+ word chars and was reported as a live openai_anthropic_key,
        blocking a legitimate commit. Same substring-vs-word-boundary class that made
        a routing hook inert and broke the metrics-marker regex.
        """
        import re
        import secret_scan
        pattern, flags = secret_scan.PATTERNS["openai_anthropic_key"]
        rx = re.compile(pattern, flags)

        # Must NOT fire inside a longer word
        assert not rx.search("disk-wins-on-any-disagreement")
        assert not rx.search("a disk-based-cache-invalidation-thing")

        # Must STILL fire on real key shapes
        key = "sk-" + "abcdefghij0123456789XYZ"
        assert rx.search(key)
        assert rx.search('KEY="%s"' % key)
        assert rx.search("OPENAI_API_KEY=%s" % key)
        assert rx.search("(%s)" % key)

class TestPragmaNotSoftensFatalSecrets(unittest.TestCase):
    """ITEM 1: Pragma should NOT soften fatal secret categories."""

    def test_pem_key_with_pragma_is_fatal(self):
        """PEM private key with pragma should still be FATAL (exit 1)."""
        # Dummy PEM block; header assembled from fragments at runtime
        pem_begin = _j("-----BEGIN RSA ", "PRIVATE", " KEY-----")
        pem_end = _j("-----END RSA ", "PRIVATE", " KEY-----")
        temp_path = _write_fixture(
            ".pem",
            [
                PRAGMA_LINE,
                pem_begin,
                "MIIEpAIBAAKCAQEA1234567890abcdef1234567890abcdef1234567890ab",
                pem_end,
            ],
        )

        try:
            findings = scan_file(Path(temp_path))

            self.assertTrue(len(findings) > 0, "Should find PEM key")

            fatal_findings = [f for f in findings if f[3] is True]
            self.assertTrue(
                len(fatal_findings) > 0,
                f"PEM key should be fatal even with pragma. Findings: {findings}",
            )

            pem_findings = [f for f in findings if f[1] == "pem_private_key"]
            self.assertTrue(len(pem_findings) > 0, "Should detect pem_private_key rule")
            self.assertTrue(
                pem_findings[0][3] is True, "pem_private_key should be fatal"
            )
        finally:
            os.unlink(temp_path)

    def test_aws_access_key_with_pragma_is_fatal(self):
        """AWS access key with pragma should still be FATAL (exit 1)."""
        # AWS's canonical documentation dummy key, assembled at runtime
        dummy_aws = _j("AKIA", "IOSFODNN7EXAMPLE")
        temp_path = _write_fixture(
            ".py",
            [PRAGMA_LINE, "k = '" + dummy_aws + "'"],
        )

        try:
            findings = scan_file(Path(temp_path))

            aws_findings = [f for f in findings if f[1] == "aws_access_key"]
            self.assertTrue(len(aws_findings) > 0, "Should detect aws_access_key rule")
            self.assertTrue(
                aws_findings[0][3] is True, "aws_access_key should be fatal"
            )
        finally:
            os.unlink(temp_path)

    def test_gitignore_scanned_but_git_dir_skipped(self):
        """.gitignore must be SCANNED; only '.git' as a path COMPONENT is skipped.

        Regression: should_skip_file() used `'.git' in str(path)`, a substring
        match that also skipped '.gitignore' (and any '.git*' file), so secrets
        pasted into .gitignore went undetected.
        """
        from secret_scan import should_skip_file
        # .gitignore (and other .git-prefixed files) must NOT be skipped
        self.assertFalse(should_skip_file(Path("repo/.gitignore")),
                         ".gitignore must be scanned, not skipped")
        self.assertFalse(should_skip_file(Path(".gitignore")))
        self.assertFalse(should_skip_file(Path("repo/.gitattributes")))
        # actual .git directory contents must still be skipped
        self.assertTrue(should_skip_file(Path("repo/.git/config")),
                        ".git/ contents must still be skipped")
        self.assertTrue(should_skip_file(Path("repo/.git/hooks/pre-push")))

    def test_github_token_with_pragma_is_fatal(self):
        """GitHub token with pragma should still be FATAL (exit 1)."""
        # Dummy GitHub-style token (prefix + filler), assembled at runtime
        dummy_gh = _j("ghp_", "1234567890abcdefghij1234567890")
        temp_path = _write_fixture(
            ".js",
            [JS_PRAGMA_LINE, "const v = '" + dummy_gh + "'"],
        )

        try:
            findings = scan_file(Path(temp_path))

            github_findings = [f for f in findings if f[1] == "github_token"]
            self.assertTrue(len(github_findings) > 0, "Should detect github_token rule")
            self.assertTrue(
                github_findings[0][3] is True, "github_token should be fatal"
            )
        finally:
            os.unlink(temp_path)

    def test_pragma_softens_generic_secret_only(self):
        """Pragma SHOULD soften generic_secret_assignment (non-fatal)."""
        # Keyword split so the assignment shape never appears in this source file
        kw = _j("pass", "word")
        temp_path = _write_fixture(
            ".py",
            [PRAGMA_LINE, kw + ' = "this_is_a_test_example_password_12345"'],
        )

        try:
            findings = scan_file(Path(temp_path))

            generic_findings = [
                f for f in findings if f[1] == "generic_secret_assignment"
            ]
            self.assertTrue(
                len(generic_findings) > 0,
                "Should detect generic_secret_assignment",
            )
            # With pragma, this should be non-fatal
            self.assertTrue(
                generic_findings[0][3] is False,
                "generic_secret_assignment should be non-fatal with pragma",
            )
        finally:
            os.unlink(temp_path)

    def test_pragma_softens_env_access_only(self):
        """Pragma SHOULD soften env_access (non-fatal)."""
        # env accessor assembled at runtime so the access shape is not in source
        env_call = _j("os.envi", "ron['API_", "TOKEN']")
        temp_path = _write_fixture(
            ".py",
            [PRAGMA_LINE, "v = " + env_call],
        )

        try:
            findings = scan_file(Path(temp_path))

            env_findings = [f for f in findings if f[1] == "env_access"]
            self.assertTrue(len(env_findings) > 0, "Should detect env_access")
            # With pragma, this should be non-fatal
            self.assertTrue(
                env_findings[0][3] is False,
                "env_access should be non-fatal with pragma",
            )
        finally:
            os.unlink(temp_path)

    def test_slack_token_with_pragma_is_fatal(self):
        """Slack token with pragma should still be FATAL (exit 1)."""
        # Dummy Slack-style token, assembled at runtime
        dummy_slack = _j("xox", "b-1234567890-abcdefghijklmnop")
        temp_path = _write_fixture(
            ".py",
            [PRAGMA_LINE, "v = '" + dummy_slack + "'"],
        )

        try:
            findings = scan_file(Path(temp_path))

            slack_findings = [f for f in findings if f[1] == "slack_token"]
            self.assertTrue(len(slack_findings) > 0, "Should detect slack_token")
            self.assertTrue(
                slack_findings[0][3] is True, "slack_token should be fatal"
            )
        finally:
            os.unlink(temp_path)

    def test_connection_string_with_pragma_is_fatal(self):
        """Connection string with pragma should still be FATAL (exit 1)."""
        # Dummy connection URL assembled at runtime (scheme/creds/host split)
        # Using prod.mycompany.com (not allowlisted) instead of example.com (now allowlisted for test domains)
        dummy_url = _j(
            "postgresql:", "//user:dummypass", "@prod.mycompany.com:5432/db"
        )
        temp_path = _write_fixture(
            ".py",
            [PRAGMA_LINE, "url = '" + dummy_url + "'"],
        )

        try:
            findings = scan_file(Path(temp_path))

            conn_findings = [f for f in findings if f[1] == "connection_string"]
            self.assertTrue(len(conn_findings) > 0, "Should detect connection_string")
            self.assertTrue(
                conn_findings[0][3] is True, "connection_string should be fatal"
            )
        finally:
            os.unlink(temp_path)

    def test_openai_anthropic_key_with_pragma_is_fatal(self):
        """OpenAI/Anthropic key with pragma should still be FATAL (exit 1)."""
        # Dummy sk-style key, assembled at runtime
        dummy_sk = _j("sk", "-1234567890abcdefghij1234567890")
        temp_path = _write_fixture(
            ".js",
            [JS_PRAGMA_LINE, "const v = '" + dummy_sk + "'"],
        )

        try:
            findings = scan_file(Path(temp_path))

            openai_findings = [f for f in findings if f[1] == "openai_anthropic_key"]
            self.assertTrue(
                len(openai_findings) > 0, "Should detect openai_anthropic_key"
            )
            self.assertTrue(
                openai_findings[0][3] is True, "openai_anthropic_key should be fatal"
            )
        finally:
            os.unlink(temp_path)


class TestSizeAndBinaryBypassDetection(unittest.TestCase):
    """Test that size/binary skip logic does NOT bypass FATAL_RULES detection.

    Vulnerability: should_skip_file() previously skipped ALL checking for files >1MB
    or containing null bytes, unconditionally returning empty findings. Now files
    over the size threshold are scanned at least partially, and FATAL_RULES patterns
    are checked over the raw bytes.
    """

    def test_large_file_with_aws_key_is_fatal(self):
        """File >1MB with embedded AWS key should be FATAL (not skipped silently)."""
        # AWS key assembled at runtime
        dummy_aws = _j("AKIA", "IOSFODNN7EXAMPLE")

        # Create a >1MB file with AWS key embedded early on
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write("# Large file\n")
            f.write(f"secret_key = '{dummy_aws}'\n")
            # Pad to >1MB with repetitive content
            padding = "x" * 1000
            for i in range(1100):  # 1100 * 1000 = 1.1MB
                f.write(f"data_{i} = '{padding}'\n")
            temp_path = f.name

        try:
            # Verify file is >1MB
            file_size = os.path.getsize(temp_path)
            self.assertGreater(file_size, 1024 * 1024, f"Test file must be >1MB, got {file_size}")

            findings = scan_file(Path(temp_path))

            # Should detect the AWS key despite file size
            aws_findings = [f for f in findings if f[1] == "aws_access_key"]
            self.assertTrue(
                len(aws_findings) > 0,
                "AWS key should be detected in large file (not skipped by size)"
            )

            # Should be fatal
            fatal_aws = [f for f in aws_findings if f[3] is True]
            self.assertTrue(
                len(fatal_aws) > 0,
                "AWS key in large file should be FATAL"
            )
        finally:
            os.unlink(temp_path)

    def test_binary_file_with_aws_key_is_fatal(self):
        """File with null bytes and embedded AWS key should be FATAL (not skipped silently)."""
        # AWS key assembled at runtime
        dummy_aws = _j("AKIA", "IOSFODNN7EXAMPLE")

        # Create a file with embedded null bytes (binary)
        # The AWS key is placed where it might be found
        content = f"some binary junk\x00key = '{dummy_aws}'\x00more data"

        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".bin", delete=False
        ) as f:
            f.write(content.encode("latin-1"))
            temp_path = f.name

        try:
            findings = scan_file(Path(temp_path))

            # Should detect the AWS key despite binary nature
            aws_findings = [f for f in findings if f[1] == "aws_access_key"]
            self.assertTrue(
                len(aws_findings) > 0,
                "AWS key should be detected in binary file (not skipped by null bytes)"
            )

            # Should be fatal
            fatal_aws = [f for f in aws_findings if f[3] is True]
            self.assertTrue(
                len(fatal_aws) > 0,
                "AWS key in binary file should be FATAL"
            )
        finally:
            os.unlink(temp_path)

    def test_large_clean_file_reports_skip_status(self):
        """Large clean file should exit 0 but report SKIPPED-LARGE status on stderr."""
        # Create a >1MB file with NO secrets
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write("# Clean large file\n")
            # Pad to >1MB with repetitive content
            padding = "x" * 1000
            for i in range(1100):  # 1100 * 1000 = 1.1MB
                f.write(f"data_{i} = '{padding}'\n")
            temp_path = f.name

        try:
            # Verify file is >1MB
            file_size = os.path.getsize(temp_path)
            self.assertGreater(file_size, 1024 * 1024, f"Test file must be >1MB, got {file_size}")

            # Run via command line to capture stderr
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), temp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Should exit 0 (no secrets found)
            self.assertEqual(result.returncode, 0, f"Large clean file should exit 0. stderr: {result.stderr}")

            # Should report SKIPPED-LARGE in stderr (so caller can distinguish "clean" from "partially scanned")
            self.assertIn(
                "SKIPPED-LARGE",
                result.stderr,
                "Should emit SKIPPED-LARGE note to stderr for large files"
            )
        finally:
            os.unlink(temp_path)


class TestSkipCompiledArtifacts(unittest.TestCase):
    """Compiled Python artifacts (.pyc, .pyo, __pycache__) should be skipped.

    Rationale: bytecode is generated from source files which are already scanned.
    Scanning bytecode produces false positives because regex patterns (like the
    PEM detection regex) appear as literals in the compiled code. This test
    verifies that path-based scanning skips these artifacts.
    """

    def test_pyc_file_skipped_even_with_pattern(self):
        """A .pyc file with embedded pattern bytes should NOT be flagged."""
        # Create a temp directory with both source and compiled artifacts
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a real .py source file WITH a secret (should be flagged)
            dummy_pem = _j("-----BEGIN RSA ", "PRIVATE", " KEY-----")
            source_file = tmpdir_path / "test_module.py"
            source_file.write_text(
                f"# Source file\n{dummy_pem}\nkey_data = 'xxx'\n",
                encoding="utf-8"
            )

            # Create __pycache__ directory
            pycache_dir = tmpdir_path / "__pycache__"
            pycache_dir.mkdir()

            # Create a .pyc file with the same pattern embedded as bytes
            # (simulating what Python compiler would create)
            pyc_file = pycache_dir / "test_module.cpython-312.pyc"
            # .pyc format: magic (4 bytes) + timestamp (4 bytes) + bytecode
            # We'll just write raw bytes containing the PEM pattern
            pyc_content = b"\x00\x00\x00\x00\x00\x00\x00\x00" + dummy_pem.encode("utf-8") + b"\x00"
            pyc_file.write_bytes(pyc_content)

            # Scan the directory
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), str(tmpdir)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Should find the pattern in the SOURCE file
            self.assertIn(
                "pem_private_key",
                result.stdout,
                "Should detect PEM pattern in .py source file"
            )
            self.assertEqual(
                result.returncode,
                1,
                f"Should exit 1 (found secret in source). stdout: {result.stdout}"
            )

            # Should NOT report the .pyc file findings
            # (the key indicator is that we only have 1 finding, not 2)
            lines = [l for l in result.stdout.split("\n") if l.startswith("HIGH ")]
            high_findings = [l for l in lines if "pem_private_key" in l]

            self.assertEqual(
                len(high_findings),
                1,
                f"Should find PEM only in source, not in .pyc. "
                f"Findings: {high_findings}"
            )
            # Verify the finding is in the source file, not in __pycache__
            self.assertNotIn(
                "__pycache__",
                result.stdout,
                "Should NOT report findings from __pycache__ directory"
            )

    def test_pycache_directory_completely_skipped(self):
        """__pycache__ directory should be completely skipped during path traversal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create __pycache__ with a .pyc containing a pattern
            pycache_dir = tmpdir_path / "__pycache__"
            pycache_dir.mkdir()

            dummy_pem = _j("-----BEGIN RSA ", "PRIVATE", " KEY-----")
            pyc_file = pycache_dir / "fake.cpython-312.pyc"
            pyc_file.write_bytes(dummy_pem.encode("utf-8"))

            # Create a clean source file
            source_file = tmpdir_path / "clean.py"
            source_file.write_text("# Clean file\nprint('hello')\n", encoding="utf-8")

            # Scan should exit 0 (no findings)
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), str(tmpdir)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"Should exit 0 when only __pycache__ has patterns. "
                f"stdout: {result.stdout}"
            )
            self.assertIn(
                "CLEAN",
                result.stdout,
                "Should report CLEAN when only __pycache__ has patterns"
            )
            self.assertNotIn(
                "__pycache__",
                result.stdout,
                "Should not mention __pycache__ in output"
            )

    def test_pyo_file_skipped(self):
        """A .pyo file should also be skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            dummy_pem = _j("-----BEGIN RSA ", "PRIVATE", " KEY-----")
            pyo_file = tmpdir_path / "test.pyo"
            pyo_file.write_bytes(dummy_pem.encode("utf-8"))

            # Scan should exit 0
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), str(tmpdir)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                result.returncode,
                0,
                f"Should exit 0 and skip .pyo file. stdout: {result.stdout}"
            )
            self.assertIn("CLEAN", result.stdout)
            self.assertNotIn(".pyo", result.stdout)


class TestScannerSelfScanClean(unittest.TestCase):
    """The scanner must scan its OWN source clean with ZERO pragma reliance.

    Rationale: the pragma can no longer soften fatal classes (pem_private_key
    etc.), so any contiguous self-matching pattern literal in the scanner's
    source would fatally flag the scanner itself. Pattern literals that
    self-match must be runtime-assembled from fragments instead.
    """

    def test_scanner_has_no_pragma(self):
        """tools/secret_scan.py must NOT rely on the in-file pragma."""
        self.assertFalse(
            has_pragma(SCANNER_PATH),
            "Scanner source must not carry the pragma; it must scan itself "
            "clean by construction (fragment-assembled pattern literals).",
        )

    def test_scanner_scans_itself_clean(self):
        """python tools/secret_scan.py tools/secret_scan.py -> CLEAN, exit 0."""
        result = subprocess.run(
            [sys.executable, str(SCANNER_PATH), str(SCANNER_PATH)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Self-scan must exit 0. stdout: {result.stdout!r} "
            f"stderr: {result.stderr!r}",
        )
        self.assertIn("CLEAN", result.stdout, "Self-scan must report CLEAN")
        self.assertNotIn(
            "ALLOWED-DOC",
            result.stdout,
            "Self-scan must be clean WITHOUT pragma-softened findings",
        )


class TestUnquotedSecretDetection(unittest.TestCase):
    """Regression tests for unquoted secret value detection.

    Previously, generic_secret_assignment only matched quoted values.
    This tests that unquoted values (common in shell scripts and configs)
    are now properly detected.
    """

    def test_unquoted_password_assignment(self):
        """Unquoted password assignment should be detected."""
        # Assemble the test value at runtime so this source file scans clean
        test_value = _j("supersecretu", "alue123")
        temp_path = _write_fixture(
            ".py",
            [_j("pass", "word = " + test_value)],
        )

        try:
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), temp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Should exit 1 (found secret)
            self.assertEqual(
                result.returncode,
                1,
                f"Unquoted password should be detected. stdout: {result.stdout}",
            )

            # Should mention generic_secret_assignment
            self.assertIn(
                "generic_secret_assignment",
                result.stdout,
                f"Should detect as generic_secret_assignment. stdout: {result.stdout}",
            )
        finally:
            os.unlink(temp_path)

    def test_unquoted_api_key_assignment(self):
        """Unquoted api_key assignment should be detected."""
        # Assemble the test value at runtime so this source file scans clean
        test_value = _j("longsecretap", "ikey1234567890")
        temp_path = _write_fixture(
            ".sh",
            [_j("API_", "KEY=" + test_value)],
        )

        try:
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), temp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Should exit 1 (found secret)
            self.assertEqual(
                result.returncode,
                1,
                f"Unquoted API key should be detected. stdout: {result.stdout}",
            )

            # Should mention generic_secret_assignment or env_assignment
            self.assertTrue(
                "generic_secret_assignment" in result.stdout or "env_assignment" in result.stdout,
                f"Should detect assignment. stdout: {result.stdout}",
            )
        finally:
            os.unlink(temp_path)

    def test_unquoted_token_in_env_file(self):
        """Unquoted token in .env file should be detected."""
        temp_path = _write_fixture(
            ".env",
            ["SECRET_TOKEN=verylongtokenvalue123456789"],
        )

        try:
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), temp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Should exit 1 (found secret)
            self.assertEqual(
                result.returncode,
                1,
                f"Unquoted token in .env should be detected. stdout: {result.stdout}",
            )

            # Should mention env_assignment
            self.assertIn(
                "env_assignment",
                result.stdout,
                f"Should detect as env_assignment. stdout: {result.stdout}",
            )
        finally:
            os.unlink(temp_path)

    def test_unquoted_placeholder_skipped(self):
        """Unquoted placeholder values should be skipped (not fatal)."""
        temp_path = _write_fixture(
            ".py",
            ["password = changeme"],
        )

        try:
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), temp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Should exit 0 (placeholder is not a secret)
            self.assertEqual(
                result.returncode,
                0,
                f"Placeholder should be skipped. stdout: {result.stdout}",
            )
        finally:
            os.unlink(temp_path)

    def test_unquoted_vs_quoted_consistency(self):
        """Both quoted and unquoted forms should be detected."""
        # Test quoted form - assemble at runtime for clean scanning
        test_value = _j("secretval", "ue123")
        temp_quoted = _write_fixture(
            ".py",
            [_j('pass', 'word = "' + test_value + '"')],
        )

        # Test unquoted form - assemble at runtime for clean scanning
        test_value2 = _j("secretval", "ue123")
        temp_unquoted = _write_fixture(
            ".py",
            [_j("pass", "word = " + test_value2)],
        )

        try:
            result_quoted = subprocess.run(
                [sys.executable, str(SCANNER_PATH), temp_quoted],
                capture_output=True,
                text=True,
                timeout=30,
            )

            result_unquoted = subprocess.run(
                [sys.executable, str(SCANNER_PATH), temp_unquoted],
                capture_output=True,
                text=True,
                timeout=30,
            )

            # Both should detect the secret
            self.assertEqual(
                result_quoted.returncode,
                1,
                "Quoted form should be detected",
            )
            self.assertEqual(
                result_unquoted.returncode,
                1,
                "Unquoted form should be detected",
            )

            # Both should mention the rule
            self.assertIn("generic_secret_assignment", result_quoted.stdout)
            self.assertIn("generic_secret_assignment", result_unquoted.stdout)
        finally:
            os.unlink(temp_quoted)
            os.unlink(temp_unquoted)


class TestRangeMode(unittest.TestCase):
    """Regression tests for --range mode (commit range scanning).

    The pre-push hook needs to scan files changed between remote and local
    commits, since git diff --cached is empty at push time.
    """

    def test_range_mode_accepts_commit_range(self):
        """--range mode should accept git commit ranges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
            )

            # Create initial clean commit
            test_file = Path(tmpdir) / "file.txt"
            test_file.write_text("clean")
            subprocess.run(
                ["git", "add", "file.txt"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=tmpdir,
                capture_output=True,
            )

            # Create commit with secret
            dummy_key = _j("sk", "-1234567890abcdefghij1234567890")
            test_file.write_text(f"api_key = {dummy_key}")
            subprocess.run(
                ["git", "add", "file.txt"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "add secret"],
                cwd=tmpdir,
                capture_output=True,
            )

            # Get commit hashes
            result = subprocess.run(
                ["git", "log", "--format=%H"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            commits = result.stdout.strip().split("\n")
            old_commit = commits[1]
            new_commit = commits[0]

            # Scan with --range (must run in the git repo directory)
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), "--range", f"{old_commit}..{new_commit}"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=tmpdir,
            )

            # Should detect the secret
            self.assertEqual(
                result.returncode,
                1,
                f"Should detect secret in range. stdout: {result.stdout}",
            )
            self.assertIn(
                "openai_anthropic_key",
                result.stdout,
                f"Should detect sk- pattern. stdout: {result.stdout}",
            )

    def test_range_mode_clean_range_passes(self):
        """--range mode should pass when range contains no secrets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
            )

            # Create two clean commits
            test_file = Path(tmpdir) / "file.txt"
            test_file.write_text("clean1")
            subprocess.run(
                ["git", "add", "file.txt"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "commit1"],
                cwd=tmpdir,
                capture_output=True,
            )

            test_file.write_text("clean2")
            subprocess.run(
                ["git", "add", "file.txt"],
                cwd=tmpdir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "commit2"],
                cwd=tmpdir,
                capture_output=True,
            )

            # Get commit hashes
            result = subprocess.run(
                ["git", "log", "--format=%H"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )
            commits = result.stdout.strip().split("\n")
            old_commit = commits[1]
            new_commit = commits[0]

            # Scan with --range (must run in the git repo directory)
            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), "--range", f"{old_commit}..{new_commit}"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=tmpdir,
            )

            # Should pass (exit 0)
            self.assertEqual(
                result.returncode,
                0,
                f"Clean range should pass. stdout: {result.stdout}",
            )
            self.assertIn(
                "CLEAN",
                result.stdout,
                f"Should report CLEAN. stdout: {result.stdout}",
            )


class TestBlobScanClosesWorktreeBypass(unittest.TestCase):
    """Wave-25 P2 regression tests: --staged and --range must scan the git
    OBJECT being staged/pushed, not the working-tree copy of the file.

    Previously both modes built file lists via `git diff`/`git diff --cached`
    but then read $repo/<path> straight off disk (scan_file), so a secret
    that lived only in the staged index or in a committed-but-tip blob was
    invisible if the on-disk working copy no longer matched it.
    """

    def _init_repo(self, tmpdir):
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmpdir, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmpdir, capture_output=True,
        )

    def test_staged_bypass_stage_secret_then_edit_worktree_without_restaging(self):
        """stage-secret -> edit-worktree-without-restaging: the STAGED INDEX
        blob still carries the secret even though the on-disk file was
        overwritten with clean content afterward (and never re-staged).
        The old disk-based --staged scan missed this; the fixed scan (git
        show :<path>) must catch it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            self._init_repo(tmpdir)

            test_file = Path(tmpdir) / "config.py"
            test_file.write_text("clean = 1\n")
            subprocess.run(["git", "add", "config.py"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, capture_output=True)

            # Stage a secret. Keyword fragment-assembled (like the rest of this
            # file) so this test's own source never spells the assignment
            # shape contiguously -- otherwise the push gate would flag its
            # OWN f-string literal, unrelated to the fixture content it
            # dynamically produces at test-run time.
            dummy_key = _j("sk", "-1234567890abcdefghij1234567890")
            key_field = _j("api", "_key")
            test_file.write_text(f"{key_field} = '{dummy_key}'\n")
            subprocess.run(["git", "add", "config.py"], cwd=tmpdir, capture_output=True)

            # Edit the worktree copy WITHOUT re-staging: disk is now clean,
            # but the staged index blob still has the secret.
            test_file.write_text("# nothing to see here (worktree redacted this)\n")

            result = subprocess.run(
                [sys.executable, str(SCANNER_PATH), "--staged", "--repo", tmpdir],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                result.returncode,
                1,
                f"Staged-index secret must be caught even though disk copy is clean. "
                f"stdout: {result.stdout} stderr: {result.stderr}",
            )
            self.assertIn(
                "openai_anthropic_key",
                result.stdout,
                f"Should detect sk- secret from the staged blob. stdout: {result.stdout}",
            )

    def test_range_bypass_commit_secret_then_redact_worktree_without_new_commit(self):
        """commit-secret -> redact-in-worktree -> push: the secret is
        committed (tip commit's blob has it), then the worktree file is
        edited to remove it WITHOUT a new commit. The pushed commit (tip of
        the range) still carries the secret in its blob. The old disk-based
        --range scan (reading $repo/<path> after the redact) missed this;
        the fixed scan (git show <tip>:<path>) must catch it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            self._init_repo(tmpdir)

            test_file = Path(tmpdir) / "config.py"
            test_file.write_text("clean = 1\n")
            subprocess.run(["git", "add", "config.py"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, capture_output=True)

            old_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=tmpdir, capture_output=True, text=True
            ).stdout.strip()

            # Commit a secret (this becomes the tip of the range). Keyword
            # fragment-assembled (like the rest of this file) so this test's
            # own source never spells the assignment shape contiguously.
            dummy_key = _j("sk", "-1234567890abcdefghij1234567890")
            key_field = _j("api", "_key")
            test_file.write_text(f"{key_field} = '{dummy_key}'\n")
            subprocess.run(["git", "add", "config.py"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add secret"], cwd=tmpdir, capture_output=True)

            new_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=tmpdir, capture_output=True, text=True
            ).stdout.strip()

            # Redact in the worktree WITHOUT committing: disk is now clean,
            # but the tip commit's blob still has the secret.
            test_file.write_text("# nothing to see here (worktree redacted this)\n")

            result = subprocess.run(
                [
                    sys.executable, str(SCANNER_PATH),
                    "--range", f"{old_commit}..{new_commit}",
                    "--repo", tmpdir,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(
                result.returncode,
                1,
                f"Tip-commit secret must be caught even though disk copy was redacted "
                f"without a new commit. stdout: {result.stdout} stderr: {result.stderr}",
            )
            self.assertIn(
                "openai_anthropic_key",
                result.stdout,
                f"Should detect sk- secret from the tip commit's blob. stdout: {result.stdout}",
            )


class TestRangeModeFailsClosedOnGitError(unittest.TestCase):
    """Wave-25 P2 regression test: an unresolvable --range must fail CLOSED
    (non-zero exit, no CLEAN), not be silently treated as "zero files
    changed" -> CLEAN -> exit 0.
    """

    def _init_repo(self, tmpdir):
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmpdir, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmpdir, capture_output=True,
        )
        test_file = Path(tmpdir) / "file.txt"
        test_file.write_text("clean")
        subprocess.run(["git", "add", "file.txt"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, capture_output=True)

    def test_unresolvable_range_exits_nonzero_not_clean(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._init_repo(tmpdir)

            result = subprocess.run(
                [
                    sys.executable, str(SCANNER_PATH),
                    "--range", "totally-bogus-ref-aaaa..also-bogus-bbbb",
                    "--repo", tmpdir,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertNotEqual(
                result.returncode,
                0,
                f"Unresolvable range must fail CLOSED (non-zero exit), not exit 0. "
                f"stdout: {result.stdout} stderr: {result.stderr}",
            )
            self.assertNotIn(
                "CLEAN",
                result.stdout,
                f"Must not report CLEAN when the git command itself failed. "
                f"stdout: {result.stdout}",
            )

    def test_get_range_files_raises_on_git_error(self):
        """Unit-level: get_range_files() itself must raise GitScanError on a
        bad range rather than returning [] (indistinguishable from clean)."""
        from secret_scan import get_range_files, GitScanError

        with tempfile.TemporaryDirectory() as tmpdir:
            self._init_repo(tmpdir)
            with self.assertRaises(
                GitScanError,
                msg="get_range_files must raise GitScanError on an unresolvable range, "
                    "not silently return [].",
            ):
                get_range_files(tmpdir, "totally-bogus-ref-aaaa..also-bogus-bbbb")

    def test_get_range_files_empty_range_is_not_an_error(self):
        """Sanity check: a VALID range with genuinely zero changed files must
        still return [] cleanly (not raise) -- only actual git errors are
        fail-closed."""
        from secret_scan import get_range_files

        with tempfile.TemporaryDirectory() as tmpdir:
            self._init_repo(tmpdir)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=tmpdir, capture_output=True, text=True
            ).stdout.strip()
            result = get_range_files(tmpdir, f"{head}..{head}")
            self.assertEqual(result, [])



class TestRedactionSourceExemption(unittest.TestCase):
    """USER-APPROVED (2026-07-22) narrow exemption: connection_string findings
    in REDACTION_SOURCE_FILES are non-fatal (redaction-regex source necessarily
    contains connection-string-shaped text). Everything else stays fatal."""

    def _conn_string_line(self):
        # Runtime-assembled so this test file itself never contains the shape
        return "pattern = " + chr(39) + "http" + "s" + chr(58) + chr(47) + chr(47) + "usr" + chr(58) + "p" + "wd123@prod.internal" + chr(47) + "db" + chr(39)

    def test_exempted_file_connection_string_nonfatal(self):
        import secret_scan
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "bench"
            d.mkdir()
            f = d / "sample_transcripts_judgment.py"
            f.write_text(self._conn_string_line(), encoding="utf-8")
            findings = secret_scan.scan_file(f)
            conn = [x for x in findings if x[1] == "connection_string"]
            self.assertTrue(conn, "finding must still be REPORTED")
            self.assertFalse(any(x[3] for x in conn), "must be non-fatal in exempted file")

    def test_other_file_connection_string_still_fatal(self):
        import secret_scan
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "config_notes.py"
            f.write_text(self._conn_string_line(), encoding="utf-8")
            findings = secret_scan.scan_file(f)
            conn = [x for x in findings if x[1] == "connection_string"]
            self.assertTrue(conn)
            self.assertTrue(all(x[3] for x in conn), "fatal everywhere else")

    def test_exempted_file_other_rules_still_fatal(self):
        import secret_scan
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "bench"
            d.mkdir()
            f = d / "sample_transcripts_judgment.py"
            fake_key = "sk-" + "a1b2c3d4e5f6g7h8i9j0" + "k1l2m3n4"
            f.write_text("key = " + chr(39) + fake_key + chr(39), encoding="utf-8")
            findings = secret_scan.scan_file(f)
            keys = [x for x in findings if x[1] == "openai_anthropic_key"]
            self.assertTrue(keys)
            self.assertTrue(all(x[3] for x in keys), "non-connection_string rules stay fatal in exempted file")

    def test_is_redaction_source_matching(self):
        import secret_scan
        self.assertTrue(secret_scan._is_redaction_source("bench/sample_transcripts_judgment.py"))
        self.assertTrue(secret_scan._is_redaction_source("C:" + chr(92) + "x" + chr(92) + "bench" + chr(92) + "sample_transcripts_judgment.py"))
        self.assertFalse(secret_scan._is_redaction_source("bench/other.py"))
        self.assertFalse(secret_scan._is_redaction_source("sample_transcripts_judgment.py"))
        self.assertFalse(secret_scan._is_redaction_source(None))

if __name__ == "__main__":
    unittest.main()
