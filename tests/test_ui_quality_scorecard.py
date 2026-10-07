#!/usr/bin/env python3
"""Tests for ui/quality_scorecard.py — per-agent-specialty quality metrics."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config
import quality_scorecard


class TestQualityScorecardEmpty(unittest.TestCase):
    """Empty ledger: returns empty summary with proper shape."""

    def test_empty_ledger_file_missing(self):
        """Missing ledger file returns empty summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, 'STATE_DIR', Path(tmpdir)):
                result = quality_scorecard.get_quality_scorecard()
                self.assertEqual(result['specialties'], {})
                self.assertEqual(result['top_by_success'], [])
                self.assertEqual(result['top_by_retry'], [])
                self.assertEqual(result['skipped_lines'], 0)

    def test_empty_ledger_file_exists_but_empty(self):
        """Empty ledger file returns empty summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_dir = Path(tmpdir) / 'ledger'
            ledger_dir.mkdir(parents=True)
            ledger_file = ledger_dir / 'OUTCOMES-LEDGER.md'
            ledger_file.write_text('')

            with patch.object(config, 'STATE_DIR', Path(tmpdir)):
                result = quality_scorecard.get_quality_scorecard()
                self.assertEqual(result['specialties'], {})
                self.assertEqual(result['top_by_success'], [])
                self.assertEqual(result['top_by_retry'], [])


class TestQualityScorecardParsing(unittest.TestCase):
    """Ledger parsing: extracts per-agent-type quality metrics."""

    def setUp(self):
        """Create a temporary ledger for testing."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.ledger_dir = Path(self.tmpdir.name) / 'ledger'
        self.ledger_dir.mkdir(parents=True)
        self.ledger_file = self.ledger_dir / 'OUTCOMES-LEDGER.md'

    def tearDown(self):
        """Clean up temporary directory."""
        self.tmpdir.cleanup()

    def test_basic_ledger_parsing(self):
        """Basic ledger with haiku/sonnet entries parses correctly."""
        ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
| 2026-07-13T14:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 14000 | 4200 | OK |
| 2026-07-13T14:10:00Z | sonnet | claude-sonnet-4-5-20250929 | 85 | 28000 | 8100 | OK |
| 2026-07-13T14:15:00Z | haiku | claude-haiku-4-5-20251001 | 40 | 11000 | 3200 | FAILED |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'STATE_DIR', Path(self.tmpdir.name)):
            result = quality_scorecard.get_quality_scorecard()

        # Check haiku stats
        self.assertIn('haiku', result['specialties'])
        haiku_stats = result['specialties']['haiku']
        self.assertEqual(haiku_stats['total_runs'], 3)
        self.assertEqual(haiku_stats['success_count'], 2)
        self.assertEqual(haiku_stats['failed_count'], 1)
        self.assertAlmostEqual(haiku_stats['success_rate'], 2/3, places=3)

        # Check sonnet stats
        self.assertIn('sonnet', result['specialties'])
        sonnet_stats = result['specialties']['sonnet']
        self.assertEqual(sonnet_stats['total_runs'], 1)
        self.assertEqual(sonnet_stats['success_count'], 1)
        self.assertEqual(sonnet_stats['failed_count'], 0)
        self.assertAlmostEqual(sonnet_stats['success_rate'], 1.0, places=3)

    def test_verdict_counts(self):
        """Verdict counters correctly tally OK/FAILED/EMPTY/HUNG."""
        ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
| 2026-07-13T14:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 14000 | 4200 | FAILED |
| 2026-07-13T14:10:00Z | haiku | claude-haiku-4-5-20251001 | 60 | 16000 | 5000 | EMPTY |
| 2026-07-13T14:15:00Z | haiku | claude-haiku-4-5-20251001 | 70 | 18000 | 5500 | HUNG |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'STATE_DIR', Path(self.tmpdir.name)):
            result = quality_scorecard.get_quality_scorecard()

        haiku_stats = result['specialties']['haiku']
        self.assertEqual(haiku_stats['total_runs'], 4)
        self.assertEqual(haiku_stats['success_count'], 1)
        self.assertEqual(haiku_stats['failed_count'], 1)
        self.assertEqual(haiku_stats['empty_count'], 1)
        self.assertEqual(haiku_stats['hung_count'], 1)

    def test_repair_cycle_detection(self):
        """Repair cycles (failure followed by success) are counted."""
        ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | FAILED |
| 2026-07-13T14:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 14000 | 4200 | OK |
| 2026-07-13T14:10:00Z | haiku | claude-haiku-4-5-20251001 | 60 | 16000 | 5000 | FAILED |
| 2026-07-13T14:15:00Z | haiku | claude-haiku-4-5-20251001 | 70 | 18000 | 5500 | OK |
| 2026-07-13T14:20:00Z | haiku | claude-haiku-4-5-20251001 | 80 | 20000 | 6000 | OK |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'STATE_DIR', Path(self.tmpdir.name)):
            result = quality_scorecard.get_quality_scorecard()

        haiku_stats = result['specialties']['haiku']
        # 2 repair cycles: FAILED->OK (2 times)
        self.assertEqual(haiku_stats['repair_count'], 2)
        self.assertAlmostEqual(haiku_stats['retry_frequency'], 2/5, places=3)

    def test_malformed_lines_skipped(self):
        """Malformed lines are skipped and counted."""
        ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
