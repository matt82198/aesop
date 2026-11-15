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


def _v1_day(root, day, seqs, prev_start=""):
    """Write one v1 day file. Each event gets the given seq; prev is rebuilt over the
    written line order so only the seq check can reject a bad order or number."""
    prev, lines = prev_start, []
    for i, s in enumerate(seqs):
        ev = {"ts": day + "T%02d:00:00Z" % i, "seq": s, "kind": "note",
              "actor": "a", "session": "s", "text": "t%d" % s, "refs": {}, "prev": prev}
        line = json.dumps(ev, sort_keys=True).encode("utf-8")
        prev = sha(line)
        lines.append(line)
    (root / "events" / (day + ".jsonl")).write_bytes(b"\n".join(lines) + b"\n")


class V1PerFileSeqTests(unittest.TestCase):
    """Real v1 logs restart seq at 1 in every day file (README: seq is the 1-based line
    number in the day file). verify must accept that and still reject real damage."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name

    def _root(self):
        root = Path(self.tmp) / "events-log"
        (root / "events").mkdir(parents=True)
        return root

    def test_s_v1_seq_restarting_per_file_verifies(self):
        # RED on origin/main: verify carries last_seq across day files, so the first
        # line of 2026-09-02 (seq 1) reads as "not increasing" after seq 3.
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 2, 3])
        _v1_day(root, "2026-09-02", [1, 2])
        self.assertEqual(Store(root).verify(), [])

    def test_t_v1_broken_prev_link_across_files_fails(self):
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 2])
        _v1_day(root, "2026-09-02", [1, 2], prev_start="0" * 64)
        self.assertTrue(Store(root).verify())

    def test_u_v1_seq_gap_within_file_fails(self):
        # Not caught on origin/main: 1 then 3 is still increasing.
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 3])
        self.assertTrue(Store(root).verify())

    def test_v_v1_duplicate_seq_within_file_fails(self):
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 1])
        self.assertTrue(Store(root).verify())

    def test_w_v1_reordered_lines_within_file_fail(self):
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 3, 2])
        self.assertTrue(Store(root).verify())

    def test_x_v1_swapped_day_files_fail(self):
        # RED on bf78d3dc: prev and seq restart per file, so swapping two valid day files
        # still verifies. The ts date of each event must match its file name.
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 2])
        _v1_day(root, "2026-09-02", [1, 2])
        a = root / "events" / "2026-09-01.jsonl"
        b = root / "events" / "2026-09-02.jsonl"
        a_bytes, b_bytes = a.read_bytes(), b.read_bytes()
        a.write_bytes(b_bytes)
        b.write_bytes(a_bytes)
        self.assertTrue(Store(root).verify())

    def test_y_v1_event_with_other_day_ts_fails(self):
        # RED on bf78d3dc: an event dated 2026-09-02 inside the 2026-09-01 file verifies.
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 2])
        _v1_day(root, "2026-09-02", [1, 2])
        day1 = root / "events" / "2026-09-01.jsonl"
        lines = [x for x in day1.read_bytes().split(b"\n") if x]
        lines[1] = lines[1].replace(b"2026-09-01T01", b"2026-09-02T01")
        day1.write_bytes(b"\n".join(lines) + b"\n")
        self.assertTrue(Store(root).verify())

    def test_z_v1_ts_decreasing_within_file_fails(self):
        # RED on bf78d3dc: timestamps running backwards inside one file verify.
        root = self._root()
        stamps = ["2026-09-01T05:00:00Z", "2026-09-01T02:00:00Z"]
        prev, lines = "", []
        for i, ts in enumerate(stamps, 1):
            ev = {"ts": ts, "seq": i, "kind": "note", "actor": "a", "session": "s",
                  "text": "t%d" % i, "refs": {}, "prev": prev}
            line = json.dumps(ev, sort_keys=True).encode("utf-8")
            prev = sha(line)
            lines.append(line)
        (root / "events" / "2026-09-01.jsonl").write_bytes(b"\n".join(lines) + b"\n")
        self.assertTrue(Store(root).verify())

    def test_known_limit_v1_deleted_middle_day_is_not_detected(self):
        # KNOWN LIMIT (v1): a missing whole day file is not detectable from v1 data alone.
        # prev and seq restart per file and the remaining files stay internally valid.
        # Closing this needs a seal manifest or the v2 cross-file chain. When that lands,
        # this test must be updated to assert detection.
        root = self._root()
        _v1_day(root, "2026-09-01", [1, 2])
        _v1_day(root, "2026-09-02", [1, 2])
        _v1_day(root, "2026-09-03", [1, 2])
        (root / "events" / "2026-09-02.jsonl").unlink()
        self.assertEqual(Store(root).verify(), [])


def encode_line(ev):
    return json.dumps(ev, sort_keys=True).encode("utf-8") + b"\n"


class AppendFailureModeTests(unittest.TestCase):
    """Append failure modes: a head write failing after the durable line, a torn last
    line, and a store.json.tmp left behind by a failed head write."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name

    def test_ea_head_failure_after_durable_line_is_store_error_do_not_retry(self):
        from unittest import mock
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        with mock.patch("event_store.store.os.replace",
                        side_effect=PermissionError(13, "held by a reader")):
            with self.assertRaises(StoreError) as cm:
                s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        self.assertIn("durable at seq 1; do not retry", str(cm.exception))
        self.assertEqual(count_events(root), 1)

    def test_eb_cli_head_failure_exits_nonzero_with_durable_message_no_traceback(self):
        import argparse
        import contextlib
        import io
        from unittest import mock
        from event_store import cli as cli_mod
        rp, stores = registry_for(self.tmp)
        args = argparse.Namespace(kind="note", actor="o", text="x", ref=[],
                                  project="keyrun", session="")
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"EVENT_STORES_REGISTRY": str(rp)}), \
                mock.patch("event_store.store.os.replace",
                           side_effect=PermissionError(13, "held by a reader")), \
                contextlib.redirect_stderr(err):
            rc = cli_mod.cmd_append(args)
        self.assertNotEqual(rc, 0)
        self.assertIn("durable at seq 1; do not retry", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertEqual(count_events(stores["keyrun"]["path"]), 1)

    def test_ec_head_failure_then_verify_reports_head_mismatch(self):
        from unittest import mock
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        s.append("note", "o", "two", ts="2026-10-01T11:00:00Z")
        self.assertEqual(s.verify(), [])
        with mock.patch("event_store.store.os.replace",
                        side_effect=PermissionError(13, "held by a reader")):
            with self.assertRaises(StoreError):
                s.append("note", "o", "three", ts="2026-10-01T12:00:00Z")
        problems = Store(root).verify()
        self.assertTrue(any("head does not match" in p for p in problems), problems)

    def test_ed_torn_last_line_is_store_error_naming_line_and_writes_nothing(self):
        root = make_store(Path(self.tmp) / "st", "keyrun")
        Store(root).append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        day = root / "events" / "2026-10-01.jsonl"
        with day.open("ab") as fh:
            fh.write(b'{"v":2,"ts":"2026-10-01T1')
        before = day.read_bytes()
        head_before = (root / "store.json").read_bytes()
        with self.assertRaises(StoreError) as cm:
            Store(root).append("note", "o", "two", ts="2026-10-01T11:00:00Z")
        self.assertIn("2026-10-01.jsonl", str(cm.exception))
        self.assertEqual(day.read_bytes(), before)
        self.assertEqual((root / "store.json").read_bytes(), head_before)

    def test_ee_store_json_tmp_removed_when_head_tmp_write_fails(self):
        from unittest import mock
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        real_write_text = Path.write_text

        def failing_write_text(self_path, data, *args, **kwargs):
            if self_path.name == "store.json.tmp":
                real_write_text(self_path, '{"project": "keyr', encoding="utf-8")
                raise OSError(28, "no space left on device")
            return real_write_text(self_path, data, *args, **kwargs)

        with mock.patch.object(Path, "write_text", failing_write_text):
            with self.assertRaises((OSError, StoreError)):
                s.append("note", "o", "two", ts="2026-10-01T11:00:00Z")
        self.assertFalse((root / "store.json.tmp").exists())

    def test_ef_permission_head_failure_raises_head_permission_error(self):
        from unittest import mock
        from event_store.store import HeadPermissionError
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        with mock.patch("event_store.store.os.replace",
                        side_effect=PermissionError(13, "held by a reader")):
            with self.assertRaises(HeadPermissionError) as cm:
                s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        self.assertIs(type(cm.exception), HeadPermissionError)

    def test_eg_head_write_error_bases_are_store_error_oserror_and_permission(self):
        from event_store.store import HeadWriteError, HeadPermissionError
        self.assertTrue(issubclass(HeadWriteError, StoreError))
        self.assertTrue(issubclass(HeadWriteError, OSError))
        self.assertTrue(issubclass(HeadPermissionError, HeadWriteError))
        self.assertTrue(issubclass(HeadPermissionError, PermissionError))
        self.assertTrue(issubclass(HeadPermissionError, StoreError))

    def test_eh_head_oserror_gives_head_write_error_not_permission_with_durable_text(self):
        from unittest import mock
        from event_store.store import HeadWriteError, HeadPermissionError
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        with mock.patch("event_store.store.os.replace",
                        side_effect=OSError(28, "no space left on device")):
            with self.assertRaises(HeadWriteError) as cm:
                s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        self.assertIs(type(cm.exception), HeadWriteError)
        self.assertNotIsInstance(cm.exception, HeadPermissionError)
        self.assertIn("durable at seq 1; do not retry", str(cm.exception))
        self.assertEqual(count_events(root), 1)

    def test_ei_cli_head_oserror_exits_nonzero_with_durable_message_no_traceback(self):
        import argparse
        import contextlib
        import io
        from unittest import mock
        from event_store import cli as cli_mod
        rp, stores = registry_for(self.tmp)
        args = argparse.Namespace(kind="note", actor="o", text="x", ref=[],
                                  project="keyrun", session="")
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"EVENT_STORES_REGISTRY": str(rp)}), \
                mock.patch("event_store.store.os.replace",
                           side_effect=OSError(28, "no space left on device")), \
                contextlib.redirect_stderr(err):
            rc = cli_mod.cmd_append(args)
        self.assertNotEqual(rc, 0)
        self.assertIn("durable at seq 1; do not retry", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertEqual(count_events(stores["keyrun"]["path"]), 1)

    def test_ej_non_oserror_from_head_write_propagates_unwrapped(self):
        from unittest import mock
        root = make_store(Path(self.tmp) / "st", "keyrun")
        s = Store(root)
        with mock.patch("event_store.store.os.replace",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                s.append("note", "o", "one", ts="2026-10-01T10:00:00Z")

    def test_ek_torn_non_utf8_last_line_is_store_error_naming_day_file_writes_nothing(self):
        root = make_store(Path(self.tmp) / "st", "keyrun")
        Store(root).append("note", "o", "one", ts="2026-10-01T10:00:00Z")
        day = root / "events" / "2026-10-01.jsonl"
        with day.open("ab") as fh:
            fh.write(b'\xff\xfe{"v":2,"ts":"2026-10-01T1')
        before = day.read_bytes()
        head_before = (root / "store.json").read_bytes()
        with self.assertRaises(StoreError) as cm:
            Store(root).append("note", "o", "two", ts="2026-10-01T11:00:00Z")
        self.assertIn("2026-10-01.jsonl", str(cm.exception))
        self.assertEqual(day.read_bytes(), before)
        self.assertEqual((root / "store.json").read_bytes(), head_before)


if __name__ == "__main__":
    unittest.main()
