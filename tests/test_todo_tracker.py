"""Tests for tools/todo_tracker.py -- TODO/FIXME/HACK/XXX comment tracker."""

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
import todo_tracker


class TestScanFile(unittest.TestCase):
    """Tests for single-file scanning."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = self.tmpdir.name

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent(content))
        return path

    def test_python_todo(self):
        """Detects # TODO in Python files."""
        path = self._write('example.py', '''\
            x = 1
            # TODO: fix this later
            y = 2
        ''')
        pattern = todo_tracker._build_pattern(['TODO'])
        findings = todo_tracker.scan_file(path, pattern)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['tag'], 'TODO')
        self.assertEqual(findings[0]['line'], 2)
        self.assertIn('fix this later', findings[0]['text'])

    def test_js_fixme(self):
        """Detects // FIXME in JavaScript files."""
        path = self._write('app.js', '''\
            const x = 1;
            // FIXME: broken logic
            const y = 2;
        ''')
        pattern = todo_tracker._build_pattern(['FIXME'])
        findings = todo_tracker.scan_file(path, pattern)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['tag'], 'FIXME')
        self.assertIn('broken logic', findings[0]['text'])

    def test_multiline_block_comment(self):
        """Detects tags inside block comments (* HACK)."""
        path = self._write('block.js', '''\
            /*
             * HACK: workaround for upstream bug
             */
        ''')
        pattern = todo_tracker._build_pattern(['HACK'])
        findings = todo_tracker.scan_file(path, pattern)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['tag'], 'HACK')

    def test_case_insensitive(self):
        """Tags are matched case-insensitively and normalized to upper."""
        path = self._write('case.py', '''\
            # todo: lowercase tag
            # Todo: mixed case
        ''')
        pattern = todo_tracker._build_pattern(['TODO'])
        findings = todo_tracker.scan_file(path, pattern)
        self.assertEqual(len(findings), 2)
        for f in findings:
            self.assertEqual(f['tag'], 'TODO')

    def test_no_findings(self):
        """File with no tags returns empty list."""
        path = self._write('clean.py', '''\
            x = 1
            # This is a regular comment
            y = 2
        ''')
        pattern = todo_tracker._build_pattern(todo_tracker.DEFAULT_TAGS)
        findings = todo_tracker.scan_file(path, pattern)
        self.assertEqual(findings, [])

    def test_multiple_tags_same_file(self):
        """Multiple different tags in one file are all captured."""
        path = self._write('multi.py', '''\
            # TODO: first
            # FIXME: second
            # HACK: third
            # XXX: fourth
        ''')
        pattern = todo_tracker._build_pattern(todo_tracker.DEFAULT_TAGS)
        findings = todo_tracker.scan_file(path, pattern)
        self.assertEqual(len(findings), 4)
        tags = {f['tag'] for f in findings}
        self.assertEqual(tags, {'TODO', 'FIXME', 'HACK', 'XXX'})


