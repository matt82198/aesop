"""TDD tests for ui/cost.py — ledger parsing and cost aggregation.

Tests the get_cost_summary() function which parses a markdown ledger table
and returns per-model, per-day, and overall cost/token/verdict aggregations.

Run: python -m unittest tests.test_ui_cost
     python tests/test_ui_cost.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config
import cost

# Add tools directory to path for fleet_ledger import
TOOLS_DIR = Path(__file__).parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class CostIsolationCase(unittest.TestCase):
    """Base class for cost tests with isolated temp directories."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-cost-test-"))
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


class TestCostSummaryShape(CostIsolationCase):
    """Test the structure and shape of the CostSummary return value."""

    def test_empty_ledger_returns_documented_shape(self):
        """When ledger is absent, return an empty summary with documented shape."""
        summary = cost.get_cost_summary()

        # Verify documented shape
        self.assertIsInstance(summary, dict)
        self.assertIn("models", summary)
        self.assertIn("daily_totals", summary)
        self.assertIn("overall_scorecard", summary)
        self.assertIn("skipped_lines", summary)
        self.assertIn("has_pricing", summary)
        self.assertIn("estimates_by_model", summary)

        # Verify it's empty
        self.assertEqual(len(summary["models"]), 0)
        self.assertEqual(len(summary["daily_totals"]), 0)
        self.assertEqual(summary["skipped_lines"], 0)
        self.assertFalse(summary["has_pricing"])
        self.assertEqual(len(summary["estimates_by_model"]), 0)

    def test_overall_scorecard_shape(self):
        """overall_scorecard contains verdict counters and rates."""
        summary = cost.get_cost_summary()
        scorecard = summary["overall_scorecard"]

        # Verify shape
        self.assertIn("total_runs", scorecard)
        self.assertIn("ok_count", scorecard)
        self.assertIn("failed_count", scorecard)
        self.assertIn("empty_count", scorecard)
        self.assertIn("hung_count", scorecard)
        self.assertIn("ok_rate", scorecard)
        self.assertIn("failed_rate", scorecard)
        self.assertIn("empty_rate", scorecard)
        self.assertIn("hung_rate", scorecard)

    def test_models_dict_contains_model_entries(self):
        """When ledger has entries, models dict contains per-model stats."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | OK |
| 2026-07-11T22:08:22 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | FAILED |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Verify models dict
        self.assertIn("claude-haiku-4-5-20251001", summary["models"])
        self.assertIn("claude-opus-4-8", summary["models"])

        # Verify model stats structure
        haiku_stats = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertIn("runs", haiku_stats)
        self.assertIn("tokens_in", haiku_stats)
        self.assertIn("tokens_out", haiku_stats)
        self.assertIn("verdicts", haiku_stats)

    def test_verdicts_contains_all_verdict_types(self):
        """Verdicts dict in model stats contains OK/FAILED/EMPTY/HUNG counters."""
        summary = cost.get_cost_summary()
        verdicts = summary["overall_scorecard"]

        # All verdict types should be present with numeric counts
        self.assertIsInstance(verdicts["ok_count"], int)
        self.assertIsInstance(verdicts["failed_count"], int)
        self.assertIsInstance(verdicts["empty_count"], int)
        self.assertIsInstance(verdicts["hung_count"], int)


class TestCostParsing(CostIsolationCase):
    """Test ledger parsing and aggregation logic."""

    def test_parses_valid_ledger_rows(self):
        """Parse valid markdown table rows and aggregate correctly."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 2)
        self.assertEqual(haiku["tokens_in"], 16)  # 8 + 8
        self.assertEqual(haiku["tokens_out"], 302)  # 186 + 116
        self.assertEqual(haiku["verdicts"]["OK"], 2)
        self.assertEqual(summary["skipped_lines"], 0)

    def test_aggregates_by_date(self):
        """Aggregate tokens by ISO date."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T23:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
| 2026-07-12T01:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 100 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should have two dates
        self.assertIn("2026-07-11", summary["daily_totals"])
        self.assertIn("2026-07-12", summary["daily_totals"])

        # Verify totals per day
        day1 = summary["daily_totals"]["2026-07-11"]
        self.assertEqual(day1["tokens_in"], 16)  # 8 + 8
        self.assertEqual(day1["tokens_out"], 302)  # 186 + 116

        day2 = summary["daily_totals"]["2026-07-12"]
        self.assertEqual(day2["tokens_in"], 8)
        self.assertEqual(day2["tokens_out"], 100)

    def test_counts_all_verdict_types(self):
        """Count OK, FAILED, EMPTY, and HUNG verdicts."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | FAILED |
