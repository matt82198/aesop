#!/usr/bin/env python3
"""Tests for ui/wave_audit_tail.py — audit tail verdict extraction and validation."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

UI_DIR = Path(__file__).parent.parent / "ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))

import config
import wave_audit_tail


class TestWaveAuditTailLedgerParsing(unittest.TestCase):
    """Ledger parsing: extracts verdicts from correct column (7) and validates."""

    def setUp(self):
        """Create a temporary ledger for testing."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)
        self.ledger_dir = self.state_dir / 'ledger'
        self.ledger_dir.mkdir(parents=True)
        self.ledger_file = self.ledger_dir / 'OUTCOMES-LEDGER.md'

    def tearDown(self):
        """Clean up temporary directory."""
        self.tmpdir.cleanup()

    def test_correct_9column_verdict_extraction(self):
        """9-column ledger with correct verdict in column 7 extracts correctly."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | OK | build | 1 |
| 2026-07-21T12:05:00Z | sonnet | claude-sonnet-4-5-20250929 | 60 | 2000 | 800 | FAILED | verify | 1 |
| 2026-07-21T12:10:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1500 | 600 | EMPTY | repair | 1 |
| 2026-07-21T12:15:00Z | opus | claude-opus-4-1-20250805 | 70 | 3000 | 1000 | HUNG | build | 2 |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'LEDGER_FILE', self.ledger_file):
            verdicts = wave_audit_tail._parse_ledger_recent_verdicts()

        # Verify all verdicts were extracted correctly
        self.assertEqual(len(verdicts), 4)
        verdict_strs = [v['verdict'] for v in verdicts]
        self.assertIn('OK', verdict_strs)
        self.assertIn('FAILED', verdict_strs)
        self.assertIn('EMPTY', verdict_strs)
        self.assertIn('HUNG', verdict_strs)

    def test_forged_verdict_skipped(self):
        """Forged/invalid verdicts are skipped and not included in results."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | OK | build | 1 |
| 2026-07-21T12:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1200 | 600 | FORGED_VERDICT | verify | 1 |
| 2026-07-21T12:10:00Z | haiku | claude-haiku-4-5-20251001 | 55 | 1100 | 550 | FAILED | repair | 1 |
| 2026-07-21T12:15:00Z | haiku | claude-haiku-4-5-20251001 | 60 | 1300 | 650 | INJECTED_ATTACK | build | 2 |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'LEDGER_FILE', self.ledger_file):
            verdicts = wave_audit_tail._parse_ledger_recent_verdicts()

        # Only valid verdicts should be included
        self.assertEqual(len(verdicts), 2)
        verdict_strs = [v['verdict'] for v in verdicts]
        self.assertIn('OK', verdict_strs)
        self.assertIn('FAILED', verdict_strs)
        # Invalid verdicts should NOT be present
        self.assertNotIn('FORGED_VERDICT', verdict_strs)
        self.assertNotIn('INJECTED_ATTACK', verdict_strs)

    def test_legacy_7column_format_supported(self):
        """Legacy 7-column format (without phase/wave) is also supported."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | OK |
| 2026-07-21T12:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1200 | 600 | FAILED |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'LEDGER_FILE', self.ledger_file):
            verdicts = wave_audit_tail._parse_ledger_recent_verdicts()

        # Both rows should parse correctly
        self.assertEqual(len(verdicts), 2)
        verdict_strs = [v['verdict'] for v in verdicts]
        self.assertIn('OK', verdict_strs)
        self.assertIn('FAILED', verdict_strs)

    def test_mixed_7_and_9column_rows(self):
        """Ledger with mixed 7-column (legacy) and 9-column rows parses both."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | OK |
