"""Visible reproduction test for task workflow transitions."""
import pytest

from workflow import Task


class TestWorkflowRepro:
    """Visible test: tasks transition through workflow states correctly."""

    def test_task_transitions_states(self):
        """A new task starts as QUEUED and transitions to READY when processed."""
        task = Task("sample_task")

        # Initial state is QUEUED
        assert task.status == "QUEUED"

        # After processing, status changes
        task.process()
        assert task.status != "QUEUED"
        assert task.status in ["READY", "PROCESSING", "COMPLETED"]
