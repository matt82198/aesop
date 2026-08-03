#!/usr/bin/env python3
"""
Test suite for templates/aesop-dispatch-template.yml

Verifies:
1. YAML validity (parses without errors)
2. CLI command references (all npx aesop commands exist in bin/cli.js --help)
3. Workflow structure (required keys, permissions, steps)
4. Quoted behavior examples (no invented flags or outputs)

Exit codes:
  0 = all tests pass
  1 = test failures found
  2 = error (missing file, subprocess error, etc.)
"""

import sys
import subprocess
import json
import os
import re
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: yaml module not found. Install via: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def find_repo_root():
    """Find the repo root by looking for a .git directory."""
    current = Path.cwd()
    for _ in range(10):
        if (current / '.git').exists():
            return current
        current = current.parent
    raise RuntimeError("Could not find repository root (.git not found)")


def get_cli_help_output(repo_root):
    """Run `node bin/cli.js --help` and return the output."""
    cli_path = repo_root / 'bin' / 'cli.js'
    if not cli_path.exists():
        raise FileNotFoundError(f"CLI not found: {cli_path}")

    try:
        result = subprocess.run(
            ['node', str(cli_path), '--help'],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            encoding='utf-8'
        )
        if result.returncode != 0:
            raise RuntimeError(f"CLI help failed with exit code {result.returncode}")
        return result.stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError("CLI help timed out (>10s)")


def extract_available_commands(cli_help):
    """Extract available commands from CLI help output.

    Looks for patterns like:
    - "  doctor" (indented command)
    - "- wave" (namespace listing with dash prefix)
    - "  doctor                  Preflight readiness check" (command + description)
    """
    commands = set()

    for line in cli_help.split('\n'):
        # Skip completely empty lines
        if not line or not line.strip():
            continue

        # Skip lines that are clearly not command listings
        if line.strip().startswith('npx') or line.strip().startswith('→'):
            continue

        # Skip lines that are section headers (end with colon, all caps or Title Case)
        if line.strip().endswith(':'):
            continue

        # Extract leading whitespace to detect indentation level
        leading_spaces = len(line) - len(line.lstrip())

        # Only process lines with 2-4 spaces of indentation (command listings)
        # or lines starting with "- " (namespace listings)
        if not ((2 <= leading_spaces <= 4) or line.lstrip().startswith('- ')):
            continue

        stripped = line.strip()

        # Remove leading dash if present (namespace listings)
        if stripped.startswith('- '):
            stripped = stripped[2:]

        # Split on whitespace or dash-based comment patterns
        parts = stripped.split()
        if not parts:
            continue

        cmd = parts[0]

        # Skip if it looks like a flag or control character
        if cmd.startswith('-') or cmd.startswith('('):
            continue

        # Skip all-caps words (likely section headers that leaked through)
        if cmd.isupper():
            continue

        # Add the command if it starts with an alpha character
        if cmd and cmd[0].isalpha():
            commands.add(cmd)

            # Try to extract a subcommand from the next word
            if len(parts) >= 2:
                subcmd = parts[1]
                # Only add if it looks like a real subcommand (short, no punctuation, starts with alpha)
                if (subcmd and subcmd[0].isalpha() and
                    not subcmd.startswith('-') and
                    '(' not in subcmd and len(subcmd) < 25):
                    commands.add(f"{cmd} {subcmd}")

    return commands


def extract_commands_from_workflow(workflow_dict):
    """
    Extract all aesop commands referenced in the workflow.

    Looks for:
    - npx aesop@latest <command>
    - npx @matt82198/aesop <command>
    Returns list of (command, source_step) tuples.
    """
    commands = []

    for job_name, job_config in workflow_dict.get('jobs', {}).items():
        for step in job_config.get('steps', []):
            step_name = step.get('name', 'unknown')

            # Check run field
            run_script = step.get('run', '')
            if run_script:
                # Find all npx aesop commands
                # Pattern: npx aesop@latest <cmd> or npx @matt82198/aesop <cmd>
                matches = re.findall(
                    r'npx\s+(?:aesop@latest|@matt82198/aesop)\s+([a-z0-9\s-]+?)(?:\s+--|\s*$|\s*||)',
                    run_script,
                    re.MULTILINE | re.IGNORECASE
                )
                for match in matches:
                    cmd = match.strip()
                    if cmd:
                        commands.append((cmd, step_name))

            # Check github-script for command references in comments
            with_config = step.get('with', {})
            if isinstance(with_config, dict):
                script = with_config.get('script', '')
                if 'aesop' in script:
                    # Look for commented examples
                    matches = re.findall(
                        r'#\s*npx\s+(?:aesop@latest|@matt82198/aesop)\s+([a-z0-9\s-]+?)(?:\s+--|\s*$)',
                        script,
                        re.MULTILINE | re.IGNORECASE
                    )
                    for match in matches:
                        cmd = match.strip()
                        if cmd:
                            commands.append((cmd, step_name))

    return commands


