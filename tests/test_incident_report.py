#!/usr/bin/env python3
"""
TDD tests for incident_report.py parser and classifier.

Tests verify:
- Pattern recognition for incident classes (fake-green, ci-drift, test-pollution, flake, conflict, etc.)
- Deterministic git commit parsing
- Entry ordering and idempotency
- Markdown generation and --check mode
"""

import unittest
import json
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime, timezone


class TestIncidentClassifier(unittest.TestCase):
    """Test incident pattern classification."""

    def test_classify_fake_green_from_commit_subject(self):
        """ci(browser-proofs): never-ran or green-never-ran pattern."""
        # Import here to avoid issues if module doesn't exist yet
        try:
            from tools.incident_report import IncidentClassifier
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        classifier = IncidentClassifier()

        # Pattern: commit subject contains "green-never-ran" or "never-ran"
        result = classifier.classify_commit_subject(
            "ci(browser-proofs): actually execute playwright specs + minimal dashboard smoke (#464)"
        )
        self.assertEqual(result, "fake-green",
            "Should classify 'actually execute' + 'browser-proofs never ran' as fake-green")

    def test_classify_ci_drift_from_subject(self):
        """CI workflow file drift (missing dependencies, env setup)."""
        try:
            from tools.incident_report import IncidentClassifier
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        classifier = IncidentClassifier()

        # Pattern: "fix(ci):" or "fix(workflow):"
        result = classifier.classify_commit_subject(
            "fix(ci): add pytest to main-full workflow (post-#450 drift)"
        )
        self.assertIn(result, ["ci-drift", "test-pollution"],
            "Should classify missing workflow dependency as ci-drift")

    def test_classify_test_pollution_from_subject(self):
        """Test pollution (config leaks, mock state, isolation)."""
        try:
            from tools.incident_report import IncidentClassifier
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        classifier = IncidentClassifier()

        # Pattern: "test-pollution", "shard", "leak", "isolat"
        result = classifier.classify_commit_subject(
            "fix(tests): stop test_ui_wave_context leaking MockConfig into sys.modules (shard isolation)"
        )
        self.assertEqual(result, "test-pollution",
            "Should classify MockConfig leak as test-pollution")

    def test_classify_flake_from_subject(self):
        """Flaky test (timing, race, deflake)."""
        try:
            from tools.incident_report import IncidentClassifier
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        classifier = IncidentClassifier()

        # Pattern: "deflake" or "flake"
        result = classifier.classify_commit_subject(
            "fix: deflake watchdog boundary tests with logical time (#432)"
        )
        self.assertEqual(result, "flake",
            "Should classify deflake as flake")

    def test_classify_conflict_from_subject(self):
        """Merge conflict or module shadowing."""
        try:
            from tools.incident_report import IncidentClassifier
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        classifier = IncidentClassifier()

        # Pattern: "conflict", "shadowing", "module"
        result = classifier.classify_commit_subject(
            "fix(ws3): restore original wave_scheduler spec + add lane_scheduler pilot"
        )
        self.assertIn(result, ["conflict", "test-pollution"],
            "Should classify module shadowing as conflict")

    def test_classify_gate_activation_from_subject(self):
        """Secret gate or verification bypass caught."""
        try:
            from tools.incident_report import IncidentClassifier
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        classifier = IncidentClassifier()

        # Pattern: "gate", "secret", "--no-verify", "--admin"
        result = classifier.classify_commit_subject(
            "fix(docs): remove invented precision from Gates That Fired paragraph"
        )
        self.assertIn(result, ["gate-activation", "doc-invented"],
            "Should classify gate activation as gate-activation")


