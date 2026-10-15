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

import json
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


FIXTURE_REQUIRED = ["ci (0)", "ci (1)", "ci (2)", "ci (3)", "windows"]


def _write_manifest(tmpdir, required=FIXTURE_REQUIRED):
    manifest = Path(tmpdir) / "tools" / "required-checks.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"required_contexts": list(required)}), encoding="utf-8")
    return manifest


def _write_workflow(tmpdir, content, required=FIXTURE_REQUIRED):
    workflows_dir = Path(tmpdir) / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    path = workflows_dir / "ci.yml"
    path.write_text(content, encoding="utf-8")
    _write_manifest(tmpdir, required)
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


class TestRequiredCheckNames(unittest.TestCase):
    """Required names come from tools/required-checks.json and must resolve to a job (F-07/F-10)."""

    def test_renamed_required_job_is_a_finding(self):
        """Red-first: 'windows' is required but its job was renamed to 'windows-x' -- must FAIL."""
        renamed = FIXED_YAML.replace("\n  windows:\n", "\n  windows-x:\n", 1)
        self.assertNotEqual(renamed, FIXED_YAML)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_workflow(tmpdir, renamed)
            findings, _ = guard.check_workflow(path)
        flagged = {f["feeds_required_check"] for f in findings}
        self.assertIn("windows", flagged)

    def test_renamed_required_job_cli_exits_1(self):
        renamed = FIXED_YAML.replace("\n  windows:\n", "\n  windows-x:\n", 1)
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, renamed)
            self.assertEqual(guard.main(["--root", tmpdir]), 1)

    def test_protection_payload_contexts_are_required(self):
        """A required context from captured branch protection with no job must be a finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_workflow(tmpdir, FIXED_YAML)
            payload = Path(tmpdir) / "protection.json"
            payload.write_text(json.dumps({"required_status_checks": {
                "contexts": ["ci (0)", "ci (1)", "ci (2)", "ci (3)", "windows", "lint-gone"]}}),
                encoding="utf-8")
            findings, _ = guard.check_workflow(path, None, str(payload))
        self.assertIn("lint-gone", {f["feeds_required_check"] for f in findings})

    def test_unreadable_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_workflow(tmpdir, FIXED_YAML)
            (Path(tmpdir) / "tools" / "required-checks.json").unlink()
            with self.assertRaises(guard.GuardError):
                guard.check_workflow(path)
            self.assertEqual(guard.main(["--root", tmpdir]), 2)


# Rendered-check-name fixtures (r975 adversarial pass 2): a `name:` override renders the
# check name GitHub reports, and a matrix suffix ` (N)` must be a real matrix value.
RENDER_CI_REQUIRED = ["ci (0)", "ci (1)", "ci (2)", "ci (3)", "windows"]
RENDER_NAME_OVERRIDE = "    name: shard-${{ matrix.python-shard }}\n"
RENDER_NAME_MATCHING = "    name: ci (${{ matrix.python-shard }})\n"
RENDER_MATRIX_FULL = "      matrix:\n        python-shard: [0, 1, 2, 3]\n"
RENDER_MATRIX_SHRUNK = "      matrix:\n        python-shard: [0, 1]\n"
RENDER_MATRIX_EXPRESSION = "      matrix: ${{ fromJSON(needs.x.outputs.m) }}\n"


def _render_fixture(name_line="", matrix_block=RENDER_MATRIX_FULL):
    return (
        "jobs:\n"
        "  ci:\n"
        "    if: always()\n"
        + name_line +
        "    runs-on: ubuntu-latest\n"
        "    strategy:\n"
        + matrix_block +
        "    steps:\n"
        "      - run: echo noop\n"
        "  windows:\n"
        "    if: always()\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: echo noop\n"
    )


class TestRequiredContextRendering(unittest.TestCase):
    """A required context `job (N)` must resolve to a job whose RENDERED check name equals it."""

    def _findings(self, content):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _write_workflow(tmpdir, content, RENDER_CI_REQUIRED)
            findings, _ = guard.check_workflow(path)
        return findings

    def test_name_override_that_renders_other_names_is_a_finding(self):
        """r975 M1: `name: shard-${{ matrix.python-shard }}` never reports 'ci (N)' -- must FAIL."""
        findings = self._findings(_render_fixture(RENDER_NAME_OVERRIDE))
        flagged = {f["feeds_required_check"] for f in findings}
        self.assertTrue({"ci (0)", "ci (1)", "ci (2)", "ci (3)"} <= flagged, findings)

    def test_name_override_that_renders_other_names_cli_exits_1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_workflow(tmpdir, _render_fixture(RENDER_NAME_OVERRIDE), RENDER_CI_REQUIRED)
            self.assertEqual(guard.main(["--root", tmpdir]), 1)

    def test_suffix_not_a_matrix_value_is_a_finding(self):
        """r975 M2: matrix shrunk to [0, 1] -- 'ci (2)' and 'ci (3)' can never report -- must FAIL."""
        flagged = {f["feeds_required_check"]
                   for f in self._findings(_render_fixture(matrix_block=RENDER_MATRIX_SHRUNK))}
        self.assertTrue({"ci (2)", "ci (3)"} <= flagged, flagged)
        self.assertNotIn("ci (0)", flagged)

    def test_matrix_expression_cannot_verify_suffixes(self):
        flagged = {f["feeds_required_check"]
                   for f in self._findings(_render_fixture(matrix_block=RENDER_MATRIX_EXPRESSION))}
        self.assertTrue({"ci (0)", "ci (1)", "ci (2)", "ci (3)"} <= flagged, flagged)

    def test_override_rendering_the_context_passes(self):
        """Control: an override whose rendered form equals the context is clean."""
        self.assertEqual(self._findings(_render_fixture(RENDER_NAME_MATCHING)), [])

    def test_plain_matrix_job_passes(self):
        """Control: the real shape (no override, full matrix) is clean."""
        self.assertEqual(self._findings(_render_fixture()), [])


if __name__ == "__main__":
    unittest.main()
