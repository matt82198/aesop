"""Integrity of the committed LOOP-MANIFEST.json: every listed loop file and declared test exists.

A manifest that names files which do not exist would silently protect nothing. Paths are
checked as POSIX-relative strings so the same assertions hold on Windows and Linux.
"""

import json
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'LOOP-MANIFEST.json'

ANCHORS = ['daemons/run-watchdog.sh', 'daemons/selfheal.sh', 'daemons/backup-fleet.sh',
           'tools/loop_proof_check.py']


def _load():
    doc = json.loads(MANIFEST.read_text(encoding='utf-8'))
    return doc['files']


def _resolve(rel):
    """Return the on-disk path for a POSIX-relative manifest entry."""
    return ROOT.joinpath(*PurePosixPath(rel).parts)


class TestLoopManifestIntegrity(unittest.TestCase):

    def test_manifest_parses_and_names_the_daemons(self):
        entries = {e['path']: e for e in _load()}
        self.assertGreaterEqual(len(entries), 4)
        for anchor in ANCHORS:
            self.assertIn(anchor, entries)

    def test_every_listed_file_exists(self):
        missing = [e['path'] for e in _load() if not _resolve(e['path']).is_file()]
        self.assertEqual(missing, [])

    def test_every_declared_test_exists(self):
        missing = [(e['path'], t) for e in _load() for t in e['tests']
                   if not _resolve(t).is_file()]
        self.assertEqual(missing, [])

    def test_paths_are_safe_relative_posix(self):
        bad = []
        for e in _load():
            for p in [e['path'], *e['tests']]:
                pp = PurePosixPath(p)
                if '\\' in p or pp.is_absolute() or '..' in pp.parts or not p:
                    bad.append(p)
        self.assertEqual(bad, [])

    def test_every_entry_has_a_one_line_reason(self):
        for e in _load():
            self.assertTrue(e['reason'].strip(), e['path'])
            self.assertNotIn('\n', e['reason'], e['path'])


if __name__ == '__main__':
    unittest.main()
