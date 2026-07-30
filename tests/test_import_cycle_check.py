"""Tests for tools/import_cycle_check.py — AST-based import cycle detector."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure tools/ is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'tools'))

import import_cycle_check


class TestModuleNameFromPath(unittest.TestCase):
    """Test module_name_from_path conversion."""

    def test_simple_file(self):
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / 'foo.py'
            fp.write_text('', encoding='utf-8')
            result = import_cycle_check.module_name_from_path(fp, td)
            self.assertEqual(result, 'foo')

    def test_nested_module(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / 'pkg' / 'sub'
            pkg.mkdir(parents=True)
            fp = pkg / 'mod.py'
            fp.write_text('', encoding='utf-8')
            result = import_cycle_check.module_name_from_path(fp, td)
            self.assertEqual(result, 'pkg.sub.mod')

    def test_init_file(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / 'pkg'
            pkg.mkdir()
            fp = pkg / '__init__.py'
            fp.write_text('', encoding='utf-8')
            result = import_cycle_check.module_name_from_path(fp, td)
            self.assertEqual(result, 'pkg')


class TestResolveRelativeImport(unittest.TestCase):
    """Test relative import resolution."""

    def test_level_one_with_name(self):
        result = import_cycle_check.resolve_relative_import('pkg.mod', 1, 'other')
        self.assertEqual(result, 'pkg.other')

    def test_level_one_no_name(self):
        result = import_cycle_check.resolve_relative_import('pkg.mod', 1, None)
        self.assertEqual(result, 'pkg')

    def test_level_two(self):
        result = import_cycle_check.resolve_relative_import('a.b.c', 2, 'x')
        self.assertEqual(result, 'a.x')


class TestExtractImports(unittest.TestCase):
    """Test AST-based import extraction."""

    def test_absolute_import(self):
        with tempfile.TemporaryDirectory() as td:
            # Create two modules
            (Path(td) / 'alpha.py').write_text(
                'import beta\n', encoding='utf-8'
            )
            (Path(td) / 'beta.py').write_text('', encoding='utf-8')
            known = {'alpha', 'beta'}
            imports = import_cycle_check.extract_imports(
                Path(td) / 'alpha.py', td, known
            )
            self.assertIn('beta', imports)

    def test_from_import(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'alpha.py').write_text(
                'from beta import something\n', encoding='utf-8'
            )
            (Path(td) / 'beta.py').write_text('', encoding='utf-8')
            known = {'alpha', 'beta'}
            imports = import_cycle_check.extract_imports(
                Path(td) / 'alpha.py', td, known
            )
            self.assertIn('beta', imports)

    def test_stdlib_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'alpha.py').write_text(
                'import os\nimport json\n', encoding='utf-8'
            )
            known = {'alpha'}
            imports = import_cycle_check.extract_imports(
                Path(td) / 'alpha.py', td, known
            )
            self.assertEqual(imports, [])

    def test_syntax_error_handled(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'bad.py').write_text(
                'def broken(\n', encoding='utf-8'
            )
            known = {'bad'}
            imports = import_cycle_check.extract_imports(
                Path(td) / 'bad.py', td, known
            )
            self.assertEqual(imports, [])

    def test_relative_import(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / 'pkg'
            pkg.mkdir()
            (pkg / '__init__.py').write_text('', encoding='utf-8')
            (pkg / 'a.py').write_text(
                'from . import b\n', encoding='utf-8'
            )
            (pkg / 'b.py').write_text('', encoding='utf-8')
            known = {'pkg', 'pkg.a', 'pkg.b'}
            imports = import_cycle_check.extract_imports(
                pkg / 'a.py', td, known
            )
            self.assertTrue(any('pkg' in imp for imp in imports))


class TestFindCycles(unittest.TestCase):
    """Test cycle detection in dependency graphs."""

    def test_no_cycle(self):
        graph = {'a': {'b'}, 'b': {'c'}, 'c': set()}
        cycles = import_cycle_check.find_cycles(graph)
        self.assertEqual(cycles, [])

    def test_simple_cycle(self):
        graph = {'a': {'b'}, 'b': {'a'}}
        cycles = import_cycle_check.find_cycles(graph)
        self.assertGreater(len(cycles), 0)
        # The cycle should contain both a and b
        flat = [node for cycle in cycles for node in cycle]
        self.assertIn('a', flat)
        self.assertIn('b', flat)

    def test_triangle_cycle(self):
        graph = {'a': {'b'}, 'b': {'c'}, 'c': {'a'}}
        cycles = import_cycle_check.find_cycles(graph)
        deduped = import_cycle_check.dedupe_cycles(cycles)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(len(deduped[0]), 4)  # a -> b -> c -> a

    def test_disconnected_no_cycle(self):
        graph = {'a': set(), 'b': set(), 'c': set()}
        cycles = import_cycle_check.find_cycles(graph)
        self.assertEqual(cycles, [])


class TestBuildGraph(unittest.TestCase):
    """Test full graph building from fixture directories."""

    def test_builds_graph_no_cycles(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'foo.py').write_text(
                'import bar\n', encoding='utf-8'
            )
            (Path(td) / 'bar.py').write_text('', encoding='utf-8')
            graph = import_cycle_check.build_graph([td], td)
            self.assertIn('foo', graph)
            self.assertIn('bar', graph)
            self.assertIn('bar', graph['foo'])

    def test_builds_graph_with_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'x.py').write_text(
                'import y\n', encoding='utf-8'
            )
            (Path(td) / 'y.py').write_text(
                'import x\n', encoding='utf-8'
            )
            graph = import_cycle_check.build_graph([td], td)
            cycles = import_cycle_check.find_cycles(graph)
            self.assertGreater(len(cycles), 0)


class TestMainCLI(unittest.TestCase):
    """Test the main() CLI entry point."""

    def test_help_returns_zero(self):
        rc = import_cycle_check.main(['--help'])
        self.assertEqual(rc, 0)

    def test_no_cycles_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'a.py').write_text('', encoding='utf-8')
            (Path(td) / 'b.py').write_text('', encoding='utf-8')
            rc = import_cycle_check.main(['--paths', td])
            self.assertEqual(rc, 0)

    def test_cycles_returns_one(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'p.py').write_text(
                'import q\n', encoding='utf-8'
            )
            (Path(td) / 'q.py').write_text(
                'import p\n', encoding='utf-8'
            )
            rc = import_cycle_check.main(['--check', '--paths', td])
            self.assertEqual(rc, 1)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / 'solo.py').write_text('', encoding='utf-8')
            # Capture stdout
            import io
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            try:
                rc = import_cycle_check.main(['--json', '--paths', td])
            finally:
                output = sys.stdout.getvalue()
                sys.stdout = old_stdout
            self.assertEqual(rc, 0)
            data = json.loads(output)
            self.assertEqual(data['cycles_found'], 0)
            self.assertIn('module_count', data)


class TestDedupeCycles(unittest.TestCase):
    """Test cycle deduplication."""

    def test_removes_duplicates(self):
        # Same cycle, different starting point
        cycles = [
            ['a', 'b', 'c', 'a'],
            ['b', 'c', 'a', 'b'],
        ]
        deduped = import_cycle_check.dedupe_cycles(cycles)
        self.assertEqual(len(deduped), 1)

    def test_keeps_distinct_cycles(self):
        cycles = [
            ['a', 'b', 'a'],
            ['c', 'd', 'c'],
        ]
        deduped = import_cycle_check.dedupe_cycles(cycles)
        self.assertEqual(len(deduped), 2)


if __name__ == '__main__':
    unittest.main()
