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
