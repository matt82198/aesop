"""CLI regression tests using over-cap lines from three historical Git blobs."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "claudemd_split.py"
FIXTURES = ROOT / "tests" / "fixtures" / "claudemd_split"


class TestClaudeMdSplit(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "tools").mkdir()
        (self.root / "tools/claudemd_line_cap_baseline.json").write_text(
            json.dumps({"hooks/CLAUDE.md": 6}), encoding="utf-8"
        )

    def document(self, data, domain="chat"):
        path = self.root / domain / "CLAUDE.md"
        path.parent.mkdir()
        path.write_bytes(data)
        return path

    def invoke(self, path, mode, status):
        run = subprocess.run([sys.executable, str(SCRIPT), mode, str(path)],
                             cwd=self.root, capture_output=True, text=True,
                             encoding="utf-8", timeout=15)
        self.assertEqual(status, run.returncode, run.stderr + run.stdout)
        return run.stderr + run.stdout

    def repair(self, path):
        original = path.read_bytes()
        self.assertTrue(self.invoke(path, "--check", 1).strip())
        self.assertEqual(original, path.read_bytes())
        self.invoke(path, "--write", 0)
        fixed = path.read_bytes()
        self.assertEqual(original.decode().split(), fixed.decode().split())
        self.invoke(path, "--check", 0)
        self.invoke(path, "--write", 0)
        self.assertEqual(fixed, path.read_bytes())
        return fixed

    def chat_fixture(self, name):
        original = (FIXTURES / name).read_bytes()
        fixed = self.repair(self.document(original))
        self.assertEqual(original, fixed.replace(b"\n\n", b" "))
        self.assertEqual(2, len(fixed.rstrip(b"\n").split(b"\n\n")))
        self.assertLessEqual(max(map(len, fixed.splitlines())), 1024)

    def test_single_line_contract_rejects_an_over_cap_replacement(self):
        from unittest.mock import patch
        from tools import claudemd_split

        first = b"Safe."
        last = ("\u00e9" * 513).encode()
        line = first + b" " + last
        for proposal in ([line], [first, last]):
            with self.subTest(lengths=list(map(len, proposal))):
                with patch.object(claudemd_split, "split_sentences", return_value=proposal):
                    with self.assertRaisesRegex(ValueError, "1024"):
                        claudemd_split.single_line_replacement(line)

    def test_chat_1440(self):
        self.chat_fixture("chat_1440.txt")  # 5f1492de, line 85

    def test_chat_1432(self):
        self.chat_fixture("chat_1432.txt")  # dd17d376, line 49

    def test_hooks_allowance(self):
        original = (FIXTURES / "hooks_allowance.txt").read_bytes()
        fixed = self.repair(self.document(original, "hooks"))
        first, second, remainder = fixed.split(b"\n", 2)
        self.assertEqual(original, first + b" " + second + b"\n" + remainder)
        self.assertEqual(6, sum(len(line) > 1024 for line in fixed.splitlines()))
        self.assertEqual(original.split(b"\n", 1)[1], remainder)  # 5581b911, split line 17

    def test_no_boundary_refuses_whole_write(self):
        original = (FIXTURES / "chat_1440.txt").read_bytes() + b"word " * 210
        path = self.document(original)
        for mode in ("--check", "--write"):
            self.assertIn("sentence", self.invoke(path, mode, 2).lower())
            self.assertEqual(original, path.read_bytes())

    def test_compliant_file_is_byte_identical(self):
        original = b"unchanged  \r\n```text\r\n" + b"x" * 1025 + b"\n```\nlast"
        path = self.document(original)
        for mode in ("--check", "--write"):
            self.invoke(path, mode, 0)
            self.assertEqual(original, path.read_bytes())

    def test_last_fitting_boundary_counts_utf8_bytes(self):
        first = ("\u00e9" * 300 + ". " + "b" * 420 + "!").encode()
        self.assertEqual(1023, len(first))
        path = self.document(b"header  \n" + first + b"  C? End.\r\nlast")
        self.assertEqual(b"header  \n" + first + b"\r\n\r\nC? End.\r\nlast",
                         self.repair(path))

    def test_code_span_is_not_a_sentence_boundary(self):
        first = b"A" * 600 + b"."
        code = b"`" + b"x" * 300 + b". example` " + b"z" * 200
        self.assertEqual(first + b"\n\n" + code,
                         self.repair(self.document(first + b" " + code)))

    def test_sentence_ending_in_quote(self):
        first = b'He said "' + b"a" * 750 + b'."'
        last = b"b" * 300
        self.assertEqual(first + b"\n\n" + last,
                         self.repair(self.document(first + b" " + last)))

    def test_split_does_not_turn_inline_text_into_a_heading(self):
        first = b"x" * 520 + b"."
        rest = b"Keep this literal. # example " + b"z" * 520
        self.assertEqual(first + b"\n\n" + rest,
                         self.repair(self.document(first + b" " + rest)))

    def test_allowance_prefers_fewest_breaks_then_first_line(self):
        three = (b"x" * 620 + b". ") * 3
        two = (b"y" * 620 + b". ") * 2
        original = b"\n".join([three, two, two] + [b"q" * 1050] * 4)
        fixed = self.repair(self.document(original, "hooks"))
        self.assertEqual(original.replace(two, two.replace(b". ", b".\n\n", 1), 1), fixed)


if __name__ == "__main__":
    unittest.main()
