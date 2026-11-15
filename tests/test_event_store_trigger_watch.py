"""Trigger projection with a durable cursor (tools/event_store/watch.py).

Each test builds its own store and state root under a temp dir and points AESOP_STATE_ROOT
at that temp dir. No real store, real home or real state root is touched. The race and
lock rows use real OS processes; the lock-timeout row patches the timeout in process.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from event_store import store as store_mod  # noqa: E402
from event_store import watch  # noqa: E402
from event_store.store import Store  # noqa: E402

WATCH = TOOLS / "event_store" / "watch.py"
WORKER = REPO / "tests" / "event_store_append_worker.py"
PROJECT = "watchme"
RACERS = 4


def make_store(root, project=PROJECT):
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        json.dumps({"project": project, "schema": 2}), encoding="utf-8")
    return root


def add(root, kind, text="event"):
    return Store(root).append(kind, "tester", text)


def triggers(out):
    return [line for line in out.splitlines() if line.startswith("TRIGGER ")]


def snapshot(root):
    snap = {"store.json": (root / "store.json").read_bytes()}
    for f in sorted((root / "events").glob("*.jsonl")):
        snap[f.name] = f.read_bytes()
    return snap


def child_env(state_root):
    env = dict(os.environ)
    env["AESOP_STATE_ROOT"] = str(state_root)
    return env


def spawn_cli(store_root, state_root, *args):
    cmd = [sys.executable, "-I", str(WATCH), "--path", str(store_root), *args]
    return subprocess.Popen(cmd, cwd=str(REPO), env=child_env(state_root),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace")


def run_cli(store_root, state_root, *args):
    proc = spawn_cli(store_root, state_root, *args)
    out, _ = proc.communicate(timeout=120)
    return proc.returncode, out


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.store_root = make_store(base / "store")
        self.state_root = base / "state"
        self.state_root.mkdir()
        self.cursor = watch.cursor_file_for(self.state_root, PROJECT)
        self.holder = None

    def tearDown(self):
        if self.holder is not None:
            self.holder.kill()
            self.holder.communicate()
        self.tmp.cleanup()

    def cli(self, *args):
        return run_cli(self.store_root, self.state_root, *args)

    def cursor_bytes(self):
        return self.cursor.read_bytes()

    def day_file(self):
        return next((self.store_root / "events").glob("*.jsonl"))

    # Fire and pass

    def test_f1_first_pass_fires_triggers_in_seq_order(self):
        self.assertEqual(self.cli("--init")[0], 0)
        add(self.store_root, "verdict")
        add(self.store_root, "note")
        add(self.store_root, "ruling")
        add(self.store_root, "handback")
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(triggers(out), [
            "TRIGGER seq=1 kind=verdict ref=" + PROJECT,
            "TRIGGER seq=3 kind=ruling ref=" + PROJECT,
            "TRIGGER seq=4 kind=handback ref=" + PROJECT,
        ])
        self.assertEqual(out.splitlines()[-1], "WATCH OK cursor=4")

    def test_f2_immediate_repeat_pass_emits_nothing(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        self.assertEqual(self.cli()[0], 0)
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(triggers(out), [])
        self.assertEqual(out.splitlines()[-1], "WATCH OK cursor=1")

    def test_f3_one_new_event_after_cursor_fires_once(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        self.cli()
        add(self.store_root, "ruling")
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(triggers(out), ["TRIGGER seq=2 kind=ruling ref=" + PROJECT])
        self.assertEqual(json.loads(self.cursor_bytes())["seq"], 2)

    def test_f4_non_trigger_kind_advances_cursor_silently(self):
        self.cli("--init")
        add(self.store_root, "note")
        rc, out = self.cli()
        self.assertEqual(rc, 0, out)
        self.assertEqual(triggers(out), [])
        self.assertEqual(out.splitlines()[-1], "WATCH OK cursor=1")
        self.assertEqual(json.loads(self.cursor_bytes())["seq"], 1)

    def test_f5_empty_store_init_writes_cursor_zero(self):
        rc, out = self.cli("--init")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.splitlines()[-1], "WATCH OK cursor=0")
        self.assertEqual(json.loads(self.cursor_bytes()), {"seq": 0, "head": ""})

    def test_f6_cursor_write_is_atomic_and_leaves_no_tmp(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        self.assertEqual(self.cli()[0], 0)
        tmp = self.cursor.with_name(self.cursor.name + ".tmp")
        self.assertFalse(tmp.exists())
        self.assertEqual(set(json.loads(self.cursor_bytes())), {"seq", "head"})

    # Refuse

    def test_r1_tampered_chain_fails_and_cursor_unchanged(self):
        self.cli("--init")
        for kind in ("verdict", "note", "ruling"):
            add(self.store_root, kind)
        before = self.cursor_bytes()
        lines = self.day_file().read_bytes().split(b"\n")
        lines[1] = lines[1].replace(b"event", b"forged")
        self.day_file().write_bytes(b"\n".join(lines))
        rc, out = self.cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAILED", out)
        self.assertEqual(triggers(out), [])
        self.assertEqual(self.cursor_bytes(), before)

    def test_r2_duplicate_event_fails_and_cursor_unchanged(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        add(self.store_root, "note")
        before = self.cursor_bytes()
        first = self.day_file().read_bytes().split(b"\n")[0]
        with open(self.day_file(), "ab") as fh:
            fh.write(first + b"\n")
        rc, out = self.cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAILED", out)
        self.assertEqual(triggers(out), [])
        self.assertEqual(self.cursor_bytes(), before)

    def test_r3_racing_watchers_fire_each_event_once(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        procs = [spawn_cli(self.store_root, self.state_root) for _ in range(RACERS)]
        results = [p.communicate(timeout=120) for p in procs]
        fired = []
        for proc, (out, _) in zip(procs, results):
            self.assertEqual(proc.returncode, 0, out)
            fired += triggers(out)
        self.assertEqual(fired, ["TRIGGER seq=1 kind=verdict ref=" + PROJECT])

    def test_r4_missing_cursor_on_nonempty_store_fails(self):
        add(self.store_root, "verdict")
        rc, out = self.cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAILED", out)
        self.assertFalse(self.cursor.exists())

    def test_r5_cursor_ahead_of_tail_fails(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        self.cursor.write_text(json.dumps({"seq": 9, "head": "0" * 64}), encoding="utf-8")
        before = self.cursor_bytes()
        rc, out = self.cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAILED", out)
        self.assertIn("ahead", out)
        self.assertEqual(self.cursor_bytes(), before)

    def test_r6_unknown_kind_refused_nothing_emitted(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        add(self.store_root, "frobnicate")
        before = self.cursor_bytes()
        rc, out = self.cli()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAILED", out)
        self.assertEqual(triggers(out), [])
        self.assertEqual(self.cursor_bytes(), before)

    def test_r7_store_lock_held_elsewhere_fails_without_writing(self):
        self.cli("--init")
        add(self.store_root, "verdict")
        self.holder = subprocess.Popen(
            [sys.executable, str(WORKER), "hold", str(self.store_root), "30"],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace")
        self.assertEqual(self.holder.stdout.readline().strip(), "READY")
        before_cursor = self.cursor_bytes()
        before_store = snapshot(self.store_root)
        emitted = []
        with mock.patch.object(store_mod, "LOCK_TIMEOUT_SECONDS", 0.3):
            code = watch.run(Store(self.store_root), self.cursor, emit=emitted.append)
        self.assertEqual(code, 1)
        self.assertIn("FAILED", emitted[-1])
        self.assertEqual(triggers("\n".join(emitted)), [])
        self.assertEqual(self.cursor_bytes(), before_cursor)
        self.assertEqual(snapshot(self.store_root), before_store)

    def test_r8_cursor_inside_store_is_refused(self):
        inside = self.store_root / "cursor-state"
        inside.mkdir()
        rc, out = run_cli(self.store_root, inside, "--init")
        self.assertEqual(rc, 2, out)
        self.assertFalse(watch.cursor_file_for(inside, PROJECT).exists())

    def test_r9_unset_state_root_is_refused(self):
        env = dict(os.environ)
        env.pop("AESOP_STATE_ROOT", None)
        proc = subprocess.run(
            [sys.executable, "-I", str(WATCH), "--path", str(self.store_root), "--init"],
            cwd=str(REPO), env=env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertFalse(self.cursor.exists())


if __name__ == "__main__":
    unittest.main()
