"""Tests for tools/ci_job_execution_verifier.py (silent-CI-job detector).

Hermetic: no network. GitHub Actions run/job payloads come from inline fixture
JSON; workflow YAML comes from tempdir fixtures. The gh seam is exercised by
patching the module-level subprocess wrapper.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import ci_job_execution_verifier as cjev  # noqa: E402


def iso(dt):
    """Render a datetime as a GitHub-style UTC ISO-8601 timestamp."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
RECENT = iso(NOW - timedelta(days=1))
OLD = iso(NOW - timedelta(days=40))


def api_job(name, conclusion="success", steps=None, started=RECENT):
    """Build one GitHub API job record."""
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": started,
        "completed_at": started,
        "steps": steps or [],
    }


def api_run(run_id, jobs, created_at=RECENT, conclusion="success"):
    """Build one GitHub API run record with its jobs attached."""
    return {
        "databaseId": run_id,
        "createdAt": created_at,
        "status": "completed",
        "conclusion": conclusion,
        "jobs": jobs,
    }


def run_main(argv):
    """Call main() with stdout/stderr captured so test output stays clean."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cjev.main(argv)
    return rc, out.getvalue(), err.getvalue()


def write_workflow(directory, filename, text):
    """Write a workflow file under <directory>/.github/workflows/."""
    wf_dir = Path(directory) / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / filename
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


# --------------------------------------------------------------------------
# Workflow fixtures
# --------------------------------------------------------------------------

WF_HEALTHY_MATRIX = """\
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  windows-shard:
    runs-on: windows-latest
    strategy:
      matrix:
        python-shard: [0, 1, 2, 3]
    steps:
      - name: Run Python tests (shard ${{ matrix.python-shard }}/4)
        run: python tools/ci_shard_runner.py ${{ matrix.python-shard }} 4
  windows:
    needs: windows-shard
    runs-on: ubuntu-latest
    if: always()
    steps:
      - name: Check all Windows shards passed
        run: echo ok
"""

WF_DEAD_JOB = """\
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Compile
        run: make
  orphan-gate:
    runs-on: ubuntu-latest
    steps:
      - name: Never runs
        run: python tools/orphan_gate.py
"""

WF_IF_FALSE = """\
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Compile
        run: make
  disabled-gate:
    runs-on: ubuntu-latest
    if: false
    steps:
      - name: Dead gate
        run: python tools/dead_gate.py
"""

WF_NEEDS_CHAIN = """\
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  never-parent:
    runs-on: ubuntu-latest
    if: false
    steps:
      - name: Parent
        run: echo parent
  child-gate:
    needs: never-parent
    runs-on: ubuntu-latest
    steps:
      - name: Child
        run: echo child
"""

WF_DOCS_ONLY_SKIP = """\
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  docs-only-gate:
    runs-on: ubuntu-latest
    steps:
      - name: Detect docs-only change
        run: echo detect
      - name: Lightweight secret scan (docs-only)
        if: steps.is-docs-only.outputs.is_docs_only == 'true'
        run: python tools/secret_scan.py .
"""

WF_CONDITIONAL_NEVER = """\
name: CI
on:
  pull_request:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Compile
        run: make
  nightly-only:
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule'
    steps:
      - name: Nightly
        run: echo nightly
