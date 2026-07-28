#!/usr/bin/env python3
"""Wave bridge: connects AgentDriver backends to wave-flat-dispatch manifest items.

Phase 3: the driver->wave-manifest seam that makes the tier-2 wiring from PR #215
actually driven by a backend's honest self-report.

ARCHITECTURE
------------
Two core functions bridge the gap between AgentDriver backends and the wave's
item manifest:

1. build_manifest_item(driver, item) -> dict
   Given an AgentDriver + a backlog item {slug, ownsFiles, prompt, testCmd, workDir},
   produce the manifest-item dict the wave expects, with verificationTier and model
   baked in from the driver's probe. This makes tier-driven verification tier driven
   by backend capability, not config knobs.

2. dispatch_item(driver, item) -> dict
   Route execution by the driver's capabilities:
   - If worker_filesystem_access is True (Claude/tier-1 harness path): return a
     descriptor indicating the item should go through the normal harness worker
     path (do NOT try to fake a Claude agent here — return {route:'harness', ...}).
   - If False (codex/tier-2 non-agentic): DRIVE it via orchestrator-side contract:
     call driver.dispatch_worker(WorkerRequest(...)), then run the item's test via
     driver.run_command() and decide pass ONLY from exit code 0 (NEVER from the
     model's say-so). Return {route:'driver', ok, testExit, filesWritten}.

HONESTY & FAIL-SAFE
-------------------
Any exception -> a failed result, never a false green. Green only from run_command
exit 0. Ownership is enforced at the driver level (dispatch_worker rejects
out-of-scope paths). The bridge trusts the driver's probe truthfulness — if it
lies about worker_filesystem_access, the routing will be wrong, but the failure
will be loud and caught in CI.

stdlib-only, ASCII-only, Windows + Linux safe.
"""

import json
import sys
from pathlib import Path
from typing import Dict, Optional, Any

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

from agent_driver import (
    AgentDriver,
    WorkerRequest,
    ROLE_WORKER,
)
from verification_policy import verification_policy


def build_manifest_item(driver: AgentDriver, item: Dict[str, Any]) -> Dict[str, Any]:
    """Build a manifest item from a backlog item and an AgentDriver.

    Takes a backlog item {slug, ownsFiles, prompt, testCmd, workDir, ...} and an
    AgentDriver, and produces a manifest-item dict that the wave expects, with
    verificationTier and model baked in from the driver's probed capabilities,
    PLUS all four resolved policy knobs.

    This is the seam that makes tier-driven verification actually driven by the
    backend's honest self-report: a weak backend raises the orchestrator's burden
    via recommended_verification_tier, not config.

    The policy is RESOLVED ONCE here in Python and carried as literal manifest
    fields, so JS cannot recompute/drift. The template consumes these resolved
    values; tier-1/Claude path with no verificationTier must behave exactly as
    before (tier-1 defaults: repairCap=1, requireAdversarialReview=false,
    spotCheckFrac=0.10, validateAllJson=false).

    Args:
        driver: AgentDriver instance (provides probe_capabilities, resolve_model).
        item: dict with at least {slug, ownsFiles, prompt}. May also have
              testCmd, workDir, selfCheckCmd, label, phase, effort.

    Returns:
        dict with all item fields PLUS:
          - model: concrete model id from driver.resolve_model('worker')
          - verificationTier: recommended tier from driver.probe_capabilities()
          - repairCap: resolved repair budget for this tier
          - requireAdversarialReview: resolved adversarial review requirement
          - spotCheckFrac: resolved spot-check fraction for this tier
          - validateAllJson: resolved JSON validation strictness for this tier
        The dict is suitable for passing to wave-flat-dispatch.template.mjs as an
        item in the items[] array. All four policy fields are consumed directly
        by the template; it does NOT recompute them.
    """
    caps = driver.probe_capabilities()
    model = driver.resolve_model(ROLE_WORKER)

    # Resolve all four policy knobs from the backend's verification tier.
    # Wrap to provide driver name in error.
    try:
        policy = verification_policy(caps)
    except ValueError as e:
        raise ValueError(
            f"Unsupported verification tier {caps.recommended_verification_tier} "
            f"from driver '{driver.name}': {e}"
        )

    # Copy the input item and enrich with model, tier, and all four policy knobs.
    # Preserve all original fields including the optional `repo` field.
    result = dict(item)
    result["model"] = model
    result["verificationTier"] = caps.recommended_verification_tier
    result["repairCap"] = policy["repair_cap"]
    result["requireAdversarialReview"] = policy["require_adversarial_review"]
    result["spotCheckFrac"] = policy["spot_check_frac"]
    result["validateAllJson"] = policy["validate_all_json"]

    return result


