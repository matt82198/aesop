"""Append lock for the v2 event store (tools/event_store/store.py Store.append).

The race test runs N separate OS processes that append to one store at the same time.
Without the lock two processes read the same tail and write the same seq and prev.
The lock is an OS advisory lock on a sidecar file: a crashed holder frees it at exit and
nothing ever judges a live holder stale.
"""
import json
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


def snapshot(root):
    """Bytes of every event file plus store.json."""
    snap = {"store.json": (Path(root) / "store.json").read_bytes()}
    for f in sorted((Path(root) / "events").glob("*.jsonl")):
        snap[f.name] = f.read_bytes()
    return snap


def start_holder(root, seconds, flag_path=""):
    """Start a process that holds the append lock; returns it once it reports READY."""
    cmd = [sys.executable, str(WORKER), "hold", str(root), repr(seconds)]
    if flag_path:
        cmd.append(str(flag_path))
    proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    line = proc.stdout.readline().decode("utf-8", "replace").strip()
    if line != "READY":
        proc.kill()
        proc.communicate()
        raise AssertionError("holder did not take the lock: %r" % line)
    return proc


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

    def test_slow_live_holder_is_never_robbed(self):
        # A holder stays in the section past any age a waiter could use to judge it stale
        # (the age limit is shrunk to 0.5 s here; the waiter must still wait it out).
        Store(self.root).append("k", "a", "first")
        flag = Path(self.tmp.name) / "left.flag"
        holder = start_holder(self.root, 3.0, flag)
        try:
            with mock.patch.object(store_mod, "STALE_LOCK_SECONDS", 0.5, create=True), \
                    mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 30.0):
                Store(self.root).append("k", "a", "second")
            self.assertTrue(flag.exists(), "waiter appended while the holder was inside")
        finally:
            holder.communicate(timeout=60)
        self.assertEqual(holder.returncode, 0)
        seqs = [json.loads(x.decode("utf-8"))["seq"] for x in event_lines(self.root)]
        self.assertEqual(seqs, [1, 2])
        self.assertEqual(Store(self.root).verify(), [])

    def test_dead_holder_frees_lock_promptly(self):
        Store(self.root).append("k", "a", "first")
        holder = start_holder(self.root, 600.0)
        holder.kill()
        holder.communicate(timeout=60)
        started = time.monotonic()
        with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 30.0):
            Store(self.root).append("k", "a", "second")
        self.assertLess(time.monotonic() - started, 10.0)
        self.assertEqual(len(event_lines(self.root)), 2)
        self.assertEqual(Store(self.root).verify(), [])

    def test_garbage_in_lock_file_does_not_block_or_crash(self):
        garbage = [b"", b'{"pid": 12', b"5", b"\xff\xfe\x00\x01\x80binary", b"[]", b"null"]
        for i, content in enumerate(garbage):
            with self.subTest(content=content):
                (self.root / LOCK).write_bytes(content)
                with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 3.0):
                    Store(self.root).append("k", "a", "g%d" % i)
        self.assertEqual(len(event_lines(self.root)), len(garbage))
        self.assertEqual(Store(self.root).verify(), [])

    def test_timeout_raises_and_writes_nothing(self):
        Store(self.root).append("k", "a", "first")
        before = snapshot(self.root)
        holder = start_holder(self.root, 5.0)
        try:
            started = time.monotonic()
            with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 0.5):
                with self.assertRaises(StoreError) as cm:
                    Store(self.root).append("k", "a", "blocked")
            self.assertLess(time.monotonic() - started, 4.0)
            self.assertIn("timed out", str(cm.exception))
            self.assertEqual(snapshot(self.root), before)
        finally:
            holder.kill()
            holder.communicate(timeout=60)

    def test_lock_released_when_append_raises(self):
        with mock.patch.object(Store, "_write_head", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                Store(self.root).append("k", "a", "t")
        with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 3.0):
            Store(self.root).append("k", "a", "t")
        self.assertEqual(len(event_lines(self.root)), 2)

    def test_lock_released_on_keyboard_interrupt(self):
        with mock.patch.object(Store, "_write_head", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                Store(self.root).append("k", "a", "t")
        with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 3.0):
            Store(self.root).append("k", "a", "t")
        self.assertEqual(len(event_lines(self.root)), 2)

    def test_lock_released_after_plain_section_exit(self):
        with store_mod._append_lock(self.root):
            pass
        with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 3.0):
            with store_mod._append_lock(self.root):
                pass

    def test_crash_between_line_append_and_head_is_reported_then_repaired(self):
        Store(self.root).append("k", "a", "first")
        with mock.patch.object(Store, "_write_head", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                Store(self.root).append("k", "a", "second")
        self.assertEqual(len(event_lines(self.root)), 2)
        problems = Store(self.root).verify()
        self.assertTrue(any("head does not match" in p for p in problems), problems)
        Store(self.root).append("k", "a", "third")
        self.assertEqual(Store(self.root).verify(), [])
        self.assertEqual(len(event_lines(self.root)), 3)


class HeadReplaceRetryTest(unittest.TestCase):
    # os.replace of store.json can fail with PermissionError on Windows while another process
    # holds the file open (a reader). The line is already appended at that point, so the head
    # write must retry briefly rather than fail an append that landed.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_store(Path(self.tmp.name) / "store")
        self.real_replace = store_mod.os.replace

    def tearDown(self):
        self.tmp.cleanup()

    def test_permission_error_then_success_appends_and_head_matches(self):
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) <= 2:
                raise PermissionError(13, "Access is denied", str(dst))
            return self.real_replace(src, dst)

        with mock.patch.object(store_mod.os, "replace", new=flaky):
            Store(self.root).append("k", "a", "first")
        self.assertEqual(len(calls), 3)
        lines = event_lines(self.root)
        head = json.loads((self.root / "store.json").read_text(encoding="utf-8"))["head"]
        self.assertEqual(head, store_mod.line_sha(lines[-1]))
        self.assertFalse((self.root / "store.json.tmp").exists())
        self.assertEqual(Store(self.root).verify(), [])

    def test_permission_error_every_time_surfaces_and_leaves_no_tmp(self):
        attempts = []

        def locked(src, dst):
            attempts.append(src)
            raise PermissionError(13, "Access is denied", str(dst))

        with mock.patch.object(store_mod.os, "replace", new=locked):
            with self.assertRaises(PermissionError):
                Store(self.root).append("k", "a", "first")
        self.assertEqual(len(attempts), store_mod.REPLACE_RETRIES)
        self.assertFalse((self.root / "store.json.tmp").exists())

    def test_non_permission_oserror_is_not_retried(self):
        attempts = []

        def full(src, dst):
            attempts.append(src)
            raise OSError(28, "No space left on device")

        with mock.patch.object(store_mod.os, "replace", new=full):
            with self.assertRaises(OSError):
                Store(self.root).append("k", "a", "first")
        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
