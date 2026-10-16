#!/usr/bin/env python3
"""Report the lane-contract A/B experiment, stdlib only.

INDEX: lane_ab_report.py -- Lane-contract A/B report (arm A full contract vs arm B walls-only): dispatches, forced, denials, arm B fault relaunches, rules_by_fault_count; fail-closed exit 2 on bad input; stdlib only.

Arm A dispatches run under the full LANE-CONTRACT.md; arm B dispatches run under
LANE-CONTRACT-MIN.md (walls only). This tool opens ONLY the paths passed by flag.

Inputs
  --ab-ledger PATH          state/lane-ab.jsonl, one JSON object per dispatch with
                            ts (ISO-8601), arm ("A" or "B"), prompt_sha256, and
                            optional reason (reason "forced" counts as forced),
                            agent_type, model, project.
  --resistance-ledger PATH  hook denial records. Their shape is NOT documented in
                            this repo: no file under tools/, docs/ or state_store/
                            names resistance-ledger. So this tool accepts any JSON
                            line that carries a ts and a decision or kind field.
                            A record is a denial when that field is one of deny,
                            denied, block or blocked (case-insensitive). The rule
                            name is taken from rule, then hook, then rule_name,
                            else "unknown".
  --task-events PATH        optional JSONL of lane task events (shape of
                            tools/ingest_task_events.py: ts, agent_id, agent_type,
                            ...). Tokens are summed from a numeric tokens or
                            total_tokens field when present, matched by agent_type.
                            The repo's documented event shape carries no token
                            field, so tokens per dispatch reads null until a writer
                            adds one. Arms that share an agent_type share its tokens.

Definitions
  fault     an arm B dispatch whose prompt_sha256 is dispatched again under arm A
            within --fault-window minutes (inclusive). That is a page-fault relaunch.
  denial    attributed to the latest dispatch at or before the denial ts, in the
            same project when the denial names one. Unattributed denials are
            counted apart.
  rules_by_fault_count
            denials attributed to faulting arm B dispatches, grouped by rule. Rules
            that keep faulting are the candidates for pre-loading.
  verdict   printed only when BOTH arms have at least --min-n dispatches. It is a
            descriptive comparison of the arm B fault rate against the arm A forced
            rate; the decision stays with the operator. Otherwise the output is
            "INSUFFICIENT N".

Exit codes (fail closed)
  0  report produced, including the INSUFFICIENT N case.
  2  input error: missing or unreadable file, malformed JSON, a non-object line,
     a missing ts or prompt_sha256, an unknown arm, or a bad timestamp.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARMS = ("A", "B")
DENIAL_VALUES = frozenset({"deny", "denied", "block", "blocked"})
TOKEN_FIELDS = ("tokens", "total_tokens")
DEFAULT_FAULT_WINDOW_MIN = 60.0
DEFAULT_MIN_N = 10


class LedgerError(Exception):
    """An input the report cannot read exactly. Callers must fail closed on it."""


def _parse_ts(value, where):
    """Parse an ISO-8601 timestamp; a naive value is taken as UTC."""
    if not isinstance(value, str) or not value:
        raise LedgerError(f"{where}: missing or non-string ts")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LedgerError(f"{where}: bad ts {value!r}: {exc}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _read_jsonl(path_text, label):
    """Return the JSON objects of one JSONL file. Blank lines are skipped."""
    path = Path(path_text)
    if not path.is_file():
        raise LedgerError(f"{label}: not a file: {path_text}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LedgerError(f"{label}: unreadable {path_text}: {exc}") from exc
    rows = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{label} line {lineno}: malformed JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise LedgerError(f"{label} line {lineno}: expected a JSON object")
        rows.append(record)
    return rows


def _dispatches(ab_rows):
    """Validate and normalise arm ledger rows, sorted by time."""
    out = []
    for index, rec in enumerate(ab_rows, start=1):
        where = f"ab-ledger row {index}"
        arm = rec.get("arm")
        if arm not in ARMS:
            raise LedgerError(f"{where}: arm must be 'A' or 'B', got {arm!r}")
        sha = rec.get("prompt_sha256")
        if not isinstance(sha, str) or not sha:
            raise LedgerError(f"{where}: missing prompt_sha256")
        out.append({
            "t": _parse_ts(rec.get("ts"), where),
            "arm": arm,
            "sha": sha,
            "forced": str(rec.get("reason") or "").strip().lower() == "forced",
            "agent_type": str(rec.get("agent_type") or ""),
            "project": rec.get("project"),
        })
    out.sort(key=lambda d: d["t"])
    return out


def _denials(resistance_rows):
    """Return the denial records. Non-denial decisions are valid and ignored."""
    out = []
    for index, rec in enumerate(resistance_rows, start=1):
        where = f"resistance-ledger row {index}"
        t = _parse_ts(rec.get("ts"), where)
        if "decision" not in rec and "kind" not in rec:
            raise LedgerError(f"{where}: record has neither decision nor kind")
        value = str(rec.get("decision", rec.get("kind"))).strip().lower()
        if value not in DENIAL_VALUES:
            continue
        rule = rec.get("rule") or rec.get("hook") or rec.get("rule_name") or "unknown"
        out.append({"t": t, "rule": str(rule), "project": rec.get("project")})
    return out


def _faulting_indexes(dispatches, window_minutes):
    """Indexes of arm B dispatches re-dispatched under arm A inside the window."""
    a_times = {}
    for d in dispatches:
        if d["arm"] == "A":
            a_times.setdefault(d["sha"], []).append(d["t"])
    limit_delta = timedelta(minutes=window_minutes)
    faults = set()
    for index, d in enumerate(dispatches):
        if d["arm"] != "B":
            continue
        limit = d["t"] + limit_delta
        if any(d["t"] < t <= limit for t in a_times.get(d["sha"], [])):
            faults.add(index)
    return faults


def _attribute(denial, dispatches):
    """Index of the latest dispatch at or before the denial, or None."""
    for index in range(len(dispatches) - 1, -1, -1):
        d = dispatches[index]
        if d["t"] > denial["t"]:
            continue
        if denial["project"] is None or d["project"] == denial["project"]:
            return index
    return None


def _tokens_for(events, agent_types):
    """Return (token total, number of events that carried a token field)."""
    total = 0
    seen = 0
    for rec in events:
        if not agent_types or str(rec.get("agent_type") or "") not in agent_types:
            continue
        for field in TOKEN_FIELDS:
            value = rec.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total += value
                seen += 1
                break
    return total, seen


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def build_report(ab_rows, resistance_rows, window_minutes, task_events=None, min_n=DEFAULT_MIN_N):
    """Build the A/B report dict from raw ledger rows. Raises LedgerError on bad input."""
    if window_minutes < 0:
        raise LedgerError("fault window must be >= 0 minutes")
    if min_n < 1:
        raise LedgerError("min-n must be >= 1")

    dispatches = _dispatches(ab_rows)
    denials = _denials(resistance_rows)
    faults = _faulting_indexes(dispatches, window_minutes)

    denial_counts = Counter()
    unattributed = 0
    rule_counts = Counter()
    for denial in denials:
        index = _attribute(denial, dispatches)
        if index is None:
            unattributed += 1
            continue
        denial_counts[dispatches[index]["arm"]] += 1
        if index in faults:
            rule_counts[denial["rule"]] += 1

    arms = {}
    for arm in ARMS:
        arm_rows = [d for d in dispatches if d["arm"] == arm]
        n = len(arm_rows)
        stats = {
            "dispatches": n,
            "forced": sum(1 for d in arm_rows if d["forced"]),
            "denials": denial_counts[arm],
            "denials_per_dispatch": _ratio(denial_counts[arm], n),
        }
        if arm == "B":
            fault_count = len(faults)
            stats["faults"] = fault_count
            stats["faults_per_dispatch"] = _ratio(fault_count, n)
        else:
            stats["faults"] = None
            stats["faults_per_dispatch"] = None
        agent_types = {d["agent_type"] for d in arm_rows if d["agent_type"]}
        if task_events is None:
            stats["tokens_per_dispatch"] = None
        else:
            total, seen = _tokens_for(task_events, agent_types)
            stats["tokens_per_dispatch"] = round(total / n, 2) if seen and n else None
        arms[arm] = stats

    rules = [
        {"rule": rule, "faults": count}
        for rule, count in sorted(rule_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    n_a = arms["A"]["dispatches"]
    n_b = arms["B"]["dispatches"]
    insufficient = n_a < min_n or n_b < min_n
    verdict = None
    if not insufficient:
        rate_b = arms["B"]["faults_per_dispatch"]
        rate_a = _ratio(arms["A"]["forced"], n_a)
        if rate_b < rate_a:
            word = "LOWER"
        elif rate_b > rate_a:
            word = "HIGHER"
        else:
            word = "EQUAL"
        verdict = (
            f"verdict: arm B fault rate {rate_b:.2f} ({arms['B']['faults']}/{n_b}) "
            f"vs arm A forced rate {rate_a:.2f} ({arms['A']['forced']}/{n_a}): "
            f"arm B is {word}"
        )

    return {
        "window_minutes": window_minutes,
        "min_n": min_n,
        "arms": arms,
        "rules_by_fault_count": rules,
        "unattributed_denials": unattributed,
        "task_events": None if task_events is None else len(task_events),
        "insufficient": insufficient,
        "verdict": verdict,
    }


def _fmt(value):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def format_text(report):
    """Render the report as a text table plus the verdict or INSUFFICIENT line."""
    lines = [
        f"lane-contract A/B report  window={report['window_minutes']:g} min  min-n={report['min_n']}",
        f"{'arm':<5}{'dispatches':>11}{'forced':>8}{'denials':>9}{'den/disp':>10}"
        f"{'faults':>8}{'fault/disp':>12}{'tok/disp':>10}",
    ]
    for arm in ARMS:
        s = report["arms"][arm]
        lines.append(
            f"{arm:<5}{s['dispatches']:>11}{s['forced']:>8}{s['denials']:>9}"
            f"{_fmt(s['denials_per_dispatch']):>10}{_fmt(s['faults']):>8}"
            f"{_fmt(s['faults_per_dispatch']):>12}{_fmt(s['tokens_per_dispatch']):>10}"
        )
    lines.append("rules_by_fault_count:")
    if report["rules_by_fault_count"]:
        for entry in report["rules_by_fault_count"]:
            lines.append(f"  {entry['rule']}  {entry['faults']}")
    else:
        lines.append("  (none)")
    if report["task_events"] is None:
        lines.append("task-events: not provided")
    else:
        lines.append(f"task-events: {report['task_events']} records read")
    lines.append(f"unattributed denials: {report['unattributed_denials']}")
    if report["insufficient"]:
        n_a = report["arms"]["A"]["dispatches"]
        n_b = report["arms"]["B"]["dispatches"]
        lines.append(
            f"INSUFFICIENT N (arm A={n_a}, arm B={n_b}, need min-n={report['min_n']} per arm)"
        )
    else:
        lines.append(report["verdict"])
    return "\n".join(lines)


def main(argv=None):
    """CLI entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(description="Report the lane-contract A/B experiment.")
    parser.add_argument("--ab-ledger", required=True, help="path to lane-ab.jsonl")
    parser.add_argument("--resistance-ledger", required=True, help="path to resistance-ledger.jsonl")
    parser.add_argument("--task-events", default=None, help="optional lane task-events JSONL")
    parser.add_argument(
        "--fault-window", type=float, default=DEFAULT_FAULT_WINDOW_MIN,
        help="minutes within which an arm A repeat of an arm B prompt is a fault (default 60)",
    )
    parser.add_argument("--min-n", type=int, default=DEFAULT_MIN_N, help="dispatches needed per arm for a verdict (default 10)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text table")
    args = parser.parse_args(argv)
    try:
        ab_rows = _read_jsonl(args.ab_ledger, "ab-ledger")
        resistance_rows = _read_jsonl(args.resistance_ledger, "resistance-ledger")
        events = _read_jsonl(args.task_events, "task-events") if args.task_events else None
        report = build_report(
            ab_rows, resistance_rows, args.fault_window,
            task_events=events, min_n=args.min_n,
        )
    except LedgerError as exc:
        print(f"lane_ab_report: input error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
