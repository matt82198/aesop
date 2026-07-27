"""Test suite for plugin registration and event handling."""

import sys
import os

# Add repo to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'repo'))

from initializer import initialize
import event_registry


class TestPluginRegistration:
    """Tests for proper plugin registration and event dispatch."""

    def setup_method(self):
        """Reset registry before each test."""
        # Note: calling initialize() will clear registrations due to defect
        pass

    def test_both_handlers_registered(self):
        """Both plugins should register handlers at initialization."""
        initialize()

        # After initialization, both plugin handlers should be registered
        handler_count = event_registry.get_handlers("data_event")
        assert handler_count == 2, \
            f"Expected 2 handlers, got {handler_count}"

    def test_event_dispatch_calls_all_handlers(self):
        """Dispatching an event should call all registered handlers."""
        initialize()

        results = event_registry.dispatch("data_event", "test_message")

        # Both handlers should execute and return results
        assert len(results) == 2, \
            f"Expected 2 handler results, got {len(results)}"
        assert any("plugin_a_handled" in str(r) for r in results), \
            "Plugin A handler should be called"
        assert any("plugin_b_handled" in str(r) for r in results), \
            "Plugin B handler should be called"

    def test_handler_results_contain_plugin_names(self):
        """Each handler result should identify its source plugin."""
        initialize()

        results = event_registry.dispatch("data_event", "data123")

        result_strs = [str(r) for r in results]

        assert any("plugin_a" in r for r in result_strs), \
            "Results should identify plugin_a"
        assert any("plugin_b" in r for r in result_strs), \
            "Results should identify plugin_b"

    def test_data_passed_to_handlers(self):
        """Event data should be passed to all handlers."""
        initialize()

        test_data = "my_test_data"
        results = event_registry.dispatch("data_event", test_data)

        result_strs = [str(r) for r in results]

        # All results should contain the passed data
        assert all(test_data in r for r in result_strs), \
            f"All handlers should receive {test_data}"

    def test_multiple_dispatches_work(self):
        """Event dispatch should work multiple times."""
        initialize()

        # First dispatch
        results1 = event_registry.dispatch("data_event", "first")
        # Second dispatch
        results2 = event_registry.dispatch("data_event", "second")

        assert len(results1) == 2, f"First dispatch: expected 2 results, got {len(results1)}"
        assert len(results2) == 2, f"Second dispatch: expected 2 results, got {len(results2)}"
