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


class TestPyflakesIncrementalParity(unittest.TestCase):
    """The per-file incremental scan must give the same verdict as a full scan.

    A file's pyflakes result is reused only when its path and content hash match
    a cached entry. A full scan (--no-cache) recomputes every file. Both paths must
    agree on findings, new/stale keys and exit code, and every fallback to a full
    scan must say so in the JSON "scan" block.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.root = os.path.join(self.test_dir, 'repo')
        self.cache_dir = os.path.join(self.test_dir, 'cache')
        os.makedirs(os.path.join(self.root, 'pkg'))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write(self, relative_path, content, root=None):
        full_path = os.path.join(root or self.root, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _baseline(self, violations, root=None):
        path = os.path.join(root or self.root, '.pyflakes-baseline.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'violations': violations}, f)
        return path

    def _run(self, *extra, root=None, baseline=None):
        root = root or self.root
        baseline = baseline or os.path.join(root, '.pyflakes-baseline.json')
        cmd = [sys.executable, GATE_SCRIPT, '--root', root, '--paths', 'pkg',
               '--baseline', baseline, '--json', '--cache-dir', self.cache_dir, *extra]
        result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
        data = json.loads(result.stdout) if result.returncode in (0, 1) else None
        return result.returncode, data, result.stderr

    @REQUIRES_PYFLAKES
    def test_incremental_verdict_matches_full_scan_after_one_file_changes(self):
        """RED: one changed file with one new finding; incremental == full scan."""
        self._write('pkg/a.py', 'def a():\n    return 1\n')
        self._write('pkg/b.py', 'def b():\n    return 2\n')
        self._baseline({})
        seed_code, _, seed_err = self._run()
        self.assertEqual(seed_code, 0, seed_err)

        self._write('pkg/b.py', 'import os\n\n\ndef b():\n    return 2\n')
        inc_code, inc, inc_err = self._run()
        full_code, full, full_err = self._run('--no-cache')
        self.assertEqual(full_code, 1, full_err)
        self.assertEqual(inc_code, full_code, inc_err)
        self.assertEqual(inc['ok'], full['ok'])
        self.assertEqual(inc['new'], full['new'])
        self.assertEqual(inc['stale'], full['stale'])
        self.assertEqual(inc['findings'], full['findings'])
        self.assertEqual(inc['by_category'], full['by_category'])
        self.assertEqual(inc['scan']['mode'], 'incremental')
        self.assertEqual(inc['scan']['scanned'], 1, 'only the changed file may be re-scanned')
        self.assertEqual(inc['scan']['reused'], 1)
        self.assertTrue(any('pkg/b.py@UnusedImport' in item for item in inc['new']))

    @REQUIRES_PYFLAKES
    def test_unchanged_tree_reuses_every_cached_file(self):
        """RED: a second run over an unchanged tree re-scans nothing."""
        self._write('pkg/a.py', 'def a():\n    return 1\n')
        self._write('pkg/b.py', 'def b():\n    return 2\n')
        self._baseline({})
        self._run()
        code, out, err = self._run()
        self.assertEqual(code, 0, err)
        self.assertEqual(out['scan']['mode'], 'incremental')
        self.assertEqual(out['scan']['scanned'], 0)
        self.assertEqual(out['scan']['reused'], 2)

    @REQUIRES_PYFLAKES
    def test_cold_cache_falls_back_to_full_scan_and_says_so(self):
        """RED: no cache on disk -> full scan, reason printed in the JSON output."""
        self._write('pkg/a.py', 'def a():\n    return 1\n')
        self._baseline({})
        code, out, err = self._run()
        self.assertEqual(code, 0, err)
        self.assertEqual(out['scan']['mode'], 'full')
        self.assertIn('cold cache', out['scan']['reason'])

    @REQUIRES_PYFLAKES
    def test_missing_baseline_forces_full_scan(self):
        """RED: a missing baseline file forces a full scan (reason says baseline missing)."""
        self._write('pkg/a.py', 'def a():\n    return 1\n')
        self._baseline({})
        self._run()
        missing = os.path.join(self.root, 'does-not-exist.json')
        code, out, err = self._run(baseline=missing)
        self.assertEqual(code, 0, err)
        self.assertEqual(out['scan']['mode'], 'full')
        self.assertIn('baseline missing', out['scan']['reason'])

    @REQUIRES_PYFLAKES
    def test_corrupt_cache_falls_back_to_full_scan_and_still_verdicts_correctly(self):
        """RED: an unreadable cache never changes the verdict; it forces a full scan."""
        self._write('pkg/a.py', 'import os\n\n\ndef a():\n    return 1\n')
        self._baseline({})
        self._run()
        for name in os.listdir(self.cache_dir):
            with open(os.path.join(self.cache_dir, name), 'w', encoding='utf-8') as f:
                f.write('{not json')
        code, out, err = self._run()
        self.assertEqual(code, 1, err)
        self.assertEqual(out['scan']['mode'], 'full')
        self.assertIn('cache unreadable', out['scan']['reason'])
        self.assertTrue(any('pkg/a.py@UnusedImport' in item for item in out['new']))

    @REQUIRES_PYFLAKES
    def test_check_mode_is_read_only_and_writes_no_cache(self):
        """RED: --check is the read-only verification mode; it must not create the cache."""
        self._write('pkg/a.py', 'def a():\n    return 1\n')
        self._baseline({})
        code, out, err = self._run('--check')
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.isdir(self.cache_dir) and os.listdir(self.cache_dir),
                         'check mode must not write the per-file cache')

    @REQUIRES_PYFLAKES
    def test_root_change_does_not_reuse_other_roots_cache(self):
        """RED: the same tree under a different --root starts cold (no cross-root reuse)."""
        self._write('pkg/a.py', 'def a():\n    return 1\n')
        self._baseline({})
        self._run()
        other = os.path.join(self.test_dir, 'other-repo')
        self._write('pkg/a.py', 'def a():\n    return 1\n', root=other)
        self._baseline({}, root=other)
        code, out, err = self._run(root=other)
        self.assertEqual(code, 0, err)
        self.assertEqual(out['scan']['mode'], 'full')
        self.assertIn('cold cache', out['scan']['reason'])


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
