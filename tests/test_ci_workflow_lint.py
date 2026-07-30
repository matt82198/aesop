"""
Test suite for ci_workflow_lint.py

Tests verify that the linter correctly detects:
  1. npm ci steps without package-lock.json (the exact bug from wave-rc5)
  2. Test scripts defined in package.json but not invoked by workflows
  3. YAML parse errors
  4. File reference issues (best-effort)

Fixture-root isolated tests using tempfile to avoid pollution.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import ci_workflow_lint


class CIWorkflowLintTest(unittest.TestCase):
    """Test ci_workflow_lint functionality."""

    def setUp(self):
        """Create isolated fixture root with .github/workflows directory."""
        self.fixture_root = Path(tempfile.mkdtemp(prefix="ci-lint-test-"))
        self.workflows_dir = self.fixture_root / ".github" / "workflows"
        self.workflows_dir.mkdir(parents=True)

    def tearDown(self):
        """Clean up fixture root."""
        if self.fixture_root.exists():
            shutil.rmtree(self.fixture_root)

    def _write_workflow(self, filename, content):
        """Write a workflow file to the fixtures directory."""
        workflow_path = self.workflows_dir / filename
        workflow_path.write_text(content, encoding='utf-8')
        return workflow_path

    def _write_package_json(self, subdir, data):
        """Write a package.json file to a subdirectory."""
        pkg_dir = self.fixture_root / subdir
        pkg_dir.mkdir(parents=True, exist_ok=True)
        pkg_file = pkg_dir / "package.json"
        pkg_file.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return pkg_file

    def _write_package_lock(self, subdir):
        """Write an empty package-lock.json file."""
        pkg_dir = self.fixture_root / subdir
        pkg_dir.mkdir(parents=True, exist_ok=True)
        lock_file = pkg_dir / "package-lock.json"
        lock_file.write_text("{}\n", encoding='utf-8')
        return lock_file

    def test_npm_ci_without_lockfile_bug(self):
        """
        Reproduce the exact bug from wave-rc5: npm ci without package-lock.json.

        A workflow step runs "npm ci" at the repo root, but there's no
        package-lock.json at the root. This should be caught as a finding.
        """
        # Write the root package.json
        self._write_package_json(".", {"name": "test", "scripts": {}})

        # Write a workflow with npm ci but no package-lock.json at root
        workflow_content = """
name: CI

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v7

      - name: Install dependencies
        run: npm ci
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should find the issue
        self.assertEqual(exit_code, 1)
        self.assertTrue(any("npm ci" in f for f in findings),
                       f"Expected npm ci finding, got: {findings}")
        self.assertTrue(any("package-lock.json" in f for f in findings),
                       f"Expected package-lock.json finding, got: {findings}")

    def test_npm_ci_with_lockfile_passes(self):
        """
        npm ci with package-lock.json present should pass.
        """
        # Write root package.json and package-lock.json
        self._write_package_json(".", {"name": "test", "scripts": {}})
        self._write_package_lock(".")

        # Write workflow with npm ci
        workflow_content = """
name: CI

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v7

      - name: Install
        run: npm ci
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should pass (no npm ci findings)
        npm_ci_findings = [f for f in findings if "npm ci" in f and "package-lock" in f]
        self.assertFalse(npm_ci_findings, f"Should not find npm ci issues, got: {npm_ci_findings}")

    def test_npm_ci_with_working_directory(self):
        """
        npm ci with working-directory should check lockfile in that directory.
        """
        # Write root package.json (no lockfile)
        self._write_package_json(".", {"name": "root", "scripts": {}})

        # Write ui/web package.json and lockfile
        self._write_package_json("ui/web", {"name": "ui", "scripts": {}})
        self._write_package_lock("ui/web")

        # Write workflow with working-directory
        workflow_content = """
name: CI

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Build UI
        working-directory: ui/web
        run: npm ci
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should pass because package-lock.json exists in ui/web
        npm_ci_findings = [f for f in findings if "npm ci" in f and "package-lock" in f]
        self.assertFalse(npm_ci_findings, f"Should not find npm ci issues, got: {npm_ci_findings}")

    def test_npm_ci_with_cd_in_run(self):
        """
        npm ci after cd should check lockfile in the cd directory.
        """
        # Write root and ui/web
        self._write_package_json(".", {"name": "root", "scripts": {}})
        self._write_package_json("ui/web", {"name": "ui", "scripts": {}})
        self._write_package_lock("ui/web")

        # Write workflow with cd in run
        workflow_content = """
name: CI

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Build UI
        run: |
          cd ui/web
          npm ci
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should pass because package-lock.json exists in ui/web
        npm_ci_findings = [f for f in findings if "npm ci" in f and "package-lock" in f]
        self.assertFalse(npm_ci_findings, f"Should not find npm ci issues, got: {npm_ci_findings}")

    def test_test_script_not_invoked(self):
        """
        Test scripts in package.json but not run by workflows should be flagged.
        """
        # Write package.json with test:py
        self._write_package_json(".", {
            "name": "test",
            "scripts": {
                "test:py": "python -m unittest",
                "test:node": "node --test"
            }
        })

        # Write workflow that only runs test:node
        workflow_content = """
