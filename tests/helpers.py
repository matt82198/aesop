#!/usr/bin/env python3
"""
Shared test helpers for isolation and safety enforcement.

Provides guards to prevent test contamination:
- assert_sandboxed_cwd(): Verify test is running in a temporary directory
- assert_no_repo_root(): Fail if test would run in the actual aesop repo
"""

import os
import sys
from pathlib import Path


def assert_sandboxed_cwd(repo_root=None):
    """Assert that current working directory is NOT the aesop repo root.

    Prevents accidental cwd-based mutations (git add/commit/etc) from
    polluting the real repository. Tests that mutate git state must run
    in temp directories.

    Args:
        repo_root: Optional Path to repo root. Defaults to parent of tests dir.

    Raises:
        AssertionError: If cwd is inside the repo root (test not sandboxed)
    """
    if repo_root is None:
        # Infer repo root as parent of tests/ directory
        repo_root = Path(__file__).parent.parent
    else:
        repo_root = Path(repo_root)

    current_cwd = Path.cwd()
    repo_root_abs = repo_root.resolve()
    current_abs = current_cwd.resolve()

    # Check if cwd is repo_root or under it (except via a temp marker)
    try:
        current_abs.relative_to(repo_root_abs)
        # If no exception, current_cwd IS under repo_root — FAIL
        raise AssertionError(
            f"Test is running in repo root or under it. "
            f"cwd={current_abs}, repo_root={repo_root_abs}. "
            f"Tests that mutate git state (commit/init/add) MUST run in temp dirs. "
            f"Use tempfile.TemporaryDirectory() and os.chdir() with tearDown restoration, "
            f"or use subprocess(..., cwd=...) to isolate mutations."
        )
    except ValueError:
        # Good: current_cwd is NOT under repo_root
        pass


def assert_git_mutation_isolated(repo_root=None):
    """Alias for assert_sandboxed_cwd (more specific name for git mutations)."""
    return assert_sandboxed_cwd(repo_root)
