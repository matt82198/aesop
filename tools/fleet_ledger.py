#!/usr/bin/env python3
r"""
Fleet Outcome Ledger — append-only audit trail for dispatched agents.

Subcommands:
  append <timestamp> <agent_type> <model> <duration_sec> <tokens_in> <tokens_out> [verdict] [phase] [wave]
    Manually append one ledger line (verdict = OK|FAILED|EMPTY|HUNG, default OK)
    phase = build|verify|repair|other (optional, default null)
    wave = wave number as integer (optional, default null)
  append-wave --report-file <path> --wave <id> --phase <phase> --timestamp <iso>
    Append one ledger row from a workflow report JSON for a specific phase.
    Report shape: {tokens:{buildOut,verifyOut,repairOut,...}, integration:{green:bool,...}, ...}
    Creates: model=haiku, verdict from integration.green, tokens_out from tokens.<phase>Out.
    Skips if identical wave+phase+timestamp row already exists (idempotent).
    Tolerates missing fields (defaults to 0).
  harvest
    Scan session tasks directories for agent outcomes and append missing entries.
    (Tracks state in ledger directory for resume capability)
  rotate
    Archive ledger lines exceeding ~200 lines to dated archive, keep recent tail in live ledger
  summary
    Print total cost/tokens grouped by wave number and phase; supports --json for machine reading

Ledger format (markdown table):
  | ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |

Environment:
  AESOP_STATE_ROOT: Path to state directory (default: ./state relative to cwd)
  AESOP_TEMP_ROOT: Path to temp directory for tasks scanning (optional)
"""

import sys
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import re
from collections import defaultdict

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


def get_ledger_paths():
    """Get ledger file and sidecar state file paths."""
    state_dir = get_state_dir()
    ledger_dir = state_dir / "ledger"
    ledger_file = ledger_dir / "OUTCOMES-LEDGER.md"
    harvest_state_file = ledger_dir / ".fleet-ledger-harvest.json"
    return ledger_file, harvest_state_file, ledger_dir


def ensure_ledger_header():
    """Ensure ledger file exists with markdown table header.

    WRITE PATH ONLY. Call this from append/harvest/rotate -- never from a reader.
    Readers (parse_ledger_rows, summary) stay side-effect free so that every
    downstream `--check` mode remains read-only on a fresh tree.
    """
    ledger_file, _, ledger_dir = get_ledger_paths()
    if not ledger_file.exists():
        ledger_dir.mkdir(parents=True, exist_ok=True)
        header = '| ISO ts | agent_type | model | duration_sec | tokens_in | tokens_out | verdict | phase | wave |\n'
        header += '|--------|------------|-------|--------------|-----------|------------|--------|-------|------|\n'
        ledger_file.write_text(header, encoding='utf-8')


def _validate_iso_timestamp(ts):
    """Validate and normalize ISO 8601 timestamp.

    Args:
        ts: Timestamp string to validate

    Returns:
        Validated timestamp string or None if invalid

    Raises:
        ValueError: If timestamp is invalid
    """
    import re
    if not ts:
        return None

    ts_str = str(ts).strip()

    # Reject if contains control characters, pipes, newlines, etc.
    if '\n' in ts_str or '\r' in ts_str or '|' in ts_str:
        raise ValueError(f"Timestamp contains forbidden characters: {repr(ts_str)}")

    # Validate ISO 8601 format: YYYY-MM-DDTHH:MM:SS[.fff][+HH:MM]
    # This is a strict pattern to prevent injection via malformed timestamps
    iso_pattern = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:[+-]\d{2}:\d{2}|Z)?$'
    if not re.match(iso_pattern, ts_str):
        raise ValueError(f"Timestamp does not match ISO 8601 format: {ts_str}")

    return ts_str


def _validate_phase(phase):
    """Validate and normalize phase name.

    Args:
        phase: Phase name to validate

    Returns:
        Validated phase string (max 15 chars) or empty string if None

    Raises:
        ValueError: If phase contains forbidden characters
    """
    if phase is None:
        return ''

    phase_str = str(phase).strip()

    # Reject if contains forbidden characters (pipes, newlines, etc.)
    if '|' in phase_str or '\n' in phase_str or '\r' in phase_str:
        raise ValueError(f"Phase contains forbidden characters: {repr(phase_str)}")

    # Truncate to 15 chars for idempotency consistency
    return phase_str[:15]