name: CI

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Run Node tests
        run: npm run test:node
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should find that test:py is not invoked
        self.assertEqual(exit_code, 1)
        self.assertTrue(any("test:py" in f and "not invoked" in f for f in findings),
                       f"Expected test:py not invoked, got: {findings}")

    def test_all_test_scripts_invoked(self):
        """
        When all test scripts are invoked, linter should pass (for that check).
        """
        # Write package.json with test scripts
        self._write_package_json(".", {
            "name": "test",
            "scripts": {
                "test:py": "python -m unittest",
                "test:node": "node --test",
                "test:sh": "bash tests/test.sh"
            }
        })

        # Write workflow that runs all three
        workflow_content = """
name: CI

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Run Python tests
        run: python -m unittest discover

      - name: Run Node tests
        run: npm run test:node

      - name: Run Shell tests
        run: bash tests/test.sh
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # No test coverage findings expected
        test_coverage_findings = [f for f in findings if "not invoked" in f]
        self.assertFalse(test_coverage_findings,
                        f"Should not find test coverage issues, got: {test_coverage_findings}")

    def test_yaml_parse_error(self):
        """
        Invalid YAML should be caught.
        """
        # Write invalid YAML (unmatched bracket in mapping)
        workflow_content = """
name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Invalid YAML
        run: echo "hello"
        invalid: [unclosed bracket
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should find parse error
        self.assertEqual(exit_code, 1)
        self.assertTrue(any("parse" in f.lower() or "YAML" in f for f in findings),
                       f"Expected YAML parse error, got: {findings}")

    def test_json_output(self):
        """
        JSON output format should include exit_code and findings.
        """
        # Write a simple workflow with an issue
        self._write_package_json(".", {
            "name": "test",
            "scripts": {"test:py": "python -m unittest"}
        })

        workflow_content = """
name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: No tests run
        run: echo "hello"
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter with JSON output
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root), json_output=True)

        # Check that findings are returned as strings (they're already formatted)
        self.assertEqual(exit_code, 1)
        self.assertTrue(len(findings) > 0)
        self.assertTrue(all(isinstance(f, str) for f in findings))

    def test_no_workflows_found(self):
        """
        If no workflows exist, should report this.
        """
        # Remove the workflows directory we created
        shutil.rmtree(self.workflows_dir.parent)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should report no workflows
        self.assertEqual(exit_code, 1)
        self.assertTrue(any("No workflow files found" in f for f in findings))

    def test_no_package_json_no_error(self):
        """
        If no package.json exists, linter should still work (just check YAML).
        """
        # Write a valid workflow with no package.json
        workflow_content = """
name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v7
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should not crash, might have other findings but not about package.json
        # The only finding might be about test coverage
        self.assertIsInstance(exit_code, int)
        self.assertIsInstance(findings, list)

    def _write_claudemd(self, content):
        """Write a tools/CLAUDE.md file."""
        tools_dir = self.fixture_root / "tools"
        tools_dir.mkdir(exist_ok=True)
        claudemd_path = tools_dir / "CLAUDE.md"
        claudemd_path.write_text(content, encoding='utf-8')
        return claudemd_path

    def test_cli_gate_parity_missing_documented_gate(self):
        """
        Reproduce the escape: a documented CI gate is missing from workflows.

        Fixture scenario:
        - tools/CLAUDE.md documents verify_test_suite_count.py as a CI gate
        - ci.yml does NOT invoke it
        - Linter should flag this
        """
        # Write CLAUDE.md documenting a verify_*.py as a CI gate
        claudemd_content = """# tools/ — Build utilities

- `verify_test_suite_count.py` — Test suite count drift gate (auto-verifiable + auto-fixable); CLI: `--check` (fail if counts drift; CI gate)
- `verify_test_coverage.py` — Guardrail G2: CI gate that verifies all on-disk test files are run
"""
        self._write_claudemd(claudemd_content)

        # Write a workflow that does NOT invoke these gates
        workflow_content = """
name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v7
      - name: Some other check
        run: python tools/some_other_check.py
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should find that documented gates are missing
        self.assertEqual(exit_code, 1)
        gate_parity_findings = [f for f in findings if "gate not invoked" in f.lower()]
        self.assertTrue(gate_parity_findings,
                       f"Expected gate parity findings, got all findings: {findings}")

        # Should report both missing gates
        finding_text = " ".join(gate_parity_findings)
        self.assertIn("verify_test_suite_count.py", finding_text,
                     f"Expected verify_test_suite_count.py in findings, got: {finding_text}")
        self.assertIn("verify_test_coverage.py", finding_text,
                     f"Expected verify_test_coverage.py in findings, got: {finding_text}")

    def test_cli_gate_parity_all_gates_present(self):
        """
        When all documented CI gates are invoked by workflows, linter passes.

        Fixture scenario:
        - tools/CLAUDE.md documents two gates
        - ci.yml invokes both
        - Linter should NOT report gate parity findings
        """
        # Write CLAUDE.md documenting gates
        claudemd_content = """# tools/ — Build utilities

- `verify_test_suite_count.py` — Test suite count drift gate; CLI: `--check` (CI gate)
- `verify_test_coverage.py` — Guardrail G2: CI gate that verifies test coverage
"""
        self._write_claudemd(claudemd_content)

        # Write a workflow that invokes both gates
        workflow_content = """
name: CI
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v7

      - name: Test coverage gate
        run: python tools/verify_test_coverage.py --check

      - name: Test suite count gate
        run: python tools/verify_test_suite_count.py --check
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should NOT report gate parity findings
        gate_parity_findings = [f for f in findings if "gate not invoked" in f.lower()]
        self.assertFalse(gate_parity_findings,
                        f"Should not find gate parity issues, got: {gate_parity_findings}")

    def test_cli_gate_parity_partial_gates_present(self):
        """
        When some documented CI gates are missing from workflows, report only missing ones.

        Fixture scenario:
        - CLAUDE.md documents three gates
        - ci.yml invokes two
        - Linter should report only the missing one
        """
        # Write CLAUDE.md documenting three gates (all with gate keywords)
        claudemd_content = """# tools/ — Build utilities

- `verify_foo.py` — Verification gate for foo feature (self-hosted test port)
- `verify_test_suite_count.py` — Test suite count drift gate; CLI: `--check` (CI gate)
- `verify_test_coverage.py` — Guardrail G2: CI gate that verifies test coverage
"""
        self._write_claudemd(claudemd_content)

        # Write a workflow that invokes only two of them
        workflow_content = """
name: CI
jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - name: Test coverage gate
        run: python tools/verify_test_coverage.py --check

      - name: Test suite count gate
        run: python tools/verify_test_suite_count.py --check
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should report only verify_foo.py as missing
        gate_parity_findings = [f for f in findings if "gate not invoked" in f.lower()]
        self.assertEqual(len(gate_parity_findings), 1,
                        f"Expected one gate parity finding, got: {gate_parity_findings}")
        self.assertIn("verify_foo.py", gate_parity_findings[0],
                     f"Expected verify_foo.py in finding, got: {gate_parity_findings[0]}")

    def test_cli_gate_parity_no_claudemd(self):
        """
        If tools/CLAUDE.md doesn't exist, gate parity check should not fail.

        This is a graceful degradation: the check won't run, but it shouldn't
        cause the linter to crash.
        """
        # Write a minimal valid workflow
        workflow_content = """
name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v7
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter (tools/CLAUDE.md was never written)
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should not crash
        self.assertIsInstance(exit_code, int)
        self.assertIsInstance(findings, list)

    def test_cli_gate_parity_guardrail_markers(self):
        """
        Verify that the gate detection looks for Guardrail markers (G1, G2, etc).

        Fixture scenario:
        - CLAUDE.md has a verify_*.py with "Guardrail G5" in description
        - ci.yml does NOT invoke it
        - Linter should detect it as a documented gate and report it missing
        """
        # Write CLAUDE.md with Guardrail G5 marker
        claudemd_content = """# tools/ — Build utilities

- `verify_sync.py` — Guardrail G5: CLAUDE.md sync gate (blah blah)
"""
        self._write_claudemd(claudemd_content)

        # Write a workflow that doesn't invoke it
        workflow_content = """
name: CI
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Nothing to see here
        run: echo "hello"
"""
        self._write_workflow("ci.yml", workflow_content)

        # Run linter
        exit_code, findings = ci_workflow_lint.lint_workflows(str(self.fixture_root))

        # Should report verify_sync.py as missing
        gate_parity_findings = [f for f in findings if "gate not invoked" in f.lower()]
        self.assertTrue(gate_parity_findings,
                       f"Expected gate parity findings, got: {findings}")
        self.assertIn("verify_sync.py", " ".join(gate_parity_findings))


class GuardrailGateWiringTest(unittest.TestCase):
    """Meta-gate: the six guardrail tools must stay wired as REAL enforcement.

    An evidence audit (2026-07-29) found these tools existed with unit tests
    but were never invoked against the real repo from .github/workflows/ or
    hooks/ -- decorations, while README/#518 claimed enforcement. This class
    asserts each tool's check-mode invocation is present at its enforcement
    point, guarding against future unwiring.

    Enforcement points:
      - CI (.github/workflows/ci.yml): watcher_linter (G3),
        spec_contract_validator (G4), subprocess_guard (G6, ratchet),
        agent_prompt_hygiene, portability_check (ratchet).
      - Pre-push hook (hooks/pre-push-policy.sh): tracker_guard -- its
        subject (state/tracker.json) is git-ignored runtime state that a CI
        checkout never has, so a CI step would be permanently-green
        decoration; the hook runs where the real state lives.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent

    # Exact invocation substrings (not just tool names) so a commented-out or
    # renamed step cannot satisfy the check with a stray mention.
    CI_GATE_INVOCATIONS = [
        "python tools/watcher_linter.py --check",
        "python tools/spec_contract_validator.py --check",
        "python tools/subprocess_guard.py --check --baseline .subprocess-guard-baseline.json",
        "python tools/agent_prompt_hygiene.py .",
        "python tools/portability_check.py --root . --baseline .portability-baseline.json",
    ]

    def _read(self, relative):
        path = self.REAL_REPO_ROOT / relative
        self.assertTrue(path.exists(), "%s missing from repo" % relative)
        return path.read_text(encoding="utf-8")

    def test_ci_workflow_invokes_guardrail_gates(self):
        """Each CI-enforced gate appears as a run command in ci.yml."""
        ci_yml = self._read(".github/workflows/ci.yml")
        for invocation in self.CI_GATE_INVOCATIONS:
            self.assertIn(
                invocation, ci_yml,
                "Guardrail gate unwired from ci.yml: expected '%s'" % invocation,
            )

    def test_ci_gate_steps_are_uncommented_run_commands(self):
        """The gate invocations live on `run:` lines, not comments."""
        ci_yml = self._read(".github/workflows/ci.yml")
        for invocation in self.CI_GATE_INVOCATIONS:
            found = False
            for line in ci_yml.splitlines():
                stripped = line.strip()
                if invocation in stripped:
                    self.assertFalse(
                        stripped.startswith("#"),
                        "Gate invocation is commented out in ci.yml: %s" % stripped,
                    )
                    self.assertTrue(
                        stripped.startswith("run:"),
                        "Gate invocation is not a run command in ci.yml: %s" % stripped,
                    )
                    found = True
            self.assertTrue(found, "Gate invocation missing from ci.yml: %s" % invocation)

    def test_pre_push_hook_invokes_tracker_guard(self):
        """tracker_guard --check is wired into the pre-push policy hook."""
        hook = self._read("hooks/pre-push-policy.sh")
        self.assertIn("tracker_guard.py", hook)
        self.assertIn("check_tracker_guard", hook)
        self.assertRegex(
            hook,
            r'"\$guard_script"\s+--check',
            "tracker_guard.py must be invoked with --check in the hook",
        )
        self.assertIn(
            "if ! check_tracker_guard; then", hook,
            "check_tracker_guard must gate main() fail-closed",
        )

    def test_ratchet_baseline_files_exist_and_parse(self):
        """Both ratchet baselines are committed, parse, and are non-trivial."""
        for name in (".subprocess-guard-baseline.json", ".portability-baseline.json"):
            raw = self._read(name)
            data = json.loads(raw)
            self.assertIsInstance(data.get("violations"), dict, "%s malformed" % name)
            for key, count in data["violations"].items():
                self.assertIn("@", key, "%s: baseline key '%s' not file@type" % (name, key))
                self.assertIsInstance(count, int, "%s: count for '%s' not int" % (name, key))
                self.assertGreater(count, 0, "%s: count for '%s' must be > 0" % (name, key))


class TestToolsImportable(unittest.TestCase):
    """Verify ci_workflow_lint is importable and callable."""

    def test_import_ci_workflow_lint(self):
        """ci_workflow_lint module should import without error."""
        # Already imported at module level above
        self.assertTrue(hasattr(ci_workflow_lint, 'lint_workflows'))
        self.assertTrue(callable(ci_workflow_lint.lint_workflows))

    def test_main_function_exists(self):
        """main function should exist."""
        self.assertTrue(hasattr(ci_workflow_lint, 'main'))
        self.assertTrue(callable(ci_workflow_lint.main))


if __name__ == "__main__":
    unittest.main()
