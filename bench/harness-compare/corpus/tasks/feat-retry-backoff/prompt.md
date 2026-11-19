# Add retry with exponential backoff

The working directory contains `retry.py` with a stub `retry(fn, attempts=3, base_delay=0.1, sleep=None)`. Implement it.

Behaviour:
- Call `fn()` up to `attempts` times. Return the first successful result.
- Between failed attempts, call `sleep(delay)` where the delays are `base_delay`, then `base_delay * 2`, then `base_delay * 4`, and so on.
- `sleep` defaults to `time.sleep` when it is `None`.
- If every attempt raises, re-raise the exception from the last attempt.

Write the test first in `tests/test_retry.py` and make it fail against the stub. Then implement the function until the test passes. The test file must exist in the working directory when you finish.

## Done when

`tests/test_retry.py` exists and passes, and `retry` meets the behaviour above when `sleep` is a recording fake.
