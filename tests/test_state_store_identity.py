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
from unittest import mock


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
        # Deep-scan B3: this previously asserted the epochs were EQUAL, which is the bug
        # stated as a requirement — if two live processes share a fencing token, the token
        # fences nothing. The stable_id is what persists across processes; the epoch is
        # what distinguishes them.
        self.assertEqual(
            epoch_2,
            epoch_1 + 1,
            "Each acquiring process must get a strictly higher epoch (fencing token)",
        )

    def test_epoch_monotonicity_across_simulated_restarts(self):
        """Verify epoch increments on simulated restarts — WITHOUT the test doing the bump.

        Deep-scan B3: the prior version of this test hand-edited the epoch in the
        identity file and then asserted the file it had just written. Production
        never incremented anything, so `get_identity_with_epoch` returned 1 forever
        and epoch could not distinguish a pre-crash from a post-crash instance.
        """
        from state_store.identity import get_identity_with_epoch
        import state_store.identity as id_module

        # First "boot"
        stable_id_1, epoch_1 = get_identity_with_epoch(self.state_root)

        # Simulate restart: clear the per-process cache ONLY. Production must do
        # the incrementing; the test must not touch the identity file.
        id_module._IDENTITY_CACHE.clear()
        stable_id_2, epoch_2 = get_identity_with_epoch(self.state_root)

        self.assertEqual(stable_id_1, stable_id_2, "Stable ID should not change across restarts")
        self.assertEqual(epoch_2, epoch_1 + 1, "Epoch should increment on restart")

    def test_epoch_strictly_increases_over_many_restarts(self):
        """Deep-scan B3: epoch is a real monotonic boot counter, not a constant 1."""
        from state_store.identity import get_identity_with_epoch
        import state_store.identity as id_module

        seen = []
        stable_ids = set()
        for _ in range(5):
            id_module._IDENTITY_CACHE.clear()
            stable_id, epoch = get_identity_with_epoch(self.state_root)
            seen.append(epoch)
            stable_ids.add(stable_id)

        self.assertEqual(seen, [1, 2, 3, 4, 5], f"Epoch must strictly increase, got {seen}")
        self.assertEqual(len(stable_ids), 1, "Stable ID must not change across restarts")

    def test_epoch_is_cached_within_a_single_process(self):
        """Epoch must NOT increment on every call — only on acquisition (per process)."""
        from state_store.identity import get_identity_with_epoch

        _, epoch_1 = get_identity_with_epoch(self.state_root)
        _, epoch_2 = get_identity_with_epoch(self.state_root)
        _, epoch_3 = get_identity_with_epoch(self.state_root)

        self.assertEqual(epoch_1, epoch_2)
        self.assertEqual(epoch_2, epoch_3)

    def test_incremented_epoch_is_persisted_to_disk(self):
        """The bumped epoch must be durable, or the next boot reuses a live epoch."""
        from state_store.identity import get_identity_with_epoch
        import state_store.identity as id_module

        get_identity_with_epoch(self.state_root)
        id_module._IDENTITY_CACHE.clear()
        _, epoch = get_identity_with_epoch(self.state_root)

        id_file = Path(self.state_root) / "instance-id"
        with open(id_file, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["epoch"], epoch, "On-disk epoch must match the returned epoch")
        self.assertEqual(data["epoch"], 2)

    def test_epoch_persist_failure_fails_closed(self):
        """If the bumped epoch cannot be persisted, startup must FAIL, not reuse the epoch.

        Silently returning the un-bumped epoch would hand a restarted instance the
        same fencing token its pre-crash self is still using.
        """
        from state_store.identity import get_identity_with_epoch, EpochPersistError
        import state_store.identity as id_module

        get_identity_with_epoch(self.state_root)
        id_module._IDENTITY_CACHE.clear()

        with mock.patch.object(
            id_module, "_write_identity_atomic", side_effect=OSError("disk full")
        ):
            with self.assertRaises(EpochPersistError):
                get_identity_with_epoch(self.state_root)

    def test_epoch_persist_error_is_an_identity_corruption_error(self):
        """EpochPersistError subclasses IdentityCorruptionError (fail-closed callers keep working)."""
        from state_store.identity import EpochPersistError, IdentityCorruptionError

        self.assertTrue(issubclass(EpochPersistError, IdentityCorruptionError))

    def test_non_integer_epoch_fails_closed(self):
        """A non-integer epoch cannot be incremented monotonically: fail closed."""
        from state_store.identity import get_identity_with_epoch, IdentityCorruptionError
        import state_store.identity as id_module

        id_file = Path(self.state_root) / "instance-id"
        id_file.write_text('{"stable_id": "host:abc", "epoch": "two"}', encoding="utf-8")
        id_module._IDENTITY_CACHE.clear()

        with self.assertRaises(IdentityCorruptionError):
            get_identity_with_epoch(self.state_root)

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

    def test_existing_id_file_is_loaded_and_bumped(self):
        """A valid existing identity file is loaded, and its epoch is bumped on acquisition.

        Replaces test_read_only_valid_id_file_succeeds, whose premise ("no write is
        needed on the read path") is exactly what deep-scan B3 identified as the bug:
        an acquisition that never writes can never advance the fencing token. The
        write-failure branch is covered deterministically by
        test_epoch_persist_failure_fails_closed (chmod-based coverage is unreliable:
        it is a no-op for root on Linux CI).
        """
        from state_store.identity import get_identity_with_epoch

        id_file = Path(self.state_root) / "instance-id"
        id_file.write_text('{"stable_id": "test:123abc", "epoch": 2}', encoding="utf-8")

        stable_id, epoch = get_identity_with_epoch(self.state_root)

        self.assertEqual(stable_id, "test:123abc", "Should have read the persisted stable_id")
        self.assertEqual(epoch, 3, "Acquisition must bump the persisted epoch 2 -> 3")
        with open(id_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["epoch"], 3)

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

    def test_release_own_stale_raises_not_implemented(self):
        """Deep-scan B3: the reclamation stub must not claim success it never performed.

        release_own_stale() previously returned an unconditional True while doing
        nothing, so a caller that trusted it believed prior-epoch claims had been
        reclaimed and would proceed to write files still leased by its pre-crash
        self. Until the lease-backend coordination lands (Inc 5), it fails loudly.
        """
        from state_store.identity import get_identity_with_epoch, release_own_stale

        stable_id, current_epoch = get_identity_with_epoch(self.state_root)

        with self.assertRaises(NotImplementedError):
            release_own_stale(self.state_root, stable_id, [current_epoch - 1])

    def test_release_own_stale_raises_even_for_empty_epoch_list(self):
        """No silent 'success' path: an empty list must not read as 'reclaimed'."""
        from state_store.identity import get_identity_with_epoch, release_own_stale

        stable_id, _ = get_identity_with_epoch(self.state_root)
        with self.assertRaises(NotImplementedError):
            release_own_stale(self.state_root, stable_id, [])

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
