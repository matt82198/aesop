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


def _fixture_required(yaml_content: str) -> list:
    """Default required contexts for a fixture: every job it defines, so check (d) passes."""
    parts = yaml_content.split('\njobs:', 1)
    if len(parts) < 2:
        return ['(no-jobs)']
    return re.findall(r'^  ([A-Za-z0-9_.-]+):\s*$', parts[1], re.M)


def _write_manifest(root: Path, required: list) -> None:
    """Write tools/required-checks.json under root with the given required_contexts."""
    manifest = root / 'tools' / 'required-checks.json'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({'required_contexts': list(required)}), encoding='utf-8')


class TestCIGateRunability(unittest.TestCase):
    """Test suite for CI gate runability validation."""

    def run_tool(self, yaml_content: str, extra_args: list = None,
                 required: list = None, stub_files: list = None) -> tuple:
        """
        Run the tool on a temporary workflow file.
        required: the fixture's required-check manifest (default: every job it defines).
        stub_files: files to create (default: every tools/*.py the workflow cites);
        pass a list to leave the rest missing on purpose.
        Returns (exit_code, stdout, stderr, parsed_json_if_requested).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            workflows_dir = tmppath / '.github' / 'workflows'
            workflows_dir.mkdir(parents=True)

            workflow_file = workflows_dir / 'test.yml'
            workflow_file.write_text(yaml_content)
            _write_manifest(tmppath, _fixture_required(yaml_content) if required is None else required)

            # Create a stub for every tools/*.py this fixture's workflow references,
            # so the fixture is self-consistent. The tool correctly flags steps that
            # reference missing files; it previously skipped that check whenever the
            # repo path looked like a Windows temp dir ('AppData' + 'Temp'), so these
            # fixtures passed on Windows and failed on Linux. With that Windows-only
            # exemption removed, a fixture must provide the files it cites.
            if stub_files is None:
                stub_files = set(re.findall(r'tools/[A-Za-z0-9_./-]+\.py', yaml_content))
            for referenced in stub_files:
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


class TestCIGateRunabilityLocationIndependence(unittest.TestCase):
    """The gate's verdict must be a pure function of (workflow content, files on disk).

    Regression guard for a fail-open that shipped twice: the tool skipped its
    file-existence check whenever the repo root path merely *looked* like a Windows
    temp directory (contained both 'AppData' and 'Temp'). Commit 6afb94ba removed one
    copy from find_file_on_disk() but a second copy survived in check_workflow(), so
    byte-identical fixtures still produced rc0 under a temp-shaped path and rc1
    outside it. Any CI runner whose workspace lives under such a path silently lost
    file-existence checking entirely.
    """

    # References a verify_*.py that does not exist -> get_suite_family() matches, so
    # check (c) runs and must report the missing file.
    MISSING_FILE_YAML = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Verify absent gate
        run: python tools/verify_definitely_absent_gate.py --check
"""

    # Trigger-shaped suffix: contains both 'AppData' and 'Temp' on EVERY platform, so
    # the old exemption fires on Linux runners too (not just Windows).
    TEMP_SHAPED_SUFFIX = Path('AppData') / 'Temp' / 'repo'
    ORDINARY_SUFFIX = Path('ordinary') / 'workspace' / 'repo'

    def _build_fixture(self, root: Path, yaml_content: str, stub_files=()):
        """Materialize a workflow fixture (plus any stub files) under root."""
        workflows_dir = root / '.github' / 'workflows'
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / 'test.yml').write_text(yaml_content, encoding='utf-8')
        _write_manifest(root, _fixture_required(yaml_content))
        for rel in stub_files:
            stub = root / rel
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text('# test fixture stub\n', encoding='utf-8')

    def _run_at(self, root: Path):
        """Run the gate against root; returns (exit_code, stdout)."""
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parent.parent / 'tools' / 'ci_gate_runability.py'),
                '--root', str(root),
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            cwd=str(Path(__file__).parent.parent),
        )
        return (result.returncode, result.stdout)

    def test_missing_file_flagged_under_temp_shaped_root(self):
        """A missing file reference is flagged even when the root path looks temp-ish.

        Cross-platform repro: the root ends in 'AppData/Temp/repo' on every OS, so the
        removed exemption would fire on Linux CI as well as Windows.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / self.TEMP_SHAPED_SUFFIX
            self._build_fixture(root, self.MISSING_FILE_YAML)

            exit_code, stdout = self._run_at(root)

            self.assertEqual(
                exit_code, 1,
                "Gate must flag the missing file regardless of where the repo lives; "
                f"got rc={exit_code} for root {root}\n{stdout}"
            )
            self.assertIn('verify_definitely_absent_gate.py', stdout)

    def test_verdict_identical_from_temp_shaped_and_ordinary_roots(self):
        """Byte-identical fixtures at two locations must produce identical verdicts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_shaped = Path(tmpdir) / self.TEMP_SHAPED_SUFFIX
            ordinary = Path(tmpdir) / self.ORDINARY_SUFFIX
            self._build_fixture(temp_shaped, self.MISSING_FILE_YAML)
            self._build_fixture(ordinary, self.MISSING_FILE_YAML)

            temp_rc, temp_out = self._run_at(temp_shaped)
            ordinary_rc, ordinary_out = self._run_at(ordinary)

            self.assertEqual(
                temp_rc, ordinary_rc,
                "Verdict depends on the repo's location on disk (fail-open): "
                f"temp-shaped rc={temp_rc}, ordinary rc={ordinary_rc}\n"
                f"temp-shaped output:\n{temp_out}\nordinary output:\n{ordinary_out}"
            )
            # Findings differ only by the (absolute) workflow path they cite.
            self.assertEqual(
                temp_out.count('references missing file'),
                ordinary_out.count('references missing file'),
                "Finding counts differ between locations"
            )

    def test_present_file_is_clean_under_temp_shaped_root(self):
        """Removing the exemption must not invert into a false positive.

        Same trigger-shaped root, but the referenced file exists -> clean.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / self.TEMP_SHAPED_SUFFIX
            self._build_fixture(
                root,
                self.MISSING_FILE_YAML,
                stub_files=[Path('tools') / 'verify_definitely_absent_gate.py'],
            )

            exit_code, stdout = self._run_at(root)

            self.assertEqual(
                exit_code, 0,
                f"Fixture provides the file it cites, so the gate must be clean:\n{stdout}"
            )

    def test_source_has_no_path_shape_sniffing(self):
        """Static guard: the gate must never branch on the shape of its root path.

        Rules-as-code -- the prose fix was applied once and silently regressed because
        a second copy survived. This fails on any new copy anywhere in the module.
        """
        source = (
            Path(__file__).parent.parent / 'tools' / 'ci_gate_runability.py'
        ).read_text(encoding='utf-8')

        code_lines = [
            (i, line) for i, line in enumerate(source.splitlines(), 1)
            if not line.lstrip().startswith('#')
        ]
        offenders = [
            f"{i}: {line.strip()}"
            for i, line in code_lines
            if 'AppData' in line or 'is_temp_fixture' in line
        ]
        self.assertEqual(
            offenders, [],
            "ci_gate_runability.py must not special-case temp-shaped repo roots; "
            "test fixtures create the files they reference instead. Offending lines:\n"
            + "\n".join(offenders)
        )


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

        # The real ci.yml must be clean: every gate step's script exists and every
        # required check in tools/required-checks.json names a job.
        self.assertEqual(result.returncode, 0,
                         f"real ci.yml has runability findings:\n{result.stdout}\n{result.stderr}")


class TestCIGateRenamedGates(unittest.TestCase):
    """Behavioural red-first cases for the 2026-10-14 adv-enforcement findings F-07/F-08/F-09/F-10.

    Each case drives the real tool against a fixture root and asserts its exit code.
    """

    _CI = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  ci:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-shard: [0, 1]
    steps:
      - uses: actions/checkout@v4
      - name: Shard tests
        run: python -m pytest tests/
  %(aggregator)s:
    runs-on: ubuntu-latest
    steps:
      - name: Aggregate
        run: echo ok
"""

    def run_tool(self, yaml_content, **kwargs):
        return TestCIGateRunability.run_tool(self, yaml_content, **kwargs)

    def test_renamed_required_job_is_a_finding(self):
        """F-07: required check 'windows' with its job renamed to 'windows-x' must fail (red today)."""
        yaml = self._CI % {'aggregator': 'windows-x'}
        exit_code, stdout, _, _ = self.run_tool(yaml, required=['ci (0)', 'ci (1)', 'windows'])
        self.assertEqual(exit_code, 1, stdout)
        self.assertIn("'windows'", stdout)

    def test_unrenamed_required_jobs_are_clean(self):
        """Control for F-07: the same fixture with the job name intact passes."""
        yaml = self._CI % {'aggregator': 'windows'}
        exit_code, stdout, _, _ = self.run_tool(yaml, required=['ci (0)', 'ci (1)', 'windows'])
        self.assertEqual(exit_code, 0, stdout)

    def test_renamed_gate_step_in_block_scalar_is_a_finding(self):
        """F-08/F-09: a `run: |` gate whose script was renamed away must fail (red today)."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  secrets:
    runs-on: ubuntu-latest
    steps:
      - name: Secret scan
        run: |
          python tools/secret_scan_renamed.py .
"""
        exit_code, stdout, _, _ = self.run_tool(yaml, stub_files=[])
        self.assertEqual(exit_code, 1, stdout)
        self.assertIn('tools/secret_scan_renamed.py', stdout)

    def test_multi_file_step_second_file_missing_is_a_finding(self):
        """F-08/F-09: the first file exists, the second does not; the step must still fail."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  gates:
    runs-on: ubuntu-latest
    steps:
      - name: Two gates
        run: |
          python tools/secret_scan.py .
          python tools/gone_gate_missing.py
"""
        exit_code, stdout, _, _ = self.run_tool(yaml, stub_files=['tools/secret_scan.py'])
        self.assertEqual(exit_code, 1, stdout)
        self.assertIn('tools/gone_gate_missing.py', stdout)

    def test_non_family_gate_missing_file_is_a_finding(self):
        """F-08: a gate outside the known suite families is still checked for its script."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  misc:
    runs-on: ubuntu-latest
    steps:
      - name: Custom check
        run: python tools/totally_new_check.py --strict
"""
        exit_code, stdout, _, _ = self.run_tool(yaml, stub_files=[])
        self.assertEqual(exit_code, 1, stdout)
        self.assertIn('tools/totally_new_check.py', stdout)

    def test_path_mentioned_in_shell_comment_is_not_a_finding(self):
        """A `# ...` comment inside a run block naming a missing path is not a gate invocation (main CI red)."""
        yaml = """
name: CI

on:
  pull_request:
    branches: [main]

jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - name: Detect docs-only change
        run: |
          # Anchored: an unanchored docs/ matched hooks/docs/pre-push.sh, which does not exist here.
          echo ok
"""
        exit_code, stdout, _, _ = self.run_tool(yaml, stub_files=[])
        self.assertEqual(exit_code, 0, stdout)

    def test_missing_manifest_fails_closed(self):
        """Without a readable required-checks manifest the tool must exit 2, never pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir) / '.github' / 'workflows'
            workflows_dir.mkdir(parents=True)
            (workflows_dir / 'test.yml').write_text(self._CI % {'aggregator': 'windows'}, encoding='utf-8')
            result = subprocess.run(
                [sys.executable,
                 str(Path(__file__).parent.parent / 'tools' / 'ci_gate_runability.py'),
                 '--root', tmpdir],
                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class TestDocsOnlyDetectorFailClosed(unittest.TestCase):
    """Safety test: docs-only detector must fail closed on mixed or novel diffs.

    The docs-only-gate outputs is_docs_only based on file patterns. Any unrecognized
    path class or mixed diff (docs + non-docs) must yield is_docs_only=false to run
    the full suite, ensuring no code drift is missed by over-scoping.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_docs_only_gate_exists(self):
        """docs-only-gate job must exist and output is_docs_only."""
        import yaml
        ci_path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        self.assertTrue(ci_path.exists(), f"ci.yml not found at {ci_path}")

        with open(ci_path, 'r', encoding='utf-8') as f:
            workflow = yaml.safe_load(f)

        docs_only_job = workflow['jobs'].get('docs-only-gate')
        self.assertIsNotNone(docs_only_job, "docs-only-gate job not found")

        # Verify it outputs is_docs_only
        outputs = docs_only_job.get('outputs', {})
        self.assertIn('is_docs_only', outputs,
            "docs-only-gate must output is_docs_only")

    def test_browser_proofs_uses_is_docs_only(self):
        """browser-proofs must skip ONLY when docs-only-gate succeeded as docs-only.

        Originally this asserted the job's if: contained the literal substring
        'false' (an `== 'false'` check). That phrasing is itself unsafe: with no
        always(), GitHub implicitly ANDs success() onto the condition, so a
        failed/cancelled docs-only-gate makes this job report skipped -- which the
        windows/browser-proofs aggregators can mistake for "correctly skipped",
        manufacturing a false green for a check that never ran (root-caused
        2026-10-05). The fixed condition is fail-OPEN: skip only on a confirmed
        'success' + 'true' docs-only result; run in every other case.
        """
        import yaml
        ci_path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'

        with open(ci_path, 'r', encoding='utf-8') as f:
            workflow = yaml.safe_load(f)

        browser_job = workflow['jobs'].get('browser-proofs')
        self.assertIsNotNone(browser_job, "browser-proofs job not found")

        job_if = browser_job.get('if', '')
        self.assertIn('is_docs_only', job_if,
            "browser-proofs must use is_docs_only output in its if condition")
        self.assertIn('always()', job_if,
            "browser-proofs if: must call always(), otherwise GitHub implicitly "
            "ANDs success() onto it and a failed/cancelled docs-only-gate "
            "skip-cascades this job instead of running it")
        self.assertIn("!= 'success'", job_if,
            "browser-proofs must treat a non-successful docs-only-gate as "
            "not-docs-only (fail open) so it runs rather than skips")

    def test_windows_shard_uses_is_docs_only(self):
        """windows-shard job must skip on is_docs_only == 'false'."""
        import yaml
        ci_path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'

        with open(ci_path, 'r', encoding='utf-8') as f:
            workflow = yaml.safe_load(f)

        windows_job = workflow['jobs'].get('windows-shard')
        self.assertIsNotNone(windows_job, "windows-shard job not found")

        # Must have an if condition using is_docs_only output
        job_if = windows_job.get('if', '')
        self.assertIn('is_docs_only', job_if,
            "windows-shard must use is_docs_only output in its if condition")


