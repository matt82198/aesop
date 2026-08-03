#!/usr/bin/env python3
"""Silent-CI-job detector: find workflow jobs that are DEFINED but never EXECUTE.
INDEX: Silent-CI-job detector: parses one workflow's job/step inventory (name/if/needs/matrix) and accounts it against real run history (`gh run list` + `gh api .../runs/<id>/jobs`, bounded to `--runs` sampled runs) to catch jobs and named steps DEFINED but never EXECUTED (renamed steps, `if:`-false gates, dead `needs:` chains) — the complement to gate-wiring lints, which ask whether a gate is present in YAML rather than whether the YAML job runs. Job-name matching is anchored + matrix-suffix aware so `windows` never absorbs `windows-shard (0)`, and `${{ }}` in a name matches any rendering; step evidence is only counted from green jobs (a failed job truncates its step list). CLI: `[--workflow ci.yml] [--lookback-days 14] [--runs 5] [--root DIR] [--json] [--fixture PATH]` (`--fixture` = offline payloads, hermetic test seam); exit 0=clean / 1=findings / 2=gh-or-parse failure OR zero runs in lookback (fail-closed: no evidence is never a green). Needs PyYAML + gh; NOT yet wired into ci.yml

The failure class this catches is "green can mean never ran": a job (or a named
step) exists in .github/workflows/<wf>, branch protection is happy, CI is green
-- and the job has not actually executed once. Causes seen in the wild: a step
renamed so nothing invokes it any more, a condition that can never be true, and
a `needs:` chain whose parent itself never runs.

How it works:
  1. Parse the workflow YAML into a job + step inventory (names, `if`, `needs`,
     matrix presence).
  2. Sample recent real runs via `gh run list` + `gh api .../runs/<id>/jobs`
     (bounded: one listing call plus at most --runs job calls).
  3. Account per job: executed count, skipped count, never-executed flag, and
     last-executed timestamp.
  4. Report findings for jobs with ZERO executions in the lookback window whose
     condition does not look satisfiable, plus named steps that never appear in
     any successful run of a job that did execute.

Job-name matching is word-boundary safe. API job names are NOT YAML job ids:
matrix jobs expand to `<name> (<value>[, <value>...])` and a `name:` key
overrides the id (and may itself contain `${{ }}` expressions). Matching
anchors both ends and allows only a parenthesised matrix suffix, so `windows`
never absorbs `windows-shard (0)` -- a naive prefix match would report a dead
job as healthy. Names containing `${{ }}` match any rendering of the literal
parts around the expression.

Exit codes (fail-closed -- never exit 0 with no data):
  0  clean
  1  findings
  2  gh failure, parse failure, or no runs available in the lookback window

CLI:
  ci_job_execution_verifier.py [--workflow ci.yml] [--lookback-days 14]
                               [--runs 5] [--root DIR] [--json]
                               [--fixture PATH]

--fixture reads run/job payloads from a JSON file instead of calling gh
(offline mode; used by the hermetic test suite).
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - import stays soft for the smoke gate
    yaml = None

GH_TIMEOUT_SECONDS = 60
DEFAULT_RUNS = 5
DEFAULT_LOOKBACK_DAYS = 14
RUN_LIST_FIELDS = "databaseId,createdAt,status,conclusion,headBranch,event"

# A job/step conclusion that proves the body actually ran.
EXECUTED_CONCLUSIONS = {"success", "failure", "cancelled", "timed_out", "action_required"}

_EXPR = re.compile(r"\$\{\{.*?\}\}")


class VerifierError(Exception):
    """Any condition that must fail closed (exit 2)."""


# ---------------------------------------------------------------------------
# Workflow inventory
# ---------------------------------------------------------------------------

def parse_workflow(path):
    """Parse a workflow file into a job + step inventory.

    Returns a dict: {"path", "workflow_name", "jobs": {job_id: jobdef}} where
    jobdef has keys id, display, if, needs, matrix, steps (list of
    {"name", "if"} for steps whose rendered name is predictable).

    Raises VerifierError if the file is missing, unreadable, or not parseable.
    """
    if yaml is None:
        raise VerifierError(
            "PyYAML is required to parse workflows; install it (pip install pyyaml). "
            "Failing closed rather than reporting an unverified green.")
    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise VerifierError("cannot read workflow %s: %s" % (path, exc))
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise VerifierError("cannot parse workflow %s: %s" % (path, exc))
    if not isinstance(doc, dict):
        raise VerifierError("workflow %s is not a YAML mapping" % path)
    raw_jobs = doc.get("jobs")
    if not isinstance(raw_jobs, dict) or not raw_jobs:
        raise VerifierError("workflow %s defines no jobs" % path)

    jobs = {}
    for job_id, body in raw_jobs.items():
        if not isinstance(body, dict):
            raise VerifierError("workflow %s job %s is not a mapping" % (path, job_id))
        name = body.get("name")
        display = name if isinstance(name, str) and name.strip() else str(job_id)
        needs = body.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        needs = [str(n) for n in needs]
        strategy = body.get("strategy")
        matrix = bool(isinstance(strategy, dict) and strategy.get("matrix"))
        jobs[str(job_id)] = {
            "id": str(job_id),
            "display": display,
            "if": _as_condition(body.get("if")),
            "needs": needs,
            "matrix": matrix,
            "steps": _parse_steps(body.get("steps")),
        }
    return {
        "path": str(path),
        "workflow_name": doc.get("name") if isinstance(doc.get("name"), str) else path.name,
        "jobs": jobs,
    }


def _parse_steps(raw_steps):
    """Collect steps whose rendered API name is predictable.

    Explicitly named steps map to their `name:`. Unnamed `uses:` steps render
    as "Run <uses>". Unnamed `run:` steps are skipped: GitHub derives (and
    truncates) their name from the script, so matching them would be guesswork.
    """
    steps = []
    if not isinstance(raw_steps, list):
        return steps
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        name = step.get("name")
        if isinstance(name, str) and name.strip():
            rendered = name
        elif isinstance(step.get("uses"), str):
            rendered = "Run %s" % step["uses"]
        else:
            continue
        steps.append({"name": rendered, "if": _as_condition(step.get("if"))})
    return steps


def _as_condition(value):
    """Normalise an `if:` value to a string (YAML may hand back a bool)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _is_static_false(condition):
    """True when an `if:` can never be satisfied (a permanently dead gate)."""
    if condition is None:
        return False
    stripped = condition.strip()
    match = _EXPR.fullmatch(stripped)
    if match:
        stripped = stripped[3:-2].strip()
    stripped = stripped.strip("'\"").strip().lower()
    return stripped in ("false", "0")


