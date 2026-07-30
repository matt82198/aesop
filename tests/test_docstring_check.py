"""Tests for tools/docstring_check.py — docstring coverage checker."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Import the checker functions directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from docstring_check import (  # noqa: E402
    _has_docstring,
    _get_line_suppression,
    find_items_missing_docstrings,
    scan_file,
    gather_targets,
    run,
    format_ascii,
)


class TestDocstringDetection(unittest.TestCase):
    """Tests for basic docstring detection in AST."""

    def test_function_with_docstring(self):
        """Function with docstring is recognized."""
        source = '''
def foo():
    """This is a docstring."""
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 1)
        self.assertEqual(with_docs, 1)
        self.assertEqual(len(findings), 0)

    def test_function_without_docstring(self):
        """Function without docstring is flagged."""
        source = '''
def foo():
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 1)
        self.assertEqual(with_docs, 0)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["type"], "function")
        self.assertEqual(findings[0]["name"], "foo")

    def test_class_with_docstring(self):
        """Class with docstring is recognized."""
        source = '''
class MyClass:
    """This is a class docstring."""
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 1)
        self.assertEqual(with_docs, 1)
        self.assertEqual(len(findings), 0)

    def test_class_without_docstring(self):
        """Class without docstring is flagged."""
        source = '''
class MyClass:
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 1)
        self.assertEqual(with_docs, 0)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["type"], "class")
        self.assertEqual(findings[0]["name"], "MyClass")

    def test_private_function_ignored(self):
        """Private functions (starting with _) are ignored."""
        source = '''
def _private_func():
    pass

def public_func():
    """Has docstring."""
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 1)  # Only public_func counted
        self.assertEqual(with_docs, 1)
        self.assertEqual(len(findings), 0)

    def test_private_class_ignored(self):
        """Private classes (starting with _) are ignored."""
        source = '''
class _PrivateClass:
    pass

class PublicClass:
    """Has docstring."""
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 1)  # Only PublicClass counted
        self.assertEqual(with_docs, 1)
        self.assertEqual(len(findings), 0)

    def test_dunder_method_ignored(self):
        """Dunder methods (starting with __) are ignored."""
        source = '''
class MyClass:
    """Class docstring."""
    def __init__(self):
        pass

    def public_method(self):
        pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 2)  # MyClass and public_method
        self.assertEqual(with_docs, 1)  # Only MyClass has docstring
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["name"], "public_method")

    def test_multiple_items_mixed(self):
        """Multiple functions and classes with mixed docstrings."""
        source = '''
def func_with_doc():
    """Has docstring."""
    pass

def func_without_doc():
    pass

class ClassWithDoc:
    """Has docstring."""
    pass

class ClassWithoutDoc:
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 4)
        self.assertEqual(with_docs, 2)
        self.assertEqual(len(findings), 2)
        names = [f["name"] for f in findings]
        self.assertIn("func_without_doc", names)
        self.assertIn("ClassWithoutDoc", names)

    def test_syntax_error_handled(self):
        """Syntax errors are handled gracefully."""
        source = '''
def broken_func(
    pass  # Missing closing paren
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(len(findings), 0)
        self.assertEqual(total, 0)


class TestSuppressionMarker(unittest.TestCase):
    """Tests for suppression marker detection."""

    def test_suppression_marker_recognized(self):
        """# docstring-ok suppression marker is recognized."""
        source = '''
def no_doc():  # docstring-ok
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(total, 1)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["suppressed"])

    def test_suppression_marker_not_applied_without(self):
        """Without marker, missing docstring is not suppressed."""
        source = '''
def no_doc():
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        self.assertEqual(len(findings), 1)
        self.assertFalse(findings[0]["suppressed"])

    def test_multiple_suppressed_items(self):
        """Multiple items can be suppressed independently."""
        source = '''
def func_suppressed():  # docstring-ok
    pass

def func_not_suppressed():
    pass

class ClassSuppressed:  # docstring-ok
    pass
'''
        findings, total, with_docs = find_items_missing_docstrings(source)
        # 3 items total: func_suppressed, func_not_suppressed, ClassSuppressed
        self.assertEqual(len(findings), 3)
        suppressed_names = [f["name"] for f in findings if f["suppressed"]]
        unsuppressed_names = [f["name"] for f in findings if not f["suppressed"]]
        self.assertIn("func_suppressed", suppressed_names)
        self.assertIn("func_not_suppressed", unsuppressed_names)
        self.assertIn("ClassSuppressed", suppressed_names)