# Rendered-check-name cases (r975 adversarial pass 2): see test_ci_needs_skip_guard.py.
_RENDER_REQUIRED = ['ci (0)', 'ci (1)', 'ci (2)', 'ci (3)']
_RENDER_HEAD = "jobs:\n  ci:\n    runs-on: ubuntu-latest\n"
_RENDER_TAIL = "    steps:\n      - run: echo noop\n"
_RENDER_FULL = "    strategy:\n      matrix:\n        python-shard: [0, 1, 2, 3]\n"


def _render_yaml(name_line='', strategy=_RENDER_FULL):
    return _RENDER_HEAD + name_line + strategy + _RENDER_TAIL


class TestRequiredContextRendering(unittest.TestCase):
    """A required context `job (N)` must resolve to a job whose RENDERED check name equals it."""

    def _run(self, content, required=_RENDER_REQUIRED):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            workflows_dir = tmppath / '.github' / 'workflows'
            workflows_dir.mkdir(parents=True)
            (workflows_dir / 'test.yml').write_text(content, encoding='utf-8')
            _write_manifest(tmppath, required)
            result = subprocess.run(
                [sys.executable,
                 str(Path(__file__).parent.parent / 'tools' / 'ci_gate_runability.py'),
                 '--root', str(tmppath)],
                capture_output=True, text=True, encoding='utf-8', errors='replace')
        return result.returncode, result.stdout

    def test_name_override_that_renders_other_names_is_a_finding(self):
        """r975 M1: `name: shard-${{ matrix.python-shard }}` never reports 'ci (N)' -- must FAIL."""
        code, out = self._run(_render_yaml("    name: shard-${{ matrix.python-shard }}\n"))
        self.assertEqual(code, 1, out)
        self.assertIn("required check 'ci (0)'", out)

    def test_suffix_not_a_matrix_value_is_a_finding(self):
        """r975 M2: matrix shrunk to [0, 1] -- 'ci (2)' can never report -- must FAIL."""
        shrunk = "    strategy:\n      matrix:\n        python-shard: [0, 1]\n"
        code, out = self._run(_render_yaml(strategy=shrunk))
        self.assertEqual(code, 1, out)
        self.assertIn("required check 'ci (2)'", out)
        self.assertNotIn("required check 'ci (0)'", out)

    def test_matrix_expression_cannot_verify_suffixes(self):
        expr = "    strategy:\n      matrix: ${{ fromJSON(needs.x.outputs.m) }}\n"
        code, out = self._run(_render_yaml(strategy=expr))
        self.assertEqual(code, 1, out)
        self.assertIn("required check 'ci (0)'", out)

    def test_override_rendering_the_context_passes(self):
        """Control: an override whose rendered form equals the context is clean."""
        code, out = self._run(_render_yaml("    name: ci (${{ matrix.python-shard }})\n"))
        self.assertEqual(code, 0, out)

    def test_plain_matrix_job_passes(self):
        """Control: the real shape (no override, full matrix) is clean."""
        code, out = self._run(_render_yaml())
        self.assertEqual(code, 0, out)


if __name__ == '__main__':
    unittest.main()
