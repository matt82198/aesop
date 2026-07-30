#!/usr/bin/env python3
"""Test suite for bash_guard_check.py"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestBashGuardCheck(unittest.TestCase):
    """Test cases for BASH_SOURCE exec guard validation."""

    def setUp(self):
        """Create temp directory for test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.tools_dir = Path(__file__).parent.parent / 'tools'

    def tearDown(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def write_shell_file(self, filename: str, content: str) -> Path:
        """Write a shell script to temp directory."""
        filepath = self.temp_path / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding='utf-8')
        return filepath

    def run_check(self, *args) -> tuple[int, str]:
        """Run bash_guard_check.py and return (exit_code, output)."""
        cmd = [
            sys.executable,
            str(self.tools_dir / 'bash_guard_check.py'),
            *args
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.temp_path)
        return result.returncode, result.stdout

    def test_pure_function_library_needs_no_guard(self):
        """Pure function libraries (no executable commands) need no guard."""
        content = """#!/bin/bash
# A library of utility functions
helper_func() {
  echo "helping"
}

another_func() {
  local x=10
  return 0
}
"""
        self.write_shell_file('lib.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Pure function library should pass: {output}")

    def test_pure_script_needs_no_guard(self):
        """Pure executable scripts (no functions) need no guard."""
        content = """#!/bin/bash
set -uo pipefail

echo "doing work"
result=$(find . -name "*.sh")
echo "$result"
"""
        self.write_shell_file('script.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Pure script should pass: {output}")

    def test_mixed_file_without_guard_fails(self):
        """Mixed file (functions + commands) without guard should fail."""
        content = """#!/bin/bash
helper() {
  echo "help"
}

echo "doing work"
helper
"""
        self.write_shell_file('bad.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 1, f"Mixed file without guard should fail: {output}")
        self.assertIn('Missing BASH_SOURCE guard', output)

    def test_mixed_file_with_guard_passes(self):
        """Mixed file with proper guard should pass."""
        content = """#!/bin/bash
helper() {
  echo "help"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "doing work"
  helper
fi
"""
        self.write_shell_file('good.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Mixed file with guard should pass: {output}")

    def test_guard_before_commands(self):
        """Guard must come before executable commands."""
        content = """#!/bin/bash
helper() {
  echo "help"
}

echo "first command"

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "guarded"
fi
"""
        self.write_shell_file('misplaced.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 1, f"Misplaced guard should fail: {output}")

    def test_guard_ok_suppression(self):
        """Files with # guard-ok comment should be skipped."""
        content = """#!/bin/bash
# guard-ok
helper() {
  echo "help"
}

echo "doing work"
helper
"""
        self.write_shell_file('suppressed.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"File with guard-ok should pass: {output}")

    def test_alternative_guard_pattern(self):
        """Alternative guard pattern should be recognized."""
        content = """#!/bin/bash
helper() {
  echo "help"
}

[[ $0 == "${BASH_SOURCE[0]}" ]] && {
  echo "doing work"
  helper
}
"""
        self.write_shell_file('alt_guard.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Alternative guard pattern should pass: {output}")

    def test_multiple_files_mixed_results(self):
        """Checker should report all files with findings."""
        self.write_shell_file('good1.sh', """#!/bin/bash
helper() { echo help; }
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "work"
fi
""")

        self.write_shell_file('bad1.sh', """#!/bin/bash
helper() { echo help; }
echo "work"
""")

        self.write_shell_file('good2.sh', """#!/bin/bash
echo "pure script"
""")

        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 1, "Mixed results should exit 1")
        self.assertIn('bad1.sh', output)
        self.assertNotIn('good1.sh', output)
        self.assertNotIn('good2.sh', output)

    def test_json_output_format(self):
        """JSON output should be properly formatted."""
        self.write_shell_file('test.sh', """#!/bin/bash
helper() { echo help; }
echo "work"
""")

        code, output = self.run_check('--json', '--paths', str(self.temp_path))
        self.assertEqual(code, 1)
        data = json.loads(output)
        self.assertIn('clean', data)
        self.assertIn('total', data)
        self.assertIn('findings', data)
        self.assertGreater(len(data['findings']), 0)

    def test_function_keyword_syntax(self):
        """Functions defined with 'function' keyword should be recognized."""
        content = """#!/bin/bash
function helper {
  echo "help"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "work"
fi
"""
        self.write_shell_file('func_keyword.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"'function' keyword syntax should pass: {output}")

    def test_subdirectory_scan(self):
        """Tool should recursively scan subdirectories."""
        self.write_shell_file('subdir/scripts/test.sh', """#!/bin/bash
helper() { echo help; }
echo "work"
""")

        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 1)
        self.assertIn('subdir', output)

    def test_guard_variations(self):
        """Different guard pattern variations should be accepted."""
        variations = [
            """#!/bin/bash
helper() { true; }
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then true; fi
""",
            """#!/bin/bash
helper() { true; }
[[ "${BASH_SOURCE[0]}" == "${0}" ]] && true
""",
            """#!/bin/bash
helper() { true; }
if [[ ${BASH_SOURCE[0]} == ${0} ]]; then true; fi
""",
        ]
        for i, content in enumerate(variations):
            self.write_shell_file(f'var{i}.sh', content)

        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Guard variations should pass: {output}")

    def test_nested_functions(self):
        """Files with nested functions should be checked."""
        content = """#!/bin/bash
outer() {
  inner() {
    echo "nested"
  }
  inner
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  outer
fi
"""
        self.write_shell_file('nested.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Nested functions should pass: {output}")

    def test_bash_suffix_files(self):
        """Files with .bash suffix should be checked."""
        self.write_shell_file('script.bash', """#!/bin/bash
helper() { echo help; }
echo "work"
""")

        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 1)
        self.assertIn('script.bash', output)

    def test_comments_not_commands(self):
        """Comment-only lines should not count as executable commands."""
        content = """#!/bin/bash
helper() { echo help; }
# This is a comment
# Another comment

# More comments
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  true
fi
"""
        self.write_shell_file('comments.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Comments should not trigger guard requirement: {output}")

    def test_variable_assignments_not_commands(self):
        """Variable assignments should not count as executable commands."""
        content = """#!/bin/bash
helper() { echo help; }

VAR="value"
ANOTHER=123
CONFIG_VALUE="${CONFIG_DEFAULT:-.}"

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "$VAR"
fi
"""
        self.write_shell_file('vars.sh', content)
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0, f"Variable assignments should not trigger guard: {output}")

    def test_empty_directory(self):
        """Empty directory should result in clean pass."""
        code, output = self.run_check('--paths', str(self.temp_path))
        self.assertEqual(code, 0)

    def test_nonexistent_directory(self):
        """Nonexistent directory should not cause error."""
        nonexistent = self.temp_path / 'nonexistent'
        code, output = self.run_check('--paths', str(nonexistent))
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
