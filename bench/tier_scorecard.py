"""Summarize matched archived coding runs and local infrastructure judgments."""
import json
from collections import Counter
from pathlib import Path

from operational_transfer import SHARED_CASES

RESULTS = Path(__file__).resolve().parent / "results"
ARCHIVE = {
    "one-shot": "seam-u-orig-2026-07-27.jsonl",
    "one-shot with repro": "seam-u-repro.jsonl",
    "seated one-pass": "seam-s-checkpoint.jsonl",
    "seated with repair": "seam-s-loop.jsonl",
}
LOCAL = ("qwen2.5-coder-14b", "qwen3-coder-30b", "qwen3.8-27b")
BLIND_TAGS = ("haiku-4.5", "sonnet-5", "opus-4.8", "fable-blind", "gpt-4o",
              "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "")
JUDGMENTS = {
    "new capability": {
        "registration-audit", "tracker-latency", "shard-timing", "merge-contention",
        "journal-latency", "burn-projection", "task-stream", "new-worker-provider",
    },
    "reported value": {"wave-duration", "timeline-timestamps", "status-heartbeat", "dashboard-root"},
    "state across steps": {"backup-cycle", "concurrent-store-append", "tracker-retry", "disk-only-items"},
}


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
        correct = set()
        for r in rows:
            if (r["model"].replace(":", "-") != model or r["arm"] != "baseline"
                    or r["expected"] != expected[r["case"]]):
                raise ValueError(f"Mismatched run metadata for {r['case']}")
            verdict = r["decision"]["verdict"]
            if not r["decision"]["schema_validated"] or verdict == "DECISION_FAILED":
                counts["unscored"] += 1
            elif verdict == expected[r["case"]]:
                counts["correct"] += 1
                correct.add(r["case"])
            else:
                counts["disagreed"] += 1
                print(model, r["case"], "expected", expected[r["case"]], "returned", verdict)
        print(model, dict(counts), "tokens", sum(r["tokens"] for r in rows))
        print({group: (len(correct & ids), len(ids)) for group, ids in JUDGMENTS.items()})


def shared_counts():
    path = RESULTS.parents[1] / "driver/decisions/shadow/corpus-2026-07-23.jsonl"
    truth = {r["id"]: r["labels"]["ground_truth"] for r in
             map(json.loads, path.read_text(encoding="utf-8").splitlines()) if r["id"] in SHARED_CASES}
    for tag in (*BLIND_TAGS, *LOCAL):
        if tag in LOCAL:
            rows = read_rows(f"operational-shared-{tag}.jsonl")
            if any(r["model"].replace(":", "-") != tag or r["arm"] != "baseline"
                   or r["expected"] != truth.get(r["case"]) for r in rows):
                raise ValueError(f"Mismatched shared-corpus metadata for {tag}")
            pairs = [(r["case"], r["decision"]["verdict"], r["decision"]["schema_validated"]) for r in rows]
        else:
            suffix = "-" + tag if tag else ""
            path = RESULTS / f"shadow-adjudication-2026-07-23{suffix}.json"
            rows = json.loads(path.read_text(encoding="utf-8"))["items"]
            pairs = [(r["id"], r["challenger_classification"], r["schema_valid"]) for r in rows if r["id"] in truth]
        if len(pairs) != len(SHARED_CASES) or {key for key, _, _ in pairs} != set(SHARED_CASES):
            raise ValueError(f"Missing or duplicated common cases for {tag}")
        by_class = Counter(truth[key] for key, verdict, valid in pairs if valid and truth[key] == verdict)
        print(tag or "gpt-4o-mini", dict(by_class), "unscored",
              sum(not valid or verdict == "DECISION_FAILED" for _, verdict, valid in pairs))


def transfer_counts(trial, cases, models):
    truth = {case["id"]: case["expected"] for case in cases}
    arms = ("baseline", "candidate")
    cells = {(key, arm) for key in truth for arm in arms}
    for tag in models:
        rows = read_rows(f"operational-{trial}-{tag}.jsonl")
        if len(rows) != len(cells) or {(row["case"], row["arm"]) for row in rows} != cells:
            raise ValueError(f"Missing or duplicated {trial} decisions for {tag}")
        correct = {arm: set() for arm in arms}
        tokens = Counter()
        failed = Counter()
        for row in rows:
            key, arm, decision = row["case"], row["arm"], row["decision"]
            if row["expected"] != truth[key] or row["model"].replace(":", "-") != tag:
                raise ValueError(f"Mismatched {trial} metadata for {tag} {key}")
            tokens[arm] += row["tokens"]
            if not decision["schema_validated"] or decision["verdict"] == "DECISION_FAILED":
                failed[arm] += 1
            elif decision["verdict"] == truth[key]:
                correct[arm].add(key)
        before, after = (correct[arm] for arm in arms)
        print(tag, trial, len(before), len(after), "gains", sorted(after - before),
              "losses", sorted(before - after), "tokens", dict(tokens), "failed", dict(failed))


if __name__ == "__main__":
    archive_counts()
    local_counts()
    shared_counts()
    fixtures = RESULTS.parent / "fixtures"
    transfer_counts("terminal", json.loads((fixtures / "operational-terminal-transfer.json").read_text(encoding="utf-8")), LOCAL)
    receipt = json.loads((fixtures / "operational-receipt-transfer.json").read_text(encoding="utf-8"))
    transfer_counts("receipt", receipt["cases"], [receipt["model"].replace(":", "-")])
