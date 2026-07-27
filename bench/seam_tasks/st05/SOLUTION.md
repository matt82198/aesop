# ST05: Config Default Override — Solution

## Defect Class
**Config plumbing: default override through validation layer**

## Hop Structure
- **Hop 0 (Symptom):** main.py calls process_items(100) expecting 100 items, receives 10
- **Hop 1:** processor.py calls validate_and_filter(items, 100) with the limit parameter
- **Hop 2 (Root cause):** validator.py receives max_items=100 but ignores it and uses config.DEFAULT_MAX_ITEMS (10)

The caller's explicit argument is silently overridden by a hardcoded default in the intermediate validation layer.

## Reference Fix

In `validator.py`, change line 19-22 from:
```python
if max_items is None:
    return items

limit = DEFAULT_MAX_ITEMS
return items[:limit]
```

To:
```python
if max_items is None:
    return items

return items[:max_items]
```

**Rationale:** The validator function accepts a `max_items` parameter but ignores it when not None, instead using the module-level DEFAULT_MAX_ITEMS constant. This causes callers' explicit limits to be silently replaced with the default configuration value, creating a 2-hop defect where the wrong behavior (capping at 10) surfaces in the processor output but the root cause (ignoring the parameter) lies in the validator module.

## Verification Transcript

### Before Fix
```
oracle\test_processor.py:18: AssertionError: Expected 25 items with limit=100, got 10
oracle\test_processor.py:23: AssertionError: Expected 5 items with limit=5, got 10
oracle\test_processor.py:28: AssertionError: Expected 3 items with limit=3, got 10
oracle\test_processor.py:39: AssertionError: Expected [1, 2, 3, 4, 5, 6, 7], got [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
oracle\test_processor.py:44: AssertionError: Expected 0 items with limit=0, got 10
=========================== 5 failed, 1 passed ===========================
```

### After Fix
Apply the reference fix to `repo/validator.py` line 22 (change `limit = DEFAULT_MAX_ITEMS` to `limit = max_items`).

Run `python -m pytest oracle -q`:
```
.......                                                                  [100%]
========================= 7 passed in 0.06s =========================
```

All 7 tests pass after applying the fix.
