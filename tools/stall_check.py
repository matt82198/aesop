#!/usr/bin/env python3
"""
Automated silent-hang detection for agent transcripts.
INDEX: Automated agent transcript stall detector; optional --active-from flag refines STALLED verdict to require both stale mtime AND active task file; --emit-recovery emits JSON advisories; --recovery-dir writes recovery-<agent>.json files (idempotent)

Usage:
  stall_check.py [--transcripts-root DIR] [--threshold-seconds SEC] [--json] [--exit-nonzero-on-stall]
                 [--active-from DIR] [--emit-recovery] [--recovery-dir DIR]

Options:
  --transcripts-root DIR       Root directory to scan for agent-*.jsonl transcripts.
                               Defaults to AESOP_TRANSCRIPTS_ROOT env var, or ~/.claude/projects if unset.
  --threshold-seconds SEC      Max age in seconds for a "fresh" transcript (default: 600).
                               Transcripts older than this are flagged as stalled.
  --json                       Output as JSON list of {agent_id, age_seconds, stalled, last_mtime}.
  --exit-nonzero-on-stall      Exit 1 if any agent is detected as stalled; default exit 0 always.
  --active-from DIR            Optional. When set, an agent is ACTIVE only if a matching task/status
                               file exists in DIR (named <agent_id>.task or <agent_id>.status).
                               STALLED = stale mtime AND active. Default behavior (no flag):
                               STALLED = stale mtime only.
  --emit-recovery              When set, emit recovery advisory JSON for each STALLED agent.
                               Advisory includes: agent, verdict, age_s, suggested_action (list).
                               Output as JSON blocks (one per STALLED agent).
  --recovery-dir DIR           Optional; only used with --emit-recovery. When set, write one
                               recovery-<agent>.json file per STALLED agent to DIR (idempotent).
                               No files written when none stalled.

Behavior:
  - Walks transcripts-root for files matching agent-*.jsonl.
  - For each file, computes age = now - file mtime (seconds).
  - Without --active-from: Reports agents as stalled if age > threshold-seconds (legacy).
  - With --active-from: Reports agents as stalled if age > threshold-seconds AND active file exists.
  - With --emit-recovery: Emits JSON advisory for each STALLED agent (to stdout).
  - With --recovery-dir: Additionally writes recovery-<agent>.json files (idempotent, overwrite).
  - Default output: human-readable table; add --json for structured output.
  - Gracefully reports "no transcripts found" if root is missing or empty.
  - Exit code: 0 always (unless --exit-nonzero-on-stall specified and stalls detected).
"""

import sys
import os
import time
import json
import argparse
import re
from pathlib import Path


def sanitize_agent_id(agent_id, filename):
    """Validate and sanitize agent ID to prevent path traversal (CWE-22).

    Args:
        agent_id: Extracted agent ID from filename
        filename: Original filename for warning message

    Returns:
        Sanitized agent_id if valid, None if invalid (caller should skip entry)
    """
    # Allowlist: only alphanumeric, underscore, dash
    if not re.match(r"^[A-Za-z0-9_-]+$", agent_id):
        # Log warning with filename only, never echo raw agent_id into a path
        sys.stderr.write(f"Warning: Skipping transcript with invalid agent ID in file: {filename}\n")
        return None
    return agent_id


def verify_path_containment(resolved_path, base_dir):
    """Verify that a path is contained within a base directory (defense-in-depth).

    Args:
        resolved_path: Resolved absolute path to verify
        base_dir: Base directory that path must be contained in

    Returns:
        True if path is safely contained, False if escape attempted
    """
    # Resolve BOTH sides: an unresolved 8.3 short-form candidate compared
    # against a resolved long-form base raises ValueError and reads as an
    # escape (windows-runner regression: stale+active reported ok).
    resolved_base = Path(base_dir).resolve()
    try:
        Path(resolved_path).resolve().relative_to(resolved_base)
        return True
    except ValueError:
        # Path is outside base_dir
        return False


def get_transcripts_root():
    """Resolve transcripts root from env var or default to ~/.claude/projects."""
    if os.environ.get("AESOP_TRANSCRIPTS_ROOT"):
        return Path(os.environ["AESOP_TRANSCRIPTS_ROOT"])
    # Default to ~/.claude/projects
    return Path.home() / ".claude" / "projects"


