#!/usr/bin/env python3
"""
Dead-baseline liveness check (GUARDRAIL #3 / STATE.md NEXT STEPS item 7).
INDEX: Dead-baseline liveness check: for every `.*-baseline.json` ratchet baseline at the repo root (and tools/), finds its consumer tool (hardcoded path inside tools/*.py, or wired via `--baseline` in .github/workflows/*.yml or hooks/*.sh); a baseline with NO consumer is DEAD (fail-closed); for a baseline with a live consumer, re-invokes that consumer's own `--baseline FILE --json` ratchet check and surfaces any STALE entries it names (a finding no longer present -- an inflated allowance that lets a NEW violation hide under it); `--prune` rewrites a baseline to drop exactly its stale entries (list-style: remove the entry; count-style: shrink to the current count, or drop if zero) -- ratchet only ever shrinks, never adds; CLI: `[--root DIR] [--prune] [--json]`; exit 0=every baseline has a consumer and zero stale entries / 1=dead baseline(s) or stale entries found / 2=usage error; stdlib-only.

Why this exists
----------------
A ratchet baseline (.encoding-baseline.json, .stateapi-baseline.json, ...) lets an
EXISTING backlog of tolerated findings stay visible without blocking every push,
while any NEW finding still fails closed. Two decay modes defeat that contract
silently:

  1. DEAD baseline -- its consumer tool was removed, renamed, or never wired up, so
     nothing ever reads the file again. It just sits there, inert, documenting a
     situation no gate enforces.
  2. STALE entries -- the finding a baseline entry names was fixed (or its file
     deleted), but the entry itself was never removed. The allowance it represented
     is now free real estate: a brand-new violation can land on that exact
     file/pattern and hide under the old entry's budget instead of failing closed.

This tool answers both questions for every `.*-baseline.json` file, using each
baseline's OWN consumer tool as the oracle for staleness (re-running its real
pattern detector via `--baseline FILE --json`) rather than a naive file-existence
check -- so it also catches a finding that moved lines but not files.

Consumer discovery
-------------------
A baseline's consumer is found two ways (either is sufficient):

  1. A `tools/*.py` file contains the filename as a quoted Python string literal
     immediately after `/` (path-join), `(` (a call argument -- open(/Path(/
     joinpath(), or `=` (assignment) -- i.e. CODE usage. A markdown/docstring
     mention (backtick-quoted prose, or a bare entry in a protected-literals
     list) never matches this and does NOT count as a reader.
  2. An actual `.github/workflows/*.yml` or `hooks/*.sh` SCRIPT (never a .md
     doc) has a line invoking `tools/<tool>.py` together with the baseline's
     filename (the CLI-arg-driven wiring used by portability_check.py /
     subprocess_guard.py).

Zero hits under either strategy means the baseline is DEAD.

Consumer contract (for staleness)
----------------------------------
Any discovered consumer MUST support `<consumer> --baseline FILE --json` and print
a JSON object on stdout containing a `"stale"` or `"stale_entries"` list of the
baseline entries it no longer finds. Every real consumer in this repo
(stateapi_lint.py, portability_check.py, subprocess_guard.py) already implements
this as part of its own bidirectional ratchet check -- this tool only reads it.

Stale entries come in two shapes depending on the baseline's own format:
  - bare key (list-style baselines, e.g. "tools/foo.py@some-id")
  - "key (baseline N, current M)" (count-style baselines) -- M is the entry's
    CURRENT count, used by --prune to shrink (never delete outright unless M==0).

Usage
-----
  python tools/baseline_liveness_check.py [--root DIR] [--json]
  python tools/baseline_liveness_check.py --prune [--root DIR]

Exit codes: 0 = clean, 1 = dead baseline(s) and/or stale entries found, 2 = usage
or environment error.
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_STALE_COUNT_RE = re.compile(r"^(.*) \(baseline (\d+), current (\d+)\)$")
_TOOL_REF_RE = re.compile(r"tools[/\\]([A-Za-z0-9_\-]+\.py)")
_BASELINE_GLOB = "*-baseline.json"


def discover_baseline_files(repo_root):
    """Return sorted Paths of every `*-baseline.json` at repo root and tools/."""
    repo_root = Path(repo_root)
    found = []
    for directory in (repo_root, repo_root / "tools"):
        if not directory.is_dir():
            continue
        for name in os.listdir(str(directory)):
            if fnmatch.fnmatch(name, _BASELINE_GLOB):
                found.append(directory / name)
    return sorted(found, key=lambda p: str(p.relative_to(repo_root)).replace("\\", "/"))


def _real_usage_pattern(filename):
    """Match `filename` as an actual Python string literal immediately after
    `/` (path-join), `(` (a call argument, e.g. open(/Path(/joinpath(), or `=`
    (assignment) -- i.e. CODE usage. A markdown/docstring mention (wrapped in
    backticks, or just prose) never satisfies this, so a filename merely
    documented or listed as a protected literal does not count as a reader."""
    return re.compile(r"[/(=]\s*[\"']" + re.escape(filename) + r"[\"']")


def _is_real_usage_line(line, filename):
    """True if `line` uses `filename` as actual code (path/arg/assignment),
    not a bare list entry or prose mention."""
    if filename not in line:
        return False
    return bool(_real_usage_pattern(filename).search(line))


def find_consumer(baseline_name, repo_root):
    """Return the repo-relative (POSIX-style) path to the tool that reads this
    baseline, or None if no consumer exists (the dead-baseline case)."""
    repo_root = Path(repo_root)
    tools_dir = repo_root / "tools"
    if tools_dir.is_dir():
        for py_file in sorted(tools_dir.glob("*.py")):
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if _is_real_usage_line(line, baseline_name):
                    return str(py_file.relative_to(repo_root)).replace("\\", "/")

    # Wiring-based discovery: an actual CI workflow or pre-push hook SCRIPT
    # (never a .md doc, which only talks about tools, it doesn't invoke them)
    # that invokes `tools/<tool>.py` together with this baseline's filename.
    wiring_files = []
    workflows_dir = repo_root / ".github" / "workflows"
    if workflows_dir.is_dir():
        wiring_files.extend(sorted(workflows_dir.glob("*.yml")))
        wiring_files.extend(sorted(workflows_dir.glob("*.yaml")))
    hooks_dir = repo_root / "hooks"
    if hooks_dir.is_dir():
        wiring_files.extend(sorted(hooks_dir.glob("*.sh")))

    for f in wiring_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if baseline_name in line:
                m = _TOOL_REF_RE.search(line)
                if m:
                    return "tools/" + m.group(1)
    return None


def query_consumer_stale(consumer_rel_path, baseline_path, repo_root):
    """Invoke the consumer's own `--baseline FILE --json` ratchet check.

    Returns (stale_raw, error): stale_raw is the list of stale entry strings
    (possibly empty); error is None on success or a short description of what
    went wrong (consumer missing, non-zero/unparseable output, etc.).
    """
    repo_root = Path(repo_root)
    consumer_path = repo_root / consumer_rel_path
    if not consumer_path.is_file():
        return [], "consumer tool missing: {0}".format(consumer_rel_path)

    cmd = [sys.executable, str(consumer_path), "--baseline", str(baseline_path), "--json"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], "failed to run consumer {0}: {1}".format(consumer_rel_path, exc)

    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return [], "consumer {0} did not emit parseable JSON (exit {1}): {2}".format(
            consumer_rel_path, proc.returncode, proc.stderr.strip()[:300]
        )

    stale = data.get("stale", data.get("stale_entries", []))
    return list(stale), None


def parse_stale_entry(entry):
    """Split a stale-entry string into (key, current_count_or_None)."""
    m = _STALE_COUNT_RE.match(entry)
    if m:
        return m.group(1), int(m.group(3))
    return entry, None


def prune_baseline(baseline_path, parsed_stale):
    """Rewrite baseline_path dropping exactly the stale entries in parsed_stale.

    List-style baselines (`{"violations": [...]}`): the stale key is removed
    outright. Count-style baselines (`{"violations": {key: count}}`): the key's
    count is shrunk to its current count, or removed if that count is zero.
    Never adds a key or raises a count -- the ratchet only ever shrinks.

    Returns a list of human-readable description strings of what changed.
    """
    data = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
    changes = []
    violations = data.get("violations")

    if isinstance(violations, list):
        stale_keys = {key for key, _count in parsed_stale}
        kept = [v for v in violations if v not in stale_keys]
        removed = [v for v in violations if v in stale_keys]
        data["violations"] = kept
        for v in removed:
            changes.append("removed {0}".format(v))
    elif isinstance(violations, dict):
        for key, current_count in parsed_stale:
            if key not in violations:
                continue
            old_count = violations[key]
            if current_count and current_count > 0:
                violations[key] = current_count
                changes.append(
                    "shrank {0} ({1} -> {2})".format(key, old_count, current_count)
                )
            else:
                del violations[key]
                changes.append("removed {0} (was {1})".format(key, old_count))
    else:
        return changes

    baseline_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return changes


def check_repo(repo_root, prune=False):
    """Run the full liveness check. Returns a result dict:
      {
        "dead": [baseline_name, ...],
        "stale": [{"baseline": name, "consumer": path, "entries": [raw, ...]}],
        "errors": [{"baseline": name, "error": msg}],
        "pruned": [{"baseline": name, "changes": [desc, ...]}],
        "ok_baselines": [{"baseline": name, "consumer": path, "entry_count": N}],
      }
    """
    repo_root = Path(repo_root)
    result = {"dead": [], "stale": [], "errors": [], "pruned": [], "ok_baselines": []}

    for baseline_path in discover_baseline_files(repo_root):
        baseline_name = baseline_path.name
        consumer = find_consumer(baseline_name, repo_root)
        if consumer is None:
            result["dead"].append(baseline_name)
            continue

        stale_raw, error = query_consumer_stale(consumer, baseline_path, repo_root)
        if error:
            result["errors"].append({"baseline": baseline_name, "error": error})
            continue

        if stale_raw:
            result["stale"].append(
                {"baseline": baseline_name, "consumer": consumer, "entries": stale_raw}
            )
            if prune:
                parsed = [parse_stale_entry(e) for e in stale_raw]
                changes = prune_baseline(baseline_path, parsed)
                result["pruned"].append({"baseline": baseline_name, "changes": changes})
        else:
            result["ok_baselines"].append({"baseline": baseline_name, "consumer": consumer})

    return result


def _render_text(result, prune):
    lines = []
    if result["dead"]:
        lines.append("DEAD (no consumer found -- fail-closed):")
        for name in result["dead"]:
            lines.append("  {0}".format(name))
        lines.append("")

    if result["errors"]:
        lines.append("ERROR (could not query consumer):")
        for item in result["errors"]:
            lines.append("  {0}: {1}".format(item["baseline"], item["error"]))
        lines.append("")

    if result["stale"]:
        header = "PRUNED stale entries:" if prune else "STALE entries (no longer found by consumer):"
        lines.append(header)
        for item in result["stale"]:
            lines.append(
                "  {0} (consumer: {1})".format(item["baseline"], item["consumer"])
            )
            for entry in item["entries"]:
                lines.append("    {0}".format(entry))
        lines.append("")

    if prune and result["pruned"]:
        lines.append("Prune actions taken:")
        for item in result["pruned"]:
            lines.append("  {0}:".format(item["baseline"]))
            for change in item["changes"]:
                lines.append("    {0}".format(change))
        lines.append("")

    for item in result["ok_baselines"]:
        lines.append(
            "OK: {0} (consumer: {1}, all entries live)".format(
                item["baseline"], item["consumer"]
            )
        )

    remaining_dead = result["dead"]
    remaining_stale = [] if prune else result["stale"]
    if not remaining_dead and not remaining_stale and not result["errors"]:
        lines.append("PASS: every baseline has a consumer and zero stale entries")
    else:
        parts = []
        if remaining_dead:
            parts.append("{0} dead baseline(s)".format(len(remaining_dead)))
        if remaining_stale:
            total_entries = sum(len(i["entries"]) for i in remaining_stale)
            parts.append("{0} stale entry/entries".format(total_entries))
        if result["errors"]:
            parts.append("{0} consumer error(s)".format(len(result["errors"])))
        lines.append("FAIL: " + ", ".join(parts))

    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="baseline_liveness_check.py",
        description="Dead-baseline liveness check (GUARDRAIL #3): every ratchet "
        "baseline must have a live consumer and zero stale entries.",
    )
    parser.add_argument("--root", default=".", help="Repo root (default: cwd)")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Rewrite baselines, dropping exactly their stale entries (never adds).",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    repo_root = Path(args.root).resolve()
    if not repo_root.is_dir():
        print("baseline_liveness_check: --root is not a directory: {0}".format(repo_root), file=sys.stderr)
        return 2

    result = check_repo(repo_root, prune=args.prune)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_render_text(result, args.prune))

    remaining_stale = [] if args.prune else result["stale"]
    ok = not result["dead"] and not remaining_stale and not result["errors"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
