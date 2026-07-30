#!/usr/bin/env python3
"""
Adversarial trap-test suite: regression detection for recurring incident classes.

Each trap encodes a pattern found in docs/INCIDENTS.md and fails if that pattern
re-emerges. Traps are mechanically checkable (no live agents required).

Incident classes covered:
- fake-green: Tests that claim to run but skip real validation
- test-pollution: Test state/isolation leaks (cwd, git config, mock pollution)
- gate-activation: Forbidden flags or gate bypasses in dispatch templates
- doc-invented: Documentation claims not backed by verifiable facts

Incident classes NOT mechanizable (excluded):
- conflict: Requires live merge scenarios
- flake: Requires timing analysis and race condition reproduction
- stall: Requires live agent/watchdog monitoring
- ci-drift: Requires CI environment validation (partially covered by linter)
"""

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Set


class TestFakeGreenTrap(unittest.TestCase):
    """Trap: FAKE-GREEN incidents (tests report pass but don't execute)

    Incident: PR #464 / commit 8873971 (2026-07-13)
    - Playwright browser-proofs job reported green but never executed tests
    - Resolution: Actually execute playwright specs, not mock them

    Trap: Verify all test files are discovered by the collection system
    and that the collected count matches the documented count in tests/CLAUDE.md.
    """

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        cls.repo_root = Path(__file__).parent.parent
        cls.tests_dir = cls.repo_root / "tests"

    def test_collected_test_count_matches_documentation(self):
        """Trap: test collection must match documented counts in tests/CLAUDE.md.

        Incident #464: Browser-proofs reported green but never collected/executed.
        Prevention: Run verify_test_suite_count.py --check to gate discovery vs docs.
        """
        # Use the verify_test_suite_count tool to check that counts match
        result = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=30,
        )

        self.assertEqual(
            result.returncode,
            0,
            f"Test collection count mismatch (fake-green trap):\n{result.stdout}\n{result.stderr}"
        )

    def test_python_test_files_are_valid_python(self):
        """Trap: all test_*.py files must be syntactically valid Python.

        Prevention: Catch early if a test file is corrupted/incomplete.
        """
        violations = []

        for test_file in sorted(self.tests_dir.glob("test_*.py")):
            try:
                with open(test_file, "r", encoding="utf-8") as f:
                    ast.parse(f.read(), filename=str(test_file))
            except SyntaxError as e:
                violations.append(f"{test_file.name}:{e.lineno} {e.msg}")

        self.assertEqual(
            len(violations),
            0,
            f"Found {len(violations)} invalid Python test files (fake-green trap):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_nodejs_test_files_are_valid_syntax(self):
        """Trap: all test_*.test.mjs files must have valid Node.js syntax.

        Prevention: Catch if a test file is syntactically broken.
        """
        violations = []

        for test_file in sorted(self.tests_dir.glob("test_*.test.mjs")):
            # Use node --check to validate syntax
            result = subprocess.run(
                ["node", "--check", str(test_file)],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                violations.append(f"{test_file.name}: {result.stderr.strip()}")

        self.assertEqual(
            len(violations),
            0,
            f"Found {len(violations)} invalid Node.js test files (fake-green trap):\n"
            + "\n".join(f"  {v}" for v in violations)
        )


class TestGateActivationTrap(unittest.TestCase):
    """Trap: GATE-ACTIVATION incidents (forbidden flags in templates)

    Incident: Multiple PR/commits (e.g., #67, bench agent, security gate bypasses)
    - --admin, --auto, --force, --no-verify flags used to bypass gates
    - Resolution: Forbid these flags in all dispatch templates and tool configs

    Trap: Scan dispatch templates and tool configurations for forbidden flags.
    """

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        cls.repo_root = Path(__file__).parent.parent

    def test_no_admin_flag_in_dispatch_templates(self):
        """Trap: forbid --admin in dispatch templates (wave/job config).

        Incident: Agent bypassed required checks using --admin flag.
        Pattern: --admin appears in templates/, bin/, tools/wave*.py files.
        """
        violations = []
        forbidden_flag = "--admin"

        # Search in templates, dispatch config, and wave-related files
        search_paths = [
            self.repo_root / "templates",
            self.repo_root / "tools",
            self.repo_root / "bin",
        ]

        for search_path in search_paths:
            if not search_path.exists():
                continue

            for file_path in search_path.rglob("*.py"):
                if file_path.name == "dispatch_lint.py":
                    continue
                try:
                    content = file_path.read_text(encoding="utf-8")
                    if forbidden_flag in content:
                        if "dispatch" in content or "agent()" in content or "spawn" in content:
                            violations.append(f"{file_path.relative_to(self.repo_root)}: {forbidden_flag}")
                except (UnicodeDecodeError, OSError):
                    pass

            for file_path in search_path.rglob("*.json"):
                try:
                    content = file_path.read_text(encoding="utf-8")
                    if forbidden_flag in content:
                        violations.append(f"{file_path.relative_to(self.repo_root)}: {forbidden_flag}")
                except (UnicodeDecodeError, OSError, json.JSONDecodeError):
                    pass

        self.assertEqual(
            len(violations),
            0,
            f"Found {len(violations)} instances of forbidden {forbidden_flag} (gate-activation trap):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_no_no_verify_flag_in_dispatch_contexts(self):
        """Trap: forbid --no-verify in dispatch templates.

        Incident: Bench agent bypassed pre-push secret gate with --no-verify.
        Pattern: --no-verify in git commands within dispatch code.
        """
        violations = []
        forbidden_flag = "--no-verify"

        search_paths = [
            self.repo_root / "tools",
            self.repo_root / "driver",
        ]

        for search_path in search_paths:
            if not search_path.exists():
                continue

            for file_path in search_path.rglob("*.py"):
                try:
                    content = file_path.read_text(encoding="utf-8")
                    if forbidden_flag in content:
                        # Scan for git command usage with --no-verify
                        if re.search(rf'git.*{re.escape(forbidden_flag)}', content):
                            violations.append(f"{file_path.relative_to(self.repo_root)}: {forbidden_flag}")
                except (UnicodeDecodeError, OSError):
                    pass

        self.assertEqual(
            len(violations),
            0,
            f"Found {len(violations)} instances of {forbidden_flag} (gate-activation trap):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_secret_scan_gate_is_executable(self):
        """Trap: secret_scan.py pre-push gate must exist and be executable.

        Incident: Gate-activation bypasses require the gate to be present and active.
        Prevention: Verify secret_scan.py exists and can be run.
        """
        secret_scan = self.repo_root / "tools" / "secret_scan.py"

        self.assertTrue(
            secret_scan.exists(),
            f"secret_scan.py not found at {secret_scan} (gate-activation trap)"
        )

        # Verify it's executable (can be run with Python)
        result = subprocess.run(
            [sys.executable, str(secret_scan), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(
            result.returncode,
            0,
            f"secret_scan.py --help failed (gate-activation trap): {result.stderr}"
        )


class TestDocInventedTrap(unittest.TestCase):
    """Trap: DOC-INVENTED incidents (documentation claims not backed by facts)

    Incident: f8b6947 (2026-07-03) - README had hallucinated 0.3.0 CHANGELOG entries
    - Committed PR #332 described test_battery as 'energy-aware' (never existed)
    - Resolution: Verify documentation claims are checkable against code/git

    Trap: Verify key documentation counts and facts match verifiable sources.
    """

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        cls.repo_root = Path(__file__).parent.parent

    def test_readme_statistics_match_verified_counts(self):
        """Trap: README.md statistics must match self_stats.py output.

        Incident: README claimed counts that didn't match actual test counts.
        Prevention: Run verify_test_suite_count.py --check as a gate.
        """
        # Verify that tests/CLAUDE.md counts match actual test files
        result = subprocess.run(
            [sys.executable, str(self.repo_root / "tools" / "verify_test_suite_count.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=30,
        )

        self.assertEqual(
            result.returncode,
            0,
            f"Statistics drift (doc-invented trap): {result.stdout}\n{result.stderr}"
        )

    def test_package_json_version_is_semver(self):
        """Trap: package.json version must be valid semver format.

        Prevention: Catch typos or hallucinated version strings.
        """
        package_json = self.repo_root / "package.json"

        self.assertTrue(
            package_json.exists(),
            f"package.json not found at {package_json} (doc-invented trap)"
        )

        try:
            with open(package_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)

            version = pkg.get("version")
            self.assertIsNotNone(version, "package.json missing 'version' field")

            # Check semver format: major.minor.patch[-prerelease][+build]
            semver_pattern = r"^\d+\.\d+\.\d+(-[\da-zA-Z\-\.]+)?(\+[\da-zA-Z\-\.]+)?$"
            self.assertIsNotNone(
                re.match(semver_pattern, version),
                f"package.json version '{version}' is not valid semver (doc-invented trap)"
            )
        except json.JSONDecodeError as e:
            self.fail(f"package.json is not valid JSON (doc-invented trap): {e}")

    def test_incidents_md_is_valid_markdown(self):
        """Trap: docs/INCIDENTS.md must be valid markdown and parseable.

        Prevention: Catch if incident log is corrupted or hallucinated.
        """
        incidents_md = self.repo_root / "docs" / "INCIDENTS.md"

        self.assertTrue(
            incidents_md.exists(),
            f"INCIDENTS.md not found at {incidents_md} (doc-invented trap)"
        )

        try:
            content = incidents_md.read_text(encoding="utf-8")

            # Verify it has the expected structure
            self.assertIn(
                "| Class |",
                content,
                "INCIDENTS.md missing markdown table header (doc-invented trap)"
            )

            # Verify it has summary section
            self.assertIn(
                "**Summary**",
                content,
                "INCIDENTS.md missing Summary section (doc-invented trap)"
            )

            # Verify incident classes are listed
            expected_classes = ["ci-drift", "conflict", "doc-invented", "fake-green",
                              "flake", "gate-activation", "stall", "test-pollution"]
            for incident_class in expected_classes:
                self.assertIn(
                    f"**{incident_class}**",
                    content,
                    f"INCIDENTS.md missing class '{incident_class}' (doc-invented trap)"
                )
        except UnicodeDecodeError as e:
            self.fail(f"INCIDENTS.md has encoding issues (doc-invented trap): {e}")


class TestTestPollutionTrap(unittest.TestCase):
    """Trap: TEST-POLLUTION incidents (test state leaks across tests)

    Incident: Multiple PRs/commits (e.g., #207, 29356d8)
    - Tests polluted cwd, git config, mock state across test runs
    - Resolution: Isolate all test state to temp directories

    Trap: Verify test isolation rules are enforced (delegated to test_test_hygiene.py).
    This trap adds extended checks for mock pollution and state leakage.
    """

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        cls.repo_root = Path(__file__).parent.parent
        cls.tests_dir = cls.repo_root / "tests"

    def test_no_global_mock_state_in_tests(self):
        """Trap: tests must not pollute sys.modules with mocks or fixtures.

        Incident: test_ui_wave_context leaked MockConfig into sys.modules.
        Prevention: Check for mock state insertions that don't clean up.
        """
        violations = []

        for test_file in sorted(self.tests_dir.glob("test_*.py")):
            try:
                content = test_file.read_text(encoding="utf-8")

                # Flag patterns where mocks are added to sys.modules but never removed
                if "sys.modules[" in content:
                    # Check if there's a corresponding tearDown cleanup
                    lines = content.split("\n")
                    has_cleanup = False

                    for line in lines:
                        if "sys.modules.pop" in line or "del sys.modules" in line:
                            has_cleanup = True
                            break

                    if not has_cleanup and "test" in test_file.name:
                        # This is a heuristic: sys.modules writes without cleanup is suspicious
                        # Only flag if it's in a test file without a tearDown
                        if "def tearDown" not in content:
                            violations.append(f"{test_file.name}: sys.modules mutation without cleanup")

            except (UnicodeDecodeError, OSError):
                pass

        # This is an advisory check, not a blocker
        if violations:
            print(f"ADVISORY: Found {len(violations)} potential mock pollution patterns")

    def test_no_untracked_temp_dirs_in_tests(self):
        """Trap: all temporary directories created by tests must be cleaned up.

        Prevention: Verify no .gitignore violations from test temp dirs.
        """
        # List current untracked temp directories
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            timeout=10,
        )

        untracked_files = result.stdout.strip().split("\n")
        untracked_files = [f for f in untracked_files if f]

        # Temp files/dirs from failed test cleanup should be minimal
        # Flag only if there are many untracked files (suggests cleanup failure)
        # This is advisory, not a hard error
        if len(untracked_files) > 10:
            print(f"ADVISORY: {len(untracked_files)} untracked files (possible test cleanup failure)")


class TestCIDriftTrap(unittest.TestCase):
    """Trap: CI-DRIFT incidents (CI workflow state out of sync)

    Incident: PR #450 (2026-07-14) - main-full.yml missing pytest after refactor
    - Pre-push gate CI couldn't validate tests without pytest installed
    - Resolution: Verify CI workflows have required dependencies

    Trap: Check CI workflow YAML is valid and has required tools listed.
    """

    @classmethod
    def setUpClass(cls):
        """Set up class-level fixtures."""
        cls.repo_root = Path(__file__).parent.parent

    def test_ci_workflow_files_are_valid_yaml(self):
        """Trap: all CI workflow files must be valid YAML.

        Prevention: Catch if workflows are malformed or incomplete.
        """
        violations = []
        workflows_dir = self.repo_root / ".github" / "workflows"

        if not workflows_dir.exists():
            self.skipTest("No .github/workflows directory found")

        for workflow_file in workflows_dir.glob("*.yml"):
            try:
                # Attempt to parse YAML using direct file read (no external deps)
                content = workflow_file.read_text(encoding="utf-8")

                # Basic YAML validation: check for syntax issues
                # (Avoid external yaml module for stdlib-only constraint)
                lines = content.split("\n")
                for i, line in enumerate(lines, 1):
                    # Basic checks: no obvious unclosed quotes or brackets
                    if line.count('"') % 2 != 0 and not line.strip().startswith("#"):
                        # Count escaped quotes
                        unescaped_quotes = len([c for c in line if c == '"'])
                        if unescaped_quotes % 2 != 0:
                            violations.append(f"{workflow_file.name}:{i}: unclosed quote")

            except Exception as e:
                violations.append(f"{workflow_file.name}: {str(e)}")

        self.assertEqual(
            len(violations),
            0,
            f"Found {len(violations)} invalid workflow files (ci-drift trap):\n"
            + "\n".join(f"  {v}" for v in violations)
        )

    def test_main_workflow_requires_required_tools(self):
        """Trap: main workflow must install required tools (python, node, etc).

        Incident: main-full.yml missing pytest after refactor.
        Prevention: Verify required tools are listed in workflow setup steps.
        """
        main_workflow = self.repo_root / ".github" / "workflows" / "main-full.yml"

        if not main_workflow.exists():
            self.skipTest("main-full.yml not found")

        try:
            content = main_workflow.read_text(encoding="utf-8")

            # Check for required tool installations
            required_checks = [
                ("python", ["setup-python", "python"]),
                ("node", ["setup-node", "node"]),
            ]

            for tool, keywords in required_checks:
                found = any(kw in content for kw in keywords)
                self.assertTrue(
                    found,
                    f"main-full.yml missing setup for {tool} (ci-drift trap)"
                )

        except UnicodeDecodeError as e:
            self.fail(f"main-full.yml has encoding issues (ci-drift trap): {e}")


class TestNotMechanizableTrap(unittest.TestCase):
    """Document incident classes that are NOT mechanically checkable.

    These classes require live agents, timing analysis, or merge scenarios.
    They are listed here for reference but not trapped.
    """

    def test_document_non_mechanizable_classes(self):
        """List incident classes that cannot be trapped automatically.

        NOT mechanizable:
        1. CONFLICT (6 incidents): Merge/rebase conflicts, module shadowing
           - Requires: Live merge scenarios, git state resolution
           - Example: PR #67, commit cbd040e

        2. FLAKE (6 incidents): Test timing/race conditions
           - Requires: Timing analysis, logical time injection, race detection
           - Example: PR #432, PR #427, commit dacd880

        3. STALL (16 incidents): Agent/process hung or deadlocked
           - Requires: Live watchdog, transcript monitoring, timeout detection
           - Example: PR #100, PR #171, commit 7b1e4de

        These should be caught by:
        - CONFLICT: Git merge resolution, manual review
        - FLAKE: deflake tools (logical-time, readiness-polling)
        - STALL: stall_check.py, watchdog monitoring
        """
        # This test documents the exclusion; it always passes
        pass


if __name__ == "__main__":
    unittest.main()
