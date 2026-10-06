"""
Test suite for tools/portability_check.py
"""

import unittest
import tempfile
import os
import sys
import json
from pathlib import Path
import subprocess


class TestPortabilityCheck(unittest.TestCase):
    """Tests for portability_check.py"""

    def setUp(self):
        """Create a temporary directory for test files."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.test_dir)

    def create_package_json(self, files_array):
        """Create a minimal package.json with specified 'files' array."""
        pkg = {
            "name": "test-package",
            "version": "1.0.0",
            "files": files_array
        }
        pkg_path = os.path.join(self.test_dir, 'package.json')
        with open(pkg_path, 'w') as f:
            json.dump(pkg, f)

    def create_test_file(self, relative_path, content):
        """Create a test file with given content."""
        full_path = os.path.join(self.test_dir, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w') as f:
            f.write(content)
        return full_path

    def run_portability_check(self, json_output=False):
        """Run portability_check.py and return exit code and output."""
        script_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'tools',
            'portability_check.py'
        )
        cmd = [sys.executable, script_path, '--root', self.test_dir]
        if json_output:
            cmd.append('--json')

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        return result.returncode, result.stdout, result.stderr

    def test_detects_windows_user_path_backslash(self):
        """Test detection of Windows path with backslashes."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'CONFIG_PATH = r"C:\\Users\\myuser\\config"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 1, f"Expected exit code 1, got {exit_code}")
        self.assertIn('C:\\Users\\myuser', stderr)

    def test_detects_windows_user_path_forward_slash(self):
        """Test detection of Windows path with forward slashes."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'CONFIG_PATH = "C:/Users/alice/config"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 1)
        self.assertIn('C:/Users/alice', stderr)

    def test_ignores_exception_lines_with_example(self):
        """Test that lines marked 'example' are not flagged."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            '# Example: CONFIG_PATH = "C:/Users/myuser/config"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 0, f"Expected clean exit, got: {stderr}")

    def test_ignores_exception_lines_with_default(self):
        """Test that lines marked 'default' are not flagged."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'DEFAULT_HOME = "C:/Users/matt8"  # default value'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 0, f"Expected clean exit, got: {stderr}")

    def test_ignores_exception_lines_with_e_g(self):
        """Test that lines marked 'e.g.' are not flagged."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            '# e.g., C:/Users/conductor3/project'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 0, f"Expected clean exit, got: {stderr}")

    def test_detects_posix_home_path(self):
        """Test detection of POSIX /home/<name> path."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'CONFIG_PATH = "/home/alice/config"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 1)
        self.assertIn('/home/alice', stderr)

    def test_detects_posix_users_path(self):
        """Test detection of POSIX /Users/<name> path."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'CONFIG_PATH = "/Users/bob/config"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 1)
        self.assertIn('/Users/bob', stderr)

    def test_detects_conductor3_token(self):
        """Test detection of 'conductor3' token."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'WATCH_PATH = "/c/Users/conductor3/state"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 1)
        self.assertIn('conductor3', stderr)

    def test_detects_matt8_token(self):
        """Test detection of 'matt8' token."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'USER_NAME = "matt8"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 1)
        self.assertIn('matt8', stderr)

    def test_allows_matt8_in_package_name(self):
        """Test that 'matt8' in author field (not code) doesn't flag."""
        # Note: This is a bit tricky since package.json is JSON, not scanned.
        # The test verifies scanner behavior on .py files.
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            '# Author: @matt82198 (not flagged: contains 82)'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        # Should be clean because we're looking for whole word 'matt8', not 'matt82198'
        self.assertEqual(exit_code, 0, f"Unexpected flag for @matt82198: {stderr}")

    def test_ignores_files_in_tests_directory(self):
        """Test that files outside package.json 'files' globs are ignored."""
        # Only include lib/ in files array, not tests/
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'tests/test_module.py',
            'CONFIG = "C:/Users/testuser/config"'
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 0, f"Should ignore tests/: {stderr}")

    def test_globs_expansion(self):
        """Test that glob patterns are properly expanded."""
        self.create_package_json(['lib/**/*.py'])
        self.create_test_file('lib/subdir/module.py', 'x = "C:/Users/user"')

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 1)
        self.assertIn('module.py', stderr)

    def test_json_output(self):
        """Test --json flag output format."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            'CONFIG = "C:/Users/baduser"'
        )

        exit_code, stdout, stderr = self.run_portability_check(json_output=True)
        self.assertEqual(exit_code, 1)

        findings = json.loads(stdout)
        self.assertIsInstance(findings, list)
        self.assertGreater(len(findings), 0)
        self.assertIn('file', findings[0])
        self.assertIn('line', findings[0])
        self.assertIn('type', findings[0])

    def test_clean_shipped_surface(self):
        """Test that clean code passes."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            '''
# A clean module
def get_config_path():
    """Return config path (platform-independent)."""
    return os.path.expanduser("~/.config/app")
            '''
        )

        exit_code, stdout, stderr = self.run_portability_check()
        self.assertEqual(exit_code, 0, f"Expected clean exit: {stderr}")

    def test_multiple_issues_same_file(self):
        """Test detection of multiple issues in one file."""
        self.create_package_json(['lib/*.py'])
        self.create_test_file(
            'lib/module.py',
            '''
path1 = "C:/Users/alice"
path2 = "/home/bob"
machine_tag = "conductor3"
            '''
        )

        exit_code, stdout, stderr = self.run_portability_check(json_output=True)
        self.assertEqual(exit_code, 1)

        findings = json.loads(stdout)
        self.assertGreaterEqual(len(findings), 3)


class TestPortabilityCheckAgainstRealBaseline(unittest.TestCase):
    """Regression: run the real gate against the real repo + committed baseline.

    This reproduces the exact CI step (`ci.yml`'s "Portability gate (ratchet
    mode)") locally so a new unmarked private-token mention (e.g. a bare
    'conductor3' in a docstring/comment added to tools/common.py) is caught
    before push instead of failing all CI shards via matrix fail-fast.
    """

    def test_repo_passes_portability_ratchet(self):
        repo_root = os.path.join(os.path.dirname(__file__), '..')
        script_path = os.path.join(repo_root, 'tools', 'portability_check.py')
        baseline_path = os.path.join(repo_root, '.portability-baseline.json')

        result = subprocess.run(
            [
                sys.executable, script_path,
                '--root', repo_root,
                '--baseline', baseline_path,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            "portability_check ratchet failed against committed baseline "
            "(new or stale private-token/path findings) -- mark legitimate "
            "'default'/'example'/'e.g.' lines or regenerate the baseline:\n"
            + result.stderr
        )


class TestPortabilityCheckImport(unittest.TestCase):
    """Test that portability_check can be imported."""

    def test_importable(self):
        """Test that the module can be imported."""
        script_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            'tools',
            'portability_check.py'
        )
        # Try to execute it as a script to verify no syntax errors
        result = subprocess.run(
            [sys.executable, script_path, '--help'],
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
