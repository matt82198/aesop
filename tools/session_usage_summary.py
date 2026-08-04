#!/usr/bin/env python3
"""
Sum token usage across a session's main thread and subagent transcripts.
INDEX: Aggregate token usage across session transcripts

Usage:
  python sum_session_tokens.py <session_dir> [<main_transcript>]

Args:
  session_dir: Path to session directory (e.g., .../f1bfdcf9-2ece-42f3-8798-c9066146f6a1)
  main_transcript: Path to main-thread JSONL transcript (auto-detected if not provided)

Extracts per-agent totals of input_tokens, output_tokens, cache_read_input_tokens,
cache_creation_input_tokens from all .output files in <session_dir>/tasks/ plus
the main thread. Outputs a table with per-agent summaries and totals.
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def extract_usage(line_obj):
    """Extract usage fields from a message object."""
    if "message" not in line_obj or "usage" not in line_obj["message"]:
        return None
    usage = line_obj["message"]["usage"]
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
    }


def extract_model(line_obj):
    """Extract model name from a message object."""
    if "message" not in line_obj:
        return None
    msg = line_obj["message"]
    if "model" in msg:
        return msg["model"]
    # Fallback: check if it's in content metadata
    return None


def parse_transcript(filepath, agent_name=None):
    """
    Parse a JSONL transcript file and sum usage per message.
    Returns (agent_name, model, total_usage_dict).
    """
    total_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    model = None
    message_count = 0

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    usage = extract_usage(obj)
                    if usage:
                        for key in total_usage:
                            total_usage[key] += usage.get(key, 0)
                        message_count += 1
                        if model is None:
                            model = extract_model(obj)
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"Warning: Failed to parse {filepath}: {e}", file=sys.stderr)
        return None

    if message_count == 0:
        return None  # No usage data

    return (agent_name or filepath.stem, model, total_usage)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(f"Usage: {sys.argv[0]} <session_dir> [<main_transcript>]")
        sys.exit(0)

    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <session_dir> [<main_transcript>]", file=sys.stderr)
        sys.exit(1)

    session_dir = Path(sys.argv[1])
    if not session_dir.exists():
        print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
        sys.exit(1)

    # Find main transcript
    if len(sys.argv) >= 3:
        main_transcript = Path(sys.argv[2])
    else:
        # Auto-detect: look for largest .jsonl matching session ID in parent or memory dirs
        session_id = session_dir.name
        proj_dirs = [
            session_dir.parent,  # If session_dir is already in projects/
            Path.home() / ".claude" / "projects" / session_dir.parent.name,
        ]
        main_transcript = None
        for proj_dir in proj_dirs:
            if proj_dir.exists():
                candidates = list(proj_dir.glob(f"{session_id}*.jsonl"))
                if candidates:
                    main_transcript = max(candidates, key=lambda p: p.stat().st_size)
                    break

    # Collect usage data
    results = {}

    # Parse main thread
    if main_transcript and main_transcript.exists():
        result = parse_transcript(main_transcript, "orchestrator (main thread)")
        if result:
            name, model, usage = result
            results[name] = (model or "Fable/Opus (inferred)", usage)

    # Parse subagent tasks
    tasks_dir = session_dir / "tasks"
    if tasks_dir.exists():
        for output_file in sorted(tasks_dir.glob("*.output")):
            result = parse_transcript(output_file)
            if result:
                name, model, usage = result
                results[name] = (model or "Unknown", usage)

    if not results:
        print("No usage data found in transcripts.", file=sys.stderr)
        sys.exit(1)

    # Print results
    print("\n" + "=" * 100)
    print("SESSION TOKEN USAGE SUMMARY")
    print("=" * 100)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()

    print(f"{'Agent':<35} {'Model':<15} {'Input':>12} {'Output':>12} {'CacheRead':>12} {'CacheCreate':>12} {'Total':>12}")
    print("-" * 112)

    grand_total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }

    for agent_name in sorted(results.keys()):
        model, usage = results[agent_name]
        inp = usage["input_tokens"]
        out = usage["output_tokens"]
        cache_read = usage["cache_read_input_tokens"]
        cache_create = usage["cache_creation_input_tokens"]
        total = inp + out + cache_read + cache_create

        print(
            f"{agent_name:<35} {model:<15} {inp:>12,} {out:>12,} {cache_read:>12,} {cache_create:>12,} {total:>12,}"
        )

        for key in grand_total:
            grand_total[key] += usage[key]

    print("-" * 112)
    grand_inp = grand_total["input_tokens"]
    grand_out = grand_total["output_tokens"]
    grand_cache_read = grand_total["cache_read_input_tokens"]
    grand_cache_create = grand_total["cache_creation_input_tokens"]
    grand_sum = grand_inp + grand_out + grand_cache_read + grand_cache_create

    print(
        f"{'TOTAL':<35} {'':<15} {grand_inp:>12,} {grand_out:>12,} {grand_cache_read:>12,} {grand_cache_create:>12,} {grand_sum:>12,}"
    )
    print("=" * 100)
    print()

    # Summary by category
    print("SUMMARY BY CATEGORY:")
    print(f"  Input tokens (fresh):        {grand_inp:>12,}")
    print(f"  Output tokens:               {grand_out:>12,}")
    print(f"  Cache read tokens:           {grand_cache_read:>12,}")
    print(f"  Cache creation tokens:       {grand_cache_create:>12,}")
    print(f"  TOTAL:                       {grand_sum:>12,}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
