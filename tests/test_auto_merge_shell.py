"""Verify auto_merge.run() uses list-form subprocess (no shell=True)."""
import ast
import os
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


if __name__ == '__main__':
    unittest.main()
