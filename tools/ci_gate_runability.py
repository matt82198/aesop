#!/usr/bin/env python3
"""
CI Gate Runability Validator (Guardrail G2.5)
INDEX: CI gate-runability validator (Guardrail G2.5): prevents "green can mean never ran" incidents by verifying known suite families (Python unit suites via ci_shard_runner.py, npm test:node, run_shell_tests.sh, playwright, verify_*.py, lint/guard gates) are not silently skipped due to branch protection misconfiguration; checks job/step-level if conditions that exclude PRs, continue-on-error on gates, every script a gate step invokes (block-scalar and multi-file steps included) exists on disk, and that every required branch-protection check name (tools/required-checks.json, plus an optional captured protection payload) resolves to a job in the workflows (fail closed: a renamed required job is a finding, and an unreadable manifest exits 2); CLI: `[--check] [--json] [--root DIR] [--workflows DIR] [--required-checks PATH] [--protection-file PATH]`; exit 0=clean/1=findings/2=error; stdlib-only; wired as a blocking step in ci.yml. **Verdicts are location-independent**: the result is a pure function of (workflow content, files present under `--root`) and must never branch on the shape of the root path -- a sniff for OS temp-directory name fragments in the root path silently disabled the missing-file check for any runner with a temp-based workspace, and survived in a second copy after the first was deleted. Fixtures create the files they reference; the gate never recognises its own fixtures (regression-guarded by `TestCIGateRunabilityLocationIndependence`, incl. a static no-path-sniffing scan of the source)

Verifies that known CI test suite families can actually run and are not silently skipped
due to branch protection misconfiguration. Prevents "green can mean never ran" incidents.

Known suite families:
- pytest / ci_shard_runner.py (Python unit tests)
- npm test:node (Node.js tests)
- run_shell_tests.sh (Shell test suite)
- playwright test (TypeScript component tests)
- verify_*.py gates (Browser proof gates)
- Lint/guard gates (secret_scan, claudemd_sync_gate, etc.)

Checks:
(a) Gate step not under job-level/step-level `if:` that can never fire on PR events
(b) No continue-on-error on a required gate
(c) Every script path a step invokes exists on disk. Runs for EVERY step (not only the
    known suite families), over the whole `run:` text including `run: |` block scalars,
    so a multi-file step fails if any one of its files is missing. Dynamic paths
    (`$VAR` / `${{ }}`) cannot be resolved statically and are skipped.
(d) Every required-check name in tools/required-checks.json (plus any name in an optional
    captured protection payload) matches a job id or `name:` in the workflows. A required
    check with no job can never report, so the PR waits forever (fail closed).

Exit codes:
  0 = clean (all checks pass)
  1 = findings found (but not fatal)
  2 = error (usage/parsing error, unreadable or empty required-checks manifest)
"""

import json
import sys
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling import (sanctioned guard)
from ci_job_contexts import resolve_required_context  # noqa: E402


REQUIRED_CHECKS_MANIFEST = Path("tools") / "required-checks.json"
_BLOCK_SCALAR_RE = re.compile(r"^[|>][-+0-9]*$")
# A script path with at least one directory component (tools/x.py, ./tests/y.sh).
_PATH_TOKEN_RE = re.compile(
    r"(?<![\w$./{-])((?:\.{1,2}/|/)?[\w.\-]+(?:/[\w.\-]+)+\.(?:py|sh|mjs))(?![\w./-])")
# A bare script name invoked directly by an interpreter (python x.py, bash x.sh).
_BARE_INTERP_RE = re.compile(
    r"(?<![\w$./{-])(?:python3?|bash|sh|node)\s+(?:-\S+\s+)*([\w.\-]+\.(?:py|sh|mjs))(?![\w./-])")


class ManifestError(Exception):
    """The required-checks manifest (or protection payload) cannot be used: fail closed."""


def _protection_contexts(path) -> List[str]:
    """Contexts from a captured `gh api .../branches/<b>/protection` payload."""
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


def load_required_contexts(manifest_path, protection_file=None) -> List[str]:
    """Required-check names from the declared manifest, unioned with a protection payload when given."""
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