def append_ledger_line(iso_ts, agent_type, model, duration_sec, tokens_in, tokens_out, verdict='OK', phase=None, wave=None):
    """Append one line to the ledger.

    Args:
        iso_ts: ISO 8601 timestamp
        agent_type: agent type string
        model: model identifier
        duration_sec: duration in seconds
        tokens_in: input tokens
        tokens_out: output tokens
        verdict: OK|FAILED|EMPTY|HUNG (default OK)
        phase: build|verify|repair|other (optional, default None)
        wave: wave number as int or None (optional, default None)

    Raises:
        ValueError: If timestamp or phase validation fails
    """
    ensure_ledger_header()
    ledger_file, _, _ = get_ledger_paths()

    # Validate and sanitize iso_ts: must be valid ISO 8601 format
    try:
        iso_ts = _validate_iso_timestamp(iso_ts)
        if iso_ts is None:
            iso_ts = '-'
    except ValueError as e:
        # Reject invalid timestamps to prevent injection
        raise ValueError(f"Invalid timestamp: {e}") from e

    # Sanitize fields: no pipes, truncate to reasonable length
    agent_type = str(agent_type or '-').replace('|', '').strip()[:30]
    model = str(model or '-').replace('|', '').strip()[:30]
    verdict = str(verdict or 'OK').replace('|', '').strip()[:10]

    # Validate and sanitize optional fields
    try:
        phase = _validate_phase(phase)
    except ValueError as e:
        raise ValueError(f"Invalid phase: {e}") from e

    if wave is not None:
        try:
            wave = str(int(wave))
        except (ValueError, TypeError):
            wave = ''
    else:
        wave = ''

    try:
        dur = int(duration_sec) if duration_sec else 0
    except (ValueError, TypeError):
        dur = 0

    try:
        ti = int(tokens_in) if tokens_in else 0
    except (ValueError, TypeError):
        ti = 0

    try:
        to = int(tokens_out) if tokens_out else 0
    except (ValueError, TypeError):
        to = 0

    line = f'| {iso_ts} | {agent_type} | {model} | {dur} | {ti} | {to} | {verdict} | {phase} | {wave} |\n'
    with open(ledger_file, 'a', encoding='utf-8') as f:
        f.write(line)


def load_harvest_state():
    """Load last-harvest state (set of already-seen agent IDs)."""
    _, harvest_state_file, _ = get_ledger_paths()
    if harvest_state_file.exists():
        try:
            data = json.loads(harvest_state_file.read_text(encoding='utf-8'))
            return set(data.get('seen_agents', [])), data.get('last_harvest_ts', None)
        except (json.JSONDecodeError, IOError):
            pass
    return set(), None


def save_harvest_state(seen_agents, last_harvest_ts=None):
    """Save last-harvest state."""
    _, harvest_state_file, ledger_dir = get_ledger_paths()
    ledger_dir.mkdir(parents=True, exist_ok=True)
    state = {
        'seen_agents': sorted(list(seen_agents)),
        'last_harvest_ts': last_harvest_ts or datetime.now(timezone.utc).isoformat()
    }
    harvest_state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')


def harvest():
    """Scan task output files and append missing agent outcomes."""
    ensure_ledger_header()
    seen_agents, _ = load_harvest_state()
    new_seen = set(seen_agents)
    harvested_count = 0
    skipped_count = 0

    # Determine temp root for tasks scanning
    if os.environ.get("AESOP_TEMP_ROOT"):
        temp_root = Path(os.environ["AESOP_TEMP_ROOT"])
    else:
        # Default to system temp + /claude
        import tempfile
        temp_root = Path(tempfile.gettempdir()) / "claude"

    # Find all .output files in tasks directories
    for output_file in temp_root.rglob('tasks/*.output'):
        if not output_file.is_file():
            continue

        try:
            content = output_file.read_text(encoding='utf-8', errors='ignore')
        except (IOError, OSError):
            continue

        # Parse JSONL: each line is a JSON object
        for line_idx, line in enumerate(content.split('\n')):
            if not line.strip():
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Type guard: skip non-dict scalars (int, string, etc.)
            if not isinstance(obj, dict):
                skipped_count += 1
                continue

            # Look for agent spawn events (type='assistant' with Agent/Task tool_use)
            # or completion events (type='assistant' with usage data)
            agent_id = obj.get('agentId') or obj.get('uuid')
            if not agent_id:
                continue

            # Skip if already seen
            if agent_id in seen_agents:
                continue

            # Try to extract completion info
            msg = obj.get('message', {})
            ts = obj.get('timestamp', '')
            msg_type = msg.get('type', '')

            # Look for assistant message with usage (indicates completion)
            if msg_type == 'message' and msg.get('role') == 'assistant':
                usage = msg.get('usage', {})
                if usage:
                    # Found a completion event
                    agent_type = 'Agent'  # placeholder; ideally track subagent_type from spawn
                    model = msg.get('model', '-')

                    # Try to extract duration from timestamps or usage
                    input_tokens = usage.get('input_tokens', 0)
                    output_tokens = usage.get('output_tokens', 0)

                    # Verdict: assume OK if no stop_reason error
                    stop_reason = msg.get('stop_reason')
                    verdict = 'OK'
                    if stop_reason in ('error', 'end_turn'):
                        verdict = 'OK'  # normal completion
                    elif not stop_reason:
                        verdict = 'OK'

                    duration_sec = 0  # TODO: parse from timestamps if available

                    # Append to ledger
                    if ts:
                        append_ledger_line(ts[:19], agent_type, model, duration_sec,
                                         input_tokens, output_tokens, verdict)
                        new_seen.add(agent_id)
                        harvested_count += 1

        # Save state after processing each file to avoid re-processing
        save_harvest_state(new_seen)

    ledger_file, _, _ = get_ledger_paths()
    print(f'Harvested {harvested_count} new agent outcomes to {ledger_file}')
    if skipped_count > 0:
        print(f'Skipped {skipped_count} malformed JSONL lines (non-dict scalars)')
    return harvested_count