| 2026-07-21T12:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1200 | 600 | FAILED | build | 1 |
| 2026-07-21T12:10:00Z | haiku | claude-haiku-4-5-20251001 | 55 | 1100 | 550 | EMPTY |
| 2026-07-21T12:15:00Z | opus | claude-opus-4-1-20250805 | 70 | 3000 | 1000 | HUNG | verify | 2 |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'LEDGER_FILE', self.ledger_file):
            verdicts = wave_audit_tail._parse_ledger_recent_verdicts()

        # All 4 valid rows should parse
        self.assertEqual(len(verdicts), 4)
        verdict_counts = {}
        for v in verdicts:
            verdict_counts[v['verdict']] = verdict_counts.get(v['verdict'], 0) + 1
        self.assertEqual(verdict_counts['OK'], 1)
        self.assertEqual(verdict_counts['FAILED'], 1)
        self.assertEqual(verdict_counts['EMPTY'], 1)
        self.assertEqual(verdict_counts['HUNG'], 1)

    def test_verdict_case_insensitive_upper(self):
        """Verdicts are normalized to uppercase (OK/FAILED/EMPTY/HUNG)."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | ok | build | 1 |
| 2026-07-21T12:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1200 | 600 | Failed | verify | 1 |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'LEDGER_FILE', self.ledger_file):
            verdicts = wave_audit_tail._parse_ledger_recent_verdicts()

        # Should extract 2 verdicts and normalize to uppercase
        self.assertEqual(len(verdicts), 2)
        verdict_strs = [v['verdict'] for v in verdicts]
        self.assertIn('OK', verdict_strs)
        self.assertIn('FAILED', verdict_strs)

    def test_empty_ledger_file_missing(self):
        """Missing ledger file returns empty verdict list."""
        # Don't create the ledger file
        with patch.object(config, 'LEDGER_FILE', self.ledger_file):
            verdicts = wave_audit_tail._parse_ledger_recent_verdicts()

        self.assertEqual(verdicts, [])

    def test_malformed_row_skipped(self):
        """Malformed rows without proper pipe delimiters are skipped."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | OK | build | 1 |
This is a malformed line without pipes that should be skipped
| 2026-07-21T12:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1200 | 600 | FAILED | verify | 1 |
"""
        self.ledger_file.write_text(ledger_content)

        with patch.object(config, 'LEDGER_FILE', self.ledger_file):
            verdicts = wave_audit_tail._parse_ledger_recent_verdicts()

        # Only valid rows should be included
        self.assertEqual(len(verdicts), 2)


class TestWaveAuditTailIntegration(unittest.TestCase):
    """Integration: full audit tail data collection and filtering."""

    def setUp(self):
        """Create temporary files for testing."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)
        self.ledger_dir = self.state_dir / 'ledger'
        self.ledger_dir.mkdir(parents=True)
        self.ledger_file = self.ledger_dir / 'OUTCOMES-LEDGER.md'
        self.backlog_file = self.state_dir / 'AUDIT-BACKLOG.md'

    def tearDown(self):
        """Clean up temporary directory."""
        self.tmpdir.cleanup()

    def test_get_wave_audit_tail_with_valid_verdicts(self):
        """get_wave_audit_tail returns only valid verdicts in result."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | OK | build | 1 |
| 2026-07-21T12:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1200 | 600 | FAILED | verify | 1 |
"""
        self.ledger_file.write_text(ledger_content)
        self.backlog_file.write_text("")  # Empty backlog

        with patch.object(config, 'LEDGER_FILE', self.ledger_file), \
             patch.object(config, 'AUDIT_BACKLOG_FILE', self.backlog_file):
            result = wave_audit_tail.get_wave_audit_tail()

        self.assertTrue(result['available'])
        self.assertGreater(len(result['audit_items']), 0)
        # Check that verdict items are present
        verdict_items = [item for item in result['audit_items'] if item.get('type') == 'verdict']
        self.assertEqual(len(verdict_items), 2)

    def test_get_wave_audit_tail_filters_forged_verdicts(self):
        """get_wave_audit_tail filters out forged verdicts from results."""
        ledger_content = """| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-07-21T12:00:00Z | haiku | claude-haiku-4-5-20251001 | 45 | 1000 | 500 | OK | build | 1 |
| 2026-07-21T12:05:00Z | haiku | claude-haiku-4-5-20251001 | 50 | 1200 | 600 | FORGED | verify | 1 |
| 2026-07-21T12:10:00Z | haiku | claude-haiku-4-5-20251001 | 55 | 1100 | 550 | FAILED | repair | 1 |
"""
        self.ledger_file.write_text(ledger_content)
        self.backlog_file.write_text("")

        with patch.object(config, 'LEDGER_FILE', self.ledger_file), \
             patch.object(config, 'AUDIT_BACKLOG_FILE', self.backlog_file):
            result = wave_audit_tail.get_wave_audit_tail()

        verdict_items = [item for item in result['audit_items'] if item.get('type') == 'verdict']
        # Should only have 2 valid verdicts (OK and FAILED), not the FORGED one
        self.assertEqual(len(verdict_items), 2)
        verdicts = [item['verdict'] for item in verdict_items]
        self.assertNotIn('FORGED', verdicts)
        self.assertIn('OK', verdicts)
        self.assertIn('FAILED', verdicts)


if __name__ == '__main__':
    unittest.main()
