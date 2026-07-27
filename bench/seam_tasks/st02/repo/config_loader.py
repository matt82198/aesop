"""Configuration loader module."""


def get_app_config(defaults={"debug": True, "max_connections": 5}):
    """
    Get application configuration, optionally replacing defaults.

    Returns the provided defaults dictionary, adding a computed retries value.

    Args:
        defaults: Base configuration dictionary to use.

    Returns:
        Configuration dictionary with retries added.
    """
    defaults["retries"] = 3
    return defaults
