"""Behavioural tests for the read-only context builder (tools/event_store/context.py).

Every test drives build_context through its public function against a temp store
built with Store.append. No source text is inspected.
"""
import hashlib
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
from event_store.store import Store  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
