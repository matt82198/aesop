#!/usr/bin/env python3
"""Tests for gen_tool_index.py — the generated tools/INDEX.md builder (A2).

Covers the fail-closed / determinism contract that the byte-identity gate in
claudemd_lint.py depends on:
- regenerate then --check is clean (exit 0)
- a hand-edited INDEX.md is caught (--check exit 1)
- a tools file with NO INDEX: line fails closed (--check AND --regenerate exit 1)
- the generator is deterministic (two --regenerate runs are byte-identical)
- a non-ASCII INDEX: line round-trips (regenerate -> check clean, stable)
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).parent.parent / "tools"
GEN = TOOLS / "gen_tool_index.py"
sys.path.insert(0, str(TOOLS))

import gen_tool_index  # noqa: E402


def _run(root: Path, *args):
    """Run gen_tool_index.py --root <root> <args> hermetically (cwd bound)."""
    return subprocess.run(
        [sys.executable, str(GEN), "--root", str(root), *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _make_repo(tmp: Path, tool_files: dict):
    """Create a temp git repo with tools/<name> = content, staged (no commit)."""
    (tmp / "tools").mkdir()
    for name, content in tool_files.items():
        (tmp / "tools" / name).write_text(content, encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q"], cwd=str(tmp), check=True,
                   capture_output=True, text=True, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True,
                   capture_output=True, text=True, encoding="utf-8")


PY_TOOL = '#!/usr/bin/env python3\n"""Summary line.\n\nINDEX: {desc}\n"""\nprint("x")\n'
SH_TOOL = "#!/usr/bin/env bash\n# INDEX: {desc}\necho x\n"


class TestRegenerateCheck(unittest.TestCase):
    def test_regenerate_then_check_clean(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {
                "alpha.py": PY_TOOL.format(desc="Alpha tool does alpha"),
                "beta.sh": SH_TOOL.format(desc="Beta shell tool"),
            })
            self.assertEqual(_run(tmp, "--regenerate").returncode, 0)
            self.assertTrue((tmp / "tools" / "INDEX.md").exists())
            self.assertEqual(_run(tmp, "--check").returncode, 0)

    def test_index_is_sorted_and_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {
                "zeta.py": PY_TOOL.format(desc="Zeta"),
                "alpha.py": PY_TOOL.format(desc="Alpha"),
            })
            _run(tmp, "--regenerate")
            body = (tmp / "tools" / "INDEX.md").read_text(encoding="utf-8")
            self.assertLess(body.index("`alpha.py`"), body.index("`zeta.py`"))
            self.assertIn(gen_tool_index.SENTINEL, body)
            self.assertIn(gen_tool_index.END_MARKER, body)


class TestHandEditCaught(unittest.TestCase):
    def test_hand_edit_index_md_fails_check(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {"alpha.py": PY_TOOL.format(desc="Alpha")})
            _run(tmp, "--regenerate")
            idx = tmp / "tools" / "INDEX.md"
            idx.write_text(idx.read_text(encoding="utf-8") + "\nsneaky hand edit\n",
                           encoding="utf-8", newline="\n")
            self.assertEqual(_run(tmp, "--check").returncode, 1)

    def test_missing_index_md_fails_check(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {"alpha.py": PY_TOOL.format(desc="Alpha")})
            self.assertEqual(_run(tmp, "--check").returncode, 1)


class TestMissingIndexFailsClosed(unittest.TestCase):
    def test_tool_without_index_line_fails_check(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {
                "alpha.py": PY_TOOL.format(desc="Alpha"),
                "noindex.py": '#!/usr/bin/env python3\n"""No index here."""\nprint("x")\n',
            })
            res = _run(tmp, "--check")
            self.assertEqual(res.returncode, 1)
            self.assertIn("noindex.py", res.stderr)

    def test_tool_without_index_line_blocks_regenerate(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {
                "alpha.py": PY_TOOL.format(desc="Alpha"),
                "noindex.py": '"""No index."""\n',
            })
            res = _run(tmp, "--regenerate")
            self.assertEqual(res.returncode, 1)
            # fail-closed: no partial INDEX.md written
            self.assertFalse((tmp / "tools" / "INDEX.md").exists())

    def test_missing_reported_in_json(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {"noindex.py": '"""x."""\n'})
            res = _run(tmp, "--json")
            self.assertEqual(res.returncode, 1)
            self.assertIn("noindex.py", res.stdout)


class TestDeterminism(unittest.TestCase):
    def test_two_regenerations_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {
                "gamma.py": PY_TOOL.format(desc="Gamma; CLI: `--check` | `--json`"),
                "alpha.sh": SH_TOOL.format(desc="Alpha shell"),
                "beta.py": PY_TOOL.format(desc="Beta"),
            })
            _run(tmp, "--regenerate")
            first = (tmp / "tools" / "INDEX.md").read_bytes()
            _run(tmp, "--regenerate")
            second = (tmp / "tools" / "INDEX.md").read_bytes()
            self.assertEqual(first, second)


class TestNonAsciiRoundTrip(unittest.TestCase):
    def test_non_ascii_index_line_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _make_repo(tmp, {
                "cafe.py": PY_TOOL.format(desc="Cafe tool with an accent: café latte"),
            })
            self.assertEqual(_run(tmp, "--regenerate").returncode, 0)
            self.assertEqual(_run(tmp, "--check").returncode, 0)
            first = (tmp / "tools" / "INDEX.md").read_bytes()
            _run(tmp, "--regenerate")
            self.assertEqual(first, (tmp / "tools" / "INDEX.md").read_bytes())


class TestExtractIndexLine(unittest.TestCase):
    def test_extracts_from_python_docstring(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.py"
            p.write_text(PY_TOOL.format(desc="hello world"), encoding="utf-8")
            self.assertEqual(gen_tool_index.extract_index_line(p), "hello world")

    def test_extracts_from_shell_comment(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.sh"
            p.write_text(SH_TOOL.format(desc="shell purpose"), encoding="utf-8")
            self.assertEqual(gen_tool_index.extract_index_line(p), "shell purpose")

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.py"
            p.write_text('"""no marker."""\n', encoding="utf-8")
            self.assertIsNone(gen_tool_index.extract_index_line(p))


if __name__ == "__main__":
    unittest.main()
