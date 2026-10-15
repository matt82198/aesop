#!/usr/bin/env python3
"""
Shared test helpers for isolation and safety enforcement.

Provides guards to prevent test contamination:
- assert_sandboxed_cwd(): Verify test is running in a temporary directory
- assert_no_repo_root(): Fail if test would run in the actual aesop repo
"""

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


def publish_origin_main_tools(repo, skip=(), tools_src=None):
    """Give a fixture repo an `origin` whose main carries the real tools/ tree.

    The pre-push hook judges every push with gate CODE read from origin/main
    (hooks/pre-push-policy.sh gate_base_dir, fail-closed). A fixture remote with
    no tools/ therefore blocks every gate test with "origin/main:tools is
    unreadable". This builds a sibling base repo at `<repo>.base` holding a copy
    of the real tools/ (minus `skip` names and __pycache__), makes it `origin`,
    and fetches its main. Re-running replaces the previous base.

    Args:
        repo: Path of the fixture repository (must already be a git repo).
        skip: Tool file/dir names to leave out of the base copy.
        tools_src: Source tools/ directory; defaults to this repo's tools/.

    Returns:
        Path of the base repo (callers may delete it in cleanup).
    """
    import shutil
    import subprocess

    repo = Path(repo)
    src = Path(tools_src) if tools_src is not None else Path(__file__).parent.parent / "tools"
    base = Path(str(repo) + ".base")
    if base.exists():
        shutil.rmtree(base)
    shutil.copytree(src, base / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", *skip))

    def _git(cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True,
            text=True, encoding="utf-8", timeout=120)

    _git(base, "init", "-q", "-b", "main")
    _git(base, "add", "-A")
    _git(base, "-c", "user.email=test@example.com",
         "-c", "user.name=Test User", "commit", "-q", "-m", "base tools")
    _git(repo, "remote", "remove", "origin")
    _git(repo, "remote", "add", "origin", str(base))
    fetched = _git(repo, "fetch", "-q", "origin", "main")
    if fetched.returncode != 0:
        raise RuntimeError("publish_origin_main_tools: fetch failed: %s" % fetched.stderr)
    return base