def rotate():
    """Archive old ledger lines if ledger exceeds ~200 lines."""
    ensure_ledger_header()
    ledger_file, _, ledger_dir = get_ledger_paths()

    try:
        lines = ledger_file.read_text(encoding='utf-8').split('\n')
    except (IOError, OSError):
        print('Error reading ledger')
        return

    # Count non-header lines
    data_lines = [l for l in lines if l.strip() and not l.startswith('|---|')]
    header_lines = [l for l in lines if l.startswith('|') and '---|' in l]

    if len(data_lines) <= 202:  # Leave some headroom
        print(f'Ledger has {len(data_lines)} lines; no rotation needed (threshold: 200)')
        return

    # Create archive with date stamp
    archive_dir = ledger_dir / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_name = datetime.now(timezone.utc).strftime('FLEET-LEDGER-%Y%m%d-%H%M%S.md')
    archive_file = archive_dir / archive_name

    # Write header + oldest 50% of data lines to archive
    archive_count = len(data_lines) // 2
    archive_lines = header_lines + data_lines[:archive_count]
    archive_file.write_text('\n'.join(archive_lines) + '\n', encoding='utf-8')

    # Keep header + newest lines in live ledger
    new_ledger = header_lines + data_lines[archive_count:]
    ledger_file.write_text('\n'.join(new_ledger) + '\n', encoding='utf-8')

    print(f'Rotated {archive_count} lines to {archive_file}')
    print(f'Live ledger now has {len(new_ledger) - len(header_lines)} data lines')


def append_wave(report_file, wave, phase, timestamp):
    """Append one row to the ledger from a workflow report JSON.

    Args:
        report_file: Path to JSON report file
        wave: Wave number (string or int)
        phase: Phase name (build|verify|repair)
        timestamp: ISO 8601 timestamp (must be provided by caller)

    Workflow report shape:
        {
            "tokens": {
                "buildOut": int,
                "verifyOut": int,
                "repairOut": int,
                "totalOut": int,
                ...
            },
            "integration": {
                "green": bool,
                ...
            },
            ...
        }

    Returns:
        Tuple: (success, message)
    """
    try:
        report_path = Path(report_file)
        if not report_path.exists():
            return False, f"Report file not found: {report_file}"

        report_text = report_path.read_text(encoding='utf-8')
        report = json.loads(report_text)
    except (json.JSONDecodeError, IOError) as e:
        return False, f"Failed to read/parse report: {e}"

    # Extract data from report with graceful defaults
    tokens_section = report.get('tokens', {})
    integration_section = report.get('integration', {})

    # Determine verdict from integration.green
    is_green = integration_section.get('green', False)
    verdict = 'OK' if is_green else 'FAILED'

    # Extract tokens_out based on phase
    phase_key = f'{phase}Out'
    tokens_out = tokens_section.get(phase_key, 0)
    try:
        tokens_out = int(tokens_out) if tokens_out else 0
    except (ValueError, TypeError):
        tokens_out = 0

    # Model is always haiku for append-wave
    model = 'haiku'
    agent_type = 'waverun'

    # Validate inputs
    try:
        wave_num = int(wave) if wave else None
    except (ValueError, TypeError):
        return False, f"Invalid wave number: {wave}"

    # Validate and sanitize inputs consistently with append_ledger_line
    # This ensures idempotency check uses the same values as the actual write
    try:
        iso_ts_sanitized = _validate_iso_timestamp(timestamp)
        if iso_ts_sanitized is None:
            iso_ts_sanitized = '-'
    except ValueError as e:
        return False, f"Invalid timestamp: {e}"

    try:
        phase_sanitized = _validate_phase(phase)
    except ValueError as e:
        return False, f"Invalid phase: {e}"

    # Idempotency check: see if this exact row already exists
    existing_rows = parse_ledger_rows()
    for row in existing_rows:
        if (row['iso_ts'] == iso_ts_sanitized and
            row['phase'] == phase_sanitized and
            row['wave'] == wave_num and
            row['model'] == model):
            return True, f"Row already exists (wave={wave_num}, phase={phase_sanitized}, ts={iso_ts_sanitized}); skipping"

    # Append the row with validated inputs
    try:
        append_ledger_line(timestamp, agent_type, model, 0, 0, tokens_out, verdict, phase, wave_num)
        return True, f"Appended: wave={wave_num} phase={phase_sanitized} tokens_out={tokens_out} verdict={verdict}"
    except ValueError as e:
        return False, f"Failed to append row: {e}"


