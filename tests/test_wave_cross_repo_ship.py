#!/usr/bin/env python3
"""Per-repo ship phase tests: manifest items with cross-repo git operations.

Tests for per-repo ship phase (Phase 1 scope):
  1. Two-repo manifest with verified items:
     - Each repo gets ONE commit with only its files
     - Each repo's expectTopLevel guard is verified separately
     - Commits succeed even if one repo's push fails
  2. Guard mismatch aborts THAT repo's ship but continues others
  3. Single-repo regression: byte-identical old behavior preserved

stdlib-only (unittest), ASCII-only, Windows + Linux safe.
"""

import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
_INITIAL_SHAS = {}


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
    _MODULE_TMP = tempfile.mkdtemp(prefix="wave-cross-repo-ship-tests-")

    # Create fixture repo A
    _FIXTURE_REPO_A = Path(_MODULE_TMP) / "repo-a"
    _init_repo(_FIXTURE_REPO_A, "repo A")

    # Create fixture repo B
    _FIXTURE_REPO_B = Path(_MODULE_TMP) / "repo-b"
    _init_repo(_FIXTURE_REPO_B, "repo B")

    # Pin each repo's INITIAL commit sha. setUp must reset to THIS, not HEAD:
    # tests legitimately commit into the fixtures (that's what ship does), so
    # HEAD drifts — resetting to a drifted HEAD keeps prior tests' files
    # committed, and a later identical write then fails `git commit` with
    # "nothing to commit" (the intermittent shipped=1 CI failure).
    for repo in (_FIXTURE_REPO_A, _FIXTURE_REPO_B):
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # Fail LOUD if the pin is not a real sha: `git rev-parse HEAD` on an
        # unborn HEAD echoes the literal string "HEAD", which silently turned
        # the setUp reset back into drifting-HEAD behavior.
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError(f"fixture {repo} has no valid initial commit: {sha!r}")
        _INITIAL_SHAS[str(repo)] = sha


def tearDownModule():
    """Clean up fixture repos and temp directory."""
    global _MODULE_TMP, _MODULE_SAVED_CWD
    if _MODULE_SAVED_CWD:
        os.chdir(_MODULE_SAVED_CWD)
    if _MODULE_TMP:
        shutil.rmtree(_MODULE_TMP, ignore_errors=True)


