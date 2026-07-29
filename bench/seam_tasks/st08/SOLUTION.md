# ST08: Event Ordering Dependency — Solution

## Defect Class
**Initialization order breaks dependent registration—registry cleared after plugins register**

## Hop Structure
- **Hop 0 (Symptom):** main.py calls initialize(), then dispatch(), but no handlers execute (results list empty)
- **Hop 1:** initializer.py loads plugins, then calls _initialize_registry()
- **Hop 2 (Root cause):** plugins register handlers BEFORE _initialize_registry() is called, but _initialize_registry() clears the _handlers dict, losing all registrations

The handlers are registered at the right layer (plugins), but the initialization order in initializer.py causes them to be wiped out. The symptom surfaces in main.py as missing event handlers, but the root cause is the initialization timing in initializer.py.

## Reference Fix

In `initializer.py`, move the `event_registry._initialize_registry()` call to BEFORE the plugin imports. 

Change from:
```python
import event_registry
import plugin_a
import plugin_b
event_registry._initialize_registry()
```

To:
```python
import event_registry
event_registry._initialize_registry()
import plugin_a
import plugin_b
```

**Rationale:** The plugins register their event handlers during module import (at the top level of plugin_a.py and plugin_b.py). The initializer.py file imports these plugins and then immediately calls `_initialize_registry()`, which clears the _handlers dict that the plugins just populated. This 2-3 hop defect manifests as missing event handlers in the output but the root cause is the reversed initialization order in the initializer module. Swapping the order so initialization happens first allows plugin registration to succeed.

## Verification Transcript

### Before Fix
```
FFF.F                                                                    [100%]
================================== FAILURES ===================================
test_both_handlers_registered: Expected 2 handlers, got 0
test_event_dispatch_calls_all_handlers: Expected 2 handler results, got 0
test_handler_results_contain_plugin_names: Results should identify plugin_a
test_multiple_dispatches_work: First dispatch: expected 2 results, got 0
=========================== 4 failed, 1 passed in 0.06s =========================
```

### After Fix
Apply the reference fix to `repo/initializer.py` (move `event_registry._initialize_registry()` to line 6, before the plugin imports).

Run `python -m pytest oracle -q`:
```
.....                                                                  [100%]
========================= 5 passed in 0.01s =========================
```

All 5 tests pass after applying the fix.

## Visible Repro Test

### Test Assertions
The visible test `repo/test_repro.py` encodes the observable symptom:
- both plugins are registered during initialization

### Fail Output (Defective Code)
```
cd bench/seam_tasks/st08/repo && python -m pytest test_repro.py -q

F...                                                                     [100%]
...
only one or no handlers registered
...
1 failed, 0+ passed in X.XXs
```

### Pass Output (Fixed Code)
```
cd bench/seam_tasks/st08/repo && python -m pytest test_repro.py -q

...                                                                      [100%]
1+ passed in 0.XXs
```

### Distinction from Oracle
The visible test is simpler and more focused than the oracle suite:
- Visible: Minimal test demonstrating the observable symptom
- Oracle: Comprehensive tests covering edge cases and multiple scenarios
- Visible test encodes only what the task statement describes; oracle is thorough verification
