"""Oracle tests for config-logger-validator interaction bug."""
import pytest
import sys
import os
import logging
from io import StringIO
import subprocess

# Add repo to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "repo"))


def test_validator_detects_invalid_records():
    """Test that validator correctly identifies invalid records."""
    from validator import validate_record

    assert validate_record({"id": 1, "value": "test"}) is True
    assert validate_record({"id": 1}) is False
    assert validate_record({"value": "test"}) is False
    assert validate_record(None) is False


def test_logger_level_development():
    """Test that logger allows WARNING messages in development."""
    os.environ["APP_ENV"] = "development"
    from config import is_production, get_log_level

    assert is_production() is False
    assert get_log_level() == "DEBUG"


def test_logger_level_production():
    """Test that logger suppresses WARNING messages in production."""
    os.environ["APP_ENV"] = "production"
    from config import is_production, get_log_level

    assert is_production() is True
    assert get_log_level() == "ERROR"


def test_process_invalid_records_with_production_env():
    """
    Test that demonstrates the bug: validation errors are suppressed in production
    when logged at WARNING level (which is suppressed in production mode).

    This test runs in a subprocess to ensure clean environment isolation.
    The fix: change validator to log at ERROR level so errors appear in production.
    """
    # Create a test script that runs in production mode
    test_script = """
import os
os.environ["APP_ENV"] = "production"
import sys
sys.path.insert(0, "../repo")

from processor import process_records
from logger import get_logger
import logging
from io import StringIO

# Set up logging capture
log = get_logger("app")
stream = StringIO()
handler = logging.StreamHandler(stream)
handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
log.handlers = [handler]  # Replace all handlers

invalid_records = [
    {"id": 1},  # missing value
    {"value": "test"},  # missing id
]

valid_count, bad_records = process_records(invalid_records)

output = stream.getvalue()
print(f"Valid: {valid_count}")
print(f"Invalid: {len(bad_records)}")
print(f"Output: {repr(output)}")
print(f"Has validation error: {'required field' in output}")
"""

    test_file = os.path.join(
        os.path.dirname(__file__), "temp_prod_test.py"
    )
    try:
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(test_script)

        result = subprocess.run(
            [sys.executable, test_file],
            cwd=os.path.join(os.path.dirname(__file__), "..", "oracle"),
            capture_output=True,
            text=True,
        )

        output = result.stdout
        print(output)

        # The bug: validation error messages are suppressed in production
        # because they're logged at WARNING level which is filtered out in production
        # The fix: log validation errors at ERROR level so they appear in all environments
        # This assertion should fail on the defective code (no error output)
        # and pass on the fixed code (error output present)
        assert "Has validation error: True" in output, (
            f"Validation errors should appear in production logs. Got: {output}"
        )

    finally:
        if os.path.exists(test_file):
            os.remove(test_file)


def test_validation_errors_in_development():
    """Test that validator logs validation errors in development mode."""
    os.environ["APP_ENV"] = "development"

    # Fresh imports with development environment
    import importlib
    import logger as logger_mod
    import validator as val_mod

    importlib.reload(logger_mod)
    importlib.reload(val_mod)

    log = logger_mod.get_logger("test")

    # Capture output
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.handlers = [handler]

    # Call validator with invalid record
    val_mod.validate_record({"id": 1})  # missing value

    output = stream.getvalue()
    # In development, validation errors should appear
    assert "required field" in output, f"Expected validation error in development. Got: {output}"
    assert "value" in output, f"Expected 'value' in error message. Got: {output}"
