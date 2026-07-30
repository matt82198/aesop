"""Verify auto_merge.run() uses list-form subprocess (no shell=True)."""
import ast
import os
import subprocess
import sys
import time
import unittest


class TestAutoMergeNoShell(unittest.TestCase):
    def test_run_helper_uses_list_form(self):
        """The run() helper must not pass shell=True to subprocess."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'auto_merge.py')
        with open(src, encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_subprocess = False
            if isinstance(func, ast.Attribute) and func.attr == 'run':
                if isinstance(func.value, ast.Attribute):
                    is_subprocess = func.value.attr == 'subprocess'
                elif isinstance(func.value, ast.Name):
                    is_subprocess = func.value.id == 'subprocess'
            if not is_subprocess:
                continue

            for kw in node.keywords:
                if kw.arg == 'shell':
                    self.assertFalse(
                        isinstance(kw.value, ast.Constant) and kw.value.value is True,
                        f'subprocess.run() at line {node.lineno} uses shell=True'
                    )

    def test_run_callers_pass_lists(self):
        """Every call to the module-level run() must pass a list, not a string."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'auto_merge.py')
        with open(src, encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == 'run'):
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            self.assertNotIsInstance(
                first_arg, (ast.Constant, ast.JoinedStr),
                f'run() at line {node.lineno} passes a string instead of a list'
            )


class TestAutoMergeTimeout(unittest.TestCase):
    """Test that run() helper has timeout protection."""

    def test_run_has_timeout_parameter(self):
        """The run() helper must accept a timeout parameter."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'auto_merge.py')
        with open(src, encoding='utf-8') as f:
            content = f.read()

        # Parse and find the run() function definition
        tree = ast.parse(content, filename=src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != 'run':
                continue
            # Check that timeout is in parameters (default or otherwise)
            arg_names = [arg.arg for arg in node.args.args]
            self.assertIn('timeout', arg_names,
                         'run() helper missing timeout parameter')
            return
        self.fail('Could not find run() function in auto_merge.py')

    def test_run_subprocess_has_timeout(self):
        """The subprocess.run() call inside run() must pass a timeout."""
        src = os.path.join(os.path.dirname(__file__), '..', 'tools', 'auto_merge.py')
        with open(src, encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content, filename=src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name != 'run':
                continue
            # Find subprocess.run call within this function
            for subnode in ast.walk(node):
                if not isinstance(subnode, ast.Call):
                    continue
                func = subnode.func
                is_subprocess_run = False
                if isinstance(func, ast.Attribute) and func.attr == 'run':
                    if isinstance(func.value, ast.Name):
                        is_subprocess_run = func.value.id == 'subprocess'
                if not is_subprocess_run:
                    continue
                # Found subprocess.run, verify it has timeout
                has_timeout = any(kw.arg == 'timeout' for kw in subnode.keywords)
                self.assertTrue(has_timeout,
                              'subprocess.run() in run() helper must have timeout parameter')
                return
            self.fail('Could not find subprocess.run() call in run() function')

    def test_subprocess_timeout_raises_exception(self):
        """Verify that subprocess timeout raises TimeoutExpired."""
        # Import the run function directly
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
        try:
            from auto_merge import run
        finally:
            sys.path.pop(0)

        # Test with a command that will timeout: sleep longer than timeout
        # Use sys.executable to call python (portable across Windows/Linux)
        with self.assertRaises(subprocess.TimeoutExpired):
            run([sys.executable, '-c', 'import time; time.sleep(2)'],
                timeout=0.5)

    def test_timeout_not_swallowed(self):
        """Verify TimeoutExpired is not silently caught."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
        try:
            from auto_merge import run
        finally:
            sys.path.pop(0)

        # Try to verify the exception propagates, not caught internally
        try:
            run([sys.executable, '-c', 'import time; time.sleep(3)'],
                timeout=0.5, check=False)
            self.fail('Expected TimeoutExpired but no exception was raised')
        except subprocess.TimeoutExpired:
            # Expected: exception should propagate
            pass


if __name__ == '__main__':
    unittest.main()
