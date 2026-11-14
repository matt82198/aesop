"""Subprocess helper for test_event_store_append_lock.py.

Usage: python event_store_append_worker.py <store_root> <count> <tag> <start_at_epoch>

Or: python event_store_append_worker.py hold <store_root> <seconds> [flag_path]
(takes the append lock, prints READY, holds it, writes flag_path just before leaving).

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


def hold(store_root, seconds, flag_path):
    """Take the append lock, say READY, stay inside the section, then mark and leave."""
    from event_store.store import _append_lock
    with _append_lock(store_root):
        sys.stdout.write("READY\n")
        sys.stdout.flush()
        time.sleep(seconds)  # sleep-ok: holder stays inside the critical section
        if flag_path:
            Path(flag_path).write_text("left", encoding="utf-8")
    return 0


def main(argv):
    if argv[1] == "hold":
        return hold(argv[2], float(argv[3]), argv[4] if len(argv) > 4 else "")
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