malformed line without pipes
| 2026-07-13T14:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | notanumber | 4200 | OK |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'STATE_DIR', Path(self.tmpdir.name)):
            result = quality_scorecard.get_quality_scorecard()

        # Should still parse the valid line
        self.assertEqual(result['specialties']['haiku']['total_runs'], 1)
        # Should count 2 skipped lines (one without pipes, one with non-numeric tokens)
        self.assertEqual(result['skipped_lines'], 2)
        # Skipped lines should not affect the valid line count
        self.assertEqual(result['specialties']['haiku']['success_count'], 1)

    def test_ranking_by_success_rate(self):
        """top_by_success ranked by success_rate descending."""
        ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
| 2026-07-13T14:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 14000 | 4200 | FAILED |
| 2026-07-13T14:10:00Z | sonnet | claude-sonnet-4-5-20250929 | 85 | 28000 | 8100 | OK |
| 2026-07-13T14:15:00Z | sonnet | claude-sonnet-4-5-20250929 | 90 | 30000 | 9000 | OK |
| 2026-07-13T14:20:00Z | orchestrator | claude-opus-4-20250805 | 120 | 50000 | 12000 | OK |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'STATE_DIR', Path(self.tmpdir.name)):
            result = quality_scorecard.get_quality_scorecard()

        top_by_success = result['top_by_success']
        # sonnet and orchestrator both have 100% success, haiku has 50%
        # sonnet and orchestrator should be first (tied at 100%), then haiku
        self.assertEqual(len(top_by_success), 3)
        self.assertEqual(top_by_success[0]['agent_type'], 'sonnet')
        self.assertEqual(top_by_success[0]['success_rate'], 1.0)
        self.assertEqual(top_by_success[2]['agent_type'], 'haiku')
        self.assertAlmostEqual(top_by_success[2]['success_rate'], 0.5, places=3)

    def test_ranking_by_retry_frequency(self):
        """top_by_retry ranked by retry_frequency descending."""
        ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | FAILED |
| 2026-07-13T14:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 14000 | 4200 | OK |
| 2026-07-13T14:10:00Z | sonnet | claude-sonnet-4-5-20250929 | 85 | 28000 | 8100 | OK |
| 2026-07-13T14:15:00Z | sonnet | claude-sonnet-4-5-20250929 | 90 | 30000 | 9000 | OK |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'STATE_DIR', Path(self.tmpdir.name)):
            result = quality_scorecard.get_quality_scorecard()

        top_by_retry = result['top_by_retry']
        # haiku has 1 repair in 2 runs (50%), sonnet has 0 repairs in 2 runs (0%)
        self.assertEqual(top_by_retry[0]['agent_type'], 'haiku')
        self.assertAlmostEqual(top_by_retry[0]['retry_frequency'], 0.5, places=3)
        self.assertEqual(top_by_retry[1]['agent_type'], 'sonnet')
        self.assertAlmostEqual(top_by_retry[1]['retry_frequency'], 0.0, places=3)


class TestQualityScorecardShape(unittest.TestCase):
    """Response shape: always returns documented structure."""

    def test_response_shape_empty(self):
        """Empty result has all required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(config, 'STATE_DIR', Path(tmpdir)):
                result = quality_scorecard.get_quality_scorecard()
                self.assertIn('specialties', result)
                self.assertIn('top_by_success', result)
                self.assertIn('top_by_retry', result)
                self.assertIn('skipped_lines', result)

    def test_response_shape_with_data(self):
        """Populated result has all required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_dir = Path(tmpdir) / 'ledger'
            ledger_dir.mkdir(parents=True)
            ledger_file = ledger_dir / 'OUTCOMES-LEDGER.md'
            ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
"""
            ledger_file.write_text(ledger_content)

            with patch.object(config, 'STATE_DIR', Path(tmpdir)):
                result = quality_scorecard.get_quality_scorecard()
                # Check specialties entry structure
                self.assertIn('haiku', result['specialties'])
                haiku = result['specialties']['haiku']
                self.assertIn('total_runs', haiku)
                self.assertIn('success_count', haiku)
                self.assertIn('failed_count', haiku)
                self.assertIn('empty_count', haiku)
                self.assertIn('hung_count', haiku)
                self.assertIn('success_rate', haiku)
                self.assertIn('repair_count', haiku)
                self.assertIn('retry_frequency', haiku)


class TestQualityScorecardJSON(unittest.TestCase):
    """JSON serialization: result is JSON-serializable."""

    def test_json_serializable(self):
        """Result can be serialized to JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_dir = Path(tmpdir) / 'ledger'
            ledger_dir.mkdir(parents=True)
            ledger_file = ledger_dir / 'OUTCOMES-LEDGER.md'
            ledger_content = """| timestamp | agent_type | model | duration_seconds | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-13T14:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 12000 | 3500 | OK |
"""
            ledger_file.write_text(ledger_content)

            with patch.object(config, 'STATE_DIR', Path(tmpdir)):
                result = quality_scorecard.get_quality_scorecard()
                json_str = json.dumps(result)
                self.assertIsInstance(json_str, str)
                # Round-trip
                parsed = json.loads(json_str)
                self.assertIn('haiku', parsed['specialties'])


if __name__ == '__main__':
    unittest.main()
