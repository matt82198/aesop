#!/usr/bin/env python3
"""Registry of repo paths whose content is MACHINE-GENERATED.
INDEX: The single home for BOTH generated-path registries, which answer different questions and must not be conflated. (1) `REGISTRY` + `is_generated(path) -> dict | None` + the `--list`/`--check` CLI: fully machine-generated files with exactly one legitimate writer (`state/ledger/*.jsonl`, `tools/INDEX.md`, `tests/SUITE-COUNTS.md`); `hooks/pre-push-policy.sh check_generated_paths()` rejects a push that hand-edits one, and `AESOP_ALLOW_GENERATED=1` is the DESIGNED writer path (generator / merge-train regeneration / daemon push), not a weakening. Matching is lexical segment-wise fnmatch, so a path may be declared before its generator lands. (2) `GENERATED_PATHS` + `generated_paths()`: the AUTOMATION-RESTORABLE tuple (`tests/CLAUDE.md`, `tools/CLAUDE.md`, `tools/INDEX.md`) -- tracked paths a committed gate rewrites deterministically (suite-count lines, normalised CLAUDE.md docs, the generated tool index), so unattended automation (`merge_queue.worktree_is_safe`, `regenerate_on_batch`) may restore them individually BY NAME, never via the shared stash and never a blanket checkout. Registration is what lets the merge queue repair a batch whose UNION drifts a generated file every member had correct in isolation; every entry must therefore be paired with a `merge_queue.REGENERATORS` command that rebuilds it, or the queue fail-closes on a branch it can never publish. Registration never weakens the gate guarding the file: the artifact is a pure function of unregistered sources, so the only edit automation can discard is an edit to the derived bytes. The two lists are deliberately NOT the same set -- tests/CLAUDE.md and tools/CLAUDE.md carry hand-authored prose alongside their gate-rewritten lines, so they are restorable by automation but must never be blocked at push, while `tools/INDEX.md` is in both because it is fully generated AND needs union repair on an integration branch

A generated file has exactly one legitimate writer: its generator. When a human
or an agent hand-edits one, two things go wrong at once -- the edit is silently
reverted on the next regeneration, and every concurrent lane that regenerates it
collides on the same lines. That is the contended-file conflict class. This
module is the single declared list of those paths, plus the gate that keeps them
out of ordinary pushes.

API:
    is_generated(path) -> dict | None   the matching REGISTRY entry, or None
    GENERATED_PATHS / generated_paths() the automation-restorable tuple (below)

TWO REGISTRIES, TWO QUESTIONS -- do not conflate them:

Membership criteria -- all three must hold:
  1. A committed tool in this repo rewrites the file deterministically
     (`tools/verify_test_suite_count.py` rewrites the `**<Lang> (N suites):**`
     count lines; `tools/claudemd_lint.py` normalises the same documents;
     `tools/gen_tool_index.py --regenerate` builds `tools/INDEX.md` from the
     `INDEX:` header line of every file under `tools/`).
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
suite-count lines jammed the board on 2026-08-03; `tools/INDEX.md` was the same
shape waiting to happen, since two members each adding a tool with its own
`INDEX:` line produce a merged tree neither of them ever indexed.

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

  REGISTRY / is_generated()  "is this file fully machine-written?"
      Every byte comes from a generator, so a hand edit is both lost on the next
      regeneration and a guaranteed conflict with every concurrent lane. The
      pre-push gate REJECTS a push that touches one of these.

  GENERATED_PATHS / generated_paths()  "may unattended automation `git restore`
      this file if it turns up dirty?"
      A broader, weaker property. These documents are partly hand-authored prose
      and partly gate-rewritten lines (the suite-count lines in tests/CLAUDE.md,
      the normalisation claudemd_lint.py applies), so a dirty copy in a shared
      worktree carries no information a human put there and merge_queue may
      restore it BY NAME. Editing them is legitimate, so they are NOT in
      REGISTRY and are never blocked at push.

CLI:
    generated_paths.py --list [--json]        show the registry
    generated_paths.py --check PATH [PATH..]  exit 1 if any path is registered
    generated_paths.py --check                same, reading paths from stdin

Exit codes: 0 = no registered path touched (or escape hatch set); 1 = at least
one registered path touched; 2 = usage error.

Escape hatch: AESOP_ALLOW_GENERATED=1 turns --check into a no-op that reports
what it would have blocked. This is NOT a gate weakening -- it is the DESIGNED
writer path. The generators themselves, the regeneration step of the merge
train, and the daemon/orchestrator regeneration pushes set it precisely because
they are the legitimate writer; everyone else is not.

Matching is purely lexical (segment-wise fnmatch on a POSIX-normalized relative
path); a registered path does not have to exist on disk, so entries can be
declared before the generator that will own them lands.
"""

import fnmatch
import os
import sys
from typing import Dict, List, Optional

USAGE = (
    "usage: generated_paths.py --list [--json]\n"
    "       generated_paths.py --check [PATH ...]   (paths from stdin when omitted)\n"
)

ALLOW_ENV = "AESOP_ALLOW_GENERATED"

# Each entry: pattern (repo-relative, POSIX separators, segment-wise globs),
# generator (the ONLY writer), why (what the file is).
REGISTRY: List[Dict[str, str]] = [
    {
        "pattern": "state/ledger/*.jsonl",
        "generator": "tools/transcript_digest.py / tools/fleet_ledger.py (append-only writers)",
        "why": "append-only machine ledgers; hand edits break the journal invariant",
    },
    {
        "pattern": "tools/INDEX.md",
        "generator": "tools/gen_tool_index.py --regenerate",
        "why": "generated tool index extracted from per-module INDEX: docstrings",
    },
    {
        "pattern": "tests/SUITE-COUNTS.md",
        "generator": "tools/verify_test_suite_count.py --fix",
        "why": "generated SUITE-COUNTS marker block (test suite counts by family)",
    },
]