def parse_ledger_rows():
    """Parse and return all ledger rows as structured data.

    Returns:
        list of dicts, each with keys: iso_ts, agent_type, model, duration_sec,
        tokens_in, tokens_out, verdict, phase, wave (wave as int or None)

    Returns empty list if ledger doesn't exist or is unreadable.

    READ-ONLY: this function must never create the ledger file or its directory.
    Header creation lives on the APPEND path (ensure_ledger_header, called from
    append_ledger_line/harvest/rotate). A reader that materialized the ledger
    turned every downstream `--check` mode (cost_ceiling, cost_projection, the
    state_store read facade) into a writer on a fresh tree.
    """
    ledger_file, _, _ = get_ledger_paths()

    if not ledger_file.exists():
        return []

    try:
        lines = ledger_file.read_text(encoding='utf-8').split('\n')
    except (IOError, OSError):
        return []

    rows = []
    for line in lines:
        # Skip empty, header, separator lines
        if not line.strip() or '---|' in line or not line.startswith('|'):
            continue

        # Parse markdown table row
        cells = [c.strip() for c in line.split('|')[1:-1]]  # split by |, skip first/last empty
        if len(cells) < 7:
            continue

        try:
            # Original columns: ISO ts, agent_type, model, duration_sec, tokens_in, tokens_out, verdict
            # New columns: phase, wave
            iso_ts = cells[0]
            agent_type = cells[1]
            model = cells[2]
            duration_sec = int(cells[3]) if cells[3] else 0
            tokens_in = int(cells[4]) if cells[4] else 0
            tokens_out = int(cells[5]) if cells[5] else 0
            verdict = cells[6] if len(cells) > 6 else 'OK'
            phase = cells[7].strip() if len(cells) > 7 and cells[7].strip() else None
            wave = cells[8].strip() if len(cells) > 8 and cells[8].strip() else None

            # Try to parse wave as int
            wave_num = None
            if wave:
                try:
                    wave_num = int(wave)
                except ValueError:
                    pass

            rows.append({
                'iso_ts': iso_ts,
                'agent_type': agent_type,
                'model': model,
                'duration_sec': duration_sec,
                'tokens_in': tokens_in,
                'tokens_out': tokens_out,
                'verdict': verdict,
                'phase': phase,
                'wave': wave_num,
            })
        except (ValueError, IndexError):
            # Skip malformed lines silently
            continue

    return rows


