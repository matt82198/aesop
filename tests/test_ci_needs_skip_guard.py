"""Tests for tools/ci_needs_skip_guard.py (needs-skip-cascade guard).

Hermetic: no network, no gh. Workflow YAML comes from tempdir fixtures.

Root cause this guards: GitHub Actions implicitly ANDs success() onto a job's
own `if:` whenever that job has a `needs:` entry and the `if:` does not itself
call always()/cancelled()/failure(). On 2026-10-05 this made `docs-only-gate`
cancellations (runner/concurrency queue starvation; the job never ran a single
step) cascade `ci (0..3)` to `skipped` on every armed PR for ~8h -- a skipped
required check can never satisfy branch protection (the exact deadlock
.github/workflows/ci.yml's own header comment warns about, via a path it
didn't close).
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import ci_needs_skip_guard as guard  # noqa: E402


# The exact shape that broke on main before the 2026-10-05 fix: `ci` has no
# `if:` at all, and `windows-shard` has a custom `if:` that never names a
# status function -- both are implicitly success()-gated on docs-only-gate.
BROKEN_YAML = """
jobs:
  docs-only-gate:
    runs-on: ubuntu-latest
    steps:
      - run: echo noop
    outputs:
      is_docs_only: ${{ steps.x.outputs.is_docs_only }}

  ci:
    needs: docs-only-gate
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-shard: [0, 1, 2, 3]
    steps:
      - run: echo noop

  windows-shard:
    needs: docs-only-gate
    if: needs.docs-only-gate.outputs.is_docs_only == 'false'
    runs-on: windows-latest
    steps:
      - run: echo noop

  windows:
    needs: windows-shard
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: echo noop

  ps1-syntax-check:
    runs-on: windows-latest
    steps:
      - run: echo noop
"""

# The fixed shape: always() on all three jobs that sit downstream of
# docs-only-gate and feed a required check.
FIXED_YAML = """
jobs:
  docs-only-gate:
    runs-on: ubuntu-latest
    steps:
      - run: echo noop
    outputs:
      is_docs_only: ${{ steps.x.outputs.is_docs_only }}

  ci:
    needs: docs-only-gate
    if: always()
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-shard: [0, 1, 2, 3]
    steps:
      - run: echo noop

  windows-shard:
    needs: docs-only-gate
    if: always() && (needs.docs-only-gate.result != 'success' || needs.docs-only-gate.outputs.is_docs_only != 'true')
    runs-on: windows-latest
    steps:
      - run: echo noop

  windows:
    needs: windows-shard
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: echo noop

  ps1-syntax-check:
    runs-on: windows-latest
    steps:
      - run: echo noop
"""


def _write_workflow(tmpdir, content):
    workflows_dir = Path(tmpdir) / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    path = workflows_dir / "ci.yml"
    path.write_text(content, encoding="utf-8")
    return path


class TestCiNeedsSkipGuard(unittest.TestCase):
    def test_red_first_catches_the_real_defect(self):
        """Must FAIL against the exact broken shape that caused the 2026-10-05 escape."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, BROKEN_YAML)
            findings, notes = guard.check_workflow(
                Path(tmpdir) / ".github" / "workflows" / "ci.yml")
        jobs_flagged = {f["job"] for f in findings}
        self.assertIn("ci", jobs_flagged)
        self.assertIn("windows-shard", jobs_flagged)
        self.assertEqual(len(findings), 2)

    def test_fixed_shape_is_clean(self):
        """Must PASS against the always()-guarded fix; no false positives."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, FIXED_YAML)
            findings, notes = guard.check_workflow(
                Path(tmpdir) / ".github" / "workflows" / "ci.yml")
        self.assertEqual(findings, [])

    def test_bare_success_call_still_flagged(self):
        """A job that writes `if: success()` explicitly is STILL skip-cascade
        unsafe -- success() is exactly the implicit default it must override
        for a required check, so spelling it out changes nothing."""
        yaml_text = BROKEN_YAML.replace(
            "if: needs.docs-only-gate.outputs.is_docs_only == 'false'",
            "if: success() && needs.docs-only-gate.outputs.is_docs_only == 'false'")
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, yaml_text)
            findings, _ = guard.check_workflow(
                Path(tmpdir) / ".github" / "workflows" / "ci.yml")
        self.assertIn("windows-shard", {f["job"] for f in findings})

    def test_job_with_no_needs_is_never_flagged(self):
        """A job with no needs: can't be skip-cascaded; must never appear."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, FIXED_YAML)
            findings, _ = guard.check_workflow(
                Path(tmpdir) / ".github" / "workflows" / "ci.yml")
        self.assertNotIn("docs-only-gate", {f["job"] for f in findings})
        self.assertNotIn("ps1-syntax-check", {f["job"] for f in findings})

    def test_real_repo_ci_yml_is_clean(self):
        """The actual, currently-committed .github/workflows/ci.yml must pass."""
        findings, _ = guard.check_workflow(REPO_ROOT / ".github" / "workflows" / "ci.yml")
        self.assertEqual(findings, [], msg="real ci.yml has a skip-cascade finding: %r" % findings)

    def test_unparseable_workflow_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_workflow(tmpdir, "not: [valid yaml")
            with self.assertRaises(guard.GuardError):
                guard.check_workflow(path)

    def test_no_jobs_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_workflow(tmpdir, "name: empty\n")
            with self.assertRaises(guard.GuardError):
                guard.check_workflow(path)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, BROKEN_YAML)
            self.assertEqual(guard.main(["--root", tmpdir]), 1)
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, FIXED_YAML)
            self.assertEqual(guard.main(["--root", tmpdir]), 0)


if __name__ == "__main__":
    unittest.main()
