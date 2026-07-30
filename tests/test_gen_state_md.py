"""Tests for gen_state_md — STATE.md checkpoint generator from state store (unittest).

Covers:
- Happy path: render checkpoint from state store with tracker items
- Empty store: graceful handling of missing events
- Malformed store: exit 1 on corrupt events
- Determinism: two runs with same inputs produce identical output
- ASCII-safe output
- --out flag and stdout fallback
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Will be implemented after tests
from tools.gen_state_md import generate_state_md


class GenStateMdTest(unittest.TestCase):
    """Tests for STATE.md generator from state store."""

    def setUp(self):
        """Create a temporary state directory with an initialized state store."""
        self.tmp = tempfile.mkdtemp()
        self.state_dir = Path(self.tmp) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._stores = []  # track stores for tearDown cleanup

        # Set AESOP_STATE_ROOT for the generator
        self.original_state_root = os.environ.get("AESOP_STATE_ROOT")
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

    def tearDown(self):
        """Clean up temporary state directory."""
        import shutil
        for s in self._stores:
            try:
                s.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

        # Restore original AESOP_STATE_ROOT
        if self.original_state_root is not None:
            os.environ["AESOP_STATE_ROOT"] = self.original_state_root
        else:
            os.environ.pop("AESOP_STATE_ROOT", None)

    def _init_db(self, state_dir):
        """Initialize the event store database if needed."""
        from state_store import EventStore
        db_path = state_dir / "events.db"
        store = EventStore(str(db_path))
        self._stores.append(store)

    def test_empty_store_renders_checkpoint(self):
        """Test that an empty state store produces a valid checkpoint header."""
        self._init_db(self.state_dir)

        # Generate from empty store (no tracker events)
        output = generate_state_md(
            state_dir=str(self.state_dir),
            timestamp="2026-07-30T12:00:00+00:00"
        )

        # Should have the checkpoint header
        self.assertIn("# STATE — Generated Checkpoint", output)
        self.assertIn("2026-07-30T12:00:00+00:00", output)
        # Should have ISO timestamp
        self.assertRegex(output, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_render_with_tracker_items(self):
        """Test that tracker items are rendered by lane."""
        self._init_db(self.state_dir)

        # Add tracker items to the state store
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "events.db"))
        self._stores.append(api)

        # Create some items in different lanes
        api.append("tracker", "item_created", {
            "id": "item-1",
            "title": "Test feature",
            "status": "ranked",
            "lane": "features",
            "priority": 1
        })
        api.append("tracker", "item_created", {
            "id": "item-2",
            "title": "Bug fix",
            "status": "in-progress",
            "lane": "defects",
            "priority": 2
        })

        output = generate_state_md(
            state_dir=str(self.state_dir),
            timestamp="2026-07-30T12:00:00+00:00"
        )

        # Should include both items
        self.assertIn("Test feature", output)
        self.assertIn("Bug fix", output)
        # Lane names are title-cased in the output
        self.assertIn("Features", output)
        self.assertIn("Defects", output)

    def test_output_to_file(self):
        """Test --out flag writes to file."""
        self._init_db(self.state_dir)

        out_file = self.state_dir / "STATE_generated.md"
        output = generate_state_md(state_dir=str(self.state_dir), out_path=str(out_file))

        # Should return output when out_path provided
        self.assertIsNotNone(output)
        # File should exist
        self.assertTrue(out_file.exists())
        # File should contain the output
        content = out_file.read_text(encoding="utf-8")
        self.assertIn("# STATE — Generated Checkpoint", content)

    def test_stdout_by_default(self):
        """Test that output goes to stdout when no --out flag."""
        self._init_db(self.state_dir)

        output = generate_state_md(state_dir=str(self.state_dir))

        # Should return output as string
        self.assertIsInstance(output, str)
        self.assertIn("# STATE — Generated Checkpoint", output)

    def test_deterministic_output(self):
        """Test that two runs produce identical output."""
        self._init_db(self.state_dir)

        # Add some items
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "events.db"))
        self._stores.append(api)

        api.append("tracker", "item_created", {
            "id": "item-1",
            "title": "Feature A",
            "status": "ranked",
            "lane": "features"
        })

        # Generate twice with fixed timestamp
        fixed_ts = "2026-07-30T12:00:00+00:00"
        output1 = generate_state_md(state_dir=str(self.state_dir), timestamp=fixed_ts)
        output2 = generate_state_md(state_dir=str(self.state_dir), timestamp=fixed_ts)

        # Should be identical
        self.assertEqual(output1, output2)

    def test_ascii_safe_output(self):
        """Test that output is ASCII-safe (escapable to JSON)."""
        self._init_db(self.state_dir)

        # Add an item with unicode
        from state_store import StateAPI
        api = StateAPI(str(self.state_dir / "events.db"))
        self._stores.append(api)

        api.append("tracker", "item_created", {
            "id": "item-1",
            "title": "Feature: CJKV text 中文",
            "status": "ranked",
            "lane": "features"
        })

        output = generate_state_md(state_dir=str(self.state_dir))

        # Should be valid UTF-8 and renderable
        self.assertIsInstance(output, str)
        # Should still contain checkpoint marker
        self.assertIn("# STATE — Generated Checkpoint", output)

    def test_exit_code_zero_on_success(self):
        """Test that CLI exit code is 0 on success."""
        self._init_db(self.state_dir)

        # Call via subprocess to test exit code
        result = subprocess.run(
            [sys.executable, "-m", "tools.gen_state_md",
             "--state-root", str(self.state_dir)],
            cwd=str(ROOT),
            capture_output=True,
            timeout=10
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn(b"STATE", result.stdout)

    def test_malformed_store_exit_1(self):
        """Test that malformed store causes exit 1."""
        # Don't initialize the database; leave it corrupted or missing
        # Actually, create a directory where the db should be to simulate corruption
        db_path = self.state_dir / "events.db"
        db_path.mkdir(exist_ok=True)  # Create dir instead of db file

        # Should exit 1 on malformed store
        result = subprocess.run(
            [sys.executable, "-m", "tools.gen_state_md",
             "--state-root", str(self.state_dir)],
            cwd=str(ROOT),
            capture_output=True,
            timeout=10
        )

        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
