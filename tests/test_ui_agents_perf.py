"""Perf tests for ui/agents.py _transcripts_fingerprint() caching.

Tests that fingerprint caching reduces glob() calls from ~1Hz to once per
cache TTL (5s default). Measures that:
  1. Fingerprint is returned from cache within the TTL window (glob not re-run)
  2. Fingerprint refreshes after the TTL expires (glob re-run)
  3. Cache can be cleared for testing (reset to None + 0.0)

Run: python -m unittest tests.test_ui_agents_perf
     python tests/test_ui_agents_perf.py
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config
import agents

ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class FingerprintCachingCase(unittest.TestCase):
    """Test _transcripts_fingerprint() caching behavior."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-fingerprint-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)

        # Create transcripts dir structure
        self.transcripts_root = self.fixture_root / "transcripts"
        self.transcripts_root.mkdir(parents=True)

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.transcripts_root)
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "1.0"

        # Reload config to pick up new env vars
        config.reload()

        # Clear the fingerprint cache for a clean test
        agents._FINGERPRINT_CACHE["value"] = None
        agents._FINGERPRINT_CACHE["expires"] = 0.0

        # Use short TTL for testing (0.5s instead of 5s)
        self._original_ttl = agents._FINGERPRINT_CACHE_TTL
        agents._FINGERPRINT_CACHE_TTL = 0.5

    def tearDown(self):
        """Restore original env and clean up temp files."""
        # Restore original TTL
        agents._FINGERPRINT_CACHE_TTL = self._original_ttl

        # Clear the fingerprint cache
        agents._FINGERPRINT_CACHE["value"] = None
        agents._FINGERPRINT_CACHE["expires"] = 0.0

        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.reload()
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def _create_dummy_agent(self, agent_name):
        """Create a dummy agent transcript file."""
        project_dir = self.transcripts_root / "test-project"
        subagents_dir = project_dir / "subagents"
        subagents_dir.mkdir(parents=True, exist_ok=True)

        agent_file = subagents_dir / f"agent-{agent_name}.jsonl"
        # Write a minimal NDJSON transcript
        content = '{"type":"user","content":"test"}\n{"type":"assistant","content":"response"}\n'
        agent_file.write_text(content, encoding='utf-8')
        return agent_file

    def test_fingerprint_cached_within_window(self):
        """Fingerprint should be returned from cache within TTL without re-globbing."""
        self._create_dummy_agent("test-1")

        # Patch Path.glob to track calls
        original_glob = Path.glob
        glob_call_count = {"count": 0}

        def counting_glob(self, pattern):
            if pattern == "**/agent-*.jsonl":
                glob_call_count["count"] += 1
            return original_glob(self, pattern)

        with patch.object(Path, "glob", counting_glob):
            # First call should trigger a glob
            result1 = agents._transcripts_fingerprint()
            self.assertEqual(glob_call_count["count"], 1)
            self.assertEqual(result1, (1, result1[1]))  # 1 file

            # Immediately call again (within TTL window) — glob should NOT be called again
            result2 = agents._transcripts_fingerprint()
            self.assertEqual(glob_call_count["count"], 1, "Glob should not be re-run within TTL")
            self.assertEqual(result1, result2, "Cached result should be identical")

    def test_fingerprint_refreshes_after_ttl(self):
        """After TTL expires, fingerprint should be recomputed."""
        self._create_dummy_agent("test-2a")

        # Patch glob to count calls
        original_glob = Path.glob
        glob_call_count = {"count": 0}

        def counting_glob(self, pattern):
            if pattern == "**/agent-*.jsonl":
                glob_call_count["count"] += 1
            return original_glob(self, pattern)

        with patch.object(Path, "glob", counting_glob):
            # First call
            result1 = agents._transcripts_fingerprint()
            self.assertEqual(glob_call_count["count"], 1)

            # Wait for TTL to expire (using the short test TTL of 0.5s)
            time.sleep(0.6)

            # Second call after TTL expiration should re-glob
            result2 = agents._transcripts_fingerprint()
            self.assertEqual(glob_call_count["count"], 2, "Glob should be re-run after TTL expiration")

    def test_fingerprint_detects_new_file(self):
        """Fingerprint should detect new files after cache refresh."""
        self._create_dummy_agent("test-3a")

        # First call establishes cache
        result1 = agents._transcripts_fingerprint()
        self.assertEqual(result1[0], 1)  # 1 file

        # Add another file
        self._create_dummy_agent("test-3b")

        # Within TTL: should still see 1 file (cached)
        result2 = agents._transcripts_fingerprint()
        self.assertEqual(result2[0], 1, "Should return cached result within TTL")
        self.assertEqual(result1, result2, "Cached values should match")

        # Wait for TTL to expire
        time.sleep(0.6)

        # After TTL: should see 2 files (re-globbed)
        result3 = agents._transcripts_fingerprint()
        self.assertEqual(result3[0], 2, "After cache refresh, should detect the new file")

    def test_multiple_rapid_calls_use_cache(self):
        """Multiple rapid calls should reuse cache without re-globbing."""
        self._create_dummy_agent("test-4")

        original_glob = Path.glob
        glob_call_count = {"count": 0}

        def counting_glob(self, pattern):
            if pattern == "**/agent-*.jsonl":
                glob_call_count["count"] += 1
            return original_glob(self, pattern)

        with patch.object(Path, "glob", counting_glob):
            # Simulate collector loop: 10 calls within the TTL window
            for i in range(10):
                result = agents._transcripts_fingerprint()
                if i == 0:
                    first_result = result

            # All 10 calls should use the cached result (glob called only once)
            self.assertEqual(glob_call_count["count"], 1, "Glob should be called only once across 10 calls")

            # Verify all results are identical
            result_final = agents._transcripts_fingerprint()
            self.assertEqual(result_final, first_result)

    def test_cache_initialized_correctly(self):
        """Cache should be properly initialized on first call."""
        # Verify cache starts empty
        self.assertIsNone(agents._FINGERPRINT_CACHE["value"])
        self.assertEqual(agents._FINGERPRINT_CACHE["expires"], 0.0)

        self._create_dummy_agent("test-5")

        # First call should initialize cache
        result = agents._transcripts_fingerprint()
        self.assertIsNotNone(agents._FINGERPRINT_CACHE["value"])
        self.assertGreater(agents._FINGERPRINT_CACHE["expires"], time.time())
        self.assertEqual(agents._FINGERPRINT_CACHE["value"], result)

    def test_empty_transcripts_root(self):
        """Fingerprint should handle empty transcripts root gracefully."""
        # Don't create any agent files
        result = agents._transcripts_fingerprint()
        self.assertEqual(result, (0, 0.0))

        # Call again (should use cache)
        result2 = agents._transcripts_fingerprint()
        self.assertEqual(result2, (0, 0.0))

    def test_cache_respects_config_ttl(self):
        """Cache TTL should be configurable via _FINGERPRINT_CACHE_TTL."""
        # Use a longer TTL for this test
        agents._FINGERPRINT_CACHE_TTL = 2.0
        agents._FINGERPRINT_CACHE["value"] = None
        agents._FINGERPRINT_CACHE["expires"] = 0.0

        self._create_dummy_agent("test-6")

        original_glob = Path.glob
        glob_call_count = {"count": 0}

        def counting_glob(self, pattern):
            if pattern == "**/agent-*.jsonl":
                glob_call_count["count"] += 1
            return original_glob(self, pattern)

        with patch.object(Path, "glob", counting_glob):
            # First call
            result1 = agents._transcripts_fingerprint()
            self.assertEqual(glob_call_count["count"], 1)

            # Wait 0.5s (less than TTL)
            time.sleep(0.5)

            # Should still use cache
            result2 = agents._transcripts_fingerprint()
            self.assertEqual(glob_call_count["count"], 1)

            # Wait another 1.6s (total 2.1s, > 2.0s TTL)
            time.sleep(1.6)

            # Now should re-glob
            result3 = agents._transcripts_fingerprint()
            self.assertEqual(glob_call_count["count"], 2)

        # Restore short test TTL
        agents._FINGERPRINT_CACHE_TTL = 0.5

    def test_fingerprint_mtime_tracking(self):
        """Fingerprint should correctly track max mtime of files."""
        self._create_dummy_agent("test-7a")
        time.sleep(0.1)
        self._create_dummy_agent("test-7b")

        result = agents._transcripts_fingerprint()
        self.assertEqual(result[0], 2)  # 2 files
        self.assertGreater(result[1], 0.0)  # mtime should be positive

        # The mtime should be at least from the second file creation
        # (it should be the max of the two)
        self.assertIsInstance(result[1], float)

    def test_uncached_function_always_globs(self):
        """_transcripts_fingerprint_uncached() should never use cache."""
        self._create_dummy_agent("test-8a")

        original_glob = Path.glob
        glob_call_count = {"count": 0}

        def counting_glob(self, pattern):
            if pattern == "**/agent-*.jsonl":
                glob_call_count["count"] += 1
            return original_glob(self, pattern)

        with patch.object(Path, "glob", counting_glob):
            # Call uncached version multiple times
            for i in range(3):
                result = agents._transcripts_fingerprint_uncached()

            # Should glob 3 times (no caching)
            self.assertEqual(glob_call_count["count"], 3,
                           "Uncached version should glob every call")

    def test_cache_persists_across_calls_within_ttl(self):
        """Cache should persist the exact same tuple object across calls."""
        self._create_dummy_agent("test-9")

        # First call
        result1 = agents._transcripts_fingerprint()
        cached_value_1 = agents._FINGERPRINT_CACHE["value"]

        # Second call (within TTL)
        result2 = agents._transcripts_fingerprint()
        cached_value_2 = agents._FINGERPRINT_CACHE["value"]

        # Should be the same cached tuple object
        self.assertIs(result1, result2,
                     "Both calls should return the same cached tuple object")
        self.assertIs(cached_value_1, cached_value_2,
                     "Cache should preserve the exact tuple object")


if __name__ == "__main__":
    unittest.main()
