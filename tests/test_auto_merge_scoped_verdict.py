#!/usr/bin/env python3
"""Scoped verdict of tools/auto_merge.py must be decided by GitHub MERGED state.

Regression for F-03 (2026-10-14 reliability refutation): `auto_merge.py <n>` exited 0 and
printed "All targeted PRs merged!" for a nonexistent PR and for a closed-unmerged PR. The
fix verifies every named PR with `gh pr view <n> --json state,mergedAt` and fails closed.

All GitHub access is stubbed: no test reaches a real remote or a live PR.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import auto_merge  # noqa: E402


def _ok(stdout=''):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr='')


def _fail(stderr='could not resolve to a PullRequest'):
    return SimpleNamespace(returncode=1, stdout='', stderr=stderr)


class FakeGitHub:
    """Stub for subprocess_common.gh: answers `pr view <n>` from a state table."""

    def __init__(self, pr_states, raw=None):
        # pr_states: {number: (state, mergedAt)}; a missing number models a nonexistent PR.
        self.pr_states = pr_states
        self.raw = raw or {}
        self.view_calls = []

    def __call__(self, args, check=False, **kwargs):
        if args[:2] == ['pr', 'view']:
            number = int(args[2])
            self.view_calls.append(number)
            if '--json' in args and 'state,mergedAt' in args:
                if number in self.raw:
                    return _ok(self.raw[number])
                if number not in self.pr_states:
                    return _fail()
                state, merged_at = self.pr_states[number]
                return _ok(json.dumps({'state': state, 'mergedAt': merged_at}))
            # mergeability / checks probes for open PRs: report pending, never merge.
            return _ok('UNKNOWN') if 'mergeable' in args else _fail('no checks')
        if args[:2] == ['pr', 'checks']:
            return _fail('no checks')
        raise AssertionError('unexpected gh call %r' % (args,))


class ScopedVerdictTests(unittest.TestCase):

    def _run(self, argv, open_prs, gh_stub):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(auto_merge, 'get_open_prs', return_value=open_prs), \
                mock.patch.object(auto_merge, 'gh', side_effect=gh_stub), \
                mock.patch.object(auto_merge, 'merge_pr',
                                  side_effect=AssertionError('verdict test must not merge')), \
                mock.patch.object(sys, 'argv', ['auto_merge.py'] + argv), \
                redirect_stdout(out), redirect_stderr(err):
            rc = auto_merge.main()
        return rc, out.getvalue(), err.getvalue()

    def test_nonexistent_pr_exits_nonzero_and_names_it(self):
        rc, out, err = self._run(['999999', '--no-fix'], [], FakeGitHub({}))
        self.assertEqual(rc, 1)
        self.assertIn('#999999', err)
        self.assertNotIn('All targeted PRs merged!', out)

    def test_closed_unmerged_pr_exits_nonzero_and_names_it(self):
        gh = FakeGitHub({921: ('CLOSED', None)})
        rc, out, err = self._run(['921', '--no-fix'], [], gh)
        self.assertEqual(rc, 1)
        self.assertIn('#921', err)
        self.assertIn('CLOSED', err)
        self.assertNotIn('All targeted PRs merged!', out)

    def test_merged_pr_exits_zero_with_verified_message(self):
        gh = FakeGitHub({921: ('MERGED', '2026-10-14T12:00:00Z')})
        rc, out, err = self._run(['921', '--no-fix'], [], gh)
        self.assertEqual(rc, 0)
        self.assertIn('All targeted PRs merged!', out)
        self.assertEqual(gh.view_calls, [921])

    def test_one_unmerged_among_merged_fails_and_names_only_the_unmerged(self):
        gh = FakeGitHub({921: ('MERGED', '2026-10-14T12:00:00Z'),
                         922: ('OPEN', None)})
        rc, out, err = self._run(['921', '922', '--no-fix'],
                                 [{'number': 922, 'title': 'x', 'headRefName': 'b'}], gh)
        self.assertEqual(rc, 1)
        self.assertIn('#922', err)
        self.assertNotIn('#921', err)
        self.assertNotIn('All targeted PRs merged!', out)

    def test_merged_state_without_mergedAt_is_not_verified(self):
        gh = FakeGitHub({921: ('MERGED', None)})
        rc, _, err = self._run(['921', '--no-fix'], [], gh)
        self.assertEqual(rc, 1)
        self.assertIn('#921', err)

    def test_unparseable_gh_output_fails_closed(self):
        gh = FakeGitHub({}, raw={921: 'CLOSED\n'})
        rc, _, err = self._run(['921', '--no-fix'], [], gh)
        self.assertEqual(rc, 1)
        self.assertIn('#921', err)

    def test_json_mode_still_fails_closed_without_printing_success(self):
        gh = FakeGitHub({921: ('CLOSED', None)})
        rc, out, _ = self._run(['921', '--no-fix', '--json'], [], gh)
        self.assertEqual(rc, 1)
        self.assertNotIn('All targeted PRs merged!', out)

    def test_all_mode_exit_semantics_unchanged(self):
        rc, out, _ = self._run(['--all', '--no-fix'], [], FakeGitHub({}))
        self.assertEqual(rc, 0)
        self.assertIn('No open PRs.', out)


class _BoardGitHub:
    """Stub for subprocess_common.gh that models an open board and records every call.

    A green PR is armed for merge: `pr merge` marks it MERGED, so a test can observe
    whether a merge actor acted by reading the recorded calls.
    """

    def __init__(self, open_prs, mergeable='MERGEABLE'):
        self.open_prs = open_prs
        self.mergeable = mergeable
        self.calls = []
        self.merged = set()

    def __call__(self, args, check=False, **kwargs):
        self.calls.append(list(args))
        if args[:2] == ['pr', 'list']:
            return _ok(json.dumps(self.open_prs))
        if args[:2] == ['pr', 'checks']:
            return _ok(json.dumps([{'name': 'ci', 'bucket': 'pass', 'state': 'SUCCESS'}]))
        if args[:2] == ['pr', 'merge']:
            self.merged.add(int(args[2]))
            return _ok()
        if args[:2] == ['pr', 'view']:
            number = int(args[2])
            if 'mergeable' in args:
                return _ok(self.mergeable)
            if 'state,mergedAt' in args:
                if number in self.merged:
                    return _ok(json.dumps({'state': 'MERGED', 'mergedAt': '2026-10-15T00:00:00Z'}))
                return _ok(json.dumps({'state': 'OPEN', 'mergedAt': None}))
            if 'state' in args:
                return _ok('MERGED' if number in self.merged else 'OPEN')
        raise AssertionError('unexpected gh call %r' % (args,))


class HaltGateTests(unittest.TestCase):
    """auto_merge.py must honour the box-wide kill switch (tools/halt.py) before any action.

    merge_queue.py and merge_train.py consult halt.active_halt() (box-wide, then
    checkout-scoped) and fail closed on any reader error. auto_merge.py must do the same
    before it arms, merges, or writes to origin, on both the board-wide and scoped paths.

    All halt roots are pinned at a temp tree; the live conductor3 root is never read.
    """

    OPEN_GREEN_921 = [{'number': 921, 'title': 'green', 'headRefName': 'feature/x'}]

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='aesop-automerge-halt-')
        root = Path(self._tmp)
        self.conductor = root / 'conductor3'
        self.checkout = root / 'checkout'
        self.checkout.mkdir()
        self.box_sentinel = self.conductor / 'state' / '.HALT'
        self.local_sentinel = self.checkout / 'state' / '.HALT'
        self._env = mock.patch.dict(os.environ, {
            'CONDUCTOR_ROOT': str(self.conductor),
            'AESOP_ROOT': str(self.checkout),
        })
        self._env.start()
        # An explicit AESOP_STATE_ROOT disables the box-wide reader by design; it must be absent.
        os.environ.pop('AESOP_STATE_ROOT', None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def _run(self, argv, gh, git_calls):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(auto_merge, 'gh', side_effect=gh), \
                mock.patch.object(auto_merge, 'git',
                                  side_effect=lambda args, **kw: (git_calls.append(list(args)), _ok())[1]), \
                mock.patch.object(sys, 'argv', ['auto_merge.py'] + argv), \
                redirect_stdout(out), redirect_stderr(err):
            rc = auto_merge.main()
        return rc, out.getvalue(), err.getvalue()

    def _merge_calls(self, gh):
        return [c for c in gh.calls if c[:2] == ['pr', 'merge']]

    def test_box_wide_sentinel_blocks_merge_and_exits_nonzero(self):
        self._write(self.box_sentinel, json.dumps({'reason': 'operator halt', 'timestamp': 'T'}))
        gh, git_calls = _BoardGitHub(self.OPEN_GREEN_921), []
        rc, out, err = self._run(['921', '--no-fix'], gh, git_calls)
        self.assertNotEqual(rc, 0)
        self.assertEqual(self._merge_calls(gh), [])
        self.assertEqual(gh.calls, [], 'a halted box must make no GitHub call at all')
        self.assertEqual(git_calls, [])
        self.assertIn('operator halt', err)
        self.assertNotIn('All targeted PRs merged!', out)

    def test_checkout_sentinel_blocks_merge_and_exits_nonzero(self):
        self._write(self.local_sentinel, json.dumps({'reason': 'checkout halt', 'timestamp': 'T'}))
        gh, git_calls = _BoardGitHub(self.OPEN_GREEN_921), []
        rc, _, err = self._run(['921', '--no-fix'], gh, git_calls)
        self.assertNotEqual(rc, 0)
        self.assertEqual(self._merge_calls(gh), [])
        self.assertIn('checkout halt', err)

    def test_board_wide_all_mode_halted_makes_no_merge(self):
        self._write(self.box_sentinel, json.dumps({'reason': 'operator halt', 'timestamp': 'T'}))
        gh, git_calls = _BoardGitHub(self.OPEN_GREEN_921), []
        rc, _, _ = self._run(['--all', '--no-fix'], gh, git_calls)
        self.assertNotEqual(rc, 0)
        self.assertEqual(self._merge_calls(gh), [])
        self.assertEqual(git_calls, [])

    def test_halted_fix_path_never_writes_origin(self):
        self._write(self.box_sentinel, json.dumps({'reason': 'operator halt', 'timestamp': 'T'}))
        gh = _BoardGitHub(self.OPEN_GREEN_921, mergeable='CONFLICTING')
        git_calls = []
        with mock.patch.object(auto_merge, 'fix_branch',
                               side_effect=AssertionError('halted box must not fix/push')):
            rc, _, _ = self._run(['921'], gh, git_calls)
        self.assertNotEqual(rc, 0)
        self.assertEqual(git_calls, [])
        self.assertEqual(self._merge_calls(gh), [])

    def test_unreadable_sentinel_fails_closed(self):
        self._write(self.box_sentinel, '{not json')
        gh, git_calls = _BoardGitHub(self.OPEN_GREEN_921), []
        rc, _, _ = self._run(['921', '--no-fix'], gh, git_calls)
        self.assertNotEqual(rc, 0)
        self.assertEqual(self._merge_calls(gh), [])

    def test_halt_reader_error_fails_closed(self):
        gh, git_calls = _BoardGitHub(self.OPEN_GREEN_921), []
        with mock.patch('halt.active_halt', side_effect=OSError('sentinel unreadable')):
            rc, _, err = self._run(['921', '--no-fix'], gh, git_calls)
        self.assertNotEqual(rc, 0)
        self.assertEqual(self._merge_calls(gh), [])
        self.assertIn('sentinel unreadable', err)

    def test_absent_sentinel_merges_exactly_as_before(self):
        gh, git_calls = _BoardGitHub(self.OPEN_GREEN_921), []
        rc, out, _ = self._run(['921', '--no-fix'], gh, git_calls)
        self.assertEqual(rc, 0)
        self.assertEqual(self._merge_calls(gh), [['pr', 'merge', '921', '--merge']])
        self.assertIn('All targeted PRs merged!', out)


if __name__ == '__main__':
    unittest.main()
