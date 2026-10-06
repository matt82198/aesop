#!/usr/bin/env python3
"""Falsifiable no-double-grant proof for the shared-filesystem claim log (multibox Inc 6).
INDEX: Falsifiable no-double-grant proof for the shared-FS claim log (multibox Inc 6); drives `tests/multibox_sim.py` (simulated NFS/SMB visibility at the `os.listdir` boundary, virtual clock, seeded jitter) against `state_store/fs_claim_log.py` as shipped; 4 proofs, all must hold: (1) 200 seeded rounds with delay <= settle -> ZERO double grants, (2) **falsifiability** — the SAME harness with delay > settle DOES double-grant (a safety property that cannot fail proves nothing; without this the gate would pass on a harness that never contends), (3) at least half the sweep rounds must actually contend, so proof 1 is not vacuous, (4) undecorated-tmpdir smoke with a real 0.05s settle. Hermetic (tempdirs, no network, no sleeping outside proof 4); CLI: `--check` (default) | `--json` | `--runs N` | `--help`; ~3.5s; exit 0=proof holds, 1=double grant OR unfalsifiable harness, 2=usage

The CI gate for `state_store/fs_claim_log.py`'s central safety claim: under a
bounded directory-visibility delay, two instances never both get the same path.

It is a *proof*, not a test wrapper, because it runs both halves of a matched
pair and fails if either half misbehaves:

  PROOF 1  delay <= settle, 200 seeded randomized rounds -> ZERO double grants.
  PROOF 2  delay >  settle, same harness, same code path -> a double grant IS
           observed.

Proof 2 is the load-bearing one. A safety property that cannot be made to fail
proves nothing about the mechanism it claims to test: "no double grant" is
trivially satisfied by a harness that never creates contention, or one whose
simulated share has no delay in it at all. By demanding that the harness DOES
break once the settle window is too small, this gate keeps proof 1 meaningful --
and turns Inc 0's measurement of the real p99 visibility delay into an enforced
precondition rather than a documented hope.

Two supporting checks close the remaining holes:

  PROOF 3  the sweep really contends (most rounds produce a refused claim), so a
           green proof 1 cannot come from instances that never met.
  PROOF 4  the same protocol still grants exactly once on an UNDECORATED tmpdir
           with a real 0.05s settle window, so the simulator has not quietly
           become the thing under test.

Hermetic: tempdirs only, no network, no cwd pollution, and no real sleeping
outside proof 4's settle window (every other clock in here is virtual).

Usage:
  python tools/verify_multibox.py            # run the proof (default --check)
  python tools/verify_multibox.py --check    # same, explicit
  python tools/verify_multibox.py --json     # machine-readable result
  python tools/verify_multibox.py --runs 50  # smaller sweep for a local loop
  python tools/verify_multibox.py --help

Exit codes:
  0: every proof passed
  1: a proof failed (double grant, or an unfalsifiable harness)
  2: usage/argument error
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
for _path in (str(REPO_ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import multibox_sim as sim  # noqa: E402
from state_store.claim_backend import ClaimConflict  # noqa: E402
from state_store.fs_claim_log import FsClaimLog  # noqa: E402

#: Sweep size mandated by the Inc 6 spec. Virtual time throughout, so the cost is
#: file I/O only -- a few seconds, no sleeping.
DEFAULT_RUNS = 200
#: Settle window used by both halves of the pair. Virtual seconds.
SETTLE = 1.0
#: Real settle window for the undecorated filesystem smoke, in real seconds.
REAL_SETTLE = 0.05
#: Minimum fraction of sweep rounds that must actually contend for proof 1 to
#: mean anything.
MIN_CONTENTION_RATE = 0.5


def proof_no_double_grant(tmp: str, runs: int) -> tuple:
    """PROOF 1: ``delay <= settle`` never grants one path to two instances.

    Returns:
        ``(proof_dict, raw_sweep_report)`` -- the raw report is handed on to
        :func:`proof_contention` so the sweep runs once, not twice.
    """
    report = sim.sweep_no_double_grant(
        lambda index: os.path.join(tmp, "sweep", "run%04d" % index, "claims"),
        runs=runs, settle=SETTLE,
    )
    return {
        "name": "no_double_grant",
        "ok": not report["violations"],
        "runs": report["runs"],
        "grants": report["grants"],
        "contended_runs": report["contended_runs"],
        "violations": [[seed, bad] for seed, bad in report["violations"][:5]],
        "detail": "%d seeded rounds, delay <= settle, %d grants, %d violations"
                  % (report["runs"], report["grants"], len(report["violations"])),
    }, report


def proof_falsifiability(tmp: str, seeds: int = 10) -> dict:
    """PROOF 2: ``delay > settle`` DOES double-grant, in the same harness."""
    observed = 0
    example = None
    for seed in range(seeds):
        outcomes = sim.run_claim_round(
            os.path.join(tmp, "falsify", "s%02d" % seed),
            sim.blind_pair(settle=SETTLE, delay=SETTLE * 3),
            settle=SETTLE, seed=seed,
        )
        bad = sim.find_double_grants(outcomes)
        if bad:
            observed += 1
            example = example or bad
    return {
        "name": "falsifiability",
        "ok": observed == seeds,
        "seeds": seeds,
        "double_grants_observed": observed,
        "example": example,
        "detail": "delay > settle double-granted in %d/%d seeded rounds"
                  % (observed, seeds),
    }


def proof_contention(report: dict) -> dict:
    """PROOF 3: the sweep is not vacuous -- rounds really do fight."""
    runs = max(1, report["runs"])
    rate = report["contended_runs"] / runs
    return {
        "name": "contention",
        "ok": rate >= MIN_CONTENTION_RATE,
        "contention_rate": round(rate, 4),
        "minimum": MIN_CONTENTION_RATE,
        "detail": "%d/%d sweep rounds produced a refused claim"
                  % (report["contended_runs"], runs),
    }


def proof_real_filesystem(tmp: str) -> dict:
    """PROOF 4: undecorated tmpdir, real clock, real settle -- one winner."""
    claims_dir = os.path.join(tmp, "realfs", "claims")
    results: dict = {}
    barrier = threading.Barrier(2, timeout=30)

    def contend(name: str) -> None:
        """One real instance: wait on the barrier, then claim the shared path."""
        backend = FsClaimLog(claims_dir, settle_seconds=REAL_SETTLE)
        try:
            barrier.wait()
            results[name] = backend.claim(["src/real.py"], name, 300.0)
        except ClaimConflict:
            results[name] = None
        except Exception as exc:  # fail-closed
            results[name] = "ERROR:%r" % (exc,)

    threads = [threading.Thread(target=contend, args=("boxR%d:%d:sim" % (i, i),))
               for i in (1, 2)]
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    elapsed = time.time() - started

    granted = [name for name, lease in results.items()
               if lease and not str(lease).startswith("ERROR:")]
    return {
        "name": "real_filesystem",
        "ok": len(results) == 2 and len(granted) == 1 and elapsed >= REAL_SETTLE,
        "granted": granted,
        "elapsed_seconds": round(elapsed, 3),
        "detail": "%d/2 instances granted on a real tmpdir in %.3fs"
                  % (len(granted), elapsed),
    }


def run_proofs(runs: int = DEFAULT_RUNS) -> dict:
    """Run every proof against a private tempdir and summarize.

    Args:
        runs: sweep size for proof 1.

    Returns:
        ``{ok, elapsed_seconds, proofs: [...]}``.
    """
    started = time.time()
    tmp = tempfile.mkdtemp(prefix="verify-multibox-")
    try:
        sweep, report = proof_no_double_grant(tmp, runs)
        proofs = [
            sweep,
            proof_falsifiability(tmp),
            proof_contention(report),
            proof_real_filesystem(tmp),
        ]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {
        "ok": all(proof["ok"] for proof in proofs),
        "elapsed_seconds": round(time.time() - started, 2),
        "proofs": proofs,
    }


def render(result: dict) -> str:
    """Human-readable report of a :func:`run_proofs` result."""
    lines = ["multibox Inc 6 -- simulated-multibox claim-log proof", ""]
    for proof in result["proofs"]:
        lines.append("  [%s] %-18s %s"
                     % ("PASS" if proof["ok"] else "FAIL",
                        proof["name"], proof["detail"]))
        if not proof["ok"] and proof.get("violations"):
            lines.append("         violations: %r" % (proof["violations"],))
    lines.append("")
    lines.append("%s in %.2fs"
                 % ("PROOF HOLDS" if result["ok"] else "PROOF FAILED",
                    result["elapsed_seconds"]))
    if not result["ok"]:
        lines.append("A failing falsifiability proof means the harness can no "
                     "longer detect a double grant; treat it as severely as a "
                     "failing safety proof.")
    return "\n".join(lines)


def main(argv=None) -> int:
    """CLI entry point.

    Returns:
        0 when every proof holds, 1 when any fails.
    """
    parser = argparse.ArgumentParser(
        description="Falsifiable no-double-grant proof for the multibox claim log.",
    )
    parser.add_argument("--check", action="store_true",
                        help="run the proof (default behaviour)")
    parser.add_argument("--json", action="store_true",
                        help="emit the result as JSON instead of text")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                        help="sweep size for proof 1 (default: %d)" % DEFAULT_RUNS)
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be positive")

    result = run_proofs(runs=args.runs)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