def dispatch_item(
    driver: AgentDriver, item: Dict[str, Any], workdir: Optional[str] = None
) -> Dict[str, Any]:
    """Route execution of a backlog item through an AgentDriver backend.

    Dispatches a single item through the driver, honoring its capabilities:

    - If driver.probe_capabilities().worker_filesystem_access is True (Claude Code
      tier-1 harness path): return {route:'harness', ...} to indicate the item
      should go through the normal harness worker path. We do NOT attempt to fake
      a Claude agent here — the harness will handle it.

    - If False (Codex/tier-2, non-agentic): DRIVE it via orchestrator-side contract:
      1. Call driver.dispatch_worker(WorkerRequest(...)) with the item's prompt,
         owned files, and workdir. The driver injects file context, returns produced
         files.
      2. Run the item's test via driver.run_command(...) and decide pass ONLY from
         exit code 0 (NEVER from the model's say-so). This is the center verification
         rule: the orchestrator is the ground truth, not the model.
      3. Return a structured result {route:'driver', ok, testExit, filesWritten}.

    FAIL-SAFE: any exception -> a failed result (ok=False), never a false green.
    Ownership is enforced at the driver level (dispatch_worker rejects out-of-scope
    paths wholesale). Green only from run_command exit code 0.

    Args:
        driver: AgentDriver instance.
        item: dict with at least {slug, ownsFiles, prompt, testCmd}. May also have
              workDir, selfCheckCmd, label, phase, effort.
        workdir: optional override for the working directory (defaults to item.workDir
                 or '.').

    Returns:
        dict with the following keys:
          - route: 'harness' or 'driver'
          - ok: bool (only True if driver route AND test passed)
          - testExit: int (exit code from run_command, if route=='driver'; None otherwise)
          - filesWritten: list of str (files written by the driver, if route=='driver'; None if 'harness')
          - error: str (error message, if ok=False; None otherwise)
          - workerId: str (worker id from dispatch_worker, if route=='driver'; None otherwise)
          - verified: bool (True only if test was run and passed; False if not tested or test failed)
          - reason: str (optional; present when item could not be verified, e.g. 'no_test_command')
    """
    try:
        caps = driver.probe_capabilities()
        slug = item.get("slug", "unknown")

        # Determine working directory.
        exec_workdir = workdir or item.get("workdir") or item.get("workDir") or "."

        # Route by driver capability.
        if caps.worker_filesystem_access:
            # Claude Code tier-1 path: harness will handle it.
            return {
                "route": "harness",
                "ok": False,  # Unknown status; harness will set actual result
                "testExit": None,
                "filesWritten": None,
                "error": None,
                "workerId": None,
                "verified": False,  # Not verified by orchestrator (harness will verify)
            }

        # Codex/tier-2 non-agentic path: orchestrator-managed dispatch.
        # Extract fields from item.
        prompt = item.get("prompt", "")
        owned_files = tuple(item.get("ownsFiles", []))
        test_cmd = item.get("testCmd", "")
        model = item.get("model")

        # Dispatch the worker.
        request = WorkerRequest(
            prompt=prompt,
            owned_files=owned_files,
            workdir=exec_workdir,
            model=model,
            role=ROLE_WORKER,
            label=f"dispatch:{slug}",
        )
        result = driver.dispatch_worker(request)

        if not result.ok:
            # Worker dispatch failed (malformed JSON, ownership violation, etc.).
            return {
                "route": "driver",
                "ok": False,
                "testExit": None,
                "filesWritten": None,
                "error": f"dispatch_worker failed: {result.error}",
                "workerId": result.worker_id,
                "verified": False,  # Not verified (dispatch failed)
                "testStdout": "",
                "testStderr": "",
            }

        # Worker succeeded; now run the test command.
        # Green is decided ONLY by exit code 0, never by the model's done:true.
        if not test_cmd:
            # No test command: MUST fail-closed. An unverified item must never
            # report success — that violates aesop's core honesty guarantee:
            # "no test to fail" is NOT the same as "verified correct."
            return {
                "route": "driver",
                "ok": False,
                "testExit": None,
                "filesWritten": list(result.files_written or []),
                "error": "no testCmd: cannot verify, refusing to report success",
                "workerId": result.worker_id,
                "verified": False,  # Not verified (no test to run)
                "reason": "no_test_command",
                "testStdout": "",
                "testStderr": "",
            }

        test_result = driver.run_command(test_cmd, cwd=exec_workdir)

        # Decision: ok=True ONLY if test exit code is 0.
        ok = test_result.exit_code == 0

        return {
            "route": "driver",
            "ok": ok,
            "testExit": test_result.exit_code,
            "filesWritten": list(result.files_written or []),
            "error": None if ok else f"test failed with exit {test_result.exit_code}",
            "workerId": result.worker_id,
            "verified": ok,  # Verified if and only if test passed (exit 0)
            "testStdout": test_result.stdout,
            "testStderr": test_result.stderr,
        }

    except Exception as exc:
        # Catch-all: any unexpected error -> failed result, never a false green.
        return {
            "route": "driver",
            "ok": False,
            "testExit": None,
            "filesWritten": None,
            "error": f"dispatch_item internal error: {exc}",
            "workerId": None,
            "verified": False,  # Not verified (exception during execution)
            "testStdout": "",
            "testStderr": "",
        }
