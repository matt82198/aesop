# ST06: Cache Key Mismatch — Solution

## Defect Class
**Cache key format mismatch across retrieve and store operations**

## Hop Structure
- **Hop 0 (Symptom):** main.py calls get_settings(1) twice, each call takes 50ms (no cache speedup observed)
- **Hop 1:** settings_service.py retrieves from cache, but stores with string key format
- **Hop 2 (Root cause):** cache check uses integer key (1) but cache was populated with string key ("1"), so they don't match

The cache stores results with one key type (string) but the retrieval logic checks with a different key type (integer), causing all lookups to miss even for repeated requests.

## Reference Fix

In `settings_service.py`, change line 16 from:
```python
cached = _cache.get(user_id)
```

To:
```python
cached = _cache.get(f"{user_id}")
```

**Rationale:** The cache storage operation stores settings with a string-formatted key (`f"{user_id}"`), but the retrieval check uses the raw integer `user_id`. This causes every cache lookup to miss because the key formats don't match. The defect manifests 2 hops away from the root cause: the caller (main.py) gets poor performance because the retrieval layer fails to hit the cache, but the root cause is the key format mismatch in the service layer where keys are stored as strings but retrieved as integers.

## Verification Transcript

### Before Fix
```
.FFFF                                                                    [100%]
================================== FAILURES ===================================
test_cache_hit_returns_same_object: Cache should return the same object instance
test_cache_hit_different_user: User 1 should be cached
test_cache_hit_user_three: User 3 settings should be cached
test_multiple_users_all_cached: User 1 cache miss
=========================== 4 failed, 1 passed in 0.80s =========================
```

### After Fix
Apply the reference fix to `repo/settings_service.py` line 16 (change `_cache.get(user_id)` to `_cache.get(f"{user_id}")`).

Run `python -m pytest oracle -q`:
```
.....                                                                  [100%]
========================= 5 passed in 0.41s =========================
```

All 5 tests pass after applying the fix.
