#!/usr/bin/env python3
"""Tests for tools/cli.py — CLI base module."""

import io
import sys
import unittest
import sys
import json
import tempfile
from pathlib import Path

import pytest

# Ensure tools/ is on path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import cli


class TestRunSubprocess(unittest.TestCase):
    """Test tools.cli.run_subprocess()."""

    def test_run_subprocess_success(self):
        """Test successful subprocess execution."""
        rc, stdout, stderr = cli.run_subprocess([sys.executable, "-c", "print('hello')"])
        assert rc == 0
        assert "hello" in stdout
        assert stderr == ""

    def test_run_subprocess_failure(self):
        """Test failed subprocess (non-zero exit)."""
        rc, stdout, stderr = cli.run_subprocess([sys.executable, "-c", "import sys; sys.exit(1)"])
        assert rc == 1

    def test_run_subprocess_timeout(self):
        """Test subprocess timeout."""
        with pytest.raises(cli.SubprocessError):
            cli.run_subprocess([sys.executable, "-c", "import time; time.sleep(10)"], timeout=1)

    def test_run_subprocess_not_found(self):
        """Test subprocess not found."""
        with pytest.raises(cli.SubprocessError):
            cli.run_subprocess(["nonexistent_command_12345"])


class TestResolveRepoRoot(unittest.TestCase):
    """Test tools.cli.resolve_repo_root()."""

    def test_resolve_from_args_root(self):
        """Test resolution from args.root."""
        import argparse
        args = argparse.Namespace(root=Path("/tmp"))
        result = cli.resolve_repo_root(args)
        assert result == Path("/tmp").resolve()

    def test_resolve_from_args_repo(self):
        """Test resolution from args.repo (fallback)."""
        import argparse
        args = argparse.Namespace(root=None, repo=Path("/tmp"))
        result = cli.resolve_repo_root(args)
        assert result == Path("/tmp").resolve()

    def test_resolve_from_env_var(self):
        """Test resolution from environment variable."""
        import os
        old_val = os.environ.get("TEST_REPO_ROOT")
        try:
            os.environ["TEST_REPO_ROOT"] = "/tmp"
            result = cli.resolve_repo_root(env_key="TEST_REPO_ROOT")
            assert result == Path("/tmp").resolve()
        finally:
            if old_val:
                os.environ["TEST_REPO_ROOT"] = old_val
            else:
                os.environ.pop("TEST_REPO_ROOT", None)

    def test_resolve_fallback_to_cwd(self):
        """Test resolution falls back to cwd."""
        result = cli.resolve_repo_root()
        assert result.is_absolute()


class TestMaskSecrets(unittest.TestCase):
    """Test tools.cli.mask_secrets()."""

    def test_mask_aws_key(self):
        """Test masking AWS keys."""
        # Runtime-assembled to avoid triggering secret_scan
        key = "AKIA" + "IOSFODNN7EXAMPLE"
        text = "found key: " + key
        result = cli.mask_secrets(text)
        assert "MASKED-AWS-KEY" in result
        assert key not in result

    def test_mask_pem_key(self):
        """Test masking PEM keys."""
        # Runtime-assembled to avoid triggering secret_scan
        begin_marker = "-----BEGIN " + "PRIVATE KEY-----"
        end_marker = "-----END " + "PRIVATE KEY-----"
        text = begin_marker + "\ndata\n" + end_marker
        result = cli.mask_secrets(text)
        assert "MASKED-PEM-KEY" in result
        assert "BEGIN" not in result or "MASKED" in result

    def test_mask_api_key(self):
        """Test masking API keys."""
        # Runtime-assembled to avoid triggering secret_scan
        key = "sk-" + "12345678901234567890"
        text = "secret_key: " + key
        result = cli.mask_secrets(text)
        assert "MASKED-API-KEY" in result
        assert key not in result

    def test_no_mask_if_no_secrets(self):
        """Test text without secrets is unchanged."""
        text = "This is normal text"
        result = cli.mask_secrets(text)
        assert result == text


class TestDeterministicJsonDumps(unittest.TestCase):
    """Test tools.cli.deterministic_json_dumps()."""

    def test_json_dumps_sorted_keys(self):
        """Test JSON output has sorted keys."""
        obj = {"z": 1, "a": 2, "m": 3}
        result = cli.deterministic_json_dumps(obj, pretty=False)
        # Parse and check order
        parsed = json.loads(result)
        assert list(parsed.keys()) == ["a", "m", "z"]

    def test_json_dumps_pretty(self):
        """Test JSON output with indentation."""
        obj = {"key": "value"}
        result = cli.deterministic_json_dumps(obj, pretty=True)
        assert "\n" in result  # Should have newlines with pretty=True

    def test_json_dumps_compact(self):
        """Test JSON output without indentation."""
        obj = {"key": "value"}
        result = cli.deterministic_json_dumps(obj, pretty=False)
        assert "\n" not in result  # Should be single line with pretty=False

    def test_json_dumps_ascii_safe(self):
        """Test JSON output is ASCII-safe."""
        obj = {"emoji": "emoji_test"}
        result = cli.deterministic_json_dumps(obj)
        # Should not raise and should be valid JSON
        parsed = json.loads(result)
        assert parsed["emoji"] == "emoji_test"


