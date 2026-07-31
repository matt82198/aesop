#!/usr/bin/env python3
"""Test suite for tools/lint_core.py shared linting core."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Add tools/ to path so we can import lint_core
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools'))

import lint_core


class TestFinding(unittest.TestCase):
    """Test Finding class."""

    def test_finding_creation(self):
        """Test Finding creation and conversion."""
        f = lint_core.Finding('tools/foo.py', 42, 'unused-function', 'function bar is never used')
        self.assertEqual(f.file, 'tools/foo.py')
        self.assertEqual(f.line, 42)
        self.assertEqual(f.type, 'unused-function')
        self.assertEqual(f.message, 'function bar is never used')

    def test_finding_to_dict(self):
        """Test Finding.to_dict()."""
        f = lint_core.Finding('test.py', 1, 'error', 'test message')
        d = f.to_dict()
        self.assertEqual(d['file'], 'test.py')
        self.assertEqual(d['line'], 1)
        self.assertEqual(d['type'], 'error')
        self.assertEqual(d['message'], 'test message')


class TestNormalizePath(unittest.TestCase):
    """Test path normalization."""

    def test_forward_slashes_unchanged(self):
        """Forward slashes should pass through unchanged."""
        self.assertEqual(lint_core.normalize_path('tools/foo/bar.py'), 'tools/foo/bar.py')

    def test_backslashes_converted(self):
        """Backslashes should convert to forward slashes."""
        self.assertEqual(lint_core.normalize_path('tools\\foo\\bar.py'), 'tools/foo/bar.py')

    def test_mixed_slashes_normalized(self):
        """Mixed slashes should all become forward slashes."""
        self.assertEqual(lint_core.normalize_path('tools\\foo/bar\\baz.py'), 'tools/foo/bar/baz.py')


class TestDiscoverFiles(unittest.TestCase):
    """Test file discovery."""

    def setUp(self):
        """Create a temporary directory with test files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

        # Create a directory structure
        (self.root / 'tools').mkdir()
        (self.root / 'tools' / 'a.py').write_text('# file a\n')
        (self.root / 'tools' / 'b.js').write_text('// file b\n')

        (self.root / 'tests').mkdir()
        (self.root / 'tests' / 'test_a.py').write_text('# test\n')

        (self.root / '__pycache__').mkdir()
        (self.root / '__pycache__' / 'cached.pyc').write_text('')

        (self.root / '.git').mkdir()
        (self.root / '.git' / 'config').write_text('')

    def tearDown(self):
        """Clean up."""
        self.tmpdir.cleanup()

    def test_discover_all_files(self):
        """Discover all files without filters."""
        files = lint_core.discover_files(self.root)
        names = sorted([f.name for f in files])
        # Should find .py, .js, but not .git or __pycache__ by default
        self.assertIn('a.py', names)
        self.assertIn('b.js', names)
        self.assertIn('test_a.py', names)
        self.assertNotIn('config', names)
        self.assertNotIn('cached.pyc', names)

    def test_discover_py_files_only(self):
        """Filter to .py files only."""
        files = lint_core.discover_files(self.root, extensions=['.py'])
        names = sorted([f.name for f in files])
        self.assertIn('a.py', names)
        self.assertIn('test_a.py', names)
        self.assertNotIn('b.js', names)

    def test_discover_with_include_glob(self):
        """Filter with include glob patterns."""
        files = lint_core.discover_files(self.root, include=['tools/**/*.py'])
        names = sorted([f.name for f in files])
        self.assertIn('a.py', names)
        self.assertNotIn('test_a.py', names)

    def test_discover_with_exclude_glob(self):
        """Filter with exclude glob patterns."""
        files = lint_core.discover_files(self.root, exclude=['tests/**'])
        names = sorted([f.name for f in files])
        self.assertIn('a.py', names)
        self.assertNotIn('test_a.py', names)

    def test_discover_nonexistent_root(self):
        """Discovering from nonexistent root returns empty list."""
        files = lint_core.discover_files(Path('/nonexistent/path/to/nowhere'))
        self.assertEqual(files, [])