class TestScan(unittest.TestCase):
    """Tests for the full scan() function."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = self.tmpdir.name

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent(content))
        return path

    def test_scan_directory(self):
        """Scans a directory tree recursively."""
        self._write('a.py', '# TODO: in a\n')
        os.makedirs(os.path.join(self.root, 'sub'), exist_ok=True)
        self._write('sub/b.py', '# FIXME: in b\n')
        findings = todo_tracker.scan(root=self.root)
        self.assertEqual(len(findings), 2)

    def test_skip_non_source(self):
        """Non-source files (e.g. .txt, .md) are skipped."""
        self._write('notes.txt', '# TODO: ignored\n')
        self._write('readme.md', '# TODO: also ignored\n')
        self._write('real.py', '# TODO: found\n')
        findings = todo_tracker.scan(root=self.root)
        self.assertEqual(len(findings), 1)
        self.assertIn('found', findings[0]['text'])

    def test_tag_filter(self):
        """--tag filters to specified tags only."""
        self._write('mixed.py', '# TODO: keep\n# FIXME: drop\n')
        findings = todo_tracker.scan(tags=['TODO'], root=self.root)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['tag'], 'TODO')

    def test_paths_filter(self):
        """--paths limits scan to specified directories."""
        os.makedirs(os.path.join(self.root, 'src'), exist_ok=True)
        os.makedirs(os.path.join(self.root, 'vendor'), exist_ok=True)
        self._write('src/a.py', '# TODO: included\n')
        self._write('vendor/b.py', '# TODO: excluded\n')
        findings = todo_tracker.scan(paths=['src'], root=self.root)
        self.assertEqual(len(findings), 1)
        self.assertIn('included', findings[0]['text'])

    def test_skip_hidden_dirs(self):
        """Hidden directories (starting with .) are skipped."""
        os.makedirs(os.path.join(self.root, '.hidden'), exist_ok=True)
        self._write('.hidden/secret.py', '# TODO: hidden\n')
        self._write('visible.py', '# TODO: visible\n')
        findings = todo_tracker.scan(root=self.root)
        self.assertEqual(len(findings), 1)
        self.assertIn('visible', findings[0]['text'])


class TestGroupByTag(unittest.TestCase):
    """Tests for grouping and formatting."""

    def test_group_by_tag(self):
        """Findings are grouped by tag type."""
        findings = [
            {'file': 'a.py', 'line': 1, 'tag': 'TODO', 'text': 'x'},
            {'file': 'b.py', 'line': 2, 'tag': 'FIXME', 'text': 'y'},
            {'file': 'c.py', 'line': 3, 'tag': 'TODO', 'text': 'z'},
        ]
        groups = todo_tracker.group_by_tag(findings)
        self.assertEqual(len(groups['TODO']), 2)
        self.assertEqual(len(groups['FIXME']), 1)


class TestCLI(unittest.TestCase):
    """Tests for CLI main() entry point."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = self.tmpdir.name

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent(content))
        return path

    def test_check_clean(self):
        """--check exits 0 when only TODOs found (no critical tags)."""
        self._write('clean.py', '# TODO: informational only\n')
        rc = todo_tracker.main(['--check', '--root', self.root])
        self.assertEqual(rc, 0)

    def test_check_fixme_fails(self):
        """--check exits 1 when FIXME found."""
        self._write('bad.py', '# FIXME: critical issue\n')
        rc = todo_tracker.main(['--check', '--root', self.root])
        self.assertEqual(rc, 1)

    def test_check_hack_fails(self):
        """--check exits 1 when HACK found."""
        self._write('bad.py', '# HACK: workaround\n')
        rc = todo_tracker.main(['--check', '--root', self.root])
        self.assertEqual(rc, 1)

    def test_json_output(self):
        """--json produces valid JSON with expected structure."""
        self._write('j.py', '# TODO: json test\n')
        import io as _io
        captured = _io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            rc = todo_tracker.main(['--json', '--root', self.root])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(rc, 0)
        data = json.loads(captured.getvalue())
        self.assertIn('findings', data)
        self.assertIn('summary', data)
        self.assertIn('total', data)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['summary']['TODO'], 1)

    def test_unknown_flag_exits_2(self):
        """Unknown CLI flags exit with code 2 (error)."""
        rc = todo_tracker.main(['--bogus'])
        self.assertEqual(rc, 2)

    def test_check_with_tag_filter(self):
        """--check respects --tag filter for critical determination."""
        self._write('f.py', '# FIXME: present\n# TODO: also present\n')
        # Only scanning TODO -- FIXME not in scope, so no critical
        rc = todo_tracker.main(['--check', '--tag', 'TODO', '--root', self.root])
        self.assertEqual(rc, 0)

    def test_empty_directory(self):
        """Empty directory produces no findings, exits 0."""
        rc = todo_tracker.main(['--root', self.root])
        self.assertEqual(rc, 0)

    def test_shell_comment(self):
        """Detects tags in shell script comments."""
        self._write('script.sh', '#!/bin/bash\n# XXX: shell issue\n')
        rc = todo_tracker.main(['--json', '--root', self.root])
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            todo_tracker.main(['--json', '--root', self.root])
        finally:
            sys.stdout = old_stdout
        data = json.loads(captured.getvalue())
        self.assertEqual(data['summary'].get('XXX', 0), 1)

    def test_typescript_scan(self):
        """Scans .ts files for tagged comments."""
        self._write('app.ts', '// TODO: typescript todo\n')
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            todo_tracker.main(['--json', '--root', self.root])
        finally:
            sys.stdout = old_stdout
        data = json.loads(captured.getvalue())
        self.assertEqual(data['total'], 1)


if __name__ == '__main__':
    unittest.main()
