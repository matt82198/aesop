# Solution: State Machine-Notifier Interaction Bug (st10)

## Defect Class
**State machine disagreement across modules**: One module (state_machine) defines valid transitions including a REQUEUED state, but another module (notifier) was built without handler support for that state, causing runtime failures when the workflow uses retry logic.

## Interaction Chain (Why localization requires 4+ modules)

1. **constants.py**: Defines workflow states and VALID_TRANSITIONS including REQUEUED
2. **state_machine.py**: Enforces state transitions according to VALID_TRANSITIONS
3. **notifier.py**: Sends notifications on state changes, but only handles a subset of states
4. **task_manager.py**: Orchestrates the workflow, calling both state_machine and notifier
5. **The Defect Interaction**:
   - constants.py defines QUEUED -> READY -> EXECUTING -> REQUEUED -> READY -> EXECUTING -> DONE as valid
   - state_machine.py correctly allows this transition sequence (validates against VALID_TRANSITIONS)
   - notifier.py defines NOTIFY_STATES that does NOT include REQUEUED
   - When task_manager.retry_task() transitions to REQUEUED and calls notifier.notify(), it crashes
   - The bug is only visible when testing the RETRY path (REQUEUED state)

**Why this requires 4+ modules to localize**:
- Can't find the bug by reading constants.py alone (transitions look correct)
- Can't find the bug by reading state_machine.py alone (it correctly validates transitions)
- Can't find the bug by reading notifier.py alone (NOTIFY_STATES selection might seem intentional)
- Can't find the bug by reading task_manager.py alone without understanding what states can exist
- The bug emerges from DISAGREEMENT between:
  - What constants.py says is valid (REQUEUED can transition)
  - What notifier.py is prepared to handle (REQUEUED is not in handlers)
  - How task_manager.py uses both (assuming state_machine allowed means notifier can handle)
- Localization requires reading all 4 modules to understand that the state is valid but not handled

## Fix

**Solution**: Add REQUEUED to NOTIFY_STATES and add a handler for it in the Notifier.

**File: constants.py** - Update NOTIFY_STATES:
```python
NOTIFY_STATES = {READY, EXECUTING, DONE, FAILED, REQUEUED}  # Add REQUEUED
```

**File: notifier.py** - Add handler:
```python
def _handle_requeued(self, task_id):
    return f"Task {task_id} requeued"

# In the handlers dict, add:
REQUEUED: self._handle_requeued,
```

Alternative simpler fix: In notifier.py, just skip notification for REQUEUED (if notifications aren't critical):
```python
def notify(self, task_id, state):
    """Send a notification for a state change."""
    # Skip notification for internal retry state
    if state == REQUEUED:
        return None
    
    if state not in NOTIFY_STATES:
        ...
```

## Verification Transcript

### Before Fix (Defective Code)

```
FAILED test_state_machine_notifier.py::test_task_with_retry - ValueError: Cannot notify for state: REQUEUED
FAILED test_state_machine_notifier.py::test_retry_task_method - ValueError: Cannot notify for state: REQUEUED
FAILED test_state_machine_notifier.py::test_notification_messages_for_all_states - ValueError: Cannot notify for state: REQUEUED
FAILED test_state_machine_notifier.py::test_multiple_tasks_with_retries - ValueError: Cannot notify for state: REQUEUED
1 passed, 4 failed in 0.08s
```

### After Fix (Applied to repo copy)

Changes applied:
1. constants.py: `NOTIFY_STATES = {READY, EXECUTING, DONE, FAILED, REQUEUED}`
2. notifier.py: Added `_handle_requeued()` method and handler mapping

Oracle Output:
```
test_state_machine_notifier.py::test_simple_task_workflow PASSED
test_state_machine_notifier.py::test_task_with_retry PASSED
test_state_machine_notifier.py::test_retry_task_method PASSED
test_state_machine_notifier.py::test_notification_messages_for_all_states PASSED
test_state_machine_notifier.py::test_multiple_tasks_with_retries PASSED
5 passed in 0.04s
```

## Summary

This bug requires tracing across state definition (constants), state validation (state_machine), event handling (notifier), and orchestration (task_manager). The state machine allows a transition that the notification system cannot handle, creating a multi-module interaction bug. Localization requires reading all 4 modules to understand that a valid state is not properly handled by dependent code.
