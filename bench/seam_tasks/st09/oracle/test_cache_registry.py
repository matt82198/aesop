"""Oracle tests for cache decorator and registry interaction bug."""
import pytest
import sys
import os

# Add repo directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "repo"))

from app import load_application, get_route_data, get_route_uncached
from cache_decorator import cached
import registry
from registry import load_data, get_users, get_all_data


@pytest.fixture(autouse=True)
def reset_state():
    """Reset registry and cache state before each test."""
    get_route_data.clear_cache()
    registry._data = {}
    yield
    # Cleanup after test
    registry._data = {}
    get_route_data.clear_cache()


def test_happy_path_correct_order():
    """Test that loading data BEFORE accessing cached route works correctly."""
    # Reset everything
    get_route_data.clear_cache()

    # Load data first
    load_data()

    # Now access the route
    result = get_route_data()

    # Registry should be populated
    assert result["registry"]["users"] == ["alice", "bob", "charlie"]
    assert result["registry"]["settings"]["timeout"] == 30


def test_uncached_route_always_returns_current_data():
    """Test that uncached route shows current registry state."""
    # Clear cache
    get_route_data.clear_cache()

    # Before loading data
    result_before = get_route_uncached()
    assert result_before["registry"] == {}

    # Load data
    load_data()

    # Uncached route should show new data
    result_after = get_route_uncached()
    assert result_after["registry"]["users"] == ["alice", "bob", "charlie"]


def test_get_users_direct_access():
    """Test direct access to registry after loading."""
    load_data()
    users = get_users()
    assert users == ["alice", "bob", "charlie"]


def test_defect_cached_before_load():
    """
    Test that demonstrates the defect: accessing cached route before
    loading data causes it to return stale cached data.
    """
    # Reset
    get_route_data.clear_cache()

    # Call setup_routes which accesses cached function before loading
    load_application()

    # After setup_routes called but now that load_data has been called,
    # the cached route should still return fresh data (the fix)
    result = get_route_data()

    # The bug: result["registry"] is empty because cache was set before load_data
    # The fix should make this assertion pass
    assert result["registry"]["users"] == ["alice", "bob", "charlie"]
    assert result["registry"]["settings"]["timeout"] == 30


def test_cache_consistency_after_load():
    """
    Test that cached results are consistent with registry after load.
    """
    get_route_data.clear_cache()

    # Simulate startup: setup_routes called first
    setup_routes_result = None
    try:
        from app import setup_routes
        setup_routes_result = setup_routes()
    except:
        pass

    # Then load data
    load_data()

    # Cached route must return populated data
    result = get_route_data()
    assert "users" in result["registry"]
    assert len(result["registry"]["users"]) == 3


def test_multiple_accesses_consistent():
    """
    Test that multiple cached accesses return consistent data.
    """
    get_route_data.clear_cache()
    load_data()

    result1 = get_route_data()
    result2 = get_route_data()

    assert result1 == result2
    assert result1["registry"]["users"] == ["alice", "bob", "charlie"]
