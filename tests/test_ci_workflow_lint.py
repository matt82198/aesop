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
        # Orphan sweep (gate inventory, PR #709): these three shipped with unit
        # tests but were invoked by NOTHING -- tools that grade nothing.
        "python tools/sibling_import_check.py --check",
        "python tools/fixture_intent_check.py --root .",
        "python tools/port_fidelity_check.py --check --root .",
    ]

    # git_identity_check.py is deliberately NOT in the list above. Its subject is a
    # managed target repo's *local* git identity; a GitHub Actions checkout sets only
    # a --global identity, so the tool reports mismatch on every CI run (verified:
    # exit 1, "user.name mismatch: expected '...' but git has 'None'"). Wiring it as a
    # CI gate would be permanently red; wiring it with --mode warn or
    # continue-on-error would be the decoration this class exists to prevent. It stays
    # unwired until it has a real enforcement point (an aesop-managed repo), tracked
    # rather than softened.
    DELIBERATELY_UNWIRED = ["git_identity_check.py"]

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

    # Steps added by the orphan sweep, with the invocation each must carry.
    ORPHAN_SWEEP_STEPS = {
        "Sibling import guard (tools/ sys.path discipline)":
            "python tools/sibling_import_check.py --check",
        "Deliberately-broken fixture manifest gate":
            "python tools/fixture_intent_check.py --root .",
        "Port-task fidelity gate":
            "python tools/port_fidelity_check.py --check --root .",
    }

    def _ci_steps_by_name(self):
        import yaml
        path = self.REAL_REPO_ROOT / ".github" / "workflows" / "ci.yml"
        with open(path, "r", encoding="utf-8") as handle:
            workflow = yaml.safe_load(handle)
        ci_job = workflow["jobs"].get("ci")
        self.assertIsNotNone(ci_job, "ci job not found in ci.yml")
        return {s.get("name"): s for s in ci_job.get("steps", []) if s.get("name")}

    def test_orphan_sweep_steps_exist_with_expected_invocations(self):
        """Each newly-wired orphan gate is a named step running its check command."""
        steps = self._ci_steps_by_name()
        for name, invocation in self.ORPHAN_SWEEP_STEPS.items():
            with self.subTest(step=name):
                self.assertIn(name, steps, "ci step %r missing from ci.yml" % name)
                self.assertEqual(
                    str(steps[name].get("run", "")).strip(), invocation,
                    "ci step %r must run exactly %r" % (name, invocation),
                )

    def test_orphan_sweep_steps_are_real_enforcement(self):
        """Shard-0 scoped (they are shard-invariant) and never continue-on-error.

        continue-on-error would turn the gate back into decoration -- the exact
        failure mode the orphan sweep was fixing.
        """
        steps = self._ci_steps_by_name()
        for name in self.ORPHAN_SWEEP_STEPS:
            with self.subTest(step=name):
                step = steps[name]
                self.assertIn(
                    "matrix.python-shard == 0", str(step.get("if", "")),
                    "ci step %r is shard-invariant; gate it on shard 0" % name,
                )
                self.assertNotIn(
                    "continue-on-error", step,
                    "ci step %r must fail the build, not continue-on-error" % name,
                )

    def test_orphan_inventory_fully_accounted_for(self):
        """Every tool in the PR #709 orphan inventory is wired or explicitly excepted.

        Guards against a fourth orphan quietly reappearing: each of the four tools
        must be either invoked in ci.yml or named in DELIBERATELY_UNWIRED with the
        reason recorded above.
        """
        ci_yml = self._read(".github/workflows/ci.yml")
        inventory = [
            "sibling_import_check.py",
            "git_identity_check.py",
            "fixture_intent_check.py",
            "port_fidelity_check.py",
        ]
        for tool in inventory:
            with self.subTest(tool=tool):
                wired = ("python tools/%s" % tool) in ci_yml
                excepted = tool in self.DELIBERATELY_UNWIRED
                self.assertTrue(
                    wired or excepted,
                    "%s is neither wired into ci.yml nor listed in "
                    "DELIBERATELY_UNWIRED with a recorded reason" % tool,
                )
                self.assertFalse(
                    wired and excepted,
                    "%s is both wired and listed as deliberately unwired" % tool,
                )

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


