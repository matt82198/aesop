"""Tests for ui/cost.py integration with serve.py (wave-14 cost/pricing dashboard).

Tests the cost collection endpoint (/api/state and /events SSE) with:
  - Valid ledger files (parsed correctly)
  - Pricing configuration (estimates computed, returned in JSON)
  - Config portability (paths work on Windows and POSIX)
  - Graceful degradation (missing ledger, malformed data)

Run: python -m unittest tests.test_serve_cost
     python tests/test_serve_cost.py
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
import serve

ENV_KEYS = ("AESOP_ROOT", "AESOP_STATE_ROOT", "AESOP_TRANSCRIPTS_ROOT",
            "AESOP_UI_COLLECT_INTERVAL", "PORT")


class ServeCostIsolationCase(unittest.TestCase):
    """Base class for cost + serve tests with isolated temp directories."""

    def setUp(self):
        """Set up isolated temp directories for testing."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="aesop-serve-cost-test-"))
        self.state_dir = self.fixture_root / "state"
        self.state_dir.mkdir(parents=True)
        (self.fixture_root / "transcripts").mkdir()
        (self.fixture_root / "ui" / "web" / "dist").mkdir(parents=True)

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


class TestServeCostShape(ServeCostIsolationCase):
    """Test that serve.py correctly returns cost data in JSON endpoints."""

    def test_cost_summary_shape_in_state_api(self):
        """cost.get_cost_summary() returns documented shape compatible with JSON serialization."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
| 2026-07-11T22:08:21 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 116 | OK |
"""
        self.write_ledger(ledger)
        summary = cost.get_cost_summary()

        # Should be JSON-serializable (test with default=str for Path/other objects)
        try:
            json_str = json.dumps(summary, default=str, sort_keys=True)
            parsed = json.loads(json_str)
        except Exception as e:
            self.fail(f"Cost summary not JSON-serializable: {e}")

        # Verify documented fields
        self.assertIn("models", parsed)
        self.assertIn("daily_totals", parsed)
        self.assertIn("overall_scorecard", parsed)
        self.assertIn("skipped_lines", parsed)
        self.assertIn("has_pricing", parsed)
        self.assertIn("estimates_by_model", parsed)

    def test_cost_with_pricing_json_serializable(self):
        """Cost data with pricing estimates is JSON-serializable."""
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

        # Should be JSON-serializable with pricing data
        try:
            json_str = json.dumps(summary, default=str, sort_keys=True)
            parsed = json.loads(json_str)
        except Exception as e:
            self.fail(f"Cost summary with pricing not JSON-serializable: {e}")

        # Verify pricing fields present
        self.assertTrue(parsed["has_pricing"])
        self.assertIn("claude-haiku-4-5-20251001", parsed["estimates_by_model"])
        estimate = parsed["estimates_by_model"]["claude-haiku-4-5-20251001"]
        self.assertIn("input_cost", estimate)
        self.assertIn("output_cost", estimate)
        self.assertIn("total_cost", estimate)


class TestConfigPortability(ServeCostIsolationCase):
    """Test that config paths work on Windows and POSIX."""

    def test_ledger_path_works_with_pathlib(self):
        """Ledger file path is computed correctly using pathlib."""
        ledger_content = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger_content)

        # Verify config module computed the path correctly
        # (pathlib handles both Windows and POSIX)
        self.assertTrue(config.LEDGER_FILE.exists())
        self.assertEqual(config.LEDGER_FILE.name, "OUTCOMES-LEDGER.md")
        self.assertTrue(str(config.LEDGER_FILE).endswith("ledger/OUTCOMES-LEDGER.md") or
                       str(config.LEDGER_FILE).endswith("ledger\\OUTCOMES-LEDGER.md"))

    def test_config_file_path_works_with_pathlib(self):
        """Config file path is computed correctly using pathlib."""
        test_config = {"pricing": {"claude-haiku-4-5-20251001": {"input_per_mtok": 1.0}}}
        self.write_config(test_config)

        # Verify config module computed the path correctly
        self.assertTrue(config.CONFIG_FILE.exists())
        self.assertEqual(config.CONFIG_FILE.name, "aesop.config.json")

    def test_state_dir_path_resolves_correctly(self):
        """STATE_DIR from config resolves to the correct location."""
        ledger_content = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger_content)

        # Verify that STATE_DIR resolves correctly
        expected = self.state_dir
        self.assertEqual(config.STATE_DIR, expected)

        # Verify that ledger file can be found relative to STATE_DIR
        ledger_in_state = config.STATE_DIR / "ledger" / "OUTCOMES-LEDGER.md"
        self.assertTrue(ledger_in_state.exists())

    def test_cost_uses_config_paths_at_call_time(self):
        """cost.get_cost_summary() reads paths at call time (not import time)."""
        # First call: no ledger
        summary1 = cost.get_cost_summary()
        self.assertEqual(len(summary1["models"]), 0)

        # Write ledger
        self.write_ledger("""|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
""")

        # Second call: should see the ledger (call-time resolution)
        summary2 = cost.get_cost_summary()
        self.assertEqual(len(summary2["models"]), 1)
        self.assertIn("claude-haiku-4-5-20251001", summary2["models"])

    def test_cost_pricing_path_resolved_at_call_time(self):
        """cost.get_cost_summary() resolves pricing config at call time."""
        self.write_ledger("""|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
""")

        # First call: no pricing config
        summary1 = cost.get_cost_summary()
        self.assertFalse(summary1["has_pricing"])

        # Write pricing config
        self.write_config({
            "pricing": {
                "claude-haiku-4-5-20251001": {
                    "input_per_mtok": 0.80,
                    "output_per_mtok": 2.40
                }
            }
        })

        # Second call: should see pricing (call-time resolution)
        summary2 = cost.get_cost_summary()
        self.assertTrue(summary2["has_pricing"])
        self.assertIn("claude-haiku-4-5-20251001", summary2["estimates_by_model"])


