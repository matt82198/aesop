#!/usr/bin/env python3
"""
INDEX: Hot-swap trial runner driving the aesop worker seat with a Google Gemini model (--config/--result/--max-attempts; key read by env var name only; exit 0/1/2)

Hot-swap trial runner: drive the aesop worker seat with a Google Gemini model.

Reads a config file whose ``seats.worker`` block selects the openai-compatible
backend pointed at Google's OpenAI-compatible endpoint. For each trial task it
asks the model for a module that satisfies a test file, writes the module,
runs the task's test command, and records pass/fail, attempts and tokens.

Safety properties:
  * The API key is read only from the environment variable named by the seat's
    ``api_key_env``; its value is never printed, logged, or written to results.
  * If that variable is unset the runner prints the variable NAME and exits 2
    before any network call.
  * The seat is validated through ``backend_config.build_driver`` (SSRF and
    api_key_env allowlist checks) before anything runs.

Usage:
  python tools/google_seat_trial.py --config tools/google_seat_trial.example.json
  python tools/google_seat_trial.py --config my.json --result out/result.json

Exit codes: 0 all tasks passed, 1 one or more tasks failed or config invalid,
2 the seat's api_key_env is not set.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO_ROOT / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from backend_config import build_driver  # noqa: E402
from openai_compatible_driver import make_openai_compatible_transport  # noqa: E402

DEFAULT_MAX_ATTEMPTS = 3
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_KEY_UNSET = 2


def _load_config(path: Path) -> dict:
    """Load and minimally shape-check the trial config (no secrets expected)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config root must be a JSON object")
    return data


def _build_prompt(slug: str, test_path: Path) -> str:
    """Ask for the full module that makes the given test file pass."""
    test_src = test_path.read_text(encoding="utf-8")
    return (
        f"Write the complete contents of the Python module for the aesop item "
        f"'{slug}'. The module file is currently empty. The test file below is "
        "the full specification: make every test in it pass. Do not modify the "
        "tests. Return the complete module file content only.\n\n"
        f"TEST FILE:\n```python\n{test_src}\n```\n"
    )


def _strip_fence(text: str) -> str:
    """Remove a single leading/trailing markdown code fence if present."""
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines) + "\n"


def _run_task(task: dict, transport, model: str, max_attempts: int) -> dict:
    """Run one trial task with up to max_attempts model calls."""
    slug = str(task["slug"])
    test_path = REPO_ROOT / str(task["test_path"])
    module_path = REPO_ROOT / str(task["module_path"])
    test_cmd = str(task["test_cmd"])
    prompt = _build_prompt(slug, test_path)

    result = {
        "slug": slug,
        "passed": False,
        "attempts": 0,
        "tokens": 0,
        "test_last_line": "",
        "error": None,
    }
    for attempt in range(1, max_attempts + 1):
        result["attempts"] = attempt
        try:
            response = transport(
                {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
        except RuntimeError as exc:
            result["error"] = f"model call failed: {exc}"
            continue
        usage = response.get("usage") or {}
        result["tokens"] += int(usage.get("total_tokens") or 0)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            result["error"] = "response missing choices[0].message.content"
            continue
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(_strip_fence(content or ""), encoding="utf-8")
        proc = subprocess.run(
            test_cmd, shell=True, cwd=REPO_ROOT, capture_output=True,
            text=True, timeout=300,
        )
        lines = (proc.stdout + proc.stderr).strip().splitlines()
        result["test_last_line"] = lines[-1] if lines else ""
        if proc.returncode == 0:
            result["passed"] = True
            result["error"] = None
            break
        result["error"] = f"tests exit {proc.returncode}"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the aesop worker-seat trial against a Google Gemini model."
    )
    parser.add_argument("--config", required=True, type=Path,
                        help="Trial config JSON (seats.worker + trial.tasks)")
    parser.add_argument("--result", type=Path,
                        default=Path("google_seat_trial_result.json"),
                        help="Where to write the JSON result file")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                        help="Model attempts per task (default 3)")
    args = parser.parse_args(argv)

    try:
        config = _load_config(args.config)
        driver = build_driver(config)  # validates seat (SSRF, key allowlist)
    except (OSError, ValueError, TypeError) as exc:
        print(f"ERROR: invalid trial config: {exc}", file=sys.stderr)
        return EXIT_FAIL

    seat = config["seats"]["worker"]
    key_env = seat.get("api_key_env", "")
    if not os.environ.get(key_env):
        # Name only; the value is never read into output.
        print(
            f"ERROR: environment variable {key_env} is not set. Set it in your "
            "shell and re-run. (No key value is read or printed.)",
            file=sys.stderr,
        )
        return EXIT_KEY_UNSET

    transport = make_openai_compatible_transport(
        base_url=seat["base_url"],
        api_key_env=key_env,
        timeout_s=float(seat.get("timeout_s", 120.0)),
        is_local=bool(seat.get("is_local", False)),
    )
    model = driver.resolve_model("worker")
    tasks = config.get("trial", {}).get("tasks", [])
    if not tasks:
        print("ERROR: config has no trial.tasks", file=sys.stderr)
        return EXIT_FAIL

    started = time.time()
    results = [_run_task(t, transport, model, args.max_attempts) for t in tasks]
    summary = {
        "model": model,
        "base_url": seat["base_url"],
        "api_key_env": key_env,
        "started_unix": int(started),
        "elapsed_s": round(time.time() - started, 2),
        "tasks_total": len(results),
        "tasks_passed": sum(1 for r in results if r["passed"]),
        "total_tokens": sum(r["tokens"] for r in results),
        "tasks": results,
    }
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.result}: {summary['tasks_passed']}/{summary['tasks_total']} passed")
    return EXIT_OK if summary["tasks_passed"] == summary["tasks_total"] else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
