#!/usr/bin/env python3
"""
Tests for claudemd_sync_gate.py.

TDD: Write failing tests first.
"""

import json
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path


def run_gate(root_dir, base_ref="main", json_output=False):
    """Run the sync gate and return (exit_code, stdout, stderr)."""
    cmd = [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(root_dir), "--base-ref", base_ref]
    if json_output:
        cmd.append("--json")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    return result.returncode, result.stdout, result.stderr


class TestClaudeMdSyncGate(unittest.TestCase):
    """Test suite for claudemd_sync_gate.py gate."""

    def test_parse_basic_usage(self):
        """Tool should have --help and be runnable."""
        result = subprocess.run(
            [sys.executable, "tools/claudemd_sync_gate.py", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("CLAUDE.md", result.stdout)

    def test_no_changes_passes(self):
        """No git changes should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo with no changes
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit
            (tmpdir_path / "README.md").write_text("Initial")
            subprocess.run(
                ["git", "add", "README.md"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Run gate on repo with no changes
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            # Should exit 0 or 2 (error getting diff, which is OK for this test)
            self.assertIn(result.returncode, [0, 2])

    def test_test_only_changes_pass(self):
        """Changes only in tests/ should pass (exempted)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit
            (tmpdir_path / "README.md").write_text("Initial")
            subprocess.run(["git", "add", "README.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path, capture_output=True)

            # Add test changes only
            tests_dir = tmpdir_path / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_example.py").write_text("def test_foo(): pass")
            subprocess.run(["git", "add", "tests/test_example.py"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Add test"], cwd=tmpdir_path, capture_output=True)

            # Run gate
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            # Should exit 0 (tests are exempted)
            self.assertEqual(result.returncode, 0, f"stdout: {result.stdout}\nstderr: {result.stderr}")

    def test_docs_only_changes_pass(self):
        """Changes only in docs/ should pass (exempted)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit
            (tmpdir_path / "README.md").write_text("Initial")
            subprocess.run(["git", "add", "README.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path, capture_output=True)

            # Add docs changes only
            docs_dir = tmpdir_path / "docs"
            docs_dir.mkdir()
            (docs_dir / "example.md").write_text("# Example")
            subprocess.run(["git", "add", "docs/example.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Add docs"], cwd=tmpdir_path, capture_output=True)

            # Run gate
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            # Should exit 0 (docs are exempted)
            self.assertEqual(result.returncode, 0, f"stdout: {result.stdout}\nstderr: {result.stderr}")

    def test_meta_file_changes_pass(self):
        """Changes only to meta files (stats.json, README.md, etc.) should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit
            (tmpdir_path / "README.md").write_text("Initial")
            subprocess.run(["git", "add", "README.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path, capture_output=True)

            # Modify stats.json only
            (tmpdir_path / "stats.json").write_text('{"key": "value"}')
            subprocess.run(["git", "add", "stats.json"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Update stats"], cwd=tmpdir_path, capture_output=True)

            # Run gate
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            # Should exit 0 (meta files are exempted)
            self.assertEqual(result.returncode, 0, f"stdout: {result.stdout}\nstderr: {result.stderr}")

    def test_domain_code_change_without_claudemd_fails(self):
        """Domain code changes without CLAUDE.md update should fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit with domain structure
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()
            (tools_dir / "CLAUDE.md").write_text("# Tools domain")
            (tools_dir / "example.py").write_text("print('hello')")
            subprocess.run(["git", "add", "tools/"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path, capture_output=True)

            # Create a second commit to have a valid HEAD~1
            (tmpdir_path / "README.md").write_text("readme")
            subprocess.run(["git", "add", "README.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Add README"], cwd=tmpdir_path, capture_output=True)

            # Now modify code file without updating CLAUDE.md
            (tools_dir / "example.py").write_text("print('world')")
            subprocess.run(["git", "add", "tools/example.py"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Update code"], cwd=tmpdir_path, capture_output=True)

            # Run gate
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            # Should exit 1 (code change without CLAUDE.md update)
            self.assertEqual(result.returncode, 1, f"stdout: {result.stdout}\nstderr: {result.stderr}")
            self.assertIn("DRIFT", result.stdout)
            self.assertIn("tools", result.stdout)

    def test_domain_code_with_claudemd_update_passes(self):
        """Domain code changes WITH CLAUDE.md update should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit with domain structure
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()
            (tools_dir / "CLAUDE.md").write_text("# Tools domain")
            (tools_dir / "example.py").write_text("print('hello')")
            subprocess.run(["git", "add", "tools/"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path, capture_output=True)

            # Create a second commit to have a valid HEAD~1
            (tmpdir_path / "README.md").write_text("readme")
            subprocess.run(["git", "add", "README.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Add README"], cwd=tmpdir_path, capture_output=True)

            # Update both code AND CLAUDE.md together
            (tools_dir / "example.py").write_text("print('world')")
            (tools_dir / "CLAUDE.md").write_text("# Tools domain\n\nUpdated description")
            subprocess.run(["git", "add", "tools/"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Update tools"], cwd=tmpdir_path, capture_output=True)

            # Run gate
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            # Should exit 0 (both code and CLAUDE.md updated)
            self.assertEqual(result.returncode, 0, f"stdout: {result.stdout}\nstderr: {result.stderr}")
            self.assertIn("OK", result.stdout)

    def test_claudemd_only_changes_pass(self):
        """Changes only to CLAUDE.md within a domain should pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit with domain structure
            tools_dir = tmpdir_path / "tools"
            tools_dir.mkdir()
            (tools_dir / "CLAUDE.md").write_text("# Tools domain")
            (tools_dir / "example.py").write_text("print('hello')")
            subprocess.run(["git", "add", "tools/"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path, capture_output=True)

            # Create a second commit to have a valid HEAD~1
            (tmpdir_path / "README.md").write_text("readme")
            subprocess.run(["git", "add", "README.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Add README"], cwd=tmpdir_path, capture_output=True)

            # Update only CLAUDE.md, no code changes
            (tools_dir / "CLAUDE.md").write_text("# Tools domain\n\nUpdated description only")
            subprocess.run(["git", "add", "tools/CLAUDE.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Update docs"], cwd=tmpdir_path, capture_output=True)

            # Run gate
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path)],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            # Should exit 0 (CLAUDE.md-only changes are OK)
            self.assertEqual(result.returncode, 0, f"stdout: {result.stdout}\nstderr: {result.stderr}")

    def test_json_output_format(self):
        """JSON output should have correct structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir_path,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir_path,
                capture_output=True,
            )

            # Create initial commit
            (tmpdir_path / "README.md").write_text("Initial")
            subprocess.run(["git", "add", "README.md"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial"], cwd=tmpdir_path, capture_output=True)

            # Create a second commit with no changes (so HEAD~1 exists)
            (tmpdir_path / "LICENSE").write_text("MIT License")
            subprocess.run(["git", "add", "LICENSE"], cwd=tmpdir_path, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Add license"], cwd=tmpdir_path, capture_output=True)

            # Run gate with JSON output
            result = subprocess.run(
                [sys.executable, "tools/claudemd_sync_gate.py", "--root", str(tmpdir_path), "--json"],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
            )

            self.assertEqual(result.returncode, 0, f"stdout: {result.stdout}\nstderr: {result.stderr}")
            output = json.loads(result.stdout)
            self.assertIn("status", output)
            self.assertIn("exit_code", output)
            self.assertIn("findings", output)
            self.assertIn("summary", output)


class TestDomainDocSurfaces(unittest.TestCase):
    """tools/INDEX.md is a documentation surface, not code (regression for #751).

    #751 moved the per-tool index out of tools/CLAUDE.md into the generated
    tools/INDEX.md. A tool change accompanied by an INDEX.md update is a
    documented change; before this, the gate saw only CLAUDE.md and reported
    drift, which blocked every tool-touching PR from pushing.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
        import claudemd_sync_gate

        cls.gate = claudemd_sync_gate

    def test_index_md_is_a_domain_doc(self):
        self.assertTrue(self.gate.is_domain_doc("tools/INDEX.md"))
        self.assertTrue(self.gate.is_domain_doc("tools/CLAUDE.md"))
        self.assertTrue(self.gate.is_domain_doc("tools\\INDEX.md"))

    def test_code_is_not_a_domain_doc(self):
        self.assertFalse(self.gate.is_domain_doc("tools/rotate_logs.py"))
        # Matched on basename, so a lookalike filename is still code.
        self.assertFalse(self.gate.is_domain_doc("tools/MY_INDEX.md"))

    def test_index_md_satisfies_sync_requirement(self):
        """Tool code + regenerated INDEX.md must NOT read as drift."""
        classified = {"tools": ["tools/rotate_logs.py", "tools/INDEX.md"]}
        findings, code = self.gate.check_domain_claudemd_sync(Path("."), classified)
        self.assertEqual(findings, [])
        self.assertEqual(code, 0)

    def test_undocumented_tool_change_still_fails_closed(self):
        """The gate must not be weakened: a bare code change is still drift."""
        classified = {"tools": ["tools/rotate_logs.py"]}
        findings, code = self.gate.check_domain_claudemd_sync(Path("."), classified)
        self.assertEqual(code, 1)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["changed_files"], ["tools/rotate_logs.py"])


if __name__ == "__main__":
    unittest.main()
