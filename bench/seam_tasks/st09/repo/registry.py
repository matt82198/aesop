"""Central data registry loaded during application startup."""

_data = {}


def load_data():
    """Load data into the registry. Called after application initialization."""
    global _data
    _data = {
        "users": ["alice", "bob", "charlie"],
        "settings": {
            "debug": False,
            "timeout": 30,
        },
    }


def get_all_data():
    """Get all data from the registry."""
    return dict(_data)


def get_users():
    """Get list of users from the registry."""
    return _data.get("users", [])