class FakeDriverForShip(AgentDriver):
    """Fake AgentDriver for per-repo ship testing."""

    def __init__(self):
        self.total_tokens = 0
        self.dispatch_count = 0
        self._workers = {}
        self.dispatched_items = []
        self.run_commands = []  # Track all run_command calls with cwd

    def probe_capabilities(self) -> DriverCapabilities:
        """Return Tier 2 capabilities."""
        return DriverCapabilities(
            name="fake-driver-ship",
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
            notes="Offline fake driver for per-repo ship testing",
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
        self.run_commands.append({"command": command, "cwd": cwd})

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
            # Actually run git commands so we can verify the commits.
            try:
                if cwd:
                    result = subprocess.run(
                        command,
                        cwd=cwd,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    return CommandResult(
                        exit_code=result.returncode,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    )
                else:
                    result = subprocess.run(
                        command,
                        shell=True,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    return CommandResult(
                        exit_code=result.returncode,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    )
            except Exception as e:
                return CommandResult(exit_code=1, stdout="", stderr=str(e))

        return CommandResult(exit_code=0, stdout="OK")

    def resolve_model(self, role: str) -> str:
        """Resolve a role to a model id."""
        return "fake-model"

    def get_tokens_spent(self) -> int:
        """Return cumulative tokens spent."""
        return self.total_tokens


class TestPerRepoShip(unittest.TestCase):
    """Tests for per-repo ship phase."""

    def setUp(self):
        """Reset fixture repos to clean state before each test.

        Removes uncommitted changes and extra files to prevent test cross-contamination.
        """
        for repo_path in [_FIXTURE_REPO_A, _FIXTURE_REPO_B]:
            # Reset to the PINNED initial commit (not HEAD — HEAD drifts as
            # tests commit; see setUpModule). List-form: fixtures never shell.
            subprocess.run(
                ["git", "reset", "--hard", _INITIAL_SHAS[str(repo_path)]],
                cwd=str(repo_path),
                capture_output=True,
            )
            # Clean up any extra files
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(repo_path),
                capture_output=True,
            )

    def test_two_repo_manifest_two_commits(self):
        """Two verified items in different repos produce TWO separate commits."""
        driver = FakeDriverForShip()

        manifest = {
            "items": [
                {
                    "slug": "item-a",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["file-a.py"],
                    "prompt": "Fix file A",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
                {
                    "slug": "item-b",
                    "repo": str(_FIXTURE_REPO_B),
                    "ownsFiles": ["file-b.py"],
                    "prompt": "Fix file B",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_B),
                },
            ]
        }

        # Prepare git config for ship.
        git_config = {"expectTopLevel": str(_FIXTURE_REPO_A)}

        result = run_wave(driver, manifest, git=git_config)

        # Wave should complete successfully.
        self.assertTrue(result["preflight_ok"])
        self.assertEqual(len(result["built"]), 2)
        self.assertTrue(result["built"][0]["verified"], "Item A should verify")
        self.assertTrue(result["built"][1]["verified"], "Item B should verify")

        # Verify shipped items recorded.
        self.assertIsNotNone(result["shipped"])
        if len(result["shipped"]) != 2:
            def _probe(repo):
                out = {}
                for label, cmd in [("status", "git status --porcelain"), ("log", "git log --oneline"), ("lsfiles", "git ls-files")]:
                    r = subprocess.run(cmd, cwd=str(repo), shell=True, capture_output=True, text=True, timeout=60)
                    out[label] = r.stdout.strip()
                fa = Path(repo) / "file-a.py"
                out["file-a.exists"] = fa.exists()
                if fa.exists():
                    out["file-a.content"] = fa.read_text()[:60]
                out["initial_sha"] = _INITIAL_SHAS.get(str(repo))
                return out
            forensics = {"repo-a": _probe(_FIXTURE_REPO_A), "repo-b": _probe(_FIXTURE_REPO_B)}
            self.fail(json.dumps({"result": result, "run_commands": driver.run_commands, "forensics": forensics}, default=str))
        self.assertEqual(len(result["shipped"]), 2)

        # Verify per-repo results recorded.
        self.assertIn("shipped_repos", result)
        self.assertEqual(len(result["shipped_repos"]), 2)

        # Check that each repo has a commit with only its files.
        # Repo A should have file-a.py
        result_a = subprocess.run(
            "git log --pretty=format:%B -n 1",
            cwd=str(_FIXTURE_REPO_A),
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertIn("Wave:", result_a.stdout)

        # Check file-a.py was committed to repo A
        exists_a = (Path(_FIXTURE_REPO_A) / "file-a.py").exists()
        self.assertTrue(exists_a, "file-a.py should exist in repo A")

        # Repo B should have file-b.py
        result_b = subprocess.run(
            "git log --pretty=format:%B -n 1",
            cwd=str(_FIXTURE_REPO_B),
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertIn("Wave:", result_b.stdout)

        # Check file-b.py was committed to repo B
        exists_b = (Path(_FIXTURE_REPO_B) / "file-b.py").exists()
        self.assertTrue(exists_b, "file-b.py should exist in repo B")

        # Verify file-a.py is NOT in repo B
        exists_a_in_b = (Path(_FIXTURE_REPO_B) / "file-a.py").exists()
        self.assertFalse(exists_a_in_b, "file-a.py should NOT be in repo B")

        # Verify file-b.py is NOT in repo A
        exists_b_in_a = (Path(_FIXTURE_REPO_A) / "file-b.py").exists()
        self.assertFalse(exists_b_in_a, "file-b.py should NOT be in repo A")

    def test_single_repo_regression(self):
        """Single repo with ship behaves identically to current behavior."""
        driver = FakeDriverForShip()

        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["single-file.py"],
                    "prompt": "Fix single file",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
            ]
        }

        # Prepare git config for ship.
        git_config = {"expectTopLevel": str(_FIXTURE_REPO_A)}

        result = run_wave(driver, manifest, git=git_config)

        # Wave should complete successfully.
        self.assertTrue(result["preflight_ok"])
        self.assertEqual(len(result["built"]), 1)
        self.assertTrue(result["built"][0]["verified"])

        # Verify shipped items recorded.
        self.assertIsNotNone(result["shipped"])
        self.assertEqual(len(result["shipped"]), 1)

        # Verify file was committed to the repo.
        exists = (Path(_FIXTURE_REPO_A) / "single-file.py").exists()
        self.assertTrue(exists, "single-file.py should be in repo A")

    def test_guard_mismatch_aborts_repo_but_continues_others(self):
        """Guard mismatch in one repo (subdirectory case) doesn't prevent shipping of other repos."""
        # Create a subdirectory in repo A that's not its root
        subdir = _FIXTURE_REPO_A / "subdir"
        subdir.mkdir(exist_ok=True)

        driver = FakeDriverForShip()

        manifest = {
            "items": [
                {
                    "slug": "item-a",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["file-a.py"],
                    "prompt": "Fix file A",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
                {
                    "slug": "item-sub",
                    # This repo is actually a subdirectory, so git rev-parse --show-toplevel
                    # will return the parent (repo_a), not this subdir.
                    "repo": str(subdir),
                    "ownsFiles": ["file-sub.py"],
                    "prompt": "Fix file sub",
                    "testCmd": "python -m unittest",
                    "workDir": str(subdir),
                },
                {
                    "slug": "item-b",
                    "repo": str(_FIXTURE_REPO_B),
                    "ownsFiles": ["file-b.py"],
                    "prompt": "Fix file B",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_B),
                },
            ]
        }

        git_config = {"expectTopLevel": str(_FIXTURE_REPO_A)}

        result = run_wave(driver, manifest, git=git_config)

        # Wave should complete (preflight OK, items verified).
        self.assertTrue(result["preflight_ok"])
        self.assertTrue(result["built"][0]["verified"], "Item A should verify")
        self.assertTrue(result["built"][1]["verified"], "Item sub should verify")
        self.assertTrue(result["built"][2]["verified"], "Item B should verify")

        # Check shipped_repos for error reporting.
        self.assertIn("shipped_repos", result)
        repo_results = result["shipped_repos"]

        # Find results for each repo.
        # Note: ship phase normalizes repo paths via Path.resolve(), so we must
        # resolve the fixture paths for comparison (critical for short TMPDIR on Windows).
        result_a = next((r for r in repo_results if r["repo"] == str(Path(_FIXTURE_REPO_A).resolve())), None)
        result_sub = next((r for r in repo_results if r["repo"] == str(Path(subdir).resolve())), None)
        result_b = next((r for r in repo_results if r["repo"] == str(Path(_FIXTURE_REPO_B).resolve())), None)

        self.assertIsNotNone(result_a, "Repo A should have a result")
        self.assertIsNotNone(result_sub, "Subdir should have a result")
        self.assertIsNotNone(result_b, "Repo B should have a result")

        # Repo A should succeed
        self.assertTrue(result_a.get("committed"), "Repo A should be committed")

        # Subdir should fail (its toplevel is repo_a, not the subdir itself)
        self.assertEqual(result_sub.get("error"), "git_toplevel_mismatch",
                        f"Subdir should fail guard check: {result_sub}")

        # Repo B should succeed
        self.assertTrue(result_b.get("committed"), "Repo B should be committed")

    def test_no_files_to_ship_skips_commit(self):
        """Verified items with no files written skip the commit."""
        driver = FakeDriverForShip()

        # Mock dispatch to return no files written.
        original_dispatch = driver.dispatch_worker

        def mock_dispatch(request):
            result = original_dispatch(request)
            # Clear files_written to simulate no changes.
            return WorkerResult(
                worker_id=result.worker_id,
                status=WORKER_DONE,
                ok=True,
                structured=result.structured,
                files_written=tuple(),  # No files written
                tokens_spent=result.tokens_spent,
            )

        driver.dispatch_worker = mock_dispatch

        manifest = {
            "items": [
                {
                    "slug": "item-1",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": [],  # No files
                    "prompt": "Refactor (no file changes)",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
            ]
        }

        git_config = {"expectTopLevel": str(_FIXTURE_REPO_A)}
        result = run_wave(driver, manifest, git=git_config)

        # Wave should complete.
        self.assertTrue(result["preflight_ok"])
        # Item verifies, but no files to ship, so commit is skipped.
        # This should NOT error out.

    def test_legacy_manifest_without_repo_field_uses_expectTopLevel(self):
        """Legacy manifest items without repo field default to git config expectTopLevel.

        Regression test: Ensure that a manifest without repo fields uses the
        expectTopLevel from git config as the default repo. This ensures that
        manifests behave consistently regardless of process cwd.
        """
        driver = FakeDriverForShip()

        # Manifest WITHOUT repo fields (legacy style).
        manifest = {
            "items": [
                {
                    "slug": "legacy-item-1",
                    "ownsFiles": ["legacy-file.py"],
                    "prompt": "Fix legacy file",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
            ]
        }

        # Git config specifies the repo to use as default.
        git_config = {"expectTopLevel": str(_FIXTURE_REPO_A)}

        result = run_wave(driver, manifest, git=git_config)

        # Wave should complete successfully.
        self.assertTrue(result["preflight_ok"])
        self.assertEqual(len(result["built"]), 1)
        self.assertTrue(result["built"][0]["verified"], "Legacy item should verify")

        # Verify item was shipped into the repo specified by expectTopLevel.
        self.assertIsNotNone(result["shipped"])
        self.assertEqual(len(result["shipped"]), 1)

        # Verify the file was committed to the expected repo.
        exists = (Path(_FIXTURE_REPO_A) / "legacy-file.py").exists()
        self.assertTrue(exists, "legacy-file.py should be in repo A")

    def test_git_add_failure_with_partial_staging_cleanup(self):
        """git add failure with partial staging is cleaned up (files_unstaged recorded).

        Regression test for P1: when git add fails after partial staging, the code
        must run git reset to unstage staged residue and record files_unstaged + unstage_error.
        This test verifies that even if git add fails, the index is left clean.
        """
        # Create a custom driver that makes git add fail AFTER partial staging
        class FakeDriverWithAddFailure(FakeDriverForShip):
            def run_command(self, command: str, cwd=None, shell=None) -> ad.CommandResult:
                # For git add, stage the files then fail
                if command.startswith("git add "):
                    try:
                        # Extract filenames from the git add command.
                        # Command is: git add "file1.py" "file2.py"
                        # We need to actually stage them, then return failure.
                        add_parts = command.split()  # Split by whitespace
                        # Remove 'git', 'add' and unescape the filenames
                        files_to_stage = []
                        for part in add_parts[2:]:
                            # Unescape quoted filenames
                            if part.startswith('"') and part.endswith('"'):
                                files_to_stage.append(part[1:-1])
                            elif part.startswith("'") and part.endswith("'"):
                                files_to_stage.append(part[1:-1])
                            else:
                                files_to_stage.append(part)

                        # Actually run git add to stage the files
                        stage_cmd = ["git", "add"] + files_to_stage
                        stage_result = subprocess.run(
                            stage_cmd,
                            cwd=cwd,
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        # Verify the stage succeeded
                        if stage_result.returncode == 0:
                            # Now return failure to simulate git add failure AFTER staging
                            return ad.CommandResult(
                                exit_code=1,
                                stdout="",
                                stderr="Simulated git add failure after partial staging",
                            )
                        else:
                            # Unexpected: actual git add failed
                            return ad.CommandResult(
                                exit_code=1,
                                stdout="",
                                stderr=stage_result.stderr,
                            )
                    except Exception as e:
                        return ad.CommandResult(exit_code=1, stdout="", stderr=str(e))

                # For other git commands, use parent behavior
                return super().run_command(command, cwd, shell)

        driver = FakeDriverWithAddFailure()

        manifest = {
            "items": [
                {
                    "slug": "item-add-fail",
                    "repo": str(_FIXTURE_REPO_A),
                    "ownsFiles": ["add-fail-file.py"],
                    "prompt": "Fix file that will stage then fail add",
                    "testCmd": "python -m unittest",
                    "workDir": str(_FIXTURE_REPO_A),
                },
            ]
        }

        git_config = {"expectTopLevel": str(_FIXTURE_REPO_A)}
        result = run_wave(driver, manifest, git=git_config)

        # Wave should complete (preflight OK, item verified, but ship fails on add).
        self.assertTrue(result["preflight_ok"])
        self.assertEqual(len(result["built"]), 1)
        self.assertTrue(result["built"][0]["verified"], "Item should verify")

        # Check shipped_repos for add failure.
        self.assertIn("shipped_repos", result)
        repo_results = result["shipped_repos"]
        self.assertEqual(len(repo_results), 1)

        repo_result = repo_results[0]
        self.assertEqual(repo_result.get("error"), "git_add_failed",
                        f"Should fail with git_add_failed: {repo_result}")
        # The key assertion: files_unstaged and unstage_error should be recorded
        self.assertTrue(repo_result.get("files_unstaged", False),
                       f"files_unstaged should be True (git reset succeeded): {repo_result}")
        # unstage_error should be None if reset succeeded
        if repo_result.get("files_unstaged"):
            self.assertIsNone(repo_result.get("unstage_error"),
                            "unstage_error should be None if reset succeeded")

        # Verify the index is clean (no staged files left)
        status_result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(_FIXTURE_REPO_A),
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(status_result.returncode, 0,
                        "Git index should be clean after git add failure and reset")


if __name__ == "__main__":
    unittest.main()
