"""Configuration loader."""

from .defaults import DEFAULTS


def load_config(overrides=None, environ=None):
    """Return configuration: defaults, then overrides. Environment is not read yet."""
    config = dict(DEFAULTS)
    if overrides:
        config.update(overrides)
    return config