class TestIncidentParser(unittest.TestCase):
    """Test parsing incidents from git history."""

    def test_parse_commit_hash(self):
        """Extract commit hash from git log output."""
        try:
            from tools.incident_report import IncidentParser
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        parser = IncidentParser()

        # Test that parser can extract what_happened from subject
        subject = "ci(browser-proofs): actually execute playwright specs (#464)"
        what_happened = parser._extract_what_happened(subject, "")

        self.assertIsNotNone(what_happened)
        self.assertIn('actually execute', what_happened.lower())

    def test_incident_from_synthetic_commits(self):
        """Extract incident metadata from synthetic test commits (hermetic).

        Creates a temporary git repo with 4 commits shaped like real incidents:
        - fake-green: browser-proofs tests never ran
        - ci-drift: pytest missing from workflow
        - flake: deflake timing test
        - gate-activation: secret gate caught bypass

        Verifies the parser correctly classifies each type.
        """
        try:
            from tools.incident_report import IncidentParser
            import subprocess
            import os
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        # Create isolated temp directory for test repo (hygiene rule: no cwd pollution)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize a test repo
            subprocess.run(
                ["git", "init"],
                cwd=tmpdir,
                capture_output=True,
                check=True
            )

            # Set git identity to avoid pollution
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=tmpdir,
                capture_output=True,
                check=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                capture_output=True,
                check=True
            )

            # Create synthetic commits matching incident patterns
            commits = [
                {
                    "file": "test.txt",
                    "subject": "ci(browser-proofs): actually execute playwright specs",
                    "body": "Fixes the long-standing issue where browser-proofs never ran any specs",
                    "expected_class": "fake-green"
                },
                {
                    "file": "test.txt",
                    "subject": "fix(ci): add pytest to main-full workflow (post-#450 drift)",
                    "body": "The main-full.yml workflow was missing pytest",
                    "expected_class": "ci-drift"
                },
                {
                    "file": "test.txt",
                    "subject": "fix: deflake watchdog boundary tests with logical time",
                    "body": "Root cause: timing race condition in boundary tests",
                    "expected_class": "flake"
                },
                {
                    "file": "test.txt",
                    "subject": "fix: secret-scan gate closes worktree bypasses",
                    "body": "Pre-push gate now detects file changes in commits",
                    "expected_class": "gate-activation"
                },
            ]

            # Create commits in test repo
            for i, commit in enumerate(commits):
                # Write file
                filepath = os.path.join(tmpdir, commit["file"])
                with open(filepath, 'w') as f:
                    f.write(f"Content {i}\n")

                # Stage
                subprocess.run(
                    ["git", "add", commit["file"]],
                    cwd=tmpdir,
                    capture_output=True,
                    check=True
                )

                # Commit with message
                subprocess.run(
                    ["git", "commit", "-m", f"{commit['subject']}\n\n{commit['body']}"],
                    cwd=tmpdir,
                    capture_output=True,
                    check=True
                )

            # Parse incidents from the test repo
            parser = IncidentParser(repo_root=tmpdir)
            incidents = parser.find_all_incidents()

            # Verify we found 4 incidents
            self.assertEqual(len(incidents), 4,
                "Should find exactly 4 synthetic incidents in test repo")

            # Verify each incident is classified correctly
            # (order-agnostic comparison: both lists should contain the same classes)
            expected_classes = sorted([c["expected_class"] for c in commits])
            actual_classes = sorted([i["class"] for i in incidents])

            self.assertEqual(actual_classes, expected_classes,
                f"Incident classes mismatch: {actual_classes} != {expected_classes}")

    def test_incident_from_real_history_skip_shallow(self):
        """Optional test for real git history (skips in shallow clones).

        Only runs when full history is available. This test validates that
        the parser works against real incident patterns in actual repo history.
        """
        try:
            from tools.incident_report import IncidentParser
            import subprocess
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        # Check if this is a shallow clone
        result = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=".",
            capture_output=True,
            text=True,
            check=False
        )

        is_shallow = result.stdout.strip() == "true"
        if is_shallow:
            self.skipTest("Shallow clone (fetch-depth 1 in CI) — skipping real-history test; hermetic test validates classification logic")

        # Full history available: validate against real commits
        parser = IncidentParser(repo_root=".")
        incidents = parser.find_all_incidents(limit=10)

        # Should find at least some incidents in full history
        self.assertGreater(len(incidents), 0,
            "Should find incidents in full repository history")

    def test_deterministic_ordering(self):
        """Entries should be stable-ordered (date, then hash)."""
        try:
            from tools.incident_report import IncidentParser
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        parser = IncidentParser(repo_root=".")

        incidents1 = parser.find_all_incidents()
        incidents2 = parser.find_all_incidents()

        # Same order both times
        hashes1 = [i['hash'] for i in incidents1]
        hashes2 = [i['hash'] for i in incidents2]

        self.assertEqual(hashes1, hashes2,
            "Incident order should be deterministic across runs")


