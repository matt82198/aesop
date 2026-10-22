"""Behavioural tests for the per-project event store projections (tools/event_store/project.py).

Each test seeds real stores through Store.append and drives the real CLI
(`cli.py project ...`) against a registry written to a temp dir.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG_PARENT = REPO / "tools"
if str(PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(PKG_PARENT))

from event_store.store import Store  # noqa: E402

CLI = REPO / "tools" / "event_store" / "cli.py"


def make_store(root, project):
    root = Path(root)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        json.dumps({"project": project, "schema": 2}), encoding="utf-8")
    return root


def add(store_path, kind, ts, text, refs=None, actor="orchestrator"):
    event = Store(store_path).append(kind, actor, text, refs=refs, ts=ts)
    return event["seq"]


def write_registry(path, stores):
    Path(path).write_text(json.dumps({"tool": str(REPO), "stores": stores}),
                          encoding="utf-8")


def run_cli(registry, *args):
    env = dict(os.environ)
    env["EVENT_STORES_REGISTRY"] = str(registry)
    return subprocess.run(
        [sys.executable, "-I", str(CLI), "project", *args], env=env,
        capture_output=True, encoding="utf-8", errors="replace")


def snapshot(*store_paths):
    out = []
    for sp in store_paths:
        for p in sorted((Path(sp) / "events").rglob("*")):
            if p.is_file():
                out.append((str(p), p.read_bytes()))
    return out


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.aesop = make_store(self.root / "s-aesop", "aesop")
        self.keyrun = make_store(self.root / "s-keyrun", "keyrun")
        self.registry = self.root / "event-stores.json"
        self.stores = {"aesop": {"path": str(self.aesop), "goals": ["G0", "G1"]},
                       "keyrun": {"path": str(self.keyrun), "goals": ["G2"]}}
        write_registry(self.registry, self.stores)

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args):
        return run_cli(self.registry, *args)

    def seed_rulings(self):
        add(self.aesop, "ruling", "2026-10-20T10:00:00Z", "aesop decision",
            refs={"goal": "G1"})
        add(self.keyrun, "ruling", "2026-10-21T09:00:00Z", "keyrun decision",
            refs={"goal": "G2.1"})
        add(self.keyrun, "ruling", "2026-10-22T09:00:00Z", "keyrun no goal")

    def test_rulings_all_stores_newest_last_with_suffixes(self):
        self.seed_rulings()
        r = self.cli("rulings", "--all-stores")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "2026-10-20T10:00:00Z orchestrator aesop decision [project=aesop] [goal=G1]",
            "2026-10-21T09:00:00Z orchestrator keyrun decision [project=keyrun] [goal=G2.1]",
            "2026-10-22T09:00:00Z orchestrator keyrun no goal [project=keyrun]",
        ])

    def test_rulings_by_goal_groups_each_goal_once_no_goal_last(self):
        self.seed_rulings()
        r = self.cli("rulings", "--all-stores", "--by-goal")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "goal=G1",
            "2026-10-20T10:00:00Z orchestrator aesop decision [project=aesop] [goal=G1]",
            "goal=G2.1",
            "2026-10-21T09:00:00Z orchestrator keyrun decision [project=keyrun] [goal=G2.1]",
            "(no goal)",
            "2026-10-22T09:00:00Z orchestrator keyrun no goal [project=keyrun]",
        ])

    def test_pending_closes_across_stores_only_through_xref(self):
        seq = add(self.aesop, "verdict", "2026-10-20T10:00:00Z", "deploy is green",
                  refs={"observable": "deploy green"})
        r = self.cli("pending", "--all-stores")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "2026-10-20T10:00:00Z | orchestrator | deploy is green | observable: deploy green",
        ])
        # A cross-store finding that names the observable in closes must not close it.
        add(self.keyrun, "finding", "2026-10-21T09:00:00Z", "same words elsewhere",
            refs={"closes": "deploy green"})
        # An xref that points at the wrong seq must not close it either.
        add(self.keyrun, "finding", "2026-10-21T09:05:00Z", "wrong seq",
            refs={"xref": "aesop:%d" % (seq + 1)})
        r = self.cli("pending", "--all-stores")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 1, r.stdout)
        # The xref to the exact aesop seq closes it.
        add(self.keyrun, "finding", "2026-10-21T09:10:00Z", "closed by xref",
            refs={"xref": "aesop:%d" % seq})
        r = self.cli("pending", "--all-stores")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_checkpoints_last_one_per_store(self):
        add(self.aesop, "checkpoint", "2026-10-19T08:00:00Z", "aesop old checkpoint")
        add(self.aesop, "checkpoint", "2026-10-20T08:00:00Z", "aesop new checkpoint")
        add(self.keyrun, "checkpoint", "2026-10-21T08:00:00Z", "keyrun checkpoint")
        r = self.cli("checkpoints", "--all-stores", "--last", "1", "--per-store")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "# 2026-10-20T08:00:00Z orchestrator [project=aesop]",
            "aesop new checkpoint",
            "# 2026-10-21T08:00:00Z orchestrator [project=keyrun]",
            "keyrun checkpoint",
        ])
        # Without --per-store the single newest checkpoint across stores wins.
        r = self.cli("checkpoints", "--all-stores", "--last", "1")
        self.assertEqual(r.stdout.splitlines(), [
            "# 2026-10-21T08:00:00Z orchestrator [project=keyrun]",
            "keyrun checkpoint",
        ])

    def test_checkpoints_latest_per_goal(self):
        add(self.aesop, "checkpoint", "2026-10-19T08:00:00Z", "aesop G1 old",
            refs={"goal": "G1"})
        add(self.aesop, "checkpoint", "2026-10-20T08:00:00Z", "aesop G1 new",
            refs={"goal": "G1"})
        add(self.keyrun, "checkpoint", "2026-10-21T08:00:00Z", "keyrun G2 only",
            refs={"goal": "G2"})
        r = self.cli("checkpoints", "--all-stores", "--per-goal")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), [
            "# 2026-10-20T08:00:00Z orchestrator [project=aesop] [goal=G1]",
            "aesop G1 new",
            "# 2026-10-21T08:00:00Z orchestrator [project=keyrun] [goal=G2]",
            "keyrun G2 only",
        ])

    def test_projection_is_read_only_and_deterministic(self):
        self.seed_rulings()
        before = snapshot(self.aesop, self.keyrun)
        first = self.cli("rulings", "--all-stores", "--by-goal")
        second = self.cli("rulings", "--all-stores", "--by-goal")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(before, snapshot(self.aesop, self.keyrun))

    def test_broken_chain_is_reported_and_other_store_still_renders(self):
        self.seed_rulings()
        day = self.keyrun / "events" / "2026-10-21.jsonl"
        day.write_text(day.read_text(encoding="utf-8").replace(
            "keyrun decision", "keyrun tampered"), encoding="utf-8")
        r = self.cli("rulings", "--all-stores")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("CHAIN BROKEN keyrun: ", r.stdout)
        self.assertIn("aesop decision", r.stdout)
        self.assertNotIn("keyrun tampered", r.stdout)

    def test_unreadable_store_is_reported_and_other_store_still_renders(self):
        self.seed_rulings()
        self.stores["ghost"] = {"path": str(self.root / "missing"), "goals": []}
        write_registry(self.registry, self.stores)
        r = self.cli("rulings", "--all-stores")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("store ghost unreadable: ", r.stdout)
        self.assertIn("aesop decision", r.stdout)

    def test_refuses_without_a_store_selector(self):
        self.seed_rulings()
        r = self.cli("rulings")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("refused", r.stderr)


if __name__ == "__main__":
    unittest.main()