| 2026-07-11T22:08:22 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 1 | EMPTY |
| 2026-07-11T22:08:23 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 1 | HUNG |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        scorecard = summary["overall_scorecard"]
        self.assertEqual(scorecard["total_runs"], 4)
        self.assertEqual(scorecard["ok_count"], 1)
        self.assertEqual(scorecard["failed_count"], 1)
        self.assertEqual(scorecard["empty_count"], 1)
        self.assertEqual(scorecard["hung_count"], 1)

    def test_calculates_success_rate(self):
        """Calculate success/failure rates."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
| 2026-07-11T22:08:22 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 1 | FAILED |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        scorecard = summary["overall_scorecard"]
        self.assertAlmostEqual(scorecard["ok_rate"], 2.0 / 3.0, places=2)
        self.assertAlmostEqual(scorecard["failed_rate"], 1.0 / 3.0, places=2)

    def test_skips_malformed_lines(self):
        """Skip malformed lines and count them."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| this is not a valid row at all |
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
| 2026-07-11T22:08:22 | broken | incomplete |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should have parsed only the 2 valid lines
        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 2)
        # Should count the 2 malformed lines (separator lines not counted)
        self.assertEqual(summary["skipped_lines"], 2)

    def test_handles_missing_ledger_file(self):
        """Gracefully handle missing ledger file."""
        # Don't write any ledger file
        summary = cost.get_cost_summary()

        # Should return empty summary
        self.assertEqual(len(summary["models"]), 0)
        self.assertEqual(summary["skipped_lines"], 0)
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 0)

    def test_handles_empty_ledger_file(self):
        """Gracefully handle empty ledger file."""
        self.write_ledger("")
        summary = cost.get_cost_summary()

        # Should return empty summary
        self.assertEqual(len(summary["models"]), 0)
        self.assertEqual(summary["skipped_lines"], 0)

    def test_handles_header_separator_lines(self):
        """Skip markdown table header separator lines."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should have parsed only the 2 data rows
        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 2)
        # Separator lines are silently ignored, not counted as skipped (only malformed lines count)
        self.assertEqual(summary["skipped_lines"], 0)


class TestPricingIntegration(CostIsolationCase):
    """Test pricing map loading from config and cost estimation."""

    def test_no_estimates_without_pricing_config(self):
        """When no pricing in config, has_pricing is False and no estimates."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)
        self.write_config({})
        summary = cost.get_cost_summary()

        self.assertFalse(summary["has_pricing"])
        self.assertEqual(len(summary["estimates_by_model"]), 0)

    def test_loads_pricing_from_config(self):
        """Load pricing map from aesop.config.json."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)
        self.write_config({
            "pricing": {
                "claude-haiku-4-5-20251001": {
                    "input_per_mtok": 0.80,
                    "output_per_mtok": 2.40
                }
            }
        })
        summary = cost.get_cost_summary()

        self.assertTrue(summary["has_pricing"])
        self.assertIn("claude-haiku-4-5-20251001", summary["estimates_by_model"])

    def test_calculates_costs_correctly(self):
        """Calculate estimated costs from tokens and pricing."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)
        # haiku: input_per_mtok=0.80, output_per_mtok=2.40
        # 8 tokens in: 8 / 1_000_000 * 0.80 = 0.0000064
        # 186 tokens out: 186 / 1_000_000 * 2.40 = 0.0004464
        self.write_config({
            "pricing": {
                "claude-haiku-4-5-20251001": {
                    "input_per_mtok": 0.80,
                    "output_per_mtok": 2.40
                }
            }
        })
        summary = cost.get_cost_summary()

        estimate = summary["estimates_by_model"]["claude-haiku-4-5-20251001"]
        self.assertIsInstance(estimate, dict)
        self.assertIn("input_cost", estimate)
        self.assertIn("output_cost", estimate)
        self.assertIn("total_cost", estimate)
        # Verify calculation: 8 * 0.80 + 186 * 2.40 = 6.4 + 446.4 = 452.8 (in millicents per million tokens)
        # which is 0.0000064 + 0.0004464 = 0.0005128 dollars
        expected_total = (8 * 0.80 + 186 * 2.40) / 1_000_000
        self.assertAlmostEqual(estimate["total_cost"], expected_total, places=10)

    def test_handles_missing_model_in_pricing(self):
        """Gracefully handle model not in pricing map."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | OK |
"""
        self.write_ledger(ledger)
        # Only define pricing for haiku, not opus
        self.write_config({
            "pricing": {
                "claude-haiku-4-5-20251001": {
                    "input_per_mtok": 0.80,
                    "output_per_mtok": 2.40
                }
            }
        })
        summary = cost.get_cost_summary()

        self.assertTrue(summary["has_pricing"])
        # Should have estimate for haiku
        self.assertIn("claude-haiku-4-5-20251001", summary["estimates_by_model"])
        # Should NOT have estimate for opus
        self.assertNotIn("claude-opus-4-8", summary["estimates_by_model"])

    def test_utf8_encoding_explicit(self):
        """Ensure ledger and config are read with explicit UTF-8 encoding."""
        # Create ledger with UTF-8 content
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should parse without errors
        self.assertEqual(len(summary["models"]), 1)