class TestFileScanning(unittest.TestCase):
    """Tests for file-based scanning."""

    def test_scan_file_with_findings(self):
        """Scanning a file returns findings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_module.py"
            test_file.write_text(
                '''
def documented():
    """Has docstring."""
    pass

def undocumented():
    pass
''',
                encoding="utf-8"
            )
            findings, total, with_docs, unsuppressed = scan_file(test_file)
            self.assertEqual(total, 2)
            self.assertEqual(with_docs, 1)
            self.assertEqual(unsuppressed, 1)
            self.assertEqual(len(findings), 1)

    def test_scan_nonexistent_file(self):
        """Scanning nonexistent file returns empty result."""
        findings, total, with_docs, unsuppressed = scan_file(Path("/nonexistent/file.py"))
        self.assertEqual(len(findings), 0)
        self.assertEqual(total, 0)
        self.assertEqual(unsuppressed, 0)

    def test_scan_file_with_encoding_issues(self):
        """Files with encoding issues are handled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test_module.py"
            # Write with UTF-8 but pretend to be binary
            test_file.write_bytes(b"def foo():\n    pass")
            findings, total, with_docs, unsuppressed = scan_file(test_file)
            # Should not raise, should read successfully
            self.assertEqual(total, 1)


class TestGatherTargets(unittest.TestCase):
    """Tests for target file gathering."""

    def test_gather_from_directory(self):
        """Files are gathered from a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create some Python files
            (tmppath / "module1.py").write_text("def foo(): pass")
            (tmppath / "module2.py").write_text("def bar(): pass")
            (tmppath / "test_module.py").write_text("def test(): pass")  # Should be ignored

            targets = gather_targets(tmppath, [str(tmppath)])
            filenames = [t.name for t in targets]

            self.assertIn("module1.py", filenames)
            self.assertIn("module2.py", filenames)
            self.assertNotIn("test_module.py", filenames)  # Test files ignored

    def test_gather_with_nonexistent_paths(self):
        """Nonexistent paths are silently skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "valid.py").write_text("def foo(): pass")

            targets = gather_targets(tmppath, [str(tmppath / "nonexistent")])
            self.assertEqual(len(targets), 0)

    def test_gather_ignores_test_files(self):
        """Test files are excluded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "module.py").write_text("def foo(): pass")
            (tmppath / "test_module.py").write_text("def test(): pass")
            (tmppath / "module.test.py").write_text("def test(): pass")
            (tmppath / "__init__.py").write_text("")

            targets = gather_targets(tmppath, [str(tmppath)])
            filenames = [t.name for t in targets]

            self.assertEqual(filenames, ["module.py"])

    def test_gather_deduplicates(self):
        """Duplicate paths are deduplicated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "module.py").write_text("def foo(): pass")

            targets = gather_targets(tmppath, [str(tmppath), str(tmppath / "module.py"), str(tmppath)])
            # Should only have module.py once
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0].name, "module.py")


