# st04 Solution: Pagination Merge Precedence Fix

## Defect Class
Bad dict-merge precedence (backwards update order)

## Files
- `repo/paginator.py` (single file, two functions with the same bug)

## The Fix
**File:** `repo/paginator.py`
**Functions:** `get_pagination_params` and `apply_pagination_defaults`
**Change:** Reverse the merge order from `user.update(defaults)` to `defaults.update(user)`

```diff
def get_pagination_params(user_params):
    defaults = {"page": 1, "page_size": 10, "sort_by": "created"}

-   # BUG: The merge order is backwards
-   user_params.update(defaults)
+   # FIX: Update defaults with user_params so user values take precedence
+   defaults.update(user_params)
    return defaults

def apply_pagination_defaults(request_params):
    defaults = {"limit": 20, "offset": 0}

-   # BUG: Wrong merge order
-   request_params.update(defaults)
+   # FIX: Update defaults with request_params so user input takes precedence
+   defaults.update(request_params)
    return defaults
```

## Rationale
When merging dictionaries, the merge order determines precedence. In Python, `dict.update()` applies the argument to the caller, so the pattern should be to start with defaults and update them with user values to give user values precedence. The buggy code reverses this: it updates the user-provided dict with defaults, causing all default values to overwrite user-provided ones. The fix is to update the defaults dict with user values instead, so user-provided parameters take precedence over sensible defaults.

## Notes on Fixture Code
The fixture code in `repo/paginator.py` contains no comments explaining the defect — it reads like honest production code with a subtle logic error in the merge sequence that would be discovered through testing.

## Verification Transcript

### Before Fix (Buggy Code): Oracle FAILS
```
cd bench/seam_tasks/st04 && python -m pytest oracle -q

FFFF.FF                                                                  [100%]
================================== FAILURES ===================================
_________________ TestPaginator.test_user_page_size_respected _________________

    def test_user_page_size_respected(self):
        """User-provided page_size should override the default."""
        user_params = {"page": 2, "page_size": 50}
        result = get_pagination_params(user_params)
>       assert result["page_size"] == 50
E       assert 10 == 50

oracle\test_paginator.py:15: AssertionError
__________________ TestPaginator.test_user_sort_by_respected __________________

    def test_user_sort_by_respected(self):
        """User-provided sort_by should override the default."""
        user_params = {"sort_by": "updated"}
        result = get_pagination_params(user_params)
>       assert result["sort_by"] == "updated"
E       AssertionError: assert 'created' == 'updated'

oracle\test_paginator.py:23: AssertionError
_________ TestPaginator.test_partial_user_params_merged_with_defaults _________

    def test_partial_user_params_merged_with_defaults(self):
        """Partial user params should be merged with defaults."""
        user_params = {"page": 3}
        result = get_pagination_params(user_params)
>       assert result["page"] == 3
E       assert 1 == 3

oracle\test_paginator.py:30: AssertionError
_________________ TestPaginator.test_request_limit_respected __________________

    def test_request_limit_respected(self):
        """User-provided limit should override default."""
        request_params = {"limit": 50, "offset": 10}
        result = apply_pagination_defaults(request_params)
>       assert result["limit"] == 50
E       assert 20 == 50

oracle\test_paginator.py:41: AssertionError
________________ TestPaginator.test_all_user_params_respected _________________

    def test_all_user_params_respected(self):
        """All user-provided params should be respected."""
        user_params = {"page": 5, "page_size": 25, "sort_by": "name"}
        result = get_pagination_params(user_params)
>       assert result["page"] == 5
E       assert 1 == 5

oracle\test_paginator.py:56: AssertionError
_________________ TestPaginator.test_request_offset_respected _________________

    def test_request_offset_respected(self):
        """User-provided offset should override default."""
        request_params = {"offset": 100}
        result = apply_pagination_defaults(request_params)
>       assert result["offset"] == 100
E       assert 0 == 100

oracle\test_paginator.py:64: AssertionError
=========================== short test summary info ===========================
FAILED oracle/test_paginator.py::TestPaginator::test_user_page_size_respected
FAILED oracle/test_paginator.py::TestPaginator::test_user_sort_by_respected
FAILED oracle/test_paginator.py::TestPaginator::test_partial_user_params_merged_with_defaults
FAILED oracle/test_paginator.py::TestPaginator::test_request_limit_respected
FAILED oracle/test_paginator.py::TestPaginator::test_all_user_params_respected
FAILED oracle/test_paginator.py::TestPaginator::test_request_offset_respected
6 failed, 1 passed in 0.07s
```

### After Fix: Oracle PASSES
```
cd bench/seam_tasks/st04 && python -m pytest oracle -q

.......                                                                  [100%]
7 passed in 0.01s
```

## Oracle Tests
- Total count: 7 focused tests
- `test_user_page_size_respected`: Catches the merge order bug (user values overwritten)
- `test_user_sort_by_respected`: Catches the merge order bug (user values overwritten)
- `test_partial_user_params_merged_with_defaults`: Verifies partial params merge correctly
- `test_request_limit_respected`: Catches the merge order bug in apply_pagination_defaults
- `test_defaults_applied_to_empty_params`: Happy path with no user params
- `test_all_user_params_respected`: Verifies all user params are preserved
- `test_request_offset_respected`: Catches the merge order bug (user values overwritten)

## Visible Repro Test

### Test Assertions
The visible test `repo/test_repro.py` contains two focused assertions:
- User-provided page_size overrides default page_size
- User-provided page overrides default page

### Fail Output (Defective Code)
```
cd bench/seam_tasks/st04/repo && python -m pytest test_repro.py -q

FF                                                                       [100%]
================================== FAILURES ===================================
__________ TestPaginatorRepro.test_user_page_size_overrides_default ___________

    def test_user_page_size_overrides_default(self):
        user_params = {"page_size": 50}
        result = get_pagination_params(user_params)
>       assert result["page_size"] == 50
E       assert 10 == 50

test_repro.py:15: AssertionError
```

### Pass Output (Fixed Code)
```
cd bench/seam_tasks/st04/repo && python -m pytest test_repro.py -q

..                                                                       [100%]
2 passed in 0.01s
```

### Distinction from Oracle
The visible test is simpler and more focused than the oracle suite:
- Visible: Two scenarios (user page_size and user page override defaults)
- Oracle: 7 comprehensive tests covering both functions, partial merges, and edge cases
- Visible test encodes only the observable symptom; oracle is thorough verification
