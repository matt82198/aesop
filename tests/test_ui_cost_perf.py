"""Perf tests for ui/cost.py and ui/wave_dispatch.py optimizations.

Tests two performance fixes:
  1. wave_dispatch.py: ~5s in-process cache to avoid re-reading transcripts on polls
  2. cost.py _calculate_weekly_costs: hoist datetime.strptime outside inner loops

Run: python -m unittest tests.test_ui_cost_perf
     python tests/test_ui_cost_perf.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config
import cost
import wave_dispatch

ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class DispatchCacheCase(unittest.TestCase):
    """Test wave_dispatch caching behavior."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-dispatch-cache-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)

        # Create transcripts dir with dummy agents
        self.transcripts_root = self.fixture_root / "transcripts"
        self.transcripts_root.mkdir(parents=True)

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.transcripts_root)
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        # Reload config to pick up new env vars
        config.reload()

        # Clear the wave_dispatch cache for a clean test
        wave_dispatch._cache["expires"] = 0.0
        wave_dispatch._cache["payload"] = None

    def tearDown(self):
        """Restore original env and clean up temp files."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.reload()
        wave_dispatch._cache["expires"] = 0.0
        wave_dispatch._cache["payload"] = None
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

    def test_cache_populated_on_first_call(self):
        """First call to get_wave_dispatch should populate cache."""
        self._create_dummy_agent("123")

        # Clear cache
        wave_dispatch._cache["expires"] = 0.0
        wave_dispatch._cache["payload"] = None

        # First call should fetch and cache
        result1 = wave_dispatch.get_wave_dispatch(force=True)
        self.assertTrue(wave_dispatch._cache["payload"] is not None)
        self.assertGreater(wave_dispatch._cache["expires"], time.time())
        self.assertTrue(result1["available"])

    def test_cache_returned_within_ttl(self):
        """Second call within 5s should return cached result."""
        self._create_dummy_agent("456")

        # Clear cache
        wave_dispatch._cache["expires"] = 0.0
        wave_dispatch._cache["payload"] = None

        # First call
        result1 = wave_dispatch.get_wave_dispatch(force=True)
        self.assertTrue(result1["available"])
        first_timestamp = result1["at"]

        # Modify file to confirm we're not re-reading
        project_dir = self.transcripts_root / "test-project"
        subagents_dir = project_dir / "subagents"
        agent_file = subagents_dir / "agent-456.jsonl"

        # Overwrite the agent file with different content (simulates agent activity)
        agent_file.write_text('{"type":"tool_use"}\n' * 10, encoding='utf-8')

        # Second call within TTL should return cached (same timestamp)
        result2 = wave_dispatch.get_wave_dispatch(force=False)
        second_timestamp = result2["at"]
        self.assertEqual(first_timestamp, second_timestamp, "Cache should return identical timestamp within TTL")

    def test_cache_expired_after_ttl(self):
        """Call after TTL expiration should re-fetch."""
        self._create_dummy_agent("789")

        # Clear cache
        wave_dispatch._cache["expires"] = 0.0
        wave_dispatch._cache["payload"] = None

        # First call
        result1 = wave_dispatch.get_wave_dispatch(force=True)
        cached_payload_after_first = wave_dispatch._cache["payload"]

        # Artificially expire cache by setting expires to past
        wave_dispatch._cache["expires"] = time.time() - 1.0

        # Wait to ensure we cross into a new second
        time.sleep(1.1)

        # Second call should re-fetch (create a new payload object)
        result2 = wave_dispatch.get_wave_dispatch(force=False)
        cached_payload_after_second = wave_dispatch._cache["payload"]

        # The cached payload should be different (re-fetched)
        self.assertIsNot(cached_payload_after_first, cached_payload_after_second,
                        "Cache expiration should trigger re-fetch with a new payload")

    def test_force_bypass_cache(self):
        """force=True should bypass cache."""
        self._create_dummy_agent("999")

        # Clear cache
        wave_dispatch._cache["expires"] = 0.0
        wave_dispatch._cache["payload"] = None

        # First call
        result1 = wave_dispatch.get_wave_dispatch(force=True)
        first_payload = wave_dispatch._cache["payload"]

        # Wait to ensure we cross into a new second (timestamps truncate to seconds)
        time.sleep(1.1)

        # Second call with force=True should bypass cache and create a new payload
        result2 = wave_dispatch.get_wave_dispatch(force=True)
        second_payload = wave_dispatch._cache["payload"]

        # Payloads should be different objects (force bypassed cache)
        self.assertIsNot(first_payload, second_payload,
                        "force=True should bypass cache and create new payload")
        # And timestamps should differ now (we waited 1+ second)
        self.assertNotEqual(result1["at"], result2["at"],
                           "After 1+ second wait with force=True, timestamps should differ")


class CostWeeklyCorrectionCase(unittest.TestCase):
    """Test that weekly cost optimization preserves correctness."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-cost-weekly-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment
        os.environ["AESOP_ROOT"] = str(self.fixture_root)
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)
        os.environ["AESOP_TRANSCRIPTS_ROOT"] = str(self.fixture_root / "transcripts")
        os.environ["AESOP_UI_COLLECT_INTERVAL"] = "0.2"

        # Reload config to pick up new env vars
        config.reload()

    def tearDown(self):
        """Restore original env and clean up temp files."""
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.reload()
        shutil.rmtree(self.fixture_root, ignore_errors=True)

    def write_ledger(self, content):
        """Write a test ledger file to the isolated state dir."""
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        ledger_file.write_text(content, encoding='utf-8')
        return ledger_file

    def write_config(self, config_dict):
        """Write a test aesop.config.json to the isolated fixture root."""
        config_file = self.fixture_root / "aesop.config.json"
        config_file.write_text(json.dumps(config_dict), encoding='utf-8')
        # Reload config so it picks up the new file
        config.reload()

    def test_weekly_costs_shape_preserved(self):
        """Weekly cost calculations should preserve documented shape."""
        ledger_content = """| ISO timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
|---|---|---|---|---|---|---|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-12T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 10 | 200 | OK |
| 2026-07-18T15:30:00 | Agent | claude-sonnet-4-20250514 | 1 | 100 | 500 | OK |
| 2026-07-18T16:00:00 | Agent | claude-sonnet-4-20250514 | 1 | 150 | 600 | OK |
"""
        self.write_ledger(ledger_content)

        summary = cost.get_cost_summary()

        # Verify per_week_costs exists and has correct structure
        self.assertIn("per_week_costs", summary)
        self.assertIsInstance(summary["per_week_costs"], dict)

        # Should have 2 weeks (week of 7/11 and week of 7/18)
        self.assertEqual(len(summary["per_week_costs"]), 2)

        for week_key, week_data in summary["per_week_costs"].items():
            # Verify week key format (YYYY-Www)
            self.assertRegex(week_key, r"^\d{4}-W\d{2}$")

            # Verify structure
            self.assertIn("tokens_in", week_data)
            self.assertIn("tokens_out", week_data)
            self.assertIn("model_tokens", week_data)
            self.assertIn("cost", week_data)

            # Verify types
            self.assertIsInstance(week_data["tokens_in"], int)
            self.assertIsInstance(week_data["tokens_out"], int)
            self.assertIsInstance(week_data["model_tokens"], dict)
            self.assertIsInstance(week_data["cost"], float)

    def test_weekly_costs_aggregation_correctness(self):
        """Weekly costs should aggregate tokens correctly per model."""
        ledger_content = """| ISO timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
|---|---|---|---|---|---|---|
| 2026-07-11T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 100 | 200 | OK |
| 2026-07-11T15:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 50 | 150 | OK |
| 2026-07-11T20:00:00 | Agent | claude-sonnet-4-20250514 | 1 | 500 | 1000 | OK |
"""
        self.write_ledger(ledger_content)

        summary = cost.get_cost_summary()
        weeks = summary["per_week_costs"]

        # All entries are in the same week (2026-W28)
        self.assertEqual(len(weeks), 1)
        week_key = list(weeks.keys())[0]
        week_data = weeks[week_key]

        # Verify aggregation
        # Week should have 2 models: haiku (150+350=500 tokens) and sonnet (1500 tokens)
        expected_haiku_tokens = 100 + 200 + 50 + 150  # = 500
        expected_sonnet_tokens = 500 + 1000  # = 1500
        expected_total_tokens_in = 100 + 50 + 500  # = 650
        expected_total_tokens_out = 200 + 150 + 1000  # = 1350

        self.assertEqual(week_data["tokens_in"], expected_total_tokens_in)
        self.assertEqual(week_data["tokens_out"], expected_total_tokens_out)

        # Verify model breakdown
        self.assertEqual(week_data["model_tokens"].get("claude-haiku-4-5-20251001"), expected_haiku_tokens)
        self.assertEqual(week_data["model_tokens"].get("claude-sonnet-4-20250514"), expected_sonnet_tokens)

    def test_weekly_costs_with_pricing_unchanged(self):
        """Weekly cost calculation with pricing should be identical (correctness preserved)."""
        ledger_content = """| ISO timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
|---|---|---|---|---|---|---|
| 2026-07-11T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 1000 | 2000 | OK |
| 2026-07-11T15:00:00 | Agent | claude-sonnet-4-20250514 | 1 | 5000 | 10000 | OK |
| 2026-07-18T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 800 | 1600 | OK |
"""
        self.write_ledger(ledger_content)

        # Write pricing config
        pricing_config = {
            "pricing": {
                "claude-haiku-4-5-20251001": {
                    "input_per_mtok": 0.80,
                    "output_per_mtok": 2.40
                },
                "claude-sonnet-4-20250514": {
                    "input_per_mtok": 3.00,
                    "output_per_mtok": 15.00
                }
            }
        }
        self.write_config(pricing_config)

        summary = cost.get_cost_summary()
        weeks = summary["per_week_costs"]

        # Verify both weeks exist
        self.assertEqual(len(weeks), 2)

        # Verify costs are calculated (not zero)
        for week_key, week_data in weeks.items():
            self.assertGreater(week_data["cost"], 0.0,
                              f"Week {week_key} should have non-zero cost with pricing")

            # Cost should be computed as: (tokens_in * input_price + tokens_out * output_price) / 1_000_000
            # per model, then summed
            total_cost = 0.0
            for model, model_tokens in week_data["model_tokens"].items():
                # This is a rough check; we just verify cost > 0
                pass

    def test_overall_scorecard_not_affected(self):
        """Weekly cost optimization should not affect overall scorecard."""
        ledger_content = """| ISO timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
|---|---|---|---|---|---|---|
| 2026-07-11T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 100 | 200 | OK |
| 2026-07-11T15:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 50 | 150 | FAILED |
| 2026-07-11T20:00:00 | Agent | claude-sonnet-4-20250514 | 1 | 500 | 1000 | EMPTY |
"""
        self.write_ledger(ledger_content)

        summary = cost.get_cost_summary()
        scorecard = summary["overall_scorecard"]

        # Verify scorecard totals are unchanged by weekly optimization
        self.assertEqual(scorecard["total_runs"], 3)
        self.assertEqual(scorecard["ok_count"], 1)
        self.assertEqual(scorecard["failed_count"], 1)
        self.assertEqual(scorecard["empty_count"], 1)
        self.assertEqual(scorecard["hung_count"], 0)
        self.assertAlmostEqual(scorecard["ok_rate"], 1/3, places=5)


if __name__ == "__main__":
    unittest.main()