def _read_block(lines: List[str], i: int, prop_indent: int) -> Tuple[str, int]:
    """Consume a block-scalar (or plain multi-line) value whose body is indented past prop_indent."""
    body = []
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            body.append("")
            i += 1
            continue
        if len(raw) - len(raw.lstrip()) <= prop_indent:
            break
        body.append(raw.strip())
        i += 1
    return "\n".join(body).rstrip("\n"), i


def _apply_step_prop(step: Dict, text: str, prop_indent: int, lines: List[str], i: int) -> int:
    """Apply one `key: value` step property; return the index of the next unconsumed line."""
    key, sep, val = text.partition(":")
    if not sep:
        return i
    key = key.strip()
    val = val.strip()
    if key == "run":
        if val == "" or _BLOCK_SCALAR_RE.match(val):
            step["run"], i = _read_block(lines, i, prop_indent)
        else:
            step["run"] = val
    elif key == "name":
        step["name"] = val
    elif key == "if":
        step["if_condition"] = val or None
    elif key == "continue-on-error":
        step["continue_on_error"] = val.lower() == "true"
    elif key == "uses":
        step["uses"] = val
    return i


def _flow_item(text: str) -> str:
    item = text.strip()
    if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
        item = item[1:-1]
    return item


def _matrix_from_lines(body: List[str]):
    """strategy.matrix of one job body: None (absent), a str (expression), or {dim: values}.

    Values are the literal list items; a scalar or empty block is kept as-is so the
    resolver rejects it (fail closed). include/exclude keys are kept so they are rejected too.
    """
    matrix = None
    strategy = False
    in_matrix = False
    dim = None
    dim_indent = None
    for raw in body:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.split(" #")[0].strip()
        if indent <= 4:
            strategy = indent == 4 and key == "strategy"
            in_matrix = False
            dim = None
            continue
        if not strategy:
            continue
        if indent <= 6:
            in_matrix = indent == 6 and key == "matrix"
            dim = None
            dim_indent = None
            if in_matrix:
                if val:
                    return val  # inline expression or scalar: unverifiable
                matrix = {}
            continue
        if not in_matrix or matrix is None:
            continue
        if dim_indent is None:
            dim_indent = indent
        if indent == dim_indent:
            dim = key
            if key in ("include", "exclude"):
                matrix[key] = val or None
                dim = None
            elif val.startswith("[") and val.endswith("]"):
                matrix[key] = [_flow_item(x) for x in val[1:-1].split(",") if x.strip()]
            elif val:
                matrix[key] = val
            else:
                matrix[key] = []
            continue
        if dim is not None and indent > dim_indent and stripped.startswith("-"):
            matrix[dim].append(_flow_item(stripped[1:]))
    return matrix


def _attach_matrices(lines: List[str], jobs: Dict) -> None:
    """Record each job's strategy.matrix from its own body lines (between job headers)."""
    headers = sorted((job["line"] - 1, job) for job in jobs.values())
    for pos, (start, job) in enumerate(headers):
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(lines)
        for k in range(start + 1, end):
            raw = lines[k]
            if raw.strip() and not raw.lstrip().startswith("#") and raw == raw.lstrip():
                end = k  # a top-level (indent 0) key ends the jobs mapping
                break
        job["matrix"] = _matrix_from_lines(lines[start + 1:end])