class TestConfigReloadAtCallTime(CostIsolationCase):
    """Test that config is read at call time, not import time."""

    def test_config_read_at_call_time(self):
        """Config paths are read at call time, so isolation works."""
        # First call without ledger
        summary1 = cost.get_cost_summary()
        self.assertEqual(len(summary1["models"]), 0)

        # Now write a ledger
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)

        # Second call should see the ledger (because config is read at call time)
        summary2 = cost.get_cost_summary()
        self.assertEqual(len(summary2["models"]), 1)
        self.assertIn("claude-haiku-4-5-20251001", summary2["models"])


class TestLargeScaleData(CostIsolationCase):
    """Test parsing and aggregation with larger datasets."""

    def test_multiple_models(self):
        """Aggregate data across multiple models correctly."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | OK |
| 2026-07-11T22:08:22 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
| 2026-07-11T22:08:23 | Agent | claude-opus-4-8 | 0 | 2 | 1021 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Verify both models present
        self.assertEqual(len(summary["models"]), 2)

        # Verify haiku aggregation
        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 2)
        self.assertEqual(haiku["tokens_in"], 16)
        self.assertEqual(haiku["tokens_out"], 302)

        # Verify opus aggregation
        opus = summary["models"]["claude-opus-4-8"]
        self.assertEqual(opus["runs"], 2)
        self.assertEqual(opus["tokens_in"], 4)
        self.assertEqual(opus["tokens_out"], 1024)

    def test_daily_aggregation_multiple_days(self):
        """Aggregate tokens across multiple days."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-12T01:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 100 | OK |
| 2026-07-13T10:00:00 | Agent | claude-opus-4-8 | 0 | 2 | 1000 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        self.assertEqual(len(summary["daily_totals"]), 3)
        self.assertIn("2026-07-11", summary["daily_totals"])
        self.assertIn("2026-07-12", summary["daily_totals"])
        self.assertIn("2026-07-13", summary["daily_totals"])