class TestCIJobNoJobLevelIf(unittest.TestCase):
    """Safety test: ci job must never have a job-level if condition (PR #170 deadlock).

    The ci job is REQUIRED under branch protection. If it has a job-level if condition,
    it can report skipped status, which deadlocks PRs forever (skipped does not satisfy
    required check). Step-level conditions are safe; job-level conditions are forbidden.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_ci_job_has_no_job_level_if(self):
        """The ci job must not have an if condition at the job level."""
        ci_path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        self.assertTrue(ci_path.exists(), f"ci.yml not found at {ci_path}")

        import yaml
        with open(ci_path, 'r', encoding='utf-8') as f:
            workflow = yaml.safe_load(f)

        ci_job = workflow['jobs'].get('ci')
        self.assertIsNotNone(ci_job, "ci job not found in ci.yml")

        # ci job must NOT have an 'if' key at the job level
        self.assertNotIn('if', ci_job,
            "ci job has a job-level if condition, which causes PR deadlock (skipped status). "
            "PR #170 documented this: skipped required checks do not satisfy branch protection. "
            "Use step-level conditions instead.")


class TestWindowsAggregatorHandlesSkipped(unittest.TestCase):
    """Safety test: windows aggregator must treat skipped as pass.

    The windows-shard job is conditional (skipped on docs-only). The windows aggregator
    (required check) must handle needs.windows-shard.result == "skipped" and treat it as
    a pass, otherwise the aggregator fails when windows-shard is skipped.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_windows_aggregator_accepts_skipped(self):
        """The windows aggregator must accept skipped result from windows-shard."""
        ci_path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        self.assertTrue(ci_path.exists(), f"ci.yml not found at {ci_path}")

        with open(ci_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check that windows aggregator explicitly handles skipped
        # The check should look for: != "success" AND != "skipped" to fail
        self.assertIn('needs.windows-shard.result', content,
            "windows aggregator must reference needs.windows-shard.result")

        # Look for the pattern that accepts both success and skipped
        self.assertIn('skipped', content.split('windows:')[1].split('ps1-syntax-check:')[0],
            "windows aggregator must explicitly handle skipped result")


class TestVerifyTestSuiteCountOnceInCI(unittest.TestCase):
    """Safety test: verify_test_suite_count --check appears exactly once in ci job.

    C1 removed the duplicate verify_test_suite_count step (it appeared twice).
    Must stay exactly once to avoid redundant runs and drift confusion.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent

    def test_verify_test_suite_count_appears_once(self):
        """verify_test_suite_count --check must appear exactly once in ci.yml."""
        ci_path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        self.assertTrue(ci_path.exists(), f"ci.yml not found at {ci_path}")

        with open(ci_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Count occurrences of the verify_test_suite_count --check command
        count = content.count('python tools/verify_test_suite_count.py --check')

        self.assertEqual(count, 1,
            f"verify_test_suite_count --check must appear exactly once in ci.yml, found {count} times. "
            "Duplicate steps cause redundant runs and confusion about actual drift state.")


class TestMainFullConcurrencyNotSelfCancelling(unittest.TestCase):
    """main-full.yml must never cancel a previous merge's post-merge verification.

    main-full is the POST-MERGE drift guard: it runs the full sequential suite on the
    merged commit. It triggered on `push: branches: [main]` while grouping concurrency
    on `github.ref` -- which on a push to main is ALWAYS `refs/heads/main`. Combined
    with `cancel-in-progress: true`, every merge killed the previous merge's
    verification run. Measured 2026-08-02: 8 of the last 12 main-full runs CANCELLED,
    i.e. the post-merge net was off 67% of the time, worst exactly during merge bursts
    when drift is most likely.

    The group must be per-COMMIT (github.sha / run id), and cancellation must be off.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent

    def _load_main_full(self):
        import yaml
        path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'main-full.yml'
        self.assertTrue(path.exists(), f"main-full.yml not found at {path}")
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f), path.read_text(encoding='utf-8')

    def test_main_full_has_concurrency_block(self):
        workflow, _ = self._load_main_full()
        self.assertIn('concurrency', workflow,
            "main-full.yml must declare a concurrency block")

    def test_main_full_cancel_in_progress_is_false(self):
        """cancel-in-progress must be false: every merged commit gets verified."""
        workflow, _ = self._load_main_full()
        cancel = workflow['concurrency'].get('cancel-in-progress')
        self.assertIs(cancel, False,
            "main-full.yml has cancel-in-progress != false. main-full is the post-merge "
            "drift guard, not a merge gate -- cancelling it means merged commits ship "
            "unverified. Measured 8/12 runs CANCELLED before this fix.")

    def test_main_full_concurrency_group_is_per_commit(self):
        """The group key must vary per commit, not be constant on refs/heads/main."""
        workflow, _ = self._load_main_full()
        group = str(workflow['concurrency'].get('group', ''))
        self.assertTrue(
            'github.sha' in group or 'github.run_id' in group,
            f"main-full concurrency group {group!r} is not per-commit. Grouping on "
            "github.ref collapses every push to main into one group, so consecutive "
            "merges contend for the same slot.")

    def test_main_full_concurrency_group_not_bare_github_ref(self):
        """Regression pin: the exact pre-fix group must not come back."""
        workflow, _ = self._load_main_full()
        group = str(workflow['concurrency'].get('group', ''))
        self.assertNotIn('github.ref', group,
            "main-full concurrency group still keys on github.ref, which is always "
            "refs/heads/main for push-to-main events.")


