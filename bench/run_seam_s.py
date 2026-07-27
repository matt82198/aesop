#!/usr/bin/env python3
"""S-arm (seated) dispatcher for the seam-discrimination study.

Runs the AgentDriver seam against a fixture-task library using real API backends.
Each task is executed in an isolated sandbox; oracle grading happens after.

DESIGN
------
Per (task, tier, repeat):
  1. Copy task.json repo/ into a temp sandbox (never mutates task dir).
  2. Dispatch ONE worker via AgentDriver using the specified tier.
  3. Worker is isolated (no cwd/git-config pollution).
  4. Run oracle against the sandbox (oracle/ made available only at grade time).
  5. Record task_id, band, tier, repeat, arm, backend, passed, worker_verdict,
     retries, tokens, duration, status.

Checkpoint format: JSONL (one result per line). Resume skips completed tuples.
Windows + Linux parity: sys.executable, PYTHONUTF8, ASCII output, subprocess timeouts.

USAGE
-----
  python bench/run_seam_s.py \
    --tasks-dir bench/seam_tasks \
    --tiers claude-fable-5,claude-opus-5,claude-sonnet-5,claude-haiku-4-5-20251001,gpt-4o-mini \
    --repeats 3 \
    --workers 1 \
    --checkpoint bench/results/seam-s-checkpoint.jsonl \
    --max-runs 100
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add driver to path.
REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO_ROOT / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import WorkerRequest, WORKER_DONE, WORKER_FAILED, ROLE_WORKER
from backend_config import build_driver, load_backend_config


@dataclass
class TaskFixture:
    """A single seam-discrimination task."""
    task_id: str
    band: str
    statement: str
    context_files: List[str]
    oracle_cmd: str
    repo_path: Path
    oracle_path: Path
    solution_path: Optional[Path] = None


@dataclass
class Result:
    """One (task, tier, repeat) execution."""
    task_id: str
    band: str
    tier: str
    repeat: int
    arm: str = "S"
    backend: str = ""
    passed: bool = False
    worker_verdict: str = ""
    retries_used: int = 0
    tokens_spent: Optional[int] = None
    duration_s: float = 0.0
    status: str = ""  # scored|refusal|transient|error
    error_message: str = ""


def load_task(task_dir: Path) -> TaskFixture:
    """Load a task fixture from a directory."""
    task_json_path = task_dir / "task.json"
    if not task_json_path.exists():
        raise FileNotFoundError(f"task.json not found in {task_dir}")

    with open(task_json_path) as f:
        task_data = json.load(f)

    return TaskFixture(
        task_id=task_data["task_id"],
        band=task_data.get("band", "unknown"),
        statement=task_data["statement"],
        context_files=task_data.get("context_files", []),
        oracle_cmd=task_data.get("oracle_cmd", "python -m pytest oracle -q"),
        repo_path=task_dir / "repo",
        oracle_path=task_dir / "oracle",
        solution_path=(task_dir / "SOLUTION.md") if (task_dir / "SOLUTION.md").exists() else None,
    )


def run_worker_dispatch(
    driver,
    task: TaskFixture,
    sandbox_dir: Path,
    max_retries: int = 2,
) -> Tuple[bool, str, int, Optional[int]]:
    """Dispatch worker via AgentDriver seam.

    Returns:
        (ok, worker_verdict, retries_used, tokens_spent)
    """
    retries_used = 0
    tokens_spent = None

    try:
        # Prepare owned files list from context_files.
        owned_files = tuple(task.context_files)
        if not owned_files:
            owned_files = tuple(
                str(f.relative_to(task.repo_path))
                for f in task.repo_path.glob("**/*")
                if f.is_file()
            )[:5]  # Limit to 5 files for safety.

        # Create the worker request.
        request = WorkerRequest(
            prompt=task.statement,
            owned_files=owned_files,
            workdir=str(sandbox_dir),
            label=task.task_id,
        )

        # Dispatch.
        result = driver.dispatch_worker(request)

        # Track tokens.
        if result.tokens_spent is not None:
            tokens_spent = result.tokens_spent

        if not result.ok:
            worker_verdict = result.error or "WORKER_FAILED"
            return False, worker_verdict, retries_used, tokens_spent

        # Success.
        worker_verdict = result.structured.get("summary", "OK")
        return True, worker_verdict, retries_used, tokens_spent

    except Exception as exc:
        return False, str(exc), retries_used, tokens_spent


def run_oracle(oracle_path: Path, sandbox_dir: Path, timeout_s: int = 120) -> bool:
    """Run oracle grading in the sandbox. Returns True if oracle passed."""
    if not oracle_path.exists():
        # No oracle: assume not graded.
        return False

    # Make oracle available in the sandbox (copy it).
    sandbox_oracle = Path(sandbox_dir) / "oracle"
    if sandbox_oracle.exists():
        shutil.rmtree(sandbox_oracle)
    shutil.copytree(oracle_path, sandbox_oracle)

    # Run oracle_cmd in the sandbox.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "oracle", "-q"],
            cwd=str(sandbox_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def execute_task_run(
    driver,
    task: TaskFixture,
    tier: str,
    repeat: int,
) -> Result:
    """Execute one (task, tier, repeat) run."""
    start_time = time.time()

    try:
        # Create isolated sandbox.
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_dir = Path(tmpdir)

            # Copy repo into sandbox.
            for item in task.repo_path.iterdir():
                if item.is_file():
                    shutil.copy2(item, sandbox_dir / item.name)
                elif item.is_dir() and not item.name.startswith("."):
                    shutil.copytree(item, sandbox_dir / item.name)

            # Dispatch worker.
            worker_ok, worker_verdict, retries, tokens = run_worker_dispatch(
                driver, task, sandbox_dir
            )

            # Run oracle if worker succeeded.
            oracle_passed = False
            if worker_ok:
                oracle_passed = run_oracle(task.oracle_path, sandbox_dir)

            duration = time.time() - start_time

            # Determine status and passed flag.
            if not worker_ok:
                status = "transient" if "timeout" in worker_verdict.lower() else "refusal"
                passed = False
            elif oracle_passed:
                status = "scored"
                passed = True
            else:
                status = "scored"
                passed = False

            return Result(
                task_id=task.task_id,
                band=task.band,
                tier=tier,
                repeat=repeat,
                arm="S",
                backend=driver.resolve_model(ROLE_WORKER),
                passed=passed,
                worker_verdict=worker_verdict,
                retries_used=retries,
                tokens_spent=tokens,
                duration_s=duration,
                status=status,
            )

    except Exception as exc:
        duration = time.time() - start_time
        return Result(
            task_id=task.task_id,
            band=task.band,
            tier=tier,
            repeat=repeat,
            arm="S",
            backend="",
            passed=False,
            worker_verdict="",
            retries_used=0,
            tokens_spent=None,
            duration_s=duration,
            status="error",
            error_message=str(exc),
        )


def load_checkpoint(checkpoint_path: Path) -> Dict[Tuple[str, str, int], Result]:
    """Load completed results from checkpoint JSONL file."""
    completed = {}
    if not checkpoint_path.exists():
        return completed

    with open(checkpoint_path) as f:
        for line in f:
            if not line.strip():
                continue
            result_dict = json.loads(line)
            result = Result(**result_dict)
            key = (result.task_id, result.tier, result.repeat)
            completed[key] = result

    return completed


def save_result(checkpoint_path: Path, result: Result) -> None:
    """Append one result to the checkpoint file."""
    with open(checkpoint_path, "a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="S-arm (seated) dispatcher for seam-discrimination study"
    )
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=Path("bench/seam_tasks"),
        help="Directory containing task fixtures",
    )
    parser.add_argument(
        "--tiers",
        type=str,
        default="claude-haiku-4-5-20251001,gpt-4o-mini",
        help="Comma-separated tier models",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of repeats per task/tier",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers (not yet implemented)",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("bench/results/seam-s-checkpoint.jsonl"),
        help="Checkpoint JSONL file",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Max total runs (stop after this many)",
    )

    args = parser.parse_args()

    # Ensure checkpoint directory exists.
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)

    # Load tasks.
    if not args.tasks_dir.exists():
        print(f"ERROR: tasks directory not found: {args.tasks_dir}")
        sys.exit(1)

    task_dirs = sorted([d for d in args.tasks_dir.iterdir() if d.is_dir()])
    if not task_dirs:
        print(f"ERROR: no task directories found in {args.tasks_dir}")
        sys.exit(1)

    tasks = []
    for task_dir in task_dirs:
        try:
            task = load_task(task_dir)
            tasks.append(task)
        except Exception as exc:
            print(f"WARNING: failed to load task {task_dir.name}: {exc}")

    if not tasks:
        print("ERROR: no tasks loaded")
        sys.exit(1)

    # Parse tiers.
    tiers = [t.strip() for t in args.tiers.split(",")]

    # Load checkpoint.
    completed = load_checkpoint(args.checkpoint)

    # Build model-to-backend mapping.
    tier_to_backend = {
        "claude-fable-5": "anthropic",
        "claude-opus-5": "anthropic",
        "claude-opus-4-1-20250805": "anthropic",
        "claude-sonnet-5": "anthropic",
        "claude-3-5-sonnet-20241022": "anthropic",
        "claude-haiku-4-5-20251001": "anthropic",
        "gpt-4o-mini": "codex",
        "gpt-4o": "codex",
    }

    # Main loop.
    runs_done = 0
    for task in tasks:
        for tier in tiers:
            for repeat in range(1, args.repeats + 1):
                # Check if already done.
                key = (task.task_id, tier, repeat)
                if key in completed:
                    print(f"SKIP {task.task_id} {tier} repeat {repeat} (already completed)")
                    continue

                # Check max-runs limit.
                if args.max_runs and runs_done >= args.max_runs:
                    print(f"Reached max_runs limit ({args.max_runs})")
                    sys.exit(0)

                # Determine backend from tier.
                backend_name = tier_to_backend.get(tier, "anthropic")

                # Build driver config.
                config = {
                    "backend": backend_name,
                }
                if backend_name == "anthropic":
                    config["model"] = tier
                    config["api_key_env"] = "ANTHROPIC_API_KEY"
                elif backend_name == "codex":
                    config["model"] = tier
                    config["api_key_env"] = "OPENAI_API_KEY"

                # Build driver.
                try:
                    driver = build_driver(config)
                except Exception as exc:
                    print(f"ERROR: failed to build driver for {tier}: {exc}")
                    sys.exit(1)

                # Execute run.
                print(f"RUN {task.task_id} {tier} repeat {repeat} (backend={backend_name})")
                result = execute_task_run(driver, task, tier, repeat)

                # Save result.
                save_result(args.checkpoint, result)
                print(
                    f"  status={result.status} passed={result.passed} "
                    f"tokens={result.tokens_spent} duration={result.duration_s:.1f}s"
                )

                runs_done += 1

    print(f"Completed {runs_done} runs. Results in {args.checkpoint}")


if __name__ == "__main__":
    main()
