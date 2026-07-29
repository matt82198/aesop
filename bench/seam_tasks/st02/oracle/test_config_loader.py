"""Oracle tests for config loader."""
import pytest

from config_loader import get_app_config


class TestConfigLoader:
    """Tests for config loader."""

    def test_consecutive_calls_return_different_objects(self):
        """Consecutive calls should return different dictionary objects."""
        # First call
        config1 = get_app_config()
        config1_id = id(config1)

        # Second call should return a new dict, not the same object
        config2 = get_app_config()
        config2_id = id(config2)

        # The critical check: should be different objects
        assert config1_id != config2_id, "Should return different dict objects"

    def test_defaults_not_modified_across_calls(self):
        """Defaults should not accumulate changes across calls."""
        # First call gets the defaults
        config1 = get_app_config()
        assert "retries" in config1
        config1_keys = len(config1)

        # Second call should have same number of keys
        config2 = get_app_config()
        assert len(config2) == config1_keys

    def test_user_provided_defaults_not_modified(self):
        """When user provides defaults, should not modify the original."""
        user_defaults = {"debug": False, "max_connections": 10}
        original_keys = set(user_defaults.keys())

        config = get_app_config(user_defaults)

        # The returned config should have retries added
        assert "retries" in config
        # But the user-provided dict should not be modified
        assert set(user_defaults.keys()) == original_keys
        assert "retries" not in user_defaults

    def test_custom_defaults_returned(self):
        """Custom defaults passed in should be in returned config."""
        custom = {"debug": False, "timeout": 60}
        config = get_app_config(custom)

        assert config["debug"] is False
        assert config["timeout"] == 60
        assert "retries" in config

    def test_happy_path_returns_dict_with_retries(self):
        """Function should return a dict with debug, connections, and retries."""
        config = get_app_config()
        assert isinstance(config, dict)
        assert "debug" in config
        assert "max_connections" in config
        assert "retries" in config
