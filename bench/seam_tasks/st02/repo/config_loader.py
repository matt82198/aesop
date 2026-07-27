"""Configuration loader with mutable default argument bug."""


def get_app_config(defaults={"debug": True, "max_connections": 5}):
    """
    Get application configuration, optionally replacing defaults.

    Returns the provided defaults dictionary, adding a computed retries value.

    Args:
        defaults: Base configuration dictionary (BUG: mutable default argument).

    Returns:
        Configuration dictionary with retries added.
    """
    # BUG: The defaults parameter is a mutable dict that gets modified
    # Since it's a default argument, the same dict instance persists across calls
    # and accumulates changes from previous invocations
    defaults["retries"] = 3
    return defaults
