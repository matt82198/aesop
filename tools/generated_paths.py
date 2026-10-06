#!/usr/bin/env python3
"""The single registry of files this repository GENERATES rather than authors.
INDEX: The single registry of files this repository GENERATES rather than authors -- tracked paths a committed gate rewrites deterministically (normalised CLAUDE.md docs, the generated `tools/INDEX.md` tool index); unattended automation (merge_queue worktree-safety, regeneration hook) may `git restore` them individually by name but never `git stash`. Registration is what lets the merge queue repair a batch whose UNION drifts a generated file every member had correct in isolation; every entry must therefore be paired with a `merge_queue.REGENERATORS` command that rebuilds it, or the queue fail-closes on a branch it can never publish. Registration never weakens the gate that guards the file: the artifact is a pure function of its sources, so the only edit automation can discard is an edit to the derived bytes, and every meaningful edit lives in an unregistered source file and still faces the unchanged gate

A generated path is one that some deterministic gate rewrites from ground truth
whenever it runs, so an uncommitted modification to it carries no information a
human put there. Unattended automation is allowed to discard such a change; it
is NEVER allowed to discard anything else.

Membership criteria -- all three must hold:
  1. A committed tool in this repo rewrites the file deterministically
     (`tools/claudemd_lint.py` normalises CLAUDE.md docs; `tools/gen_tool_index.py
     --regenerate` builds `tools/INDEX.md` from the `INDEX:` header line of every
     file under `tools/`).
  2. The rewrite is reproducible: re-running the gate restores the same bytes,
     so discarding the working-tree copy loses nothing recoverable.
  3. The file is TRACKED. An untracked file is never restorable and is never
     treated as generated, no matter its path.

Registering a path is HALF the contract. The other half is a matching entry in
`tools/merge_queue.py::REGENERATORS`, because the failure this registry exists
to prevent is a batch whose UNION drifts a generated file that every member had
correct in isolation: each member's own CI is green, the integration branch is
not, the byte-identity gate fail-closes on a branch the queue already built,
and the queue wedges holding something it can never publish. That is how the
hand-maintained suite-count lines in tests/CLAUDE.md jammed the board on
2026-08-03 (later moved to a generated `tests/SUITE-COUNTS.json` artifact by PR
#776, and then -- when THAT artifact itself drifted on two clean merges, PR
#828 postmortem -- removed outright by PR #830, since nothing but this
generator/gate/registry triangle ever consumed its committed value); the same
wedge shape is why `tools/INDEX.md` is registered, since two members each
adding a tool with its own `INDEX:` line produce a merged tree neither of them
ever indexed.

Registration does NOT weaken the gate guarding a generated file, and must never
be used on a file where it would. A registered artifact is a pure function of
unregistered sources, so the only edit automation is permitted to discard is an
edit to the derived bytes -- information-free by construction, since
regenerating reproduces them exactly. Every edit that carries meaning lives in
a source file, is not registered, and still faces the same gate: a `tools/`
file with no `INDEX:` line still fails `gen_tool_index.py` closed and no index
is written, and a tampered `INDEX:` line is propagated INTO the regenerated
index where the diff shows it. What registration adds is a repair path on the
integration branch; it removes no check anywhere.

Consumers must restore these paths individually and by name (`git restore --
<path>`). `git stash` is forbidden repo-wide: the stash stack is shared across
worktrees, so a stash in one lane silently eats another lane's work in progress.
A blanket `git checkout .` is equally forbidden -- it is not targeted.

Historical note: `tools/auto_merge.py` already hard-codes a `--theirs` checkout
of `tests/CLAUDE.md` / `tools/CLAUDE.md` while resolving merge conflicts, then
runs `tools/claudemd_lint.py` to renormalise them. This module exists so that
the generated-paths list lives in exactly one place; new consumers import it
rather than re-typing it.
"""

# Ordered, ASCII, repo-root-relative POSIX paths.
GENERATED_PATHS = (
    "tests/CLAUDE.md",
    "tools/CLAUDE.md",
    "tools/INDEX.md",
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
