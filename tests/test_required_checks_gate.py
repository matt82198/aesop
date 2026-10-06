#!/usr/bin/env python3
"""
Unit tests for tools/required_checks_gate.py

The gap under test is the REVERSE of ci_gate_runability.py. That tool asks
"can this required check actually run?"; this one asks "is this gating job
actually required?". A ci.yml job intended to gate merges but absent from
branch protection's required set never blocks anything -- the job goes red and
the PR merges anyway. Neither the workflow file nor the runability gate can see
that, because the required set lives only in the GitHub API.

tools/required-checks.json is the declared intent; the gate diffs it against
live protection and fails on drift in EITHER direction:
  - MISSING:    declared as gating, absent from protection (never blocks)
  - UNDECLARED: enforced by protection, absent from intent (unreviewed gate)

Tests cover:
- Exact match -> exit 0
- Declared-but-unprotected context -> exit 1 (MISSING)
- Protected-but-undeclared context -> exit 1 (UNDECLARED)
- Drift in both directions at once
- `checks[]` fallback when the deprecated `contexts[]` key is absent
- Protection payload with no required_status_checks at all
- GitHub API error payload -> exit 2
- Malformed / unreadable protection JSON -> exit 2
- Missing or malformed intent file -> exit 2
- --json output shape
- REAL RUN: committed intent vs live branch protection
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / 'tools' / 'required_checks_gate.py'
INTENT = REPO_ROOT / 'tools' / 'required-checks.json'


def _protection(contexts=None, checks=None, include_rsc=True):
    """Build a branch-protection payload shaped like the GitHub API's."""
    payload = {'url': 'https://api.github.com/repos/o/r/branches/main/protection'}
    if include_rsc:
        rsc = {'strict': False}
        if contexts is not None:
            rsc['contexts'] = contexts
        if checks is not None:
            rsc['checks'] = [{'context': c, 'app_id': 15368} for c in checks]
        payload['required_status_checks'] = rsc
    return payload


