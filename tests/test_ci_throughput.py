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
        self.assertIn(".sh", run, "Must detect *.sh files")
        self.assertIn("bin/", run, "Must detect bin/ changes")
        self.assertIn("package.json", run, "Must detect package.json changes")
        self.assertIn("package-lock", run, "Must detect package-lock.json changes")
        self.assertIn("ui/web/", run, "Must detect ui/web/ changes")
        self.assertIn(".github/workflows/", run, "Must detect .github/workflows/ changes")
        self.assertIn("halt", run, "Must detect tools/halt.py specifically")
        self.assertIn("ci_shard_runner", run, "Must detect tools/ci_shard_runner.py specifically")
        self.assertIn("subprocess_common", run, "Must detect tools/subprocess_common.py specifically")

    def test_windows_sensitive_paths_narrow_not_broad(self):
        """windows-sensitive-paths must NOT trigger on generic tools/ files.

        Changes to tools/gen_tool_index.py (not in Windows-specific list) should NOT trigger
        Windows tests. Changes to tools/halt.py (in the list) should trigger Windows tests.
        The pattern must explicitly list specific OS-sensitive tools files, not use broad
        'tools/.*\.py' which would match every PR.
        """
        jobs = self.ci_workflow.get("jobs", {})
        wsp_job = jobs.get("windows-sensitive-paths", {})
        steps = wsp_job.get("steps", [])

        detect_step = None
        for step in steps:
            if step.get("name") == "Detect Windows-sensitive changes":
                detect_step = step
                break

        self.assertIsNotNone(detect_step, "Missing detect step")
        run = detect_step.get("run", "")

        # Find the grep -qE pattern line
        pattern_lines = [l for l in run.split('\n') if 'grep -qE' in l]
        self.assertGreater(len(pattern_lines), 0, "Must have grep pattern for path detection")

        pattern = pattern_lines[0]
        # The pattern should NOT use broad "tools/.*\.py" which matches all tools Python files
        self.assertNotIn("tools/.*\\.py", pattern,
                        "Pattern must not match all tools/*.py; narrow to specific OS-sensitive files")

        # The pattern SHOULD explicitly list specific tools files with OS-specific behavior
        expected_tools_files = [
            "ci_shard_runner",      # subprocess/Windows shard runner
            "halt",                 # Windows task termination
            "test_isolation_tripwire",  # test isolation on Windows
            "remote_refs_tripwire",     # network isolation
            "test_network_isolation",   # network isolation on Windows
            "subprocess_common",    # subprocess semantics on Windows
            "encoding_lint",        # UTF-8 encoding on Windows
            "power_selftest",       # PowerShell integration test
            "hook_preflight",       # pre-push hook execution
            "task_cadence_check",   # Windows task scheduling
            "build_static_dash",    # build output handling
        ]
        for file in expected_tools_files:
            self.assertIn(file, pattern, f"Pattern must explicitly list tools/{file}.py")

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

    def test_main_full_runs_full_matrix_on_both_platforms(self):
        """main-full is the post-merge integration gate: full os x shard matrix.

        GAP 2026-10-06 (run 37504613105): #850 put `exclude:` beside `matrix:`
        under `strategy:` to drop ubuntu shards 1-3. GitHub rejects that key
        ("Unexpected value 'exclude'"), so main-full produced ZERO jobs for ~10
        merges -- and the previous version of this test asserted the invalid
        location, so it only passed BECAUSE the file was broken. Also, #850's
        ubuntu "shard 0 only" ran `ci_shard_runner.py 0 4`, i.e. 1/4 of the
        Python tests, not "all Python tests in one shard" as its comment said.
        """
        jobs = self.main_full_workflow.get("jobs", {})
        main_full_verify = jobs.get("main-full-verify", {})
        strategy = main_full_verify.get("strategy", {})

        # `exclude`/`include` are matrix keys; under `strategy` GitHub rejects the file.
        self.assertEqual(
            set(strategy.keys()) - {"fail-fast", "matrix", "max-parallel"},
            set(),
            "strategy may only contain fail-fast/matrix/max-parallel; "
            "`exclude` belongs INSIDE matrix (GitHub rejects it here)",
        )

        matrix = strategy.get("matrix", {})
        self.assertEqual(matrix.get("os"), ["ubuntu-latest", "windows-latest"])
        self.assertEqual(matrix.get("python-shard"), [0, 1, 2, 3])

        # No platform may be trimmed: every os must keep every shard.
        for entry in matrix.get("exclude", []) or []:
            self.fail(
                "main-full-verify must run every shard on every platform; "
                "exclude entry %r drops coverage from the integration gate" % (entry,)
            )


if __name__ == "__main__":
    unittest.main()
