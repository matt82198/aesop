"""Visible reproduction test for task retry and notification bug."""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from task_manager import TaskManager
from constants import QUEUED, READY, EXECUTING, REQUEUED


def test_retry_task_notifies_successfully():
    """Test that retrying a task sends notifications without crashing."""
    manager = TaskManager()
    manager.create_task("task1")

    task = manager.tasks["task1"]

    task.transition(READY)
    manager.notifier.notify("task1", READY)

    task.transition(EXECUTING)
    manager.notifier.notify("task1", EXECUTING)

    task.transition(REQUEUED)
    try:
        manager.notifier.notify("task1", REQUEUED)
    except ValueError as e:
        pytest.fail(f"Notifier should handle REQUEUED state, but raised: {e}")

    messages = manager.get_notifications()
    assert len(messages) >= 3, f"Should have at least 3 notification messages, got {len(messages)}"
