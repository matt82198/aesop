"""Task manager that orchestrates state transitions and notifications."""
from state_machine import StateMachine
from notifier import Notifier
from constants import QUEUED, READY, EXECUTING, DONE, FAILED, REQUEUED


class TaskManager:
    """Manages task lifecycle with state transitions and notifications."""

    def __init__(self):
        """Initialize task manager."""
        self.tasks = {}
        self.notifier = Notifier()

    def create_task(self, task_id):
        """Create a new task in QUEUED state."""
        self.tasks[task_id] = StateMachine(initial_state=QUEUED)

    def process_task(self, task_id):
        """Process a task through normal workflow: QUEUED -> READY -> EXECUTING -> DONE."""
        task = self.tasks[task_id]

        # Transition to READY
        task.transition(READY)
        self.notifier.notify(task_id, READY)

        # Transition to EXECUTING
        task.transition(EXECUTING)
        self.notifier.notify(task_id, EXECUTING)

        # Transition to DONE
        task.transition(DONE)
        self.notifier.notify(task_id, DONE)

    def retry_task(self, task_id):
        """Retry a failed or executing task."""
        task = self.tasks[task_id]

        # If executing, can go to REQUEUED
        if task.get_state() == EXECUTING:
            task.transition(REQUEUED)
            # This will fail because REQUEUED is not in NOTIFY_STATES
            self.notifier.notify(task_id, REQUEUED)

        # Then back to READY
        task.transition(READY)
        self.notifier.notify(task_id, READY)

    def get_task_state(self, task_id):
        """Get current state of a task."""
        return self.tasks[task_id].get_state()

    def get_notifications(self):
        """Get all notifications sent."""
        return self.notifier.get_messages()
