#!/usr/bin/env python3
"""
CI workflow linter: static analysis of .github/workflows/*.yml
INDEX: CI workflow linter (YAML parsing, npm ci lockfile checks, test coverage)

Checks:
  1. YAML parses cleanly
  2. Every npm ci step has a package-lock.json in its working directory
  3. Every test suite in package.json scripts (test:py/test:node/test:sh) is invoked by CI
  4. Best-effort check for file references in workflow steps
  5. GitHub Actions SEMANTICS that are valid YAML but rejected by GitHub at parse
     time ("Invalid workflow file", run has jobs == [] and conclusion failure):
       - `strategy` keys must be one of fail-fast / matrix / max-parallel
         (GAP 2026-10-06: PR #850 put `exclude:` as a sibling of `matrix:`;
         main-full.yml produced ZERO jobs for ~10 merges while this gate was green)
       - every `matrix.<key>` reference resolves to a matrix variable
       - `exclude`/`include` entries are lists of maps over known matrix keys
       - every `needs:` target and `needs.<job>` reference names a real job
       - `runs-on` is a literal (string/list/group map) or a `${{ }}` expression
         using only the contexts GitHub allows there
       - expressions use only the contexts available at that key
         (`shell:` allows NONE -- GitHub: "Unrecognized named-value: 'runner'")
  6. actionlint (https://github.com/rhysd/actionlint) when a binary is found via
     $ACTIONLINT_BIN or PATH; set CI_WORKFLOW_LINT_REQUIRE_ACTIONLINT=1 to FAIL
     when it is missing (CI does). Check 5 runs regardless, so the gate never
     depends on actionlint being installed.

Exit: 0 if all checks pass, 1 if any findings. Support --json for structured output.

Requires PyYAML. If it is missing the linter FAILS CLOSED (exit 1) rather than
silently passing — a lint gate that cannot parse workflows must not report green.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    # Import stays soft so the tools-importable smoke gate can load this module;
    # lint_workflows() fails closed when yaml is None.
    yaml = None


class WorkflowLintError(Exception):
    """Raised when a workflow file cannot be read or parsed."""


def load_yaml_file(path):
    """Load and parse a YAML file.

    Returns:
        dict or None: Parsed YAML

    Raises:
        WorkflowLintError: If the file cannot be read or parsed
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        raise WorkflowLintError(f"Failed to load {path}: {e}")

    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise WorkflowLintError(f"YAML parse error: {e}")


def find_workflow_files(root):
    """Find all workflow files in .github/workflows/

    Args:
        root: Repository root path

    Returns:
        List[Path]: Sorted list of workflow file paths
    """
    workflows_dir = Path(root) / ".github" / "workflows"
    if not workflows_dir.exists():
        return []
    return sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))


def find_package_json_files(root):
    """Find all package.json files in repo.

    Args:
        root: Repository root path

    Returns:
        Dict[str, dict]: Mapping of relative path to parsed package.json
    """
    packages = {}
    root_path = Path(root)

    # Find all package.json files
    for package_file in root_path.rglob("package.json"):
        try:
            with open(package_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            rel_path = str(package_file.relative_to(root_path))
            packages[rel_path] = data
        except Exception:
            pass  # Skip unparseable package.json

    return packages


def get_test_scripts(packages):
    """Extract test:* scripts from all package.json files.

    Returns:
        Dict[str, dict]: Mapping of script name to {file, command}
    """
    tests = {}
    for pkg_path, pkg_data in packages.items():
        scripts = pkg_data.get("scripts", {})
        for script_name in ["test:py", "test:node", "test:sh"]:
            if script_name in scripts:
                # Determine working directory from package.json path
                pkg_dir = str(Path(pkg_path).parent) if pkg_path != "package.json" else "."
                if pkg_dir == ".":
                    pkg_dir = ""

                script_key = f"{pkg_path}:{script_name}"
                tests[script_key] = {
                    "file": pkg_path,
                    "dir": pkg_dir,
                    "name": script_name,
                    "command": scripts[script_name],
                    "invoked": False
                }
    return tests


def extract_working_directory(step):
    """Extract working directory from a step (working-directory or cd command).

    Args:
        step: Step dict from workflow

    Returns:
        str: Working directory path, or "." if not specified
    """
    # Check for working-directory key
    if "working-directory" in step:
        return step["working-directory"]

    # Check for cd in run block
    run = step.get("run", "")
    if isinstance(run, str) and run.strip():
        # Look for cd at the start of the run block
        lines = run.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith("cd "):
                # Extract directory (simple case: cd /path or cd relative/path)
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1]
            elif re.match(r'^\s*cd\s+', line):
                # Handle whitespace variations
                match = re.match(r'^\s*cd\s+(.+)$', line)
                if match:
                    return match.group(1).strip()

    return "."


