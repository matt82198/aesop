#!/usr/bin/env python3
"""auto_merge.py must READ the generated-path registry, never re-type it.

Two guards:

1. Behavioural -- `fix_branch()` resolves conflicts over whatever
   `tools/generated_paths.py` currently contains. Proven by injecting a
   sentinel entry into the registry at runtime and asserting auto_merge
   acts on it. A hard-coded copy cannot see the sentinel, so this test
   fails on the duplicated list and passes only on a live read.

2. Structural (the durable guard) -- an AST source scan over `tools/`
   that fails if ANY module re-types the registry as a literal
   collection. The same class of duplication otherwise returns the next
   time someone needs the list.
"""
import ast
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, 'tools')
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import auto_merge  # noqa: E402
import generated_paths  # noqa: E402

# Modules allowed to hold the registry as a literal: the registry itself.
_REGISTRY_MODULE = 'generated_paths.py'
# Minimum registry members in one literal collection before it counts as a
# re-typed copy. One incidental mention (a tool naming its own output) is
# not a copy; two or more, with nothing else in the collection, is.
_COPY_THRESHOLD = 2


def _literal_strings(node):
    """The string constants of a literal list/tuple/set, or None."""
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.append(element.value)
    return values


def find_registry_copies(directory, registry):
    """Literal collections under `directory` that re-type `registry`.

    A copy is a literal list/tuple/set whose elements are ALL registry
    members and which holds at least `_COPY_THRESHOLD` of them. A broader
    list that merely overlaps the registry (e.g. a conflict-prone-files
    roster with extra entries) carries its own meaning and is not a copy.
    """
    members = set(registry)
    findings = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith('.py') or name == _REGISTRY_MODULE:
            continue
        path = os.path.join(directory, name)
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            values = _literal_strings(node)
            if not values:
                continue
            hits = [v for v in values if v in members]
            if len(hits) >= _COPY_THRESHOLD and len(hits) == len(values):
                findings.append((name, node.lineno, sorted(hits)))
    return findings


class TestAutoMergeReadsRegistry(unittest.TestCase):
    """auto_merge sees registry entries it was never written with."""

    def setUp(self):
        self._real_registry = generated_paths.GENERATED_PATHS

    def tearDown(self):
        generated_paths.GENERATED_PATHS = self._real_registry

    def _fix_branch_with_registry(self, registry):
        """Run fix_branch to the conflict-resolution step; return git calls."""
        generated_paths.GENERATED_PATHS = registry
        calls = []

        class FakeResult(object):
            def __init__(self, returncode):
                self.returncode = returncode
                self.stdout = ''
                self.stderr = ''

        def fake_git(args, **kwargs):
            calls.append(list(args))
            # Force the merge to conflict so the generated-path resolution
            # branch runs, then fail --continue so fix_branch returns without
            # touching the real repo.
            if args[:2] == ['merge', 'origin/main']:
                return FakeResult(1)
            if 'merge' in args and '--continue' in args:
                return FakeResult(1)
            return FakeResult(0)

        original_git = auto_merge.git
        auto_merge.git = fake_git
        try:
            ok, detail = auto_merge.fix_branch('feature/example')
        finally:
            auto_merge.git = original_git
        self.assertFalse(ok)
        self.assertEqual(detail, 'merge conflict unresolvable')
        return calls

    def test_registry_is_read_not_copied(self):
        """A sentinel added to the registry must reach auto_merge."""
        sentinel = 'docs/SENTINEL-NOT-IN-ANY-HARDCODED-LIST.md'
        calls = self._fix_branch_with_registry(
            tuple(self._real_registry) + (sentinel,))
        self.assertIn(['checkout', '--theirs', sentinel], calls)
        self.assertIn(['add', sentinel], calls)

    def test_real_registry_entries_are_resolved(self):
        """Every real registry entry is resolved during a conflict."""
        calls = self._fix_branch_with_registry(self._real_registry)
        for path in self._real_registry:
            self.assertIn(['checkout', '--theirs', path], calls)
            self.assertIn(['add', path], calls)

    def test_no_registry_entries_outside_the_registry(self):
        """Shrinking the registry shrinks what auto_merge resolves."""
        calls = self._fix_branch_with_registry(())
        resolved = [c for c in calls if c[:2] == ['checkout', '--theirs']]
        self.assertEqual(resolved, [])


class TestNoHardCodedRegistryCopies(unittest.TestCase):
    """Durable guard: no module in tools/ may re-type the registry."""

    def test_tools_have_no_registry_copies(self):
        findings = find_registry_copies(
            TOOLS_DIR, generated_paths.generated_paths())
        self.assertEqual(
            findings, [],
            'hard-coded copies of the generated-path registry found '
            '(import tools/generated_paths.py instead): '
            + '; '.join('%s:%d %s' % f for f in findings))

    def test_guard_detects_a_planted_copy(self):
        """Falsifiability: the scan must flag a copy when one exists."""
        scratch = os.path.join(
            REPO_ROOT, 'state', 'test-registry-scan-%d' % os.getpid())
        os.makedirs(scratch, exist_ok=True)
        planted = os.path.join(scratch, 'planted.py')
        registry = generated_paths.generated_paths()
        try:
            with open(planted, 'w', encoding='utf-8') as handle:
                handle.write('COPY = [\n')
                for path in registry:
                    handle.write('    %r,\n' % (path,))
                handle.write(']\n')
            findings = find_registry_copies(scratch, registry)
            self.assertEqual(len(findings), 1, findings)
            self.assertEqual(findings[0][0], 'planted.py')
        finally:
            if os.path.exists(planted):
                os.remove(planted)
            if os.path.isdir(scratch):
                os.rmdir(scratch)

    def test_guard_ignores_a_broader_overlapping_list(self):
        """A list with non-registry members is not a re-typed registry."""
        scratch = os.path.join(
            REPO_ROOT, 'state', 'test-registry-scan-wide-%d' % os.getpid())
        os.makedirs(scratch, exist_ok=True)
        wider = os.path.join(scratch, 'wider.py')
        registry = generated_paths.generated_paths()
        try:
            with open(wider, 'w', encoding='utf-8') as handle:
                handle.write('CONTENDED = [\n')
                for path in registry:
                    handle.write('    %r,\n' % (path,))
                handle.write('    "README.md",\n]\n')
            self.assertEqual(find_registry_copies(scratch, registry), [])
        finally:
            if os.path.exists(wider):
                os.remove(wider)
            if os.path.isdir(scratch):
                os.rmdir(scratch)


if __name__ == '__main__':
    unittest.main()
