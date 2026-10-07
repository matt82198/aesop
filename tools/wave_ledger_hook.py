#!/usr/bin/env python3
r"""
Wave Ledger Hook — orchestrator wrapper to append per-wave telemetry to OUTCOMES-LEDGER.md.
INDEX: Orchestrator-tail CLI wrapper to append per-wave telemetry to OUTCOMES-LEDGER.md (idempotent phase appends; validates timestamp for markdown table safety)

Orchestrator-tail command to write build/verify/repair phase outcomes from a workflow report
into the aesop ledger, closing the 'ledger empty, all telemetry reconstructed from transcripts'
gap. Calls fleet_ledger.append_wave for each phase to ensure idempotency and consistency.

Usage:
  python tools/wave_ledger_hook.py --report-file <path> --wave <id> --timestamp <iso>

Arguments:
  --report-file   Path to workflow report JSON (required)
                  Shape: {tokens:{buildOut,verifyOut,repairOut,totalOut},
                          integration:{green,passed,failed}, repairsUsed,
                          build:[{slug,...}], adversarialReviewMode?}
  --wave          Wave ID (required, integer)
  --timestamp     ISO 8601 timestamp (required, never invented; string without pipes/newlines)

Behavior:
  - Validates timestamp: rejects if contains pipe (|) or newline characters
  - Calls fleet_ledger.append_wave for each phase present in report
  - Appends rows to OUTCOMES-LEDGER.md in state/ledger/ directory
  - Idempotent: safe to call multiple times with same parameters (existing rows skipped)
  - Returns exit 0 on success, 1 on validation/parse errors

Exit codes:
  0 = All phases successfully appended (or already exist)
  1 = Validation error, missing argument, parse error, or append failure

Environment:
  AESOP_STATE_ROOT: Path to state directory (default: ./state relative to cwd)
"""

import sys
import json
import argparse
from pathlib import Path

try:
    from fleet_ledger import append_wave
except ImportError:
    from tools.fleet_ledger import append_wave


def validate_timestamp(timestamp_str):
    """Validate timestamp string for safety.

    Args:
        timestamp_str: ISO 8601 timestamp string to validate

    Returns:
        (is_valid, error_message) tuple
        - is_valid: True if timestamp is safe, False if contains pipes/newlines
        - error_message: Human-readable error if invalid, empty string if valid
    """
    if not timestamp_str:
        return False, "Timestamp cannot be empty"

    # Reject pipe (markdown table injection)
    if '|' in timestamp_str:
        return False, "Timestamp cannot contain pipe character (|)"

    # Reject newline and carriage return (table formatting break)
    if '\n' in timestamp_str or '\r' in timestamp_str:
        return False, "Timestamp cannot contain newline characters"

    return True, ""


def main():
    parser = argparse.ArgumentParser(
        description='Append per-wave telemetry to OUTCOMES-LEDGER.md'
    )
    parser.add_argument(
        '--report-file',
        required=True,
        help='Path to workflow report JSON'
    )
    parser.add_argument(
        '--wave',
        required=True,
        help='Wave ID (integer)'
    )
    parser.add_argument(
        '--timestamp',
        required=True,
        help='ISO 8601 timestamp (required, never invented)'
    )

    args = parser.parse_args()

    # Validate timestamp
    is_valid, error_msg = validate_timestamp(args.timestamp)
    if not is_valid:
        print(f"ERROR: Invalid timestamp: {error_msg}")
        return 1

    # Read and parse report
    try:
        report_path = Path(args.report_file)
        if not report_path.exists():
            print(f"ERROR: Report file not found: {args.report_file}")
            return 1

        report_text = report_path.read_text(encoding='utf-8')
        report = json.loads(report_text)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse report JSON: {e}")
        return 1
    except (IOError, OSError) as e:
        print(f"ERROR: Failed to read report file: {e}")
        return 1

    # Determine which phases to append based on report contents
    phases_to_append = []

    # Always append build phase if we have token data
    tokens_section = report.get('tokens', {})
    if 'buildOut' in tokens_section or 'build' in report:
        phases_to_append.append('build')

    # Append verify phase if we have token data
    if 'verifyOut' in tokens_section:
        phases_to_append.append('verify')

    # Append repair phase only if repairsUsed > 0
    repairs_used = report.get('repairsUsed', 0)
    if repairs_used > 0 and 'repairOut' in tokens_section:
        phases_to_append.append('repair')

    # If no phases identified, default to all three if any token data exists
    if not phases_to_append and tokens_section:
        phases_to_append = ['build', 'verify', 'repair']

    # Append each phase
    all_succeeded = True
    for phase in phases_to_append:
        success, message = append_wave(args.report_file, args.wave, phase, args.timestamp)
        if not success:
            print(f"ERROR: Failed to append {phase} phase: {message}")
            all_succeeded = False
        else:
            print(f"OK: {message}")

    return 0 if all_succeeded else 1


if __name__ == '__main__':
    sys.exit(main())
