#!/usr/bin/env python3
"""Tests for CI throughput optimizations: PR head-SHA checkout, Windows gating, aggregator reporting.

These tests pin down the four mechanisms that reduce per-PR job count:

1. PR runs check out the PR HEAD commit (not merge commit), so a red main
   does not turn every PR red; main-full + regen-on-main own integration.

2. Windows matrix (windows-shard + full Windows in main-full) gated on pull_request
   by windows-sensitive-paths job; always runs on workflow_dispatch, schedule, push to main.

3. `windows` aggregator always reports a conclusion (success when matrix was
   intentionally skipped on pull_request).

4. Nightly schedule at 03:30 UTC runs full CI matrix on all platforms.

Hermetic: parses .github/workflows/ci.yml and main-full.yml YAML, no network/gh.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class CIThroughputTests(unittest.TestCase):
    """Tests for CI throughput optimizations."""

    @classmethod
    def setUpClass(cls):
        """Load workflow files once."""
        ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        main_full_path = REPO_ROOT / ".github" / "workflows" / "main-full.yml"

        with open(ci_path, "r") as f:
            cls.ci_workflow = yaml.safe_load(f)
        with open(main_full_path, "r") as f:
            cls.main_full_workflow = yaml.safe_load(f)

    def test_ci_has_schedule_trigger(self):
        """ci.yml must have a schedule trigger for nightly runs."""
        # YAML parses 'on:' as boolean True, not string 'on'
        on = self.ci_workflow.get(True, {})
        self.assertIsInstance(on, dict, "on: section must be a dict")
        self.assertIn("schedule", on, "ci.yml missing schedule trigger")
        schedule = on["schedule"]
        self.assertIsInstance(schedule, list, "schedule must be a list")
        self.assertGreater(len(schedule), 0, "schedule list is empty")
        # Check for 03:30 UTC (30 3 * * *)
        cron_found = False
        for entry in schedule:
            if isinstance(entry, dict) and "cron" in entry:
                if "30 3 * * *" in entry["cron"]:
                    cron_found = True
                    break
        self.assertTrue(cron_found, "schedule must include 03:30 UTC cron (30 3 * * *)")

    def test_main_full_has_schedule_trigger(self):
        """main-full.yml must have a schedule trigger for nightly runs."""
        # YAML parses 'on:' as boolean True, not string 'on'
        on = self.main_full_workflow.get(True, {})
        self.assertIsInstance(on, dict, "on: section must be a dict")
        self.assertIn("schedule", on, "main-full.yml missing schedule trigger")
        schedule = on["schedule"]
        self.assertIsInstance(schedule, list, "schedule must be a list")
        self.assertGreater(len(schedule), 0, "schedule list is empty")
        # Check for 03:30 UTC (30 3 * * *)
        cron_found = False
        for entry in schedule:
            if isinstance(entry, dict) and "cron" in entry:
                if "30 3 * * *" in entry["cron"]:
                    cron_found = True
                    break
        self.assertTrue(cron_found, "schedule must include 03:30 UTC cron (30 3 * * *)")

    def test_ci_has_windows_sensitive_paths_job(self):
        """ci.yml must have windows-sensitive-paths job."""
        jobs = self.ci_workflow.get("jobs", {})
        self.assertIn(
            "windows-sensitive-paths",
            jobs,
            "ci.yml missing windows-sensitive-paths job"
        )

    def test_windows_sensitive_paths_detects_windows_paths(self):
        """windows-sensitive-paths must detect Windows-sensitive file changes."""
        jobs = self.ci_workflow.get("jobs", {})
        wsp_job = jobs.get("windows-sensitive-paths", {})
        steps = wsp_job.get("steps", [])

        # Find the "Detect Windows-sensitive changes" step
        detect_step = None
        for step in steps:
            if step.get("name") == "Detect Windows-sensitive changes":
                detect_step = step
                break

        self.assertIsNotNone(detect_step, "Missing 'Detect Windows-sensitive changes' step")

        # Verify it outputs windows_touched
        step_id = detect_step.get("id")
        self.assertEqual(step_id, "windows-touched", "Step id must be 'windows-touched'")

        run = detect_step.get("run", "")
        # Check for detection of key Windows-sensitive paths
        self.assertIn("daemons/", run, "Must detect daemons/ changes")
        self.assertIn("hooks/", run, "Must detect hooks/ changes")
        self.assertIn(".ps1", run, "Must detect *.ps1 files")
        self.assertIn("tools/", run, "Must detect tools/ changes")
        self.assertIn("bin/", run, "Must detect bin/ changes")
        self.assertIn("package.json", run, "Must detect package.json changes")

    def test_ci_job_uses_pr_head_sha(self):
        """ci job must check out PR HEAD commit for pull_request events."""
        jobs = self.ci_workflow.get("jobs", {})
        ci_job = jobs.get("ci", {})
        steps = ci_job.get("steps", [])

        # Find the checkout step
        checkout_step = None
        for step in steps:
            if step.get("uses", "").startswith("actions/checkout"):
                checkout_step = step
                break

        self.assertIsNotNone(checkout_step, "ci job must have checkout step")

        # Check for ref parameter that uses PR head SHA
        with_section = checkout_step.get("with", {})
        ref = with_section.get("ref", "")
        self.assertIn(
            "pull_request",
            ref,
            "checkout ref must condition on pull_request event"
        )
        self.assertIn(
            "head.sha",
            ref,
            "checkout ref must use github.event.pull_request.head.sha for PR"
        )

    def test_windows_shard_job_exists(self):
        """windows-shard job must exist in ci.yml."""
        jobs = self.ci_workflow.get("jobs", {})
        self.assertIn("windows-shard", jobs, "ci.yml missing windows-shard job")

    def test_windows_shard_depends_on_windows_sensitive_paths(self):
        """windows-shard must depend on windows-sensitive-paths job."""
        jobs = self.ci_workflow.get("jobs", {})
        windows_shard = jobs.get("windows-shard", {})
        needs = windows_shard.get("needs", [])

        # needs can be string or list
        if isinstance(needs, str):
            needs = [needs]

        self.assertIn(
            "windows-sensitive-paths",
            needs,
            "windows-shard must depend on windows-sensitive-paths"
        )

    def test_windows_shard_gates_on_windows_paths(self):
        """windows-shard must gate on windows_touched output on pull_request."""
        jobs = self.ci_workflow.get("jobs", {})
        windows_shard = jobs.get("windows-shard", {})
        if_condition = windows_shard.get("if", "")

        self.assertIn(
            "windows-sensitive-paths",
            if_condition,
            "windows-shard if: must reference windows-sensitive-paths"
        )
        self.assertIn(
            "windows_touched",
            if_condition,
            "windows-shard if: must check windows_touched output"
        )
        self.assertIn(
            "pull_request",
            if_condition,
            "windows-shard if: must condition on pull_request"
        )

    def test_windows_shard_uses_pr_head_sha(self):
        """windows-shard must check out PR HEAD commit for pull_request events."""
        jobs = self.ci_workflow.get("jobs", {})
        windows_shard = jobs.get("windows-shard", {})
        steps = windows_shard.get("steps", [])

        # Find the checkout step
        checkout_step = None
        for step in steps:
            if step.get("uses", "").startswith("actions/checkout"):
                checkout_step = step
                break

        self.assertIsNotNone(checkout_step, "windows-shard must have checkout step")

        # Check for ref parameter that uses PR head SHA
        with_section = checkout_step.get("with", {})
        ref = with_section.get("ref", "")
        self.assertIn(
            "pull_request",
            ref,
            "windows-shard checkout ref must condition on pull_request"
        )
        self.assertIn(
            "head.sha",
            ref,
            "windows-shard checkout ref must use github.event.pull_request.head.sha for PR"
        )

    def test_windows_aggregator_exists(self):
        """windows aggregator job must exist in ci.yml."""
        jobs = self.ci_workflow.get("jobs", {})
        self.assertIn("windows", jobs, "ci.yml missing windows aggregator job")

    def test_windows_aggregator_depends_on_windows_shard_and_paths(self):
        """windows aggregator must depend on both windows-shard and windows-sensitive-paths."""
        jobs = self.ci_workflow.get("jobs", {})
        windows = jobs.get("windows", {})
        needs = windows.get("needs", [])

        # needs can be string or list
        if isinstance(needs, str):
            needs = [needs]

        self.assertIn(
            "windows-shard",
            needs,
            "windows aggregator must depend on windows-shard"
        )
        self.assertIn(
            "windows-sensitive-paths",
            needs,
            "windows aggregator must depend on windows-sensitive-paths"
        )

    def test_windows_aggregator_always_reports(self):
        """windows aggregator must have if: always() and handle skipped case."""
        jobs = self.ci_workflow.get("jobs", {})
        windows = jobs.get("windows", {})

        if_condition = windows.get("if", "")
        self.assertIn(
            "always()",
            if_condition,
            "windows aggregator must have if: always() to always report"
        )

        # Check the step logic handles the skipped case
        steps = windows.get("steps", [])
        self.assertGreater(len(steps), 0, "windows aggregator must have steps")

        # Look for step that checks for skipped case
        found_skip_check = False
        for step in steps:
            run = step.get("run", "")
            if "pull_request" in run and "windows_touched" in run:
                found_skip_check = True
                break

        self.assertTrue(
            found_skip_check,
            "windows aggregator must check for skipped Windows-sensitive-paths in its step"
        )

    def test_main_full_has_windows_shard_matrix(self):
        """main-full.yml must have sharded Windows tests (0-3) like ci.yml."""
        jobs = self.main_full_workflow.get("jobs", {})
        main_full_verify = jobs.get("main-full-verify", {})

        strategy = main_full_verify.get("strategy", {})
        matrix = strategy.get("matrix", {})

        self.assertIn(
            "python-shard",
            matrix,
            "main-full-verify must have python-shard in matrix"
        )

        shards = matrix.get("python-shard", [])
        self.assertEqual(
            shards,
            [0, 1, 2, 3],
            "main-full-verify must shard Python tests 0-3"
        )

    def test_main_full_excludes_unnecessary_ubuntu_shards(self):
        """main-full.yml must exclude unnecessary Ubuntu shards (runs shard 0 only)."""
        jobs = self.main_full_workflow.get("jobs", {})
        main_full_verify = jobs.get("main-full-verify", {})

        strategy = main_full_verify.get("strategy", {})
        exclude = strategy.get("exclude", [])

        # Ubuntu should exclude shards 1, 2, 3 to avoid waste
        self.assertGreater(len(exclude), 0, "main-full-verify must have exclude entries")

        # Check that Ubuntu+shard combinations are excluded
        found_excludes = 0
        for entry in exclude:
            if entry.get("os") == "ubuntu-latest":
                found_excludes += 1

        self.assertGreaterEqual(
            found_excludes,
            3,
            "main-full-verify must exclude Ubuntu shards 1, 2, 3"
        )


if __name__ == "__main__":
    unittest.main()
