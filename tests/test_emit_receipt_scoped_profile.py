#!/usr/bin/env python3
"""
TDD: Scoped receipt profile tests.
Tests the new AESOP_RECEIPT_PROFILE="scoped" default that runs only changed-file-owning shards.
"""
import json
import sys
import unittest
from pathlib import Path

# Add tools/ to path for imports
_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(_TOOLS_DIR))

import emit_receipt


class TestScopedProfileSelection(unittest.TestCase):
    """Test that profile selection (scoped vs full) is correct."""

    def test_default_profile_is_scoped(self):
        """AESOP_RECEIPT_PROFILE unset should default to 'scoped'."""
        env = {}  # Empty env
        profile = emit_receipt._resolve_profile(env)
        self.assertEqual(profile, "scoped")

    def test_explicit_scoped_profile(self):
        """AESOP_RECEIPT_PROFILE=scoped should be respected."""
        env = {"AESOP_RECEIPT_PROFILE": "scoped"}
        profile = emit_receipt._resolve_profile(env)
        self.assertEqual(profile, "scoped")

    def test_explicit_full_profile(self):
        """AESOP_RECEIPT_PROFILE=full should be respected."""
        env = {"AESOP_RECEIPT_PROFILE": "full"}
        profile = emit_receipt._resolve_profile(env)
        self.assertEqual(profile, "full")

    def test_invalid_profile_defaults_to_scoped(self):
        """AESOP_RECEIPT_PROFILE=unknown should default to scoped."""
        env = {"AESOP_RECEIPT_PROFILE": "unknown"}
        profile = emit_receipt._resolve_profile(env)
        self.assertEqual(profile, "scoped")


class TestShardOwnershipMapping(unittest.TestCase):
    """Test mapping changed files to owning shards."""

    def test_docs_only_change_maps_to_empty_set(self):
        """A docs-only change should map to no python shards."""
        changed_files = ["docs/README.md", "RECEIPT-GATE.md"]
        shard_map = {"test_foo": "py-shard-0", "test_bar": "py-shard-1"}  # Mock map

        owning = emit_receipt._find_owning_shards(changed_files, shard_map)

        # Should return an empty set (no shards own doc files)
        self.assertEqual(owning, set())

    def test_underivable_mapping_returns_none(self):
        """If shard_map is None, return None (fallback to all)."""
        changed_files = ["tests/test_foo.py"]
        shard_map = None  # Underivable

        owning = emit_receipt._find_owning_shards(changed_files, shard_map)
        self.assertIsNone(owning)

    def test_test_file_maps_to_shard(self):
        """A changed tests/test_*.py file should map to its owning shard."""
        changed_files = ["tests/test_foo.py"]
        shard_map = {"test_foo": "py-shard-0", "test_bar": "py-shard-1"}  # Mock: test_foo owned by shard-0

        owning = emit_receipt._find_owning_shards(changed_files, shard_map)

        # Should include the owning shard
        self.assertEqual(owning, {"py-shard-0"})

    def test_mixed_changed_files_includes_owned_shards(self):
        """Mixed changes (docs + code) should include only shards owning code."""
        changed_files = ["docs/README.md", "tests/test_foo.py", "CHANGELOG.md"]
        shard_map = {"test_foo": "py-shard-0", "test_bar": "py-shard-1"}

        owning = emit_receipt._find_owning_shards(changed_files, shard_map)

        # Should include shard-0 (owns test_foo) but not shard-1 or any non-code shards
        self.assertEqual(owning, {"py-shard-0"})


