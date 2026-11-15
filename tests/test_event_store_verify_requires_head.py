"""Finding A1: verify must require the head anchor on a non-empty schema-2 store.

Before this change a schema-2 store.json with no "head" key (or a null head) skipped the
head check entirely, so deleting the last event line verified clean. Every fixture lives
under a temp directory; no real store is touched.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from event_store.store import Store, encode_event, line_sha  # noqa: E402

TS_BASE = "2026-11-14T00:00:%02dZ"


def make_v2_root(base, project="headreq"):
    root = Path(base) / "store"
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        json.dumps({"project": project, "schema": 2}), encoding="utf-8")
    return root


def day_file(root):
    files = sorted((Path(root) / "events").glob("*.jsonl"))
    assert len(files) == 1, files
    return files[0]


def drop_last_line(root):
    f = day_file(root)
    lines = [x for x in f.read_bytes().split(b"\n") if x]
    f.write_bytes(b"\n".join(lines[:-1]) + b"\n")


def read_meta(root):
    return json.loads((Path(root) / "store.json").read_text(encoding="utf-8"))


def write_meta(root, meta):
    (Path(root) / "store.json").write_text(json.dumps(meta), encoding="utf-8")


def fill(root, count):
    store = Store(root)
    for i in range(1, count + 1):
        store.append("note", "tester", "event %d" % i, ts=TS_BASE % i)
    return store


class VerifyRequiresHeadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_v2_root(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_r1_missing_head_and_tail_deleted_is_reported(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        self.assertIn("head", meta, "precondition: append must have written a head")
        del meta["head"]
        write_meta(self.root, meta)
        drop_last_line(self.root)
        problems = Store(self.root).verify()
        self.assertTrue(problems,
                        "non-empty v2 store with no head and a deleted tail verified clean")

    def test_r2_missing_head_nothing_deleted_is_reported(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        del meta["head"]
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "non-empty v2 store with no head verified clean: %r" % (problems,))

    def test_r2b_single_event_store_without_head_is_reported(self):
        # One event is the smallest non-empty store; the head requirement must hold there too.
        fill(self.root, 1)
        meta = read_meta(self.root)
        del meta["head"]
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "one-event v2 store with no head verified clean: %r" % (problems,))

    def test_r3_null_head_is_reported(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["head"] = None
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "non-empty v2 store with a null head verified clean: %r" % (problems,))

    def test_r4_wrong_type_head_is_reported(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["head"] = 12345
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "non-empty v2 store with a non-string head verified clean: %r"
                        % (problems,))

    def test_r5_malformed_head_shape_is_reported(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["head"] = "not-a-sha256"
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "non-empty v2 store with a malformed head shape verified clean: %r"
                        % (problems,))

    def test_s1_head_63_hex_chars_is_malformed(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["head"] = "a" * 63
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "63-char head verified as well-formed: %r" % (problems,))

    def test_s2_uppercase_64_hex_head_is_malformed(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["head"] = "A" * 64
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "uppercase 64-hex head verified as well-formed: %r" % (problems,))

    def test_s3_64_char_non_hex_head_is_malformed(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["head"] = "z" * 64
        write_meta(self.root, meta)
        problems = Store(self.root).verify()
        self.assertTrue(any("missing or malformed" in p for p in problems),
                        "64-char non-hex head verified as well-formed: %r" % (problems,))

    def test_d1_downgraded_schema_with_head_kept_and_tail_deleted_is_reported(self):
        # store.json says schema 1 but the lines are v2: a downgrade must not verify clean.
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["schema"] = 1
        write_meta(self.root, meta)
        drop_last_line(self.root)
        problems = Store(self.root).verify()
        self.assertTrue(problems,
                        "schema-1 store.json over v2 lines with a deleted tail verified clean")

    def test_d2_downgraded_schema_with_head_removed_and_tail_deleted_is_reported(self):
        fill(self.root, 3)
        meta = read_meta(self.root)
        meta["schema"] = 1
        del meta["head"]
        write_meta(self.root, meta)
        drop_last_line(self.root)
        problems = Store(self.root).verify()
        self.assertTrue(problems,
                        "schema-1 store.json over v2 lines with no head and a deleted tail "
                        "verified clean")

    def test_d3_genuine_schema_1_store_with_store_json_still_verifies_clean(self):
        # Control: a real v1 log (no v2 lines) with store.json schema 1 keeps its verdict.
        root = Path(self.tmp.name) / "genuine_v1"
        (root / "events").mkdir(parents=True)
        (root / "store.json").write_text(
            json.dumps({"project": "genuine", "schema": 1}), encoding="utf-8")
        day = root / "events" / "2026-11-14.jsonl"
        first = {"kind": "note", "actor": "t", "seq": 1, "text": "one",
                 "ts": "2026-11-14T00:00:01Z", "prev": "", "session": "", "refs": {}}
        first_bytes = encode_event(first)
        second = {"kind": "note", "actor": "t", "seq": 2, "text": "two",
                  "ts": "2026-11-14T00:00:02Z", "prev": line_sha(first_bytes),
                  "session": "", "refs": {}}
        day.write_bytes(first_bytes + b"\n" + encode_event(second) + b"\n")
        self.assertEqual(Store(root).verify(), [])

    def _v1_store_with_v_line(self, name, count, index, value):
        # Schema-1 store.json over a valid v1 chain of `count` lines. Line `index` (1-based,
        # None for none) carries a "v" key with `value`; its bytes feed the next prev link.
        root = Path(self.tmp.name) / name
        (root / "events").mkdir(parents=True)
        (root / "store.json").write_text(
            json.dumps({"project": name, "schema": 1}), encoding="utf-8")
        lines = []
        prev = ""
        for i in range(1, count + 1):
            ev = {"kind": "note", "actor": "t", "seq": i, "text": "event %d" % i,
                  "ts": "2026-11-14T00:00:%02dZ" % i, "prev": prev, "session": "", "refs": {}}
            if i == index:
                ev["v"] = value
            raw = encode_event(ev)
            lines.append(raw)
            prev = line_sha(raw)
        (root / "events" / "2026-11-14.jsonl").write_bytes(b"\n".join(lines) + b"\n")
        return Store(root).verify()

    def test_r1_v_line_in_middle_of_schema1_store_is_flagged(self):
        problems = self._v1_store_with_v_line("mid", 3, 2, 2)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "middle line with v=2 not flagged: %r" % (problems,))

    def test_r1_v_line_first_in_schema1_store_is_flagged(self):
        problems = self._v1_store_with_v_line("first", 3, 1, 2)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:1:") for p in problems),
                        "first line with v=2 not flagged: %r" % (problems,))

    def test_r1_v_line_last_in_schema1_store_is_flagged(self):
        problems = self._v1_store_with_v_line("last", 3, 3, 2)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:3:") for p in problems),
                        "last line with v=2 not flagged: %r" % (problems,))

    def test_r2_string_v_on_schema1_line_is_flagged(self):
        problems = self._v1_store_with_v_line("strv", 3, 2, "2")
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as string \"2\" not flagged: %r" % (problems,))

    def test_r2_bool_true_v_on_schema1_line_is_flagged(self):
        problems = self._v1_store_with_v_line("boolv", 3, 2, True)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as bool true not flagged: %r" % (problems,))

    def test_r2_float_v_on_schema1_line_is_flagged(self):
        problems = self._v1_store_with_v_line("floatv", 3, 2, 2.0)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as float 2.0 not flagged: %r" % (problems,))

    def test_r2_control_genuine_schema1_store_without_v_keys_is_clean(self):
        self.assertEqual(self._v1_store_with_v_line("novkeys", 3, None, None), [])

    def test_r3_v_int_zero_on_schema1_line_is_flagged(self):
        # Falsy v values must still count as a "v" key: a truthiness check would skip them.
        problems = self._v1_store_with_v_line("zerov", 3, 2, 0)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as int 0 not flagged: %r" % (problems,))

    def test_r3_v_false_on_schema1_line_is_flagged(self):
        problems = self._v1_store_with_v_line("falsev", 3, 2, False)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as bool false not flagged: %r" % (problems,))

    def test_r3_v_null_on_schema1_line_is_flagged(self):
        problems = self._v1_store_with_v_line("nullv", 3, 2, None)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as null not flagged: %r" % (problems,))

    def test_r3_v_empty_string_on_schema1_line_is_flagged(self):
        problems = self._v1_store_with_v_line("emptyv", 3, 2, "")
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as empty string not flagged: %r" % (problems,))

    def test_r3_v_int_one_on_schema1_line_is_flagged(self):
        problems = self._v1_store_with_v_line("onev", 3, 2, 1)
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v as int 1 not flagged: %r" % (problems,))

    def test_r3_v2_line_in_store_json_less_v1_layout_is_flagged(self):
        # No store.json: the layout is implied v1 (first line has no v key). A v=2 line on
        # line 2 must still be reported; gating the v check on store.json presence would skip it.
        root = Path(self.tmp.name) / "nojson_v1"
        (root / "events").mkdir(parents=True)
        day = root / "events" / "2026-11-14.jsonl"
        first = {"kind": "note", "actor": "t", "seq": 1, "text": "one",
                 "ts": "2026-11-14T00:00:01Z", "prev": "", "session": "", "refs": {}}
        first_bytes = encode_event(first)
        second = {"v": 2, "kind": "note", "actor": "t", "seq": 2, "text": "two",
                  "ts": "2026-11-14T00:00:02Z", "prev": line_sha(first_bytes),
                  "session": "", "refs": {}}
        day.write_bytes(first_bytes + b"\n" + encode_event(second) + b"\n")
        store = Store(root)
        self.assertEqual(store.schema, 1)
        problems = store.verify()
        self.assertTrue(any(p.startswith("2026-11-14.jsonl:2:") for p in problems),
                        "v=2 line in store.json-less v1 layout not flagged: %r" % (problems,))

    def test_p1_zero_event_store_without_head_is_clean(self):
        self.assertEqual(Store(self.root).verify(), [])

    def test_p2_intact_store_with_head_is_clean(self):
        fill(self.root, 3)
        self.assertEqual(Store(self.root).verify(), [])

    def test_p3_control_head_intact_tail_deleted_is_reported(self):
        fill(self.root, 3)
        drop_last_line(self.root)
        problems = Store(self.root).verify()
        self.assertTrue(any("head does not match" in p for p in problems), problems)

    def test_p4_v1_store_without_store_json_keeps_clean_verdict(self):
        # Schema 1 (legacy conductor3 layout): day files only, no store.json, no head.
        root = Path(self.tmp.name) / "legacy"
        (root / "events").mkdir(parents=True)
        day = root / "events" / "2026-11-14.jsonl"
        first = {"kind": "note", "actor": "t", "seq": 1, "text": "one",
                 "ts": "2026-11-14T00:00:01Z", "prev": "", "session": "", "refs": {}}
        first_bytes = encode_event(first)
        second = {"kind": "note", "actor": "t", "seq": 2, "text": "two",
                  "ts": "2026-11-14T00:00:02Z", "prev": line_sha(first_bytes),
                  "session": "", "refs": {}}
        day.write_bytes(first_bytes + b"\n" + encode_event(second) + b"\n")
        store = Store(root)
        self.assertEqual(store.schema, 1)
        self.assertEqual(store.verify(), [])


if __name__ == "__main__":
    unittest.main()
