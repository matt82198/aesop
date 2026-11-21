#!/usr/bin/env python3
"""F1 chat bridge (tools/chat_bridge.py): reply writer and bounded serve loop (R9, R12)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "tools" / "chat_bridge.py"
NO_LISTENER = "no orchestrator listening"


class TestChatBridge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fixture = self.root / "fakeseat.py"
        self.fixture.write_text(
            "def seat_a(text):\n    yield 'A:' + text\n\n"
            "def seat_b(text):\n    yield 'B1:'\n    yield text\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args, seat=None):
        env = os.environ.copy()
        env["AESOP_STATE_ROOT"] = str(self.root)
        env["PYTHONPATH"] = str(self.root)
        env.pop("AESOP_CHAT_SEAT", None)
        if seat is not None:
            env["AESOP_CHAT_SEAT"] = seat
        return subprocess.run(
            [sys.executable, str(BRIDGE), *args],
            env=env, cwd=str(self.root), capture_output=True, text=True, timeout=60,
        )

    def _seed(self, turn_id, text):
        with open(self.root / "ui-inbox.md", "a", encoding="utf-8", newline="\n") as f:
            f.write(f"- [2026-11-21T00:00:00Z] chat({turn_id}): {text}\n")

    def _records(self, turn_id):
        p = self.root / "chat" / f"{turn_id}.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]

    def test_reply_appends_chunk_then_final(self):
        self.assertEqual(self._run("reply", "t1", "hello").returncode, 0)
        self.assertEqual(self._run("reply", "t1", "", "--final").returncode, 0)
        recs = self._records("t1")
        self.assertEqual([r["chunk"] for r in recs], ["hello", ""])
        self.assertEqual([r["final"] for r in recs], [False, True])
        self.assertEqual([r["seq"] for r in recs], [0, 1])
        self.assertTrue(all(r["turn_id"] == "t1" for r in recs))

    def test_serve_routes_seeded_turn_to_configured_seat(self):
        self._seed("t2", "hi there")
        r = self._run("serve", "--max-iterations", "1", seat="fakeseat:seat_a")
        self.assertEqual(r.returncode, 0, r.stderr)
        recs = self._records("t2")
        self.assertEqual("".join(x["chunk"] for x in recs), "A:hi there")
        self.assertTrue(recs[-1]["final"])

    def test_serve_swapping_config_routes_to_second_seat_without_code_change(self):
        self._seed("t3", "x")
        r = self._run("serve", "--max-iterations", "1", seat="fakeseat:seat_b")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual("".join(x["chunk"] for x in self._records("t3")), "B1:x")

    def test_serve_without_seat_replies_no_orchestrator_listening_exit_0(self):
        self._seed("t4", "anyone")
        r = self._run("serve", "--max-iterations", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        recs = self._records("t4")
        self.assertEqual("".join(x["chunk"] for x in recs), NO_LISTENER)
        self.assertTrue(recs[-1]["final"])

    def test_serve_answers_each_turn_once(self):
        self._seed("t5", "once")
        self._run("serve", "--max-iterations", "1", seat="fakeseat:seat_a")
        self._run("serve", "--max-iterations", "1", seat="fakeseat:seat_a")
        self.assertEqual(sum(1 for r in self._records("t5") if r["final"]), 1)

    def test_serve_unescapes_inbox_text(self):
        self._seed("t6", "a\\nb\\\\c")
        self._run("serve", "--max-iterations", "1", seat="fakeseat:seat_a")
        self.assertEqual("".join(x["chunk"] for x in self._records("t6")), "A:a\nb\\c")

    def test_serve_is_bounded_with_no_turns(self):
        r = self._run("serve", "--max-iterations", "2", "--interval", "0.01")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