def parse_yaml_line_by_line(content: str) -> Dict:
    """
    Minimal YAML parser for CI workflow structures.
    Extracts job ids, job names, job/step if conditions, step names, run text (including
    `run: |` block scalars and `- run:` on the step marker line) and continue-on-error.
    Only the top-level `jobs:` mapping is read, so `on:` triggers are never mistaken for jobs.
    Not a full YAML parser, just targeted extraction for CI workflows.
    """
    lines = content.split("\n")
    jobs: Dict = {}
    in_jobs = False
    job = None
    in_steps = False
    step = None
    marker_indent = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        lineno = i + 1
        i += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())

        if indent == 0:
            in_jobs = stripped == "jobs:"
            job = None
            in_steps = False
            step = None
            continue
        if not in_jobs:
            continue

        # Job id (two-space indent, opens a mapping).
        if indent == 2 and not stripped.startswith("-") and stripped.endswith(":"):
            job = {"name": None, "if_condition": None, "steps": [], "line": lineno}
            jobs[stripped[:-1].strip()] = job
            in_steps = False
            step = None
            marker_indent = None
            continue
        if job is None:
            continue

        # Job-level keys (four-space indent).
        if indent == 4 and not stripped.startswith("-"):
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            in_steps = key == "steps"
            step = None
            if key == "if":
                job["if_condition"] = val or None
            elif key == "name":
                job["name"] = val or None
            continue

        # Step marker: `- ...` at the steps-list indent.
        if in_steps and stripped.startswith("-") and (marker_indent is None or indent == marker_indent):
            marker_indent = indent
            step = {"name": None, "if_condition": None, "run": None,
                    "continue_on_error": False, "line": lineno, "uses": None}
            job["steps"].append(step)
            rest = stripped[1:].strip()
            if rest:
                i = _apply_step_prop(step, rest, indent + 2, lines, i)
            continue

        # `with: path:` of a step (checkout destination) sits four columns inside the marker.
        if in_steps and step is not None and marker_indent is not None and indent == marker_indent + 4 \
                and stripped.startswith("path:"):
            step["checkout_path"] = stripped.partition(":")[2].strip() or None
            continue

        # Step properties sit exactly two columns inside the marker.
        if in_steps and step is not None and marker_indent is not None and indent == marker_indent + 2:
            i = _apply_step_prop(step, stripped, indent, lines, i)

    _attach_matrices(lines, jobs)
    return jobs


def is_condition_always_false_for_pr(condition: Optional[str]) -> bool:
    """
    Check if an if: condition can never fire on PR events.
    Examples:
    - "github.event_name == 'push'" -> True (never fires on PR)
    - "github.event_name == 'pull_request'" -> False (always fires on PR)
    - "matrix.python-shard == 0 && github.event_name == 'pull_request'" -> False
    - "matrix.python-shard == 0" -> False (fires on PR)
    """
    if not condition:
        return False

    # Check for explicit push-only conditions
    if "github.event_name == 'push'" in condition and "pull_request" not in condition:
        return True
    if 'github.event_name' in condition and 'pull_request' not in condition and 'push' in condition:
        if "== 'push'" in condition:
            return True

    return False


def file_exists(repo_root: str, file_path: str) -> bool:
    """Check if a file exists, relative to repo root."""
    # Handle paths that might be shell variables or special syntax
    if '$' in file_path or '{' in file_path:
        return True  # Can't verify dynamic paths

    abs_path = Path(repo_root) / file_path
    return abs_path.exists()


def extract_run_commands(run_text: str) -> List[str]:
    """Extract individual commands from a run block (which may have pipes, &&, etc.)."""
    commands = []

    # Split by || and &&
    parts = re.split(r'[|&]{2}|\n(?!\s)', run_text)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Extract the main command (first word after redirects)
        match = re.match(r'(?:^|[\s;])(python|npm|node|bash|sh|git|npx|echo|test|if|for|while)\s+', part)
        if match:
            commands.append(match.group(1))

        # Also check for direct file paths like ./script.sh
        if part.startswith('./') or part.startswith('/'):
            cmd = part.split()[0]
            commands.append(cmd)

    return commands


def get_suite_family(run_text: Optional[str]) -> Optional[str]:
    """Identify which test suite family a run block invokes."""
    if not run_text:
        return None

    # Check for each known family
    if 'pytest' in run_text or 'ci_shard_runner.py' in run_text:
        return 'pytest'
    if 'npm run test:node' in run_text or 'node --test' in run_text:
        return 'npm_test_node'
    if 'run_shell_tests.sh' in run_text or 'npm run test:sh' in run_text:
        return 'shell_tests'
    if 'playwright test' in run_text or 'npx playwright' in run_text:
        return 'playwright'
    if 'verify_' in run_text and '.py' in run_text:
        match = re.search(r'(verify_\w+\.py)', run_text)
        if match:
            return f'verify_{match.group(1)}'
    if 'secret_scan.py' in run_text:
        return 'secret_scan'
    if 'claudemd_sync_gate.py' in run_text:
        return 'claudemd_sync_gate'
    if 'claudemd_lint.py' in run_text:
        return 'claudemd_lint'
    if 'watcher_linter.py' in run_text:
        return 'watcher_linter'
    if 'spec_contract_validator.py' in run_text:
        return 'spec_contract_validator'
    if 'subprocess_guard.py' in run_text:
        return 'subprocess_guard'

    return None


