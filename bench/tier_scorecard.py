"""Summarize matched archived coding runs and local infrastructure judgments."""
import json
from collections import Counter
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
ARCHIVE = {
    "one-shot": "seam-u-orig-2026-07-27.jsonl",
    "one-shot with repro": "seam-u-repro.jsonl",
    "seated one-pass": "seam-s-checkpoint.jsonl",
    "seated with repair": "seam-s-loop.jsonl",
}
LOCAL = ("qwen2.5-coder-14b", "qwen3-coder-30b", "qwen3.8-27b")


def read_rows(filename):
    return [json.loads(line) for line in (RESULTS / filename).read_text(encoding="utf-8").splitlines()]


def archive_counts():
    expected_tasks = {f"st{i:02d}" for i in range(1, 13)}
    expected_models = {
        "claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5",
        "claude-fable-5", "gpt-4o-mini",
    }
    for arm, filename in ARCHIVE.items():
        rows = read_rows(filename)
        repeats = range(3) if arm.startswith("one-shot") else range(1, 4)
        expected = {(t, m, r) for t in expected_tasks for m in expected_models for r in repeats}
        keys = [(row["task_id"], row["tier"], row["repeat"]) for row in rows]
        if len(keys) != len(expected) or set(keys) != expected:
            raise ValueError(f"Incomplete or duplicated archive cells in {filename}")
        for model in sorted(expected_models):
            group = [row for row in rows if row["tier"] == model]
            passes = sum(row.get("passed") is True for row in group)
            wrong = sum(row["status"] in ("fail", "scored") and row.get("passed") is False for row in group)
            other = Counter(row["status"] for row in group if row["status"] not in ("scored", "pass", "fail"))
            print(arm, model, "pass", passes, "scored wrong", wrong, "other", dict(other))
        if arm == "seated with repair":
            for task in sorted(expected_tasks):
                print(task, {model: sum(row.get("passed") is True for row in rows
                                        if row["task_id"] == task and row["tier"] == model)
                             for model in sorted(expected_models)})


def local_counts():
    fixture_dir = RESULTS.parent / "fixtures"
    expected = {}
    for name in ("operational-transfer", "operational-transfer-holdout", "operational-tier-corpus"):
        for case in json.loads((fixture_dir / f"{name}.json").read_text(encoding="utf-8")):
            if case["id"] in expected:
                raise ValueError(f"Duplicate fixture ID {case['id']}")
            expected[case["id"]] = case["expected"]
    for model in LOCAL:
        rows = read_rows(f"operational-tier-{model}.jsonl")
        if len(rows) != len(expected) or {r["case"] for r in rows} != set(expected):
            raise ValueError(f"Missing or duplicated local cells for {model}")
        counts = Counter()
        for r in rows:
            if (r["model"].replace(":", "-") != model or r["arm"] != "baseline"
                    or r["expected"] != expected[r["case"]]):
                raise ValueError(f"Mismatched run metadata for {r['case']}")
            verdict = r["decision"]["verdict"]
            if not r["decision"]["schema_validated"] or verdict == "DECISION_FAILED":
                counts["unscored"] += 1
            elif verdict == expected[r["case"]]:
                counts["correct"] += 1
            else:
                counts["disagreed"] += 1
                print(model, r["case"], "expected", expected[r["case"]], "returned", verdict)
        print(model, dict(counts), "tokens", sum(r["tokens"] for r in rows))


if __name__ == "__main__":
    archive_counts()
    local_counts()
