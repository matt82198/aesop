#!/usr/bin/env python3
"""Quality scorecard collector — per-agent-specialty success rates and retry frequencies.

This module provides get_quality_scorecard() which parses the outcomes ledger
and returns per-agent-specialty quality metrics: success rate (green/total)
and retry/repair frequency, derived from ledger agent_type + verdict columns.

Ledger format (same as cost.py):
  | ISO timestamp | agent_type | model | duration | tokens_in | tokens_out | verdict |
  | 2026-07-11T22:08:17 | haiku | claude-haiku-4-5-20251001 | 0 | 8 | 186 | OK |

QualityScorecard JSON shape (returned by get_quality_scorecard()):
  {
    "specialties": {
      "agent-type": {
        "total_runs": int,
        "success_count": int,
        "failed_count": int,
        "empty_count": int,
        "hung_count": int,
        "success_rate": float (0.0-1.0),
        "repair_count": int (consecutive failures followed by success),
        "retry_frequency": float (repairs / total_runs)
      },
      ...
    },
    "top_by_success": [
      {"agent_type": str, "success_rate": float, "total_runs": int},
      ...
    ],
    "top_by_retry": [
      {"agent_type": str, "retry_frequency": float, "total_runs": int},
      ...
    ],
    "skipped_lines": int
  }

Key behavior:
  - Missing ledger file: returns empty summary with documented shape.
  - Malformed lines: skipped and counted in skipped_lines field.
  - Config read at CALL time (not import time) for test isolation.
  - UTF-8 explicit encoding for all file operations.
  - No external dependencies (pure stdlib).
  - Retry frequency calculated by detecting "failure -> success" transitions (repair cycles).
  - Rankings sorted by metric descending (highest success rate / highest retry frequency first).
"""

import config


def get_quality_scorecard():
    """Parse the outcomes ledger and return per-agent-specialty quality metrics.

    Reads from the ledger path exposed by config (config.STATE_DIR/ledger/OUTCOMES-LEDGER.md).
    Returns an empty summary with documented shape if ledger is missing or empty.
    Malformed lines are skipped and counted in skipped_lines.

    Extracts agent_type from the ledger (haiku, sonnet, opus, orchestrator, etc.) and
    aggregates success rates and repair (retry) frequencies.

    Repair cycles detected by counting consecutive failures followed by a success
    in the same agent_type's history.

    Returns:
        dict: QualityScorecard with specialties, top_by_success, top_by_retry,
              and skipped_lines (or error field if invalid).
    """

    # Read ledger path at call time
    ledger_file = config.STATE_DIR / "ledger" / "OUTCOMES-LEDGER.md"

    # Initialize result structure
    result = {
        "specialties": {},
        "top_by_success": [],
        "top_by_retry": [],
        "skipped_lines": 0,
    }

    # If ledger file doesn't exist, return empty summary
    if not ledger_file.exists():
        return result

    # Read and parse ledger with explicit UTF-8 encoding
    try:
        content = ledger_file.read_text(encoding='utf-8')
    except Exception:
        # Graceful: if read fails, return empty summary
        return result

    lines = content.strip().split('\n')

    # Parse each line and collect per-agent-type history
    agent_verdicts = {}  # agent_type -> [verdict1, verdict2, ...]

    for line in lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Skip header separator lines (all dashes, pipes, and spaces)
        if all(c in '|- ' for c in line):
            continue

        # Parse pipe-delimited row
        if not line.startswith('|') or not line.endswith('|'):
            result["skipped_lines"] += 1
            continue

        # Split by pipe and strip whitespace
        parts = [p.strip() for p in line.split('|')]

        if len(parts) < 9:  # Need at least 9 parts (empty + 7 columns + empty)
            result["skipped_lines"] += 1
            continue

        # Extract columns (skip leading/trailing empty)
        try:
            timestamp = parts[1]  # ISO timestamp
            agent_type = parts[2]  # "haiku", "sonnet", "orchestrator", etc.
            parts[3]
            parts[4]
            tokens_in_str = parts[5]
            tokens_out_str = parts[6]
            verdict = parts[7]
        except IndexError:
            result["skipped_lines"] += 1
            continue

        # Skip header line
        if 'timestamp' in timestamp.lower() or 'iso' in timestamp.lower():
            continue

        # Validate verdict before processing
        if verdict not in ("OK", "FAILED", "EMPTY", "HUNG"):
            result["skipped_lines"] += 1
            continue

        # Parse and validate numeric fields
        try:
            int(tokens_in_str)
            int(tokens_out_str)
        except ValueError:
            result["skipped_lines"] += 1
            continue

        # Initialize agent_type entry if not seen
        if agent_type not in result["specialties"]:
            result["specialties"][agent_type] = {
                "total_runs": 0,
                "success_count": 0,
                "failed_count": 0,
                "empty_count": 0,
                "hung_count": 0,
                "success_rate": 0.0,
                "repair_count": 0,
                "retry_frequency": 0.0,
            }
            agent_verdicts[agent_type] = []

        # Increment verdict counters
        result["specialties"][agent_type]["total_runs"] += 1
        if verdict == "OK":
            result["specialties"][agent_type]["success_count"] += 1
        elif verdict == "FAILED":
            result["specialties"][agent_type]["failed_count"] += 1
        elif verdict == "EMPTY":
            result["specialties"][agent_type]["empty_count"] += 1
        elif verdict == "HUNG":
            result["specialties"][agent_type]["hung_count"] += 1

        # Collect verdict history for repair detection
        agent_verdicts[agent_type].append(verdict)

    # Calculate success rates and detect repair cycles
    for agent_type, stats in result["specialties"].items():
        total = stats["total_runs"]
        if total > 0:
            stats["success_rate"] = stats["success_count"] / total
            stats["retry_frequency"] = stats["repair_count"] / total

        # Detect repair cycles (consecutive failures followed by success)
        verdicts = agent_verdicts.get(agent_type, [])
        repair_count = 0
        in_failure_sequence = False

        for verdict in verdicts:
            if verdict in ("FAILED", "EMPTY", "HUNG"):
                in_failure_sequence = True
            elif verdict == "OK" and in_failure_sequence:
                # Success after failures = repair cycle
                repair_count += 1
                in_failure_sequence = False

        stats["repair_count"] = repair_count
        if total > 0:
            stats["retry_frequency"] = repair_count / total

    # Generate top-by-success ranking (highest success rate first)
    success_ranking = sorted(
        [
            {
                "agent_type": agent_type,
                "success_rate": stats["success_rate"],
                "total_runs": stats["total_runs"],
            }
            for agent_type, stats in result["specialties"].items()
        ],
        key=lambda x: (-x["success_rate"], -x["total_runs"]),  # Sort desc by success_rate, then runs
    )
    result["top_by_success"] = success_ranking

    # Generate top-by-retry ranking (highest retry/repair frequency first)
    retry_ranking = sorted(
        [
            {
                "agent_type": agent_type,
                "retry_frequency": stats["retry_frequency"],
                "total_runs": stats["total_runs"],
            }
            for agent_type, stats in result["specialties"].items()
        ],
        key=lambda x: (-x["retry_frequency"], -x["total_runs"]),  # Sort desc by retry_frequency, then runs
    )
    result["top_by_retry"] = retry_ranking

    return result