def strip_shell_comments(run_text: str) -> str:
    """Drop shell comments so a path named in a comment is not treated as invoked."""
    kept = []
    for line in run_text.split("\n"):
        if line.strip().startswith("#"):
            continue
        kept.append(re.sub(r"(^|\s)#.*$", "", line))
    return "\n".join(kept)


def extract_script_paths(run_text: str) -> List[str]:
    """Every script path a run block invokes, in order, de-duplicated.

    Matches directory-qualified paths anywhere in the text and bare script names
    invoked directly by an interpreter. Dynamic references (`$VAR`, `${{ }}`) do
    not match and are skipped (they cannot be resolved statically).
    """
    found = []
    for match in _PATH_TOKEN_RE.finditer(run_text):
        found.append((match.start(1), match.group(1)))
    for match in _BARE_INTERP_RE.finditer(run_text):
        found.append((match.start(1), match.group(1)))
    found.sort(key=lambda item: item[0])
    return list(dict.fromkeys(token for _, token in found))


def find_file_on_disk(repo_root: str, pattern: str) -> bool:
    """Check if a file matching the pattern exists."""
    if pattern.startswith('./'):
        pattern = pattern[2:]
    if pattern.startswith('/'):
        pattern = pattern.lstrip('/')

    abs_path = Path(repo_root) / pattern

    # Direct check. There is deliberately no fallback for temp-shaped or fixture
    # roots: a gate must not recognise its own test fixtures; fixtures create the
    # files they reference (see TestCIGateRunabilityLocationIndependence).
    return abs_path.exists()


def _strip_checkout_root(file_path: str, checkout_roots) -> str:
    """Map `<checkout path>/x` to `x` when a checkout step of the same job lands there."""
    for root in checkout_roots:
        prefix = root.strip("/") + "/"
        if prefix != "/" and file_path.startswith(prefix):
            return file_path[len(prefix):]
    return file_path


def check_jobs(repo_root: str, workflow_path: str, jobs: Dict) -> List[str]:
    """Checks (a), (b) and (c) over one parsed workflow."""
    findings = []

    for job_name, job_info in jobs.items():
        # Check job-level if conditions
        if is_condition_always_false_for_pr(job_info['if_condition']):
            findings.append(
                f"{workflow_path}:{job_info['line']}: "
                f"Job '{job_name}' has PR-excluding if condition: {job_info['if_condition']}"
            )

        # Destinations of this job's checkout steps (`with: path:`). A file under one is
        # the checked-out tree of another ref (the verify-receipt job's `base/` is origin/main);
        # it is resolved against the repo tree, which is that same ref for the gate's own checkout.
        checkout_roots = [s["checkout_path"] for s in job_info['steps'] if s.get("checkout_path")]

        for step in job_info['steps']:
            step_name = step['name'] or '(unnamed)'
            run_text = step['run'] or ''

            # Check (c): every script the step invokes exists. Runs for every step,
            # over the whole run text, so a multi-file step cannot hide a missing file.
            for file_path in extract_script_paths(strip_shell_comments(run_text)):
                if not find_file_on_disk(repo_root, _strip_checkout_root(file_path, checkout_roots)):
                    findings.append(
                        f"{workflow_path}:{step['line']}: "
                        f"Step '{step_name}' references missing file: {file_path}"
                    )

            suite_family = get_suite_family(step['run'])
            if not suite_family:
                continue

            # Check (a): Step not under if condition that excludes PRs
            if is_condition_always_false_for_pr(step['if_condition']):
                findings.append(
                    f"{workflow_path}:{step['line']}: "
                    f"Step '{step_name}' ({suite_family}) has PR-excluding if condition: {step['if_condition']}"
                )

            # Check (b): No continue-on-error on required gates
            if step['continue_on_error'] and suite_family not in ['ps1_syntax_check']:
                findings.append(
                    f"{workflow_path}:{step['line']}: "
                    f"Step '{step_name}' ({suite_family}) has continue-on-error: true"
                )

    return findings


