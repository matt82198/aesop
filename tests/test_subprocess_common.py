#!/usr/bin/env python3
"""Test suite for subprocess_common.py.

Tests:
  - Timeout is applied and distinguishable from non-zero exits
  - A timeout raises subprocess.TimeoutExpired (not indistinguishable from clean non-zero)
  - Malformed JSON parsing raises rather than returning empty
  - shell=True is never used (via inspection)
  - Encoding is always UTF-8
  - gh() and git() apply correct default timeouts
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import subprocess_common


class TestRunFunction(unittest.TestCase):
    """Test the run() function."""

    def test_run_success(self):
        """Test a successful command execution."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['echo', 'hello'],
                returncode=0,
                stdout='hello\n',
                stderr='',
            )
            result = subprocess_common.run(['echo', 'hello'], timeout=10)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, 'hello\n')

            # Verify run was called with correct parameters
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            self.assertFalse(call_kwargs.get('shell', False), "shell=True is forbidden")
            self.assertEqual(call_kwargs['encoding'], 'utf-8', "encoding must be utf-8")
            self.assertEqual(call_kwargs['timeout'], 10, "timeout must be passed through")

    def test_run_non_zero_exit_no_check(self):
        """Test non-zero exit with check=False (should not raise)."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['false'],
                returncode=1,
                stdout='',
                stderr='failure',
            )
            result = subprocess_common.run(['false'], check=False, timeout=10)
            self.assertEqual(result.returncode, 1)

    def test_run_non_zero_exit_with_check(self):
        """Test non-zero exit with check=True (should raise CalledProcessError)."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['false'],
                returncode=1,
                stdout='',
                stderr='failure',
            )
            with self.assertRaises(subprocess.CalledProcessError) as ctx:
                subprocess_common.run(['false'], check=True, timeout=10)
            self.assertEqual(ctx.exception.returncode, 1)

    def test_run_timeout_raises_timeout_expired(self):
        """Test that timeout raises subprocess.TimeoutExpired (distinguishable)."""
        with mock.patch('subprocess.run') as mock_run:
            # Simulate a timeout
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=['sleep', '100'],
                timeout=5,
                output='',
                stderr='',
            )
            with self.assertRaises(subprocess.TimeoutExpired) as ctx:
                subprocess_common.run(['sleep', '100'], timeout=5)
            self.assertEqual(ctx.exception.timeout, 5)

    def test_timeout_distinguishable_from_exit_code(self):
        """Test that timeout is NOT confused with exit code 124 (bash timeout exit)."""
        # A timeout exception is raised, not a CalledProcessError with returncode 124
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=['cmd'],
                timeout=1,
            )
            # Should raise TimeoutExpired, not CalledProcessError
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess_common.run(['cmd'], timeout=1, check=True)

    def test_run_file_not_found(self):
        """Test that missing command raises FileNotFoundError."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with self.assertRaises(FileNotFoundError) as ctx:
                subprocess_common.run(['nonexistent_cmd_xyz'], timeout=10)
            self.assertIn("nonexistent_cmd_xyz", str(ctx.exception))


class TestGhFunction(unittest.TestCase):
    """Test the gh() wrapper."""

    def test_gh_default_timeout(self):
        """Test that gh() applies default timeout of 30s."""
        with mock.patch('subprocess_common.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['gh', 'pr', 'list'],
                returncode=0,
                stdout='[]',
                stderr='',
            )
            subprocess_common.gh(['pr', 'list'])
            # Verify run was called with timeout=30
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args[1]['timeout'], 30)

    def test_gh_override_timeout(self):
        """Test that gh() allows override of timeout."""
        with mock.patch('subprocess_common.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['gh', 'pr', 'list'],
                returncode=0,
                stdout='[]',
                stderr='',
            )
            subprocess_common.gh(['pr', 'list'], timeout=60)
            # Verify run was called with overridden timeout
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args[1]['timeout'], 60)

    def test_gh_prepends_gh_command(self):
        """Test that gh() prepends 'gh' to the argument list."""
        with mock.patch('subprocess_common.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['gh', 'pr', 'view', '42'],
                returncode=0,
                stdout='{}',
                stderr='',
            )
            subprocess_common.gh(['pr', 'view', '42'])
            # Verify 'gh' was prepended
            call_args = mock_run.call_args[0][0]
            self.assertEqual(call_args[0], 'gh')
            self.assertEqual(call_args[1:], ['pr', 'view', '42'])


class TestGitFunction(unittest.TestCase):
    """Test the git() wrapper."""

    def test_git_default_timeout(self):
        """Test that git() applies default timeout of 60s."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['git', 'log'],
                returncode=0,
                stdout='abc123\n',
                stderr='',
            )
            subprocess_common.git(['log'])
            # Verify subprocess.run was called with timeout=60
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs['timeout'], 60)

    def test_git_override_timeout(self):
        """Test that git() allows override of timeout."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['git', 'log'],
                returncode=0,
                stdout='abc123\n',
                stderr='',
            )
            subprocess_common.git(['log'], timeout=30)
            # Verify timeout was overridden
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs['timeout'], 30)

    def test_git_with_cwd(self):
        """Test that git() accepts cwd parameter."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['git', 'log'],
                returncode=0,
                stdout='',
                stderr='',
            )
            subprocess_common.git(['log'], cwd='/path/to/repo')
            # Verify cwd was passed
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs['cwd'], '/path/to/repo')

    def test_git_never_shell(self):
        """Test that git() never uses shell=True."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['git', 'log'],
                returncode=0,
                stdout='',
                stderr='',
            )
            subprocess_common.git(['log'])
            call_kwargs = mock_run.call_args[1]
            self.assertFalse(call_kwargs.get('shell', False), "shell=True is forbidden")

    def test_git_encoding_utf8(self):
        """Test that git() always uses UTF-8 encoding."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=['git', 'log'],
                returncode=0,
                stdout='',
                stderr='',
            )
            subprocess_common.git(['log'])
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs['encoding'], 'utf-8')