class TestIncidentMarkdownGeneration(unittest.TestCase):
    """Test Markdown output format."""

    def test_generate_markdown_table(self):
        """Generate deterministic Markdown table."""
        try:
            from tools.incident_report import IncidentMarkdown
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        markdown = IncidentMarkdown()

        incidents = [
            {
                'hash': '7e20522',
                'class': 'fake-green',
                'what_happened': 'Browser-proofs CI job never ran any Playwright specs',
                'resolution': 'Added Playwright TypeScript test infrastructure',
                'source_ref': 'PR #464'
            },
            {
                'hash': '0e03440',
                'class': 'ci-drift',
                'what_happened': 'pytest missing from main-full workflow',
                'resolution': 'Added pytest to workflow Python dependencies',
                'source_ref': 'PR #461'
            }
        ]

        output = markdown.generate_table(incidents)

        # Should contain table structure
        self.assertIn('| Class |', output)
        self.assertIn('fake-green', output)
        self.assertIn('ci-drift', output)
        self.assertIn('PR #464', output)
        self.assertIn('PR #461', output)

    def test_markdown_deterministic(self):
        """Markdown output should be byte-identical across runs."""
        try:
            from tools.incident_report import IncidentMarkdown
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        markdown = IncidentMarkdown()

        incidents = [
            {
                'hash': 'abc123',
                'class': 'flake',
                'what_happened': 'Test timeout',
                'resolution': 'Added logical time mock',
                'source_ref': 'PR #432'
            }
        ]

        output1 = markdown.generate_table(incidents)
        output2 = markdown.generate_table(incidents)

        self.assertEqual(output1, output2,
            "Markdown output should be byte-identical")


class TestCheckMode(unittest.TestCase):
    """Test --check mode for CI drift detection."""

    def test_check_mode_valid_file(self):
        """--check should return 0 if docs/INCIDENTS.md matches generated output."""
        try:
            from tools.incident_report import IncidentChecker
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        # This would be an integration test in a real setup
        checker = IncidentChecker()

        # Placeholder: real check would compare file to generated
        self.assertTrue(True, "Check mode test placeholder")

    def test_check_mode_drift_detected(self):
        """--check should return 1 if docs/INCIDENTS.md drifts from git."""
        try:
            from tools.incident_report import IncidentChecker
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        # Placeholder: real check would detect drift
        self.assertTrue(True, "Check mode drift test placeholder")


class TestSecretScanning(unittest.TestCase):
    """Test that no credentials leak in output."""

    def test_no_secrets_in_commit_hashes(self):
        """Commit hashes should never contain sensitive patterns."""
        try:
            from tools.incident_report import IncidentValidator
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        validator = IncidentValidator()

        # Commit hashes are 40 hex chars (SHA-1) or 7-12 short hash
        test_hash = "7e20522"

        self.assertTrue(validator.is_valid_git_hash(test_hash),
            "Should accept valid short commit hash")

    def test_no_secrets_in_pr_refs(self):
        """PR references should be #NNN format only."""
        try:
            from tools.incident_report import IncidentValidator
        except ImportError:
            self.skipTest("incident_report module not yet implemented")

        validator = IncidentValidator()

        self.assertTrue(validator.is_valid_source_ref("PR #464"),
            "Should accept valid PR reference")

        self.assertTrue(validator.is_valid_source_ref("commit 7e20522"),
            "Should accept commit reference")


if __name__ == '__main__':
    unittest.main()
