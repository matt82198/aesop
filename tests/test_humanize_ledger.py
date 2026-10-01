#!/usr/bin/env python3
"""
Tests for humanize_ledger.py — Append-only JSONL ledger for humanization fixes.

Behavioral tests: align before/after text, record edits, verify append-only,
summarize with aggregation. Tests use temp directories; dummy lint fallback.
"""

import json
import os
import tempfile
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


def test_split_sentences():
    """Test sentence splitting on basic punctuation."""
    text = "First sentence. Second one! Third?"
    sentences = split_sentences(text)
    assert len(sentences) == 3, f"Expected 3 sentences, got {len(sentences)}: {sentences}"
    assert sentences[0] == "First sentence."
    assert sentences[1] == "Second one!"
    assert sentences[2] == "Third?"
    print("✓ test_split_sentences passed")


def test_align_sentences_simple_replace():
    """Test sentence alignment with a simple replacement."""
    before = "The store runs on Squarespace. There is no backend."
    after = "The store runs on Squarespace. There is absolutely no backend."

    alignments = align_sentences(before, after)
    # Should have 2 alignments (one equal, one replace)
    assert len(alignments) > 0, f"Expected alignments, got: {alignments}"

    # Find the replace operation
    replaces = [a for a in alignments if a[2] == "replace"]
    assert len(replaces) > 0, f"Expected at least one replace, got: {alignments}"

    before_sent, after_sent, op = replaces[0]
    assert "no backend" in before_sent
    assert "absolutely no backend" in after_sent
    print("✓ test_align_sentences_simple_replace passed")


def test_align_sentences_delete():
    """Test sentence alignment with deletion."""
    before = "First sentence. Second sentence. Third sentence."
    after = "First sentence. Third sentence."

    alignments = align_sentences(before, after)
    deletes = [a for a in alignments if a[2] == "delete"]
    assert len(deletes) > 0, f"Expected delete operation, got: {alignments}"
    print("✓ test_align_sentences_delete passed")


def test_align_sentences_insert():
    """Test sentence alignment with insertion."""
    before = "First sentence. Third sentence."
    after = "First sentence. Second sentence. Third sentence."

    alignments = align_sentences(before, after)
    inserts = [a for a in alignments if a[2] == "insert"]
    assert len(inserts) > 0, f"Expected insert operation, got: {alignments}"
    print("✓ test_align_sentences_insert passed")


def test_record_single_edit():
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

        assert rc == 0, f"Expected rc=0, got {rc}"
        # When there are no changes, the ledger file may not be created or be empty
        if ledger_path.exists():
            lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
            assert len([l for l in lines if l.strip()]) == 0, f"Expected no entries, got: {lines}"
        print("✓ test_record_single_edit (no changes) passed")


def test_record_with_change():
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

        assert rc == 0, f"Expected rc=0, got {rc}"
        assert ledger_path.exists()

        lines = ledger_path.read_text(encoding="utf-8").strip().split("\n")
        entries = [json.loads(l) for l in lines if l.strip()]

        assert len(entries) > 0, f"Expected at least one entry, got: {lines}"
        entry = entries[0]
        assert entry["before"] == "This is bad."
        assert entry["after"] == "This is good."
        assert entry["op"] == "replace"
        assert entry["detector"] == "test"
        assert entry["note"] == "improved"
        print("✓ test_record_with_change passed")


def test_append_only():
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

        assert rc1 == 0
        lines1 = [l for l in ledger_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
        count1 = len(lines1)
        assert count1 > 0, f"Expected entries after first record, got: {lines1}"

        # Second edit (append)
        before_file.write_text("Second sentence.", encoding="utf-8")
        after_file.write_text("Second revised sentence.", encoding="utf-8")

        rc2 = record_edits(
            before_file=str(before_file),
            after_file=str(after_file),
            ledger_path=str(ledger_path),
        )

        assert rc2 == 0
        lines2 = [l for l in ledger_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
        count2 = len(lines2)

        assert count2 > count1, f"Append-only failed: {count1} -> {count2}, expected increase"
        print(f"✓ test_append_only passed ({count1} -> {count2} entries)")


def test_summarize_basic():
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
        assert rc == 0

        # Summarize as JSON
        rc = summarize_ledger(str(ledger_path), json_output=True)
        assert rc == 0

        # Summarize as Markdown
        rc = summarize_ledger(str(ledger_path), markdown_output=True)
        assert rc == 0

        print("✓ test_summarize_basic passed")


def test_summarize_missing_ledger():
    """Test summarize on non-existent ledger."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        ledger_path = tmpdir / "nonexistent.jsonl"

        rc = summarize_ledger(str(ledger_path))
        assert rc == 2, f"Expected rc=2 for missing ledger, got {rc}"
        print("✓ test_summarize_missing_ledger passed")


def run_suite():
    """Run all tests and return exit code."""
    tests = [
        test_split_sentences,
        test_align_sentences_simple_replace,
        test_align_sentences_delete,
        test_align_sentences_insert,
        test_record_single_edit,
        test_record_with_change,
        test_append_only,
        test_summarize_basic,
        test_summarize_missing_ledger,
    ]

    print("Running humanize_ledger tests...\n")

    failed = []
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed.append(test.__name__)
        except Exception as e:
            print(f"✗ {test.__name__} ERROR: {e}")
            failed.append(test.__name__)

    print(f"\n{len(tests) - len(failed)}/{len(tests)} tests passed")

    if failed:
        print(f"Failed: {', '.join(failed)}")
        return 1

    return 0, [test.__name__ for test in tests]


if __name__ == "__main__":
    result = run_suite()
    if isinstance(result, tuple):
        exit_code, test_names = result
        sys.exit(exit_code)
    else:
        sys.exit(result)


def test_net_score_movement_counts_each_recording_once(tmp_path, capsys):
    """Two sentence edits from one recording with scores 35.5 -> 7.4 move the net by -28.1, not -56.2."""
    before = tmp_path / "b.md"; after = tmp_path / "a.md"; ledger = tmp_path / "l.jsonl"
    before.write_text("The first sentence is long and formal. The second sentence is also long and formal.", encoding="utf-8")
    after.write_text("First one. Short now. Second one, shorter.", encoding="utf-8")
    rc = record_edits(str(before), str(after), str(ledger), score_before=35.5, score_after=7.4, detector="zerogpt")
    assert rc == 0
    lines = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) >= 2
    capsys.readouterr()
    assert summarize_ledger(str(ledger), json_output=True) == 0
    summary = json.loads(capsys.readouterr().out)
    assert abs(summary["net_score_movement"] - (-28.1)) < 1e-6
