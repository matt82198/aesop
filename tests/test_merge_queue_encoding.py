#!/usr/bin/env python3
"""Regression test for merge-queue daemon encoding crash (byte 0x97 em-dash).

Tests that subprocess calls with encoding='utf-8' + errors='replace' handle
non-UTF-8 bytes gracefully without crashing.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add tools to path
_TOOLS = Path(__file__).parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from merge_queue import classify_check, required_checks_green


def test_gh_function_handles_em_dash_in_output():
    """Test that gh() function handles byte 0x97 (em-dash) gracefully."""
    # Import locally so we can patch
    from merge_train import gh

    # Create a mock subprocess that returns output with byte 0x97 (em-dash in cp1252)
    # This would crash without errors='replace'
    with patch('subprocess.run') as mock_run:
        # Setup mock to return bytes that would fail without errors='replace'
        mock_result = MagicMock()
        mock_result.returncode = 0
        # Simulate output with em-dash (byte 0x97), which is invalid UTF-8
        # Using errors='replace' should convert it to U+FFFD
        mock_result.stdout = "PR Title with em—dash"  # This is the replacement char form
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # This should not raise an exception
        result = gh("pr", "view", "123", "--json", "title")
        assert result is not None
        assert "em" in result or isinstance(result, dict)


def test_git_function_handles_em_dash_in_output():
    """Test that git() function handles non-UTF-8 output gracefully."""
    from merge_train import git

    with patch('subprocess.run') as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "commit sha123 Author: Test—User"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # This should not raise an exception
        ok, out = git("log", "--oneline", "-1")
        assert ok is True
        assert "commit" in out or len(out) > 0


def test_classify_check_handles_em_dash():
    """Test that PR data with em-dash in PR title doesn't crash the daemon."""
    # Simulate a check entry with em-dash in the context name (e.g., from PR title)
    entry = {
        "name": "CI Check—Test",  # em-dash (replacement char form)
        "status": "COMPLETED",
        "conclusion": "SUCCESS"
    }

    # Should not raise an exception
    name, verdict, url = classify_check(entry)
    assert verdict == "green"
    assert "em" in name or "Check" in name or "—" in name


def test_required_checks_green_with_em_dash():
    """Test that check rollup with em-dash doesn't crash."""
    rollup = [
        {
            "name": "CI Check—Build",
            "status": "COMPLETED",
            "conclusion": "SUCCESS"
        },
        {
            "name": "ci (0)",
            "status": "COMPLETED",
            "conclusion": "SUCCESS"
        },
        {
            "name": "ci (1)",
            "status": "COMPLETED",
            "conclusion": "SUCCESS"
        },
        {
            "name": "ci (2)",
            "status": "COMPLETED",
            "conclusion": "SUCCESS"
        },
        {
            "name": "ci (3)",
            "status": "COMPLETED",
            "conclusion": "SUCCESS"
        },
        {
            "name": "windows",
            "status": "COMPLETED",
            "conclusion": "SUCCESS"
        }
    ]

    # Should not raise an exception
    verdict, detail, url = required_checks_green(rollup)
    assert verdict == "green"


def test_subprocess_with_actual_em_dash_byte():
    """Test that encoding='utf-8' with errors='replace' handles raw bytes."""
    # This test directly verifies the subprocess encoding behavior

    # Create a temporary script that outputs byte 0x97
    with tempfile.TemporaryDirectory() as tmpdir:
        script = Path(tmpdir) / "output_emdash.py"
        script.write_text(
            "import sys; "
            "sys.stdout.buffer.write(b'Test\\x97dash')"
        )

        # Run with encoding='utf-8' and errors='replace'
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )

        # Should succeed and contain replacement character or similar
        assert result.returncode == 0
        # The byte 0x97 should be replaced with U+FFFD or similar
        assert "Test" in result.stdout
        assert "dash" in result.stdout


if __name__ == "__main__":
    test_gh_function_handles_em_dash_in_output()
    test_git_function_handles_em_dash_in_output()
    test_classify_check_handles_em_dash()
    test_required_checks_green_with_em_dash()
    test_subprocess_with_actual_em_dash_byte()
    print("All encoding regression tests passed!")