class TestPerWeekCosts(CostIsolationCase):
    """Test per-week cost rollup."""

    def test_empty_result_has_per_week_costs_key(self):
        """Empty summary should have per_week_costs dict."""
        summary = cost.get_cost_summary()
        self.assertIn("per_week_costs", summary)
        self.assertIsInstance(summary["per_week_costs"], dict)

    def test_groups_by_iso_week(self):
        """Group daily totals by ISO week."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-13T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-14T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 2 | 3 | OK |
| 2026-07-20T01:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 100 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should have 2 weeks (2026-W29 and 2026-W30 based on ISO calendar)
        per_week = summary["per_week_costs"]
        self.assertGreater(len(per_week), 0)

        # Verify structure
        for week_key, week_data in per_week.items():
            self.assertIn("tokens_in", week_data)
            self.assertIn("tokens_out", week_data)
            self.assertIn("model_tokens", week_data)
            self.assertIn("cost", week_data)

    def test_week_cost_with_pricing(self):
        """Calculate cost per week when pricing is available."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)
        self.write_config({
            "pricing": {
                "claude-haiku-4-5-20251001": {
                    "input_per_mtok": 0.80,
                    "output_per_mtok": 2.40
                }
            }
        })
        summary = cost.get_cost_summary()

        per_week = summary["per_week_costs"]
        self.assertGreater(len(per_week), 0)

        # Verify cost is calculated
        for week_data in per_week.values():
            self.assertGreaterEqual(week_data["cost"], 0.0)

    def test_each_week_uses_only_own_model_mix_regression(self):
        """REGRESSION TEST: Each week's cost uses ONLY that week's per-model token counts.

        This is a regression test for a bug where _calculate_weekly_costs() would apply
        the GLOBAL model distribution to every week, causing inflation. This test creates
        2+ weeks with DIFFERENT per-week model mixes and asserts that each week reflects
        only its own model token counts (not the global distribution).

        Example of the bug:
          - Week 1 (2026-07-14): only Haiku, 100 tokens
          - Week 2 (2026-07-21): only Opus, 200 tokens
          - BUG: Week 1 would incorrectly include Opus tokens (200) from week 2's data
          - FIX: Week 1 should only have Haiku (100), Week 2 only Opus (200)
        """
        # Week 1 (2026-W29, July 14-20): only Haiku with different token count
        # Week 2 (2026-W30, July 21-27): only Opus with different token count
        # Week 3 (2026-W31, July 28 onwards): different mix
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-14T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 10 | 90 | OK |
| 2026-07-15T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 10 | 90 | OK |
| 2026-07-21T10:00:00 | Agent | claude-opus-4-8 | 0 | 20 | 180 | OK |
| 2026-07-22T10:00:00 | Agent | claude-opus-4-8 | 0 | 20 | 180 | OK |
| 2026-07-28T10:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 5 | 50 | OK |
| 2026-07-28T11:00:00 | Agent | claude-opus-4-8 | 0 | 15 | 150 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        per_week = summary["per_week_costs"]

        # Should have 3 weeks (W29, W30, W31)
        self.assertEqual(len(per_week), 3, f"Expected 3 weeks, got {len(per_week)}: {list(per_week.keys())}")

        # Week 1 (2026-W29): only Haiku
        # Should have only haiku in model_tokens, not opus
        week_29 = per_week.get("2026-W29")
        self.assertIsNotNone(week_29, "2026-W29 not found in per_week_costs")
        self.assertIn("claude-haiku-4-5-20251001", week_29["model_tokens"],
                     "Week 29 should have haiku")
        self.assertNotIn("claude-opus-4-8", week_29["model_tokens"],
                        "Week 29 should NOT have opus (bug: applying global distribution)")
        # Haiku tokens: 10+10 = 20 in, 90+90 = 180 out, total = 200
        self.assertEqual(week_29["model_tokens"]["claude-haiku-4-5-20251001"], 200,
                        "Week 29 haiku should have exactly 200 total tokens")
        self.assertEqual(week_29["tokens_in"], 20, "Week 29 should have 20 tokens_in")
        self.assertEqual(week_29["tokens_out"], 180, "Week 29 should have 180 tokens_out")

        # Week 2 (2026-W30): only Opus
        # Should have only opus in model_tokens, not haiku
        week_30 = per_week.get("2026-W30")
        self.assertIsNotNone(week_30, "2026-W30 not found in per_week_costs")
        self.assertNotIn("claude-haiku-4-5-20251001", week_30["model_tokens"],
                        "Week 30 should NOT have haiku (bug: applying global distribution)")
        self.assertIn("claude-opus-4-8", week_30["model_tokens"],
                     "Week 30 should have opus")
        # Opus tokens: 20+20 = 40 in, 180+180 = 360 out, total = 400
        self.assertEqual(week_30["model_tokens"]["claude-opus-4-8"], 400,
                        "Week 30 opus should have exactly 400 total tokens")
        self.assertEqual(week_30["tokens_in"], 40, "Week 30 should have 40 tokens_in")
        self.assertEqual(week_30["tokens_out"], 360, "Week 30 should have 360 tokens_out")

        # Week 3 (2026-W31): mixed (both Haiku and Opus)
        # Should have both models with their respective token counts
        week_31 = per_week.get("2026-W31")
        self.assertIsNotNone(week_31, "2026-W31 not found in per_week_costs")
        self.assertIn("claude-haiku-4-5-20251001", week_31["model_tokens"],
                     "Week 31 should have haiku")
        self.assertIn("claude-opus-4-8", week_31["model_tokens"],
                     "Week 31 should have opus")
        # Haiku tokens: 5 in, 50 out, total = 55
        self.assertEqual(week_31["model_tokens"]["claude-haiku-4-5-20251001"], 55,
                        "Week 31 haiku should have exactly 55 total tokens")
        # Opus tokens: 15 in, 150 out, total = 165
        self.assertEqual(week_31["model_tokens"]["claude-opus-4-8"], 165,
                        "Week 31 opus should have exactly 165 total tokens")
        self.assertEqual(week_31["tokens_in"], 20, "Week 31 should have 20 tokens_in (5+15)")
        self.assertEqual(week_31["tokens_out"], 200, "Week 31 should have 200 tokens_out (50+150)")


