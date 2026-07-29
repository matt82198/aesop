"""Global event registry for handler registration and dispatch."""

# Global handlers dict
_handlers = {}

# Registration counter - incremented when registry is reset/cleared
_registration_generation = 0


def _initialize_registry():
    """Initialize/reset the registry. Clears any previous handlers."""
    global _handlers, _registration_generation
    _handlers = {}
    _registration_generation += 1


def register(event_name, handler_func):
    """
    Register a handler function for an event.

    Args:
        event_name: Name of the event to handle
        handler_func: Callable that handles the event
    """
    if event_name not in _handlers:
        _handlers[event_name] = []
    _handlers[event_name].append(handler_func)


def dispatch(event_name, *args, **kwargs):
    """
    Dispatch an event to all registered handlers.

    Args:
        event_name: Name of the event to dispatch
        *args: Arguments to pass to handlers
        **kwargs: Keyword arguments to pass to handlers

    Returns:
        List of return values from all handlers
    """
    if event_name not in _handlers:
        return []

    results = []
    for handler in _handlers[event_name]:
        result = handler(*args, **kwargs)
        results.append(result)
    return results


def get_handlers(event_name):
    """Get count of handlers registered for an event."""
    return len(_handlers.get(event_name, []))
