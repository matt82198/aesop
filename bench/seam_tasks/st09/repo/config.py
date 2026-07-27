"""Application configuration."""


def should_load_registry_on_startup():
    """Whether to load registry during application initialization."""
    return True


def get_registry_source():
    """Get the source file or location for registry data."""
    return "default_data"