class TestRunAndFormatting(unittest.TestCase):
    """Tests for the main run() function and output formatting."""

    def test_run_calculates_coverage(self):
        """run() correctly calculates coverage percentage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            test_file = tmppath / "module.py"
            test_file.write_text(
                '''
def doc1():
    """Has docstring."""
    pass

def doc2():
    """Has docstring."""
    pass

def no_doc1():
    pass

def no_doc2():
    pass
'''
            )

            result = run(tmppath, [str(tmppath)])

            # 4 items total, 2 with docstrings = 50%
            self.assertEqual(result["total_items"], 4)
            self.assertEqual(result["items_with_docstrings"], 2)
            self.assertEqual(result["coverage_percent"], 50)

    def test_run_with_threshold_pass(self):
        """run() passes when coverage meets threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            test_file = tmppath / "module.py"
            test_file.write_text(
                '''
def doc():
    """Has docstring."""
    pass
'''
            )

            result = run(tmppath, [str(tmppath)])
            # 100% coverage
            self.assertEqual(result["coverage_percent"], 100)

    def test_run_tracks_unsuppressed_findings(self):
        """run() counts unsuppressed findings separately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            test_file = tmppath / "module.py"
            test_file.write_text(
                '''
def no_doc1():
    pass

def no_doc2():  # docstring-ok
    pass
'''
            )

            result = run(tmppath, [str(tmppath)])

            # 2 findings total, but only 1 unsuppressed
            self.assertEqual(len(result["findings"]), 2)
            self.assertEqual(result["unsuppressed_findings"], 1)

    def test_format_ascii_output(self):
        """ASCII formatting produces readable output."""
        result = {
            "ok": False,
            "coverage_percent": 50,
            "total_items": 4,
            "items_with_docstrings": 2,
            "unsuppressed_findings": 1,
            "scanned_files": 1,
            "findings": [
                {
                    "file": "/path/to/module.py",
                    "type": "function",
                    "name": "foo",
                    "line": 5,
                    "suppressed": False,
                }
            ],
        }

        output = format_ascii(result)

        self.assertIn("docstring-check:", output)
        self.assertIn("1 finding(s)", output)
        self.assertIn("50%", output)
        self.assertIn("foo", output)
        self.assertIn("missing docstring", output)

    def test_format_ascii_no_findings(self):
        """ASCII formatting shows PASS when no findings."""
        result = {
            "ok": True,
            "coverage_percent": 100,
            "total_items": 2,
            "items_with_docstrings": 2,
            "unsuppressed_findings": 0,
            "scanned_files": 1,
            "findings": [],
        }

        output = format_ascii(result)

        self.assertIn("PASS", output)
        self.assertIn("100%", output)
        self.assertIn("no findings", output)


class TestCLI(unittest.TestCase):
    """Integration tests for CLI."""

    def test_cli_json_output(self):
        """CLI --json produces valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            test_file = tmppath / "module.py"
            test_file.write_text("def foo():\n    pass")

            result = subprocess.run(
                [sys.executable, "tools/docstring_check.py", "--json",
                 "--paths", str(tmppath), "--root", str(tmppath)],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 1)  # Has findings

            output = json.loads(result.stdout)
            self.assertIn("coverage_percent", output)
            self.assertIn("findings", output)
            self.assertIsInstance(output["findings"], list)

    def test_cli_threshold_enforcement(self):
        """CLI --threshold enforces minimum coverage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # File with 50% coverage (2 out of 4)
            test_file = tmppath / "module.py"
            test_file.write_text(
                '''
def doc():
    """Has docstring."""
    pass

def no_doc():
    pass
'''
            )

            # With threshold 75%, should fail (50% < 75%)
            result = subprocess.run(
                [sys.executable, "tools/docstring_check.py",
                 "--threshold", "75", "--paths", str(tmppath), "--root", str(tmppath)],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 1)

            # File with 100% coverage (all documented)
            clean_file = tmppath / "clean.py"
            clean_file.write_text(
                '''
def documented1():
    """Has docstring."""
    pass

def documented2():
    """Also has docstring."""
    pass
'''
            )

            # With threshold 25% and only scanning clean.py, should pass (100% >= 25%)
            result = subprocess.run(
                [sys.executable, "tools/docstring_check.py",
                 "--threshold", "25", "--paths", str(clean_file), "--root", str(tmppath)],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 0)

    def test_cli_with_specific_paths(self):
        """CLI --paths accepts specific directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            subdir1 = tmppath / "subdir1"
            subdir2 = tmppath / "subdir2"
            subdir1.mkdir()
            subdir2.mkdir()

            (subdir1 / "module1.py").write_text("def foo():\n    pass")
            (subdir2 / "module2.py").write_text("def bar():\n    pass")

            # Scan only subdir1
            result = subprocess.run(
                [sys.executable, "tools/docstring_check.py",
                 "--json", "--paths", str(subdir1), "--root", str(tmppath)],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            if result.returncode not in (0, 1):
                # Debug: print stderr if there's an error
                raise AssertionError(f"Unexpected return code {result.returncode}: stderr={result.stderr}")

            output = json.loads(result.stdout.strip())
            self.assertEqual(output["scanned_files"], 1)

    def test_cli_exit_codes(self):
        """CLI returns correct exit codes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # File with all functions documented = exit 0
            (tmppath / "clean.py").write_text("def foo():\n    \"\"\"Doc.\"\"\"\n    pass")

            result = subprocess.run(
                [sys.executable, "tools/docstring_check.py", "--root", str(tmppath),
                 "--paths", str(tmppath / "clean.py")],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 0)

            # File with missing docstrings = exit 1
            (tmppath / "dirty.py").write_text("def foo():\n    pass")

            result = subprocess.run(
                [sys.executable, "tools/docstring_check.py", "--root", str(tmppath),
                 "--paths", str(tmppath / "dirty.py")],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
                timeout=30,
            )

            self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