def summary(output_format='text'):
    """Aggregate ledger entries by wave and phase; report total tokens/cost.

    Args:
        output_format: 'text' (default) or 'json'
    """
    # Use shared parser to get rows
    rows = parse_ledger_rows()

    # Parse ledger: skip header and separator lines, parse data lines
    by_wave_phase = defaultdict(lambda: {'tokens_out': 0, 'tokens_in': 0, 'entries': 0, 'duration': 0})
    by_wave = defaultdict(lambda: {'tokens_out': 0, 'tokens_in': 0, 'entries': 0, 'duration': 0})
    totals = {'tokens_out': 0, 'tokens_in': 0, 'entries': 0, 'duration': 0}

    for row in rows:
        tokens_in = row['tokens_in']
        tokens_out = row['tokens_out']
        duration_sec = row['duration_sec']
        wave_num = row['wave']
        phase = row['phase']

        # Accumulate by wave+phase
        key = (wave_num, phase)
        by_wave_phase[key]['tokens_out'] += tokens_out
        by_wave_phase[key]['tokens_in'] += tokens_in
        by_wave_phase[key]['entries'] += 1
        by_wave_phase[key]['duration'] += duration_sec

        # Accumulate by wave
        by_wave[wave_num]['tokens_out'] += tokens_out
        by_wave[wave_num]['tokens_in'] += tokens_in
        by_wave[wave_num]['entries'] += 1
        by_wave[wave_num]['duration'] += duration_sec

        # Accumulate totals
        totals['tokens_out'] += tokens_out
        totals['tokens_in'] += tokens_in
        totals['entries'] += 1
        totals['duration'] += duration_sec

    if output_format == 'json':
        import json
        result = {
            'by_wave_phase': {str(k): v for k, v in sorted(by_wave_phase.items())},
            'by_wave': {str(k): v for k, v in sorted(by_wave.items())},
            'totals': totals
        }
        print(json.dumps(result, indent=2))
    else:
        # Text format: human-readable table
        print('\n=== Ledger Summary ===')
        print(f'\nTotal: {totals["entries"]} entries | {totals["tokens_in"]} in | {totals["tokens_out"]} out | {totals["duration"]}s\n')

        if by_wave_phase:
            print('By Wave + Phase:')
            print('  Wave | Phase    | Entries | Tokens In | Tokens Out | Duration')
            print('  -----|----------|---------|-----------|------------|----------')
            for (wave_num, phase), stats in sorted(by_wave_phase.items()):
                w_str = str(wave_num) if wave_num is not None else 'None'
                p_str = phase if phase else '(no phase)'
                print(f'  {w_str:4} | {p_str:8} | {stats["entries"]:7} | {stats["tokens_in"]:9} | {stats["tokens_out"]:10} | {stats["duration"]:8}')

        if by_wave:
            print('\nBy Wave (Total):')
            print('  Wave | Entries | Tokens In | Tokens Out | Duration')
            print('  -----|---------|-----------|------------|----------')
            for wave_num, stats in sorted(by_wave.items()):
                w_str = str(wave_num) if wave_num is not None else 'None'
                print(f'  {w_str:4} | {stats["entries"]:7} | {stats["tokens_in"]:9} | {stats["tokens_out"]:10} | {stats["duration"]:8}')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == 'append':
        # append <ts> <agent_type> <model> <dur> <tokens_in> <tokens_out> [verdict] [phase] [wave]
        if len(sys.argv) < 7:
            print('Usage: fleet_ledger.py append <ts> <agent_type> <model> <dur_sec> <tokens_in> <tokens_out> [verdict] [phase] [wave]')
            sys.exit(1)

        ts = sys.argv[2]
        agent_type = sys.argv[3]
        model = sys.argv[4]
        dur = sys.argv[5]
        ti = sys.argv[6]
        to = sys.argv[7] if len(sys.argv) > 7 else '0'
        verdict = sys.argv[8] if len(sys.argv) > 8 else 'OK'
        phase = sys.argv[9] if len(sys.argv) > 9 else None
        wave = sys.argv[10] if len(sys.argv) > 10 else None

        try:
            append_ledger_line(ts, agent_type, model, dur, ti, to, verdict, phase, wave)
            print(f'Appended: {ts} {agent_type} {model} {dur}s {ti}->{to} [{verdict}] phase={phase} wave={wave}')
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(1)

    elif cmd == 'append-wave':
        # append-wave --report-file <path> --wave <id> --phase <phase> --timestamp <iso>
        import argparse
        parser = argparse.ArgumentParser(description='Append wave outcome to ledger from report JSON')
        parser.add_argument('--report-file', required=True, help='Path to workflow report JSON')
        parser.add_argument('--wave', required=True, help='Wave number')
        parser.add_argument('--phase', required=True, help='Phase name (build|verify|repair)')
        parser.add_argument('--timestamp', required=True, help='ISO 8601 timestamp')

        try:
            args = parser.parse_args(sys.argv[2:])
        except SystemExit:
            sys.exit(1)

        success, message = append_wave(args.report_file, args.wave, args.phase, args.timestamp)
        if success:
            print(message)
        else:
            print(message, file=sys.stderr)
        sys.exit(0 if success else 1)

    elif cmd == 'harvest':
        harvest()

    elif cmd == 'rotate':
        rotate()

    elif cmd == 'summary':
        output_fmt = 'json' if '--json' in sys.argv else 'text'
        summary(output_fmt)

    else:
        print(f'Unknown command: {cmd}')
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
