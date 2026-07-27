"""Notification system for task state changes."""
from constants import NOTIFY_STATES, READY, EXECUTING, DONE, FAILED


class Notifier:
    """Sends notifications when tasks change state."""

    def __init__(self):
        """Initialize notifier with message log."""
        self.messages = []

    def notify(self, task_id, state):
        """Send a notification for a state change."""
        if state not in NOTIFY_STATES:
            raise ValueError(f"Cannot notify for state: {state}")

        handlers = {
            READY: self._handle_ready,
            EXECUTING: self._handle_executing,
            DONE: self._handle_done,
            FAILED: self._handle_failed,
        }

        if state not in handlers:
            raise KeyError(f"No handler for state: {state}")

        message = handlers[state](task_id)
        self.messages.append(message)
        return message

    def _handle_ready(self, task_id):
        return f"Task {task_id} is ready"

    def _handle_executing(self, task_id):
        return f"Task {task_id} is executing"

    def _handle_done(self, task_id):
        return f"Task {task_id} completed"

    def _handle_failed(self, task_id):
        return f"Task {task_id} failed"

    def get_messages(self):
        """Get all notification messages."""
        return list(self.messages)
