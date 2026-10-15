#!/usr/bin/env python3
"""Needs-skip-cascade guard for CI workflows.
INDEX: Needs-skip-cascade guard: for every required branch-protection check (tools/required-checks.json; a required name with no job in ci.yml is itself a finding, fail closed), walks its `needs:` ancestry in .github/workflows/ci.yml and fails if any job in that lineage has `needs:` but no `if:` containing always()/cancelled()/failure() -- GitHub Actions implicitly prepends `success() &&` to a job's own scheduling `if:` whenever it is omitted or doesn't name a status function, so a needs-parent that concludes anything but success (a real failure, a cancellation from runner/concurrency starvation, a timeout) makes the job report `skipped` instead of running. A skipped required check can never satisfy branch protection (deadlock, PR #170 2026-07-17); a skipped NON-required job upstream of a required aggregator (e.g. windows-shard feeding windows) can instead manufacture a false green by making the aggregator treat "never ran" as "correctly skipped". Root-caused 2026-10-05: docs-only-gate cancellations (runner/queue starvation, zero steps ever executed) cascaded `ci (0..3)` to skipped on ~every armed PR for ~8h. Exit 0 clean / 1 findings / 2 cannot evaluate (fail-closed: PyYAML missing, workflow unreadable/unparseable, or no jobs).

CLI: ci_needs_skip_guard.py [--root DIR] [--workflow ci.yml] [--json] [--required-checks PATH] [--protection-file PATH]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling import (sanctioned guard)
from ci_job_contexts import resolve_required_context, split_context  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - soft import for the tools-importable smoke gate
    yaml = None

# Required-check names come from the declared manifest tools/required-checks.json, never a
# hard-coded copy that can drift from branch protection. A required name with no job in the
# workflow is a finding (fail closed): a renamed job would otherwise silently drop out of
# the cascade check. Mirrors tools/ci_gate_runability.load_required_contexts (kept as a
# copy because tools/ modules may not import each other without the sanctioned sys.path guard).
REQUIRED_CHECKS_MANIFEST = Path("tools") / "required-checks.json"


class ManifestError(Exception):
    """The required-checks manifest (or protection payload) cannot be used: fail closed."""


def _protection_contexts(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError("cannot read protection payload %s: %s" % (path, exc))
    rsc = payload.get("required_status_checks") if isinstance(payload, dict) else None
    if not isinstance(rsc, dict):
        raise ManifestError("protection payload %s has no required_status_checks (API error or unprotected branch)" % path)
    names = rsc.get("contexts")
    if not isinstance(names, list):
        names = [c.get("context") for c in rsc.get("checks", []) if isinstance(c, dict)]
    return [n for n in names if isinstance(n, str)]


def load_required_contexts(manifest_path, protection_file=None):
    """Required-check names from the manifest, unioned with a protection payload when given."""
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError("cannot read required-checks manifest %s: %s" % (manifest_path, exc))
    contexts = data.get("required_contexts") if isinstance(data, dict) else None
    if not isinstance(contexts, list) or not contexts or not all(isinstance(c, str) for c in contexts):
        raise ManifestError('manifest %s has an empty or invalid "required_contexts" list' % manifest_path)
    names = list(contexts)
    if protection_file:
        names.extend(_protection_contexts(protection_file))
    return list(dict.fromkeys(names))

_STATUS_FUNCS = ("always(", "cancelled(", "failure(")


class GuardError(Exception):
    """Raised for conditions that must fail closed (exit 2)."""


def _resolver_jobs(jobs):
    """Job bodies in the shape tools/ci_job_contexts.py resolves: rendered name + matrix."""
    out = {}
    for job_id, body in jobs.items():
        body = body if isinstance(body, dict) else {}
        strategy = body.get("strategy")
        matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
        out[job_id] = {"name": body.get("name"), "matrix": matrix}
    return out


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


def check_workflow(workflow_path, required_checks=None, protection_file=None):
    """Return (findings, notes) for one workflow file. Raises GuardError on unparseable input.

    required_checks defaults to the manifest at <repo>/tools/required-checks.json, where
    <repo> is the directory holding .github/ above workflow_path; a missing or unusable
    manifest raises GuardError (exit 2), never a silent empty set.
    """
    workflow_path = Path(workflow_path)
    if yaml is None:
        raise GuardError("PyYAML is required to parse workflows; install it (pip install pyyaml)")
    if required_checks is None:
        try:
            required_checks = load_required_contexts(
                workflow_path.parents[2] / REQUIRED_CHECKS_MANIFEST, protection_file)
        except ManifestError as exc:
            raise GuardError(str(exc))
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
    resolver_jobs = _resolver_jobs(jobs)
    for check in required_checks:
        job_id, why = resolve_required_context(check, resolver_jobs)
        if job_id is None:
            # Fail closed: a required check that no job renders can never report, so the PR
            # waits forever, and the cascade check would otherwise silently verify nothing.
            findings.append({
                "job": split_context(check)[0],
                "feeds_required_check": check,
                "if": None,
                "detail": "required check %r has no job rendering it in %s: %s "
                          "(renamed job, name: override, or matrix suffix out of range? "
                          "update tools/required-checks.json or the workflow)"
                          % (check, workflow_path.name, why),
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
    parser.add_argument("--required-checks", default=None,
                        help="required-checks manifest (default: <root>/tools/required-checks.json)")
    parser.add_argument("--protection-file", default=None,
                        help="optional captured branch-protection payload; its contexts are also required")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    workflow_path = root / ".github" / "workflows" / args.workflow
    required = None
    if args.required_checks:
        try:
            required = load_required_contexts(Path(args.required_checks), args.protection_file)
        except ManifestError as exc:
            sys.stderr.write("ERROR: %s\n" % exc)
            return 2

    try:
        findings, notes = check_workflow(workflow_path, required, args.protection_file)
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
