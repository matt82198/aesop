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

    def test_base_pinned_invocation_counts_as_wired(self):
        """Test: a workflow running the base-tree copy (python base/tools/<gate>) still wires the gate."""
        self._make_test_structure(
            self.test_dir,
            gates_in_claudemd=['test_gate_1.py'],
            gates_wired_in_ci=[]
        )
        wf = os.path.join(self.test_dir, '.github', 'workflows', 'verify-receipt.yml')
        with open(wf, 'w', encoding='utf-8') as f:
            f.write("name: V\n\njobs:\n  v:\n    steps:\n      - run: python base/tools/test_gate_1.py --check\n")
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Expected exit 0 for base-pinned invocation. stderr: {stderr}")

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

        # Copy the real gate in, so the run exercises its own fail-closed path
        import shutil
        shutil.copy2(os.path.join(os.path.dirname(__file__), "..", "tools", "verify_gates_wired.py"),
                     os.path.join(tools_dir, "verify_gates_wired.py"))

        # Create ci.yml but missing CLAUDE.md files
        ci_yml_path = os.path.join(self.test_dir, '.github', 'workflows', 'ci.yml')
        with open(ci_yml_path, 'w', encoding='utf-8') as f:
            f.write("name: CI\njobs:\n  ci:\n    runs-on: ubuntu-latest\n")

        rc, stdout, stderr = self._run_gate(self.test_dir)
        # Should fail closed (exit 2 on error or exit 1)
        self.assertEqual(rc, 2, "Expected exit 2 (fail-closed) when CLAUDE.md missing, got %d: %s" % (rc, stderr))

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

    def _fixture(self, claudemd_line, ci_steps=()):
        """Build a repo whose tools/CLAUDE.md carries one line and whose ci.yml runs ci_steps."""
        import shutil
        tools_dir = os.path.join(self.test_dir, 'tools')
        tests_dir = os.path.join(self.test_dir, 'tests')
        github_dir = os.path.join(self.test_dir, '.github', 'workflows')
        for d in (tools_dir, tests_dir, github_dir):
            os.makedirs(d)
        script_src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'verify_gates_wired.py')
        shutil.copy2(script_src, os.path.join(tools_dir, 'verify_gates_wired.py'))
        with open(os.path.join(tools_dir, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
            f.write("# tools/ — Build utilities\n\n## Core invariants\n" + claudemd_line + "\n")
        with open(os.path.join(tests_dir, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
            f.write("# tests/ — Test suites\n")
        ci_yml = "name: CI\njobs:\n  ci:\n    runs-on: ubuntu-latest\n    steps:\n"
        for step in ci_steps:
            ci_yml += f"      - run: {step}\n"
        with open(os.path.join(github_dir, 'ci.yml'), 'w', encoding='utf-8') as f:
            f.write(ci_yml)

    def test_identifier_tokens_on_guardrail_line_are_not_gates(self):
        """Bare identifiers and keywords in backticks on a Guardrail line are not gate names."""
        self._fixture(
            "- **Symbol survival (Guardrail G13)**: collects `def` and `class` names "
            "such as `my_helper` and `build_bisect_batches`."
        )
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Identifiers must not be treated as gates. stderr: {stderr}")
        self.assertNotIn('Unwired', stderr)
        self.assertNotIn('ambiguous', stderr)

    def test_directory_list_on_guardrail_line_is_not_a_gate(self):
        """A directory reference such as `tools/` on a Guardrail line is not a gate."""
        self._fixture("- pyflakes ratchet: `tools/ bin/ ui/ tests/` — Guardrail G15: wraps pyflakes")
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Directory references must not be gates. stderr: {stderr}")

    def test_glob_pattern_on_guardrail_line_is_not_a_gate(self):
        """A glob such as `test_*.py` names a set of files, not a single gate script."""
        self._fixture("- coverage: `tests/test_*.py` and `*.py` — Guardrail G2: globbed scan")
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Glob patterns must not be gates. stderr: {stderr}")

    def test_documented_script_path_not_wired_still_errors(self):
        """A path-form gate script documented on a Guardrail line and not invoked in CI exits 1."""
        self._fixture("- `tools/missing_gate.py` — Guardrail G7: a documented gate")
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 1, f"Unwired path-form gate must fail closed. stderr: {stderr}")
        self.assertIn('missing_gate.py', stderr)
        self.assertIn('Unwired', stderr)

    def test_ambiguous_extensionless_path_errors(self):
        """A path under a gate directory with no extension cannot be classified and errors."""
        self._fixture("- `tools/foo` — Guardrail G7: a documented gate")
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 1, f"Ambiguous gate reference must fail closed. stderr: {stderr}")
        self.assertIn('ambiguous gate reference', stderr)
        self.assertIn('tools/foo', stderr)

    def test_prefixed_script_wired_with_arguments_passes(self):
        """An invocation prefix plus arguments still names the script; a CI invocation wires it."""
        self._fixture(
            "- `python tools/my_gate.py --check` — Guardrail G7: a documented gate",
            ci_steps=["python tools/my_gate.py --check"],
        )
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"Prefixed, wired gate must pass. stderr: {stderr}")

    def test_node_script_gate_is_invoked_by_node(self):
        """A .mjs gate documented on a Guardrail line is wired by a node invocation."""
        self._fixture(
            "- `hooks/check_thing.mjs` — Guardrail G7: a documented gate",
            ci_steps=["node hooks/check_thing.mjs"],
        )
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 0, f"node-invoked .mjs gate must count as wired. stderr: {stderr}")

    def test_nested_tools_claudemd_guardrail_is_checked(self):
        """A Guardrail gate line in a nested tools/<group>/CLAUDE.md is checked, so an unwired gate exits 1."""
        self._fixture("- plain note, no gates here.")
        nested_dir = os.path.join(self.test_dir, 'tools', 'x')
        os.makedirs(nested_dir)
        with open(os.path.join(nested_dir, 'CLAUDE.md'), 'w', encoding='utf-8') as f:
            f.write("# tools/x — nested group\n\n- `verify_zz.py` is the Guardrail G99 gate\n")
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 1, f"Unwired gate in nested tools CLAUDE.md must fail closed. stderr: {stderr}")
        self.assertIn('verify_zz.py', stderr)
        self.assertIn('Unwired', stderr)

    def test_same_guardrail_line_in_tools_claudemd_is_reported(self):
        """Pin: the same Guardrail gate line in tools/CLAUDE.md is reported as unwired today."""
        self._fixture("- `verify_zz.py` is the Guardrail G99 gate")
        rc, stdout, stderr = self._run_gate(self.test_dir)
        self.assertEqual(rc, 1, f"Unwired gate in tools/CLAUDE.md must fail closed. stderr: {stderr}")
        self.assertIn('verify_zz.py', stderr)
        self.assertIn('Unwired', stderr)


if __name__ == '__main__':
    unittest.main()
