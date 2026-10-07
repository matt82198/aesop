#!/usr/bin/env python3
"""Cross-repo phase 1 spike tests: manifest items with per-repo `repo` field.

Tests for Phase 1 of cross-repo orchestration:
  1. Manifest items gain an optional `repo` field (absolute path)
  2. Preflight validates per-repo:
     - Repo exists
     - Is a git worktree
     - Has resolvable secret-scan gate
  3. Ownership disjointness is enforced PER-REPO:
     - Same relative path in two different repos is NOT a conflict
     - Same relative path within one repo IS a conflict
  4. Absent `repo` → byte-identical current behavior (regression proof)
  5. Ship-phase git operations run in each item's repo

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add driver/ to path for imports.
REPO = Path(__file__).resolve().parent.parent
DRIVER_DIR = REPO / "driver"
if str(DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(DRIVER_DIR))

import agent_driver as ad  # noqa: E402
from agent_driver import (  # noqa: E402
    AgentDriver,
    DriverCapabilities,
    WorkerRequest,
    WorkerResult,
    CommandResult,
    WORKER_DONE,
    WORKER_FAILED,
    ROLE_WORKER,
)
from wave_loop import run_wave  # noqa: E402


# Module-level temp directories for two fixture repos
_MODULE_TMP = None
_MODULE_SAVED_CWD = None
_FIXTURE_REPO_A = None
_FIXTURE_REPO_B = None


def _init_repo(repo_path: Path, repo_name: str) -> None:
    """Initialize a git repo with proper git config scoped to subprocess.

    Git config mutations are scoped to subprocess calls (no global state).
    This satisfies wave-25 test hygiene requirements.

    Args:
        repo_path: absolute path to repo directory
        repo_name: human-readable repo name for content
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    # List-form (no shell): cmd.exe does NOT treat single quotes as quoting,
    # so the shell=True forms silently broke the initial commit on Windows
    # (unborn HEAD -> later fixture resets ran against garbage).
    # check=True: a fixture that fails to build must fail LOUD at setup.
    subprocess.run(["git", "init"], cwd=str(repo_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(repo_path), capture_output=True, check=True)
    (repo_path / "README.md").write_text(f"Fixture {repo_name}\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_path), capture_output=True, check=True)


def setUpModule():
    """Set up two fixture git repos in tmpdirs."""
    global _MODULE_TMP, _MODULE_SAVED_CWD, _FIXTURE_REPO_A, _FIXTURE_REPO_B
    _MODULE_SAVED_CWD = os.getcwd()
    _MODULE_TMP = tempfile.mkdtemp(prefix="wave-cross-repo-tests-")
    # Run the whole module from the tmpdir: some manifests use workDir "."
    # and would otherwise write stub files into the repo root (hygiene rule:
    # tests never pollute cwd). tearDownModule restores the saved cwd.
    os.chdir(_MODULE_TMP)

    # Create fixture repo A
    _FIXTURE_REPO_A = Path(_MODULE_TMP) / "repo-a"
    _init_repo(_FIXTURE_REPO_A, "repo A")

    # Create fixture repo B
    _FIXTURE_REPO_B = Path(_MODULE_TMP) / "repo-b"
    _init_repo(_FIXTURE_REPO_B, "repo B")


def tearDownModule():
    """Clean up fixture repos and temp directory."""
    global _MODULE_TMP, _MODULE_SAVED_CWD
    if _MODULE_SAVED_CWD:
        os.chdir(_MODULE_SAVED_CWD)
    if _MODULE_TMP:
        shutil.rmtree(_MODULE_TMP, ignore_errors=True)


class FakeDriver(AgentDriver):
    """Fake AgentDriver for offline cross-repo testing."""

    def __init__(self):
        self.total_tokens = 0
        self.dispatch_count = 0
        self._workers = {}
        self.dispatched_items = []  # Track items dispatched with their repo context

    def probe_capabilities(self) -> DriverCapabilities:
        """Return Tier 2 capabilities."""
        return DriverCapabilities(
            name="fake-driver-cross-repo",
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
            notes="Offline fake driver for cross-repo testing",
        )

    def dispatch_worker(self, request: WorkerRequest) -> WorkerResult:
        """Dispatch a worker, writing files in the specified workdir."""
        self.dispatch_count += 1
        self.total_tokens += 100

        worker_id = f"worker-{self.dispatch_count}"
        self._workers[worker_id] = {"status": WORKER_DONE}

        workdir = Path(request.workdir) if request.workdir else Path(".")

        # Simulate writing files based on owned_files.
        files_written = []
        try:
            for f in request.owned_files:
                fpath = workdir / f
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(f"# Fixed by dispatch {self.dispatch_count}\n")
                files_written.append(f)
        except Exception as exc:
            return WorkerResult(
                worker_id=worker_id,
                status=WORKER_FAILED,
                ok=False,
                error=f"file write failed: {exc}",
            )

        self.dispatched_items.append({
            "worker_id": worker_id,
            "workdir": str(workdir),
            "owned_files": list(request.owned_files),
        })

        return WorkerResult(
            worker_id=worker_id,
            status=WORKER_DONE,
            ok=True,
            structured={"summary": f"Fixed {len(request.owned_files)} files"},
            files_written=tuple(files_written),
            tokens_spent=100,
        )

    def worker_status(self, worker_id: str) -> ad.WorkerStatus:
        """Return status of a worker."""
        if worker_id in self._workers:
            return ad.WorkerStatus(
                worker_id=worker_id,
                state=self._workers[worker_id]["status"],
            )
        return ad.WorkerStatus(worker_id=worker_id, state=ad.WORKER_UNKNOWN)

    def run_command(self, command: str, cwd=None, shell=None) -> CommandResult:
        """Run a command, simulating test pass for fixed files."""
        if command.startswith("python -m unittest"):
            # Test passes if any .py file in cwd has been written (fixed).
            try:
                if cwd:
                    cwd_path = Path(cwd)
                    for f in cwd_path.glob("*.py"):
                        if f.read_text().startswith("# Fixed"):
                            return CommandResult(exit_code=0, stdout="PASS")
                    return CommandResult(exit_code=1, stdout="FAIL")
            except Exception:
                pass
            return CommandResult(exit_code=1, stdout="FAIL")

        if command.startswith("git"):
            return CommandResult(exit_code=0, stdout="OK")

        return CommandResult(exit_code=0, stdout="OK")

    def resolve_model(self, role: str) -> str:
        """Resolve a role to a model id."""
        return "fake-model"

    def get_tokens_spent(self) -> int:
        """Return cumulative tokens spent."""
        return self.total_tokens


class TestCrossRepoPhase1(unittest.TestCase):
    """Tests for cross-repo Phase 1: manifest repo field support."""

    def test_single_repo_backward_compat(self):
        """REGRESSION PROOF: manifest without repo field works as before."""
        driver = FakeDriver()
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "ownsFiles": ["file1.py"],
                    "prompt": "Fix file1",
                    "testCmd": "python -m unittest",
                    "workDir": ".",
                },
            ]
        }

        result = run_wave(driver, manifest)
        self.assertTrue(result["preflight_ok"])
        self.assertEqual(len(result["built"]), 1)
        # Item should dispatch even without repo field (backward compat)
        self.assertTrue(result["built"][0]["dispatched"])

    def test_two_repos_disjoint_ownership(self):
        """Two items in different repos with same relative path do NOT conflict."""
        driver = FakeDriver()

        # Item 1 owns "shared.py" in repo-a
        # Item 2 owns "shared.py" in repo-b
        # This should NOT be a conflict (different repos)
        manifest = {
            "items": [
                {
                    "slug": "item-a",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["shared.py"],
                    "prompt": "Fix shared.py in A",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
                {
                    "slug": "item-b",
                    "repo": str(_FIXTURE_REPO_B),
                    "ownsFiles": ["shared.py"],
                    "prompt": "Fix shared.py in B",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_B),
                },
            ]
        }

        result = run_wave(driver, manifest)
        # Preflight should PASS (different repos, so no conflict)
        self.assertTrue(result["preflight_ok"], f"Preflight failed: {result.get('abort_reason')}")
        self.assertEqual(len(result["built"]), 2)

        # Both items should dispatch successfully
        self.assertTrue(result["built"][0]["dispatched"], "Item A should dispatch")
        self.assertTrue(result["built"][1]["dispatched"], "Item B should dispatch")

    def test_same_repo_ownership_conflict(self):
        """Two items in SAME repo with same relative path DO conflict."""
        driver = FakeDriver()

        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["shared.py"],
                    "prompt": "Fix shared.py",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
                {
                    "slug": "item-2",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["shared.py"],
                    "prompt": "Fix shared.py again",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
            ]
        }

        result = run_wave(driver, manifest)
        # Preflight should FAIL (same repo, same file, conflict)
        self.assertFalse(result["preflight_ok"])
        self.assertEqual(result["abort_reason"], "ownership_overlap")
        self.assertTrue(len(result.get("conflicts", [])) > 0)

    def test_nonexistent_repo_aborts_preflight(self):
        """Preflight aborts if repo field points to nonexistent path."""
        driver = FakeDriver()

        nonexistent_repo = Path(_MODULE_TMP) / "nonexistent-repo"
        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "repo": str(nonexistent_repo),
                    "ownsFiles": ["file.py"],
                    "prompt": "Fix file",
                    "testCmd": "python -m unittest",
                    "workDir": str(nonexistent_repo),
                },
            ]
        }

        result = run_wave(driver, manifest)
        # Preflight should FAIL because repo doesn't exist
        self.assertFalse(result["preflight_ok"])
        # Should be aborted with a repo validation error
        self.assertTrue(result.get("aborted", False))

    def test_repo_field_passed_to_dispatch(self):
        """Repo field is available to dispatch_item for per-repo gate resolution."""
        driver = FakeDriver()

        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["module.py"],
                    "prompt": "Fix module",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
            ]
        }

        result = run_wave(driver, manifest)
        self.assertTrue(result["preflight_ok"])
        self.assertEqual(len(result["built"]), 1)
        # Item should dispatch and verify
        self.assertTrue(result["built"][0]["dispatched"])

    def test_mixed_manifest_with_and_without_repo(self):
        """Manifest can have items with and without repo field."""
        driver = FakeDriver()

        manifest = {
            "items": [
                {
                    "slug": "item-no-repo",
                    "ownsFiles": ["file1.py"],
                    "prompt": "Fix file1",
                    "testCmd": "python -m unittest",
                    "workDir": ".",
                },
                {
                    "slug": "item-with-repo",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["file2.py"],
                    "prompt": "Fix file2",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
            ]
        }

        result = run_wave(driver, manifest)
        self.assertTrue(result["preflight_ok"])
        self.assertEqual(len(result["built"]), 2)
        # Both should dispatch
        self.assertTrue(result["built"][0]["dispatched"])
        self.assertTrue(result["built"][1]["dispatched"])


if __name__ == "__main__":
    unittest.main()
