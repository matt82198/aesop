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
  --no-cache: full scan (no per-file result cache read or write)
  --cache-dir DIR: per-file result cache directory (default
                   $AESOP_STATE_ROOT/cache/pyflakes, else the temp dir)

Speed: pyflakes analyses one file at a time, so unchanged files reuse their
recorded messages from the cache (keyed by path + sha256 of bytes). The
verdict is identical to a full scan; the JSON "scan" block and the
"pyflakes_gate: scan ..." line say whether a full-scan fallback happened and
why (cold cache, unreadable/mismatched cache, missing baseline, --no-cache).

Exit codes: 0 clean/baselined, 1 new-or-stale findings (ratchet mismatch)
or bare findings (non-ratchet mode), 2 error (pyflakes not installed,
unreadable paths, malformed baseline/usage error).
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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


CACHE_SCHEMA = 1
_CACHE_MESSAGE_KEYS = ("line", "col", "category", "text")


def default_cache_dir() -> Path:
    """Per-machine cache directory, outside the repo (state root, else the temp dir)."""
    state_root = os.environ.get("AESOP_STATE_ROOT")
    base = Path(state_root) if state_root else Path(tempfile.gettempdir()) / "aesop"
    return base / "cache" / "pyflakes"


def _cache_file(cache_dir: Path, root: Path) -> Path:
    """One cache file per repo root, so a different --root never reuses another root's results."""
    digest = hashlib.sha256(str(root).encode("utf-8", "replace")).hexdigest()[:24]
    return Path(cache_dir) / f"{digest}.json"


def _cache_tag(pyflakes_module) -> str:
    """Anything that can change a per-file pyflakes result invalidates the whole cache."""
    return "schema=%d;pyflakes=%s;python=%s" % (
        CACHE_SCHEMA,
        getattr(pyflakes_module, "__version__", "?"),
        ".".join(str(part) for part in sys.version_info[:3]),
    )


def _entry_key(rel_path: str, data: bytes) -> str:
    """Content key: the path as pyflakes sees it (basename matters) plus the file bytes."""
    digest = hashlib.sha256()
    digest.update(rel_path.encode("utf-8", "replace"))
    digest.update(b"\0")
    digest.update(data)
    return digest.hexdigest()


def _valid_entry(entry) -> bool:
    if not isinstance(entry, list):
        return False
    for msg in entry:
        if not isinstance(msg, dict) or any(k not in msg for k in _CACHE_MESSAGE_KEYS):
            return False
        if not isinstance(msg["line"], int) or not isinstance(msg["col"], int):
            return False
    return True


def _load_cache(cache_file: Path, tag: str) -> Tuple[Optional[dict], str]:
    """Return (entries, reason). entries is None when the cache cannot be used (full scan)."""
    if not cache_file.exists():
        return None, "cold cache"
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "cache unreadable"
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return None, "cache unreadable"
    if data.get("tag") != tag:
        return None, "cache version mismatch"
    return data["entries"], ""


