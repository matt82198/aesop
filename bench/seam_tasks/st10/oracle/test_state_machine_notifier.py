"""Oracle tests for state machine and notifier interaction bug."""
import pytest
import sys
import os

# Add repo to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "repo"))

from task_manager import TaskManager
from constants import QUEUED, READY, EXECUTING, DONE, FAILED, REQUEUED


@pytest.fixture
def manager():
    """Create a fresh task manager for each test."""
    return TaskManager()


def test_simple_task_workflow(manager):
    """Test that a simple task workflow works without retries."""
    manager.create_task("task1")
    manager.process_task("task1")

    assert manager.get_task_state("task1") == DONE
    messages = manager.get_notifications()
    assert len(messages) >= 3


def test_task_with_retry(manager):
    """
    Test that a task can be retried after execution.
    This exposes the bug: REQUEUED state is not handled by notifier.
    """
    manager.create_task("task2")

    # Start processing
    task = manager.tasks["task2"]
    task.transition(READY)
    manager.notifier.notify("task2", READY)

    task.transition(EXECUTING)
    manager.notifier.notify("task2", EXECUTING)

    # Now retry the task - this transitions to REQUEUED
    # The bug: notifier crashes when trying to notify REQUEUED state
    task.transition(REQUEUED)

    # This should work but currently fails
    manager.notifier.notify("task2", REQUEUED)

    # Complete the retry
    task.transition(READY)
    manager.notifier.notify("task2", READY)

    task.transition(EXECUTING)
    manager.notifier.notify("task2", EXECUTING)

    task.transition(DONE)
    manager.notifier.notify("task2", DONE)

    assert manager.get_task_state("task2") == DONE


def test_retry_task_method(manager):
    """Test the retry_task method which combines state and notification logic."""
    manager.create_task("task3")

    # Process normally until executing
    task = manager.tasks["task3"]
    task.transition(READY)
    manager.notifier.notify("task3", READY)

    task.transition(EXECUTING)
    manager.notifier.notify("task3", EXECUTING)

    # Now retry - this should transition to REQUEUED then back to READY
    # The bug triggers here when trying to notify REQUEUED
    manager.retry_task("task3")

    assert manager.get_task_state("task3") == READY


def test_notification_messages_for_all_states(manager):
    """Test that notifications are sent for all workflow states including REQUEUED."""
    manager.create_task("task4")

    task = manager.tasks["task4"]

    # Manually step through all states and verify notifications
    states_to_test = [READY, EXECUTING, REQUEUED, READY, EXECUTING, DONE]
    task_state = QUEUED

    for next_state in states_to_test:
        task.transition(next_state)
        # This should work for all states
        manager.notifier.notify("task4", next_state)

    # Should have notifications for all transitions
    messages = manager.get_notifications()
    assert len(messages) == len(states_to_test)


def test_multiple_tasks_with_retries(manager):
    """Test multiple tasks going through different paths."""
    # Task 1: simple path (no retry)
    manager.create_task("task5")
    manager.process_task("task5")

    # Task 2: with retry
    manager.create_task("task6")
    task6 = manager.tasks["task6"]
    task6.transition(READY)
    manager.notifier.notify("task6", READY)
    task6.transition(EXECUTING)
    manager.notifier.notify("task6", EXECUTING)
    task6.transition(REQUEUED)
    manager.notifier.notify("task6", REQUEUED)
    task6.transition(READY)
    manager.notifier.notify("task6", READY)
    task6.transition(EXECUTING)
    manager.notifier.notify("task6", EXECUTING)
    task6.transition(DONE)
    manager.notifier.notify("task6", DONE)

    # Both should complete successfully
    assert manager.get_task_state("task5") == DONE
    assert manager.get_task_state("task6") == DONE
