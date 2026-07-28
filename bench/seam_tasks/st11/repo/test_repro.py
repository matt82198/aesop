"""Visible reproduction test for config-logger-validator interaction bug."""
import pytest
import sys
import os
import logging
from io import StringIO

sys.path.insert(0, os.path.dirname(__file__))


def test_validation_errors_visible_in_production():
    """Test that validation errors are logged visibly in production mode."""
    os.environ["APP_ENV"] = "production"

    from config import get_log_level
    from validator import validate_record
    from logger import get_logger

    assert get_log_level() == "ERROR", "Production should set log level to ERROR"

    log = get_logger("validator")

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log.handlers = [handler]

    result = validate_record({"id": 1})
    assert result is False, "Validation should fail for missing 'value' field"

    output = stream.getvalue()
    assert "value" in output.lower(), \
        f"Validation error should mention 'value' field in production logs, got: {repr(output)}"
    assert "ERROR" in output or "error" in output.lower(), \
        f"Validation error should appear at ERROR level in production, got: {repr(output)}"