def load_and_validate_yaml(template_path):
    """Load and validate YAML syntax."""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            workflow = yaml.safe_load(f)
        if not isinstance(workflow, dict):
            raise ValueError("YAML root must be a dictionary")
        return workflow
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error: {e}")


def test_yaml_validity(template_path):
    """Test 1: YAML syntax is valid."""
    print("TEST 1: YAML Validity...", end=' ')
    try:
        load_and_validate_yaml(template_path)
        print("[PASS]")
        return True
    except Exception as e:
        print(f"[FAIL]: {e}")
        return False


def test_workflow_structure(template_path):
    """Test 2: Workflow has required structure."""
    print("TEST 2: Workflow Structure...", end=' ')
    try:
        workflow = load_and_validate_yaml(template_path)

        # Check required keys
        # Note: YAML parses 'on' as boolean True (YAML literal), so check for both
        assert 'name' in workflow, "Missing 'name' key"
        assert 'on' in workflow or True in workflow, "Missing 'on'/'on:' (trigger) key"
        assert 'jobs' in workflow, "Missing 'jobs' key"

        # Check jobs
        jobs = workflow['jobs']
        assert jobs, "No jobs defined"

        # Check orchestration job
        assert 'orchestration' in jobs, "Missing 'orchestration' job"
        job = jobs['orchestration']

        # Check required job properties
        assert 'runs-on' in job, "Job missing 'runs-on'"
        assert 'steps' in job, "Job missing 'steps'"
        assert job['steps'], "Job has no steps"

        # Check for key steps
        step_names = {s.get('name', '') for s in job['steps']}
        assert 'Checkout' in step_names, "Missing 'Checkout' step"
        assert 'Setup Node.js' in step_names, "Missing 'Setup Node.js' step"
        assert 'Setup Python' in step_names, "Missing 'Setup Python' step"
        assert 'Orchestration' in step_names, "Missing 'Orchestration' step"

        print("[PASS]")
        return True
    except AssertionError as e:
        print(f"[FAIL]: {e}")
        return False
    except Exception as e:
        print(f"[ERROR]: {e}")
        return False


def test_cli_command_existence(template_path, repo_root):
    """Test 3: All CLI commands referenced in workflow exist."""
    print("TEST 3: CLI Command Existence...", end=' ')
    try:
        # Get available commands from CLI
        cli_help = get_cli_help_output(repo_root)
        available = extract_available_commands(cli_help)

        # Extract commands from workflow
        workflow = load_and_validate_yaml(template_path)
        referenced_cmds = extract_commands_from_workflow(workflow)

        # Also extract workflow_dispatch input options (these are selectable commands)
        dispatch_options = []
        triggers = workflow.get(True, {}) or workflow.get('on', {})  # 'on' parses as boolean True
        dispatch = triggers.get('workflow_dispatch', {})
        inputs = dispatch.get('inputs', {})
        operation = inputs.get('operation', {})
        options = operation.get('options', [])
        dispatch_options.extend(options)

        # Combine all referenced commands
        all_cmds = [(cmd, step) for cmd, step in referenced_cmds] + \
                  [(cmd, 'workflow_dispatch input') for cmd in dispatch_options]

        if not all_cmds:
            print("[PASS] (no aesop commands referenced)")
            return True

        failures = []
        for cmd, source in all_cmds:
            # Check if command exists
            # Handle both simple commands (doctor) and compound (wave preflight)
            parts = cmd.split()
            if len(parts) == 1:
                # Simple command: must be in available commands
                if cmd not in available:
                    failures.append(f"  '{cmd}' ({source}) -- NOT FOUND in CLI")
            elif len(parts) == 2:
                # Compound command: check main namespace exists
                namespace = parts[0]
                if namespace not in available:
                    failures.append(
                        f"  '{cmd}' ({source}) -- namespace '{namespace}' NOT FOUND in CLI"
                    )

        if failures:
            print("[FAIL]")
            for failure in failures:
                print(failure)
            return False

        print("[PASS]")
        return True
    except Exception as e:
        print(f"[ERROR]: {e}")
        return False