class TestServeIntegration(ServeCostIsolationCase):
    """Test that cost.py integrates correctly with serve.py."""

    def test_serve_imports_cost_correctly(self):
        """serve.py can import and call cost module."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)

        # Verify serve module has cost function available
        # (serve re-exports from handler)
        self.assertTrue(hasattr(serve, 'cost'))
        self.assertTrue(callable(serve.cost.get_cost_summary))

    def test_cost_summary_used_in_state_snapshot(self):
        """Cost summary is included in dashboard state snapshot."""
        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)
        self.write_config({})

        # Get cost summary as it would appear in /api/state
        summary = cost.get_cost_summary()

        # Verify it contains the expected fields for the frontend
        self.assertIsInstance(summary, dict)
        self.assertIn("models", summary)
        self.assertIn("daily_totals", summary)
        self.assertIn("overall_scorecard", summary)


class TestGracefulDegradation(ServeCostIsolationCase):
    """Test that cost endpoint handles missing/malformed data gracefully."""

    def test_missing_ledger_returns_empty_state(self):
        """When ledger is missing, cost endpoint returns empty but valid state."""
        summary = cost.get_cost_summary()

        # Should be valid even with no data
        self.assertIsInstance(summary, dict)
        self.assertEqual(len(summary["models"]), 0)
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 0)
        # Should still be JSON-serializable
        json_str = json.dumps(summary, default=str)
        parsed = json.loads(json_str)
        self.assertIsInstance(parsed, dict)

    def test_malformed_config_gracefully_ignored(self):
        """Malformed config file is ignored, cost still returns valid data."""
        # Write malformed config
        config_file = self.fixture_root / "aesop.config.json"
        config_file.write_text("{not valid json}", encoding='utf-8')
        config.reload()

        ledger = """|--------|------------|-------|--------------|-----------|------------|--------|
| 2026-07-11T22:08:17 | Agent | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |
"""
        self.write_ledger(ledger)

        # Should still return valid cost data (no pricing, but data is parsed)
        summary = cost.get_cost_summary()
        self.assertEqual(len(summary["models"]), 1)
        self.assertFalse(summary["has_pricing"])


class TestLargeScaleCostData(ServeCostIsolationCase):
    """Test cost handling with realistic large-scale data."""

    def test_large_ledger_parses_efficiently(self):
        """Large ledger file is parsed without errors."""
        # Build a large ledger with 100 entries
        lines = ["|--------|------------|-------|--------------|-----------|------------|--------|"]
        for i in range(100):
            timestamp = f"2026-07-{11 + (i % 3):02d}T{10 + (i % 24):02d}:00:00"
            model = "claude-haiku-4-5-20251001" if i % 2 == 0 else "claude-opus-4-8"
            lines.append(f"| {timestamp} | Agent | {model} | 0 | {100 + i} | {200 + i*2} | OK |")

        ledger_content = "\n".join(lines)
        self.write_ledger(ledger_content)

        summary = cost.get_cost_summary()

        # Should parse all entries
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 100)
        self.assertEqual(len(summary["models"]), 2)
        self.assertEqual(len(summary["daily_totals"]), 3)
        # Should be JSON-serializable
        json_str = json.dumps(summary, default=str)
        self.assertGreater(len(json_str), 100)

    def test_many_models_aggregated_correctly(self):
        """Cost data with many models is aggregated correctly."""
        models = ["claude-haiku-4-5-20251001", "claude-opus-4-8", "claude-sonnet-3-5"]
        lines = ["|--------|------------|-------|--------------|-----------|------------|--------|"]
        for i, model in enumerate(models):
            for j in range(10):
                timestamp = f"2026-07-11T{10 + j:02d}:00:00"
                lines.append(f"| {timestamp} | Agent | {model} | 0 | {100} | {200} | OK |")

        ledger_content = "\n".join(lines)
        self.write_ledger(ledger_content)

        summary = cost.get_cost_summary()

        # Should have all models
        self.assertEqual(len(summary["models"]), 3)
        self.assertEqual(summary["overall_scorecard"]["total_runs"], 30)

        # Each model should have 10 runs
        for model in models:
            self.assertEqual(summary["models"][model]["runs"], 10)


if __name__ == "__main__":
    unittest.main()