class TestCIDoesNotDuplicateMainFull(unittest.TestCase):
    """ci.yml must not re-run the full suite on push to main.

    main-full.yml exists precisely because main is already protected by PR checks
    (its own header comment says so). Leaving `push: branches: [main]` on ci.yml made
    every merge pay ~30 job-minutes twice -- once via ci, once via main-full -- for
    a commit whose ci run already passed on the PR.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent

    def _load_ci(self):
        import yaml
        path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        self.assertTrue(path.exists(), f"ci.yml not found at {path}")
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _triggers(self, workflow):
        # PyYAML parses the bare key `on:` as the boolean True.
        return workflow.get('on', workflow.get(True, {}))

    def test_ci_has_no_push_to_main_trigger(self):
        workflow = self._load_ci()
        triggers = self._triggers(workflow)
        push = triggers.get('push')
        if push is None:
            return  # no push trigger at all -- correct
        branches = (push or {}).get('branches', []) or []
        self.assertNotIn('main', branches,
            "ci.yml still triggers on push to main, duplicating main-full.yml's full "
            "run on every merge. main is protected by PR checks; post-merge "
            "verification is main-full's job.")

    def test_ci_still_triggers_on_pull_request(self):
        """Removing push must NOT remove the PR trigger -- ci is the required gate."""
        workflow = self._load_ci()
        triggers = self._triggers(workflow)
        self.assertIn('pull_request', triggers,
            "ci.yml must still run on pull_request -- it is the required merge gate")

    def test_main_full_still_covers_push_to_main(self):
        """Post-merge coverage must not be lost -- main-full keeps the push trigger."""
        import yaml
        path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'main-full.yml'
        with open(path, 'r', encoding='utf-8') as f:
            workflow = yaml.safe_load(f)
        triggers = workflow.get('on', workflow.get(True, {}))
        branches = (triggers.get('push') or {}).get('branches', []) or []
        self.assertIn('main', branches,
            "main-full.yml must trigger on push to main -- it is the only post-merge net")


class TestUIBuildStepsShardScoped(unittest.TestCase):
    """The ui/web build chain must run once, not identically on all 4 ci shards.

    The React build + tsc + dist-drift + vitest steps are shard-invariant: all four
    ubuntu shards did the same npm ci + build + type-check + vitest work, ~3x wasted.
    Gating them is STEP-level on purpose. A JOB-level `if:` on `ci` makes the matrix
    report `ci (N)` as skipped, and a skipped required check never satisfies branch
    protection -- PR #170 deadlocked forever that way. Never move this to job level.
    """

    REAL_REPO_ROOT = Path(__file__).resolve().parent.parent
    SHARD_ZERO = 'matrix.python-shard == 0'

    # Step names that must carry the shard-0 condition.
    SHARD_SCOPED_STEPS = [
        'Build React dashboard (ui/web/)',
        'TypeScript type check (ui/web/)',
        'Dist drift gate (committed ui/web/dist must match fresh build)',
        'Run React component tests (vitest)',
    ]

    def _ci_job_steps(self):
        import yaml
        path = self.REAL_REPO_ROOT / '.github' / 'workflows' / 'ci.yml'
        with open(path, 'r', encoding='utf-8') as f:
            workflow = yaml.safe_load(f)
        ci_job = workflow['jobs'].get('ci')
        self.assertIsNotNone(ci_job, "ci job not found in ci.yml")
        return ci_job, {s.get('name'): s for s in ci_job.get('steps', []) if s.get('name')}

    def test_ui_build_steps_are_shard_zero_only(self):
        _, steps = self._ci_job_steps()
        for name in self.SHARD_SCOPED_STEPS:
            with self.subTest(step=name):
                self.assertIn(name, steps, f"ci step {name!r} not found in ci.yml")
                condition = str(steps[name].get('if', ''))
                self.assertIn(self.SHARD_ZERO, condition,
                    f"ci step {name!r} runs on all 4 shards but is shard-invariant; "
                    f"gate it with `if: {self.SHARD_ZERO}`.")

    def test_ci_job_has_no_job_level_if_still(self):
        """Shard scoping must stay step-level (PR #170 deadlock guard)."""
        ci_job, _ = self._ci_job_steps()
        self.assertNotIn('if', ci_job,
            "ci job gained a job-level if condition -- skipped required checks "
            "deadlock PRs (PR #170). Conditions belong on steps.")

    def test_python_shard_matrix_still_four_way(self):
        """Sanity: the shard-0 condition only makes sense with a shard matrix."""
        ci_job, _ = self._ci_job_steps()
        shards = ci_job.get('strategy', {}).get('matrix', {}).get('python-shard')
        self.assertEqual(shards, [0, 1, 2, 3],
            "ci python-shard matrix changed; revisit the shard-0 step conditions")


if __name__ == "__main__":
    unittest.main()
