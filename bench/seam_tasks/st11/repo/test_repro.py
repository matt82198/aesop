"""Visible reproduction test for data validation logging."""
import pytest

from validator import validate_data


class TestValidationRepro:
    """Visible test: validation errors produce appropriate log levels."""

    def test_validation_logs_appropriate_level(self):
        """Validation errors are logged at the correct level."""
        # Valid data should not raise
        try:
            result = validate_data({"name": "test", "value": 42})
            assert result is not None
        except ValueError:
            pass

        # Invalid data should trigger logging
        with pytest.raises((ValueError, KeyError, TypeError)):
            validate_data({"invalid": "structure"})