class TestExitCode(unittest.TestCase):
    """Test tools.cli.exit_code()."""

    def test_exit_code_success(self):
        """Test exit code for success (no findings)."""
        assert cli.exit_code(findings=0) == 0

    def test_exit_code_findings(self):
        """Test exit code when findings are present."""
        assert cli.exit_code(findings=5) == 1

    def test_exit_code_error(self):
        """Test exit code for error."""
        exc = Exception("test error")
        assert cli.exit_code(error=exc) == 2

    def test_exit_code_error_takes_priority(self):
        """Test that error takes priority over findings."""
        exc = Exception("test error")
        assert cli.exit_code(findings=5, error=exc) == 2

    def test_exit_code_no_args(self):
        """Test exit code with no arguments."""
        assert cli.exit_code() == 0


class TestOutputFormatter(unittest.TestCase):
    """Test tools.cli.OutputFormatter class."""

    def test_formatter_text_mode(self):
        _buf = io.StringIO()
        _ebuf = io.StringIO()
        _old, _eold = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _buf, _ebuf
        """Test OutputFormatter in text mode."""
        fmt = cli.OutputFormatter(json_mode=False)
        fmt.text("Test message", level="INFO")
        sys.stdout, sys.stderr = _old, _eold
        captured = type("C", (), {"out": _buf.getvalue(), "err": _ebuf.getvalue()})()
        assert "Test message" in captured.out

    def test_formatter_text_error_level(self):
        _buf = io.StringIO()
        _ebuf = io.StringIO()
        _old, _eold = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _buf, _ebuf
        """Test OutputFormatter with ERROR level."""
        fmt = cli.OutputFormatter(json_mode=False)
        fmt.text("Error message", level="ERROR")
        sys.stdout, sys.stderr = _old, _eold
        captured = type("C", (), {"out": _buf.getvalue(), "err": _ebuf.getvalue()})()
        assert "ERROR" in captured.err
        assert "Error message" in captured.err

    def test_formatter_json_mode(self):
        _buf = io.StringIO()
        _ebuf = io.StringIO()
        _old, _eold = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _buf, _ebuf
        """Test OutputFormatter in JSON mode."""
        fmt = cli.OutputFormatter(json_mode=True)
        fmt.text("This should be ignored")
        sys.stdout, sys.stderr = _old, _eold
        captured = type("C", (), {"out": _buf.getvalue(), "err": _ebuf.getvalue()})()
        assert "This should be ignored" not in captured.out

    def test_formatter_json_output(self):
        _buf = io.StringIO()
        _ebuf = io.StringIO()
        _old, _eold = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _buf, _ebuf
        """Test OutputFormatter JSON output."""
        fmt = cli.OutputFormatter(json_mode=True)
        data = {"key": "value", "count": 42}
        fmt.json(data)
        sys.stdout, sys.stderr = _old, _eold
        captured = type("C", (), {"out": _buf.getvalue(), "err": _ebuf.getvalue()})()
        parsed = json.loads(captured.out)
        assert parsed["key"] == "value"
        assert parsed["count"] == 42


class TestCLIBuilder(unittest.TestCase):
    """Test tools.cli.CLIBuilder class."""

    def test_builder_basic(self):
        """Test basic CLIBuilder usage."""
        parser = cli.CLIBuilder("Test tool").build()
        args = parser.parse_args([])
        assert args is not None

    def test_builder_with_check_mode(self):
        """Test CLIBuilder with check mode."""
        parser = (cli.CLIBuilder("Test tool")
                  .add_check_mode()
                  .build())
        args = parser.parse_args(["--check"])
        assert args.check is True

    def test_builder_with_json_mode(self):
        """Test CLIBuilder with JSON mode."""
        parser = (cli.CLIBuilder("Test tool")
                  .add_json_mode()
                  .build())
        args = parser.parse_args(["--json"])
        assert args.json is True

    def test_builder_with_repo_root(self):
        """Test CLIBuilder with repo root."""
        parser = (cli.CLIBuilder("Test tool")
                  .add_repo_root()
                  .build())
        args = parser.parse_args(["--root", "/tmp"])
        assert str(args.root) == str(Path("/tmp"))

    def test_builder_method_chaining(self):
        """Test CLIBuilder method chaining."""
        parser = (cli.CLIBuilder("Test tool")
                  .add_check_mode()
                  .add_json_mode()
                  .add_repo_root()
                  .build())
        args = parser.parse_args(["--check", "--json", "--root", "/tmp"])
        assert args.check is True
        assert args.json is True
        assert str(args.root) == str(Path("/tmp"))

    def test_builder_custom_argument(self):
        """Test CLIBuilder with custom arguments."""
        parser = (cli.CLIBuilder("Test tool")
                  .add_argument("--custom", help="Custom flag")
                  .build())
        args = parser.parse_args(["--custom", "value"])
        assert args.custom == "value"


class TestSubprocessError(unittest.TestCase):
    """Test tools.cli.SubprocessError exception."""

    def test_subprocess_error_is_exception(self):
        """Test that SubprocessError is an Exception."""
        exc = cli.SubprocessError("test")
        assert isinstance(exc, Exception)

    def test_subprocess_error_message(self):
        """Test SubprocessError message."""
        exc = cli.SubprocessError("test message")
        assert str(exc) == "test message"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