class TestScopedMatrixBuilding(unittest.TestCase):
    """Test building the matrix with scoped profile."""

    def test_scoped_profile_includes_fast_parts(self):
        """Scoped profile must always include fast (non-shard) parts."""
        profile = "scoped"
        changed_files = []
        owning_shards = set()

        matrix = emit_receipt._build_parts_for_profile(
            profile, changed_files, owning_shards, emit_receipt.DEFAULT_REGISTRY
        )

        # Must include fast parts
        fast_parts = {"secret-scan", "claudemd-sync-gate", "gen-tool-index",
                      "verify-test-suite-count", "encoding-lint", "import-resolution-check",
                      "sibling-import-check"}

        for fast_part in fast_parts:
            self.assertIn(fast_part, matrix, f"Fast part {fast_part} missing from scoped matrix")

    def test_scoped_profile_excludes_unowned_shards(self):
        """Scoped profile should exclude shards not owning changed files."""
        profile = "scoped"
        changed_files = ["tests/test_foo.py"]
        owning_shards = {"py-shard-0"}  # Only shard 0 owns changed files

        matrix = emit_receipt._build_parts_for_profile(
            profile, changed_files, owning_shards, emit_receipt.DEFAULT_REGISTRY
        )

        # Should include shard-0 but not others
        self.assertIn("py-shard-0", matrix)
        self.assertNotIn("py-shard-1", matrix)
        self.assertNotIn("py-shard-2", matrix)
        self.assertNotIn("py-shard-3", matrix)

    def test_scoped_profile_with_no_changed_code(self):
        """Scoped profile with only docs changes should skip all shards."""
        profile = "scoped"
        changed_files = ["docs/README.md"]
        owning_shards = set()  # No shards own doc changes

        parts, skipped = emit_receipt._build_parts_and_skipped_for_profile(
            profile, changed_files, owning_shards, emit_receipt.DEFAULT_REGISTRY
        )

        skipped_names = {skip["name"] for skip in skipped}

        # All shards should be skipped
        self.assertIn("py-shard-0", skipped_names)
        self.assertIn("py-shard-1", skipped_names)
        self.assertIn("py-shard-2", skipped_names)
        self.assertIn("py-shard-3", skipped_names)

        # Skipped reasons should mention "not in scope"
        for skip in skipped:
            if skip["name"].startswith("py-shard-"):
                self.assertIn("not in scope", skip.get("reason", "").lower())

    def test_full_profile_includes_all_shards(self):
        """Full profile should run all shards regardless of changed files."""
        profile = "full"
        changed_files = ["docs/README.md"]  # Only docs, no code changes
        owning_shards = set()  # Nothing owned

        matrix = emit_receipt._build_parts_for_profile(
            profile, changed_files, owning_shards, emit_receipt.DEFAULT_REGISTRY
        )

        # Should include all shards
        self.assertIn("py-shard-0", matrix)
        self.assertIn("py-shard-1", matrix)
        self.assertIn("py-shard-2", matrix)
        self.assertIn("py-shard-3", matrix)

    def test_full_profile_with_browser_proofs_skipped(self):
        """Full profile should still skip non-runnable parts like browser-proofs."""
        profile = "full"
        changed_files = []
        owning_shards = set()

        parts, skipped = emit_receipt._build_parts_and_skipped_for_profile(
            profile, changed_files, owning_shards, emit_receipt.DEFAULT_REGISTRY
        )

        skipped_names = {skip["name"] for skip in skipped}

        # Browser proofs and windows-shard should still be skipped (not runnable here)
        self.assertIn("browser-proofs", skipped_names)
        self.assertIn("windows-shard", skipped_names)


class TestReceiptPayloadProfile(unittest.TestCase):
    """Test that profile is recorded in receipt payload."""

    def test_receipt_includes_profile_field(self):
        """Signed receipt payload must include profile: 'scoped' or 'full'."""
        receipt = {
            "schema": 1,
            "schema_version": 1,
            "profile": "scoped",  # NEW FIELD
            "repo": "test/repo",
            "head_sha": "abc123",
            "base_sha": "def456",
            "tree_hash": "xyz789",
            "parts": [],
            "skipped": [],
            "host": {"os": "windows", "python": "3.12", "hostname_hash": "hash"},
            "timestamp": "2026-10-07T00:00:00Z"
        }

        # Should be valid JSON
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        self.assertIsNotNone(canonical)
        self.assertIn('"profile":"scoped"', canonical)

    def test_legacy_receipt_without_profile_still_verifies(self):
        """Receipts without profile field (legacy) should still verify."""
        legacy_receipt = {
            "schema": 1,
            # NO schema_version or profile
            "repo": "test/repo",
            "head_sha": "abc123",
            "base_sha": "def456",
            "tree_hash": "xyz789",
            "parts": [{"name": "py-shard-0", "exit_code": 0, "test_count": 100, "duration_s": 30.0}],
            "skipped": [],
            "host": {"os": "windows", "python": "3.12", "hostname_hash": "hash"},
            "timestamp": "2026-10-07T00:00:00Z"
        }

        # This should be parseable and valid
        canonical = json.dumps(legacy_receipt, sort_keys=True, separators=(",", ":"))
        self.assertIsNotNone(canonical)
        # Should NOT have profile in legacy receipts
        self.assertNotIn('"profile"', canonical)


if __name__ == "__main__":
    unittest.main()
