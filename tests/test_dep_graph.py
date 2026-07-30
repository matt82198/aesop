"""Tests for tools/dep_graph.py — Python module dependency graph generator."""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import dep_graph


class TestFindPythonFiles(unittest.TestCase):
    """Test Python file discovery."""

    def test_finds_py_files_in_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("x = 1", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "b.py").write_text("y = 2", encoding="utf-8")
            (root / "not_py.txt").write_text("z", encoding="utf-8")
            found = dep_graph.find_python_files(root)
            names = [f.name for f in found]
            self.assertIn("a.py", names)
            self.assertIn("b.py", names)
            self.assertNotIn("not_py.txt", names)

    def test_filters_by_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inc").mkdir()
            (root / "exc").mkdir()
            (root / "inc" / "a.py").write_text("x = 1", encoding="utf-8")
            (root / "exc" / "b.py").write_text("y = 2", encoding="utf-8")
            found = dep_graph.find_python_files(root, ["inc"])
            names = [f.name for f in found]
            self.assertIn("a.py", names)
            self.assertNotIn("b.py", names)


class TestFileToModule(unittest.TestCase):
    """Test path-to-module conversion."""

    def test_simple_file(self):
        root = Path("/project")
        fp = Path("/project/tools/foo.py")
        self.assertEqual(dep_graph.file_to_module(fp, root), "tools.foo")

    def test_init_file(self):
        root = Path("/project")
        fp = Path("/project/pkg/__init__.py")
        self.assertEqual(dep_graph.file_to_module(fp, root), "pkg")


class TestExtractImports(unittest.TestCase):
    """Test import extraction via ast."""

    def test_import_and_from_import(self):
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "mod.py"
            fp.write_text(
                textwrap.dedent("""\
                    import os
                    from pathlib import Path
                    import json
                """),
                encoding="utf-8",
            )
            imports = dep_graph.extract_imports(fp)
            self.assertIn("os", imports)
            self.assertIn("pathlib", imports)
            self.assertIn("json", imports)

    def test_syntax_error_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "bad.py"
            fp.write_text("def broken(:\n", encoding="utf-8")
            imports = dep_graph.extract_imports(fp)
            self.assertEqual(imports, [])


class TestBuildGraph(unittest.TestCase):
    """Test graph construction from fixture directories."""

    def _make_fixture(self, td):
        root = Path(td)
        (root / "alpha.py").write_text(
            "import beta\n", encoding="utf-8"
        )
        (root / "beta.py").write_text(
            "from gamma import something\n", encoding="utf-8"
        )
        (root / "gamma.py").write_text(
            "x = 1\n", encoding="utf-8"
        )
        return root

    def test_linear_deps(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._make_fixture(td)
            graph, modules = dep_graph.build_graph(root)
            self.assertIn("beta", graph["alpha"])
            self.assertIn("gamma", graph["beta"])
            self.assertEqual(graph["gamma"], set())

    def test_no_self_edges(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "self_imp.py").write_text(
                "import self_imp\n", encoding="utf-8"
            )
            graph, _ = dep_graph.build_graph(root)
            self.assertNotIn("self_imp", graph.get("self_imp", set()))


class TestFindCycles(unittest.TestCase):
    """Test cycle detection."""

    def test_detects_cycle(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        cycles = dep_graph.find_cycles(graph)
        self.assertTrue(len(cycles) > 0)
        # The cycle a->b->c->a should be present
        flat = set()
        for c in cycles:
            flat.update(c)
        self.assertIn("a", flat)
        self.assertIn("b", flat)
        self.assertIn("c", flat)

    def test_no_cycle(self):
        graph = {"a": {"b"}, "b": {"c"}, "c": set()}
        cycles = dep_graph.find_cycles(graph)
        self.assertEqual(cycles, [])


class TestRenderers(unittest.TestCase):
    """Test output renderers."""

    def setUp(self):
        self.graph = {"alpha": {"beta"}, "beta": set()}
        self.cycles = []

    def test_mermaid_output(self):
        out = dep_graph.render_mermaid(self.graph, self.cycles)
        self.assertIn("flowchart LR", out)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_dot_output(self):
        out = dep_graph.render_dot(self.graph, self.cycles)
        self.assertIn("digraph dependencies", out)
        self.assertIn("alpha -> beta", out)

    def test_json_output(self):
        out = dep_graph.render_json(self.graph, self.cycles)
        data = json.loads(out)
        self.assertIn("modules", data)
        self.assertIn("edges", data)
        self.assertIn("cycles", data)
        self.assertEqual(len(data["edges"]), 1)

    def test_cycle_edges_highlighted_mermaid(self):
        graph = {"a": {"b"}, "b": {"a"}}
        cycles = dep_graph.find_cycles(graph)
        out = dep_graph.render_mermaid(graph, cycles)
        self.assertIn("fill:#f99", out)


class TestCLI(unittest.TestCase):
    """Test CLI entry point."""

    def test_help_exits_zero(self):
        rc = dep_graph.main(["--help"])
        self.assertEqual(rc, 0)

    def test_bad_root_exits_two(self):
        rc = dep_graph.main(["--root", "/nonexistent_path_xyz"])
        self.assertEqual(rc, 2)

    def test_json_format_on_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "foo.py").write_text("import bar\n", encoding="utf-8")
            (root / "bar.py").write_text("x = 1\n", encoding="utf-8")
            outfile = str(root / "out.json")
            rc = dep_graph.main(
                ["--root", str(root), "--format", "json", "--output", outfile]
            )
            self.assertEqual(rc, 0)
            data = json.loads(Path(outfile).read_text(encoding="utf-8"))
            self.assertIn("foo", data["modules"])
            self.assertIn("bar", data["modules"])


if __name__ == "__main__":
    unittest.main()
