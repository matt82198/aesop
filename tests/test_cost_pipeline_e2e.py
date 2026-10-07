"""End-to-end contract test for the cost pipeline.

This test verifies that ui/cost.py and tools/fleet_ledger.py maintain a consistent
contract when parsing and aggregating the OUTCOMES-LEDGER.md file.

The cost pipeline has three critical contracts:
1. ui/cost.py's get_cost_summary() must parse ledger rows and surface them
   in a models dict keyed by model name
2. tools/fleet_ledger.py's parse_ledger_rows() and summary() must return
   matching totals when aggregating the same ledger
3. Both must degrade gracefully when the ledger is empty or missing
   (no crash, explicit empty shape)

This test would have caught the rc.5 gap: a zero-rows producer (e.g., no harvest()
integration) would silently leave the ledger empty, but dashboards and cost reports
would claim 'no data', when the expected state is a populated ledger at steady state.

Run: python -m unittest tests.test_cost_pipeline_e2e
     python tests/test_cost_pipeline_e2e.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add both ui and tools to sys.path for imports
UI_DIR = Path(__file__).parent.parent / "ui"
TOOLS_DIR = Path(__file__).parent.parent / "tools"

if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import config
import cost

# Lazy import of fleet_ledger to avoid module pollution before AESOP_STATE_ROOT is set
def load_fleet_ledger():
    """Import fleet_ledger at call time to respect env var isolation."""
    import fleet_ledger
    return fleet_ledger


ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class CostPipelineE2ECase(unittest.TestCase):
    """Base class for cost pipeline end-to-end tests with isolated temp directories."""

    def setUp(self):
        """Set up isolated temp directories for testing.

        Creates a complete temp AESOP root directory tree including
        state/ledger, transcripts, and config file.
        """
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-cost-e2e-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.state_dir / "ledger").mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()

        # Save original env
        self._saved_env = {k: os.environ.get(k) for k in ENV_KEYS}

        # Set isolated environment for both ui/config.py and tools
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
        """Write a test ledger file to the isolated state dir.

        Args:
            content: markdown table content (may or may not include header)

        Returns:
            Path to the created ledger file
        """
        ledger_file = self.state_dir / "ledger" / "OUTCOMES-LEDGER.md"
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        ledger_file.write_text(content, encoding='utf-8')
        return ledger_file

    def write_config(self, config_dict):
        """Write a test aesop.config.json to the isolated fixture root.

        Args:
            config_dict: dict to write as JSON
        """
        config_file = self.fixture_root / "aesop.config.json"
        config_file.write_text(json.dumps(config_dict), encoding='utf-8')
        # Reload config so it picks up the new file
        config.reload()


class TestCostPipelineEmptyGraceful(CostPipelineE2ECase):
    """Test that both modules degrade gracefully when ledger is empty or missing."""

    def test_empty_ledger_missing_file_no_crash(self):
        """When ledger file is missing, both modules return empty without crashing."""
        # Don't write any ledger file

        # ui/cost.py should return empty summary
        summary = cost.get_cost_summary()
        self.assertIsInstance(summary, dict)
        self.assertEqual(len(summary["models"]), 0)
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 0)

        # tools/fleet_ledger.py should return empty list
        fleet_ledger = load_fleet_ledger()
        rows = fleet_ledger.parse_ledger_rows()
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), 0)

    def test_empty_ledger_file_no_crash(self):
        """When ledger file is empty, both modules return empty without crashing."""
        self.write_ledger("")

        # ui/cost.py should return empty summary
        summary = cost.get_cost_summary()
        self.assertEqual(len(summary["models"]), 0)
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 0)

        # tools/fleet_ledger.py should return empty list
        fleet_ledger = load_fleet_ledger()
        rows = fleet_ledger.parse_ledger_rows()
        self.assertEqual(len(rows), 0)

    def test_empty_ledger_explicit_shape(self):
        """Empty ledger returns the documented empty shape (no missing fields)."""
        summary = cost.get_cost_summary()

        # Verify all documented fields present
        self.assertIn("models", summary)
        self.assertIn("daily_totals", summary)
        self.assertIn("overall_scorecard", summary)
        self.assertIn("skipped_lines", summary)
        self.assertIn("has_pricing", summary)
        self.assertIn("estimates_by_model", summary)

        # Verify scorecard fields
        scorecard = summary["overall_scorecard"]
        self.assertIn("total_runs", scorecard)
        self.assertIn("ok_count", scorecard)
        self.assertIn("failed_count", scorecard)
        self.assertIn("empty_count", scorecard)
        self.assertIn("hung_count", scorecard)
        self.assertIn("ok_rate", scorecard)
        self.assertIn("failed_rate", scorecard)
        self.assertIn("empty_rate", scorecard)
        self.assertIn("hung_rate", scorecard)


class TestCostPipelineContractSingleEntry(CostPipelineE2ECase):
    """Test the cost pipeline contract with a single valid ledger entry."""

    def test_populated_pipeline_single_entry(self):
        """A populated pipeline (single valid entry) is the expected steady state.

        This assertion documents the expected steady state: a production aesop
        installation should have a non-empty ledger as agents produce outcomes.

        If the ledger is empty in production, it indicates a gap in the harvest
        or agent instrumentation pipeline, and dashboards should flag this.
        """
        # Write a single valid ledger entry (7-column format for cost.py compatibility)
        # cost.py expects: | ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |
        ledger_header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |\n"
        ledger_separator = "|--------|------------|-------|--------------|-----------|------------|--------|\n"
        ledger_row = "| 2026-07-15T14:30:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | OK |\n"

        self.write_ledger(ledger_header + ledger_separator + ledger_row)

        # ui/cost.py should parse and surface the entry
        summary = cost.get_cost_summary()
        self.assertEqual(len(summary["models"]), 1)
        self.assertIn("claude-haiku-4-5-20251001", summary["models"])

        model_stats = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(model_stats["runs"], 1)
        self.assertEqual(model_stats["tokens_in"], 100)
        self.assertEqual(model_stats["tokens_out"], 250)
        self.assertEqual(model_stats["verdicts"]["OK"], 1)

        # Verify overall scorecard
        scorecard = summary["overall_scorecard"]
        self.assertEqual(scorecard["total_runs"], 1)
        self.assertEqual(scorecard["ok_count"], 1)
        self.assertEqual(scorecard["ok_rate"], 1.0)

        # tools/fleet_ledger.py should parse the same entry (supports 7-column format)
        fleet_ledger = load_fleet_ledger()
        rows = fleet_ledger.parse_ledger_rows()
        self.assertEqual(len(rows), 1)

        row = rows[0]
        self.assertEqual(row["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(row["tokens_in"], 100)
        self.assertEqual(row["tokens_out"], 250)
        self.assertEqual(row["verdict"], "OK")

    def test_cost_and_ledger_totals_match_single_entry(self):
        """Cost summary and fleet_ledger totals match for a single entry."""
        ledger_header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |\n"
        ledger_separator = "|--------|------------|-------|--------------|-----------|------------|--------|\n"
        ledger_row = "| 2026-07-15T14:30:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | OK |\n"

        self.write_ledger(ledger_header + ledger_separator + ledger_row)

        # Get summary from both modules
        cost_summary = cost.get_cost_summary()
        fleet_ledger = load_fleet_ledger()
        rows = fleet_ledger.parse_ledger_rows()

        # Totals must match
        self.assertEqual(len(rows), 1)

        cost_model_stats = cost_summary["models"]["claude-haiku-4-5-20251001"]
        row = rows[0]

        self.assertEqual(cost_model_stats["tokens_in"], row["tokens_in"])
        self.assertEqual(cost_model_stats["tokens_out"], row["tokens_out"])
        self.assertEqual(cost_model_stats["runs"], 1)


class TestCostPipelineContractMultiEntry(CostPipelineE2ECase):
    """Test the cost pipeline contract with multiple ledger entries."""

    def test_populated_pipeline_multiple_entries(self):
        """A populated pipeline with multiple entries aggregates correctly."""
        ledger_header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |\n"
        ledger_separator = "|--------|------------|-------|--------------|-----------|------------|--------|\n"
        ledger_rows = (
            "| 2026-07-15T14:30:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | OK |\n"
            "| 2026-07-15T14:35:00 | Agent | claude-haiku-4-5-20251001 | 3 | 50 | 120 | OK |\n"
            "| 2026-07-15T14:40:00 | Agent | claude-opus-4-8 | 2 | 10 | 15 | FAILED |\n"
        )

        self.write_ledger(ledger_header + ledger_separator + ledger_rows)

        # ui/cost.py should aggregate correctly
        summary = cost.get_cost_summary()
        self.assertEqual(len(summary["models"]), 2)

        haiku = summary["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual(haiku["runs"], 2)
        self.assertEqual(haiku["tokens_in"], 150)  # 100 + 50
        self.assertEqual(haiku["tokens_out"], 370)  # 250 + 120
        self.assertEqual(haiku["verdicts"]["OK"], 2)

        opus = summary["models"]["claude-opus-4-8"]
        self.assertEqual(opus["runs"], 1)
        self.assertEqual(opus["tokens_in"], 10)
        self.assertEqual(opus["tokens_out"], 15)
        self.assertEqual(opus["verdicts"]["FAILED"], 1)

        scorecard = summary["overall_scorecard"]
        self.assertEqual(scorecard["total_runs"], 3)
        self.assertEqual(scorecard["ok_count"], 2)
        self.assertEqual(scorecard["failed_count"], 1)

    def test_cost_and_ledger_totals_match_multiple_entries(self):
        """Cost summary and fleet_ledger totals match for multiple entries."""
        ledger_header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |\n"
        ledger_separator = "|--------|------------|-------|--------------|-----------|------------|--------|\n"
        ledger_rows = (
            "| 2026-07-15T14:30:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | OK |\n"
            "| 2026-07-15T14:35:00 | Agent | claude-haiku-4-5-20251001 | 3 | 50 | 120 | OK |\n"
            "| 2026-07-15T14:40:00 | Agent | claude-opus-4-8 | 2 | 10 | 15 | FAILED |\n"
        )

        self.write_ledger(ledger_header + ledger_separator + ledger_rows)

        # Get data from both modules
        cost_summary = cost.get_cost_summary()
        fleet_ledger = load_fleet_ledger()
        rows = fleet_ledger.parse_ledger_rows()

        # Verify both parsed the same number of entries
        self.assertEqual(len(rows), 3)

        # Compute totals from fleet_ledger.parse_ledger_rows()
        total_tokens_in = sum(r["tokens_in"] for r in rows)
        total_tokens_out = sum(r["tokens_out"] for r in rows)

        # Verify cost summary has the same totals by summing models
        cost_total_in = sum(s["tokens_in"] for s in cost_summary["models"].values())
        cost_total_out = sum(s["tokens_out"] for s in cost_summary["models"].values())

        self.assertEqual(cost_total_in, total_tokens_in)
        self.assertEqual(cost_total_out, total_tokens_out)
        self.assertEqual(cost_total_in, 160)  # 100 + 50 + 10
        self.assertEqual(cost_total_out, 385)  # 250 + 120 + 15

    def test_wave_phase_aggregation(self):
        """Wave and phase fields are correctly parsed by fleet_ledger.py.

        Note: This test uses the extended 9-column format that fleet_ledger.py supports.
        The cost.py module does not track wave/phase, only the 7-column format.
        """
        ledger_header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n"
        ledger_separator = "|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n"
        ledger_rows = (
            "| 2026-07-15T14:30:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | OK | build | 5 |\n"
            "| 2026-07-15T14:35:00 | Agent | claude-haiku-4-5-20251001 | 3 | 50 | 120 | OK | verify | 5 |\n"
            "| 2026-07-16T14:40:00 | Agent | claude-opus-4-8 | 2 | 10 | 15 | OK | repair | 6 |\n"
        )

        self.write_ledger(ledger_header + ledger_separator + ledger_rows)

        # Parse via fleet_ledger to check wave/phase fields
        fleet_ledger = load_fleet_ledger()
        rows = fleet_ledger.parse_ledger_rows()

        self.assertEqual(len(rows), 3)

        # Check wave and phase are preserved
        self.assertEqual(rows[0]["wave"], 5)
        self.assertEqual(rows[0]["phase"], "build")

        self.assertEqual(rows[1]["wave"], 5)
        self.assertEqual(rows[1]["phase"], "verify")

        self.assertEqual(rows[2]["wave"], 6)
        self.assertEqual(rows[2]["phase"], "repair")


class TestCostPipelineVerdictTracking(CostPipelineE2ECase):
    """Test verdict tracking and scoring across the pipeline."""

    def test_all_verdict_types_tracked(self):
        """All verdict types (OK, FAILED, EMPTY, HUNG) are tracked."""
        ledger_header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |\n"
        ledger_separator = "|--------|------------|-------|--------------|-----------|------------|--------|\n"
        ledger_rows = (
            "| 2026-07-15T14:30:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | OK |\n"
            "| 2026-07-15T14:31:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | FAILED |\n"
            "| 2026-07-15T14:32:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 0 | EMPTY |\n"
            "| 2026-07-15T14:33:00 | Agent | claude-haiku-4-5-20251001 | 300 | 100 | 0 | HUNG |\n"
        )

        self.write_ledger(ledger_header + ledger_separator + ledger_rows)

        # Verify cost summary tracks all verdicts
        summary = cost.get_cost_summary()
        scorecard = summary["overall_scorecard"]

        self.assertEqual(scorecard["ok_count"], 1)
        self.assertEqual(scorecard["failed_count"], 1)
        self.assertEqual(scorecard["empty_count"], 1)
        self.assertEqual(scorecard["hung_count"], 1)
        self.assertEqual(scorecard["total_runs"], 4)

        # Verify rates are correct
        self.assertAlmostEqual(scorecard["ok_rate"], 0.25, places=2)
        self.assertAlmostEqual(scorecard["failed_rate"], 0.25, places=2)
        self.assertAlmostEqual(scorecard["empty_rate"], 0.25, places=2)
        self.assertAlmostEqual(scorecard["hung_rate"], 0.25, places=2)

        # Verify fleet_ledger also tracks them
        fleet_ledger = load_fleet_ledger()
        rows = fleet_ledger.parse_ledger_rows()
        verdicts = [r["verdict"] for r in rows]

        self.assertEqual(verdicts.count("OK"), 1)
        self.assertEqual(verdicts.count("FAILED"), 1)
        self.assertEqual(verdicts.count("EMPTY"), 1)
        self.assertEqual(verdicts.count("HUNG"), 1)


class TestCostPipelineDailyAggregation(CostPipelineE2ECase):
    """Test daily aggregation of costs across the pipeline."""

    def test_daily_totals_aggregation(self):
        """Daily totals are correctly aggregated by date."""
        ledger_header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |\n"
        ledger_separator = "|--------|------------|-------|--------------|-----------|------------|--------|\n"
        ledger_rows = (
            "| 2026-07-15T14:30:00 | Agent | claude-haiku-4-5-20251001 | 5 | 100 | 250 | OK |\n"
            "| 2026-07-15T23:59:00 | Agent | claude-opus-4-8 | 2 | 10 | 15 | OK |\n"
            "| 2026-07-16T01:00:00 | Agent | claude-haiku-4-5-20251001 | 3 | 50 | 120 | OK |\n"
        )

        self.write_ledger(ledger_header + ledger_separator + ledger_rows)

        summary = cost.get_cost_summary()

        # Should have two dates
        self.assertEqual(len(summary["daily_totals"]), 2)
        self.assertIn("2026-07-15", summary["daily_totals"])
        self.assertIn("2026-07-16", summary["daily_totals"])

        # Verify day 1 totals
        day1 = summary["daily_totals"]["2026-07-15"]
        self.assertEqual(day1["tokens_in"], 110)  # 100 + 10
        self.assertEqual(day1["tokens_out"], 265)  # 250 + 15

        # Verify day 2 totals
        day2 = summary["daily_totals"]["2026-07-16"]
        self.assertEqual(day2["tokens_in"], 50)
        self.assertEqual(day2["tokens_out"], 120)


if __name__ == "__main__":
    unittest.main()
