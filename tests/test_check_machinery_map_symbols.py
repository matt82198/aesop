#!/usr/bin/env python3
"""Tests for tools/check_machinery_map_symbols.py (machinery-map symbol citations).

Each test drives the real CLI against a throwaway repo: a tools/ file that holds
(or lacks) a named symbol, and a docs/MACHINERY-MAP.md with the citations under
test. Covered: a symbol cite that resolves passes; a bare `:<digits>` line cite
fails (the red proof); a missing symbol fails; a `(was :N, now stale: ...)`
annotation passes and is counted; a command flag in a code span is not a symbol;
a missing map fails closed with exit 2; the real repository map passes.
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
CHECK = ROOT / "tools" / "check_machinery_map_symbols.py"

FIXTURE_TOOL = (
    "import sys\n"
    "\n"
    "\n"
    "def check_thing():\n"
    "    return 0\n"
    "\n"
    "\n"
    "def other():\n"
    "    return 1\n"
)


def _make_repo(tmp: Path, map_text: str) -> Path:
    """Write tools/fixture.py and docs/MACHINERY-MAP.md under tmp; return the repo root."""
    (tmp / "tools").mkdir()
    (tmp / "tools" / "fixture.py").write_text(FIXTURE_TOOL, encoding="utf-8", newline="\n")
    (tmp / "docs").mkdir()
    (tmp / "docs" / "MACHINERY-MAP.md").write_text(map_text, encoding="utf-8", newline="\n")
    return tmp


def _run(root: Path):
    """Run the checker hermetically against root (its own --root, cwd bound)."""
    return subprocess.run(
        [sys.executable, str(CHECK), "--check", "--root", str(root)],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


class TestSymbolCitesPass(unittest.TestCase):
    def test_resolving_symbol_cite_passes(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), "| Gate | tools/fixture.py `check_thing` | Runs. |\n")
            r = _run(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(r.stdout.startswith("PASS machinery-map symbols: 1 symbol citations resolved"), r.stdout)

    def test_bare_file_name_resolves_by_unique_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), "Invoked fixture.py `other`.\n")
            r = _run(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_stale_annotation_passes_and_is_counted(self):
        text = ("| Gate | tools/fixture.py `check_thing` (was :123, now stale: "
                "line 123 is in other, check_thing is defined at line 4) |\n")
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), text)
            r = _run(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("1 stale annotations", r.stdout)

    def test_command_flag_code_span_is_not_a_symbol(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), "Command tools/fixture.py `--check --repo .` runs.\n")
            r = _run(root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestDriftFails(unittest.TestCase):
    def test_bare_line_citation_fails_red_proof(self):
        # The red proof: a fixture map with a :123 line citation must fail.
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), "| Gate | tools/fixture.py:123 | Runs. |\n")
            r = _run(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("line citation :123", r.stdout)

    def test_shorthand_line_citation_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), "| Gate | tools/fixture.py `check_thing` | Invoked :3355. |\n")
            r = _run(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("line citation :3355", r.stdout)

    def test_missing_symbol_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), "| Gate | tools/fixture.py `renamed_away` | Runs. |\n")
            r = _run(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("symbol renamed_away not found in tools/fixture.py", r.stdout)

    def test_missing_cited_file_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = _make_repo(Path(d), "| Gate | tools/gone.py `check_thing` | Runs. |\n")
            r = _run(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("cited file not found or ambiguous: tools/gone.py", r.stdout)


class TestFailClosed(unittest.TestCase):
    def test_missing_map_exits_two(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tools").mkdir()
            r = _run(root)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("machinery map not found", r.stderr)


class TestRealRepositoryMap(unittest.TestCase):
    def test_repository_map_has_no_line_citations(self):
        r = subprocess.run(
            [sys.executable, str(CHECK), "--check"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsNotNone(re.search(r"PASS machinery-map symbols: \d+ symbol citations resolved", r.stdout))
        self.assertIn("0 line citations", r.stdout)


if __name__ == "__main__":
    unittest.main()
