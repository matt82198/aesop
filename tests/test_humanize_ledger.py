#!/usr/bin/env python3
"""
Tests for humanize_ledger.py — Append-only JSONL ledger for humanization fixes.

Behavioral tests: align before/after text, record edits, verify append-only,
summarize with aggregation. Tests use temp directories; dummy lint fallback.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Tuple
import sys

# Add tools to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.humanize_ledger import (
    align_sentences,
    record_edits,
    summarize_ledger,
    split_sentences,
)


class TestHumanizeLedger(unittest.TestCase):
    """Test suite for humanize_ledger module."""

    def test_split_sentences(self):
        """Test sentence splitting on basic punctuation."""
        text = "First sentence. Second one! Third?"
        sentences = split_sentences(text)
        self.assertEqual(len(sentences), 3, f"Expected 3 sentences, got {len(sentences)}: {sentences}")
        self.assertEqual(sentences[0], "First sentence.")
        self.assertEqual(sentences[1], "Second one!")
        self.assertEqual(sentences[2], "Third?")

    def test_align_sentences_simple_replace(self):
        """Test sentence alignment with a simple replacement."""
        before = "The store runs on Squarespace. There is no backend."
        after = "The store runs on Squarespace. There is absolutely no backend."

        alignments = align_sentences(before, after)
        # Should have 2 alignments (one equal, one replace)
        self.assertGreater(len(alignments), 0, f"Expected alignments, got: {alignments}")

        # Find the replace operation
        replaces = [a for a in alignments if a[2] == "replace"]
        self.assertGreater(len(replaces), 0, f"Expected at least one replace, got: {alignments}")

        before_sent, after_sent, op = replaces[0]
        self.assertIn("no backend", before_sent)
        self.assertIn("absolutely no backend", after_sent)

    def test_align_sentences_delete(self):
        """Test sentence alignment with deletion."""
        before = "First sentence. Second sentence. Third sentence."
        after = "First sentence. Third sentence."

        alignments = align_sentences(before, after)
        deletes = [a for a in alignments if a[2] == "delete"]
        self.assertGreater(len(deletes), 0, f"Expected delete operation, got: {alignments}")

    def test_align_sentences_insert(self):
        """Test sentence alignment with insertion."""
        before = "First sentence. Third sentence."
        after = "First sentence. Second sentence. Third sentence."

        alignments = align_sentences(before, after)
        inserts = [a for a in alignments if a[2] == "insert"]
        self.assertGreater(len(inserts), 0, f"Expected insert operation, got: {alignments}")

    def test_record_single_edit(self):
        """Test recording when there are no changes (should produce no entries)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create before/after files with identical content
            before_file = tmpdir / "before.txt"
            after_file = tmpdir / "after.txt"
            ledger_path = tmpdir / "test-ledger.jsonl"

            before_file.write_text("The store runs on Squarespace. There is no backend.", encoding="utf-8")
            after_file.write_text("The store runs on Squarespace. There is no backend.", encoding="utf-8")

            # Record with no changes (should produce no entries)
            rc = record_edits(
                before_file=str(before_file),
                after_file=str(after_file),
                ledger_path=str(ledger_path),
            )

            self.assertEqual(rc, 0, f"Expected rc=0, got {rc}")
            # When there are no changes, the ledger file may not be created or be empty
            if ledger_path.exists():
                lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
                self.assertEqual(len([l for l in lines if l.strip()]), 0, f"Expected no entries, got: {lines}")

    def test_record_with_change(self):
        """Test recording with an actual sentence change."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            before_file = tmpdir / "before.txt"
            after_file = tmpdir / "after.txt"
            ledger_path = tmpdir / "test-ledger.jsonl"

            before_file.write_text("This is bad.", encoding="utf-8")
            after_file.write_text("This is good.", encoding="utf-8")

            rc = record_edits(
                before_file=str(before_file),
                after_file=str(after_file),
                ledger_path=str(ledger_path),
                detector="test",
                note="improved",
            )

            self.assertEqual(rc, 0, f"Expected rc=0, got {rc}")
            self.assertTrue(ledger_path.exists())

            lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
            entries = [json.loads(l) for l in lines if l.strip()]

            self.assertGreater(len(entries), 0, f"Expected at least one entry, got: {lines}")
            entry = entries[0]
            self.assertEqual(entry["before"], "This is bad.")
            self.assertEqual(entry["after"], "This is good.")
            self.assertEqual(entry["op"], "replace")
            self.assertEqual(entry["detector"], "test")
            self.assertEqual(entry["note"], "improved")

    def test_append_only(self):
        """Test that ledger is append-only: multiple records increase entry count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            before_file = tmpdir / "before.txt"
            after_file = tmpdir / "after.txt"
            ledger_path = tmpdir / "test-ledger.jsonl"

            # First edit
            before_file.write_text("First sentence.", encoding="utf-8")
            after_file.write_text("First revised sentence.", encoding="utf-8")

            rc1 = record_edits(
                before_file=str(before_file),
                after_file=str(after_file),
                ledger_path=str(ledger_path),
            )

            self.assertEqual(rc1, 0)
            lines1 = [l for l in ledger_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
            count1 = len(lines1)
            self.assertGreater(count1, 0, f"Expected entries after first record, got: {lines1}")

            # Second edit (append)
            before_file.write_text("Second sentence.", encoding="utf-8")
            after_file.write_text("Second revised sentence.", encoding="utf-8")

            rc2 = record_edits(
                before_file=str(before_file),
                after_file=str(after_file),
                ledger_path=str(ledger_path),
            )

            self.assertEqual(rc2, 0)
            lines2 = [l for l in ledger_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
            count2 = len(lines2)

            self.assertGreater(count2, count1, f"Append-only failed: {count1} -> {count2}, expected increase")

    def test_summarize_basic(self):
        """Test summarize on a simple ledger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a simple ledger manually
            ledger_path = tmpdir / "test-ledger.jsonl"

            entries = [
                {
                    "ts": "2026-10-01T10:00:00Z",
                    "doc": "test",
                    "before": "bad sentence",
                    "after": "good sentence",
                    "op": "replace",
                    "rules_cleared": ["ai-lexicon"],
                    "rules_introduced": [],
                    "score_before": 50,
                    "score_after": 75,
                    "detector": "test",
                    "note": "",
                },
            ]

            for entry in entries:
                ledger_path.write_text(json.dumps(entry) + "\n", encoding="utf-8", errors="replace")

            # Summarize as text (default)
            rc = summarize_ledger(str(ledger_path))
            self.assertEqual(rc, 0)

            # Summarize as JSON
            rc = summarize_ledger(str(ledger_path), json_output=True)
            self.assertEqual(rc, 0)

            # Summarize as Markdown
            rc = summarize_ledger(str(ledger_path), markdown_output=True)
            self.assertEqual(rc, 0)

    def test_summarize_missing_ledger(self):
        """Test summarize on non-existent ledger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            ledger_path = tmpdir / "nonexistent.jsonl"

            rc = summarize_ledger(str(ledger_path))
            self.assertEqual(rc, 2, f"Expected rc=2 for missing ledger, got {rc}")

    def test_net_score_movement_counts_each_recording_once(self):
        """Two sentence edits from one recording with scores 35.5 -> 7.4 move the net by -28.1, not -56.2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            before = tmpdir / "b.md"
            after = tmpdir / "a.md"
            ledger = tmpdir / "l.jsonl"

            before.write_text("The first sentence is long and formal. The second sentence is also long and formal.", encoding="utf-8")
            after.write_text("First one. Short now. Second one, shorter.", encoding="utf-8")
            rc = record_edits(str(before), str(after), str(ledger), score_before=35.5, score_after=7.4, detector="zerogpt")
            self.assertEqual(rc, 0)
            lines = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertGreaterEqual(len(lines), 2)

            # Capture stdout for summarize_ledger output
            out_buffer = io.StringIO()
            with contextlib.redirect_stdout(out_buffer):
                rc = summarize_ledger(str(ledger), json_output=True)
            self.assertEqual(rc, 0)

            summary = json.loads(out_buffer.getvalue())
            self.assertAlmostEqual(summary["net_score_movement"], -28.1, places=6)


def run_suite():
    """Run all tests and return exit code."""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestHumanizeLedger)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return (0, [test.id().split()[-1].split('.')[-1] for test in suite]) if result.wasSuccessful() else (1, [])


if __name__ == "__main__":
    result = run_suite()
    if isinstance(result, tuple):
        exit_code, test_names = result
        sys.exit(exit_code)
    else:
        sys.exit(result)
