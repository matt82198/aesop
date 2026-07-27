#!/usr/bin/env python3
"""S-arm (seated) dispatcher for the seam-discrimination study.

Runs the AgentDriver seam against a fixture-task library using real API backends
with REAL BOUNDED REPAIR LOOP (reuses wave_loop.py Phases 4-5 pattern).

DESIGN (REPAIR LOOP FROM wave_loop.py)
--------------------------------------
Per (task, tier, repeat):
  1. Copy task.json repo/ into temp sandbox (never mutates task dir).
  2. Build manifest_item via build_manifest_item (gets repair_cap from policy).
  3. For each repair attempt (up to repair_cap):
     - Create dispatch item (prompt + failure history from previous attempts)
     - Dispatch worker via dispatch_item (NOT direct dispatch_worker!)
     - Visible test runs within dispatch_item (oracle still hidden)
     - On failure: append test output to prompt for next attempt
  4. After final repair attempt, run HIDDEN oracle for grading-only.
  5. Record task_id, band, tier, repeat, arm:"S", backend, passed,
     worker_verdict, retries_used (actual repair attempts), tokens per attempt,
     duration, status.

Checkpoint format: JSONL (one result per line), key = (task_id, tier, repeat, arm).
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

from agent_driver import ROLE_WORKER
from backend_config import build_driver, load_backend_config
from wave_bridge import build_manifest_item, dispatch_item
from verification_policy import verification_policy


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
class AttemptResult:
    """Result from one repair attempt."""
    attempt_num: int
    ok: bool
    test_exit: Optional[int] = None
    files_written: Tuple[str, ...] = field(default_factory=tuple)
    error: str = ""
    tokens_spent: Optional[int] = None


@dataclass
class Result:
    """Final result for one (task, tier, repeat, arm) execution."""
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
    policy_repair_cap: Optional[int] = None  # Driver's recommended cap from verification_policy
    applied_repair_cap: Optional[int] = None  # Explicit cap applied (CLI override)


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


def run_bounded_repair(
    driver,
    task: TaskFixture,
    sandbox_dir: Path,
    repair_cap: int,
) -> Tuple[bool, str, int, Optional[int]]:
    """Run bounded repair loop (from wave_loop.py Phase 5 pattern).

    Returns:
        (ok, worker_verdict, retries_used, tokens_spent)
    """
    # Get owned files from task context_files.
    owned_files = tuple(task.context_files)
    if not owned_files:
        owned_files = tuple(
            str(f.relative_to(task.repo_path))
            for f in task.repo_path.glob("**/*")
            if f.is_file()
        )[:5]

    # Build initial dispatch item (from aesop backlog item).
    dispatch_item_dict = {
        "slug": task.task_id,
        "prompt": task.statement,
        "ownsFiles": list(owned_files),
        "workDir": str(sandbox_dir),
        "testCmd": "python -m pytest . -q",  # Basic test; fixture may override via oracle
        "model": driver.resolve_model(ROLE_WORKER),
    }

    # Build manifest item (gets model, tier, policy knobs).
    try:
        manifest_item = build_manifest_item(driver, dispatch_item_dict)
    except Exception as exc:
        return False, f"build_manifest failed: {exc}", 0, None

    # Bounded repair loop (reuses wave_loop.py Phase 5 logic).
    # repair_cap = number of REPAIRS allowed (not total attempts).
    # Total attempts = 1 (initial) + repair_cap (repairs) = 1 + repair_cap.
    # retries_used = number of actual repairs that happened (0 = first attempt succeeded).
    retries_used = 0
    total_tokens = 0
    failed_item = manifest_item
    last_error = ""
    last_test_exit = None
    total_attempts = 1 + repair_cap

    for attempt in range(total_attempts):
        try:
            # Dispatch the (possibly repaired) item.
            result = dispatch_item(driver, failed_item, workdir=str(sandbox_dir))

            # Track tokens if reported.
            if driver.get_tokens_spent() is not None:
                total_tokens = driver.get_tokens_spent()

            # Check result.
            ok = result.get("ok", False)
            test_exit = result.get("testExit")
            error = result.get("error", "")

            if ok:
                # Test passed! Return success.
                # retries_used = number of repairs that happened before this success.
                return True, error or "Fixed", retries_used, total_tokens

            # Test failed: prepare for next repair attempt.
            last_error = error
            last_test_exit = test_exit

            if attempt < total_attempts - 1:
                # Will do another repair attempt.
                retries_used = attempt + 1  # Increment: we're about to do the (attempt+1)-th repair.

                # Build repair prompt: append test failure to original.
                original_prompt = dispatch_item_dict["prompt"]
                test_output = f"\n\nTest failed with exit code {test_exit}.\n"
                if error:
                    test_output += f"Error: {error}\n"
                repair_prompt = original_prompt + test_output

                # Create repair item for next attempt.
                repair_item = dict(manifest_item)
                repair_item["prompt"] = repair_prompt
                failed_item = repair_item

        except Exception as exc:
            return False, f"dispatch attempt {attempt} failed: {exc}", retries_used, total_tokens

    # All attempts exhausted, last one failed.
    return False, last_error or "Repair attempts exhausted", retries_used, total_tokens


def run_oracle(oracle_path: Path, sandbox_dir: Path, timeout_s: int = 120) -> bool:
    """Run oracle grading in the sandbox. Returns True if oracle passed."""
    if not oracle_path.exists():
        # No oracle: cannot grade.
        return False

    # Make oracle available in sandbox (copy it).
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
    applied_repair_cap: int,
) -> Result:
    """Execute one (task, tier, repeat, arm) run with bounded repair.

    Args:
        applied_repair_cap: explicit repair cap to apply (overrides policy).
    """
    start_time = time.time()

    try:
        # Get policy-recommended cap for recording (but don't use it).
        caps = driver.probe_capabilities()
        policy = verification_policy(caps)
        policy_repair_cap = policy.get("repair_cap", 1)

        # Create isolated sandbox with proper layout.
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox_dir = Path(tmpdir)
            sandbox_repo_dir = sandbox_dir / "repo"
            sandbox_repo_dir.mkdir()

            # Copy repo into sandbox/repo/ (not flat!).
            # Oracle expects to find code at ../repo relative to oracle/ dir.
            for item in task.repo_path.iterdir():
                if item.is_file():
                    shutil.copy2(item, sandbox_repo_dir / item.name)
                elif item.is_dir() and not item.name.startswith("."):
                    shutil.copytree(item, sandbox_repo_dir / item.name)

            # Run bounded repair loop with APPLIED cap (uniform across tiers).
            # Worker edits go into sandbox/repo/, visible tests run from there.
            worker_ok, worker_verdict, retries, tokens = run_bounded_repair(
                driver, task, sandbox_repo_dir, applied_repair_cap
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
                policy_repair_cap=policy_repair_cap,
                applied_repair_cap=applied_repair_cap,
            )

    except Exception as exc:
        duration = time.time() - start_time
        # Try to get policy cap even on error.
        policy_repair_cap = None
        try:
            caps = driver.probe_capabilities()
            policy = verification_policy(caps)
            policy_repair_cap = policy.get("repair_cap", 1)
        except Exception:
            pass

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
            policy_repair_cap=policy_repair_cap,
            applied_repair_cap=applied_repair_cap,
        )


def load_checkpoint(checkpoint_path: Path) -> Dict[Tuple[str, str, int, str], Result]:
    """Load completed results from checkpoint JSONL file.

    Key is (task_id, tier, repeat, arm).
    """
    completed = {}
    if not checkpoint_path.exists():
        return completed

    with open(checkpoint_path) as f:
        for line in f:
            if not line.strip():
                continue
            result_dict = json.loads(line)
            result = Result(**result_dict)
            key = (result.task_id, result.tier, result.repeat, result.arm)
            completed[key] = result

    return completed


def save_result(checkpoint_path: Path, result: Result) -> None:
    """Append one result to the checkpoint file."""
    with open(checkpoint_path, "a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="S-arm (seated) dispatcher for seam-discrimination study (with real repair loop)"
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
        help="Checkpoint JSONL file (key includes arm)",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Max total runs (stop after this many)",
    )
    parser.add_argument(
        "--repair-cap",
        type=int,
        default=2,
        help="Repair budget (number of repairs, not total attempts). "
             "Total attempts = 1 (initial) + repair_cap. Default: 2 (3 total attempts max).",
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
                # Check if already done (key now includes arm).
                key = (task.task_id, tier, repeat, "S")
                if key in completed:
                    print(f"SKIP {task.task_id} {tier} repeat {repeat} arm=S (already completed)")
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

                # Use UNIFORM repair cap from CLI args (overrides per-tier policy).
                # This ensures fair comparison across tiers: same treatment except for backend.
                applied_repair_cap = args.repair_cap

                # Execute run (with REAL bounded repair loop, uniform cap).
                print(
                    f"RUN {task.task_id} {tier} repeat {repeat} arm=S "
                    f"(backend={backend_name}, applied_repair_cap={applied_repair_cap})"
                )
                result = execute_task_run(driver, task, tier, repeat, applied_repair_cap)

                # Save result.
                save_result(args.checkpoint, result)
                print(
                    f"  status={result.status} passed={result.passed} "
                    f"retries={result.retries_used} tokens={result.tokens_spent} "
                    f"policy_cap={result.policy_repair_cap} applied_cap={result.applied_repair_cap} "
                    f"duration={result.duration_s:.1f}s"
                )

                runs_done += 1

    print(f"Completed {runs_done} runs. Results in {args.checkpoint}")


if __name__ == "__main__":
    main()
