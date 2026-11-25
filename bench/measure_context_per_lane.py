"""Estimate context admission from one local orchestrator JSONL archive."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from driver.turn_context import CHARS_PER_TOKEN, size_ollama_context


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def estimated_tokens(content):
    messages = [{"content": compact(content)}]
    return size_ollama_context(messages).prompt_tokens


def lookup_text(row, content):
    if (row.get("origin") or {}).get("kind") == "human" and row["type"] == "user":
        if isinstance(content, str):
            return content
        return "\n".join(b["text"] for b in content if b.get("type") == "text")
    if row["type"] == "assistant" and isinstance(content, list):
        return "\n".join(compact(b["input"]) for b in content if b.get("type") == "tool_use")
    return ""


def measure(rows, session):
    """Charge each new message once. Store exact report versions for page faults."""
    seen = set()
    lanes = set()
    pages = {}
    latest = {}
    baseline = paged = faults = fault_estimate = messages = 0

    for row in rows:
        role = row["type"]
        if (role not in ("user", "assistant") or row.get("isSidechain")
                or row["sessionId"] != session or row["uuid"] in seen):
            continue
        seen.add(row["uuid"])
        content = row["message"]["content"]
        raw_estimate = estimated_tokens(content)
        messages += 1
        baseline += raw_estimate
        paged += raw_estimate
        origin = row.get("origin") or {}
        if role == "user" and origin.get("kind") == "peer" and origin.get("handback") is True:
            lane = origin["senderTaskId"]
            report = origin["handbackReportText"]
            if not lane or not report.strip():
                raise ValueError("final hand-back lacks a lane or report")
            lanes.add(lane)
            latest.pop(lane, None)
            headline = report.strip().splitlines()[0]
            if not re.match(r"^DONE\b", headline):
                continue
            # Change only the harness-indented report; preserve the trust envelope.
            body = "\n".join("  " + line for line in report.split("\n"))
            if not isinstance(content, str) or content.count(body) != 1:
                raise ValueError("cannot locate the exact report in its delivery envelope")
            key = f"handbacks/{row['uuid']}.txt"
            pages[key] = report
            latest[lane] = key
            receipt = f"  {headline}\n  Page: {key}"
            paged += estimated_tokens(content.replace(body, receipt)) - raw_estimate
            continue

        # In replay, a human or tool naming a paged lane serves as an explicit lookup.
        # Each distinct lane faults once per referencing message, including repeat reads.
        reference = lookup_text(row, content)
        named_lanes = set(re.findall(r"[\w-]+", reference)) & latest.keys()
        handles = set(re.findall(r"handbacks/[\w-]+\.txt\b", reference))
        requested = {latest[lane] for lane in named_lanes} | (handles & pages.keys())
        for key in requested:
            request = [{"type": "tool_use", "name": "read_page", "input": {"page": key}}]
            response = [{"type": "tool_result", "content": pages[key]}]
            charge = estimated_tokens(request) + estimated_tokens(response)
            paged += charge
            faults += 1
            fault_estimate += charge

    finishes = len(lanes)
    if not finishes:
        raise ValueError("no final peer hand-backs for this main session")
    return {
        "main_thread_messages": messages,
        "lane_finishes": finishes,
        "paged_reports": len(pages),
        "page_ins": faults,
        "page_in_estimated_tokens": fault_estimate,
        "baseline_estimated_tokens": baseline,
        "paged_estimated_tokens": paged,
        "baseline_estimated_tokens_per_lane_finish": baseline / finishes,
        "paged_estimated_tokens_per_lane_finish": paged / finishes,
    }


def replay(path, session):
    digest = hashlib.sha256()

    def records(stream):
        for line in stream:
            digest.update(line)
            yield json.loads(line)

    with path.open("rb") as stream:
        metrics = measure(records(stream), session)
    return {
        "command": f"python bench/measure_context_per_lane.py --session {session}",
        "transcript_sha256": digest.hexdigest(),
        "token_estimate_method": (
            "driver.turn_context.size_ollama_context: count UTF-16 code units in each "
            f"compact JSON message, divide by {CHARS_PER_TOKEN}, round up, then sum."
        ),
        **metrics,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=UUID, required=True)
    parser.add_argument("--transcript", type=Path, help="archived JSONL at another location")
    parser.add_argument("--out", type=Path, help="also write the aggregate JSON")
    args = parser.parse_args()
    session = str(args.session)
    try:
        path = args.transcript
        if path is None:
            root = Path.home() / ".claude" / "projects"
            matches = list(root.glob(f"*/{session}.jsonl"))
            if len(matches) != 1:
                raise ValueError("pass --transcript to select the session archive")
            path = matches[0]
        result = json.dumps(replay(path, session), indent=2) + "\n"
        if args.out:
            if args.out.resolve() == path.resolve():
                raise ValueError("--out must differ from the input archive")
            with args.out.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(result)
        sys.stdout.write(result)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(2, f"replay failed: {exc}\n")


if __name__ == "__main__":
    main()
