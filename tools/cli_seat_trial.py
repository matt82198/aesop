#!/usr/bin/env python3
"""Real trial: run aesop items through a headless CLI worker seat and record per-task gate results.
INDEX: CLI worker seat trial runner: builds throwaway task repos under --runs-dir, invokes driver.wave_loop.run_wave with backend 'cli' from --config, re-runs tests independently, and writes JSON results to --out; stdlib-only

For each selected item the trial builds a fresh git repo under --runs-dir, copies
the full --tasks-dir tree (minus VCS, caches and run output, as the local trial's
worktree was used whole), commits the target module EMPTY, and runs
driver.wave_loop.run_wave with the worker seat built from --config (backend "cli").
The test command is re-run independently afterwards, and per-task results are
written to --out as JSON. The same pass criteria as local-trial/run_trial.py apply:
a task passes when the final test command exits 0; first-attempt pass means one
dispatch and exit 0. repair_cap is forced to 3, as in the local trial.

Nothing is committed, pushed or opened as a PR by this script. Run repos are
disposable and live under --runs-dir (default: local-trial-agy/runs).

Usage:
  python tools/cli_seat_trial.py --config examples/agy-cli-seat.json \
      --tasks-dir . --out local-trial-agy/results/agy.json --limit 3 --offset 0
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "driver"))

import backend_config  # noqa: E402
import verification_policy as vp_mod  # noqa: E402
import wave_bridge  # noqa: E402
import wave_loop  # noqa: E402

REPAIR_CAP = 3
TEST_TIMEOUT_S = 300.0
DEFAULT_RUNS_DIR = REPO_ROOT / "local-trial-agy" / "runs"
TASK_SLUGS = (
    "commit_lint",
    "file_size_lint",
    "gate_stub_list",
    "reconcile_log",
    "state_root",
)
_PYTEST_COUNT = re.compile(r"(\d+) (passed|failed|error|errors)")


def forced_policy(caps) -> dict:
    """Production tier-3 policy with repair_cap forced to REPAIR_CAP (trial spec)."""
    policy = dict(vp_mod.verification_policy(caps))
    policy["repair_cap"] = REPAIR_CAP
    return policy


def build_prompt(slug: str, test_rel: str, test_src: str) -> str:
    """Same prompt shape as local-trial/run_trial.py so results stay comparable."""
    return (
        f"Write the complete contents of the Python module for the aesop item '{slug}'. "
        "The module file is currently empty. The test file below is the full specification: "
        "make every test in it pass. Do not modify the tests. Return the complete module "
        "file content only.\n\n"
        f"TEST FILE {test_rel}:\n```python\n{test_src}\n```\n"
    )


def parse_counts(text: str) -> Tuple[int, int]:
    """Return (passed, total) from the last pytest summary line found in text."""
    passed = failed = 0
    for num, kind in _PYTEST_COUNT.findall(text):
        if kind == "passed":
            passed = int(num)
        else:
            failed += int(num)
    return passed, passed + failed


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


_SNAPSHOT_SKIP_NAMES = frozenset({".git", "__pycache__", ".pytest_cache", "local-trial-agy", "runs"})


def _snapshot_ignorer(tasks_dir: Path, runs_dir: Path):
    """copytree ignore hook: skip VCS, caches, the trial output tree and the runs dir itself."""
    excluded = {runs_dir.resolve()}

    def ignore(directory: str, names: List[str]) -> set:
        skipped = set()
        for name in names:
            if name in _SNAPSHOT_SKIP_NAMES:
                skipped.add(name)
            elif (Path(directory) / name).resolve() in excluded:
                skipped.add(name)
        return skipped

    return ignore


def make_task_repo(tasks_dir: Path, runs_dir: Path, slug: str, rel: str) -> Path:
    """Fresh git repo: copy the full tasks tree (as run_trial.py's worktree), commit the target empty."""
    repo = runs_dir / slug
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(tasks_dir, repo, ignore=_snapshot_ignorer(tasks_dir, runs_dir))
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "trial@example.invalid")
    _git(repo, "config", "user.name", "trial")
    (repo / rel).write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed: empty target module")
    return repo


def _last_line(text: str) -> str:
    """Last non-blank line of text, or ''."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def failure_reason(exc_repr: Optional[str], wave_result: Optional[dict], out_text: str) -> str:
    """Explain a failed task: exception first, then the wave's item error, then the final test output."""
    if exc_repr:
        return exc_repr
    for item in (wave_result or {}).get("built") or []:
        if item.get("error"):
            tail = _last_line(out_text)
            return f"{item['error']}; {tail}" if tail else str(item["error"])
    return _last_line(out_text) or "test failed (no output)"


