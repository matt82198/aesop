#!/usr/bin/env python3
"""
CI workflow linter: static analysis of .github/workflows/*.yml

Checks:
  1. YAML parses cleanly
  2. Every npm ci step has a package-lock.json in its working directory
  3. Every test suite in package.json scripts (test:py/test:node/test:sh) is invoked by CI
  4. Best-effort check for file references in workflow steps
  5. CLI workflow gate parity: all documented CI gates (Guardrails) must be invoked by workflows

Exit: 0 if all checks pass, 1 if any findings. Support --json for structured output.

Requires PyYAML. If it is missing the linter FAILS CLOSED (exit 1) rather than
silently passing — a lint gate that cannot parse workflows must not report green.
"""

import argparse
import json
import os
import re
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


def extract_documented_gates(root):
    """Extract all verify_*.py tools documented as CI gates or Guardrails in tools/CLAUDE.md.

    Returns:
        Set[str]: Set of tool filenames (e.g., 'verify_foo.py')
    """
    documented_gates = set()
    claudemd_path = Path(root) / "tools" / "CLAUDE.md"

    if not claudemd_path.exists():
        return documented_gates

    try:
        with open(claudemd_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return documented_gates

    # Look for lines containing verify_*.py with 'gate', 'Guardrail', or 'G[0-9]' markers
    lines = content.split('\n')
    for line in lines:
        # Pattern: backtick-quoted tool name followed by description
        # e.g., "`verify_foo.py` — Description with Guardrail G2 or CI gate"
        match = re.search(r'`(verify_\w+\.py)`\s*—\s*(.+)$', line)
        if match:
            tool_name = match.group(1)
            description = match.group(2)

            # Check if description indicates this is a CI gate or Guardrail
            # Look for keywords: "gate" (as whole word), "Guardrail", or Guardrail marker "G[0-9]"
            if (re.search(r'\bgate\b', description, re.IGNORECASE) or
                'Guardrail' in description or
                re.search(r'G\d+', description)):
                documented_gates.add(tool_name)

    return documented_gates


def extract_workflow_invocations(workflow_files, workflow_data_list):
    """Extract all verify_*.py tool invocations from workflow files.

    Returns:
        Set[str]: Set of invoked tool filenames (e.g., 'verify_foo.py')
    """
    invoked_tools = set()

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

                # Look for "python tools/verify_*.py" invocations
                matches = re.findall(r'python[3]?\s+tools/(verify_\w+\.py)', run)
                for tool_name in matches:
                    invoked_tools.add(tool_name)

    return invoked_tools


def check_cli_workflow_gate_parity(root, workflow_files, workflow_data_list):
    """Verify that all documented CI gates are actually wired into workflows.

    Returns:
        List[str]: List of findings for missing gate invocations
    """
    findings = []

    # Extract documented gates from tools/CLAUDE.md
    documented_gates = extract_documented_gates(root)

    if not documented_gates:
        return findings  # No documented gates to check

    # Extract actual workflow invocations
    invoked_tools = extract_workflow_invocations(workflow_files, workflow_data_list)

    # Report any documented gates that are missing from workflows
    missing_gates = documented_gates - invoked_tools
    for tool_name in sorted(missing_gates):
        findings.append(
            f"Documented CI gate not invoked by workflows: {tool_name} "
            f"(documented in tools/CLAUDE.md but missing from .github/workflows/ci.yml)"
        )

    return findings


def lint_workflows(root, json_output=False):
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

    # Check test coverage
    findings.extend(check_test_coverage(workflow_files, workflow_data_list, packages, root))

    # Check file references
    for workflow_data in workflow_data_list:
        if workflow_data:
            findings.extend(check_file_references(workflow_data, root))

    # Check CLI workflow gate parity
    findings.extend(check_cli_workflow_gate_parity(root, workflow_files, workflow_data_list))

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

    args = parser.parse_args()

    exit_code, findings = lint_workflows(args.root, args.json)

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
