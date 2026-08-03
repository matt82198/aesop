#!/usr/bin/env python3
"""The single registry of files this repository GENERATES rather than authors.

A generated path is one that some deterministic gate rewrites from ground truth
whenever it runs, so an uncommitted modification to it carries no information a
human put there. Unattended automation is allowed to discard such a change; it
is NEVER allowed to discard anything else.

Membership criteria -- all three must hold:
  1. A committed tool in this repo rewrites the file deterministically
     (`tools/verify_test_suite_count.py` rewrites the `**<Lang> (N suites):**`
     count lines; `tools/claudemd_lint.py` normalises the same documents).
  2. The rewrite is reproducible: re-running the gate restores the same bytes,
     so discarding the working-tree copy loses nothing recoverable.
  3. The file is TRACKED. An untracked file is never restorable and is never
     treated as generated, no matter its path.

Consumers must restore these paths individually and by name (`git restore --
<path>`). `git stash` is forbidden repo-wide: the stash stack is shared across
worktrees, so a stash in one lane silently eats another lane's work in progress.
A blanket `git checkout .` is equally forbidden -- it is not targeted.

Historical note: `tools/auto_merge.py` already hard-codes this same pair while
resolving merge conflicts. This module exists so that list lives in exactly one
place; new consumers import it rather than re-typing it.
"""

# Ordered, ASCII, repo-root-relative POSIX paths.
GENERATED_PATHS = (
    "tests/CLAUDE.md",
    "tools/CLAUDE.md",
)


def is_generated(path: str) -> bool:
    """True when `path` is a registered generated file.

    Accepts either separator so callers can pass raw `git status` output on
    Windows without normalising first.
    """
    if not path:
        return False
    return str(path).replace("\\", "/").strip() in GENERATED_PATHS


def generated_paths() -> tuple:
    """The registry as an immutable tuple (callers must not mutate it)."""
    return GENERATED_PATHS


if __name__ == "__main__":
    for entry in GENERATED_PATHS:
        print(entry)
