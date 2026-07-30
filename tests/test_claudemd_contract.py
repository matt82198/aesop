#!/usr/bin/env python3
"""
Tests for claudemd_contract.py validation gate.

TDD: write failing tests first, then implement.
"""

import sys
import tempfile
import subprocess
import unittest
from pathlib import Path


def run_validator(root_dir):
    """Run the validator and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "tools/claudemd_contract.py", root_dir],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    return result.returncode, result.stdout, result.stderr


class TestClaudeMdContract(unittest.TestCase):
    """Validation-gate behavior for tools/claudemd_contract.py."""

    def test_empty_file_fails(self):
        """Empty CLAUDE.md should fail validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create domain directory with empty CLAUDE.md
            domain_dir = Path(tmpdir) / "test_domain"
            domain_dir.mkdir()
            (domain_dir / "CLAUDE.md").write_text("")

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(exit_code, 1, f"Empty file should fail; got exit {exit_code}")
            self.assertTrue("FAIL" in stderr or "failed" in stderr.lower())

    def test_valid_minimal_file_passes(self):
        """Minimal valid CLAUDE.md with purpose + section should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_dir = Path(tmpdir) / "test_domain"
            domain_dir.mkdir()

            content = """# test_domain/ - Test domain

**What**: A test domain for validation.

## Universal rules

- Always test
- Keep it simple
"""
            (domain_dir / "CLAUDE.md").write_text(content)

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(
                exit_code, 0,
                f"Valid file should pass; got exit {exit_code}\nstderr: {stderr}",
            )
            self.assertTrue("passed" in stdout.lower() or "OK" in stdout)

    def test_missing_purpose_statement_fails(self):
        """File without purpose statement should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_dir = Path(tmpdir) / "test_domain"
            domain_dir.mkdir()

            # Deliberately omit any heading or purpose marker, but provide enough text
            content = """## Universal rules

- Always test
- Keep things simple and clear
- Write good documentation
- Test all edge cases carefully
"""
            (domain_dir / "CLAUDE.md").write_text(content)

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(exit_code, 1, f"Missing purpose should fail; got exit {exit_code}")
            self.assertIn("purpose", stderr.lower())

    def test_missing_key_sections_fails(self):
        """File without key sections should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_dir = Path(tmpdir) / "test_domain"
            domain_dir.mkdir()

            content = """# test_domain/ - Test domain

**What**: A test domain.

Some description here with lots of text to make it not empty but no sections.
"""
            (domain_dir / "CLAUDE.md").write_text(content)

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(exit_code, 1, f"Missing sections should fail; got exit {exit_code}")
            self.assertTrue("key sections" in stderr.lower() or "invariants" in stderr.lower())

    def test_multiple_domains_all_pass(self):
        """Multiple valid domain CLAUDE.md files should all pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            for domain in ["domain_a", "domain_b"]:
                domain_dir = Path(tmpdir) / domain
                domain_dir.mkdir()

                content = f"""# {domain}/ - {domain.upper()}

**What**: Domain {domain} specification.

## Universal rules

- Rule 1
- Rule 2

## Key invariants

- Invariant 1
"""
                (domain_dir / "CLAUDE.md").write_text(content)

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(
                exit_code, 0,
                f"All valid files should pass; got exit {exit_code}\nstderr: {stderr}",
            )
            self.assertIn("2", stdout)  # Should report 2 files

    def test_no_domain_files_fails(self):
        """Directory with no domain CLAUDE.md files should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create empty directory with no CLAUDE.md files
            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(exit_code, 1, f"No domain files should fail; got exit {exit_code}")
            self.assertTrue("No domain" in stderr or "no domain" in stderr.lower())

    def test_help_flag(self):
        """--help flag should print help and exit 0."""
        result = subprocess.run(
            [sys.executable, "tools/claudemd_contract.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )

        self.assertEqual(
            result.returncode, 0,
            f"--help should exit 0; got {result.returncode}",
        )
        self.assertTrue("Usage" in result.stdout or "usage" in result.stdout.lower())

    def test_valid_with_alternative_section_names(self):
        """Valid file with alternative section names (Contracts, Core invariants, etc.) should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_dir = Path(tmpdir) / "test_domain"
            domain_dir.mkdir()

            content = """# test_domain/ - Test domain

**What**: A test domain for validation.

## Core invariants

- Invariant 1
- Invariant 2

## Contracts

- Contract 1
- Contract 2
"""
            (domain_dir / "CLAUDE.md").write_text(content)

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(
                exit_code, 0,
                f"Alternative section names should pass; got exit {exit_code}\nstderr: {stderr}",
            )

    def test_header_only_empty_body_fails(self):
        """File with proper headers but empty bodies should FAIL validation.
        This is the key test for the fix: headers alone should not pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_dir = Path(tmpdir) / "fakedomain"
            domain_dir.mkdir()

            # This is the problematic case: header with no body
            content = """# fakedomain/ - Placeholder text to pad length past fifty chars easily

## Files
"""
            (domain_dir / "CLAUDE.md").write_text(content)

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(
                exit_code, 1,
                f"Header-only file should FAIL; got exit {exit_code}\nstderr: {stderr}",
            )
            self.assertTrue("FAIL" in stderr or "failed" in stderr.lower())

    def test_minimal_body_content_passes(self):
        """File with headers + minimal body content (2+ lines) should PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            domain_dir = Path(tmpdir) / "test_domain"
            domain_dir.mkdir()

            content = """# test_domain/ - Test domain

**What**: A test domain for validation.

## Files

- file1.py
- file2.py
"""
            (domain_dir / "CLAUDE.md").write_text(content)

            exit_code, stdout, stderr = run_validator(tmpdir)

            self.assertEqual(
                exit_code, 0,
                f"File with minimal body should pass; got exit {exit_code}\nstderr: {stderr}",
            )
            self.assertTrue("passed" in stdout.lower() or "OK" in stdout)


if __name__ == "__main__":
    unittest.main()
