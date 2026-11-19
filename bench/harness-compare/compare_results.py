"""Compare harnesses over one run id in the shared results home.

A task with no recorded row for a harness is shown as "missing". It is never
counted as a pass or a fail. Disagreements are tasks where recorded harnesses
differ on passed.

Usage:
    python compare_results.py --run-id r1 [--results-root DIR] [--json]

Stdlib only. Reads JSON files only; no model or API call happens here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import harness_corpus as hc

NUMERIC_TOTALS = ("input_tokens", "output_tokens", "cost_usd", "wall_seconds")


def _load_rows(results_root: Path, run_id: str) -> dict[str, dict[str, dict]]:
    """Return {harness: {task_id: row}} for one run id."""
    rows: dict[str, dict[str, dict]] = {}
    if not results_root.is_dir():
        return rows
    for run_dir in sorted(results_root.glob("*/%s" % run_id)):
        if not run_dir.is_dir():
            continue
        harness = run_dir.parent.name
        for path in sorted(run_dir.glob("*.json")):
            row = hc.read_json(path)
            rows.setdefault(harness, {})[row["task_id"]] = row
    return rows


def _summarise(rows: dict[str, dict[str, dict]], task_ids: list[str]) -> dict:
    harnesses: dict[str, dict] = {}
    for harness, by_task in sorted(rows.items()):
        attempted = len(by_task)
        passed = sum(1 for r in by_task.values() if r["passed"] is True)
        totals: dict = {key: 0 for key in NUMERIC_TOTALS}
        unreported_tokens = 0
        for row in by_task.values():
            for key in NUMERIC_TOTALS:
                if row.get(key) is not None:
                    totals[key] += row[key]
            if row.get("input_tokens") is None:
                unreported_tokens += 1
        totals["unreported_input_tokens"] = unreported_tokens
        harnesses[harness] = {
            "attempted": attempted,
            "passed": passed,
            "missing": len(task_ids) - attempted,
            "totals": totals,
        }

    matrix: dict[str, dict[str, str]] = {}
    disagreements: list[str] = []
    for task_id in task_ids:
        cells: dict[str, str] = {}
        verdicts = set()
        for harness in sorted(rows):
            row = rows[harness].get(task_id)
            if row is None:
                cells[harness] = "missing"
            else:
                cells[harness] = "pass" if row["passed"] is True else "fail"
                verdicts.add(row["passed"] is True)
        matrix[task_id] = cells
        if len(verdicts) > 1:
            disagreements.append(task_id)

    return {
        "total_tasks": len(task_ids),
        "harnesses": harnesses,
        "matrix": matrix,
        "disagreements": disagreements,
    }


def _table(summary: dict, run_id: str) -> str:
    harnesses = list(summary["harnesses"])
    lines = ["run %s: %d tasks" % (run_id, summary["total_tasks"])]
    header = ["task"] + harnesses
    body = []
    for task_id, cells in summary["matrix"].items():
        body.append([task_id] + [cells.get(h, "missing") for h in harnesses])
    widths = [max(len(str(r[i])) for r in [header] + body) for i in range(len(header))]
    for row in [header] + body:
        lines.append("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(row)).rstrip())
    lines.append("")
    for harness in harnesses:
        info = summary["harnesses"][harness]
        lines.append(
            "%s: attempted %d, passed %d, missing %d"
            % (harness, info["attempted"], info["passed"], info["missing"])
        )
    lines.append("disagreements: %s" % (", ".join(summary["disagreements"]) or "none"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--results-root", type=Path, default=hc.RESULTS_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not hc.valid_name(args.run_id):
        parser.error("--run-id is not a safe name: %r" % args.run_id)

    task_ids = [row["id"] for row in hc.load_manifest()]
    rows = _load_rows(args.results_root, args.run_id)
    summary = _summarise(rows, task_ids)

    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(_table(summary, args.run_id) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
