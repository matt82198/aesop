"""Web application with caching layer and data registry."""
from cache_decorator import cached
from registry import get_all_data
from loader import initialize_registry


@cached
def get_route_data():
    """Get data for the main route. This is cached."""
    data = get_all_data()
    return {"route": "main", "registry": data}


def setup_routes():
    """Set up application routes. Called at startup."""
    result = get_route_data()
    return result


def load_application():
    """Initialize the application in the correct order."""
    setup_routes()
    initialize_registry()


def get_route_uncached():
    """Get data for a route without caching."""
    data = get_all_data()
    return {"route": "main", "registry": data}
