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
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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


if __name__ == '__main__':
    unittest.main()
