"""Workflow state constants."""

# Task states
QUEUED = "QUEUED"
READY = "READY"
EXECUTING = "EXECUTING"
DONE = "DONE"
REQUEUED = "REQUEUED"
FAILED = "FAILED"

# Valid transitions defined in state machine
VALID_TRANSITIONS = {
    QUEUED: [READY],
    READY: [EXECUTING],
    EXECUTING: [DONE, REQUEUED, FAILED],
    REQUEUED: [READY],
    FAILED: [REQUEUED],
    DONE: [],
}

# States that require notification
NOTIFY_STATES = {READY, EXECUTING, DONE, FAILED}
