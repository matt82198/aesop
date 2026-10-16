#!/usr/bin/env python3
"""Pyflakes ratchet gate: never let unused-import/unused-variable debt grow.
INDEX: Pyflakes ratchet gate wrapping `python -m pyflakes` over tools/bin/ui/state_store/driver/monitor/daemons/tests/bench; keys findings as "file@MessageClass" against a committed baseline (.pyflakes-baseline.json); fail-closed on NEW findings above baseline, fail-closed on STALE entries (single-baseline mode), --trunk-baseline (pre-push) passes a branch burn-down of the trunk baseline and blocks additions, fail-closed (exit 2) if the optional `pyflakes` package is not installed; --update-baseline is review-only, CI must never pass it

Pyflakes is a dev-only lint dependency (NOT a runtime dependency of aesop
itself -- see tools/CLAUDE.md "stdlib-only" policy). Install it with:
    python -m pip install pyflakes
CI installs it alongside pyyaml/pytest in ci.yml's "Install Python test
dependencies" step.

Why a gate at all (not just a one-off cleanup): a ~900-finding backlog of
unused imports/variables accumulated silently for months because nothing
ever ran pyflakes in CI. This gate is the ratchet that prevents that from
recurring: it does not require the whole backlog to be fixed before this
lands (the remaining findings -- deliberately-frozen bench/seam_tasks ground
truth fixtures, guarded re-export/import-probe patterns with an explicit
`# noqa: F401`/`# noqa: F841` reason, and a handful of pre-existing
findings outside unused-import/unused-variable scope such as
FStringMissingPlaceholders -- are captured in the committed baseline) but it
DOES mean no new one can land un-noticed again.

Categorization: findings are keyed by pyflakes' own message class name
(e.g. "UnusedImport", "UnusedVariable", "FStringMissingPlaceholders",
"RedefinedWhileUnused", "UndefinedName", "ImportStarUsed") rather than by
re-parsing text, since pyflakes exposes this directly via
`type(message).__name__` -- robust to wording changes across pyflakes
versions.

Ratchet baseline (same bidirectional exact-match pattern as
.portability-baseline.json / .stateapi-baseline.json): keys are
"<repo-relative file, forward slashes>@<MessageClassName>" -> count.
PASS only when the current scan EXACTLY matches the baseline. A NEW
finding (new key, or count above baseline) FAILS. A STALE entry (key gone,
or count below baseline -- i.e. you fixed something) also FAILS, so the
fix must be recorded by regenerating the baseline (`--update-baseline`)
in the same PR, same as every other ratchet gate in this repo. A missing
baseline file behaves as an empty baseline (fail-closed on every finding).

CLI:
  --check (default): run the ratchet check, exit 0=clean/1=new findings/2=error
  --json: machine-readable output (ok/mode/baseline/stale/new/findings/by_category)
  --paths DIR...: override the scanned directories (default: the 9 production
                  + test directories documented above)
  --root DIR: repository root (default: current directory)
  --baseline FILE: baseline file (default: .pyflakes-baseline.json)
  --update-baseline: regenerate --baseline from the current scan
                      (review-only; CI must never pass this)

Exit codes: 0 clean/baselined, 1 new-or-stale findings (ratchet mismatch)
or bare findings (non-ratchet mode), 2 error (pyflakes not installed,
unreadable paths, malformed baseline/usage error).
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_PATHS = [
    "tools",
    "bin",
    "ui",
    "state_store",
    "driver",
    "monitor",
    "daemons",
    "tests",
    "bench",
]

DEFAULT_BASELINE = ".pyflakes-baseline.json"


def _normalize(path: str) -> str:
    return str(path).replace("\\", "/")


class _Collector:
    """pyflakes Reporter-protocol collector (flake/syntaxError/unexpectedError)."""

    def __init__(self):
        self.flakes = []
        self.errors: List[str] = []

    def unexpectedError(self, filename, msg):  # noqa: N802 -- pyflakes Reporter API
        self.errors.append(f"{filename}: {msg}")

    def syntaxError(self, filename, msg, lineno, offset, text):  # noqa: N802
        self.errors.append(f"{filename}:{lineno}: {msg}")

    def flake(self, message):
        self.flakes.append(message)


def run_pyflakes(root: Path, paths: List[str]) -> Tuple[List[dict], List[str], bool]:
    """Run pyflakes over the given repo-relative paths.

    Returns (findings, errors, pyflakes_available). Each finding is a dict with
    file (repo-relative, forward slashes), line, col, category (message class
    name), message (str(warning)).
    """
    try:
        import pyflakes.api as api
    except ImportError:
        return [], [], False

    existing = [p for p in paths if (root / p).exists()]
    if not existing:
        return [], [f"no scannable paths found under {root} (looked for {paths})"], True

    collector = _Collector()
    cwd = os.getcwd()
    try:
        os.chdir(root)
        api.checkRecursive(existing, collector)
    finally:
        os.chdir(cwd)

    findings = []
    for w in collector.flakes:
        findings.append({
            "file": _normalize(os.path.relpath(w.filename, ".") if os.path.isabs(w.filename) else w.filename),
            "line": getattr(w, "lineno", 0),
            "category": type(w).__name__,
            "message": str(w),
        })
    findings.sort(key=lambda f: (f["file"], f["line"], f["category"]))
    return findings, collector.errors, True


def findings_to_baseline_keys(findings: List[dict]) -> Dict[str, int]:
    """Aggregate findings into {"file@Category": count}."""
    keys: Dict[str, int] = {}
    for f in findings:
        key = f"{_normalize(f['file'])}@{f['category']}"
        keys[key] = keys.get(key, 0) + 1
    return keys


def load_baseline(baseline_file: Path) -> Dict[str, int]:
    """Load a baseline file; missing/unreadable means empty baseline (fail-closed)."""
    p = Path(baseline_file)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    violations = data.get("violations", {})
    if not isinstance(violations, dict):
        return {}
    return {_normalize(k): int(v) for k, v in violations.items()}


def save_baseline(baseline_file: Path, keys: Dict[str, int]) -> None:
    """Write the baseline file from a {"file@Category": count} dict."""
    data = {
        "_comment": (
            "Pyflakes ratchet baseline (see tools/pyflakes_gate.py --help). "
            "Regenerate ONLY via --update-baseline after reviewing the diff; "
            "CI must never pass --update-baseline."
        ),
        "violations": {k: keys[k] for k in sorted(keys)},
    }
    Path(baseline_file).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def check_ratchet(baseline_keys: Dict[str, int], current_keys: Dict[str, int]):
    """Bidirectional exact-match ratchet. Returns (is_ok, stale, new)."""
    stale = []
    new = []
    for key in sorted(set(baseline_keys) | set(current_keys)):
        b = baseline_keys.get(key, 0)
        c = current_keys.get(key, 0)
        if c > b:
            new.append(f"{key} (baseline {b}, current {c})")
        elif c < b:
            stale.append(f"{key} (baseline {b}, current {c})")
    return (not stale and not new), stale, new


def load_baseline_strict(baseline_file: Path) -> Dict[str, int]:
    """Load a baseline or raise ValueError when it is missing, unreadable or malformed.

    Used for the pre-push trunk mode, where a branch baseline that cannot be
    read must block (never be treated as empty).
    """
    p = Path(baseline_file)
    if not p.is_file():
        raise ValueError(f"baseline missing: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"baseline unreadable or malformed: {p} ({exc})")
    violations = data.get("violations") if isinstance(data, dict) else None
    if not isinstance(violations, dict):
        raise ValueError(f"baseline malformed (no violations object): {p}")
    out: Dict[str, int] = {}
    for key, count in violations.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"baseline malformed (bad count for {key}): {p}")
        out[_normalize(key)] = count
    return out


def find_additions(trunk_keys: Dict[str, int], branch_keys: Dict[str, int]) -> List[str]:
    """Branch entries the trunk baseline does not record, or records at a lower count."""
    out = []
    for key in sorted(branch_keys):
        trunk_count = trunk_keys.get(key, 0)
        branch_count = branch_keys[key]
        if branch_count > trunk_count:
            out.append(f"{key} (trunk {trunk_count}, branch {branch_count})")
    return out


def burned_down_count(trunk_keys: Dict[str, int], branch_keys: Dict[str, int]) -> int:
    """Findings the branch baseline removed from the trunk baseline (0 = no burn-down)."""
    return sum(max(0, count - branch_keys.get(key, 0)) for key, count in trunk_keys.items())


def find_new_over_baseline(baseline_keys: Dict[str, int], current_keys: Dict[str, int]) -> List[str]:
    """Current findings above the branch baseline (a finding the baseline does not record)."""
    out = []
    for key in sorted(current_keys):
        baseline_count = baseline_keys.get(key, 0)
        current_count = current_keys[key]
        if current_count > baseline_count:
            out.append(f"{key} (baseline {baseline_count}, current {current_count})")
    return out


def category_counts(findings: List[dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--paths", nargs="+", default=None, help="Override scanned directories/files (default: %s)" % ", ".join(DEFAULT_PATHS))
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE, help=f"Ratchet baseline file (default: {DEFAULT_BASELINE}; pass empty string to disable ratchet mode)")
    parser.add_argument("--trunk-baseline", default=None, help="Pre-push trunk mode: the trunk's baseline. --baseline (the branch copy) must be a burn-down of it (strict subset, scan within it) or the gate fails; additions and missing/malformed baselines fail")
    parser.add_argument("--update-baseline", action="store_true", help="Regenerate --baseline from the current scan (review-only; CI must never pass this)")
    parser.add_argument("--check", action="store_true", help="(default behavior) run the gate check")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    paths = args.paths if args.paths else DEFAULT_PATHS

    if args.update_baseline and not args.baseline:
        print("pyflakes_gate: --update-baseline requires --baseline FILE", file=sys.stderr)
        return 2

    findings, errors, available = run_pyflakes(root, paths)

    if not available:
        print(
            "pyflakes_gate: the 'pyflakes' package is not installed (dev-only "
            "dependency, not a runtime dependency of aesop). Install with:\n"
            "    python -m pip install pyflakes",
            file=sys.stderr,
        )
        return 2

    if errors:
        for e in errors:
            print(f"pyflakes_gate: evaluation error: {e}", file=sys.stderr)
        return 2

    baseline_path = root / args.baseline if args.baseline else None

    if args.baseline and args.update_baseline:
        current_keys = findings_to_baseline_keys(findings)
        save_baseline(baseline_path, current_keys)
        print(
            f"pyflakes_gate: baseline updated ({len(current_keys)} entries, "
            f"{len(findings)} finding(s)) -> {args.baseline}"
        )
        return 0

    if args.baseline:
        current_keys = findings_to_baseline_keys(findings)
        burned = 0
        if args.trunk_baseline:
            # D40 trunk mode: the branch baseline must be readable, must not add
            # anything the trunk does not record, and may only burn the trunk down.
            try:
                baseline_keys = load_baseline_strict(baseline_path)
                trunk_path = root / args.trunk_baseline
                trunk_keys = load_baseline_strict(trunk_path) if trunk_path.exists() else {}
            except ValueError as exc:
                print(f"pyflakes_gate: FAIL: {exc}", file=sys.stderr)
                return 1
            additions = find_additions(trunk_keys, baseline_keys)
            if additions:
                print("pyflakes_gate: FAIL: branch baseline adds entries the trunk baseline does not record:", file=sys.stderr)
                for item in additions:
                    print(f"  ADDED {item}", file=sys.stderr)
                return 1
            burned = burned_down_count(trunk_keys, baseline_keys)
        else:
            baseline_keys = load_baseline(baseline_path)
        if burned:
            stale = []
            new = find_new_over_baseline(baseline_keys, current_keys)
            is_ok = not new
        else:
            is_ok, stale, new = check_ratchet(baseline_keys, current_keys)
        if args.json:
            print(json.dumps({
                "ok": is_ok,
                "mode": "ratchet",
                "baseline": str(args.baseline),
                "stale": stale,
                "new": new,
                "burned_down": burned,
                "by_category": category_counts(findings),
                "findings": findings,
            }, indent=2))
        else:
            if is_ok:
                print(
                    f"pyflakes_gate: PASS (ratchet: {len(findings)} baselined "
                    f"finding(s) across {len(baseline_keys)} entries)"
                )
                if burned:
                    print(f"pyflakes baseline burned down: {burned} entries removed")
            else:
                if new:
                    print(f"pyflakes_gate: {len(new)} NEW violation key(s) above baseline:", file=sys.stderr)
                    for item in new:
                        print(f"  NEW   {item}", file=sys.stderr)
                if stale:
                    print(
                        f"pyflakes_gate: {len(stale)} STALE baseline entries "
                        "(violations fixed; regenerate the baseline to record "
                        "the burn-down):",
                        file=sys.stderr,
                    )
                    for item in stale:
                        print(f"  STALE {item}", file=sys.stderr)
                print(
                    "\nFAIL: baseline mismatch. Fix new violations, then "
                    "regenerate with --update-baseline if intentional.",
                    file=sys.stderr,
                )
        return 0 if is_ok else 1

    # Non-ratchet mode (no --baseline): report raw findings.
    if args.json:
        print(json.dumps({
            "findings": findings,
            "by_category": category_counts(findings),
        }, indent=2))
    else:
        if findings:
            print(f"Found {len(findings)} pyflakes finding(s):", file=sys.stderr)
            for i, f in enumerate(findings, 1):
                print(f"{i}. {f['file']}:{f['line']}: [{f['category']}] {f['message']}", file=sys.stderr)
        else:
            print("pyflakes_gate: no findings.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