def is_agent_active(agent_id, active_from_dir):
    """Check if an agent is active by looking for task/status files.

    Args:
        agent_id: Agent identifier (e.g., 'abc123') - must be pre-sanitized
        active_from_dir: Directory to scan for <agent_id>.task or <agent_id>.status files

    Returns:
        True if any matching task/status file exists, False otherwise.
    """
    if not active_from_dir:
        return None  # Flag not provided

    active_from_path = Path(active_from_dir)
    if not active_from_path.exists():
        return False

    # Check for <agent_id>.task or <agent_id>.status files with containment verification
    for pattern in [f"{agent_id}.task", f"{agent_id}.status"]:
        candidate_path = (active_from_path / pattern)
        # Defense-in-depth: verify path is contained in active_from_path
        if verify_path_containment(candidate_path, active_from_path):
            if candidate_path.exists():
                return True

    return False


def scan_transcripts(transcripts_root, threshold_seconds, active_from_dir=None):
    """Scan transcripts root for agent-*.jsonl files and compute staleness.

    Args:
        transcripts_root: Root directory to scan
        threshold_seconds: Staleness threshold in seconds
        active_from_dir: Optional. When set, agent is ACTIVE only if task/status file exists.
                        STALLED = stale mtime AND active. Default (None): STALLED = stale mtime only.

    Returns: list of dicts {agent, transcript, mtime_age_s, verdict, suggested_action, last_mtime (ISO), active}
    """
    transcripts_root = Path(transcripts_root)

    if not transcripts_root.exists():
        return None  # Signal: root missing

    now = time.time()
    results = []

    # Thresholds for verdict classification
    STALE_THRESHOLD = threshold_seconds
    DEAD_THRESHOLD = threshold_seconds * 2  # Dead if 2x threshold

    # Walk all subdirectories for agent-*.jsonl files
    for jsonl_file in transcripts_root.rglob("agent-*.jsonl"):
        if not jsonl_file.is_file():
            continue

        mtime = jsonl_file.stat().st_mtime
        age_seconds = int(now - mtime)

        # Extract agent_id from filename (e.g., agent-abc123.jsonl -> abc123)
        agent_id = jsonl_file.stem.replace("agent-", "")

        # Sanitize agent_id to prevent path traversal (CWE-22)
        agent_id = sanitize_agent_id(agent_id, str(jsonl_file))
        if agent_id is None:
            # Invalid agent_id, skip this entry
            continue

        # Check active status if flag is provided
        active = is_agent_active(agent_id, active_from_dir)

        # Determine verdict
        if age_seconds <= STALE_THRESHOLD:
            verdict = "ok"
            suggested_action = None
        elif age_seconds <= DEAD_THRESHOLD:
            # If active_from_dir is set, only stale if also active
            if active_from_dir is not None and not active:
                verdict = "ok"
                suggested_action = None
            else:
                verdict = "stale"
                suggested_action = "monitor for progress or investigate why transcript is stalled"
        else:
            # If active_from_dir is set, only dead if also active
            if active_from_dir is not None and not active:
                verdict = "ok"
                suggested_action = None
            else:
                verdict = "dead"
                suggested_action = "investigate immediately; agent may be hung or crashed"

        results.append({
            "agent": agent_id,
            "transcript": str(jsonl_file),
            "mtime_age_s": age_seconds,
            "verdict": verdict,
            "suggested_action": suggested_action,
            "last_mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime)),
            "active": active if active_from_dir is not None else None,
        })

    return results


def emit_recovery_advisories(results):
    """Emit recovery advisory JSON blocks for stalled agents.

    Yields JSON strings (one per stalled agent) with advisory structure:
    {
        "agent": <agent_id>,
        "verdict": <verdict>,
        "age_s": <seconds>,
        "suggested_action": [<ordered list of actions>]
    }
    """
    if not results:
        return

    stalled_entries = [r for r in results if r["verdict"] in ("stale", "dead")]

    for entry in stalled_entries:
        # Build ordered list of suggested actions
        actions = []

        if entry["verdict"] == "stale":
            actions.append("SendMessage resume with scope recap")
            actions.append("TaskStop + relaunch from journal")
            actions.append("inspect transcript tail")
        elif entry["verdict"] == "dead":
            actions.append("TaskStop immediately")
            actions.append("inspect transcript for crash/error")
            actions.append("relaunch from last known-good checkpoint")

        advisory = {
            "agent": entry["agent"],
            "verdict": entry["verdict"],
            "age_s": entry["mtime_age_s"],
            "suggested_action": actions,
        }

        yield json.dumps(advisory)


