"""Record one harness run of one corpus task into the shared results home.

Pass/fail is measured here by running the task's hidden check against the
working copy given by --workdir. The passed flag a harness claims is never
trusted. Exit 2 on any invalid input, and never overwrite an existing row.

Usage:
    python record_result.py --harness agy --model M --run-id r1 --task bug-paginate \
        --workdir <dir> --wall-seconds 12.5 --started-at 2026-11-18T10:00:00+00:00 \
        --input-tokens 100 --output-tokens 50 --cost-usd 0.02 --turns 3 --tool-calls 4

Stdlib only. No model or API call happens here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import harness_corpus as hc


def _nonneg_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0: %r" % text)
    return value


def _nonneg_float(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0: %r" % text)
    return value


def _aware_timestamp(text: str) -> str:
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("not an ISO-8601 timestamp: %r" % text) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp needs a timezone offset: %r" % text)
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--harness", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--wall-seconds", required=True, type=_nonneg_float)
    parser.add_argument("--started-at", required=True, type=_aware_timestamp)
    parser.add_argument("--input-tokens", type=_nonneg_int)
    parser.add_argument("--output-tokens", type=_nonneg_int)
    parser.add_argument("--cost-usd", type=_nonneg_float)
    parser.add_argument("--turns", type=_nonneg_int)
    parser.add_argument("--tool-calls", type=_nonneg_int)
    parser.add_argument("--notes", default="")
    parser.add_argument("--results-root", type=Path, default=hc.RESULTS_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    for label, value in (("--harness", args.harness), ("--run-id", args.run_id)):
        if not hc.valid_name(value):
            parser.error("%s is not a safe name: %r" % (label, value))

    try:
        row = hc.task_by_id(args.task)
    except KeyError:
        parser.error("unknown task: %r" % args.task)

    if not args.workdir.is_dir():
        parser.error("--workdir is not a directory: %s" % args.workdir)

    dest = args.results_root / args.harness / args.run_id / (args.task + ".json")
    if dest.exists():
        parser.error("refusing to overwrite recorded row: %s" % dest)

    check = hc.run_check(row, args.workdir)
    record = {
        "harness": args.harness,
        "model": args.model,
        "run_id": args.run_id,
        "task_id": args.task,
        "passed": check.passed,
        "check_exit": check.exit_code,
        "wall_seconds": args.wall_seconds,
        "input_tokens": args.input_tokens,
        "output_tokens": args.output_tokens,
        "cost_usd": args.cost_usd,
        "turns": args.turns,
        "tool_calls": args.tool_calls,
        "started_at": args.started_at,
        "notes": args.notes,
    }
    hc.write_json(dest, record)
    print("recorded %s %s %s passed=%s" % (args.harness, args.run_id, args.task, check.passed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
