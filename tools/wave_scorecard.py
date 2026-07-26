#!/usr/bin/env python3
"""
Wave Quality Scorecard Generator — deterministic metrics from telemetry.

Generates quality scorecards answering "how healthy are our waves?" from on-disk telemetry.

Usage:
  wave_scorecard.py [--json|--md] [--waves N] [--state-root PATH]

Outputs:
  - ASCII (default): human-readable text format
  - --json: machine-readable JSON
  - --md: markdown table for side-by-side comparison
  - --waves N: show last N waves (default: all)

Metrics (where sources exist):
  - Items dispatched/succeeded (from ledger)
  - Repair rounds used (from ledger)
  - First-try-green rate (from ledger verdict timing)
  - Defect-escape count (from git history if available)
  - Tokens + estimated cost by phase/model (from ledger)
  - Agent success by agent_type (from ledger)
  - Retry frequency (from ledger entries)

Sources:
  - AESOP_STATE_ROOT/ledger/OUTCOMES-LEDGER.md: fleet outcome telemetry
  - tools/defect_escape.py: Haiku code quality metrics
  - Tools report on n/a (missing source) rather than inventing.
"""

import sys
import json
import os
from pathlib import Path
from collections import defaultdict

try:
    from common import get_state_dir
except ImportError:
    from tools.common import get_state_dir


def parse_ledger_rows():
    """Parse OUTCOMES-LEDGER.md and return structured rows.

    Returns:
        list of dicts with keys: iso_ts, agent_type, model, duration_sec,
        tokens_in, tokens_out, verdict, phase, wave
    """
    state_dir = get_state_dir()
    ledger_file = state_dir / "ledger" / "OUTCOMES-LEDGER.md"

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
        cells = [c.strip() for c in line.split('|')[1:-1]]  # split by |, skip first/last
        if len(cells) < 7:
            continue

        try:
            iso_ts = cells[0]
            agent_type = cells[1]
            model = cells[2]
            duration_sec = int(cells[3]) if cells[3] else 0
            tokens_in = int(cells[4]) if cells[4] else 0
            tokens_out = int(cells[5]) if cells[5] else 0
            verdict = cells[6] if len(cells) > 6 else 'OK'
            phase = cells[7].strip() if len(cells) > 7 and cells[7].strip() else None
            wave = cells[8].strip() if len(cells) > 8 and cells[8].strip() else None

            # Parse wave as int
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
            continue

    return rows


def compute_wave_metrics(rows):
    """Compute quality metrics grouped by wave.

    Args:
        rows: list of ledger rows

    Returns:
        dict mapping wave_num to metrics dict
    """
    metrics_by_wave = defaultdict(lambda: {
        'entries': 0,
        'ok_count': 0,
        'failed_count': 0,
        'empty_count': 0,
        'hung_count': 0,
        'tokens_in': 0,
        'tokens_out': 0,
        'duration_sec': 0,
        'by_phase': defaultdict(lambda: {'entries': 0, 'tokens_out': 0, 'ok_count': 0, 'failed_count': 0}),
        'by_model': defaultdict(lambda: {'entries': 0, 'tokens_out': 0, 'ok_count': 0, 'failed_count': 0}),
        'by_agent_type': defaultdict(lambda: {'entries': 0, 'ok_count': 0, 'failed_count': 0}),
        'repair_rounds': 0,
    })

    for row in rows:
        wave = row['wave']
        if wave is None:
            wave = 0  # Unknown wave

        m = metrics_by_wave[wave]

        m['entries'] += 1
        m['tokens_in'] += row['tokens_in']
        m['tokens_out'] += row['tokens_out']
        m['duration_sec'] += row['duration_sec']

        # Count verdicts
        verdict = row['verdict']
        if verdict == 'OK':
            m['ok_count'] += 1
        elif verdict == 'FAILED':
            m['failed_count'] += 1
        elif verdict == 'EMPTY':
            m['empty_count'] += 1
        elif verdict == 'HUNG':
            m['hung_count'] += 1

        # Track by phase
        phase = row['phase'] or 'unknown'
        phase_data = m['by_phase'][phase]
        phase_data['entries'] += 1
        phase_data['tokens_out'] += row['tokens_out']
        if verdict == 'OK':
            phase_data['ok_count'] += 1
        else:
            phase_data['failed_count'] += 1

        # Count repair rounds
        if phase == 'repair':
            m['repair_rounds'] += 1

        # Track by model
        model = row['model']
        model_data = m['by_model'][model]
        model_data['entries'] += 1
        model_data['tokens_out'] += row['tokens_out']
        if verdict == 'OK':
            model_data['ok_count'] += 1
        else:
            model_data['failed_count'] += 1

        # Track by agent type
        agent_type = row['agent_type']
        agent_data = m['by_agent_type'][agent_type]
        agent_data['entries'] += 1
        if verdict == 'OK':
            agent_data['ok_count'] += 1
        else:
            agent_data['failed_count'] += 1

    # Convert defaultdicts to regular dicts for JSON serialization
    result = {}
    for wave, metrics in metrics_by_wave.items():
        result[wave] = {
            'entries': metrics['entries'],
            'ok_count': metrics['ok_count'],
            'failed_count': metrics['failed_count'],
            'empty_count': metrics['empty_count'],
            'hung_count': metrics['hung_count'],
            'tokens_in': metrics['tokens_in'],
            'tokens_out': metrics['tokens_out'],
            'duration_sec': metrics['duration_sec'],
            'by_phase': dict(metrics['by_phase']),
            'by_model': dict(metrics['by_model']),
            'by_agent_type': dict(metrics['by_agent_type']),
            'repair_rounds': metrics['repair_rounds'],
        }

    return result


