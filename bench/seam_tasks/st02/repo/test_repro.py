"""Visible reproduction test for config loader."""
import pytest

from config_loader import get_app_config


class TestConfigLoaderRepro:
    """Visible test: user-provided config is not modified by the function."""

    def test_user_config_not_modified(self):
        """When a user provides a dict, it must not be modified by get_app_config."""
        user_config = {"setting": "value"}
        original_keys = set(user_config.keys())

        # Call get_app_config with the user config
        result = get_app_config(user_config)

        # The result includes retries added by the function
        assert "retries" in result

        # But the original user_config dict is unchanged
        assert set(user_config.keys()) == original_keys
        assert "retries" not in user_config
