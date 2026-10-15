#!/usr/bin/env python3
"""Registry of repo paths whose content is MACHINE-GENERATED.
INDEX: The single home for BOTH generated-path registries, which answer different questions and must not be conflated. (1) `REGISTRY` + `is_generated(path) -> dict | None` + the `--list`/`--check` CLI: fully machine-generated files with exactly one legitimate writer (`state/ledger/*.jsonl`, `tools/INDEX.md`, `tests/SUITE-COUNTS.md`); `hooks/pre-push-policy.sh check_generated_paths()` rejects a push that hand-edits one, and `AESOP_ALLOW_GENERATED=1` is the DESIGNED writer path (generator / merge-queue regeneration / daemon push), not a weakening. Matching is lexical segment-wise fnmatch, so a path may be declared before its generator lands. (2) `GENERATED_PATHS` + `generated_paths()`: the AUTOMATION-RESTORABLE tuple (`tests/CLAUDE.md`, `tools/CLAUDE.md`, `tools/INDEX.md`) -- tracked paths a committed gate rewrites deterministically (normalised CLAUDE.md docs, the generated tool index), so unattended automation (`merge_queue.worktree_is_safe`, `regenerate_on_batch`) may restore them individually BY NAME, never via the shared stash and never a blanket checkout. Registration is what lets the merge queue repair a batch whose UNION drifts a generated file every member had correct in isolation; every entry must therefore be paired with a `merge_queue.REGENERATORS` command that rebuilds it, or the queue fail-closes on a branch it can never publish. Registration never weakens the gate guarding the file: the artifact is a pure function of unregistered sources, so the only edit automation can discard is an edit to the derived bytes. The two lists are deliberately NOT the same set -- tests/CLAUDE.md and tools/CLAUDE.md carry hand-authored prose alongside their gate-rewritten lines, so they are restorable by automation but must never be blocked at push, while `tools/INDEX.md` is in both because it is fully generated AND needs union repair on an integration branch

A generated file has exactly one legitimate writer: its generator. When a human
or an agent hand-edits one, two things go wrong at once -- the edit is silently
reverted on the next regeneration, and every concurrent lane that regenerates it
collides on the same lines. That is the contended-file conflict class. This
module is the single declared list of those paths, plus the gate that keeps them
out of ordinary pushes.

API:
    is_generated(path) -> dict | None   the matching REGISTRY entry, or None
    GENERATED_PATHS / generated_paths() the automation-restorable tuple (below)
    is_restorable(path) -> bool         the predicate for GENERATED_PATHS

TWO REGISTRIES, TWO QUESTIONS -- do not conflate them:

  REGISTRY / is_generated()  "is this file fully machine-written?"
      Every byte comes from a generator, so a hand edit is both lost on the next
      regeneration and a guaranteed conflict with every concurrent lane. The
      pre-push gate REJECTS a push that touches one of these.

  GENERATED_PATHS / generated_paths() / is_restorable()  "may unattended
      automation `git restore` this file if it turns up dirty?"
      A broader, weaker property. These documents are partly hand-authored prose
      and partly gate-rewritten lines (the normalisation claudemd_lint.py
      applies), so a dirty copy in a shared worktree carries no information a
      human put there and merge_queue may restore it BY NAME. Editing them is
      legitimate, so they are NOT in REGISTRY and are never blocked at push.

Membership criteria for REGISTRY -- all three must hold:
  1. A committed tool in this repo rewrites the file deterministically
     (`tools/merge_telemetry.py --append` / `tools/transcript_digest.py` append
     to `state/ledger/*.jsonl`; `tools/gen_tool_index.py --regenerate` builds
     `tools/INDEX.md` from the `INDEX:` header line of every file under
     `tools/`).
  2. The rewrite is reproducible: re-running the gate restores the same bytes
     (or, for an append-only ledger, the same prefix), so discarding an
     unmerged working-tree copy loses nothing recoverable.
  3. The file is TRACKED, or declared ahead of the generator that will own it
     (see "Matching is purely lexical" below) -- an untracked, undeclared file
     is never treated as generated.

Registering a path in REGISTRY is independent of GENERATED_PATHS: a path may
need push-blocking without needing batch-union repair (the ledgers are
append-only and never drift on merge), or need batch-union repair without
needing push-blocking (tests/CLAUDE.md and tools/CLAUDE.md are hand-editable
prose). `tools/INDEX.md` is the one path that needs both, which is why the two
lists deliberately overlap there and nowhere else. A GENERATED_PATHS entry that
is also machine-generated must be paired with a matching entry in
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
wedge shape is why `tools/INDEX.md` is registered in both lists, since two
members each adding a tool with its own `INDEX:` line produce a merged tree
neither of them ever indexed.

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

CLI:
    generated_paths.py --list [--json]        show the registry
    generated_paths.py --check PATH [PATH..]  exit 1 if any path is registered
    generated_paths.py --check                same, reading paths from stdin

Exit codes: 0 = no registered path touched (or escape hatch set); 1 = at least
one registered path touched; 2 = usage error.

Escape hatch: AESOP_ALLOW_GENERATED=1 turns --check into a no-op that reports
what it would have blocked. This is NOT a gate weakening -- it is the DESIGNED
writer path. The generators themselves, the regeneration step of the merge
queue (`tools/merge_queue.py::build_batch`, scoped to the one push that just
committed regenerated bytes), and the daemon/orchestrator regeneration pushes
set it precisely because they are the legitimate writer; everyone else is not.
A bot push from GitHub Actions needs no escape hatch at all: Actions checkouts
never have this repo's local pre-push hook installed, so the gate never runs
there in the first place -- the env var only matters for a LOCAL push (a
seated session, the merge-queue daemon, auto_merge.py) that is itself the
generator.

Matching is purely lexical (segment-wise fnmatch on a POSIX-normalized relative
path); a registered path does not have to exist on disk, so entries can be
declared before the generator that will own them lands.

Merge-carried copies are not touches. A branch that merges origin/main inherits
main's bytes for a registered path that main regenerated. `--check` counts a
path as touched only when HEAD's blob differs from the merge-base blob AND from
origin/main's blob (`is_touched_by_push`). Identical-to-origin/main content
passes without the override; a hand edit or a real regeneration still blocks.
No origin/main or no merge-base fails closed (treated as touched).

Regenerated-identical content is not a hand edit. A touched path is still
allowed when the bytes HEAD carries are exactly what its registered generator
writes from the HEAD tree (`regen_matches_head`). The generator runs in a
throwaway detached worktree of HEAD, so the pusher's working tree is never
written. Any failure to run, locate or compare the generator is a block.
`AESOP_ALLOW_GENERATED=1` still bypasses the whole check, unchanged.
"""

