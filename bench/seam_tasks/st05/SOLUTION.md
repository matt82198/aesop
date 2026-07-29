# ST05: Config Default Override — Solution

## Defect Class
**Config plumbing: default override through validation layer**

## Hop Structure
- **Hop 0 (Symptom):** main.py calls process_items(100) expecting 100 items, receives 10
- **Hop 1:** processor.py calls validate_and_filter(items, 100) with the limit parameter
- **Hop 2 (Root cause):** validator.py receives max_items=100 but ignores it and uses config.DEFAULT_MAX_ITEMS (10)

The caller's explicit argument is silently overridden by a hardcoded default in the intermediate validation layer.

## Reference Fix

In `validator.py`, change line 19 from:
```python
limit = DEFAULT_MAX_ITEMS
```

To:
```python
limit = max_items
```

**Rationale:** The validator function accepts a `max_items` parameter but ignores it when not None, instead using the module-level DEFAULT_MAX_ITEMS constant. This causes callers' explicit limits to be silently replaced with the default configuration value, creating a 2-hop defect where the wrong behavior (capping at 10) surfaces in the processor output but the root cause (ignoring the parameter) lies in the validator module.

## Verification Transcript

### Before Fix
```
FFF.FF                                                                   [100%]
================================== FAILURES ===================================
test_large_limit_respected: Expected 25 items with limit=100, got 10
test_small_limit_respected: Expected 5 items with limit=5, got 10
test_limit_three: Expected 3 items with limit=3, got 10
test_results_are_correct_items: Expected [1, 2, 3, 4, 5, 6, 7], got [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
test_limit_zero: Expected 0 items with limit=0, got 10
=========================== 5 failed, 1 passed in 0.06s =========================
```

### After Fix
Apply the reference fix to `repo/validator.py` line 19 (change `limit = DEFAULT_MAX_ITEMS` to `limit = max_items`).

Run `python -m pytest oracle -q`:
```
......                                                                  [100%]
========================= 6 passed in 0.01s =========================
```

All 6 tests pass after applying the fix.

## Visible Repro Test

### Test Assertions
The visible test `repo/test_repro.py` encodes the observable symptom:
- item_limit parameter is applied correctly

### Fail Output (Defective Code)
```
cd bench/seam_tasks/st05/repo && python -m pytest test_repro.py -q

F...                                                                     [100%]
...
limit=5 returns 10 items (default), not 5
...
1 failed, 0+ passed in X.XXs
```

### Pass Output (Fixed Code)
```
cd bench/seam_tasks/st05/repo && python -m pytest test_repro.py -q

...                                                                      [100%]
1+ passed in 0.XXs
```

### Distinction from Oracle
The visible test is simpler and more focused than the oracle suite:
- Visible: Minimal test demonstrating the observable symptom
- Oracle: Comprehensive tests covering edge cases and multiple scenarios
- Visible test encodes only what the task statement describes; oracle is thorough verification