class TestRequiredChecksGate(unittest.TestCase):
    """Test suite for the required-checks drift gate."""

    def run_gate(self, intent, protection, extra_args=None):
        """
        Run the gate with a fixture intent dict and fixture protection payload.
        Returns (rc, stdout, stderr).
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            intent_path = tmpdir / 'required-checks.json'
            if isinstance(intent, str):
                intent_path.write_text(intent, encoding='utf-8')
            elif intent is not None:
                intent_path.write_text(json.dumps(intent), encoding='utf-8')

            prot_path = tmpdir / 'protection.json'
            if isinstance(protection, str):
                prot_path.write_text(protection, encoding='utf-8')
            elif protection is not None:
                prot_path.write_text(json.dumps(protection), encoding='utf-8')

            cmd = [
                sys.executable, str(TOOL),
                '--intent', str(intent_path),
                '--protection-file', str(prot_path),
            ]
            if extra_args:
                cmd.extend(extra_args)
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding='utf-8', cwd=str(tmpdir)
            )
            return result.returncode, result.stdout, result.stderr

    # ---- clean ----------------------------------------------------------

    def test_exact_match_is_clean(self):
        intent = {'repo': 'o/r', 'branch': 'main',
                  'required_contexts': ['ci (0)', 'ci (1)', 'windows']}
        prot = _protection(contexts=['ci (0)', 'ci (1)', 'windows'])
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 0, f'exact match must be clean: {out}{err}')

    def test_order_does_not_matter(self):
        intent = {'repo': 'o/r', 'branch': 'main',
                  'required_contexts': ['windows', 'ci (0)']}
        prot = _protection(contexts=['ci (0)', 'windows'])
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 0, 'the required set is a set, not a sequence')

    def test_checks_array_fallback(self):
        """GitHub is deprecating contexts[]; checks[].context must be honored."""
        intent = {'repo': 'o/r', 'branch': 'main',
                  'required_contexts': ['ci (0)', 'windows']}
        prot = _protection(contexts=None, checks=['ci (0)', 'windows'])
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 0, f'checks[] fallback failed: {out}{err}')

    # ---- drift: declared but not enforced -------------------------------

    def test_declared_context_absent_from_protection(self):
        """The core escape: a gating job that never actually blocks a merge."""
        intent = {'repo': 'o/r', 'branch': 'main',
                  'required_contexts': ['ci (0)', 'ci (1)', 'windows']}
        prot = _protection(contexts=['ci (0)', 'ci (1)'])
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 1, f'unprotected gating job must exit 1, got {rc}')
        combined = out + err
        self.assertIn('windows', combined)
        self.assertIn('MISSING', combined.upper())

    # ---- drift: enforced but not declared -------------------------------

    def test_protected_context_absent_from_intent(self):
        intent = {'repo': 'o/r', 'branch': 'main',
                  'required_contexts': ['ci (0)']}
        prot = _protection(contexts=['ci (0)', 'surprise-gate'])
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 1, f'undeclared enforced gate must exit 1, got {rc}')
        combined = out + err
        self.assertIn('surprise-gate', combined)
        self.assertIn('UNDECLARED', combined.upper())

    def test_drift_in_both_directions(self):
        intent = {'repo': 'o/r', 'branch': 'main',
                  'required_contexts': ['ci (0)', 'gone']}
        prot = _protection(contexts=['ci (0)', 'extra'])
        rc, out, err = self.run_gate(intent, prot, ['--json'])
        self.assertEqual(rc, 1)
        data = json.loads(out)
        kinds = {f['kind'] for f in data['findings']}
        self.assertEqual(kinds, {'MISSING', 'UNDECLARED'})
        by_kind = {f['kind']: f['context'] for f in data['findings']}
        self.assertEqual(by_kind['MISSING'], 'gone')
        self.assertEqual(by_kind['UNDECLARED'], 'extra')

    def test_protection_without_required_status_checks(self):
        """Status checks switched off entirely: every declared gate is inert."""
        intent = {'repo': 'o/r', 'branch': 'main', 'required_contexts': ['ci (0)']}
        prot = _protection(include_rsc=False)
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 1, 'no required_status_checks means nothing gates')
        self.assertIn('MISSING', (out + err).upper())

    # ---- errors ---------------------------------------------------------

    def test_github_error_payload_is_error(self):
        """A `message`-only payload (404 'Branch not protected', 403, rate limit)."""
        intent = {'repo': 'o/r', 'branch': 'main', 'required_contexts': ['ci (0)']}
        prot = {'message': 'Branch not protected',
                'documentation_url': 'https://docs.github.com'}
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 2, f'API error payload must exit 2, got {rc}')

    def test_malformed_protection_json_is_error(self):
        intent = {'repo': 'o/r', 'branch': 'main', 'required_contexts': ['ci (0)']}
        rc, out, err = self.run_gate(intent, '{not json at all')
        self.assertEqual(rc, 2, f'malformed protection JSON must exit 2, got {rc}')

    def test_missing_intent_file_is_error(self):
        prot = _protection(contexts=['ci (0)'])
        rc, out, err = self.run_gate(None, prot)
        self.assertEqual(rc, 2, f'missing intent must exit 2, got {rc}')

    def test_malformed_intent_file_is_error(self):
        prot = _protection(contexts=['ci (0)'])
        rc, out, err = self.run_gate('{oops', prot)
        self.assertEqual(rc, 2, f'malformed intent must exit 2, got {rc}')

    def test_empty_intent_context_list_is_error(self):
        """An empty declared set would make the gate vacuously green."""
        intent = {'repo': 'o/r', 'branch': 'main', 'required_contexts': []}
        prot = _protection(contexts=['ci (0)'])
        rc, out, err = self.run_gate(intent, prot)
        self.assertEqual(rc, 2, 'an empty intent list is a broken manifest, not a pass')

    def test_missing_protection_file_is_error(self):
        intent = {'repo': 'o/r', 'branch': 'main', 'required_contexts': ['ci (0)']}
        with tempfile.TemporaryDirectory() as tmp:
            intent_path = Path(tmp) / 'i.json'
            intent_path.write_text(json.dumps(intent), encoding='utf-8')
            cmd = [sys.executable, str(TOOL), '--intent', str(intent_path),
                   '--protection-file', str(Path(tmp) / 'nope.json')]
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding='utf-8', cwd=tmp
            )
            self.assertEqual(result.returncode, 2)

    # ---- json shape -----------------------------------------------------

    def test_json_clean_shape(self):
        intent = {'repo': 'o/r', 'branch': 'main', 'required_contexts': ['ci (0)']}
        prot = _protection(contexts=['ci (0)'])
        rc, out, err = self.run_gate(intent, prot, ['--json'])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data['status'], 'clean')
        self.assertEqual(data['exit_code'], 0)
        self.assertEqual(data['findings'], [])
        self.assertEqual(sorted(data['declared']), ['ci (0)'])
        self.assertEqual(sorted(data['enforced']), ['ci (0)'])

    # ---- committed intent -----------------------------------------------

    def test_committed_intent_file_is_well_formed(self):
        self.assertTrue(INTENT.exists(), 'tools/required-checks.json must be committed')
        data = json.loads(INTENT.read_text(encoding='utf-8'))
        self.assertEqual(data['repo'], 'matt82198/aesop')
        self.assertEqual(data['branch'], 'main')
        self.assertTrue(data['required_contexts'], 'declared set must be non-empty')
        for ctx in data['required_contexts']:
            self.assertIsInstance(ctx, str)
            self.assertTrue(ctx.strip())

    def test_committed_intent_matches_recorded_snapshot(self):
        """
        Hermetic guard on the committed manifest: the declared set must equal
        the protection snapshot captured from the live API when this gate was
        built. The network-backed comparison runs as a pre-push/manual REAL RUN
        (`python tools/required_checks_gate.py`); CI stays offline.
        """
        data = json.loads(INTENT.read_text(encoding='utf-8'))
        snapshot = data.get('snapshot', {}).get('contexts')
        self.assertIsNotNone(snapshot, 'intent must record the protection snapshot')
        self.assertEqual(sorted(data['required_contexts']), sorted(snapshot))

    # ---- real run (network; skipped when gh is unavailable) -------------

    @unittest.skipIf(os.environ.get('AESOP_SKIP_NETWORK_TESTS') == '1',
                     'network tests disabled')
    def test_live_protection_matches_intent(self):
        """
        REAL RUN against live branch protection. Skips (never fails) when gh is
        absent or unauthenticated -- a missing transport is a SKIP, not a hunt
        and not a red gate.
        """
        result = subprocess.run(
            [sys.executable, str(TOOL), '--json'],
            capture_output=True, text=True, encoding='utf-8', cwd=str(REPO_ROOT)
        )
        if result.returncode == 2:
            self.skipTest(f'gh unavailable/unauthenticated: {result.stderr.strip()[:200]}')
        self.assertEqual(
            result.returncode, 0,
            f'live required-checks drift:\n{result.stdout}\n{result.stderr}'
        )


if __name__ == '__main__':
    unittest.main()
