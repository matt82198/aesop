# ST08: Event Ordering Dependency — Solution

## Defect Class
**Initialization order breaks dependent registration—registry cleared after plugins register**

## Hop Structure
- **Hop 0 (Symptom):** main.py calls initialize(), then dispatch(), but no handlers execute (results list empty)
- **Hop 1:** initializer.py loads plugins, then calls _initialize_registry()
- **Hop 2 (Root cause):** plugins register handlers BEFORE _initialize_registry() is called, but _initialize_registry() clears the _handlers dict, losing all registrations

The handlers are registered at the right layer (plugins), but the initialization order in initializer.py causes them to be wiped out. The symptom surfaces in main.py as missing event handlers, but the root cause is the initialization timing in initializer.py.

## Reference Fix

In `initializer.py`, change the order from:
```python
import event_registry

# Load plugins - they register their handlers at import time
import plugin_a
import plugin_b

# DEFECT: Registry initialization happens AFTER plugins are imported.
event_registry._initialize_registry()
```

To:
```python
import event_registry

# Initialize registry FIRST, before plugins try to register
event_registry._initialize_registry()

# Now load plugins - they register their handlers at import time
import plugin_a
import plugin_b
```

**Rationale:** The plugins register their event handlers during module import (at the top level of plugin_a.py and plugin_b.py). The initializer.py file imports these plugins but then immediately calls `_initialize_registry()`, which clears the _handlers dict that the plugins just populated. This 2-3 hop defect manifests as missing event handlers in the output but the root cause is the reversed initialization order in the initializer module. Swapping the import and initialization calls fixes the defect.

## Verification Transcript

### Before Fix
```
oracle\test_plugin_registration.py:27: AssertionError: Expected 2 handlers, got 0
oracle\test_plugin_registration.py:37: AssertionError: Expected 2 handler results, got 0
oracle\test_plugin_registration.py:52: AssertionError: Results should identify plugin_a
oracle\test_plugin_registration.py:79: AssertionError: First dispatch: expected 2 results, got 0
=========================== 4 failed, 1 passed in 0.06s =========================
```

### After Fix
Apply the reference fix to `repo/initializer.py` (move `event_registry._initialize_registry()` to before the plugin imports).

Run `python -m pytest oracle -q`:
```
.....                                                                  [100%]
========================= 5 passed in 0.06s =========================
```

All 5 tests pass after applying the fix.
