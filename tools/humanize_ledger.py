#!/usr/bin/env python3
"""
Humanize Ledger — Records before/after text edits and their rule improvements.
INDEX: Append-only JSONL ledger for humanization fixes; records sentence-level edits with rules cleared/introduced and score movement for pattern learning.

Append-only JSONL ledger capturing sentence-level edits with rule clearances and
score movement. Enables pattern learning from real human-written text.

Usage:
  python tools/humanize_ledger.py record --before FILE --after FILE --ledger PATH \\
    [--score-before N] [--score-after N] [--detector NAME] [--note TEXT] [--reference FILE]

  python tools/humanize_ledger.py summarize --ledger PATH [--json] [--markdown]
"""

import argparse
import difflib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

# INDEX: humanize_ledger.py — Append-only JSONL ledger for humanization fixes
# with sentence-level alignments, rules cleared/introduced, and pattern learning.

# Try to import split_sentences and lint_text from humanize_lint
_lint_text_available = False
_import_warning_shown = False

try:
    from tools.humanize_lint import split_sentences, lint_text
    _lint_text_available = True
except ImportError:
    try:
        # Try sibling import if tools is not a package
        from humanize_lint import split_sentences, lint_text
        _lint_text_available = True
    except ImportError:
        # Fallback: provide stub implementations
        def split_sentences(text: str) -> List[str]:
            """Fallback sentence splitter: split on [.!?] followed by space + capital."""
            # Simple regex-based fallback for when humanize_lint is not yet available
            sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z"\'])', text)
            return [s.strip() for s in sentences if s.strip()]

        def lint_text(text: str, reference: Optional[str] = None) -> List[Any]:
            """Fallback lint function: returns empty findings until humanize_lint is available."""
            return []


def _ensure_lint_warning():
    """Print a one-time warning if lint_text is unavailable."""
    global _import_warning_shown
    if not _lint_text_available and not _import_warning_shown:
        print("WARNING: humanize_lint module not found. Linting unavailable; entries will have empty rules.", file=sys.stderr)
        _import_warning_shown = True


def resolve_state_root(override: Optional[str] = None) -> Path:
    """Resolve AESOP_STATE_ROOT, defaulting to ./state."""
    if override:
        return Path(override)
    env_root = os.environ.get("AESOP_STATE_ROOT")
    if env_root:
        return Path(env_root)
    return Path("./state")


def align_sentences(before_text: str, after_text: str) -> List[Tuple[str, str, str]]:
    """
    Align before/after sentences using difflib.SequenceMatcher.
    Returns list of (before_sentence, after_sentence, operation) where operation is
    'replace', 'delete', or 'insert'.
    """
    before_sents = split_sentences(before_text)
    after_sents = split_sentences(after_text)

    matcher = difflib.SequenceMatcher(None, before_sents, after_sents)
    alignments = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # No change
            for before_sent, after_sent in zip(before_sents[i1:i2], after_sents[j1:j2]):
                alignments.append((before_sent, after_sent, "equal"))
        elif tag == "replace":
            # Sentence(s) replaced
            before_part = before_sents[i1:i2]
            after_part = after_sents[j1:j2]
            # Pair them up; extras are treated as inserts/deletes
            for before_sent, after_sent in zip(before_part, after_part):
                alignments.append((before_sent, after_sent, "replace"))
            # Handle unmatched sentences
            if len(before_part) > len(after_part):
                for before_sent in before_part[len(after_part):]:
                    alignments.append((before_sent, "", "delete"))
            elif len(after_part) > len(before_part):
                for after_sent in after_part[len(before_part):]:
                    alignments.append(("", after_sent, "insert"))
        elif tag == "delete":
            for before_sent in before_sents[i1:i2]:
                alignments.append((before_sent, "", "delete"))
        elif tag == "insert":
            for after_sent in after_sents[j1:j2]:
                alignments.append(("", after_sent, "insert"))

    return alignments


def get_sentence_char_offsets(text: str) -> List[Tuple[str, int, int]]:
    """
    Return list of (sentence, start_offset, end_offset) for each sentence in text.
    Offsets are absolute character positions in the original text.
    """
    sentences = split_sentences(text)
    offsets = []
    search_pos = 0

    for sent in sentences:
        # Find this sentence in the remaining text
        start = text.find(sent, search_pos)
        if start == -1:
            # Sentence not found, skip
            continue
        end = start + len(sent)
        offsets.append((sent, start, end))
        search_pos = end

    return offsets


