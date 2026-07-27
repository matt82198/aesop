"""System initializer - sets up registry and loads plugins."""

import event_registry

# Load plugins - they register their handlers at import time
import plugin_a
import plugin_b

# Initialize the registry for event handling
event_registry._initialize_registry()


def initialize():
    """Initialize the system (plugins already loaded by import above)."""
    pass
