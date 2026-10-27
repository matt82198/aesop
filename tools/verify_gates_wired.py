#!/usr/bin/env python3
"""
Verify that all documented CI gates in CLAUDE.md files are wired into CI workflows.
INDEX: Guardrail G2.6: gates-wired enforcer (prevents "documented-gate-not-wired" escapes); parses CLAUDE.md for "(Guardrail Gx)" + "verify_*.py are mandatory CI gates"; asserts each gate is invoked in .github/workflows/*.yml; fail-closed on missing files; exit 0=wired/1=unwired/2=error; stdlib-only

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
from pathlib import Path


# A documented gate token is a gate SCRIPT: a path or bare name ending in one of these.
GATE_EXTENSIONS = ('.py', '.sh', '.mjs', '.js', '.ps1')
# Path prefixes where a gate script lives. An extensionless path under one of these is
# a gate reference we cannot classify, so it is reported as ambiguous and fails closed.
GATE_DIRS = ('tools/', 'hooks/', 'daemons/', 'scripts/')
INVOCATION_PREFIXES = ('python', 'python3', 'bash', 'sh', 'node', 'pwsh', 'powershell')
# Each pattern captures the script basename of an invocation in a workflow step.
INVOCATION_PATTERNS = (
    r'\bpython3?\s+(?:[\w.\-]+/)*([\w.\-]+\.py)\b',
    r'\bbash\s+(?:[\w.\-]+/)*([\w.\-]+\.sh)\b',
    r'\bnode\s+(?:[\w.\-]+/)*([\w.\-]+\.(?:mjs|js))\b',
    r'\bpwsh\s+(?:-\S+\s+)*(?:[\w.\-]+/)*([\w.\-]+\.ps1)\b',
)


def classify_token(token):
    """Classify one backticked token from a Guardrail line.

    Returns ('gate', basename), ('ambiguous', token), or None for a token that is
    not a gate reference (bare identifiers, keywords, flags, non-script paths).
    """
    words = token.split()
    if not words:
        return None
    if len(words) > 1 and words[0] in INVOCATION_PREFIXES:
        words = words[1:]
    path = words[0]
    basename = path.rsplit('/', 1)[-1]
    if not basename:
        return None  # a directory reference such as `tools/` is not a script
    if any(ch in path for ch in '*?['):
        return None  # a glob such as `test_*.py` names a set of files, not one gate
    if basename.endswith(GATE_EXTENSIONS):
        return ('gate', basename)
    if path.startswith(GATE_DIRS) and '.' not in basename:
        return ('ambiguous', token)
    return None


def discover_gates(claudemd_path, ambiguous=None):
    """Parse CLAUDE.md for documented CI gates.

    Returns dict of {gate_basename: True, ...}
    Only captures tokens explicitly documented as CI gates:
    - Backticked gate script on a line marked "(Guardrail Gx)" and NOT pre-push-only
    - Backticked verify_*.py in the "verify_*.py are mandatory CI gates" section
    Unclassifiable gate references are appended to `ambiguous` (a list) when given.
    """
    gates = {}

    if not os.path.isfile(claudemd_path):
        return None  # Missing CLAUDE.md is fail-closed, never a silent pass

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

        # Classify every backtick-quoted token on this line; identifiers are not gates
        for match in re.finditer(r'`([^`\n]+)`', line):
            kind = classify_token(match.group(1))
            if kind is None:
                continue
            if kind[0] == 'gate':
                gates[kind[1]] = True
            elif ambiguous is not None:
                ambiguous.append(kind[1])

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
        #  - python base/tools/verify_receipt.py   (base-pinned checkout, verify-receipt.yml)

        # Match "python <path>/tool_name.py", "bash <path>/x.sh", "node <path>/x.mjs", etc.
        for pattern in INVOCATION_PATTERNS:
            for match in re.finditer(pattern, content):
                tool_name = match.group(1)
                invoked.add(tool_name)

    return invoked


SKIP_DIRS = ('node_modules', '__pycache__', '.git')


def find_nested_claudemds(tools_dir, skip_path):
    """Yield every tools/**/CLAUDE.md except skip_path (the top-level page, parsed by main)."""
    for dirpath, dirnames, filenames in os.walk(tools_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if 'CLAUDE.md' not in filenames:
            continue
        path = os.path.join(dirpath, 'CLAUDE.md')
        if os.path.normpath(path) != os.path.normpath(skip_path):
            yield path


def main():
    repo_root = os.getcwd()

    # Discover gates from CLAUDE.md files
    gates = {}
    # gate -> relative paths of NESTED CLAUDE.md files that document it (for the report)
    sources = {}
    ambiguous = []

    def absorb(claudemd, nested):
        found = discover_gates(claudemd, ambiguous)
        if found is None:
            print(f"ERROR: Cannot read {claudemd}", file=sys.stderr)
            return False
        rel = os.path.relpath(claudemd, repo_root).replace(os.sep, '/')
        for gate in found:
            gates[gate] = True
            if nested:
                sources.setdefault(gate, []).append(rel)
        return True

    # Parse tools/CLAUDE.md and tests/CLAUDE.md
    tools_claudemd = os.path.join(repo_root, 'tools', 'CLAUDE.md')
    if not absorb(tools_claudemd, nested=False):
        return 2
    tests_claudemd = os.path.join(repo_root, 'tests', 'CLAUDE.md')
    if not absorb(tests_claudemd, nested=False):
        return 2

    # Parse every nested tools/**/CLAUDE.md: a group page can document a gate too
    for nested_path in find_nested_claudemds(os.path.join(repo_root, 'tools'), tools_claudemd):
        if not absorb(nested_path, nested=True):
            return 2

    # A gate-directory reference that cannot be classified is never silently ignored
    if ambiguous:
        print("ERROR: ambiguous gate reference (gate directory path with no script extension):", file=sys.stderr)
        for token in ambiguous:
            print(f"  - {token}", file=sys.stderr)
        return 1

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
            where = sources.get(gate)
            suffix = f" (documented in {', '.join(where)})" if where else ''
            print(f"  - {gate}{suffix}", file=sys.stderr)
        return 1

    print("OK: All documented CI gates are wired into CI workflows")
    return 0


if __name__ == '__main__':
    sys.exit(main())
