"""Configuration loader."""

import os

from .defaults import DEFAULTS

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_PREFIX = "APP_"


def _coerce(raw, default):
    """Convert an environment string to the type of default."""
    if isinstance(default, bool):  # check before int: bool is a subclass of int
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError("not a boolean: %r" % raw)
    if isinstance(default, int):
        return int(raw)
    return raw


def load_config(overrides=None, environ=None):
    """Return configuration: defaults, then APP_ environment, then overrides."""
    env = os.environ if environ is None else environ
    config = dict(DEFAULTS)
    for key, raw in env.items():
        if not key.startswith(_PREFIX):
            continue
        name = key[len(_PREFIX):].lower()
        if name in DEFAULTS:
            config[name] = _coerce(raw, DEFAULTS[name])
    if overrides:
        config.update(overrides)
    return config
