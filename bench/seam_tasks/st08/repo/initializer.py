"""System initializer - sets up registry and loads plugins."""

import event_registry

# Load plugins - they register their handlers at import time
import plugin_a
import plugin_b

# DEFECT: Registry initialization happens AFTER plugins are imported.
# This clears the handlers that the plugins just registered!
# The correct order should be: initialize registry FIRST, then import plugins.
event_registry._initialize_registry()


def initialize():
    """Initialize the system (plugins already loaded by import above)."""
    pass
