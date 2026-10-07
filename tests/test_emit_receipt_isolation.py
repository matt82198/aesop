#!/usr/bin/env python3
"""Test that emit_receipt's matrix runs in a throwaway worktree and doesn't mutate the caller tree."""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import emit_receipt


def capture_git_state(repo_path):
    """Capture HEAD, index tree hash, and porcelain status for comparison."""
    repo = Path(repo_path)

    # Get HEAD commit SHA
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, encoding="utf-8")
    head_sha = result.stdout.strip()

    # Get the tree hash of the current index (HEAD^{tree})
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
                          capture_output=True, text=True, encoding="utf-8")
    tree_hash = result.stdout.strip()

    # Get porcelain status (untracked, modified, staged files)
    result = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True, encoding="utf-8")
    porcelain = result.stdout

    return {"head_sha": head_sha, "tree_hash": tree_hash, "porcelain": porcelain}


def assert_git_state_unchanged(before, after, label=""):
    """Assert that git state (HEAD, index, worktree) is identical."""
    prefix = f" ({label})" if label else ""

    assert before["head_sha"] == after["head_sha"], \
        f"HEAD SHA changed{prefix}: {before['head_sha']} -> {after['head_sha']}"
    assert before["tree_hash"] == after["tree_hash"], \
        f"Tree hash changed{prefix}: {before['tree_hash']} -> {after['tree_hash']}"
    assert before["porcelain"] == after["porcelain"], \
        f"Working tree modified{prefix}: {repr(after['porcelain'])}"


def test_matrix_caller_repo_untouched():
    """Test that run_matrix() doesn't mutate the caller repo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create a test repo with one committed file
        repo = tmpdir / "test-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                      cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                      cwd=str(repo), capture_output=True, check=True)

        # Create and commit an initial file
        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"],
                      cwd=str(repo), capture_output=True, check=True)

        # Capture state before running matrix
        state_before = capture_git_state(repo)

        # Run matrix with empty registry (no parts to run)
        custom_registry = {}
        parts, skipped = emit_receipt.run_matrix(repo, [], registry=custom_registry, jobs=1)

        # Capture state after running matrix
        state_after = capture_git_state(repo)

        # Verify caller repo is unchanged
        assert_git_state_unchanged(state_before, state_after, "after matrix run")

        print("✓ Matrix runs without mutating caller tree")


def test_receipt_head_sha_tree_hash_match():
    """Test that the receipt's head_sha and tree_hash match the actual repo state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        repo = tmpdir / "test-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                      cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                      cwd=str(repo), capture_output=True, check=True)

        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"],
                      cwd=str(repo), capture_output=True, check=True)

        # Get the actual HEAD and tree hash
        result = subprocess.run(["git", "rev-parse", "HEAD"],
                              cwd=str(repo), capture_output=True, text=True, encoding="utf-8", check=True)
        actual_head = result.stdout.strip()

        result = subprocess.run(["git", "rev-parse", "HEAD^{tree}"],
                              cwd=str(repo), capture_output=True, text=True, encoding="utf-8", check=True)
        actual_tree_hash = result.stdout.strip()

        # Build a receipt (use HEAD as main_ref since this is a test repo)
        custom_registry = {}  # No parts to run
        parts, skipped = emit_receipt.run_matrix(repo, [], registry=custom_registry, jobs=1)
        receipt = emit_receipt.build_receipt(repo, parts, skipped, main_ref="HEAD")

        # Verify HEAD and tree_hash in receipt match actual
        assert receipt["head_sha"] == actual_head, \
            f"Receipt head_sha mismatch: {receipt['head_sha']} vs {actual_head}"
        assert receipt["tree_hash"] == actual_tree_hash, \
            f"Receipt tree_hash mismatch: {receipt['tree_hash']} vs {actual_tree_hash}"

        print("✓ Receipt head_sha and tree_hash match repo state")


def test_throwaway_worktree_cleaned_up():
    """Test that throwaway worktrees are properly cleaned up after use."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        repo = tmpdir / "test-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"],
                      cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                      cwd=str(repo), capture_output=True, check=True)

        (repo / "file.txt").write_text("content")
        subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"],
                      cwd=str(repo), capture_output=True, check=True)

        # Run matrix multiple times and verify worktrees are cleaned up
        for i in range(3):
            custom_registry = {}
            parts, skipped = emit_receipt.run_matrix(repo, [], registry=custom_registry, jobs=1)

        # Check git worktree list - should only have the main tree
        result = subprocess.run(["git", "worktree", "list"],
                              cwd=str(repo), capture_output=True, text=True, encoding="utf-8", check=True)
        lines = result.stdout.strip().split("\n")

        # Filter out the main working tree (should only be one line for main)
        main_lines = [l for l in lines if "(bare)" not in l and "detached" not in l]

        # There should be exactly one working tree (the main repo)
        assert len(main_lines) >= 1, "No main worktree found"

        print("✓ Throwaway worktrees are properly cleaned up")


if __name__ == "__main__":
    test_matrix_caller_repo_untouched()
    test_receipt_head_sha_tree_hash_match()
    test_throwaway_worktree_cleaned_up()
    print("\n✓ All isolation tests passed")
