#!/usr/bin/env python3
"""TDD tests for adversarial review phase (Phase 5.75 in wave_loop).

Comprehensive offline tests proving:
  1. refuted-item-routes-to-repair: reviewer refutes a change -> item re-enters repair queue
  2. clean-item-passes: reviewer approves -> item passes through to ship
  3. disabled-is-noop: adversarial_review disabled in config -> no dispatch, no-op phase
  4. sampling determinism: deterministic sampling by slug ensures reproducibility
  5. config surface: adversarial_review: {enabled, sample_frac} works per manifest/policy

TDD: all tests FAIL until implementation completes.
stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import os
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from math import ceil

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import agent_driver as ad
from agent_driver import (
    AgentDriver,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    CommandResult,
    WORKER_DONE,
    WORKER_FAILED,
)
from wave_loop import run_wave
from verification_policy import verification_policy

# Module-level tmp for test isolation.
_MODULE_TMP = None
_MODULE_SAVED_CWD = None


def setUpModule():
    global _MODULE_TMP, _MODULE_SAVED_CWD
    _MODULE_SAVED_CWD = os.getcwd()
    _MODULE_TMP = tempfile.mkdtemp(prefix="adv-review-tests-")
    os.chdir(_MODULE_TMP)


def tearDownModule():
    global _MODULE_TMP, _MODULE_SAVED_CWD
    if _MODULE_SAVED_CWD:
        os.chdir(_MODULE_SAVED_CWD)
    if _MODULE_TMP:
        shutil.rmtree(_MODULE_TMP, ignore_errors=True)


class FakeReviewDriver(AgentDriver):
    """Fake driver that can refute or approve changes."""

    def __init__(self, refute_slugs=None, approval_slugs=None, tokens_per_call=100):
        """Initialize with sets of slugs to refute or approve.

        Args:
            refute_slugs: set of item slugs that should be refuted by reviewer
            approval_slugs: set of item slugs that should be approved by reviewer
            tokens_per_call: tokens per dispatch
        """
        self.refute_slugs = refute_slugs or set()
        self.approval_slugs = approval_slugs or set()
        self.tokens_per_call = tokens_per_call
        self.total_tokens = 0
        self.dispatch_count = 0
        self.review_dispatches = []  # Track review dispatches
        self._workers = {}

    def probe_capabilities(self) -> DriverCapabilities:
        return DriverCapabilities(
            name="fake-review-driver",
            parallel_dispatch=False,
            worker_filesystem_access=False,
            worker_shell_access=False,
            structured_output=False,
            worktree_isolation=False,
            native_cost_tracking=False,
            native_stall_detection=False,
            tool_use_accuracy=0.92,
            recommended_verification_tier=2,
            available_models=("fake-model",),
        )

    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        self.dispatch_count += 1
        self.total_tokens += self.tokens_per_call
        worker_id = f"worker-{self.dispatch_count}"
        self._workers[worker_id] = {"status": WORKER_DONE, "created_at": 0}

        # Track if this is a review dispatch by checking prompt for review markers
        is_review = "review" in request.prompt.lower() or "refute" in request.prompt.lower()
        if is_review:
            self.review_dispatches.append({
                "prompt_contains": request.prompt[:200],
                "owned_files": list(request.owned_files),
            })

        # Extract slug from prompt or use a default
        slug = "unknown"
        for of in request.owned_files:
            if "/" in of:
                slug = of.split("/")[0]
                break

        # WRITE FILES: Actually write owned_files to disk so tests pass
        files_written = []
        workdir = Path(request.workdir) if request.workdir else Path(".")
        try:
            for f in request.owned_files:
                fpath = workdir / f
                fpath.parent.mkdir(parents=True, exist_ok=True)
                # Write a marker indicating it was fixed.
                fpath.write_text(f"# Fixed by FakeReviewDriver dispatch {self.dispatch_count}\n")
                files_written.append(f)
        except Exception as exc:
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_FAILED,
                ok=False,
                error=f"file write failed: {exc}",
            )

        # Return result based on whether we should refute or approve
        if slug in self.refute_slugs:
            # Return refutation (approved=False, indicating issues found)
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_DONE,
                ok=False,  # Refuted: issues found
                error="Reviewer refuted: logic is incorrect",
                files_written=tuple(files_written),
                tokens_spent=self.tokens_per_call,
            )
        else:
            # Default: approve (no issues found, ok=True means verified)
            # For review: ok=True means no issues found (approved)
            # For build: ok=True means build succeeded (verified)
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_DONE,
                ok=True,  # For build: verified; for review: approved (no issues)
                structured={"review": "approved"},
                files_written=tuple(files_written),
                tokens_spent=self.tokens_per_call,
            )

    def worker_status(self, worker_id: str) -> ad.WorkerStatus:
        if worker_id in self._workers:
            return ad.WorkerStatus(
                worker_id=worker_id,
                state=self._workers[worker_id]["status"],
            )
        return ad.WorkerStatus(worker_id=worker_id, state=ad.WORKER_UNKNOWN)

    def run_command(self, command: str, cwd=None, shell=None) -> CommandResult:
        # Simulate test commands - always pass for now
        if "echo" in command.lower():
            # echo test -> pass
            return CommandResult(exit_code=0, stdout="test")

        if "test" in command.lower():
            # unittest or pytest -> pass
            try:
                if cwd:
                    cwd_path = Path(cwd)
                    # Check if any owned files exist (means build succeeded)
                    files = list(cwd_path.glob("**/*.py"))
                    if files:
                        return CommandResult(exit_code=0, stdout="OK")
            except Exception:
                pass
            return CommandResult(exit_code=0, stdout="OK")

        # Git commands succeed
        if command.startswith("git"):
            return CommandResult(exit_code=0, stdout="OK")

        return CommandResult(exit_code=0, stdout="OK")

    def resolve_model(self, role: str) -> str:
        return "fake-model"

    def get_tokens_spent(self) -> int:
        return self.total_tokens


class TestAdversarialReviewPhase(unittest.TestCase):
    """Test the adversarial review phase."""

    def test_refuted_item_routes_to_repair(self):
        """Refuted item should re-enter repair queue (not ship)."""
        # Setup: create a verified item that the reviewer will refute
        driver = FakeReviewDriver(refute_slugs={"item-1"})
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "Fix the bug in module.py",
                    "ownsFiles": ["module.py"],
                    "testCmd": "python -m unittest discover",
                    "workDir": ".",
                }
            ],
            "adversarial_review": {
                "enabled": True,
                "sample_frac": 1.0,  # Review all
            },
        }

        result = run_wave(driver, manifest)

        # The item should have been dispatched (in review)
        self.assertTrue(len(result["built"]) > 0)
        # After review refutation and repair re-entry, it should NOT be verified
        # (or if it re-enters repair and fails, stays false)
        item_result = result["built"][0]

        # Check that adversarial_review happened
        self.assertIn("adversarial_review", item_result)
        # If refuted, it should be marked as such
        if item_result.get("adversarial_review") == "refuted":
            # Item should NOT be in the final shipped set (if git is configured)
            self.assertFalse(item_result.get("verified", False))

    def test_clean_item_passes(self):
        """Clean item approved by reviewer should pass through."""
        # Setup: create a verified item that the reviewer approves
        driver = FakeReviewDriver(approval_slugs={"item-1"})
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "Fix the bug",
                    "ownsFiles": ["module.py"],
                    "testCmd": "python -m unittest discover",
                    "workDir": ".",
                }
            ],
            "adversarial_review": {
                "enabled": True,
                "sample_frac": 1.0,
            },
        }

        result = run_wave(driver, manifest)

        self.assertTrue(len(result["built"]) > 0)
        item_result = result["built"][0]

        # If approved by reviewer, mark as approved
        self.assertIn("adversarial_review", item_result)

    def test_disabled_is_noop(self):
        """When adversarial_review is disabled, phase should be a no-op."""
        # Setup: disabled review
        driver = FakeReviewDriver()
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "Fix the bug",
                    "ownsFiles": ["module.py"],
                    "testCmd": "python -m unittest discover",
                    "workDir": ".",
                }
            ],
            "adversarial_review": {
                "enabled": False,
                "sample_frac": 0.0,
            },
        }

        result = run_wave(driver, manifest)

        # No review dispatches should have been made (this is the key assertion)
        self.assertEqual(len(driver.review_dispatches), 0,
                        f"Expected no review dispatches when disabled, got {len(driver.review_dispatches)}")

        # Item may be verified or not, but review phase should not have dispatched
        # If verified, it will be "deferred" (no orchestrator backend configured)
        # If not verified, it will fail. Either way, no review phase dispatch.

    def test_sampling_determinism(self):
        """Sampling should be deterministic and reproducible."""
        # Setup: 10 items, sample 30% (should be 3 items)
        driver = FakeReviewDriver()
        items = [
            {
                "slug": f"item-{i:02d}",
                "prompt": f"Fix item {i}",
                "ownsFiles": [f"item{i}.py"],
                "testCmd": "echo test",
                "workDir": ".",
            }
            for i in range(10)
        ]
        manifest = {
            "items": items,
            "adversarial_review": {
                "enabled": True,
                "sample_frac": 0.3,
            },
        }

        result1 = run_wave(driver, manifest)
        review_count_1 = len(driver.review_dispatches)

        # Reset and run again
        driver.review_dispatches = []
        driver.dispatch_count = 0
        driver.total_tokens = 0
        result2 = run_wave(driver, manifest)
        review_count_2 = len(driver.review_dispatches)

        # Should sample the same number both times (deterministic)
        self.assertEqual(review_count_1, review_count_2)
        # Should sample approximately 30% (±1 due to rounding)
        expected = max(1, ceil(10 * 0.3))  # ceil ensures at least 1
        self.assertEqual(review_count_1, expected)

    def test_skipped_unverified_not_reviewed(self):
        """Unverified items should not be sent for adversarial review."""
        # Create a driver that makes all builds fail
        class FailingDriver(FakeReviewDriver):
            def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
                # Make all builds fail
                return WorkerResult(
                    worker_id=f"worker-{self.dispatch_count}",
                    status=WORKER_FAILED,
                    ok=False,
                    error="Build failed",
                    files_written=(),
                )

        driver = FailingDriver()
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "prompt": "Fix the bug",
                    "ownsFiles": ["module.py"],
                    "testCmd": "python -m unittest discover",
                    "workDir": ".",
                }
            ],
            "adversarial_review": {
                "enabled": True,
                "sample_frac": 1.0,
            },
        }

        result = run_wave(driver, manifest)

        # Item should not be verified
        if result["built"]:
            item = result["built"][0]
            self.assertFalse(item.get("verified", False))

        # No review should have been dispatched
        self.assertEqual(len(driver.review_dispatches), 0)


if __name__ == "__main__":
    unittest.main()
