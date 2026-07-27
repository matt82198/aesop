"""Web application with caching layer and data registry."""
from cache_decorator import cached
from registry import load_data, get_all_data


@cached
def get_route_data():
    """Get data for the main route. This is cached."""
    data = get_all_data()
    return {"route": "main", "registry": data}


def setup_routes():
    """Set up application routes. Called at startup."""
    # Access the cached route handler early, before registry is loaded
    result = get_route_data()
    return result


def load_application():
    """Initialize the application in the correct order."""
    setup_routes()
    load_data()


def get_route_uncached():
    """Get data for a route without caching."""
    data = get_all_data()
    return {"route": "main", "registry": data}
