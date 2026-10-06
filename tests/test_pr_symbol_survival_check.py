#!/usr/bin/env python3
"""
Tests for tools/pr_symbol_survival_check.py (Guardrail G13) -- PR symbol survival check.

Builds real throwaway git repos at runtime (not mocks) and drives the tool as a real
subprocess against them, per the "behavioral proof, never source-grep" rule: each
scenario exercises the actual conflict-resolution mechanic from the PR #745 incident
(a merge resolved by taking one side's whole file), not an inspection of the tool's
source text.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL = os.path.join(_REPO_ROOT, 'tools', 'pr_symbol_survival_check.py')


def _git(repo, *args, check=True):
    result = subprocess.run(
        ['git', '-C', repo] + list(args),
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        timeout=30,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            'git {} failed (exit {}): {}'.format(args, result.returncode, result.stderr)
        )
    return result.stdout


def _write(repo, rel_path, content):
    full = os.path.join(repo, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(content)


def _commit(repo, message):
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', message)
    return _git(repo, 'rev-parse', 'HEAD').strip()


def _init_repo(tmp_dir):
    repo = os.path.join(tmp_dir, 'repo')
    os.makedirs(repo)
    _git(repo, 'init', '-q', '-b', 'main')
    _git(repo, 'config', 'user.email', 'test@example.com')
    _git(repo, 'config', 'user.name', 'Test User')
    # No GPG signing, no global config touched -- everything is local to this throwaway repo.
    _git(repo, 'config', 'commit.gpgsign', 'false')
    return repo


def _run_tool(repo, *extra_args):
    result = subprocess.run(
        [sys.executable, _TOOL] + list(extra_args),
        cwd=repo,
        capture_output=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
    )
    return result.returncode, result.stdout, result.stderr


class TestPrSymbolSurvivalCheck(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix='symsurv_')
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_conflict_resolution_takes_theirs_wholesale_fails_naming_dropped_symbol(self):
        """Reproduces the PR #745 mechanic: a conflicting merge resolved by taking
        the other side's whole file silently drops a function the feature commit
        added. The check must FAIL and name it."""
        repo = _init_repo(self._tmp_dir)

        _write(repo, 'mod.py', 'def existing():\n    pass\n')
        base_sha = _commit(repo, 'base: existing()')

        _git(repo, 'checkout', '-q', '-b', 'feature')
        _write(repo, 'mod.py', 'def existing():\n    pass\n\n\ndef alpha():\n    pass\n')
        alpha_commit_sha = _commit(repo, 'feat: add alpha')

        _git(repo, 'checkout', '-q', 'main')
        _write(repo, 'mod.py', 'def existing():\n    pass\n\n\ndef gamma():\n    pass\n')
        _commit(repo, 'main: add gamma')

        _git(repo, 'checkout', '-q', 'feature')
        merge = subprocess.run(
            ['git', '-C', repo, 'merge', 'main', '-q', '-m', 'merge attempt'],
            capture_output=True, encoding='utf-8', errors='replace', timeout=30,
        )
        self.assertNotEqual(merge.returncode, 0, 'expected mod.py to conflict between the two sides')

        # The incident mechanic: resolve the conflict by taking the other side's
        # whole file, silently dropping this branch's own addition (alpha).
        _git(repo, 'checkout', '--theirs', 'mod.py')
        _git(repo, 'add', 'mod.py')
        head_sha = _commit(repo, 'merge: resolve by taking theirs (drops alpha)')

        rc, stdout, stderr = _run_tool(repo, '--base', base_sha, '--head', head_sha, '--json')
        self.assertEqual(rc, 1, 'stdout={}\nstderr={}'.format(stdout, stderr))
        report = json.loads(stdout)
        missing_names = {entry['symbol'] for entry in report['missing']}
        self.assertIn('alpha', missing_names)
        self.assertNotIn('gamma', missing_names)  # gamma genuinely survives, must not be flagged
        alpha_entry = next(e for e in report['missing'] if e['symbol'] == 'alpha')
        self.assertEqual(alpha_entry['added_by']['sha'], alpha_commit_sha)
        self.assertIsNotNone(alpha_entry['dropped_by'], 'should identify the merge commit that dropped it')
        self.assertEqual(alpha_entry['dropped_by']['sha'], head_sha)

    def test_proper_merge_keeping_both_intents_passes(self):
        """A merge resolution that keeps both sides' additions must PASS."""
        repo = _init_repo(self._tmp_dir)

        _write(repo, 'mod.py', 'def existing():\n    pass\n')
        base_sha = _commit(repo, 'base: existing()')

        _git(repo, 'checkout', '-q', '-b', 'feature')
        _write(repo, 'mod.py', 'def existing():\n    pass\n\n\ndef alpha():\n    pass\n')
        _commit(repo, 'feat: add alpha')

        _git(repo, 'checkout', '-q', 'main')
        _write(repo, 'mod.py', 'def existing():\n    pass\n\n\ndef gamma():\n    pass\n')
        _commit(repo, 'main: add gamma')

        _git(repo, 'checkout', '-q', 'feature')
        merge = subprocess.run(
            ['git', '-C', repo, 'merge', 'main', '-q', '-m', 'merge attempt'],
            capture_output=True, encoding='utf-8', errors='replace', timeout=30,
        )
        self.assertNotEqual(merge.returncode, 0, 'expected mod.py to conflict between the two sides')

        # Proper resolution: keep BOTH intents.
        _write(
            repo, 'mod.py',
            'def existing():\n    pass\n\n\ndef alpha():\n    pass\n\n\ndef gamma():\n    pass\n',
        )
        _git(repo, 'add', 'mod.py')
        head_sha = _commit(repo, 'merge: resolve keeping both alpha and gamma')

        rc, stdout, stderr = _run_tool(repo, '--base', base_sha, '--head', head_sha, '--json')
        self.assertEqual(rc, 0, 'stdout={}\nstderr={}'.format(stdout, stderr))
        report = json.loads(stdout)
        self.assertEqual(report['missing'], [])
        self.assertIn('alpha', report['ok'])
        self.assertIn('gamma', report['ok'])

    def test_renamed_file_keeping_symbol_passes_with_info(self):
        """A symbol that survives under a renamed/moved file must PASS, reported
        as an INFO-level 'moved' entry, never as missing."""
        repo = _init_repo(self._tmp_dir)

        _write(repo, 'a.py', 'def existing():\n    pass\n')
        base_sha = _commit(repo, 'base: existing()')

        _write(repo, 'a.py', 'def existing():\n    pass\n\n\ndef alpha():\n    pass\n')
        _commit(repo, 'feat: add alpha')

        _git(repo, 'mv', 'a.py', 'b.py')
        head_sha = _commit(repo, 'rename a.py to b.py')

        rc, stdout, stderr = _run_tool(repo, '--base', base_sha, '--head', head_sha, '--json')
        self.assertEqual(rc, 0, 'stdout={}\nstderr={}'.format(stdout, stderr))
        report = json.loads(stdout)
        self.assertEqual(report['missing'], [])
        moved_names = {entry['symbol'] for entry in report['moved']}
        self.assertIn('alpha', moved_names)
        moved_entry = next(e for e in report['moved'] if e['symbol'] == 'alpha')
        self.assertEqual(moved_entry['old_file'], 'a.py')
        self.assertEqual(moved_entry['new_file'], 'b.py')

    def test_no_symbols_added_is_a_trivial_pass(self):
        repo = _init_repo(self._tmp_dir)
        _write(repo, 'README.md', 'hello\n')
        base_sha = _commit(repo, 'base')
        _write(repo, 'README.md', 'hello world\n')
        head_sha = _commit(repo, 'docs: tweak readme')

        rc, stdout, stderr = _run_tool(repo, '--base', base_sha, '--head', head_sha, '--json')
        self.assertEqual(rc, 0, 'stdout={}\nstderr={}'.format(stdout, stderr))
        report = json.loads(stdout)
        self.assertEqual(report['symbols_checked'], 0)

    def test_bad_ref_exits_2(self):
        repo = _init_repo(self._tmp_dir)
        _write(repo, 'README.md', 'hello\n')
        _commit(repo, 'base')
        rc, stdout, stderr = _run_tool(repo, '--base', 'does-not-exist', '--head', 'HEAD')
        self.assertEqual(rc, 2)

    def test_original_head_override_diffs_as_single_unit(self):
        """--original-head diffs base..original-head as one unit rather than
        walking each non-merge commit -- still reports survival against --head."""
        repo = _init_repo(self._tmp_dir)

        _write(repo, 'mod.py', 'def existing():\n    pass\n')
        base_sha = _commit(repo, 'base: existing()')

        _write(repo, 'mod.py', 'def existing():\n    pass\n\n\ndef alpha():\n    pass\n')
        original_head_sha = _commit(repo, 'feat: add alpha')

        # A later commit that keeps alpha but also adds something else.
        _write(
            repo, 'mod.py',
            'def existing():\n    pass\n\n\ndef alpha():\n    pass\n\n\ndef beta():\n    pass\n',
        )
        head_sha = _commit(repo, 'feat: add beta too')

        rc, stdout, stderr = _run_tool(
            repo, '--base', base_sha, '--head', head_sha, '--original-head', original_head_sha, '--json'
        )
        self.assertEqual(rc, 0, 'stdout={}\nstderr={}'.format(stdout, stderr))
        report = json.loads(stdout)
        # Only alpha was added between base and original_head -- beta is not tracked.
        self.assertEqual(report['symbols_checked'], 1)
        self.assertIn('alpha', report['ok'])


if __name__ == '__main__':
    unittest.main()
