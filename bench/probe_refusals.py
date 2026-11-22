"""Cheap refusal probe for the frontier slice (Amendment 3/4 pre-run gate).

Sends every task prompt to the given Claude tiers over the direct-HTTP API
transport with a small max_tokens and reports which (task, tier) pairs come
back with stop_reason=refusal. Supports both regex (v4) and tool (v5) modes.
No scoring, no checkpoint writes. Cost is a few cents per model.

Usage:
  python bench/probe_refusals.py                                 # fable + opus, regex mode
  python bench/probe_refusals.py --models claude-fable-5 --tasks ft101,ft102
  python bench/probe_refusals.py --answer-mode tool              # tool mode

API-only per the bench-no-cli-fallback directive: BENCH_API_KEY required;
missing key = exit 2, never a CLI fallback or credential hunt.
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API_URL = "https://api.anthropic.com/v1/messages"

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from tools.bench_runner import ollama_dummy_api_key, validate_ollama_base_url

# Import frontier_eligibility for tool mode
sys.path.insert(0, ".")
try:
    from bench.frontier_eligibility import parse_token_set, extract_correct_token, audit_tasks
except ImportError:
    parse_token_set = extract_correct_token = audit_tasks = None


def transform_prompt_for_tool_mode(prompt):
    """Remove the prose answer-format instruction and add tool instruction."""
    instruction_pattern = re.compile(
        r"(?:First line(?:\s+of\s+your\s+response)?:\s*exactly\s+.+?(?:\n|$))\s*"
        r"|(?:Answer with\s+.+?\s+on\s+the\s+first\s+line\s*(?:\n|$))",
        re.IGNORECASE,
    )
    transformed = instruction_pattern.sub("", prompt).strip()
    transformed += "\n\nSubmit your final answer by calling the submit_answer tool."
    return transformed


def probe(
    model,
    task_id,
    prompt,
    api_key,
    max_tokens,
    timeout_s,
    answer_mode="regex",
    tool_info=None,
    base_url=None,
    transport=None,
    allow_remote_ollama=False,
):
    """Probe a task/model pair for refusal.

    Args:
        model, task_id, prompt: as before
        api_key, max_tokens, timeout_s: as before
        answer_mode: "regex" or "tool"
        tool_info: Dict {task_id: (token_set, correct_token)} for tool mode
    """
    is_ollama = model.startswith("ollama/")
    effective_model = model[len("ollama/"):] if is_ollama else model

    # Transform prompt if tool mode
    request_prompt = prompt
    body_dict = {
        "model": effective_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": request_prompt}],
    }

    if answer_mode == "tool" and tool_info and task_id in tool_info:
        # Tool mode
        request_prompt = transform_prompt_for_tool_mode(prompt)
        token_set, _ = tool_info[task_id]
        body_dict["messages"][0]["content"] = request_prompt
        if is_ollama:
            body_dict["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_answer",
                        "description": "Submit the final answer",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "answer": {
                                    "type": "string",
                                    "enum": token_set,
                                    "description": "The final answer token",
                                }
                            },
                            "required": ["answer"],
                        },
                    },
                }
            ]
            body_dict["tool_choice"] = {
                "type": "function",
                "function": {"name": "submit_answer"},
            }
        else:
            body_dict["tools"] = [
                {
                    "name": "submit_answer",
                    "description": "Submit the final answer",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "answer": {
                                "type": "string",
                                "enum": token_set,
                                "description": "The final answer token"
                            }
                        },
                        "required": ["answer"]
                    }
                }
            ]
            body_dict["tool_choice"] = {"type": "tool", "name": "submit_answer"}

    if is_ollama:
        raw_url = (
            base_url
            if base_url is not None
            else os.environ.get("AESOP_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        )
        try:
            resolved_url = validate_ollama_base_url(
                raw_url, allow_remote=allow_remote_ollama
            )
            call_transport = transport
            if call_transport is None:
                driver_dir = str(_REPO_ROOT / "driver")
                if driver_dir not in sys.path:
                    sys.path.insert(0, driver_dir)
                from openai_transport import default_openai_transport

                call_transport = default_openai_transport
            out = call_transport(
                body_dict,
                timeout_s=timeout_s,
                base_url=resolved_url,
                api_key=ollama_dummy_api_key(),
            )
            choices = out.get("choices") or []
            finish = choices[0].get("finish_reason") if choices else None
            stop_reason = "refusal" if finish == "content_filter" else (finish or "stop")
            return task_id, model, stop_reason, None
        except Exception as e:
            return task_id, model, None, str(e)[:120]

    body = json.dumps(body_dict).encode("utf-8")
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
    ap.add_argument("--answer-mode", default="regex", choices=["regex", "tool"],
                    help="Answer mode: 'regex' (v4, default) or 'tool' (v5)")
    ap.add_argument("--models", default="claude-fable-5,claude-opus-5")
    ap.add_argument("--base-url", default=None, help="Optional base URL for ollama/<model> runs")
    ap.add_argument(
        "--allow-remote-ollama",
        action="store_true",
        help="Allow non-loopback Ollama base URL (never sends paid API keys)",
    )
    ap.add_argument("--tasks", default="", help="Comma-separated task-id prefixes to probe (default: all)")
    ap.add_argument("--tasks-file", default="bench/tasks_frontier.jsonl")
    ap.add_argument("--ground-truth-file", default="bench/ground_truth_frontier.jsonl")
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    needs_paid_key = any(not m.startswith("ollama/") for m in models)
    api_key = os.environ.get("BENCH_API_KEY", "") if needs_paid_key else ""
    if needs_paid_key and not api_key:
        print("ERROR: BENCH_API_KEY not set - probe is API-only (no CLI fallback).", file=sys.stderr)
        sys.exit(2)
    if any(m.startswith("ollama/") for m in models):
        validate_ollama_base_url(
            args.base_url
            if args.base_url is not None
            else os.environ.get("AESOP_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            allow_remote=args.allow_remote_ollama,
        )

    prefixes = [p for p in args.tasks.split(",") if p]
    tasks = []
    with open(args.tasks_file, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            if not prefixes or any(o["id"].startswith(p) for p in prefixes):
                tasks.append((o["id"], o["prompt"]))

    # Load tool_info if in tool mode
    tool_info = None
    if args.answer_mode == "tool" and audit_tasks:
        tool_info, _ = audit_tasks(args.tasks_file, args.ground_truth_file)

    jobs = [(m, tid, prompt) for m in models for tid, prompt in tasks]
    print(f"Probing {len(tasks)} tasks x {len(models)} models = {len(jobs)} calls "
          f"(mode={args.answer_mode}, max_tokens={args.max_tokens})")
    if tool_info:
        print(f"  Tool mode: {len(tool_info)} tasks with token sets")

    refusals = []
    errors = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(probe, m, tid, prompt, api_key, args.max_tokens, args.timeout,
                         args.answer_mode, tool_info, args.base_url, None, args.allow_remote_ollama)
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
