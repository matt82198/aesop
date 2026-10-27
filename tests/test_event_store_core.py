"""Behavioural tests for the per-project event store core (tools/event_store/)."""
import hashlib
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

from event_store.store import Store, StoreError  # noqa: E402

CLI = REPO / "tools" / "event_store" / "cli.py"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_store(root, project, schema=2):
    root = Path(root)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        json.dumps({"project": project, "schema": schema}), encoding="utf-8")
    return root


def registry_for(tmp):
    stores = {}
    goals = {"aesop": ["G0", "G1"], "keyrun": ["G2"], "psinasty": ["G3"], "brain": []}
    for name, g in goals.items():
        p = make_store(Path(tmp) / ("s-" + name), name)
        stores[name] = {"path": str(p), "goals": g}
    reg = {"tool": str(REPO), "stores": stores, "frozen": {"log": str(Path(tmp) / "frozen")}}
    rp = Path(tmp) / "event-stores.json"
    rp.write_text(json.dumps(reg), encoding="utf-8")
    return rp, stores


def run_cli(reg, *args):
    env = dict(os.environ)
    env["EVENT_STORES_REGISTRY"] = str(reg)
    return subprocess.run(
        [sys.executable, "-I", str(CLI), *args], env=env,
        capture_output=True, encoding="utf-8", errors="replace")


def count_events(store_path):
    n = 0
    for f in (Path(store_path) / "events").glob("*.jsonl"):
        n += len([x for x in f.read_text(encoding="utf-8").split("\n") if x])
    return n


class StoreChainTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name

    def test_a_v2_chains_across_day_files(self):
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        s.append("note", "o", "two", ts="2026-10-01T11:00:00Z")
        s.append("note", "o", "three", ts="2026-10-02T09:00:00Z")
        d1 = (root / "events" / "2026-10-01.jsonl").read_bytes()
        d2 = (root / "events" / "2026-10-02.jsonl").read_bytes()
        last1 = [x for x in d1.split(b"\n") if x][-1]
        first2 = json.loads([x for x in d2.split(b"\n") if x][0])
        self.assertEqual(first2["prev"], sha(last1))
        self.assertEqual(first2["seq"], 3)
        self.assertEqual(first2["v"], 2)
        self.assertEqual(s.verify(), [])
        self.assertTrue(d2.endswith(b"\n"))

    def test_b_deleting_first_day_fails_verify(self):
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        s.append("note", "o", "two", ts="2026-10-02T10:00:00Z")
        self.assertEqual(s.verify(), [])
        (root / "events" / "2026-10-01.jsonl").unlink()
        self.assertTrue(Store(root).verify())

    def test_c_v1_fixture_verifies_under_v1_rules(self):
        root = make_store(Path(self.tmp) / "v1", "frozen", schema=1)
        seq = 0
        for day in ("2026-09-01", "2026-09-02"):
            prev = ""
            lines = []
            for i in range(2):
                seq += 1
                ev = {"ts": day + "T0%d:00:00Z" % i, "seq": seq, "kind": "note",
                      "actor": "a", "session": "s", "text": "t", "refs": {}, "prev": prev}
                line = json.dumps(ev, sort_keys=True).encode("utf-8")
                prev = sha(line)
                lines.append(line)
            (root / "events" / (day + ".jsonl")).write_bytes(b"\n".join(lines) + b"\n")
        self.assertEqual(Store(root).verify(), [])
        # v1 tamper inside a day is still caught
        p = root / "events" / "2026-09-01.jsonl"
        p.write_bytes(p.read_bytes().replace(b'"t"', b'"x"', 1))
        self.assertTrue(Store(root).verify())


class CliTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name
        self.reg, self.stores = registry_for(self.tmp)

    def total(self):
        return sum(count_events(v["path"]) for v in self.stores.values())

    def test_d_no_project_no_goal_exits_2(self):
        r = run_cli(self.reg, "append", "--kind", "note", "--actor", "o", "--text", "x")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.total(), 0)

    def test_e_goal_ref_resolves_project(self):
        r = run_cli(self.reg, "append", "--kind", "note", "--actor", "o", "--text", "x",
                    "--ref", "goal=G2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(count_events(self.stores["keyrun"]["path"]), 1)
        r = run_cli(self.reg, "append", "--kind", "note", "--actor", "o", "--text", "y",
                    "--ref", "goal=G2.1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(count_events(self.stores["keyrun"]["path"]), 2)
        self.assertEqual(count_events(self.stores["aesop"]["path"]), 0)

    def test_f_store_project_mismatch_refused(self):
        make_store(self.stores["keyrun"]["path"], "psinasty")
        r = run_cli(self.reg, "append", "--project", "keyrun", "--kind", "note",
                    "--actor", "o", "--text", "x")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.total(), 0)

    def test_g_repo_encoded_ref_key_refused(self):
        r = run_cli(self.reg, "append", "--project", "keyrun", "--kind", "note",
                    "--actor", "o", "--text", "x", "--ref", "keyrun_pr_606=1")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--project", r.stderr)
        self.assertIn("pr=", r.stderr)
        self.assertEqual(self.total(), 0)

    def test_h_ruling_without_goal_refused(self):
        r = run_cli(self.reg, "append", "--project", "keyrun", "--kind", "ruling",
                    "--actor", "o", "--text", "x")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(self.total(), 0)

    def test_append_commits_in_git_store(self):
        path = Path(self.stores["keyrun"]["path"])
        for cmd in (["init", "-q"], ["config", "user.name", "t"],
                    ["config", "user.email", "t@example.com"]):
            subprocess.run(["git", "-C", str(path), *cmd], check=True)
        r = run_cli(self.reg, "append", "--project", "keyrun", "--kind", "note",
                    "--actor", "o", "--text", "x")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = subprocess.run(["git", "-C", str(path), "log", "--oneline"],
                             capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(len(out.stdout.strip().splitlines()), 1)


def write_v1_days(root):
    """Two conductor3 v1 day files (no 'v' key, chain restarts per file), no store.json."""
    (root / "events").mkdir(parents=True, exist_ok=True)
    seq = 0
    for day in ("2026-09-01", "2026-09-02"):
        prev, lines = "", []
        for i in range(2):
            seq += 1
            ev = {"ts": day + "T0%d:00:00Z" % i, "seq": seq, "kind": "note",
                  "actor": "a", "session": "s", "text": "t", "refs": {}, "prev": prev}
            line = json.dumps(ev, sort_keys=True).encode("utf-8")
            prev = sha(line)
            lines.append(line)
        (root / "events" / (day + ".jsonl")).write_bytes(b"\n".join(lines) + b"\n")
    return root


class V1NoStoreJsonTests(unittest.TestCase):
    """A v1 log directory (day files, no store.json) opens read-only as schema 1."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name

    def test_l_v1_day_files_without_store_json_open_read_only(self):
        root = write_v1_days(Path(self.tmp) / "events-log")
        s = Store(root)
        self.assertEqual(s.schema, 1)
        self.assertEqual(len(s.load()), 4)
        self.assertEqual(s.verify(), [])

    def test_m_v1_tamper_without_store_json_still_caught(self):
        root = write_v1_days(Path(self.tmp) / "events-log")
        p = root / "events" / "2026-09-01.jsonl"
        p.write_bytes(p.read_bytes().replace(b'"t"', b'"x"', 1))
        self.assertTrue(Store(root).verify())

    def test_n_v1_without_store_json_refuses_append_and_writes_nothing(self):
        root = write_v1_days(Path(self.tmp) / "events-log")
        before = {f.name: f.read_bytes() for f in (root / "events").iterdir()}
        with self.assertRaises(StoreError) as cm:
            Store(root).append("note", "o", "x")
        self.assertIn("store.json", str(cm.exception))
        self.assertFalse((root / "store.json").exists())
        after = {f.name: f.read_bytes() for f in (root / "events").iterdir()}
        self.assertEqual(before, after)

    def test_o_v2_day_files_without_store_json_still_refused(self):
        root = Path(self.tmp) / "v2"
        (root / "events").mkdir(parents=True)
        ev = {"v": 2, "ts": "2026-10-01T10:00:00Z", "seq": 1, "kind": "note", "actor": "o",
              "session": "", "text": "x", "refs": {}, "prev": ""}
        (root / "events" / "2026-10-01.jsonl").write_bytes(encode_line(ev))
        with self.assertRaises(StoreError):
            Store(root)

    def test_p_empty_dir_without_store_json_still_refused(self):
        root = Path(self.tmp) / "empty"
        root.mkdir()
        with self.assertRaises(StoreError):
            Store(root)

    def test_q_cli_verify_v1_dir_without_store_json(self):
        root = write_v1_days(Path(self.tmp) / "events-log")
        reg, _ = registry_for(self.tmp)
        r = run_cli(reg, "verify", "--path", str(root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("verify: 0 problem(s)", r.stdout)

    def test_r_last_line_text_edit_fails_verify(self):
        # RED on origin/main: verify checks each line's prev against its predecessor only,
        # so the final line's own text is covered by no hash and an edit goes unreported.
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        s.append("note", "o", "two", ts="2026-10-01T11:00:00Z")
        s.append("note", "o", "three", ts="2026-10-01T12:00:00Z")
        self.assertEqual(s.verify(), [])
        day = root / "events" / "2026-10-01.jsonl"
        lines = [x for x in day.read_bytes().split(b"\n") if x]
        self.assertIn(b'"text":"three"', lines[-1])
        lines[-1] = lines[-1].replace(b'"text":"three"', b'"text":"tampered"')
        json.loads(lines[-1].decode("utf-8"))  # still valid JSON
        day.write_bytes(b"\n".join(lines) + b"\n")
        self.assertTrue(Store(root).verify(),
                        "verify reported 0 problems after the last line was edited")


def encode_line(ev):
    return json.dumps(ev, sort_keys=True).encode("utf-8") + b"\n"


if __name__ == "__main__":
    unittest.main()
