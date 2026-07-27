"""Cheap refusal probe for the frontier slice (Amendment 3 pre-run gate).

Sends every task prompt to the given Claude tiers over the direct-HTTP API
transport with a small max_tokens and reports which (task, tier) pairs come
back with stop_reason=refusal. No scoring, no checkpoint writes. Cost is a
few cents per model (input tokens only, tiny output cap).

Usage:
  python bench/probe_refusals.py                       # fable + opus, all tasks
  python bench/probe_refusals.py --models claude-fable-5 --tasks ft101,ft102

API-only per the bench-no-cli-fallback directive: BENCH_API_KEY required;
missing key = exit 2, never a CLI fallback or credential hunt.
"""
import argparse
import concurrent.futures
import json
import os
import sys
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"


def probe(model, task_id, prompt, api_key, max_tokens, timeout_s):
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        return task_id, model, out.get("stop_reason"), None
    except Exception as e:
        return task_id, model, None, str(e)[:120]


def main():
    ap = argparse.ArgumentParser(description="Refusal probe (no scoring, tiny output cap)")
    ap.add_argument("--models", default="claude-fable-5,claude-opus-5")
    ap.add_argument("--tasks", default="", help="Comma-separated task-id prefixes to probe (default: all)")
    ap.add_argument("--tasks-file", default="bench/tasks_frontier.jsonl")
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    api_key = os.environ.get("BENCH_API_KEY")
    if not api_key:
        print("ERROR: BENCH_API_KEY not set - probe is API-only (no CLI fallback).", file=sys.stderr)
        sys.exit(2)

    prefixes = [p for p in args.tasks.split(",") if p]
    tasks = []
    with open(args.tasks_file, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            if not prefixes or any(o["id"].startswith(p) for p in prefixes):
                tasks.append((o["id"], o["prompt"]))

    models = args.models.split(",")
    jobs = [(m, tid, prompt) for m in models for tid, prompt in tasks]
    print(f"Probing {len(tasks)} tasks x {len(models)} models = {len(jobs)} calls "
          f"(max_tokens={args.max_tokens})")

    refusals = []
    errors = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(probe, m, tid, prompt, api_key, args.max_tokens, args.timeout)
                for m, tid, prompt in jobs]
        for fut in concurrent.futures.as_completed(futs):
            tid, model, stop_reason, err = fut.result()
            done += 1
            if err:
                errors.append((model, tid, err))
            elif stop_reason == "refusal":
                refusals.append((model, tid))
            if done % 50 == 0:
                print(f"  {done}/{len(jobs)} probed ({len(refusals)} refusals so far)")

    print()
    if refusals:
        print(f"REFUSALS ({len(refusals)}):")
        for model, tid in sorted(refusals):
            print(f"  {model}  {tid}")
    else:
        print("REFUSALS: none")
    if errors:
        print(f"TRANSPORT ERRORS ({len(errors)}, retry or inspect):")
        for model, tid, err in sorted(errors)[:20]:
            print(f"  {model}  {tid}  {err}")
    sys.exit(1 if refusals else 0)


if __name__ == "__main__":
    main()
