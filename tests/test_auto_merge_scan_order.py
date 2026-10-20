#!/usr/bin/env python3
"""auto_merge's secret scan must run BEFORE the fix commit is created (tracker 9b9f0e79b0a9).

Defect: `_fix_in_worktree` committed the fix first and only then scanned the committed
range, so a secret in the fix was already a commit object when the gate looked at it.
Fix: the staged INDEX is scanned with `secret_scan.py --staged` before `git commit`, and a
failing scan leaves HEAD and origin untouched.

Every git repository here is a tempfile repo; the "origin" is a local bare repo in the
same tempdir. No network, no real remote, no gh. The secret fixture is assembled at
runtime so this file never contains a literal secret.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, 'tools')
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import auto_merge  # noqa: E402

BRANCH = 'fix/scan-order-fixture'


_IDENTITY_ENV = {
    'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
    'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
}


def _git(cwd, *args):
    """Run git in cwd with utf-8 capture; return stripped stdout. Raises on failure.

    The commit identity comes from environment variables, never `git config user.*`,
    so the temp repos never mutate git configuration.
    """
    return subprocess.run(
        ['git', *args], cwd=str(cwd), capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=60, check=True,
        env={**os.environ, **_IDENTITY_ENV},
    ).stdout.strip()


def _fixture_secret():
    """An AWS-access-key-shaped value, assembled at runtime (never a literal in source)."""
    return 'AKIA' + 'Q3ZX7LM2NB4VC6HD'


class ScanBeforeCommitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='scan-order-'))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        # auto_merge's own `git commit` runs without _git's env, so it needs the identity in
        # the process environment. Without this the test depends on a host global identity:
        # present on the dev box, absent on the Windows CI shard ("commit failed").
        identity = mock.patch.dict(os.environ, _IDENTITY_ENV)
        identity.start()
        self.addCleanup(identity.stop)
        self.bare = self.tmp / 'origin.git'
        seed = self.tmp / 'seed'
        self.wt = self.tmp / 'wt'

        _git(self.tmp, 'init', '-q', '--bare', str(self.bare))
        _git(self.tmp, 'init', '-q', '-b', 'main', str(seed))
        (seed / 'config.txt').write_text('value=1\n', encoding='utf-8')
        _git(seed, 'add', 'config.txt')
        _git(seed, 'commit', '-q', '-m', 'seed')
        _git(seed, 'push', '-q', str(self.bare), 'main')
        # The fix branch already exists on origin (as for a real PR branch), so the
        # committed-range gate can diff against origin/<branch>.
        _git(seed, 'push', '-q', str(self.bare), 'main:refs/heads/' + BRANCH)
        _git(self.bare, 'symbolic-ref', 'HEAD', 'refs/heads/main')

        _git(self.tmp, 'clone', '-q', str(self.bare), str(self.wt))

    def _remote_sha(self):
        """The SHA origin's fix branch points at (the branch exists from setup)."""
        return _git(self.bare, 'rev-parse', 'refs/heads/' + BRANCH)

    def test_secret_in_fix_is_blocked_before_commit_is_created(self):
        (self.wt / 'config.txt').write_text(
            'line ' + _fixture_secret() + '\n', encoding='utf-8')
        head_before = _git(self.wt, 'rev-parse', 'HEAD')
        remote_before = self._remote_sha()

        ok, detail = auto_merge._fix_in_worktree(BRANCH, str(self.wt))

        self.assertFalse(ok, 'a secret in the fix must fail the fix')
        self.assertIn('secret', detail)
        self.assertEqual(
            _git(self.wt, 'rev-parse', 'HEAD'), head_before,
            'a commit was created before the secret scan ran')
        self.assertEqual(self._remote_sha(), remote_before, 'the blocked fix was pushed')

    def test_clean_fix_still_commits_and_pushes(self):
        (self.wt / 'config.txt').write_text('value=2\n', encoding='utf-8')
        head_before = _git(self.wt, 'rev-parse', 'HEAD')
        remote_before = self._remote_sha()

        ok, detail = auto_merge._fix_in_worktree(BRANCH, str(self.wt))

        self.assertTrue(ok, detail)
        self.assertNotEqual(_git(self.wt, 'rev-parse', 'HEAD'), head_before)
        self.assertNotEqual(self._remote_sha(), remote_before, 'clean fix was not pushed')


if __name__ == '__main__':
    unittest.main()
