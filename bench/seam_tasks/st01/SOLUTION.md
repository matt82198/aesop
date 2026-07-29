# st01 Solution: Rate Limiter Boundary Condition Fix

## Defect Class
Off-by-one boundary error in comparison operator

## Files
- `repo/rate_limiter.py` (single file, line 26)

## The Fix
**File:** `repo/rate_limiter.py`
**Line:** 26
**Change:** `<=` to `<`

```diff
@@ -25,7 +25,7 @@ class RateLimiter:
         now = time.time()

         # Remove requests outside the window
         self.request_times = [
             req_time
             for req_time in self.request_times
-            if now - req_time <= self.window_duration
+            if now - req_time < self.window_duration
         ]
```

## Rationale
The rate limiter must allow N requests per window_duration. When checking if a request is still within the window, the comparison `now - req_time <= self.window_duration` incorrectly keeps requests that are exactly at the window boundary. This prevents new requests from being accepted at exactly window_duration seconds after the first request. The fix changes the comparison to `<` so requests at exactly the boundary are considered expired and can be replaced by new ones. This is a classic off-by-one error where the boundary condition was inclusive when it should be exclusive.

## Notes on Fixture Code
The fixture code in `repo/rate_limiter.py` contains no comments explaining the defect — it reads like honest production code with subtle boundary logic that would be discovered through testing.

## Verification Transcript

### Before Fix (Buggy Code): Oracle FAILS
```
cd bench/seam_tasks/st01 && python -m pytest oracle -q

F...                                                                     [100%]
================================== FAILURES ===================================
_______ TestRateLimiterBoundary.test_request_at_exact_boundary_allowed ________

    def test_request_at_exact_boundary_allowed(self):
        """At exactly window_duration seconds, new requests should be allowed."""
        limiter = RateLimiter(max_requests=2, window_duration=10)

        # Simulate time progression
        with patch('rate_limiter.time') as mock_time:
            # First request at T=0
            mock_time.time.return_value = 0
            assert limiter.allow() is True

            # Second request at T=5 (within window)
            mock_time.time.return_value = 5
            assert limiter.allow() is True

            # Third request at T=10 (exactly at boundary)
            # First request should be considered outside window now
            mock_time.time.return_value = 10
>           assert limiter.allow() is True  # Should be allowed
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E           assert False is True

oracle\test_rate_limiter.py:29: AssertionError
=========================== short test summary info ===========================
FAILED oracle/test_rate_limiter.py::TestRateLimiterBoundary::test_request_at_exact_boundary_allowed
1 failed, 3 passed in 0.07s
```

### After Fix: Oracle PASSES
```
cd bench/seam_tasks/st01 && python -m pytest oracle -q

....                                                                     [100%]
4 passed in 0.03s
```

## Oracle Tests
- Total count: 4 focused tests
- `test_request_at_exact_boundary_allowed`: Catches the off-by-one bug
- `test_request_after_boundary_allowed`: Verifies old requests are cleared after window
- `test_normal_rate_limit_within_window`: Ensures rate limit is enforced (happy path)
- `test_reset_clears_history`: Verifies reset works correctly (happy path)

## Visible Repro Test

### Test Assertions
The visible test `repo/test_repro.py` contains one focused assertion:
- Requests made at exactly the window boundary (after `window_duration` seconds) must be allowed

### Fail Output (Defective Code)
```
cd bench/seam_tasks/st01/repo && python -m pytest test_repro.py -q

F                                                                        [100%]
================================== FAILURES ===================================
________ TestRateLimiterBoundaryRepro.test_request_at_window_boundary _________

    def test_request_at_window_boundary(self):
        limiter = RateLimiter(max_requests=2, window_duration=10)
        with patch('rate_limiter.time') as mock_time:
            mock_time.time.return_value = 0
            assert limiter.allow() is True
            mock_time.time.return_value = 5
            assert limiter.allow() is True
            mock_time.time.return_value = 10
>           assert limiter.allow() is True
E           assert False is True

test_repro.py:26: AssertionError
1 failed in 0.07s
```

### Pass Output (Fixed Code)
```
cd bench/seam_tasks/st01/repo && python -m pytest test_repro.py -q

.                                                                        [100%]
1 passed in 0.02s
```

### Distinction from Oracle
The visible test is simpler and more focused than the oracle suite:
- Visible: Single test case with 1 focused scenario (boundary at exactly window_duration)
- Oracle: 4 comprehensive tests covering multiple scenarios (boundary, after-boundary, within-window, reset)
- Visible test encodes only the observable symptom; oracle is thorough verification