class TestASTCache(unittest.TestCase):
    """Test AST caching."""

    def setUp(self):
        """Create temporary Python files."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

        # Valid Python file
        self.valid_file = self.root / 'valid.py'
        self.valid_file.write_text('def foo():\n    pass\n')

        # Invalid Python file
        self.invalid_file = self.root / 'invalid.py'
        self.invalid_file.write_text('def foo(\n    # missing closing paren\n')

    def tearDown(self):
        """Clean up."""
        self.tmpdir.cleanup()

    def test_parse_valid_file(self):
        """Parse a valid Python file."""
        cache = lint_core.ASTCache()
        result = cache.parse(self.valid_file)
        self.assertIsNotNone(result)
        tree, source_lines = result
        self.assertIsInstance(tree, __import__('ast').AST)
        self.assertTrue(len(source_lines) > 0)

    def test_parse_invalid_file(self):
        """Parse an invalid Python file returns None."""
        cache = lint_core.ASTCache()
        result = cache.parse(self.invalid_file)
        self.assertIsNone(result)

    def test_cache_hit(self):
        """Caching prevents re-parsing."""
        cache = lint_core.ASTCache()
        result1 = cache.parse(self.valid_file)
        result2 = cache.parse(self.valid_file)
        self.assertIsNotNone(result1)
        self.assertIsNotNone(result2)
        # Should be same object from cache
        self.assertIs(result1[0], result2[0])

    def test_cache_error_on_invalid_file(self):
        """Errors are cached (second parse also returns None)."""
        cache = lint_core.ASTCache()
        result1 = cache.parse(self.invalid_file)
        result2 = cache.parse(self.invalid_file)
        self.assertIsNone(result1)
        self.assertIsNone(result2)


class TestFormatFindings(unittest.TestCase):
    """Test finding formatters."""

    def test_format_empty_text(self):
        """Format empty findings as text."""
        findings = []
        text = lint_core.format_findings_text(findings)
        self.assertIn("No findings", text)

    def test_format_findings_text(self):
        """Format findings as text."""
        findings = [
            lint_core.Finding('a.py', 1, 'error', 'test 1'),
            lint_core.Finding('b.py', 2, 'warning', 'test 2'),
        ]
        text = lint_core.format_findings_text(findings)
        self.assertIn('2 issue(s)', text)
        self.assertIn('a.py:1', text)
        self.assertIn('b.py:2', text)

    def test_format_findings_json(self):
        """Format findings as JSON."""
        findings = [
            lint_core.Finding('a.py', 1, 'error', 'test 1'),
        ]
        json_str = lint_core.format_findings_json(findings)
        data = json.loads(json_str)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['file'], 'a.py')
        self.assertEqual(data[0]['line'], 1)


class TestRatchetBaseline(unittest.TestCase):
    """Test ratchet baseline loading and checking."""

    def setUp(self):
        """Create temporary baseline file."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.baseline_file = Path(self.tmpdir.name) / 'baseline.json'

    def tearDown(self):
        """Clean up."""
        self.tmpdir.cleanup()

    def test_load_missing_baseline(self):
        """Loading a missing baseline returns empty dict."""
        ratchet = lint_core.RatchetBaseline(self.baseline_file)
        self.assertEqual(ratchet.data, {})

    def test_load_valid_baseline(self):
        """Load a valid baseline file."""
        baseline_data = {
            '_comment': 'Test baseline',
            'violations': {
                'file1@type1': 2,
                'file2@type2': 1,
            }
        }
        self.baseline_file.write_text(json.dumps(baseline_data))

        ratchet = lint_core.RatchetBaseline(self.baseline_file)
        self.assertEqual(ratchet.data['file1@type1'], 2)
        self.assertEqual(ratchet.data['file2@type2'], 1)

    def test_load_malformed_baseline(self):
        """Loading malformed JSON returns empty dict."""
        self.baseline_file.write_text('{ invalid json')
        ratchet = lint_core.RatchetBaseline(self.baseline_file)
        self.assertEqual(ratchet.data, {})

    def test_check_ratchet_clean(self):
        """Check with matching baseline."""
        baseline_data = {
            '_comment': 'Test baseline',
            'violations': {'file1@type1': 2}
        }
        self.baseline_file.write_text(json.dumps(baseline_data))
        ratchet = lint_core.RatchetBaseline(self.baseline_file)

        is_ok, stale, new = ratchet.check({'file1@type1': 2})
        self.assertTrue(is_ok)
        self.assertEqual(stale, [])
        self.assertEqual(new, [])

    def test_check_ratchet_increased(self):
        """Check fails when count increased."""
        baseline_data = {
            '_comment': 'Test baseline',
            'violations': {'file1@type1': 2}
        }
        self.baseline_file.write_text(json.dumps(baseline_data))
        ratchet = lint_core.RatchetBaseline(self.baseline_file)

        is_ok, stale, new = ratchet.check({'file1@type1': 3})
        self.assertFalse(is_ok)
        self.assertEqual(stale, [])
        self.assertEqual(len(new), 1)
        self.assertIn('baseline 2, current 3', new[0])

    def test_check_ratchet_decreased(self):
        """Check detects when count decreased."""
        baseline_data = {
            '_comment': 'Test baseline',
            'violations': {'file1@type1': 2}
        }
        self.baseline_file.write_text(json.dumps(baseline_data))
        ratchet = lint_core.RatchetBaseline(self.baseline_file)

        is_ok, stale, new = ratchet.check({'file1@type1': 1})
        self.assertFalse(is_ok)
        self.assertEqual(len(stale), 1)
        self.assertEqual(new, [])
        self.assertIn('baseline 2, current 1', stale[0])

    def test_check_ratchet_new_violation(self):
        """Check detects new violations."""
        baseline_data = {
            '_comment': 'Test baseline',
            'violations': {'file1@type1': 2}
        }
        self.baseline_file.write_text(json.dumps(baseline_data))
        ratchet = lint_core.RatchetBaseline(self.baseline_file)

        is_ok, stale, new = ratchet.check({'file1@type1': 2, 'file2@type2': 1})
        self.assertFalse(is_ok)
        self.assertEqual(stale, [])
        self.assertEqual(len(new), 1)

    def test_check_ratchet_zero_files_scanned(self):
        """Check with no files scanned (empty current dict)."""
        baseline_data = {
            '_comment': 'Test baseline',
            'violations': {'file1@type1': 2}
        }
        self.baseline_file.write_text(json.dumps(baseline_data))
        ratchet = lint_core.RatchetBaseline(self.baseline_file)

        is_ok, stale, new = ratchet.check({})
        self.assertFalse(is_ok)  # Bidirectional: all baseline items missing
        self.assertEqual(len(stale), 1)  # file1@type1 is stale

    def test_save_baseline(self):
        """Save a baseline file."""
        current = {
            'file1@type1': 2,
            'file2@type2': 1,
        }
        ratchet = lint_core.RatchetBaseline(self.baseline_file)
        ratchet.save(current, comment='Test comment')

        # Verify saved file
        self.assertTrue(self.baseline_file.exists())
        data = json.loads(self.baseline_file.read_text())
        self.assertEqual(data['_comment'], 'Test comment')
        self.assertEqual(data['violations']['file1@type1'], 2)
        self.assertEqual(data['violations']['file2@type2'], 1)

    def test_ratchet_windows_paths_normalized(self):
        """Windows paths with backslashes are normalized."""
        baseline_data = {
            '_comment': 'Test baseline',
            'violations': {
                'tools\\foo.py@type1': 1,  # Windows path in baseline
            }
        }
        self.baseline_file.write_text(json.dumps(baseline_data))
        ratchet = lint_core.RatchetBaseline(self.baseline_file)

        # Check with POSIX path
        is_ok, stale, new = ratchet.check({'tools/foo.py@type1': 1})
        self.assertTrue(is_ok)  # Should match after normalization


class TestExitCode(unittest.TestCase):
    """Test exit code determination."""

    def test_exit_clean(self):
        """Exit 0 for no findings and no errors."""
        code = lint_core.exit_code([])
        self.assertEqual(code, 0)

    def test_exit_findings(self):
        """Exit 1 when findings present."""
        findings = [lint_core.Finding('test.py', 1, 'error', 'test')]
        code = lint_core.exit_code(findings)
        self.assertEqual(code, 1)

    def test_exit_could_not_evaluate(self):
        """Exit 2 when could not evaluate."""
        code = lint_core.exit_code([], could_not_evaluate=True)
        self.assertEqual(code, 2)

    def test_exit_baseline_error(self):
        """Exit 2 when baseline file malformed."""
        code = lint_core.exit_code([], baseline_error=True)
        self.assertEqual(code, 2)

    def test_exit_could_not_evaluate_overrides_findings(self):
        """Exit 2 overrides exit 1 for could_not_evaluate."""
        findings = [lint_core.Finding('test.py', 1, 'error', 'test')]
        code = lint_core.exit_code(findings, could_not_evaluate=True)
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
