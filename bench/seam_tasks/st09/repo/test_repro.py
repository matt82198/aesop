"""Visible reproduction test for cache-registry initialization order bug."""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import load_application, get_route_data, get_route_uncached
from cache_decorator import cached
import registry


@pytest.fixture(autouse=True)
def reset_state():
    """Reset registry and cache state before each test."""
    get_route_data.clear_cache()
    registry._data = {}
    yield
    registry._data = {}
    get_route_data.clear_cache()


def test_cached_route_returns_current_data_after_load():
    """Test that demonstrates the defect: cached route returns stale data after load_application."""
    get_route_data.clear_cache()

    call_result = load_application()

    result = get_route_data()

    registry_data = result.get("registry", {})
    assert "users" in registry_data and len(registry_data) > 0, \
        f"Cached route should return current data after load_application, got: {registry_data}"
    assert registry_data["users"] == ["alice", "bob", "charlie"]
    assert registry_data["settings"]["timeout"] == 30