def _save_cache(cache_file: Path, tag: str, entries: Dict[str, list]) -> None:
    """Atomically replace the cache with the entries seen this run. Failure only costs speed."""
    tmp_name = None
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(cache_file.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"tag": tag, "entries": entries}, sort_keys=True))
        os.replace(tmp_name, cache_file)
    except OSError:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def run_pyflakes(root: Path, paths: List[str], cache_file: Optional[Path] = None,
                 forced_reason: str = "") -> Tuple[List[dict], List[str], bool, dict]:
    """Run pyflakes over the given repo-relative paths, reusing cached per-file results.

    pyflakes analyses one file at a time, so a file's messages depend only on its
    path and bytes. Each file is keyed by (path, sha256 of bytes); a hit reuses the
    recorded messages, a miss runs pyflakes on that file alone. Every file in the
    scan set is still enumerated and read, so the finding set is the same as a full
    scan. cache_file=None (or a non-empty forced_reason) makes it a full scan.

    Returns (findings, errors, pyflakes_available, scan). Each finding is a dict with
    file (repo-relative, forward slashes), line, category (message class name),
    message (str(warning)). scan reports mode (full|incremental), reason, scanned,
    reused.
    """
    try:
        import pyflakes
        import pyflakes.api as api
    except ImportError:
        return [], [], False, {}

    existing = [p for p in paths if (root / p).exists()]
    if not existing:
        return [], [f"no scannable paths found under {root} (looked for {paths})"], True, {}

    tag = _cache_tag(pyflakes)
    old_entries: dict = {}
    reason = forced_reason
    if cache_file is None:
        reason = forced_reason or "cache disabled (--no-cache)"
    elif not forced_reason:
        loaded, load_reason = _load_cache(cache_file, tag)
        if loaded is None:
            reason = load_reason
        else:
            old_entries = loaded

    findings: List[dict] = []
    errors: List[str] = []
    live: Dict[str, list] = {}
    scanned = reused = 0
    cwd = os.getcwd()
    try:
        os.chdir(root)
        for filename in api.iterSourceCode(existing):
            try:
                with open(filename, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                errors.append(f"{filename}: {exc.args[1] if len(exc.args) > 1 else exc}")
                continue
            key = _entry_key(filename, data)
            cached = old_entries.get(key)
            if _valid_entry(cached):
                messages = cached
                reused += 1
            else:
                collector = _Collector()
                api.check(data, filename, collector)
                errors.extend(collector.errors)
                if collector.errors:
                    continue
                messages = [
                    {
                        "line": getattr(w, "lineno", 0),
                        "col": getattr(w, "col", 0),
                        "category": type(w).__name__,
                        "text": w.message % w.message_args,
                    }
                    for w in collector.flakes
                ]
                scanned += 1
            live[key] = messages
            rel = _normalize(os.path.relpath(filename, ".") if os.path.isabs(filename) else filename)
            for msg in messages:
                findings.append({
                    "file": rel,
                    "line": msg["line"],
                    "category": msg["category"],
                    "message": "%s:%s:%s: %s" % (filename, msg["line"], msg["col"] + 1, msg["text"]),
                })
    finally:
        os.chdir(cwd)

    if cache_file is not None and not errors:
        _save_cache(cache_file, tag, live)
    if not reason and reused == 0:
        reason = "no cached entries matched"
    scan = {
        "mode": "incremental" if reused else "full",
        "reason": reason,
        "scanned": scanned,
        "reused": reused,
    }
    findings.sort(key=lambda f: (f["file"], f["line"], f["category"]))
    return findings, errors, True, scan


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


def format_scan_line(scan: dict) -> str:
    """One line saying whether the scan was incremental or a full fallback, and why."""
    if not scan:
        return "pyflakes_gate: scan: none"
    line = "pyflakes_gate: scan %s (scanned %d, reused %d)" % (
        scan["mode"], scan["scanned"], scan["reused"],
    )
    if scan["mode"] == "full" and scan["reason"]:
        line += " -- full scan fallback: %s" % scan["reason"]
    return line


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="Repository root (default: current directory)")
    parser.add_argument("--paths", nargs="+", default=None, help="Override scanned directories/files (default: %s)" % ", ".join(DEFAULT_PATHS))
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE, help=f"Ratchet baseline file (default: {DEFAULT_BASELINE}; pass empty string to disable ratchet mode)")
    parser.add_argument("--trunk-baseline", default=None, help="Pre-push trunk mode: the trunk's baseline. --baseline (the branch copy) must be a burn-down of it (strict subset, scan within it) or the gate fails; additions and missing/malformed baselines fail")
    parser.add_argument("--update-baseline", action="store_true", help="Regenerate --baseline from the current scan (review-only; CI must never pass this)")
    parser.add_argument("--check", action="store_true", help="(default behavior) run the gate check")
    parser.add_argument("--no-cache", action="store_true", help="Full scan: neither read nor write the per-file result cache")
    parser.add_argument("--cache-dir", default=None, help="Per-file result cache directory (default: $AESOP_STATE_ROOT/cache/pyflakes, else the temp dir)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    paths = args.paths if args.paths else DEFAULT_PATHS

    if args.update_baseline and not args.baseline:
        print("pyflakes_gate: --update-baseline requires --baseline FILE", file=sys.stderr)
        return 2

    # The cache only skips re-analysis of unchanged files; it never changes the verdict.
    # --update-baseline and --no-cache always do a full scan; a missing baseline does too.
    cache_file = None
    forced_reason = ""
    if args.update_baseline:
        forced_reason = "full scan requested (--update-baseline)"
    elif args.no_cache:
        cache_file = None
    else:
        cache_file = _cache_file(Path(args.cache_dir) if args.cache_dir else default_cache_dir(), root)
    if args.baseline and not args.update_baseline and not (root / args.baseline).is_file():
        forced_reason = "baseline missing"

    findings, errors, available, scan = run_pyflakes(root, paths, cache_file, forced_reason)

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
                "scan": scan,
                "findings": findings,
            }, indent=2))
        else:
            print(format_scan_line(scan))
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
            "scan": scan,
        }, indent=2))
    else:
        print(format_scan_line(scan))
        if findings:
            print(f"Found {len(findings)} pyflakes finding(s):", file=sys.stderr)
            for i, f in enumerate(findings, 1):
                print(f"{i}. {f['file']}:{f['line']}: [{f['category']}] {f['message']}", file=sys.stderr)
        else:
            print("pyflakes_gate: no findings.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