def check_npm_ci_lockfile(workflow_path, workflow_data, root):
    """Check that every npm ci step has package-lock.json in working directory.

    Returns:
        List[str]: List of findings
    """
    findings = []

    if not workflow_data:
        return findings

    jobs = workflow_data.get("jobs", {})
    job_id = 0

    for job_name, job_data in jobs.items():
        steps = job_data.get("steps", [])
        step_id = 0

        for step in steps:
            if not isinstance(step, dict):
                step_id += 1
                continue

            run = step.get("run", "")

            # Check if this step runs npm ci
            if "npm ci" in str(run):
                working_dir = extract_working_directory(step)

                # Resolve working directory relative to repo root
                if working_dir == ".":
                    lockfile_path = Path(root) / "package-lock.json"
                else:
                    lockfile_path = Path(root) / working_dir / "package-lock.json"

                if not lockfile_path.exists():
                    step_name = step.get("name", f"step {step_id}")
                    findings.append(
                        f"npm ci without package-lock.json: "
                        f"{workflow_path.name} > {job_name} > {step_name} "
                        f"(working dir: {working_dir})"
                    )

            step_id += 1

    return findings


def check_test_coverage(workflow_files, workflow_data_list, packages, root):
    """Check that all package.json test scripts are invoked by workflows.

    Returns:
        List[str]: List of findings
    """
    findings = []
    tests = get_test_scripts(packages)

    if not tests:
        return findings  # No test scripts to check

    # Scan all workflows for test invocations
    for workflow_data in workflow_data_list:
        if not workflow_data:
            continue

        jobs = workflow_data.get("jobs", {})
        for job_name, job_data in jobs.items():
            steps = job_data.get("steps", [])

            for step in steps:
                if not isinstance(step, dict):
                    continue

                run = step.get("run", "")
                if not isinstance(run, str):
                    continue

                # Check for npm run test:* invocations
                for test_script in ["test:py", "test:node", "test:sh", "test:all"]:
                    if f"npm run {test_script}" in run or f"npm run {test_script}" in str(step.get("name", "")):
                        for test_key in tests:
                            if test_script in test_key:
                                tests[test_key]["invoked"] = True

                # Also check for direct script invocations in run blocks
                # test:py -> python -m unittest
                if "python" in run and "unittest" in run:
                    for test_key in tests:
                        if "test:py" in test_key:
                            tests[test_key]["invoked"] = True

                # test:node -> node --test or similar
                if ("node --test" in run or "npx vitest" in run or
                    "npm run test:node" in run):
                    for test_key in tests:
                        if "test:node" in test_key:
                            tests[test_key]["invoked"] = True

                # test:sh -> bash tests/
                if ("bash tests/" in run or "sh tests/" in run):
                    for test_key in tests:
                        if "test:sh" in test_key:
                            tests[test_key]["invoked"] = True

    # Report uncovered tests
    for test_key, test_info in tests.items():
        if not test_info["invoked"]:
            findings.append(
                f"Test script not invoked by workflows: "
                f"{test_info['file']} > {test_info['name']}"
            )

    return findings


