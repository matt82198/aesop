#!/usr/bin/env python3
"""Needs-skip-cascade guard for CI workflows.
INDEX: Needs-skip-cascade guard: for every required branch-protection check (tools/merge_queue.EXPECTED_REQUIRED_CHECKS), walks its `needs:` ancestry in .github/workflows/ci.yml and fails if any job in that lineage has `needs:` but no `if:` containing always()/cancelled()/failure() -- GitHub Actions implicitly prepends `success() &&` to a job's own scheduling `if:` whenever it is omitted or doesn't name a status function, so a needs-parent that concludes anything but success (a real failure, a cancellation from runner/concurrency starvation, a timeout) makes the job report `skipped` instead of running. A skipped required check can never satisfy branch protection (deadlock, PR #170 2026-07-17); a skipped NON-required job upstream of a required aggregator (e.g. windows-shard feeding windows) can instead manufacture a false green by making the aggregator treat "never ran" as "correctly skipped". Root-caused 2026-10-05: docs-only-gate cancellations (runner/queue starvation, zero steps ever executed) cascaded `ci (0..3)` to skipped on ~every armed PR for ~8h. Exit 0 clean / 1 findings / 2 cannot evaluate (fail-closed: PyYAML missing, workflow unreadable/unparseable, or no jobs).

CLI: ci_needs_skip_guard.py [--root DIR] [--workflow ci.yml] [--json]
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - soft import for the tools-importable smoke gate
    yaml = None

# Mirrors tools/merge_queue.EXPECTED_REQUIRED_CHECKS without importing merge_queue
# (merge_queue.py is agent-only / hook-gated; this guard must stay runnable anywhere,
# including the plain `--check` lint shard that has no business touching that module).
DEFAULT_REQUIRED_CHECKS = ("ci (0)", "ci (1)", "ci (2)", "ci (3)", "windows")

_STATUS_FUNCS = ("always(", "cancelled(", "failure(")
_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


class GuardError(Exception):
    """Raised for conditions that must fail closed (exit 2)."""


def _job_id_for_check(check_name, jobs):
    """Resolve a required-check context name (e.g. 'ci (0)') to a job id."""
    base = _SUFFIX_RE.sub("", check_name).strip()
    if base in jobs:
        return base
    # Fall back to matching a job's `name:` override, if any.
    for job_id, body in jobs.items():
        name = body.get("name") if isinstance(body, dict) else None
        if isinstance(name, str) and name.strip() == base:
            return job_id
    return None


def _needs_list(body):
    needs = body.get("needs") if isinstance(body, dict) else None
    if needs is None:
        return []
    if isinstance(needs, str):
        return [needs]
    if isinstance(needs, list):
        return [str(n) for n in needs]
    return []


def _is_status_guarded(condition):
    """True if this `if:` explicitly names a status function (always/cancelled/failure).

    GitHub Actions implicitly ANDs success() onto any custom `if:` that does not
    itself call one of always()/cancelled()/failure()/success() -- so an `if:`
    that only checks e.g. `needs.x.outputs.y == 'z'` is STILL success()-gated and
    will skip-cascade exactly like having no `if:` at all.
    """
    if condition is None:
        return False
    text = str(condition).lower()
    return any(func in text for func in _STATUS_FUNCS)


def ancestry(job_id, jobs, _seen=None):
    """All job ids job_id transitively needs, including job_id itself."""
    if _seen is None:
        _seen = set()
    if job_id in _seen or job_id not in jobs:
        return _seen
    _seen.add(job_id)
    for parent in _needs_list(jobs[job_id]):
        ancestry(parent, jobs, _seen)
    return _seen


def check_workflow(workflow_path, required_checks=DEFAULT_REQUIRED_CHECKS):
    """Return (findings, notes) for one workflow file. Raises GuardError on unparseable input."""
    if yaml is None:
        raise GuardError("PyYAML is required to parse workflows; install it (pip install pyyaml)")
    try:
        with open(workflow_path, "r", encoding="utf-8") as handle:
            doc = yaml.safe_load(handle.read())
    except OSError as exc:
        raise GuardError("cannot read %s: %s" % (workflow_path, exc))
    except yaml.YAMLError as exc:
        raise GuardError("cannot parse %s: %s" % (workflow_path, exc))
    if not isinstance(doc, dict):
        raise GuardError("%s is not a YAML mapping" % workflow_path)
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise GuardError("%s defines no jobs" % workflow_path)

    findings = []
    notes = []
    checked_lineage = set()
    for check in required_checks:
        job_id = _job_id_for_check(check, jobs)
        if job_id is None:
            notes.append({
                "check": check,
                "detail": "no job in %s matches required check %r (nothing to verify; "
                          "confirm the workflow still defines this job)" % (workflow_path.name, check),
            })
            continue
        for lineage_id in ancestry(job_id, jobs):
            if lineage_id in checked_lineage:
                continue
            checked_lineage.add(lineage_id)
            body = jobs[lineage_id]
            if not isinstance(body, dict) or not _needs_list(body):
                continue  # nothing upstream can cascade a skip onto this job
            condition = body.get("if")
            if not _is_status_guarded(condition):
                findings.append({
                    "job": lineage_id,
                    "feeds_required_check": check,
                    "if": None if condition is None else str(condition),
                    "detail": (
                        "job %r has needs: %s but its if: (%s) does not call "
                        "always()/cancelled()/failure(); GitHub Actions implicitly "
                        "ANDs success() onto it, so a needs-parent that fails, is "
                        "cancelled, or times out will make this job report "
                        "`skipped` and cascade toward required check %r"
                        % (lineage_id, _needs_list(body),
                           "none" if condition is None else condition, check)
                    ),
                })
    return findings, notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--workflow", default="ci.yml",
                        help="workflow file under .github/workflows (default: ci.yml)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    workflow_path = root / ".github" / "workflows" / args.workflow

    try:
        findings, notes = check_workflow(workflow_path)
    except GuardError as exc:
        if args.json:
            sys.stdout.write(json.dumps({"error": str(exc), "exit_code": 2}, indent=2) + "\n")
        else:
            sys.stderr.write("ERROR: %s\n" % exc)
        return 2

    exit_code = 1 if findings else 0
    if args.json:
        sys.stdout.write(json.dumps(
            {"findings": findings, "notes": notes, "exit_code": exit_code},
            indent=2, sort_keys=True) + "\n")
        return exit_code

    if notes:
        for note in notes:
            print("NOTE: %s" % note["detail"])
    if findings:
        print("FINDINGS (%d):" % len(findings))
        for finding in findings:
            print("  [%s] %s" % (finding["job"], finding["detail"]))
    else:
        print("OK: every job feeding a required check is skip-cascade safe")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
