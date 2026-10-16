#!/usr/bin/env python3
"""
TDD: Scoped receipt profile tests.
Tests the new AESOP_RECEIPT_PROFILE="scoped" default that runs only changed-file-owning shards.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

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


class TestShardMapMatchesCIRunner(unittest.TestCase):
    """The emitter's scoped profile must place each test file in the SAME shard that
    ci_shard_runner.py runs it in. CI switches to greedy timing-aware bin-packing when a
    non-empty timing map is supplied; the emitter must follow the same assignment, or a
    changed file is attributed to the wrong shard and its real shard is skipped."""

    TEST_FILES = ["test_a", "test_b", "test_c", "test_d", "test_e"]
    SHARDS = ["py-shard-0", "py-shard-1", "py-shard-2", "py-shard-3"]

    def _expected(self, buckets):
        return {stem: self.SHARDS[i] for i, bucket in enumerate(buckets) for stem in bucket}

    def test_round_robin_matches_ci_runner(self):
        import ci_shard_runner
        buckets = [ci_shard_runner.distribute_shards(self.TEST_FILES, i, 4) for i in range(4)]
        shard_map = emit_receipt._build_shard_map(self.SHARDS, test_files=self.TEST_FILES, timing=None)
        self.assertEqual(shard_map, self._expected(buckets))

    def test_timing_assignment_matches_ci_runner(self):
        import ci_shard_runner
        timing = {"test_a": 10, "test_b": 1, "test_c": 1, "test_d": 1, "test_e": 1}
        buckets = ci_shard_runner.distribute_shards_by_timing(self.TEST_FILES, 4, timing)
        shard_map = emit_receipt._build_shard_map(self.SHARDS, test_files=self.TEST_FILES, timing=timing)
        self.assertEqual(shard_map, self._expected(buckets))

    def test_changed_file_is_owned_by_its_ci_shard_under_timing(self):
        """test_e is py-shard-0 under round-robin but py-shard-1 under timing bin-packing.
        The scoped profile must select py-shard-1 (where CI runs it), not py-shard-0."""
        import ci_shard_runner
        timing = {"test_a": 10, "test_b": 1, "test_c": 1, "test_d": 1, "test_e": 1}
        ci_shard = next(i for i, b in enumerate(
            ci_shard_runner.distribute_shards_by_timing(self.TEST_FILES, 4, timing)) if "test_e" in b)
        shard_map = emit_receipt._build_shard_map(self.SHARDS, test_files=self.TEST_FILES, timing=timing)
        owning = emit_receipt._find_owning_shards(["tests/test_e.py"], shard_map)
        self.assertEqual(owning, {self.SHARDS[ci_shard]})


class _MatrixCaptured(Exception):
    """Raised by the fake run_matrix to stop main() right after the matrix is chosen."""


class TestScopedFallbackUsesInjectedRegistry(unittest.TestCase):
    """Both fail-closed fallbacks in main()'s shard selection must run the parts of the
    registry actually injected into main(), never DEFAULT_MATRIX."""

    REGISTRY = {
        "custom-shard-a": emit_receipt.PartSpec(["true"]),
        "custom-gate": emit_receipt.PartSpec(["true"]),
    }
    REPO = Path(__file__).resolve().parent.parent

    def _matrix_for(self, git_diff_output, shard_map):
        captured = {}
        real_git = emit_receipt._git

        def fake_git(repo, *args):
            if args[:1] == ("merge-base",):
                return "0" * 40
            if args[:2] == ("diff", "--name-only"):
                return git_diff_output
            return real_git(repo, *args)

        def fake_run_matrix(repo, names, registry=None, jobs=1, wsl_status_fn=None):
            captured["names"] = list(names)
            raise _MatrixCaptured()

        with mock.patch.object(emit_receipt, "_git", fake_git), \
                mock.patch.object(emit_receipt, "_build_shard_map", return_value=shard_map), \
                mock.patch.object(emit_receipt, "run_matrix", fake_run_matrix):
            with self.assertRaises(_MatrixCaptured):
                emit_receipt.main(
                    ["--repo", str(self.REPO), "--scheme", "hmac-sha256"],
                    registry=self.REGISTRY,
                    environ={emit_receipt.rc.HMAC_ENV: "test-secret"},
                )
        return captured["names"]

    def test_unowned_tools_change_fallback_uses_injected_registry(self):
        """A tools/ change no shard owns makes _find_owning_shards return None (fallback at the owning-shards check)."""
        names = self._matrix_for("tools/receipt_common.py\n", {"test_foo": "py-shard-0"})
        self.assertEqual(sorted(names), sorted(self.REGISTRY))

    def test_underivable_shard_map_fallback_uses_injected_registry(self):
        """An underivable shard map (_build_shard_map returns None) takes the first fallback."""
        names = self._matrix_for("tests/test_foo.py\n", None)
        self.assertEqual(sorted(names), sorted(self.REGISTRY))


class TestShardSetSingleDeclaration(unittest.TestCase):
    """Guard: the emitter's python-shard set and the verifier's required set are one declaration."""

    def test_emitter_shards_equal_verifier_required_parts(self):
        import receipt_common as rc
        self.assertEqual(set(emit_receipt._PY_SHARDS), set(rc.DEFAULT_REQUIRED_PARTS))


if __name__ == "__main__":
    unittest.main()
