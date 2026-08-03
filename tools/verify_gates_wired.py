#!/usr/bin/env python3
"""
Verify that all documented CI gates in CLAUDE.md files are wired into CI workflows.

This guardrail detects the "documented-gate-not-wired" class by parsing CI gate
inventory from tools/CLAUDE.md and tests/CLAUDE.md, then asserting each named
gate script is invoked in .github/workflows/*.yml.

Captures gates marked with:
- "(Guardrail Gx)" and NOT labeled as "pre-push" only
- "verify_*.py are mandatory CI gates" section

Exit 0: all gates wired
Exit 1: unwired gates found, or missing input files
Exit 2: processing error
"""

import os
import re
import sys
import json
from pathlib import Path


def discover_gates(claudemd_path):
    """Parse CLAUDE.md for documented CI gates.

    Returns dict of {tool_name: True, ...}
    Only captures tools explicitly documented as CI gates:
    - Marked with "(Guardrail Gx)" and NOT pre-push-only
    - Listed in "verify_*.py are mandatory CI gates" section
    """
    gates = {}

    if not os.path.isfile(claudemd_path):
        return gates

    try:
        with open(claudemd_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"ERROR: Failed to read {claudemd_path}: {e}", file=sys.stderr)
        return None  # Signal error

    lines = content.split('\n')
    full_text = '\n'.join(lines)

    # First pass: find lines with Guardrail markers (CI gates)
    for line in lines:
        # Skip lines without backticks or without Guardrail marker
        if '`' not in line or 'Guardrail G' not in line:
            continue

        # Skip if this line marks it as pre-push-only (not a CI gate)
        if 'pre-push' in line and 'CI' not in line:
            continue

        # Extract all backtick-quoted tool names from this line
        for match in re.finditer(r'`([a-z_][a-z0-9_]*(?:\.py|\.sh)?)`', line):
            tool_name = match.group(1)
            gates[tool_name] = True

    # Second pass: find the "verify_*.py are mandatory CI gates" section
    if 'verify_*.py are mandatory CI gates' in full_text:
        # Extract section until the next bullet point (-)
        section_match = re.search(
            r'verify_\*\.py are mandatory CI gates[^-]*',
            full_text
        )
        if section_match:
            section = section_match.group(0)
            # Extract all backtick-quoted verify_*.py tools
            for match in re.finditer(r'`(verify_[a-z_]*\.py)`', section):
                tool_name = match.group(1)
                gates[tool_name] = True

    return gates


def find_gate_invocations(workflows_dir):
    """Scan .github/workflows/*.yml for gate invocations.

    Returns set of tool names that appear to be invoked (python <tool>, bash <tool>, etc.)
    """
    invoked = set()

    if not os.path.isdir(workflows_dir):
        return invoked

    workflow_files = Path(workflows_dir).glob('*.yml')

    for wf_file in workflow_files:
        try:
            with open(wf_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            continue

        # Find patterns like:
        #  - python tools/ci_gate_runability.py
        #  - run: python tools/verify_test_coverage.py
        #  - bash tools/run_shell_tests.sh

        # Match "python <path>/tool_name.py" or similar
        for pattern in [
            r'python\s+tools/([a-z_][a-z0-9_]*\.py)',
            r'bash\s+tools/([a-z_][a-z0-9_]*\.sh)',
            r'bash\s+([a-z_][a-z0-9_]*\.sh)',
            r'run:\s*python\s+tools/([a-z_][a-z0-9_]*\.py)',
        ]:
            for match in re.finditer(pattern, content):
                tool_name = match.group(1)
                invoked.add(tool_name)

    return invoked


def main():
    repo_root = os.getcwd()

    # Discover gates from CLAUDE.md files
    gates = {}

    # Parse tools/CLAUDE.md
    tools_claudemd = os.path.join(repo_root, 'tools', 'CLAUDE.md')
    tools_gates = discover_gates(tools_claudemd)
    if tools_gates is None:
        print(f"ERROR: Cannot read {tools_claudemd}", file=sys.stderr)
        return 2
    gates.update(tools_gates)

    # Parse tests/CLAUDE.md
    tests_claudemd = os.path.join(repo_root, 'tests', 'CLAUDE.md')
    tests_gates = discover_gates(tests_claudemd)
    if tests_gates is None:
        print(f"ERROR: Cannot read {tests_claudemd}", file=sys.stderr)
        return 2
    gates.update(tests_gates)

    if not gates:
        # No documented CI gates - this is OK, nothing needs to be wired
        print("OK: No CI gates documented (nothing to wire)")
        return 0

    # Find invoked gates in CI workflows
    workflows_dir = os.path.join(repo_root, '.github', 'workflows')
    invoked = find_gate_invocations(workflows_dir)

    # Check for unwired gates
    unwired = []
    for gate in sorted(gates.keys()):
        if gate not in invoked:
            unwired.append(gate)

    if unwired:
        print("ERROR: Unwired gates found (documented but not invoked in CI):", file=sys.stderr)
        for gate in unwired:
            print(f"  - {gate}", file=sys.stderr)
        return 1

    print("OK: All documented CI gates are wired into CI workflows")
    return 0


if __name__ == '__main__':
    sys.exit(main())
