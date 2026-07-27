"""Logging module."""
import logging
from config import get_log_level

# Module-level logger setup
_logger = None


def get_logger(name):
    """Get a configured logger for the given name."""
    global _logger
    if _logger is None:
        _logger = logging.getLogger("app")
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        _logger.addHandler(handler)

        # Set log level based on environment
        log_level = get_log_level()
        level = getattr(logging, log_level)
        _logger.setLevel(level)

    return _logger