"""


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

class TestParseWorkflow(unittest.TestCase):
    """The YAML side of the inventory: jobs, ifs, needs, steps."""

    def test_parses_jobs_ifs_needs_and_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_workflow(tmp, "ci.yml", WF_HEALTHY_MATRIX)
            inv = cjev.parse_workflow(path)
        self.assertEqual(inv["workflow_name"], "CI")
        self.assertEqual(sorted(inv["jobs"]), ["windows", "windows-shard"])
        shard = inv["jobs"]["windows-shard"]
        self.assertTrue(shard["matrix"])
        self.assertEqual(shard["needs"], [])
        self.assertEqual(len(shard["steps"]), 1)
        agg = inv["jobs"]["windows"]
        self.assertEqual(agg["needs"], ["windows-shard"])
        self.assertEqual(agg["if"], "always()")

    def test_missing_workflow_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cjev.VerifierError):
                cjev.parse_workflow(Path(tmp) / "nope.yml")

    def test_malformed_yaml_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_workflow(tmp, "bad.yml", "jobs:\n  a:\n   - [unclosed\n")
            with self.assertRaises(cjev.VerifierError):
                cjev.parse_workflow(path)


# --------------------------------------------------------------------------
# Name matching (YAML job id vs API job name)
# --------------------------------------------------------------------------

class TestNameMatching(unittest.TestCase):
    """API job names differ from YAML ids: matrix suffixes and name: overrides."""

    def test_matrix_job_matched_by_suffix(self):
        self.assertTrue(cjev.job_name_matches("windows-shard", "windows-shard (0)"))
        self.assertTrue(cjev.job_name_matches("ci", "ci (3)"))
        self.assertTrue(cjev.job_name_matches("main-full-verify",
                                              "main-full-verify (ubuntu-latest)"))

    def test_word_boundary_safe(self):
        # 'windows' must NOT swallow 'windows-shard (0)' — that is the whole
        # reason a naive prefix match reports a dead job as healthy.
        self.assertFalse(cjev.job_name_matches("windows", "windows-shard (0)"))
        self.assertFalse(cjev.job_name_matches("ci", "ci-extra"))
        self.assertFalse(cjev.job_name_matches("build", "rebuild"))
        self.assertTrue(cjev.job_name_matches("windows", "windows"))

    def test_expression_in_name_matches_any_rendering(self):
        self.assertTrue(cjev.step_name_matches(
            "Run Python tests (shard ${{ matrix.python-shard }}/4)",
            "Run Python tests (shard 2/4)"))
        self.assertFalse(cjev.step_name_matches(
            "Run Python tests (shard ${{ matrix.python-shard }}/4)",
            "Run Node.js tests"))

    def test_step_names_are_exact_not_prefix(self):
        self.assertTrue(cjev.step_name_matches("Secret scan", "Secret scan"))
        self.assertFalse(cjev.step_name_matches("Secret scan", "Secret scan (docs-only)"))


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

class TestAnalyze(unittest.TestCase):
    """Per-job execution accounting and finding classification."""

    def _inventory(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            return cjev.parse_workflow(write_workflow(tmp, "ci.yml", text))

    def test_healthy_matrix_job_not_flagged(self):
        inv = self._inventory(WF_HEALTHY_MATRIX)
        runs = [api_run(1, [
            api_job("windows-shard (0)"),
            api_job("windows-shard (1)"),
            api_job("windows-shard (2)"),
            api_job("windows-shard (3)"),
            api_job("windows"),
        ])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        self.assertEqual(report["findings"], [])
        shard = report["jobs_by_id"]["windows-shard"]
        self.assertEqual(shard["executed"], 4)
        self.assertEqual(shard["skipped"], 0)
        self.assertFalse(shard["never_executed"])
        self.assertEqual(shard["last_executed"], RECENT)
        # The aggregator must be accounted separately, not absorbed by the shard.
        self.assertEqual(report["jobs_by_id"]["windows"]["executed"], 1)

    def test_defined_but_never_ran_job_is_flagged(self):
        inv = self._inventory(WF_DEAD_JOB)
        runs = [api_run(n, [api_job("build")]) for n in (1, 2, 3)]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        kinds = {(f["kind"], f["job"]) for f in report["findings"]}
        self.assertIn(("JOB_NEVER_EXECUTED_UNCONDITIONAL", "orphan-gate"), kinds)
        self.assertNotIn("build", {f["job"] for f in report["findings"]})
        orphan = report["jobs_by_id"]["orphan-gate"]
        self.assertTrue(orphan["never_executed"])
        self.assertIsNone(orphan["last_executed"])

    def test_if_false_job_flagged_as_dead_condition(self):
        inv = self._inventory(WF_IF_FALSE)
        runs = [api_run(1, [api_job("build")])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        kinds = {(f["kind"], f["job"]) for f in report["findings"]}
        self.assertIn(("JOB_DEAD_CONDITION", "disabled-gate"), kinds)

    def test_needs_parent_never_ran_flags_child(self):
        inv = self._inventory(WF_NEEDS_CHAIN)
        runs = [api_run(1, [])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        kinds = {(f["kind"], f["job"]) for f in report["findings"]}
        self.assertIn(("JOB_DEAD_NEEDS_CHAIN", "child-gate"), kinds)

    def test_skipped_by_docs_only_but_executed_in_lookback_not_flagged(self):
        inv = self._inventory(WF_DOCS_ONLY_SKIP)
        steps_skipped = [
            {"name": "Detect docs-only change", "conclusion": "success"},
            {"name": "Lightweight secret scan (docs-only)", "conclusion": "skipped"},
        ]
        steps_ran = [
            {"name": "Detect docs-only change", "conclusion": "success"},
            {"name": "Lightweight secret scan (docs-only)", "conclusion": "success"},
        ]
        runs = [
            api_run(1, [api_job("docs-only-gate", steps=steps_skipped)]),
            api_run(2, [api_job("docs-only-gate", steps=steps_ran)]),
        ]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        self.assertEqual(report["findings"], [])
        job = report["jobs_by_id"]["docs-only-gate"]
        self.assertEqual(job["executed"], 2)
        self.assertFalse(job["never_executed"])

    def test_job_skipped_in_every_run_counts_as_never_executed(self):
        inv = self._inventory(WF_DEAD_JOB)
        runs = [api_run(1, [
            api_job("build"),
            api_job("orphan-gate", conclusion="skipped"),
        ])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        job = report["jobs_by_id"]["orphan-gate"]
        self.assertEqual(job["executed"], 0)
        self.assertEqual(job["skipped"], 1)
        self.assertTrue(job["never_executed"])
        self.assertIn("orphan-gate", {f["job"] for f in report["findings"]})

    def test_conditional_never_ran_is_a_note_not_a_finding(self):
        inv = self._inventory(WF_CONDITIONAL_NEVER)
        runs = [api_run(1, [api_job("build")])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        self.assertEqual(report["findings"], [])
        kinds = {(n["kind"], n["job"]) for n in report["notes"]}
        self.assertIn(("JOB_NEVER_EXECUTED_CONDITIONAL", "nightly-only"), kinds)

    def test_runs_outside_lookback_are_ignored(self):
        inv = self._inventory(WF_DEAD_JOB)
        runs = [
            api_run(1, [api_job("build")], created_at=RECENT),
            api_run(2, [api_job("orphan-gate")], created_at=OLD),
        ]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        self.assertEqual(report["runs_sampled"], 1)
        self.assertIn("orphan-gate", {f["job"] for f in report["findings"]})

    def test_no_runs_in_lookback_is_an_error_not_a_vacuous_green(self):
        inv = self._inventory(WF_DEAD_JOB)
        runs = [api_run(1, [api_job("build")], created_at=OLD)]
        with self.assertRaises(cjev.VerifierError):
            cjev.analyze(inv, runs, lookback_days=14, now=NOW)

    def test_renamed_step_never_executed_is_flagged(self):
        inv = self._inventory(WF_DEAD_JOB)
        runs = [api_run(1, [api_job("build", steps=[
            {"name": "Compile the thing", "conclusion": "success"},
        ]), api_job("orphan-gate")])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        steps_flagged = {(f["job"], f["step"]) for f in report["findings"]
                         if f["kind"] == "STEP_NEVER_EXECUTED"}
        self.assertIn(("build", "Compile"), steps_flagged)

    def test_matrix_step_expression_step_not_flagged(self):
        inv = self._inventory(WF_HEALTHY_MATRIX)
        shard_steps = [{"name": "Run Python tests (shard 0/4)", "conclusion": "success"}]
        agg_steps = [{"name": "Check all Windows shards passed", "conclusion": "success"}]
        runs = [api_run(1, [
            api_job("windows-shard (0)", steps=shard_steps),
            api_job("windows-shard (1)", steps=shard_steps),
            api_job("windows-shard (2)", steps=shard_steps),
            api_job("windows-shard (3)", steps=shard_steps),
            api_job("windows", steps=agg_steps),
        ])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        self.assertEqual(report["findings"], [])

    def test_step_analysis_ignores_failed_jobs(self):
        # A failed job truncates its step list; treating that as "never executed"
        # would flood findings on any red run.
        inv = self._inventory(WF_DEAD_JOB)
        runs = [api_run(1, [
            api_job("build", conclusion="failure", steps=[]),
            api_job("orphan-gate"),
        ])]
        report = cjev.analyze(inv, runs, lookback_days=14, now=NOW)
        self.assertEqual([f for f in report["findings"]
                          if f["kind"] == "STEP_NEVER_EXECUTED"], [])


# --------------------------------------------------------------------------
# gh seam
# --------------------------------------------------------------------------

class TestGhSeam(unittest.TestCase):
    """gh failures must fail closed (exit 2), never green-with-no-data."""

    def _tmp_repo(self, text=WF_DEAD_JOB):
        """Temp repo root that is removed when the test finishes (no pollution)."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        write_workflow(holder.name, "ci.yml", text)
        return holder.name

    def test_fetch_runs_raises_on_gh_failure(self):
        fake = subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="gh: not authenticated")
        with mock.patch.object(cjev, "_run_gh", return_value=fake):
            with self.assertRaises(cjev.VerifierError):
                cjev.fetch_runs("ci.yml", limit=5, root=".")

    def test_fetch_runs_raises_on_bad_json(self):
        fake = subprocess.CompletedProcess(
            args=["gh"], returncode=0, stdout="not json", stderr="")
        with mock.patch.object(cjev, "_run_gh", return_value=fake):
            with self.assertRaises(cjev.VerifierError):
                cjev.fetch_runs("ci.yml", limit=5, root=".")

    def test_main_exits_2_when_gh_fails(self):
        root = self._tmp_repo()
        fake = subprocess.CompletedProcess(
            args=["gh"], returncode=1, stdout="", stderr="gh: not found")
        with mock.patch.object(cjev, "_run_gh", return_value=fake):
            rc, _, err = run_main(["--workflow", "ci.yml", "--root", root])
        self.assertEqual(rc, 2)
        self.assertIn("gh exited 1", err)

    def test_main_exits_2_when_gh_missing(self):
        root = self._tmp_repo()
        with mock.patch.object(cjev, "_run_gh", side_effect=FileNotFoundError("gh")):
            rc, _, err = run_main(["--workflow", "ci.yml", "--root", root])
        self.assertEqual(rc, 2)
        self.assertIn("gh CLI not found", err)

    def test_bounded_api_calls(self):
        # One `gh run list` + at most --runs `gh api .../jobs` calls.
        root = self._tmp_repo()
        listing = json.dumps([
            {"databaseId": i, "createdAt": iso(datetime.now(timezone.utc)),
             "status": "completed", "conclusion": "success"}
            for i in range(20)
        ])
        jobs_payload = json.dumps({"jobs": [
            {"name": "build", "status": "completed", "conclusion": "success",
             "started_at": iso(datetime.now(timezone.utc)),
             "completed_at": iso(datetime.now(timezone.utc)), "steps": []},
            {"name": "orphan-gate", "status": "completed", "conclusion": "success",
             "started_at": iso(datetime.now(timezone.utc)),
             "completed_at": iso(datetime.now(timezone.utc)), "steps": []},
        ]})

        calls = []

        def fake_run_gh(args, root, timeout=60):
            calls.append(args)
            out = listing if args[1] == "run" else jobs_payload
            return subprocess.CompletedProcess(args=args, returncode=0,
                                               stdout=out, stderr="")

        with mock.patch.object(cjev, "_run_gh", side_effect=fake_run_gh):
            rc, _, _ = run_main(
                ["--workflow", "ci.yml", "--root", root, "--runs", "5"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 6)


# --------------------------------------------------------------------------
# CLI end-to-end (offline fixture mode, real subprocess)
# --------------------------------------------------------------------------

class TestCli(unittest.TestCase):
    """Exit codes and output shape through a real subprocess, no network."""

    TOOL = str(REPO_ROOT / "tools" / "ci_job_execution_verifier.py")

    def _run(self, tmp, workflow_text, fixture_obj, extra=None):
        write_workflow(tmp, "ci.yml", workflow_text)
        fixture_path = Path(tmp) / "fixture.json"
        with open(fixture_path, "w", encoding="utf-8") as handle:
            json.dump(fixture_obj, handle)
        argv = [sys.executable, self.TOOL, "--workflow", "ci.yml",
                "--root", tmp, "--fixture", str(fixture_path)]
        argv.extend(extra or [])
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(argv, cwd=tmp, capture_output=True, text=True,
                              encoding="utf-8", timeout=120, env=env)

    def _now_fixture(self, jobs):
        stamp = iso(datetime.now(timezone.utc) - timedelta(hours=1))
        return {"runs": [{"databaseId": 1, "createdAt": stamp,
                          "status": "completed", "conclusion": "success",
                          "jobs": [dict(j, started_at=stamp, completed_at=stamp)
                                   for j in jobs]}]}

    def test_exit_0_when_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._now_fixture([
                api_job("build", steps=[{"name": "Compile", "conclusion": "success"}]),
                api_job("orphan-gate",
                        steps=[{"name": "Never runs", "conclusion": "success"}]),
            ])
            proc = self._run(tmp, WF_DEAD_JOB, fixture)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("CLEAN", proc.stdout)

    def test_exit_1_on_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._now_fixture([
                api_job("build", steps=[{"name": "Compile", "conclusion": "success"}]),
            ])
            proc = self._run(tmp, WF_DEAD_JOB, fixture)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("orphan-gate", proc.stdout)

    def test_json_mode_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._now_fixture([
                api_job("build", steps=[{"name": "Compile", "conclusion": "success"}]),
            ])
            proc = self._run(tmp, WF_DEAD_JOB, fixture, extra=["--json"])
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        for key in ("workflow", "runs_sampled", "lookback_days", "jobs",
                    "findings", "notes", "exit_code"):
            self.assertIn(key, data)
        self.assertEqual(data["exit_code"], 1)
        job_ids = {j["job_id"] for j in data["jobs"]}
        self.assertEqual(job_ids, {"build", "orphan-gate"})

    def test_exit_2_on_missing_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._now_fixture([api_job("build")])
            fixture_path = Path(tmp) / "fixture.json"
            with open(fixture_path, "w", encoding="utf-8") as handle:
                json.dump(fixture, handle)
            proc = subprocess.run(
                [sys.executable, self.TOOL, "--workflow", "absent.yml",
                 "--root", tmp, "--fixture", str(fixture_path)],
                cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                timeout=120)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_exit_2_on_missing_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_workflow(tmp, "ci.yml", WF_DEAD_JOB)
            proc = subprocess.run(
                [sys.executable, self.TOOL, "--workflow", "ci.yml",
                 "--root", tmp, "--fixture", str(Path(tmp) / "absent.json")],
                cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                timeout=120)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_ascii_only_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._now_fixture([api_job("build")])
            proc = self._run(tmp, WF_DEAD_JOB, fixture)
        proc.stdout.encode("ascii")


if __name__ == "__main__":
    unittest.main()
