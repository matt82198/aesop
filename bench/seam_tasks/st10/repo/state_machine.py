"""State machine for task transitions."""
from constants import VALID_TRANSITIONS, QUEUED


class StateMachine:
    """Manages state transitions for tasks."""

    def __init__(self, initial_state=QUEUED):
        """Initialize with a starting state."""
        self.state = initial_state

    def transition(self, new_state):
        """Transition to a new state if valid."""
        if new_state not in VALID_TRANSITIONS.get(self.state, []):
            raise ValueError(
                f"Invalid transition from {self.state} to {new_state}"
            )
        self.state = new_state
        return self.state

    def get_state(self):
        """Get current state."""
        return self.state
