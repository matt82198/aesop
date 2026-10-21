"""Loop-Proof gate: a change to a listed loop file needs a red-then-green trailer.

Drives tools/loop_proof_check.py as a subprocess against throwaway git repositories, so the
assertions are on the observable exit code and message, not on source text.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / 'tools' / 'loop_proof_check.py'
LOOP_FILE = 'daemons/loop-fixture.sh'
TEST_FILE = 'tests/test_loop_fixture.py'
MANIFEST = (
    '{"version": 1, "files": [{"path": "daemons/loop-fixture.sh", '
    '"reason": "fixture loop", "tests": ["tests/test_loop_fixture.py"]}]}\n'
)


def _git(repo, *args):
    proc = subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *args],
        cwd=repo, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        raise AssertionError(f'git {args} failed: {proc.stderr}')
    return proc.stdout.strip()


def _write(repo, rel, text):
    target = repo.joinpath(*rel.split('/'))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding='utf-8')


def _commit(repo, subject, trailer=None):
    msg = subject if trailer is None else f'{subject}\n\n{trailer}'
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', msg)
    return _git(repo, 'rev-parse', 'HEAD')


def _check(repo, base, head):
    proc = subprocess.run(
        [sys.executable, '-I', str(CHECKER), '--repo', str(repo), '--base', base, '--head', head],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    return proc.returncode, proc.stdout + proc.stderr


class TestLoopProofGate(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / 'repo'
        self.repo.mkdir()
        _git(self.repo, 'init', '-q', '-b', 'main')
        _write(self.repo, 'LOOP-MANIFEST.json', MANIFEST)
        _write(self.repo, LOOP_FILE, 'echo v1\n')
        _write(self.repo, TEST_FILE, 'def test_fixture():\n    assert True\n')
        self.base = _commit(self.repo, 'base')

    def tearDown(self):
        self._tmp.cleanup()

    def _change_loop_file(self, body='echo v2\n'):
        _write(self.repo, LOOP_FILE, body)

    def test_loop_change_without_trailer_is_refused(self):
        self._change_loop_file()
        head = _commit(self.repo, 'change loop with no proof')
        rc, out = _check(self.repo, self.base, head)
        self.assertNotEqual(rc, 0, out)
        self.assertIn('Loop-Proof', out)

    def test_loop_change_with_correct_trailer_passes(self):
        self._change_loop_file()
        head = _commit(self.repo, 'change loop with proof',
                       'Loop-Proof: tests/test_loop_fixture.py red=1/2 green=2/2')
        rc, out = _check(self.repo, self.base, head)
        self.assertEqual(rc, 0, out)

    def test_trailer_naming_an_undeclared_test_is_refused(self):
        self._change_loop_file()
        head = _commit(self.repo, 'wrong test named',
                       'Loop-Proof: tests/test_other.py red=1/2 green=2/2')
        rc, out = _check(self.repo, self.base, head)
        self.assertNotEqual(rc, 0, out)

    def test_trailer_that_is_not_fully_green_is_refused(self):
        self._change_loop_file()
        head = _commit(self.repo, 'still red',
                       'Loop-Proof: tests/test_loop_fixture.py red=1/2 green=1/2')
        rc, out = _check(self.repo, self.base, head)
        self.assertNotEqual(rc, 0, out)

    def test_trailer_without_red_before_is_refused(self):
        self._change_loop_file()
        head = _commit(self.repo, 'no red before',
                       'Loop-Proof: tests/test_loop_fixture.py red=0/2 green=2/2')
        rc, out = _check(self.repo, self.base, head)
        self.assertNotEqual(rc, 0, out)

    def test_change_outside_the_manifest_needs_no_trailer(self):
        _write(self.repo, 'docs/notes.md', 'hello\n')
        head = _commit(self.repo, 'docs only')
        rc, out = _check(self.repo, self.base, head)
        self.assertEqual(rc, 0, out)

    def test_listed_loop_file_with_no_declared_test_is_refused(self):
        _write(self.repo, 'LOOP-MANIFEST.json',
               '{"version": 1, "files": [{"path": "daemons/loop-fixture.sh", '
               '"reason": "fixture loop", "tests": []}]}\n')
        _commit(self.repo, 'manifest without tests')
        base = _git(self.repo, 'rev-parse', 'HEAD')
        self._change_loop_file()
        head = _commit(self.repo, 'change loop, no test declared',
                       'Loop-Proof: tests/test_loop_fixture.py red=1/2 green=2/2')
        rc, out = _check(self.repo, base, head)
        self.assertNotEqual(rc, 0, out)

    def test_manifest_introduced_in_range_is_enforced(self):
        _git(self.repo, 'rm', '-q', 'LOOP-MANIFEST.json')
        bare = _commit(self.repo, 'no manifest yet')
        _write(self.repo, 'LOOP-MANIFEST.json', MANIFEST)
        self._change_loop_file()
        head = _commit(self.repo, 'introduce manifest and change loop')
        rc, out = _check(self.repo, bare, head)
        self.assertNotEqual(rc, 0, out)

    def test_manifest_introduced_in_range_passes_with_trailer(self):
        _git(self.repo, 'rm', '-q', 'LOOP-MANIFEST.json')
        bare = _commit(self.repo, 'no manifest yet')
        _write(self.repo, 'LOOP-MANIFEST.json', MANIFEST)
        self._change_loop_file()
        head = _commit(self.repo, 'introduce manifest and change loop',
                       'Loop-Proof: tests/test_loop_fixture.py red=1/2 green=2/2')
        rc, out = _check(self.repo, bare, head)
        self.assertEqual(rc, 0, out)

    def test_missing_manifest_fails_closed(self):
        _git(self.repo, 'rm', '-q', 'LOOP-MANIFEST.json')
        self._change_loop_file()
        head = _commit(self.repo, 'manifest deleted')
        rc, out = _check(self.repo, self.base, head)
        self.assertEqual(rc, 2, out)


if __name__ == '__main__':
    unittest.main()
