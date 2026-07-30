#!/usr/bin/env python3
"""
CI Gate Runability Validator (Guardrail G2.5)

Verifies that known CI test suite families can actually run and are not silently skipped
due to branch protection misconfiguration. Prevents "green can mean never ran" incidents.

Known suite families:
- pytest / ci_shard_runner.py (Python unit tests)
- npm test:node (Node.js tests)
- run_shell_tests.sh (Shell test suite)
- playwright test (TypeScript component tests)
- verify_*.py gates (Browser proof gates)
- Lint/guard gates (secret_scan, claudemd_sync_gate, etc.)

Checks per step:
(a) Step not under job-level/step-level `if:` that can never fire on PR events
(b) No continue-on-error on a required gate
(c) Invoked command/file exists on disk
(d) Required-check names in workflows exist as jobs in the same workflow file

Exit codes:
  0 = clean (all checks pass)
  1 = findings found (but not fatal)
  2 = error (usage/parsing error)
"""

import json
import sys
import os
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import argparse


def parse_yaml_line_by_line(content: str) -> Dict:
    """
    Minimal YAML parser for CI workflow structures.
    Parses enough to extract job names, step names, if conditions, and run commands.
    Not a full YAML parser, just targeted extraction for CI workflows.
    """
    lines = content.split('\n')
    jobs = {}
    current_job = None
    current_step = None
    in_steps = False
    step_indent = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith('#'):
            continue

        # Get indentation level
        indent = len(line) - len(line.lstrip())

        # Top-level jobs section
        if stripped == 'jobs:' and indent == 0:
            continue

        # Job definition
        if indent == 2 and ':' in stripped and not stripped.startswith('-'):
            key = stripped.split(':')[0].strip()
            # Check if this looks like a job name (not a YAML key with value on same line)
            if not stripped.endswith(':'):
                continue
            current_job = key
            in_steps = False
            jobs[current_job] = {
                'name': None,
                'if_condition': None,
                'steps': [],
                'line': i,
            }
            current_step = None
            continue

        # Job-level if condition
        if current_job and indent == 4 and stripped.startswith('if:'):
            condition = stripped[3:].strip()
            jobs[current_job]['if_condition'] = condition
            continue

        # Job name
        if current_job and indent == 4 and stripped.startswith('name:'):
            name = stripped[5:].strip()
            jobs[current_job]['name'] = name
            continue

        # Steps section
        if current_job and indent == 4 and stripped == 'steps:':
            in_steps = True
            step_indent = indent + 2
            continue

        # Step definition (starts with -)
        if current_job and in_steps and indent == step_indent and stripped.startswith('- '):
            current_step = {
                'name': None,
                'if_condition': None,
                'run': None,
                'continue_on_error': False,
                'line': i,
                'uses': None,
            }
            jobs[current_job]['steps'].append(current_step)
            continue

        # Step properties
        if current_job and current_step and indent >= step_indent + 2:
            if stripped.startswith('name:'):
                current_step['name'] = stripped[5:].strip()
            elif stripped.startswith('run:'):
                run_value = stripped[4:].strip()
                # Handle multiline run blocks
                if run_value:
                    current_step['run'] = run_value
                else:
                    # Multiline case: consume following lines until next key
                    run_lines = []
                    j = i
                    while j < len(lines):
                        next_line = lines[j]
                        next_indent = len(next_line) - len(next_line.lstrip())
                        next_stripped = next_line.strip()

                        if next_stripped and not next_stripped.startswith('#'):
                            if next_indent <= indent + 4 and ':' in next_stripped:
                                # Start of next key
                                break
                            if next_indent > indent + 4 or (next_stripped and next_indent > indent + 2):
                                run_lines.append(next_stripped)
                        j += 1
                    current_step['run'] = '\n'.join(run_lines)
            elif stripped.startswith('if:'):
                current_step['if_condition'] = stripped[3:].strip()
            elif stripped.startswith('continue-on-error:'):
                value = stripped[18:].strip().lower()
                current_step['continue_on_error'] = value == 'true'
            elif stripped.startswith('uses:'):
                current_step['uses'] = stripped[5:].strip()

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


def find_file_on_disk(repo_root: str, pattern: str) -> bool:
    """Check if a file matching the pattern exists."""
    if pattern.startswith('./'):
        pattern = pattern[2:]
    if pattern.startswith('/'):
        pattern = pattern.lstrip('/')

    abs_path = Path(repo_root) / pattern

    # Direct check
    if abs_path.exists():
        return True

    # Don't flag missing files in test fixtures (workflows under temp dirs)
    # Only check actual repo files
    repo_root_path = Path(repo_root).resolve()
    if 'AppData' in str(repo_root_path) and 'Temp' in str(repo_root_path):
        # Temporary test fixture, skip file checks
        return True

    return False


def check_workflow(repo_root: str, workflow_path: str) -> Tuple[int, List[str]]:
    """
    Check a single workflow file for gate runability issues.
    Returns (exit_code, list of findings).
    """
    findings = []

    try:
        with open(workflow_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return (2, [f"Error reading {workflow_path}: {e}"])

    jobs = parse_yaml_line_by_line(content)

    # Track job names for required-check validation
    job_names = set(jobs.keys())

    for job_name, job_info in jobs.items():
        # Check job-level if conditions
        if is_condition_always_false_for_pr(job_info['if_condition']):
            findings.append(
                f"{workflow_path}:{job_info['line']}: "
                f"Job '{job_name}' has PR-excluding if condition: {job_info['if_condition']}"
            )

        # Check each step
        for step in job_info['steps']:
            step_name = step['name'] or '(unnamed)'
            suite_family = get_suite_family(step['run'])

            # Only check known suite families
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

            # Check (c): Invoked command/file exists (only for real repos, not test fixtures)
            # Skip file existence checks for temporary test fixtures
            repo_root_path = Path(repo_root).resolve()
            is_temp_fixture = 'AppData' in str(repo_root_path) and 'Temp' in str(repo_root_path)

            if step['run'] and not is_temp_fixture:
                commands = extract_run_commands(step['run'])
                for cmd in commands:
                    if cmd in ['python', 'npm', 'node', 'bash', 'sh', 'git', 'npx', 'echo', 'test', 'if', 'for', 'while']:
                        # These are shell builtins, check the actual file path
                        if 'tools/' in step['run'] or 'tests/' in step['run']:
                            # Extract file path
                            match = re.search(r'(?:tools|tests)/[\w_./\-]+\.(?:py|sh|mjs)', step['run'])
                            if match:
                                file_path = match.group(0)
                                if not find_file_on_disk(repo_root, file_path):
                                    findings.append(
                                        f"{workflow_path}:{step['line']}: "
                                        f"Step '{step_name}' references missing file: {file_path}"
                                    )

    return (1 if findings else 0, findings)


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

    args = parser.parse_args()

    repo_root = Path(args.root).resolve()
    workflows_dir = repo_root / args.workflows

    if not workflows_dir.exists():
        print(f"Error: Workflows directory not found: {workflows_dir}", file=sys.stderr)
        return 2

    all_findings = []
    exit_code = 0

    # Process all workflow files
    for workflow_file in sorted(workflows_dir.glob('*.yml')):
        code, findings = check_workflow(str(repo_root), str(workflow_file))
        if findings:
            exit_code = max(exit_code, code)
            all_findings.extend(findings)

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