def get_rules_in_range(findings: List[Any], start: int, end: int) -> List[str]:
    """
    Given a list of findings (with start/end character offsets),
    return the set of rule IDs for findings that overlap with [start, end).
    """
    rules = set()
    for finding in findings:
        # Check if finding overlaps with the range [start, end)
        if finding.end > start and finding.start < end:
            if hasattr(finding, 'rule'):
                rules.add(finding.rule)
            elif isinstance(finding, dict) and 'rule' in finding:
                rules.add(finding['rule'])
    return sorted(list(rules))


def record_edits(
    before_file: str,
    after_file: str,
    ledger_path: str,
    score_before: Optional[float] = None,
    score_after: Optional[float] = None,
    detector: str = "manual",
    note: str = "",
    reference_file: Optional[str] = None,
) -> int:
    """
    Record edits from before/after files to the ledger.
    Writes one JSONL entry per sentence change.

    Lints the WHOLE before and after documents (not individual sentences),
    then attributes findings to sentences based on character offsets.
    Rules cleared/introduced are determined by comparing the rules
    in each sentence's position before vs. after.
    """
    _ensure_lint_warning()

    try:
        before_text = Path(before_file).read_text(encoding="utf-8")
        after_text = Path(after_file).read_text(encoding="utf-8")
    except Exception as e:
        print(f"ERROR: Failed to read files: {e}", file=sys.stderr)
        return 2

    # Create ledger directory if it doesn't exist
    ledger = Path(ledger_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)

    reference_text = None
    if reference_file:
        try:
            reference_text = Path(reference_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"WARNING: Failed to read reference file: {e}", file=sys.stderr)

    # Run lint on the WHOLE before and after documents
    before_findings = lint_text(before_text, reference_text) if _lint_text_available else []
    after_findings = lint_text(after_text, reference_text) if _lint_text_available else []

    # Record the availability of lint for debugging
    lint_status = "available" if _lint_text_available else "unavailable"

    # Get sentence alignments
    alignments = align_sentences(before_text, after_text)

    # Get character offsets for sentences in before and after documents
    before_offsets = get_sentence_char_offsets(before_text)
    after_offsets = get_sentence_char_offsets(after_text)

    # Create maps from sentence text to (start, end) for quick lookup
    before_offset_map = {sent: (start, end) for sent, start, end in before_offsets}
    after_offset_map = {sent: (start, end) for sent, start, end in after_offsets}

    entries_written = 0
    for before_sent, after_sent, op in alignments:
        # Only record changes (not equal sentences)
        if op == "equal":
            continue

        rules_cleared = []
        rules_introduced = []

        # Get character offsets for this sentence
        before_range = before_offset_map.get(before_sent, (0, 0))
        after_range = after_offset_map.get(after_sent, (0, 0))

        # Get rules that apply to this sentence in before and after
        rules_before = set(get_rules_in_range(before_findings, before_range[0], before_range[1]))
        rules_after = set(get_rules_in_range(after_findings, after_range[0], after_range[1]))

        # Rules cleared: in before but not in after
        rules_cleared = sorted(list(rules_before - rules_after))
        # Rules introduced: in after but not in before
        rules_introduced = sorted(list(rules_after - rules_before))

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "doc": "cover_letter",  # placeholder; in real use this would be parameterized
            "before": before_sent,
            "after": after_sent,
            "op": op,
            "rules_cleared": rules_cleared,
            "rules_introduced": rules_introduced,
            "score_before": score_before,
            "score_after": score_after,
            "detector": detector,
            "note": note,
            "lint": lint_status,
        }

        # Append to ledger
        try:
            with open(ledger, "a", encoding="utf-8", errors="replace") as f:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            entries_written += 1
        except Exception as e:
            print(f"ERROR: Failed to write to ledger: {e}", file=sys.stderr)
            return 2

    print(f"Recorded {entries_written} edits to {ledger}")
    return 0


