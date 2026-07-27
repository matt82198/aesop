"""Registry data loader."""
import registry
from config import should_load_registry_on_startup, get_registry_source


def initialize_registry():
    """Load registry data based on configuration."""
    if should_load_registry_on_startup():
        source = get_registry_source()
        registry.load_data()


def is_registry_initialized():
    """Check if registry has been loaded."""
    return bool(registry.get_all_data())
