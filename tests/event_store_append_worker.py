"""Subprocess helper for test_event_store_append_lock.py.

Usage: python event_store_append_worker.py <store_root> <count> <tag> <start_at_epoch>

Waits until start_at_epoch so that all workers begin appending together, then appends
<count> events to the v2 store at <store_root> as its own OS process.
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from event_store.store import Store  # noqa: E402


def main(argv):
    store_root, count, tag, start_at = argv[1], int(argv[2]), argv[3], float(argv[4])
    delay = start_at - time.time()
    if delay > 0:
        time.sleep(delay)  # sleep-ok: start barrier, not a timing assertion
    store = Store(store_root)
    for i in range(count):
        store.append("race", "worker-" + tag, "%s-%d" % (tag, i))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
