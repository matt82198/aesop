#!/usr/bin/env python3
"""Unit tests for inbox_drain.py UI inbox submission tracking."""
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestInboxDrain(unittest.TestCase):
    """Test cases for inbox_drain.py subcommands."""

    def setUp(self):
        """Create temporary directories for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.drain_script = Path(__file__).parent.parent / "tools" / "inbox_drain.py"
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up temporary directories."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _run_drain(self, *args, env_overrides=None):
        """Run inbox_drain.py with arguments."""
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        if env_overrides:
            env.update(env_overrides)

        cmd = [sys.executable, str(self.drain_script)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return result

    def test_pending_no_inbox_file(self):
        """Test graceful degradation when inbox file missing."""
        result = self._run_drain("pending")
        self.assertEqual(result.returncode, 0)
        self.assertIn("NO PENDING", result.stdout)

    def test_pending_empty_inbox(self):
        """Test pending with empty inbox file."""
        inbox_path = self.state_dir / "ui-inbox.md"
        inbox_path.write_text("")
        result = self._run_drain("pending")
        self.assertEqual(result.returncode, 0)
        self.assertIn("NO PENDING", result.stdout)

    def test_pending_with_items(self):
        """Test listing pending inbox items."""
        inbox_path = self.state_dir / "ui-inbox.md"
        ts1 = "2024-07-13T10:00:00Z"
        ts2 = "2024-07-13T10:05:00Z"
        inbox_path.write_text(f"- [{ts1}] Item 1\n- [{ts2}] Item 2\n")

        result = self._run_drain("pending")
        self.assertEqual(result.returncode, 0)
        self.assertIn(ts1, result.stdout)
        self.assertIn("Item 1", result.stdout)
        self.assertIn(ts2, result.stdout)
        self.assertIn("Item 2", result.stdout)

    def test_mark_single_item(self):
        """Test marking one item as processed."""
        inbox_path = self.state_dir / "ui-inbox.md"
        ts = "2024-07-13T10:00:00Z"
        inbox_path.write_text(f"- [{ts}] Item 1\n")

        result = self._run_drain("mark", ts)
        self.assertEqual(result.returncode, 0)
        self.assertIn("marked", result.stderr)

        # Now pending should show no items
        result = self._run_drain("pending")
        self.assertIn("NO PENDING", result.stdout)

    def test_mark_idempotent(self):
        """Test that marking same item twice is idempotent."""
        inbox_path = self.state_dir / "ui-inbox.md"
        ts = "2024-07-13T10:00:00Z"
        inbox_path.write_text(f"- [{ts}] Item 1\n")

        # Mark first time
        result1 = self._run_drain("mark", ts)
        self.assertEqual(result1.returncode, 0)

        # Mark again (should be idempotent)
        result2 = self._run_drain("mark", ts)
        self.assertEqual(result2.returncode, 0)
        self.assertIn("already marked", result2.stderr)

    def test_mark_all(self):
        """Test marking all pending items at once."""
        inbox_path = self.state_dir / "ui-inbox.md"
        ts1 = "2024-07-13T10:00:00Z"
        ts2 = "2024-07-13T10:05:00Z"
        ts3 = "2024-07-13T10:10:00Z"
        inbox_path.write_text(f"- [{ts1}] Item 1\n- [{ts2}] Item 2\n- [{ts3}] Item 3\n")

        result = self._run_drain("mark-all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("marked all", result.stderr)

        # Verify all are marked
        result = self._run_drain("pending")
        self.assertIn("NO PENDING", result.stdout)

    def test_mark_all_with_partial_seen(self):
        """Test mark-all when some items already marked."""
        inbox_path = self.state_dir / "ui-inbox.md"
        ts1 = "2024-07-13T10:00:00Z"
        ts2 = "2024-07-13T10:05:00Z"
        ts3 = "2024-07-13T10:10:00Z"
        inbox_path.write_text(f"- [{ts1}] Item 1\n- [{ts2}] Item 2\n- [{ts3}] Item 3\n")

        # Mark first item
        self._run_drain("mark", ts1)

        # Mark all (should skip ts1, mark ts2 and ts3)
        result = self._run_drain("mark-all")
        self.assertEqual(result.returncode, 0)

        # Verify all are marked
        result = self._run_drain("pending")
        self.assertIn("NO PENDING", result.stdout)

    def test_mark_all_empty_inbox(self):
        """Test mark-all with no pending items."""
        inbox_path = self.state_dir / "ui-inbox.md"
        inbox_path.write_text("")

        result = self._run_drain("mark-all")
        self.assertEqual(result.returncode, 0)
        self.assertIn("no pending items", result.stderr)

    def test_graceful_degradation_seen_file_missing(self):
        """Test graceful degradation when seen file missing."""
        inbox_path = self.state_dir / "ui-inbox.md"
        ts = "2024-07-13T10:00:00Z"
        inbox_path.write_text(f"- [{ts}] Item 1\n")

        # Don't create seen file
        result = self._run_drain("pending")
        self.assertEqual(result.returncode, 0)
        self.assertIn(ts, result.stdout)


if __name__ == "__main__":
    unittest.main()