def summarize_ledger(ledger_path: str, json_output: bool = False, markdown_output: bool = False) -> int:
    """
    Summarize the ledger: aggregate fixes per rule, recent pairs, patterns learned.
    """
    ledger = Path(ledger_path)

    if not ledger.exists():
        print(f"Ledger not found: {ledger}", file=sys.stderr)
        return 2

    entries = []
    try:
        with open(ledger, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"ERROR: Failed to read ledger: {e}", file=sys.stderr)
        return 2

    if not entries:
        summary = {"total_entries": 0, "rules_by_frequency": {}, "patterns_learned": []}
    else:
        # Aggregate by rule
        rule_fixes = defaultdict(list)
        rule_frequency = defaultdict(int)

        for entry in entries:
            for rule in entry.get("rules_cleared", []):
                rule_frequency[rule] += 1
                rule_fixes[rule].append(
                    {
                        "before": entry["before"],
                        "after": entry["after"],
                        "ts": entry.get("ts", ""),
                    }
                )

        # Sort rules by frequency
        rules_by_frequency = sorted(rule_frequency.items(), key=lambda x: x[1], reverse=True)

        # Build patterns_learned: rules sorted by frequency, most recent fixes first
        patterns_learned = []
        for rule, freq in rules_by_frequency:
            recent = sorted(rule_fixes[rule], key=lambda x: x.get("ts", ""), reverse=True)[:10]
            patterns_learned.append(
                {
                    "rule": rule,
                    "occurrences": freq,
                    "recent_fixes": recent,
                }
            )

        # Net score movement: a document's detector score moves once per recording, not once per
        # sentence edit, so count each (doc, ts, before, after) once.
        score_deltas = []
        seen_docs = set()
        for entry in entries:
            before = entry.get("score_before")
            after = entry.get("score_after")
            if before is not None and after is not None:
                key = (entry.get("doc"), entry.get("detector"), entry.get("note"), before, after)  # one recording = one movement
                if key in seen_docs:
                    continue
                seen_docs.add(key)
                score_deltas.append(after - before)

        summary = {
            "total_entries": len(entries),
            "rules_by_frequency": dict(rules_by_frequency),
            "net_score_movement": sum(score_deltas) if score_deltas else None,
            "patterns_learned": patterns_learned,
        }

    if json_output:
        print(json.dumps(summary, indent=2, separators=(",", ": ")))
    elif markdown_output:
        print("# Humanize Ledger Summary\n")
        print(f"**Total Entries:** {summary['total_entries']}\n")

        if summary.get("net_score_movement") is not None:
            print(f"**Net Score Movement:** {summary['net_score_movement']:+.1f}\n")

        if summary.get("rules_by_frequency"):
            print("## Most Common Rules Cleared\n")
            for rule, count in sorted(summary["rules_by_frequency"].items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"- `{rule}`: {count} fixes")
            print()

        if summary.get("patterns_learned"):
            print("## Patterns Learned\n")
            for pattern in summary["patterns_learned"][:5]:  # Top 5 patterns
                print(f"### {pattern['rule']} ({pattern['occurrences']} fixes)\n")
                for fix in pattern["recent_fixes"][:3]:  # Show 3 most recent
                    print(f"- **Before:** {fix['before'][:80]}")
                    print(f"  **After:** {fix['after'][:80]}\n")
    else:
        # Text output (default)
        print(f"Ledger: {ledger}")
        print(f"Total entries: {summary['total_entries']}")
        if summary.get("rules_by_frequency"):
            print("\nTop rules by frequency:")
            for rule, count in list(summary["rules_by_frequency"].items())[:10]:
                print(f"  {rule}: {count}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Humanize Ledger - Record and summarize text improvement edits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    # record subcommand
    record_parser = subparsers.add_parser("record", help="Record before/after edits to ledger")
    record_parser.add_argument("--before", required=True, help="Path to before text file")
    record_parser.add_argument("--after", required=True, help="Path to after text file")
    record_parser.add_argument(
        "--ledger",
        default=str(resolve_state_root() / "humanize-ledger.jsonl"),
        help="Path to ledger file (default: $AESOP_STATE_ROOT/humanize-ledger.jsonl)",
    )
    record_parser.add_argument("--score-before", type=float, help="Humanness score before edit")
    record_parser.add_argument("--score-after", type=float, help="Humanness score after edit")
    record_parser.add_argument("--detector", default="manual", help="Detector name (default: manual)")
    record_parser.add_argument("--note", default="", help="Optional note about the edit")
    record_parser.add_argument("--reference", help="Reference text file for comparison")

    # summarize subcommand
    summarize_parser = subparsers.add_parser("summarize", help="Summarize ledger entries")
    summarize_parser.add_argument(
        "--ledger",
        default=str(resolve_state_root() / "humanize-ledger.jsonl"),
        help="Path to ledger file (default: $AESOP_STATE_ROOT/humanize-ledger.jsonl)",
    )
    summarize_parser.add_argument("--json", action="store_true", help="Output as JSON")
    summarize_parser.add_argument("--markdown", action="store_true", help="Output as Markdown")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 2

    if args.command == "record":
        return record_edits(
            before_file=args.before,
            after_file=args.after,
            ledger_path=args.ledger,
            score_before=args.score_before,
            score_after=args.score_after,
            detector=args.detector,
            note=args.note,
            reference_file=args.reference,
        )
    elif args.command == "summarize":
        return summarize_ledger(
            ledger_path=args.ledger,
            json_output=args.json,
            markdown_output=args.markdown,
        )

    return 2


if __name__ == "__main__":
    sys.exit(main())
