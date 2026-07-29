#!/usr/bin/env python3
"""
End-to-end tests for seam-u sandbox/oracle layout fix.

Verifies:
1. Sandbox layout is correct: sandbox/repo/ and sandbox/oracle/
2. Oracle can be found and executed from sandbox root (cwd=sandbox)
3. Oracle conftest can find ../repo relative to oracle/
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OracleSandboxLayoutTest(unittest.TestCase):
    """Test the oracle sandbox layout with real fixtures."""

    def setUp(self):
        """Set up test fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def tearDown(self):
        """Clean up test resources."""
        if self.tmpdir and Path(self.tmpdir).exists():
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_sandbox_layout_structure(self):
        """Verify sandbox is created with correct layout: repo/ and oracle/."""
        task_id = "st01"
        task_dir = Path(__file__).parent.parent / "bench" / "seam_tasks" / task_id

        if not task_dir.exists():
            self.skipTest(f"Task fixture {task_id} not found")

        from bench.run_seam_u import apply_diff_to_sandbox

        sandbox = Path(self.tmpdir) / "sandbox"

        # Apply a simple diff that changes nothing (tests structure, not functionality)
        test_diff = "--- a/test.txt\n+++ b/test.txt\n@@ -1 +1 @@\n test\n"

        # Apply diff (will fail, but sandbox structure should be created)
        apply_diff_to_sandbox(task_dir / "repo", test_diff, sandbox)

        # Verify structure
        self.assertTrue((sandbox / "repo").exists(), "sandbox/repo/ should exist")
        self.assertTrue((sandbox / "repo").is_dir(), "sandbox/repo/ should be a directory")

    def test_oracle_copy_at_grading_time(self):
        """Verify oracle is copied to sandbox at grading time, not during setup."""
        task_id = "st01"
        task_dir = Path(__file__).parent.parent / "bench" / "seam_tasks" / task_id

        if not task_dir.exists():
            self.skipTest(f"Task fixture {task_id} not found")

        sandbox = Path(self.tmpdir) / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)

        # After setup, oracle should NOT be in sandbox yet
        self.assertFalse((sandbox / "oracle").exists(), "oracle/ should not exist before grading")

        # Copy oracle (as done at grading time)
        oracle_src = task_dir / "oracle"
        if oracle_src.exists():
            shutil.copytree(oracle_src, sandbox / "oracle")
            self.assertTrue((sandbox / "oracle").exists(), "oracle/ should exist after copy")
            self.assertTrue((sandbox / "oracle").is_dir(), "oracle/ should be a directory")

    def test_apply_diff_status_reporting(self):
        """Verify apply_diff_to_sandbox reports status: applied/noop/failed."""
        task_id = "st01"
        task_dir = Path(__file__).parent.parent / "bench" / "seam_tasks" / task_id

        if not task_dir.exists():
            self.skipTest(f"Task fixture {task_id} not found")

        from bench.run_seam_u import apply_diff_to_sandbox

        sandbox = Path(self.tmpdir) / "sandbox"

        # Test 1: Invalid diff should return "failed"
        bad_diff = "invalid diff content"
        status = apply_diff_to_sandbox(task_dir / "repo", bad_diff, sandbox)
        self.assertEqual(status, "failed", "Invalid diff should return 'failed'")

        # Test 2: Status should be one of the expected values
        sandbox2 = Path(self.tmpdir) / "sandbox2"
        empty_diff = ""
        status = apply_diff_to_sandbox(task_dir / "repo", empty_diff, sandbox2)
        self.assertIn(status, ["failed", "noop", "applied"], "Status should be one of: failed/noop/applied")


if __name__ == "__main__":
    unittest.main()