def write_recovery_files(results, recovery_dir):
    """Write recovery-<agent>.json files for each stalled agent (idempotent).

    Args:
        results: List of scan results
        recovery_dir: Directory to write recovery files to

    Returns:
        Count of files written
    """
    if not results or not recovery_dir:
        return 0

    recovery_path = Path(recovery_dir)
    recovery_path.mkdir(parents=True, exist_ok=True)

    files_written = 0
    stalled_entries = [r for r in results if r["verdict"] in ("stale", "dead")]

    for entry in stalled_entries:
        # Build ordered list of suggested actions
        actions = []

        if entry["verdict"] == "stale":
            actions.append("SendMessage resume with scope recap")
            actions.append("TaskStop + relaunch from journal")
            actions.append("inspect transcript tail")
        elif entry["verdict"] == "dead":
            actions.append("TaskStop immediately")
            actions.append("inspect transcript for crash/error")
            actions.append("relaunch from last known-good checkpoint")

        advisory = {
            "agent": entry["agent"],
            "verdict": entry["verdict"],
            "age_s": entry["mtime_age_s"],
            "suggested_action": actions,
        }

        recovery_file = recovery_path / f"recovery-{entry['agent']}.json"

        # Defense-in-depth: verify recovery_file is contained in recovery_path
        if not verify_path_containment(recovery_file, recovery_path):
            sys.stderr.write(f"Warning: Recovery file path escape detected, skipping: {entry['agent']}\n")
            continue

        try:
            recovery_file.write_text(json.dumps(advisory, indent=2), encoding='utf-8')
            files_written += 1
        except Exception as e:
            # Fail-open: log error but continue
            sys.stderr.write(f"Warning: Failed to write {recovery_file}: {e}\n")

    return files_written


def print_human_table(results):
    """Print results as a human-readable table."""
    if not results:
        print("no transcripts found")
        return

    # Sort by mtime_age_s (oldest first)
    sorted_results = sorted(results, key=lambda r: r["mtime_age_s"], reverse=True)

    # Header
    print(f"{'AGENT':<30} {'AGE (s)':<10} {'VERDICT':<10} {'ACTION':<40}")
    print("-" * 90)

    for entry in sorted_results:
        action = entry["suggested_action"] or "—"
        # Truncate action for display
        action = action[:38] if len(action) > 38 else action
        print(f"{entry['agent']:<30} {entry['mtime_age_s']:<10} {entry['verdict']:<10} {action:<40}")


def print_json_output(results):
    """Print results as JSON."""
    if results is None:
        print(json.dumps([]))
    else:
        print(json.dumps(results, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--transcripts-root",
        default=None,
        help="Root directory to scan for agent-*.jsonl files (default: AESOP_TRANSCRIPTS_ROOT or ~/.claude/projects)",
    )
    parser.add_argument(
        "--threshold-seconds",
        type=int,
        default=600,
        help="Max age in seconds for a 'fresh' transcript (default: 600)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of human-readable table",
    )
    parser.add_argument(
        "--exit-nonzero-on-stall",
        action="store_true",
        help="Exit 1 if any agent is stalled (default: always exit 0)",
    )
    parser.add_argument(
        "--active-from",
        default=None,
        help="Optional. Directory to scan for task/status files. Agent is ACTIVE only if file exists.",
    )
    parser.add_argument(
        "--emit-recovery",
        action="store_true",
        help="Emit recovery advisory JSON blocks to stdout for each STALLED agent",
    )
    parser.add_argument(
        "--recovery-dir",
        default=None,
        help="Optional; only used with --emit-recovery. Write recovery-<agent>.json files to this directory",
    )

    args = parser.parse_args()

    # Resolve transcripts root
    transcripts_root = args.transcripts_root if args.transcripts_root else get_transcripts_root()

    # Scan transcripts
    results = scan_transcripts(transcripts_root, args.threshold_seconds, args.active_from)

    # Emit recovery advisories if requested
    if args.emit_recovery and results:
        for advisory_json in emit_recovery_advisories(results):
            print(advisory_json)

    # Write recovery files if requested
    if args.emit_recovery and args.recovery_dir and results:
        write_recovery_files(results, args.recovery_dir)

    # Output (human-readable or JSON)
    # Note: if --emit-recovery was used, recovery advisories already printed above
    if not args.emit_recovery:
        # Only print human-readable/JSON if we're not emitting recovery advisories
        if args.json:
            print_json_output(results)
        else:
            if results is None:
                print("no transcripts found")
            else:
                print_human_table(results)

    # Determine exit code
    exit_code = 0
    if args.exit_nonzero_on_stall and results:
        has_stalled = any(r["verdict"] in ("stale", "dead") for r in results)
        if has_stalled:
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