import fnmatch
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
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
        # Fully derived from git history (HEAD-scoped since #1008). Any lane commit that
        # carries an Incident-Class trailer changes the table, so the file must be
        # regenerated at push and in the merge queue, not hand-edited after the fact.
        "pattern": "docs/INCIDENTS.md",
        "generator": "tools/incident_report.py --regenerate",
        "why": "incident taxonomy table derived from HEAD's commit trailers and subjects",
        "regen": ["tools/incident_report.py", "--regenerate"],
    },
    {
        "pattern": "state/ledger/*.jsonl",
        "generator": "tools/merge_telemetry.py --append / tools/transcript_digest.py (append-only writers)",
        "why": "append-only machine ledgers; hand edits break the journal invariant",
        "regen": None,
    },
    {
        "pattern": "tools/INDEX.md",
        "generator": "tools/gen_tool_index.py --regenerate",
        "why": "generated tool index extracted from per-module INDEX: docstrings",
        "regen": ["tools/gen_tool_index.py", "--regenerate"],
    },
    {
        "pattern": "tests/SUITE-COUNTS.md",
        "generator": "tools/gen_suite_counts.py --regenerate",
        "why": "reserved for a future committed suite-count snapshot; no generator "
               "currently writes it (counts are live-computed since PR #830), but the "
               "path stays registered so one may land without a race to un-block it",
        "regen": None,
    },
]

# `regen` is the MACHINE-RUNNABLE regenerator (argv, repo-root-relative, run with
# cwd = repo root) for an artifact that is a pure, idempotent function of its
# sources -- the contract the regenerating merge driver (generated_merge.py) and
# the pre-push byte-identity gate (generated_push_gate.py) rely on. Append-only
# ledgers and not-yet-generated reservations have none (None): they are
# push-blocked by REGISTRY but can neither be re-merged nor re-verified by
# regeneration. `merge_queue.REGENERATORS` must stay equal to this set
# (tests/test_generated_merge.py pins it), so the batch repair, the driver and
# the gate never disagree about who rebuilds an artifact.


def regenerator_for(path: str) -> Optional[List[str]]:
    """The regenerator argv for ``path`` (a copy), or None when the path is not
    registered or its artifact has no machine-runnable regenerator."""
    entry = is_generated(path)
    if entry is None or not entry.get("regen"):
        return None
    return list(entry["regen"])


def regenerable_paths() -> List[str]:
    """Registered patterns that carry a regenerator (currently exact paths)."""
    return [entry["pattern"] for entry in REGISTRY if entry.get("regen")]