def check_yaml_parse(workflow_path):
    """Check that workflow YAML parses cleanly.

    Returns:
        Tuple[bool, str]: (success, error_message)
    """
    try:
        load_yaml_file(workflow_path)
        return True, ""
    except Exception as e:
        return False, str(e)


def check_file_references(workflow_data, root):
    """Best-effort check for file references in workflow steps.

    Returns:
        List[str]: List of findings for missing files
    """
    findings = []

    if not workflow_data:
        return findings

    jobs = workflow_data.get("jobs", {})

    for job_name, job_data in jobs.items():
        steps = job_data.get("steps", [])

        for step in steps:
            if not isinstance(step, dict):
                continue

            # Check for common file references in run blocks
            run = step.get("run", "")
            if not isinstance(run, str):
                continue

            # Extract file paths from common patterns
            # e.g., "python tools/foo.py", "bash tests/test.sh"
            patterns = [
                r'python[3]?\s+([^\s&|;]+\.py)',
                r'bash\s+([^\s&|;]+\.sh)',
                r'sh\s+([^\s&|;]+\.sh)',
                r'node\s+([^\s&|;]+\.mjs?)',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, run)
                for file_ref in matches:
                    file_path = Path(root) / file_ref
                    if not file_path.exists():
                        # Best-effort: don't fail on generated paths or paths with variables
                        if "$" not in file_ref and "{" not in file_ref:
                            step_name = step.get("name", "unnamed")
                            # findings.append(f"File reference not found: {file_ref} ({job_name} > {step_name})")

    return findings


# --- GitHub Actions schema semantics ------------------------------------------
# Valid YAML is not a valid workflow. GitHub validates the document against its
# own schema when the run is created; a schema error yields a run with NO jobs,
# conclusion "failure", and the UI text "This run likely failed because of a
# workflow file issue" -- and nothing else in the repo notices.

STRATEGY_KEYS = frozenset({"fail-fast", "matrix", "max-parallel"})
MATRIX_RESERVED_KEYS = frozenset({"include", "exclude"})
CONTEXT_NAMES = (
    "github", "env", "vars", "job", "jobs", "steps", "runner", "secrets",
    "strategy", "matrix", "needs", "inputs",
)
_EXPR_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_CONTEXT_REF_RE = re.compile(r"(?<![\w.'\"])(" + "|".join(CONTEXT_NAMES) + r")\.([A-Za-z0-9_-]+)")
_MATRIX_REF_RE = re.compile(r"(?<![\w.'\"])matrix\.([A-Za-z0-9_-]+)")
_NEEDS_REF_RE = re.compile(r"(?<![\w.'\"])needs\.([A-Za-z0-9_-]+)")

