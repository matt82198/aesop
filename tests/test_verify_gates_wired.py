#!/usr/bin/env python3
"""
Tests for tools/verify_gates_wired.py — CI gates wiring guardrail.

Verifies that all documented CI gates are actually wired into .github/workflows/*.yml
"""

import os
import sys
import tempfile
import unittest
import subprocess
from pathlib import Path


class TestVerifyGatesWired(unittest.TestCase):
    """Test suite for verify_gates_wired.py guardrail."""

    def setUp(self):
        """Create temporary test fixtures."""
        self.test_dir = tempfile.mkdtemp(prefix='test_gates_')
        self.addCleanup(lambda: self._cleanup_dir(self.test_dir))

    def _cleanup_dir(self, path):
        """Recursively clean up directory."""
        import shutil
        if os.path.isdir(path):
            shutil.rmtree(path)

    def _run_gate(self, working_dir):
        """Run verify_gates_wired.py in given directory, return exit code and output."""
        result = subprocess.run(
            [sys.executable, 'tools/verify_gates_wired.py'],
            cwd=working_dir,
            capture_output=True,
            text=True,
            encoding='utf-8'
        )
        return result.returncode, result.stdout, result.stderr

    def _make_test_structure(self, test_dir, gates_in_claudemd=None, gates_wired_in_ci=None):
        """Create minimal repo structure with test CLAUDE.md and ci.yml files."""
        tools_dir = os.path.join(test_dir, 'tools')
        github_dir = os.path.join(test_dir, '.github', 'workflows')
        tests_dir = os.path.join(test_dir, 'tests')

        os.makedirs(tools_dir)
        os.makedirs(github_dir)
        os.makedirs(tests_dir)

        # Copy verify_gates_wired.py to the test tools directory
        import shutil
        script_src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'verify_gates_wired.py')
        script_dst = os.path.join(tools_dir, 'verify_gates_wired.py')
        if os.path.isfile(script_src):
            shutil.copy2(script_src, script_dst)

        # Create dummy tools files
        for gate in (gates_in_claudemd or []):
            gate_file = os.path.join(tools_dir, gate)
            Path(gate_file).touch()

        # Create tools/CLAUDE.md with documented gates
        tools_claudemd_content = "# tools/ — Build utilities\n\n## Core invariants\n"
        if gates_in_claudemd:
            for gate in gates_in_claudemd:
                if gate.startswith('verify_'):
                    # Add verify_*.py to mandatory CI gates section
                    tools_claudemd_content += f"- **verify_*.py are mandatory CI gates**: `{gate}` are required gates\n"
                else:
                    # Add Guardrail marker
                    tools_claudemd_content += f"- `{gate}` — Guardrail GX: test gate\n"

        tools_claudemd_path = os.path.join(tools_dir, 'CLAUDE.md')
        with open(tools_claudemd_path, 'w', encoding='utf-8') as f:
            f.write(tools_claudemd_content)

        # Create tests/CLAUDE.md (minimal, no gates for these tests)
        tests_claudemd_content = "# tests/ — Test suites\n\nNo documented gates here.\n"
        tests_claudemd_path = os.path.join(tests_dir, 'CLAUDE.md')
        with open(tests_claudemd_path, 'w', encoding='utf-8') as f:
            f.write(tests_claudemd_content)

        # Create .github/workflows/ci.yml with wired gates
        ci_yml_content = "name: CI\n\njobs:\n  ci:\n    runs-on: ubuntu-latest\n    steps:\n"
        if gates_wired_in_ci:
            for gate in gates_wired_in_ci:
                ci_yml_content += f"      - run: python tools/{gate}\n"

        ci_yml_path = os.path.join(github_dir, 'ci.yml')
        with open(ci_yml_path, 'w', encoding='utf-8') as f:
            f.write(ci_yml_content)

    def test_all_gates_wired(self):
        """Test: when all documented gates are wired, exit 0."""
        gates = ['test_gate_1.py', 'test_gate_2.py']
        self._make_test_structure(
            self.test_dir,
            gates_in_claudemd=gates,
            gates_wired_in_ci=gates
        )
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Expected exit 0 when all gates wired. stderr: {stderr}")
        self.assertIn('OK', stdout)

    def test_unwired_gate_detected(self):
        """Test: when a documented gate is not wired, exit 1."""
        self._make_test_structure(
            self.test_dir,
            gates_in_claudemd=['test_gate_1.py', 'test_gate_2.py'],
            gates_wired_in_ci=['test_gate_1.py']  # Missing test_gate_2.py
        )
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 1, f"Expected exit 1 when gate unwired. stderr: {stderr}")
        self.assertIn('test_gate_2.py', stderr)
        self.assertIn('Unwired', stderr)

    def test_verify_gates_mandatory_ci_pattern(self):
        """Test: verify_*.py gates in mandatory CI gates section are captured."""
        gates = ['verify_dash.py', 'verify_submit_encoding.py']
        self._make_test_structure(
            self.test_dir,
            gates_in_claudemd=gates,
            gates_wired_in_ci=gates
        )
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Expected exit 0 for verify_*.py gates. stderr: {stderr}")

    def test_missing_claudemd_files_fails_closed(self):
        """Test: missing CLAUDE.md files cause exit 1 (fail-closed)."""
        # Create minimal directory structure but no CLAUDE.md
        tools_dir = os.path.join(self.test_dir, 'tools')
        os.makedirs(os.path.join(self.test_dir, '.github', 'workflows'))
        os.makedirs(os.path.join(self.test_dir, 'tests'))
        os.makedirs(tools_dir)

        # Create ci.yml but missing CLAUDE.md files
        ci_yml_path = os.path.join(self.test_dir, '.github', 'workflows', 'ci.yml')
        with open(ci_yml_path, 'w', encoding='utf-8') as f:
            f.write("name: CI\njobs:\n  ci:\n    runs-on: ubuntu-latest\n")

        rc, stdout, stderr = self._run_gate(self.test_dir)
        # Should fail closed (exit 2 on error or exit 1)
        self.assertNotEqual(rc, 0, "Expected non-zero exit when CLAUDE.md missing")

    def test_guardrail_gate_marker(self):
        """Test: gates marked with (Guardrail Gx) are captured."""
        # Create custom CLAUDE.md with Guardrail marker
        tools_dir = os.path.join(self.test_dir, 'tools')
        tests_dir = os.path.join(self.test_dir, 'tests')
        github_dir = os.path.join(self.test_dir, '.github', 'workflows')

        os.makedirs(tools_dir)
        os.makedirs(tests_dir)
        os.makedirs(github_dir)

        # Copy verify_gates_wired.py
        import shutil
        script_src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'verify_gates_wired.py')
        if os.path.isfile(script_src):
            shutil.copy2(script_src, os.path.join(tools_dir, 'verify_gates_wired.py'))

        # Create tools/CLAUDE.md with Guardrail marker
        claudemd_content = """# tools/ — Build utilities

## Core invariants
- `my_gate_tool.py` — Guardrail G1: My custom gate; CLI: `--check`; exit 0=clean/1=findings
"""
        with open(os.path.join(tools_dir, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
            f.write(claudemd_content)

        with open(os.path.join(tests_dir, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
            f.write("# tests/ — Test suites\n")

        # Create ci.yml with the gate wired
        ci_yml_content = """name: CI
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - run: python tools/my_gate_tool.py
"""
        with open(os.path.join(github_dir, 'ci.yml'), 'w', encoding='utf-8') as f:
            f.write(ci_yml_content)

        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Expected exit 0 for Guardrail gate when wired. stderr: {stderr}")

    def test_pre_push_only_gate_not_captured(self):
        """Test: gates marked as 'pre-push' only (not CI) are not flagged as unwired."""
        tools_dir = os.path.join(self.test_dir, 'tools')
        tests_dir = os.path.join(self.test_dir, 'tests')
        github_dir = os.path.join(self.test_dir, '.github', 'workflows')

        os.makedirs(tools_dir)
        os.makedirs(tests_dir)
        os.makedirs(github_dir)

        # Copy verify_gates_wired.py
        import shutil
        script_src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'verify_gates_wired.py')
        if os.path.isfile(script_src):
            shutil.copy2(script_src, os.path.join(tools_dir, 'verify_gates_wired.py'))

        # Create CLAUDE.md marking a gate as pre-push only
        claudemd_content = """# tools/ — Build utilities

## Core invariants
- `prepush_gate.py` — Guardrail G5: pre-push gate only; integrated into pre-push-policy.sh
"""
        with open(os.path.join(tools_dir, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
            f.write(claudemd_content)

        with open(os.path.join(tests_dir, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
            f.write("# tests/ — Test suites\n")

        # Create ci.yml WITHOUT the prepush gate (it shouldn't be expected there)
        ci_yml_content = """name: CI
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - run: echo "ci job"
"""
        with open(os.path.join(github_dir, 'ci.yml'), 'w', encoding='utf-8') as f:
            f.write(ci_yml_content)

        rc, stdout, stderr = self._run_gate(self.test_dir)
        # Should pass (exit 0) because prepush_gate is not a CI gate
        self.assertEqual(rc, 0, f"Expected exit 0 when pre-push-only gate not in CI. stderr: {stderr}")


if __name__ == '__main__':
    unittest.main()