def model_label(argv: List[str]) -> str:
    """Return the value that follows --model in argv, or '' when absent."""
    for i, part in enumerate(argv[:-1]):
        if part == "--model":
            return argv[i + 1]
    return ""


def run_task(
    config: dict,
    tasks_dir: Path,
    runs_dir: Path,
    slug: str,
) -> dict:
    """Run one item through run_wave and return its per-task result record."""
    rel = f"tools/{slug}.py"
    test_rel = f"tests/test_{slug}.py"
    test_cmd = f"python -m pytest {test_rel} -q -p no:cacheprovider"
    repo = make_task_repo(tasks_dir, runs_dir, slug, rel)
    test_src = (repo / test_rel).read_text(encoding="utf-8")
    item = {
        "slug": slug,
        "ownsFiles": [rel],
        "prompt": build_prompt(slug, test_rel, test_src),
        "testCmd": test_cmd,
        "workDir": str(repo),
        "label": slug,
    }

    driver = backend_config.build_driver(config)
    attempts: List[dict] = []
    real_dispatch = driver.dispatch_worker

    def counting_dispatch(request):
        result = real_dispatch(request)
        attempts.append(
            {"ok": bool(result.ok), "error": result.error, "files": list(result.files_written)}
        )
        return result

    driver.dispatch_worker = counting_dispatch

    t0 = time.time()
    error: Optional[str] = None
    wave_result = None
    try:
        wave_result = wave_loop.run_wave(driver, {"items": [item]}, state_dir=None, git=None)
    except Exception as exc:  # recorded, never swallowed silently
        error = repr(exc)
    wall = round(time.time() - t0, 1)

    final = subprocess.run(
        test_cmd,
        shell=True,
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TEST_TIMEOUT_S,
    )
    out_text = (final.stdout or "") + (final.stderr or "")
    passed, total = parse_counts(out_text)
    module_text = (repo / rel).read_text(encoding="utf-8")
    (runs_dir / f"{slug}.module.py").write_text(module_text, encoding="utf-8")

    attempts_used = len(attempts)
    wave_dict = wave_result if isinstance(wave_result, dict) else None
    reason = None
    if final.returncode != 0:
        reason = failure_reason(error, wave_dict, out_text)
    return {
        "model": model_label(list(config.get("argv", []))),
        "slug": slug,
        "passed_first_attempt": final.returncode == 0 and attempts_used == 1,
        "passed": final.returncode == 0,
        "attempts_used": attempts_used,
        "checks_passed": passed,
        "checks_total": total,
        "wall_s": wall,
        "final_test_rc": final.returncode,
        "error": reason,
        "attempts": attempts,
        "wave_item": wave_dict,
    }


def select(slugs: Tuple[str, ...], offset: int, limit: Optional[int]) -> List[str]:
    """Apply --offset then --limit to the sorted slug list."""
    ordered = sorted(slugs)[offset:]
    return ordered if limit is None else ordered[:limit]


def main(argv: Optional[List[str]] = None) -> int:
    """Parse args, run the selected tasks, and write the results JSON."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True, type=Path, help="backend JSON (backend 'cli')")
    ap.add_argument("--tasks-dir", type=Path, default=REPO_ROOT, help="repo with tools/ and tests/")
    ap.add_argument("--out", required=True, type=Path, help="results JSON path")
    ap.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR, help="disposable run repos")
    ap.add_argument("--limit", type=int, default=None, help="run at most N tasks")
    ap.add_argument("--offset", type=int, default=0, help="skip the first N sorted tasks")
    args = ap.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    wave_loop.verification_policy = forced_policy
    wave_bridge.verification_policy = forced_policy

    slugs = select(TASK_SLUGS, args.offset, args.limit)
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    results: List[Dict] = []
    for slug in slugs:
        results.append(run_task(config, args.tasks_dir, args.runs_dir, slug))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"results": results}, indent=2, default=str), encoding="utf-8")
    for r in results:
        print(
            f"{r['slug']}: passed={r['passed']} first={r['passed_first_attempt']} "
            f"attempts={r['attempts_used']} checks={r['checks_passed']}/{r['checks_total']} "
            f"wall_s={r['wall_s']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
