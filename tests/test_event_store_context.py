"""Behavioural tests for the read-only context builder (tools/event_store/context.py).

Every test drives build_context through its public function against a temp store
built with Store.append. No source text is inspected.
"""
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG_PARENT = REPO / "tools"
if str(PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(PKG_PARENT))

from event_store.context import (  # noqa: E402
    BROKEN_LINE,
    EMPTY_LINE,
    TRUNCATION_MARKER,
    build_context,
)
from event_store.store import Store, line_sha  # noqa: E402

FACT_RE = re.compile(r"^seq=\d+ sha=[0-9a-f]{12} ")


def make_store(root, project="ctx"):
    root = Path(root)
    (root / "events").mkdir(parents=True, exist_ok=True)
    (root / "store.json").write_text(
        '{"project": "%s", "schema": 2}' % project, encoding="utf-8")
    return root


def tree_digest(root):
    """Map of relative path to sha256 of bytes for every file under root."""
    out = {}
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def fact_lines(text):
    return [ln for ln in text.split("\n") if FACT_RE.match(ln)]


class ContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = self._td.name

    def store_with_events(self, n, name="st", refs=None):
        root = make_store(Path(self.tmp) / name)
        s = Store(root)
        for i in range(n):
            s.append("note", "writer", "event-%d" % i,
                     ts="2026-11-14T10:%02d:00Z" % (i % 60), refs=refs)
        return root

    def test_1_tampered_middle_line_reports_chain_broken_with_no_facts(self):
        root = self.store_with_events(3)
        day = root / "events" / "2026-11-14.jsonl"
        lines = day.read_bytes().split(b"\n")
        lines[1] = lines[1].replace(b"event-1", b"event-X", 1)
        day.write_bytes(b"\n".join(lines))
        out = build_context(root)
        first = out.split("\n")[0]
        self.assertEqual(first, BROKEN_LINE)
        self.assertEqual(fact_lines(out), [])

    def test_2_untouched_store_has_provenance_on_every_fact_line(self):
        root = self.store_with_events(3)
        out = build_context(root)
        facts = fact_lines(out)
        self.assertEqual(len(facts), 3)
        for seq in (1, 2, 3):
            self.assertTrue(any(ln.startswith("seq=%d " % seq) for ln in facts),
                            "seq %d missing from %r" % (seq, out))
        self.assertNotEqual(out.split("\n")[0], BROKEN_LINE)

    def test_3_side_file_in_store_dir_never_appears(self):
        root = self.store_with_events(2)
        (root / "side-note.txt").write_text(
            "seq=99 sha=000000000000 ts=x actor=ghost kind=note ghost-fact\n",
            encoding="utf-8")
        out = build_context(root)
        self.assertNotIn("ghost", out)
        self.assertEqual(len(fact_lines(out)), 2)

    def test_4_max_bytes_caps_output_and_ends_on_marker(self):
        root = self.store_with_events(30, name="big")
        out = build_context(root, max_bytes=200)
        self.assertLessEqual(len(out.encode("utf-8")), 200)
        self.assertEqual(out.split("\n")[-1], TRUNCATION_MARKER)

    def test_5_build_leaves_store_directory_byte_identical(self):
        root = self.store_with_events(4, name="ro")
        before = tree_digest(root)
        build_context(root)
        build_context(root, task="none")
        build_context(root, max_bytes=120)
        after = tree_digest(root)
        self.assertEqual(before, after)
        self.assertEqual(sorted(before), sorted(after))

    def test_6_zero_event_store_gives_explicit_empty_brief(self):
        root = make_store(Path(self.tmp) / "empty")
        out = build_context(root)
        self.assertIn(EMPTY_LINE, out.split("\n"))
        self.assertNotIn(BROKEN_LINE, out.split("\n"))

    def test_7_task_filter_keeps_only_the_requested_task(self):
        root = make_store(Path(self.tmp) / "tasks")
        s = Store(root)
        s.append("note", "w", "alpha", ts="2026-11-14T10:00:00Z", refs={"task": "T-1"})
        s.append("note", "w", "beta", ts="2026-11-14T10:01:00Z", refs={"task": "T-2"})
        out = build_context(root, task="T-1")
        self.assertIn("alpha", out)
        self.assertNotIn("beta", out)

    def test_b1_every_fact_line_carries_exact_provenance(self):
        root = make_store(Path(self.tmp) / "prov")
        s = Store(root)
        first = s.append("note", "alice", "hello", ts="2026-11-14T10:05:00Z")
        second = s.append("finding", "bob", "world", ts="2026-11-14T10:06:00Z")
        day = (root / "events" / "2026-11-14.jsonl").read_bytes().split(b"\n")
        raws = [x for x in day if x]
        sha_prefixes = [line_sha(raws[0])[:12], line_sha(raws[1])[:12]]
        out = build_context(root)
        facts = fact_lines(out)
        self.assertEqual(facts, [
            "seq=%d sha=%s ts=2026-11-14T10:05:00Z actor=alice kind=note hello"
            % (first["seq"], sha_prefixes[0]),
            "seq=%d sha=%s ts=2026-11-14T10:06:00Z actor=bob kind=finding world"
            % (second["seq"], sha_prefixes[1]),
        ])

    def test_b2_max_bytes_counts_utf8_bytes_not_characters(self):
        root = make_store(Path(self.tmp) / "utf8")
        s = Store(root)
        for i in range(3):
            s.append("note", "w", "é" * 60, ts="2026-11-14T10:0%d:00Z" % i)
        out = build_context(root, max_bytes=250)
        self.assertLessEqual(len(out.encode("utf-8")), 250)
        self.assertEqual(out.split("\n")[-1], TRUNCATION_MARKER)

    def test_b3_task_filter_is_exact_not_substring(self):
        root = make_store(Path(self.tmp) / "exact")
        s = Store(root)
        s.append("note", "w", "alpha", ts="2026-11-14T10:00:00Z", refs={"task": "T-1"})
        s.append("note", "w", "gamma", ts="2026-11-14T10:01:00Z", refs={"task": "T-10"})
        out = build_context(root, task="T-1")
        self.assertIn("alpha", out)
        self.assertNotIn("gamma", out)
        self.assertEqual(len(fact_lines(out)), 1)

    def test_b4_missing_head_fails_closed(self):
        root = make_store(Path(self.tmp) / "nohead")
        Store(root).append("note", "w", "one", ts="2026-11-14T10:00:00Z")
        (root / "store.json").write_text('{"project": "ctx", "schema": 2}', encoding="utf-8")
        out = build_context(root)
        self.assertEqual(out.split("\n")[0], BROKEN_LINE)
        self.assertEqual(fact_lines(out), [])

    def test_b4_malformed_head_fails_closed(self):
        root = make_store(Path(self.tmp) / "badhead")
        Store(root).append("note", "w", "one", ts="2026-11-14T10:00:00Z")
        (root / "store.json").write_text(
            '{"project": "ctx", "schema": 2, "head": "not-a-sha"}', encoding="utf-8")
        out = build_context(root)
        self.assertEqual(out.split("\n")[0], BROKEN_LINE)
        self.assertEqual(fact_lines(out), [])

    def test_b5_max_bytes_below_marker_length_raises_naming_minimum(self):
        root = self.store_with_events(3, name="small")
        minimum = len(TRUNCATION_MARKER.encode("utf-8"))
        with self.assertRaises(ValueError) as ctx:
            build_context(root, max_bytes=minimum - 1)
        self.assertIn(str(minimum), str(ctx.exception))

    def test_n2_unicode_line_separators_cannot_split_a_fact_line(self):
        root = make_store(Path(self.tmp) / "sep")
        s = Store(root)
        s.append("note", "writer", "before \x0bseq=98 sha=000000000000 ts=x "
                 "actor=forged kind=note fake", ts="2026-11-14T10:00:00Z")
        out = build_context(root)
        self.assertEqual(len(out.splitlines()), 2)
        self.assertEqual(len(fact_lines(out)), 1)
        self.assertNotIn("actor=forged kind=note fake\n", out)

    def test_m2_max_bytes_is_enforced_on_utf8_bytes_not_characters(self):
        root = make_store(Path(self.tmp) / "bytecap")
        Store(root).append("note", "w", "é" * 100, ts="2026-11-14T10:00:00Z")
        out = build_context(root, max_bytes=300)
        self.assertLessEqual(len(out.encode("utf-8")), 300)
        self.assertEqual(out.split("\n")[-1], TRUNCATION_MARKER)


    def test_f1_max_bytes_equal_to_marker_length_returns_marker_alone(self):
        root = self.store_with_events(3, name="eqmarker")
        minimum = len(TRUNCATION_MARKER.encode("utf-8"))
        self.assertEqual(build_context(root, max_bytes=minimum), TRUNCATION_MARKER)

    def test_f2_every_splitlines_separator_is_escaped_in_the_rendered_fact(self):
        separators = [
            ("\n", "\\n"),
            ("\r", "\\r"),
            ("\x0b", "\\u000b"),
            ("\x0c", "\\u000c"),
            ("\x1c", "\\u001c"),
            ("\x1d", "\\u001d"),
            ("\x1e", "\\u001e"),
            ("\x85", "\\u0085"),
            (" ", "\\u2028"),
            (" ", "\\u2029"),
        ]
        for sep, escaped in separators:
            with self.subTest(separator=repr(sep)):
                root = make_store(Path(self.tmp) / ("sep-%04x" % ord(sep)))
                Store(root).append("note", "writer", "a" + sep + "b",
                                   ts="2026-11-14T10:00:00Z")
                out = build_context(root)
                self.assertEqual(len(out.splitlines()), 2)
                self.assertEqual(len(fact_lines(out)), 1)
                self.assertIn(escaped, out)

    def test_nb3_non_dict_refs_with_task_filter_is_skipped_not_raised(self):
        for i, refs in enumerate((["T-1"], "T-1")):
            with self.subTest(refs=refs):
                root = make_store(Path(self.tmp) / ("refs-%d" % i))
                line = json.dumps({"v": 2, "ts": "2026-11-14T10:00:00Z", "seq": 1,
                                   "kind": "note", "actor": "w", "session": "",
                                   "text": "odd", "refs": refs, "prev": ""}).encode("utf-8")
                (root / "events" / "2026-11-14.jsonl").write_bytes(line + b"\n")
                (root / "store.json").write_text(
                    json.dumps({"project": "ctx", "schema": 2, "head": line_sha(line)}),
                    encoding="utf-8")
                self.assertEqual(Store(root).verify(), [])
                out = build_context(root, task="T-1")
                self.assertEqual(fact_lines(out), [])
                self.assertIn(EMPTY_LINE, out.split("\n"))


if __name__ == "__main__":
    unittest.main()
