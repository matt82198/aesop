"""
Ledger excuse pins for tools/test_isolation_tripwire.py (S1 spec, row f-aca3c525 part B).

Contract under test: a memory write made during a wrapped run is excused ONLY when
<root>/.claude/state/memory-write-ledger.jsonl holds a row whose `path` (posix, relative
to <root>/.claude, e.g. "memory/aesop/x.md") AND `sha256` (hex of the file's current
bytes) both match the changed file. Every pin here drives the real CLI against a
throwaway --root fixture; none reads the tripwire's source.

Pins:
  E1 changed memory file whose path AND current sha256 match a ledger row -> excused
     (no finding, exit 0).                                           [guard]
  E2 same path, ledger sha256 is for different bytes -> NOT excused.   [guard]
  E3 ledger row names a different path -> does not excuse the changed file. [guard]
  E4 changed file outside memory/ (settings.json) is never excused by any row. [guard]
  E5 missing or malformed ledger lines excuse nothing and do not crash. [guard]
  E5c a 100000-'[' ledger line (RecursionError, not ValueError) excuses nothing, does not
     crash, and does not stop a valid row after it from excusing its file.
  P1 guard: row path 'memory/aesop/x' (string prefix of x.md) with x.md's sha -> NOT excused.
  P2 guard: row path 'memory/aesop/x.md.bak' with x.md's sha -> NOT excused.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_test_isolation_tripwire import run_tripwire, _fail_bullets  # noqa: E402

LEDGER_REL = Path(".claude") / "state" / "memory-write-ledger.jsonl"
MEMORY_REL = "memory/aesop/x.md"
ORIGINAL = "original memory note\n"
NEW = "rewritten by a test\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_row(ledger: Path, path: str, sha: str) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    row = {"path": path, "sha256": sha, "ts": "2026-11-01T00:00:00Z", "writer": "test"}
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def _make_profile(tmp: Path) -> Path:
    root = tmp / "fake-home"
    (root / ".claude" / "memory" / "aesop").mkdir(parents=True)
    (root / ".claude" / "memory" / "aesop" / "x.md").write_text(ORIGINAL, encoding="utf-8")
    (root / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    return root


def _writer_command(target: Path, content: str) -> list:
    """Wrapped command: overwrite `target` with `content` during the run."""
    # write_bytes, not write_text: text mode emits CRLF on Windows, and the ledger hashes the
    # LF bytes of `content` (see _sha), so the on-disk bytes must be exactly content.encode('utf-8').
    code = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.argv[2].encode('utf-8'))"
    return [sys.executable, "-c", code, str(target), content]


class TestTripwireLedgerExcuse(unittest.TestCase):
    def test_e1_matching_path_and_sha_excuses_memory_write(self):
        """E1: the ledger names the changed memory file and its current sha256 -> exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            _write_row(root / LEDGER_REL, MEMORY_REL, _sha(NEW))

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(_fail_bullets(proc.stderr), [], proc.stderr)

    def test_e2_same_path_different_sha_is_not_excused(self):
        """E2: the ledger row's sha256 is for other bytes -> the write is still a finding."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            _write_row(root / LEDGER_REL, MEMORY_REL, _sha("some other content\n"))

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(MEMORY_REL, "\n".join(_fail_bullets(proc.stderr)), proc.stderr)

    def test_e3_row_for_different_path_does_not_excuse(self):
        """E3: the ledger row names another memory file with the right sha -> no excuse."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            _write_row(root / LEDGER_REL, "memory/aesop/other.md", _sha(NEW))

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(MEMORY_REL, "\n".join(_fail_bullets(proc.stderr)), proc.stderr)

    def test_e4_non_memory_file_is_never_excused(self):
        """E4: settings.json changed with a ledger row for it (matching sha) -> still a finding;
        the ledger only excuses memory/ paths."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            settings = root / ".claude" / "settings.json"
            _write_row(root / LEDGER_REL, "settings.json", _sha('{"changed": true}\n'))

            proc = run_tripwire(root, _writer_command(settings, '{"changed": true}\n'))

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("settings.json", "\n".join(_fail_bullets(proc.stderr)), proc.stderr)

    def test_e5_malformed_and_missing_ledger_excuse_nothing_and_do_not_crash(self):
        """E5: (a) garbage and truncated JSON lines excuse nothing; (b) a ledger file that
        does not exist at all excuses nothing. Neither may raise a Python traceback."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            ledger = root / LEDGER_REL
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                "not json at all\n"
                '{"path": "memory/aesop/x.md", "sha256": "' + _sha(NEW) + '"\n',  # truncated
                encoding="utf-8",
            )

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertIn(MEMORY_REL, "\n".join(_fail_bullets(proc.stderr)), proc.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            self.assertFalse((root / LEDGER_REL).exists())

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertIn(MEMORY_REL, "\n".join(_fail_bullets(proc.stderr)), proc.stderr)

    def test_e5c_hostile_nested_bracket_line_excuses_nothing_and_does_not_crash(self):
        """E5c: a ledger line of 100000 '[' characters (json.loads raises RecursionError, which
        is not a ValueError) must be skipped like any malformed line. (a) alone it excuses
        nothing and the run is a normal finding; (b) followed by a valid row for x.md, the
        hostile line still excuses nothing and the valid row still excuses x.md -> exit 0."""
        hostile = "[" * 100000 + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            ledger = root / LEDGER_REL
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(hostile, encoding="utf-8")

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotIn("Traceback", proc.stderr)
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(MEMORY_REL, "\n".join(_fail_bullets(proc.stderr)), proc.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            ledger = root / LEDGER_REL
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(hostile, encoding="utf-8")
            _write_row(ledger, MEMORY_REL, _sha(NEW))

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotIn("Traceback", proc.stderr)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(_fail_bullets(proc.stderr), [], proc.stderr)

    def test_p1_prefix_path_row_does_not_excuse(self):
        """P1 guard: a row whose path 'memory/aesop/x' is a string prefix of 'memory/aesop/x.md'
        carries the correct sha256 of x.md -> x.md is still a finding (exact path match only)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            _write_row(root / LEDGER_REL, "memory/aesop/x", _sha(NEW))

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(MEMORY_REL, "\n".join(_fail_bullets(proc.stderr)), proc.stderr)

    def test_p2_backup_suffix_path_row_does_not_excuse(self):
        """P2 guard: a row whose path 'memory/aesop/x.md.bak' extends x.md carries the correct
        sha256 of x.md -> x.md is still a finding (exact path match only)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_profile(Path(tmp))
            target = root / ".claude" / "memory" / "aesop" / "x.md"
            _write_row(root / LEDGER_REL, "memory/aesop/x.md.bak", _sha(NEW))

            proc = run_tripwire(root, _writer_command(target, NEW))

            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(MEMORY_REL, "\n".join(_fail_bullets(proc.stderr)), proc.stderr)


if __name__ == "__main__":
    unittest.main()