class TestVerdictWeightedCost(CostIsolationCase):
    """Test verdict-weighted cost-per-outcome metrics."""

    def test_empty_result_has_verdict_weighted_cost_key(self):
        """Empty summary should have verdict_weighted_cost dict."""
        summary = cost.get_cost_summary()
        self.assertIn("verdict_weighted_cost", summary)
        vwc = summary["verdict_weighted_cost"]
        self.assertIn("cost_per_ok", vwc)
        self.assertIn("cost_per_failed", vwc)
        self.assertIn("cost_per_empty", vwc)
        self.assertIn("cost_per_hung", vwc)

    def test_cost_per_ok_calculation(self):
        """Calculate cost per OK outcome."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
| 2026-07-11T22:08:22 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 100 | FAILED |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        vwc = summary["verdict_weighted_cost"]
        # Total cost (token proxy) = 8+186+8+116+8+100 = 426
        # OK count = 2
        # cost_per_ok = 426 / 2 = 213
        self.assertGreater(vwc["cost_per_ok"], 0)
        self.assertGreater(vwc["cost_per_failed"], 0)

    def test_cost_per_outcome_with_pricing(self):
        """Calculate cost per outcome with pricing enabled."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
"""
        self.write_ledger(ledger)
        self.write_config({
            "pricing": {
                "claude-haiku-4-5-20251001": {
                    "input_per_mtok": 0.80,
                    "output_per_mtok": 2.40
                }
            }
        })
        summary = cost.get_cost_summary()

        vwc = summary["verdict_weighted_cost"]
        self.assertGreater(vwc["cost_per_ok"], 0)

    def test_zero_outcomes_returns_zero_cost(self):
        """Cost per outcome is 0 when outcome count is 0."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        vwc = summary["verdict_weighted_cost"]
        # Should be 0 for outcomes that didn't occur
        self.assertEqual(vwc["cost_per_failed"], 0.0)
        self.assertEqual(vwc["cost_per_empty"], 0.0)
        self.assertEqual(vwc["cost_per_hung"], 0.0)


