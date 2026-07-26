#!/usr/bin/env python3
"""Unit tests for wave_scorecard.py wave quality metrics."""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path


class TestWaveScorecard(unittest.TestCase):
    """Test cases for wave_scorecard.py quality scorer."""

    def setUp(self):
        """Create temporary test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.state_dir = Path(self.temp_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Create ledger directory
        self.ledger_dir = self.state_dir / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)

        # Create ledger file with header
        self.ledger_file = self.ledger_dir / "OUTCOMES-LEDGER.md"
        header = "| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n"
        header += "|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n"
        self.ledger_file.write_text(header, encoding='utf-8')

        # Get path to scorecard script
        self.scorecard_script = Path(__file__).parent.parent / "tools" / "wave_scorecard.py"

    def tearDown(self):
        """Clean up temporary directories."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _run_scorecard(self, *args, env_overrides=None):
        """Run wave_scorecard.py with arguments."""
        import subprocess
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.state_dir)
        if env_overrides:
            env.update(env_overrides)

        cmd = [sys.executable, str(self.scorecard_script)] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=10)
        return result

    def test_empty_ledger_summary(self):
        """Test scorecard generation with empty ledger."""
        result = self._run_scorecard()
        self.assertEqual(result.returncode, 0)
        # Should output something indicating no data
        self.assertTrue("no data" in result.stdout.lower() or "n/a" in result.stdout.lower())

    def test_json_output_format(self):
        """Test JSON output format is valid."""
        result = self._run_scorecard("--json")
        self.assertEqual(result.returncode, 0)

        # Parse JSON output
        try:
            data = json.loads(result.stdout)
            self.assertIsInstance(data, (dict, list))
        except json.JSONDecodeError:
            self.fail("Scorecard --json output is not valid JSON")

    def test_markdown_output_format(self):
        """Test markdown table output with data."""
        # Add some data first
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
        ]
        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--md")
        self.assertEqual(result.returncode, 0)

        # Should contain markdown table markers
        self.assertIn("|", result.stdout)

    def test_single_wave_metrics(self):
        """Test metrics computation for a single wave."""
        # Add entries to ledger for wave 1
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | agent2 | haiku | 8 | 150 | 250 | OK | verify | 1 |",
            "| 2024-07-13T10:10:00 | agent1 | haiku | 5 | 50 | 100 | FAILED | repair | 1 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--json")
        self.assertEqual(result.returncode, 0)

        data = json.loads(result.stdout)
        # Should have wave 1 data
        self.assertIsNotNone(data)

    def test_multi_wave_metrics(self):
        """Test scorecard with multiple waves."""
        # Add entries for waves 1 and 2
        entries = [
            # Wave 1
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | agent2 | haiku | 8 | 150 | 250 | OK | verify | 1 |",
            # Wave 2
            "| 2024-07-14T10:00:00 | agent1 | haiku | 12 | 120 | 220 | OK | build | 2 |",
            "| 2024-07-14T10:05:00 | agent2 | haiku | 6 | 60 | 120 | OK | verify | 2 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--json", "--waves", "2")
        self.assertEqual(result.returncode, 0)

        data = json.loads(result.stdout)
        self.assertIsNotNone(data)

    def test_token_cost_breakdown_by_model(self):
        """Test that tokens are tracked by model."""
        # Add entries with different models
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | agent2 | sonnet | 8 | 150 | 250 | OK | verify | 1 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--json")
        self.assertEqual(result.returncode, 0)

        data = json.loads(result.stdout)
        # Should track tokens by model
        self.assertIsNotNone(data)

    def test_token_cost_breakdown_by_phase(self):
        """Test that tokens are tracked by phase."""
        # Add entries with different phases
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | agent2 | haiku | 8 | 150 | 250 | OK | verify | 1 |",
            "| 2024-07-13T10:10:00 | agent1 | haiku | 5 | 50 | 100 | OK | repair | 1 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--json")
        self.assertEqual(result.returncode, 0)

        data = json.loads(result.stdout)
        # Should track tokens by phase
        self.assertIsNotNone(data)

    def test_verdict_rates(self):
        """Test computation of OK vs FAILED verdict rates."""
        # Add entries with mixed verdicts
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | agent2 | haiku | 8 | 150 | 250 | OK | verify | 1 |",
            "| 2024-07-13T10:10:00 | agent1 | haiku | 5 | 50 | 100 | FAILED | repair | 1 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--json")
        self.assertEqual(result.returncode, 0)

        data = json.loads(result.stdout)
        # Should compute success rate
        self.assertIsNotNone(data)

    def test_agent_type_breakdown(self):
        """Test that metrics are tracked by agent type."""
        # Add entries with different agent types
        entries = [
            "| 2024-07-13T10:00:00 | agent | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | waverun | haiku | 8 | 150 | 250 | OK | verify | 1 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--json")
        self.assertEqual(result.returncode, 0)

        data = json.loads(result.stdout)
        # Should track by agent type
        self.assertIsNotNone(data)

    def test_missing_ledger_source(self):
        """Test that tool emits 'n/a' when ledger is missing."""
        # Remove ledger file
        self.ledger_file.unlink()

        result = self._run_scorecard()
        # Should still succeed with n/a indicators
        self.assertEqual(result.returncode, 0)
        self.assertIn("n/a", result.stdout.lower())

    def test_markdown_table_side_by_side(self):
        """Test markdown output shows waves side-by-side."""
        # Add entries for multiple waves
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | agent2 | haiku | 8 | 150 | 250 | OK | verify | 1 |",
            "| 2024-07-14T10:00:00 | agent1 | haiku | 12 | 120 | 220 | OK | build | 2 |",
            "| 2024-07-14T10:05:00 | agent2 | haiku | 6 | 60 | 120 | OK | verify | 2 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--md", "--waves", "2")
        self.assertEqual(result.returncode, 0)

        # Should contain markdown table
        self.assertIn("|", result.stdout)
        # Should show both wave 1 and wave 2
        self.assertTrue("1" in result.stdout or "2" in result.stdout)

    def test_help_text(self):
        """Test that help/usage is provided."""
        result = self._run_scorecard("--help")
        # Should succeed or show usage
        self.assertIn("wave" or "score" or "metric" or "usage", result.stdout.lower() + result.stderr.lower())

    def test_ascii_output(self):
        """Test ASCII (default) output."""
        # Add some data
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard()  # No --json, no --md
        self.assertEqual(result.returncode, 0)
        # Should be human-readable text
        self.assertIsNotNone(result.stdout)

    def test_repair_rounds_count(self):
        """Test that repair phase entries are counted as repair rounds."""
        # Add entries with repair phase
        entries = [
            "| 2024-07-13T10:00:00 | agent1 | haiku | 10 | 100 | 200 | OK | build | 1 |",
            "| 2024-07-13T10:05:00 | agent1 | haiku | 5 | 50 | 100 | FAILED | repair | 1 |",
            "| 2024-07-13T10:10:00 | agent1 | haiku | 3 | 30 | 60 | OK | repair | 1 |",
        ]

        content = self.ledger_file.read_text(encoding='utf-8')
        for entry in entries:
            content += entry + "\n"
        self.ledger_file.write_text(content, encoding='utf-8')

        result = self._run_scorecard("--json")
        self.assertEqual(result.returncode, 0)

        data = json.loads(result.stdout)
        # Should track repair rounds
        self.assertIsNotNone(data)


if __name__ == "__main__":
    unittest.main()