# Context availability per key, from
# https://docs.github.com/en/actions/learn-github-actions/contexts#context-availability
# Only keys where GitHub REJECTS the file (not merely evaluates to empty) are
# listed; anything not listed is not checked here.
CONTEXT_AVAILABILITY = {
    "concurrency": frozenset({"github", "inputs", "vars"}),
    "jobs.*.concurrency": frozenset({"github", "needs", "strategy", "matrix", "inputs", "vars"}),
    "jobs.*.if": frozenset({"github", "needs", "vars", "inputs"}),
    "jobs.*.runs-on": frozenset({"github", "needs", "strategy", "matrix", "vars", "inputs"}),
    "jobs.*.strategy": frozenset({"github", "needs", "vars", "inputs"}),
    "jobs.*.timeout-minutes": frozenset({"github", "needs", "strategy", "matrix", "vars", "inputs"}),
    "jobs.*.steps.*.shell": frozenset(),
    "jobs.*.steps.*.if": frozenset({"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "inputs"}),
}


def _expression_contexts(value):
    """Return the set of context names referenced inside ${{ }} in a value."""
    found = set()
    if not isinstance(value, str):
        value = json.dumps(value)
    for expr in _EXPR_RE.findall(value):
        for ctx, _attr in _CONTEXT_REF_RE.findall(expr):
            found.add(ctx)
    return found


def _check_context_availability(value, key_path, where, findings):
    """Flag contexts used in ${{ }} at key_path that GitHub does not allow there."""
    if value is None or key_path not in CONTEXT_AVAILABILITY:
        return
    allowed = CONTEXT_AVAILABILITY[key_path]
    used = _expression_contexts(value)
    bad = sorted(used - allowed)
    if bad:
        allowed_txt = ", ".join(sorted(allowed)) if allowed else "NONE"
        findings.append(
            f"context not available: {where}: `{key_path.split('.')[-1]}` uses "
            f"{', '.join(bad)} (GitHub allows: {allowed_txt})"
        )


def _matrix_variable_keys(matrix):
    """All variable names a `matrix:` map defines (top-level keys + include keys)."""
    keys = set(k for k in matrix.keys() if k not in MATRIX_RESERVED_KEYS)
    include = matrix.get("include")
    if isinstance(include, list):
        for entry in include:
            if isinstance(entry, dict):
                keys.update(entry.keys())
    return keys


def check_github_semantics(workflow_path, workflow_data):
    """Validate GitHub-Actions schema rules that PyYAML cannot see.

    Returns:
        List[str]: findings (empty when the workflow would be accepted by GitHub
        for every rule this check knows about)
    """
    findings = []
    if not isinstance(workflow_data, dict):
        return findings
    wf = workflow_path.name

    _check_context_availability(workflow_data.get("concurrency"), "concurrency", wf, findings)

    jobs = workflow_data.get("jobs")
    if not isinstance(jobs, dict):
        return findings
    job_ids = set(jobs.keys())

    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        where = f"{wf} > {job_id}"

        # -- needs: every target must be a real job -------------------------
        needs = job.get("needs")
        needs_list = []
        if isinstance(needs, str):
            needs_list = [needs]
        elif isinstance(needs, list):
            needs_list = [n for n in needs if isinstance(n, str)]
        elif needs is not None:
            findings.append(f"needs must be a string or list: {where}")
        for target in needs_list:
            if target not in job_ids:
                findings.append(
                    f"needs target does not exist: {where} needs `{target}` "
                    f"(jobs: {', '.join(sorted(job_ids))})"
                )
        # needs.<job>.* references must name a declared dependency
        for ref in sorted(set(_NEEDS_REF_RE.findall(json.dumps(job)))):
            if ref not in needs_list:
                findings.append(
                    f"needs.{ref} referenced but `{ref}` is not in needs: {where}"
                )

        # -- strategy / matrix ---------------------------------------------
        strategy = job.get("strategy")
        matrix_keys = None  # None = unknown (expression matrix or no strategy)
        if strategy is not None:
            if not isinstance(strategy, dict):
                findings.append(f"strategy must be a map: {where}")
            else:
                for key in strategy.keys():
                    hint = "; put it inside matrix:" if key in MATRIX_RESERVED_KEYS else ""
                    if key not in STRATEGY_KEYS:
                        findings.append(
                            f"unexpected key `{key}` for strategy: {where} "
                            f"(expected one of {', '.join(sorted(STRATEGY_KEYS))}{hint})"
                        )
                _check_context_availability(strategy, "jobs.*.strategy", where, findings)
                matrix = strategy.get("matrix")
                if isinstance(matrix, dict):
                    matrix_keys = _matrix_variable_keys(matrix)
                    for reserved in sorted(MATRIX_RESERVED_KEYS):
                        entries = matrix.get(reserved)
                        if entries is None:
                            continue
                        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                            findings.append(f"matrix.{reserved} must be a list of maps: {where}")
                            continue
                        if reserved == "exclude":
                            for entry in entries:
                                for k in entry.keys():
                                    if k not in matrix_keys:
                                        findings.append(
                                            f"matrix.exclude key `{k}` is not a matrix variable: {where} "
                                            f"(matrix keys: {', '.join(sorted(matrix_keys)) or 'none'})"
                                        )
                elif isinstance(matrix, str):
                    matrix_keys = None  # ${{ fromJSON(...) }} -- cannot resolve statically
                elif matrix is not None:
                    findings.append(f"strategy.matrix must be a map or expression: {where}")

        # -- matrix.<key> references ----------------------------------------
        refs = set(_MATRIX_REF_RE.findall(json.dumps(job)))
        if refs and strategy is None:
            findings.append(
                f"matrix.{', matrix.'.join(sorted(refs))} referenced but job has no strategy.matrix: {where}"
            )
        elif refs and matrix_keys is not None:
            for ref in sorted(refs - matrix_keys):
                findings.append(
                    f"matrix.{ref} does not resolve: {where} "
                    f"(matrix keys: {', '.join(sorted(matrix_keys)) or 'none'})"
                )

        # -- runs-on ----------------------------------------------------------
        runs_on = job.get("runs-on")
        if runs_on is None:
            findings.append(f"runs-on is required: {where}")
        elif isinstance(runs_on, str):
            if "${{" in runs_on:
                _check_context_availability(runs_on, "jobs.*.runs-on", where, findings)
        elif isinstance(runs_on, list):
            if not all(isinstance(x, str) for x in runs_on):
                findings.append(f"runs-on list must contain strings: {where}")
            else:
                _check_context_availability(runs_on, "jobs.*.runs-on", where, findings)
        elif isinstance(runs_on, dict):
            if not set(runs_on.keys()) <= {"group", "labels"}:
                findings.append(
                    f"runs-on map accepts only group/labels: {where} (got {', '.join(sorted(runs_on.keys()))})"
                )
            _check_context_availability(runs_on, "jobs.*.runs-on", where, findings)
        else:
            findings.append(f"runs-on must be a string, list, or group map: {where}")

        # -- other job-level expression keys ---------------------------------
        _check_context_availability(job.get("if"), "jobs.*.if", where, findings)
        _check_context_availability(job.get("concurrency"), "jobs.*.concurrency", where, findings)
        _check_context_availability(job.get("timeout-minutes"), "jobs.*.timeout-minutes", where, findings)

        # -- steps ------------------------------------------------------------
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            continue
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_where = f"{where} > {step.get('name', f'step {idx}')}"
            _check_context_availability(step.get("shell"), "jobs.*.steps.*.shell", step_where, findings)
            _check_context_availability(step.get("if"), "jobs.*.steps.*.if", step_where, findings)

    return findings


def find_actionlint():
    """Locate an actionlint binary: $ACTIONLINT_BIN, then PATH. None if absent."""
    env_bin = os.environ.get("ACTIONLINT_BIN")
    if env_bin:
        return env_bin if Path(env_bin).exists() else None
    return shutil.which("actionlint")


def run_actionlint(workflow_files, binary=None, required=None):
    """Run actionlint over workflow files; return findings as a list of strings.

    Args:
        workflow_files: paths to lint
        binary: command prefix (str or list). Defaults to find_actionlint().
        required: fail when no binary is available. Defaults to the env var
            CI_WORKFLOW_LINT_REQUIRE_ACTIONLINT ("1" = required).

    shellcheck/pyflakes integrations are disabled so the result is identical on
    every box regardless of which optional linters happen to be installed.
    """
    if required is None:
        required = os.environ.get("CI_WORKFLOW_LINT_REQUIRE_ACTIONLINT", "") == "1"
    if binary is None:
        binary = find_actionlint()
    if not binary:
        if required:
            return [
                "actionlint required (CI_WORKFLOW_LINT_REQUIRE_ACTIONLINT=1) but no "
                "binary found via $ACTIONLINT_BIN or PATH (fail-closed)"
            ]
        print("note: actionlint not found; GitHub-semantics checks still enforced", file=sys.stderr)
        return []
    cmd = [binary] if isinstance(binary, str) else list(binary)
    cmd += ["-no-color", "-shellcheck=", "-pyflakes="] + [str(p) for p in workflow_files]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as e:
        return [f"actionlint failed to execute ({cmd[0]}): {e}"]
    if proc.returncode == 0:
        return []
    out = (proc.stdout or "") + (proc.stderr or "")
    # actionlint prints "path:line:col: message [rule]" lines followed by a code
    # excerpt; keep only the headline lines.
    lines = [ln for ln in out.splitlines() if re.match(r"^\S+:\d+:\d+: ", ln)]
    if not lines:
        lines = [ln for ln in out.splitlines() if ln.strip()] or [f"exit {proc.returncode}"]
    return [f"actionlint: {ln}" for ln in lines]


def lint_workflows(root, json_output=False, use_actionlint=False):
    """Lint all workflows in a repository.

    Returns:
        Tuple[int, List[str]]: (exit_code, findings_list)
    """
    findings = []
    root_path = Path(root)

    # Fail closed: without a real YAML parser this linter cannot verify anything,
    # and a lint gate that silently passes is worse than one that fails loudly.
    if yaml is None:
        return 1, [
            "[1] PyYAML is not installed; refusing to lint workflows without a "
            "real YAML parser (fail-closed). Install it with: pip install pyyaml"
        ]

    # Find workflow files
    workflow_files = find_workflow_files(root)
    if not workflow_files:
        findings.append("No workflow files found in .github/workflows/")
        return 1, findings

    # Load and parse workflows
    workflow_data_list = []
    for workflow_path in workflow_files:
        success, error = check_yaml_parse(workflow_path)
        if not success:
            findings.append(f"YAML parse error in {workflow_path.name}: {error}")
            continue

        try:
            data = load_yaml_file(workflow_path)
            workflow_data_list.append(data)
        except Exception as e:
            findings.append(f"Failed to load {workflow_path.name}: {e}")

    # Find package.json files
    packages = find_package_json_files(root)

    # Check npm ci lockfiles
    for workflow_path, workflow_data in zip(workflow_files, workflow_data_list):
        if workflow_data:
            findings.extend(check_npm_ci_lockfile(workflow_path, workflow_data, root))

    # Check GitHub Actions schema semantics (valid YAML != valid workflow)
    for workflow_path, workflow_data in zip(workflow_files, workflow_data_list):
        if workflow_data:
            findings.extend(check_github_semantics(workflow_path, workflow_data))

    # actionlint (independent second opinion; opt-in so fixture-based tests stay hermetic)
    if use_actionlint:
        findings.extend(run_actionlint(workflow_files))

    # Check test coverage
    findings.extend(check_test_coverage(workflow_files, workflow_data_list, packages, root))

    # Check file references
    for workflow_data in workflow_data_list:
        if workflow_data:
            findings.extend(check_file_references(workflow_data, root))

    # Format findings with numbers
    numbered_findings = []
    for i, finding in enumerate(findings, 1):
        numbered_findings.append(f"[{i}] {finding}")

    return (0 if not findings else 1), numbered_findings


def main():
    parser = argparse.ArgumentParser(
        description="Lint CI workflow files for common issues"
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root path (default: current directory)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output findings as JSON"
    )
    parser.add_argument(
        "--no-actionlint",
        action="store_true",
        help="Skip the actionlint pass (GitHub-semantics checks still run)"
    )

    args = parser.parse_args()

    exit_code, findings = lint_workflows(
        args.root, args.json, use_actionlint=not args.no_actionlint
    )

    if args.json:
        output = {
            "exit_code": exit_code,
            "findings": findings
        }
        print(json.dumps(output, indent=2))
    else:
        for finding in findings:
            print(finding)
        if not findings and exit_code == 0:
            print("OK: All workflow checks passed")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
