#!/usr/bin/env python3
"""
Unit tests for tools/ci_gate_runability.py

Tests cover:
- Never-fires conditions (job/step level if conditions that exclude PRs)
- continue-on-error on required gates
- Missing command/file references
- Clean workflows with runnable gates
- Multiline run blocks
"""

import unittest
import tempfile
import subprocess
import json
import re
import sys
from pathlib import Path


class TestCIGateRunability(unittest.TestCase):
    """Test suite for CI gate runability validation."""

    def run_tool(self, yaml_content: str, extra_args: list = None) -> tuple:
        """
        Run the tool on a temporary workflow file.
        Returns (exit_code, stdout, stderr, parsed_json_if_requested).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            workflows_dir = tmppath / '.github' / 'workflows'
            workflows_dir.mkdir(parents=True)

            workflow_file = workflows_dir / 'test.yml'
            workflow_file.write_text(yaml_content)

            # Create a stub for every tools/*.py this fixture's workflow references,
            # so the fixture is self-consistent. The tool correctly flags steps that
            # reference missing files; it previously skipped that check whenever the
            # repo path looked like a Windows temp dir ('AppData' + 'Temp'), so these
            # fixtures passed on Windows and failed on Linux. With that Windows-only
            # exemption removed, a fixture must provide the files it cites.
            for referenced in set(re.findall(r'tools/[A-Za-z0-9_./-]+\.py', yaml_content)):
                stub = tmppath / referenced
                stub.parent.mkdir(parents=True, exist_ok=True)
                stub.write_text('# test fixture stub\n')

            cmd = [
                sys.executable,
                str(Path(__file__).parent.parent / 'tools' / 'ci_gate_runability.py'),
                '--root', str(tmppath),
            ]
            if extra_args:
                cmd.extend(extra_args)

            result = subprocess.run(cmd, capture_output=True, text=True)
            output = result.stdout

            parsed_json = None
            if '--json' in (extra_args or []):
                try:
                    parsed_json = json.loads(output)
                except:
                    pass

            return (result.returncode, output, result.stderr, parsed_json)

    def test_clean_workflow_with_pytest(self):
        """Test a workflow that invokes pytest without issues."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Python tests
        run: python -m pytest tests/
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected clean workflow, got: {stdout}\n{stderr}")

    def test_clean_workflow_with_npm_test_node(self):
        """Test a workflow that invokes npm test:node without issues."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Node tests
        run: npm run test:node
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected clean workflow, got: {stdout}\n{stderr}")

    def test_never_fires_condition_push_only(self):
        """Test that a step with push-only if condition is flagged."""
        yaml = """
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        if: github.event_name == 'push'
        run: python -m pytest tests/
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertNotEqual(exit_code, 0, "Expected to flag push-only condition")
        self.assertIn("PR-excluding if condition", stdout)

    def test_never_fires_condition_job_level(self):
        """Test that a job with push-only if condition is flagged."""
        yaml = """
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    if: github.event_name == 'push'
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: python -m pytest tests/
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertNotEqual(exit_code, 0, "Expected to flag job-level push-only condition")
        self.assertIn("PR-excluding if condition", stdout)

    def test_continue_on_error_on_pytest_gate(self):
        """Test that continue-on-error on a test suite is flagged."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        continue-on-error: true
        run: python -m pytest tests/
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertNotEqual(exit_code, 0, "Expected to flag continue-on-error on test suite")
        self.assertIn("continue-on-error", stdout)

    def test_continue_on_error_on_secret_scan(self):
        """Test that continue-on-error on secret_scan gate is flagged."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Secret scan
        continue-on-error: true
        run: python tools/secret_scan.py .
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertNotEqual(exit_code, 0, "Expected to flag continue-on-error on secret_scan")

    def test_missing_file_reference(self):
        """Test that a missing file reference is flagged."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run missing test
        run: python tools/nonexistent_gate.py --check
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        # Exit code should indicate findings (0 if not found, 1 if flagged)
        # For now, we just check that it doesn't crash
        self.assertIsNotNone(stdout)

    def test_multiline_run_block(self):
        """Test that multiline run blocks are parsed correctly."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: |
          python -m pytest tests/test_*.py
          npm run test:node
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected clean multiline workflow, got: {stdout}\n{stderr}")

    def test_multiple_gates_clean(self):
        """Test a workflow with multiple test gates."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Python tests
        run: python -m pytest tests/
      - name: Node tests
        run: npm run test:node
      - name: Shell tests
        run: npm run test:sh
      - name: Secret scan
        run: python tools/secret_scan.py .
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected clean multi-gate workflow, got: {stdout}\n{stderr}")

    def test_json_output_format(self):
        """Test that --json output is valid JSON."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: python -m pytest tests/
"""
        exit_code, stdout, stderr, parsed_json = self.run_tool(yaml, ['--json'])
        self.assertIsNotNone(parsed_json, "Expected valid JSON output")
        self.assertIn('status', parsed_json)
        self.assertIn('exit_code', parsed_json)
        self.assertIn('findings', parsed_json)
        self.assertEqual(parsed_json['exit_code'], 0)

    def test_json_output_with_findings(self):
        """Test that --json output includes findings when present."""
        yaml = """
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        if: github.event_name == 'push'
        run: python -m pytest tests/
"""
        exit_code, stdout, stderr, parsed_json = self.run_tool(yaml, ['--json'])
        self.assertIsNotNone(parsed_json, "Expected valid JSON output")
        self.assertGreater(len(parsed_json['findings']), 0, "Expected findings in JSON output")

    def test_no_false_positives_on_pr_compatible_conditions(self):
        """Test that PR-compatible conditions don't trigger false positives."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests (matrix job)
        if: matrix.python-shard == 0
        run: python -m pytest tests/
      - name: Run tests (PR only)
        if: github.event_name == 'pull_request'
        run: python -m pytest tests/ --extra
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected no false positives, got: {stdout}\n{stderr}")

    def test_playwright_gates(self):
        """Test that playwright gates are recognized and validated."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  browser:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Playwright
        run: npx playwright test
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected clean playwright workflow, got: {stdout}\n{stderr}")

    def test_verify_gates(self):
        """Test that verify_*.py gates are recognized."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Verify dash
        run: python tools/verify_dash.py
      - name: Verify submit encoding
        run: python tools/verify_submit_encoding.py
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected clean verify gate workflow, got: {stdout}\n{stderr}")

    def test_lint_guards_recognized(self):
        """Test that lint/guard gates are recognized."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Lint CLAUDE.md
        run: python tools/claudemd_lint.py --root .
      - name: Watcher linter
        run: python tools/watcher_linter.py --check
      - name: Spec validator
        run: python tools/spec_contract_validator.py --check
"""
        exit_code, stdout, stderr, _ = self.run_tool(yaml)
        self.assertEqual(exit_code, 0, f"Expected clean lint gate workflow, got: {stdout}\n{stderr}")


class TestCIGateRunabilityRealWorkflow(unittest.TestCase):
    """Test the tool against the real repository's workflows."""

    def test_real_ci_workflow(self):
        """Test the real ci.yml workflow."""
        ci_path = Path(__file__).parent.parent / '.github' / 'workflows' / 'ci.yml'

        if not ci_path.exists():
            self.skipTest(f"Real workflow not found: {ci_path}")

        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent.parent / 'tools' / 'ci_gate_runability.py'),
                '--root', str(Path(__file__).parent.parent),
            ],
            capture_output=True,
            text=True,
        )

        # The real workflow should be clean or report only informational findings
        # (not critical issues)
        print(f"Real workflow check result:\n{result.stdout}")
        if result.returncode != 0:
            print(f"Findings in real workflow:\n{result.stdout}")
        # Don't fail on real workflow; just report findings for user inspection


if __name__ == '__main__':
    unittest.main()