# ---------------------------------------------------------------------------
# The automation-restorable registry (the SECOND question -- see module docstring).
# ---------------------------------------------------------------------------
# Ordered, ASCII, repo-root-relative POSIX paths. Membership -- all three hold:
#   1. A committed tool in this repo rewrites the file deterministically
#      (`tools/verify_test_suite_count.py` rewrites the `**<Lang> (N suites):**`
#      count lines; `tools/claudemd_lint.py` normalises the same documents).
#   2. The rewrite is reproducible: re-running the gate restores the same bytes,
#      so discarding the working-tree copy loses nothing recoverable.
#   3. The file is TRACKED. An untracked file is never restorable and is never
#      treated as generated, no matter its path.
# Consumers must restore these individually and by name (`git restore -- <path>`).
# `git stash` is forbidden repo-wide: the stash stack is shared across worktrees,
# so a stash in one lane silently eats another lane's work in progress. A blanket
# `git checkout .` is equally forbidden -- it is not targeted.
GENERATED_PATHS = (
    "tests/CLAUDE.md",
    "tools/CLAUDE.md",
    "tools/INDEX.md",
)


def generated_paths() -> tuple:
    """The automation-restorable registry as an immutable tuple.

    Callers must not mutate it. This is NOT the machine-generated REGISTRY the
    pre-push gate enforces; see the module docstring for the distinction.
    """
    return GENERATED_PATHS


def is_restorable(path: str) -> bool:
    """True when `path` is in the automation-restorable tuple.

    The predicate for GENERATED_PATHS -- "may unattended automation restore this
    if it turns up dirty?". Deliberately distinct from is_generated(), which
    answers the narrower "is every byte of this machine-written?" against
    REGISTRY and drives the push-blocking gate. Accepts either separator so
    callers can pass raw `git status` output on Windows without normalising.
    """
    if not path:
        return False
    return str(path).replace("\\", "/").strip() in GENERATED_PATHS


def normalize(path: str) -> str:
    """Normalize a path to a repo-relative POSIX string for lexical matching."""
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def _match(path: str, pattern: str) -> bool:
    """Segment-wise fnmatch so ``*`` never crosses a directory separator."""
    parts = normalize(path).split("/")
    globs = pattern.split("/")
    if len(parts) != len(globs):
        return False
    return all(fnmatch.fnmatchcase(p, g) for p, g in zip(parts, globs))


def is_generated(path: str) -> Optional[Dict[str, str]]:
    """Return the registry entry owning ``path``, or None when it is authored."""
    for entry in REGISTRY:
        if _match(path, entry["pattern"]):
            return entry
    return None


def check_paths(paths: List[str]) -> List[Dict[str, str]]:
    """Return one {path, pattern, generator, why} record per registered path."""
    hits = []
    for raw in paths:
        path = normalize(raw)
        if not path:
            continue
        entry = is_generated(path)
        if entry is not None:
            record = dict(entry)
            record["path"] = path
            hits.append(record)
    return hits


def _emit_list(as_json: bool) -> int:
    if as_json:
        import json

        sys.stdout.write(json.dumps({"registry": REGISTRY}, indent=2) + "\n")
        return 0
    sys.stdout.write("Generated-path registry (%d entries)\n" % len(REGISTRY))
    for entry in REGISTRY:
        sys.stdout.write("  %-28s generator: %s\n" % (entry["pattern"], entry["generator"]))
        sys.stdout.write("  %-28s %s\n" % ("", entry["why"]))
    return 0


def _emit_check(paths: List[str], as_json: bool) -> int:
    hits = check_paths(paths)
    allowed = os.environ.get(ALLOW_ENV, "") == "1"

    if as_json:
        import json

        sys.stdout.write(json.dumps(
            {"hits": hits, "count": len(hits), "allowed": allowed}, indent=2) + "\n")
    elif hits:
        stream = sys.stdout if allowed else sys.stderr
        verb = "ALLOWED (%s=1)" % ALLOW_ENV if allowed else "BLOCKED"
        stream.write("generated_paths: %s -- %d machine-generated path(s) touched\n"
                     % (verb, len(hits)))
        for hit in hits:
            stream.write("  %s\n" % hit["path"])
            stream.write("      generator: %s\n" % hit["generator"])
            stream.write("      %s\n" % hit["why"])
        if not allowed:
            stream.write("  Do not hand-edit these. Re-run the generator above; the\n")
            stream.write("  generator/daemon path sets %s=1 to push the result.\n" % ALLOW_ENV)

    if not hits or allowed:
        return 0
    return 1


def main(argv: List[str]) -> int:
    if "--help" in argv or "-h" in argv:
        sys.stdout.write(__doc__ + "\n" + USAGE)
        return 0

    as_json = "--json" in argv
    do_list = "--list" in argv
    do_check = "--check" in argv
    paths: List[str] = []
    for arg in argv:
        if arg in ("--json", "--list", "--check"):
            continue
        if arg.startswith("-"):
            sys.stderr.write("generated_paths: unknown flag %r\n%s" % (arg, USAGE))
            return 2
        paths.append(arg)

    if do_list and do_check:
        sys.stderr.write("generated_paths: --list and --check are mutually exclusive\n" + USAGE)
        return 2
    if not do_list and not do_check:
        return _emit_list(as_json)
    if do_list:
        if paths:
            sys.stderr.write("generated_paths: --list takes no paths\n" + USAGE)
            return 2
        return _emit_list(as_json)

    if not paths and not sys.stdin.isatty():
        paths = [line for line in sys.stdin.read().splitlines() if line.strip()]
    return _emit_check(paths, as_json)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