# ---------------------------------------------------------------------------
# The automation-restorable registry (the SECOND question -- see module docstring).
# ---------------------------------------------------------------------------
# Ordered, ASCII, repo-root-relative POSIX paths. Membership -- all three hold:
#   1. A committed tool in this repo rewrites the file deterministically
#      (`tools/claudemd_lint.py` normalises CLAUDE.md docs; `tools/gen_tool_index.py
#      --regenerate` builds `tools/INDEX.md`).
#   2. The rewrite is reproducible: re-running the gate restores the same bytes,
#      so discarding the working-tree copy loses nothing recoverable.
#   3. The file is TRACKED. An untracked file is never restorable and is never
#      treated as generated, no matter its path.
# Consumers must restore these individually and by name (`git restore -- <path>`).
# `git stash` is forbidden repo-wide: the stash stack is shared across worktrees,
# so a stash in one lane silently eats another lane's work in progress. A blanket
# `git checkout .` is equally forbidden -- it is not targeted.
GENERATED_PATHS = (
    "docs/INCIDENTS.md",
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
    """Return the REGISTRY entry owning ``path``, or None when it is authored.

    Answers "is every byte of this file machine-written?" -- the narrower,
    push-blocking question. See is_restorable() for the broader
    "may automation discard a dirty copy?" question against GENERATED_PATHS.
    """
    if not path:
        return None
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


MAIN_REF = "origin/main"


def _git_stdout(*args: str) -> Optional[str]:
    """Stripped stdout of a git command, or None on any failure (callers fail closed)."""
    try:
        proc = subprocess.run(
            ["git"] + list(args), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _blob_at(rev: str, path: str) -> Optional[str]:
    """Blob id of ``path`` at ``rev``, or None when absent there."""
    return _git_stdout("rev-parse", "--verify", "-q", "%s:%s" % (rev, path))


def is_touched_by_push(path: str) -> bool:
    """True when this push CHANGED ``path`` relative to what it already carried.

    The push head is HEAD. A registered path reaches a push two ways: a real
    local edit, or a branch that MERGED origin/main after main changed the file.
    The merged copy is byte-identical to origin/main, so it is not a touch. A
    path is touched only when its head blob differs from the merge-base blob AND
    from origin/main's blob. Anything unresolvable (no origin/main, no
    merge-base, git failure) counts as touched: the exemption must be proven.
    """
    main = _git_stdout("rev-parse", "--verify", "-q", MAIN_REF + "^{commit}")
    head = _git_stdout("rev-parse", "--verify", "-q", "HEAD^{commit}")
    if main is None or head is None:
        return True
    base = _git_stdout("merge-base", main, head)
    if base is None:
        return True
    head_blob = _blob_at(head, path)
    carried = head_blob == _blob_at(base, path) or head_blob == _blob_at(main, path)
    return not carried


def _git_bytes(*args: str) -> Optional[bytes]:
    """Raw stdout bytes of a git command, or None on any failure (callers fail closed)."""
    try:
        proc = subprocess.run(
            ["git"] + list(args), capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_rc(*args: str) -> int:
    """Exit code of a git command; non-zero (never raised) on any failure."""
    try:
        return subprocess.run(
            ["git"] + list(args), capture_output=True, timeout=300).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


def regen_matches_head(path: str) -> bool:
    """True when HEAD's bytes for ``path`` are exactly the registered generator's output.

    The generator (the registry's ``regen`` argv) runs inside a throwaway
    detached worktree of HEAD, never the pusher's tree. The worktree is checked
    out with ``core.eol=lf`` so the generator sees the same LF bytes the blob
    stores (a CRLF checkout on Windows would make byte equality meaningless). The
    comparison is byte-for-byte against the raw HEAD blob. A hand edit, a stale
    regeneration, a missing or crashing generator, or a worktree that cannot be
    created all return False (fail closed).
    """
    argv = regenerator_for(path)
    if not argv:
        return False
    blob = _git_bytes("cat-file", "blob", "HEAD:" + path)
    if blob is None:
        return False
    tmp = Path(tempfile.mkdtemp(prefix="aesop-regen-check-"))
    wt = tmp / "wt"
    added = False
    try:
        if _git_rc("-c", "core.eol=lf", "-c", "core.autocrlf=false",
                   "worktree", "add", "--detach", "-q", str(wt), "HEAD") != 0:
            return False
        added = True
        script = wt / argv[0]
        if not script.is_file():
            return False
        proc = subprocess.run(
            [sys.executable, str(script)] + list(argv[1:]),
            cwd=str(wt), capture_output=True, timeout=300)
        if proc.returncode != 0:
            return False
        target = wt / path
        if not target.is_file():
            return False
        return target.read_bytes() == blob
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if added:
            _git_rc("worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)
        _git_rc("worktree", "prune")


def _emit_check(paths: List[str], as_json: bool) -> int:
    registered = check_paths(paths)
    touched = [hit for hit in registered if is_touched_by_push(hit["path"])]
    touched_paths = {hit["path"] for hit in touched}
    carried = [hit["path"] for hit in registered if hit["path"] not in touched_paths]
    regenerated = [hit["path"] for hit in touched if regen_matches_head(hit["path"])]
    regenerated_paths = set(regenerated)
    hits = [hit for hit in touched if hit["path"] not in regenerated_paths]
    allowed = os.environ.get(ALLOW_ENV, "") == "1"

    if as_json:
        import json

        sys.stdout.write(json.dumps(
            {"hits": hits, "count": len(hits), "allowed": allowed,
             "carried": carried, "regenerated": regenerated}, indent=2) + "\n")
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

    if carried and not as_json:
        sys.stdout.write("generated_paths: OK -- carried unchanged from %s by a merge, "
                         "not touched: %s\n" % (MAIN_REF, ", ".join(carried)))
    if regenerated and not as_json:
        sys.stdout.write("generated_paths: OK -- regenerated identically by its generator "
                         "from the pushed tree: %s\n" % ", ".join(regenerated))
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