def _is_dynamic(condition):
    """True when an `if:` depends on run context (event, branch, matrix, ...)."""
    if condition is None:
        return False
    return not _is_static_false(condition)


# ---------------------------------------------------------------------------
# Name matching (YAML identity vs GitHub API rendering)
# ---------------------------------------------------------------------------

def _name_regex(defined_name, allow_matrix_suffix):
    """Compile an anchored regex for a defined job/step name."""
    parts = _EXPR.split(defined_name)
    pattern = ".*".join(re.escape(part) for part in parts)
    if allow_matrix_suffix:
        pattern += r"(?: \(.*\))?"
    return re.compile(r"\A" + pattern + r"\Z")


def job_name_matches(defined_name, api_name):
    """Does an API job name correspond to this defined job?

    Anchored at both ends; only a parenthesised matrix suffix may follow, so
    sibling ids that share a prefix (windows / windows-shard) never collide.
    """
    if not isinstance(api_name, str):
        return False
    return bool(_name_regex(defined_name, True).match(api_name))


def step_name_matches(defined_name, api_name):
    """Does an API step name correspond to this defined step? Exact, not prefix."""
    if not isinstance(api_name, str):
        return False
    return bool(_name_regex(defined_name, False).match(api_name))


# ---------------------------------------------------------------------------
# gh seam
# ---------------------------------------------------------------------------

