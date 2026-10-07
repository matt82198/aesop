#!/usr/bin/env python3
"""
CI-shard-coverage gate: every live, git-tracked Python test file must be
actually scheduled by the CI shard matrix in .github/workflows/ci.yml.
INDEX: Guardrail: verifies the CI shard matrix (`.github/workflows/ci.yml`) actually covers every git-tracked `tests/test_*.py` file -- fails closed (exit 1) when a `matrix.<key>: [ids]` array does not contiguously span `0..total-1` for the `total` a `ci_shard_runner.py <id> <total>` step invokes under it, because a gap there means some shard index NEVER RUNS in CI and every test file round-robin-assigned to it is silently never executed (modern fake-green: the file is tracked and would be collected, but no job slot ever asks for it). Pure text/regex parse of ci.yml (stdlib-only, no YAML dependency) paired with a live import of `tools/ci_shard_runner.py` to prove totality against the REAL `distribute_shards()` function, not a re-derivation of its math. Exit 0 when every discovered shard invocation is fully covered OR when ci.yml has no `ci_shard_runner.py` step at all (a different-shaped repo -- not this project's CI -- has nothing to verify, so this is a pass, not a fail-closed refusal). `--check` (default, read-only, the only mode -- there is nothing to regenerate since structural fix #830 removed the committed tests/SUITE-COUNTS.json this tool used to gate against). CLI: `--check` [--repo ROOT] [--ci-config PATH]; exit 0=covered or N/A, 1=a shard gap would silently drop tracked tests, 2=cannot evaluate (not a git repo, git failure, ci.yml unreadable)

Why this tool's job changed (PR #830, "guard: compute suite counts live"):
Before #830 this was a thin backward-compatible wrapper delegating to
`tools/gen_suite_counts.py --check`, which compared a committed
`tests/SUITE-COUNTS.json` snapshot against live `git ls-files` counts. That
artifact drifted on two clean merges (PR #828 postmortem) even though nothing
else in the repo ever consumed its committed VALUE -- only this
generator/gate/registry triangle did -- so #830 deleted the artifact rather
than keep policing it. Deleting it does not retire the underlying incident
class this tool exists to catch (PR #605: a Python test file existed on disk
and in git, CI's shard runner silently never ran it, and nothing caught that
locally before push). With no stored count left to compare, this tool is
repointed at the one DISTINCT way that incident can still happen even with
counts computed fresh on every call: the CI workflow's own shard matrix
drifting out of sync with the `total_shards` argument `ci_shard_runner.py` is
actually invoked with, leaving a shard index that no job ever requests and
every test file round-robin-assigned to it permanently unexecuted. (The
separate, already-covered risk of a test file existing on disk but never
`git add`ed is `tools/verify_test_coverage.py`'s job -- Guardrail G2, wired as
its own pre-push/CI step -- and is not duplicated here.)

Usage:
    python tools/verify_test_suite_count.py --check [--repo ROOT] [--ci-config PATH]

Exit codes:
    0  every configured CI shard invocation is fully covered, or none exists
    1  a shard-matrix gap would silently drop tracked test file(s)
    2  cannot evaluate (not a git repo, git failure, ci.yml unreadable)
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Resolved relative to THIS file's location, never a cwd-relative string: a
# pre-push hook or a test commonly runs this tool with the process cwd set to
# the repo being graded (via --repo or a plain cwd change), not to this aesop
# checkout, so a cwd-relative sibling import would silently fail in exactly
# the cases this tool exists to serve.
_TOOLS_DIR = Path(__file__).resolve().parent
# Unconditional top-level insert is the repo's sanctioned sibling-import guard
# (same form as auto_merge.py / tracker_autoclose.py / merge_queue.py) and the
# only form tools/sibling_import_check.py recognizes. The conditional
# `if ... not in sys.path` variant guards identically at runtime but is
# invisible to the checker's AST walk (which only looks for a bare
# sys.path.insert(...) statement directly in the module body, not nested
# inside an `if`), which is what left the two imports below reported as
# unguarded violations. A module body executes once, so an unconditional
# insert cannot accumulate duplicate sys.path entries.
sys.path.insert(0, str(_TOOLS_DIR))

import ci_shard_runner  # noqa: E402  (sys.path adjusted above)
import gen_suite_counts  # noqa: E402  (sys.path adjusted above)


# `ci_shard_runner.py ${{ matrix.python-shard }} 4` (plus trailing flags).
SHARD_INVOCATION_RE = re.compile(
    r"ci_shard_runner\.py\s+\$\{\{\s*matrix\.([\w-]+)\s*\}\}\s+(\d+)"
)
# A YAML mapping entry whose value is a flow-sequence, e.g. `python-shard: [0, 1, 2, 3]`.
MATRIX_ARRAY_RE = re.compile(r"^[ \t]*([\w-]+):[ \t]*\[([^\]]*)\][ \t]*$", re.MULTILINE)

DEFAULT_CI_CONFIG = ".github/workflows/ci.yml"

# (matrix_key, configured_shard_ids, total_shards)
ShardPair = Tuple[str, List[int], int]


def parse_matrix_arrays(ci_text: str) -> List[Tuple[str, List[int], int]]:
    """Every `<key>: [ids]` flow-sequence mapping in the text, with its offset."""
    out = []
    for m in MATRIX_ARRAY_RE.finditer(ci_text):
        key, raw_ids = m.group(1), m.group(2)
        ids = []
        ok = True
        for token in raw_ids.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                ids.append(int(token))
            except ValueError:
                ok = False
                break
        if ok and ids:
            out.append((key, ids, m.start()))
    return out


def parse_shard_invocations(ci_text: str) -> List[Tuple[str, int, int]]:
    """Every `ci_shard_runner.py ${{ matrix.KEY }} TOTAL` invocation, with its offset."""
    return [(m.group(1), int(m.group(2)), m.start())
            for m in SHARD_INVOCATION_RE.finditer(ci_text)]


def pair_invocations_with_matrices(ci_text: str) -> List[ShardPair]:
    """Pair each shard invocation with the nearest PRECEDING matrix array of the
    same key (a job's `strategy: matrix:` block is always declared above its own
    `steps:`, so "nearest above" resolves correctly per job).

    An invocation with no preceding matrix array of its key is skipped --
    malformed enough that there is nothing to pair against.
    """
    matrices = parse_matrix_arrays(ci_text)
    invocations = parse_shard_invocations(ci_text)
    pairs: List[ShardPair] = []
    for key, total, pos in invocations:
        candidates = [(ids, mpos) for (mkey, ids, mpos) in matrices
                      if mkey == key and mpos < pos]
        if not candidates:
            continue
        ids, _ = max(candidates, key=lambda c: c[1])
        pairs.append((key, ids, total))
    return pairs


def find_shard_gaps(pairs: List[ShardPair]) -> List[str]:
    """Report every (matrix_key, ids, total) pair whose configured ids do not
    contiguously span 0..total-1 -- the shape that leaves a shard index no job
    ever requests."""
    problems = []
    for key, ids, total in pairs:
        if total <= 0:
            problems.append(
                f"matrix '{key}' pairs with a non-positive total_shards={total}"
            )
            continue
        missing_ids = sorted(set(range(total)) - set(ids))
        if missing_ids:
            problems.append(
                f"matrix '{key}' configures shard ids {sorted(set(ids))} but "
                f"ci_shard_runner.py is invoked with total_shards={total} "
                f"(expected contiguous 0..{total - 1}); shard id(s) {missing_ids} "
                "never run in CI, so every tracked test file round-robin-assigned "
                "to them is silently never executed"
            )
    return problems


def find_uncovered_tracked_files(repo_root: Path, pairs: List[ShardPair]) -> List[str]:
    """Actually call the REAL `ci_shard_runner.distribute_shards()` across every
    configured id of every pair and prove the union covers every live,
    git-tracked `tests/test_*.py` file.

    A config gap already fails via find_shard_gaps(); this additionally proves
    the property against the real function rather than re-deriving its math, so
    a future change to distribute_shards() that stops totalizing (even with ids
    contiguous 0..total-1) is caught here too.
    """
    if not pairs:
        return []

    tracked = gen_suite_counts.list_git_files(repo_root, "tests/test_*.py")
    stems = sorted(Path(p).stem for p in tracked)
    if not stems:
        return []

    covered_by_any = set()
    for _key, ids, total in pairs:
        if total <= 0:
            continue
        for shard_id in ids:
            if 0 <= shard_id < total:
                covered_by_any.update(
                    ci_shard_runner.distribute_shards(stems, shard_id, total)
                )

    return [stem for stem in stems if stem not in covered_by_any]


def check_mode(repo_root: Path, ci_config: Path) -> int:
    """READ-ONLY. Returns 0/1/2 per the module docstring's exit codes."""
    if not ci_config.exists():
        print(
            f"[OK] {ci_config} not found; no CI shard matrix to verify "
            "(this is not this project's CI shape)."
        )
        return 0

    try:
        ci_text = ci_config.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[ERROR] Cannot read {ci_config}: {exc}", file=sys.stderr)
        return 2

    pairs = pair_invocations_with_matrices(ci_text)
    if not pairs:
        print(
            f"[OK] {ci_config} has no ci_shard_runner.py matrix invocation; "
            "nothing to verify."
        )
        return 0

    gaps = find_shard_gaps(pairs)
    try:
        uncovered = find_uncovered_tracked_files(repo_root, pairs)
    except gen_suite_counts.StructureError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code

    if not gaps and not uncovered:
        tracked_count = len(gen_suite_counts.list_git_files(repo_root, "tests/test_*.py"))
        print(
            f"[OK] {len(pairs)} CI shard matrix configuration(s) fully cover "
            f"{tracked_count} tracked Python test file(s)."
        )
        return 0

    for problem in gaps:
        print(f"[GAP] {problem}", file=sys.stderr)
    if uncovered:
        preview = uncovered[:10]
        print(
            f"[GAP] {len(uncovered)} tracked test file(s) are not assigned to any "
            f"configured shard: {preview}" + (" ..." if len(uncovered) > 10 else ""),
            file=sys.stderr,
        )
    print(
        "\nFix: make the matrix array's shard ids exactly 0..total_shards-1 for "
        "every ci_shard_runner.py invocation in the CI workflow.",
        file=sys.stderr,
    )
    return 1


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only: verify the CI shard matrix covers every tracked test "
             "file; exit 1 on a gap. Default (and only) mode.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Alias for --check (reserved for backward compatibility).",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Repository root (default: current directory)",
    )
    parser.add_argument(
        "--ci-config",
        type=Path,
        default=None,
        help="Path to the CI workflow file (default: <repo>/.github/workflows/ci.yml)",
    )

    args = parser.parse_args()

    repo_root = (args.repo or Path.cwd()).resolve()
    if not repo_root.is_dir():
        print(f"[ERROR] repo root {repo_root} is not a directory", file=sys.stderr)
        return 2

    ci_config = args.ci_config or (repo_root / DEFAULT_CI_CONFIG)

    return check_mode(repo_root, ci_config)


if __name__ == "__main__":
    sys.exit(main())
