"""Append lock for the v2 event store (tools/event_store/store.py Store.append).

The race test runs N separate OS processes that append to one store at the same time.
Without the lock two processes read the same tail and write the same seq and prev.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from event_store import store as store_mod  # noqa: E402
from event_store.store import Store, StoreError  # noqa: E402

WORKER = REPO / "tests" / "event_store_append_worker.py"
LOCK = ".append.lock"
RACE_PROCS = 8
RACE_EVENTS = 50


def make_store(root, project="race"):
    root = Path(root)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        json.dumps({"project": project, "schema": 2}), encoding="utf-8")
    return root


def event_lines(root):
    out = []
    for f in sorted((Path(root) / "events").glob("*.jsonl")):
        out += [x for x in f.read_bytes().split(b"\n") if x]
    return out


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], cwd=str(REPO))
    proc.wait(timeout=60)
    return proc.pid


class RaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_store(Path(self.tmp.name) / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def test_concurrent_processes_keep_one_chain(self):
        start_at = time.time() + 2.0
        procs = []
        for i in range(RACE_PROCS):
            procs.append(subprocess.Popen(
                [sys.executable, str(WORKER), str(self.root), str(RACE_EVENTS),
                 "w%d" % i, repr(start_at)],
                cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        errors = []
        for p in procs:
            out, err = p.communicate(timeout=240)
            if p.returncode != 0:
                errors.append(err.decode("utf-8", "replace")[-300:])
        self.assertEqual(errors, [], "worker processes failed")
        lines = event_lines(self.root)
        self.assertEqual(len(lines), RACE_PROCS * RACE_EVENTS)
        seqs = sorted(json.loads(x.decode("utf-8"))["seq"] for x in lines)
        self.assertEqual(seqs, list(range(1, RACE_PROCS * RACE_EVENTS + 1)))
        self.assertEqual(Store(self.root).verify(), [])


class LockBehaviourTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_store(Path(self.tmp.name) / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def write_lock(self, pid, age_seconds=0.0):
        lock = self.root / LOCK
        record = {"pid": pid, "host": socket.gethostname(), "ts": time.time() - age_seconds,
                  "token": "x"}
        lock.write_text(json.dumps(record), encoding="utf-8")
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(lock, (old, old))
        return lock

    def test_timeout_raises_and_leaves_live_lock_alone(self):
        lock = self.write_lock(os.getpid())
        before = lock.read_bytes()
        with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 0.3, create=True):
            with self.assertRaises(StoreError) as cm:
                Store(self.root).append("k", "a", "t")
        self.assertIn("timed out", str(cm.exception))
        self.assertTrue(lock.exists())
        self.assertEqual(lock.read_bytes(), before)
        self.assertEqual(event_lines(self.root), [])

    def test_stale_lock_from_dead_pid_is_recovered(self):
        pid = dead_pid()
        lock = self.write_lock(pid)
        Store(self.root).append("k", "a", "t")
        self.assertFalse(lock.exists())
        self.assertEqual(Store(self.root).verify(), [])

    def test_live_fresh_lock_is_not_stolen(self):
        lock = self.write_lock(os.getpid())
        before = lock.read_bytes()
        with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 0.3, create=True):
            with self.assertRaises(StoreError):
                Store(self.root).append("k", "a", "t")
        self.assertEqual(lock.read_bytes(), before)

    def test_lock_older_than_limit_is_recovered_even_if_pid_alive(self):
        self.write_lock(os.getpid(), age_seconds=600.0)
        Store(self.root).append("k", "a", "t")
        self.assertFalse((self.root / LOCK).exists())

    def test_lock_released_when_append_raises(self):
        with mock.patch.object(Store, "_write_head", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                Store(self.root).append("k", "a", "t")
        self.assertFalse((self.root / LOCK).exists())
        Store(self.root).append("k", "a", "t")
        self.assertEqual(Store(self.root).verify(), [])

    def test_crash_between_line_append_and_head_is_reported_then_repaired(self):
        with mock.patch.object(Store, "_write_head", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                Store(self.root).append("k", "a", "first")
        self.assertEqual(len(event_lines(self.root)), 1)
        problems = Store(self.root).verify()
        self.assertTrue(any("head does not match" in p for p in problems), problems)
        Store(self.root).append("k", "a", "second")
        self.assertEqual(Store(self.root).verify(), [])
        self.assertEqual(len(event_lines(self.root)), 2)


if __name__ == "__main__":
    unittest.main()