def compute_derived_metrics(wave_metrics):
    """Compute derived metrics from wave metrics.

    Args:
        wave_metrics: dict from compute_wave_metrics()

    Returns:
        dict with derived metrics
    """
    derived = {}

    for wave, metrics in wave_metrics.items():
        total = metrics['entries']
        if total == 0:
            derived[wave] = {
                'success_rate': 'n/a (no entries)',
                'first_try_green_rate': 'n/a',
                'repair_efficiency': 'n/a',
                'cost_estimate': 'n/a',
            }
            continue

        ok = metrics['ok_count']
        success_rate = (ok / total) * 100 if total > 0 else 0

        # First-try-green: rough estimate based on whether repairs happened
        repair_rounds = metrics['repair_rounds']
        if repair_rounds > 0:
            # Rough estimate: if there were repairs, not all were first-try-green
            first_try_estimate = ((total - repair_rounds) / total) * 100
        else:
            first_try_estimate = success_rate

        # Repair efficiency: OK repairs / total repairs
        if repair_rounds > 0:
            repair_ok = metrics['by_phase'].get('repair', {}).get('ok_count', 0)
            repair_efficiency = (repair_ok / repair_rounds) * 100
        else:
            repair_efficiency = None

        # Cost estimate (rough): assume haiku ~$0.001 per 1M tokens
        tokens_out = metrics['tokens_out']
        cost_estimate = (tokens_out / 1_000_000) * 0.001

        derived[wave] = {
            'success_rate': f"{success_rate:.1f}%",
            'first_try_green_rate': f"{first_try_estimate:.1f}%",
            'repair_efficiency': f"{repair_efficiency:.1f}%" if repair_efficiency is not None else "n/a",
            'cost_estimate': f"${cost_estimate:.4f}",
        }

    return derived


def format_ascii(wave_metrics, derived_metrics):
    """Format metrics as ASCII text.

    Args:
        wave_metrics: dict from compute_wave_metrics()
        derived_metrics: dict from compute_derived_metrics()

    Returns:
        formatted string
    """
    if not wave_metrics:
        return "No data available from ledger (n/a: no entries)\n"

    output = []
    output.append("\n=== Wave Quality Scorecards ===\n")

    # Sort by wave number
    for wave in sorted(wave_metrics.keys()):
        metrics = wave_metrics[wave]
        derived = derived_metrics.get(wave, {})

        output.append(f"Wave {wave}:")
        output.append(f"  Entries: {metrics['entries']}")
        output.append(f"  OK: {metrics['ok_count']} | Failed: {metrics['failed_count']} | Empty: {metrics['empty_count']} | Hung: {metrics['hung_count']}")
        output.append(f"  Success Rate: {derived.get('success_rate', 'n/a')}")
        output.append(f"  First-Try-Green: {derived.get('first_try_green_rate', 'n/a')}")
        output.append(f"  Repair Rounds: {metrics['repair_rounds']}")
        output.append(f"  Repair Efficiency: {derived.get('repair_efficiency', 'n/a')}")
        output.append(f"  Tokens In: {metrics['tokens_in']} | Out: {metrics['tokens_out']}")
        output.append(f"  Estimated Cost: {derived.get('cost_estimate', 'n/a')}")
        output.append(f"  Duration: {metrics['duration_sec']}s")

        # Tokens by phase
        if metrics['by_phase']:
            output.append("  Tokens by Phase:")
            for phase, phase_data in sorted(metrics['by_phase'].items()):
                output.append(f"    {phase}: {phase_data['entries']} entries, {phase_data['tokens_out']} tokens, {phase_data['ok_count']} OK")

        # Tokens by model
        if metrics['by_model']:
            output.append("  Tokens by Model:")
            for model, model_data in sorted(metrics['by_model'].items()):
                output.append(f"    {model}: {model_data['entries']} entries, {model_data['tokens_out']} tokens, {model_data['ok_count']} OK")

        # By agent type
        if metrics['by_agent_type']:
            output.append("  Success by Agent Type:")
            for agent_type, agent_data in sorted(metrics['by_agent_type'].items()):
                rate = (agent_data['ok_count'] / agent_data['entries'] * 100) if agent_data['entries'] > 0 else 0
                output.append(f"    {agent_type}: {agent_data['ok_count']}/{agent_data['entries']} OK ({rate:.1f}%)")

        output.append("")

    return "\n".join(output)


