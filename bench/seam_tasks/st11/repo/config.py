"""Application configuration."""
import os


def get_environment():
    """Get current environment (dev or production)."""
    return os.environ.get("APP_ENV", "production")


def is_production():
    """Check if running in production mode."""
    return get_environment() == "production"


def get_log_level():
    """
    Get the log level for the current environment.
    This is an individually sensible default: suppress debug/info/warning noise in production.
    """
    if is_production():
        return "ERROR"  # Only show errors in production (suppress info/warnings)
    else:
        return "DEBUG"  # Show everything in development