def check_workflow(repo_root: str, workflow_path: str) -> Tuple[int, List[str]]:
    """
    Check a single workflow file for gate-runability issues (checks a, b, c).
    Returns (exit_code, list of findings).
    """
    try:
        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return (2, [f"Error reading {workflow_path}: {e}"])

    findings = check_jobs(repo_root, workflow_path, parse_yaml_line_by_line(content))
    return (1 if findings else 0, findings)


def check_required_jobs(required_contexts: List[str], jobs_by_workflow: Dict[str, Dict]) -> List[str]:
    """Check (d): every required-check name must be the rendered check name of a job (fail closed).

    Resolution is shared with tools/ci_needs_skip_guard.py via tools/ci_job_contexts.py: a
    `job (N)` context needs a job whose rendered name (id, or name: override with matrix
    substitution) equals it, and N must be a literal matrix value.
    """
    findings = []
    for context in required_contexts:
        reasons = []
        for jobs in jobs_by_workflow.values():
            job_id, why = resolve_required_context(context, jobs)
            if job_id is not None:
                break
            reasons.append(why)
        else:
            reason = next((r for r in reasons if not r.startswith("no job renders")),
                          reasons[0] if reasons else "no workflow defines any job")
            findings.append(
                f"required check '{context}' has no job rendering it in .github/workflows: {reason}. "
                f"A required check that no job renders can never be satisfied, so the PR waits "
                f"forever (renamed job, name: override, or matrix suffix out of range? "
                f"update tools/required-checks.json or the workflow)"
            )
    return findings


def main():
    parser = argparse.ArgumentParser(
        description='CI Gate Runability Validator: ensures test suites can actually run on PR events'
    )
    parser.add_argument('--check', action='store_true', default=True,
                        help='Check mode (default)')
    parser.add_argument('--json', action='store_true',
                        help='Output findings as JSON')
    parser.add_argument('--workflows', type=str, default='.github/workflows',
                        help='Path to workflows directory')
    parser.add_argument('--root', type=str, default='.',
                        help='Repository root directory')
    parser.add_argument('--required-checks', type=str, default=None,
                        help='Required-checks manifest (default: <root>/tools/required-checks.json)')
    parser.add_argument('--protection-file', type=str, default=None,
                        help='Optional captured branch-protection payload; its contexts are also required')

    args = parser.parse_args()

    repo_root = Path(args.root).resolve()
    workflows_dir = repo_root / args.workflows
    manifest = Path(args.required_checks) if args.required_checks else repo_root / REQUIRED_CHECKS_MANIFEST

    if not workflows_dir.exists():
        print(f"Error: Workflows directory not found: {workflows_dir}", file=sys.stderr)
        return 2

    try:
        required = load_required_contexts(manifest, args.protection_file)
    except ManifestError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    all_findings = []
    exit_code = 0
    jobs_by_workflow = {}

    for workflow_file in sorted(workflows_dir.glob('*.yml')):
        try:
            content = workflow_file.read_text(encoding='utf-8')
        except Exception as e:
            print(f"Error: cannot read {workflow_file}: {e}", file=sys.stderr)
            return 2
        jobs = parse_yaml_line_by_line(content)
        jobs_by_workflow[str(workflow_file)] = jobs
        all_findings.extend(check_jobs(str(repo_root), str(workflow_file), jobs))

    all_findings.extend(check_required_jobs(required, jobs_by_workflow))
    if all_findings:
        exit_code = 1

    if args.json:
        output = {
            'status': 'clean' if exit_code == 0 else 'findings',
            'exit_code': exit_code,
            'findings': all_findings,
        }
        print(json.dumps(output, indent=2))
    else:
        for finding in all_findings:
            print(finding)
        if not all_findings and exit_code == 0:
            print("[OK] All CI gates are runnable on PR events")

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