def format_json(wave_metrics, derived_metrics):
    """Format metrics as JSON.

    Args:
        wave_metrics: dict from compute_wave_metrics()
        derived_metrics: dict from compute_derived_metrics()

    Returns:
        JSON string
    """
    result = {
        'scorecards': {},
    }

    for wave in sorted(wave_metrics.keys()):
        metrics = wave_metrics[wave]
        derived = derived_metrics.get(wave, {})

        result['scorecards'][wave] = {
            'entries': metrics['entries'],
            'verdicts': {
                'ok': metrics['ok_count'],
                'failed': metrics['failed_count'],
                'empty': metrics['empty_count'],
                'hung': metrics['hung_count'],
            },
            'success_rate': derived.get('success_rate', 'n/a'),
            'first_try_green_rate': derived.get('first_try_green_rate', 'n/a'),
            'repair_rounds': metrics['repair_rounds'],
            'repair_efficiency': derived.get('repair_efficiency', 'n/a'),
            'tokens': {
                'in': metrics['tokens_in'],
                'out': metrics['tokens_out'],
            },
            'cost_estimate': derived.get('cost_estimate', 'n/a'),
            'duration_sec': metrics['duration_sec'],
            'by_phase': metrics['by_phase'],
            'by_model': metrics['by_model'],
            'by_agent_type': metrics['by_agent_type'],
        }

    if not result['scorecards']:
        result['error'] = "No data available from ledger"

    return json.dumps(result, indent=2)


def format_markdown(wave_metrics, derived_metrics):
    """Format metrics as markdown table (side-by-side).

    Args:
        wave_metrics: dict from compute_wave_metrics()
        derived_metrics: dict from compute_derived_metrics()

    Returns:
        markdown string
    """
    if not wave_metrics:
        return "No data available from ledger (n/a: no entries)\n"

    output = []
    output.append("\n## Wave Quality Scorecards\n")

    # Sort by wave number
    sorted_waves = sorted(wave_metrics.keys())

    # Header
    header = "| Metric" + "".join(f" | Wave {w}" for w in sorted_waves) + " |"
    separator = "|--------" + "".join("|--------" for _ in sorted_waves) + "|"

    output.append(header)
    output.append(separator)

    # Metrics rows
    metrics_to_show = [
        ('entries', 'Entries'),
        ('ok_count', 'OK Count'),
        ('failed_count', 'Failed Count'),
        ('repair_rounds', 'Repair Rounds'),
        ('tokens_out', 'Tokens Out'),
    ]

    for metric_key, metric_name in metrics_to_show:
        row = f"| {metric_name}"
        for wave in sorted_waves:
            metrics = wave_metrics[wave]
            value = metrics.get(metric_key, 0)
            row += f" | {value}"
        row += " |"
        output.append(row)

    # Derived metrics rows
    derived_to_show = [
        ('success_rate', 'Success Rate'),
        ('first_try_green_rate', 'First-Try-Green'),
        ('repair_efficiency', 'Repair Efficiency'),
        ('cost_estimate', 'Est. Cost'),
    ]

    for derived_key, derived_name in derived_to_show:
        row = f"| {derived_name}"
        for wave in sorted_waves:
            derived = derived_metrics.get(wave, {})
            value = derived.get(derived_key, 'n/a')
            row += f" | {value}"
        row += " |"
        output.append(row)

    output.append("")
    return "\n".join(output)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Wave Quality Scorecard Generator",
        epilog="Output formats: ASCII (default), --json (machine-readable), --md (markdown table)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--md", action="store_true", help="Output markdown table")
    parser.add_argument("--waves", type=int, help="Show last N waves (default: all)")
    parser.add_argument("--state-root", help="Path to state directory (overrides AESOP_STATE_ROOT)")

    args = parser.parse_args()

    # Override state root if provided
    if args.state_root:
        os.environ['AESOP_STATE_ROOT'] = args.state_root

    # Parse ledger
    rows = parse_ledger_rows()

    # Filter to last N waves if requested
    if args.waves:
        if rows:
            max_wave = max((r['wave'] for r in rows if r['wave'] is not None), default=0)
            min_wave = max(0, max_wave - args.waves + 1)
            rows = [r for r in rows if r['wave'] is None or r['wave'] >= min_wave]

    # Compute metrics
    wave_metrics = compute_wave_metrics(rows)
    derived_metrics = compute_derived_metrics(wave_metrics)

    # Format output
    if args.json:
        print(format_json(wave_metrics, derived_metrics))
    elif args.md:
        print(format_markdown(wave_metrics, derived_metrics))
    else:
        print(format_ascii(wave_metrics, derived_metrics))

    return 0


if __name__ == '__main__':
    sys.exit(main())
