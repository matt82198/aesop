# st02 Solution: Mutable Default Argument Fix

## Defect Class
Mutable default argument

## Files
- `repo/config_loader.py` (single file, function signature and body)

## The Fix
**File:** `repo/config_loader.py`
**Function:** `get_app_config`
**Change:** Replace mutable dict default with None, create fresh dict on each call

```diff
-def get_app_config(defaults={"debug": True, "max_connections": 5}):
+def get_app_config(defaults=None):
     """
     Get application configuration, optionally replacing defaults.

     Returns the provided defaults dictionary, adding a computed retries value.

     Args:
-        defaults: Base configuration dictionary (BUG: mutable default argument).
+        defaults: Optional base configuration dictionary.

     Returns:
         Configuration dictionary with retries added.
     """
-    # BUG: The defaults parameter is a mutable dict that gets modified
-    # Since it's a default argument, the same dict instance persists across calls
-    # and accumulates changes from previous invocations
+    # FIX: Use None as default, then create a new dict if needed
+    if defaults is None:
+        defaults = {"debug": True, "max_connections": 5}
+    else:
+        # Make a copy so we don't modify the caller's dict
+        defaults = dict(defaults)
+
     defaults["retries"] = 3
     return defaults
```

## Rationale
This is a classic Python gotcha: using a mutable default argument (a dict or list) causes the same object to be reused across function calls. When the function modifies this object, the changes persist and affect all future calls. The fix uses None as the default sentinel and creates a fresh dict on each call. When a user provides their own dict, we create a shallow copy to avoid modifying the caller's dictionary.

## Notes on Fixture Code
The fixture code in `repo/config_loader.py` contains no comments explaining the defect — it reads like honest production code with a subtle bug that would be discovered through testing.

## Verification Transcript

### Before Fix (Buggy Code): Oracle FAILS
```
cd bench/seam_tasks/st02 && python -m pytest oracle -q

F.F..                                                                    [100%]
================================== FAILURES ===================================
______ TestConfigLoader.test_consecutive_calls_return_different_objects _______

    def test_consecutive_calls_return_different_objects(self):
        """Consecutive calls should return different dictionary objects."""
        # First call
        config1 = get_app_config()
        config1_id = id(config1)

        # Second call should return a new dict, not the same object
        config2 = get_app_config()
        config2_id = id(config2)

        # The critical check: should be different objects
>       assert config1_id != config2_id, "Should return different dict objects"
E       AssertionError: Should return different dict objects
E       assert 2105378625088 != 2105378625088

oracle\test_config_loader.py:21: AssertionError
__________ TestConfigLoader.test_user_provided_defaults_not_modified __________

    def test_user_provided_defaults_not_modified(self):
        """When user provides defaults, should not modify the original."""
        user_defaults = {"debug": False, "max_connections": 10}
        original_keys = set(user_defaults.keys())

        config = get_app_config(user_defaults)

        # The returned config should have retries added
        assert "retries" in config
        # But the user-provided dict should not be modified
>       assert set(user_defaults.keys()) == original_keys
E       AssertionError: assert {'debug', 'ma...s', 'retries'} == {'debug', 'max_connections'}
E         
E         Extra items in the left set:
E         'retries'
E         Use -v to get more diff

oracle\test_config_loader.py:44: AssertionError
=========================== short test summary info ===========================
FAILED oracle/test_config_loader.py::TestConfigLoader::test_consecutive_calls_return_different_objects
FAILED oracle/test_config_loader.py::TestConfigLoader::test_user_provided_defaults_not_modified
2 failed, 3 passed in 0.05s
```

### After Fix: Oracle PASSES
```
cd bench/seam_tasks/st02 && python -m pytest oracle -q

.....                                                                    [100%]
5 passed in 0.01s
```

## Oracle Tests
- Total count: 5 focused tests
- `test_consecutive_calls_return_different_objects`: Catches the core mutable default bug
- `test_defaults_not_modified_across_calls`: Verifies clean state between calls
- `test_user_provided_defaults_not_modified`: Ensures caller's dict isn't mutated
- `test_custom_defaults_returned`: Happy path with custom defaults
- `test_happy_path_returns_dict_with_retries`: Happy path with function defaults
