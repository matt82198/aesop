#!/usr/bin/env python3
"""Lock reclaim must consult the holder's pid, not only its age.

tools/merge_queue.py's advancer lock is the `.merge-queue-lock` directory. The
holder records its pid and a timestamp. Before this change a lock whose
timestamp was older than the stale bound was reclaimed even when its holder
was still alive, so a long-running live pass could have its lock stolen and
two actors then merged concurrently.

Contract under test:
  * a recorded pid that is ALIVE is never reclaimed, whatever the age;
  * a recorded pid that is DEAD is reclaimable regardless of age;
  * with no usable pid recorded, the age bound is the only reclaim path.

Hermetic: every lock lives under a tempfile directory, never the real state
dir. Dead pids come from a child process that has already exited and been
reaped; `os.kill(pid, 0)` is never used against a live process on Windows.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "merge_queue.py"


def load_module():
    """Load tools/merge_queue.py as an isolated module object."""
    spec = importlib.util.spec_from_file_location("merge_queue_lock_liveness", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reaped_dead_pid():
    """Return the pid of a child process that has exited and been reaped."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=60)
    return proc.pid


class LockFixture(unittest.TestCase):
    """Each test gets a private state root and a freshly loaded module."""

    def setUp(self):
        self.module = load_module()
        self._tmp = tempfile.TemporaryDirectory()
        self.state_root = Path(self._tmp.name) / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.lock = self.state_root / self.module.LOCK_DIRNAME

    def tearDown(self):
        self._tmp.cleanup()

    def _write_lock(self, timestamp=None, pid=None):
        self.lock.mkdir(parents=True)
        if timestamp is not None:
            (self.lock / "timestamp").write_text(str(timestamp), encoding="utf-8")
        if pid is not None:
            (self.lock / "pid").write_text(str(pid), encoding="utf-8")


class TestLivenessProbe(LockFixture):
    """The pid probe itself: it must tell a live pid from a dead one."""

    def test_own_pid_is_alive(self):
        self.assertTrue(self.module._pid_alive(os.getpid()))

    def test_reaped_child_pid_is_dead(self):
        self.assertFalse(self.module._pid_alive(reaped_dead_pid()))


class TestLockReclaimByPid(LockFixture):
    """Reclaim decision keyed on the recorded holder pid."""

    def test_live_holder_with_old_timestamp_is_not_reclaimed(self):
        """(a) THE DEFECT: a live holder must keep its lock past the stale age.

        Holder pid is this test process (alive). Timestamp is epoch 1, far older
        than stale_s=60. Before the fix this returned True and deleted the live
        holder's lock.
        """
        self._write_lock(timestamp=1, pid=os.getpid())
        self.assertFalse(self.module.acquire_lock(self.lock, stale_s=60))
        self.assertTrue((self.lock / "pid").exists(), "live holder's lock was taken")
        self.assertEqual((self.lock / "pid").read_text(encoding="utf-8").strip(),
                         str(os.getpid()))

    def test_dead_holder_is_reclaimable_regardless_of_age(self):
        """(b) A dead holder's lock is reclaimable even with a fresh timestamp."""
        self._write_lock(timestamp=int(time.time()), pid=reaped_dead_pid())
        self.assertTrue(self.module.acquire_lock(self.lock, stale_s=600))

    def test_no_pid_recorded_old_timestamp_is_reclaimed(self):
        """(c) Age remains the reclaim path when no pid was ever recorded."""
        self._write_lock(timestamp=1)
        self.assertTrue(self.module.acquire_lock(self.lock, stale_s=60))

    def test_no_pid_recorded_fresh_timestamp_is_not_reclaimed(self):
        """(d) With no pid and a fresh timestamp the lock is held."""
        self._write_lock(timestamp=int(time.time()))
        self.assertFalse(self.module.acquire_lock(self.lock, stale_s=600))


if __name__ == "__main__":
    unittest.main()