def test_no_invented_flags(template_path):
    """Test 4: Workflow examples do not use invented/undocumented flags."""
    print("TEST 4: No Invented Flags...", end=' ')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Common aesop flags that are documented
        # (this is not exhaustive; the gate is comment-checking for obviously-invented ones)
        documented_flags = {
            '--help', '-h',
            '--force', '--yes',
            '--name', '--domains', '--repos', '--repo-urls',
            '--json', '--check', '--dry-run',
            '--root', '--output',
            '--allow-skip',
            '--demo',
        }

        # Look for flag-like patterns in run scripts (avoid false positives)
        # Scan for patterns like "aesop ... --flag-name" that aren't in documented_flags
        undocumented = set()
        for match in re.finditer(r'aesop[^#\n]*?--([a-z-]+)', content):
            flag = '--' + match.group(1)
            if flag not in documented_flags:
                # Allow flags in comments and examples (marked with #)
                # Only report flags in actual run commands
                line_start = content.rfind('\n', 0, match.start()) + 1
                if 'run:' in content[line_start:line_start+100]:
                    undocumented.add(flag)

        if undocumented:
            print("[FAIL]")
            for flag in sorted(undocumented):
                print(f"  Potentially invented flag: {flag}")
            return False

        print("[PASS]")
        return True
    except Exception as e:
        print(f"[ERROR]: {e}")
        return False


def test_no_fabricated_output(template_path):
    """Test 5: Workflow does not show fabricated command outputs."""
    print("TEST 5: No Fabricated Outputs...", end=' ')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Patterns that suggest output is being faked
        suspicious_patterns = [
            r'echo\s+.*Orchestration (success|passed)',  # Faked success messages
            r'echo\s+.*[Cc]ost.*\d+\.\d+',  # Faked cost numbers
            r'echo\s+.*agents.*spawned',  # Faked agent output
        ]

        fabricated = []
        for pattern in suspicious_patterns:
            for match in re.finditer(pattern, content):
                # Allow in comments (preceded by #)
                line_start = content.rfind('\n', 0, match.start()) + 1
                line = content[line_start:match.end() + 50]
                if not line.lstrip().startswith('#'):
                    fabricated.append(match.group(0)[:60])

        if fabricated:
            print("[FAIL]")
            for output in fabricated:
                print(f"  Potentially fabricated: {output}...")
            return False

        print("[PASS]")
        return True
    except Exception as e:
        print(f"[ERROR]: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 70)
    print("AESOP GITHUB ACTIONS DISPATCH TEMPLATE TEST SUITE")
    print("=" * 70)
    print()

    try:
        repo_root = find_repo_root()
        template_path = repo_root / 'templates' / 'aesop-dispatch-template.yml'

        if not template_path.exists():
            print(f"Error: Template not found: {template_path}", file=sys.stderr)
            sys.exit(2)

        print(f"Template: {template_path}")
        print(f"Repo root: {repo_root}")
        print()

        # Run tests
        results = [
            test_yaml_validity(template_path),
            test_workflow_structure(template_path),
            test_cli_command_existence(template_path, repo_root),
            test_no_invented_flags(template_path),
            test_no_fabricated_output(template_path),
        ]

        print()
        print("=" * 70)
        passed = sum(results)
        total = len(results)
        status = "PASS" if all(results) else "FAIL"
        print(f"Results: {passed}/{total} tests passed [{status}]")
        print("=" * 70)

        sys.exit(0 if all(results) else 1)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
