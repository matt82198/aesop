"""
Test suite for tools/pyflakes_gate.py

Red-first: a fixture with one unused import is a NEW finding (exit 1) against
an empty/mismatched baseline, and the SAME fixture passes (exit 0) once that
exact finding is recorded in the baseline. These two tests are the ratchet's
own falsifiability proof -- if either one breaks, the gate itself is inert.

pyflakes is a dev-only lint dependency (tools/CLAUDE.md "stdlib-only" policy
exception), not guaranteed present on every CI runner (e.g. a windows-shard
job that never installs it). Every test below that needs the real pyflakes
analysis is skipped -- not failed -- when the package is absent, via
`REQUIRES_PYFLAKES`. The one test that must run EVERYWHERE regardless of
whether pyflakes happens to be installed in this environment is
`test_pyflakes_not_installed_is_fail_closed_exit_2`: it forces the "not
installed" path with `python -S` (disables site-packages, so even an
installed pyflakes becomes unimportable for that one subprocess) rather than
depending on the ambient environment, so the exit-2 fail-closed contract is
exercised unconditionally, not just skipped-around.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

GATE_SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'tools', 'pyflakes_gate.py')

REQUIRES_PYFLAKES = unittest.skipUnless(
    importlib.util.find_spec("pyflakes") is not None,
    "pyflakes not installed (dev-only dependency, e.g. windows-shard CI)",
)


class TestPyflakesGate(unittest.TestCase):
    """Tests for pyflakes_gate.py against isolated fixture trees."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write(self, relative_path, content):
        full_path = os.path.join(self.test_dir, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return full_path

    def _run_gate(self, extra_args=None, json_output=False):
        cmd = [sys.executable, GATE_SCRIPT, '--root', self.test_dir, '--paths', 'pkg']
        if json_output:
            cmd.append('--json')
        if extra_args:
            cmd.extend(extra_args)
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

    # --- RED: a NEW finding above an empty/mismatched baseline must fail ---

    @REQUIRES_PYFLAKES
    def test_unused_import_is_new_finding_against_empty_baseline(self):
        """RED: one unused import, no baseline on disk -> NEW finding, exit 1."""
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')

        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')
        self.assertFalse(os.path.exists(baseline_path))

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path])
        self.assertEqual(exit_code, 1, f"expected exit 1 (new finding), got {exit_code}: {stderr}")
        self.assertIn('NEW', stderr)
        self.assertIn('UnusedImport', stderr)

    @REQUIRES_PYFLAKES
    def test_unused_import_is_new_finding_against_wrong_baseline(self):
        """RED: an unrelated committed baseline still treats the unused import as NEW."""
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')
        with open(baseline_path, 'w', encoding='utf-8') as f:
            json.dump({'violations': {'pkg/other.py@UnusedImport': 1}}, f)

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path])
        self.assertEqual(exit_code, 1, f"expected exit 1 (new + stale), got {exit_code}: {stderr}")
        self.assertIn('NEW', stderr)
        self.assertIn('pkg/mod.py@UnusedImport', stderr)

    # --- GREEN: the exact same finding, once baselined, passes ---

    @REQUIRES_PYFLAKES
    def test_unused_import_baselined_passes(self):
        """GREEN: the same unused import, recorded in the baseline, exits 0."""
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')
        with open(baseline_path, 'w', encoding='utf-8') as f:
            json.dump({'violations': {'pkg/mod.py@UnusedImport': 1}}, f)

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path])
        self.assertEqual(exit_code, 0, f"expected exit 0 (baselined), got {exit_code}: {stderr}")
        self.assertIn('PASS', stdout)

    @REQUIRES_PYFLAKES
    def test_clean_file_passes_with_empty_baseline(self):
        """A file with no findings passes even with no baseline on disk."""
        self._write('pkg/mod.py', 'def f():\n    return 1\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path])
        self.assertEqual(exit_code, 0, f"expected clean exit, got {exit_code}: {stderr}")

    # --- Burn-down: a fixed finding must be removed from the baseline, not left stale ---

    @REQUIRES_PYFLAKES
    def test_fixed_finding_leaves_stale_baseline_entry_failing(self):
        """A baseline entry whose finding was fixed is STALE, not silently fine."""
        self._write('pkg/mod.py', 'def f():\n    return 1\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')
        with open(baseline_path, 'w', encoding='utf-8') as f:
            json.dump({'violations': {'pkg/mod.py@UnusedImport': 1}}, f)

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path])
        self.assertEqual(exit_code, 1, f"expected exit 1 (stale baseline), got {exit_code}: {stderr}")
        self.assertIn('STALE', stderr)

    # --- --update-baseline (review-only) regenerates an exact-matching baseline ---

    @REQUIRES_PYFLAKES
    def test_update_baseline_then_check_passes(self):
        self._write('pkg/mod.py', 'import os\nimport sys\n\n\ndef f():\n    return os\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path, '--update-baseline'])
        self.assertEqual(exit_code, 0, stderr)
        self.assertTrue(os.path.exists(baseline_path))

        with open(baseline_path, encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data['violations'].get('pkg/mod.py@UnusedImport'), 1)

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path])
        self.assertEqual(exit_code, 0, f"expected clean exit after update-baseline, got {exit_code}: {stderr}")

    @REQUIRES_PYFLAKES
    def test_unused_variable_detected(self):
        """A dead local assignment is caught as UnusedVariable."""
        self._write('pkg/mod.py', 'def f():\n    x = 1\n    return 2\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path])
        self.assertEqual(exit_code, 1)
        self.assertIn('UnusedVariable', stderr)

    @REQUIRES_PYFLAKES
    def test_json_output_reports_by_category(self):
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')

        exit_code, stdout, stderr = self._run_gate(['--baseline', baseline_path], json_output=True)
        self.assertEqual(exit_code, 1)
        data = json.loads(stdout)
        self.assertFalse(data['ok'])
        self.assertEqual(data['by_category'].get('UnusedImport'), 1)
        self.assertEqual(len(data['new']), 1)

    @REQUIRES_PYFLAKES
    def test_missing_baseline_file_is_fail_closed_not_crash(self):
        """A --baseline path that doesn't exist is treated as an empty baseline."""
        self._write('pkg/mod.py', 'def f():\n    return 1\n')
        missing_baseline = os.path.join(self.test_dir, 'does-not-exist.json')

        exit_code, stdout, stderr = self._run_gate(['--baseline', missing_baseline])
        self.assertEqual(exit_code, 0, f"clean tree + missing baseline should pass: {stderr}")

    def test_pyflakes_not_installed_is_fail_closed_exit_2(self):
        """Fail-closed exit 2 when pyflakes can't be imported -- runs EVERYWHERE,
        not gated by REQUIRES_PYFLAKES: forces unavailability with `python -S`
        (disables site-packages for this one subprocess) instead of depending
        on whether the ambient test environment happens to have pyflakes
        installed, so this exact contract is exercised unconditionally on
        every CI runner (including a windows-shard job with no pyflakes)."""
        self._write('pkg/mod.py', 'def f():\n    return 1\n')
        baseline_path = os.path.join(self.test_dir, '.pyflakes-baseline.json')

        result = subprocess.run(
            [sys.executable, '-S', GATE_SCRIPT, '--root', self.test_dir, '--paths', 'pkg',
             '--baseline', baseline_path],
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 2,
            f"expected exit 2 (pyflakes unavailable), got {result.returncode}: {result.stderr}"
        )
        self.assertIn("not installed", result.stderr)
        self.assertIn("pip install pyflakes", result.stderr)


class TestPyflakesBurnDownAgainstTrunk(unittest.TestCase):
    """D40: the pre-push ratchet judges a branch against the TRUNK baseline.

    --trunk-baseline is the trunk's .pyflakes-baseline.json (the gate authority);
    --baseline is the branch's own copy. A burn-down (branch baseline is a strict
    subset of trunk, current findings within the branch baseline) passes with a
    note line. An addition, a finding outside the trunk, or a missing/malformed
    branch baseline still blocks.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write(self, relative_path, content):
        full_path = os.path.join(self.test_dir, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return full_path

    def _baseline(self, name, violations):
        path = os.path.join(self.test_dir, name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'violations': violations}, f)
        return path

    def _run_gate(self, branch_baseline, trunk_baseline):
        cmd = [sys.executable, GATE_SCRIPT, '--root', self.test_dir, '--paths', 'pkg',
               '--baseline', branch_baseline, '--trunk-baseline', trunk_baseline]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        return result.returncode, result.stdout, result.stderr

    def _two_file_trunk(self):
        return self._baseline('trunk.json', {
            'pkg/mod.py@UnusedImport': 1,
            'pkg/other.py@UnusedImport': 1,
        })

    # --- GREEN: a burn-down passes and says so ---

    @REQUIRES_PYFLAKES
    def test_burn_down_passes_with_note(self):
        """pkg/other.py was fixed on the branch; its trunk entry is removed. Must PASS."""
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        self._write('pkg/other.py', 'def g():\n    return 2\n')
        trunk = self._two_file_trunk()
        branch = self._baseline('branch.json', {'pkg/mod.py@UnusedImport': 1})

        exit_code, stdout, stderr = self._run_gate(branch, trunk)
        self.assertEqual(exit_code, 0, f"burn-down must pass (exit 0), got {exit_code}: {stderr}")
        self.assertIn('pyflakes baseline burned down: 1 entries removed', stdout)
        self.assertIn('PASS', stdout)

    # --- RED: additions and new findings still block ---

    @REQUIRES_PYFLAKES
    def test_addition_to_branch_baseline_blocks(self):
        """The branch baseline adds an entry the trunk lacks -> exit 1 even if the scan matches it."""
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        self._write('pkg/extra.py', 'import sys\n\n\ndef h():\n    return 3\n')
        trunk = self._baseline('trunk.json', {'pkg/mod.py@UnusedImport': 1})
        branch = self._baseline('branch.json', {
            'pkg/mod.py@UnusedImport': 1,
            'pkg/extra.py@UnusedImport': 1,
        })

        exit_code, stdout, stderr = self._run_gate(branch, trunk)
        self.assertEqual(exit_code, 1, f"addition must block (exit 1), got {exit_code}: {stderr}")
        self.assertIn('pkg/extra.py@UnusedImport', stderr)

    @REQUIRES_PYFLAKES
    def test_new_finding_outside_trunk_blocks_even_with_burn_down(self):
        """Branch burns down pkg/other.py but a NEW finding appears in pkg/x.py -> exit 1."""
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        self._write('pkg/x.py', 'import json\n\n\ndef k():\n    return 4\n')
        trunk = self._two_file_trunk()
        branch = self._baseline('branch.json', {'pkg/mod.py@UnusedImport': 1})

        exit_code, stdout, stderr = self._run_gate(branch, trunk)
        self.assertEqual(exit_code, 1, f"new finding must block (exit 1), got {exit_code}: {stderr}")
        self.assertIn('NEW', stderr)
        self.assertIn('pkg/x.py@UnusedImport', stderr)

    @REQUIRES_PYFLAKES
    def test_current_finding_above_branch_baseline_blocks(self):
        """Branch baseline shrinks pkg/other.py to 0 but the scan still finds it -> exit 1."""
        self._write('pkg/mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        self._write('pkg/other.py', 'import sys\n\n\ndef g():\n    return 2\n')
        trunk = self._two_file_trunk()
        branch = self._baseline('branch.json', {'pkg/mod.py@UnusedImport': 1})

        exit_code, stdout, stderr = self._run_gate(branch, trunk)
        self.assertEqual(exit_code, 1, f"unrecorded finding must block (exit 1), got {exit_code}: {stderr}")
        self.assertIn('pkg/other.py@UnusedImport', stderr)

    # --- RED: missing or malformed branch baseline blocks (never fail open) ---

    @REQUIRES_PYFLAKES
    def test_missing_branch_baseline_blocks(self):
        """Branch baseline file absent -> exit 1, even with a clean tree."""
        self._write('pkg/mod.py', 'def f():\n    return 1\n')
        trunk = self._two_file_trunk()
        branch = os.path.join(self.test_dir, 'does-not-exist.json')

        exit_code, stdout, stderr = self._run_gate(branch, trunk)
        self.assertEqual(exit_code, 1, f"missing branch baseline must block (exit 1), got {exit_code}: {stderr}")

    @REQUIRES_PYFLAKES
    def test_malformed_branch_baseline_blocks(self):
        """Branch baseline is not valid JSON -> exit 1, even with a clean tree."""
        self._write('pkg/mod.py', 'def f():\n    return 1\n')
        trunk = self._two_file_trunk()
        branch = os.path.join(self.test_dir, 'branch.json')
        with open(branch, 'w', encoding='utf-8') as f:
            f.write('{not json')

        exit_code, stdout, stderr = self._run_gate(branch, trunk)
        self.assertEqual(exit_code, 1, f"malformed branch baseline must block (exit 1), got {exit_code}: {stderr}")

    def test_pyflakes_unavailable_with_trunk_still_exit_2(self):
        """Unavailable pyflakes stays exit 2 in trunk mode (runs everywhere, forced via python -S)."""
        self._write('pkg/mod.py', 'def f():\n    return 1\n')
        trunk = self._two_file_trunk()
        branch = self._baseline('branch.json', {'pkg/mod.py@UnusedImport': 1})
        result = subprocess.run(
            [sys.executable, '-S', GATE_SCRIPT, '--root', self.test_dir, '--paths', 'pkg',
             '--baseline', branch, '--trunk-baseline', trunk],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
        )
        self.assertEqual(result.returncode, 2, f"expected exit 2, got {result.returncode}: {result.stderr}")


class TestPyflakesGateAgainstRealBaseline(unittest.TestCase):
    """Regression: run the real gate against the real repo + committed baseline.

    Mirrors TestPortabilityCheckAgainstRealBaseline / the ci.yml "State API lint
    gate" step: reproduces the exact CI invocation locally so a newly-introduced
    unused import/variable is caught before push instead of failing CI shard 0.
    """

    def test_repo_passes_pyflakes_ratchet(self):
        repo_root = os.path.join(os.path.dirname(__file__), '..')
        baseline_path = os.path.join(repo_root, '.pyflakes-baseline.json')

        result = subprocess.run(
            [sys.executable, GATE_SCRIPT, '--root', repo_root, '--baseline', baseline_path],
            capture_output=True,
            text=True,
        )
        if 'pyflakes_gate: the \'pyflakes\' package is not installed' in result.stderr:
            self.skipTest("pyflakes not installed in this environment (dev-only dependency)")
        self.assertEqual(
            result.returncode, 0,
            "pyflakes_gate ratchet failed against committed baseline (new or "
            "stale unused-import/unused-variable findings) -- fix new findings "
            "or regenerate the baseline with --update-baseline after review:\n"
            + result.stderr
        )


class TestPyflakesGateImport(unittest.TestCase):
    """Test that pyflakes_gate.py has no syntax errors and --help works."""

    def test_help(self):
        result = subprocess.run(
            [sys.executable, GATE_SCRIPT, '--help'],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