class TestJsonOutput(unittest.TestCase):
    """Test the json_output() helper."""

    def test_json_output_valid_array(self):
        """Test parsing valid JSON array."""
        result = subprocess.CompletedProcess(
            args=['gh', 'pr', 'list', '--json', 'number'],
            returncode=0,
            stdout='[{"number": 1}, {"number": 2}]',
            stderr='',
        )
        data = subprocess_common.json_output(result)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['number'], 1)

    def test_json_output_valid_object(self):
        """Test parsing valid JSON object."""
        result = subprocess.CompletedProcess(
            args=['gh', 'pr', 'view', '42', '--json', 'state'],
            returncode=0,
            stdout='{"state": "MERGED"}',
            stderr='',
        )
        data = subprocess_common.json_output(result)
        self.assertEqual(data['state'], 'MERGED')

    def test_json_output_malformed_raises(self):
        """Test that malformed JSON raises json.JSONDecodeError."""
        result = subprocess.CompletedProcess(
            args=['cmd'],
            returncode=0,
            stdout='not valid json {]',
            stderr='',
        )
        with self.assertRaises(json.JSONDecodeError):
            subprocess_common.json_output(result)

    def test_json_output_empty_raises(self):
        """Test that empty stdout raises ValueError."""
        result = subprocess.CompletedProcess(
            args=['cmd'],
            returncode=0,
            stdout='',
            stderr='',
        )
        with self.assertRaises(ValueError) as ctx:
            subprocess_common.json_output(result)
        self.assertIn("empty stdout", str(ctx.exception))

    def test_json_output_whitespace_only_raises(self):
        """Test that whitespace-only stdout raises ValueError."""
        result = subprocess.CompletedProcess(
            args=['cmd'],
            returncode=0,
            stdout='   \n\t  ',
            stderr='',
        )
        with self.assertRaises(ValueError):
            subprocess_common.json_output(result)


class TestNoShellUsage(unittest.TestCase):
    """Test that shell=True is never used."""

    def test_run_never_uses_shell_true(self):
        """Verify run() never passes shell=True."""
        # Inspect the source code (excluding docstrings)
        import inspect
        source = inspect.getsource(subprocess_common.run)
        # Remove docstring from source
        lines = source.split('\n')
        # Find the actual code after the docstring
        in_docstring = False
        code_lines = []
        for line in lines:
            if '"""' in line:
                in_docstring = not in_docstring
            elif not in_docstring:
                code_lines.append(line)
        code = '\n'.join(code_lines)
        # Should not contain shell=True in actual code
        self.assertNotIn('shell=True', code, "shell=True is forbidden in run()")

    def test_git_never_uses_shell_true(self):
        """Verify git() never passes shell=True."""
        import inspect
        source = inspect.getsource(subprocess_common.git)
        self.assertNotIn('shell=True', source, "shell=True is forbidden in git()")


class TestTimeoutBoundary(unittest.TestCase):
    """Test timeout behavior at boundaries."""

    def test_timeout_zero_allowed(self):
        """Test that timeout=0 is technically allowed (though not useful)."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=['cmd'],
                timeout=0,
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess_common.run(['cmd'], timeout=0)

    def test_timeout_preserved_in_exception(self):
        """Test that timeout value is preserved in TimeoutExpired."""
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=['cmd'],
                timeout=42,
            )
            try:
                subprocess_common.run(['cmd'], timeout=42)
            except subprocess.TimeoutExpired as e:
                self.assertEqual(e.timeout, 42)


if __name__ == '__main__':
    unittest.main()