def _run_gh(args, root, timeout=GH_TIMEOUT_SECONDS):
    """Run a gh command inside the repo root and return the CompletedProcess."""
    return subprocess.run(
        args,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def _gh_json(args, root, what):
    """Run gh and decode JSON stdout, failing closed on any error."""
    try:
        proc = _run_gh(args, root)
    except FileNotFoundError:
        raise VerifierError("gh CLI not found on PATH; cannot verify %s" % what)
    except subprocess.TimeoutExpired:
        raise VerifierError("gh timed out fetching %s" % what)
    except OSError as exc:
        raise VerifierError("gh failed fetching %s: %s" % (what, exc))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise VerifierError("gh exited %d fetching %s: %s"
                            % (proc.returncode, what,
                               detail[0] if detail else "no output"))
    try:
        return json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise VerifierError("gh returned unparseable JSON for %s: %s" % (what, exc))


def fetch_runs(workflow, limit=DEFAULT_RUNS, root="."):
    """List recent workflow runs (newest first) via `gh run list`."""
    data = _gh_json(
        ["gh", "run", "list", "--workflow", workflow,
         "--limit", str(max(limit * 4, limit)), "--json", RUN_LIST_FIELDS],
        root, "run list for %s" % workflow)
    if not isinstance(data, list):
        raise VerifierError("gh run list returned %s, expected a list"
                            % type(data).__name__)
    return data


def fetch_run_jobs(run_id, root="."):
    """Fetch the job records (with steps) for one run via the REST API."""
    # per_page=100 instead of --paginate: this endpoint returns an OBJECT, and
    # gh --paginate would emit one JSON object per page (an unparseable stream).
    data = _gh_json(
        ["gh", "api",
         "repos/{owner}/{repo}/actions/runs/%s/jobs?per_page=100" % run_id],
        root, "jobs for run %s" % run_id)
    if isinstance(data, dict):
        jobs = data.get("jobs")
    else:
        jobs = data
    if not isinstance(jobs, list):
        raise VerifierError("jobs payload for run %s has no job list" % run_id)
    return jobs


def load_fixture(path):
    """Load offline run/job payloads from a JSON file (test/offline mode)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise VerifierError("cannot read fixture %s: %s" % (path, exc))
    except ValueError as exc:
        raise VerifierError("cannot parse fixture %s: %s" % (path, exc))
    runs = data.get("runs") if isinstance(data, dict) else data
    if not isinstance(runs, list):
        raise VerifierError("fixture %s has no 'runs' list" % path)
    return runs


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _parse_timestamp(value):
    """Parse a GitHub ISO-8601 UTC timestamp; None when absent/unparseable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_lookback(run, cutoff):
    created = _parse_timestamp(run.get("createdAt") or run.get("created_at"))
    return created is not None and created >= cutoff


def analyze(inventory, runs, lookback_days=DEFAULT_LOOKBACK_DAYS, now=None):
    """Account defined jobs/steps against sampled run history.

    `runs` are run records each carrying a "jobs" list of API job records.
    Raises VerifierError when no run falls inside the lookback window: with no
    evidence the only honest answer is "unverified", never a vacuous green.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    sampled = [r for r in runs if _in_lookback(r, cutoff)
               and str(r.get("status", "completed")) == "completed"]
    if not sampled:
        raise VerifierError(
            "no completed runs of %s within the last %d days; cannot verify job "
            "execution (failing closed rather than reporting a vacuous green)"
            % (inventory.get("path", "workflow"), lookback_days))

    jobs = inventory["jobs"]
    reports = {}
    for job_id, jobdef in jobs.items():
        reports[job_id] = {
            "job_id": job_id,
            "display": jobdef["display"],
            "matrix": jobdef["matrix"],
            "if": jobdef["if"],
            "needs": list(jobdef["needs"]),
            "executed": 0,
            "skipped": 0,
            "never_executed": True,
            "last_executed": None,
            "matched_api_names": [],
        }

    # step_id -> set of API step names seen in successful runs of that job
    seen_steps = {job_id: set() for job_id in jobs}
    step_evidence = {job_id: False for job_id in jobs}
    unmatched_api_names = set()

    for run in sampled:
        for api_job in run.get("jobs") or []:
            api_name = api_job.get("name")
            conclusion = str(api_job.get("conclusion") or "").lower()
            owner = None
            for job_id, jobdef in jobs.items():
                if job_name_matches(jobdef["display"], api_name):
                    owner = job_id
                    break
            if owner is None:
                if isinstance(api_name, str):
                    unmatched_api_names.add(api_name)
                continue
            report = reports[owner]
            if api_name not in report["matched_api_names"]:
                report["matched_api_names"].append(api_name)
            if conclusion == "skipped":
                report["skipped"] += 1
                continue
            if conclusion not in EXECUTED_CONCLUSIONS:
                continue
            report["executed"] += 1
            report["never_executed"] = False
            stamp = api_job.get("started_at") or api_job.get("completed_at")
            parsed = _parse_timestamp(stamp)
            previous = _parse_timestamp(report["last_executed"])
            if parsed is not None and (previous is None or parsed > previous):
                report["last_executed"] = stamp
            # Step-level evidence is only trustworthy for a job that finished
            # green: a failed or cancelled job truncates its step list, and an
            # empty list means the API returned no step data at all.
            api_steps = api_job.get("steps") or []
            if conclusion == "success" and api_steps:
                step_evidence[owner] = True
                for api_step in api_steps:
                    if isinstance(api_step, dict) and isinstance(api_step.get("name"), str):
                        seen_steps[owner].add(api_step["name"])

    findings = []
    notes = []
    for job_id in sorted(jobs):
        jobdef = jobs[job_id]
        report = reports[job_id]
        if report["never_executed"]:
            if _is_static_false(jobdef["if"]):
                findings.append(_finding(
                    "JOB_DEAD_CONDITION", job_id,
                    "job never executed in %d sampled run(s) and its condition "
                    "(if: %s) can never be satisfied" % (len(sampled), jobdef["if"])))
            elif any(reports.get(parent, {}).get("never_executed", False)
                     for parent in jobdef["needs"]):
                dead_parents = [p for p in jobdef["needs"]
                                if reports.get(p, {}).get("never_executed", False)]
                findings.append(_finding(
                    "JOB_DEAD_NEEDS_CHAIN", job_id,
                    "job never executed; needs-parent(s) %s never executed either"
                    % ", ".join(sorted(dead_parents))))
            elif not _is_dynamic(jobdef["if"]):
                findings.append(_finding(
                    "JOB_NEVER_EXECUTED_UNCONDITIONAL", job_id,
                    "job is unconditional but did not execute in any of %d "
                    "sampled run(s) in the last %d days"
                    % (len(sampled), lookback_days)))
            else:
                notes.append(_finding(
                    "JOB_NEVER_EXECUTED_CONDITIONAL", job_id,
                    "job never executed in the lookback window, but its condition "
                    "(if: %s) may simply not have been met" % jobdef["if"],
                    severity="info"))
            continue
        if not step_evidence[job_id]:
            continue
        for step in jobdef["steps"]:
            if any(step_name_matches(step["name"], seen)
                   for seen in seen_steps[job_id]):
                continue
            if _is_dynamic(step["if"]):
                notes.append(_finding(
                    "STEP_NEVER_EXECUTED_CONDITIONAL", job_id,
                    "step never appeared in any successful run; its condition "
                    "(if: %s) may not have been met" % step["if"],
                    step=step["name"], severity="info"))
                continue
            findings.append(_finding(
                "STEP_NEVER_EXECUTED", job_id,
                "step is defined but never appeared in any successful run of "
                "this job (renamed, removed from the script, or unreachable)",
                step=step["name"]))

    ordered = [reports[job_id] for job_id in sorted(reports)]
    return {
        "workflow": inventory.get("path"),
        "workflow_name": inventory.get("workflow_name"),
        "lookback_days": lookback_days,
        "runs_sampled": len(sampled),
        "run_ids": [r.get("databaseId") or r.get("id") for r in sampled],
        "jobs": ordered,
        "jobs_by_id": reports,
        "unmatched_api_jobs": sorted(unmatched_api_names),
        "findings": findings,
        "notes": notes,
    }


def _finding(kind, job, detail, step=None, severity="finding"):
    return {"kind": kind, "job": job, "step": step,
            "severity": severity, "detail": detail}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_text(report):
    """Render an ASCII report."""
    lines = []
    lines.append("CI job execution verifier -- %s (%s)"
                 % (report["workflow_name"], report["workflow"]))
    lines.append("sampled %d run(s) over the last %d day(s)"
                 % (report["runs_sampled"], report["lookback_days"]))
    lines.append("")
    lines.append("%-28s %8s %8s %-22s" % ("JOB", "EXECUTED", "SKIPPED", "LAST EXECUTED"))
    for job in report["jobs"]:
        lines.append("%-28s %8d %8d %-22s"
                     % (job["job_id"][:28], job["executed"], job["skipped"],
                        job["last_executed"] or "NEVER"))
    if report["unmatched_api_jobs"]:
        lines.append("")
        lines.append("API job names with no defined job (stale run history):")
        for name in report["unmatched_api_jobs"]:
            lines.append("  - %s" % name)
    if report["notes"]:
        lines.append("")
        lines.append("NOTES (%d, not failures):" % len(report["notes"]))
        for note in report["notes"]:
            lines.append("  [%s] %s%s: %s"
                         % (note["kind"], note["job"],
                            " > " + note["step"] if note["step"] else "",
                            note["detail"]))
    lines.append("")
    if report["findings"]:
        lines.append("FINDINGS (%d):" % len(report["findings"]))
        for finding in report["findings"]:
            lines.append("  [%s] %s%s"
                         % (finding["kind"], finding["job"],
                            " > " + finding["step"] if finding["step"] else ""))
            lines.append("      %s" % finding["detail"])
    else:
        lines.append("CLEAN: every defined job executed in the lookback window.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Detect workflow jobs/steps that are defined but never execute.")
    parser.add_argument("--workflow", default="ci.yml",
                        help="workflow file name under .github/workflows (default: ci.yml)")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                        help="only consider runs newer than this (default: 14)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                        help="max runs to sample; bounds gh API calls (default: 5)")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--fixture",
                        help="read run/job payloads from a JSON file instead of gh "
                             "(offline mode; used by the test suite)")
    return parser


def collect_runs(args, root):
    """Gather run records with their jobs attached, from gh or a fixture."""
    if args.fixture:
        return load_fixture(args.fixture)
    if args.runs < 1:
        raise VerifierError("--runs must be >= 1")
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.lookback_days)
    listing = [r for r in fetch_runs(args.workflow, limit=args.runs, root=root)
               if _in_lookback(r, cutoff)
               and str(r.get("status", "")) == "completed"]
    runs = []
    for run in listing[:args.runs]:
        run_id = run.get("databaseId")
        if run_id is None:
            continue
        enriched = dict(run)
        enriched["jobs"] = fetch_run_jobs(run_id, root=root)
        runs.append(enriched)
    if not runs:
        raise VerifierError(
            "gh returned no completed runs of %s in the last %d days; cannot "
            "verify (failing closed)" % (args.workflow, args.lookback_days))
    return runs


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    workflow_path = root / ".github" / "workflows" / args.workflow
    try:
        inventory = parse_workflow(workflow_path)
        runs = collect_runs(args, root)
        report = analyze(inventory, runs,
                         lookback_days=args.lookback_days,
                         now=datetime.now(timezone.utc))
    except VerifierError as exc:
        if args.json:
            sys.stdout.write(json.dumps(
                {"error": str(exc), "exit_code": 2}, indent=2) + "\n")
        else:
            sys.stderr.write("ERROR: %s\n" % exc)
        return 2

    exit_code = 1 if report["findings"] else 0
    if args.json:
        payload = {key: value for key, value in report.items() if key != "jobs_by_id"}
        payload["exit_code"] = exit_code
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_text(report) + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
