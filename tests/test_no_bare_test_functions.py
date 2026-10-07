"""
Guard test: ensures all test_* functions are wrapped in a unittest.TestCase.

This test scans all tests/test_*.py files and fails if any module-level
def test_*() functions are found outside of a class.
"""

import ast
import unittest
from pathlib import Path


class TestNoBareTestFunctions(unittest.TestCase):
    """Enforce that all test functions are inside a TestCase."""

    def test_no_bare_test_functions(self):
        """Scan tests/ directory and fail if any bare test functions exist."""
        tests_dir = Path(__file__).parent
        bare_functions = []

        for test_file in tests_dir.glob("test_*.py"):
            if test_file.name == "test_no_bare_test_functions.py":
                # Skip this file
                continue

            try:
                with open(test_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(test_file))
            except SyntaxError as e:
                self.fail(f"Syntax error in {test_file}: {e}")

            # Find all module-level function definitions
            for node in ast.walk(tree):
                if isinstance(node, ast.Module):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                            # This is a bare test function (not in a class)
                            bare_functions.append(f"{test_file.name}:{item.lineno} {item.name}()")

        if bare_functions:
            msg = "Found bare test functions (should be inside a unittest.TestCase):\n"
            msg += "\n".join(f"  {fn}" for fn in bare_functions)
            self.fail(msg)

    def test_no_baseless_test_classes(self):
        """Fail on Test* classes with no base class: unittest discover silently
        collects ZERO tests from them (pytest-style classes), so their tests
        never run in CI. Every Test* class must subclass unittest.TestCase
        (directly or via any explicit base)."""
        tests_dir = Path(__file__).parent
        baseless = []

        for test_file in tests_dir.glob("test_*.py"):
            if test_file.name == "test_no_bare_test_functions.py":
                continue

            try:
                with open(test_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(test_file))
            except SyntaxError as e:
                self.fail(f"Syntax error in {test_file}: {e}")

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    has_test_methods = any(
                        isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
                        for item in node.body
                    )
                    if has_test_methods and not node.bases:
                        baseless.append(f"{test_file.name}:{node.lineno} class {node.name}")

        if baseless:
            msg = (
                "Found baseless Test* classes (invisible to unittest discover — "
                "their tests NEVER run; subclass unittest.TestCase):\n"
            )
            msg += "\n".join(f"  {c}" for c in baseless)
            self.fail(msg)


if __name__ == "__main__":
    unittest.main()
