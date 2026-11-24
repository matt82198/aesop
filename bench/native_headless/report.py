"""Render native-arm comparisons offline, optionally importing the original trace receipts."""
import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

if __package__:
    from .qualify import Arm, observed_arm, read_rollouts
else:
    from qualify import Arm, observed_arm, read_rollouts


METRICS = ("tests_pass", "scoped", "accepted")


def render(rows):
    groups = defaultdict(dict)
    for row in rows:
        arm = Arm(*(row[k] for k in ("driver", "model", "provider")))
        if not all(isinstance(v, str) and v.strip() and not any(c in v for c in "|\r\n")
                   for v in asdict(arm).values()):
            raise ValueError("invalid arm identity")
        slot = (row["ticket"], row["repeat"])
        if slot in groups[arm]:
            raise ValueError(f"duplicate {arm.label} slot {slot}")
        if any(type(row[k]) is not bool for k in METRICS):
            raise ValueError(f"invalid score for {arm.label} slot {slot}")
        groups[arm][slot] = row
    if not groups:
        raise ValueError("no scored receipts")
    arms = sorted(groups, key=lambda arm: (arm.driver, arm.model, arm.provider))
    slots = groups[arms[0]].keys()
    if any(groups[arm].keys() != slots for arm in arms):
        raise ValueError("arm schedules differ")
    lines = ["# " + " vs. ".join(arm.label for arm in arms), "",
             "| Driver / model ID / provider | Ticket tests passed | Scoped edits | Accepted edits |",
             "|---|---:|---:|---:|"]
    for arm in arms:
        trials = groups[arm].values()
        scores = [f"{sum(row[k] for row in trials)}/{len(trials)}" for k in METRICS]
        lines.append("| " + " | ".join([arm.label, *scores]) + " |")
    return "\n".join(lines) + "\n"


def archived_rows(root):
    """Join each verified score to its private rollout by the receipt's SHA-256."""
    rows, sources = [], []
    for archive, shards in ((root, root / "parallel"),
                            (root / "default-05", root / "default-05")):
        receipt = archive / "verified/run.json"
        raw = receipt.read_bytes()
        run = json.loads(raw)
        source = receipt.relative_to(root).as_posix()
        sources.append(dict(receipt=source, sha256=hashlib.sha256(raw).hexdigest()))
        if len(run["rows"]) != run["planned"] or run["planned"] != 9:
            raise ValueError(f"incomplete archived schedule in {source}")
        for score in run["rows"]:
            ticket, repeat = score["ticket"], score["repeat"]
            shard = json.loads((shards / ticket / "run.json").read_bytes())
            matches = [r for r in shard["rows"] if (r["ticket"], r["repeat"]) == (ticket, repeat)]
            if len(matches) != 1:
                raise ValueError(f"ambiguous receipt for {ticket}-{repeat}")
            original = matches[0]
            if "trace_files" in original:
                paths = list(map(Path, original["trace_files"]))
            else:
                paths = sorted(Path(original["native_trace_dir"]).rglob("rollout*.jsonl"))
            events, hashes = read_rollouts(paths)
            if not hashes or sorted(hashes) != sorted(score["trace_sha256"]):
                raise ValueError(f"rollout checksum mismatch for {ticket}-{repeat}")
            arm = observed_arm(events)
            rows.append(dict(**asdict(arm), ticket=ticket, repeat=repeat,
                             **{k: score[k] for k in METRICS},
                             receipt=source, trace_sha256=hashes))
    render(rows)
    return {"sources": sources, "rows": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--receipts", nargs="+", type=Path)
    source.add_argument("--archive", type=Path, help="original native-ticket-comparison directory")
    parser.add_argument("--save-receipts", type=Path, help="write portable archive projection")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.archive:
        data = archived_rows(args.archive)
        rows = data["rows"]
        if args.save_receipts:
            args.save_receipts.write_bytes((json.dumps(data, indent=2) + "\n").encode())
    else:
        if args.save_receipts:
            parser.error("--save-receipts requires --archive")
        rows = [row for p in args.receipts for row in json.loads(p.read_bytes())["rows"]]
    args.out.write_bytes(render(rows).encode())


if __name__ == "__main__":
    main()