class TestModelMixTrend(CostIsolationCase):
    """Test model usage distribution trend."""

    def test_empty_result_has_model_mix_trend_key(self):
        """Empty summary should have model_mix_trend dict."""
        summary = cost.get_cost_summary()
        self.assertIn("model_mix_trend", summary)
        self.assertIsInstance(summary["model_mix_trend"], dict)

    def test_calculates_daily_model_distribution(self):
        """Calculate per-day model distribution as percentages."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | OK |
| 2026-07-12T01:00:00 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 100 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        trend = summary["model_mix_trend"]
        # Should have entries for both days
        self.assertIn("2026-07-11", trend)
        self.assertIn("2026-07-12", trend)

        # Verify structure (percentages)
        for date_str, model_dist in trend.items():
            for model, percentage in model_dist.items():
                self.assertIsInstance(percentage, float)
                self.assertGreaterEqual(percentage, 0.0)
                self.assertLessEqual(percentage, 100.0)

    def test_model_mix_sums_to_100(self):
        """Daily model mix percentages should sum to approximately 100%."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        trend = summary["model_mix_trend"]
        for date_str, model_dist in trend.items():
            total_pct = sum(model_dist.values())
            # Allow small floating point error
            self.assertAlmostEqual(total_pct, 100.0, places=1)


class TestLedgerSchemaVariations(CostIsolationCase):
    """Test parsing of 7-column (old) and 9-column (new) ledger formats.

    Issue fix: Accept both 7-column format (legacy, no phase/wave) and 9-column
    format (new with phase/wave optional trailing columns).
    """

    def test_parses_7_column_ledger(self):
        """Accept legacy 7-column format (no phase/wave columns)."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | FAILED |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should parse both rows without error
        self.assertEqual(len(summary["models"]), 2)
        self.assertIn("claude-haiku-4-5-20251001", summary["models"])
        self.assertIn("claude-opus-4-8", summary["models"])
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 2)
        self.assertEqual(summary["overall_scorecard"]["ok_count"], 1)
        self.assertEqual(summary["overall_scorecard"]["failed_count"], 1)
        self.assertEqual(summary["skipped_lines"], 0)
        # No error should be set
        self.assertNotIn("error", summary)

    def test_parses_9_column_ledger(self):
        """Accept new 9-column format (with phase and wave columns)."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|-------|------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK | build | 7 |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | FAILED | verify | 7 |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should parse both rows without error
        self.assertEqual(len(summary["models"]), 2)
        self.assertIn("claude-haiku-4-5-20251001", summary["models"])
        self.assertIn("claude-opus-4-8", summary["models"])
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 2)
        self.assertEqual(summary["overall_scorecard"]["ok_count"], 1)
        self.assertEqual(summary["overall_scorecard"]["failed_count"], 1)
        self.assertEqual(summary["skipped_lines"], 0)
        # No error should be set
        self.assertNotIn("error", summary)

    def test_parses_mixed_7_and_9_column_rows(self):
        """Accept mixed ledger with both 7-column (old) and 9-column (new) rows."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|-------|------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | FAILED | build | 7 |
| 2026-07-11T22:08:22 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK | verify | 7 |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should parse all rows despite column count variations
        self.assertEqual(len(summary["models"]), 2)
        haiku_stats = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku_stats["runs"], 2)
        self.assertEqual(haiku_stats["tokens_in"], 16)  # 8 + 8
        self.assertEqual(haiku_stats["tokens_out"], 302)  # 186 + 116

        opus_stats = summary["models"]["claude-opus-4-8"]
        self.assertEqual(opus_stats["runs"], 1)
        self.assertEqual(opus_stats["tokens_in"], 2)
        self.assertEqual(opus_stats["tokens_out"], 3)

        self.assertEqual(summary["overall_scorecard"]["total_runs"], 3)
        self.assertEqual(summary["overall_scorecard"]["ok_count"], 2)
        self.assertEqual(summary["overall_scorecard"]["failed_count"], 1)
        self.assertEqual(summary["skipped_lines"], 0)
        self.assertNotIn("error", summary)

    def test_9_column_with_empty_phase_wave(self):
        """Accept 9-column format with empty phase/wave cells."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|-------|------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |  |  |
| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 0 | 2 | 3 | FAILED | build |  |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should parse both rows even with empty trailing columns
        self.assertEqual(len(summary["models"]), 2)
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 2)
        self.assertEqual(summary["skipped_lines"], 0)
        self.assertNotIn("error", summary)


class TestCrossArtifactWithFleetLedger(CostIsolationCase):
    """Cross-artifact test: append via fleet_ledger.py, parse via cost.py."""

    def test_cost_parses_fleet_ledger_appended_9_column_rows(self):
        """Verify cost.get_cost_summary() works with rows appended by fleet_ledger.append_ledger_line()."""
        # This test verifies the bridge between fleet_ledger.py (writer) and cost.py (reader)
        # by using the actual fleet_ledger append function.
        try:
            import fleet_ledger
        except ImportError:
            self.skipTest("fleet_ledger module not available")

        # Set environment so fleet_ledger uses our isolated state dir
        os.environ["AESOP_STATE_ROOT"] = str(self.state_dir)

        # Import datetime for valid ISO-8601 timestamps
        from datetime import datetime

        # Use fleet_ledger.append_ledger_line to append 9-column rows
        # This ensures the test truly tests the integration between writer and reader
        base_time = datetime.fromisoformat("2026-07-11T22:08:17+00:00")
        ts1 = base_time.isoformat()
        ts2 = base_time.replace(second=21).isoformat()
        ts3 = base_time.replace(second=22).isoformat()

        fleet_ledger.append_ledger_line(ts1, "Agent", "claude-haiku-4-5-20251001", 0, 8, 186, "OK", "build", 7)
        fleet_ledger.append_ledger_line(ts2, "Agent", "claude-opus-4-8", 1, 2, 3, "FAILED", "verify", 7)
        fleet_ledger.append_ledger_line(ts3, "Agent", "claude-haiku-4-5-20251001", 0, 8, 116, "OK", "repair", 7)

        # Reload config to pick up the ledger
        config.reload()

        # Now parse with cost.get_cost_summary()
        summary = cost.get_cost_summary()

        # Verify all rows were parsed successfully
        self.assertEqual(len(summary["models"]), 2)
        self.assertIn("claude-haiku-4-5-20251001", summary["models"])
        self.assertIn("claude-opus-4-8", summary["models"])

        # Verify haiku stats
        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 2)
        self.assertEqual(haiku["tokens_in"], 16)  # 8 + 8
        self.assertEqual(haiku["tokens_out"], 302)  # 186 + 116
        self.assertEqual(haiku["verdicts"]["OK"], 2)

        # Verify opus stats
        opus = summary["models"]["claude-opus-4-8"]
        self.assertEqual(opus["runs"], 1)
        self.assertEqual(opus["tokens_in"], 2)
        self.assertEqual(opus["tokens_out"], 3)
        self.assertEqual(opus["verdicts"]["FAILED"], 1)

        # Verify overall scorecard
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 3)
        self.assertEqual(summary["overall_scorecard"]["ok_count"], 2)
        self.assertEqual(summary["overall_scorecard"]["failed_count"], 1)

        # Verify no errors
        self.assertEqual(summary["skipped_lines"], 0)
        self.assertNotIn("error", summary)

    def test_cost_parses_manually_written_9_column_rows(self):
        """Verify cost.get_cost_summary() handles manually-written 9-column rows.

        This test ensures the parser handles hand-crafted markdown rows correctly,
        but it does NOT verify integration with fleet_ledger.py (that's in
        test_cost_parses_fleet_ledger_appended_9_column_rows).
        """
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        ledger_file.parent.mkdir(parents=True, exist_ok=True)

        # Create ledger with header
        header = '| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n'
        header += '|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n'
        ledger_file.write_text(header, encoding='utf-8')

        # Append manually-written 9-column rows
        row1 = '| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK | build | 7 |\n'
        row2 = '| 2026-07-11T22:08:21 | Agent | claude-opus-4-8 | 1 | 2 | 3 | FAILED | verify | 7 |\n'
        row3 = '| 2026-07-11T22:08:22 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK | repair | 7 |\n'

        with open(ledger_file, 'a', encoding='utf-8') as f:
            f.write(row1)
            f.write(row2)
            f.write(row3)

        # Reload config to pick up the ledger
        config.reload()

        # Now parse with cost.get_cost_summary()
        summary = cost.get_cost_summary()

        # Verify all rows were parsed successfully
        self.assertEqual(len(summary["models"]), 2)
        self.assertIn("claude-haiku-4-5-20251001", summary["models"])
        self.assertIn("claude-opus-4-8", summary["models"])

        # Verify haiku stats
        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 2)
        self.assertEqual(haiku["tokens_in"], 16)  # 8 + 8
        self.assertEqual(haiku["tokens_out"], 302)  # 186 + 116
        self.assertEqual(haiku["verdicts"]["OK"], 2)

        # Verify opus stats
        opus = summary["models"]["claude-opus-4-8"]
        self.assertEqual(opus["runs"], 1)
        self.assertEqual(opus["tokens_in"], 2)
        self.assertEqual(opus["tokens_out"], 3)
        self.assertEqual(opus["verdicts"]["FAILED"], 1)

        # Verify overall scorecard
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 3)
        self.assertEqual(summary["overall_scorecard"]["ok_count"], 2)
        self.assertEqual(summary["overall_scorecard"]["failed_count"], 1)

        # Verify no errors
        self.assertEqual(summary["skipped_lines"], 0)
        self.assertNotIn("error", summary)

    def test_cost_parses_8_column_rows(self):
        """Verify cost.get_cost_summary() accepts 8-column rows (phase optional, wave omitted).

        8-column format: | timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict | phase |
        This is an intermediate format between 7-column (legacy) and 9-column (full).
        """
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        ledger_file.parent.mkdir(parents=True, exist_ok=True)

        # Create ledger with header
        header = '| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase |\n'
        header += '|--------|------------|-------|--------------|-----------|------------|--------|-------|\n'
        ledger_file.write_text(header, encoding='utf-8')

        # Append 8-column rows (no wave column)
        row1 = '| 2026-07-11T22:10:01 | Agent | claude-haiku-4-5-20251001 | 0 | 10 | 200 | OK | build |\n'
        row2 = '| 2026-07-11T22:10:05 | Agent | claude-opus-4-8 | 2 | 5 | 50 | OK | verify |\n'

        with open(ledger_file, 'a', encoding='utf-8') as f:
            f.write(row1)
            f.write(row2)

        # Reload config to pick up the ledger
        config.reload()

        # Now parse with cost.get_cost_summary()
        summary = cost.get_cost_summary()

        # Verify both rows were parsed successfully (8-column format is accepted)
        self.assertEqual(len(summary["models"]), 2)
        self.assertIn("claude-haiku-4-5-20251001", summary["models"])
        self.assertIn("claude-opus-4-8", summary["models"])

        # Verify haiku stats
        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 1)
        self.assertEqual(haiku["tokens_in"], 10)
        self.assertEqual(haiku["tokens_out"], 200)

        # Verify opus stats
        opus = summary["models"]["claude-opus-4-8"]
        self.assertEqual(opus["runs"], 1)
        self.assertEqual(opus["tokens_in"], 5)
        self.assertEqual(opus["tokens_out"], 50)

        # Verify overall scorecard
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 2)
        self.assertEqual(summary["overall_scorecard"]["ok_count"], 2)
        self.assertEqual(summary["skipped_lines"], 0)
        self.assertNotIn("error", summary)


if __name__ == "__main__":
    unittest.main()
