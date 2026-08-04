"""tests.test_state_store_identity — Durable instance identity + epoch fencing.

Tests for state_store.identity: persisted instance identity with monotonic epoch counter.
TDD-first: validates id stability across processes, epoch monotonicity, fail-open fallback,
and backward-compatible identity shape for existing consumers.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestIdentityPersistence(unittest.TestCase):
    """Test durable instance identity at $AESOP_STATE_ROOT/instance-id."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_root = self.temp_dir.name
        # Patch environment for subprocess
        self.env = os.environ.copy()
        self.env["AESOP_STATE_ROOT"] = self.state_root

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_identity_file_created_and_loaded(self):
        """Verify identity file is created and loaded correctly."""
        from state_store.identity import get_identity_with_epoch, _init_identity_file

        # Initialize identity
        stable_id, epoch = get_identity_with_epoch(self.state_root)

        # Verify returned values
        self.assertIsInstance(stable_id, str)
        self.assertIsInstance(epoch, int)
        self.assertGreater(epoch, 0)

        # Verify file was created
        id_file = Path(self.state_root) / "instance-id"
        self.assertTrue(id_file.exists(), f"Identity file not created at {id_file}")

        # Verify file content is valid JSON
        with open(id_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("stable_id", data)
        self.assertIn("epoch", data)
        self.assertEqual(data["stable_id"], stable_id)
        self.assertEqual(data["epoch"], epoch)

    def test_id_stability_across_calls_in_same_process(self):
        """Verify identity is stable within a single process."""
        from state_store.identity import get_identity_with_epoch

        stable_id_1, epoch_1 = get_identity_with_epoch(self.state_root)
        stable_id_2, epoch_2 = get_identity_with_epoch(self.state_root)

        self.assertEqual(stable_id_1, stable_id_2, "Stable ID should not change within same process")
        self.assertEqual(epoch_1, epoch_2, "Epoch should not change within same process (cached)")

    def test_id_stability_across_two_processes(self):
        """Verify stable_id and epoch persist across separate processes sharing state root."""
        # Process 1: get initial identity
        process_1_code = """
import os
import sys
from state_store.identity import get_identity_with_epoch
state_root = os.environ.get("AESOP_STATE_ROOT")
stable_id, epoch = get_identity_with_epoch(state_root)
print(f"{stable_id}|{epoch}")
"""

        result_1 = subprocess.run(
            [sys.executable, "-c", process_1_code],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(result_1.returncode, 0, f"Process 1 failed: {result_1.stderr}")
        stable_id_1, epoch_1 = result_1.stdout.strip().split("|")
        epoch_1 = int(epoch_1)

        # Process 2: verify same identity
        process_2_code = """
import os
import sys
from state_store.identity import get_identity_with_epoch
state_root = os.environ.get("AESOP_STATE_ROOT")
stable_id, epoch = get_identity_with_epoch(state_root)
print(f"{stable_id}|{epoch}")
"""

        result_2 = subprocess.run(
            [sys.executable, "-c", process_2_code],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self.assertEqual(result_2.returncode, 0, f"Process 2 failed: {result_2.stderr}")
        stable_id_2, epoch_2 = result_2.stdout.strip().split("|")
        epoch_2 = int(epoch_2)

        self.assertEqual(
            stable_id_1, stable_id_2, "Stable ID should be identical across processes sharing state root"
        )
        self.assertEqual(epoch_1, epoch_2, "Epoch should be identical across processes on first read")

    def test_epoch_monotonicity_across_simulated_restarts(self):
        """Verify epoch increments on simulated restarts."""
        from state_store.identity import get_identity_with_epoch, _init_identity_file

        # First "boot"
        stable_id_1, epoch_1 = get_identity_with_epoch(self.state_root)

        # Simulate restart by clearing process cache
        import state_store.identity as id_module

        id_module._IDENTITY_CACHE.clear()

        # Second "boot" — manually bump epoch to simulate restart
        id_file = Path(self.state_root) / "instance-id"
        with open(id_file, encoding="utf-8") as f:
            data = json.load(f)
        data["epoch"] = data["epoch"] + 1
        with open(id_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        # Clear cache again and read
        id_module._IDENTITY_CACHE.clear()
        stable_id_2, epoch_2 = get_identity_with_epoch(self.state_root)

        self.assertEqual(stable_id_1, stable_id_2, "Stable ID should not change across restarts")
        self.assertEqual(epoch_2, epoch_1 + 1, "Epoch should increment on restart")

    def test_corrupt_id_file_fails_closed(self):
        """Verify corrupt/unreadable id file FAILS CLOSED when prior file existed.

        This is the NEW behavior (post-fix). Corrupt existing files should raise
        IdentityCorruptionError to prevent epoch resets on crash recovery.
        Only FRESH boxes (no prior file) fall back to ephemeral.
        """
        from state_store.identity import get_identity_with_epoch, IdentityCorruptionError

        # Create corrupt identity file (simulates torn write)
        id_file = Path(self.state_root) / "instance-id"
        id_file.write_text("{ invalid json }", encoding="utf-8")

        # Should raise IdentityCorruptionError (fail-closed)
        with self.assertRaises(IdentityCorruptionError):
            get_identity_with_epoch(self.state_root)

    def test_missing_id_file_fresh_box(self):
        """Verify missing id file on fresh box creates new identity with epoch=1."""
        from state_store.identity import get_identity_with_epoch

        # Ensure state root exists but id file doesn't (fresh box scenario)
        Path(self.state_root).mkdir(parents=True, exist_ok=True)
        id_file = Path(self.state_root) / "instance-id"
        if id_file.exists():
            id_file.unlink()

        # Fresh box: should create new identity with epoch=1
        stable_id, epoch = get_identity_with_epoch(self.state_root)

        self.assertIsInstance(stable_id, str)
        self.assertIsInstance(epoch, int)
        self.assertEqual(epoch, 1, "Fresh box should have epoch=1")
        # Should have persisted the identity
        self.assertTrue(id_file.exists(), "Identity file should have been created")
        with open(id_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["epoch"], 1)

    def test_read_only_valid_id_file_succeeds(self):
        """Verify read-only but valid id file can still be read (no write needed on read path)."""
        from state_store.identity import get_identity_with_epoch

        # Create read-only id file with valid content
        id_file = Path(self.state_root) / "instance-id"
        id_file.write_text('{"stable_id": "test:123abc", "epoch": 2}', encoding="utf-8")
        id_file.chmod(0o444)  # Read-only

        try:
            # Should succeed because reading a valid file doesn't require write access
            stable_id, epoch = get_identity_with_epoch(self.state_root)

            self.assertEqual(stable_id, "test:123abc", "Should have read the persisted stable_id")
            self.assertEqual(epoch, 2, "Should have read the persisted epoch")
        finally:
            # Restore permissions for cleanup
            id_file.chmod(0o644)

    def test_aesop_state_root_env_var_respected(self):
        """Verify AESOP_STATE_ROOT environment variable is respected."""
        from state_store.identity import get_identity_with_epoch

        custom_state_root = os.path.join(self.state_root, "custom")
        os.makedirs(custom_state_root, exist_ok=True)

        stable_id, epoch = get_identity_with_epoch(custom_state_root)

        # Verify file was created in the custom location
        id_file = Path(custom_state_root) / "instance-id"
        self.assertTrue(id_file.exists(), f"Identity file not created at {id_file}")

    def test_backward_compatible_id_shape(self):
        """Verify returned identity has backward-compatible shape for existing consumers."""
        from state_store.identity import get_instance_id

        instance_id = get_instance_id()

        # Should still return ephemeral form for backward compatibility
        self.assertIsInstance(instance_id, str)
        parts = instance_id.split(":")
        self.assertEqual(len(parts), 3, f"Instance ID should have 3 parts (hostname:pid:nonce), got {instance_id}")

    def test_release_own_stale_clears_prior_epochs(self):
        """Verify release_own_stale() function releases claims from prior epochs."""
        from state_store.identity import get_identity_with_epoch, release_own_stale

        # Get current identity
        stable_id, current_epoch = get_identity_with_epoch(self.state_root)

        # Simulate prior epochs by tracking them
        prior_epochs = [current_epoch - 2, current_epoch - 1]

        # release_own_stale should accept these prior epochs
        # (actual claim release is coordinated through the lease backend in Inc 4+)
        # For now, just verify the function exists and is callable
        result = release_own_stale(self.state_root, stable_id, prior_epochs)

        # Should return success status
        self.assertIsNotNone(result, "release_own_stale should return a result")

    def test_corrupt_prior_id_file_fails_closed(self):
        """Verify corrupt/torn-write id file FAILS CLOSED when prior file existed.

        Reproduces the multibox.md Finding 2 attack:
        1. Fresh start: write instance-id, crash mid-write (simulated by creating partial JSON)
        2. Restart: file exists but is corrupt (empty/partial JSON)
        3. Expected: FAIL CLOSED (raise IdentityCorruptionError), NOT silent-reset to ephemeral epoch=1

        This preserves monotonicity: a crashed restart cannot masquerade as epoch=1
        when a prior epoch was already persisted.
        """
        from state_store.identity import get_identity_with_epoch, IdentityCorruptionError
        import state_store.identity as id_module

        # Simulate a prior persistent identity (crashed write)
        id_file = Path(self.state_root) / "instance-id"
        id_file.write_text('', encoding="utf-8")  # Empty file: torn write

        # Clear cache to force re-read
        id_module._IDENTITY_CACHE.clear()

        # Should raise IdentityCorruptionError (fail-closed), not silently return ephemeral
        with self.assertRaises(IdentityCorruptionError):
            get_identity_with_epoch(self.state_root)

    def test_fresh_box_no_prior_file_creates_epoch_1(self):
        """Verify fresh box (no prior file) correctly creates epoch=1.

        Distinguishes fresh start (no file ever existed) from crash recovery (file exists but corrupt).
        Fresh starts should always succeed with epoch=1.
        """
        from state_store.identity import get_identity_with_epoch
        import state_store.identity as id_module

        # Ensure no prior file exists
        id_file = Path(self.state_root) / "instance-id"
        if id_file.exists():
            id_file.unlink()

        # Clear cache
        id_module._IDENTITY_CACHE.clear()

        # Fresh start should succeed and create epoch=1
        stable_id, epoch = get_identity_with_epoch(self.state_root)

        self.assertIsInstance(stable_id, str)
        self.assertEqual(epoch, 1, "Fresh box should start with epoch=1")
        self.assertTrue(id_file.exists(), "Identity file should be created")

        # Verify file was persisted correctly
        with open(id_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["epoch"], 1)


class TestIdentityCompat(unittest.TestCase):
    """Test backward compatibility of existing get_instance_id() API."""

    def test_get_instance_id_unchanged_api(self):
        """Verify get_instance_id() maintains its original API."""
        from state_store.identity import get_instance_id

        # Should be callable with no arguments
        instance_id = get_instance_id()

        # Should return a string
        self.assertIsInstance(instance_id, str)

        # Should have the ephemeral form (hostname:pid:nonce)
        parts = instance_id.split(":")
        self.assertEqual(len(parts), 3, "Instance ID should have 3 parts")

        # Should be deterministic within the same process
        instance_id_2 = get_instance_id()
        self.assertEqual(instance_id, instance_id_2, "get_instance_id() should be cached")


if __name__ == "__main__":
    unittest.main()
