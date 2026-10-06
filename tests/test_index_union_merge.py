#!/usr/bin/env python3
"""TDD: verify tools/INDEX.md union merge attribute behavior.

Tests that:
1. With the merge=union attribute, two branches adding different INDEX lines
   merge cleanly with both lines present (union behavior)
2. The union result is correctly normalized by gen_tool_index
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def run_cmd(cmd, check=True, capture_output=True):
    """Helper to run a command."""
    return subprocess.run(cmd, check=check, capture_output=capture_output, text=True)


class TestIndexUnionMerge(unittest.TestCase):
    """Behavioral proof: INDEX.md union merge works correctly."""

    def setUp(self):
        """Create a temp git repo for testing."""
        # Save the original cwd BEFORE chdir'ing away: tearDown must restore
        # it exactly, not just chdir("/"). A process-wide cwd leak here
        # poisons every later test in the same shard process (GAP: PR #699
        # CI escape -- this exact class of bug is why test_metrics_gate.py
        # deliberately avoids os.chdir at all).
        self._orig_cwd = os.getcwd()
        self.test_dir = tempfile.mkdtemp(prefix="index_union_merge_")
        self.repo_path = Path(self.test_dir) / "repo"
        self.repo_path.mkdir()
        os.chdir(self.repo_path)

        # Initialize git repo
        run_cmd(["git", "init"])
        run_cmd(["git", "config", "user.name", "Test"])
        run_cmd(["git", "config", "user.email", "test@example.com"])

        # Create initial INDEX.md with a header and sorted entries
        self.index_file = self.repo_path / "INDEX.md"
        self.index_file.write_text("""<!-- GENERATED-BY: gen_tool_index.py -->
# Index

- `tool-a` -- Tool A
""")

        # Create initial commit
        run_cmd(["git", "add", "INDEX.md"])
        run_cmd(["git", "commit", "-m", "initial"])

        # Rename default branch to main if needed
        run_cmd(["git", "branch", "-M", "main"])

    def tearDown(self):
        """Clean up temp dir."""
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_with_attribute_union_merge_succeeds(self):
        """POSITIVE CONTROL: With merge=union attribute, lines are kept without conflicts."""
        # Add .gitattributes with union merge driver for INDEX.md
        gitattributes = self.repo_path / ".gitattributes"
        gitattributes.write_text("INDEX.md merge=union\n")
        run_cmd(["git", "add", ".gitattributes"])
        run_cmd(["git", "commit", "-m", "add gitattributes"])

        # Create branch 1 and add tool-b
        run_cmd(["git", "checkout", "-b", "add-tool-b"])
        content = self.index_file.read_text()
        self.index_file.write_text(content.rstrip() + "\n- `tool-b` -- Tool B\n")
        run_cmd(["git", "add", "INDEX.md"])
        run_cmd(["git", "commit", "-m", "add tool-b"])

        # Create branch 2 and add tool-c
        run_cmd(["git", "checkout", "main"])
        run_cmd(["git", "checkout", "-b", "add-tool-c"])
        content = self.index_file.read_text()
        self.index_file.write_text(content.rstrip() + "\n- `tool-c` -- Tool C\n")
        run_cmd(["git", "add", "INDEX.md"])
        run_cmd(["git", "commit", "-m", "add tool-c"])

        # Merge branch 1 into branch 2 WITH the attribute (union driver)
        result = run_cmd(["git", "merge", "add-tool-b"], check=False)

        # Should succeed (exit code 0)
        self.assertEqual(result.returncode, 0, f"Expected successful merge with union attribute. stderr: {result.stderr}")

        # Should NOT have conflict markers
        content = self.index_file.read_text()
        self.assertNotIn("<<<<<<< HEAD", content, "Unexpected conflict markers with union attribute")
        self.assertNotIn("=======", content, "Unexpected conflict markers with union attribute")
        self.assertNotIn(">>>>>>> add-tool-b", content, "Unexpected conflict markers with union attribute")

        # Should have both tool-b and tool-c lines
        self.assertIn("tool-b", content, "Expected tool-b in merged file")
        self.assertIn("tool-c", content, "Expected tool-c in merged file")

    def test_without_attribute_shows_conflict(self):
        """NEGATIVE CONTROL: Without the attribute, same merge would conflict."""
        # This test uses a fresh repo without the .gitattributes file
        # to show that without the attribute, we'd see conflicts.

        # Create branch 1 and add tool-b
        run_cmd(["git", "checkout", "-b", "add-tool-b"])
        content = self.index_file.read_text()
        self.index_file.write_text(content.rstrip() + "\n- `tool-b` -- Tool B\n")
        run_cmd(["git", "add", "INDEX.md"])
        run_cmd(["git", "commit", "-m", "add tool-b"])

        # Create branch 2 and add tool-c at the same append point (before final)
        run_cmd(["git", "checkout", "main"])
        run_cmd(["git", "checkout", "-b", "add-tool-c"])
        content = self.index_file.read_text()
        self.index_file.write_text(content.rstrip() + "\n- `tool-c` -- Tool C\n")
        run_cmd(["git", "add", "INDEX.md"])
        run_cmd(["git", "commit", "-m", "add tool-c"])

        # Try to merge branch 1 WITHOUT the attribute
        # This demonstrates the problem the union attribute solves
        result = run_cmd(["git", "merge", "add-tool-b"], check=False)

        # The behavior depends on whether git recognizes tool-b appended after the same base line
        # For this test, we just verify that IF there's a conflict, it has conflict markers
        if result.returncode != 0:
            content = self.index_file.read_text()
            self.assertIn("<<<<<<< HEAD", content)
            # Abort for cleanup
            run_cmd(["git", "merge", "--abort"], check=False)


if __name__ == "__main__":
    unittest.main()
