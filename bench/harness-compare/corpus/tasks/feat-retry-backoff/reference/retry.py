"""Retry helper."""

import time


def retry(fn, attempts=3, base_delay=0.1, sleep=None):
    """Call ``fn`` up to ``attempts`` times with exponential backoff.

    Sleeps ``base_delay * 2**i`` between failed attempts ``i``. Re-raises the
    last exception when every attempt fails.
    """
    if sleep is None:
        sleep = time.sleep
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # retry on any error
            last_exc = exc
            if i < attempts - 1:
                sleep(base_delay * (2 ** i))
    raise last_exc
